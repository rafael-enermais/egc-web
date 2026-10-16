# -*- coding: utf-8 -*-
"""
EGC | Creditos PIS/COFINS -- banco (blocos 28, 29 e 30 do schema.sql).  (v0.52.0, 16/10/2026)

Cada upload (SPED ou Detalhamento) e' uma VERSAO do mes (empresa+competencia) e NUNCA e' apagada nem some da tela. Uma versao por mes e'
a APONTADA (a que vale para dashboard e conferencia padrao); as demais ficam guardadas como "outras versoes" e podem ser conferidas,
comparadas e promovidas a apontada. (Tecnicamente: arquivado_em IS NULL = apontada; preenchido = outra versao.)
As edicoes da contadora (cred_ajuste) ficam presas a versao do SPED em que foram feitas (envio_ref) e tem trilha (cred_ajuste_log).
O historico jan-jul (cred_historico) e' carga fixa; o dashboard junta historico + versoes apontadas.
(v0.52.0) Cada mes pode ter varias FONTES (SPED enviados + carga fixa); o dashboard usa a "oficial" escolhida pela contadora (cred_mes,
bloco 30) ou, se ela nao escolheu, a regra padrao (a apontada). Tudo e' sugestao/organizacao: nada trava.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Optional

import pandas as pd
from psycopg2.extras import Json, execute_values

import nf_sienge
import sped_contribuicoes as sc

TABELAS = ("cred_sped_envio", "cred_sped_linha", "cred_sped_receita", "cred_det_envio", "cred_det_linha", "cred_ajuste",
           "cred_ajuste_log", "cred_historico")


class Bloco28Indisponivel(Exception):
    """O bloco 28 do schema.sql ainda nao foi rodado neste banco."""


def tem_bloco28(conn) -> bool:
    try:
        return all(nf_sienge._tabela_existe(conn, f"egc.{t}") is True for t in TABELAS)
    except Exception:
        return False


def tem_bloco29(conn) -> bool:
    """O bloco 29 (envio_ref nas edicoes) ja' foi rodado neste banco?"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = 'egc' AND table_name = 'cred_ajuste' AND column_name = 'envio_ref'")
            return cur.fetchone() is not None
    except Exception:
        return False


@contextmanager
def _transacao(conn):
    with nf_sienge._transacao(conn):
        yield


def _f(v) -> float:
    try:
        x = float(v)
        return 0.0 if x != x else x
    except (TypeError, ValueError):
        return 0.0


