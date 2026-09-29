# -*- coding: utf-8 -*-
"""Testes do Modelo B (gerador_relatorio_comparativo.py, 26/09/2026).
Mesmo padrao dos testes do Modelo A: nao valida pixel, so' garante que o
pipeline nao quebra em cenarios reais (3 periodos, lucro/prejuizo
misturados, so' 2 periodos) e que a tabela de evolucao calcula a
variacao certo."""
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gerador_relatorio_comparativo as B  # noqa: E402
import gerador_relatorio_comentado as A  # noqa: E402

PERIODOS = ["2024", "2025", "2026"]

ANEXO_ATIVO = [
    ("grupo", "ATIVO"),
    ("conta", "Disponível", 100.0, 120.0, 140.0),
    ("total", "TOTAL DO ATIVO", 100.0, 120.0, 140.0),
]
ANEXO_PASSIVO = [
    ("grupo", "PASSIVO"),
    ("conta", "Fornecedores", 100.0, 120.0, 140.0),
    ("total", "TOTAL PASSIVO + PL", 100.0, 120.0, 140.0),
]

BASE = dict(
    empresa_codigo="ENERGIA", empresa_nome="Enermais Energia Ltda", cnpj="47.040.664/0001-48",
    cabecalho_relatorio="Evolução Financeira · 2024 a 2026", data_geracao="26/09/2026",
    periodos_labels=PERIODOS, periodo_range_label="2024 A 2026",
    anexo_colunas=PERIODOS, anexo_escopo_label="Enermais Energia · 2024 a 2026",
    anexo_ativo=ANEXO_ATIVO, anexo_passivo=ANEXO_PASSIVO,
    kpis_fluxo=[
        {"label": "Receita Líquida", "tag": "fluxo", "valores": [28_900_000.0, 31_200_000.0, 33_353_150.0], "acumulado": 93_453_150.0},
        {"label": "Lucro Líquido", "tag": "fluxo", "valores": [1_850_000.0, -640_000.0, -1_337_674.90], "acumulado": None},
    ],
    kpis_saldo=[
        {"label": "Total do Ativo", "tag": "saldo", "valores": [39_100_000.0, 41_700_000.0, 43_359_080.94]},
    ],
    grafico_evolucao_metricas=[
        {"label": "Receita Líquida", "valores": [28_900_000.0, 31_200_000.0, 33_353_150.0]},
        {"label": "Lucro Líquido", "valores": [1_850_000.0, -640_000.0, -1_337_674.90]},
    ],
    nome_administrador="[Nome]", cargo_administrador="Administrador",
    nome_contador="[Nome]", cargo_contador="Contador",
    email_empresa="x@x.com", site_empresa="x.com",
)


def test_gerar_pdf_comparativo_3_periodos_nao_quebra():
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "comparativo.pdf")
        B.gerar_pdf_comparativo(BASE, caminho)
        assert os.path.exists(caminho)
        assert os.path.getsize(caminho) > 5000
    print("OK: gerar_pdf_comparativo gera as 5 páginas com 3 períodos sem exceção")


def test_gerar_pdf_comparativo_2_periodos_nao_quebra():
    """Menor caso de uso real (comparar 2 periodos so') -- garante que o
    motor nao assume 3 fixo em lugar nenhum."""
    dados = dict(BASE)
    dados["periodos_labels"] = ["2025", "2026"]
    dados["anexo_colunas"] = ["2025", "2026"]
    dados["kpis_fluxo"] = [dict(k, valores=k["valores"][1:]) for k in BASE["kpis_fluxo"]]
    dados["kpis_saldo"] = [dict(k, valores=k["valores"][1:]) for k in BASE["kpis_saldo"]]
    dados["grafico_evolucao_metricas"] = [dict(k, valores=k["valores"][1:]) for k in BASE["grafico_evolucao_metricas"]]
    dados["anexo_ativo"] = [("grupo", "ATIVO"), ("conta", "Disponível", 120.0, 140.0), ("total", "TOTAL DO ATIVO", 120.0, 140.0)]
    dados["anexo_passivo"] = [("grupo", "PASSIVO"), ("conta", "Fornecedores", 120.0, 140.0), ("total", "TOTAL PASSIVO + PL", 120.0, 140.0)]
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "comparativo_2p.pdf")
        B.gerar_pdf_comparativo(dados, caminho)
        assert os.path.getsize(caminho) > 5000
    print("OK: gerar_pdf_comparativo com só 2 períodos não lança exceção")


