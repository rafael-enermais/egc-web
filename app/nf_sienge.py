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
  - Universo: documentIdentificationId em ('NFE ', 'NF  ').
    01/10/2026 (Rafael + pedido da contadora): o snapshot passa a guardar
    SÓ esses dois tipos. Antes guardava todos os tipos pra um fallback
    "sem tipo" (nota lançada sob outro código por engano); o fallback foi
    removido -- nota lançada sob outro tipo agora aparece como
    NAO_ENCONTRADA (pendência real: o Suprimentos corrige o tipo no
    Sienge). Motivo: títulos PPC (provisão) e a NFE que os substitui são o
    mesmo lançamento visto 2x -- puxar o PPC duplicava o resultado.
"""
from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import psycopg2
import requests
from requests.auth import HTTPBasicAuth
from psycopg2.extras import execute_values

import nf_parser

TIPOS_NOTA_FISCAL = ("NFE ", "NF  ")
_CODIGOS_NOTA_FISCAL = {t.strip() for t in TIPOS_NOTA_FISCAL}  # {"NFE", "NF"}


def eh_nota_fiscal(tipo_documento) -> bool:
    """True se o documentIdentificationId do titulo e' NFE ou NF (a API
    devolve com padding -- 'NFE ', 'NF  '; compara sem espacos, sem
    diferenciar caixa). 01/10/2026 (Rafael: "so' puxar NF, nao os outros"):
    PPC (provisao, 'substituido' pela NFE depois), RDV, NFS, FAT, GUIA...
    ficam de fora do snapshot -- um PPC e a NFE que o substituiu eram o
    MESMO lancamento visto 2x no Sienge."""
    return (tipo_documento or "").strip().upper() in _CODIGOS_NOTA_FISCAL
TOLERANCIA_VALOR = 0.01  # 1 centavo
LIMITE_PAGINA = 200      # teto documentado da API REST do Sienge
PROXIMIDADE_VALOR_IGNORADA = 0.05  # nota ignorada com valor ate' 5% diferente conta como "par provavel"
JANELA_COBERTURA_DIAS = 7  # tolerancia p/ avisar que o espelho do Sienge nao cobre o manifesto
JANELA_DATA_ORFAOS_DIAS = 15  # FIX_20260928f -- ver identificar_e_gravar_bills_orfaos
# v0.45.0: "numero divergente" (mesmo CNPJ + valor, numero diferente) so' olha titulos com data de emissao
# a ate' N dias da nota. Sem isso, com o espelho cobrindo o ano todo, a nota de agosto de um fornecedor
# de valor fixo mensal (aluguel...) virava "Sienge tem a nota nº X" apontando pro titulo de julho.
JANELA_NUMERO_DIVERGENTE_DIAS = 20
DISTANCIA_MAX_NUMERO_DIVERGENTE = 2   # v0.45.1: "erro de digitacao" = numeros PARECIDOS (ate' 2 digitos de diferenca)


# ─────────────────────────────────────────────
#  SINCRONIZAÇÃO (Sienge -> egc.nf_bills_sync / egc.nf_creditors_sync)
# ─────────────────────────────────────────────

def _sessao_sienge(base_url: str, usuario: str, senha: str):
    """
    requests.Session() reaproveita a conexao TCP/TLS entre as paginas
    (antes era 1 handshake novo por pagina, com requests.get solto) --
    parte do fix de lentidao pedido pelo Rafael em 25/09/2026 (10min pra
    sincronizar). A outra parte e' o upsert em lote (ver as duas funcoes
    de sync abaixo), que era o gargalo real: 1 INSERT por linha, 1
    round-trip pro Postgres por nota/credor, em vez de 1 por pagina de 200.
    """
    sessao = requests.Session()
    sessao.auth = HTTPBasicAuth(usuario, senha)
    return base_url.rstrip("/"), sessao


class SincronizacaoTruncada(Exception):
    """A janela de datas tem mais registros do que cabem em max_paginas x 200:
    parar aqui deixaria titulos de fora SEM ninguem saber (v0.44.3 -- antes o
    loop terminava calado no teto de 10.000 e a conferencia mostrava "nao
    encontrada" falso). Quem chama divide a janela (sincronizar_bills_por_mes)."""


def sincronizar_bills(conn, base_url: str, usuario: str, senha: str,
                       data_inicio: date, data_fim: date, max_paginas: int = 50) -> int:
    """
    Puxa os títulos de /v1/bills no intervalo de datas e faz upsert em
    egc.nf_bills_sync SÓ dos que são NFE/NF (desde 01/10/2026; antes
    gravava todos os tipos pra alimentar um fallback sem tipo, removido --
    ver eh_nota_fiscal). Retorna quantos títulos NFE/NF foram sincronizados.

    Upsert em LOTE (execute_values, 1 round-trip por página de até 200
    títulos) -- antes era 1 INSERT por título (o gargalo real dos ~10min
    reportados pelo Rafael em 25/09/2026, não a chamada à API em si).
    """
    base, sessao = _sessao_sienge(base_url, usuario, senha)
    total = 0
    offset = 0
    terminou = False
    with conn.cursor() as cur:
        while offset < LIMITE_PAGINA * max_paginas:
            params = {
                "startDate": data_inicio.isoformat(),
                "endDate": data_fim.isoformat(),
                "limit": LIMITE_PAGINA,
                "offset": offset,
            }
            r = sessao.get(f"{base}/v1/bills", params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            registros = data.get("results", data) if isinstance(data, dict) else data
            if not registros:
                terminou = True
                break
            # 01/10/2026: so' NFE/NF entram no snapshot (ver eh_nota_fiscal).
            # Filtra DEPOIS de ler a pagina -- a API nao tem filtro por tipo
            # validado (nao dava pra confirmar o parametro sem a conta real);
            # a paginacao abaixo continua olhando o tamanho CRU da pagina.
            linhas = [
                (
                    b.get("id"), b.get("debtorId"), b.get("creditorId"),
                    b.get("documentIdentificationId"), b.get("documentNumber"),
                    b.get("issueDate"), b.get("totalInvoiceAmount"),
                    b.get("accessKeyNumber"), b.get("status"),
                )
                for b in registros if eh_nota_fiscal(b.get("documentIdentificationId"))
            ]
            if not linhas:
                if len(registros) < LIMITE_PAGINA:
                    terminou = True
                    break
                offset += LIMITE_PAGINA
                continue
            execute_values(
                cur,
                """
                INSERT INTO egc.nf_bills_sync
                    (bill_id, debtor_id, creditor_id, document_identification_id,
                     document_number, issue_date, total_invoice_amount,
                     access_key_number, status, sincronizado_em)
                VALUES %s
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
                linhas,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, now())",
            )
            total += len(linhas)
            if len(registros) < LIMITE_PAGINA:
                terminou = True
                break
            offset += LIMITE_PAGINA
    if not terminou:
        raise SincronizacaoTruncada(
            f"A janela {data_inicio} a {data_fim} tem mais de {LIMITE_PAGINA * max_paginas} registros no Sienge "
            f"(limite por chamada); divida o periodo."
        )
    return total


def _meses_da_janela(data_inicio: date, data_fim: date) -> list:
    """[(inicio, fim)] por mes civil, cobrindo exatamente [data_inicio, data_fim]."""
    janelas = []
    atual = data_inicio
    while atual <= data_fim:
        prox_mes = (atual.replace(day=1) + timedelta(days=32)).replace(day=1)
        fim_mes = min(prox_mes - timedelta(days=1), data_fim)
        janelas.append((atual, fim_mes))
        atual = fim_mes + timedelta(days=1)
    return janelas


def sincronizar_bills_adaptativo(conn, base_url, usuario, senha, data_inicio: date, data_fim: date,
                                  max_paginas: int = 50) -> int:
    """sincronizar_bills; se a janela estourar o teto por chamada, divide ao meio
    (recursivo, ate' 1 dia) em vez de truncar calado. Upsert e' idempotente,
    entao reler um trecho nao duplica nada."""
    try:
        return sincronizar_bills(conn, base_url, usuario, senha, data_inicio, data_fim, max_paginas)
    except SincronizacaoTruncada:
        if data_inicio >= data_fim:
            raise
        meio = data_inicio + (data_fim - data_inicio) // 2
        return (sincronizar_bills_adaptativo(conn, base_url, usuario, senha, data_inicio, meio, max_paginas)
                + sincronizar_bills_adaptativo(conn, base_url, usuario, senha, meio + timedelta(days=1), data_fim,
                                               max_paginas))


def sincronizar_bills_por_mes(conn, base_url, usuario, senha, data_inicio: date, data_fim: date,
                               log=None) -> dict:
    """Sincroniza mes a mes (cada mes com a protecao de teto). Devolve
    {"AAAA-MM": n_titulos_NFE_NF}. `log(texto)` (opcional) recebe 1 linha por mes
    -- o GitHub Actions imprime isso, entao da' pra ver o volume real do Sienge."""
    por_mes: dict = {}
    for ini, fim in _meses_da_janela(data_inicio, data_fim):
        n = sincronizar_bills_adaptativo(conn, base_url, usuario, senha, ini, fim)
        por_mes[f"{ini:%Y-%m}"] = por_mes.get(f"{ini:%Y-%m}", 0) + n
        if log:
            log(f"  {ini} a {fim}: {n} titulo(s) NFE/NF")
    return por_mes


def sincronizar_creditores(conn, base_url: str, usuario: str, senha: str, max_paginas: int = 50) -> int:
    """Puxa /v1/creditors (paginado) e faz upsert em egc.nf_creditors_sync
    em lote (mesma otimização de sincronizar_bills acima -- ver a nota lá)."""
    base, sessao = _sessao_sienge(base_url, usuario, senha)
    total = 0
    offset = 0
    with conn.cursor() as cur:
        while offset < LIMITE_PAGINA * max_paginas:
            params = {"limit": LIMITE_PAGINA, "offset": offset}
            r = sessao.get(f"{base}/v1/creditors", params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            registros = data.get("results", data) if isinstance(data, dict) else data
            if not registros:
                break
            linhas = [
                (c.get("id"), c.get("name"), c.get("tradeName"), c.get("cnpj"), c.get("cpf"))
                for c in registros
            ]
            execute_values(
                cur,
                """
                INSERT INTO egc.nf_creditors_sync (creditor_id, nome, nome_fantasia, cnpj, cpf, sincronizado_em)
                VALUES %s
                ON CONFLICT (creditor_id) DO UPDATE SET
                    nome = EXCLUDED.nome, nome_fantasia = EXCLUDED.nome_fantasia,
                    cnpj = EXCLUDED.cnpj, cpf = EXCLUDED.cpf, sincronizado_em = now()
                """,
                linhas,
                template="(%s, %s, %s, %s, %s, now())",
            )
            total += len(registros)
            if len(registros) < LIMITE_PAGINA:
                break
            offset += LIMITE_PAGINA
    return total


# ─────────────────────────────────────────────
#  ARQUIVAMENTO DE ENVIOS (v0.46.0, bloco 23)
# ─────────────────────────────────────────────
# "Desfazer upload" = ARQUIVAR o envio (nada e' apagado): as consultas abaixo ignoram imports com
# arquivado_em preenchido. Se o banco ainda nao tem a coluna (bloco 23 nao rodado), tudo funciona
# como antes e arquivar/restaurar avisa que ainda nao esta disponivel.

class ArquivamentoIndisponivel(Exception):
    """Bloco 23 do schema.sql ainda nao foi rodado neste banco."""


_COL_ARQUIVADO = {"ok": False}


def tem_arquivamento(conn) -> bool:
    if _COL_ARQUIVADO["ok"]:
        return True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = 'egc' "
                    "AND table_name = 'nf_manifesto_import' AND column_name = 'arquivado_em'")
        achou = cur.fetchone() is not None
    if achou:
        _COL_ARQUIVADO["ok"] = True
    return achou


def _ativo(conn, alias: str = "") -> str:
    """Condicao SQL "import nao arquivado" (TRUE se o banco ainda nao tem a coluna)."""
    if not tem_arquivamento(conn):
        return "TRUE"
    return f"{alias + '.' if alias else ''}arquivado_em IS NULL"


# ─────────────────────────────────────────────
#  IMPORTAÇÃO DO MANIFESTO (planilha da Receita já parseada por nf_parser)
# ─────────────────────────────────────────────

def gravar_manifesto(conn, empresa_codigo: str, periodo_referencia: str,
                      df, nome_arquivo: str, usuario: str, lote_id: Optional[str] = None) -> str:
    """
    Grava cada linha do DataFrame (já normalizado por
    nf_parser.ler_manifesto_xlsx) em egc.nf_manifesto_import, sob um
    import_id novo (1 import_id = 1 upload = 1 rodada de conferência).
    Retorna o import_id (string uuid) pra encadear com conciliar_import.
    v0.46.0: `lote_id` agrupa os meses do MESMO arquivo (arquivar/restaurar o envio inteiro).
    """
    import_id = str(uuid.uuid4())
    usa_lote = bool(lote_id) and tem_arquivamento(conn)
    with conn.cursor() as cur:
        for _, row in df.iterrows():
            # v0.44.3: data ja' parseada (date) -- nunca a string crua da Receita
            # ("2026.07.06"), que o Postgres/pandas podiam ler com dia/mes trocados.
            data_emissao = row.get("_data_emissao") if "_data_emissao" in row.index else None
            if data_emissao is None or (not isinstance(data_emissao, date) and pd.isna(data_emissao)):
                data_emissao = nf_parser.parse_data_emissao(row.get("DtEmi"))
            cur.execute(
                """
                INSERT INTO egc.nf_manifesto_import
                    (import_id, empresa_codigo, periodo_referencia, numero_nota,
                     numero_normalizado, tipo_documento, data_emissao, valor, cfop,
                     fornecedor_nome, fornecedor_cnpj, cnpj_normalizado, uf,
                     chave_acesso, chave_modelo, chave_serie, chave_numero,
                     arquivo_nome, criado_por, criado_em""" + (", lote_id" if usa_lote else "") + """)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()"""
                + (", %s::uuid" if usa_lote else "") + """)
                """,
                (
                    import_id, empresa_codigo, periodo_referencia,
                    str(row.get("Num") or ""), row.get("_numero_normalizado"),
                    row.get("Tipo"), data_emissao, row.get("_valor_float"), row.get("CFOP"),
                    row.get("Emissor Nome"), row.get("Emissor CNPJ/CPF"), row.get("_cnpj_normalizado"),
                    row.get("UF"), row.get("Chave"), row.get("_chave_modelo"),
                    row.get("_chave_serie"), row.get("_chave_numero"), nome_arquivo, usuario,
                ) + ((str(lote_id),) if usa_lote else ()),
            )
    return import_id


def gravar_ignoradas(conn, import_id: str, empresa_codigo: str, periodo_referencia: str,
                      ignoradas: Optional[pd.DataFrame], usuario: str) -> int:
    """Grava as notas que o upload retirou da conferencia (canceladas e
    Entrada) em egc.nf_manifesto_ignoradas, pra a contadora conferir depois
    (aba "Ignoradas" da planilha e expander da tela). Tabela ainda inexistente
    (bloco 22 do schema nao rodado) -> 0, sem quebrar a conferencia."""
    if ignoradas is None or ignoradas.empty:
        return 0
    try:
        with conn.cursor() as cur:
            for _, r in ignoradas.iterrows():
                data = r.get("data_emissao")
                data = None if (data is None or (not isinstance(data, date) and pd.isna(data))) else data
                valor = r.get("valor")
                cur.execute(
                    """
                    INSERT INTO egc.nf_manifesto_ignoradas
                        (import_id, empresa_codigo, periodo_referencia, numero_nota, data_emissao, valor, cfop,
                         fornecedor_nome, fornecedor_cnpj, cnpj_normalizado, tipo_doc, natureza, motivo, criado_por, criado_em)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                    """,
                    (import_id, empresa_codigo, periodo_referencia, r.get("numero_nota"), data,
                     None if valor is None or pd.isna(valor) else float(valor),
                     None if pd.isna(r.get("cfop")) else r.get("cfop"),
                     None if pd.isna(r.get("fornecedor_nome")) else r.get("fornecedor_nome"),
                     None if pd.isna(r.get("fornecedor_cnpj")) else r.get("fornecedor_cnpj"),
                     r.get("cnpj_normalizado"),
                     None if pd.isna(r.get("tipo_doc")) else r.get("tipo_doc"),
                     None if pd.isna(r.get("natureza")) else r.get("natureza"),
                     r.get("motivo"), usuario),
                )
        return len(ignoradas)
    except psycopg2.errors.UndefinedTable:
        return 0


COLUNAS_IGNORADAS = ["numero_nota", "data_emissao", "valor", "cfop", "fornecedor_nome", "fornecedor_cnpj",
                     "tipo_doc", "natureza", "motivo", "cnpj_normalizado"]  # a ultima e' interna (casa com o manifesto)


def listar_ignoradas(conn, import_id: str) -> pd.DataFrame:
    """Notas retiradas da conferencia desta rodada (vazio se a tabela nao existe)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT " + ", ".join(COLUNAS_IGNORADAS) + " FROM egc.nf_manifesto_ignoradas "
                "WHERE import_id = %s ORDER BY motivo, numero_nota", (import_id,))
            return pd.DataFrame(cur.fetchall(), columns=COLUNAS_IGNORADAS)
    except psycopg2.errors.UndefinedTable:
        return pd.DataFrame(columns=COLUNAS_IGNORADAS)


def _brl(v) -> str:
    return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def observacoes_de_ignoradas(manifesto: pd.DataFrame, ignoradas: pd.DataFrame) -> dict:
    """{indice_da_nota: texto} para nota PENDENTE cujo fornecedor tem uma nota
    de ENTRADA (devolucao/retorno) ignorada no mesmo upload, de valor igual ou
    proximo -- caso das notas 5700 (devolvida pela 5735) e 29968 (devolucao
    71713) da Energia 07/2026, que a contadora tirou a mao. NAO exclui nada: so' avisa, a
    contadora decide pela planilha. Valor igual (+-1 centavo) vem primeiro."""
    if ignoradas is None or ignoradas.empty or manifesto.empty:
        return {}
    por_cnpj: dict = {}
    for _, g in ignoradas.iterrows():
        # So' ENTRADA (devolucao/retorno) pode anular uma compra. Nota cancelada
        # + reemissao e' o fluxo normal (224 cancelada -> 225 emitida) e a
        # reemitida E' a nota valida -- avisar nela so' faria ruido.
        if not str(g["motivo"]).startswith("Entrada"):
            continue
        por_cnpj.setdefault(g["cnpj_normalizado"], []).append(g)
    obs: dict = {}
    for idx, row in manifesto.iterrows():
        outras = por_cnpj.get(row["_cnpj_normalizado"])
        if not outras:
            continue
        valor = row["_valor_float"]

        def _igual(g):
            return pd.notna(g["valor"]) and pd.notna(valor) and round(abs(float(g["valor"]) - float(valor)), 2) <= TOLERANCIA_VALOR

        def _proximo(g):   # devolucao parcial/quase igual (ex.: 7.879,50 x 7.809,60)
            return pd.notna(g["valor"]) and pd.notna(valor) and float(valor) != 0 \
                and abs(float(g["valor"]) - float(valor)) / abs(float(valor)) <= PROXIMIDADE_VALOR_IGNORADA

        relevantes = sorted((g for g in outras if _igual(g) or _proximo(g)), key=lambda g: (not _igual(g), str(g["numero_nota"])))
        if not relevantes:
            continue   # mesmo fornecedor mas valor sem relacao: nao polui a observacao
        partes = []
        for g in relevantes[:3]:
            tipo = "devolução/entrada"
            igual = "mesmo valor" if _igual(g) else f"{_brl(g['valor'])}, valor próximo"
            partes.append(f"{tipo} nº {g['numero_nota']} ({igual})")
        obs[idx] = "Mesmo fornecedor tem, neste manifesto, " + "; ".join(partes) + " — confira se anula esta nota."
    return obs


def ultima_sincronizacao(conn):
    """
    Timestamp da sincronização mais recente entre nf_bills_sync e
    nf_creditors_sync (o mais antigo dos dois "ganha", já que uma
    conferência precisa dos dois atualizados). None se nunca sincronizou
    -- usado pela tela pra avisar a contadora antes de rodar a
    conferência com dado do Sienge desatualizado ou inexistente (pedido
    do Rafael 25/09/2026: não travar o botão de conferência, só avisar,
    já que agora o sync roda sozinho todo dia via GitHub Actions).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT LEAST(
                (SELECT MAX(sincronizado_em) FROM egc.nf_bills_sync),
                (SELECT MAX(sincronizado_em) FROM egc.nf_creditors_sync)
            )
            """
        )
        row = cur.fetchone()
    return row[0] if row else None


# ─────────────────────────────────────────────
#  CONCILIAÇÃO
# ─────────────────────────────────────────────

def _carregar_bills_creditores(conn) -> pd.DataFrame:
    """
    FIX_20260928f: acrescentados debtor_id, issue_date e creditor_nome --
    nao usados pelo matching direto (_classificar_nota), mas necessarios
    pra identificar_e_gravar_bills_orfaos (direcao reversa, ver abaixo).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT b.bill_id, b.debtor_id, b.creditor_id, b.document_identification_id,
                   b.document_number, b.issue_date, b.total_invoice_amount, b.access_key_number,
                   c.cnpj AS creditor_cnpj, c.nome AS creditor_nome
            FROM egc.nf_bills_sync b
            LEFT JOIN egc.nf_creditors_sync c ON c.creditor_id = b.creditor_id
            WHERE UPPER(TRIM(b.document_identification_id)) IN ('NFE', 'NF')
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


FASES_CLASSIFICACAO = ("LANCADA", "VALOR_DIVERGENTE", "NUMERO_DIVERGENTE")


def _escolher_bill(candidatos: pd.DataFrame, usados: set, debtors_preferidos: Optional[set]):
    """Escolhe 1 titulo entre os candidatos. Prefere (1) titulo ainda nao
    consumido por outra nota e (2) titulo cujo devedor e' da empresa do
    manifesto; desempate pelo menor bill_id (resultado deterministico)."""
    def chave(b):
        usado = int(b["bill_id"]) in usados
        fora = bool(debtors_preferidos) and (pd.isna(b.get("debtor_id")) or int(b["debtor_id"]) not in debtors_preferidos)
        return (usado, fora, int(b["bill_id"]))
    ordenados = sorted((r for _, r in candidatos.iterrows()), key=chave)
    return ordenados[0]


def _distancia_edicao(a: str, b: str) -> int:
    """Levenshtein (numeros de nota, strings curtas)."""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        atual = [i]
        for j, cb in enumerate(b, 1):
            atual.append(min(anterior[j] + 1, atual[j - 1] + 1, anterior[j - 1] + (ca != cb)))
        anterior = atual
    return anterior[-1]


def numeros_parecidos(a, b) -> bool:
    """v0.45.1: NUMERO_DIVERGENTE so' vale quando os numeros PARECEM o mesmo digitado errado: ate' 2 digitos
    de diferenca (troca, falta ou sobra), ou um e' o final do outro (serie/prefixo colado, ex.: 1908 x 20261908).
    Antes bastava CNPJ+valor+data: fornecedor com nota recorrente de mesmo valor (14327 x 14281, 2.753,20) ou valor
    redondo (R$ 301,34 x NFE 1277603) virava 'erro de digitacao' falso."""
    a, b = str(a or "").lstrip("0"), str(b or "").lstrip("0")
    if not a or not b:
        return False
    curto, longo = sorted((a, b), key=len)
    if len(curto) >= 4 and longo.endswith(curto):
        return True
    return len(curto) >= 3 and _distancia_edicao(a, b) <= DISTANCIA_MAX_NUMERO_DIVERGENTE


def _montar_indice(bills: pd.DataFrame):
    """(universo NFE/NF, {cnpj: titulos}, {chave: titulos}) -- pra classificar
    milhares de notas sem varrer o espelho inteiro a cada uma (v0.45.0: o ano
    todo da Energia, ~1.800 notas x ~15 mil titulos, levava ~18 s)."""
    universo = bills[bills["document_identification_id"].astype(str).str.strip().isin(_CODIGOS_NOTA_FISCAL)] if not bills.empty else bills
    if universo.empty:
        return universo, {}, {}
    por_cnpj = {k: g for k, g in universo.groupby("cnpj_normalizado")}
    com_chave = universo[universo["access_key_number"] != ""]
    por_chave = {k: g for k, g in com_chave.groupby("access_key_number")} if not com_chave.empty else {}
    return universo, por_cnpj, por_chave


def _classificar_nota(row, bills: pd.DataFrame, usados: Optional[set] = None,
                      fases: tuple = FASES_CLASSIFICACAO, debtors_preferidos: Optional[set] = None,
                      indice=None) -> dict:
    """Classifica UMA nota do manifesto contra os titulos do Sienge.

    v0.44.3: `usados` (bill_ids ja' consumidos por outras notas na mesma
    rodada) e `fases` permitem que classificar_manifesto rode o matching em
    fases (LANCADA -> VALOR_DIVERGENTE -> NUMERO_DIVERGENTE) sem que um titulo
    seja "candidato de numero divergente" de uma nota quando ele ja' e' o
    titulo certo de OUTRA nota (causava ~75% de NUMERO_DIVERGENTE falso).
    Sem esses parametros o comportamento e' o de sempre (nota isolada)."""
    usados = usados if usados is not None else set()
    cnpj = row["_cnpj_normalizado"]
    numero = row["_numero_normalizado"]
    valor = row["_valor_float"]
    chave = row.get("Chave") or ""

    if indice is None:
        indice = _montar_indice(bills)
    universo, por_cnpj, por_chave = indice
    do_cnpj_todos = por_cnpj.get(cnpj, universo.iloc[0:0]) if not universo.empty else universo

    def _bate_valor(v):
        # FIX_20260928c (Rafael pediu pra conferir pendencias_nf_ENERGIA_07_2026.xlsx
        # contra o manifesto ja conferido a mao pela contadora): nota nº 10486
        # (Gramaeira Pereira, Sienge R$ 6.000,01 x manifesto R$ 6.000,00 -- 1
        # centavo, dentro da TOLERANCIA_VALOR) saia como VALOR_DIVERGENTE em vez
        # de LANCADA. Causa: abs(6000.01 - 6000.0) em float da 0.010000000000218,
        # que e' MAIOR que 0.01 por erro de representacao binaria -- nunca bate
        # o "<=" mesmo a diferenca sendo exatamente 1 centavo. round() pros
        # centavos antes de comparar elimina o erro de ponto flutuante sem
        # afrouxar a tolerancia real (ainda rejeita 2+ centavos de diferenca).
        return v is not None and pd.notna(v) and valor is not None and pd.notna(valor) \
            and round(abs(float(v) - float(valor)), 2) <= TOLERANCIA_VALOR

    def _res(status, confianca, b, observacao=None):
        return dict(status=status, confianca=confianca, sienge_bill_id=int(b["bill_id"]),
                    sienge_valor=float(b["total_invoice_amount"]), observacao=observacao)

    if universo.empty:
        return dict(status="NAO_ENCONTRADA", confianca=None, sienge_bill_id=None, sienge_valor=None, observacao=None)

    if "LANCADA" in fases:
        # 1º passe: chave de acesso idêntica (confiança máxima), só no universo restrito.
        if chave:
            achou_chave = por_chave.get(chave)
            if achou_chave is not None and not achou_chave.empty:
                return _res("LANCADA", "CHAVE", _escolher_bill(achou_chave, usados, debtors_preferidos))

    # 2º passe: CNPJ + número exatos, dentro do universo NFE/NF.
    candidatos = do_cnpj_todos[do_cnpj_todos["numero_normalizado"] == numero]
    if not candidatos.empty:
        bate = candidatos[candidatos["total_invoice_amount"].apply(_bate_valor)]
        if not bate.empty:
            if "LANCADA" in fases:
                return _res("LANCADA", "NUMERO_CNPJ_VALOR", _escolher_bill(bate, usados, debtors_preferidos))
        elif "VALOR_DIVERGENTE" in fases:
            b = _escolher_bill(candidatos, usados, debtors_preferidos)
            return _res("VALOR_DIVERGENTE", "NUMERO_CNPJ", b,
                        f"Sienge tem R$ {b['total_invoice_amount']}, manifesto tem R$ {valor}")
        return dict(status="NAO_ENCONTRADA", confianca=None, sienge_bill_id=None, sienge_valor=None, observacao=None)

    # CNPJ + valor batem, número não -- possível erro de digitação do número.
    # So' titulos que nenhuma outra nota da rodada consumiu (v0.44.3).
    if "NUMERO_DIVERGENTE" in fases:
        do_cnpj = do_cnpj_todos   # ja' filtrado por CNPJ (indice) ANTES de comparar valor
        mesmo_cnpj_valor = do_cnpj[do_cnpj["total_invoice_amount"].apply(_bate_valor)
                                   & ~do_cnpj["bill_id"].astype(int).isin(usados)] if not do_cnpj.empty else do_cnpj
        # v0.45.0: so' titulos com data proxima da nota (titulo sem data = sem informacao, nao exclui).
        data_nota = row.get("_data_emissao")
        if not mesmo_cnpj_valor.empty and data_nota is not None and pd.notna(data_nota):
            datas = pd.to_datetime(mesmo_cnpj_valor["issue_date"], errors="coerce")
            dist = (datas - pd.Timestamp(data_nota)).abs()
            mesmo_cnpj_valor = mesmo_cnpj_valor[datas.isna() | (dist <= pd.Timedelta(days=JANELA_NUMERO_DIVERGENTE_DIAS))]
        if not mesmo_cnpj_valor.empty:
            mesmo_cnpj_valor = mesmo_cnpj_valor[mesmo_cnpj_valor["numero_normalizado"].map(lambda n: numeros_parecidos(n, numero))]
        if not mesmo_cnpj_valor.empty:
            b = _escolher_bill(mesmo_cnpj_valor, usados, debtors_preferidos)
            return _res("NUMERO_DIVERGENTE", "CNPJ_VALOR", b,
                        f"Sienge tem nota nº {b['document_number']}, manifesto tem nº {row.get('Num')}")

    return dict(status="NAO_ENCONTRADA", confianca=None, sienge_bill_id=None, sienge_valor=None, observacao=None)


def classificar_manifesto(manifesto: pd.DataFrame, bills: pd.DataFrame,
                          mapa_debtor: Optional[dict] = None,
                          mapas_por_import: Optional[dict] = None) -> dict:
    """Classifica TODAS as notas do manifesto de uma rodada. Devolve
    {indice_da_linha: resultado}. Roda em fases pra um titulo nunca ser
    usado duas vezes como "numero divergente":

      1. LANCADA   (chave, ou CNPJ+numero+valor)
      2. VALOR_DIVERGENTE (CNPJ+numero, valor diferente)
      3. NUMERO_DIVERGENTE (CNPJ+valor, so' com titulos ainda sem dono)

    Em cada fase, titulo ja' consumido por outra nota so' e' reaproveitado
    quando a nota e' duplicada no manifesto (mesmo CNPJ+numero+valor).
    `mapa_debtor` ({debtor_id: empresa}) desempata entre titulos iguais
    preferindo o devedor da empresa do manifesto.

    v0.45.0: o manifesto pode juntar VARIOS meses da mesma empresa (coluna
    `_import_id`); o consumo de titulos e' comum a todos (um titulo usado
    por julho nao serve de "numero divergente" pra agosto). `mapas_por_import`
    ({import_id: mapa}) mantem o mapa de devedores de cada import (o de cada
    um nao conta a propria rodada -- ver _mapear_debtor_para_empresa)."""
    mapa_debtor = mapa_debtor or {}
    preferidos_por_empresa: dict = {}
    resultados: dict = {}
    usados: set = set()
    indice = _montar_indice(bills)
    for fase in FASES_CLASSIFICACAO:
        for idx, row in manifesto.iterrows():
            if idx in resultados:
                continue
            empresa = row.get("_empresa_codigo")
            mapa_linha = mapa_debtor
            imp = row.get("_import_id") if "_import_id" in row.index else None
            if mapas_por_import and imp in mapas_por_import:
                mapa_linha = mapas_por_import[imp]
            chave_pref = (imp if mapas_por_import else None, empresa)
            if chave_pref not in preferidos_por_empresa:
                preferidos_por_empresa[chave_pref] = {d for d, e in mapa_linha.items() if e == empresa}
            r = _classificar_nota(row, bills, usados, (fase,), preferidos_por_empresa[chave_pref], indice)
            if r["status"] != "NAO_ENCONTRADA":
                resultados[idx] = r
                usados.add(r["sienge_bill_id"])
    for idx in manifesto.index:
        resultados.setdefault(idx, dict(status="NAO_ENCONTRADA", confianca=None, sienge_bill_id=None,
                                        sienge_valor=None, observacao=None))
    return resultados


# ─────────────────────────────────────────────
#  DIREÇÃO REVERSA -- título do Sienge sem nota no manifesto (bloco 14)
# ─────────────────────────────────────────────
#
# FIX_20260928f (pedido explicito do Rafael, 28/09/2026: "A nota q estiver
# no Sienge, e não tiver na receita, tem q virar pendencia tb, (não é pra
# acontecer, mas caso aconteça importante não passar batido)").
#
# Problema de escopo que _classificar_nota (direção normal) não tem: o
# Sienge aqui é 1 conta só compartilhada pelas 6 empresas do grupo (ver
# nota no topo do modulo) -- sincronizar_bills nao filtra por empresa, e
# nao ha' hoje nenhum de-para "debtor_id do Sienge -> qual das 6 empresas"
# documentado nem na API (so' 14% dos titulos tem chave de acesso, e
# debtor_id nunca foi usado ate' este fix). Sem isolar por empresa, um
# titulo de QUALQUER uma das outras 5 empresas apareceria como "pendencia"
# de todas -- pior que nao ter o alerta (a contadora perderia confianca no
# botao inteiro por causa de ruido).
#
# Solucao: aprender sozinho o de-para a partir do proprio historico de
# matches LANCADA ja feitos (_mapear_debtor_para_empresa) -- um debtor_id
# so' e' usado pra flagar orfao quando ele SEMPRE bateu com a mesma
# empresa ate' hoje. Ambiguo ou nunca visto = fica de fora (nao arrisca
# falso positivo cruzando empresa errada). Consequencia aceita: o alerta
# reverso so' funciona pra empresa que ja' teve pelo menos 1 nota
# encontrada no Sienge alguma vez -- nao afeta o restante do fluxo.

STATUS_LANCADA_OUTRA_EMPRESA = "LANCADA_OUTRA_EMPRESA"


def _historico_debtor_empresas(conn, excluir_import_id: Optional[str] = None) -> dict:
    """{debtor_id: {empresa_codigo: n_matches}} a partir dos matches LANCADA
    (e LANCADA_OUTRA_EMPRESA nao conta: ela ja' foi gerada USANDO o mapa)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT b.debtor_id, m.empresa_codigo, COUNT(*) AS n
            FROM egc.nf_conciliacao c
            JOIN egc.nf_manifesto_import m ON m.id = c.manifesto_id
            JOIN egc.nf_bills_sync b ON b.bill_id = c.sienge_bill_id
            WHERE c.status = 'LANCADA' AND b.debtor_id IS NOT NULL
              AND (%s::uuid IS NULL OR c.import_id <> %s::uuid) AND __ATIVO_M__
            GROUP BY b.debtor_id, m.empresa_codigo
            """.replace("__ATIVO_M__", _ativo(conn, "m")),
            (excluir_import_id, excluir_import_id),
        )
        linhas = cur.fetchall()
    por_debtor: dict = {}
    for debtor_id, empresa_codigo, n in linhas:
        por_debtor.setdefault(int(debtor_id), {})[empresa_codigo] = int(n)
    return por_debtor


def _mapear_debtor_para_empresa(conn, excluir_import_id: Optional[str] = None) -> dict:
    """Devolve {debtor_id: empresa_codigo} -- SO' o que foi confirmado em
    egc.nf_debtor_empresa ("o app segue o que o Sienge dita": o debtor do
    titulo diz a qual empresa ele foi lancado).

    v0.45.1: o mapa APRENDIDO do historico deixou de valer como regra (so'
    aparece como sugestao na tela). Motivo, visto em producao: sem o mapa
    confirmado, a 1a empresa conferida "adotava" um devedor que so' tinha tocado
    por acaso (devedor 5 = Construtora virou ENERGIA) e a Construtora saiu com
    55 notas "lancadas em outra empresa" e 55 titulos como orfaos da Energia.
    `excluir_import_id` fica na assinatura por compatibilidade."""
    return carregar_mapa_debtor_manual(conn)


def carregar_mapa_debtor_manual(conn) -> dict:
    """{debtor_id: empresa_codigo} confirmado manualmente (bloco 19 do schema).
    Tabela ainda inexistente -> {} (comportamento anterior)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT debtor_id, empresa_codigo FROM egc.nf_debtor_empresa")
            return {int(d): e for d, e in cur.fetchall()}
    except psycopg2.errors.UndefinedTable:
        return {}


def listar_debtors_sienge(conn) -> pd.DataFrame:
    """Um debtor_id por linha, com quantos titulos NFE/NF ele tem, a empresa
    que o historico sugere (aprendida) e a confirmada manualmente."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT debtor_id, COUNT(*) AS titulos FROM egc.nf_bills_sync
               WHERE debtor_id IS NOT NULL AND UPPER(TRIM(document_identification_id)) IN ('NFE','NF')
               GROUP BY debtor_id ORDER BY COUNT(*) DESC"""
        )
        df = pd.DataFrame(cur.fetchall(), columns=["debtor_id", "titulos"])
    # v0.46.3: 3 titulos recentes de cada devedor (nº, credor, valor) pra conferir no Sienge a que empresa pertence.
    exemplos: dict = {}
    if not df.empty:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT debtor_id, bill_id, document_identification_id, document_number, creditor_nome, total_invoice_amount
                   FROM (
                     SELECT b.debtor_id, b.bill_id, b.document_identification_id, b.document_number,
                            COALESCE(cr.nome, 'credor ' || b.creditor_id::text) AS creditor_nome, b.total_invoice_amount,
                            ROW_NUMBER() OVER (PARTITION BY b.debtor_id ORDER BY b.issue_date DESC NULLS LAST, b.bill_id DESC) AS rn
                     FROM egc.nf_bills_sync b
                     LEFT JOIN egc.nf_creditors_sync cr ON cr.creditor_id = b.creditor_id
                     WHERE b.debtor_id IS NOT NULL AND UPPER(TRIM(b.document_identification_id)) IN ('NFE','NF')
                   ) x WHERE rn <= 3 ORDER BY debtor_id, rn"""
            )
            for d, bid, tipo, num, cred, val in cur.fetchall():
                exemplos.setdefault(int(d), []).append(
                    f"título {bid} ({(tipo or '').strip()} {num}) · {str(cred or '')[:28]} · R$ {float(val or 0):,.2f}")
    df["exemplos"] = df["debtor_id"].map(lambda d: exemplos.get(int(d), []))
    manual = carregar_mapa_debtor_manual(conn)
    historico = _historico_debtor_empresas(conn)
    # aprendida = so' quando o historico aponta UMA empresa (e nao ha' manual)
    aprendido = {d: next(iter(emp)) for d, emp in historico.items() if len(emp) == 1 and d not in manual}
    df["empresa_aprendida"] = df["debtor_id"].map(lambda d: aprendido.get(int(d)))
    df["empresa_confirmada"] = df["debtor_id"].map(lambda d: manual.get(int(d)))
    # v0.44.3: historico ambiguo ("ENERGIA (2283), CONST (59)") aparece na tela
    # em vez de virar "—" sem explicacao.
    df["historico_ambiguo"] = df["debtor_id"].map(
        lambda d: ", ".join(f"{e} ({n})" for e, n in sorted(historico.get(int(d), {}).items(), key=lambda x: -x[1]))
        if len(historico.get(int(d), {})) > 1 else None)
    return df


def salvar_mapa_debtor(conn, debtor_id: int, empresa_codigo: Optional[str], usuario: str) -> None:
    """Confirma (ou, com empresa_codigo=None, remove a confirmacao de) o de-para."""
    with conn.cursor() as cur:
        if empresa_codigo is None:
            cur.execute("DELETE FROM egc.nf_debtor_empresa WHERE debtor_id = %s", (int(debtor_id),))
        else:
            cur.execute(
                """INSERT INTO egc.nf_debtor_empresa (debtor_id, empresa_codigo, atualizado_por, atualizado_em)
                   VALUES (%s, %s, %s, now())
                   ON CONFLICT (debtor_id) DO UPDATE SET empresa_codigo = EXCLUDED.empresa_codigo,
                       atualizado_por = EXCLUDED.atualizado_por, atualizado_em = now()""",
                (int(debtor_id), empresa_codigo, usuario),
            )


def checar_empresa_do_lancamento(resultado: dict, empresa_manifesto: str, debtor_id, mapa_debtor: dict) -> dict:
    """Verificacao "foi lancado na empresa errada?": o titulo casou com a nota
    do manifesto de `empresa_manifesto` (a Receita diz que a nota e' dela),
    mas o debtor do titulo no Sienge pertence a OUTRA empresa. Funcao pura.
    So' age em LANCADA; debtor desconhecido/ambiguo nao gera alarme."""
    if resultado.get("status") != "LANCADA" or debtor_id is None or pd.isna(debtor_id):
        return resultado
    empresa_sienge = mapa_debtor.get(int(debtor_id))
    if not empresa_sienge or empresa_sienge == empresa_manifesto:
        return resultado
    novo = dict(resultado)
    novo["status"] = STATUS_LANCADA_OUTRA_EMPRESA
    novo["observacao"] = (
        f"Nota do manifesto de {empresa_manifesto}, mas o título {resultado.get('sienge_bill_id')} está lançado no "
        f"Sienge na empresa {empresa_sienge} (devedor {int(debtor_id)}) — possível lançamento na empresa errada."
    )
    return novo


def _filtrar_bills_orfaos(bills: pd.DataFrame, mapa_debtor: dict, empresa_codigo: str,
                           bill_ids_ja_associados: set, data_min, data_max) -> pd.DataFrame:
    """
    Função pura (sem tocar banco) -- o filtro de verdade por trás de
    identificar_e_gravar_bills_orfaos, testada isolada como
    _classificar_nota. "Órfão" = título NFE/NF, dentro da janela de datas
    do import, cujo debtor_id mapeia (sem ambiguidade) pra esta empresa, e
    que não está associado a NENHUMA nota do manifesto ainda (nem como
    match perfeito nem como candidato de VALOR/NUMERO_DIVERGENTE -- esses
    já têm sienge_bill_id preenchido em egc.nf_conciliacao).
    """
    if bills.empty:
        return bills
    universo = bills[bills["document_identification_id"].astype(str).str.strip().isin(_CODIGOS_NOTA_FISCAL)].copy()
    if universo.empty:
        return universo
    universo["_empresa_do_debtor"] = universo["debtor_id"].apply(
        lambda d: mapa_debtor.get(int(d)) if pd.notna(d) else None
    )
    universo = universo[universo["_empresa_do_debtor"] == empresa_codigo]
    # Comparacao vetorizada (>=/<=), nao .apply -- com 0 linhas .apply
    # devolve a serie ORIGINAL sem converter pra bool (quirk conhecido do
    # pandas: sem elemento nenhum pra inferir o tipo de retorno, ele so'
    # devolve a serie de entrada), e df[serie_nao_bool] descarta as
    # colunas em vez de filtrar -- comparacao vetorizada sempre da' bool,
    # vazio ou nao.
    datas = pd.to_datetime(universo["issue_date"])
    universo = universo[datas.notna() & (datas >= data_min) & (datas <= data_max)]
    universo = universo[~universo["bill_id"].astype(int).isin(bill_ids_ja_associados)]
    return universo.drop(columns=["_empresa_do_debtor"])


def _bills_associados_no_escopo(conn, import_id: str, empresa_codigo: str) -> set:
    """Titulos que ja' tem dono "valido" pra efeito de 'Sienge sem manifesto':
      - os associados a notas DESTA rodada (qualquer status);
      - os associados na ULTIMA rodada de cada OUTRO periodo da MESMA empresa
        (titulo de julho que foi conferido no manifesto de agosto nao e' orfao).
    v0.44.3: antes olhava QUALQUER rodada de QUALQUER empresa -- (a) rodada
    antiga/duplicada do mesmo periodo escondia orfao de verdade e o resultado
    dependia da ordem em que as conferencias foram rodadas; (b) titulo
    lancado no devedor de uma empresa mas com nota no manifesto de OUTRA
    nunca aparecia como orfao da primeira. Agora so' conta o escopo da propria
    empresa; o caso (b) vira orfao com a observacao 'consta no manifesto de X'."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT c.sienge_bill_id
            FROM egc.nf_conciliacao c
            WHERE c.sienge_bill_id IS NOT NULL
              AND (c.import_id = %s
                   OR c.import_id IN (
                        SELECT DISTINCT ON (periodo_referencia) import_id
                        FROM egc.nf_manifesto_import
                        WHERE empresa_codigo = %s AND __ATIVO__
                          AND periodo_referencia IS DISTINCT FROM (
                                SELECT periodo_referencia FROM egc.nf_manifesto_import
                                WHERE import_id = %s LIMIT 1)
                        ORDER BY periodo_referencia, criado_em DESC))
            """.replace("__ATIVO__", _ativo(conn)),
            (import_id, empresa_codigo, import_id),
        )
        return {int(r[0]) for r in cur.fetchall()}


def avisos_conciliacao(conn, import_id: str, empresa_codigo: str) -> list:
    """Avisos que a tela mostra junto do resultado, pra "0 Sienge sem
    manifesto" nunca significar "nao deu pra verificar" sem a contadora saber:
      1) nenhum devedor do Sienge esta' associado a esta empresa -> a direcao
         reversa nao roda;
      2) o espelho do Sienge nao cobre o periodo do manifesto (titulos so'
         a partir de X / ate' Y) -> 'nao encontrada' pode ser falta de dado."""
    avisos: list = []
    try:
        mapa = _mapear_debtor_para_empresa(conn)
        if empresa_codigo not in set(mapa.values()):
            avisos.append(
                f"Nenhum devedor do Sienge está associado à empresa {empresa_codigo}: a checagem "
                f"\"Sienge sem manifesto\" NÃO foi feita (o resultado 0 não significa 'sem divergência'). "
                f"Confirme o devedor no painel \"Empresa de cada devedor do Sienge\" e rode de novo."
            )
        with conn.cursor() as cur:
            cur.execute("SELECT MIN(data_emissao), MAX(data_emissao) FROM egc.nf_manifesto_import WHERE import_id = %s",
                        (import_id,))
            man_min, man_max = cur.fetchone() or (None, None)
            cur.execute("SELECT MIN(issue_date), MAX(issue_date) FROM egc.nf_bills_sync "
                        "WHERE UPPER(TRIM(document_identification_id)) IN ('NFE','NF')")
            esp_min, esp_max = cur.fetchone() or (None, None)
        tol = pd.Timedelta(days=JANELA_COBERTURA_DIAS)
        if man_min and esp_min and pd.Timestamp(esp_min) - pd.Timestamp(man_min) > tol:
            avisos.append(
                f"O espelho do Sienge só tem títulos a partir de {pd.Timestamp(esp_min):%d/%m/%Y}, mas o manifesto "
                f"tem notas desde {pd.Timestamp(man_min):%d/%m/%Y}: notas anteriores podem aparecer como "
                f"\"não encontrada\" só por falta de dado. Ajuste o \"De\" do passo 1 e atualize do Sienge."
            )
        if man_max and esp_max and pd.Timestamp(man_max) - pd.Timestamp(esp_max) > tol:
            avisos.append(
                f"O espelho do Sienge só tem títulos até {pd.Timestamp(esp_max):%d/%m/%Y}, mas o manifesto "
                f"tem notas até {pd.Timestamp(man_max):%d/%m/%Y}: atualize do Sienge antes de conferir."
            )
    except psycopg2.Error:
        conn.rollback()
    return avisos


def janela_orfaos(periodo_referencia, data_min, data_max):
    """Intervalo de datas (issue_date do titulo) em que um titulo do Sienge pode ser "Sienge sem
    manifesto" de UM periodo.

    v0.45.2: e' o MES-CALENDARIO do periodo ("08/2026" -> 01/08 a 31/08), sem folga. Antes era
    min/max das notas do import +-15 dias: as janelas de meses vizinhos se sobrepunham (o mesmo titulo
    virava orfao de 2 ou 3 periodos) e o ultimo mes pegava titulos do mes seguinte, que ainda nem tem
    manifesto. Titulo que pertence a nota de outro mes nao precisa de folga: ele casa com a nota (o
    casamento nao depende de data) e some dos orfaos. Periodo fora do padrao MM/AAAA: cai no
    comportamento antigo (min/max das notas +-JANELA_DATA_ORFAOS_DIAS). Devolve (inicio, fim) ou None."""
    m = re.match(r"^\s*(\d{1,2})\s*/\s*(\d{4})\s*$", str(periodo_referencia or ""))
    if m and 1 <= int(m.group(1)) <= 12:
        ini = pd.Timestamp(year=int(m.group(2)), month=int(m.group(1)), day=1)
        return ini, ini + pd.offsets.MonthEnd(0)
    if data_min and data_max:
        folga = pd.Timedelta(days=JANELA_DATA_ORFAOS_DIAS)
        return pd.Timestamp(data_min) - folga, pd.Timestamp(data_max) + folga
    return None


def identificar_e_gravar_bills_orfaos(conn, import_id: str, empresa_codigo: str) -> int:
    """
    Roda o filtro reverso pra um import_id (chamado por conciliar_import,
    logo depois do matching normal) e grava o resultado em
    egc.nf_bills_orfaos. Idempotente como conciliar_import: apaga e
    regrava os órfãos deste import_id, preservando pendencia_status já
    setado manualmente. Janela de datas = mes-calendario do periodo do import
    (janela_orfaos, v0.45.2: cada titulo so' e' orfao do mes da sua data, e so'
    se esse mes tem manifesto). Periodo fora do padrao e sem nenhuma
    data_emissao no manifesto: nao da' pra montar a janela -- nao arrisca,
    devolve 0.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MIN(data_emissao), MAX(data_emissao), MAX(periodo_referencia) "
            "FROM egc.nf_manifesto_import WHERE import_id = %s",
            (import_id,),
        )
        data_min, data_max, periodo = cur.fetchone() or (None, None, None)
    janela = janela_orfaos(periodo, data_min, data_max)
    if janela is None:
        return 0
    data_min_janela, data_max_janela = janela

    try:
        mapa_debtor = _mapear_debtor_para_empresa(conn)
        bills = _carregar_bills_creditores(conn)

        ja_associados = _bills_associados_no_escopo(conn, import_id, empresa_codigo)

        orfaos = _filtrar_bills_orfaos(
            bills, mapa_debtor, empresa_codigo, ja_associados, data_min_janela, data_max_janela
        )

        with conn.cursor() as cur:
            # status manual: o deste import manda; senao, o de imports ANTIGOS do mesmo
            # empresa+periodo (re-upload do mes -- v0.45.0).
            cur.execute(
                """
                SELECT o.bill_id, o.pendencia_status FROM egc.nf_bills_orfaos o
                WHERE o.pendencia_status IS NOT NULL AND o.pendencia_status <> 'PENDENTE'
                  AND (o.import_id = %s::uuid OR o.import_id IN (
                        SELECT DISTINCT m.import_id FROM egc.nf_manifesto_import m
                        WHERE m.empresa_codigo = %s AND m.import_id <> %s::uuid
                          AND m.periodo_referencia = (SELECT periodo_referencia FROM egc.nf_manifesto_import
                                                      WHERE import_id = %s::uuid LIMIT 1)))
                ORDER BY (o.import_id = %s::uuid) ASC, o.atualizado_em ASC NULLS FIRST
                """,
                (import_id, empresa_codigo, import_id, import_id, import_id),
            )
            status_preservados = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute("DELETE FROM egc.nf_bills_orfaos WHERE import_id = %s", (import_id,))

            for _, b in orfaos.iterrows():
                bill_id = int(b["bill_id"])
                cur.execute(
                    """
                    INSERT INTO egc.nf_bills_orfaos
                        (import_id, empresa_codigo, bill_id, document_number, issue_date,
                         total_invoice_amount, creditor_nome, creditor_cnpj, pendencia_status, atualizado_em)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    """,
                    (import_id, empresa_codigo, bill_id, b["document_number"], b["issue_date"],
                     float(b["total_invoice_amount"]) if pd.notna(b["total_invoice_amount"]) else None,
                     b.get("creditor_nome"), b.get("creditor_cnpj"),
                     status_preservados.get(bill_id, "PENDENTE")),
                )
    except psycopg2.errors.UndefinedTable:
        # egc.nf_bills_orfaos ainda nao existe (bloco 14 do schema.sql nao
        # rodado no Supabase) -- degrada pro comportamento de antes deste
        # fix (0 orfaos) em vez de quebrar a conferencia inteira (que ja'
        # rodou e gravou a parte normal antes de chegar aqui).
        conn.rollback()
        return 0

    return len(orfaos)


# ─────────────────────────────────────────────
#  CONFERENCIA VIVA (v0.45.0)
# ─────────────────────────────────────────────
#
# Rafael (02/10/2026): "o resultado anterior nao sera alterado pelo posterior?
# ... nao conseguimos ir alimentando essa base, e a conferencia gerar 1 por mes,
# completa, conforme upload?". Antes: cada upload era uma "rodada" congelada
# (julho conferido antes de agosto existir, nada recalculava). Agora:
#   - a conferencia VIGENTE de cada (empresa, periodo) e' o import mais recente
#     daquele periodo (subir o mesmo mes de novo substitui; o antigo fica so'
#     como auditoria e passa o status manual das pendencias adiante);
#   - toda vez que entra manifesto novo OU o Sienge e' atualizado, TODOS os
#     meses vigentes da empresa sao conferidos juntos (reconferir_empresa):
#     consumo de titulo comum aos meses, "Sienge sem manifesto" olhando todos
#     os meses, resultado independente da ordem dos uploads.

_STATUS_RESUMO = {"LANCADA_OUTRA_EMPRESA": "outra_empresa", "VALOR_DIVERGENTE": "valor_divergente",
                  "NUMERO_DIVERGENTE": "numero_divergente", "NAO_ENCONTRADA": "nao_encontradas"}


@contextmanager
def _transacao(conn):
    """Em conexao autocommit (producao) agrupa as gravacoes numa transacao so':
    ninguem enxerga a conferencia "pela metade" nem fica sem resultado se algo
    falhar no meio. Em conexao ja' transacional nao mexe (quem chamou manda)."""
    if not getattr(conn, "autocommit", False):
        yield
        return
    conn.autocommit = False
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = True


def _chave_nota(chave, cnpj, numero) -> tuple:
    """Identidade da nota entre uploads do mesmo periodo: chave de acesso (se
    houver); senao CNPJ do fornecedor + numero."""
    c = chave.strip() if isinstance(chave, str) else ""
    return ("K", c) if c else ("N", str(cnpj or ""), str(numero or ""))


def listar_vigentes(conn, empresa_codigo: Optional[str] = None) -> list[dict]:
    """Imports VIGENTES: o mais recente de cada (empresa, periodo), mais novo
    primeiro. Cada item: import_id, empresa_codigo, periodo_referencia,
    arquivo_nome, enviado_em (quando o manifesto desse periodo entrou)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (empresa_codigo, periodo_referencia)
                   import_id::text, empresa_codigo, periodo_referencia, arquivo_nome, ult
            FROM (SELECT import_id, empresa_codigo, periodo_referencia,
                         MAX(arquivo_nome) AS arquivo_nome, MAX(criado_em) AS ult
                  FROM egc.nf_manifesto_import
                  WHERE (%s::text IS NULL OR empresa_codigo = %s) AND __ATIVO__
                  GROUP BY import_id, empresa_codigo, periodo_referencia) x
            ORDER BY empresa_codigo, periodo_referencia, ult DESC
            """.replace("__ATIVO__", _ativo(conn)),
            (empresa_codigo, empresa_codigo),
        )
        itens = [dict(import_id=r[0], empresa_codigo=r[1], periodo_referencia=r[2], arquivo_nome=r[3], enviado_em=r[4])
                 for r in cur.fetchall()]
    return sorted(itens, key=lambda i: (i["empresa_codigo"], _chave_periodo(i["periodo_referencia"])))


