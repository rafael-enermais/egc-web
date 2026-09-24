# -*- coding: utf-8 -*-
"""
Testes de app/gerador_relatorio_comentado.py -- modulo puro (sem banco/
Streamlit), mesmo padrao de test_indicadores.py. Nao valida pixel (isso
e' feito visualmente, comparando contra o PDF-modelo real -- ver 00-
handoff.md secao 46), so' garante que o pipeline nao quebra e que o
texto por frase-modelo reage certo ao sinal do resultado (lucro x
prejuizo, positivo x negativo) -- ponto que so' um teste automatizado
pega de verdade (o exemplo real usado no piloto so' cobriu o caso
"prejuizo").

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


def test_leitura_executiva_com_prejuizo_usa_texto_certo():
    paragrafos = g._leitura_executiva(DADOS_PREJUIZO)
    assert len(paragrafos) == 2
    assert "prejuízo" in paragrafos[1]
    assert "lucro" not in paragrafos[1].lower() or "lucro bruto" in paragrafos[1].lower()
    print("OK: resultado negativo -> frase-modelo usa 'prejuízo', não 'lucro'")


def test_leitura_executiva_com_lucro_usa_texto_certo():
    dados_lucro = dict(DADOS_PREJUIZO, resultado_liquido=2_000_000.00, margem_liquida=0.06)
    paragrafos = g._leitura_executiva(dados_lucro)
    assert "lucro de" in paragrafos[1]
    assert "prejuízo" not in paragrafos[1]
    print("OK: resultado positivo -> frase-modelo usa 'lucro', não 'prejuízo' (regressão evitada)")


def test_leitura_executiva_com_ebitda_negativo_usa_texto_certo():
    dados_ebitda_neg = dict(DADOS_PREJUIZO, ebitda=-200_000.00)
    paragrafos = g._leitura_executiva(dados_ebitda_neg)
    assert "EBITDA negativo" in paragrafos[0]
    print("OK: EBITDA negativo -> frase-modelo usa 'negativo', não 'positivo'")


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
