# -*- coding: utf-8 -*-
"""
Tela ASSISTENTE — Dashboard + Chat lado a lado, 6o (ultimo) item da fila
combinada com Rafael (Visao Grupo -> rodape -> Dashboard de Projecao ->
chat estilo TIA.go/Viaj.ai). Layout escolhido por ele em 22/09/2026
(AskUserQuestion): "Dashboard + chat lado a lado", replicando o layout de
$HOME/mnt/Projetos/TIA.go/arquivos/app_tiago.py (app de referencia dele,
ja em producao) -- nao uma pagina so' de chat.

Escopo do chat (combinado com Rafael 22/09/2026): so' BP/DRE (1 empresa)
+ Visao Grupo (consolidado) -- Dashboard de Projecao FICA DE FORA de
proposito (ver docstring de app/chat_egc.py e app/consultas_chat.py pro
motivo: metodo estatistico hoje e' basico demais com so' 1 periodo real
de historico pra virar "previsao respondida pelo chat" sem enganar).

Coluna esquerda (dashboard): consulta rapida de BP/DRE de 1 empresa —
REUSA consultas_chat.consultar_bp_dre (a MESMA funcao que o chat chama
como ferramenta), sem logica duplicada. Quando o chat acabou de fazer
alguma consulta, a ULTIMA aparece tambem aqui em cima (mesma ideia do
dash_extra reativo do app_tiago.py, AGORA COM GRAFICO Plotly tambem --
pedido do Rafael (23/09/2026) de replicar o padrao do TIA.go
(go.Bar/go.Figure + st.plotly_chart, mesmo import ja usado la). Uma
funcao _grafico_* por formato de resultado (contas por grupo,
periodos por empresa, completude por periodo) -- indicadores fica SO'
tabela de proposito (mistura x/R$/% no mesmo indicador, um grafico
de barra unico ali enganaria por causa da escala).

Coluna direita (chat): mesmo padrao de 2 fases do app_tiago.py (grava a
pergunta + rerun; processa na recarga seguinte, sem pergunta pendente) —
evita o bug de ordem ja documentado la (nunca renderiza a mensagem nova
"na mao" fora do loop). client Anthropic so' e' criado se o secret
ANTHROPIC_API_KEY estiver configurado (Rafael configura direto no
Streamlit Cloud, nunca passado pra mim em chat) -- sem ele, a tela
funciona normal (dashboard funciona), so' o chat fica desabilitado com
aviso.
"""
import re
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import chat_egc  # noqa: E402
import consultas_chat  # noqa: E402
import db  # noqa: E402
import formatacao  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}
EMPRESAS_CODIGOS = [cod for cod, _nome, _cnpj in EMPRESAS_FIXAS]

def _grafico_contas_por_grupo(df_contas: pd.DataFrame):
    """
    Barra horizontal com o total por GRUPO (Ativo Circulante, Passivo
    Circulante etc) -- funciona tanto pra consultar_bp_dre (coluna
    "valor") quanto consultar_visao_grupo (colunas "VALOR CONSOLIDADO",
    "ENERMAIS ENERGIA" etc), igual ao fix de formatacao acima: nunca
    assume 1 nome fixo, pega a 1a coluna de dinheiro disponivel.
    Erik.AI (23/09/2026) -- pedido do Rafael de reusar o padrao do
    TIA.go (go.Bar + st.plotly_chart) no dashboard do chat.
    """
    cols_dinheiro = [
        c for c in df_contas.columns
        if c not in ("grupo", "conta") and not str(c).strip().startswith("%")
    ]
    if not cols_dinheiro:
        return None
    col = "valor" if "valor" in cols_dinheiro else (
        "VALOR CONSOLIDADO" if "VALOR CONSOLIDADO" in cols_dinheiro else cols_dinheiro[0]
    )
    agrupado = df_contas.groupby("grupo")[col].sum().sort_values()
    fig = go.Figure(go.Bar(x=agrupado.values, y=agrupado.index, orientation="h", name=col))
    fig.update_layout(height=320, margin=dict(t=20, l=10))
    return fig


def _grafico_periodos_por_empresa(linhas_periodos: list[dict]):
    """Barra Empresa x Qtde de períodos ativos -- consultar_periodos."""
    if not linhas_periodos:
        return None
    df = pd.DataFrame(linhas_periodos)
    fig = go.Figure(go.Bar(x=df["Empresa"], y=df["Qtde"], name="Períodos"))
    fig.update_layout(height=300, margin=dict(t=20))
    return fig