def _chave_periodo(periodo) -> tuple:
    """'07/2026' -> (2026, 7) pra ordenar; texto fora do padrao vai pro fim."""
    m = re.match(r"^\s*(\d{1,2})\s*/\s*(\d{4})\s*$", str(periodo or ""))
    return (int(m.group(2)), int(m.group(1))) if m else (9999, 99, str(periodo))


def _tabela_existe(conn, nome: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (nome,))
        return bool(cur.fetchone()[0])


def _reconferir(conn, empresa_codigo: str, import_ids: list) -> dict:
    """Confere JUNTOS os imports `import_ids` (todos da mesma empresa) contra o
    espelho atual do Sienge e regrava egc.nf_conciliacao / nf_bills_orfaos /
    nf_import_historico de cada um. Devolve {import_id: resumo}.
    Status de acompanhamento manual (ENVIADO_SUPRIMENTOS/RESOLVIDO/DESCARTADO)
    e' preservado. Idempotente."""
    import_ids = [str(i) for i in import_ids]
    if not import_ids:
        return {}

    # ── 1) leitura (tudo em memoria antes de gravar) ──────────────────────
    with conn.cursor() as cur:
        cur.execute("SELECT import_id::text, total_notas, total_lancadas, total_pendencias "
                    "FROM egc.nf_import_historico WHERE import_id = ANY(%s::uuid[])", (import_ids,))
        antes = {r[0]: {"notas": r[1], "lancadas": r[2], "pendencias": r[3]} for r in cur.fetchall()}
        cur.execute(
            """
            SELECT id, import_id::text AS _import_id, periodo_referencia, numero_nota AS "Num",
                   numero_normalizado AS "_numero_normalizado", cnpj_normalizado AS "_cnpj_normalizado",
                   valor AS "_valor_float", chave_acesso AS "Chave", empresa_codigo AS "_empresa_codigo",
                   data_emissao AS "_data_emissao", arquivo_nome, criado_por, criado_em
            FROM egc.nf_manifesto_import WHERE import_id = ANY(%s::uuid[]) ORDER BY id
            """,
            (import_ids,),
        )
        cols = [d[0] for d in cur.description]
        manifesto = pd.DataFrame(cur.fetchall(), columns=cols)

    resumos = {i: {"total": 0, "lancadas": 0, "pendencias": 0, "orfaos_sienge": 0, "outra_empresa": 0,
                   "valor_divergente": 0, "numero_divergente": 0, "nao_encontradas": 0,
                   "periodo": None, "antes": antes.get(i)} for i in import_ids}
    if manifesto.empty:
        return resumos

    bills = _carregar_bills_creditores(conn)
    mapas = {i: _mapear_debtor_para_empresa(conn, i) for i in import_ids}
    debtor_por_bill = {}
    if not bills.empty and "debtor_id" in bills.columns:
        debtor_por_bill = {int(b): d for b, d in zip(bills["bill_id"], bills["debtor_id"])}

    # matching em fases com consumo de titulo COMUM a todos os meses
    classificacao = classificar_manifesto(manifesto, bills, None, mapas)

    ignoradas_por_import = {i: listar_ignoradas(conn, i) for i in import_ids}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT manifesto_id, pendencia_status FROM egc.nf_conciliacao WHERE import_id = ANY(%s::uuid[])",
            (import_ids,))
        proprios = {r[0]: r[1] for r in cur.fetchall()}   # status que cada nota JA tem neste import

    # status manual vindo de imports ANTIGOS do mesmo periodo (re-upload do mes)
    herdados: dict = {}
    periodos = {i: g["periodo_referencia"].iloc[0] for i, g in manifesto.groupby("_import_id")}
    with conn.cursor() as cur:
        for imp, periodo in periodos.items():
            cur.execute(
                """
                SELECT m.chave_acesso, m.cnpj_normalizado, m.numero_normalizado, c.pendencia_status
                FROM egc.nf_conciliacao c JOIN egc.nf_manifesto_import m ON m.id = c.manifesto_id
                WHERE m.empresa_codigo = %s AND m.periodo_referencia = %s AND m.import_id <> %s::uuid
                  AND c.pendencia_status IS NOT NULL AND c.pendencia_status <> 'PENDENTE'
                ORDER BY c.atualizado_em ASC NULLS FIRST
                """,
                (empresa_codigo, periodo, imp),
            )
            herdados[imp] = {_chave_nota(ch, cn, nu): st for ch, cn, nu, st in cur.fetchall()}

    # ── 2) gravacao da conferencia dos meses (uma transacao) ──────────────
    with _transacao(conn):
        with conn.cursor() as cur:
            for imp in import_ids:
                sub = manifesto[manifesto["_import_id"] == imp]
                if sub.empty:
                    continue
                resumo = resumos[imp]
                resumo["periodo"] = periodos[imp]
                resumo["total"] = len(sub)
                obs_ign = observacoes_de_ignoradas(sub, ignoradas_por_import[imp])
                cur.execute("DELETE FROM egc.nf_conciliacao WHERE import_id = %s::uuid", (imp,))
                for idx, row in sub.iterrows():
                    resultado = classificacao[idx]
                    if resultado.get("sienge_bill_id") is not None:
                        resultado = checar_empresa_do_lancamento(
                            resultado, row["_empresa_codigo"],
                            debtor_por_bill.get(int(resultado["sienge_bill_id"])), mapas[imp])
                    status_final = resultado["status"]
                    if status_final != "LANCADA" and idx in obs_ign:
                        resultado = dict(resultado)
                        resultado["observacao"] = " | ".join(x for x in (resultado.get("observacao"), obs_ign[idx]) if x)
                    if status_final == "LANCADA":
                        resumo["lancadas"] += 1
                        pendencia_status = None
                    else:
                        resumo["pendencias"] += 1
                        if row["id"] in proprios:
                            pendencia_status = proprios[row["id"]] or "PENDENTE"
                        else:
                            pendencia_status = herdados[imp].get(
                                _chave_nota(row["Chave"], row["_cnpj_normalizado"], row["_numero_normalizado"]), "PENDENTE")
                    params = (imp, row["id"], resultado["status"], resultado["sienge_bill_id"], resultado["sienge_valor"],
                              resultado["confianca"], resultado["observacao"], pendencia_status)
                    sql_ins = """
                        INSERT INTO egc.nf_conciliacao
                            (import_id, manifesto_id, status, sienge_bill_id, sienge_valor,
                             confianca, observacao, pendencia_status, atualizado_em)
                        VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, now())
                        """
                    cur.execute("SAVEPOINT s_nota")
                    try:
                        cur.execute(sql_ins, params)
                        cur.execute("RELEASE SAVEPOINT s_nota")
                    except psycopg2.errors.CheckViolation:
                        # CHECK de status ainda sem LANCADA_OUTRA_EMPRESA (bloco 19 nao rodado):
                        # grava como LANCADA mantendo o alerta na observacao.
                        cur.execute("ROLLBACK TO SAVEPOINT s_nota")
                        resumo["lancadas"] += 1
                        resumo["pendencias"] -= 1
                        status_final = "LANCADA"
                        cur.execute(sql_ins, params[:2] + ("LANCADA",) + params[3:7] + (None,))
                    chave_resumo = _STATUS_RESUMO.get(status_final)
                    if chave_resumo:
                        resumo[chave_resumo] += 1
                cur.execute(
                    """
                    INSERT INTO egc.nf_import_historico
                        (import_id, empresa_codigo, periodo_referencia, total_notas,
                         total_lancadas, total_pendencias, arquivo_nome, usuario, criado_em)
                    VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (import_id) DO UPDATE SET
                        total_notas = EXCLUDED.total_notas,
                        total_lancadas = EXCLUDED.total_lancadas,
                        total_pendencias = EXCLUDED.total_pendencias
                    """,
                    (imp, empresa_codigo, periodos[imp], resumo["total"], resumo["lancadas"], resumo["pendencias"],
                     sub["arquivo_nome"].iloc[0], sub["criado_por"].iloc[0], sub["criado_em"].min()),
                )

    # ── 3) direcao reversa ("Sienge sem manifesto"), DEPOIS de todos os meses
    # estarem gravados: o escopo dela olha o resultado dos outros periodos.
    if _tabela_existe(conn, "egc.nf_bills_orfaos"):
        for imp in import_ids:
            if resumos[imp]["total"]:
                with _transacao(conn):
                    resumos[imp]["orfaos_sienge"] = identificar_e_gravar_bills_orfaos(conn, imp, empresa_codigo)
    return resumos


