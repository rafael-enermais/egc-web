# -*- coding: utf-8 -*-
"""
EGC | Creditos PIS/COFINS -- banco (bloco 28 do schema.sql).  (v0.50.0, 16/10/2026)

Mesmo desenho dos envios de NFS: cada upload (SPED ou Detalhamento) e' um ENVIO por empresa+competencia; reenviar o mes ARQUIVA o anterior
(nada e' apagado). As edicoes da contadora (cred_ajuste) sao independentes do envio -- sobrevivem a um novo upload e ficam com trilha
(cred_ajuste_log). O historico jan-jul (cred_historico) e' carga fixa; o dashboard junta historico + envios vigentes.
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
def gravar_sped(conn, empresa_codigo: str, sped: dict, arquivo_nome: str, usuario: str) -> str:
    """Grava o SPED como o envio vigente da empresa+competencia (o anterior e' ARQUIVADO). Devolve o id."""
    cab = sped["cabecalho"]
    cred, rec = sped["creditos"], sped["receitas"]
    novo = str(uuid.uuid4())
    resumo = sc.resumo_apuracao(sped["apuracao"])
    resumo["linhas_credito"] = int(len(cred))
    resumo["linhas_receita"] = int(len(rec))
    with _transacao(conn):
        with conn.cursor() as cur:
            cur.execute("UPDATE egc.cred_sped_envio SET arquivado_em = now(), arquivado_por = %s, "
                        "arquivado_motivo = 'Substituído por um novo envio do mesmo mês' "
                        "WHERE empresa_codigo = %s AND competencia = %s AND arquivado_em IS NULL",
                        (usuario, empresa_codigo, cab["competencia"]))
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
    return novo


def gravar_detalhamento(conn, empresa_codigo: str, det: dict, arquivo_nome: str, usuario: str, competencia: Optional[str] = None) -> str:
    comp = competencia or det.get("competencia")
    if not comp:
        raise ValueError("Não consegui descobrir a competência (mês/ano) do Detalhamento.")
    linhas = det["linhas"]
    novo = str(uuid.uuid4())
    with _transacao(conn):
        with conn.cursor() as cur:
            cur.execute("UPDATE egc.cred_det_envio SET arquivado_em = now(), arquivado_por = %s, "
                        "arquivado_motivo = 'Substituído por um novo envio do mesmo mês' "
                        "WHERE empresa_codigo = %s AND competencia = %s AND arquivado_em IS NULL", (usuario, empresa_codigo, comp))
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
#  LEITURA DO PERIODO (reconstroi os dicionarios usados pela conferencia)
# ─────────────────────────────────────────────
def competencias(conn, empresa_codigo: str) -> list:
    """Competencias com SPED ou Detalhamento vigente (mais recente primeiro)."""
    with conn.cursor() as cur:
        cur.execute("SELECT competencia FROM egc.cred_sped_envio WHERE empresa_codigo = %s AND arquivado_em IS NULL UNION "
                    "SELECT competencia FROM egc.cred_det_envio WHERE empresa_codigo = %s AND arquivado_em IS NULL", (empresa_codigo, empresa_codigo))
        comps = [r[0] for r in cur.fetchall()]
    return sorted(set(comps), key=_chave_comp, reverse=True)


def carregar_sped(conn, empresa_codigo: str, competencia: str) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, sped_tipo, arquivo_nome, sha256, cnpj, num_rec_anterior, enviado_arquivo_em, resumo, apuracao, por_categoria, "
                    "por_natureza, enviado_por, enviado_em FROM egc.cred_sped_envio "
                    "WHERE empresa_codigo = %s AND competencia = %s AND arquivado_em IS NULL", (empresa_codigo, competencia))
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
            "enviado_por": r[11], "enviado_em": r[12]}


def carregar_detalhamento(conn, empresa_codigo: str, competencia: str) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, arquivo_nome, sha256, resumo, debitos, reconciliacao, enviado_por, enviado_em FROM egc.cred_det_envio "
                    "WHERE empresa_codigo = %s AND competencia = %s AND arquivado_em IS NULL", (empresa_codigo, competencia))
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
            "enviado_por": r[6], "enviado_em": r[7]}


