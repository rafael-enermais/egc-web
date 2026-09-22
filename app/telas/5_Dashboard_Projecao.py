# -*- coding: utf-8 -*-
"""
Tela DASHBOARD DE PROJECAO — BP/DRE futuro por conta, 3º item da fila
combinada com Rafael (Visao Grupo -> rodape -> dashboard de projecao ->
chat estilo TIA.go/Viaj.ai). Pedido dele: metodo "mais robusto ja",
deixando a base pronta pra futura integracao com o TIA.go (outro app
lendo o MESMO banco Supabase pra visao panoramica do negocio) e com a
constraint `tipo` ja aceitando FLUXO_CAIXA pra quando entrada/saida for
decidido (nao construido agora, so' destravado).

Motor de projecao (app/projecao.py, modulo puro sem banco): 3 niveis de
metodo por (grupo,conta), escolhido AUTOMATICAMENTE pelo tamanho do
historico real -- <4 periodos vira flat_ultimo_valor, 4-23 vira
tendencia_linear, 24+ (2 anos) vira tendencia_sazonal. Premissa checada
em 22/09/2026 (Visao Grupo, uniao de periodos das 6 empresas): hoje so
existe 1 periodo ativo no banco, entao a maioria das contas vai cair no
nivel 1 (flat) ate mais historico ser importado -- o metodo usado
aparece na tabela, sem fingir confianca que o dado nao sustenta ainda.

Ajuste manual (contrato novo, evento pontual): gravado em
egc.projecoes_ajustes, somado ao baseline estatistico por (grupo,conta,
periodo). "Gravar projecao no banco" materializa o resultado combinado
em egc.projecoes (nao so na tela) -- e' essa tabela que uma futura
integracao externa (TIA.go) poderia ler direto, sem rodar o modelo de
novo.

So' BP/DRE nesta versao (fluxo de caixa fica pra quando for decidido,
por pedido explicito do Rafael 22/09/2026).
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import db  # noqa: E402
import projecao  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}
METODO_LABEL = {
    "flat_ultimo_valor": "Flat (último valor)",
    "tendencia_linear": "Tendência linear",
    "tendencia_sazonal": "Tendência + sazonalidade",
}

st.title("📈 Dashboard de Projeção")

usuario = usuario_atual()
sidebar_contexto(usuario)  # so' rodape -- ver nota em conexao.sidebar_contexto
conn = get_conn()

st.caption(
    "Projeção de BP/DRE por conta, com ajuste manual sobre a base estatística. "
    "Método usado aparece por conta — com pouco histórico, a projeção é o último valor repetido, não uma tendência de verdade."
)

nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
idx_empresa = st.selectbox(
    "Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes_emp[i], key="projecao_empresa_sel",
)
cod_empresa, nome_empresa, _cnpj_empresa = EMPRESAS_FIXAS[idx_empresa]

c1, c2 = st.columns([1, 3])
tipo_sel = c1.radio("Tipo", ["BP", "DRE"], key="projecao_tipo_sel")
horizonte = c2.slider("Horizonte (meses à frente)", min_value=1, max_value=24, value=3, key="projecao_horizonte_sel")

historico_bruto = db.listar_historico_grupo(conn, cod_empresa, tipo_sel, status="ATIVO")
if not historico_bruto:
    st.info(f"Nenhum lançamento de {tipo_sel} ativo pra {nome_empresa} ainda. Importe um PDF primeiro.")
    st.stop()

historico_por_conta: dict = {}
for linha in historico_bruto:
    chave = (linha["grupo"], linha["conta"])
    historico_por_conta.setdefault(chave, []).append((linha["periodo"], float(linha["valor"])))

ultimo_periodo = max(linha["periodo"] for linha in historico_bruto)
periodos_futuros = projecao.gerar_periodos_futuros(ultimo_periodo, horizonte)

ajustes_brutos = db.listar_ajustes_projecao(conn, cod_empresa, tipo_sel, status="ATIVO")
ajustes_por_conta: dict = {}
for a in ajustes_brutos:
    chave = (a["grupo"], a["conta"])
    ajustes_por_conta.setdefault(chave, []).append({"periodo": a["periodo"], "valor_ajuste": float(a["valor_ajuste"])})

linhas_completas = []
for (grupo, conta), historico_conta in historico_por_conta.items():
    baseline = projecao.gerar_baseline(historico_conta, periodos_futuros)
    projetado = projecao.aplicar_ajustes(baseline, ajustes_por_conta.get((grupo, conta), []))
    for linha in projetado:
        linhas_completas.append({"grupo": grupo, "conta": conta, **linha})

df = pd.DataFrame(linhas_completas)

# --- Tabela resumo: 1 linha por conta, 1 coluna por período futuro -------
resumo_metodo = (
    df.groupby(["grupo", "conta"])
    .agg(metodo=("metodo", "first"), periodos_historico=("periodos_historico", "first"))
    .reset_index()
)
resumo_metodo["Método"] = resumo_metodo["metodo"].map(METODO_LABEL).fillna(resumo_metodo["metodo"])
pivot = df.pivot_table(index=["grupo", "conta"], columns="periodo", values="valor_projetado", aggfunc="sum")
pivot.columns = [c.strftime("%m/%Y") for c in pivot.columns]
pivot = pivot.reset_index()
tabela = resumo_metodo[["grupo", "conta", "Método", "periodos_historico"]].merge(pivot, on=["grupo", "conta"])
tabela = tabela.rename(columns={"grupo": "Grupo", "conta": "Conta", "periodos_historico": "Períodos de histórico"})

column_config = {
    "Grupo": st.column_config.TextColumn(disabled=True),
    "Conta": st.column_config.TextColumn(disabled=True),
    "Método": st.column_config.TextColumn(disabled=True),
    "Períodos de histórico": st.column_config.NumberColumn(disabled=True),
}
for col in tabela.columns:
    if col not in column_config:
        column_config[col] = st.column_config.NumberColumn(col, format="R$ %.2f", disabled=True)

st.dataframe(tabela, column_config=column_config, hide_index=True, use_container_width=True)
st.caption(f"{len(historico_por_conta)} conta(s) projetada(s) · horizonte de {horizonte} mês(es) a partir de {ultimo_periodo.strftime('%m/%Y')}.")

# --- Gráfico: histórico + projeção de 1 conta escolhida -------------------
st.divider()
st.subheader("Gráfico por conta")
opcoes_conta = sorted(historico_por_conta.keys())
grupo_sel, conta_sel = st.selectbox(
    "Conta", opcoes_conta, format_func=lambda gc: f"{gc[0]} — {gc[1]}", key="projecao_grafico_conta_sel",
)
serie_historico = {p: v for p, v in historico_por_conta[(grupo_sel, conta_sel)]}
serie_projetado = {
    linha["periodo"]: linha["valor_projetado"]
    for linha in linhas_completas
    if linha["grupo"] == grupo_sel and linha["conta"] == conta_sel
}
todos_periodos = sorted(set(serie_historico) | set(serie_projetado))
df_grafico = pd.DataFrame(
    {
        "Histórico": [serie_historico.get(p) for p in todos_periodos],
        "Projetado": [serie_projetado.get(p) for p in todos_periodos],
    },
    index=[p.strftime("%m/%Y") for p in todos_periodos],
)
st.line_chart(df_grafico)

# --- Ajuste manual ----------------------------------------------------
st.divider()
st.subheader("Ajuste manual")
st.caption("Some (ou subtraia, com valor negativo) um ajuste sobre a base estatística — ex. contrato novo, evento pontual.")

ca0, ca1, ca2, ca3 = st.columns([2, 1, 1, 2])
grupo_ajuste, conta_ajuste = ca0.selectbox(
    "Conta", opcoes_conta, format_func=lambda gc: f"{gc[0]} — {gc[1]}", key="projecao_ajuste_conta_sel",
)
periodo_ajuste = ca1.selectbox(
    "Período", periodos_futuros, format_func=lambda d: d.strftime("%m/%Y"), key="projecao_ajuste_periodo_sel",
)
valor_ajuste_input = ca2.number_input("Valor do ajuste", value=0.0, step=100.0, format="%.2f", key="projecao_ajuste_valor")
descricao_ajuste = ca3.text_input("Motivo (obrigatório)", key="projecao_ajuste_descricao")

if st.button("+ Adicionar ajuste"):
    if not descricao_ajuste.strip():
        st.error("Informe o motivo do ajuste.")
    elif valor_ajuste_input == 0.0:
        st.error("Valor do ajuste não pode ser zero.")
    else:
        db.salvar_ajuste_projecao(
            conn, cod_empresa, tipo_sel, periodo_ajuste, grupo_ajuste, conta_ajuste,
            valor_ajuste_input, descricao_ajuste.strip(), usuario,
        )
        st.success(f"Ajuste adicionado em {conta_ajuste} — {periodo_ajuste.strftime('%m/%Y')}.")
        st.rerun()

if ajustes_brutos:
    st.markdown("**Ajustes ativos**")
    for a in ajustes_brutos:
        col_txt, col_btn = st.columns([5, 1])
        col_txt.write(
            f"{a['grupo']} — {a['conta']} · {a['periodo'].strftime('%m/%Y')} · "
            f"R$ {a['valor_ajuste']:.2f} · {a['descricao']}"
        )
        if col_btn.button("Remover", key=f"remover_ajuste_{a['id']}"):
            db.inativar_ajuste_projecao(conn, a["id"])
            st.rerun()

# --- Gravar no banco (materializar pra consumo externo) -------------------
st.divider()
if st.button("💾 Gravar projeção no banco", type="primary"):
    linhas_para_gravar = [
        {"empresa_codigo": cod_empresa, "tipo": tipo_sel, **linha} for linha in linhas_completas
    ]
    n = db.gravar_projecoes(conn, linhas_para_gravar)
    st.success(f"{n} linha(s) de projeção gravada(s) em egc.projecoes.")