def _grafico_completude(linhas_completude: list[dict]):
    """
    Barra empilhada Completas x Pendentes por período -- extrai os 2
    números do texto "Status" (ex.: "✅ Completo (6/6)"), sem mudar o
    formato que visao_grupo.resumir_completude_por_periodo devolve.
    """
    if not linhas_completude:
        return None
    periodos, completas, pendentes = [], [], []
    for l in linhas_completude:
        m = re.search(r"\((\d+)/(\d+)\)", str(l.get("Status", "")))
        if not m:
            continue
        n_completas, n_total = int(m.group(1)), int(m.group(2))
        periodos.append(l.get("Período"))
        completas.append(n_completas)
        pendentes.append(n_total - n_completas)
    if not periodos:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(x=periodos, y=completas, name="Completas"))
    fig.add_trace(go.Bar(x=periodos, y=pendentes, name="Pendentes"))
    fig.update_layout(barmode="stack", height=300, margin=dict(t=20))
    return fig


st.title("🤖 Erik.AI")

usuario = usuario_atual()
sidebar_contexto(usuario)  # so' rodape -- ver nota em conexao.sidebar_contexto
conn = get_conn()

st.caption(
    "Pergunte sobre BP, DRE ou Visão Grupo das 6 empresas — o assistente só responde "
    "com dado que já está importado no sistema, nunca inventa número."
)


def _cliente_anthropic():
    """
    None se o secret nao estiver configurado ainda (Rafael configura
    direto no Streamlit Cloud) -- mesmo padrao defensivo de
    conexao.get_conn() pro DATABASE_URL, so' que aqui NAO trava a tela
    com st.stop() (o dashboard da esquerda funciona sem chat).
    """
    try:
        chave = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        return None
    if not chave:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=chave)


col_dash, col_chat = st.columns([3, 2])

