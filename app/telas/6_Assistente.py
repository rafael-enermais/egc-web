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
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import acoes_chat  # noqa: E402
import chat_egc  # noqa: E402
import chat_memoria  # noqa: E402
import chat_visual  # noqa: E402
import consultas_chat  # noqa: E402
import db  # noqa: E402
import formatacao  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}
EMPRESAS_CODIGOS = [cod for cod, _nome, _cnpj in EMPRESAS_FIXAS]

st.title("🤖 Erik.AI")

usuario = usuario_atual()
sidebar_contexto(usuario)  # so' rodape -- ver nota em conexao.sidebar_contexto
conn = get_conn()

st.caption(
    "Pergunte sobre BP, DRE, Visão Grupo, indicadores, evolução, completude, importações, correções e "
    "Notas Fiscais das 6 empresas. A Erik.AI só responde com dado que já está no sistema e nunca grava "
    "sozinha: quando você pedir uma alteração, ela só propõe, e nada muda até você clicar em Aprovar."
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


# Memória da conversa (Supabase, por usuário, janela limitada) -- carrega UMA vez por sessão.
if "assistente_mensagens" not in st.session_state:
    st.session_state.assistente_mensagens = chat_memoria.carregar_historico(conn, usuario)
    st.session_state["assistente_memoria_carregada"] = len(st.session_state.assistente_mensagens)

col_dash, col_chat = st.columns([3, 2])

# ─────────────────────────────────────────────
#  COLUNA ESQUERDA — dashboard
# ─────────────────────────────────────────────
with col_dash:
    st.subheader("Dashboard")

    # ── Ações propostas pela Erik.AI (aguardando aprovação humana) ──────────
    _propostas = st.session_state.setdefault("assistente_propostas", [])
    _msg_acao = st.session_state.pop("assistente_acao_msg", None)
    if _msg_acao:
        (st.success if _msg_acao[0] else st.warning)(_msg_acao[1])
    for _pr in list(_propostas):
        with st.container(border=True):
            st.markdown(f"**✋ Aguardando sua aprovação — {_pr['titulo']}**")
            if _pr.get("motivo"):
                st.caption(f"Motivo informado: {_pr['motivo']}")
            _linhas = pd.DataFrame(
                [(k, formatacao.moeda_br(v) if isinstance(v, float) and k.lower().startswith(("valor", "novo valor")) else v)
                 for k, v in _pr["resumo"]], columns=["Campo", "Valor"],
            )
            st.dataframe(_linhas.astype({"Valor": str}), hide_index=True, width="stretch")
            _ed = {}
            _edit = _pr.get("editaveis") or {}
            if "novo_status" in _edit:
                _op = _edit["novo_status"]
                _ed["novo_status"] = st.selectbox(
                    "Novo status (pode ajustar)", _op, index=_op.index(_pr["parametros"]["novo_status"]),
                    key=f"acao_status_{_pr['id']}")
            if "novo_valor" in _edit:
                _ed["novo_valor"] = st.number_input(
                    "Novo valor (pode ajustar)", value=float(_pr["parametros"]["novo_valor"]), step=0.01, format="%.2f",
                    key=f"acao_valor_{_pr['id']}")
            st.caption("Reversível: o histórico fica registrado (trilha de auditoria) e nada é apagado.")
            _b1, _b2, _ = st.columns([1.2, 1, 2])
            if _b1.button("✅ Aprovar e executar", key=f"acao_ok_{_pr['id']}", type="primary"):
                _ok, _txt = acoes_chat.executar_proposta(conn, _pr, usuario, EMPRESAS_CODIGOS, edicoes=_ed)
                _propostas[:] = [p for p in _propostas if p["id"] != _pr["id"]]
                st.session_state["assistente_acao_msg"] = (_ok, _txt)
                _nota = ("✅ Ação aprovada e executada: " if _ok else "⚠️ Ação aprovada, mas NÃO executada: ") + _txt
                st.session_state.setdefault("assistente_mensagens", []).append({"role": "assistant", "content": _nota})
                chat_memoria.salvar_mensagem(conn, usuario, "assistant", _nota)
                st.rerun()
            if _b2.button("Rejeitar", key=f"acao_no_{_pr['id']}"):
                acoes_chat.registrar_rejeicao(conn, _pr, usuario)
                _propostas[:] = [p for p in _propostas if p["id"] != _pr["id"]]
                _nota = f"Ação rejeitada por você: {_pr['titulo']}."
                st.session_state.setdefault("assistente_mensagens", []).append({"role": "assistant", "content": _nota})
                chat_memoria.salvar_mensagem(conn, usuario, "assistant", _nota)
                st.rerun()

    # ── Resultado do chat: tabela / gráfico / relatório ───────────────────
    _resultados = st.session_state.get("assistente_resultados") or []
    if _resultados:
        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            _ordem = list(range(len(_resultados) - 1, -1, -1))
            _idx = c1.selectbox(
                "Consulta do chat", _ordem, key=f"assistente_consulta_sel_{len(_resultados)}",
                format_func=lambda i: f"{i + 1}. {chat_visual.TITULOS.get(_resultados[i]['nome'], _resultados[i]['nome'])}"
                                      + ("  (mais recente)" if i == len(_resultados) - 1 else ""),
            )
            if c2.button("✕", key="assistente_limpar_ultima", help="Limpar consultas do painel"):
                st.session_state.assistente_ultima_ferramenta = None
                st.session_state.assistente_resultados = []
                st.rerun()
            ultima = _resultados[_idx]
            resultado = ultima["resultado"]
            prep = chat_visual.preparar(ultima["nome"], resultado, NOME_POR_COD)
            if "erro" in resultado:
                st.warning(resultado["erro"])
                if resultado.get("periodos_disponiveis"):
                    st.caption("Períodos disponíveis: " + ", ".join(resultado["periodos_disponiveis"]))
            elif prep is None:
                st.caption("Sem dado pra exibir dessa consulta.")
            else:
                if resultado.get("aviso_consolidado_parcial"):
                    st.warning(resultado["aviso_consolidado_parcial"])
                st.markdown(f"**{prep['titulo']}**" + (f" — {prep['subtitulo']}" if prep["subtitulo"] else ""))
                _modos = ["Tabela"] + (["Gráfico"] if prep["graficos"] else []) + ["Relatório"]
                modo = st.radio("Ver como", _modos, horizontal=True, key="assistente_visao", label_visibility="collapsed")
                _graf_sel = tipo_graf = ys_sel = None
                if modo == "Tabela":
                    st.dataframe(chat_visual.tabela_exibicao(prep), hide_index=True, width="stretch")
                if modo in ("Gráfico", "Relatório") and prep["graficos"]:
                    _nomes_g = list(prep["graficos"])
                    cg1, cg2 = st.columns([3, 2])
                    nome_g = cg1.selectbox("Gráfico", _nomes_g, key="assistente_graf_nome") if len(_nomes_g) > 1 else _nomes_g[0]
                    _graf_sel = prep["graficos"][nome_g]
                    tipo_graf = cg2.selectbox("Tipo", chat_visual.TIPOS_GRAFICO, key="assistente_graf_tipo")
                    _opc_y = [c for c in _graf_sel["df"].columns if c != _graf_sel["x"]]
                    if len(_opc_y) > 1:
                        ys_sel = st.multiselect("Séries", _opc_y, default=_graf_sel["ys"], key=f"assistente_graf_ys_{nome_g}",
                                                max_selections=3) or _graf_sel["ys"]
                    if modo == "Gráfico":
                        st.plotly_chart(chat_visual.figura(_graf_sel, tipo_graf, True, ys_sel),
                                        width="stretch", config={"displaylogo": False})
                        st.caption("Gráfico direto do resultado consultado — passe o mouse para ver o valor completo em R$.")
                if modo == "Relatório":
                    st.caption("Baixe o resultado como planilha (números reais, abre no Excel) ou PDF (com o gráfico escolhido acima).")
                    _origem = f"Erik.AI · {ultima['nome']}"
                    _slug = re.sub(r"[^a-z0-9]+", "_", prep["titulo"].lower()).strip("_")[:40] or "consulta"
                    d1, d2 = st.columns(2)
                    d1.download_button("⬇️ Excel (.xlsx)", chat_visual.excel_bytes(prep, usuario, _origem),
                                       file_name=f"erik_{_slug}.xlsx", key="assistente_dl_xlsx",
                                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    d2.download_button("⬇️ PDF", chat_visual.pdf_bytes(prep, usuario, _origem, _graf_sel, tipo_graf or "Barras", ys_sel),
                                       file_name=f"erik_{_slug}.pdf", key="assistente_dl_pdf", mime="application/pdf")
        st.divider()

    st.markdown("**Consulta rápida**")
    nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
    idx_empresa = st.selectbox(
        "Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes_emp[i], key="assistente_empresa_sel",
    )
    cod_empresa, nome_empresa, _cnpj_empresa = EMPRESAS_FIXAS[idx_empresa]
    tipo_sel = st.radio("Tipo", ["BP", "DRE"], key="assistente_tipo_sel", horizontal=True)

    granularidade_rapida = None
    resultado_rapido = consultas_chat.consultar_bp_dre(conn, cod_empresa, tipo_sel)
    if resultado_rapido.get("ambiguo"):
        # Fase 3.1 (29/09/2026): esse mes/ano tem 2 documentos ativos (ex.
        # trimestral e semestral fechando na mesma data) -- essa "Consulta
        # rápida" nao passa pelo loop de esclarecimento do chat (chama
        # consultar_bp_dre direto), entao oferece o mesmo desempate aqui
        # via seletor, em vez de mostrar so' a mensagem de erro sem saida.
        opcoes_g = [o["granularidade"] for o in resultado_rapido["opcoes"]]
        granularidade_rapida = st.selectbox(
            "Este período tem mais de 1 documento ativo — escolha qual", opcoes_g,
            key="assistente_granularidade_ambigua",
        )
        resultado_rapido = consultas_chat.consultar_bp_dre(conn, cod_empresa, tipo_sel, None, granularidade_rapida)
    if "erro" in resultado_rapido:
        st.info(resultado_rapido["erro"])
    else:
        st.caption(f"{nome_empresa} · {resultado_rapido['periodo']} · {resultado_rapido['quantidade_contas']} conta(s)")
        df_rapida = pd.DataFrame(resultado_rapido["contas"])
        df_rapida["valor"] = df_rapida["valor"].apply(formatacao.moeda_br)
        st.dataframe(
            df_rapida,
            column_config={"valor": st.column_config.TextColumn("Valor")},
            hide_index=True, width="stretch",
        )

# ─────────────────────────────────────────────
#  COLUNA DIREITA — chat
# ─────────────────────────────────────────────
with col_chat:
    st.subheader("Chat")

    _mem = chat_memoria.disponivel(conn)
    if _mem is False:
        st.caption("⚠️ Memória da conversa indisponível (rode o bloco 20 do schema.sql no Supabase) — o chat funciona, "
                   "mas não guarda o histórico entre sessões.")
    elif _mem:
        with st.expander(f"🧠 Memória: últimas {chat_memoria.JANELA_HISTORICO_CHAT} mensagens ficam salvas só para você"):
            st.caption("A conversa é guardada por usuário. Só essa janela volta ao abrir o chat e vai como contexto — "
                       "números nunca são reaproveitados do histórico: a Erik.AI reconsulta o sistema.")
            _conf = st.checkbox("Confirmo apagar meu histórico", key="assistente_apagar_conf")
            if st.button("Apagar meu histórico", disabled=not _conf, key="assistente_apagar_hist"):
                chat_memoria.apagar_historico(conn, usuario)
                st.session_state.assistente_mensagens = []
                st.session_state.assistente_resultados = []
                st.session_state.assistente_ultima_ferramenta = None
                st.rerun()

    client = _cliente_anthropic()
    if client is None:
        st.warning(
            "Secret `ANTHROPIC_API_KEY` não configurado ainda. Configure em "
            "Settings → Secrets (Streamlit Cloud) pra habilitar o chat — o "
            "dashboard ao lado funciona normalmente sem isso."
        )
        st.stop()

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
        pergunta_limpa = chat_egc.limpar_texto_entrada(pergunta)
        if len(pergunta) > chat_egc.MAX_CHARS_PERGUNTA:
            st.session_state["assistente_aviso"] = (
                f"Pergunta muito longa — usei só os primeiros {chat_egc.MAX_CHARS_PERGUNTA} caracteres. "
                "Se precisar, divida em perguntas menores."
            )
        if pergunta_limpa.strip():
            st.session_state.assistente_mensagens.append({"role": "user", "content": pergunta_limpa})
            chat_memoria.salvar_mensagem(conn, usuario, "user", pergunta_limpa)
        st.rerun()
    _aviso = st.session_state.pop("assistente_aviso", None)
    if _aviso:
        st.warning(_aviso)

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
                # detalhe tecnico (pode ter id de organizacao/trecho de requisicao) fica so' no log
                r = {"texto": "Não consegui responder agora (erro ao falar com o assistente). Tente de novo em instantes.", "ferramentas_usadas": []}
        st.session_state.assistente_mensagens.append({"role": "assistant", "content": r["texto"]})
        chat_memoria.salvar_mensagem(conn, usuario, "assistant", r["texto"], [f["nome"] for f in r["ferramentas_usadas"]] or None)
        if r["ferramentas_usadas"]:
            st.session_state.assistente_ultima_ferramenta = r["ferramentas_usadas"][-1]
            _res = st.session_state.setdefault("assistente_resultados", [])
            _res.extend(r["ferramentas_usadas"])
            del _res[:-8]  # painel guarda as 8 consultas mais recentes
        if r.get("propostas"):
            _pend = st.session_state.setdefault("assistente_propostas", [])
            _ids = {p["id"] for p in _pend}
            _pend.extend(p for p in r["propostas"] if p["id"] not in _ids)
            del _pend[:-chat_egc.MAX_PROPOSTAS_POR_RESPOSTA * 2]
        st.rerun()
