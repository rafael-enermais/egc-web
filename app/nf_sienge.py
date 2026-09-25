# -*- coding: utf-8 -*-
"""
EGC | Notas Fiscais — sincronização com o Sienge (Contas a Pagar +
Credores) e motor de conciliação contra o manifesto de NF-e da Receita.

Mesmo padrão de app/db.py: toda função recebe uma conexão psycopg2 já
aberta -- quem gerencia a conexão/credenciais é o chamador (a tela, via
st.secrets). Isso mantém este módulo testável com um conn/cursor mockado,
sem precisar de Streamlit nem de rede real (ver
app/tests/test_nf_sienge.py).

Decisões de arquitetura (fechadas com o Rafael em 25/09/2026, ver EGC
00-handoff.md seção 62), validadas contra a API real do Sienge antes de
escrever este código (não suposição):

  - Endpoint fonte é GET /v1/bills (Contas a Pagar), não
    /v1/purchase-invoices (pedido de compra) -- confirmado com a
    contadora que boa parte das notas (combustível, pedágio) nunca vira
    pedido de compra, mas sempre vira título.
  - accessKeyNumber (chave de acesso) só vem preenchido em ~14% dos
    títulos que são nota fiscal -- não dá pra usar como único critério.
    Critério principal: CNPJ do fornecedor (via creditorId ->
    /v1/creditors/{id}.cnpj) + número do documento + valor. A chave,
    quando presente dos dois lados, vira confirmação extra (maior
    confiança), nunca obrigatória.
  - Universo primário: documentIdentificationId em ('NFE ', 'NF  ').
    Sincroniza TODOS os tipos de título do período (não só esses dois),
    pra permitir uma busca de segundo passe (fallback) sem tipo, cobrindo
    o risco de uma nota ter sido lançada sob outro código por engano.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

TIPOS_NOTA_FISCAL = ("NFE ", "NF  ")
TOLERANCIA_VALOR = 0.01  # 1 centavo
LIMITE_PAGINA = 200      # teto documentado da API REST do Sienge


# ─────────────────────────────────────────────
#  SINCRONIZAÇÃO (Sienge -> egc.nf_bills_sync / egc.nf_creditors_sync)
# ─────────────────────────────────────────────

def _sessao_sienge(base_url: str, usuario: str, senha: str):
    auth = HTTPBasicAuth(usuario, senha)
    return base_url.rstrip("/"), auth


def sincronizar_bills(conn, base_url: str, usuario: str, senha: str,
                       data_inicio: date, data_fim: date, max_paginas: int = 50) -> int:
    """
    Puxa TODOS os títulos (não só NFE/NF -- ver nota no topo do módulo
    sobre o fallback) de /v1/bills no intervalo de datas e faz upsert em
    egc.nf_bills_sync. Retorna quantos títulos foram sincronizados.
    """
    base, auth = _sessao_sienge(base_url, usuario, senha)
    total = 0
    offset = 0
    with conn.cursor() as cur:
        while offset < LIMITE_PAGINA * max_paginas:
            params = {
                "startDate": data_inicio.isoformat(),
                "endDate": data_fim.isoformat(),
                "limit": LIMITE_PAGINA,
                "offset": offset,
            }
            r = requests.get(f"{base}/v1/bills", auth=auth, params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            registros = data.get("results", data) if isinstance(data, dict) else data
            if not registros:
                break
            for b in registros:
                cur.execute(
                    """
                    INSERT INTO egc.nf_bills_sync
                        (bill_id, debtor_id, creditor_id, document_identification_id,
                         document_number, issue_date, total_invoice_amount,
                         access_key_number, status, sincronizado_em)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (bill_id) DO UPDATE SET
                        debtor_id = EXCLUDED.debtor_id,
                        creditor_id = EXCLUDED.creditor_id,
                        document_identification_id = EXCLUDED.document_identification_id,
                        document_number = EXCLUDED.document_number,
                        issue_date = EXCLUDED.issue_date,
                        total_invoice_amount = EXCLUDED.total_invoice_amount,
                        access_key_number = EXCLUDED.access_key_number,
                        status = EXCLUDED.status,
                        sincronizado_em = now()
                    """,
                    (
                        b.get("id"), b.get("debtorId"), b.get("creditorId"),
                        b.get("documentIdentificationId"), b.get("documentNumber"),
                        b.get("issueDate"), b.get("totalInvoiceAmount"),
                        b.get("accessKeyNumber"), b.get("status"),
                    ),
                )
            total += len(registros)
            if len(registros) < LIMITE_PAGINA:
                break
            offset += LIMITE_PAGINA
    return total


def sincronizar_creditores(conn, base_url: str, usuario: str, senha: str, max_paginas: int = 50) -> int:
    """Puxa /v1/creditors (paginado) e faz upsert em egc.nf_creditors_sync."""
    base, auth = _sessao_sienge(base_url, usuario, senha)
    total = 0
    offset = 0
    with conn.cursor() as cur:
        while offset < LIMITE_PAGINA * max_paginas:
            params = {"limit": LIMITE_PAGINA, "offset": offset}
            r = requests.get(f"{base}/v1/creditors", auth=auth, params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            registros = data.get("results", data) if isinstance(data, dict) else data
            if not registros:
                break
            for c in registros:
                cur.execute(
                    """
                    INSERT INTO egc.nf_creditors_sync (creditor_id, nome, nome_fantasia, cnpj, cpf, sincronizado_em)
                    VALUES (%s, %s, %s, %s, %s, now())
                    ON CONFLICT (creditor_id) DO UPDATE SET
                        nome = EXCLUDED.nome, nome_fantasia = EXCLUDED.nome_fantasia,
                        cnpj = EXCLUDED.cnpj, cpf = EXCLUDED.cpf, sincronizado_em = now()
                    """,
                    (c.get("id"), c.get("name"), c.get("tradeName"), c.get("cnpj"), c.get("cpf")),
                )
            total += len(registros)
            if len(registros) < LIMITE_PAGINA:
                break
            offset += LIMITE_PAGINA
    return total


# ─────────────────────────────────────────────
#  IMPORTAÇÃO DO MANIFESTO (planilha da Receita já parseada por nf_parser)
# ─────────────────────────────────────────────

def gravar_manifesto(conn, empresa_codigo: str, periodo_referencia: str,
                      df, nome_arquivo: str, usuario: str) -> str:
    """
    Grava cada linha do DataFrame (já normalizado por
    nf_parser.ler_manifesto_xlsx) em egc.nf_manifesto_import, sob um
    import_id novo (1 import_id = 1 upload = 1 rodada de conferência).
    Retorna o import_id (string uuid) pra encadear com conciliar_import.
    """
    import_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        for _, row in df.iterrows():
            cur.execute(
                """
                INSERT INTO egc.nf_manifesto_import
                    (import_id, empresa_codigo, periodo_referencia, numero_nota,
                     numero_normalizado, tipo_documento, data_emissao, valor, cfop,
                     fornecedor_nome, fornecedor_cnpj, cnpj_normalizado, uf,
                     chave_acesso, chave_modelo, chave_serie, chave_numero,
                     arquivo_nome, criado_por, criado_em)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (
                    import_id, empresa_codigo, periodo_referencia,
                    str(row.get("Num") or ""), row.get("_numero_normalizado"),
                    row.get("Tipo"), row.get("DtEmi"), row.get("_valor_float"), row.get("CFOP"),
                    row.get("Emissor Nome"), row.get("Emissor CNPJ/CPF"), row.get("_cnpj_normalizado"),
                    row.get("UF"), row.get("Chave"), row.get("_chave_modelo"),
                    row.get("_chave_serie"), row.get("_chave_numero"), nome_arquivo, usuario,
                ),
            )
    return import_id


