# -*- coding: utf-8 -*-
"""
EGC | Creditos PIS/COFINS -- gera a CARGA FIXA do historico (SPED original x apontado) como um bloco SQL.  (v0.50.0)

Uso (na maquina que tem os SPED; precisa so' de python + pandas):
    python scripts/gerar_historico_creditos.py "C:\\...\\SPED CONTRIBUIÇÕES - ENERMAIS ENERGIA" --empresa ENERGIA --ate 07/2026 --saida historico.sql

Regra (decidida com o Rafael em 16/10/2026): para cada competencia, entre as versoes do SPED COM CONTEUDO (credito ou Bloco M),
  * ORIGINAL = a primeira versao enviada;  * APONTADO = a ultima versao enviada.
Mes com uma versao so' entra como APONTADO (nao ha' o que comparar). Versao sem conteudo (ex.: original de fev/2026 vazio) e' ignorada.
O resultado traz numeros reais da empresa: por isso a saida NAO vai para o repositorio -- fica no cofre (vault) e e' rodada no SQL Editor
do Supabase depois do bloco 28. O script e' idempotente (ON CONFLICT ... DO UPDATE).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import sped_contribuicoes as sc  # noqa: E402


def _chave(c: str) -> str:
    m, a = c.split("/")
    return f"{a}-{m}"


def _tem_conteudo(s: dict) -> bool:
    return (not s["creditos"].empty) or bool(s["apuracao"].get("m100")) or bool(s["apuracao"].get("m500"))


def ler_pasta(pasta: Path) -> dict:
    """{competencia: [sped lido, ...]} ordenado pelo envio a Receita (data no nome do arquivo)."""
    por: dict = {}
    for arq in sorted(pasta.glob("PISCOFINS_*.txt")):
        s = sc.ler_sped(arq.read_bytes(), arq.name)
        if s["erro"] and not s["cabecalho"]:
            print(f"AVISO: {arq.name}: {s['erro']}", file=sys.stderr)
            continue
        s["_conteudo"] = _tem_conteudo(s)
        por.setdefault(s["cabecalho"]["competencia"], []).append(s)
    for v in por.values():
        v.sort(key=lambda s: (s["cabecalho"].get("enviado_em_arquivo") or "", s["arquivo"]))
    return por


def montar_linhas(por: dict, ate: str | None) -> list:
    saida = []
    for comp in sorted(por, key=_chave):
        if ate and _chave(comp) > _chave(ate):
            continue
        vers = [s for s in por[comp] if s["_conteudo"]]
        if not vers:
            print(f"AVISO: {comp}: nenhuma versao com conteudo", file=sys.stderr)
            continue
        orig, apont = vers[0], vers[-1]

        def linha(s, versao, dif=None):
            cab = s["cabecalho"]
            return {"competencia": comp, "versao": versao, "sped_tipo": cab["tipo"], "enviado_arquivo_em": cab.get("enviado_em_arquivo"),
                    "arquivo_nome": s["arquivo"], "resumo": {**sc.resumo_apuracao(s["apuracao"]), "linhas_credito": int(len(s["creditos"]))},
                    "por_categoria": sc.por_categoria(s["creditos"]), "por_natureza": sc.por_natureza(s["apuracao"]),
                    "dif_fornecedores": dif or []}
        if len(vers) > 1:
            saida.append(linha(orig, "ORIGINAL"))
            saida.append(linha(apont, "APONTADO", sc.dif_fornecedores(orig["creditos"], apont["creditos"])))
        else:
            saida.append(linha(apont, "APONTADO"))
    return saida


def _sql_str(v) -> str:
    return "NULL" if v in (None, "") else "'" + str(v).replace("'", "''") + "'"


def _sql_json(v) -> str:
    return "'" + json.dumps(v, ensure_ascii=False).replace("'", "''") + "'::jsonb"


def gerar_sql(empresa: str, linhas: list) -> str:
    cab = ["-- EGC | Creditos PIS/COFINS -- carga fixa do historico (gerada por scripts/gerar_historico_creditos.py)",
           "-- Rodar DEPOIS do bloco 28. Idempotente. Numeros reais: nao colocar no repositorio.", "BEGIN;"]
    corpo = []
    for l in linhas:
        corpo.append(
            "INSERT INTO egc.cred_historico (empresa_codigo, competencia, versao, sped_tipo, enviado_arquivo_em, arquivo_nome, resumo, "
            "por_categoria, por_natureza, dif_fornecedores) VALUES "
            f"({_sql_str(empresa)}, {_sql_str(l['competencia'])}, {_sql_str(l['versao'])}, {_sql_str(l['sped_tipo'])}, "
            f"{_sql_str(l['enviado_arquivo_em'])}, {_sql_str(l['arquivo_nome'])}, {_sql_json(l['resumo'])}, {_sql_json(l['por_categoria'])}, "
            f"{_sql_json(l['por_natureza'])}, {_sql_json(l['dif_fornecedores'])}) "
            "ON CONFLICT (empresa_codigo, competencia, versao) DO UPDATE SET sped_tipo = EXCLUDED.sped_tipo, "
            "enviado_arquivo_em = EXCLUDED.enviado_arquivo_em, arquivo_nome = EXCLUDED.arquivo_nome, resumo = EXCLUDED.resumo, "
            "por_categoria = EXCLUDED.por_categoria, por_natureza = EXCLUDED.por_natureza, dif_fornecedores = EXCLUDED.dif_fornecedores;")
    ver = ("SELECT competencia, versao, sped_tipo, (resumo->>'credito_pis')::numeric AS credito_pis, (resumo->>'credito_cofins')::numeric AS credito_cofins "
           f"FROM egc.cred_historico WHERE empresa_codigo = {_sql_str(empresa)} ORDER BY competencia, versao;")
    return "\n".join(cab + corpo + ["COMMIT;", "", "-- Conferir:", ver, ""])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pasta")
    ap.add_argument("--empresa", default="ENERGIA")
    ap.add_argument("--ate", default="07/2026", help="ultima competencia da carga fixa (MM/AAAA)")
    ap.add_argument("--saida", default="historico_creditos.sql")
    a = ap.parse_args(argv)
    por = ler_pasta(Path(a.pasta))
    linhas = montar_linhas(por, a.ate)
    Path(a.saida).write_text(gerar_sql(a.empresa, linhas), encoding="utf-8")
    print(f"{len(linhas)} linhas de historico em {a.saida}")
    for l in linhas:
        r = l["resumo"]
        print(f"  {l['competencia']} {l['versao']:9s} {l['sped_tipo']:12s} PIS {r['credito_pis']:>10,.2f}  COFINS {r['credito_cofins']:>10,.2f}  {l['arquivo_nome'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
