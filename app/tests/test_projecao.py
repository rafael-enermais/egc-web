# -*- coding: utf-8 -*-
"""
Testes do motor de projecao (app/projecao.py) -- modulo puro, sem banco
nem Streamlit, com dado SINTETICO cobrindo os 3 niveis de metodo:
  - < 4 periodos de historico -> flat_ultimo_valor
  - 4-23 periodos -> tendencia_linear
  - 24+ periodos (2 anos) -> tendencia_sazonal

Rodar: python3 tests/test_projecao.py
"""
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import projecao  # noqa: E402


def _periodo(ano: int, mes: int) -> datetime.date:
    import calendar
    return datetime.date(ano, mes, calendar.monthrange(ano, mes)[1])


def test_proximo_periodo_mensal_vira_ano():
    assert projecao.proximo_periodo_mensal(_periodo(2026, 12)) == _periodo(2027, 1)
    assert projecao.proximo_periodo_mensal(_periodo(2026, 6)) == _periodo(2026, 7)
    print("OK: proximo_periodo_mensal (inclusive virada de ano)")


def test_gerar_periodos_futuros_horizonte():
    futuros = projecao.gerar_periodos_futuros(_periodo(2026, 6), 3)
    assert futuros == [_periodo(2026, 7), _periodo(2026, 8), _periodo(2026, 9)]
    print("OK: gerar_periodos_futuros")


def test_gerar_baseline_vazio_sem_historico():
    assert projecao.gerar_baseline([], [_periodo(2026, 7)]) == []
    print("OK: gerar_baseline — historico vazio devolve lista vazia")


def test_gerar_baseline_flat_com_1_periodo():
    # exatamente a realidade do banco real hoje (22/09/2026): 1 periodo so
    historico = [(_periodo(2026, 6), 1000.0)]
    futuros = projecao.gerar_periodos_futuros(_periodo(2026, 6), 2)
    resultado = projecao.gerar_baseline(historico, futuros)
    assert len(resultado) == 2
    assert all(r["metodo"] == "flat_ultimo_valor" for r in resultado)
    assert all(r["valor_base"] == 1000.0 for r in resultado)
    assert all(r["periodos_historico"] == 1 for r in resultado)
    print("OK: gerar_baseline — flat_ultimo_valor com 1 periodo (caso real hoje)")


def test_gerar_baseline_flat_com_3_periodos():
    historico = [(_periodo(2026, m), 100.0 * m) for m in (4, 5, 6)]
    futuros = projecao.gerar_periodos_futuros(_periodo(2026, 6), 1)
    resultado = projecao.gerar_baseline(historico, futuros)
    assert resultado[0]["metodo"] == "flat_ultimo_valor"
    assert resultado[0]["valor_base"] == 600.0  # ultimo valor (mes 6)
    print("OK: gerar_baseline — flat_ultimo_valor com 3 periodos (ainda abaixo do limite)")


def test_gerar_baseline_tendencia_linear_com_6_periodos():
    # serie perfeitamente linear: 100, 200, 300, 400, 500, 600 -> proximo = 700
    historico = [(_periodo(2026, m), 100.0 * (m - 0)) for m in range(1, 7)]
    futuros = projecao.gerar_periodos_futuros(_periodo(2026, 6), 2)
    resultado = projecao.gerar_baseline(historico, futuros)
    assert resultado[0]["metodo"] == "tendencia_linear"
    assert abs(resultado[0]["valor_base"] - 700.0) < 0.01
    assert abs(resultado[1]["valor_base"] - 800.0) < 0.01
    assert resultado[0]["periodos_historico"] == 6
    print("OK: gerar_baseline — tendencia_linear com serie perfeitamente linear (6 periodos)")


def test_gerar_baseline_tendencia_sazonal_com_24_periodos():
    # 2 anos de historico com tendencia crescente + pico sazonal em dezembro;
    # projeta dezembro do 3o ano e espera o pico refletido (nao so a tendencia crua)
    historico = []
    valor_base = 1000.0
    for ano_idx in range(2):  # 2026 e 2027
        ano = 2026 + ano_idx
        for mes in range(1, 13):
            valor = valor_base + (ano_idx * 12 + mes) * 10  # tendencia linear
            if mes == 12:
                valor += 500  # pico sazonal em dezembro nos 2 anos
            historico.append((_periodo(ano, mes), valor))
    assert len(historico) == 24

    ultimo_periodo = historico[-1][0]  # dez/2027
    futuros = projecao.gerar_periodos_futuros(ultimo_periodo, 12)  # ate dez/2028
    resultado = projecao.gerar_baseline(historico, futuros)
    assert all(r["metodo"] == "tendencia_sazonal" for r in resultado)

    dezembro_2028 = next(r for r in resultado if r["periodo"] == _periodo(2028, 12))
    novembro_2028 = next(r for r in resultado if r["periodo"] == _periodo(2028, 11))
    # dezembro projetado deve ficar bem acima de novembro (pico sazonal capturado,
    # nao so a tendencia linear crescendo mes a mes)
    assert dezembro_2028["valor_base"] - novembro_2028["valor_base"] > 300
    print("OK: gerar_baseline — tendencia_sazonal com 24 periodos (pico de dezembro capturado)")