# ─────────────────────────────────────────────
#  CONCILIAÇÃO
# ─────────────────────────────────────────────

def _carregar_bills_creditores(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT b.bill_id, b.creditor_id, b.document_identification_id,
                   b.document_number, b.total_invoice_amount, b.access_key_number,
                   c.cnpj AS creditor_cnpj
            FROM egc.nf_bills_sync b
            LEFT JOIN egc.nf_creditors_sync c ON c.creditor_id = b.creditor_id
            """
        )
        cols = [d[0] for d in cur.description]
        linhas = cur.fetchall()
    df = pd.DataFrame(linhas, columns=cols)
    if df.empty:
        return df
    df["cnpj_normalizado"] = df["creditor_cnpj"].astype(str).str.replace(r"\D", "", regex=True)
    df["numero_normalizado"] = df["document_number"].astype(str).str.replace(r"\D", "", regex=True).str.lstrip("0")
    df["numero_normalizado"] = df["numero_normalizado"].where(df["numero_normalizado"] != "", "0")
    df["access_key_number"] = df["access_key_number"].fillna("")
    return df


def _classificar_nota(row, bills: pd.DataFrame) -> dict:
    cnpj = row["_cnpj_normalizado"]
    numero = row["_numero_normalizado"]
    valor = row["_valor_float"]
    chave = row.get("Chave") or ""

    universo = bills[bills["document_identification_id"].isin(TIPOS_NOTA_FISCAL)] if not bills.empty else bills

    def _bate_valor(v):
        return v is not None and valor is not None and abs(float(v) - float(valor)) <= TOLERANCIA_VALOR

    # 1º passe: chave de acesso idêntica (confiança máxima), só no universo restrito.
    if chave and not universo.empty:
        achou_chave = universo[universo["access_key_number"] == chave]
        if not achou_chave.empty:
            b = achou_chave.iloc[0]
            return dict(status="LANCADA", confianca="CHAVE", sienge_bill_id=int(b["bill_id"]),
                        sienge_valor=float(b["total_invoice_amount"]), observacao=None)

    # 2º passe: CNPJ + número exatos, dentro do universo NFE/NF.
    if not universo.empty:
        candidatos = universo[(universo["cnpj_normalizado"] == cnpj) & (universo["numero_normalizado"] == numero)]
        if not candidatos.empty:
            bate = candidatos[candidatos["total_invoice_amount"].apply(_bate_valor)]
            if not bate.empty:
                b = bate.iloc[0]
                return dict(status="LANCADA", confianca="NUMERO_CNPJ_VALOR", sienge_bill_id=int(b["bill_id"]),
                            sienge_valor=float(b["total_invoice_amount"]), observacao=None)
            b = candidatos.iloc[0]
            return dict(status="VALOR_DIVERGENTE", confianca="NUMERO_CNPJ", sienge_bill_id=int(b["bill_id"]),
                        sienge_valor=float(b["total_invoice_amount"]),
                        observacao=f"Sienge tem R$ {b['total_invoice_amount']}, manifesto tem R$ {valor}")

        # CNPJ + valor batem, número não -- possível erro de digitação do número.
        mesmo_cnpj_valor = universo[(universo["cnpj_normalizado"] == cnpj) & (universo["total_invoice_amount"].apply(_bate_valor))]
        if not mesmo_cnpj_valor.empty:
            b = mesmo_cnpj_valor.iloc[0]
            return dict(status="NUMERO_DIVERGENTE", confianca="CNPJ_VALOR", sienge_bill_id=int(b["bill_id"]),
                        sienge_valor=float(b["total_invoice_amount"]),
                        observacao=f"Sienge tem nota nº {b['document_number']}, manifesto tem nº {row.get('Num')}")

    # 3º passe (fallback): mesma busca, sem restringir o tipo de documento
    # -- cobre nota lançada sob código diferente de NFE/NF por engano.
    if not bills.empty:
        candidatos_amplos = bills[(bills["cnpj_normalizado"] == cnpj) & (bills["numero_normalizado"] == numero)
                                   & (bills["total_invoice_amount"].apply(_bate_valor))]
        if not candidatos_amplos.empty:
            b = candidatos_amplos.iloc[0]
            return dict(status="LANCADA", confianca="NUMERO_CNPJ_VALOR_TIPO_DIVERGENTE", sienge_bill_id=int(b["bill_id"]),
                        sienge_valor=float(b["total_invoice_amount"]),
                        observacao=f"Achada, mas lançada como tipo '{b['document_identification_id']}', não NFE/NF")

    return dict(status="NAO_ENCONTRADA", confianca=None, sienge_bill_id=None, sienge_valor=None, observacao=None)


def conciliar_import(conn, import_id: str) -> dict:
    """
    Roda o matching pra todas as notas de um import_id contra o snapshot
    já sincronizado (egc.nf_bills_sync/nf_creditors_sync), grava o
    resultado em egc.nf_conciliacao e devolve um resumo (pros KPIs/
    egc.nf_import_historico). Idempotente: rodar de novo pro mesmo
    import_id apaga a conciliação anterior antes de regravar (pendências
    já com status alterado manualmente -- ENVIADO_SUPRIMENTOS/RESOLVIDO/
    DESCARTADO -- são preservadas, só o resultado de match é recalculado).
    """
    bills = _carregar_bills_creditores(conn)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, "Num" AS numero, "_numero_normalizado", "_cnpj_normalizado",
                   "_valor_float", "Chave"
            FROM (
                SELECT id, numero_nota AS "Num", numero_normalizado AS "_numero_normalizado",
                       cnpj_normalizado AS "_cnpj_normalizado", valor AS "_valor_float",
                       chave_acesso AS "Chave"
                FROM egc.nf_manifesto_import WHERE import_id = %s
            ) x
            """,
            (import_id,),
        )
        cols = [d[0] for d in cur.description]
        manifesto = pd.DataFrame(cur.fetchall(), columns=cols)

    resumo = {"total": len(manifesto), "lancadas": 0, "pendencias": 0}
    if manifesto.empty:
        return resumo

    with conn.cursor() as cur:
        # Preserva status de acompanhamento manual (pendencia_status) já
        # dado a linhas deste import_id -- só reseta o campo de MATCH.
        cur.execute(
            "SELECT manifesto_id, pendencia_status FROM egc.nf_conciliacao "
            "WHERE import_id = %s AND pendencia_status IS NOT NULL AND pendencia_status <> 'PENDENTE'",
            (import_id,),
        )
        status_preservados = {row[0]: row[1] for row in cur.fetchall()}

        cur.execute("DELETE FROM egc.nf_conciliacao WHERE import_id = %s", (import_id,))

        for _, row in manifesto.iterrows():
            resultado = _classificar_nota(row, bills)
            if resultado["status"] == "LANCADA":
                resumo["lancadas"] += 1
                pendencia_status = None
            else:
                resumo["pendencias"] += 1
                pendencia_status = status_preservados.get(row["id"], "PENDENTE")

            cur.execute(
                """
                INSERT INTO egc.nf_conciliacao
                    (import_id, manifesto_id, status, sienge_bill_id, sienge_valor,
                     confianca, observacao, pendencia_status, atualizado_em)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (import_id, row["id"], resultado["status"], resultado["sienge_bill_id"],
                 resultado["sienge_valor"], resultado["confianca"], resultado["observacao"], pendencia_status),
            )

    return resumo


def gravar_historico_import(conn, import_id: str, empresa_codigo: str, periodo_referencia: str,
                             nome_arquivo: str, usuario: str, resumo: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO egc.nf_import_historico
                (import_id, empresa_codigo, periodo_referencia, total_notas,
                 total_lancadas, total_pendencias, arquivo_nome, usuario, criado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (import_id) DO UPDATE SET
                total_notas = EXCLUDED.total_notas,
                total_lancadas = EXCLUDED.total_lancadas,
                total_pendencias = EXCLUDED.total_pendencias
            """,
            (import_id, empresa_codigo, periodo_referencia, resumo["total"],
             resumo["lancadas"], resumo["pendencias"], nome_arquivo, usuario),
        )


