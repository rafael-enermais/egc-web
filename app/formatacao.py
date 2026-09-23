# -*- coding: utf-8 -*-
"""
Formatacao numerica no padrao brasileiro (ponto = milhar, virgula =
decimal) -- 23/09/2026, achado do Rafael revisando o relatorio ao vivo:
varias telas mostravam numero em estilo americano ("R$ 582681.25", sem
separador de milhar e com ponto decimal) porque column_config.NumberColumn
e f-strings puras do Python (f"{v:,.2f}") usam a convencao americana por
padrao, nao a brasileira.

Reaproveita validacoes.formatar_br (ja existia, usado em Importar PDF pro
aviso de Ativo != Passivo) em vez de duplicar a logica de conversao --
so' adiciona os wrappers que faltavam (moeda com prefixo R$, percentual,
numero com sufixo tipo "x") pras telas que precisavam e ainda usavam
formatacao americana.

Modulo puro (sem streamlit/db), mesma filosofia de indicadores.py/
visao_grupo.py -- so' formata texto pra exibicao, nao decide layout nem
calcula nada nao calculado antes.
"""
from __future__ import annotations

from typing import Optional

from validacoes import formatar_br


def _e_vazio(v) -> bool:
    if v is None:
        return True
    try:
        return v != v  # NaN (float ou numpy) e' o unico valor que nao e' igual a si mesmo
    except TypeError:
        return False


def _sinal(v: float, forcar_sinal: bool) -> str:
    if v < 0:
        return "-"
    if forcar_sinal:
        return "+"
    return ""


def moeda_br(v: Optional[float], vazio: str = "—", forcar_sinal: bool = False) -> str:
    """1234.5 -> "R$ 1.234,50"; -1234.5 -> "-R$ 1.234,50". None/NaN -> vazio.
    forcar_sinal=True poe "+" explicito em valor positivo (uso em delta de
    st.metric, ex. "+R$ 500,00")."""
    if _e_vazio(v):
        return vazio
    return f"{_sinal(v, forcar_sinal)}R$ {formatar_br(abs(v))}"


def pct_br(v: Optional[float], vazio: str = "—", forcar_sinal: bool = False, casas: int = 1) -> str:
    """0.949 -> "94,9%" (1 casa decimal por padrao, mesmo padrao ja usado
    nos KPIs de indicadores.py/app.py). None/NaN -> vazio."""
    if _e_vazio(v):
        return vazio
    texto = f"{abs(v) * 100:.{casas}f}".replace(".", ",")
    return f"{_sinal(v, forcar_sinal)}{texto}%"


def numero_br(v: Optional[float], sufixo: str = "", vazio: str = "—", forcar_sinal: bool = False) -> str:
    """1234.5 -> "1.234,50"; com sufixo="x" -> "1.234,50x" (ex. Liquidez
    Corrente). None/NaN -> vazio."""
    if _e_vazio(v):
        return vazio
    return f"{_sinal(v, forcar_sinal)}{formatar_br(abs(v))}{sufixo}"