def _nn(v):
    """NaN/NaT/'' -> None (para o banco)."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, str) and v == "":
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _records(df: pd.DataFrame) -> list:
    """DataFrame -> lista de dicts SEM NaN (JSON do Postgres nao aceita NaN)."""
    if df is None or df.empty:
        return []
    return df.astype(object).where(df.notna(), None).to_dict("records")


# ─────────────────────────────────────────────
#  RESUMOS (puros) -- calculados na gravacao e guardados no envio
# ─────────────────────────────────────────────
# resumos puros (ficam em sped_contribuicoes para o script de carga do historico rodar sem banco)
por_categoria = sc.por_categoria
por_natureza = sc.por_natureza
dif_fornecedores = sc.dif_fornecedores


# ─────────────────────────────────────────────
#  ENVIOS
# ─────────────────────────────────────────────
def _versao_apontada(cur, tab: str, empresa_codigo: str, competencia: str):
    cur.execute(f"SELECT id FROM egc.{tab} WHERE empresa_codigo = %s AND competencia = %s AND arquivado_em IS NULL", (empresa_codigo, competencia))
    r = cur.fetchone()
    return str(r[0]) if r else None


def _marcar_versao(cur, tab: str, empresa_codigo: str, competencia: str, novo: str, apontar: bool, usuario: str) -> None:
    """Depois de inserir a versao `novo`: ou ela vira a APONTADA (a anterior continua guardada como outra versao) ou fica guardada sem apontar."""
    if apontar:
        cur.execute(f"UPDATE egc.{tab} SET arquivado_em = now(), arquivado_por = %s, "
                    "arquivado_motivo = 'Outra versão do mês foi apontada' "
                    "WHERE empresa_codigo = %s AND competencia = %s AND arquivado_em IS NULL AND id <> %s", (usuario, empresa_codigo, competencia, novo))
    else:
        cur.execute(f"UPDATE egc.{tab} SET arquivado_em = now(), arquivado_por = %s, arquivado_motivo = 'Guardada sem apontar (a apontada continua a mesma)' "
                    "WHERE id = %s", (usuario, novo))


def gravar_sped(conn, empresa_codigo: str, sped: dict, arquivo_nome: str, usuario: str, apontar: bool = True) -> str:
    """Grava o SPED como mais uma VERSAO do mes (nada e' apagado). `apontar=True` (padrao): vira a apontada e a anterior continua guardada.
    `apontar=False`: fica guardada como outra versao (se o mes ainda nao tem apontada, vira a apontada). Devolve o id."""
    cab = sped["cabecalho"]
    cred, rec = sped["creditos"], sped["receitas"]
    novo = str(uuid.uuid4())
    resumo = sc.resumo_apuracao(sped["apuracao"])
    resumo["linhas_credito"] = int(len(cred))
    resumo["linhas_receita"] = int(len(rec))
    with _transacao(conn):
        with conn.cursor() as cur:
            apontar = apontar or _versao_apontada(cur, "cred_sped_envio", empresa_codigo, cab["competencia"]) is None
            cur.execute(
                "INSERT INTO egc.cred_sped_envio (id, empresa_codigo, competencia, sped_tipo, arquivo_nome, sha256, cnpj, num_rec_anterior, "
                "enviado_arquivo_em, resumo, apuracao, por_categoria, por_natureza, enviado_por) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (novo, empresa_codigo, cab["competencia"], cab.get("tipo"), arquivo_nome, sped.get("sha256"), cab.get("cnpj"),
                 cab.get("num_rec_anterior") or None, cab.get("enviado_em_arquivo"), Json(resumo), Json(sped["apuracao"]),
                 Json(por_categoria(cred)), Json(por_natureza(sped["apuracao"])), usuario))
            if not cred.empty:
                execute_values(cur,
                    "INSERT INTO egc.cred_sped_linha (envio_id, registro, categoria, cnpj, fornecedor, numero, data, item, descricao, cfop, nat_bc, "
                    "cst_pis, base_pis, aliq_pis, pis, cst_cofins, base_cofins, aliq_cofins, cofins, cod_cta, chave) VALUES %s",
                    [(novo, r.registro, r.categoria, _nn(r.cnpj), _nn(r.fornecedor), _nn(r.numero), _nn(r.data), _nn(r.item), _nn(str(r.descricao)[:300]),
                      _nn(r.cfop), _nn(r.nat_bc), _nn(r.cst_pis), _f(r.base_pis), _f(r.aliq_pis), _f(r.pis), _nn(r.cst_cofins), _f(r.base_cofins),
                      _f(r.aliq_cofins), _f(r.cofins), _nn(r.cod_cta), r.chave) for r in cred.itertuples(index=False)], page_size=500)
            if not rec.empty:
                execute_values(cur,
                    "INSERT INTO egc.cred_sped_receita (envio_id, registro, cnpj, cliente, numero, data, valor, cst_pis, base_pis, aliq_pis, pis, "
                    "cst_cofins, base_cofins, aliq_cofins, cofins) VALUES %s",
                    [(novo, r.registro, _nn(r.cnpj), _nn(r.cliente), _nn(r.numero), _nn(r.data), _f(r.valor), _nn(r.cst_pis), _f(r.base_pis),
                      _f(r.aliq_pis), _f(r.pis), _nn(r.cst_cofins), _f(r.base_cofins), _f(r.aliq_cofins), _f(r.cofins))
                     for r in rec.itertuples(index=False)], page_size=500)
            _marcar_versao(cur, "cred_sped_envio", empresa_codigo, cab["competencia"], novo, apontar, usuario)
    return novo


def gravar_detalhamento(conn, empresa_codigo: str, det: dict, arquivo_nome: str, usuario: str, competencia: Optional[str] = None,
                        apontar: bool = True) -> str:
    comp = competencia or det.get("competencia")
    if not comp:
        raise ValueError("Não consegui descobrir a competência (mês/ano) do Detalhamento.")
    linhas = det["linhas"]
    novo = str(uuid.uuid4())
    with _transacao(conn):
        with conn.cursor() as cur:
            apontar = apontar or _versao_apontada(cur, "cred_det_envio", empresa_codigo, comp) is None
            cur.execute(
                "INSERT INTO egc.cred_det_envio (id, empresa_codigo, competencia, arquivo_nome, sha256, total_linhas, resumo, debitos, "
                "reconciliacao, enviado_por) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (novo, empresa_codigo, comp, arquivo_nome, det.get("sha256"), int(len(linhas)),
                 Json(_records(det["resumo"])), Json(_records(det["debitos"])), Json(_records(det["reconciliacao"])), usuario))
            if not linhas.empty:
                execute_values(cur,
                    "INSERT INTO egc.cred_det_linha (envio_id, categoria, cnpj, fornecedor, numero, especie, data_emissao, natureza, produto, "
                    "descricao, valor_total, base, cst_pis, aliq_pis, pis, cst_cofins, aliq_cofins, cofins, tipo_credito, linha_origem) VALUES %s",
                    [(novo, r.categoria, _nn(r.cnpj), _nn(r.fornecedor), _nn(r.numero), _nn(r.especie), _nn(r.data_emissao), _nn(r.natureza),
                      _nn(r.produto), _nn(str(r.descricao)[:300]), _f(r.valor_total), _f(r.base), _nn(r.cst_pis), _f(r.aliq_pis), _f(r.pis),
                      _nn(r.cst_cofins), _f(r.aliq_cofins), _f(r.cofins), _nn(r.tipo_credito), int(r.linha_origem))
                     for r in linhas.itertuples(index=False)], page_size=500)
            _marcar_versao(cur, "cred_det_envio", empresa_codigo, comp, novo, apontar, usuario)
    return novo


def listar_envios(conn, empresa_codigo: Optional[str] = None, incluir_arquivados: bool = False) -> list:
    """SPED e Detalhamento juntos: [{tipo:'SPED'|'DETALHAMENTO', id, empresa_codigo, competencia, arquivo_nome, enviado_por, enviado_em, ...}]"""
    saida: list = []
    for tipo, tab, extra in (("SPED", "cred_sped_envio", "sped_tipo AS detalhe, (resumo->>'linhas_credito')::int AS linhas"),
                             ("DETALHAMENTO", "cred_det_envio", "NULL::text AS detalhe, total_linhas AS linhas")):
        where, params = [], []
        if empresa_codigo:
            where.append("empresa_codigo = %s")
            params.append(empresa_codigo)
        if not incluir_arquivados:
            where.append("arquivado_em IS NULL")
        sql = (f"SELECT id, empresa_codigo, competencia, arquivo_nome, enviado_por, enviado_em, arquivado_em, arquivado_por, arquivado_motivo, {extra} "
               f"FROM egc.{tab}" + (" WHERE " + " AND ".join(where) if where else ""))
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                d = dict(zip(cols, r))
                d["id"], d["tipo"] = str(d["id"]), tipo
                saida.append(d)
    saida.sort(key=lambda d: (d["empresa_codigo"], _chave_comp(d["competencia"]), d["enviado_em"]), reverse=True)
    return saida


def _chave_comp(c: str) -> str:
    """'MM/AAAA' -> 'AAAA-MM' (ordenavel)."""
    try:
        m, a = str(c).split("/")
        return f"{a}-{m}"
    except ValueError:
        return str(c)


def _tab(tipo: str) -> str:
    return "cred_sped_envio" if tipo == "SPED" else "cred_det_envio"


def sha_vigente(conn, tipo: str, empresa_codigo: str, competencia: str) -> Optional[str]:
    """SHA-256 do arquivo do envio vigente (para avisar 'esse mesmo arquivo ja foi enviado')."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT sha256 FROM egc.{_tab(tipo)} WHERE empresa_codigo = %s AND competencia = %s AND arquivado_em IS NULL",
                    (empresa_codigo, competencia))
        r = cur.fetchone()
    return r[0] if r else None