# ─────────────────────────────────────────────
#  AJUSTES (edicoes da contadora)
# ─────────────────────────────────────────────
def carregar_ajustes(conn, empresa_codigo: str, competencia: str) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT aba, chave, campo, valor_original, valor_novo, editado_por, editado_em FROM egc.cred_ajuste "
                    "WHERE empresa_codigo = %s AND competencia = %s ORDER BY id", (empresa_codigo, competencia))
        return [dict(zip(("aba", "chave", "campo", "valor_original", "valor_novo", "editado_por", "editado_em"), r)) for r in cur.fetchall()]


def salvar_ajustes(conn, empresa_codigo: str, competencia: str, aba: str, mudancas: list, usuario: str) -> int:
    """mudancas = [{'chave','campo','valor_original','valor_novo'}]. Upsert + trilha. Devolve quantas edicoes gravou."""
    n = 0
    with _transacao(conn):
        with conn.cursor() as cur:
            for m in mudancas:
                cur.execute("SELECT valor_novo, valor_original FROM egc.cred_ajuste WHERE empresa_codigo = %s AND competencia = %s AND aba = %s "
                            "AND chave = %s AND campo = %s", (empresa_codigo, competencia, aba, m["chave"], m["campo"]))
                ant = cur.fetchone()
                original = ant[1] if ant else m.get("valor_original")
                antes = ant[0] if ant else m.get("valor_original")
                cur.execute(
                    "INSERT INTO egc.cred_ajuste (empresa_codigo, competencia, aba, chave, campo, valor_original, valor_novo, editado_por) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (empresa_codigo, competencia, aba, chave, campo) DO UPDATE SET "
                    "valor_novo = EXCLUDED.valor_novo, editado_por = EXCLUDED.editado_por, editado_em = now()",
                    (empresa_codigo, competencia, aba, m["chave"], m["campo"], original, m["valor_novo"], usuario))
                cur.execute("INSERT INTO egc.cred_ajuste_log (empresa_codigo, competencia, aba, chave, campo, acao, valor_antes, valor_depois, usuario) "
                             "VALUES (%s,%s,%s,%s,%s,'editar',%s,%s,%s)",
                             (empresa_codigo, competencia, aba, m["chave"], m["campo"], antes, m["valor_novo"], usuario))
                n += 1
    return n


def restaurar_ajustes(conn, empresa_codigo: str, competencia: str, aba: str, chave: str, usuario: str) -> int:
    """Remove TODAS as edicoes de uma linha (volta ao valor calculado). Fica registrado no log."""
    with _transacao(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT campo, valor_novo, valor_original FROM egc.cred_ajuste WHERE empresa_codigo = %s AND competencia = %s AND aba = %s AND chave = %s",
                        (empresa_codigo, competencia, aba, chave))
            rows = cur.fetchall()
            for campo, novo, orig in rows:
                cur.execute("INSERT INTO egc.cred_ajuste_log (empresa_codigo, competencia, aba, chave, campo, acao, valor_antes, valor_depois, usuario) "
                             "VALUES (%s,%s,%s,%s,%s,'restaurar',%s,%s,%s)", (empresa_codigo, competencia, aba, chave, campo, novo, orig, usuario))
            cur.execute("DELETE FROM egc.cred_ajuste WHERE empresa_codigo = %s AND competencia = %s AND aba = %s AND chave = %s",
                        (empresa_codigo, competencia, aba, chave))
    return len(rows)


def log_ajustes(conn, empresa_codigo: str, competencia: str, limite: int = 200) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("SELECT em, usuario, aba, chave, campo, acao, valor_antes, valor_depois FROM egc.cred_ajuste_log "
                    "WHERE empresa_codigo = %s AND competencia = %s ORDER BY id DESC LIMIT %s", (empresa_codigo, competencia, limite))
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


