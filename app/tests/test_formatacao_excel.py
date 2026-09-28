# -*- coding: utf-8 -*-
"""
Teste de regressao do bug "ValueError: Excel does not support datetimes
with timezones" na tela Notas Fiscais (28/09/2026, achado pelo Rafael
clicando em "Rodar conferência" -> "Baixar planilha de pendências" no app
publicado). Reproduz o cenario real -- DataFrame com uma coluna
timestamptz-like (datetime tz-aware, como psycopg2 devolve de
egc.nf_conciliacao.atualizado_em) -- e confirma que:
1) SEM o helper, df.to_excel(...) quebra (prova que o bug e' real, nao
   suposicao);
2) COM formatacao.remover_timezone_para_excel(df), o mesmo df.to_excel(...)
   funciona, escreve normalmente e os outros valores (nao-datetime) nao
   mudam.

Rodar: python3 tests/test_formatacao_excel.py
"""
import io
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import formatacao  # noqa: E402

_TZ = dt.timezone(dt.timedelta(hours=-3))  # America/Sao_Paulo, sem DST atualmente


def _df_com_tz():
    return pd.DataFrame({
        "numero_nota": ["123", "456"],
        "valor": [1000.50, 2500.00],
        # tz-aware -- o mesmo shape que psycopg2 devolve pra coluna
        # timestamptz (egc.nf_conciliacao.atualizado_em).
        "atualizado_em": [
            dt.datetime(2026, 9, 28, 10, 30, 0, tzinfo=_TZ),
            dt.datetime(2026, 9, 27, 9, 0, 0, tzinfo=_TZ),
        ],
        "sienge_bill_id": [None, None],  # coluna com None, tal como no bug reportado
    })


def test_to_excel_direto_quebra_com_coluna_tz_aware():
    df = _df_com_tz()
    buffer = io.BytesIO()
    try:
        df.to_excel(buffer, index=False)
    except ValueError as exc:
        assert "timezone" in str(exc).lower()
        print("OK: reproduzido o bug real -- to_excel direto quebra com ValueError de timezone")
    else:
        raise AssertionError(
            "Esperava ValueError (timezone) -- se o pandas/openpyxl instalado nao quebra mais "
            "nesse caso, o teste de regressao abaixo ainda garante que o helper e' seguro."
        )


def test_remover_timezone_para_excel_corrige_e_preserva_resto():
    df = _df_com_tz()
    limpo = formatacao.remover_timezone_para_excel(df)

    assert limpo["atualizado_em"].dt.tz is None
    # so' removeu o timezone, nao deslocou o horario (10:30 continua 10:30)
    assert limpo["atualizado_em"].iloc[0].hour == 10 and limpo["atualizado_em"].iloc[0].minute == 30

    # demais colunas inalteradas
    assert limpo["numero_nota"].tolist() == df["numero_nota"].tolist()
    assert limpo["valor"].tolist() == df["valor"].tolist()
    assert limpo["sienge_bill_id"].isna().all()

    buffer = io.BytesIO()
    limpo.to_excel(buffer, index=False, sheet_name="Pendencias")
    assert buffer.tell() > 0
    print("OK: remover_timezone_para_excel tira o timezone (sem deslocar hora) e to_excel funciona")


def test_remover_timezone_para_excel_com_dtype_objeto():
    # variante: coluna fica dtype "object" guardando datetime.datetime
    # cru (pandas nem sempre infere datetime64[ns, tz] sozinho) --
    # confirma que o helper cobre esse caso tambem, nao so' o dtype nativo.
    df = pd.DataFrame({"x": pd.Series([dt.datetime(2026, 9, 28, 10, 30, tzinfo=_TZ)], dtype="object")})
    limpo = formatacao.remover_timezone_para_excel(df)
    assert limpo["x"].iloc[0].tzinfo is None
    buffer = io.BytesIO()
    limpo.to_excel(buffer, index=False)
    print("OK: remover_timezone_para_excel cobre tambem coluna dtype objeto com datetime cru")


if __name__ == "__main__":
    test_to_excel_direto_quebra_com_coluna_tz_aware()
    test_remover_timezone_para_excel_corrige_e_preserva_resto()
    test_remover_timezone_para_excel_com_dtype_objeto()
