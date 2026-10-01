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

import datetime as _dt
from typing import Optional

import pandas as pd

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


_TZ_BR = _dt.timezone(_dt.timedelta(hours=-3))  # America/Sao_Paulo, sem horario de verao desde 2019


def hora_br(v, vazio: str = "—", formato: str = "%d/%m/%Y %H:%M") -> str:
    """Formata um datetime pro horario de Brasilia antes de exibir.

    FIX_20260928 (achado pelo Rafael: "última sincronização" na tela
    Notas Fiscais aparecia ~3h a frente do relogio dele -- "deve estar
    pegando GMT diferente"). Causa real: conexao.py NAO seta timezone de
    sessao (sem SET TIME ZONE), entao o Supabase/Postgres devolve todo
    timestamptz em UTC -- psycopg2 entrega um datetime tz-aware em UTC,
    e o .strftime() direto (sem converter) simplesmente imprime esses
    numeros UTC como se fossem hora local. Mesmo padrao em 3 lugares
    (Notas Fiscais "última sincronização", Relatório Comentado "última
    geração", Importar PDF "Importações recentes") -- corrigido nos 3
    ao mesmo tempo, com essa funcao unica em vez de 3 fixes separados.

    So' converte quando o datetime chega tz-aware (timestamptz de
    verdade) -- um datetime naive (sem tzinfo) e' exibido como veio, sem
    deslocar, pra nao inventar fuso em algo que nunca teve um.
    """
    if v is None:
        return vazio
    if getattr(v, "tzinfo", None) is not None:
        v = v.astimezone(_TZ_BR)
    return v.strftime(formato)


def remover_timezone_para_excel(df: pd.DataFrame) -> pd.DataFrame:
    """Devolve uma copia de df com toda coluna datetime tz-aware
    convertida pra tz-naive (so' tira o timezone, nao desloca a hora --
    valores ja vem do banco convertidos pro horario certo).

    FIX_20260928 (Rafael, "Rodar conferência" em Notas Fiscais quebrando
    com ValueError ao clicar em "Baixar planilha de pendências"): coluna
    tipo `timestamptz` no Postgres (ex. egc.nf_conciliacao.atualizado_em)
    volta do psycopg2 como datetime.datetime com tzinfo -- openpyxl NAO
    aceita datetime com timezone num .xlsx e levanta
    "ValueError: Excel does not support datetimes with timezones."
    Cobre os 2 jeitos que a coluna pode chegar num DataFrame montado a
    partir de cur.fetchall(): dtype datetime64[ns, tz] (quando o pandas
    consegue inferir) OU dtype "object" guardando datetime.datetime cru
    (quando nao consegue) -- nao assume um dos 2, testa os dois.
    """
    saida = df.copy()
    for col in saida.columns:
        if pd.api.types.is_datetime64_any_dtype(saida[col]) and saida[col].dt.tz is not None:
            saida[col] = saida[col].dt.tz_localize(None)
        elif saida[col].dtype == "object":
            saida[col] = saida[col].apply(
                lambda v: v.replace(tzinfo=None) if isinstance(v, _dt.datetime) and v.tzinfo is not None else v
            )
    return saida


def moeda_curta(v: Optional[float], vazio: str = "—") -> str:
    """Valor em R$ abreviado pra rotulo de grafico/card: 'R$ 39,8 mi',
    'R$ 850 mil', 'R$ 1,2 bi', 'R$ 320' (padrao BR: virgula decimal).
    Negativo leva '-' antes do R$. NaN/None -> `vazio`."""
    if _e_vazio(v):
        return vazio
    n = float(v)
    sinal = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e9:
        txt, suf = f"{a / 1e9:.1f}", " bi"
    elif a >= 1e6:
        txt, suf = f"{a / 1e6:.1f}", " mi"
    elif a >= 1e3:
        txt, suf = f"{a / 1e3:.0f}", " mil"
    else:
        txt, suf = f"{a:.0f}", ""
    return f"{sinal}R$ {txt.replace('.', ',')}{suf}"