def reconferir_empresa(conn, empresa_codigo: str) -> dict:
    """Confere de novo, juntos, todos os meses VIGENTES da empresa contra o
    Sienge atual. Devolve {periodo: resumo} (resumo["antes"] = totais da
    conferencia anterior desse periodo, ou None)."""
    vigentes = listar_vigentes(conn, empresa_codigo)
    resumos = _reconferir(conn, empresa_codigo, [v["import_id"] for v in vigentes])
    return {v["periodo_referencia"]: resumos[v["import_id"]] for v in vigentes}


def reconferir_todas(conn) -> dict:
    """reconferir_empresa de cada empresa que ja' tem manifesto. {empresa: {periodo: resumo}}"""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT empresa_codigo FROM egc.nf_manifesto_import ORDER BY 1")
        empresas = [r[0] for r in cur.fetchall()]
    return {e: reconferir_empresa(conn, e) for e in empresas}


def conciliar_import(conn, import_id: str) -> dict:
    """Confere UM import isolado (comportamento de antes da v0.45.0; o app usa
    reconferir_empresa). Idempotente; preserva o status manual das pendencias."""
    with conn.cursor() as cur:
        cur.execute("SELECT empresa_codigo FROM egc.nf_manifesto_import WHERE import_id = %s::uuid LIMIT 1", (import_id,))
        row = cur.fetchone()
    if not row:
        return {"total": 0, "lancadas": 0, "pendencias": 0, "orfaos_sienge": 0, "outra_empresa": 0,
                "valor_divergente": 0, "numero_divergente": 0, "nao_encontradas": 0}
    return _reconferir(conn, row[0], [import_id])[str(import_id)]


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

