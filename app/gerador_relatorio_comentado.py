# -*- coding: utf-8 -*-
"""
EGC | Gerador de Relatorio Comentado -- motor de PDF do "Demonstrativo
Comentado" (modelo COMERCIAL aprovado pelo Rafael em 24/09/2026, pasta
`arquivos/MODELO RELATORIO NOVO/` do vault).

PILOTO (24/09/2026): so' a pagina 2 (Destaques do Periodo) esta
implementada, pra validar o pipeline inteiro (fonte bundlada, paleta,
logo, layout A4, texto por frase-modelo com numero real) antes de fazer
as outras 8. Capa/Receita-Custos-Margem/Despesas/Resultado/EBITDA/
Balanco/Anexo/Fechamento ficam pra proxima rodada, depois que o Rafael
validar esta.

Arquitetura DELIBERADAMENTE copiada do gerador_somos.py do RHDADOS
(C:\\RHDADOS\\app\\gerador_somos.py) -- mesmo problema (PDF sem Word,
Streamlit Cloud sem fonte de sistema, mesma marca EnerMais, mesmas 6
empresas), mesma solucao ja provada em producao: ReportLab (canvas
posicionado por coordenada) + matplotlib (graficos, salvos como PNG
transparente e inseridos na pagina) + fontes Poppins bundladas em
assets_relatorio/fonts/ (nao existe fonte de sistema no Streamlit Cloud).
Paleta amostrada por pixel do PDF-modelo real bate quase exato com a
paleta ja usada no RHDADOS (mesma marca) -- reaproveitada direto.

Este modulo NAO fala com o Supabase -- so' recebe um dicionario `dados`
ja montado e desenha o PDF (mesma separacao dado x apresentacao do
RHDADOS e do resto do app EGC: db.py busca, indicadores.py calcula, este
modulo so' desenha).

Texto analitico (Leitura Executiva etc.): frase-modelo fixa preenchida
com valor real do dicionario `dados` -- decisao do Rafael (24/09/2026),
nunca geracao livre por IA. Ver `_leitura_executiva()`.
"""
from __future__ import annotations

import os
from typing import Optional

from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.utils import ImageReader

from formatacao import moeda_br, pct_br, numero_br

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets_relatorio")
FONT_DIR = os.path.join(ASSETS, "fonts")
LOGO_DIR = os.path.join(ASSETS, "logos")

PAGE_W, PAGE_H = 595.0, 842.0  # A4 retrato em pt -- mesmo tamanho do PDF-modelo (confirmado via pdfinfo)

# ---------------------------------------------------------------- paleta ---
# Amostrada por pixel do PDF-modelo real (COMERCIAL, pag. 2) -- bate quase
# exato com a paleta ja usada no RHDADOS pra mesma marca EnerMais, entao
# reaproveitada direto (mesmo NAVY/ORANGE, so' renomeado pro contexto EGC).
NAVY = "#171C60"
ORANGE = "#EA9527"
GREY_TEXT = "#525252"
GREY_BG = "#F6F8FB"
RED_ACCENT = "#C38492"
BORDER_LIGHT = "#DADFE8"

LOGO = {
    "ENERGIA": os.path.join(LOGO_DIR, "ENERGIA.png"),
    "SMG": os.path.join(LOGO_DIR, "SMG.png"),
    "ENG": os.path.join(LOGO_DIR, "ENG.png"),
    "RENOV": os.path.join(LOGO_DIR, "RENOV.png"),
    "CONST": os.path.join(LOGO_DIR, "CONST.png"),
    "SOL": os.path.join(LOGO_DIR, "SOL.png"),
    "GRUPO": os.path.join(LOGO_DIR, "GRUPO.png"),
}

# ----------------------------------------------------------- fontes ---
_FONTES_REGISTRADAS = False


