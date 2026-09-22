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

from datetime import date, datetime
from typing import Optional

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
    """Períodos distintos de uma empresa com o status pedido (ATIVO ou INATIVO)."""
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


# ─────────────────────────────────────────────
#  IMPORTACAO
# ─────────────────────────────────────────────

def inativar_periodo_existente(conn, empresa_codigo: str, periodo: date, tipo: str) -> int:
    """
    Reimportacao do MESMO empresa+periodo+tipo: inativa (nunca apaga) as
    linhas ATIVAS anteriores antes de gravar as novas — equivalente ao
    'FIX CRITICO 20/07/2026' do VBA (remove linhas pre-existentes antes
    de regravar), so que aqui vira soft-inactivate em vez de hard-delete,
    seguindo a mesma filosofia que levou a remover o 'Desfazer' do painel
    (nunca apagar historico de forma irreversivel).
    Retorna quantas linhas foram inativadas.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE egc.lancamentos
            SET status = 'INATIVO', atualizado_em = now()
            WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s AND status = 'ATIVO'
            """,
            (empresa_codigo, periodo, tipo),
        )
        return cur.rowcount


def inserir_lancamentos(
    conn,
    empresa_codigo: str,
    tipo: str,
    periodo: date,
    rows: list,
    arquivo_pdf: str,
    usuario: Optional[str] = None,
) -> int:
    """
    Insere o lote extraido do PDF. `rows` no formato de saida do parser
    (parser_egc.build_output): BP = [grupo, conta, valor_br, origem];
    DRE = [conta, valor_br, grupo, origem]. Valor chega como string
    BR-formatada (ex. "1.094.484,54") — convertida pra numeric aqui.
    """
    from parser_egc import br_to_float

    registros = []
    for r in rows:
        if tipo == "BP":
            grupo, conta, valor_br, origem = r
        else:  # DRE — ordem de colunas diferente no parser
            conta, valor_br, grupo, origem = r
        valor = br_to_float(valor_br)
        registros.append((empresa_codigo, tipo, periodo, grupo, conta, valor, origem, arquivo_pdf, usuario))

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO egc.lancamentos
                (empresa_codigo, tipo, periodo, grupo, conta, valor, origem, arquivo_pdf, usuario)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            registros,
        )
        return cur.rowcount


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
            SELECT empresa_codigo, periodo, criado_em, usuario, tipo, nivel, mensagem
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

def listar_lancamentos(conn, empresa_codigo: str, periodo: date, tipo: str, status: str = "ATIVO") -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, grupo, conta, valor, origem, pdf_original, arquivo_pdf, atualizado_em
            FROM egc.lancamentos
            WHERE empresa_codigo = %s AND periodo = %s AND tipo = %s AND status = %s
            ORDER BY grupo, conta
            """,
            (empresa_codigo, periodo, tipo, status),
        )
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
            cur.execute(
                """
                INSERT INTO egc.lancamentos
                    (empresa_codigo, tipo, periodo, grupo, conta, valor, origem, usuario)
                VALUES (%s, %s, %s, 'AJUSTE MANUAL', %s, %s, %s, %s)
                RETURNING id
                """,
                (empresa_codigo, tipo, periodo, conta, novo_valor, origem, usuario),
            )
        row = cur.fetchone()
        return row[0] if row else None


# ─────────────────────────────────────────────
#  ARQUIVAR / RECUPERAR (equivalente a ArquivarImportacao/RecuperarImportacao)
# ─────────────────────────────────────────────

def arquivar_periodo(conn, empresa_codigo: str, periodo: date) -> int:
    """Inativa TODAS as linhas (BP+DRE) do periodo — nunca apaga."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE egc.lancamentos
            SET status = 'INATIVO', atualizado_em = now()
            WHERE empresa_codigo = %s AND periodo = %s AND status = 'ATIVO'
            """,
            (empresa_codigo, periodo),
        )
        return cur.rowcount


def recuperar_periodo(conn, empresa_codigo: str, periodo: date) -> int:
    """Reativa um periodo previamente arquivado."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE egc.lancamentos
            SET status = 'ATIVO', atualizado_em = now()
            WHERE empresa_codigo = %s AND periodo = %s AND status = 'INATIVO'
            """,
            (empresa_codigo, periodo),
        )
        return cur.rowcount


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


def listar_lancamentos_grupo(
    conn, periodo: date, tipo: str, empresas_codigos: list[str], status: str = "ATIVO"
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
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT empresa_codigo, grupo, conta, valor
            FROM egc.lancamentos
            WHERE periodo = %s AND tipo = %s AND status = %s AND empresa_codigo = ANY(%s)
            ORDER BY grupo, conta
            """,
            (periodo, tipo, status, empresas_codigos),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ─────────────────────────────────────────────
#  PROJECAO (BP/DRE) — ajustes manuais + materializacao
# ─────────────────────────────────────────────

def listar_historico_grupo(conn, empresa_codigo: str, tipo: str, status: str = "ATIVO") -> list[dict]:
    """
    TODO o historico (todos os periodos, nao so 1) de 1 empresa+tipo,
    achatado (1 linha por periodo+grupo+conta) -- usado no Dashboard de
    Projecao pra montar a serie temporal de cada conta (agrupado
    client-side por (grupo,conta) e passado pra projecao.gerar_baseline).
    Ordena por periodo, grupo, conta.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT periodo, grupo, conta, valor
            FROM egc.lancamentos
            WHERE empresa_codigo = %s AND tipo = %s AND status = %s
            ORDER BY periodo, grupo, conta
            """,
            (empresa_codigo, tipo, status),
        )
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
