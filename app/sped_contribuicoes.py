# -*- coding: utf-8 -*-
"""
EGC | Creditos PIS/COFINS -- leitura do SPED EFD-Contribuicoes (.txt).  (v0.50.0, 16/10/2026)

Le o arquivo da contabilidade (ISO-8859-1, campos separados por "|", assinatura binaria depois do |9999|) e devolve:
  * cabecalho  : empresa, CNPJ, periodo, tipo (original/retificadora), numero do recibo anterior;
  * creditos   : 1 linha por item/documento que gera credito (A170, C170, C191+C195, C501/C505, D101/D105, F120, F130);
  * receitas   : 1 linha por documento de venda com PIS/COFINS apurado (debito) -- A170/C170 de saida;
  * apuracao   : totais do Bloco M (credito M100/M500, bases M105/M505, contribuicao M200/M600, DARF M205/M605,
                 por aliquota M210/M610) e retencoes na fonte (F600).

Layouts conferidos contra o Guia Pratico da EFD-Contribuicoes e contra 17 arquivos reais da Energia (jan-ago/2026); a conferencia
com o Detalhamento da contadora (jul/2026) fecha 627/627 documentos sem diferenca de valor (ver tests/test_creditos_*).

Notas de leiaute que ja custaram um erro: no C100 o numero do documento e' o campo 8 (indice 7) e a CHAVE e' o 9; no C500 (EFD-Contribuicoes) o numero e' o
campo 7 (indice 6; nao tem COD_CONS, ao contrario da EFD-ICMS). A partir das retificadoras de 29/09/2026 (e do original de agosto) as compras de mercadoria vem
CONSOLIDADAS (C190/C191/C195: fornecedor + item + CFOP, sem numero de nota) -- o resto continua por documento.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

import pandas as pd

COLUNAS_CREDITO = ["registro", "cnpj", "fornecedor", "numero", "data", "item", "descricao", "cfop", "nat_bc", "categoria",
                   "cst_pis", "base_pis", "aliq_pis", "pis", "cst_cofins", "base_cofins", "aliq_cofins", "cofins", "cod_cta"]
COLUNAS_RECEITA = ["registro", "cnpj", "cliente", "numero", "data", "valor", "cst_pis", "base_pis", "aliq_pis", "pis",
                   "cst_cofins", "base_cofins", "aliq_cofins", "cofins"]

CST_CREDITO = {f"{n:02d}" for n in list(range(50, 57)) + list(range(60, 67))}
CFOP_COMBUSTIVEL = {"1653", "2653", "1652", "2652", "1651", "2651", "1659", "2659"}
NAT_BC_NOME = {
    "01": "Mercadoria para revenda", "02": "Uso ou Consumo", "03": "Serviços Tomados", "04": "Energia Elétrica",
    "05": "Aluguel", "06": "Locação de Máquinas", "07": "Armazenagem/Frete na venda", "08": "Vale-transporte/alimentação",
    "09": "Depreciação (F120)", "10": "Imobilizado (aquisição)", "11": "Amortização", "12": "Devolução de venda",
    "13": "Outras operações",
}
NOME_TIPO = {"0": "Original", "1": "Retificadora"}


def _num(v) -> float:
    """'1234,56' -> 1234.56 (SPED usa virgula decimal, sem separador de milhar)."""
    try:
        s = str(v).strip()
        return float(s.replace(",", ".")) if s else 0.0
    except ValueError:
        return 0.0


def _g(r: list, i: int) -> str:
    return r[i] if i < len(r) else ""


def _data(v: str) -> Optional[str]:
    """ddmmaaaa -> aaaa-mm-dd (ou None)."""
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) == 8:
        return f"{s[4:8]}-{s[2:4]}-{s[0:2]}"
    return None


def _so_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _num_doc(v) -> str:
    s = str(v or "").strip()
    d = re.sub(r"\D", "", s)
    return (d.lstrip("0") or "0") if d else s.upper()


def _linhas_do_arquivo(conteudo: bytes) -> list:
    """Registros do arquivo ate' o |9999|, cada um como lista de campos (indice 0 = codigo do registro)."""
    texto = conteudo.decode("latin-1", errors="replace")
    saida = []
    for ln in texto.split("\n"):
        ln = ln.rstrip("\r")
        if not ln.startswith("|"):
            continue
        saida.append(ln.split("|")[1:-1])
        if ln.startswith("|9999|"):
            break
    return saida