# ─────────────────────────────────────────────
#  COLUNA ESQUERDA — dashboard
# ─────────────────────────────────────────────
with col_dash:
    st.subheader("Dashboard")

    ultima = st.session_state.get("assistente_ultima_ferramenta")
    if ultima:
        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**Última consulta do chat** — `{ultima['nome']}`")
            if c2.button("✕", key="assistente_limpar_ultima", help="Limpar"):
                st.session_state.assistente_ultima_ferramenta = None
                st.rerun()
            resultado = ultima["resultado"]
            if "erro" in resultado:
                st.warning(resultado["erro"])
                if resultado.get("periodos_disponiveis"):
                    st.caption("Períodos disponíveis: " + ", ".join(resultado["periodos_disponiveis"]))
            elif "contas" in resultado and resultado["contas"]:
                # Fix 23/09/2026 (mesmo achado do Rafael de formatacao
                # americana): pre-formata em BR antes de exibir, sem mexer
                # no resultado numerico original (usado so' pra exibicao
                # aqui, o modelo ja recebeu o numero cru via tool_result).
                #
                # BUG real 23/09/2026 (2a leva, reportado ao vivo: KeyError
                # 'valor' quebrando a pagina inteira): "contas" tem 2 formatos
                # bem diferentes dependendo de qual ferramenta rodou --
                # consultar_bp_dre devolve {grupo, conta, valor} (1 coluna de
                # dinheiro, minuscula), consultar_visao_grupo devolve
                # {grupo, conta, "VALOR CONSOLIDADO", "ENERMAIS ENERGIA",
                # "% ENERGIA", "EMPRESAS CONSOLIDADORAS", "% CONSOLIDADORAS"}
                # (varias colunas de dinheiro MAIUSCULAS + colunas de % --
                # ver visao_grupo.visao_macro/visao_especifica). O fix
                # anterior assumia cegamente 1 coluna "valor", quebrando com
                # KeyError sempre que o chat rodava consultar_visao_grupo.
                # Fix: formata cada coluna numerica pelo NOME dela (%  vira
                # pct_br, resto vira moeda_br), nunca assume um nome fixo.
                df_contas = pd.DataFrame(resultado["contas"])
                for col in df_contas.columns:
                    if col in ("grupo", "conta"):
                        continue
                    if col.strip().startswith("%"):
                        df_contas[col] = df_contas[col].apply(formatacao.pct_br)
                    else:
                        df_contas[col] = df_contas[col].apply(formatacao.moeda_br)
                fig_contas = _grafico_contas_por_grupo(df_contas)
                if fig_contas is not None:
                    st.plotly_chart(fig_contas, width="stretch")
                st.dataframe(df_contas, hide_index=True, use_container_width=True)
            elif "periodos_por_empresa" in resultado:
                # Fix 23/09/2026 (achado real do Rafael testando ao vivo): antes
                # disto era st.json() cru -- funcionava, mas parecia "bugado" do
                # lado do dashboard (JSON bruto recolhivel) do lado do resultado
                # limpo que o proprio chat mostra em texto (mesma consulta,
                # 2 aparencias bem diferentes). Agora usa a mesma tabela
                # Empresa/Periodos/Qtde que o modelo ja monta em prosa na
                # resposta de texto -- consistente, sem mudar nenhuma ferramenta
                # nem o formato que consultas_chat.consultar_periodos devolve.
                linhas_periodos = [
                    {
                        "Empresa": NOME_POR_COD.get(cod, cod),
                        "Períodos disponíveis": ", ".join(sorted(periodos)) if periodos else "—",
                        "Qtde": len(periodos),
                    }
                    for cod, periodos in resultado["periodos_por_empresa"].items()
                ]
                fig_periodos = _grafico_periodos_por_empresa(linhas_periodos)
                if fig_periodos is not None:
                    st.plotly_chart(fig_periodos, width="stretch")
                st.dataframe(pd.DataFrame(linhas_periodos), hide_index=True, use_container_width=True)
            elif "indicadores" in resultado:
                # Erik.AI (23/09/2026) -- nova ferramenta consultar_indicadores.
                # Mesmo mapeamento de formato por indicador que a Início usa
                # (app.py, _fmt) -- x/R$/pct por coluna, nunca genérico.
                FORMATO_INDICADOR = {
                    "Liquidez Corrente": "x", "Capital de Giro": "R$", "Endividamento Geral": "pct",
                    "Margem Bruta": "pct", "Margem Líquida": "pct", "ROA": "pct", "ROE": "pct",
                }
                st.caption(f"Período: {resultado['periodo']} · Empresas: {', '.join(resultado['empresas_incluidas'])}")
                linhas_ind = []
                for nome_ind, valor in resultado["indicadores"].items():
                    fmt = FORMATO_INDICADOR.get(nome_ind, "R$")
                    if fmt == "x":
                        texto = formatacao.numero_br(valor, sufixo="x")
                    elif fmt == "pct":
                        texto = formatacao.pct_br(valor)
                    else:
                        texto = formatacao.moeda_br(valor)
                    linhas_ind.append({"Indicador": nome_ind, "Valor": texto})
                st.dataframe(pd.DataFrame(linhas_ind), hide_index=True, use_container_width=True)
            elif "completude_por_periodo" in resultado:
                # Erik.AI (23/09/2026) -- nova ferramenta consultar_completude,
                # mesma tabela do painel de pendências da Início (texto puro,
                # sem coluna numérica pra formatar).
                if not resultado["completude_por_periodo"]:
                    st.caption("Sem período nenhum encontrado pra essas empresas.")
                else:
                    fig_completude = _grafico_completude(resultado["completude_por_periodo"])
                    if fig_completude is not None:
                        st.plotly_chart(fig_completude, width="stretch")
                    st.dataframe(
                        pd.DataFrame(resultado["completude_por_periodo"]), hide_index=True, use_container_width=True,
                    )
            else:
                st.caption("Sem dado pra exibir dessa consulta.")
        st.divider()

    st.markdown("**Consulta rápida**")
    nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
    idx_empresa = st.selectbox(
        "Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes_emp[i], key="assistente_empresa_sel",
    )
    cod_empresa, nome_empresa, _cnpj_empresa = EMPRESAS_FIXAS[idx_empresa]
    tipo_sel = st.radio("Tipo", ["BP", "DRE"], key="assistente_tipo_sel", horizontal=True)

    resultado_rapido = consultas_chat.consultar_bp_dre(conn, cod_empresa, tipo_sel)
    if "erro" in resultado_rapido:
        st.info(resultado_rapido["erro"])
    else:
        st.caption(f"{nome_empresa} · {resultado_rapido['periodo']} · {resultado_rapido['quantidade_contas']} conta(s)")
        df_rapida = pd.DataFrame(resultado_rapido["contas"])
        df_rapida["valor"] = df_rapida["valor"].apply(formatacao.moeda_br)
        st.dataframe(
            df_rapida,
            column_config={"valor": st.column_config.TextColumn("Valor")},
            hide_index=True, use_container_width=True,
        )

