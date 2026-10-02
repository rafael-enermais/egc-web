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
                     arquivo_nome, criado_por, criado_em)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (
                    import_id, empresa_codigo, periodo_referencia,
                    str(row.get("Num") or ""), row.get("_numero_normalizado"),
                    row.get("Tipo"), data_emissao, row.get("_valor_float"), row.get("CFOP"),
                    row.get("Emissor Nome"), row.get("Emissor CNPJ/CPF"), row.get("_cnpj_normalizado"),
                    row.get("UF"), row.get("Chave"), row.get("_chave_modelo"),
                    row.get("_chave_serie"), row.get("_chave_numero"), nome_arquivo, usuario,
                ),
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


def _classificar_nota(row, bills: pd.DataFrame, usados: Optional[set] = None,
                      fases: tuple = FASES_CLASSIFICACAO, debtors_preferidos: Optional[set] = None) -> dict:
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

    universo = bills[bills["document_identification_id"].isin(TIPOS_NOTA_FISCAL)] if not bills.empty else bills

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
            achou_chave = universo[universo["access_key_number"] == chave]
            if not achou_chave.empty:
                return _res("LANCADA", "CHAVE", _escolher_bill(achou_chave, usados, debtors_preferidos))

    # 2º passe: CNPJ + número exatos, dentro do universo NFE/NF.
    candidatos = universo[(universo["cnpj_normalizado"] == cnpj) & (universo["numero_normalizado"] == numero)]
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
        do_cnpj = universo[universo["cnpj_normalizado"] == cnpj]   # filtra por CNPJ ANTES de comparar valor (14k titulos x 460 notas: 11s -> <1s)
        mesmo_cnpj_valor = do_cnpj[do_cnpj["total_invoice_amount"].apply(_bate_valor)
                                   & ~do_cnpj["bill_id"].astype(int).isin(usados)] if not do_cnpj.empty else do_cnpj
        if not mesmo_cnpj_valor.empty:
            b = _escolher_bill(mesmo_cnpj_valor, usados, debtors_preferidos)
            return _res("NUMERO_DIVERGENTE", "CNPJ_VALOR", b,
                        f"Sienge tem nota nº {b['document_number']}, manifesto tem nº {row.get('Num')}")

    return dict(status="NAO_ENCONTRADA", confianca=None, sienge_bill_id=None, sienge_valor=None, observacao=None)


def classificar_manifesto(manifesto: pd.DataFrame, bills: pd.DataFrame,
                          mapa_debtor: Optional[dict] = None) -> dict:
    """Classifica TODAS as notas do manifesto de uma rodada. Devolve
    {indice_da_linha: resultado}. Roda em fases pra um titulo nunca ser
    usado duas vezes como "numero divergente":

      1. LANCADA   (chave, ou CNPJ+numero+valor)
      2. VALOR_DIVERGENTE (CNPJ+numero, valor diferente)
      3. NUMERO_DIVERGENTE (CNPJ+valor, so' com titulos ainda sem dono)

    Em cada fase, titulo ja' consumido por outra nota so' e' reaproveitado
    quando a nota e' duplicada no manifesto (mesmo CNPJ+numero+valor).
    `mapa_debtor` ({debtor_id: empresa}) desempata entre titulos iguais
    preferindo o devedor da empresa do manifesto."""
    mapa_debtor = mapa_debtor or {}
    preferidos_por_empresa: dict = {}
    resultados: dict = {}
    usados: set = set()
    for fase in FASES_CLASSIFICACAO:
        for idx, row in manifesto.iterrows():
            if idx in resultados:
                continue
            empresa = row.get("_empresa_codigo")
            if empresa not in preferidos_por_empresa:
                preferidos_por_empresa[empresa] = {d for d, e in mapa_debtor.items() if e == empresa}
            r = _classificar_nota(row, bills, usados, (fase,), preferidos_por_empresa[empresa])
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
              AND (%s::uuid IS NULL OR c.import_id <> %s::uuid)
            GROUP BY b.debtor_id, m.empresa_codigo
            """,
            (excluir_import_id, excluir_import_id),
        )
        linhas = cur.fetchall()
    por_debtor: dict = {}
    for debtor_id, empresa_codigo, n in linhas:
        por_debtor.setdefault(int(debtor_id), {})[empresa_codigo] = int(n)
    return por_debtor


def _mapear_debtor_para_empresa(conn, excluir_import_id: Optional[str] = None) -> dict:
    """Devolve {debtor_id: empresa_codigo}.

    Duas fontes (a manual vence):
      1) APRENDIDA -- debtor_id que bateu SEMPRE com a mesma empresa em todo
         o historico de matches LANCADA (sem contar `excluir_import_id`, pra
         a rodada que esta' sendo recalculada nao se auto-confirmar);
      2) MANUAL -- egc.nf_debtor_empresa, confirmada pela contadora na tela
         ("o app segue o que o Sienge dita": o debtor do titulo diz a
         qual empresa ele foi lancado).
    """
    por_debtor = _historico_debtor_empresas(conn, excluir_import_id)
    mapa = {d: next(iter(emp)) for d, emp in por_debtor.items() if len(emp) == 1}
    mapa.update(carregar_mapa_debtor_manual(conn))
    return mapa


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
    universo = bills[bills["document_identification_id"].isin(TIPOS_NOTA_FISCAL)].copy()
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
                        WHERE empresa_codigo = %s
                          AND periodo_referencia IS DISTINCT FROM (
                                SELECT periodo_referencia FROM egc.nf_manifesto_import
                                WHERE import_id = %s LIMIT 1)
                        ORDER BY periodo_referencia, criado_em DESC))
            """,
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