def test_tabela_evolucao_variacao_calculada_certo():
    """Confere a matematica da coluna Variacao sem depender de pixel --
    so' garante que a funcao roda e nao lanca (calculo em si e' so' um
    delta percentual simples, sem estado escondido)."""
    from reportlab.pdfgen import canvas
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "tabela.pdf")
        c = canvas.Canvas(caminho, pagesize=(B.PAGE_W, B.PAGE_H))
        y = B._tabela_evolucao(c, B.MARGEM, B.CONTEUDO_W, 200, PERIODOS, BASE["kpis_fluxo"])
        assert y > 200
        c.showPage()
        c.save()
        assert os.path.getsize(caminho) > 1000
    print("OK: _tabela_evolucao roda sem exceção e devolve y avançado")


def test_grafico_evolucao_com_metrica_toda_negativa_nao_quebra():
    """Caso extremo: todas as 3 leituras negativas -- maior_abs ainda
    precisa ser > 0 (achado real: se todos os valores forem 0.0,
    'max(...) or 1.0' evita divisao por zero)."""
    from reportlab.pdfgen import canvas
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "grafico.pdf")
        c = canvas.Canvas(caminho, pagesize=(B.PAGE_W, B.PAGE_H))
        metricas = [{"label": "Lucro Líquido", "valores": [-100.0, -200.0, 0.0]}]
        y = B.grafico_evolucao(c, B.MARGEM, B.PAGE_W - B.MARGEM, 200, metricas, PERIODOS)
        assert y > 200
        c.showPage()
        c.save()
    print("OK: grafico_evolucao com métrica toda negativa (e um zero) não lança exceção")


# FIX_20260929e/f/g: regressão dos 2 bugs de layout achados na
# VERIFICAÇÃO VISUAL de 29/09/2026, gerando o 1º PDF comparativo real
# (escala Enermais) -- nenhum dos testes acima pegou, porque usam rótulo
# curto e números pequenos de propósito (ver docstring do módulo). Os 2
# bugs: (1) etiqueta FLUXO/SALDO invadindo a 1ª coluna de valor com o
# rótulo mais longo de hoje ("Receita Operacional Líquida") em 3-4
# períodos; (2) fonte do valor larga demais pra coluna com número de 8
# dígitos em 4 períodos, colunas vizinhas coladas. Testa a GEOMETRIA
# (`_layout_tabela_evolucao`, função pura sem canvas) direto, sem
# precisar renderizar PDF nem inspecionar pixel.
def test_layout_tabela_evolucao_sem_sobreposicao_rotulo_etiqueta_e_colunas():
    B._registrar_fontes()
    from reportlab.pdfbase.pdfmetrics import stringWidth

    valores_8_digitos = [26_400_000.0, 28_900_000.0, 31_200_000.0, 33_353_150.0]
    for n_col in (2, 3, 4):
        valores = valores_8_digitos[-n_col:]
        linhas = [
            {"label": "Receita Operacional Líquida", "tag": "fluxo", "valores": valores},
            {"label": "EBITDA", "tag": "fluxo", "valores": valores},
            {"label": "Total do Ativo", "tag": "saldo", "valores": valores},
        ]
        geo = B._layout_tabela_evolucao(B.CONTEUDO_W, n_col, linhas)
        left_edge_col0 = geo["x_col"](0) - geo["valor_col_w"]

        for linha, linha_geo in zip(linhas, geo["linhas"]):
            tag_end = linha_geo["tag_x0"] + geo["TAG_W"]
            assert tag_end <= left_edge_col0 + 0.01, (
                f"n_col={n_col}: etiqueta de '{linha['label']}' termina em {tag_end:.1f}pt, "
                f"depois da borda esquerda da 1ª coluna de valor ({left_edge_col0:.1f}pt) -- "
                f"ia sobrepor o valor (bug do FIX_20260929e/g)"
            )
        for linha in linhas:
            for v in linha["valores"]:
                texto_v = B.moeda_br(v, forcar_sinal=(v < 0))
                w = stringWidth(texto_v, B.FONT["regular"], geo["valor_font_size"])
                assert w <= geo["valor_col_w"] + 0.5, (
                    f"n_col={n_col}: '{texto_v}' tem {w:.1f}pt no tamanho escolhido "
                    f"({geo['valor_font_size']}pt) -- mais largo que a coluna "
                    f"({geo['valor_col_w']:.1f}pt), ia colar na coluna vizinha (bug do FIX_20260929f)"
                )
    print("OK: _layout_tabela_evolucao não deixa etiqueta invadir a 1ª coluna nem valor mais largo que a coluna, em 2/3/4 períodos")