def listar_historico_importacoes(conn, empresa_codigo: Optional[str] = None, somente_vigentes: bool = False) -> list[dict]:
    """Uploads ja' feitos (auditoria), mais recente primeiro. v0.45.0: cada item
    traz `vigente` (True = e' a conferencia atual daquele empresa+periodo);
    `somente_vigentes=True` devolve so' essas. v0.46.0: traz tambem `arquivado`
    (True = envio desfeito pela contadora; nunca e' vigente)."""
    with conn.cursor() as cur:
        if empresa_codigo:
            cur.execute(
                "SELECT * FROM egc.nf_import_historico WHERE empresa_codigo = %s ORDER BY criado_em DESC",
                (empresa_codigo,),
            )
        else:
            cur.execute("SELECT * FROM egc.nf_import_historico ORDER BY criado_em DESC")
        cols = [d[0] for d in cur.description]
        linhas = [dict(zip(cols, row)) for row in cur.fetchall()]
    ids_vigentes = {v["import_id"] for v in listar_vigentes(conn, empresa_codigo)}
    arquivados: set = set()
    if tem_arquivamento(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT import_id::text FROM egc.nf_manifesto_import WHERE arquivado_em IS NOT NULL"
                        + (" AND empresa_codigo = %s" if empresa_codigo else ""),
                        (empresa_codigo,) if empresa_codigo else None)
            arquivados = {r[0] for r in cur.fetchall()}
    for h in linhas:
        h["vigente"] = str(h["import_id"]) in ids_vigentes
        h["arquivado"] = str(h["import_id"]) in arquivados
    return [h for h in linhas if h["vigente"]] if somente_vigentes else linhas