def identificar_e_gravar_bills_orfaos(conn, import_id: str, empresa_codigo: str) -> int:
    """
    Roda o filtro reverso pra um import_id (chamado por conciliar_import,
    logo depois do matching normal) e grava o resultado em
    egc.nf_bills_orfaos. Idempotente como conciliar_import: apaga e
    regrava os órfãos deste import_id, preservando pendencia_status já
    setado manualmente. Janela de datas = min/max data_emissao das notas
    deste import, +-JANELA_DATA_ORFAOS_DIAS (emissão e lançamento no
    Sienge raramente caem no mesmo dia). Sem nenhuma data_emissao no
    manifesto, não dá pra montar a janela -- não arrisca, devolve 0.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MIN(data_emissao), MAX(data_emissao) FROM egc.nf_manifesto_import WHERE import_id = %s",
            (import_id,),
        )
        data_min, data_max = cur.fetchone() or (None, None)
    if not data_min or not data_max:
        return 0

    janela = pd.Timedelta(days=JANELA_DATA_ORFAOS_DIAS)
    data_min_janela = pd.Timestamp(data_min) - janela
    data_max_janela = pd.Timestamp(data_max) + janela

    try:
        mapa_debtor = _mapear_debtor_para_empresa(conn)
        bills = _carregar_bills_creditores(conn)

        ja_associados = _bills_associados_no_escopo(conn, import_id, empresa_codigo)

        orfaos = _filtrar_bills_orfaos(
            bills, mapa_debtor, empresa_codigo, ja_associados, data_min_janela, data_max_janela
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT bill_id, pendencia_status FROM egc.nf_bills_orfaos "
                "WHERE import_id = %s AND pendencia_status IS NOT NULL AND pendencia_status <> 'PENDENTE'",
                (import_id,),
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
                   "_valor_float", "Chave", "_empresa_codigo"
            FROM (
                SELECT id, numero_nota AS "Num", numero_normalizado AS "_numero_normalizado",
                       cnpj_normalizado AS "_cnpj_normalizado", valor AS "_valor_float",
                       chave_acesso AS "Chave", empresa_codigo AS "_empresa_codigo"
                FROM egc.nf_manifesto_import WHERE import_id = %s
            ) x
            """,
            (import_id,),
        )
        cols = [d[0] for d in cur.description]
        manifesto = pd.DataFrame(cur.fetchall(), columns=cols)

    resumo = {"total": len(manifesto), "lancadas": 0, "pendencias": 0, "orfaos_sienge": 0, "outra_empresa": 0,
              "valor_divergente": 0, "numero_divergente": 0, "nao_encontradas": 0}
    if manifesto.empty:
        return resumo
    # de-para debtor -> empresa (sem contar esta propria rodada) e debtor de cada titulo
    mapa_debtor = _mapear_debtor_para_empresa(conn, import_id)
    debtor_por_bill = {}
    if not bills.empty and "debtor_id" in bills.columns:
        debtor_por_bill = {int(b): d for b, d in zip(bills["bill_id"], bills["debtor_id"])}

    # v0.44.3: matching em fases com consumo de titulo (classificar_manifesto)
    classificacao = classificar_manifesto(manifesto, bills, mapa_debtor)
    obs_ignoradas = observacoes_de_ignoradas(manifesto, listar_ignoradas(conn, import_id))

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

        for idx, row in manifesto.iterrows():
            resultado = classificacao[idx]
            if resultado.get("sienge_bill_id") is not None:
                resultado = checar_empresa_do_lancamento(
                    resultado, row["_empresa_codigo"], debtor_por_bill.get(int(resultado["sienge_bill_id"])), mapa_debtor,
                )
            status_final = resultado["status"]
            if status_final != "LANCADA" and idx in obs_ignoradas:
                resultado = dict(resultado)
                resultado["observacao"] = " | ".join(x for x in (resultado.get("observacao"), obs_ignoradas[idx]) if x)
            if status_final == "LANCADA":
                resumo["lancadas"] += 1
                pendencia_status = None
            else:
                resumo["pendencias"] += 1
                pendencia_status = status_preservados.get(row["id"], "PENDENTE")

            sql_ins = """
                INSERT INTO egc.nf_conciliacao
                    (import_id, manifesto_id, status, sienge_bill_id, sienge_valor,
                     confianca, observacao, pendencia_status, atualizado_em)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                """
            params = (import_id, row["id"], resultado["status"], resultado["sienge_bill_id"],
                      resultado["sienge_valor"], resultado["confianca"], resultado["observacao"], pendencia_status)
            try:
                cur.execute(sql_ins, params)
            except psycopg2.errors.CheckViolation:
                # CHECK de status ainda sem LANCADA_OUTRA_EMPRESA (bloco 19 do
                # schema nao rodado): grava como LANCADA mantendo o alerta na
                # observacao -- nunca perde a nota nem quebra a conferencia.
                resumo["lancadas"] += 1
                resumo["pendencias"] -= 1
                status_final = "LANCADA"
                cur.execute(sql_ins, params[:2] + ("LANCADA",) + params[3:7] + (None,))
            chave_resumo = {"LANCADA_OUTRA_EMPRESA": "outra_empresa", "VALOR_DIVERGENTE": "valor_divergente",
                            "NUMERO_DIVERGENTE": "numero_divergente", "NAO_ENCONTRADA": "nao_encontradas"}.get(status_final)
            if chave_resumo:
                resumo[chave_resumo] += 1

    # FIX_20260928f -- direção reversa (Sienge sem nota no manifesto),
    # roda depois do matching normal (usa o resultado dele: bill já
    # associado a alguma nota, mesmo que divergente, não conta como orfao).
    resumo["orfaos_sienge"] = identificar_e_gravar_bills_orfaos(
        conn, import_id, manifesto["_empresa_codigo"].iloc[0]
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
                WHERE mm.cnpj_normalizado = regexp_replace(COALESCE(o.creditor_cnpj, ''), '\\D', '', 'g')
                  AND mm.numero_normalizado = COALESCE(
                        NULLIF(ltrim(regexp_replace(COALESCE(o.document_number, ''), '\\D', '', 'g'), '0'), ''), '0')
                ORDER BY (mm.import_id = o.import_id) DESC, (mm.empresa_codigo = o.empresa_codigo) DESC, mm.criado_em DESC
                LIMIT 1
            ) mx ON true
            WHERE o.import_id = %s
            """,
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
            LEFT JOIN egc.nf_import_historico h ON h.import_id = o.import_id
            LEFT JOIN LATERAL (
                SELECT mm.cfop, mm.valor, mm.empresa_codigo AS emp, mm.periodo_referencia AS per
                FROM egc.nf_manifesto_import mm
                WHERE mm.cnpj_normalizado = regexp_replace(COALESCE(o.creditor_cnpj, ''), '\\D', '', 'g')
                  AND mm.numero_normalizado = COALESCE(
                        NULLIF(ltrim(regexp_replace(COALESCE(o.document_number, ''), '\\D', '', 'g'), '0'), ''), '0')
                ORDER BY (mm.import_id = o.import_id) DESC, (mm.empresa_codigo = o.empresa_codigo) DESC, mm.criado_em DESC
                LIMIT 1
            ) mx ON true
            WHERE (o.pendencia_status IS NULL OR o.pendencia_status IN ('PENDENTE','ENVIADO_SUPRIMENTOS'))
              AND (%s IS NULL OR o.empresa_codigo = %s)
            ORDER BY o.issue_date DESC
            LIMIT %s
            """,
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
    (qualquer rodada, a mais recente primeiro). Filtros opcionais e combinaveis:
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
            WHERE {where}
            ORDER BY m.criado_em DESC, m.data_emissao DESC
            LIMIT %s
            """,
            tuple(params) + (max(1, min(int(limite), 50)),),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


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
