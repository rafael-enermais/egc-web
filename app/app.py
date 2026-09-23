# -*- coding: utf-8 -*-
"""
EGC — Gestao Contabil EnerMais (v0.1 web)
Ponto de entrada Streamlit.

Fix (21/09/2026, v2): antes disso, as paginas em app/pages/ eram
descobertas automaticamente pelo Streamlit (convencao de pasta) e
apareciam no menu lateral MESMO pra quem nao tinha logado ainda. A 1a
tentativa (trocar pra st.navigation()/st.Page() mantendo os arquivos
dentro de uma pasta chamada "pages/") NAO resolveu: o Streamlit continua
auto-descobrindo e desenhando o menu a partir do NOME da pasta "pages/"
em si (client-side, antes do script rodar), independente do que o codigo
Python faz. Por isso os arquivos foram movidos pra app/telas/ (fora de
qualquer pasta chamada "pages") — so' assim a auto-descoberta para' de
vazar o menu, e st.navigation()/st.Page() passam a ser a UNICA fonte da
lista de paginas, registrada so' depois que require_login() confirma a
sessao. Validado direto no app publicado (nao so' no AppTest local, que
nao reproduz esse comportamento de auto-descoberta do servidor).
"""
import streamlit as st
import pandas as pd

from auth import require_login
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS
import db
import indicadores
import visao_grupo

st.set_page_config(page_title="EGC — EnerMais", page_icon="📊", layout="wide")


def _registrar_evento_seguro(conn, origem, nivel, mensagem, usuario=None, detalhe=None,
                              empresa_codigo=None, periodo=None):
    """
    Wrapper de db.registrar_evento (log completo, task #16) que NUNCA
    levanta excecao pra quem chama -- gravar o log e' melhor-esforco; se
    o proprio banco estiver fora do ar (a causa mais provavel de um erro
    aqui), o aviso original que o usuario ja viu (st.warning/st.error) e'
    o que importa, a tela nao pode quebrar por causa do log. `conn` pode
    ser None (ex. get_conn() falhou antes de existir) -- nesse caso so'
    nao grava, sem erro.
    """
    if conn is None:
        return
    try:
        db.registrar_evento(conn, origem, nivel, mensagem, empresa_codigo=empresa_codigo,
                             periodo=periodo, usuario=usuario, detalhe=detalhe)
    except Exception:
        pass


# Bloqueia aqui (st.stop() dentro de require_login) se nao autenticado.
# st.navigation() so' e' chamado DEPOIS desta linha — por isso a lista de
# paginas nunca existe pra quem nao passou do login. O e-mail retornado e'
# reaproveitado dentro de pagina_inicio() (nao chama require_login() de
# novo la' — isso duplicava o "Logado como" na sidebar).
usuario_logado = require_login()