def sha256(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def _categoria_nfe(cfop: str) -> str:
    return "Combustível" if str(cfop) in CFOP_COMBUSTIVEL else "Uso ou Consumo"


def _categoria_servico(nat: str) -> str:
    return {"03": "Serviços Tomados", "05": "Aluguel", "06": "Locação de Máquinas", "04": "Demais Produtos (energia)",
            "02": "Uso ou Consumo"}.get(nat, NAT_BC_NOME.get(nat, "Outros créditos"))


def ler_sped(conteudo: bytes, nome_arquivo: str = "") -> dict:
    """Le o SPED e devolve {erro, cabecalho, creditos, receitas, apuracao, sha256, arquivo, contagem}.
    Nunca levanta excecao por arquivo ruim: devolve `erro` em linguagem simples."""
    base = {"erro": None, "cabecalho": {}, "creditos": pd.DataFrame(columns=COLUNAS_CREDITO),
            "receitas": pd.DataFrame(columns=COLUNAS_RECEITA), "apuracao": {}, "sha256": sha256(conteudo or b""),
            "arquivo": nome_arquivo, "contagem": {}}
    try:
        regs = _linhas_do_arquivo(conteudo or b"")
    except Exception as exc:  # pragma: no cover -- defensivo
        base["erro"] = f"Não consegui ler o arquivo ({type(exc).__name__})."
        return base
    if not regs or regs[0][0] != "0000":
        base["erro"] = "Este arquivo não parece um SPED EFD-Contribuições (.txt): não começa pelo registro 0000."
        return base
    r0 = regs[0]
    if len(r0) < 9 or not _data(_g(r0, 5)):
        base["erro"] = "Não reconheci o cabeçalho (registro 0000) do SPED."
        return base
    dt_ini, dt_fin = _data(_g(r0, 5)), _data(_g(r0, 6))
    base["cabecalho"] = {
        "tipo_escrit": _g(r0, 2), "tipo": NOME_TIPO.get(_g(r0, 2), _g(r0, 2)), "num_rec_anterior": _g(r0, 4),
        "dt_ini": dt_ini, "dt_fin": dt_fin, "competencia": f"{dt_ini[5:7]}/{dt_ini[0:4]}",
        "empresa_nome": _g(r0, 7), "cnpj": _so_digitos(_g(r0, 8)), "uf": _g(r0, 9),
    }
    # nome do arquivo (PISCOFINS_aaaammdd_aaaammdd_cnpj_Original_aaaammddhhmmss_hash.txt) -> data de envio
    m = re.search(r"_(Original|Retificadora)_(\d{14})_", nome_arquivo or "")
    if m:
        s = m.group(2)
        base["cabecalho"]["enviado_em_arquivo"] = f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}"

    part: dict = {}
    for r in regs:
        if r[0] == "0150":
            part[_g(r, 1)] = (_g(r, 2), _so_digitos(_g(r, 4)) or _so_digitos(_g(r, 5)))
    item_desc = {_g(r, 1): _g(r, 3) for r in regs if r[0] == "0200"}

    def forn(cod):
        n, c = part.get(cod, ("", ""))
        return n, c

    cred, rec = [], []
    cur: dict = {}
    c190_item = ""
    contagem: dict = {}
    for r in regs:
        t = r[0]
        contagem[t] = contagem.get(t, 0) + 1
        if t == "A100":
            nome, cnpj = forn(_g(r, 3))
            cur = {"ind": _g(r, 1), "cnpj": cnpj, "nome": nome, "num": _num_doc(_g(r, 7)), "data": _data(_g(r, 9)), "reg": "A170"}
        elif t == "A170" and cur.get("reg") == "A170":
            nat = _g(r, 6)
            lin = {"registro": "A170", "cnpj": cur["cnpj"], "numero": cur["num"], "data": cur["data"], "item": _g(r, 2),
                   "descricao": _g(r, 3), "cfop": "", "nat_bc": nat, "cst_pis": _g(r, 8), "base_pis": _num(_g(r, 9)),
                   "aliq_pis": _num(_g(r, 10)), "pis": _num(_g(r, 11)), "cst_cofins": _g(r, 12), "base_cofins": _num(_g(r, 13)),
                   "aliq_cofins": _num(_g(r, 14)), "cofins": _num(_g(r, 15)), "cod_cta": _g(r, 16)}
            if cur["ind"] == "1":
                rec.append({"registro": "A170", "cnpj": cur["cnpj"], "cliente": cur["nome"], "numero": cur["num"], "data": cur["data"],
                            "valor": _num(_g(r, 4)), "cst_pis": lin["cst_pis"], "base_pis": lin["base_pis"], "aliq_pis": lin["aliq_pis"],
                            "pis": lin["pis"], "cst_cofins": lin["cst_cofins"], "base_cofins": lin["base_cofins"],
                            "aliq_cofins": lin["aliq_cofins"], "cofins": lin["cofins"]})
            else:
                lin.update(fornecedor=cur["nome"], categoria=_categoria_servico(nat))
                cred.append(lin)
        elif t == "C100":
            nome, cnpj = forn(_g(r, 3))
            cur = {"ind": _g(r, 1), "cnpj": cnpj, "nome": nome, "num": _num_doc(_g(r, 7)), "data": _data(_g(r, 10)) or _data(_g(r, 9)),
                   "reg": "C170", "cod_sit": _g(r, 5)}
        elif t == "C170" and cur.get("reg") == "C170":
            lin = {"registro": "C170", "cnpj": cur["cnpj"], "numero": cur["num"], "data": cur["data"], "item": _g(r, 2),
                   "descricao": _g(r, 3), "cfop": _g(r, 10), "nat_bc": "", "cst_pis": _g(r, 24), "base_pis": _num(_g(r, 25)),
                   "aliq_pis": _num(_g(r, 26)), "pis": _num(_g(r, 29)), "cst_cofins": _g(r, 30), "base_cofins": _num(_g(r, 31)),
                   "aliq_cofins": _num(_g(r, 32)), "cofins": _num(_g(r, 35)), "cod_cta": _g(r, 36)}
            if cur["ind"] == "1":
                if lin["pis"] or lin["cofins"]:
                    rec.append({"registro": "C170", "cnpj": cur["cnpj"], "cliente": cur["nome"], "numero": cur["num"], "data": cur["data"],
                                "valor": _num(_g(r, 6)), "cst_pis": lin["cst_pis"], "base_pis": lin["base_pis"], "aliq_pis": lin["aliq_pis"],
                                "pis": lin["pis"], "cst_cofins": lin["cst_cofins"], "base_cofins": lin["base_cofins"],
                                "aliq_cofins": lin["aliq_cofins"], "cofins": lin["cofins"]})
            elif lin["cst_pis"] in CST_CREDITO or lin["cst_cofins"] in CST_CREDITO or lin["pis"] or lin["cofins"]:
                lin.update(fornecedor=cur["nome"], categoria=_categoria_nfe(lin["cfop"]))
                cred.append(lin)
        elif t == "C190":
            c190_item = _g(r, 3)
        elif t == "C191":      # consolidado: PIS (fornecedor + CST + CFOP); a linha do COFINS (C195) vem logo depois
            cnpj = _so_digitos(_g(r, 1))
            nome = next((n for n, c in part.values() if c == cnpj), "")
            cred.append({"registro": "C191", "cnpj": cnpj, "fornecedor": nome, "numero": "", "data": None, "item": c190_item,
                         "descricao": item_desc.get(c190_item, ""), "cfop": _g(r, 3), "nat_bc": "", "categoria": _categoria_nfe(_g(r, 3)),
                         "cst_pis": _g(r, 2), "base_pis": _num(_g(r, 6)), "aliq_pis": _num(_g(r, 7)), "pis": _num(_g(r, 10)),
                         "cst_cofins": "", "base_cofins": 0.0, "aliq_cofins": 0.0, "cofins": 0.0, "cod_cta": _g(r, 11)})
        elif t == "C195":
            # junta o COFINS na linha C191 correspondente (mesmo fornecedor + item + CFOP, a mais recente sem COFINS)
            cnpj, cfop = _so_digitos(_g(r, 1)), _g(r, 3)
            for lin in reversed(cred):
                if lin["registro"] == "C191" and lin["cnpj"] == cnpj and lin["cfop"] == cfop and lin["item"] == c190_item \
                        and not lin["cst_cofins"]:
                    lin.update(cst_cofins=_g(r, 2), base_cofins=_num(_g(r, 6)), aliq_cofins=_num(_g(r, 7)), cofins=_num(_g(r, 10)))
                    break
        elif t == "C500":
            nome, cnpj = forn(_g(r, 1))
            cur = {"ind": "0", "cnpj": cnpj, "nome": nome, "num": _num_doc(_g(r, 6)), "data": _data(_g(r, 7)), "reg": "C501"}
        elif t in ("C501", "C505") and cur.get("reg") == "C501":
            if t == "C501":
                cred.append({"registro": "C501", "cnpj": cur["cnpj"], "fornecedor": cur["nome"], "numero": cur["num"], "data": cur["data"],
                             "item": "ENERGIA", "descricao": "Energia elétrica", "cfop": "", "nat_bc": _g(r, 3),
                             "categoria": "Demais Produtos (energia)", "cst_pis": _g(r, 1), "base_pis": _num(_g(r, 4)),
                             "aliq_pis": _num(_g(r, 5)), "pis": _num(_g(r, 6)), "cst_cofins": "", "base_cofins": 0.0, "aliq_cofins": 0.0,
                             "cofins": 0.0, "cod_cta": _g(r, 7)})
            else:
                for lin in reversed(cred):
                    if lin["registro"] == "C501" and lin["numero"] == cur["num"] and lin["cnpj"] == cur["cnpj"] and not lin["cst_cofins"]:
                        lin.update(cst_cofins=_g(r, 1), base_cofins=_num(_g(r, 4)), aliq_cofins=_num(_g(r, 5)), cofins=_num(_g(r, 6)))
                        break
        elif t == "D100":
            nome, cnpj = forn(_g(r, 3))
            cur = {"ind": _g(r, 1), "cnpj": cnpj, "nome": nome, "num": _num_doc(_g(r, 8)), "data": _data(_g(r, 10)), "reg": "D101"}
        elif t in ("D101", "D105") and cur.get("reg") == "D101":
            if t == "D101":
                cred.append({"registro": "D101", "cnpj": cur["cnpj"], "fornecedor": cur["nome"], "numero": cur["num"], "data": cur["data"],
                             "item": "FRETE", "descricao": "Frete", "cfop": "", "nat_bc": _g(r, 4), "categoria": "Fretes",
                             "cst_pis": _g(r, 3), "base_pis": _num(_g(r, 5)), "aliq_pis": _num(_g(r, 6)), "pis": _num(_g(r, 7)),
                             "cst_cofins": "", "base_cofins": 0.0, "aliq_cofins": 0.0, "cofins": 0.0, "cod_cta": _g(r, 8)})
            else:
                for lin in reversed(cred):
                    if lin["registro"] == "D101" and lin["numero"] == cur["num"] and lin["cnpj"] == cur["cnpj"] and not lin["cst_cofins"]:
                        lin.update(cst_cofins=_g(r, 3), base_cofins=_num(_g(r, 5)), aliq_cofins=_num(_g(r, 6)), cofins=_num(_g(r, 7)))
                        break
        elif t == "F120":
            cred.append({"registro": "F120", "cnpj": "", "fornecedor": "", "numero": "", "data": None, "item": _g(r, 2),
                         "descricao": _g(r, 17), "cfop": "", "nat_bc": _g(r, 1) or "09", "categoria": "Depreciação (F120)",
                         "cst_pis": _g(r, 7), "base_pis": _num(_g(r, 8)), "aliq_pis": _num(_g(r, 9)), "pis": _num(_g(r, 10)),
                         "cst_cofins": _g(r, 11), "base_cofins": _num(_g(r, 12)), "aliq_cofins": _num(_g(r, 13)), "cofins": _num(_g(r, 14)),
                         "cod_cta": _g(r, 15)})
        elif t == "F130":
            cred.append({"registro": "F130", "cnpj": "", "fornecedor": "", "numero": "", "data": None, "item": _g(r, 2),
                         "descricao": _g(r, 20), "cfop": "", "nat_bc": _g(r, 1) or "10", "categoria": "Imobilizado (aquisição)",
                         "cst_pis": _g(r, 10), "base_pis": _num(_g(r, 11)), "aliq_pis": _num(_g(r, 12)), "pis": _num(_g(r, 13)),
                         "cst_cofins": _g(r, 14), "base_cofins": _num(_g(r, 15)), "aliq_cofins": _num(_g(r, 16)), "cofins": _num(_g(r, 17)),
                         "cod_cta": _g(r, 18)})
    base["contagem"] = contagem
    df = pd.DataFrame(cred, columns=COLUNAS_CREDITO)
    df["chave"] = _chaves(df)
    base["creditos"] = df
    base["receitas"] = pd.DataFrame(rec, columns=COLUNAS_RECEITA)
    base["apuracao"] = _apuracao(regs)
    if df.empty and not base["apuracao"].get("m100"):
        base["erro"] = "O SPED não tem nenhum crédito de PIS/COFINS (o arquivo está vazio de créditos)."
    return base


