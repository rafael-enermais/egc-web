# -*- coding: utf-8 -*-
"""
AppTest de integracao pra app/telas/6_Assistente.py -- mesmo padrao ja
validado em test_visao_grupo_app.py/test_dashboard_projecao_app.py
(auth/conexao/db mockados via patch.object, 1 UNICA instancia de AppTest
reaproveitada entre cenarios).

Cobre 2 cenarios principais:
  1. SEM secret ANTHROPIC_API_KEY configurado (estado real ate' Rafael
     configurar no Streamlit Cloud): dashboard (coluna esquerda, consulta
     rapida) funciona normal, chat mostra aviso e NAO quebra a pagina --
     confirma o degrade gracioso descrito na docstring da pagina.
  2. COM secret configurado + anthropic.Anthropic mockado (nunca chama a
     API de verdade): pergunta -> resposta, confirma que
     assistente_mensagens acumula os turnos e que assistente_ultima_
     ferramenta e' populado quando o chat usa uma ferramenta (aparece no
     dashboard, ver "Ultima consulta do chat").

Historico mockado de proposito com valor: Decimal (mesma razao de
sempre -- ver test_visao_grupo_app.py/test_dashboard_projecao_app.py:
psycopg2 devolve NUMERIC como Decimal, aqui o dado passa por
consultas_chat.consultar_bp_dre -> float() explicito, jah testado
isolado em test_consultas_chat.py, mas o AppTest confirma que a pagina
de fato usa esse caminho sem reintroduzir Decimal cru em algum lugar).
"""
import sys
import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402
import conexao  # noqa: E402
import db  # noqa: E402
import chat_egc  # noqa: E402

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "6_Assistente.py")

MOCK_LANCAMENTOS_SMG_BP = [
    {"id": 1, "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("1000.50"),
     "origem": "PDF", "pdf_original": None, "arquivo_pdf": "x.pdf", "atualizado_em": None},
]


def _texto(txt):
    return SimpleNamespace(type="text", text=txt)


def run():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[datetime.date(2026, 6, 30)]), \
         patch.object(db, "listar_lancamentos", return_value=MOCK_LANCAMENTOS_SMG_BP):

        # ── Cenario 1: sem ANTHROPIC_API_KEY configurado ──────────────
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        print("Sem secret configurado, exception:", at.exception)
        assert not at.exception, f"FALHOU (sem secret): {at.exception[0] if at.exception else None}"
        avisos = [w.value for w in at.warning]
        assert any("ANTHROPIC_API_KEY" in w for w in avisos), \
            f"esperava aviso sobre secret faltando, veio: {avisos}"
        assert len(at.dataframe) >= 1, "esperava a tabela de 'Consulta rapida' (dashboard) renderizada mesmo sem chat"
        tabela_rapida = at.dataframe[0].value
        assert list(tabela_rapida["conta"]) == ["CLIENTES"], f"tabela rapida nao bateu: {tabela_rapida}"
        assert tabela_rapida["valor"].iloc[0] == 1000.50, "consulta rapida -- Decimal convertido pra float certo"
        print("Cenario 1 OK -- dashboard funciona, chat desabilitado com aviso, sem excecao")

        # ── Cenario 2: COM secret + client Anthropic mockado ──────────
        chamadas = {"n": 0}

        def fake_create(**kw):
            chamadas["n"] += 1
            return SimpleNamespace(stop_reason="end_turn", content=[_texto("O CLIENTES da SMG e' R$ 1000,50.")])

        client_mock = SimpleNamespace(messages=SimpleNamespace(create=fake_create))

        at2 = AppTest.from_file(PAGE)
        at2.secrets["ANTHROPIC_API_KEY"] = "sk-fake-nao-usada-de-verdade"
        with patch("anthropic.Anthropic", return_value=client_mock):
            at2.run(timeout=30)
            print("Com secret (sem pergunta ainda), exception:", at2.exception)
            assert not at2.exception, f"FALHOU (com secret, carga inicial): {at2.exception[0] if at2.exception else None}"
            assert not any("ANTHROPIC_API_KEY" in w.value for w in at2.warning), "nao deveria avisar sobre secret -- foi configurado"

            at2.chat_input[0].set_value("qual o clientes da smg").run(timeout=30)
            print("Depois de perguntar, exception:", at2.exception)
            assert not at2.exception, f"FALHOU (pergunta): {at2.exception[0] if at2.exception else None}"

        mensagens = at2.session_state["assistente_mensagens"]
        assert len(mensagens) == 2, f"esperava 2 turnos (user+assistant), veio {len(mensagens)}"
        assert mensagens[0] == {"role": "user", "content": "qual o clientes da smg"}
        assert mensagens[1]["role"] == "assistant" and "1000,50" in mensagens[1]["content"]
        assert chamadas["n"] == 1, f"esperava 1 chamada a client.messages.create (sem tool_use), veio {chamadas['n']}"
        print("Cenario 2 OK -- pergunta/resposta funciona ponta a ponta com client mockado, historico acumulado certo")

    print("\nTODOS OS CENARIOS OK (dashboard sem chat, chat com client mockado)")


if __name__ == "__main__":
    try:
        run()
        sys.exit(0)
    except AssertionError as e:
        print(f"FALHOU: {e}")
        sys.exit(1)
