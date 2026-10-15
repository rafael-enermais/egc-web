# -*- coding: utf-8 -*-
"""
EGC | Notas Fiscais -- NFS (nota de SERVICO): escrituracao da contabilidade x Sienge.  (v0.49.0, 15/10/2026)

Por que um modulo separado do nf_sienge.py (decisao com o Rafael em 15/10/2026): a conferencia de NFE/NF (manifesto da Receita x Sienge) ja'
esta pronta e validada. A NFS vem de OUTRA planilha (escrituracao da contabilidade, .xls), com outro layout e outro universo, e o Sienge a
guarda como tipo "NFS". Mantendo tudo separado -- upload proprio, tabelas proprias (bloco 27), resultado proprio (aba "NFS") -- um fluxo nunca
confunde a leitura do outro. So' o ARQUIVO de resultado e' o mesmo (a aba NFS entra na planilha da conferencia).

Fluxo: ler a planilha (analisar_planilha) -> gravar o envio da empresa/mes (gravar_envio) -> conferir contra egc.nfs_bills_sync (conferir).
A conferencia e' calculada na hora (nao grava resultado): sempre reflete o ultimo "Atualizar do Sienge".

Chave do cruzamento (validada em 15/10/2026 com a API real): CNPJ/CPF do fornecedor + numero da nota + valor. NFS nao tem chave de acesso.
Da escrituracao so' entram as especies NFSE / NFS / NFS-E; NFE, CTE, NF3E, recibos e faturas da mesma planilha ficam de fora (NFE e' conferida
pelo manifesto da Receita).
"""
from __future__ import annotations

import io
import re
import unicodedata
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from psycopg2.extras import execute_values

import nf_sienge

ESPECIES_SERVICO = {"NFSE", "NFS"}   # normalizado (_norm_especie: maiusculas, sem hifen/ponto/espaco): NFSE, NFS-E e NFS
TOLERANCIA_VALOR = 0.01
JANELA_NUMERO_DIVERGENTE_DIAS = 20

from nf_export import ROTULO_STATUS_NFS, ORDEM_STATUS_NFS  # noqa: E402  (fonte unica dos rotulos)

# nomes das colunas da planilha da escrituracao (comparados sem acento/caixa/espaco extra)
_COLS = {
    "cnpj": "cnpj / cpf / cno",
    "numero": "numero",
    "especie": "especie",
    "data_emissao": "data emissao",
    "data_escrituracao": "data escrituracao/servico",
    "fornecedor": "razao social",
    "valor": "valor total",
    "descricao": "descricao",
}
_OBRIGATORIAS = ("cnpj", "numero", "especie", "data_emissao", "fornecedor", "valor")


# ─────────────────────────────────────────────
#  LEITURA DA PLANILHA (.xls ou .xlsx)
# ─────────────────────────────────────────────
def _sem_acento(s) -> str:
    t = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip().lower()


def _norm_especie(v) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def so_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def normalizar_numero(v) -> str:
    """Numero da nota comparavel: so' digitos, sem zeros a esquerda ('000001628' e 1628.0 -> '1628'). Sem digito -> texto em maiusculas."""
    s = str(v if v is not None else "").strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    d = re.sub(r"\D", "", s)
    if d:
        return d.lstrip("0") or "0"
    return s.upper()


def _numero_exibicao(v) -> str:
    s = str(v if v is not None else "").strip()
    return s.split(".")[0] if re.fullmatch(r"\d+\.0+", s) else s


def _para_data(serie: pd.Series) -> pd.Series:
    """Datas da planilha: aaaa-mm-dd (ISO) lido como ISO; dd/mm/aaaa lido dia-primeiro (evita 10/08 virar 08/10)."""
    iso = pd.to_datetime(serie, errors="coerce", format="ISO8601")
    br = pd.to_datetime(serie, dayfirst=True, errors="coerce")
    return iso.fillna(br).dt.date