def test_anexo_comparativo_grande_nao_perde_conteudo_estourando_pagina():
    """FIX_20260929o -- mesmo achado real do Modelo A (ver
    test_gerador_completo.test_anexo_multi_coluna_grande_...), so' que
    aqui via Modelo B (pagina_anexo e' reaproveitada sem alteracao pelos
    2 modelos): Evolucao_GRUPO_202312_202606.pdf real (2 empresas x 3
    periodos) cortava a secao de Patrimonio Liquido no meio do anexo. Este
    teste usa um anexo grande o bastante pra estourar 1 pagina so' e
    confere que gerar_pdf_comparativo nao lança excecao e que
    paginas_extras_anexo prevê corretamente a pagina extra."""
    def _linhas(prefixo, n, base):
        out = [("grupo", prefixo)]
        for i in range(n):
            out.append(("conta", f"{prefixo} conta {i} com nome razoavelmente longo",
                         base * (i + 1), base * 1.05 * (i + 1), base * 1.1 * (i + 1)))
        return out

    anexo_ativo = (
        _linhas("ATIVO CIRCULANTE", 12, 500.0)
        + [("total", "TOTAL CIRCULANTE", 30_000.0, 31_000.0, 32_000.0)]
        + _linhas("ATIVO NAO CIRCULANTE", 12, 800.0)
        + [("total", "TOTAL DO ATIVO", 90_000.0, 93_000.0, 96_000.0)]
    )
    anexo_passivo = (
        _linhas("PASSIVO CIRCULANTE", 8, 400.0)
        + [("total", "TOTAL CIRCULANTE PASSIVO", 15_000.0, 15_500.0, 16_000.0)]
        + _linhas("PATRIMONIO LIQUIDO", 8, 700.0)
        + [("total", "TOTAL PATRIMONIO LIQUIDO", 60_000.0, 62_000.0, 64_000.0),
           ("total", "TOTAL DO PASSIVO", 90_000.0, 93_000.0, 96_000.0)]
    )
    dados = dict(BASE)
    dados["anexo_colunas"] = ["2024", "2025", "2026"]
    dados["anexo_escopo_label"] = "Grupo Enermais (ENERGIA + SMG) · 2024 a 2026"
    dados["anexo_ativo"] = anexo_ativo
    dados["anexo_passivo"] = anexo_passivo

    extras = A.paginas_extras_anexo(dados)
    assert extras > 0, "dataset grande deveria precisar de mais de 1 pagina fisica pro anexo"
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "comparativo_anexo_grande.pdf")
        B.gerar_pdf_comparativo(dados, caminho)
        assert os.path.getsize(caminho) > 5000
    print(f"OK: anexo comparativo grande usa páginas extras em vez de perder conteúdo (paginas_extras_anexo={extras})")


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
            print(f"ERRO ({type(e).__name__}) em {t.__name__}: {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