def test_aplicar_ajustes_soma_e_ignora_periodo_sem_ajuste():
    baseline = [
        {"periodo": _periodo(2026, 7), "valor_base": 1000.0, "metodo": "flat_ultimo_valor", "periodos_historico": 1},
        {"periodo": _periodo(2026, 8), "valor_base": 1000.0, "metodo": "flat_ultimo_valor", "periodos_historico": 1},
    ]
    ajustes = [
        {"periodo": _periodo(2026, 7), "valor_ajuste": 500.0},
        {"periodo": _periodo(2026, 7), "valor_ajuste": 200.0},  # 2 ajustes no mesmo periodo, devem somar
    ]
    resultado = projecao.aplicar_ajustes(baseline, ajustes)
    jul = next(r for r in resultado if r["periodo"] == _periodo(2026, 7))
    ago = next(r for r in resultado if r["periodo"] == _periodo(2026, 8))
    assert jul["valor_ajuste"] == 700.0
    assert jul["valor_projetado"] == 1700.0
    assert ago["valor_ajuste"] == 0.0  # sem ajuste -> 0, nao quebra
    assert ago["valor_projetado"] == 1000.0
    print("OK: aplicar_ajustes — soma multiplos ajustes no mesmo periodo, 0 quando nao ha ajuste")


def test_montar_serie_grafico_indice_e_datetime_nao_string():
    # regressao do bug real (22/09/2026, achado pelo Rafael ao vivo): indice
    # de STRING "MM/AAAA" faz o st.line_chart (Vega-Lite) tratar o eixo como
    # NOMINAL e reordenar alfabeticamente, quebrando a ordem cronologica.
    # index precisa ser um tipo temporal de verdade (DatetimeIndex).
    serie_historico = {_periodo(2026, 6): 1000.0}
    serie_projetado = {_periodo(2026, 7): 1100.0}
    df = projecao.montar_serie_grafico(serie_historico, serie_projetado)
    import pandas as pd
    assert isinstance(df.index, pd.DatetimeIndex), f"esperava DatetimeIndex, veio {type(df.index)}"
    print("OK: montar_serie_grafico — indice e' DatetimeIndex, nao string (fix do bug de ordenacao)")


def test_montar_serie_grafico_ordem_cronologica_intervalo_longo():
    # reproduz o cenario real do bug (Rafael testou com historico 2023-2026
    # e horizonte de 21 meses): mesmo com anos diferentes e meses que colidem
    # em ordem alfabetica ("01/2027" < "01/2028" < "02/2026"), o indice final
    # tem que ficar em ordem CRONOLOGICA real, monotonica crescente.
    serie_historico = {
        _periodo(2023, 12): 100.0,
        _periodo(2024, 12): 110.0,
        _periodo(2026, 6): 120.0,
    }
    serie_projetado = {
        _periodo(2026, 7): 130.0,
        _periodo(2027, 1): 140.0,
        _periodo(2027, 3): 150.0,
        _periodo(2028, 1): 160.0,
        _periodo(2028, 2): 170.0,
    }
    df = projecao.montar_serie_grafico(serie_historico, serie_projetado)
    assert df.index.is_monotonic_increasing, f"indice fora de ordem cronologica: {list(df.index)}"
    # a ordem alfabetica de string ("01/2027" < "01/2028" < "02/2028" < "03/2027")
    # colocaria 03/2027 DEPOIS de 02/2028 -- confirma que isso NAO acontece aqui
    idx_lista = list(df.index)
    pos_2027_03 = idx_lista.index(__import__("pandas").Timestamp(_periodo(2027, 3)))
    pos_2028_01 = idx_lista.index(__import__("pandas").Timestamp(_periodo(2028, 1)))
    assert pos_2027_03 < pos_2028_01, "03/2027 deveria vir ANTES de 01/2028 na ordem cronologica real"
    print("OK: montar_serie_grafico — ordem cronologica correta mesmo com intervalo 2023-2028 (bug real reproduzido e corrigido)")


def test_montar_serie_grafico_valores_mapeados_certo_com_vazio_onde_falta():
    import pandas as pd
    serie_historico = {_periodo(2026, 6): 1000.0}
    serie_projetado = {_periodo(2026, 7): 1100.0}
    df = projecao.montar_serie_grafico(serie_historico, serie_projetado)
    assert list(df["Histórico"]) == [1000.0] or (df["Histórico"].iloc[0] == 1000.0)
    assert pd.isna(df["Histórico"].iloc[1]), "periodo sem historico deveria ficar vazio (NaN), nao um valor inventado"
    assert pd.isna(df["Projetado"].iloc[0]), "periodo sem projecao deveria ficar vazio (NaN), nao um valor inventado"
    assert df["Projetado"].iloc[1] == 1100.0
    print("OK: montar_serie_grafico — Historico/Projetado mapeados certo, vazio (NaN) onde nao ha dado")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in testes:
        try:
            t()
        except AssertionError as e:
            falhas += 1
            print(f"FALHOU: {t.__name__} — {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
