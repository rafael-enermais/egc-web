# -*- coding: utf-8 -*-
"""
Testes de app/gerador_relatorio_comentado.py -- modulo puro (sem banco/
Streamlit), mesmo padrao de test_indicadores.py. Nao valida pixel (isso
e' feito visualmente, comparando contra o PDF-modelo real -- ver 00-
handoff.md secao 47), so' garante que o pipeline nao quebra e que o
texto por frase-modelo e' de fato NEUTRO (decisao do Rafael, 24/09/2026:
nada de "operacao saudavel"/"positivo"/"negativo" como adjetivo -- a
MESMA frase, palavra por palavra fora do numero, tem que servir pra
lucro ou prejuizo, EBITDA positivo ou negativo). Ponto que so' um teste
automatizado pega de verdade -- o exemplo real usado no piloto visual so'
cobria o caso "prejuizo".

Rodar: python3 tests/test_gerador_relatorio_comentado.py
"""
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gerador_relatorio_comentado as g  # noqa: E402

DADOS_PREJUIZO = dict(
    empresa_codigo="ENERGIA",
    empresa_nome="Enermais Energia Ltda",
    cabecalho_relatorio="Demonstrativo Comentado · 1º Semestre 2026",
    periodo_label="1º Semestre 2026",
    periodo_extenso="janeiro a junho de 2026",
    data_posicao="30/06/2026",
    receita_liquida=33_353_150.00,
    lucro_bruto=29_453_542.01,
    margem_bruta=0.883,
    despesas_operacionais=30_559_591.64,
    resultado_liquido=-1_337_674.90,
    margem_liquida=-0.040,
    ebitda=493_350.04,
    margem_ebitda=0.015,
    total_ativo=43_359_080.94,
    complemento_receita="Receita bruta R$ 36,7 MM · deduções R$ 3,3 MM",
    complemento_ebitda="Margem EBITDA de 1,5% · geração de caixa operacional positiva",
    complemento_despesas="96,0% administrativas",
    callout_estrutura_capital="Estrutura de capital: liquidez corrente de 0,99.",
)


def test_gerar_pdf_piloto_nao_quebra_e_gera_arquivo_nao_vazio():
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "piloto.pdf")
        g.gerar_pdf_piloto(DADOS_PREJUIZO, caminho)
        assert os.path.exists(caminho)
        assert os.path.getsize(caminho) > 1000
    print("OK: gerar_pdf_piloto gera arquivo PDF não vazio, sem exceção")


import re


def _sem_numeros(texto: str) -> str:
    """Tira tudo que e' numero/moeda/percentual pra sobrar so' a frase em
    si -- usado pra provar que o texto NAO muda de palavra conforme o
    sinal do resultado (so' o numero muda)."""
    return re.sub(r"[-+]?R\$\s*[\d.,]+|[-+]?[\d.,]+%|[-+]?[\d.,]+", "", texto)


def test_leitura_executiva_e_neutra_mesma_frase_com_lucro_ou_prejuizo():
    # decisao do Rafael (24/09/2026): nada de "lucro"/"prejuízo"/
    # "positivo"/"negativo" como adjetivo -- a MESMA frase (fora do
    # numero) tem que servir pros 2 casos. Prova isso de verdade: gera
    # com resultado negativo E positivo, tira os números dos dois textos,
    # e confere que sobrou EXATAMENTE a mesma frase.
    dados_prejuizo = DADOS_PREJUIZO
    dados_lucro = dict(DADOS_PREJUIZO, resultado_liquido=2_000_000.00, margem_liquida=0.06,
                        ebitda=800_000.00, margem_ebitda=0.024)
    p_prejuizo = g._leitura_executiva(dados_prejuizo)
    p_lucro = g._leitura_executiva(dados_lucro)
    assert len(p_prejuizo) == len(p_lucro) == 2
    for a, b in zip(p_prejuizo, p_lucro):
        assert _sem_numeros(a) == _sem_numeros(b), f"texto mudou de palavra entre os 2 sinais:\n{a}\n{b}"
    texto_completo = " ".join(p_prejuizo).lower()
    for palavra_proibida in ["lucro de", "prejuízo de", "positivo", "negativo", "saudável", "espaço claro"]:
        assert palavra_proibida not in texto_completo, f"texto não é neutro: contém '{palavra_proibida}'"
    print("OK: Leitura Executiva é neutra -- mesma frase com lucro ou prejuízo, sem adjetivo condicional")


def test_leitura_executiva_forca_sinal_explicito_no_numero():
    # já que o texto não diz mais "lucro"/"prejuízo", o "+"/"-" explícito
    # no número é o que carrega a informação de sinal -- sem isso, um
    # resultado positivo ficaria ambíguo na frase.
    dados_lucro = dict(DADOS_PREJUIZO, resultado_liquido=2_000_000.00, margem_liquida=0.06)
    p = g._leitura_executiva(dados_lucro)
    assert "+R$" in p[1] or "+ R$" in p[1]
    p_prejuizo = g._leitura_executiva(DADOS_PREJUIZO)
    assert "-R$" in p_prejuizo[1]
    print("OK: sinal (+/-) explícito no número carrega a informação que o texto não opina mais")


def test_wrap_text_quebra_paragrafo_longo_em_varias_linhas():
    linhas = g._wrap_text("uma frase razoavelmente longa pra forçar quebra de linha no teste",
                           g.FONT["regular"], 10.5, max_width=120)
    assert len(linhas) > 1
    print(f"OK: _wrap_text quebra parágrafo longo em {len(linhas)} linhas")


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
            print(f"ERRO (não AssertionError) em {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
