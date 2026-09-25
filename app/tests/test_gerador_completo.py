# -*- coding: utf-8 -*-
"""Testes das paginas 1, 3-9 (25/09/2026). Mesmo padrao dos testes da
pagina 2 (test_gerador_relatorio_comentado.py): nao valida pixel, so'
garante que o pipeline nao quebra pra lucro E pra prejuizo, e que as
frases de leitura de CADA pagina sao neutras (mesmo texto nos 2 casos,
so' o numero com sinal muda)."""
import sys
import os
import re
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gerador_relatorio_comentado as g  # noqa: E402

BASE = dict(
    empresa_codigo="ENERGIA", empresa_nome="Enermais Energia Ltda", cnpj="47.040.664/0001-48",
    cabecalho_relatorio="Demonstrativo Comentado · 1º Semestre 2026", periodo_label="1º Semestre 2026",
    periodo_extenso="janeiro a junho de 2026", data_posicao="30/06/2026", data_geracao="25/09/2026",
    receita_bruta=36_674_150.0, deducoes_receita=3_321_000.0, deducoes_pct_bruta=0.09,
    receita_liquida=33_353_150.0, custo_servicos=3_899_607.99, custo_pct_liquida=0.117,
    lucro_bruto=29_453_542.01, margem_bruta=0.883,
    despesas_operacionais=30_559_591.64, despesas_administrativas=29_339_591.64,
    despesas_financeiras=1_139_497.69, despesas_tributarias=80_502.31,
    despesas_admin_itens=[("Serviços Profissionais", 11_401_300.0, 38.9), ("Salários e Ordenados", 4_060_000.0, 13.8)],
    csll_irpj=231_625.27, resultado_liquido=-1_337_674.90, margem_liquida=-0.04,
    resultado_financeiro=1_139_497.69, deprec_amortiz=691_527.25, ebitda=493_350.04, margem_ebitda=0.015,
    total_ativo=43_359_080.94, ativo_circulante=18_239_216.72, ativo_nao_circulante=25_119_864.22,
    passivo_circulante=18_338_124.77, passivo_nao_circulante=18_249_041.92, patrimonio_liquido=6_771_914.25,
    imobilizado=19_815_256.50, liquidez_corrente=0.99, alavancagem=5.40, endividamento_geral=0.844,
    complemento_receita="x", complemento_ebitda="x", complemento_despesas="x", callout_estrutura_capital="x",
    anexo_ativo=[("grupo", "ATIVO"), ("conta", "Disponível", 100.0), ("total", "TOTAL DO ATIVO", 100.0)],
    anexo_passivo=[("grupo", "PASSIVO"), ("conta", "Fornecedores", 100.0), ("total", "TOTAL PASSIVO + PL", 100.0)],
    nome_administrador="[Nome]", cargo_administrador="Administrador",
    nome_contador="[Nome]", cargo_contador="Contador",
    email_empresa="x@x.com", site_empresa="x.com",
)


def test_gerar_pdf_completo_nao_quebra_lucro_e_prejuizo():
    for resultado, margem, ebitda, margem_ebitda in [(-1_337_674.90, -0.04, 493_350.04, 0.015),
                                                       (2_000_000.0, 0.06, 3_000_000.0, 0.09)]:
        dados = dict(BASE, resultado_liquido=resultado, margem_liquida=margem, ebitda=ebitda, margem_ebitda=margem_ebitda)
        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, "completo.pdf")
            g.gerar_pdf_completo(dados, caminho)
            assert os.path.exists(caminho)
            assert os.path.getsize(caminho) > 5000
    print("OK: gerar_pdf_completo gera as 9 páginas sem exceção, pra lucro e pra prejuízo")


def _sem_numeros(texto):
    return re.sub(r"[-+]?R\$\s*[\d.,]+|[-+]?[\d.,]+%|[-+]?[\d.,]+x?", "", texto)


PALAVRAS_PROIBIDAS = ["lucro de", "prejuízo de", "positivo", "negativo", "saudável", "espaço claro",
                       "superando", "abaixo do", "pressão", "passa a"]


def test_leituras_das_novas_paginas_sao_neutras():
    dados_prejuizo = BASE
    dados_lucro = dict(BASE, resultado_liquido=2_000_000.0, margem_liquida=0.06,
                        ebitda=3_000_000.0, margem_ebitda=0.09,
                        despesas_operacionais=20_000_000.0)  # tambem inverte despesas < lucro bruto
    funcs = [g._leitura_receita_custos, g._leitura_despesas, g._leitura_resultado,
             g._leitura_ebitda, g._leitura_balanco]
    for fn in funcs:
        p_prejuizo = fn(dados_prejuizo)
        p_lucro = fn(dados_lucro)
        assert len(p_prejuizo) == len(p_lucro)
        for a, b in zip(p_prejuizo, p_lucro):
            assert _sem_numeros(a) == _sem_numeros(b), f"{fn.__name__}: texto muda de palavra entre os 2 sinais:\n{a}\n{b}"
        texto = " ".join(p_prejuizo).lower()
        for palavra in PALAVRAS_PROIBIDAS:
            assert palavra not in texto, f"{fn.__name__}: texto não é neutro, contém '{palavra}'"
    print(f"OK: {len(funcs)} funções de leitura das páginas novas são neutras (lucro/prejuízo, despesas acima/abaixo do lucro bruto)")


