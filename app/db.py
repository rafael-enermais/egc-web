# -*- coding: utf-8 -*-
"""
Camada de acesso a dados — schema `egc` no Supabase (Postgres).

Reproduz, em SQL, as regras de negocio da BASE_HISTORICA/VBA (ver
PROJETO_EGC_v3.0.md secoes 7 e 8), com uma simplificacao estrutural:
no sistema antigo havia 2 lugares (aba BP/DRE "ao vivo" + BASE_HISTORICA
"historico"), e por isso o PDF ORIGINAL as vezes se perdia num ciclo
Arquivar->Recuperar (limitacao conhecida, secao 7). Aqui so existe UMA
tabela (egc.lancamentos) com um campo `status`; arquivar/recuperar so
alternam esse campo na MESMA linha — pdf_original nunca e tocado por
arquivar/recuperar, entao essa limitacao antiga nao existe mais aqui.

Todas as funcoes recebem uma conexao psycopg2 aberta (`conn`) — quem
gerencia connection pooling/cache e o chamador (app.py, via
st.cache_resource). Isso mantem este modulo testavel sem Streamlit nem
banco real (ver app/tests/test_db_logic.py — mocka conn/cursor).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import psycopg2.errors

# psycopg2 so e importado por quem realmente abre conexao (app.py);
# este modulo usa apenas a interface DB-API (cursor/execute/fetchall),
# entao roda com QUALQUER driver compativel (psycopg2, ou um mock nos
# testes) sem precisar da lib instalada so pra importar db.py.


# ─────────────────────────────────────────────
#  EMPRESAS
# ─────────────────────────────────────────────

def listar_empresas(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT codigo, nome, cnpj FROM egc.empresas WHERE ativo = true ORDER BY nome"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─────────────────────────────────────────────
#  PERIODOS
# ─────────────────────────────────────────────

def listar_periodos(conn, empresa_codigo: str, status: str = "ATIVO") -> list[date]:
    """
    Períodos distintos (só a data-fim) de uma empresa com o status pedido
    (ATIVO ou INATIVO). Continua devolvendo so' `date` -- usado em varios
    lugares que so' precisam da data (Revisao/Correcao, Arquivar/
    Recuperar, Visao Grupo, chat) e nao decidem entre 2 granularidades do
    mesmo periodo_fim. Onde essa decisao importa de verdade (gerador de
    relatorio, painel de completude), usa listar_periodos_detalhado.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT periodo
            FROM egc.lancamentos
            WHERE empresa_codigo = %s AND status = %s
            ORDER BY periodo DESC
            """,
            (empresa_codigo, status),
        )
        return [row[0] for row in cur.fetchall()]


def listar_periodos_detalhado(conn, empresa_codigo: str, status: str = "ATIVO") -> list[dict]:
    """
    Fase 3 (29/09/2026, "vamos estruturar e implantar a granularidade...
    podemos casar toda a estrutura com isso"): mesma consulta de
    listar_periodos, mas 1 linha por (periodo, granularidade) -- pra
    quem precisa DIFERENCIAR um semestre fechado de um trimestre que
    fecham na MESMA data (ex. 30/06/2026), em vez de colapsar num só
    `date`. Usado pelo seletor de período do Relatório Comentado e pelo
    painel de completude/pendências.

    Retorna [{"periodo": date, "granularidade": str}, ...], mais recente
    primeiro; granularidade "" = não declarada no PDF (BP sem intervalo
    próprio, ou import anterior a 24/09/2026 -- normalizado pelo bloco 16
    do schema.sql, nunca None aqui).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT periodo, COALESCE(granularidade, '') AS granularidade
            FROM egc.lancamentos
            WHERE empresa_codigo = %s AND status = %s
            ORDER BY periodo DESC, granularidade
            """,
            (empresa_codigo, status),
        )
        return [{"periodo": row[0], "granularidade": row[1]} for row in cur.fetchall()]


def listar_periodos_recuperaveis(conn, empresa_codigo: str) -> list[dict]:
    """(periodo, granularidade) que o "Recuperar" consegue reativar (v0.44.1): tem lancamento
    INATIVO de um tipo (BP/DRE) que NAO tem versao ATIVA. Versao anterior substituida por
    reimportacao (o periodo continua ativo) fica de fora -- para voltar a ela use o
    "Desfazer" do Importar PDF (db.desfazer_importacao). Antes o Recuperar listava esses
    periodos e clicar nao fazia nada."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT i.periodo, COALESCE(i.granularidade, '') AS granularidade
            FROM egc.lancamentos i
            WHERE i.empresa_codigo = %s AND i.status = 'INATIVO'
              AND NOT EXISTS (
                SELECT 1 FROM egc.lancamentos a
                WHERE a.empresa_codigo = i.empresa_codigo AND a.periodo = i.periodo AND a.tipo = i.tipo
                  AND COALESCE(a.granularidade, '') = COALESCE(i.granularidade, '') AND a.status = 'ATIVO')
            ORDER BY i.periodo DESC, granularidade
            """,
            (empresa_codigo,),
        )
        return [{"periodo": r[0], "granularidade": r[1]} for r in cur.fetchall()]


def arquivos_ativos_por_periodo(conn, empresa_codigo: str) -> dict:
    """{periodo: {arquivo_pdf, ...}} dos lancamentos ATIVOS da empresa (v0.44.0).
    O historico de importacoes usa pra saber se ESTE import (pelo nome do
    arquivo) ainda e' a versao ativa -- e nao apenas "o periodo tem algo ativo",
    que dava "Ativo" errado na linha de um import ja desfeito."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT periodo, COALESCE(arquivo_pdf, '') FROM egc.lancamentos
               WHERE empresa_codigo = %s AND status = 'ATIVO' GROUP BY 1, 2""",
            (empresa_codigo,),
        )
        out: dict = {}
        for per, arq in cur.fetchall():
            out.setdefault(per, set()).add(arq)
        return out


def listar_periodos_completos_detalhado(conn, empresa_codigo: str, status: str = "ATIVO") -> list[dict]:
    """
    v0.40.0: (periodo, granularidade) em que a empresa tem BP **E** DRE
    com o status pedido. E' o que os seletores de periodo do Relatorio
    Comentado usam: listar_periodos_detalhado devolve o par se existir
    QUALQUER tipo (so' BP, por ex.), e a tela oferecia um periodo que a
    geracao depois recusava ("nao tem DRE gravado").

    Mesmo formato de listar_periodos_detalhado ([{"periodo", "granularidade"}],
    mais recente primeiro; granularidade "" = nao declarada).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT periodo, COALESCE(granularidade, '') AS granularidade
            FROM egc.lancamentos
            WHERE empresa_codigo = %s AND status = %s
            GROUP BY periodo, COALESCE(granularidade, '')
            HAVING bool_or(tipo = 'BP') AND bool_or(tipo = 'DRE')
            ORDER BY periodo DESC, COALESCE(granularidade, '')
            """,
            (empresa_codigo, status),
        )
        return [{"periodo": row[0], "granularidade": row[1]} for row in cur.fetchall()]


def listar_inicios_documento(
    conn, empresa_codigo: str, periodo: date, granularidade: str, tipo: str = "DRE", status: str = "ATIVO",
) -> list:
    """
    v0.40.0: valores DISTINTOS de `periodo_inicio` (inicio do intervalo
    declarado no proprio PDF) das linhas ATIVAS do documento
    (empresa, periodo_fim, granularidade). Usado para conferir que o
    rotulo de granularidade gravado ("trimestral") bate com o intervalo
    que o PDF realmente declarou (01/04 a 30/06 = trimestral; 01/01 a
    30/06 = semestral) -- ver dados_relatorio_comentado.
    validar_cobertura_documento. Lista vazia se o documento nao declarou
    intervalo (BP, ou import anterior a 24/09/2026) ou se a coluna ainda
    nao existe no banco (nunca levanta por isso).
    """
    with conn.cursor() as cur:
        try:
            cur.execute(
                """
                SELECT DISTINCT periodo_inicio
                FROM egc.lancamentos
                WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s AND status = %s
                  AND COALESCE(granularidade, '') = %s AND periodo_inicio IS NOT NULL
                ORDER BY periodo_inicio
                """,
                (empresa_codigo, periodo, tipo, status, granularidade or ""),
            )
            return [row[0] for row in cur.fetchall()]
        except psycopg2.errors.UndefinedColumn:
            conn.rollback()
            return []


# ─────────────────────────────────────────────
#  IMPORTACAO
# ─────────────────────────────────────────────

def inativar_periodo_existente(
    conn, empresa_codigo: str, periodo: date, tipo: str, granularidade: str = "",
) -> int:
    """
    Reimportacao do MESMO empresa+periodo+tipo+granularidade: inativa
    (nunca apaga) as linhas ATIVAS anteriores antes de gravar as novas —
    equivalente ao 'FIX CRITICO 20/07/2026' do VBA (remove linhas
    pre-existentes antes de regravar), so que aqui vira soft-inactivate
    em vez de hard-delete, seguindo a mesma filosofia que levou a
    remover o 'Desfazer' do painel (nunca apagar historico de forma
    irreversivel).

    granularidade (Fase 3, 29/09/2026 -- resposta pra pergunta real do
    Rafael sobre 2 PDFs do mesmo empresa+periodo_fim com abrangencia
    diferente, ex. semestre x trimestre ambos fechando 30/06/2026):
    ANTES desta mudanca, so' olhava empresa+periodo+tipo -- o 2o PDF
    inativava o 1o mesmo com granularidade DIFERENTE, sem avisar que a
    abrangencia tinha mudado. Agora so' inativa o que tem a MESMA
    granularidade (default "" = sem intervalo declarado, o caso comum
    de BP e de imports anteriores a 24/09/2026) -- reimportar o
    trimestral de novo continua substituindo so' o trimestral antigo; o
    semestral que porventura esteja ativo pro mesmo periodo_fim fica
    intocado, os dois convivem (ver bloco 17 do schema.sql, chave ativa
    agora inclui granularidade).
    Retorna quantas linhas foram inativadas.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE egc.lancamentos
            SET status = 'INATIVO', atualizado_em = now()
            WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s
              AND COALESCE(granularidade, '') = %s AND status = 'ATIVO'
            """,
            (empresa_codigo, periodo, tipo, granularidade),
        )
        return cur.rowcount


def listar_documentos_ativos(
    conn, empresa_codigo: str, periodo: date, tipo: str, granularidade: str = "",
) -> list[dict]:
    """
    Documento(s) ATIVO(s) ja' gravado(s) pra (empresa, periodo, tipo,
    granularidade) -- 1 linha por arquivo de origem. A tela Importar PDF
    usa pra avisar "ja existe; gravar vai arquivar e substituir" ANTES de
    gravar (01/10/2026), em vez de inativar em silencio como
    `inativar_periodo_existente` faz. So' leitura.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(arquivo_pdf, '(sem arquivo)') AS arquivo, COUNT(*) AS contas, MAX(criado_em) AS gravado_em
            FROM egc.lancamentos
            WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s
              AND COALESCE(granularidade, '') = %s AND status = 'ATIVO'
            GROUP BY COALESCE(arquivo_pdf, '(sem arquivo)')
            ORDER BY gravado_em DESC
            """,
            (empresa_codigo, periodo, tipo, granularidade or ""),
        )
        return [{"arquivo": r[0], "contas": r[1], "gravado_em": r[2]} for r in cur.fetchall()]


def inserir_lancamentos(
    conn,
    empresa_codigo: str,
    tipo: str,
    periodo: date,
    rows: list,
    arquivo_pdf: str,
    usuario: Optional[str] = None,
    periodo_inicio: Optional[date] = None,
    granularidade: Optional[str] = None,
) -> int:
    """
    Insere o lote extraido do PDF. `rows` no formato de saida do parser
    (parser_egc.build_output): BP = [grupo, conta, valor_br, origem];
    DRE = [conta, valor_br, grupo, origem]. Valor chega como string
    BR-formatada (ex. "1.094.484,54") — convertida pra numeric aqui.

    periodo_inicio/granularidade (24/09/2026): intervalo real declarado
    no proprio PDF ("Periodo: X a Y") e a classificacao mensal/
    trimestral/semestral/anual derivada dele (parser_egc.
    calcular_granularidade). Opcionais e None por padrao -- BP nao tem
    intervalo (e foto de 1 data) e chamadores antigos continuam
    funcionando sem passar nada.

    FIX_20260930b: granularidade NUNCA vai como NULL pro banco, mesmo
    que o chamador passe None -- a coluna e' NOT NULL DEFAULT '' desde o
    bloco 16 do schema.sql, mas o DEFAULT so' se aplica quando a coluna
    fica FORA do INSERT; como este INSERT sempre lista a coluna, um None
    explicito vira NULL de verdade e quebra a constraint (bug real visto
    em producao: "null value in column granularidade... violates
    not-null constraint", gravando um BP sem intervalo declarado, o caso
    mais comum). Normaliza aqui pra "" -- defesa contra qualquer
    chamador (presente ou futuro) que passe None.
    """
    from parser_egc import br_to_float

    granularidade = granularidade if granularidade is not None else ""
    import uuid as _uuid
    lote = str(_uuid.uuid4())  # v0.44.0: identifica ESTE import (geracao) p/ desfazer/recuperar
    registros = []
    for r in rows:
        if tipo == "BP":
            grupo, conta, valor_br, origem = r
        else:  # DRE — ordem de colunas diferente no parser
            conta, valor_br, grupo, origem = r
        valor = br_to_float(valor_br)
        registros.append((
            empresa_codigo, tipo, periodo, grupo, conta, valor, origem,
            arquivo_pdf, usuario, periodo_inicio, granularidade, lote,
        ))

    # Tentativas em ordem: completa (com lote) -> sem lote (bloco 21 ainda nao
    # rodou) -> sem periodo_inicio/granularidade (bloco 16 ainda nao rodou).
    # Cada fallback so' acontece em UndefinedColumn; nao trava a importacao.
    tentativas = [
        ("empresa_codigo, tipo, periodo, grupo, conta, valor, origem, arquivo_pdf, usuario, "
         "periodo_inicio, granularidade, lote", 12),
        ("empresa_codigo, tipo, periodo, grupo, conta, valor, origem, arquivo_pdf, usuario, "
         "periodo_inicio, granularidade", 11),
        ("empresa_codigo, tipo, periodo, grupo, conta, valor, origem, arquivo_pdf, usuario", 9),
    ]
    with conn.cursor() as cur:
        for k, (colunas, n) in enumerate(tentativas):
            try:
                cur.executemany(
                    f"INSERT INTO egc.lancamentos ({colunas}) VALUES ({', '.join(['%s'] * n)})",
                    [r[:n] for r in registros],
                )
                break
            except psycopg2.errors.UndefinedColumn:
                if k == len(tentativas) - 1:
                    raise
                conn.rollback()
        return cur.rowcount


def salvar_despesas_admin_itens(
    conn,
    empresa_codigo: str,
    periodo: date,
    itens: list,
    arquivo_pdf: Optional[str] = None,
    granularidade: Optional[str] = None,
) -> int:
    """
    FIX_20260925b — grava os itens (sub-contas) do grupo "Administrativas"
    da DRE (parser_egc.extrair_despesas_admin_itens), usados so' pelo
    ranking despesas_admin_itens do gerador de relatorio comentado.

    Tabela DERIVADA/isolada (egc.despesas_admin_itens) sem trilha de
    auditoria — nao passa por Correcao Manual nem Arquivar/Recuperar, nao
    e' lida por indicadores.py/visao_grupo.py. Substitui (delete+insert)
    os itens do documento: reimportar o mesmo periodo troca os antigos
    pelos novos, sem historico (se precisar corrigir, reimporta o PDF).

    `itens` no formato de extrair_despesas_admin_itens: [(conta, valor), ...].
    Silenciosamente vira no-op se a tabela ainda nao existir no banco
    (migracao do bloco 12 do schema.sql ainda nao rodou) -- mesmo padrao
    de fallback ja usado em inserir_lancamentos p/ nao travar o resto da
    importacao por causa de uma tabela nova que so' esta Fase 2 usa.

    granularidade (Fase 4, 30/09/2026, bloco 19 do schema.sql): o
    documento e' identificado por (empresa, periodo_fim, granularidade) --
    antes, reimportar o SEMESTRAL de 06/2026 apagava os itens do
    TRIMESTRAL de 06/2026 (delete por empresa+periodo) e o ranking do
    relatorio trimestral mostrava itens do semestral. Com granularidade
    informada (inclusive ""), so' troca os itens dessa granularidade. None
    (chamador legado) mantem o comportamento antigo (empresa+periodo).
    Se a coluna `granularidade` ainda nao existe (bloco 19 nao rodou),
    cai no comportamento antigo sem quebrar.
    """
    def _legado(cur):
        cur.execute(
            "DELETE FROM egc.despesas_admin_itens WHERE empresa_codigo = %s AND periodo = %s",
            (empresa_codigo, periodo),
        )
        if itens:
            cur.executemany(
                """
                INSERT INTO egc.despesas_admin_itens
                    (empresa_codigo, periodo, ordem, conta, valor, arquivo_pdf)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [(empresa_codigo, periodo, i, conta, valor, arquivo_pdf) for i, (conta, valor) in enumerate(itens)],
            )

    with conn.cursor() as cur:
        try:
            if granularidade is None:
                _legado(cur)
            else:
                try:
                    cur.execute(
                        """
                        DELETE FROM egc.despesas_admin_itens
                        WHERE empresa_codigo = %s AND periodo = %s AND COALESCE(granularidade, '') = %s
                        """,
                        (empresa_codigo, periodo, granularidade),
                    )
                    if itens:
                        cur.executemany(
                            """
                            INSERT INTO egc.despesas_admin_itens
                                (empresa_codigo, periodo, ordem, conta, valor, arquivo_pdf, granularidade)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            [
                                (empresa_codigo, periodo, i, conta, valor, arquivo_pdf, granularidade)
                                for i, (conta, valor) in enumerate(itens)
                            ],
                        )
                except psycopg2.errors.UndefinedColumn:
                    conn.rollback()
                    with conn.cursor() as cur2:
                        _legado(cur2)
        except psycopg2.errors.UndefinedTable:
            conn.rollback()
            return 0
        return len(itens)


def listar_despesas_admin_itens(
    conn, empresa_codigo: str, periodo: date, granularidade: Optional[str] = None,
) -> list:
    """
    Retorna [(conta, valor_float), ...] na ordem original do PDF. Lista
    vazia (nunca erro) se a tabela nao existir ainda ou se o periodo nao
    tiver itens gravados (ex.: DRE importada antes desta Fase 2, ou
    periodo que nao tem grupo Administrativas) -- o gerador de relatorio
    ja trata despesas_admin_itens=[] de forma segura (degrada sem quebrar).

    granularidade (Fase 4, 30/09/2026, bloco 19): filtro por documento
    (inclusive "" = nao declarada); None nao filtra (legado). Se a coluna
    ainda nao existe no banco, devolve os itens de empresa+periodo como
    antes -- quem consome (dados_relatorio_comentado) valida a soma contra
    o total ADMINISTRATIVAS do DRE antes de usar.
    """
    with conn.cursor() as cur:
        try:
            if granularidade is not None:
                try:
                    cur.execute(
                        """
                        SELECT conta, valor FROM egc.despesas_admin_itens
                        WHERE empresa_codigo = %s AND periodo = %s AND COALESCE(granularidade, '') = %s
                        ORDER BY ordem
                        """,
                        (empresa_codigo, periodo, granularidade),
                    )
                    return [(row[0], float(row[1])) for row in cur.fetchall()]
                except psycopg2.errors.UndefinedColumn:
                    conn.rollback()
            cur.execute(
                """
                SELECT conta, valor FROM egc.despesas_admin_itens
                WHERE empresa_codigo = %s AND periodo = %s
                ORDER BY ordem
                """,
                (empresa_codigo, periodo),
            )
            return [(row[0], float(row[1])) for row in cur.fetchall()]
        except psycopg2.errors.UndefinedTable:
            conn.rollback()
            return []


def registrar_importacao(
    conn,
    empresa_codigo: Optional[str],
    periodo: Optional[date],
    arquivos: list,
    nivel: str,
    tipo: Optional[str],
    mensagem: str,
    usuario: Optional[str] = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO egc.importacoes (empresa_codigo, periodo, arquivos, nivel, tipo, mensagem, usuario)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (empresa_codigo, periodo, arquivos, nivel, tipo, mensagem, usuario),
        )


def listar_importacoes_recentes(conn, limite: int = 30) -> list[dict]:
    """
    Historico de importacoes (tabela egc.importacoes, alimentada por
    registrar_importacao a cada grava). Usado no Importar PDF pra mostrar
    "importacoes recentes" com opcao de desfazer QUALQUER uma delas (nao
    so a ultima da sessao) -- pedido do Rafael 21/09/2026. Retorna linhas
    cruas (pode ter 1 linha por tipo BP/DRE do mesmo periodo); quem chama
    agrupa por (empresa_codigo, periodo) se precisar de 1 linha por
    "evento de import".
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT empresa_codigo, periodo, criado_em, usuario, tipo, nivel, mensagem, arquivos
            FROM egc.importacoes
            WHERE empresa_codigo IS NOT NULL AND periodo IS NOT NULL
            ORDER BY criado_em DESC
            LIMIT %s
            """,
            (limite,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─────────────────────────────────────────────
#  REVISAO / CORRECAO MANUAL
# ─────────────────────────────────────────────

def listar_lancamentos(
    conn, empresa_codigo: str, periodo: date, tipo: str, status: str = "ATIVO",
    granularidade: Optional[str] = None,
) -> list[dict]:
    """
    granularidade (Fase 3, 29/09/2026): filtro OPCIONAL -- None (default)
    nao filtra (comportamento de sempre, usado por quem ainda nao decide
    entre 2 granularidades do mesmo periodo_fim, ex. Revisao/Correcao).
    Passe explicitamente (mesmo "") quando 2 documentos podem coexistir
    ATIVOS pro mesmo periodo_fim (ver inativar_periodo_existente) e o
    chamador precisa de UM dos dois, nao os dois misturados -- caso do
    gerador de relatorio (dados_relatorio_comentado.montar_dados_relatorio).
    """
    sql = """
        SELECT id, grupo, conta, valor, origem, pdf_original, arquivo_pdf, atualizado_em
        FROM egc.lancamentos
        WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s AND status = %s
    """
    params = [empresa_codigo, periodo, tipo, status]
    if granularidade is not None:
        sql += " AND COALESCE(granularidade, '') = %s"
        params.append(granularidade)
    sql += " ORDER BY grupo, conta"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def salvar_correcao_manual(
    conn,
    lancamento_id: Optional[int],
    novo_valor: float,
    periodo: date,
    usuario: Optional[str] = None,
    # usados so quando lancamento_id e None (conta nao existia -> criar linha nova,
    # igual ao VBA: "Grupo = AJUSTE MANUAL"):
    empresa_codigo: Optional[str] = None,
    tipo: Optional[str] = None,
    conta: Optional[str] = None,
    granularidade: Optional[str] = None,
) -> int:
    """
    Corrige o valor de UM lancamento (equivalente a SalvarCorrecoesManuais
    do VBA, aplicada linha a linha):
      - pdf_original so e gravado na 1a edicao (COALESCE nunca sobrescreve
        o que ja tem) — protege o valor de referencia do PDF, igual a
        regra "nunca sobrescrita depois" do VBA.
      - origem vira 'MANUAL <periodo>'.
      - se lancamento_id for None, a conta nao existia (PDF nao trouxe o
        campo): cria linha nova com grupo='AJUSTE MANUAL', sem pdf_original
        (nao havia PDF original pra essa conta).
    Retorna o id do lancamento afetado (novo ou existente).

    granularidade (Fase 3.1, 29/09/2026): so' usada quando lancamento_id
    e' None (linha nova) -- amarra a conta adicionada a mao ao MESMO
    documento (periodo, granularidade) que a contadora estava revisando
    em 2_Revisao_Correcao.py, em vez de sempre cair em granularidade=''
    (o que criaria um 3o "documento" fantasma pro mesmo periodo_fim
    quando ja existem 2 ativos). None/"" -- comportamento de sempre
    (coluna tem DEFAULT '', ver schema.sql BLOCO 16).
    """
    periodo_str = periodo.strftime("%m/%Y") if hasattr(periodo, "strftime") else str(periodo)
    origem = f"MANUAL {periodo_str}"

    with conn.cursor() as cur:
        if lancamento_id is not None:
            cur.execute(
                """
                UPDATE egc.lancamentos
                SET pdf_original = COALESCE(pdf_original, valor),
                    valor = %s,
                    origem = %s,
                    usuario = %s,
                    atualizado_em = now()
                WHERE id = %s
                RETURNING id
                """,
                (novo_valor, origem, usuario, lancamento_id),
            )
        else:
            try:
                # v0.44.0: a conta criada a mao herda o LOTE do documento ativo
                # (empresa, tipo, periodo, granularidade) -- assim ela acompanha o
                # documento no Desfazer/Recuperar em vez de virar "orfa".
                cur.execute(
                    """
                    INSERT INTO egc.lancamentos
                        (empresa_codigo, tipo, periodo, grupo, conta, valor, origem, usuario, granularidade, lote)
                    VALUES (%s, %s, %s, 'AJUSTE MANUAL', %s, %s, %s, %s, %s,
                        (SELECT lote FROM egc.lancamentos
                          WHERE empresa_codigo = %s AND tipo = %s AND periodo = %s
                            AND COALESCE(granularidade, '') = %s AND status = 'ATIVO' AND lote IS NOT NULL
                          ORDER BY criado_em DESC LIMIT 1))
                    RETURNING id
                    """,
                    (empresa_codigo, tipo, periodo, conta, novo_valor, origem, usuario, granularidade or "",
                     empresa_codigo, tipo, periodo, granularidade or ""),
                )
            except psycopg2.errors.UndefinedColumn:  # bloco 21 ainda nao rodou
                conn.rollback()
                cur.execute(
                    """
                    INSERT INTO egc.lancamentos
                        (empresa_codigo, tipo, periodo, grupo, conta, valor, origem, usuario, granularidade)
                    VALUES (%s, %s, %s, 'AJUSTE MANUAL', %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (empresa_codigo, tipo, periodo, conta, novo_valor, origem, usuario, granularidade or ""),
                )
        row = cur.fetchone()
        return row[0] if row else None


# ─────────────────────────────────────────────
#  ARQUIVAR / RECUPERAR (equivalente a ArquivarImportacao/RecuperarImportacao)
# ─────────────────────────────────────────────

def arquivar_periodo(conn, empresa_codigo: str, periodo: date, granularidade: Optional[str] = None) -> int:
    """
    Inativa TODAS as linhas (BP+DRE) do periodo — nunca apaga.

    granularidade (Fase 3, 29/09/2026): filtro OPCIONAL -- None (default)
    arquiva TODAS as granularidades desse periodo_fim de uma vez (mesmo
    comportamento de sempre; se so' existe 1 granularidade ativa nesse
    periodo, que e' o caso comum, nao muda nada). Passe explicitamente
    quando o periodo_fim tem 2 granularidades ATIVAS ao mesmo tempo (ex.
    trimestral e semestral, ambos fechando 30/06/2026) e so' 1 delas deve
    ser arquivada.
    """
    sql = """
        UPDATE egc.lancamentos
        SET status = 'INATIVO', atualizado_em = now()
        WHERE empresa_codigo = %s AND periodo = %s AND status = 'ATIVO'
    """
    params = [empresa_codigo, periodo]
    if granularidade is not None:
        sql += " AND COALESCE(granularidade, '') = %s"
        params.append(granularidade)
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.rowcount


# v0.44.0 -- "geracao" (versao) de um documento: cada gravacao do PDF recebe um
# `lote` (uuid). Reimportar o mesmo periodo arquiva a geracao antiga e grava a
# nova; sem saber qual e' qual, "Recuperar" tentava reativar as DUAS (erro de
# chave duplicada) e "Desfazer" so' arquivava a nova, sem trazer a antiga de volta.
# Linhas antigas (sem lote) caem no fallback "a:<arquivo_pdf>"; linhas sem lote
# e sem arquivo (ajustes manuais legados) ficam de fora da escolha da geracao.
_GERACAO_SQL = "COALESCE(lote::text, 'a:' || COALESCE(arquivo_pdf, ''))"


def _geracoes_inativas(cur, empresa_codigo, periodo, tipo, gran):
    """Geracoes INATIVAS de (empresa, periodo, tipo, granularidade), a mais
    recente primeiro: [(chave, qtd_linhas, ultima_gravacao, arquivo)]."""
    cur.execute(
        f"""
        SELECT {_GERACAO_SQL} AS gen, COUNT(*), MAX(criado_em), MAX(arquivo_pdf)
        FROM egc.lancamentos
        WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s
          AND COALESCE(granularidade, '') = %s AND status = 'INATIVO'
          AND NOT (lote IS NULL AND COALESCE(arquivo_pdf, '') = '')
        GROUP BY 1
        ORDER BY MAX(criado_em) DESC
        """,
        (empresa_codigo, periodo, tipo, gran),
    )
    return cur.fetchall()


def _reativar_geracao(cur, empresa_codigo, periodo, tipo, gran, chave) -> int:
    cur.execute(
        f"""
        UPDATE egc.lancamentos
        SET status = 'ATIVO', atualizado_em = now()
        WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s
          AND COALESCE(granularidade, '') = %s AND status = 'INATIVO'
          AND {_GERACAO_SQL} = %s
        """,
        (empresa_codigo, periodo, tipo, gran, chave),
    )
    return cur.rowcount


def recuperar_periodo(conn, empresa_codigo: str, periodo: date, granularidade: Optional[str] = None) -> int:
    """Reativa um periodo previamente arquivado. granularidade: mesmo filtro
    opcional de arquivar_periodo (None = todas as granularidades desse
    periodo_fim).

    v0.44.0: reativa UMA geracao (a gravada por ultimo) por (tipo,
    granularidade) -- nunca duas versoes ao mesmo tempo. Se ja existe
    documento ATIVO desse (tipo, granularidade), nao mexe nele (pra trocar
    de versao: arquive o ativo antes)."""
    sql = """
        SELECT DISTINCT tipo, COALESCE(granularidade, '')
        FROM egc.lancamentos
        WHERE empresa_codigo = %s AND periodo = %s AND status = 'INATIVO'
    """
    params = [empresa_codigo, periodo]
    if granularidade is not None:
        sql += " AND COALESCE(granularidade, '') = %s"
        params.append(granularidade)
    total = 0
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        for tipo, gran in cur.fetchall():
            cur.execute(
                """SELECT 1 FROM egc.lancamentos
                   WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s
                     AND COALESCE(granularidade, '') = %s AND status = 'ATIVO' LIMIT 1""",
                (empresa_codigo, periodo, tipo, gran),
            )
            if cur.fetchone():
                continue
            geracoes = _geracoes_inativas(cur, empresa_codigo, periodo, tipo, gran)
            if geracoes:
                total += _reativar_geracao(cur, empresa_codigo, periodo, tipo, gran, geracoes[0][0])
            else:
                # so' linhas legadas sem lote e sem arquivo: comportamento antigo
                cur.execute(
                    """UPDATE egc.lancamentos SET status = 'ATIVO', atualizado_em = now()
                       WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s
                         AND COALESCE(granularidade, '') = %s AND status = 'INATIVO'""",
                    (empresa_codigo, periodo, tipo, gran),
                )
                total += cur.rowcount
    return total


def desfazer_importacao(conn, empresa_codigo: str, periodo: date, granularidade: Optional[str] = None) -> dict:
    """"Desfazer" do historico de importacoes (v0.44.0): arquiva o documento
    ATIVO do periodo e, se havia uma versao anterior arquivada (a que a
    reimportacao substituiu), REATIVA essa versao -- por (tipo, granularidade).
    Nunca apaga nada.

    Retorna {"arquivados": n, "reativados": n, "reativados_arquivos": [nomes]}.
    """
    sql = """
        SELECT DISTINCT tipo, COALESCE(granularidade, '')
        FROM egc.lancamentos
        WHERE empresa_codigo = %s AND periodo = %s AND status = 'ATIVO'
    """
    params = [empresa_codigo, periodo]
    if granularidade is not None:
        sql += " AND COALESCE(granularidade, '') = %s"
        params.append(granularidade)
    res = {"arquivados": 0, "reativados": 0, "reativados_arquivos": []}
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        for tipo, gran in cur.fetchall():
            cur.execute(
                """UPDATE egc.lancamentos SET status = 'INATIVO', atualizado_em = now()
                   WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s
                     AND COALESCE(granularidade, '') = %s AND status = 'ATIVO'
                   RETURNING COALESCE(lote::text, 'a:' || COALESCE(arquivo_pdf, '')), criado_em""",
                (empresa_codigo, periodo, tipo, gran),
            )
            n_arq = cur.rowcount
            saindo = cur.fetchall()
            recem = {r[0] for r in saindo}
            quando_saiu = max((r[1] for r in saindo), default=None)
            res["arquivados"] += n_arq
            # a anterior = geracao inativa mais recente GRAVADA ANTES da que acabou de sair
            # (assim desfazer em cadeia anda pra tras: C -> B -> A, sem oscilar)
            anteriores = [g for g in _geracoes_inativas(cur, empresa_codigo, periodo, tipo, gran)
                          if g[0] not in recem and (quando_saiu is None or g[2] < quando_saiu)]
            if anteriores:
                chave, _qtd, _quando, arquivo = anteriores[0]
                res["reativados"] += _reativar_geracao(cur, empresa_codigo, periodo, tipo, gran, chave)
                if arquivo and arquivo not in res["reativados_arquivos"]:
                    res["reativados_arquivos"].append(arquivo)
    return res


# ─────────────────────────────────────────────
#  INDICADORES (dashboard — usa a view do schema.sql)
# ─────────────────────────────────────────────

def indicadores(conn, empresa_codigo: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM egc.v_indicadores
            WHERE empresa_codigo = %s
            ORDER BY periodo
            """,
            (empresa_codigo,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─────────────────────────────────────────────
#  VISAO GRUPO (BP/DRE consolidado multi-empresa)
# ─────────────────────────────────────────────

def listar_periodos_grupo(conn, empresas_codigos: list[str], status: str = "ATIVO") -> list[date]:
    """
    Uniao dos periodos com lancamentos (do status pedido) entre as
    empresas informadas -- usado na Visao Grupo pra montar o seletor de
    periodo a partir de QUALQUER empresa do grupo selecionado, nao so' 1.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT periodo
            FROM egc.lancamentos
            WHERE empresa_codigo = ANY(%s) AND status = %s
            ORDER BY periodo DESC
            """,
            (empresas_codigos, status),
        )
        return [row[0] for row in cur.fetchall()]


def listar_periodos_grupo_detalhado(conn, empresas_codigos: list[str], status: str = "ATIVO") -> list[dict]:
    """
    Mesma logica de listar_periodos_detalhado, mas pra uniao de varias
    empresas (equivalente detalhado de listar_periodos_grupo) -- usado
    pelo painel de completude/pendencias e pelo modo multi-empresa do
    Relatorio Comentado, que precisam saber QUAL granularidade cada
    periodo tem, nao so' a data.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT periodo, COALESCE(granularidade, '') AS granularidade
            FROM egc.lancamentos
            WHERE empresa_codigo = ANY(%s) AND status = %s
            ORDER BY periodo DESC, granularidade
            """,
            (empresas_codigos, status),
        )
        return [{"periodo": row[0], "granularidade": row[1]} for row in cur.fetchall()]


def listar_lancamentos_grupo_periodos(
    conn, periodos: list[date], tipo: str, empresas_codigos: list[str], status: str = "ATIVO",
    granularidade: Optional[str] = None,
) -> list[dict]:
    """
    Mesma coisa que listar_lancamentos_grupo, mas pra VARIOS periodos de
    uma vez (1 query, em vez de N) -- usado no resumo/KPIs multi-periodo
    da Visao Grupo "Completo" (22/09/2026, pedido do Rafael: "a visao
    poder alcancar tudo de todos os periodos, ate' 1 periodo de 1 CNPJ
    apenas"). Cada linha ganha o campo `periodo` a mais (a versao de 1
    periodo so' nao precisa dele porque ja fixa no filtro) -- quem chama
    agrupa por periodo+conta pra montar a serie temporal (ver
    visao_grupo.montar_serie_kpis_grupo). listar_lancamentos_grupo
    (1 periodo) continua existindo do jeito que esta' -- usada pela tabela
    de detalhe (1 periodo por vez) e pela ferramenta consultar_visao_grupo
    do chat, sem mudar nenhuma das duas.

    Cada linha ganha tambem `granularidade` (Fase 3, 29/09/2026) -- usado
    pelo painel de completude (visao_grupo.calcular_completude_grupo) pra
    nao misturar 2 documentos de abrangencia diferente no mesmo
    periodo_fim como se fossem 1 so'. Quem so' precisa de periodo+conta
    (ex. indicadores.calcular_indicadores) ignora a coluna extra.
    """
    sql = """
        SELECT empresa_codigo, periodo, grupo, conta, valor, COALESCE(granularidade, '') AS granularidade
        FROM egc.lancamentos
        WHERE periodo = ANY(%s) AND tipo = %s AND status = %s AND empresa_codigo = ANY(%s)
    """
    params = [periodos, tipo, status, empresas_codigos]
    if granularidade is not None:
        # v0.40.0: filtro no proprio SQL (None = legado, traz tudo e quem
        # chama resolve). Evita carregar -- e arriscar misturar -- os
        # documentos de outras granularidades do mesmo periodo_fim.
        sql += " AND COALESCE(granularidade, '') = %s"
        params.append(granularidade)
    sql += " ORDER BY periodo, grupo, conta"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def listar_lancamentos_grupo(
    conn, periodo: date, tipo: str, empresas_codigos: list[str], status: str = "ATIVO",
    granularidade: Optional[str] = None,
) -> list[dict]:
    """
    Lancamentos "achatados" (1 linha por empresa+grupo+conta) de varias
    empresas num MESMO periodo+tipo -- usado na Visao Grupo pra pivotar
    (grupo, conta) x empresa_codigo em pandas. Ordena por grupo, conta
    (mesma convencao de listar_lancamentos).

    Premissa testada ao vivo antes de usar isto num pivot por igualdade
    exata de (grupo, conta) -- ver Revisao/Correcao, SMG x Construtora,
    06/2026: quando 2 empresas tem a MESMA conta, o texto bate exatamente
    (mesma grafia/maiusculas), entao GROUP BY/pivot por igualdade exata e'
    seguro. Contas que so existem em 1 empresa ficam com 0 nas outras --
    normal (cada empresa tem seu proprio plano de contas), igual na
    planilha "BALANCO GRUPO"/"DRE GRUPO".

    granularidade (Fase 3, 29/09/2026): filtro OPCIONAL, mesma regra de
    listar_lancamentos -- None nao filtra (compatibilidade); passe
    explicitamente quando o periodo_fim pode ter 2 granularidades ATIVAS
    ao mesmo tempo e o consolidado precisa de UMA so' (ver
    dados_relatorio_comentado._consolidar_periodo).
    """
    sql = """
        SELECT empresa_codigo, grupo, conta, valor
        FROM egc.lancamentos
        WHERE periodo = %s AND tipo = %s AND status = %s AND empresa_codigo = ANY(%s)
    """
    params = [periodo, tipo, status, empresas_codigos]
    if granularidade is not None:
        sql += " AND COALESCE(granularidade, '') = %s"
        params.append(granularidade)
    sql += " ORDER BY grupo, conta"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─────────────────────────────────────────────
#  PROJECAO (BP/DRE) — ajustes manuais + materializacao
# ─────────────────────────────────────────────

def listar_historico_grupo(
    conn, empresa_codigo: str, tipo: str, status: str = "ATIVO", granularidade: Optional[str] = None,
) -> list[dict]:
    """
    TODO o historico (todos os periodos, nao so 1) de 1 empresa+tipo,
    achatado (1 linha por periodo+grupo+conta) -- usado no Dashboard de
    Projecao pra montar a serie temporal de cada conta (agrupado
    client-side por (grupo,conta) e passado pra projecao.gerar_baseline)
    e nos indicadores contabeis da Inicio (indicadores.calcular_indicadores).
    Ordena por periodo, grupo, conta.

    granularidade (Fase 3, 29/09/2026): filtro OPCIONAL, None por padrao
    (nao filtra -- comportamento de sempre). LIMITACAO CONHECIDA ainda
    NAO resolvida nesta fase: se um periodo_fim tiver 2 granularidades
    ATIVAS ao mesmo tempo (ex. trimestral e semestral, ambos 30/06/2026)
    e quem chama nao passar granularidade, indicadores.calcular_indicadores
    (groupby periodo+conta) SOMA as 2 no mesmo ponto da serie -- indicador
    errado sem aviso -- RESOLVIDO em 29/09/2026 (Fase 3.1, "alinha todo o
    app com esse escopo... os kpis com essa funcao tb"): a linha ganhou o
    campo `granularidade` a mais (mesma ideia de
    listar_lancamentos_grupo_periodos), e indicadores.calcular_indicadores
    agora resolve a ambiguidade (prioridade: anual > semestral >
    trimestral > mensal > outra > nao declarada) antes de agrupar, em vez
    de somar os 2 documentos silenciosamente.
    """
    sql = """
        SELECT periodo, grupo, conta, valor, COALESCE(granularidade, '') AS granularidade
        FROM egc.lancamentos
        WHERE empresa_codigo = %s AND tipo = %s AND status = %s
    """
    params = [empresa_codigo, tipo, status]
    if granularidade is not None:
        sql += " AND COALESCE(granularidade, '') = %s"
        params.append(granularidade)
    sql += " ORDER BY periodo, grupo, conta"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def salvar_ajuste_projecao(
    conn,
    empresa_codigo: str,
    tipo: str,
    periodo: date,
    grupo: str,
    conta: str,
    valor_ajuste: float,
    descricao: str,
    usuario: Optional[str] = None,
) -> int:
    """Grava 1 ajuste manual (equivalente ao 'contrato X fechando em <periodo>' do Rafael)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO egc.projecoes_ajustes
                (empresa_codigo, tipo, periodo, grupo, conta, valor_ajuste, descricao, usuario)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (empresa_codigo, tipo, periodo, grupo, conta, valor_ajuste, descricao, usuario),
        )
        return cur.fetchone()[0]


def listar_ajustes_projecao(conn, empresa_codigo: str, tipo: str, status: str = "ATIVO") -> list[dict]:
    """Todos os ajustes (ATIVOs por padrao) de 1 empresa+tipo, qualquer periodo futuro."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, periodo, grupo, conta, valor_ajuste, descricao, usuario, criado_em
            FROM egc.projecoes_ajustes
            WHERE empresa_codigo = %s AND tipo = %s AND status = %s
            ORDER BY periodo, grupo, conta
            """,
            (empresa_codigo, tipo, status),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def inativar_ajuste_projecao(conn, ajuste_id: int) -> int:
    """Retira um ajuste manual sem apagar (mesma filosofia de arquivar_periodo -- nunca DELETE)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE egc.projecoes_ajustes
            SET status = 'INATIVO', atualizado_em = now()
            WHERE id = %s AND status = 'ATIVO'
            """,
            (ajuste_id,),
        )
        return cur.rowcount


def gravar_projecoes(conn, linhas: list[dict]) -> int:
    """
    Upsert em lote das projecoes calculadas (saida de projecao.aplicar_ajustes,
    com empresa_codigo/tipo/grupo/conta adicionados por quem chama). Cada
    dict precisa de: empresa_codigo, tipo, periodo, grupo, conta, valor_base,
    valor_ajuste, valor_projetado, metodo, periodos_historico. Materializa
    no banco (nao so na tela) pra ficar disponivel a qualquer consumidor
    externo (ex. futura integracao TIA.go) sem rodar o modelo de novo.
    """
    if not linhas:
        return 0
    registros = [
        (
            l["empresa_codigo"], l["tipo"], l["periodo"], l["grupo"], l["conta"],
            l["valor_base"], l["valor_ajuste"], l["valor_projetado"],
            l["metodo"], l["periodos_historico"],
        )
        for l in linhas
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO egc.projecoes
                (empresa_codigo, tipo, periodo, grupo, conta, valor_base, valor_ajuste,
                 valor_projetado, metodo, periodos_historico)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (empresa_codigo, tipo, periodo, grupo, conta) DO UPDATE SET
                valor_base = EXCLUDED.valor_base,
                valor_ajuste = EXCLUDED.valor_ajuste,
                valor_projetado = EXCLUDED.valor_projetado,
                metodo = EXCLUDED.metodo,
                periodos_historico = EXCLUDED.periodos_historico,
                gerado_em = now()
            """,
            registros,
        )
        return cur.rowcount


def listar_projecoes(conn, empresa_codigo: str, tipo: str) -> list[dict]:
    """Ultima projecao materializada (ja gravada) de 1 empresa+tipo, sem recalcular."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT periodo, grupo, conta, valor_base, valor_ajuste, valor_projetado,
                   metodo, periodos_historico, gerado_em
            FROM egc.projecoes
            WHERE empresa_codigo = %s AND tipo = %s
            ORDER BY periodo, grupo, conta
            """,
            (empresa_codigo, tipo),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─────────────────────────────────────────────
#  EVENTOS DO SISTEMA (log completo — task #16)
# ─────────────────────────────────────────────

def registrar_evento(
    conn,
    origem: str,
    nivel: str,
    mensagem: str,
    empresa_codigo: Optional[str] = None,
    periodo: Optional[date] = None,
    usuario: Optional[str] = None,
    detalhe: Optional[str] = None,
) -> None:
    """
    Grava 1 linha em egc.eventos_sistema (pedido do Rafael 22/09/2026:
    "deixa pronto pra ter logs e msm sistematica, de forma q se der erro
    conseguimos arrumar facil via log"). `origem` e' string livre (nome
    da pagina/fluxo -- ex. "revisao_correcao", "arquivar_recuperar",
    "chat", "inicio") -- fluxos novos (ex. geracao de relatorio, quando
    existir no web) so' passam uma origem nova, sem migracao de schema.
    `nivel`: 'INFO' | 'AVISO' | 'ERRO'.

    Quem chama SEMPRE envolve esta funcao no proprio try/except da tela
    (ver app.py/telas/*.py) -- uma falha ao GRAVAR o log (ex. banco fora
    do ar) nunca pode ser a causa de uma tela quebrar; se acontecer, o
    erro original mostrado ao usuario e' o que importa, o log e'
    melhor-esforco.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO egc.eventos_sistema
                (origem, nivel, mensagem, detalhe, empresa_codigo, periodo, usuario)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (origem, nivel, mensagem, detalhe, empresa_codigo, periodo, usuario),
        )


def listar_eventos_recentes(
    conn, limite: int = 100, nivel: Optional[str] = None, origem: Optional[str] = None,
) -> list[dict]:
    """
    Historico de egc.eventos_sistema, mais recente primeiro -- pra
    conferencia manual via SQL Editor do Supabase por enquanto (sem UI
    dedicada nesta leva, ver task #15/#16 no vault). `nivel`/`origem`
    filtram opcionalmente (ex. so' 'ERRO', ou so' 'revisao_correcao').
    """
    filtros = []
    params: list = []
    if nivel:
        filtros.append("nivel = %s")
        params.append(nivel)
    if origem:
        filtros.append("origem = %s")
        params.append(origem)
    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    params.append(limite)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, origem, nivel, mensagem, detalhe, empresa_codigo, periodo, usuario, criado_em
            FROM egc.eventos_sistema
            {where}
            ORDER BY criado_em DESC
            LIMIT %s
            """,
            params,
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def buscar_lancamentos_manuais(
    conn, empresas_codigos: Optional[list[str]] = None, limite: int = 50,
) -> list[dict]:
    """
    Lista lancamentos com origem 'MANUAL ...' (corrigidos a mao ao menos 1x),
    de TODAS as empresas/periodos/tipos por padrao, mais recente primeiro por
    atualizado_em -- criada pra resolver "editei uma conta ontem e nao lembro
    qual" (Rafael, 23/09/2026): em vez de abrir empresa por empresa /
    periodo por periodo na tela de Revisao/Correcao pra procurar visualmente,
    esta busca cruza tudo de uma vez.  `empresas_codigos` filtra opcionalmente
    (senao busca nas 6 empresas juntas).
    """
    filtros = ["origem LIKE 'MANUAL%%'"]
    params: list = []
    if empresas_codigos:
        filtros.append("empresa_codigo = ANY(%s)")
        params.append(empresas_codigos)
    where = f"WHERE {' AND '.join(filtros)}"
    params.append(limite)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, empresa_codigo, tipo, periodo, grupo, conta, valor, pdf_original,
                   origem, usuario, atualizado_em
            FROM egc.lancamentos
            {where}
            ORDER BY atualizado_em DESC
            LIMIT %s
            """,
            params,
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─────────────────────────────────────────────
#  RELATORIOS GERADOS (Fase 2, 28/09/2026 — historico de geracao de PDF)
# ─────────────────────────────────────────────

def registrar_relatorio_gerado(
    conn, empresas_codigos: list[str], periodos: list[date], arquivo: str, usuario: Optional[str] = None,
) -> int:
    """
    Loga 1 geracao de relatorio comentado/comparativo em
    egc.relatorios_gerados (tabela ja existe desde o schema inicial —
    'substitui LOG_PDF_GERADO' do VBA, que era exatamente o log que
    NUNCA registrava nada de verdade no V3/Excel, bug conhecido corrigido
    ali; aqui e' a 1a vez que algo escreve nesta tabela). Nao levanta
    excecao pra quem chama tratar como melhor-esforco, igual
    registrar_evento -- a tela ja mostrou o PDF gerado, o log e' so'
    historico.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO egc.relatorios_gerados (empresas, periodos, arquivo, usuario)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (empresas_codigos, periodos, arquivo, usuario),
        )
        return cur.fetchone()[0]


def listar_relatorios_gerados(conn, limite: int = 20) -> list[dict]:
    """Ultimas geracoes de relatorio, mais recente primeiro -- historico
    pra tela (nao guarda o PDF em si, so' o registro de quando/quem/quais
    empresas+periodos)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, empresas, periodos, arquivo, usuario, gerado_em
            FROM egc.relatorios_gerados
            ORDER BY gerado_em DESC
            LIMIT %s
            """,
            (limite,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─────────────────────────────────────────────
#  CONTATOS DO RELATORIO (administrador/contador salvos — bloco 13)
# ─────────────────────────────────────────────

def listar_contatos_relatorio(conn) -> list[dict]:
    """
    TODOS os contatos salvos (administrador, contador, ou qualquer outro
    tipo historico), pra popular o dropdown da tela 8_Relatorio_Comentado.py
    -- reaproveita entre as 6 empresas, sem empresa_codigo de proposito.

    FIX_20260929b (Rafael, depois de testar ao vivo: "ficou separado, não
    seria mais fácil manter 1 lista só pra ambos... a lista única poderia
    trocar facilmente os lados"): ANTES filtrava por `tipo`
    ('ADMINISTRADOR' x 'CONTADOR'), gerando 2 listas -- um contato salvo do
    lado Administrador nunca aparecia no dropdown do Contador, mesmo sendo
    a mesma pessoa que so' mudou de lado num relatorio novo. Agora e' 1
    lista so', compartilhada pelos 2 seletores -- qualquer contato salvo
    pode ser escolhido pra qualquer lado, sem recadastrar. A coluna `tipo`
    continua gravada (registra de qual lado o contato foi cadastrado da
    1a vez), so' deixou de ser usada pra FILTRAR a listagem.

    Lista vazia (nunca erro) se a migracao do bloco 13 ainda nao rodou --
    mesmo padrao de fallback ja usado em listar_despesas_admin_itens, a
    tela degrada pro comportamento antigo (inputs em branco) sem quebrar.
    """
    with conn.cursor() as cur:
        try:
            cur.execute(
                """
                SELECT id, tipo, nome, cargo, email
                FROM egc.contatos_relatorio
                ORDER BY nome
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except psycopg2.errors.UndefinedTable:
            conn.rollback()
            return []


def salvar_contato_relatorio(
    conn, tipo: str, nome: str, cargo: str, email: Optional[str] = None,
    contato_id: Optional[int] = None, usuario: Optional[str] = None,
) -> Optional[int]:
    """
    Cria (contato_id=None) ou atualiza (contato_id != None) um contato
    salvo. Retorna o id, ou None se a tabela ainda nao existir (migracao
    do bloco 13 pendente) -- a tela avisa e segue com o campo digitado
    na mao, nao trava a geracao do relatorio por causa disso.
    """
    with conn.cursor() as cur:
        try:
            if contato_id is not None:
                cur.execute(
                    """
                    UPDATE egc.contatos_relatorio
                    SET nome = %s, cargo = %s, email = %s, usuario = %s, atualizado_em = now()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (nome, cargo, email, usuario, contato_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO egc.contatos_relatorio (tipo, nome, cargo, email, usuario)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (tipo, nome, cargo, email, usuario),
                )
            row = cur.fetchone()
            return row[0] if row else None
        except psycopg2.errors.UndefinedTable:
            conn.rollback()
            return None


# ─────────────────────────────────────────────
#  CONTEXTO FISCAL (conhecimento de referencia — Reforma Tributaria)
# ─────────────────────────────────────────────

def listar_contexto_fiscal(conn, tema: Optional[str] = None) -> list[dict]:
    """
    Blocos de conhecimento curado (egc.contexto_fiscal), so' os ativos
    (ativo=true) -- pedido do Rafael 22/09/2026 sobre a Reforma
    Tributaria: "queria imputar de alguma forma, pelo menos p saber ou
    ter ciencia dessas informacoes... futuramente o chat conseguira
    trabalhar com base nos dados existentes e cruzamento dessas novas
    informacoes". Usado por chat_egc.montar_system_prompt pra injetar
    esse conteudo no system prompt, claramente rotulado como referencia
    (nao dado de BP/DRE) -- ver REGRA CRITICA la'. `tema` filtra
    opcionalmente (hoje so existe 'reforma_tributaria').
    """
    with conn.cursor() as cur:
        if tema:
            cur.execute(
                """
                SELECT chave, tema, titulo, conteudo, fonte, atualizado_em
                FROM egc.contexto_fiscal
                WHERE ativo = true AND tema = %s
                ORDER BY chave
                """,
                (tema,),
            )
        else:
            cur.execute(
                """
                SELECT chave, tema, titulo, conteudo, fonte, atualizado_em
                FROM egc.contexto_fiscal
                WHERE ativo = true
                ORDER BY tema, chave
                """
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
