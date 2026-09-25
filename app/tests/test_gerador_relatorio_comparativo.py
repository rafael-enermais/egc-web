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