# ─────────────────────────────────────────────
#  COLUNA DIREITA — chat
# ─────────────────────────────────────────────
with col_chat:
    st.subheader("Chat")

    client = _cliente_anthropic()
    if client is None:
        st.warning(
            "Secret `ANTHROPIC_API_KEY` não configurado ainda. Configure em "
            "Settings → Secrets (Streamlit Cloud) pra habilitar o chat — o "
            "dashboard ao lado funciona normalmente sem isso."
        )
        st.stop()

    if "assistente_mensagens" not in st.session_state:
        st.session_state.assistente_mensagens = []

    # 23/09/2026 (feedback ao vivo): height="stretch" ficou pior -- caixa
    # nasce vazia lá em cima e cresce infinito conforme o chat cresce, em
    # vez de ter altura FIXA com a conversa crescendo pra cima como chat de
    # verdade.
    #
    # Comparado de fato com o TIA.go (pasta conectada nesta sessão,
    # $HOME/mnt/Projetos/TIA.go/arquivos/app_tiago.py) -- e a conclusão de
    # lá bate 100% com o motivo documentado do bug do "stretch" aqui: o
    # próprio TIA.go tentou esticar a caixa via CSS (unidade vh amarrada no
    # key do container) e DESISTIU, com o motivo documentado no código dele:
    # o Streamlit cria a classe CSS "st-key-<key>" DENTRO do container, não
    # no elemento que de fato controla altura/scroll -- sem garantia de
    # funcionar (github.com/streamlit/streamlit/issues/10674, issue real,
    # não suposição). A solução deles (v0.8.1/v0.8.2) foi exatamente a
    # mesma linha de raciocínio que já tinha aplicado aqui: container de
    # altura FIXA em pixels (nunca "stretch"), chat_input em fluxo normal
    # logo abaixo dentro do MESMO container -- sem tentar posição fixa.
    # Diferença que importa pro pedido dele: o TIA.go começou em 480 (mesmo
    # valor que eu tinha usado antes do fix), Rafael reportou pequeno e
    # pediu pra parar de variar e fixar "sempre grande" -- eles fixaram em
    # 820 (v0.8.2 lá, e ficou definitivo, sem reclamação depois). Aplicando
    # o MESMO valor já validado por ele no projeto irmão, em vez de
    # adivinhar um número novo. `autoscroll=True` (recurso mais novo do
    # Streamlit, não existia quando o TIA.go foi escrito) mantido aqui em
    # cima disso -- já confirmado ao vivo que mantém o scroll grudado na
    # mensagem mais recente.
    historico_box = st.container(height=820, autoscroll=True)
    with historico_box:
        for m in st.session_state.assistente_mensagens:
            with st.chat_message(m["role"]):
                st.markdown(m["content"].replace("$", "\\$"))  # $ quebra Markdown/LaTeX -- so' na exibicao, nao no historico guardado
    pergunta = st.chat_input("Pergunte sobre BP, DRE ou Visão Grupo...")

    # Padrao de 2 fases (mesmo do app_tiago.py -- evita bug de ordem
    # documentado la): fase 1 so' grava a pergunta e recarrega; fase 2
    # roda na recarga seguinte, sem pergunta nova pendente.
    if pergunta:
        st.session_state.assistente_mensagens.append({"role": "user", "content": pergunta})
        st.rerun()

    if st.session_state.assistente_mensagens and st.session_state.assistente_mensagens[-1]["role"] == "user":
        with st.spinner("Consultando..."):
            system_prompt = chat_egc.montar_system_prompt(usuario, EMPRESAS_CODIGOS, conn=conn)
            try:
                r = chat_egc.responder(
                    client, conn, st.session_state.assistente_mensagens, system_prompt, EMPRESAS_CODIGOS,
                )
            except Exception as exc:
                # log completo (task #16) -- falha na chamada da API (rede, rate
                # limit, resposta inesperada) nao pode travar a tela sem rastro.
                try:
                    db.registrar_evento(conn, "chat", "ERRO", "Falha ao chamar o assistente (API)",
                                         usuario=usuario, detalhe=str(exc))
                except Exception:
                    pass
                r = {"texto": f"Não consegui responder agora (erro na API): {exc}", "ferramentas_usadas": []}
        st.session_state.assistente_mensagens.append({"role": "assistant", "content": r["texto"]})
        if r["ferramentas_usadas"]:
            st.session_state.assistente_ultima_ferramenta = r["ferramentas_usadas"][-1]
        st.rerun()