def arquivar_envio(conn, tipo: str, envio_id: str, usuario: str, motivo: Optional[str] = None) -> None:
    with conn.cursor() as cur:
        cur.execute(f"UPDATE egc.{_tab(tipo)} SET arquivado_em = now(), arquivado_por = %s, arquivado_motivo = %s "
                    "WHERE id = %s AND arquivado_em IS NULL", (usuario, motivo or "Arquivado pela contadora", envio_id))


def restaurar_envio(conn, tipo: str, envio_id: str, usuario: str) -> None:
    """Restaura um envio arquivado; recusa se o mes ja' tem outro vigente."""
    tab = _tab(tipo)
    with conn.cursor() as cur:
        cur.execute(f"SELECT empresa_codigo, competencia FROM egc.{tab} WHERE id = %s", (envio_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Envio não encontrado.")
        cur.execute(f"SELECT 1 FROM egc.{tab} WHERE empresa_codigo = %s AND competencia = %s AND arquivado_em IS NULL AND id <> %s",
                    (row[0], row[1], envio_id))
        if cur.fetchone():
            raise ValueError("Esse mês já tem outro envio vigente. Arquive o vigente antes de restaurar este.")
        cur.execute(f"UPDATE egc.{tab} SET arquivado_em = NULL, arquivado_por = NULL, arquivado_motivo = NULL WHERE id = %s", (envio_id,))


# ─────────────────────────────────────────────
#  VERSOES DO MES (v0.51.0)
# ─────────────────────────────────────────────
def listar_versoes(conn, tipo: str, empresa_codigo: str, competencia: str) -> list:
    """Todas as versoes do mes (SPED ou DETALHAMENTO), da mais antiga para a mais nova:
    [{id, n, tipo, sped_tipo, arquivo_nome, sha256, enviado_por, enviado_em, apontada, rotulo, resumo}]. `n` = 1, 2, 3... na ordem de envio."""
    sped = tipo == "SPED"
    extra = "sped_tipo, resumo" if sped else "NULL::text AS sped_tipo, resumo"
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, {extra}, arquivo_nome, sha256, enviado_por, enviado_em, arquivado_em FROM egc.{_tab(tipo)} "
                    "WHERE empresa_codigo = %s AND competencia = %s ORDER BY enviado_em, id", (empresa_codigo, competencia))
        rows = cur.fetchall()
    saida = []
    for i, (eid, st_, resumo, arq, sha, por, em, arq_em) in enumerate(rows, 1):
        nome = (str(st_).strip() if st_ else "Detalhamento")
        v = {"id": str(eid), "n": i, "tipo": tipo, "sped_tipo": st_, "arquivo_nome": arq, "sha256": sha, "enviado_por": por, "enviado_em": em,
             "apontada": arq_em is None, "resumo": resumo or {}}
        v["rotulo"] = f"{i}ª · {nome} · {arq} · enviado por {por} em {_hora(em)}" + (" · APONTADA" if v["apontada"] else "")
        saida.append(v)
    return saida


def _hora(ts) -> str:
    try:
        return ts.strftime("%d/%m/%Y %H:%M")
    except AttributeError:
        return str(ts)


def envio_com_sha(conn, tipo: str, empresa_codigo: str, competencia: str, sha256: str) -> Optional[dict]:
    """Se este MESMO arquivo ja' foi enviado neste mes (em qualquer versao): devolve a versao (com 'n' e 'apontada')."""
    for v in listar_versoes(conn, tipo, empresa_codigo, competencia):
        if sha256 and v["sha256"] == sha256:
            return v
    return None


def definir_apontada(conn, tipo: str, envio_id: str, usuario: str) -> bool:
    """Passa a ser a versao APONTADA do mes (a que estava apontada continua guardada como outra versao). Troca numa transacao so'.
    Devolve False se ja' era a apontada."""
    tab = _tab(tipo)
    with _transacao(conn):
        with conn.cursor() as cur:
            cur.execute(f"SELECT empresa_codigo, competencia, arquivado_em FROM egc.{tab} WHERE id = %s", (envio_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Envio não encontrado.")
            if row[2] is None:
                return False
            cur.execute(f"UPDATE egc.{tab} SET arquivado_em = now(), arquivado_por = %s, arquivado_motivo = 'Outra versão do mês foi apontada' "
                        "WHERE empresa_codigo = %s AND competencia = %s AND arquivado_em IS NULL AND id <> %s", (usuario, row[0], row[1], envio_id))
            cur.execute(f"UPDATE egc.{tab} SET arquivado_em = NULL, arquivado_por = NULL, arquivado_motivo = NULL WHERE id = %s", (envio_id,))
    return True


def comparar_versoes_sped(conn, empresa_codigo: str, competencia: str) -> pd.DataFrame:
    """Tabela para comparar as versoes do SPED do mes (do resumo guardado em cada envio): uma linha por versao, numeros em R$."""
    linhas = []
    for v in listar_versoes(conn, "SPED", empresa_codigo, competencia):
        r = v["resumo"]
        linhas.append({"versao": f"{v['n']}ª · {v['sped_tipo'] or ''}" + (" (apontada)" if v["apontada"] else ""), "id": v["id"], "apontada": v["apontada"],
                       "credito_pis": _f(r.get("credito_pis")), "credito_cofins": _f(r.get("credito_cofins")),
                       "debito_nc_pis": _f(r.get("debito_nc_pis")), "debito_nc_cofins": _f(r.get("debito_nc_cofins")),
                       "saldo_pis": _f(r.get("saldo_pis")), "saldo_cofins": _f(r.get("saldo_cofins")),
                       "a_recolher_pis": _f(r.get("a_recolher_pis")), "a_recolher_cofins": _f(r.get("a_recolher_cofins")),
                       "linhas_credito": int(_f(r.get("linhas_credito"))), "arquivo": v["arquivo_nome"], "enviado_em": v["enviado_em"],
                       "enviado_por": v["enviado_por"]})
    return pd.DataFrame(linhas)


def comparar_categorias_sped(conn, empresa_codigo: str, competencia: str) -> pd.DataFrame:
    """Credito (PIS+COFINS) por categoria x versao do SPED do mes: colunas = versoes (na ordem de envio)."""
    dados: dict = {}
    cols = []
    with conn.cursor() as cur:
        cur.execute("SELECT id, por_categoria FROM egc.cred_sped_envio WHERE empresa_codigo = %s AND competencia = %s", (empresa_codigo, competencia))
        pc = {str(r[0]): (r[1] or {}) for r in cur.fetchall()}
    for v in listar_versoes(conn, "SPED", empresa_codigo, competencia):
        col = f"{v['n']}ª {v['sped_tipo'] or ''}".strip() + (" (apontada)" if v["apontada"] else "")
        cols.append(col)
        for cat, vals in pc.get(v["id"], {}).items():
            dados.setdefault(cat, {})[col] = _f((vals or {}).get("pis")) + _f((vals or {}).get("cofins"))
    if not dados:
        return pd.DataFrame()
    df = pd.DataFrame.from_dict(dados, orient="index").reindex(columns=cols).fillna(0.0)
    df.index.name = "categoria"
    return df.reset_index()


# ─────────────────────────────────────────────
#  LEITURA DO PERIODO (reconstroi os dicionarios usados pela conferencia)
# ─────────────────────────────────────────────
def competencias(conn, empresa_codigo: str) -> list:
    """Competencias com algum SPED ou Detalhamento enviado, em qualquer versao (mais recente primeiro)."""
    with conn.cursor() as cur:
        cur.execute("SELECT competencia FROM egc.cred_sped_envio WHERE empresa_codigo = %s UNION "
                    "SELECT competencia FROM egc.cred_det_envio WHERE empresa_codigo = %s", (empresa_codigo, empresa_codigo))
        comps = [r[0] for r in cur.fetchall()]
    return sorted(set(comps), key=_chave_comp, reverse=True)


def carregar_sped(conn, empresa_codigo: str, competencia: str, envio_id: Optional[str] = None) -> Optional[dict]:
    """O SPED APONTADO do mes (padrao) ou uma versao especifica (`envio_id`). 'apontada' vem no dicionario."""
    filtro, params = ("id = %s", (envio_id,)) if envio_id else ("arquivado_em IS NULL", ())
    with conn.cursor() as cur:
        cur.execute("SELECT id, sped_tipo, arquivo_nome, sha256, cnpj, num_rec_anterior, enviado_arquivo_em, resumo, apuracao, por_categoria, "
                    "por_natureza, enviado_por, enviado_em, arquivado_em FROM egc.cred_sped_envio "
                    f"WHERE empresa_codigo = %s AND competencia = %s AND {filtro}", (empresa_codigo, competencia) + params)
        r = cur.fetchone()
    if not r:
        return None
    eid = str(r[0])
    with conn.cursor() as cur:
        cur.execute("SELECT registro, cnpj, fornecedor, numero, data, item, descricao, cfop, nat_bc, categoria, cst_pis, base_pis::float8, "
                    "aliq_pis::float8, pis::float8, cst_cofins, base_cofins::float8, aliq_cofins::float8, cofins::float8, cod_cta, chave "
                    "FROM egc.cred_sped_linha WHERE envio_id = %s ORDER BY id", (eid,))
        cred = pd.DataFrame(cur.fetchall(), columns=sc.COLUNAS_CREDITO + ["chave"])
        cur.execute("SELECT registro, cnpj, cliente, numero, data, valor::float8, cst_pis, base_pis::float8, aliq_pis::float8, pis::float8, "
                    "cst_cofins, base_cofins::float8, aliq_cofins::float8, cofins::float8 FROM egc.cred_sped_receita WHERE envio_id = %s ORDER BY id", (eid,))
        rec = pd.DataFrame(cur.fetchall(), columns=sc.COLUNAS_RECEITA)
    for c in ("cnpj", "fornecedor", "numero", "item", "descricao", "cfop", "nat_bc", "cst_pis", "cst_cofins", "cod_cta"):
        cred[c] = cred[c].fillna("")
    for c in ("cnpj", "cliente", "numero", "cst_pis", "cst_cofins"):
        rec[c] = rec[c].fillna("")
    return {"envio_id": eid, "erro": None, "creditos": cred, "receitas": rec, "apuracao": r[8] or {}, "resumo": r[7] or {},
            "por_categoria": r[9] or {}, "por_natureza": r[10] or {}, "sha256": r[3], "arquivo": r[2],
            "cabecalho": {"tipo": r[1], "cnpj": r[4], "num_rec_anterior": r[5], "enviado_arquivo_em": r[6], "competencia": competencia},
            "enviado_por": r[11], "enviado_em": r[12], "apontada": r[13] is None}


def carregar_detalhamento(conn, empresa_codigo: str, competencia: str, envio_id: Optional[str] = None) -> Optional[dict]:
    """O Detalhamento APONTADO do mes (padrao) ou uma versao especifica (`envio_id`)."""
    filtro, params = ("id = %s", (envio_id,)) if envio_id else ("arquivado_em IS NULL", ())
    with conn.cursor() as cur:
        cur.execute("SELECT id, arquivo_nome, sha256, resumo, debitos, reconciliacao, enviado_por, enviado_em, arquivado_em FROM egc.cred_det_envio "
                    f"WHERE empresa_codigo = %s AND competencia = %s AND {filtro}", (empresa_codigo, competencia) + params)
        r = cur.fetchone()
    if not r:
        return None
    eid = str(r[0])
    cols = ["categoria", "cnpj", "fornecedor", "numero", "especie", "data_emissao", "natureza", "produto", "descricao", "valor_total", "base",
            "cst_pis", "aliq_pis", "pis", "cst_cofins", "aliq_cofins", "cofins", "tipo_credito", "linha_origem"]
    with conn.cursor() as cur:
        cur.execute("SELECT categoria, cnpj, fornecedor, numero, especie, data_emissao, natureza, produto, descricao, valor_total::float8, base::float8, "
                    "cst_pis, aliq_pis::float8, pis::float8, cst_cofins, aliq_cofins::float8, cofins::float8, tipo_credito, linha_origem "
                    "FROM egc.cred_det_linha WHERE envio_id = %s ORDER BY id", (eid,))
        linhas = pd.DataFrame(cur.fetchall(), columns=cols)
    for c in ("cnpj", "fornecedor", "numero", "especie", "natureza", "produto", "descricao", "cst_pis", "cst_cofins", "tipo_credito"):
        linhas[c] = linhas[c].fillna("")
    return {"envio_id": eid, "erro": None, "competencia": competencia, "linhas": linhas, "resumo": pd.DataFrame(r[3] or []),
            "debitos": pd.DataFrame(r[4] or []), "reconciliacao": pd.DataFrame(r[5] or []), "sha256": r[2], "arquivo": r[1],
            "enviado_por": r[6], "enviado_em": r[7], "apontada": r[8] is None}


# ─────────────────────────────────────────────
#  AJUSTES (edicoes da contadora)
# ─────────────────────────────────────────────
def carregar_ajustes(conn, empresa_codigo: str, competencia: str, ref: str = "") -> list:
    """Edicoes da versao `ref` (id do SPED conferido; '' = mes sem SPED)."""
    with conn.cursor() as cur:
        cur.execute("SELECT aba, chave, campo, valor_original, valor_novo, editado_por, editado_em FROM egc.cred_ajuste "
                    "WHERE empresa_codigo = %s AND competencia = %s AND envio_ref = %s ORDER BY id", (empresa_codigo, competencia, ref or ""))
        return [dict(zip(("aba", "chave", "campo", "valor_original", "valor_novo", "editado_por", "editado_em"), r)) for r in cur.fetchall()]


def salvar_ajustes(conn, empresa_codigo: str, competencia: str, aba: str, mudancas: list, usuario: str, ref: str = "") -> int:
    """mudancas = [{'chave','campo','valor_original','valor_novo'}]. Upsert + trilha, presos a versao `ref`. Devolve quantas edicoes gravou."""
    ref = ref or ""
    n = 0
    with _transacao(conn):
        with conn.cursor() as cur:
            for m in mudancas:
                cur.execute("SELECT valor_novo, valor_original FROM egc.cred_ajuste WHERE empresa_codigo = %s AND competencia = %s AND aba = %s "
                            "AND chave = %s AND campo = %s AND envio_ref = %s", (empresa_codigo, competencia, aba, m["chave"], m["campo"], ref))
                ant = cur.fetchone()
                original = ant[1] if ant else m.get("valor_original")
                antes = ant[0] if ant else m.get("valor_original")
                cur.execute(
                    "INSERT INTO egc.cred_ajuste (empresa_codigo, competencia, aba, chave, campo, valor_original, valor_novo, editado_por, envio_ref) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (empresa_codigo, competencia, aba, chave, campo, envio_ref) DO UPDATE SET "
                    "valor_novo = EXCLUDED.valor_novo, editado_por = EXCLUDED.editado_por, editado_em = now()",
                    (empresa_codigo, competencia, aba, m["chave"], m["campo"], original, m["valor_novo"], usuario, ref))
                cur.execute("INSERT INTO egc.cred_ajuste_log (empresa_codigo, competencia, aba, chave, campo, acao, valor_antes, valor_depois, usuario, envio_ref) "
                             "VALUES (%s,%s,%s,%s,%s,'editar',%s,%s,%s,%s)",
                             (empresa_codigo, competencia, aba, m["chave"], m["campo"], antes, m["valor_novo"], usuario, ref))
                n += 1
    return n


def restaurar_ajustes(conn, empresa_codigo: str, competencia: str, aba: str, chave: str, usuario: str, ref: str = "") -> int:
    """Remove TODAS as edicoes de uma linha da versao `ref` (volta ao valor calculado). Fica registrado no log."""
    ref = ref or ""
    with _transacao(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT campo, valor_novo, valor_original FROM egc.cred_ajuste WHERE empresa_codigo = %s AND competencia = %s AND aba = %s "
                        "AND chave = %s AND envio_ref = %s", (empresa_codigo, competencia, aba, chave, ref))
            rows = cur.fetchall()
            for campo, novo, orig in rows:
                cur.execute("INSERT INTO egc.cred_ajuste_log (empresa_codigo, competencia, aba, chave, campo, acao, valor_antes, valor_depois, usuario, envio_ref) "
                             "VALUES (%s,%s,%s,%s,%s,'restaurar',%s,%s,%s,%s)", (empresa_codigo, competencia, aba, chave, campo, novo, orig, usuario, ref))
            cur.execute("DELETE FROM egc.cred_ajuste WHERE empresa_codigo = %s AND competencia = %s AND aba = %s AND chave = %s AND envio_ref = %s",
                        (empresa_codigo, competencia, aba, chave, ref))
    return len(rows)


def log_ajustes(conn, empresa_codigo: str, competencia: str, limite: int = 200, ref: str = "") -> pd.DataFrame:
    """Trilha das edicoes da versao `ref`."""
    with conn.cursor() as cur:
        cur.execute("SELECT em, usuario, aba, chave, campo, acao, valor_antes, valor_depois FROM egc.cred_ajuste_log "
                    "WHERE empresa_codigo = %s AND competencia = %s AND envio_ref = %s ORDER BY id DESC LIMIT %s",
                    (empresa_codigo, competencia, ref or "", limite))
        return pd.DataFrame(cur.fetchall(), columns=["em", "usuario", "aba", "chave", "campo", "acao", "valor_antes", "valor_depois"])


# ─────────────────────────────────────────────
#  HISTORICO + SERIE MENSAL (dashboard)
# ─────────────────────────────────────────────
def upsert_historico(conn, empresa_codigo: str, linhas: list) -> int:
    """linhas = [{'competencia','versao','sped_tipo','enviado_arquivo_em','arquivo_nome','resumo','por_categoria','por_natureza','dif_fornecedores'}]"""
    with _transacao(conn):
        with conn.cursor() as cur:
            for l in linhas:
                cur.execute(
                    "INSERT INTO egc.cred_historico (empresa_codigo, competencia, versao, sped_tipo, enviado_arquivo_em, arquivo_nome, resumo, "
                    "por_categoria, por_natureza, dif_fornecedores) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (empresa_codigo, competencia, versao) DO UPDATE SET sped_tipo = EXCLUDED.sped_tipo, "
                    "enviado_arquivo_em = EXCLUDED.enviado_arquivo_em, arquivo_nome = EXCLUDED.arquivo_nome, resumo = EXCLUDED.resumo, "
                    "por_categoria = EXCLUDED.por_categoria, por_natureza = EXCLUDED.por_natureza, dif_fornecedores = EXCLUDED.dif_fornecedores",
                    (empresa_codigo, l["competencia"], l["versao"], l.get("sped_tipo"), l.get("enviado_arquivo_em"), l.get("arquivo_nome"),
                     Json(l.get("resumo", {})), Json(l.get("por_categoria", {})), Json(l.get("por_natureza", {})), Json(l.get("dif_fornecedores", []))))
    return len(linhas)


def carregar_historico(conn, empresa_codigo: str) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT competencia, versao, sped_tipo, enviado_arquivo_em, arquivo_nome, resumo, por_categoria, por_natureza, dif_fornecedores "
                    "FROM egc.cred_historico WHERE empresa_codigo = %s", (empresa_codigo,))
        cols = ["competencia", "versao", "sped_tipo", "enviado_arquivo_em", "arquivo_nome", "resumo", "por_categoria", "por_natureza", "dif_fornecedores"]
        return sorted((dict(zip(cols, r)) for r in cur.fetchall()), key=lambda d: (_chave_comp(d["competencia"]), d["versao"]))


# ---- (v0.52.0) FONTES de cada mes: o que o dashboard pode usar ------------------------------------------------------------------
HIST_APONTADO, HIST_ORIGINAL = "HIST_APONTADO", "HIST_ORIGINAL"


def fontes_por_mes(conn, empresa_codigo: str) -> dict:
    """{competencia: [fonte, ...]} com TODAS as fontes de numeros do mes: o historico (carga fixa jan-jul: ORIGINAL e APONTADO) e cada SPED enviado no app.
    fonte = {chave, tipo ('HISTORICO'|'ENVIO'), rotulo, curto, sped_tipo, arquivo, enviado_em, enviado_por, apontada, resumo, por_categoria, por_natureza,
    dif_fornecedores, envio_id}. chave = 'HIST_APONTADO' | 'HIST_ORIGINAL' | 'ENV:<id>'. A regra padrao (fonte_padrao) e' a mesma de antes da v0.52."""
    fontes: dict = {}
    for h in carregar_historico(conn, empresa_codigo):
        ap = h["versao"] == "APONTADO"
        f = {"chave": HIST_APONTADO if ap else HIST_ORIGINAL, "tipo": "HISTORICO", "sped_tipo": h.get("sped_tipo"), "arquivo": h.get("arquivo_nome"),
             "enviado_em": h.get("enviado_arquivo_em"), "enviado_por": "carga fixa", "apontada": ap, "resumo": h.get("resumo") or {},
             "por_categoria": h.get("por_categoria") or {}, "por_natureza": h.get("por_natureza") or {},
             "dif_fornecedores": h.get("dif_fornecedores") or [], "envio_id": None,
             "rotulo": ("Carga fixa jan–jul · apontada (última retificadora)" if ap else "Carga fixa jan–jul · SPED original")
                       + (f" · {h.get('arquivo_nome')}" if h.get("arquivo_nome") else ""),
             "curto": "Histórico apontado" if ap else "Histórico original"}
        fontes.setdefault(h["competencia"], []).append(f)
    with conn.cursor() as cur:
        cur.execute("SELECT competencia, sped_tipo, resumo, por_categoria, por_natureza, arquivado_em, enviado_em, id, arquivo_nome, enviado_por "
                    "FROM egc.cred_sped_envio WHERE empresa_codigo = %s ORDER BY enviado_em, id", (empresa_codigo,))
        rows = cur.fetchall()
    n_por: dict = {}
    for comp, st_, resumo, cat, nat, arq_em, em, eid, arq, por in rows:
        n_por[comp] = n_por.get(comp, 0) + 1
        n = n_por[comp]
        ap = arq_em is None
        nome = str(st_).strip() if st_ else "SPED"
        fontes.setdefault(comp, []).append({
            "chave": f"ENV:{eid}", "tipo": "ENVIO", "sped_tipo": st_, "arquivo": arq, "enviado_em": em, "enviado_por": por, "apontada": ap,
            "resumo": resumo or {}, "por_categoria": cat or {}, "por_natureza": nat or {}, "dif_fornecedores": [], "envio_id": str(eid),
            "rotulo": f"SPED {n}ª · {nome} · {arq} · enviado em {_hora(em)}" + (" · APONTADA" if ap else ""),
            "curto": f"SPED {n}ª ({nome.lower()})" + (" · apontada" if ap else "")})
    for comp in fontes:
        fontes[comp].sort(key=lambda f: (0 if f["chave"] == HIST_ORIGINAL else 1 if f["chave"] == HIST_APONTADO else 2, str(f["enviado_em"] or ""), f["chave"]))
    return fontes


def fonte_padrao(fontes_mes: list) -> Optional[dict]:
    """Regra padrao (igual a de antes da v0.52): o APONTADO do historico (carga fixa) vence; sem ele, o SPED apontado no app."""
    for f in fontes_mes:
        if f["chave"] == HIST_APONTADO:
            return f
    for f in fontes_mes:
        if f["tipo"] == "ENVIO" and f["apontada"]:
            return f
    return None


# ---- (v0.52.0) CONTROLE DO MES (bloco 30): oficial, conferido, nota -- so' organiza, nada trava -------------------------------------
def tem_bloco30(conn) -> bool:
    try:
        return all(nf_sienge._tabela_existe(conn, f"egc.{t}") is True for t in ("cred_mes", "cred_mes_log"))
    except Exception:
        return False


def carregar_mes(conn, empresa_codigo: str) -> dict:
    """{competencia: {fonte_oficial, conferido, conferido_por, conferido_em, nota, atualizado_por, atualizado_em}} (vazio se o bloco 30 nao foi rodado)."""
    if not tem_bloco30(conn):
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT competencia, fonte_oficial, conferido, conferido_por, conferido_em, nota, atualizado_por, atualizado_em "
                    "FROM egc.cred_mes WHERE empresa_codigo = %s", (empresa_codigo,))
        cols = ["fonte_oficial", "conferido", "conferido_por", "conferido_em", "nota", "atualizado_por", "atualizado_em"]
        return {r[0]: dict(zip(cols, r[1:])) for r in cur.fetchall()}


_NAO_MUDAR = object()


def salvar_mes(conn, empresa_codigo: str, competencia: str, usuario: str, fonte_oficial=_NAO_MUDAR, conferido=_NAO_MUDAR, nota=_NAO_MUDAR) -> int:
    """Grava so' o que foi informado (fonte_oficial=None volta para a regra padrao). Cada mudanca vai para a trilha. Devolve quantos campos mudaram."""
    atual = carregar_mes(conn, empresa_codigo).get(competencia) or {"fonte_oficial": None, "conferido": False, "nota": ""}
    novos = {}
    if fonte_oficial is not _NAO_MUDAR and (fonte_oficial or None) != (atual["fonte_oficial"] or None):
        novos["fonte_oficial"] = fonte_oficial or None
    if conferido is not _NAO_MUDAR and bool(conferido) != bool(atual["conferido"]):
        novos["conferido"] = bool(conferido)
    if nota is not _NAO_MUDAR and (nota or "") != (atual["nota"] or ""):
        novos["nota"] = nota or ""
    if not novos:
        return 0
    with _transacao(conn):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO egc.cred_mes (empresa_codigo, competencia, atualizado_por) VALUES (%s,%s,%s) "
                        "ON CONFLICT (empresa_codigo, competencia) DO NOTHING", (empresa_codigo, competencia, usuario))
            for campo, valor in novos.items():
                cur.execute(f"UPDATE egc.cred_mes SET {campo} = %s, atualizado_por = %s, atualizado_em = now() WHERE empresa_codigo = %s AND competencia = %s",
                            (valor, usuario, empresa_codigo, competencia))
                if campo == "conferido":
                    cur.execute("UPDATE egc.cred_mes SET conferido_por = %s, conferido_em = CASE WHEN %s THEN now() ELSE NULL END "
                                "WHERE empresa_codigo = %s AND competencia = %s", (usuario if valor else None, bool(valor), empresa_codigo, competencia))
                cur.execute("INSERT INTO egc.cred_mes_log (empresa_codigo, competencia, campo, valor_antes, valor_depois, usuario) VALUES (%s,%s,%s,%s,%s,%s)",
                            (empresa_codigo, competencia, campo, None if atual.get(campo) is None else str(atual.get(campo)),
                             None if valor is None else str(valor), usuario))
    return len(novos)