def analisar_planilha(conteudo: bytes, nome_arquivo: str = "") -> dict:
    """Le a planilha da escrituracao e devolve:
      erro (str|None), df (so' as NFS, colunas normalizadas), total_linhas, por_especie ({especie: linhas}),
      periodo (sugestao 'MM/AAAA' = mes mais frequente da Data de Escrituracao/Servico), periodos ({mes: linhas}),
      repetidas (linhas com mesma chave CNPJ+numero+valor), sem_valor (linhas de NFS ignoradas por valor invalido).
    Nunca levanta excecao por arquivo ruim: devolve `erro` em linguagem simples."""
    base = {"erro": None, "df": pd.DataFrame(), "total_linhas": 0, "por_especie": {}, "periodo": None,
            "periodos": {}, "repetidas": 0, "sem_valor": 0, "arquivo": nome_arquivo}
    try:
        bruto = pd.read_excel(io.BytesIO(conteudo))
    except ImportError as exc:
        base["erro"] = f"Falta a biblioteca para ler arquivos .xls ({exc}). Avise o suporte."
        return base
    except Exception as exc:
        base["erro"] = f"Não consegui abrir a planilha ({type(exc).__name__}). Confira se é o arquivo .xls/.xlsx da escrituração."
        return base
    mapa_cols = {_sem_acento(c): c for c in bruto.columns}
    if "emissor cnpj/cpf" in mapa_cols or "xml" in mapa_cols:
        base["erro"] = ("Este parece ser o manifesto da Receita Federal (NF-e). Ele vai no passo 2 — aqui entra só a "
                        "planilha de escrituração da contabilidade (ENTRADAS).")
        return base
    faltam = [r for r in _OBRIGATORIAS if _COLS[r] not in mapa_cols]
    if faltam:
        base["erro"] = ("Não reconheci as colunas da escrituração. Faltam: "
                        + ", ".join(_COLS[f].title() for f in faltam) + ".")
        return base
    c = lambda k: mapa_cols.get(_COLS[k])
    df = pd.DataFrame({
        "cnpj": bruto[c("cnpj")].map(so_digitos),
        "cnpj_formatado": bruto[c("cnpj")].astype(str).str.strip(),
        "numero_nota": bruto[c("numero")].map(_numero_exibicao),
        "num": bruto[c("numero")].map(normalizar_numero),
        "especie": bruto[c("especie")].astype(str).str.strip().str.upper(),
        "especie_norm": bruto[c("especie")].map(_norm_especie),
        "data_emissao": _para_data(bruto[c("data_emissao")]),
        "data_escrituracao": _para_data(bruto[c("data_escrituracao")]) if c("data_escrituracao") else pd.NaT,
        "fornecedor_nome": bruto[c("fornecedor")].astype(str).str.strip(),
        "valor": pd.to_numeric(bruto[c("valor")], errors="coerce"),
        "descricao": bruto[c("descricao")].astype(str).str.strip() if c("descricao") else "",
    })
    df["linha_origem"] = range(2, len(df) + 2)      # linha na planilha (cabecalho = 1)
    base["total_linhas"] = int(len(df))
    base["por_especie"] = {k: int(v) for k, v in df["especie"].value_counts().items()}
    nfs = df[df["especie_norm"].isin(ESPECIES_SERVICO)].copy()
    sem_valor = nfs["valor"].isna() | (nfs["cnpj"] == "") | (nfs["num"] == "")
    base["sem_valor"] = int(sem_valor.sum())
    nfs = nfs[~sem_valor].copy()
    nfs["valor"] = nfs["valor"].astype(float).round(2)
    base["repetidas"] = int(nfs.duplicated(["cnpj", "num", "valor"]).sum())
    ref = pd.to_datetime(nfs["data_escrituracao"], errors="coerce")
    if ref.notna().sum() == 0:
        ref = pd.to_datetime(nfs["data_emissao"], errors="coerce")
    meses = ref.dt.strftime("%m/%Y").value_counts()
    base["periodos"] = {k: int(v) for k, v in meses.items()}
    base["periodo"] = meses.index[0] if len(meses) else _periodo_do_nome(nome_arquivo)
    base["df"] = nfs.reset_index(drop=True)
    if nfs.empty:
        base["erro"] = "A planilha não tem nenhuma nota de serviço (espécie NFSE/NFS)."
    return base