# ─────────────────────────────────────────────
#  ENVIOS (v0.46.0): 1 arquivo subido = 1 envio = varios meses. Arquivar = desfazer sem apagar.
# ─────────────────────────────────────────────

JANELA_LOTE_LEGADO_SEG = 180   # envios antigos (sem lote_id): meses do mesmo arquivo gravados em ate' 3 min um do outro


def _agrupar_envios(imports: list) -> list:
    """Agrupa os imports (1 por mes) em envios. Com lote_id, o lote manda; sem lote_id (envios anteriores
    a v0.46.0) agrupa por empresa+arquivo+usuario com gravacoes quase juntas. Funcao pura."""
    envios: dict = {}
    legado = sorted((i for i in imports if not i.get("lote_id")), key=lambda i: (i["empresa_codigo"], i["criado_em"]))
    for i in imports:
        if i.get("lote_id"):
            envios.setdefault(("L", str(i["lote_id"])), []).append(i)
    ultimo = None
    n = 0
    for i in legado:
        chave_arq = (i["empresa_codigo"], i.get("arquivo_nome"), i.get("criado_por"))
        if ultimo and ultimo[0] == chave_arq and (i["criado_em"] - ultimo[1]).total_seconds() <= JANELA_LOTE_LEGADO_SEG:
            pass
        else:
            n += 1
        envios.setdefault(("A", n), []).append(i)
        ultimo = (chave_arq, i["criado_em"])
    saida = []
    for chave, itens in envios.items():
        itens = sorted(itens, key=lambda x: _chave_periodo(x["periodo_referencia"]))
        arq = [i for i in itens if i.get("arquivado_em")]
        saida.append({
            "envio_id": f"{chave[0]}:{chave[1]}",
            "empresa_codigo": itens[0]["empresa_codigo"], "arquivo_nome": itens[0].get("arquivo_nome"),
            "usuario": itens[0].get("criado_por"), "enviado_em": min(i["criado_em"] for i in itens),
            "import_ids": [i["import_id"] for i in itens], "periodos": [i["periodo_referencia"] for i in itens],
            "total_notas": sum(int(i.get("notas") or 0) for i in itens),
            "arquivado": len(arq) == len(itens), "arquivado_parcial": 0 < len(arq) < len(itens),
            "arquivado_em": max((i["arquivado_em"] for i in arq), default=None),
            "arquivado_por": next((i.get("arquivado_por") for i in arq), None),
            "arquivado_motivo": next((i.get("arquivado_motivo") for i in arq if i.get("arquivado_motivo")), None),
        })
    return sorted(saida, key=lambda e: e["enviado_em"], reverse=True)


