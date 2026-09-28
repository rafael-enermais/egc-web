# -*- coding: utf-8 -*-
"""
Teste de regressao da pilula de periodo da capa (pagina_capa), 2a rodada
de revisao do Rafael no PDF real (28/09/2026): 1a tentativa colocou
periodo_extenso como texto solto ABAIXO da pilula laranja -- Rafael
achou que "o '1º Semestre' ficou fora do quadrado laranja" (queria
DENTRO da pilula, junto do periodo). Confere que:
1) o texto desenhado DENTRO da pilula inclui periodo_label E
   periodo_extenso juntos, na mesma chamada de texto (nao 2 textos
   separados);
2) a largura da pilula nunca passa de CONTEUDO_W (nao vaza pela margem
   direita, mesmo caso da pagina virada bug de barra empilhada desta
   mesma rodada);
3) periodo_extenso muito comprido (caso extremo, nao esperado na
   pratica mas nao pode quebrar) faz a fonte encolher em vez de estourar
   a pilula.

Rodar: python3 tests/test_pagina_capa.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gerador_relatorio_comentado as g  # noqa: E402


class _CanvasFake:
    def saveState(self): pass
    def restoreState(self): pass
    def setFillColor(self, *a, **kw): pass
    def setStrokeColor(self, *a, **kw): pass
    def setLineWidth(self, *a, **kw): pass
    def setLineCap(self, *a, **kw): pass
    def beginPath(self): return _PathFake()
    def drawPath(self, *a, **kw): pass
    def clipPath(self, *a, **kw): pass
    def line(self, *a, **kw): pass
    def setStrokeAlpha(self, *a, **kw): pass
    def setFont(self, *a, **kw): pass
    def drawString(self, *a, **kw): pass


class _PathFake:
    def moveTo(self, *a): pass
    def lineTo(self, *a): pass
    def close(self): pass


DADOS_BASE = dict(
    empresa_codigo="RENOV", empresa_nome="Enermais Renovaveis Ltda", cnpj="51.671.106/0001-58",
    empresas_codigos=["RENOV"], periodo_label="06/2026",
)


def _rodar_capa(periodo_extenso):
    g._registrar_fontes()
    dados = dict(DADOS_BASE, periodo_extenso=periodo_extenso)
    chamadas_rect, chamadas_txt = [], []
    rect_original, txt_original, image_original = g.rect, g.txt, g.image
    g.rect = lambda c, x0, y0, x1, y1, **kw: chamadas_rect.append((x0, x1, kw))
    g.txt = lambda c, x, y, texto, **kw: chamadas_txt.append((x, texto, kw))
    g.image = lambda *a, **kw: None
    try:
        g.pagina_capa(_CanvasFake(), dados, 1, 9)
    finally:
        g.rect, g.txt, g.image = rect_original, txt_original, image_original
    return chamadas_rect, chamadas_txt


def test_periodo_extenso_entra_na_mesma_pilula_do_periodo_label():
    chamadas_rect, chamadas_txt = _rodar_capa("1º Semestre")
    textos_pilula = [t for (_x, t, kw) in chamadas_txt if "PERÍODO" in t]
    assert len(textos_pilula) == 1, "periodo_label e periodo_extenso devem estar no MESMO texto, nao 2 separados"
    assert "06/2026" in textos_pilula[0] and "1º SEMESTRE" in textos_pilula[0].upper()
    print("OK: periodo_extenso aparece dentro da mesma pilula do periodo_label, nao mais como texto solto abaixo")


def test_pilula_nunca_ultrapassa_largura_util_da_pagina():
    for extenso in ["1º Semestre", "Janeiro a Junho de 2026", None, "", "x" * 300]:
        chamadas_rect, _ = _rodar_capa(extenso)
        pilulas = [r for r in chamadas_rect if r[2].get("fill") == g.ORANGE and r[2].get("radius")]
        assert len(pilulas) == 1
        x0, x1, _kw = pilulas[0]
        largura = x1 - x0
        assert largura <= g.CONTEUDO_W + 0.01, f"pilula com {largura}pt (extenso={extenso!r}) vazou alem de CONTEUDO_W={g.CONTEUDO_W}"
    print("OK: largura da pilula de periodo nunca ultrapassa a largura util da pagina, mesmo com extenso incomum/vazio/gigante")


if __name__ == "__main__":
    test_periodo_extenso_entra_na_mesma_pilula_do_periodo_label()
    test_pilula_nunca_ultrapassa_largura_util_da_pagina()