# ─────────────────────────────────────────────
#  CONSULTAS (KPI, histórico, pendências pra exportar)
# ─────────────────────────────────────────────

def listar_historico_importacoes(conn, empresa_codigo: Optional[str] = None) -> list[dict]:
    with conn.cursor() as cur:
        if empresa_codigo:
            cur.execute(
                "SELECT * FROM egc.nf_import_historico WHERE empresa_codigo = %s ORDER BY criado_em DESC",
                (empresa_codigo,),
            )
        else:
            cur.execute("SELECT * FROM egc.nf_import_historico ORDER BY criado_em DESC")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def listar_conciliacao(conn, import_id: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.numero_nota, m.data_emissao, m.valor, m.fornecedor_nome, m.fornecedor_cnpj,
                   c.status, c.confianca, c.sienge_bill_id, c.sienge_valor, c.observacao,
                   c.pendencia_status, c.atualizado_em
            FROM egc.nf_conciliacao c
            JOIN egc.nf_manifesto_import m ON m.id = c.manifesto_id
            WHERE c.import_id = %s
            ORDER BY (c.status <> 'LANCADA') DESC, m.numero_nota
            """,
            (import_id,),
        )
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def listar_pendencias_abertas(conn, empresa_codigo: Optional[str] = None, limite: int = 50) -> pd.DataFrame:
    """Pendências ainda em aberto (PENDENTE ou ENVIADO_SUPRIMENTOS -- não
    RESOLVIDO/DESCARTADO), da rodada de conferência mais recente de cada
    empresa. Usado pelo chat (consultas_chat.consultar_notas_pendentes)
    e pela tela pra montar o .xlsx de pendências."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.empresa_codigo, m.periodo_referencia, m.numero_nota, m.data_emissao,
                   m.valor, m.fornecedor_nome, m.fornecedor_cnpj, c.status, c.observacao,
                   c.pendencia_status, c.atualizado_em
            FROM egc.nf_conciliacao c
            JOIN egc.nf_manifesto_import m ON m.id = c.manifesto_id
            WHERE c.status <> 'LANCADA'
              AND (c.pendencia_status IS NULL OR c.pendencia_status IN ('PENDENTE','ENVIADO_SUPRIMENTOS'))
              AND (%s IS NULL OR m.empresa_codigo = %s)
            ORDER BY m.data_emissao DESC
            LIMIT %s
            """,
            (empresa_codigo, empresa_codigo, limite),
        )
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def atualizar_status_pendencia(conn, manifesto_id: int, novo_status: str, usuario: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE egc.nf_conciliacao
            SET pendencia_status = %s, atualizado_por = %s, atualizado_em = now()
            WHERE manifesto_id = %s
            """,
            (novo_status, usuario, manifesto_id),
        )