def log_mes(conn, empresa_codigo: str, competencia: str, limite: int = 50) -> pd.DataFrame:
    if not tem_bloco30(conn):
        return pd.DataFrame(columns=["em", "usuario", "campo", "valor_antes", "valor_depois"])
    with conn.cursor() as cur:
        cur.execute("SELECT em, usuario, campo, valor_antes, valor_depois FROM egc.cred_mes_log WHERE empresa_codigo = %s AND competencia = %s "
                    "ORDER BY id DESC LIMIT %s", (empresa_codigo, competencia, limite))
        return pd.DataFrame(cur.fetchall(), columns=["em", "usuario", "campo", "valor_antes", "valor_depois"])


def det_por_categoria(conn, envio_id: Optional[str]) -> dict:
    """{categoria: {'pis', 'cofins'}} somando as linhas de uma versao do Detalhamento (mesmo formato do por_categoria do SPED)."""
    if not envio_id:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT categoria, COALESCE(SUM(pis),0)::float8, COALESCE(SUM(cofins),0)::float8 FROM egc.cred_det_linha WHERE envio_id = %s "
                    "GROUP BY categoria", (envio_id,))
        return {c: {"pis": p, "cofins": f} for c, p, f in cur.fetchall()}


# ---- SERIE MENSAL (dashboard) --------------------------------------------------------------------------------------------------------
def _linha_serie(c: str, fonte: Optional[dict], orig_f: Optional[dict], modo: str, qtd: int, ctrl: dict) -> dict:
    ap = (fonte or {}).get("resumo") or {}
    orig = (orig_f or {}).get("resumo") or {}
    tem = bool(orig)
    return {
        "competencia": c, "origem": "ENVIO" if fonte and fonte["tipo"] == "ENVIO" else "HISTORICO", "tem_original": tem,
        "sped_tipo": (fonte or {}).get("sped_tipo"), "fonte": (fonte or {}).get("chave"), "fonte_rotulo": (fonte or {}).get("rotulo", ""),
        "modo": modo, "qtd_fontes": qtd, "conferido": bool(ctrl.get("conferido")), "nota": ctrl.get("nota") or "",
        "cred_pis": _f(ap.get("credito_pis")), "cred_cofins": _f(ap.get("credito_cofins")),
        "orig_pis": _f(orig.get("credito_pis")) if tem else None, "orig_cofins": _f(orig.get("credito_cofins")) if tem else None,
        "deb_nc_pis": _f(ap.get("debito_nc_pis")), "deb_nc_cofins": _f(ap.get("debito_nc_cofins")),
        "deb_cum_pis": _f(ap.get("debito_cum_pis")), "deb_cum_cofins": _f(ap.get("debito_cum_cofins")),
        "orig_deb_nc_pis": _f(orig.get("debito_nc_pis")) if tem else None, "orig_deb_cum_pis": _f(orig.get("debito_cum_pis")) if tem else None,
        "orig_deb_nc_cofins": _f(orig.get("debito_nc_cofins")) if tem else None, "orig_deb_cum_cofins": _f(orig.get("debito_cum_cofins")) if tem else None,
        "usado_pis": _f(ap.get("credito_usado_pis")), "usado_cofins": _f(ap.get("credito_usado_cofins")),
        "anterior_pis": _f(ap.get("credito_anterior_pis")), "anterior_cofins": _f(ap.get("credito_anterior_cofins")),
        "saldo_pis": _f(ap.get("saldo_pis")), "saldo_cofins": _f(ap.get("saldo_cofins")),
        "ret_pis": _f(ap.get("retencao_nc_pis")), "ret_cofins": _f(ap.get("retencao_nc_cofins")),
        "recolher_pis": _f(ap.get("a_recolher_pis")), "recolher_cofins": _f(ap.get("a_recolher_cofins")),
        "receita_nc": _f(ap.get("receita_nc")), "receita_cum": _f(ap.get("receita_cum")),
        "por_categoria": (fonte or {}).get("por_categoria") or {}, "por_natureza": (fonte or {}).get("por_natureza") or {},
        "orig_por_categoria": (orig_f or {}).get("por_categoria") or {}, "dif_fornecedores": (fonte or {}).get("dif_fornecedores") or [],
    }


