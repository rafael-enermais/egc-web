# -*- coding: utf-8 -*-
"""
Teste de integracao (Streamlit AppTest) da pagina Arquivar/Recuperar
(app/telas/3_Arquivar_Recuperar.py) -- nao existia nenhum teste desta
pagina ate' agora. Criado junto com o log completo (task #16, 22/09/2026)
porque essa tela ganhou try/except + log em torno de arquivar_periodo/
recuperar_periodo que antes nao existiam (uma falha no banco quebrava a
tela sem rastro nenhum).

Cobre: carga normal (sem excecao); Arquivar com sucesso (chama
db.arquivar_periodo, sem tocar em registrar_evento); Arquivar com falha
parcial (1 dos 2 periodos falha) -- confirma que NAO propaga excecao pra
fora (a tela continua de pe', mostra erro pro periodo que falhou) e que
db.registrar_evento e' chamado com nivel='ERRO' pro periodo que falhou.

Rodar: python3 tests/test_arquivar_recuperar_app.py
"""
import sys
import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402
import conexao  # noqa: E402
import db  # noqa: E402

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "3_Arquivar_Recuperar.py")

P1 = datetime.date(2026, 5, 31)
P2 = datetime.date(2026, 6, 30)


def test_carrega_sem_excecao_com_periodos_ativos_e_arquivados():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", side_effect=lambda conn, cod, status="ATIVO": (
             [P2, P1] if status == "ATIVO" else [datetime.date(2026, 3, 31)]
         )):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao carregando Arquivar/Recuperar: {at.exception}"
        print("OK: Arquivar/Recuperar — carrega sem exceção com períodos ativos e arquivados")


def test_arquivar_sucesso_nao_registra_erro():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", side_effect=lambda conn, cod, status="ATIVO": (
             [P2, P1] if status == "ATIVO" else []
         )), \
         patch.object(db, "arquivar_periodo", return_value=5) as m_arq, \
         patch.object(db, "registrar_evento") as m_log:

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.multiselect(key="arquivar_sel").set_value([P2])
        at.run(timeout=30)
        botao_arquivar = next(b for b in at.button if "Arquivar selecionado" in b.label)
        botao_arquivar.click().run(timeout=30)

        # Nota: a página termina o clique com st.rerun() (padrão já existente,
        # não alterado aqui) -- a mensagem de sucesso "flasheia" e some no
        # rerun seguinte, então não dá pra afirmar sobre at.success aqui (o
        # AppTest só expõe o estado do ÚLTIMO run). O que importa pra este
        # teste é o comportamento: chamou o banco certo e logou nível INFO.
        assert not at.exception, f"excecao ao arquivar: {at.exception}"
        assert m_arq.call_count == 1, "esperava 1 chamada a db.arquivar_periodo (1 período selecionado)"
        assert m_log.call_count == 1, "esperava 1 chamada a registrar_evento (nível INFO, log completo cobre sucesso também)"
        assert m_log.call_args[0][2] == "INFO", f"esperava nivel='INFO' no sucesso, veio {m_log.call_args[0][2]!r}"
        print("OK: Arquivar com sucesso — loga nível INFO (log completo), chama arquivar_periodo certo")


def test_arquivar_falha_parcial_loga_erro_e_nao_quebra_pagina():
    def _arquivar_com_falha(conn, cod, periodo):
        if periodo == P1:
            raise Exception("connection reset by peer")
        return 3

    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", side_effect=lambda conn, cod, status="ATIVO": (
             [P2, P1] if status == "ATIVO" else []
         )), \
         patch.object(db, "arquivar_periodo", side_effect=_arquivar_com_falha), \
         patch.object(db, "registrar_evento") as m_log:

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.multiselect(key="arquivar_sel").set_value([P1, P2])
        at.run(timeout=30)
        botao_arquivar = next(b for b in at.button if "Arquivar selecionado" in b.label)
        botao_arquivar.click().run(timeout=30)

        assert not at.exception, (
            f"falha em 1 período NÃO deveria virar exceção não tratada na página: {at.exception}"
        )
        # registrar_evento(conn, origem, nivel, mensagem, ...) -- nivel é o 3º posicional.
        # 2 chamadas esperadas: 1 ERRO (o período que falhou) + 1 INFO (o que teve sucesso).
        niveis = [c[0][2] for c in m_log.call_args_list]
        assert m_log.call_count == 2, f"esperava 2 chamadas a registrar_evento (1 ERRO + 1 INFO), veio {niveis}"
        assert "ERRO" in niveis, f"esperava 1 chamada com nivel='ERRO', veio {niveis}"
        assert "INFO" in niveis, f"esperava 1 chamada com nivel='INFO' (o período que teve sucesso), veio {niveis}"
        # mesma nota do teste de sucesso -- st.rerun() no fim do handler faz a
        # mensagem "flashear", então a checagem de comportamento (não quebrou +
        # logou os 2 níveis certos) é o que garante a cobertura real aqui.
        print("OK: Arquivar com falha parcial — não quebra a página, loga ERRO + INFO (log completo)")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in testes:
        try:
            t()
        except AssertionError as e:
            falhas += 1
            print(f"FALHOU: {t.__name__} — {e}")
        except Exception as e:
            falhas += 1
            print(f"ERRO (não AssertionError) em {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