def listar_envios(conn, empresa_codigo: Optional[str] = None) -> list[dict]:
    """Todos os envios (arquivos subidos), mais recente primeiro, ativos e arquivados, cada um com seus meses,
    quantas notas, e quais meses ainda VALEM (`periodos_vigentes`) ou foram substituidos por um envio mais novo."""
    col = tem_arquivamento(conn)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT import_id::text, empresa_codigo, MAX(periodo_referencia), MAX(arquivo_nome), MAX(criado_por),
                   MAX(criado_em), COUNT(*)
                   {', MAX(lote_id::text), MAX(arquivado_em), MAX(arquivado_por), MAX(arquivado_motivo)' if col else ''}
            FROM egc.nf_manifesto_import
            WHERE (%s::text IS NULL OR empresa_codigo = %s)
            GROUP BY import_id, empresa_codigo
            """,
            (empresa_codigo, empresa_codigo),
        )
        imports = []
        for r in cur.fetchall():
            imports.append({"import_id": r[0], "empresa_codigo": r[1], "periodo_referencia": r[2], "arquivo_nome": r[3],
                            "criado_por": r[4], "criado_em": r[5], "notas": r[6],
                            "lote_id": r[7] if col else None, "arquivado_em": r[8] if col else None,
                            "arquivado_por": r[9] if col else None, "arquivado_motivo": r[10] if col else None})
    vigentes = {v["import_id"] for v in listar_vigentes(conn, empresa_codigo)}
    envios = _agrupar_envios(imports)
    for e in envios:
        e["periodos_vigentes"] = [p for p, i in zip(e["periodos"], e["import_ids"]) if i in vigentes]
        e["periodos_substituidos"] = [p for p, i in zip(e["periodos"], e["import_ids"])
                                      if i not in vigentes and not e["arquivado"]]
    return envios


def previa_arquivamento(conn, import_ids: list) -> list[dict]:
    """O que acontece se estes imports forem arquivados: por (empresa, periodo) deles, qual envio anterior
    passa a valer (`passa_a_valer` = nome do arquivo) ou None (periodo fica sem conferencia)."""
    import_ids = [str(i) for i in import_ids]
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT import_id::text, empresa_codigo, periodo_referencia "
                    "FROM egc.nf_manifesto_import WHERE import_id = ANY(%s::uuid[])", (import_ids,))
        alvo = cur.fetchall()
        saida = []
        for imp, emp, per in alvo:
            cur.execute(
                f"""SELECT arquivo_nome FROM egc.nf_manifesto_import
                    WHERE empresa_codigo = %s AND periodo_referencia = %s AND {_ativo(conn)}
                      AND NOT (import_id = ANY(%s::uuid[]))
                    GROUP BY import_id, arquivo_nome ORDER BY MAX(criado_em) DESC LIMIT 1""",
                (emp, per, import_ids),
            )
            row = cur.fetchone()
            saida.append({"empresa_codigo": emp, "periodo_referencia": per, "import_id": imp,
                          "passa_a_valer": (row[0] or "sem nome") if row else None})
    return sorted(saida, key=lambda x: (x["empresa_codigo"], _chave_periodo(x["periodo_referencia"])))


def _mudar_arquivamento(conn, import_ids: list, arquivar: bool, usuario: str, motivo: Optional[str]) -> dict:
    if not tem_arquivamento(conn):
        raise ArquivamentoIndisponivel("rode o bloco 23 do schema.sql no Supabase antes de arquivar/restaurar envios")
    import_ids = [str(i) for i in import_ids]
    if not import_ids:
        return {"empresas": [], "afetados": 0, "resumos": {}}
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT empresa_codigo FROM egc.nf_manifesto_import WHERE import_id = ANY(%s::uuid[])", (import_ids,))
        empresas = [r[0] for r in cur.fetchall()]
    antes = {(v["empresa_codigo"], v["periodo_referencia"]): v for e in empresas for v in listar_vigentes(conn, e)}
    with _transacao(conn):
        with conn.cursor() as cur:
            if arquivar:
                cur.execute(
                    "UPDATE egc.nf_manifesto_import SET arquivado_em = now(), arquivado_por = %s, arquivado_motivo = %s "
                    "WHERE import_id = ANY(%s::uuid[]) AND arquivado_em IS NULL",
                    (usuario, (motivo or "").strip() or None, import_ids),
                )
            else:
                cur.execute(
                    "UPDATE egc.nf_manifesto_import SET arquivado_em = NULL, arquivado_por = NULL, arquivado_motivo = NULL "
                    "WHERE import_id = ANY(%s::uuid[]) AND arquivado_em IS NOT NULL",
                    (import_ids,),
                )
            afetados = cur.rowcount
    resumos = {e: reconferir_empresa(conn, e) for e in empresas}
    depois = {(v["empresa_codigo"], v["periodo_referencia"]): v for e in empresas for v in listar_vigentes(conn, e)}
    mudou = []
    for chave in sorted(set(antes) | set(depois), key=lambda k: (k[0], _chave_periodo(k[1]))):
        a, d = antes.get(chave), depois.get(chave)
        if (a or {}).get("import_id") != (d or {}).get("import_id"):
            mudou.append({"empresa_codigo": chave[0], "periodo_referencia": chave[1],
                          "antes": (a or {}).get("arquivo_nome"), "depois": (d or {}).get("arquivo_nome")})
    return {"empresas": empresas, "afetados": int(afetados or 0), "resumos": resumos, "mudancas": mudou}


def arquivar_envio(conn, import_ids: list, usuario: str, motivo: Optional[str] = None) -> dict:
    """Desfaz um envio SEM apagar nada: marca os imports como arquivados e refaz a conferencia da empresa
    (o envio anterior de cada mes, se houver, volta a valer). Devolve {mudancas: [{periodo, antes, depois}]}."""
    return _mudar_arquivamento(conn, import_ids, True, usuario, motivo)


def restaurar_envio(conn, import_ids: list, usuario: str) -> dict:
    """Desfaz o arquivamento: o envio volta a competir como os demais (vale o mais recente de cada mes)."""
    return _mudar_arquivamento(conn, import_ids, False, usuario, None)


def resumo_vigentes(conn, empresa_codigo: Optional[str] = None) -> list[dict]:
    """Uma linha por (empresa, periodo) vigente -- a "lista de CNPJ e periodo"
    da tela: notas, lancadas, pendencias (notas do manifesto + titulos do Sienge
    sem nota), arquivo e quando foi atualizada. Ordenada por empresa e periodo."""
    vigentes = listar_vigentes(conn, empresa_codigo)
    if not vigentes:
        return []
    ids = [v["import_id"] for v in vigentes]
    com_orfaos = _tabela_existe(conn, "egc.nf_bills_orfaos")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT h.import_id::text, h.total_notas, h.total_lancadas, h.total_pendencias, h.usuario, h.criado_em,
                   (SELECT MAX(c.atualizado_em) FROM egc.nf_conciliacao c WHERE c.import_id = h.import_id)
            FROM egc.nf_import_historico h WHERE h.import_id = ANY(%s::uuid[])
            """,
            (ids,),
        )
        hist = {r[0]: r for r in cur.fetchall()}
        orfaos = {}
        if com_orfaos:
            cur.execute("SELECT import_id::text, COUNT(*) FROM egc.nf_bills_orfaos WHERE import_id = ANY(%s::uuid[]) "
                        "GROUP BY import_id", (ids,))
            orfaos = {r[0]: int(r[1]) for r in cur.fetchall()}
    saida = []
    for v in vigentes:
        h = hist.get(v["import_id"])
        notas = int(h[1]) if h else 0
        lancadas = int(h[2]) if h else 0
        pend_notas = int(h[3]) if h else 0
        n_orf = orfaos.get(v["import_id"], 0)
        saida.append({**v, "total_notas": notas, "total_lancadas": lancadas, "pendencias_notas": pend_notas,
                      "orfaos_sienge": n_orf, "total_pendencias": pend_notas + n_orf,
                      "taxa": (lancadas / notas) if notas else None,
                      "usuario": h[4] if h else None, "atualizado_em": (h[6] if h else None) or v["enviado_em"]})
    return saida