def _chaves(df: pd.DataFrame) -> pd.Series:
    """Chave de conferencia da linha: CNPJ|numero (documento) ou CNPJ|*|item|cfop (consolidado) ou F|registro|item (bens)."""
    def k(r):
        if r["registro"] in ("F120", "F130"):
            return f"{r['registro']}|{r['item']}|{str(r['descricao'])[:40]}"
        if r["numero"]:
            return f"{r['cnpj']}|{r['numero']}"
        return f"{r['cnpj']}|*|{r['item']}|{r['cfop']}"
    return df.apply(k, axis=1) if len(df) else pd.Series([], dtype=str)


def _apuracao(regs: list) -> dict:
    """Bloco M + retencoes (F600). Valores em reais; listas para registros que se repetem."""
    ap: dict = {"m100": [], "m105": [], "m500": [], "m505": [], "m210": [], "m610": [], "f600": []}
    for r in regs:
        t = r[0]
        if t in ("M100", "M500"):
            ap[t.lower()].append({"cod_cred": _g(r, 1), "base": _num(_g(r, 3)), "aliq": _num(_g(r, 4)), "credito": _num(_g(r, 7)),
                                  "ajuste_acres": _num(_g(r, 8)), "ajuste_reduc": _num(_g(r, 9)), "disponivel": _num(_g(r, 11)),
                                  "ind_desc": _g(r, 12), "descontado": _num(_g(r, 13)), "saldo": _num(_g(r, 14))})
        elif t in ("M105", "M505"):
            ap[t.lower()].append({"nat_bc": _g(r, 1), "cst": _g(r, 2), "base_total": _num(_g(r, 3)), "base_cumulativa": _num(_g(r, 4)),
                                  "base_nao_cumulativa": _num(_g(r, 5)), "base_credito": _num(_g(r, 6))})
        elif t in ("M200", "M600"):
            ap[t.lower()] = {"contribuicao_nc": _num(_g(r, 1)), "credito_descontado": _num(_g(r, 2)), "credito_anterior": _num(_g(r, 3)),
                             "nc_devida": _num(_g(r, 4)), "retencao_nc": _num(_g(r, 5)), "outras_deducoes_nc": _num(_g(r, 6)),
                             "nc_a_recolher": _num(_g(r, 7)), "contribuicao_cum": _num(_g(r, 8)), "retencao_cum": _num(_g(r, 9)),
                             "outras_deducoes_cum": _num(_g(r, 10)), "cum_a_recolher": _num(_g(r, 11)), "total_a_recolher": _num(_g(r, 12))}
        elif t in ("M205", "M605"):
            ap.setdefault(t.lower(), []).append({"campo": _g(r, 1), "cod_receita": _g(r, 2), "valor": _num(_g(r, 3))})
        elif t in ("M210", "M610"):
            ap[t.lower()].append({"cod_cont": _g(r, 1), "receita_bruta": _num(_g(r, 2)), "base": _num(_g(r, 3)), "base_ajustada": _num(_g(r, 6)),
                                  "aliq": _num(_g(r, 7)), "contribuicao": _num(_g(r, 10)), "contribuicao_periodo": _num(_g(r, 15))})
        elif t == "F600":
            ap["f600"].append({"data": _data(_g(r, 2)), "base": _num(_g(r, 3)), "retido": _num(_g(r, 4)), "cod_receita": _g(r, 5),
                               "cnpj_fonte": _so_digitos(_g(r, 7)), "pis": _num(_g(r, 8)), "cofins": _num(_g(r, 9))})
    ap["m200"] = ap.get("m200", {})
    ap["m600"] = ap.get("m600", {})
    return ap