def _periodo_do_nome(nome: str) -> Optional[str]:
    m = re.search(r"(\d{1,2})[-_.](\d{4})", nome or "")
    return f"{int(m.group(1)):02d}/{m.group(2)}" if m else None


# ─────────────────────────────────────────────
#  BANCO (bloco 27)
# ─────────────────────────────────────────────
class Bloco27Indisponivel(Exception):
    """O bloco 27 do schema.sql ainda nao foi rodado neste banco."""


def tem_bloco27(conn) -> bool:
    try:
        return all(nf_sienge._tabela_existe(conn, f"egc.{t}") is True
                   for t in ("nfs_import", "nfs_escrituracao", "nfs_bills_sync"))
    except Exception:
        return False


@contextmanager
def _transacao(conn):
    with nf_sienge._transacao(conn):
        yield


def gravar_envio(conn, empresa_codigo: str, periodo_referencia: str, arquivo_nome: str, df: pd.DataFrame,
                 total_linhas: int, usuario: str) -> str:
    """Grava as NFS da planilha como o envio vigente da empresa+mes. Envio anterior do mesmo mes e' ARQUIVADO (nao apagado).
    Devolve o id do envio."""
    if df is None or df.empty:
        raise ValueError("Nenhuma nota de serviço para gravar.")
    novo_id = str(uuid.uuid4())
    with _transacao(conn):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE egc.nfs_import SET arquivado_em = now(), arquivado_por = %s, "
                "arquivado_motivo = 'Substituído por um novo envio do mesmo mês' "
                "WHERE empresa_codigo = %s AND periodo_referencia = %s AND arquivado_em IS NULL",
                (usuario, empresa_codigo, periodo_referencia))
            cur.execute(
                "INSERT INTO egc.nfs_import (id, empresa_codigo, periodo_referencia, arquivo_nome, total_linhas, total_nfs, enviado_por) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (novo_id, empresa_codigo, periodo_referencia, arquivo_nome, int(total_linhas), int(len(df)), usuario))
            linhas = [
                (novo_id, empresa_codigo, r.numero_nota, r.num, r.cnpj_formatado, r.cnpj, r.fornecedor_nome,
                 r.data_emissao if pd.notna(r.data_emissao) else None,
                 r.data_escrituracao if pd.notna(r.data_escrituracao) else None,
                 float(r.valor), r.especie, r.descricao, int(r.linha_origem))
                for r in df.itertuples(index=False)
            ]
            execute_values(
                cur,
                "INSERT INTO egc.nfs_escrituracao (import_id, empresa_codigo, numero_nota, numero_normalizado, cnpj, cnpj_normalizado, "
                "fornecedor_nome, data_emissao, data_escrituracao, valor, especie, descricao, linha_origem) VALUES %s",
                linhas, page_size=500)
    return novo_id


def listar_envios(conn, empresa_codigo: Optional[str] = None, incluir_arquivados: bool = False) -> list:
    where, params = [], []
    if empresa_codigo:
        where.append("empresa_codigo = %s")
        params.append(empresa_codigo)
    if not incluir_arquivados:
        where.append("arquivado_em IS NULL")
    sql = ("SELECT id, empresa_codigo, periodo_referencia, arquivo_nome, total_linhas, total_nfs, enviado_por, enviado_em, "
           "arquivado_em, arquivado_por, arquivado_motivo FROM egc.nfs_import"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY empresa_codigo, enviado_em DESC")
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description]
        linhas = [dict(zip(cols, r)) for r in cur.fetchall()]
    for l in linhas:
        l["id"] = str(l["id"])
    return linhas


def arquivar_envio(conn, import_id: str, usuario: str, motivo: Optional[str] = None) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE egc.nfs_import SET arquivado_em = now(), arquivado_por = %s, arquivado_motivo = %s "
                    "WHERE id = %s AND arquivado_em IS NULL", (usuario, motivo or "Arquivado pela contadora", import_id))