def listar_conciliacao(conn, import_id: str) -> pd.DataFrame:
    """FIX_20260928c (Rafael, tabela principal de Notas Fiscais): m.cfop
    entra logo depois de numero_nota -- a contadora referencia bastante
    coisa por CFOP, não só por número da nota.
    FIX_20260928f: acrescentadas registro_id (= manifesto_id, o que
    atualizar_status_pendencia espera) e origem='MANIFESTO' -- pra dar pra
    concatenar com listar_orfaos_sienge (direção reversa) numa tabela só
    na tela, sem cada linha perder a informação de qual função de update
    usar depois."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.numero_nota, m.cfop, m.data_emissao, m.valor, m.fornecedor_nome, m.fornecedor_cnpj,
                   c.status, c.confianca, c.sienge_bill_id,
                   NULLIF(TRIM(CONCAT_WS(' ', TRIM(b.document_identification_id), b.document_number)), '') AS sienge_documento,
                   c.sienge_valor, c.observacao,
                   c.pendencia_status, c.atualizado_em, c.manifesto_id AS registro_id
            FROM egc.nf_conciliacao c
            JOIN egc.nf_manifesto_import m ON m.id = c.manifesto_id
            LEFT JOIN egc.nf_bills_sync b ON b.bill_id = c.sienge_bill_id
            WHERE c.import_id = %s
            ORDER BY (c.status <> 'LANCADA') DESC, m.numero_nota
            """,
            (import_id,),
        )
        cols = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    df["origem"] = "MANIFESTO"
    return df


_COLUNAS_ORFAOS_SIENGE = [
    "numero_nota", "cfop", "data_emissao", "valor", "fornecedor_nome", "fornecedor_cnpj",
    "status", "confianca", "sienge_bill_id", "sienge_documento", "sienge_valor", "observacao",
    "pendencia_status", "atualizado_em", "registro_id", "origem",
]


def _observacao_orfao(r) -> str:
    base = "Título lançado no Sienge sem nota correspondente no manifesto desta rodada."
    emp = r.get("mx_empresa")
    if emp is None or (not isinstance(emp, str)):
        return base
    valor = r.get("mx_valor")
    txt_valor = f", valor R$ {float(valor):,.2f}" if valor is not None and not pd.isna(valor) else ""
    cfop = r.get("mx_cfop")
    txt_cfop = f", CFOP {cfop}" if cfop and not pd.isna(cfop) else ""
    if emp != r.get("empresa_orfao"):
        return (f"A mesma nota (CNPJ + nº) consta no manifesto de {emp} ({r.get('mx_periodo')}){txt_cfop}{txt_valor} "
                f"— possível lançamento na empresa errada no Sienge.")
    return (f"A mesma nota (CNPJ + nº) consta em outro manifesto de {emp} ({r.get('mx_periodo')}){txt_cfop}{txt_valor} "
            f"— confira período/valor.")


def listar_orfaos_sienge(conn, import_id: str) -> pd.DataFrame:
    """
    FIX_20260928f -- órfãos (bloco 14) desta rodada, no MESMO formato de
    colunas de listar_conciliacao (registro_id/origem inclusive), pra
    entrar direto na mesma tabela/filtro/export da tela sem duplicar
    lógica. numero_nota fica com um marcador "[Sienge] nº <doc>" (não
    existe número de nota do lado do manifesto aqui -- é o oposto: falta
    a nota, sobra o título).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.id AS registro_id, o.bill_id, o.document_number, o.issue_date,
                   o.total_invoice_amount, o.creditor_nome, o.creditor_cnpj,
                   o.pendencia_status, o.atualizado_em, o.empresa_codigo AS empresa_orfao,
                   NULLIF(TRIM(CONCAT_WS(' ', TRIM(b.document_identification_id), o.document_number)), '') AS sienge_documento,
                   mx.cfop AS mx_cfop, mx.valor AS mx_valor, mx.emp AS mx_empresa, mx.per AS mx_periodo
            FROM egc.nf_bills_orfaos o
            LEFT JOIN egc.nf_bills_sync b ON b.bill_id = o.bill_id
            LEFT JOIN LATERAL (
                SELECT mm.cfop, mm.valor, mm.empresa_codigo AS emp, mm.periodo_referencia AS per
                FROM egc.nf_manifesto_import mm
                WHERE __ATIVO_MM__ AND mm.cnpj_normalizado = regexp_replace(COALESCE(o.creditor_cnpj, ''), '\\D', '', 'g')
                  AND mm.numero_normalizado = COALESCE(
                        NULLIF(ltrim(regexp_replace(COALESCE(o.document_number, ''), '\\D', '', 'g'), '0'), ''), '0')
                ORDER BY (mm.import_id = o.import_id) DESC, (mm.empresa_codigo = o.empresa_codigo) DESC, mm.criado_em DESC
                LIMIT 1
            ) mx ON true
            WHERE o.import_id = %s
            """.replace("__ATIVO_MM__", _ativo(conn, "mm")),
            (import_id,),
        )
        cols = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    if df.empty:
        return pd.DataFrame(columns=_COLUNAS_ORFAOS_SIENGE)
    df["numero_nota"] = df["document_number"].apply(lambda n: f"[Sienge] nº {n}" if n else "[Sienge] (sem nº)")
    # CFOP/valor "do manifesto": o orfao nao casou com nenhuma nota da rodada,
    # mas a MESMA nota (CNPJ do fornecedor + numero) pode constar em outro
    # manifesto ja' enviado (outra empresa/periodo) -- e' o que a contadora
    # precisa pra cruzar (e indica lancamento na empresa errada).
    df["cfop"] = df["mx_cfop"]
    df["data_emissao"] = df["issue_date"]
    df["valor"] = df["mx_valor"]
    df["fornecedor_nome"] = df["creditor_nome"]
    df["fornecedor_cnpj"] = df["creditor_cnpj"]
    df["status"] = "SIENGE_SEM_MANIFESTO"
    df["confianca"] = None
    df["sienge_bill_id"] = df["bill_id"]
    df["sienge_valor"] = df["total_invoice_amount"]
    df["observacao"] = df.apply(_observacao_orfao, axis=1)
    df["origem"] = "SIENGE_ORFAO"
    return df[_COLUNAS_ORFAOS_SIENGE]


def listar_pendencias_abertas(conn, empresa_codigo: Optional[str] = None, limite: int = 50) -> pd.DataFrame:
    """Pendências ainda em aberto (PENDENTE ou ENVIADO_SUPRIMENTOS -- não
    RESOLVIDO/DESCARTADO), da rodada de conferência mais recente de cada
    empresa+período. Usado pelo chat (consultas_chat.consultar_notas_pendentes)
    e pela tela pra montar o .xlsx de pendências.

    FIX_20260929 (achado revisando o pedido do Rafael sobre clicar 2x em
    "Rodar conferência"): cada rodada cria um import_id NOVO -- nada
    substitui/desativa o lote anterior do mesmo empresa+período (ao
    contrário do fluxo BP/DRE, que tem inativar_periodo_existente). O
    docstring desta função já dizia "rodada mais recente de cada
    empresa", mas o SQL antigo não filtrava por import_id nenhum --
    somava pendências de TODOS os imports já rodados. Resultado real: se
    a mesma competência for conferida 2x (de propósito ou por engano),
    a mesma nota pendente aparecia 2x pro Erik.AI. `DISTINCT ON` abaixo
    restringe a SÓ o import_id mais recente de cada (empresa, período) --
    uma competência antiga genuinamente diferente (ex.: 07/2026 depois
    de já ter rodado 08/2026) continua aparecendo normalmente, só não
    conta 2x a MESMA competência re-rodada."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH ultimo_import AS (
                SELECT DISTINCT ON (empresa_codigo, periodo_referencia) import_id
                FROM egc.nf_manifesto_import
                WHERE __ATIVO__
                ORDER BY empresa_codigo, periodo_referencia, criado_em DESC
            )
            SELECT m.empresa_codigo, m.periodo_referencia, m.numero_nota, m.cfop, m.data_emissao,
                   m.valor, m.fornecedor_nome, m.fornecedor_cnpj, c.status, c.observacao,
                   c.pendencia_status, c.atualizado_em, c.manifesto_id AS registro_id,
                   'MANIFESTO' AS origem
            FROM egc.nf_conciliacao c
            JOIN egc.nf_manifesto_import m ON m.id = c.manifesto_id
            JOIN ultimo_import ui ON ui.import_id = m.import_id
            WHERE c.status <> 'LANCADA'
              AND (c.pendencia_status IS NULL OR c.pendencia_status IN ('PENDENTE','ENVIADO_SUPRIMENTOS'))
              AND (%s IS NULL OR m.empresa_codigo = %s)
            ORDER BY m.data_emissao DESC
            LIMIT %s
            """.replace("__ATIVO__", _ativo(conn)),
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


def atualizar_status_orfao_sienge(conn, orfao_id: int, novo_status: str, usuario: str) -> None:
    """Espelha atualizar_status_pendencia, pro lado reverso (bloco 14) --
    orfao_id aqui é egc.nf_bills_orfaos.id (= registro_id que
    listar_orfaos_sienge devolve com origem='SIENGE_ORFAO')."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE egc.nf_bills_orfaos
            SET pendencia_status = %s, atualizado_por = %s, atualizado_em = now()
            WHERE id = %s
            """,
            (novo_status, usuario, orfao_id),
        )