# ─────────────────────────────────────────────
#  RESUMOS (puros)
# ─────────────────────────────────────────────
def resumo_apuracao(ap: dict) -> dict:
    """Numeros-chave do mes (PIS e COFINS) prontos para KPI/relatorio."""
    def soma(lst, c):
        return round(sum(float(x.get(c, 0) or 0) for x in (lst or [])), 2)
    m2, m6 = ap.get("m200", {}) or {}, ap.get("m600", {}) or {}
    rec_nc = sum(x["receita_bruta"] for x in ap.get("m210", []) if x["cod_cont"] == "01")
    rec_cum = sum(x["receita_bruta"] for x in ap.get("m210", []) if x["cod_cont"] != "01")
    return {
        "credito_pis": soma(ap.get("m100"), "credito"), "credito_cofins": soma(ap.get("m500"), "credito"),
        "saldo_pis": soma(ap.get("m100"), "saldo"), "saldo_cofins": soma(ap.get("m500"), "saldo"),
        "debito_nc_pis": round(m2.get("contribuicao_nc", 0), 2), "debito_nc_cofins": round(m6.get("contribuicao_nc", 0), 2),
        "debito_cum_pis": round(m2.get("contribuicao_cum", 0), 2), "debito_cum_cofins": round(m6.get("contribuicao_cum", 0), 2),
        "credito_usado_pis": round(m2.get("credito_descontado", 0), 2), "credito_usado_cofins": round(m6.get("credito_descontado", 0), 2),
        "credito_anterior_pis": round(m2.get("credito_anterior", 0), 2), "credito_anterior_cofins": round(m6.get("credito_anterior", 0), 2),
        "retencao_nc_pis": round(m2.get("retencao_nc", 0), 2), "retencao_nc_cofins": round(m6.get("retencao_nc", 0), 2),
        "a_recolher_pis": round(m2.get("total_a_recolher", 0), 2), "a_recolher_cofins": round(m6.get("total_a_recolher", 0), 2),
        "receita_nc": round(rec_nc, 2), "receita_cum": round(rec_cum, 2),
        "retencao_f600_pis": soma(ap.get("f600"), "pis"), "retencao_f600_cofins": soma(ap.get("f600"), "cofins"),
    }


