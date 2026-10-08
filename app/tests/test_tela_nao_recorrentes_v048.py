# -*- coding: utf-8 -*-
"""Teste de fumaca (Streamlit AppTest) da tela Nao Recorrentes (v0.48.0): sem tabelas -> aviso; com tabelas ->
renderiza sem excecao, mostra status e botao de confirmar. Nao mexe em banco (tudo mockado)."""
import sys
import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402
import conexao  # noqa: E402
import db  # noqa: E402
import nao_recorrentes as nr  # noqa: E402

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "9_Nao_Recorrentes.py")
PERIODO = datetime.date(2026, 6, 30)


def _base():
    return [
        patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"),
        patch.object(conexao, "get_conn", return_value=None),
        patch.object(db, "listar_periodos_detalhado", return_value=[{"periodo": PERIODO, "granularidade": "semestral"}]),
    ]


def _rodar(extra):
    ps = _base() + extra
    for p in ps:
        p.start()
    try:
        at = AppTest.from_file(PAGE, default_timeout=30)
        at.run()
        return at
    finally:
        for p in reversed(ps):
            p.stop()


def test_sem_tabelas_mostra_aviso_do_bloco_25():
    at = _rodar([patch.object(nr, "tabelas_existem", return_value=False)])
    assert not at.exception
    assert any("bloco 25" in w.value for w in at.warning)


def test_com_tabelas_lista_vazia_nao_confirmada():
    at = _rodar([
        patch.object(nr, "tabelas_existem", return_value=True),
        patch.object(nr, "listar_itens", return_value=[]),
        patch.object(nr, "listar_historico", return_value=[]),
        patch.object(nr, "status_confirmacao", return_value={"status": "nao_confirmado", "itens": 0, "total": 0.0,
                                                                "confirmado_por": None, "confirmado_em": None}),
    ])
    assert not at.exception, [e.value for e in at.exception]
    assert any("não confirmada" in i.value for i in at.info)
    assert any("não há itens" in b.label for b in at.button)


def test_com_item_confirmado_mostra_sucesso():
    item = dict(id=1, categoria="Jurídico pontual", descricao="Acordo", valor=1000.0, sinal=1, justificativa="x",
                documento=None, sugerido_regra=False, contas_dre=None, criado_por="a", atualizado_por=None, ativo=True)
    at = _rodar([
        patch.object(nr, "tabelas_existem", return_value=True),
        patch.object(nr, "listar_itens", return_value=[item]),
        patch.object(nr, "listar_historico", return_value=[]),
        patch.object(nr, "status_confirmacao", return_value={"status": "confirmado", "itens": 1, "total": 1000.0,
                                                                "confirmado_por": "a", "confirmado_em": datetime.datetime(2026, 10, 7, 10, 0)}),
    ])
    assert not at.exception, [e.value for e in at.exception]
    assert any("confirmada" in s.value for s in at.success)


# ---------------------------------------------------------------- v0.48.2: chave "desativar" o EBITDA Ajustado
def _com_tabelas(extra):
    return _rodar([
        patch.object(nr, "tabelas_existem", return_value=True),
        patch.object(nr, "listar_itens", return_value=[]),
        patch.object(nr, "listar_historico", return_value=[]),
        patch.object(nr, "status_confirmacao", return_value={"status": "nao_confirmado", "itens": 0, "total": 0.0,
                                                                "confirmado_por": None, "confirmado_em": None}),
    ] + extra)


def test_chave_desativada_mostra_aviso_e_toggle_desligado():
    at = _com_tabelas([patch.object(nr, "config_existe", return_value=True),
                       patch.object(nr, "ebitda_ajustado_ativo", return_value=False)])
    assert not at.exception, [e.value for e in at.exception]
    assert any("DESATIVADO" in e.value for e in at.error)
    assert at.toggle(key="nr_chave_ativo").value is False


def test_chave_ativa_mostra_selo_ativo():
    at = _com_tabelas([patch.object(nr, "config_existe", return_value=True),
                       patch.object(nr, "ebitda_ajustado_ativo", return_value=True)])
    assert not at.exception, [e.value for e in at.exception]
    assert any("ATIVO" in s.value and "DESATIVADO" not in s.value for s in at.success)
    assert at.toggle(key="nr_chave_ativo").value is True


def test_ligar_o_toggle_grava_a_chave():
    gravou = []
    # o "banco" lembra o que foi gravado (senao o st.rerun() da tela entraria em loop com o mock fixo)
    ps = [patch.object(nr, "config_existe", return_value=True),
          patch.object(nr, "ebitda_ajustado_ativo", side_effect=lambda c: bool(gravou and gravou[-1])),
          patch.object(nr, "definir_ebitda_ajustado_ativo", side_effect=lambda c, a, usuario=None: gravou.append(a))]
    ps_all = _base() + [
        patch.object(nr, "tabelas_existem", return_value=True),
        patch.object(nr, "listar_itens", return_value=[]),
        patch.object(nr, "listar_historico", return_value=[]),
        patch.object(nr, "status_confirmacao", return_value={"status": "nao_confirmado", "itens": 0, "total": 0.0,
                                                                "confirmado_por": None, "confirmado_em": None}),
    ] + ps
    for p in ps_all:
        p.start()
    try:
        at = AppTest.from_file(PAGE, default_timeout=30)
        at.run()
        at.toggle(key="nr_chave_ativo").set_value(True).run()
    finally:
        for p in reversed(ps_all):
            p.stop()
    assert gravou == [True]


def test_sem_tabela_de_config_avisa_para_rodar_o_bloco_25_de_novo():
    at = _com_tabelas([patch.object(nr, "config_existe", return_value=False)])
    assert not at.exception, [e.value for e in at.exception]
    assert any("ativar/desativar" in w.value for w in at.warning)
