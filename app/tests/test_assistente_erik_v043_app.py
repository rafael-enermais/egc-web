# -*- coding: utf-8 -*-
"""AppTest da tela Erik.AI v0.43.0: cartao de aprovacao, memoria, visualizacao do resultado."""
import datetime
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

import acoes_chat  # noqa: E402
import auth  # noqa: E402
import chat_egc  # noqa: E402
import chat_memoria  # noqa: E402
import conexao  # noqa: E402
import db  # noqa: E402

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "6_Assistente.py")

PROPOSTA = {
    "id": "p0000001", "tipo": "ATUALIZAR_STATUS_PENDENCIA", "titulo": "Atualizar status de pendência de nota fiscal",
    "motivo": "contadora pediu", "criada_em": "2026-10-01T10:00:00",
    "parametros": {"origem": "MANIFESTO", "registro_id": 7, "novo_status": "RESOLVIDO"},
    "resumo": [("Empresa", "ENERGIA"), ("Nota", "500"), ("Valor", 100.0), ("Status atual", "PENDENTE"), ("Novo status", "RESOLVIDO")],
    "editaveis": {"novo_status": ["PENDENTE", "ENVIADO_SUPRIMENTOS", "RESOLVIDO", "DESCARTADO"]}, "reversivel": True,
}
CONTAS = {"tipo": "BP", "empresa": "SMG", "periodo": "2026-06", "granularidade": "trimestral", "quantidade_contas": 2, "contas": [
    {"grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": 1000.5}, {"grupo": "PASSIVO CIRCULANTE", "conta": "FORN", "valor": 300.0}]}


def _base():
    return [patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"),
            patch.object(conexao, "get_conn", return_value=None),
            patch.object(db, "listar_periodos_detalhado", return_value=[]),
            patch.object(chat_memoria, "salvar_mensagem", return_value=True)]


def _com(patches, fn):
    for p in patches:
        p.start()
    try:
        return fn()
    finally:
        for p in reversed(patches):
            p.stop()


def _app():
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.secrets["ANTHROPIC_API_KEY"] = "fake"
    return at


def test_proposta_aparece_aprovar_executa_e_some():
    resp = {"texto": "Preparei a alteração; aguarda aprovação.", "ferramentas_usadas": [], "propostas": [dict(PROPOSTA)]}
    ps = _base() + [patch("anthropic.Anthropic", return_value=SimpleNamespace()),
                    patch.object(chat_egc, "responder", return_value=resp),
                    patch.object(acoes_chat, "executar_proposta", return_value=(True, "Pendência atualizada para RESOLVIDO.")),
                    patch.object(acoes_chat, "registrar_rejeicao")]

    def fluxo():
        at = _app()
        at.run()
        at.chat_input[0].set_value("marque a nota 500 como resolvida").run()
        assert not at.exception, at.exception
        assert any("Aguardando sua aprovação" in m.value for m in at.markdown)
        assert len(at.session_state["assistente_propostas"]) == 1
        assert {b.label for b in at.button} >= {"✅ Aprovar e executar", "Rejeitar"}
        # a pessoa ajusta o status no cartao antes de aprovar
        at.selectbox(key="acao_status_p0000001").set_value("DESCARTADO")
        next(b for b in at.button if b.label.startswith("✅")).click().run()
        assert not at.exception, at.exception
        chamada = acoes_chat.executar_proposta.call_args
        assert chamada.kwargs["edicoes"] == {"novo_status": "DESCARTADO"}
        assert at.session_state["assistente_propostas"] == []
        assert any("Pendência atualizada" in s.value for s in at.success)
        assert "Ação aprovada e executada" in at.session_state["assistente_mensagens"][-1]["content"]

    _com(ps, fluxo)


def test_rejeitar_registra_e_nao_executa():
    resp = {"texto": "ok", "ferramentas_usadas": [], "propostas": [dict(PROPOSTA)]}
    ps = _base() + [patch("anthropic.Anthropic", return_value=SimpleNamespace()),
                    patch.object(chat_egc, "responder", return_value=resp),
                    patch.object(acoes_chat, "executar_proposta"), patch.object(acoes_chat, "registrar_rejeicao")]

    def fluxo():
        at = _app()
        at.run()
        at.chat_input[0].set_value("resolva a nota 500").run()
        next(b for b in at.button if b.label == "Rejeitar").click().run()
        assert not at.exception, at.exception
        assert acoes_chat.registrar_rejeicao.called and not acoes_chat.executar_proposta.called
        assert at.session_state["assistente_propostas"] == []

    _com(ps, fluxo)


def test_memoria_carregada_ao_abrir_e_mensagens_salvas():
    hist = [{"role": "user", "content": "pergunta de ontem"}, {"role": "assistant", "content": "resposta de ontem"}]
    resp = {"texto": "nova resposta", "ferramentas_usadas": [], "propostas": []}
    ps = _base()[:3] + [patch.object(chat_memoria, "carregar_historico", return_value=hist),
                        patch.object(chat_memoria, "salvar_mensagem", return_value=True),
                        patch("anthropic.Anthropic", return_value=SimpleNamespace()),
                        patch.object(chat_egc, "responder", return_value=resp)]

    def fluxo():
        at = _app()
        at.run()
        assert not at.exception, at.exception
        assert [m["content"] for m in at.session_state["assistente_mensagens"]] == ["pergunta de ontem", "resposta de ontem"]
        at.chat_input[0].set_value("e hoje?").run()
        papeis = [c.args[2] for c in chat_memoria.salvar_mensagem.call_args_list]
        assert papeis == ["user", "assistant"]  # ambas gravadas
        assert len(at.session_state["assistente_mensagens"]) == 4

    _com(ps, fluxo)


def test_painel_tabela_grafico_relatorio_sem_excecao():
    resp = {"texto": "BP da SMG", "propostas": [],
            "ferramentas_usadas": [{"nome": "consultar_bp_dre", "entrada": {}, "resultado": CONTAS}]}
    ps = _base() + [patch("anthropic.Anthropic", return_value=SimpleNamespace()), patch.object(chat_egc, "responder", return_value=resp)]

    def fluxo():
        at = _app()
        at.run()
        at.chat_input[0].set_value("bp da smg").run()
        assert not at.exception, at.exception
        assert at.radio(key="assistente_visao").options == ["Tabela", "Gráfico", "Relatório"]
        assert any("CLIENTES" in list(df.value.get("Conta", [])) for df in at.dataframe)
        for modo in ("Gráfico", "Relatório", "Tabela"):
            at.radio(key="assistente_visao").set_value(modo).run()
            assert not at.exception, (modo, at.exception)
        at.radio(key="assistente_visao").set_value("Gráfico").run()
        at.selectbox(key="assistente_graf_tipo").set_value("Linhas").run()
        assert not at.exception, at.exception
        at.selectbox(key="assistente_graf_tipo").set_value("Barras horizontais").run()
        assert not at.exception, at.exception

    _com(ps, fluxo)