def restaurar_envio(conn, import_id: str, usuario: str) -> None:
    """Restaura um envio arquivado. Se o mes ja' tem outro envio vigente, recusa (arquive o vigente antes)."""
    with conn.cursor() as cur:
        cur.execute("SELECT empresa_codigo, periodo_referencia FROM egc.nfs_import WHERE id = %s", (import_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Envio não encontrado.")
        cur.execute("SELECT 1 FROM egc.nfs_import WHERE empresa_codigo = %s AND periodo_referencia = %s AND arquivado_em IS NULL "
                    "AND id <> %s", (row[0], row[1], import_id))
        if cur.fetchone():
            raise ValueError("Esse mês já tem outro envio vigente. Arquive o vigente antes de restaurar este.")
        cur.execute("UPDATE egc.nfs_import SET arquivado_em = NULL, arquivado_por = NULL, arquivado_motivo = NULL WHERE id = %s",
                    (import_id,))


def _carregar_escrituracao(conn, empresa_codigo: str, periodos: Optional[list] = None) -> pd.DataFrame:
    sql = ("SELECT i.periodo_referencia AS periodo, e.numero_normalizado AS num, e.numero_nota, e.cnpj_normalizado AS cnpj, "
           "e.cnpj AS cnpj_formatado, e.fornecedor_nome, e.data_emissao, e.data_escrituracao, e.valor::float8 AS valor, e.especie, "
           "e.descricao, e.id AS registro_id FROM egc.nfs_escrituracao e JOIN egc.nfs_import i ON i.id = e.import_id "
           "WHERE i.empresa_codigo = %s AND i.arquivado_em IS NULL")
    params: list = [empresa_codigo]
    if periodos:
        sql += " AND i.periodo_referencia = ANY(%s)"
        params.append(list(periodos))
    sql += " ORDER BY i.periodo_referencia, e.id"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def _carregar_bills(conn, tabela: str) -> pd.DataFrame:
    sql = (f"SELECT b.bill_id, b.debtor_id, b.document_identification_id AS tipo, b.document_number AS numero, b.issue_date, "
           f"b.total_invoice_amount::float8 AS valor, c.nome AS credor_nome, "
           f"regexp_replace(COALESCE(NULLIF(c.cnpj, ''), c.cpf, ''), '\\D', '', 'g') AS cnpj "
           f"FROM egc.{tabela} b LEFT JOIN egc.nf_creditors_sync c ON c.creditor_id = b.creditor_id")
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    if df.empty:
        return pd.DataFrame(columns=cols + ["num"])
    df["num"] = df["numero"].map(normalizar_numero)
    df["cnpj"] = df["cnpj"].fillna("")
    return df


def conferir(conn, empresa_codigo: str, periodos: Optional[list] = None, nome_por_empresa: Optional[dict] = None) -> pd.DataFrame:
    """Conferencia NFS da empresa nos periodos (None = todos os vigentes). Devolve a tabela (ver `classificar`)."""
    if not tem_bloco27(conn):
        raise Bloco27Indisponivel("O bloco 27 do schema.sql ainda não foi rodado neste banco.")
    esc_sel = _carregar_escrituracao(conn, empresa_codigo, periodos)
    esc_todas = esc_sel if not periodos else _carregar_escrituracao(conn, empresa_codigo, None)
    bills = _carregar_bills(conn, "nfs_bills_sync")
    try:
        nfe = _carregar_bills(conn, "nf_bills_sync")
    except Exception:
        nfe = pd.DataFrame()
    mapa = nf_sienge.carregar_mapa_debtor_manual(conn)
    return classificar(esc_sel, esc_todas, bills, nfe, mapa, empresa_codigo, nome_por_empresa or {})


# ─────────────────────────────────────────────
#  CLASSIFICACAO (pura -- sem banco, testavel)
# ─────────────────────────────────────────────
def _brl(v) -> str:
    return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _mes_rotulo(d) -> Optional[str]:
    return d.strftime("%m/%Y") if isinstance(d, date) else None


def classificar(esc_sel: pd.DataFrame, esc_todas: pd.DataFrame, bills: pd.DataFrame, nfe: pd.DataFrame,
                mapa_debtor: dict, empresa_codigo: str, nome_por_empresa: Optional[dict] = None) -> pd.DataFrame:
    """Uma linha por nota da escrituracao (origem='ESCRITURACAO') + uma por titulo NFS do Sienge sem nota na escrituracao
    (origem='SIENGE_ORFAO', so' quando o devedor do titulo esta' confirmado como esta empresa -- mesma regra da NFE).
    Colunas: periodo, numero_nota, status, data_emissao, valor, fornecedor_nome, fornecedor_cnpj, sienge_bill_id, sienge_documento,
    sienge_valor, confianca, observacao, origem, registro_id."""
    nome_por_empresa = nome_por_empresa or {}
    colunas = ["periodo", "numero_nota", "status", "data_emissao", "valor", "fornecedor_nome", "fornecedor_cnpj",
               "sienge_bill_id", "sienge_documento", "sienge_valor", "confianca", "observacao", "origem", "registro_id"]
    linhas: list = []
    idx = defaultdict(list)
    idx_cnpj = defaultdict(list)
    for b in bills.itertuples(index=False):
        idx[(b.cnpj, b.num)].append(b)
        idx_cnpj[b.cnpj].append(b)
    nfe_set = set()
    if nfe is not None and not nfe.empty:
        for b in nfe.itertuples(index=False):
            if str(b.tipo or "").strip().upper() in ("NFE", "NF") and pd.notna(b.valor):
                nfe_set.add((b.cnpj, b.num, round(float(b.valor), 2)))
    repetidas = Counter((r.cnpj, r.num, round(r.valor, 2)) for r in esc_sel.itertuples(index=False))

    def doc(b):
        return f"{str(b.tipo or '').strip()} {b.numero}".strip()

    for r in esc_sel.itertuples(index=False):
        valor = round(float(r.valor), 2)
        cands = idx.get((r.cnpj, r.num), [])
        exatos = [b for b in cands if pd.notna(b.valor) and abs(float(b.valor) - valor) <= TOLERANCIA_VALOR + 1e-9]
        status, bill, conf, obs = "NAO_ENCONTRADA", None, None, None
        if exatos:
            proprios = [b for b in exatos if mapa_debtor.get(int(b.debtor_id) if pd.notna(b.debtor_id) else -1) == empresa_codigo]
            livres = [b for b in exatos if (int(b.debtor_id) if pd.notna(b.debtor_id) else -1) not in mapa_debtor]
            bill = (proprios or livres or exatos)[0]
            dono = mapa_debtor.get(int(bill.debtor_id)) if pd.notna(bill.debtor_id) else None
            conf = "CNPJ_NUMERO_VALOR"
            if dono and dono != empresa_codigo:
                status = "LANCADA_OUTRA_EMPRESA"
                obs = (f"Lançada no Sienge na empresa {nome_por_empresa.get(dono, dono)} (devedor {int(bill.debtor_id)}) "
                       f"— corrigir o lançamento.")
            else:
                status = "LANCADA"
        elif cands:
            bill = min(cands, key=lambda b: abs(float(b.valor or 0) - valor))
            status, conf = "VALOR_DIVERGENTE", "CNPJ_NUMERO"
            obs = f"Sienge: {_brl(bill.valor)}; escrituração: {_brl(valor)} (diferença {_brl(abs(float(bill.valor or 0) - valor))})."
        else:
            for b in idx_cnpj.get(r.cnpj, []):
                if (pd.notna(b.valor) and abs(float(b.valor) - valor) <= TOLERANCIA_VALOR + 1e-9
                        and nf_sienge.numeros_parecidos(r.num, b.num)
                        and isinstance(r.data_emissao, date) and isinstance(b.issue_date, date)
                        and abs((b.issue_date - r.data_emissao).days) <= JANELA_NUMERO_DIVERGENTE_DIAS):
                    bill, status, conf = b, "NUMERO_DIVERGENTE", "CNPJ_VALOR_DATA"
                    obs = f"Sienge tem a nota nº {b.numero} (mesmo fornecedor e valor) — conferir a digitação."
                    break
            if status == "NAO_ENCONTRADA" and (r.cnpj, r.num, valor) in nfe_set:
                obs = "Está no Sienge como NFE/NF (tipo de documento diferente de NFS) — corrigir o tipo no lançamento."
        n_rep = repetidas[(r.cnpj, r.num, valor)]
        if n_rep > 1:
            obs = ((obs + " ") if obs else "") + f"Nota repetida na escrituração ({n_rep} linhas)."
        linhas.append([
            r.periodo, r.numero_nota, status, r.data_emissao, valor, r.fornecedor_nome, r.cnpj_formatado or r.cnpj,
            int(bill.bill_id) if bill is not None else None, doc(bill) if bill is not None else None,
            float(bill.valor) if bill is not None and pd.notna(bill.valor) else None, conf, obs, "ESCRITURACAO", int(r.registro_id)])

    # titulos NFS do Sienge (devedor = esta empresa) sem nota na escrituracao, dentro da janela dos meses conferidos
    donos = {d for d, e in mapa_debtor.items() if e == empresa_codigo}
    if donos and not esc_sel.empty and not bills.empty:
        meses = sorted({p for p in esc_sel["periodo"]}, key=lambda p: (int(p[3:]), int(p[:2])))
        ini = date(int(meses[0][3:]), int(meses[0][:2]), 1)
        mm, aa = int(meses[-1][:2]), int(meses[-1][3:])
        fim = (date(aa + (mm == 12), (mm % 12) + 1, 1) - timedelta(days=1))
        chaves_esc = {(r.cnpj, r.num) for r in esc_todas.itertuples(index=False)}
        for b in bills.itertuples(index=False):
            if pd.isna(b.debtor_id) or int(b.debtor_id) not in donos:
                continue
            if not isinstance(b.issue_date, date) or not (ini <= b.issue_date <= fim):
                continue
            if (b.cnpj, b.num) in chaves_esc:
                continue
            linhas.append([_mes_rotulo(b.issue_date), b.numero, "SIENGE_SEM_ESCRITURACAO", b.issue_date, None,
                           b.credor_nome, b.cnpj, int(b.bill_id), doc(b),
                           float(b.valor) if pd.notna(b.valor) else None, None,
                           f"Título NFS do Sienge (devedor {int(b.debtor_id)}) sem nota na escrituração.", "SIENGE_ORFAO", None])
    tabela = pd.DataFrame(linhas, columns=colunas)
    return tabela


def resumo_nfs(tabela: pd.DataFrame) -> dict:
    """total = notas da escrituracao; pendencias = tudo que NAO e' LANCADA (notas + titulos Sienge sem nota)."""
    if tabela is None or tabela.empty:
        return {"total": 0, "lancadas": 0, "notas_pendentes": 0, "sienge_sem_escrituracao": 0, "pendencias": 0, "taxa": None,
                "por_status": {s: 0 for s in ORDEM_STATUS_NFS}}
    esc = tabela[tabela["origem"] == "ESCRITURACAO"]
    total = len(esc)
    lancadas = int((esc["status"] == "LANCADA").sum())
    contagem = tabela["status"].value_counts().to_dict()
    return {
        "total": total, "lancadas": lancadas, "notas_pendentes": total - lancadas,
        "sienge_sem_escrituracao": int(len(tabela) - total), "pendencias": int((tabela["status"] != "LANCADA").sum()),
        "taxa": (lancadas / total) if total else None,
        "por_status": {s: int(contagem.get(s, 0)) for s in ORDEM_STATUS_NFS},
    }