def listar_orfaos_abertos(conn, empresa_codigo: Optional[str] = None, limite: int = 50) -> pd.DataFrame:
    """Espelha listar_pendencias_abertas, pro lado reverso (bloco 14) --
    usado junto dela em consultas_chat.consultar_notas_pendentes pra não
    deixar o chat cego pra esse tipo de pendência."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.empresa_codigo, h.periodo_referencia, o.document_number, o.issue_date,
                   o.total_invoice_amount, o.creditor_nome, o.creditor_cnpj,
                   o.pendencia_status, o.atualizado_em, o.id AS registro_id,
                   mx.cfop AS mx_cfop, mx.valor AS mx_valor, mx.emp AS mx_empresa, mx.per AS mx_periodo
            FROM egc.nf_bills_orfaos o
            JOIN (SELECT DISTINCT ON (empresa_codigo, periodo_referencia) import_id
                  FROM (SELECT import_id, empresa_codigo, periodo_referencia, MAX(criado_em) AS ult
                        FROM egc.nf_manifesto_import WHERE __ATIVO__
                        GROUP BY import_id, empresa_codigo, periodo_referencia) z
                  ORDER BY empresa_codigo, periodo_referencia, ult DESC) vig ON vig.import_id = o.import_id
            LEFT JOIN egc.nf_import_historico h ON h.import_id = o.import_id
            LEFT JOIN LATERAL (
                SELECT mm.cfop, mm.valor, mm.empresa_codigo AS emp, mm.periodo_referencia AS per
                FROM egc.nf_manifesto_import mm
                WHERE __ATIVO_MM__ AND mm.cnpj_normalizado = regexp_replace(COALESCE(o.creditor_cnpj, ''), '\\D', '', 'g')
                  AND mm.numero_normalizado = COALESCE(
                        NULLIF(ltrim(regexp_replace(COALESCE(o.document_number, ''), '\\D', '', 'g'), '0'), ''), '0')
                ORDER BY (mm.import_id = o.import_id) DESC, (mm.empresa_codigo = o.empresa_codigo) DESC, mm.criado_em DESC
                LIMIT 1
            ) mx ON true
            WHERE (o.pendencia_status IS NULL OR o.pendencia_status IN ('PENDENTE','ENVIADO_SUPRIMENTOS'))
              AND (%s IS NULL OR o.empresa_codigo = %s)
            ORDER BY o.issue_date DESC
            LIMIT %s
            """.replace("__ATIVO__", _ativo(conn)).replace("__ATIVO_MM__", _ativo(conn, "mm")),
            (empresa_codigo, empresa_codigo, limite),
        )
        cols = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    if df.empty:
        return df
    df["empresa_orfao"] = df["empresa_codigo"]
    df["cfop"] = df["mx_cfop"]  # CFOP da mesma nota em algum manifesto ja' enviado (se houver)
    df["observacao"] = df.apply(_observacao_orfao, axis=1)
    df = df.drop(columns=["mx_cfop", "mx_valor", "mx_empresa", "mx_periodo", "empresa_orfao"])
    df = df.rename(columns={
        "document_number": "numero_nota", "issue_date": "data_emissao",
        "total_invoice_amount": "valor", "creditor_nome": "fornecedor_nome", "creditor_cnpj": "fornecedor_cnpj",
    })
    df["status"] = "SIENGE_SEM_MANIFESTO"
    df["origem"] = "SIENGE_ORFAO"
    return df


def buscar_notas(conn, numero: Optional[str] = None, fornecedor: Optional[str] = None,
                 cnpj: Optional[str] = None, empresa_codigo: Optional[str] = None, limite: int = 20) -> list[dict]:
    """Busca nota(s) fiscal(is) do manifesto com o resultado da conferencia
    (so' a conferencia VIGENTE de cada empresa+periodo -- v0.45.0). Filtros opcionais e combinaveis:
    numero (sem zeros a esquerda), trecho do nome do fornecedor, CNPJ. Usada pelo
    chat ("a nota 1234 do fornecedor X foi lancada?")."""
    filtros, params = [], []
    if numero:
        n = re.sub(r"\D", "", str(numero)).lstrip("0") or "0"
        filtros.append("m.numero_normalizado = %s")
        params.append(n)
    if cnpj:
        filtros.append("m.cnpj_normalizado = %s")
        params.append(re.sub(r"\D", "", str(cnpj)))
    trecho = str(fornecedor or "").strip()[:60].replace("%", "").replace("_", " ").strip()
    if trecho:  # curinga puro ("%") nao pode virar "todas as notas"
        filtros.append("m.fornecedor_nome ILIKE %s")
        params.append("%" + trecho + "%")
    if empresa_codigo:
        filtros.append("m.empresa_codigo = %s")
        params.append(empresa_codigo)
    if not filtros:
        return []
    where = " AND ".join(filtros)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT m.empresa_codigo, m.periodo_referencia, m.numero_nota, m.cfop, m.data_emissao, m.valor,
                   m.fornecedor_nome, m.fornecedor_cnpj, c.status, c.observacao, c.pendencia_status,
                   c.sienge_bill_id, c.sienge_valor, c.manifesto_id AS registro_id
            FROM egc.nf_manifesto_import m
            LEFT JOIN egc.nf_conciliacao c ON c.manifesto_id = m.id
            WHERE m.import_id IN (
                    SELECT DISTINCT ON (empresa_codigo, periodo_referencia) import_id
                    FROM (SELECT import_id, empresa_codigo, periodo_referencia, MAX(criado_em) AS ult
                          FROM egc.nf_manifesto_import WHERE {_ativo(conn)}
                          GROUP BY import_id, empresa_codigo, periodo_referencia) z
                    ORDER BY empresa_codigo, periodo_referencia, ult DESC)
              AND {where}
            ORDER BY m.criado_em DESC, m.data_emissao DESC
            LIMIT %s
            """,
            tuple(params) + (max(1, min(int(limite), 50)),),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ───────────── v0.46.3: observacao automatica de combustivel + anotacoes da contadora ─────────────
# CFOP de compra de combustivel/lubrificante (posto, distribuidora). Na Energia 01-08/2026 foram 59% das "Nao
# encontradas" (461 de 783) e so' 6,5% das notas com esses CFOP estao lancadas no Sienge: na pratica nao passam
# pelo contas a pagar. E' so' uma PISTA (a linha segue nas pendencias, nada e' excluido); a contadora decide.
CFOPS_COMBUSTIVEL = frozenset({"5929", "5667", "5656"})
OBS_COMBUSTIVEL = ("Provável compra de combustível (CFOP 5929/5667/5656) — em geral não passa pelo contas a pagar; "
                   "confirmar se é caso de lançar.")


def observacao_combustivel(cfop) -> Optional[str]:
    """Texto da pista 'provavel fora do AP' quando TODOS os CFOP da nota sao de combustivel; senao None."""
    if cfop is None or (not isinstance(cfop, str)):
        return None
    codigos = [c for c in re.split(r"\D+", cfop) if c]
    if codigos and all(c in CFOPS_COMBUSTIVEL for c in codigos):
        return OBS_COMBUSTIVEL
    return None


_TAB_ANOTACAO = {"ok": False}


def tem_anotacao(conn) -> bool:
    """True se a tabela egc.nf_anotacao existe (bloco 24). Sem ela o app segue sem a coluna Anotacao."""
    if _TAB_ANOTACAO["ok"]:
        return True
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('egc.nf_anotacao') IS NOT NULL")
        achou = bool(cur.fetchone()[0])
    if achou:
        _TAB_ANOTACAO["ok"] = True
    return achou


def _num_normalizado(numero) -> str:
    d = re.sub(r"\D", "", str(numero or "")).lstrip("0")
    return d or "0"


def chave_anotacao(origem: str, empresa_codigo: str, numero_nota=None, cnpj=None, bill_id=None) -> str:
    """Chave estavel da anotacao: sobrevive a novo upload, 'Atualizar agora' e arquivar/restaurar.
    Nota do manifesto = empresa|cnpj|numero (iguais ao matching); titulo do Sienge sem nota = empresa|bill|id."""
    if origem == "SIENGE_ORFAO":
        if bill_id is not None and not pd.isna(bill_id):
            return f"{empresa_codigo}|bill|{int(bill_id)}"
        return f"{empresa_codigo}|orfao|{numero_nota}"
    cnpj_digitos = re.sub(r"\D", "", str(cnpj or ""))
    return f"{empresa_codigo}|{cnpj_digitos}|{_num_normalizado(numero_nota)}"


def carregar_anotacoes(conn, empresa_codigos) -> dict:
    """{chave_ref: texto} das empresas pedidas ({} sem a tabela)."""
    if not tem_anotacao(conn):
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT chave_ref, texto FROM egc.nf_anotacao WHERE empresa_codigo = ANY(%s)",
                    (list(empresa_codigos),))
        return {r[0]: r[1] for r in cur.fetchall()}


def salvar_anotacao(conn, empresa_codigo: str, chave_ref: str, texto: str, usuario: str) -> None:
    """Grava (ou, com texto vazio, remove) a anotacao da linha. Exige o bloco 24."""
    if not tem_anotacao(conn):
        raise RuntimeError("Anotações ainda não estão disponíveis: falta rodar o BLOCO 24 no Supabase.")
    texto = (texto or "").strip()
    with conn.cursor() as cur:
        if not texto:
            cur.execute("DELETE FROM egc.nf_anotacao WHERE chave_ref = %s", (chave_ref,))
        else:
            cur.execute(
                """INSERT INTO egc.nf_anotacao (chave_ref, empresa_codigo, texto, atualizado_por, atualizado_em)
                   VALUES (%s, %s, %s, %s, now())
                   ON CONFLICT (chave_ref) DO UPDATE SET texto = EXCLUDED.texto,
                       atualizado_por = EXCLUDED.atualizado_por, atualizado_em = now()""",
                (chave_ref, empresa_codigo, texto[:1000], usuario),
            )


def _enriquecer_conferencia(conn, tabela: pd.DataFrame, empresas: list) -> pd.DataFrame:
    """Acrescenta chave_ref (interna), anotacao (editavel pela contadora) e a pista de combustivel na observacao."""
    if tabela.empty:
        return tabela
    t = tabela.copy()
    t["chave_ref"] = [
        chave_anotacao(o, e, numero_nota=n, cnpj=c, bill_id=b)
        for o, e, n, c, b in zip(t["origem"], t["empresa_codigo"], t["numero_nota"], t["fornecedor_cnpj"],
                                 t["sienge_bill_id"])
    ]
    try:
        notas = carregar_anotacoes(conn, empresas)
    except Exception:
        notas = {}
    t["anotacao"] = t["chave_ref"].map(notas)
    obs = t["observacao"].copy()
    for i in t.index[t["status"] == "NAO_ENCONTRADA"]:
        pista = observacao_combustivel(t.at[i, "cfop"])
        if pista and (pd.isna(obs.at[i]) or not obs.at[i]):
            obs.at[i] = pista
    t["observacao"] = obs
    return t



def tabela_conferencia(conn, vigentes: list) -> pd.DataFrame:
    """v0.46.0: conferencia de VARIOS periodos numa tabela so' (notas do manifesto + titulos do Sienge sem
    nota), com a coluna `periodo`. `vigentes` = itens de resumo_vigentes/listar_vigentes (import_id +
    periodo_referencia). Ordem: periodo, depois o que ja' vinha ordenado (pendentes primeiro)."""
    partes = []
    for v in sorted(vigentes, key=lambda x: _chave_periodo(x["periodo_referencia"])):
        t = listar_conciliacao(conn, v["import_id"])
        try:
            o = listar_orfaos_sienge(conn, v["import_id"])
        except Exception:
            o = pd.DataFrame()
        if o is not None and not o.empty:
            t = pd.concat([t, o], ignore_index=True)
        if not t.empty:
            t = t.copy()
            t.insert(0, "periodo", v["periodo_referencia"])
            t["empresa_codigo"] = v["empresa_codigo"]
            partes.append(t)
    if not partes:
        return pd.DataFrame()
    return _enriquecer_conferencia(conn, pd.concat(partes, ignore_index=True),
                                   sorted({v["empresa_codigo"] for v in vigentes}))


def ignoradas_conferencia(conn, vigentes: list) -> pd.DataFrame:
    """Notas retiradas da conferencia (canceladas/Entrada) dos periodos pedidos, com a coluna `periodo`."""
    partes = []
    for v in sorted(vigentes, key=lambda x: _chave_periodo(x["periodo_referencia"])):
        d = listar_ignoradas(conn, v["import_id"])
        if d is not None and not d.empty:
            d = d.copy()
            d.insert(0, "periodo", v["periodo_referencia"])
            partes.append(d)
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


STATUS_PENDENCIA_VALIDOS = ("PENDENTE", "ENVIADO_SUPRIMENTOS", "RESOLVIDO", "DESCARTADO")


def obter_registro_pendencia(conn, origem: str, registro_id: int) -> Optional[dict]:
    """Dados de UMA pendencia (nota do manifesto ou titulo orfao do Sienge) pra
    mostrar na tela de aprovacao do chat antes de qualquer gravacao. None se nao existe."""
    with conn.cursor() as cur:
        if origem == "SIENGE_ORFAO":
            cur.execute(
                """
                SELECT o.empresa_codigo, o.document_number, o.creditor_nome, o.total_invoice_amount,
                       o.pendencia_status
                FROM egc.nf_bills_orfaos o WHERE o.id = %s
                """,
                (registro_id,),
            )
        else:
            cur.execute(
                """
                SELECT m.empresa_codigo, m.numero_nota, m.fornecedor_nome, m.valor, c.pendencia_status
                FROM egc.nf_manifesto_import m
                JOIN egc.nf_conciliacao c ON c.manifesto_id = m.id
                WHERE m.id = %s
                ORDER BY c.atualizado_em DESC NULLS LAST
                LIMIT 1
                """,
                (registro_id,),
            )
        row = cur.fetchone()
    if not row:
        return None
    return {"empresa_codigo": row[0], "numero_nota": row[1], "fornecedor": row[2],
            "valor": float(row[3]) if row[3] is not None else None, "pendencia_status": row[4] or "PENDENTE"}
