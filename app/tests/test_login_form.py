# -*- coding: utf-8 -*-
"""v0.46.4 -- login em st.form (ENTER envia) e mensagens de erro."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402

SCRIPT = """
import auth
auth.require_login()
import streamlit as st
st.write("DENTRO")
"""


def _cliente(ok=True):
    c = MagicMock()
    if ok:
        c.auth.sign_in_with_password.return_value = SimpleNamespace(session=SimpleNamespace(user=SimpleNamespace(email="a@b.com")))
    else:
        c.auth.sign_in_with_password.side_effect = Exception("credenciais")
    return c


def test_login_usa_form_e_entra_ao_enviar():
    c = _cliente()
    with patch.object(auth, "_auth_client", return_value=c):
        at = AppTest.from_string(SCRIPT)
        at.run(timeout=20)
        assert not at.exception
        assert len(at.button) == 1 and at.button[0].label == "Entrar"      # botao de form (submit com ENTER)
        at.text_input[0].set_value("  a@b.com ")
        at.text_input[1].set_value("segredo")
        at.button[0].click().run(timeout=20)
        assert not at.exception
        c.auth.sign_in_with_password.assert_called_once_with({"email": "a@b.com", "password": "segredo"})
        assert any(m.value == "DENTRO" for m in at.markdown)


def test_login_invalido_e_campos_vazios_mostram_erro_sem_entrar():
    with patch.object(auth, "_auth_client", return_value=_cliente(ok=False)):
        at = AppTest.from_string(SCRIPT)
        at.run(timeout=20)
        at.button[0].click().run(timeout=20)
        assert any("Informe o e-mail" in e.value for e in at.error)
        at.text_input[0].set_value("a@b.com"); at.text_input[1].set_value("x")
        at.button[0].click().run(timeout=20)
        assert any("Login inválido" in e.value for e in at.error)
        assert not any(m.value == "DENTRO" for m in at.markdown)