def bases_por_natureza(ap: dict) -> pd.DataFrame:
    """Base de credito por natureza (M105 PIS / M505 COFINS) e credito = base x aliquota do M100/M500."""
    linhas = []
    for pref, cred_key, nome in (("m105", "m100", "pis"), ("m505", "m500", "cofins")):
        aliq = (ap.get(cred_key) or [{}])[0].get("aliq", 1.65 if nome == "pis" else 7.6)
        for x in ap.get(pref, []):
            linhas.append({"nat_bc": x["nat_bc"], "tributo": nome, "base": x["base_credito"], "credito": round(x["base_credito"] * aliq / 100, 2)})
    if not linhas:
        return pd.DataFrame(columns=["nat_bc", "natureza", "base", "pis", "cofins"])
    d = pd.DataFrame(linhas)
    piv = d.pivot_table(index="nat_bc", columns="tributo", values=["base", "credito"], aggfunc="sum", fill_value=0)
    out = pd.DataFrame({"nat_bc": piv.index})
    out["natureza"] = out["nat_bc"].map(lambda n: NAT_BC_NOME.get(n, n))
    out["base"] = [float(piv.loc[n, ("base", "pis")]) if ("base", "pis") in piv.columns else 0.0 for n in piv.index]
    out["pis"] = [float(piv.loc[n, ("credito", "pis")]) if ("credito", "pis") in piv.columns else 0.0 for n in piv.index]
    out["cofins"] = [float(piv.loc[n, ("credito", "cofins")]) if ("credito", "cofins") in piv.columns else 0.0 for n in piv.index]
    return out.reset_index(drop=True)