def serie_mensal(conn, empresa_codigo: str) -> pd.DataFrame:
    """Uma linha por competencia para o dashboard. `origem` = ENVIO (SPED vigente no app) ou HISTORICO (carga fixa jan-jul; vence o envio do mesmo mes).
    Colunas: competencia, origem, tem_original, e (apontado) / (original) para credito, debito, a recolher, retencao..."""
    hist = carregar_historico(conn, empresa_codigo)
    with conn.cursor() as cur:
        cur.execute("SELECT competencia, sped_tipo, resumo, por_categoria, por_natureza FROM egc.cred_sped_envio "
                    "WHERE empresa_codigo = %s AND arquivado_em IS NULL", (empresa_codigo,))
        envios = {r[0]: r for r in cur.fetchall()}
    por: dict = {}
    for h in hist:
        d = por.setdefault(h["competencia"], {})
        d[h["versao"]] = h
    comps = sorted(set(por) | set(envios), key=_chave_comp)
    linhas = []
    for c in comps:
        h = por.get(c, {})
        # carga fixa jan-jul: o mes que tem APONTADO no historico vale por ele (o envio desse mes serve so' para conferir com o Detalhamento)
        env = None if h.get("APONTADO") else envios.get(c)
        ap = env[2] if env else (h.get("APONTADO") or {}).get("resumo")
        cat = env[3] if env else (h.get("APONTADO") or {}).get("por_categoria", {})
        nat = env[4] if env else (h.get("APONTADO") or {}).get("por_natureza", {})
        orig = (h.get("ORIGINAL") or {}).get("resumo")
        if not ap and not orig:
            continue
        ap = ap or {}
        orig = orig or {}
        linhas.append({
            "competencia": c, "origem": "ENVIO" if env else "HISTORICO", "tem_original": bool(orig),
            "sped_tipo": env[1] if env else (h.get("APONTADO") or {}).get("sped_tipo"),
            "cred_pis": _f(ap.get("credito_pis")), "cred_cofins": _f(ap.get("credito_cofins")),
            "orig_pis": _f(orig.get("credito_pis")) if orig else None, "orig_cofins": _f(orig.get("credito_cofins")) if orig else None,
            "deb_nc_pis": _f(ap.get("debito_nc_pis")), "deb_nc_cofins": _f(ap.get("debito_nc_cofins")),
            "deb_cum_pis": _f(ap.get("debito_cum_pis")), "deb_cum_cofins": _f(ap.get("debito_cum_cofins")),
            "orig_deb_nc_pis": _f(orig.get("debito_nc_pis")) if orig else None, "orig_deb_cum_pis": _f(orig.get("debito_cum_pis")) if orig else None,
            "orig_deb_nc_cofins": _f(orig.get("debito_nc_cofins")) if orig else None, "orig_deb_cum_cofins": _f(orig.get("debito_cum_cofins")) if orig else None,
            "usado_pis": _f(ap.get("credito_usado_pis")), "usado_cofins": _f(ap.get("credito_usado_cofins")),
            "anterior_pis": _f(ap.get("credito_anterior_pis")), "anterior_cofins": _f(ap.get("credito_anterior_cofins")),
            "saldo_pis": _f(ap.get("saldo_pis")), "saldo_cofins": _f(ap.get("saldo_cofins")),
            "ret_pis": _f(ap.get("retencao_nc_pis")), "ret_cofins": _f(ap.get("retencao_nc_cofins")),
            "recolher_pis": _f(ap.get("a_recolher_pis")), "recolher_cofins": _f(ap.get("a_recolher_cofins")),
            "receita_nc": _f(ap.get("receita_nc")), "receita_cum": _f(ap.get("receita_cum")),
            "por_categoria": cat or {}, "por_natureza": nat or {},
            "orig_por_categoria": (h.get("ORIGINAL") or {}).get("por_categoria", {}),
            "dif_fornecedores": (h.get("APONTADO") or {}).get("dif_fornecedores", []),
        })
    return pd.DataFrame(linhas)