def test_grafico_ranking_horizontal_nao_quebra_lista_vazia():
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "sem_itens.pdf")
        dados = dict(BASE, despesas_admin_itens=[])
        g.gerar_pdf_completo(dados, caminho)
        assert os.path.getsize(caminho) > 5000
    print("OK: página de despesas não quebra com despesas_admin_itens vazio")


def test_anexo_com_lista_longa_nao_lanca_excecao():
    linhas = [("grupo", "ATIVO CIRCULANTE")]
    for i in range(25):
        linhas.append(("conta", f"Conta de teste com nome razoavelmente longo número {i}", 1234.56))
    linhas.append(("total", "TOTAL DO ATIVO", 30_000.0))
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "anexo_longo.pdf")
        dados = dict(BASE, anexo_ativo=linhas)
        g.gerar_pdf_completo(dados, caminho)
        assert os.path.getsize(caminho) > 5000
    print("OK: anexo com lista longa (25 contas) não lança exceção (pode transbordar visualmente -- não validado aqui)")


def test_anexo_multi_coluna_empresas_e_multi_periodo_ano_a_ano():
    """Ponto 2 e 3 da checagem multi-empresa/multi-periodo (pedido do
    Rafael 25/09/2026): 'vamos construir ja o multi-empresa-periodo' +
    'quando mais de 1 CNPJ, logo grupo'. So' garante que o pipeline nao
    quebra com N colunas (2 empresas, depois 3 anos comparando ano a
    ano) e que o logo de grupo entra quando `empresas_codigos` tem mais
    de 1 item -- nao valida pixel."""
    linhas_2col = [
        ("grupo", "ATIVO"),
        ("conta", "Disponível", 100.0, 150.0),
        ("total", "TOTAL DO ATIVO", 100.0, 150.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "anexo_multi_empresa.pdf")
        dados = dict(
            BASE,
            empresa_nome="Grupo Enermais",
            empresas_codigos=["ENERGIA", "SMG"],
            anexo_colunas=["Enermais Energia", "SMG Soluções"],
            anexo_escopo_label="Grupo Enermais",
            anexo_ativo=linhas_2col,
            anexo_passivo=linhas_2col,
        )
        g.gerar_pdf_completo(dados, caminho)
        assert os.path.getsize(caminho) > 5000
        # logo de grupo tem que ser o escolhido (nao o de 1 empresa)
        assert g._logo_dados(dados) == g.LOGO.get("GRUPO")

    linhas_3col = [
        ("grupo", "ATIVO"),
        ("conta", "Disponível", 100.0, 120.0, 140.0),
        ("total", "TOTAL DO ATIVO", 100.0, 120.0, 140.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "anexo_3_anos.pdf")
        dados = dict(
            BASE,
            anexo_colunas=["2024", "2025", "2026"],
            anexo_escopo_label="Enermais Energia · 2024 a 2026",
            anexo_ativo=linhas_3col,
            anexo_passivo=linhas_3col,
        )
        g.gerar_pdf_completo(dados, caminho)
        assert os.path.getsize(caminho) > 5000
        # 1 CNPJ so' (sem empresas_codigos) continua usando o logo individual
        assert g._logo_dados(dados) == g.LOGO.get("ENERGIA")
    print("OK: anexo multi-coluna (2 empresas, depois 3 anos ano a ano) não lança exceção; logo de escopo correto nos 2 casos")


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


def test_gerar_pdf_completo_sem_pagina_resultado_pula_csll_irpj():
    """25/09/2026 -- lucro presumido nao tem csll_irpj real (ver
    gerar_pdf_completo). incluir_pagina_resultado=False deve gerar 8
    paginas sem acessar dados['csll_irpj'] em momento nenhum."""
    dados_sem_csll = dict(BASE)
    del dados_sem_csll["csll_irpj"]  # garante que a pagina pulada NUNCA e' lida
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "sem_pagina4.pdf")
        g.gerar_pdf_completo(dados_sem_csll, caminho, incluir_pagina_resultado=False)
        assert os.path.exists(caminho)
        assert os.path.getsize(caminho) > 1000
    print("OK: gerar_pdf_completo com incluir_pagina_resultado=False nao acessa csll_irpj")
