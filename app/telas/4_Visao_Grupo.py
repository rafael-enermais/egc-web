# -*- coding: utf-8 -*-
"""
Tela VISAO GRUPO — BP/DRE consolidado multi-empresa, equivalente as abas
"BALANCO GRUPO"/"DRE GRUPO" da planilha real da contadora (conferida em
21/09/2026, "DEMONSTRATIVOS - POR EMPRESA E CONSOLIDADO - 2o Trimestre
2026.xlsx"): coluna VALOR CONSOLIDADO = soma de todas as empresas;
ENERMAIS ENERGIA sempre separada; as outras 5 (ENG, CONST, RENOV, SOL,
SMG) somadas juntas em EMPRESAS CONSOLIDADORAS; % = participacao de cada
bloco no consolidado.

Duas visoes (pedido literal do Rafael, 21/09/2026):
  - "Macro"      — so' Energia x Consolidadoras (igual a planilha).
  - "Específica" — as empresas selecionadas abertas 1 a 1, sem agrupar.

Premissa testada ao vivo ANTES de construir isto (Revisao/Correcao, SMG x
Construtora, ambas em 06/2026, unicas 2 com dado ativo nesse periodo no
momento do teste): quando 2 empresas tem a MESMA conta, o texto bate
exatamente (mesma grafia/maiusculas) -- ver db.listar_lancamentos_grupo.
Por isso o pivot abaixo agrupa por igualdade EXATA de (grupo, conta), sem
nenhuma busca por texto/substring (a colisao conhecida do projeto, tipo
"CLIENTES" vs "ADIANTAMENTOS DE CLIENTES", so afeta busca por texto curto
-- nao afeta igualdade exata).

Subtotais (ex. "TOTAL CIRCULANTE ATIVO") NAO sao calculados aqui -- ja
vem como linha propria extraida do PDF (mesma fonte que BP/DRE normal),
entao esta tela so' pivota o que ja existe em egc.lancamentos, sem
reimplementar nenhuma logica de soma/subtotal do parser ou do
REL_14_LUCRO (regra absoluta do projeto).

So' LEITURA -- nao grava nada. Correcao de valor continua sendo feita em
Revisao/Correcao; esta tela so' reflete o que ja esta' ATIVO no banco.

Mudanca "Completo" (22/09/2026, itens #6/#9 do feedback ao vivo do
Rafael, escopo escolhido explicitamente por ele via pergunta de decisao):
  - #9: nao exige mais 2+ empresas (da' pra ver 1 CNPJ sozinho); BP e DRE
    aparecem JUNTOS na mesma tela (radio exclusivo removido); periodo
    virou multiselect (nao so' 1 por vez), escalando de "tudo de todos os
    periodos" ate' "1 periodo de 1 CNPJ" no mesmo controle.
  - #6: nova secao "Resumo do grupo" no topo com KPIs prontos (Total do
    Ativo/Passivo do BP, Receita Liquida/Lucro Bruto/Lucro Liquido do
    DRE) do periodo de detalhe escolhido, com delta vs periodo anterior
    quando disponivel na selecao, + 2 graficos de evolucao (BP e DRE,
    escalas bem diferentes -- misturar num grafico so' seria enganoso)
    quando 2+ periodos estao selecionados.
  - Tabela de detalhe (macro/especifica) continua existindo embaixo,
    agora mostrando BP e DRE empilhados pro "periodo de detalhe"
    escolhido dentro da selecao de periodos.

Fase 3.1 (29/09/2026, "alinha todo o app com esse escopo... revise a
estrutura toda"): seletores de periodo eram date-only (db.listar_periodos_grupo).
O "Resumo do grupo" (KPIs+graficos, via visao_grupo.montar_serie_kpis_grupo)
ja' resolve sozinho quando 2 granularidades estao ativas no mesmo
periodo_fim (criterio de desempate documentado la'), entao continua
recebendo so' as datas. Ja' o "periodo de detalhe" (tabela BP/DRE por
conta, abaixo) PRECISA saber qual documento mostrar quando ha' ambiguidade
-- vira selecao de (periodo, granularidade), com a granularidade no
rotulo so' quando aquele periodo_fim tem mais de 1 documento ativo.

v0.40.0: "Resumo do grupo" ganhou EBITDA, Margem Bruta/EBITDA/Liquida, ROA/ROE,
Capital de Giro, Liquidez Corrente, Endividamento Geral e Alavancagem (via
visao_grupo.montar_indicadores_grupo -> indicadores.calcular_indicadores,
respeitando o seletor "Base do periodo"), mais um grafico de margens; antes
mostrava so' Total do Ativo/Passivo, Receita Liquida, Lucro Bruto e Lucro
Liquido + 2 graficos (BP e DRE).
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import db  # noqa: E402
import visao_grupo  # noqa: E402
import indicadores  # noqa: E402
import formatacao  # noqa: E402
import visao_grupo_graficos as graficos  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}

st.title("🏢 Visão Grupo")

usuario = usuario_atual()
sidebar_contexto(usuario)  # so' rodape -- ver nota em conexao.sidebar_contexto
conn = get_conn()

st.caption(
    "BP/DRE consolidado das empresas do grupo — mesma lógica da planilha "
    "'BALANÇO GRUPO'/'DRE GRUPO'. Só leitura; correções continuam em Revisão/Correção."
)

nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]

# 01/10/2026 (Rafael: "organiza os KPI e monta um Dash completo bonito... leitura
# boa, dinâmica, fluida"): filtros numa linha só no topo; o resto vira dashboard
# (leitura rápida + KPIs de destaque + abas). A logica de dados (base unica de
# periodo+granularidade, match obrigatorio) continua a MESMA.
f_emp, f_base, f_per = st.columns([2.4, 1.1, 2.0])
with f_emp:
    idxs_sel = st.multiselect(
        "Empresa(s) no consolidado", range(len(EMPRESAS_FIXAS)),
        default=list(range(len(EMPRESAS_FIXAS))), format_func=lambda i: nomes_emp[i],
        key="grupo_empresas_sel",
    )
cods_selecionados = [EMPRESAS_FIXAS[i][0] for i in idxs_sel]
if not cods_selecionados:
    st.info("Selecione ao menos 1 empresa.")
    st.stop()

periodos_detalhados = db.listar_periodos_grupo_detalhado(conn, cods_selecionados, status="ATIVO")
if not periodos_detalhados:
    st.info("Nenhum período ativo entre as empresas selecionadas ainda.")
    st.stop()

# Fase 4 (30/09/2026, "o match de periodo+granularidade e' OBRIGATORIO em
# tudo"): "Base do período" (granularidade) explicita. Todos os KPIs,
# graficos e a tabela de detalhe vem de UMA base -- nunca mistura, por
# exemplo, o trimestral de um periodo com o semestral de outro na mesma
# serie. Default = base do periodo mais recente em que TODAS as empresas
# selecionadas tem dado (a mais abrangente se houver mais de uma).
_datas_todas = sorted({d["periodo"] for d in periodos_detalhados})
_lancs_bp_todos = db.listar_lancamentos_grupo_periodos(conn, _datas_todas, "BP", cods_selecionados, status="ATIVO")
_lancs_dre_todos = db.listar_lancamentos_grupo_periodos(conn, _datas_todas, "DRE", cods_selecionados, status="ATIVO")
bases_disponiveis = sorted(
    {d["granularidade"] for d in periodos_detalhados},
    key=lambda g: indicadores._PRIORIDADE_GRANULARIDADE.get(g, -1), reverse=True,
)
base_padrao = indicadores.granularidade_padrao(_lancs_bp_todos, _lancs_dre_todos, cods_selecionados)
if base_padrao not in bases_disponiveis:
    base_padrao = bases_disponiveis[0]
with f_base:
    base_sel = st.selectbox(
        "Base do período", bases_disponiveis, index=bases_disponiveis.index(base_padrao),
        format_func=indicadores.rotulo_granularidade, key="grupo_base_sel",
        help="Abrangência do documento (ex. trimestral x semestral fechando na mesma data). "
             "KPIs, gráficos e tabela abaixo usam só esta base.",
    )

periodos_disponiveis = sorted({d["periodo"] for d in periodos_detalhados if d["granularidade"] == base_sel})
# Trocar a base muda quais periodos existem -- zera a selecao pra "todos da
# nova base" (evita um valor guardado que nao esta nas opcoes novas).
if "grupo_periodos_sel" not in st.session_state or st.session_state.get("_grupo_base_anterior", base_sel) != base_sel:
    st.session_state["grupo_periodos_sel"] = list(periodos_disponiveis)
st.session_state["_grupo_base_anterior"] = base_sel
with f_per:
    periodos_sel = st.multiselect(
        "Período(s) da análise",
        periodos_disponiveis, format_func=lambda d: d.strftime("%m/%Y"),
        key="grupo_periodos_sel",
    )
if not periodos_sel:
    st.info("Selecione ao menos 1 período.")
    st.stop()
periodos_sel = sorted(periodos_sel)

# ─────────────────────── Dashboard do grupo ────────────────────────────────
def _tema_escuro() -> bool:
    """Tema atual do Streamlit (>=1.46); sem a API, assume escuro (o tema do app)."""
    try:
        return st.context.theme.type != "light"
    except Exception:
        return True


DARK = _tema_escuro()

st.divider()
cab_titulo, cab_foco = st.columns([3, 1.3])
with cab_foco:
    periodo_detalhe = st.selectbox(
        "Período em foco", list(reversed(periodos_sel)),
        format_func=lambda d: d.strftime("%m/%Y"), key="grupo_periodo_detalhe_sel",
        help="Período usado nos cartões, na leitura rápida, no ranking por empresa e na tabela de detalhe.",
    )
granularidade_detalhe = base_sel
periodo_anterior = None
idx_detalhe = periodos_sel.index(periodo_detalhe)
if idx_detalhe > 0:
    periodo_anterior = periodos_sel[idx_detalhe - 1]
with cab_titulo:
    st.subheader(f"Resumo do grupo — {periodo_detalhe.strftime('%m/%Y')} · {indicadores.rotulo_granularidade(base_sel)}")
    st.caption(
        f"Cada número vem de um único documento (período + granularidade); períodos de outras bases não entram nas "
        "contas nem nos deltas. "
        + (f"Variações vs {periodo_anterior.strftime('%m/%Y')} (período anterior da seleção, mesma base)."
           if periodo_anterior is not None else "Sem período anterior na seleção — sem variação.")
    )


def _so_base(lancs):
    return [r for r in lancs if r["periodo"] in periodos_sel and (r.get("granularidade") or "") == base_sel]


lancs_bp_multi = _so_base(_lancs_bp_todos)
lancs_dre_multi = _so_base(_lancs_dre_todos)

serie_bp = visao_grupo.montar_serie_kpis_grupo(
    lancs_bp_multi, cods_selecionados, visao_grupo.CONTAS_KPI_BP, periodos_sel, granularidade=base_sel,
)
serie_dre = visao_grupo.montar_serie_kpis_grupo(
    lancs_dre_multi, cods_selecionados, visao_grupo.CONTAS_KPI_DRE, periodos_sel, granularidade=base_sel,
)

_faltando_detalhe = indicadores.empresas_faltando(
    lancs_bp_multi, lancs_dre_multi, cods_selecionados, periodo_detalhe, base_sel,
)
if _faltando_detalhe:
    st.warning(
        f"Consolidado parcial: nesta base ({indicadores.rotulo_granularidade(base_sel)}) "
        f"{periodo_detalhe.strftime('%m/%Y')} não tem dado de "
        + ", ".join(NOME_POR_COD.get(c, c) for c in _faltando_detalhe)
        + " — os números somam só as empresas que têm esse documento."
    )

# v0.40.0 -- indicadores do grupo (EBITDA, margens, alavancagem, ...) por
# periodo, SOMANDO as empresas selecionadas e usando so' a base escolhida
# (mesmo `indicadores.calcular_indicadores` das outras telas; razoes
# calculadas sobre os totais somados, nao media das razoes).
ind_grupo = visao_grupo.montar_indicadores_grupo(
    lancs_bp_multi, lancs_dre_multi, cods_selecionados, periodos_sel, base_sel,
)
resumo_empresas = visao_grupo.montar_resumo_por_empresa(
    lancs_bp_multi, lancs_dre_multi, cods_selecionados, periodo_detalhe, base_sel,
)


def _valor_serie(serie, conta, periodo):
    ts = pd.Timestamp(periodo)
    return serie.loc[ts, conta] if ts in serie.index else float("nan")


def _hero(col, titulo, serie, coluna, periodo, periodo_ant, formato="moeda", ajuda=None):
    """Cartao grande: valor do periodo em foco, delta vs anterior e (com 2+
    periodos) minigrafico da serie. `serie` = DataFrame indexado por periodo."""
    ts = pd.Timestamp(periodo)
    valor = serie.loc[ts, coluna] if (ts in serie.index and coluna in serie.columns) else float("nan")
    delta = None
    if periodo_ant is not None and pd.Timestamp(periodo_ant) in serie.index and coluna in serie.columns:
        d = valor - serie.loc[pd.Timestamp(periodo_ant), coluna]
        delta = None if pd.isna(d) else d
    kwargs = dict(label=titulo, value=formatacao.moeda_br(valor),
                  delta=(formatacao.moeda_br(delta, forcar_sinal=True) if delta is not None else None), help=ajuda)
    historico = serie[coluna].dropna() if coluna in serie.columns else pd.Series(dtype=float)
    with col:
        with st.container(border=True):
            if len(historico) >= 2:
                try:
                    st.metric(**kwargs, chart_data=historico.tolist(), chart_type="area")
                    return
                except TypeError:
                    pass  # Streamlit antigo, sem minigrafico no st.metric
            st.metric(**kwargs)


hero_df = pd.DataFrame({
    "Receita Líquida": serie_dre["RECEITA OPERACIONAL LIQUIDA"],
    "EBITDA": ind_grupo["EBITDA"],
    "Resultado Líquido": serie_dre["LUCRO LIQUIDO DO EXERCICIO"],
    "Total do Ativo": serie_bp["TOTAL DO ATIVO"],
})
h = st.columns(4)
_hero(h[0], "Receita Líquida", hero_df, "Receita Líquida", periodo_detalhe, periodo_anterior)
_hero(h[1], "EBITDA", hero_df, "EBITDA", periodo_detalhe, periodo_anterior,
      ajuda="Resultado operacional antes de juros, depreciação e amortização (mesma fórmula de indicadores.py).")
_hero(h[2], "Resultado Líquido", hero_df, "Resultado Líquido", periodo_detalhe, periodo_anterior)
_hero(h[3], "Total do Ativo", hero_df, "Total do Ativo", periodo_detalhe, periodo_anterior)

# Leitura rápida: frases factuais calculadas dos mesmos numeros (sem opiniao).
_destaques = visao_grupo.gerar_destaques(
    ind_grupo, serie_dre, serie_bp, resumo_empresas, periodo_detalhe, periodo_anterior, NOME_POR_COD,
)
if _destaques:
    with st.container(border=True):
        st.markdown("**Leitura rápida**")
        for nivel, texto in _destaques:
            # "$" em markdown abre formula LaTeX ("R$ 38 ... R$ 42" vira codigo) -- escapa.
            texto = texto.replace("$", "\\$")
            st.markdown(f"- :orange[{texto}]" if nivel == "atencao" else f"- {texto}")


def _metric(col, serie, conta, label, periodo, periodo_ant):
    valor = _valor_serie(serie, conta, periodo)
    delta = None
    if periodo_ant is not None:
        d = valor - _valor_serie(serie, conta, periodo_ant)
        delta = None if pd.isna(d) else d
    col.metric(label, formatacao.moeda_br(valor),
               delta=(formatacao.moeda_br(delta, forcar_sinal=True) if delta is not None else None))


def _metric_ind(col, coluna, label, formato, periodo, periodo_ant, inverso=False, help=None):
    """KPI vindo de `ind_grupo`. formato: 'moeda' | 'pct' (delta em p.p.) | 'x'.
    `inverso=True`: subir e' pior (endividamento, alavancagem) -> seta
    vermelha na alta. Sem dado (NaN) mostra '—' e nunca inventa delta."""
    ts = pd.Timestamp(periodo)
    valor = ind_grupo.loc[ts, coluna] if (ts in ind_grupo.index and coluna in ind_grupo.columns) else float("nan")
    delta = None
    if periodo_ant is not None and pd.Timestamp(periodo_ant) in ind_grupo.index and coluna in ind_grupo.columns:
        delta = valor - ind_grupo.loc[pd.Timestamp(periodo_ant), coluna]
    if pd.isna(valor) or (delta is not None and pd.isna(delta)):
        delta = None
    if formato == "moeda":
        txt_valor = formatacao.moeda_br(valor)
        txt_delta = formatacao.moeda_br(delta, forcar_sinal=True) if delta is not None else None
    elif formato == "pct":
        txt_valor = formatacao.pct_br(valor)
        txt_delta = formatacao.pct_br(delta, forcar_sinal=True).replace("%", " p.p.") if delta is not None else None
    else:
        txt_valor = formatacao.numero_br(valor, sufixo="x")
        txt_delta = formatacao.numero_br(delta, sufixo="x", forcar_sinal=True) if delta is not None else None
    col.metric(label, txt_valor, delta=txt_delta, delta_color=("inverse" if inverso else "normal"), help=help)


tab_geral, tab_empresas, tab_estrutura, tab_detalhe = st.tabs(
    ["Visão geral", "Por empresa", "Liquidez e endividamento", "Detalhe por conta"]
)

with tab_geral:
    gg1, gg2 = st.columns([3, 2])
    with gg1:
        st.markdown("**Receita, EBITDA e resultado por período**")
        st.plotly_chart(graficos.fig_resultado_por_periodo(serie_dre, ind_grupo, base_sel, DARK),
                        width="stretch", config={"displayModeBar": False})
    with gg2:
        st.markdown("**Margens (%)**")
        st.plotly_chart(graficos.fig_margens(ind_grupo, base_sel, DARK),
                        width="stretch", config={"displayModeBar": False})
    if len(periodos_sel) < 2:
        st.caption("Com 2+ períodos selecionados os gráficos mostram a evolução; com 1 só, o valor do período.")
    st.markdown("**Rentabilidade**")
    r2 = st.columns(3)
    _metric(r2[0], serie_dre, "LUCRO BRUTO", "Lucro Bruto", periodo_detalhe, periodo_anterior)
    _metric_ind(r2[1], "Margem Bruta", "Margem Bruta", "pct", periodo_detalhe, periodo_anterior)
    _metric_ind(r2[2], "Margem EBITDA", "Margem EBITDA", "pct", periodo_detalhe, periodo_anterior)
    r2b = st.columns(3)
    _metric_ind(r2b[0], "Margem Líquida", "Margem Líquida", "pct", periodo_detalhe, periodo_anterior)
    _metric_ind(r2b[1], "ROA", "ROA", "pct", periodo_detalhe, periodo_anterior,
                help="Resultado líquido do período / Total do Ativo (não anualizado).")
    _metric_ind(r2b[2], "ROE", "ROE", "pct", periodo_detalhe, periodo_anterior,
                help="Resultado líquido do período / Patrimônio Líquido (não anualizado).")
    st.caption(
        "Consolidado = soma simples das empresas selecionadas (sem eliminações entre empresas). "
        "Margens, ROA/ROE, liquidez, endividamento e alavancagem são calculados sobre os totais somados."
    )

with tab_empresas:
    _metricas_emp = ["Receita Líquida", "EBITDA", "Resultado Líquido", "Total do Ativo", "Margem EBITDA"]
    metrica_emp = st.radio("Comparar empresas por", _metricas_emp, horizontal=True, key="grupo_metrica_empresas")
    if resumo_empresas[metrica_emp].dropna().empty:
        st.info(f"Nenhuma empresa selecionada tem {metrica_emp} em {periodo_detalhe.strftime('%m/%Y')} nesta base.")
    else:
        st.plotly_chart(graficos.fig_contribuicao(resumo_empresas, metrica_emp, NOME_POR_COD, DARK),
                        width="stretch", config={"displayModeBar": False})
    st.markdown("**Tabela comparativa**")
    tab_emp = resumo_empresas.copy()
    for col in ["Receita Líquida", "EBITDA", "Resultado Líquido", "Total do Ativo"]:
        tab_emp[col] = tab_emp[col].apply(formatacao.moeda_br)
    tab_emp["Margem EBITDA"] = tab_emp["Margem EBITDA"].apply(formatacao.pct_br)
    tab_emp["Endividamento Geral"] = tab_emp["Endividamento Geral"].apply(formatacao.pct_br)
    tab_emp.index = [NOME_POR_COD.get(c, c) for c in tab_emp.index]
    tab_emp.index.name = "Empresa"
    st.dataframe(tab_emp, use_container_width=True)
    st.caption("Empresa sem documento nesse período e base aparece com '—' (não é zero).")

with tab_estrutura:
    r3 = st.columns(3)
    _metric(r3[0], serie_bp, "TOTAL DO PASSIVO", "Total do Passivo", periodo_detalhe, periodo_anterior)
    _metric_ind(r3[1], "Capital de Giro", "Capital de Giro", "moeda", periodo_detalhe, periodo_anterior,
                help="Ativo Circulante - Passivo Circulante.")
    _metric_ind(r3[2], "Liquidez Corrente", "Liquidez Corrente", "x", periodo_detalhe, periodo_anterior)
    r3b = st.columns(3)
    _metric_ind(r3b[0], "Endividamento Geral", "Endividamento Geral", "pct", periodo_detalhe, periodo_anterior, inverso=True,
                help="Passivo exigível (circulante + não circulante) / Total do Ativo.")
    _metric_ind(r3b[1], "Alavancagem", "Alavancagem", "x", periodo_detalhe, periodo_anterior, inverso=True,
                help="Passivo exigível / Patrimônio Líquido (R$ de terceiros para cada R$ 1,00 próprio).")
    sp1, sp2, sp3 = st.columns(3)
    with sp1:
        st.markdown("**Capital de giro**")
        st.plotly_chart(graficos.fig_capital_giro(ind_grupo, base_sel, DARK), width="stretch", config={"displayModeBar": False})
    with sp2:
        st.markdown("**Liquidez corrente**")
        st.plotly_chart(graficos.fig_linha_unica(ind_grupo["Liquidez Corrente"], "Liquidez corrente", base_sel, DARK, "x", 1.0),
                        width="stretch", config={"displayModeBar": False})
    with sp3:
        st.markdown("**Endividamento geral**")
        st.plotly_chart(graficos.fig_linha_unica(ind_grupo["Endividamento Geral"], "Endividamento geral", base_sel, DARK, "pct"),
                        width="stretch", config={"displayModeBar": False})
    st.caption("Liquidez abaixo de 1,0x = ativo circulante menor que o passivo circulante. Endividamento: passivo exigível / ativo.")

with tab_detalhe:
    # ─────────────────────────── Detalhe por conta ─────────────────────────────
    st.divider()
    st.subheader(f"Detalhe por conta — {periodo_detalhe.strftime('%m/%Y')} · {indicadores.rotulo_granularidade(base_sel)}")

    visao = st.radio(
        "Visão", ["Macro (Energia × Consolidadoras)", "Específica (empresas abertas)"],
        horizontal=True, key="grupo_visao_sel",
    )

    algum_dado = False
    for tipo_sel in ("BP", "DRE"):
        # Fase 4: lancs_*_multi ja' so' tem a base escolhida (match obrigatorio
        # de periodo + granularidade); aqui filtra so' o periodo de detalhe.
        lancamentos = [r for r in (lancs_bp_multi if tipo_sel == "BP" else lancs_dre_multi) if r["periodo"] == periodo_detalhe]
        st.markdown(f"**{tipo_sel}**")
        if not lancamentos:
            st.caption(f"Nenhum lançamento de {tipo_sel} em {periodo_detalhe.strftime('%m/%Y')} pras empresas selecionadas.")
            continue
        algum_dado = True

        pivot = visao_grupo.montar_pivot_grupo(lancamentos, cods_selecionados)

        # Fix 23/09/2026 (achado do Rafael): column_config.NumberColumn com
        # format="R$ %.2f"/"percent" renderiza em padrao americano (sem
        # separador de milhar, ponto decimal em vez de virgula) -- os valores
        # NUMERICOS de visao_macro/visao_especifica continuam intactos (o chat
        # via consultas_chat.consultar_visao_grupo reusa as mesmas funcoes e
        # precisa do numero cru, nao do texto formatado); so' a COPIA exibida
        # aqui e' pre-formatada como texto BR (formatacao.py), mesma solucao
        # das outras telas.
        if visao.startswith("Macro"):
            saida = visao_grupo.visao_macro(pivot, cods_selecionados)
            saida_fmt = saida.copy()
            for col in ["VALOR CONSOLIDADO", "ENERMAIS ENERGIA", "EMPRESAS CONSOLIDADORAS"]:
                saida_fmt[col] = saida_fmt[col].apply(formatacao.moeda_br)
            for col in ["% ENERGIA", "% CONSOLIDADORAS"]:
                saida_fmt[col] = saida_fmt[col].apply(formatacao.pct_br)
            column_config = {
                "grupo": st.column_config.TextColumn("Grupo", disabled=True),
                "conta": st.column_config.TextColumn("Conta", disabled=True),
                "VALOR CONSOLIDADO": st.column_config.TextColumn("Valor consolidado"),
                "ENERMAIS ENERGIA": st.column_config.TextColumn("Enermais Energia"),
                "% ENERGIA": st.column_config.TextColumn("% Energia"),
                "EMPRESAS CONSOLIDADORAS": st.column_config.TextColumn("Empresas consolidadoras"),
                "% CONSOLIDADORAS": st.column_config.TextColumn("% Consolidadoras"),
            }
        else:
            saida = visao_grupo.visao_especifica(pivot, cods_selecionados, NOME_POR_COD)
            saida_fmt = saida.copy()
            saida_fmt["VALOR CONSOLIDADO"] = saida_fmt["VALOR CONSOLIDADO"].apply(formatacao.moeda_br)
            column_config = {
                "grupo": st.column_config.TextColumn("Grupo", disabled=True),
                "conta": st.column_config.TextColumn("Conta", disabled=True),
                "VALOR CONSOLIDADO": st.column_config.TextColumn("Valor consolidado"),
            }
            for cod in cods_selecionados:
                saida_fmt[NOME_POR_COD[cod]] = saida_fmt[NOME_POR_COD[cod]].apply(formatacao.moeda_br)
                column_config[NOME_POR_COD[cod]] = st.column_config.TextColumn(f"{NOME_POR_COD[cod]} ({cod})")

        st.dataframe(saida_fmt, column_config=column_config, hide_index=True, use_container_width=True)
        st.caption(f"{len(pivot)} conta(s) distinta(s) de {tipo_sel} · {len(cods_selecionados)} empresa(s) selecionada(s).")

    if not algum_dado:
        st.info(f"Nenhum lançamento (BP ou DRE) em {periodo_detalhe.strftime('%m/%Y')} pras empresas selecionadas.")