def _registrar_fontes():
    """Registra as fontes bundladas uma unica vez por processo (evita
    'font already registered' se o Streamlit chamar isso de novo num
    rerun) -- mesmo padrao do RHDADOS."""
    global _FONTES_REGISTRADAS
    if _FONTES_REGISTRADAS:
        return
    pdfmetrics.registerFont(TTFont("Sans", f"{FONT_DIR}/Poppins-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("Sans-Bold", f"{FONT_DIR}/Poppins-Bold.ttf"))
    pdfmetrics.registerFont(TTFont("Sans-Medium", f"{FONT_DIR}/Poppins-Medium.ttf"))
    pdfmetrics.registerFont(TTFont("Sans-Italic", f"{FONT_DIR}/Poppins-Italic.ttf"))
    _FONTES_REGISTRADAS = True


FONT = {"regular": "Sans", "bold": "Sans-Bold", "heavy": "Sans-Bold",
        "medium": "Sans-Medium", "italic": "Sans-Italic"}


# ------------------------------------------------------------- helpers ---
# Y/txt/rect/image/_wrap_text sao adaptacoes diretas dos helpers do
# gerador_somos.py do RHDADOS (mesma logica, PAGE_H trocado pro A4).
def Y(y_top: float) -> float:
    """Converte coordenada Y 'top-down' (origem no topo da pagina, como
    quem le um PDF) pro sistema bottom-up do reportlab."""
    return PAGE_H - y_top


def txt(c, x, y_top, s, font="regular", size=11, color=NAVY, align="left"):
    c.setFont(FONT[font], size)
    c.setFillColor(HexColor(color))
    y = Y(y_top)
    if align == "left":
        c.drawString(x, y, s)
    elif align == "center":
        c.drawCentredString(x, y, s)
    elif align == "right":
        c.drawRightString(x, y, s)


def rect(c, x0, y0_top, x1, y1_top, fill=None, stroke=None, width=1, radius=0):
    c.saveState()
    if fill:
        c.setFillColor(HexColor(fill))
    if stroke:
        c.setStrokeColor(HexColor(stroke))
        c.setLineWidth(width)
    y0 = Y(y1_top)
    h = y1_top - y0_top
    w = x1 - x0
    if radius:
        c.roundRect(x0, y0, w, h, radius, fill=1 if fill else 0, stroke=1 if stroke else 0)
    else:
        c.rect(x0, y0, w, h, fill=1 if fill else 0, stroke=1 if stroke else 0)
    c.restoreState()


def image(c, path, x0, y0_top, x1, y1_top, mask="auto", preserve_ratio=True):
    if preserve_ratio:
        ir = ImageReader(path)
        iw, ih = ir.getSize()
        box_w, box_h = x1 - x0, y1_top - y0_top
        scale = min(box_w / iw, box_h / ih)
        w, h = iw * scale, ih * scale
        cx, cy = (x0 + x1) / 2, (y0_top + y1_top) / 2
        x0, x1 = cx - w / 2, cx + w / 2
        y0_top, y1_top = cy - h / 2, cy + h / 2
    c.drawImage(path, x0, Y(y1_top), width=x1 - x0, height=y1_top - y0_top,
                mask=mask, preserveAspectRatio=preserve_ratio)


def _wrap_text(text, font_name, size, max_width):
    """Quebra `text` em linhas que cabem em `max_width` (pt) -- quebra por
    palavra inteira, sem limite de linhas (paragrafo de corpo, diferente
    do _wrap_label do RHDADOS que trunca com '...' pra rotulo de legenda)."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if stringWidth(trial, font_name, size) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def paragrafo(c, x, y_top, text, max_width, font="regular", size=10.5,
              color=GREY_TEXT, leading=15.5):
    """Desenha um paragrafo com quebra automatica de linha. Devolve o Y
    (top-down) logo apos a ultima linha, pra empilhar paragrafos."""
    linhas = _wrap_text(text, FONT[font], size, max_width)
    y = y_top
    for linha in linhas:
        txt(c, x, y, linha, font=font, size=size, color=color)
        y += leading
    return y


# ------------------------------------------------------------- layout ---
MARGEM = 42.0
CONTEUDO_W = PAGE_W - 2 * MARGEM


def _header(c, dados, subtitulo_pagina):
    # faixa superior navy -> orange (aprox. gradiente do modelo: navy na
    # maior parte, orange nos ~12% finais -- reportlab nao tem gradiente
    # nativo simples, simulado com 2 blocos solidos; refinar depois se o
    # Rafael achar a transicao dura demais)
    rect(c, 0, 0, PAGE_W * 0.88, 5, fill=NAVY)
    rect(c, PAGE_W * 0.88, 0, PAGE_W, 5, fill=ORANGE)

    logo_path = LOGO.get(dados["empresa_codigo"])
    if logo_path and os.path.exists(logo_path):
        image(c, logo_path, MARGEM, 18, MARGEM + 130, 55, mask="auto", preserve_ratio=True)

    txt(c, PAGE_W - MARGEM, 32, dados["cabecalho_relatorio"].upper(),
        font="bold", size=8.5, color=NAVY, align="right")
    txt(c, PAGE_W - MARGEM, 46, subtitulo_pagina,
        font="bold", size=11, color=NAVY, align="right")

    c.setStrokeColor(HexColor(BORDER_LIGHT))
    c.setLineWidth(0.75)
    c.line(MARGEM, Y(64), PAGE_W - MARGEM, Y(64))


def _footer(c, pagina, total_paginas):
    txt(c, PAGE_W / 2, PAGE_H - 24, f"Página {pagina} de {total_paginas}",
        font="regular", size=8.5, color=GREY_TEXT, align="center")


def _kpi_grande(c, x0, x1, y0, y1, label, valor, complemento, invertido=False):
    """Card de KPI grande (2 por linha) -- borda navy/fundo branco por
    padrao, ou fundo navy solido com texto branco/laranja quando
    `invertido=True` (destaque do 2o KPI, mesmo padrao do modelo: receita
    em card claro, EBITDA em card navy solido)."""
    if invertido:
        rect(c, x0, y0, x1, y1, fill=NAVY, radius=6)
        cor_label, cor_valor, cor_comp = "#FFFFFF", ORANGE, "#C9CEE8"
    else:
        rect(c, x0, y0, x1, y1, fill="#FFFFFF", stroke=NAVY, width=1.1, radius=6)
        cor_label, cor_valor, cor_comp = ORANGE, NAVY, GREY_TEXT

    pad = 16
    txt(c, x0 + pad, y0 + 22, label.upper(), font="bold", size=9, color=cor_label)
    txt(c, x0 + pad, y0 + 50, valor, font="heavy", size=25, color=cor_valor)
    paragrafo(c, x0 + pad, y0 + 68, complemento, x1 - x0 - 2 * pad,
              font="regular", size=9, color=cor_comp, leading=12)


def _kpi_pequeno(c, x0, x1, y0, y1, label, valor, complemento, cor_borda):
    rect(c, x0, y0, x0 + 3, y1, fill=cor_borda)
    rect(c, x0 + 3, y0, x1, y1, fill=GREY_BG)
    pad = 12
    paragrafo(c, x0 + pad, y0 + 16, label.upper(), x1 - x0 - 2 * pad,
              font="bold", size=7.6, color=NAVY, leading=9.5)
    txt(c, x0 + pad, y0 + 42, valor, font="heavy", size=16, color=NAVY)
    txt(c, x0 + pad, y0 + 58, complemento, font="regular", size=8, color=GREY_TEXT)


def _leitura_executiva(dados) -> list[str]:
    """Frases-modelo (24/09/2026, decisao do Rafael: nunca geracao livre
    por IA) preenchidas com valor real de `dados`. Sinal do resultado
    (lucro/prejuizo, alta/queda) escolhido em runtime -- nunca escrito
    fixo, senao um periodo com lucro real sairia com o texto errado."""
    d = dados
    lucro_prejuizo = "lucro" if d["resultado_liquido"] >= 0 else "prejuízo"
    p1 = (
        f"No período de referência, a {d['empresa_nome']} registrou receita "
        f"operacional líquida de {moeda_br(d['receita_liquida'])} com margem "
        f"bruta de {pct_br(d['margem_bruta'])} — "
        f"{'operação estruturalmente saudável' if d['margem_bruta'] >= 0.5 else 'margem operacional abaixo do usual'}. "
        f"Isolando efeitos financeiros e de depreciação, o período gerou EBITDA "
        f"{'positivo' if d['ebitda'] >= 0 else 'negativo'} de {moeda_br(abs(d['ebitda']))}."
    )
    p2 = (
        f"O resultado líquido do período foi {lucro_prejuizo} de "
        f"{moeda_br(abs(d['resultado_liquido']))} (margem de {pct_br(d['margem_liquida'], forcar_sinal=True)}), "
        f"com despesas operacionais de {moeda_br(d['despesas_operacionais'])}."
    )
    return [p1, p2]


def pagina_destaques(c, dados, pagina: int, total_paginas: int):
    _header(c, dados, "Destaques do Período")

    txt(c, MARGEM, 100, f"Destaques {dados['periodo_label']}", font="heavy", size=20, color=NAVY)
    txt(c, MARGEM, 122, f"{dados['empresa_nome']} — {dados['periodo_extenso']}",
        font="regular", size=10.5, color=GREY_TEXT)

    # 2 KPIs grandes
    meio = MARGEM + CONTEUDO_W / 2 - 6
    _kpi_grande(c, MARGEM, meio, 145, 235,
                "Receita Operacional Líquida", f"{moeda_br(dados['receita_liquida'] / 1_000_000)} MM",
                dados["complemento_receita"])
    _kpi_grande(c, meio + 12, MARGEM + CONTEUDO_W, 145, 235,
                "EBITDA do Período", f"{moeda_br(dados['ebitda'] / 1_000_000)} MM",
                dados["complemento_ebitda"], invertido=True)

    # 4 KPIs pequenos
    col_w = (CONTEUDO_W - 3 * 10) / 4
    cores = [NAVY, RED_ACCENT, NAVY, GREY_TEXT]
    kpis_pequenos = [
        ("Lucro Bruto", f"{moeda_br(dados['lucro_bruto'] / 1_000_000)} MM", f"margem {pct_br(dados['margem_bruta'])}"),
        ("Despesas Operacionais", f"{moeda_br(dados['despesas_operacionais'] / 1_000_000)} MM", dados["complemento_despesas"]),
        ("Total do Ativo", f"{moeda_br(dados['total_ativo'] / 1_000_000)} MM", f"posição em {dados['data_posicao']}"),
        (f"{'Lucro' if dados['resultado_liquido'] >= 0 else 'Prejuízo'} Líquido",
         f"{moeda_br(abs(dados['resultado_liquido']) / 1_000_000)} MM", f"margem {pct_br(dados['margem_liquida'], forcar_sinal=True)}"),
    ]
    x = MARGEM
    for (label, valor, comp), cor in zip(kpis_pequenos, cores):
        _kpi_pequeno(c, x, x + col_w, 255, 330, label, valor, comp, cor)
        x += col_w + 10

    # Leitura Executiva
    y = 375
    txt(c, MARGEM, y, "Leitura Executiva", font="heavy", size=13, color=NAVY)
    c.setStrokeColor(HexColor(ORANGE))
    c.setLineWidth(2)
    c.line(MARGEM, Y(y + 6), MARGEM + 34, Y(y + 6))
    y += 28
    for par in _leitura_executiva(dados):
        y = paragrafo(c, MARGEM, y, par, CONTEUDO_W, size=10.5, leading=15) + 10

    # callout
    y += 6
    altura_callout = 70
    rect(c, MARGEM, y, MARGEM + CONTEUDO_W, y + altura_callout, fill=GREY_BG, stroke=BORDER_LIGHT, width=0.75, radius=6)
    paragrafo(c, MARGEM + 18, y + 22, dados["callout_estrutura_capital"],
               CONTEUDO_W - 36, size=9.5, leading=13.5)

    _footer(c, pagina, total_paginas)


def gerar_pdf_piloto(dados: dict, caminho_saida: str) -> str:
    """Gera um PDF de 1 pagina (Destaques) -- piloto de validacao do
    pipeline (24/09/2026). `total_paginas` fixo em 9 pra bater com o
    rodape do modelo final (relatorio completo tera 9 paginas)."""
    _registrar_fontes()
    c = canvas.Canvas(caminho_saida, pagesize=(PAGE_W, PAGE_H))
    pagina_destaques(c, dados, pagina=2, total_paginas=9)
    c.save()
    return caminho_saida