# ─────────────────────────────────────────────
#  RESUMOS GUARDADOS NO ENVIO / NO HISTORICO (puros)
# ─────────────────────────────────────────────
def por_categoria(cred: pd.DataFrame) -> dict:
    if cred is None or cred.empty:
        return {}
    g = cred.groupby("categoria").agg(pis=("pis", "sum"), cofins=("cofins", "sum"), linhas=("pis", "count"))
    return {k: {"pis": round(float(r.pis), 2), "cofins": round(float(r.cofins), 2), "linhas": int(r.linhas)} for k, r in g.iterrows()}


def por_natureza(ap: dict) -> dict:
    b = bases_por_natureza(ap)
    return {r.nat_bc: {"natureza": r.natureza, "base": round(r.base, 2), "pis": round(r.pis, 2), "cofins": round(r.cofins, 2)}
            for r in b.itertuples(index=False)} if not b.empty else {}


def dif_fornecedores(orig: pd.DataFrame, apont: pd.DataFrame, topo: int = 30) -> list:
    """Maiores diferencas de credito por fornecedor entre a versao original e a apontada (so' fornecedores identificados)."""
    def agg(d):
        if d is None or d.empty:
            return pd.DataFrame(columns=["pis", "cofins", "fornecedor"])
        d = d[d["cnpj"] != ""]
        return d.groupby("cnpj").agg(pis=("pis", "sum"), cofins=("cofins", "sum"), fornecedor=("fornecedor", "first"))
    o, a = agg(orig), agg(apont)
    t = a.join(o, how="outer", lsuffix="_ap", rsuffix="_or").fillna(0)
    if t.empty:
        return []
    t["fornecedor"] = t["fornecedor_ap"].where(t["fornecedor_ap"] != 0, t["fornecedor_or"])
    t["dif_pis"] = (t["pis_ap"] - t["pis_or"]).round(2)
    t["dif_cofins"] = (t["cofins_ap"] - t["cofins_or"]).round(2)
    t = t.reindex(t["dif_pis"].abs().sort_values(ascending=False).index).head(topo)
    return [{"cnpj": i, "fornecedor": str(r.fornecedor), "dif_pis": float(r.dif_pis), "dif_cofins": float(r.dif_cofins),
             "pis_original": round(float(r.pis_or), 2), "pis_apontado": round(float(r.pis_ap), 2)} for i, r in t.iterrows()]
