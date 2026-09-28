# -*- coding: utf-8 -*-
"""
Teste de regressao do bug real achado pelo Rafael no PDF da Engenharia
(28/09/2026, "Esse gráfico de 'passivo + patrimonio liquido' está saindo
p direita"): empresa com Patrimônio Líquido NEGATIVO faz o Circulante
passar de 100% do total (ex.: 2629,9%) e o PL ficar bem negativo
(-2529,9%, pra ainda somar 100%) -- a largura de cada segmento, antes
calculada direto do pct sobre o total, virava um numero fora de [0, 1]
(>1 pra Circulante, <0 pro PL), desenhando um retangulo que vazava pra
fora da area de conteudo (dentro da margem direita da pagina).

Roda grafico_barra_empilhada com um `c` fake (so' grava as chamadas de
rect(), nao desenha PDF de verdade) e confere que TODO segmento fica
dentro de [x0, x1], inclusive no caso com PL negativo. Confere tambem
que o caso normal (tudo >= 0) fica EXATAMENTE igual a antes do fix
(soma dos valores absolutos == soma dos valores == total nesse caso).

Rodar: python3 tests/test_grafico_barra_empilhada.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gerador_relatorio_comentado as g  # noqa: E402


class _CanvasFake:
    """So' precisa existir pra passar como `c` -- rect() eh' monkeypatched
    antes de cada teste, entao o canvas de verdade nunca eh' usado."""
    pass


def _rodar(segmentos, x0=42.0, x1=553.0):
    """Roda grafico_barra_empilhada com rect() trocado por um gravador,
    devolve a lista de (xa, xb) de cada retangulo da BARRA (os primeiros
    len(segmentos) rect() -- a legenda desenha mais retangulos depois,
    ignorados aqui)."""
    g._registrar_fontes()  # necessario p/ stringWidth() na legenda -- gerar_pdf_completo faz isso internamente, aqui chamamos direto
    chamadas = []
    rect_original, txt_original = g.rect, g.txt
    g.rect = lambda c, xa, y0, xb, y1, fill=None, **kw: chamadas.append((xa, xb))
    g.txt = lambda *a, **kw: None  # legenda de texto nao interessa a este teste, so' a largura da barra
    try:
        g.grafico_barra_empilhada(_CanvasFake(), x0, x1, 100.0, 34.0, segmentos)
    finally:
        g.rect, g.txt = rect_original, txt_original
    return chamadas[:len(segmentos)]


def test_pl_negativo_nunca_desenha_fora_da_barra():
    # mesmo cenario real: Circulante 2629,9% do total, Nao Circulante 0%,
    # Patrimonio Liquido -2529,9% (pra somar 100%) -- caso da Engenharia.
    total = 79_609.61
    passivo_circulante = 2_090_000.0
    passivo_nao_circulante = 0.0
    patrimonio_liquido = -2_014_011.63  # circulante + pl ~= total (mesma ordem de grandeza do caso real)
    segmentos = [
        dict(label="Circulante", valor=passivo_circulante, pct=100 * passivo_circulante / total,
             cor=g.NAVY, rotulo_valor="x"),
        dict(label="Não Circulante", valor=passivo_nao_circulante, pct=100 * passivo_nao_circulante / total,
             cor="#3d4290", rotulo_valor="x"),
        dict(label="Patrimônio Líquido", valor=patrimonio_liquido, pct=100 * patrimonio_liquido / total,
             cor=g.ORANGE, rotulo_valor="x"),
    ]
    # confirma que o cenario de teste reproduz o bug relatado: pct fora de [0,100]
    assert segmentos[0]["pct"] > 100
    assert segmentos[2]["pct"] < 0

    x0, x1 = 42.0, 553.0
    retangulos = _rodar(segmentos, x0, x1)
    assert len(retangulos) == 3
    for i, (xa, xb) in enumerate(retangulos):
        assert x0 - 0.01 <= min(xa, xb) and max(xa, xb) <= x1 + 0.01, (
            f"segmento {i} ({segmentos[i]['label']}) desenhado fora de [{x0}, {x1}]: ({xa}, {xb})"
        )
    print("OK: grafico_barra_empilhada nunca desenha fora da barra mesmo com PL negativo (bug real da Engenharia)")


def test_caso_normal_sem_negativo_fica_identico_a_antes_do_fix():
    # tudo >= 0, soma bate 100% -- soma(|valor|) == soma(valor) == total,
    # entao o resultado da largura tem que ser EXATAMENTE igual ao
    # calculo antigo (pct/100 * largura_total), sem regressao visual em
    # nenhum relatorio que ja estava correto.
    total = 43_359_080.94
    circulante = 18_239_216.72
    nao_circulante = 25_119_864.22
    assert abs((circulante + nao_circulante) - total) < 0.01
    segmentos = [
        dict(label="Circulante", valor=circulante, pct=100 * circulante / total, cor=g.NAVY, rotulo_valor="x"),
        dict(label="Não Circulante", valor=nao_circulante, pct=100 * nao_circulante / total, cor="#3d4290", rotulo_valor="x"),
    ]
    x0, x1 = 42.0, 553.0
    retangulos = _rodar(segmentos, x0, x1)
    largura_total = x1 - x0
    largura_esperada_circulante = largura_total * (segmentos[0]["pct"] / 100.0)
    xa0, xb0 = retangulos[0]
    assert abs((xb0 - xa0) - largura_esperada_circulante) < 0.01
    xa1, xb1 = retangulos[1]
    assert abs(xa1 - xb0) < 0.01  # segmentos colados, sem gap nem overlap
    assert abs(xb1 - x1) < 0.01  # ultimo segmento termina exatamente na borda
    print("OK: caso normal (sem PL negativo) fica matematicamente identico ao calculo antigo")


if __name__ == "__main__":
    test_pl_negativo_nunca_desenha_fora_da_barra()
    test_caso_normal_sem_negativo_fica_identico_a_antes_do_fix()