def pagina_inicio():
    usuario = usuario_logado

    st.title("EGC — Gestão Contábil EnerMais")
    st.caption("Importação de PDF, revisão/correção, histórico e relatórios — schema `egc` no Supabase.")

    sidebar_contexto(usuario)  # so' rodape -- ver nota em conexao.sidebar_contexto

    st.markdown(
        "Use o menu à esquerda para **Importar PDF**, **Revisão/Correção** ou "
        "**Arquivar/Recuperar**."
    )

    # Seletor de empresa PROPRIO desta pagina (21/09/2026: nao depende mais
    # de nenhum estado compartilhado com outras paginas -- ver nota em
    # conexao.sidebar_contexto sobre por que o dropdown antigo foi tirado
    # da sidebar).
    nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
    idx = st.selectbox(
        "Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes_emp[i], key="inicio_empresa_sel",
    )
    cod_empresa, nome_empresa, _cnpj_empresa = EMPRESAS_FIXAS[idx]

    conn = None
    try:
        conn = get_conn()
        periodos_ativos = db.listar_periodos(conn, cod_empresa, status="ATIVO")
        periodos_inativos = db.listar_periodos(conn, cod_empresa, status="INATIVO")
        c1, c2 = st.columns(2)
        c1.metric(f"Períodos ativos — {nome_empresa}", len(periodos_ativos))
        c2.metric("Períodos arquivados", len(periodos_inativos))
        if periodos_ativos:
            st.caption("Últimos períodos ativos: " + ", ".join(p.strftime("%m/%Y") for p in periodos_ativos[:6]))
    except Exception as exc:
        st.warning(f"Não foi possível consultar o banco ainda: {exc}")
        _registrar_evento_seguro(conn, "inicio", "ERRO", "Falha ao consultar períodos",
                                  usuario=usuario, detalhe=str(exc), empresa_codigo=cod_empresa)
        return

    # ─────────────── Indicadores contábeis (Fase 4, 22/09/2026) ───────────
    # Pedido do Rafael: "poderiamos fazer todos os calculos que forem
    # uteis... tudo oq ajudar e saber e interessante" -- zero import novo,
    # calculado em cima do BP/DRE que ja esta no banco (mesma fonte que
    # Visao Grupo/Revisao). Reaproveita db.listar_historico_grupo (ja
    # existia pro motor de projecao, removido desta versao -- ver
    # app/_arquivados/).
    st.divider()
    st.subheader("Indicadores contábeis")
    try:
        hist_bp = db.listar_historico_grupo(conn, cod_empresa, "BP", status="ATIVO")
        hist_dre = db.listar_historico_grupo(conn, cod_empresa, "DRE", status="ATIVO")
        tabela_ind = indicadores.calcular_indicadores(hist_bp, hist_dre)
    except Exception as exc:
        st.warning(f"Não foi possível calcular os indicadores: {exc}")
        _registrar_evento_seguro(conn, "inicio", "ERRO", "Falha ao calcular indicadores",
                                  usuario=usuario, detalhe=str(exc), empresa_codigo=cod_empresa)
        return

    if tabela_ind.empty:
        st.caption("Sem BP/DRE suficiente pra calcular indicadores ainda.")
        return

    periodo_ind = tabela_ind.index.max()
    periodo_ind_anterior = None
    anteriores = tabela_ind.index[tabela_ind.index < periodo_ind]
    if len(anteriores) > 0:
        periodo_ind_anterior = anteriores.max()

    def _fmt(col, formato):
        valor = tabela_ind.loc[periodo_ind, col]
        delta = None
        if periodo_ind_anterior is not None and pd.notna(tabela_ind.loc[periodo_ind_anterior, col]) and pd.notna(valor):
            delta = valor - tabela_ind.loc[periodo_ind_anterior, col]
        if pd.isna(valor):
            return "—", None
        if formato == "pct":
            texto = f"{valor:.1%}"
            delta_txt = f"{delta:+.1%}" if delta is not None else None
        elif formato == "x":
            texto = f"{valor:.2f}x"
            delta_txt = f"{delta:+.2f}x" if delta is not None else None
        else:  # "R$"
            texto = f"R$ {valor:,.2f}"
            delta_txt = f"R$ {delta:+,.2f}" if delta is not None else None
        return texto, delta_txt

    st.caption(f"Período de referência: {periodo_ind.strftime('%m/%Y')}"
               + (f" · comparado a {periodo_ind_anterior.strftime('%m/%Y')}" if periodo_ind_anterior is not None else " · sem período anterior pra comparar ainda"))

    k1, k2, k3 = st.columns(3)
    for col, label, fmt, container in [
        ("Liquidez Corrente", "Liquidez Corrente", "x", k1),
        ("Capital de Giro", "Capital de Giro", "R$", k2),
        ("Endividamento Geral", "Endividamento Geral", "pct", k3),
    ]:
        texto, delta_txt = _fmt(col, fmt)
        container.metric(label, texto, delta=delta_txt)

    k4, k5, k6, k7 = st.columns(4)
    for col, label, fmt, container in [
        ("Margem Bruta", "Margem Bruta", "pct", k4),
        ("Margem Líquida", "Margem Líquida", "pct", k5),
        ("ROA", "ROA (Retorno s/ Ativo)", "pct", k6),
        ("ROE", "ROE (Retorno s/ PL)", "pct", k7),
    ]:
        texto, delta_txt = _fmt(col, fmt)
        container.metric(label, texto, delta=delta_txt)

    with st.expander("Histórico completo dos indicadores"):
        st.dataframe(
            tabela_ind.rename(index=lambda d: d.strftime("%m/%Y")),
            column_config={
                "Liquidez Corrente": st.column_config.NumberColumn(format="%.2fx"),
                "Capital de Giro": st.column_config.NumberColumn(format="R$ %.2f"),
                "Endividamento Geral": st.column_config.NumberColumn(format="percent"),
                "Margem Bruta": st.column_config.NumberColumn(format="percent"),
                "Margem Líquida": st.column_config.NumberColumn(format="percent"),
                "ROA": st.column_config.NumberColumn(format="percent"),
                "ROE": st.column_config.NumberColumn(format="percent"),
            },
            use_container_width=True,
        )
        st.caption(
            "Liquidez seca não entra: as 6 empresas do grupo (EPC/energia) não têm "
            "conta de Estoques no BP extraído hoje — ficaria idêntica à Liquidez "
            "Corrente, sem valor informativo. Fórmulas: Liquidez Corrente = Ativo "
            "Circulante ÷ Passivo Circulante · Capital de Giro = Ativo Circulante − "
            "Passivo Circulante · Endividamento Geral = (Passivo Circulante + Passivo "
            "Não Circulante) ÷ Ativo Total · Margem Bruta = Lucro Bruto ÷ Receita "
            "Líquida · Margem Líquida = Lucro Líquido ÷ Receita Líquida · ROA = Lucro "
            "Líquido ÷ Ativo Total · ROE = Lucro Líquido ÷ Patrimônio Líquido."
        )

    # ─────────── KPI consolidado do grupo (23/09/2026) ───────────
    # Retomando item combinado com o Rafael em 22/09 ("Salva o progresso,
    # amanhã terminaremos com esses pontos"): mesmos indicadores contábeis
    # acima, agora somando as 6 empresas do grupo. Zero mudança de lógica
    # em indicadores.calcular_indicadores() -- só passa lançamentos de
    # várias empresas de uma vez (o groupby(periodo,conta) já soma tudo
    # junto). Intercompany já confirmado sem transação material entre as
    # 6 empresas (perguntado direto ao Rafael antes de recomendar isto) --
    # soma direta é segura, sem eliminação.
    st.divider()
    st.subheader("KPI consolidado do grupo")
    cods_todos = [cod for cod, _nome, _cnpj in EMPRESAS_FIXAS]
    periodos_grupo, lancs_bp_grupo, lancs_dre_grupo = [], [], []
    try:
        periodos_grupo = db.listar_periodos_grupo(conn, cods_todos, status="ATIVO")
        lancs_bp_grupo = db.listar_lancamentos_grupo_periodos(conn, periodos_grupo, "BP", cods_todos, status="ATIVO")
        lancs_dre_grupo = db.listar_lancamentos_grupo_periodos(conn, periodos_grupo, "DRE", cods_todos, status="ATIVO")
        tabela_ind_grupo = indicadores.calcular_indicadores(lancs_bp_grupo, lancs_dre_grupo)
    except Exception as exc:
        st.warning(f"Não foi possível calcular os indicadores do grupo: {exc}")
        _registrar_evento_seguro(conn, "inicio", "ERRO", "Falha ao calcular indicadores do grupo",
                                  usuario=usuario, detalhe=str(exc))
        tabela_ind_grupo = pd.DataFrame()

    if tabela_ind_grupo.empty:
        st.caption("Sem BP/DRE suficiente no grupo pra calcular indicadores consolidados ainda.")
    else:
        periodo_grupo = tabela_ind_grupo.index.max()
        periodo_grupo_anterior = None
        anteriores_grupo = tabela_ind_grupo.index[tabela_ind_grupo.index < periodo_grupo]
        if len(anteriores_grupo) > 0:
            periodo_grupo_anterior = anteriores_grupo.max()

        def _fmt_grupo(col, formato):
            valor = tabela_ind_grupo.loc[periodo_grupo, col]
            delta = None
            if periodo_grupo_anterior is not None and pd.notna(tabela_ind_grupo.loc[periodo_grupo_anterior, col]) and pd.notna(valor):
                delta = valor - tabela_ind_grupo.loc[periodo_grupo_anterior, col]
            if pd.isna(valor):
                return "—", None
            if formato == "pct":
                return f"{valor:.1%}", (f"{delta:+.1%}" if delta is not None else None)
            if formato == "x":
                return f"{valor:.2f}x", (f"{delta:+.2f}x" if delta is not None else None)
            return f"R$ {valor:,.2f}", (f"R$ {delta:+,.2f}" if delta is not None else None)

        st.caption(
            f"Consolidado das 6 empresas · Período de referência: {periodo_grupo.strftime('%m/%Y')}"
            + (f" · comparado a {periodo_grupo_anterior.strftime('%m/%Y')}" if periodo_grupo_anterior is not None
               else " · sem período anterior pra comparar ainda")
        )

        gk1, gk2, gk3 = st.columns(3)
        for col, label, fmt, container in [
            ("Liquidez Corrente", "Liquidez Corrente", "x", gk1),
            ("Capital de Giro", "Capital de Giro", "R$", gk2),
            ("Endividamento Geral", "Endividamento Geral", "pct", gk3),
        ]:
            texto, delta_txt = _fmt_grupo(col, fmt)
            container.metric(label, texto, delta=delta_txt)

        gk4, gk5, gk6, gk7 = st.columns(4)
        for col, label, fmt, container in [
            ("Margem Bruta", "Margem Bruta", "pct", gk4),
            ("Margem Líquida", "Margem Líquida", "pct", gk5),
            ("ROA", "ROA (Retorno s/ Ativo)", "pct", gk6),
            ("ROE", "ROE (Retorno s/ PL)", "pct", gk7),
        ]:
            texto, delta_txt = _fmt_grupo(col, fmt)
            container.metric(label, texto, delta=delta_txt)

    # ─────────── Painel de pendências (23/09/2026) ───────────
    # 2ª metade do mesmo item retomado acima: "quais pendências ainda
    # faltam além do gerador de relatórios?" -- completude de dados por
    # empresa × período. Zero query nova: reaproveita
    # db.listar_periodos_grupo/listar_lancamentos_grupo_periodos (as
    # mesmas já chamadas na seção acima e usadas por Visão Grupo).
    st.divider()
    st.subheader("Painel de pendências")
    try:
        completude = visao_grupo.calcular_completude_grupo(
            periodos_grupo, lancs_bp_grupo, lancs_dre_grupo, EMPRESAS_FIXAS,
        )
    except Exception as exc:
        st.warning(f"Não foi possível montar o painel de pendências: {exc}")
        _registrar_evento_seguro(conn, "inicio", "ERRO", "Falha ao montar painel de pendências",
                                  usuario=usuario, detalhe=str(exc))
        completude = pd.DataFrame()

    if completude.empty:
        st.caption("Sem período nenhum no grupo ainda pra avaliar pendências.")
    else:
        completude_fmt = completude.assign(Período=completude["Período"].apply(lambda p: p.strftime("%m/%Y")))
        pendentes = completude_fmt[completude_fmt["Status"] != "✅ Completo"].sort_values(
            ["Período", "Empresa"], ascending=[False, True]
        )
        if pendentes.empty:
            st.success("Todas as empresas com BP e DRE completos em todos os períodos ativos do grupo.")
        else:
            st.caption(f"{len(pendentes)} combinação(ões) empresa×período com dado faltando (BP e/ou DRE).")
            st.dataframe(pendentes[["Período", "Empresa", "Status"]], hide_index=True, use_container_width=True)
        with st.expander("Ver matriz completa (todas as empresas × períodos)"):
            st.dataframe(completude_fmt[["Período", "Empresa", "Status"]], hide_index=True, use_container_width=True)


paginas = [
    st.Page(pagina_inicio, title="Início", icon="📊", default=True, url_path="inicio"),
    st.Page("telas/1_Importar_PDF.py", title="Importar PDF", icon="📥"),
    st.Page("telas/2_Revisao_Correcao.py", title="Revisão/Correção", icon="✏️"),
    st.Page("telas/3_Arquivar_Recuperar.py", title="Arquivar/Recuperar", icon="🗄️"),
    st.Page("telas/4_Visao_Grupo.py", title="Visão Grupo", icon="🏢"),
    # Dashboard de Projeção removido do menu por pedido do Rafael
    # (22/09/2026): numeros ficavam irreais em horizonte longo com pouco
    # historico real (tendencia linear sem teto). Codigo e tabelas
    # (egc.projecoes/projecoes_ajustes) preservados, nao apagados -- ver
    # app/_arquivados/5_Dashboard_Projecao.py pra reativar se decidir
    # retomar com mais historico acumulado.
    st.Page("telas/6_Assistente.py", title="Assistente", icon="🤖"),
]
pg = st.navigation(paginas)
pg.run()
