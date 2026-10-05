# -*- coding: utf-8 -*-
"""v0.47.1: requirements com versoes FIXAS e nenhum uso de parametro removido do Streamlit."""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent


def test_requirements_tem_versao_fixa():
    linhas = [l.strip() for l in (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines()
              if l.strip() and not l.strip().startswith("#")]
    assert linhas
    soltas = [l for l in linhas if not re.fullmatch(r"[A-Za-z0-9_.\-]+==\d[\w.]*", l)]
    assert not soltas, f"fixe a versao (==): {soltas}"
    assert any(l.startswith("streamlit==") for l in linhas)


def test_nenhum_use_container_width_no_app():
    """Removido do Streamlit (aviso 'will be removed after 2025-12-31'): usar width='stretch'/'content'."""
    achados = []
    for p in (RAIZ / "app").rglob("*.py"):
        if "tests" in p.parts:
            continue
        for n, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "use_container_width" in l:
                achados.append(f"{p.name}:{n}")
    assert not achados, achados