def serie_mensal(conn, empresa_codigo: str, escolhas: Optional[dict] = None, usar_oficial: bool = True, fontes: Optional[dict] = None,
                 controle: Optional[dict] = None) -> pd.DataFrame:
    """Uma linha por competencia para o dashboard. Qual FONTE vale em cada mes: (1) `escolhas` = {competencia: chave} (SIMULACAO, nao grava nada);
    (2) a OFICIAL marcada pela contadora (cred_mes, se `usar_oficial`); (3) a regra padrao (historico APONTADO jan-jul; depois o SPED apontado).
    `origem` = ENVIO | HISTORICO; `modo` = 'simulada' | 'oficial' | 'padrao'. O "original" (para o comparativo original x apontado) e' o ORIGINAL do historico
    ou o 1o SPED "Original" enviado no app (se nao for a propria fonte usada). `fontes`/`controle` podem ser passados para nao reler o banco."""
    fontes = fontes if fontes is not None else fontes_por_mes(conn, empresa_codigo)
    ctrl = controle if controle is not None else (carregar_mes(conn, empresa_codigo) if usar_oficial else {})
    escolhas = escolhas or {}
    linhas = []
    for c in sorted(fontes, key=_chave_comp):
        lista = fontes[c]
        por_chave = {f["chave"]: f for f in lista}
        padrao = fonte_padrao(lista)
        of = por_chave.get((ctrl.get(c) or {}).get("fonte_oficial")) if usar_oficial else None
        sim = por_chave.get(escolhas.get(c))
        fonte, modo = (sim, "simulada") if sim else (of, "oficial") if of else (padrao, "padrao")
        if sim is not None and sim is (of or padrao):
            fonte, modo = sim, ("oficial" if of else "padrao")
        orig_f = por_chave.get(HIST_ORIGINAL)
        if orig_f is None:
            orig_f = next((f for f in lista if f["tipo"] == "ENVIO" and str(f["sped_tipo"] or "").strip().lower() == "original"), None)
        if orig_f is not None and fonte is not None and orig_f["chave"] == fonte["chave"]:
            orig_f = None
        if not (fonte and fonte["resumo"]) and not (orig_f and orig_f["resumo"]):
            continue
        linhas.append(_linha_serie(c, fonte if fonte and fonte["resumo"] else None, orig_f if orig_f and orig_f["resumo"] else None, modo, len(lista), ctrl.get(c) or {}))
    return pd.DataFrame(linhas)
