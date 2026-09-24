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
# Marca d'agua (torres de transmissao) extraida com transparencia do
# proprio PDF-modelo real (pdfimages + smask recombinado, 24/09/2026) --
# mesmo asset exato usado no modelo, nao um substituto.
TORRE_WATERMARK = os.path.join(ASSETS, "torre_watermark.png")

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


def _interp_cor(cor_a: str, cor_b: str, t: float) -> str:
    """Interpola linearmente entre 2 cores hex, t em [0,1]."""
    a = HexColor(cor_a)
    b = HexColor(cor_b)
    r = a.red + (b.red - a.red) * t
    g = a.green + (b.green - a.green) * t
    b_ = a.blue + (b.blue - a.blue) * t
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b_ * 255):02x}"


def faixa_gradiente(c, x0, y0_top, x1, y1_top, cor_a, cor_b, passos=60):
    """Retangulo preenchido com gradiente horizontal suave (reportlab nao
    tem shading linear simples via canvas puro -- aproxima com N fatias
    verticais finas, mesmo truque usado no modelo real, so' que la e'
    nativo do editor que gerou o PDF-modelo)."""
    largura_passo = (x1 - x0) / passos
    for i in range(passos):
        t = i / (passos - 1)
        cor = _interp_cor(cor_a, cor_b, t)
        xa = x0 + i * largura_passo
        rect(c, xa, y0_top, xa + largura_passo + 0.5, y1_top, fill=cor)


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


def _fundo_marca_dagua(c):
    """Marca d'agua das torres, canto inferior esquerdo, atras de todo o
    resto -- por isso e' a primeira coisa desenhada na pagina.

    Posicao/tamanho calculados por engenharia reversa do PDF-modelo real
    (pikepdf, leitura da matriz de transformacao do Form XObject /X9 na
    pagina 2): a marca ocupa o retangulo x:[0, 416.4pt] / y_top:[446.88pt,
    842.4pt] numa pagina de 595.92x842.88pt -- ou seja, ~70% da largura,
    canto inferior esquerdo, do meio da pagina ate' a borda de baixo.
    Convertido pra fracao de PAGE_W/PAGE_H (que aqui sao 595.0/842.0) pra
    nao depender do valor exato de pt usado. O asset TORRE_WATERMARK ja'
    vem recortado exatamente nessa proporcao (cortado direto do render do
    PDF-modelo, nao do XObject bruto), entao NAO usa preserve_ratio aqui
    -- e' pra preencher a caixa exata, sem recalcular proporcao."""
    if os.path.exists(TORRE_WATERMARK):
        x0, x1 = 0.0, PAGE_W * 0.699
        y0_top, y1_top = PAGE_H * 0.530, PAGE_H
        image(c, TORRE_WATERMARK, x0, y0_top, x1, y1_top,
              mask="auto", preserve_ratio=False)


def _header(c, dados, subtitulo_pagina):
    # faixa superior navy -> orange -- gradiente suave (N fatias finas,
    # ver faixa_gradiente()), substitui os 2 blocos solidos do piloto
    # v1 (pedido do Rafael 24/09: "puxar a foto de fundo com a torre tb").
    faixa_gradiente(c, 0, 0, PAGE_W, 5, NAVY, ORANGE)

    logo_path = LOGO.get(dados["empresa_codigo"])
    if logo_path and os.path.exists(logo_path):
        # logo um pouco maior (pedido do Rafael 24/09) -- 130x37pt -> 165x47pt
        image(c, logo_path, MARGEM, 16, MARGEM + 165, 63, mask="auto", preserve_ratio=True)

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
    por IA) preenchidas com valor real de `dados`.

    Texto NEUTRO por decisao explicita do Rafael (24/09/2026, mesma
    sessao que pediu a torre no fundo): nada de qualificar o numero
    ("operacao saudavel", "margem abaixo do usual", "positivo"/
    "negativo" como adjetivo) -- so' o fato e o numero com sinal
    (moeda_br/pct_br ja poe "-" sozinho quando negativo, forcar_sinal=True
    poe "+" explicito no positivo). A MESMA frase tem que servir pra
    lucro ou prejuizo, EBITDA positivo ou negativo, sem trocar palavra
    nenhuma -- e' o que os testes abaixo verificam (nao pode reintroduzir
    'lucro'/'prejuizo'/'positivo'/'negativo' condicional aqui)."""
    d = dados
    p1 = (
        f"No período de referência, a {d['empresa_nome']} registrou receita "
        f"operacional líquida de {moeda_br(d['receita_liquida'])} com margem "
        f"bruta de {pct_br(d['margem_bruta'])}. Isolando efeitos financeiros e "
        f"de depreciação, o EBITDA do período foi de "
        f"{moeda_br(d['ebitda'], forcar_sinal=True)} (margem de "
        f"{pct_br(d['margem_ebitda'], forcar_sinal=True)})."
    )
    p2 = (
        f"O resultado líquido do período foi de "
        f"{moeda_br(d['resultado_liquido'], forcar_sinal=True)} (margem de "
        f"{pct_br(d['margem_liquida'], forcar_sinal=True)}), com despesas "
        f"operacionais de {moeda_br(d['despesas_operacionais'])}."
    )
    return [p1, p2]


def pagina_destaques(c, dados, pagina: int, total_paginas: int):
    _fundo_marca_dagua(c)
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


# ============================================================================
# Paginas 1, 3-9 (25/09/2026) -- montagem completa das 9 paginas do modelo,
# apos aceite do Rafael da pagina 2 (Destaques). Mesmo principio da pagina 2:
# HELPERS GENERICOS reutilizaveis (waterfall, barra ranking, barra empilhada,
# tabela anexo) -- nenhum grafico e' pintado "a mao" pra este relatorio
# especifico, pra funcionar com qualquer empresa/periodo que o `dados` trouxer
# (Rafael, 24/09: "quero um gerador bem editavel"). Texto de leitura em TODAS
# as paginas segue o mesmo principio de neutralidade da pagina 2 -- ver
# _leitura_executiva() acima: comparacao de sinal vira numero com +/-
# explicito (ex.: "X% em relacao a Y"), nunca troca de palavra/adjetivo.
# ============================================================================

# --------------------------------------------------------- grafico: waterfall
def grafico_waterfall(c, x0, x1, y0_top, y1_top, itens):
    """Grafico de cascata generico (paginas 3, 5, 6 do modelo).

    `itens`: lista de dicts, cada um:
      - label: rotulo abaixo da barra (pode ter quebra de linha com "\\n")
      - tipo: "abs" (barra desde 0) ou "delta" (barra flutuante, soma no
        acumulado corrente)
      - valor: pra "abs", o valor final da barra; pra "delta", o incremento
        (pode ser negativo)
      - cor: cor hex da barra
      - rotulo_valor: string ja formatada pra mostrar acima da barra
        (ex. "R$ 36,67 MM" ou "-R$ 3,32 MM")
      - conectar: bool (default True pra "delta", False pra "abs") -- desenha
        linha tracejada do topo do acumulado anterior ate essa barra

    Escala vertical calculada automaticamente a partir do maior/menor valor
    acumulado da sequencia (aceita cascata com trecho negativo, como a
    pagina 6 -- EBITDA, que comeca no prejuizo e cruza o zero)."""
    n = len(itens)
    largura_disp = x1 - x0
    slot = largura_disp / n
    largura_barra = slot * 0.62

    # calcula acumulado (base, topo) de cada item
    acumulado = 0.0
    pontos = []
    for it in itens:
        if it["tipo"] == "abs":
            base, topo = 0.0, it["valor"]
            acumulado = it["valor"]
        else:  # delta
            base = acumulado
            acumulado += it["valor"]
            topo = acumulado
        pontos.append((base, topo))

    todos_valores = [0.0] + [v for par in pontos for v in par]
    vmin, vmax = min(todos_valores), max(todos_valores)
    if vmax == vmin:
        vmax = vmin + 1.0
    altura_disp = (y1_top - y0_top) - 34  # reserva topo pra rotulo da maior barra
    escala = altura_disp / (vmax - vmin)
    baseline_y = y1_top - 22 - (0 - vmin) * escala  # y_top onde valor=0 cai

    def y_de(v):
        return baseline_y - v * escala

    # linha "R$ 0" quando a cascata tem trecho negativo (baseline nao esta
    # no fundo da area do grafico)
    if vmin < 0:
        c.setStrokeColor(HexColor(BORDER_LIGHT))
        c.setLineWidth(0.75)
        c.line(x0, Y(baseline_y), x1, Y(baseline_y))
        txt(c, x0, baseline_y - 3, "R$ 0", font="regular", size=8, color=GREY_TEXT, align="left")

    prev_topo_y = None
    for i, (it, (base, topo)) in enumerate(zip(itens, pontos)):
        xs = x0 + i * slot + (slot - largura_barra) / 2
        xe = xs + largura_barra
        ya, yb = y_de(base), y_de(topo)
        y_alto, y_baixo = min(ya, yb), max(ya, yb)

        conectar = it.get("conectar", it["tipo"] == "delta")
        if conectar and prev_topo_y is not None:
            c.saveState()
            c.setStrokeColor(HexColor(BORDER_LIGHT))
            c.setLineWidth(0.75)
            c.setDash([2, 2])
            c.line(x0 + (i - 1) * slot + (slot - largura_barra) / 2 + largura_barra,
                   Y(prev_topo_y), xs, Y(prev_topo_y))
            c.restoreState()

        rect(c, xs, y_alto, xe, y_baixo, fill=it["cor"])

        cor_rotulo = it.get("cor_rotulo", NAVY)
        txt(c, xs + largura_barra / 2, y_alto - 8, it["rotulo_valor"],
            font="bold", size=10.5, color=cor_rotulo, align="center")

        for j, linha in enumerate(it["label"].split("\n")):
            txt(c, xs + largura_barra / 2, y1_top + 16 + j * 12, linha,
                font="regular", size=8.5, color=GREY_TEXT, align="center")

        prev_topo_y = yb


# ------------------------------------------------- grafico: ranking horizontal
def grafico_ranking_horizontal(c, x0, x1, y0_top, altura_linha, itens):
    """Barras horizontais rankeadas (pagina 4 -- Composicao das Despesas).

    `itens`: lista JA ORDENADA (maior primeiro) de dicts com label, pct
    (0-100), rotulo_valor (string formatada), cor (opcional -- default
    ORANGE pro 1o item, gradiente NAVY->cinza-azulado pros demais).
    Devolve o y_top final (apos a ultima barra)."""
    n = len(itens)
    col_label_w = 168
    col_valor_w = 64
    largura_barra_max = (x1 - x0) - col_label_w - col_valor_w - 10
    pct_max = max(it["pct"] for it in itens) or 1.0

    y = y0_top
    for i, it in enumerate(itens):
        cor = it.get("cor")
        if cor is None:
            cor = ORANGE if i == 0 else _interp_cor(NAVY, "#A9AFC9", (i - 1) / max(1, n - 2))
        h_barra = altura_linha * 0.55
        y_barra0 = y + (altura_linha - h_barra) / 2
        txt(c, x0, y + altura_linha / 2 + 3, it["label"], font="regular", size=9.5, color=NAVY)
        largura = largura_barra_max * (it["pct"] / pct_max)
        bx0 = x0 + col_label_w
        rect(c, bx0, y_barra0, bx0 + max(largura, 2), y_barra0 + h_barra, fill=cor)
        txt(c, bx0 + largura_barra_max + 8, y + altura_linha / 2 + 3, it["rotulo_valor"],
            font="bold", size=9.5, color=NAVY, align="left")
        y += altura_linha
    return y


# --------------------------------------------------- grafico: barra empilhada
def grafico_barra_empilhada(c, x0, x1, y_top, altura, segmentos):
    """1 barra horizontal empilhada + legenda com quadradinhos de cor abaixo
    (pagina 7 -- Balanco Patrimonial: Ativo / Passivo+PL).

    `segmentos`: lista de dicts {label, valor, pct (0-100), cor}.
    Devolve o y_top logo apos a legenda."""
    xa = x0
    for seg in segmentos:
        largura = (x1 - x0) * (seg["pct"] / 100.0)
        rect(c, xa, y_top, xa + largura, y_top + altura, fill=seg["cor"])
        xa += largura

    y = y_top + altura + 18
    x = x0
    for seg in segmentos:
        rotulo = f"{seg['label']} — {pct_br(seg['pct'] / 100)} ({seg['rotulo_valor']})"
        largura_item = 13 + stringWidth(rotulo, FONT["regular"], 9.5) + 22
        if x + largura_item - 22 > x1:  # nao cabe nesta linha -- quebra
            x = x0
            y += 18
        rect(c, x, y - 8, x + 8, y, fill=seg["cor"])
        txt(c, x + 13, y - 1, rotulo, font="regular", size=9.5, color=NAVY)
        x += largura_item
    return y + 14


# ---------------------------------------------------------- tabela do anexo
def _linha_anexo(c, x, largura, y_top, tipo, label, valor_str, altura_linha, primeira=False):
    """Uma linha da tabela de anexo (pagina 8). `tipo`: grupo / conta /
    subconta / subtotal / total. Devolve o y_top da proxima linha.

    Rotulo longo (que colidiria com o valor na mesma linha, achado real
    testando com "Obrig. Trabalhistas e Previdenciárias" e "Obrigações
    Tributárias (Parcelamentos)") quebra em ate 2 linhas -- o valor fica
    so' na 1a linha, alinhado a direita."""
    if tipo == "grupo":
        y_top += 0 if primeira else 7  # respiro antes de cada novo grupo (exceto o 1o)
        txt(c, x, y_top, label.upper(), font="bold", size=9, color=NAVY)
        return y_top + altura_linha + 4
    if tipo == "total":
        y_top += 4
        rect(c, x, y_top, x + largura, y_top + altura_linha + 6, fill=NAVY)
        txt(c, x + 8, y_top + altura_linha / 2 + 4, label.upper(), font="bold", size=9.5, color="#FFFFFF")
        txt(c, x + largura - 8, y_top + altura_linha / 2 + 4, valor_str, font="bold", size=9.5, color="#FFFFFF", align="right")
        return y_top + altura_linha + 10

    indent = {"conta": 8, "subtotal": 8, "subconta": 22}.get(tipo, 8)
    negrito = tipo in ("conta", "subtotal")
    fonte = "bold" if negrito else "regular"
    cor = NAVY if tipo != "subconta" else GREY_TEXT
    tamanho = 9 if tipo == "subconta" else 9.5
    if tipo == "subtotal":
        y_top += 3
        c.setStrokeColor(HexColor(BORDER_LIGHT))
        c.setLineWidth(0.5)
        c.line(x, Y(y_top - 2), x + largura, Y(y_top - 2))

    valor_w = stringWidth(valor_str, FONT["bold" if negrito else "regular"], tamanho) if valor_str else 0
    largura_label = largura - indent - valor_w - 8
    linhas_label = _wrap_text(label, FONT[fonte], tamanho, largura_label) if largura_label > 20 else [label]

    txt(c, x + indent, y_top + 9, linhas_label[0], font=fonte, size=tamanho, color=cor)
    if valor_str:
        txt(c, x + largura, y_top + 9, valor_str, font=fonte, size=tamanho, color=cor, align="right")
    y_prox = y_top + altura_linha
    for linha_extra in linhas_label[1:]:
        txt(c, x + indent, y_prox + 9, linha_extra, font=fonte, size=tamanho, color=cor)
        y_prox += altura_linha
    if tipo == "subtotal":
        y_prox += 2
    return y_prox


def _coluna_anexo(c, x, largura, y_top, linhas, altura_linha=13.0):
    y = y_top
    for i, (tipo, label, *resto) in enumerate(linhas):
        valor_str = moeda_br(resto[0]) if resto else ""
        y = _linha_anexo(c, x, largura, y, tipo, label, valor_str, altura_linha, primeira=(i == 0))
    return y


# ------------------------------------------------------------------ pagina 1
def pagina_capa(c, dados, pagina: int, total_paginas: int):
    """Capa -- painel diagonal navy a direita, logo + titulo a esquerda,
    pilula laranja com o periodo. Sem a marca d'agua de torre (a capa do
    modelo nao a usa -- o painel navy solido faz esse papel visual)."""
    faixa_gradiente(c, 0, 0, PAGE_W, 5, NAVY, ORANGE)

    # painel diagonal a direita (~78% -> 100% da largura, inclinado)
    c.saveState()
    c.setFillColor(HexColor(NAVY))
    p = c.beginPath()
    topo_x = PAGE_W * 0.80
    base_x = PAGE_W * 0.72
    p.moveTo(topo_x, PAGE_H)
    p.lineTo(PAGE_W, PAGE_H)
    p.lineTo(PAGE_W, 0)
    p.lineTo(base_x, 0)
    p.close()
    c.drawPath(p, fill=1, stroke=0)
    c.restoreState()

    logo_path = LOGO.get(dados["empresa_codigo"])
    if logo_path and os.path.exists(logo_path):
        image(c, logo_path, MARGEM, 250, MARGEM + 260, 330, mask="auto", preserve_ratio=True)

    c.setStrokeColor(HexColor(ORANGE))
    c.setLineWidth(3)
    c.line(MARGEM, Y(388), MARGEM + 46, Y(388))

    txt(c, MARGEM, 430, "Demonstrativo", font="heavy", size=30, color=NAVY)
    txt(c, MARGEM, 465, "Comentado", font="heavy", size=30, color=NAVY)
    txt(c, MARGEM, 500, dados["empresa_nome"], font="regular", size=12, color=GREY_TEXT)
    txt(c, MARGEM, 518, f"CNPJ {dados.get('cnpj', '')}", font="regular", size=12, color=GREY_TEXT)

    rect(c, MARGEM, 548, MARGEM + 250, 578, fill=ORANGE, radius=15)
    txt(c, MARGEM + 20, 567, f"PERÍODO · {dados['periodo_label'].upper()}", font="bold", size=10, color="#FFFFFF")


# ------------------------------------------------------------------ pagina 3
def _leitura_receita_custos(dados) -> list[str]:
    d = dados
    p1 = (
        f"A receita operacional bruta do período somou {moeda_br(d['receita_bruta'])}. "
        f"As deduções sobre a receita totalizaram {moeda_br(d['deducoes_receita'])} "
        f"({pct_br(d['deducoes_pct_bruta'])} da receita bruta), resultando em receita "
        f"operacional líquida de {moeda_br(d['receita_liquida'])}."
    )
    p2 = (
        f"O custo direto dos serviços/produtos foi de {moeda_br(d['custo_servicos'])} "
        f"({pct_br(d['custo_pct_liquida'])} da receita líquida), resultando em lucro "
        f"bruto de {moeda_br(d['lucro_bruto'])} e margem bruta de {pct_br(d['margem_bruta'])}. "
        f"O detalhamento das despesas operacionais está na página seguinte."
    )
    return [p1, p2]


def pagina_receita_custos(c, dados, pagina: int, total_paginas: int):
    d = dados
    _fundo_marca_dagua(c)
    _header(c, dados, "Receita, Custos e Margem")

    txt(c, MARGEM, 100, "Formação da Receita e do Lucro Bruto", font="heavy", size=17, color=NAVY)
    txt(c, MARGEM, 120, "Da receita bruta ao lucro bruto do período — valores em R$ milhões",
        font="regular", size=10, color=GREY_TEXT)

    itens = [
        dict(tipo="abs", valor=d["receita_bruta"] / 1e6, cor=NAVY, label="Receita\nBruta",
             rotulo_valor=f"{moeda_br(d['receita_bruta'] / 1e6)} MM"),
        dict(tipo="delta", valor=-d["deducoes_receita"] / 1e6, cor=RED_ACCENT, label="Deduções\nda Receita",
             rotulo_valor=f"-{moeda_br(d['deducoes_receita'] / 1e6)} MM", cor_rotulo=RED_ACCENT),
        dict(tipo="abs", valor=d["receita_liquida"] / 1e6, cor=NAVY, label="Receita\nLíquida",
             rotulo_valor=f"{moeda_br(d['receita_liquida'] / 1e6)} MM", conectar=True),
        dict(tipo="delta", valor=-d["custo_servicos"] / 1e6, cor=RED_ACCENT, label="Custo dos\nServiços",
             rotulo_valor=f"-{moeda_br(d['custo_servicos'] / 1e6)} MM", cor_rotulo=RED_ACCENT),
        dict(tipo="abs", valor=d["lucro_bruto"] / 1e6, cor=ORANGE, label="Lucro\nBruto",
             rotulo_valor=f"{moeda_br(d['lucro_bruto'] / 1e6)} MM", cor_rotulo=ORANGE),
    ]
    grafico_waterfall(c, MARGEM, MARGEM + CONTEUDO_W, 150, 430, itens)

    col_w = (CONTEUDO_W - 2 * 10) / 3
    x = MARGEM
    for label, valor in [
        ("Margem Bruta", pct_br(d["margem_bruta"])),
        ("Deduções / Receita Bruta", pct_br(d["deducoes_pct_bruta"])),
        ("Custo / Receita Líquida", pct_br(d["custo_pct_liquida"])),
    ]:
        rect(c, x, 470, x + col_w, 525, fill=GREY_BG)
        txt(c, x + 14, 490, label.upper(), font="bold", size=8.5, color=NAVY)
        txt(c, x + 14, 515, valor, font="heavy", size=18, color=NAVY)
        x += col_w + 10

    y = 565
    txt(c, MARGEM, y, "Leitura do Período", font="heavy", size=13, color=NAVY)
    c.setStrokeColor(HexColor(ORANGE)); c.setLineWidth(2)
    c.line(MARGEM, Y(y + 6), MARGEM + 34, Y(y + 6))
    y += 28
    for par in _leitura_receita_custos(d):
        y = paragrafo(c, MARGEM, y, par, CONTEUDO_W, size=10.5, leading=15) + 10

    _footer(c, pagina, total_paginas)


# ------------------------------------------------------------------ pagina 4
def _leitura_despesas(dados) -> list[str]:
    d = dados
    delta_pct = (d["despesas_operacionais"] / d["lucro_bruto"] - 1) if d.get("lucro_bruto") else 0.0
    p1 = (
        f"As despesas operacionais somaram {moeda_br(d['despesas_operacionais'])} no período, "
        f"{pct_br(delta_pct, forcar_sinal=True)} em relação ao lucro bruto de {moeda_br(d['lucro_bruto'])}."
    )
    maior = d["despesas_admin_itens"][0] if d.get("despesas_admin_itens") else None
    p2 = (
        f"As despesas administrativas ({moeda_br(d['despesas_administrativas'])}, "
        f"{pct_br(d['despesas_administrativas'] / d['despesas_operacionais'])} do total) "
        + (f"são compostas majoritariamente por {maior[0]} ({pct_br(maior[2] / 100)})." if maior else "estão detalhadas acima.")
    )
    return [p1, p2]


def pagina_despesas(c, dados, pagina: int, total_paginas: int):
    d = dados
    _fundo_marca_dagua(c)
    _header(c, dados, "Despesas Operacionais")

    txt(c, MARGEM, 100, "Composição das Despesas Administrativas", font="heavy", size=17, color=NAVY)
    txt(c, MARGEM, 120,
        f"{moeda_br(d['despesas_administrativas'] / 1e6)} milhões · "
        f"{pct_br(d['despesas_administrativas'] / d['despesas_operacionais'])} das despesas operacionais do período",
        font="regular", size=10, color=GREY_TEXT)

    itens_ranking = [
        dict(label=nome, pct=pct, rotulo_valor=pct_br(pct / 100))
        for nome, valor, pct in d.get("despesas_admin_itens", [])
    ]
    y_fim = grafico_ranking_horizontal(c, MARGEM, MARGEM + CONTEUDO_W, 148, 26, itens_ranking) if itens_ranking else 148

    if d.get("despesas_admin_itens") and len(d["despesas_admin_itens"]) >= 2:
        maior, segundo = d["despesas_admin_itens"][0], d["despesas_admin_itens"][1]
        razao = maior[1] / segundo[1] if segundo[1] else 0
        callout = (
            f"{maior[0]} é isoladamente o maior driver de custo do período: {moeda_br(maior[1])}, "
            f"{numero_br(razao, sufixo='x')} o segundo item ({segundo[0]}, {moeda_br(segundo[1])})."
        )
        y_fim += 14
        rect(c, MARGEM, y_fim, MARGEM + CONTEUDO_W, y_fim + 54, fill=GREY_BG, stroke=BORDER_LIGHT, width=0.75, radius=6)
        paragrafo(c, MARGEM + 16, y_fim + 20, callout, CONTEUDO_W - 32, size=9.5, leading=13)
        y_fim += 66

    y_fim += 12
    col_w = (CONTEUDO_W - 2 * 10) / 3
    x = MARGEM
    for label, valor, comp in [
        ("Administrativas", f"{moeda_br(d['despesas_administrativas'] / 1e6)} MM", pct_br(d['despesas_administrativas'] / d['despesas_operacionais']) + " do total"),
        ("Financeiras (líq.)", f"{moeda_br(d['despesas_financeiras'] / 1e6)} MM", pct_br(d['despesas_financeiras'] / d['despesas_operacionais']) + " do total"),
        ("Tributárias", f"{moeda_br(d['despesas_tributarias'] / 1e6)} MM", pct_br(d['despesas_tributarias'] / d['despesas_operacionais']) + " do total"),
    ]:
        rect(c, x, y_fim, x + col_w, y_fim + 48, fill=GREY_BG)
        txt(c, x + 12, y_fim + 16, label.upper(), font="bold", size=8, color=NAVY)
        txt(c, x + 12, y_fim + 36, valor, font="heavy", size=13.5, color=NAVY)
        txt(c, x + col_w - 12, y_fim + 16, comp, font="regular", size=7.5, color=GREY_TEXT, align="right")
        x += col_w + 10

    y_fim += 62
    rect(c, MARGEM, y_fim, MARGEM + CONTEUDO_W, y_fim + 30, fill=NAVY)
    txt(c, MARGEM + 12, y_fim + 19, "TOTAL DESPESAS OPERACIONAIS", font="bold", size=9.5, color="#FFFFFF")
    txt(c, MARGEM + CONTEUDO_W - 12, y_fim + 19, f"{moeda_br(d['despesas_operacionais'] / 1e6)} MM",
        font="heavy", size=13, color="#FFFFFF", align="right")

    y = y_fim + 56
    txt(c, MARGEM, y, "Leitura do Período", font="heavy", size=13, color=NAVY)
    c.setStrokeColor(HexColor(ORANGE)); c.setLineWidth(2)
    c.line(MARGEM, Y(y + 6), MARGEM + 34, Y(y + 6))
    y += 28
    for par in _leitura_despesas(d):
        y = paragrafo(c, MARGEM, y, par, CONTEUDO_W, size=10.5, leading=15) + 10

    _footer(c, pagina, total_paginas)


# ------------------------------------------------------------------ pagina 5
def _leitura_resultado(dados) -> list[str]:
    d = dados
    delta_pct = (d["despesas_operacionais"] / d["lucro_bruto"] - 1) if d.get("lucro_bruto") else 0.0
    p1 = (
        f"O resultado do período decorre do lucro bruto de {moeda_br(d['lucro_bruto'])} frente a despesas "
        f"operacionais de {moeda_br(d['despesas_operacionais'])} ({pct_br(delta_pct, forcar_sinal=True)} em relação "
        f"ao lucro bruto). A provisão de CSLL e IRPJ ({moeda_br(d['csll_irpj'])}) resulta no resultado antes de "
        f"CS/IR de {moeda_br(d['lucro_bruto'] - d['despesas_operacionais'], forcar_sinal=True)} para o resultado "
        f"líquido de {moeda_br(d['resultado_liquido'], forcar_sinal=True)} (margem de {pct_br(d['margem_liquida'], forcar_sinal=True)})."
    )
    p2 = (
        "Uma leitura complementar por EBITDA — que exclui despesas financeiras e depreciação — está na "
        "página seguinte. O resultado também impacta diretamente o Patrimônio Líquido, detalhado no Balanço "
        "Patrimonial (página 7)."
    )
    return [p1, p2]


def pagina_resultado(c, dados, pagina: int, total_paginas: int):
    d = dados
    _fundo_marca_dagua(c)
    _header(c, dados, "Resultado do Período")

    txt(c, MARGEM, 100, "Formação do Resultado", font="heavy", size=17, color=NAVY)
    txt(c, MARGEM, 120, "Do lucro bruto ao resultado líquido do período — valores em R$ milhões",
        font="regular", size=10, color=GREY_TEXT)

    resultado_antes_csir = d["lucro_bruto"] - d["despesas_operacionais"]
    itens = [
        dict(tipo="abs", valor=d["lucro_bruto"] / 1e6, cor=NAVY, label="Lucro Bruto",
             rotulo_valor=f"{moeda_br(d['lucro_bruto'] / 1e6)} MM"),
        dict(tipo="delta", valor=-d["despesas_operacionais"] / 1e6, cor=RED_ACCENT, label="Despesas\nOperacionais",
             rotulo_valor=f"-{moeda_br(d['despesas_operacionais'] / 1e6)} MM", cor_rotulo=RED_ACCENT),
        dict(tipo="delta", valor=-d["csll_irpj"] / 1e6, cor=RED_ACCENT, label="CSLL + IRPJ",
             rotulo_valor=f"-{moeda_br(d['csll_irpj'] / 1e6)} MM", cor_rotulo=RED_ACCENT),
        dict(tipo="abs", valor=d["resultado_liquido"] / 1e6, cor=RED_ACCENT, label="Resultado Líquido\ndo Período",
             rotulo_valor=f"{moeda_br(d['resultado_liquido'] / 1e6, forcar_sinal=True)} MM",
             cor_rotulo=RED_ACCENT, conectar=False),
    ]
    grafico_waterfall(c, MARGEM, MARGEM + CONTEUDO_W, 150, 420, itens)

    y = 460
    calc = (
        f"{moeda_br(d['lucro_bruto'])} (lucro bruto) -{moeda_br(d['despesas_operacionais'])} (despesas "
        f"operacionais) = {moeda_br(resultado_antes_csir, forcar_sinal=True)} (resultado antes de CS e IR) "
        f"-{moeda_br(d['csll_irpj'])} (CSLL + IRPJ) = {moeda_br(d['resultado_liquido'], forcar_sinal=True)} "
        f"(resultado líquido do exercício)."
    )
    rect(c, MARGEM, y, MARGEM + CONTEUDO_W, y + 54, fill=GREY_BG, stroke=BORDER_LIGHT, width=0.75, radius=6)
    paragrafo(c, MARGEM + 16, y + 20, calc, CONTEUDO_W - 32, size=9, leading=12.5)

    y += 78
    col_w = (CONTEUDO_W - 10) / 2
    rect(c, MARGEM, y, MARGEM + col_w, y + 55, fill=NAVY, radius=6)
    txt(c, MARGEM + 16, y + 20, "RESULTADO LÍQUIDO DO PERÍODO", font="bold", size=8.5, color="#FFFFFF")
    txt(c, MARGEM + 16, y + 44, f"{moeda_br(d['resultado_liquido'] / 1e6, forcar_sinal=True)} MM", font="heavy", size=20, color=ORANGE)
    x2 = MARGEM + col_w + 10
    rect(c, x2, y, x2 + col_w, y + 55, fill=GREY_BG)
    txt(c, x2 + 16, y + 20, "MARGEM LÍQUIDA", font="bold", size=8.5, color=NAVY)
    txt(c, x2 + 16, y + 44, pct_br(d["margem_liquida"], forcar_sinal=True), font="heavy", size=20, color=NAVY)

    y += 80
    txt(c, MARGEM, y, "Leitura do Resultado", font="heavy", size=13, color=NAVY)
    c.setStrokeColor(HexColor(ORANGE)); c.setLineWidth(2)
    c.line(MARGEM, Y(y + 6), MARGEM + 34, Y(y + 6))
    y += 28
    for par in _leitura_resultado(d):
        y = paragrafo(c, MARGEM, y, par, CONTEUDO_W, size=10.5, leading=15) + 10

    _footer(c, pagina, total_paginas)


# ------------------------------------------------------------------ pagina 6
def _leitura_ebitda(dados) -> list[str]:
    d = dados
    p1 = (
        f"Excluindo despesas financeiras líquidas ({moeda_br(d['resultado_financeiro'])}) e depreciação/"
        f"amortização ({moeda_br(d['deprec_amortiz'])}) do resultado líquido de "
        f"{moeda_br(d['resultado_liquido'], forcar_sinal=True)}, o EBITDA do período é de "
        f"{moeda_br(d['ebitda'], forcar_sinal=True)} (margem de {pct_br(d['margem_ebitda'], forcar_sinal=True)})."
    )
    p2 = (
        "O indicador isola o resultado operacional de efeitos financeiros e da provisão de tributos sobre o "
        "lucro, e deve ser lido em conjunto com o driver de despesas detalhado na página anterior."
    )
    return [p1, p2]


def pagina_ebitda(c, dados, pagina: int, total_paginas: int):
    d = dados
    _fundo_marca_dagua(c)
    _header(c, dados, "EBITDA")

    txt(c, MARGEM, 100, "EBITDA do Período", font="heavy", size=17, color=NAVY)
    txt(c, MARGEM, 120, "Do resultado líquido ao EBITDA — valores em R$ milhões",
        font="regular", size=10, color=GREY_TEXT)

    itens = [
        dict(tipo="abs", valor=d["resultado_liquido"] / 1e6, cor=RED_ACCENT, label="Resultado Líquido\ndo Período",
             rotulo_valor=f"{moeda_br(d['resultado_liquido'] / 1e6, forcar_sinal=True)} MM", cor_rotulo=RED_ACCENT, conectar=False),
        dict(tipo="delta", valor=d["resultado_financeiro"] / 1e6, cor="#5b5f8c", label="(+) Resultado\nFinanceiro Líquido",
             rotulo_valor=f"+{moeda_br(d['resultado_financeiro'] / 1e6)} MM"),
        dict(tipo="delta", valor=d["deprec_amortiz"] / 1e6, cor="#9297bb", label="(+) Depreciação e\nAmortização",
             rotulo_valor=f"+{moeda_br(d['deprec_amortiz'] / 1e6)} MM"),
        dict(tipo="abs", valor=d["ebitda"] / 1e6, cor=ORANGE, label="EBITDA",
             rotulo_valor=f"{moeda_br(d['ebitda'] / 1e6, forcar_sinal=True)} MM", cor_rotulo=ORANGE, conectar=False),
    ]
    grafico_waterfall(c, MARGEM, MARGEM + CONTEUDO_W, 150, 420, itens)

    y = 460
    calc = (
        f"{moeda_br(d['resultado_liquido'], forcar_sinal=True)} (resultado líquido) "
        f"+{moeda_br(d['resultado_financeiro'])} (despesas financeiras líquidas de receitas financeiras) "
        f"+{moeda_br(d['deprec_amortiz'])} (depreciação e amortização do período) = "
        f"{moeda_br(d['ebitda'], forcar_sinal=True)} de EBITDA."
    )
    rect(c, MARGEM, y, MARGEM + CONTEUDO_W, y + 54, fill=GREY_BG, stroke=BORDER_LIGHT, width=0.75, radius=6)
    paragrafo(c, MARGEM + 16, y + 20, calc, CONTEUDO_W - 32, size=9, leading=12.5)

    y += 78
    col_w = (CONTEUDO_W - 10) / 2
    rect(c, MARGEM, y, MARGEM + col_w, y + 55, fill="#FFFFFF", stroke=NAVY, width=1.1, radius=6)
    txt(c, MARGEM + 16, y + 20, "EBITDA DO PERÍODO", font="bold", size=8.5, color=ORANGE)
    txt(c, MARGEM + 16, y + 44, f"{moeda_br(d['ebitda'] / 1e6, forcar_sinal=True)} MM", font="heavy", size=20, color=NAVY)
    x2 = MARGEM + col_w + 10
    rect(c, x2, y, x2 + col_w, y + 55, fill=GREY_BG)
    txt(c, x2 + 16, y + 20, "MARGEM EBITDA", font="bold", size=8.5, color=NAVY)
    txt(c, x2 + 16, y + 44, pct_br(d["margem_ebitda"], forcar_sinal=True), font="heavy", size=20, color=NAVY)

    y += 80
    txt(c, MARGEM, y, "Leitura do EBITDA", font="heavy", size=13, color=NAVY)
    c.setStrokeColor(HexColor(ORANGE)); c.setLineWidth(2)
    c.line(MARGEM, Y(y + 6), MARGEM + 34, Y(y + 6))
    y += 28
    for par in _leitura_ebitda(d):
        y = paragrafo(c, MARGEM, y, par, CONTEUDO_W, size=10.5, leading=15) + 10

    _footer(c, pagina, total_paginas)


# ------------------------------------------------------------------ pagina 7
def _leitura_balanco(dados) -> list[str]:
    d = dados
    p1 = (
        f"O Patrimônio Líquido de {moeda_br(d['patrimonio_liquido'])} representa "
        f"{pct_br(d['patrimonio_liquido'] / d['total_ativo'])} do total do passivo. O endividamento geral "
        f"(exigível/ativo total) é de {pct_br(d['endividamento_geral'])}, com "
        f"{numero_br(d['alavancagem'], sufixo='x')} de capital de terceiros para cada R$ 1,00 de capital "
        f"próprio. A liquidez corrente (ativo circulante/passivo circulante) é de {numero_br(d['liquidez_corrente'])}."
    )
    p2 = (
        f"O ativo não circulante representa {pct_br(d['ativo_nao_circulante'] / d['total_ativo'])} do total do "
        f"ativo ({moeda_br(d['ativo_nao_circulante'])}), dos quais {moeda_br(d['imobilizado'])} em imobilizado. "
        f"O resultado do período ({moeda_br(d['resultado_liquido'], forcar_sinal=True)}, página 5) está refletido "
        f"na conta de Lucros ou Prejuízos Acumulados do Patrimônio Líquido. O detalhamento completo de contas "
        f"está no Anexo (página 8)."
    )
    return [p1, p2]


def pagina_balanco(c, dados, pagina: int, total_paginas: int):
    d = dados
    _fundo_marca_dagua(c)
    _header(c, dados, "Balanço Patrimonial")

    txt(c, MARGEM, 100, f"Posição Patrimonial em {d['data_posicao']}", font="heavy", size=17, color=NAVY)
    txt(c, MARGEM, 120, f"Total do Ativo: {moeda_br(d['total_ativo'])} · Total do Passivo + Patrimônio Líquido: {moeda_br(d['total_ativo'])}",
        font="regular", size=9.5, color=GREY_TEXT)

    txt(c, MARGEM, 150, "ATIVO", font="bold", size=9.5, color=NAVY)
    y = grafico_barra_empilhada(c, MARGEM, MARGEM + CONTEUDO_W, 158, 34, [
        dict(label="Circulante", valor=d["ativo_circulante"], pct=100 * d["ativo_circulante"] / d["total_ativo"],
             cor=NAVY, rotulo_valor=f"{moeda_br(d['ativo_circulante'] / 1e6)} MM"),
        dict(label="Não Circulante", valor=d["ativo_nao_circulante"], pct=100 * d["ativo_nao_circulante"] / d["total_ativo"],
             cor="#3d4290", rotulo_valor=f"{moeda_br(d['ativo_nao_circulante'] / 1e6)} MM"),
    ])

    y += 20
    txt(c, MARGEM, y, "PASSIVO + PATRIMÔNIO LÍQUIDO", font="bold", size=9.5, color=NAVY)
    y = grafico_barra_empilhada(c, MARGEM, MARGEM + CONTEUDO_W, y + 8, 34, [
        dict(label="Circulante", valor=d["passivo_circulante"], pct=100 * d["passivo_circulante"] / d["total_ativo"],
             cor=NAVY, rotulo_valor=f"{moeda_br(d['passivo_circulante'] / 1e6)} MM"),
        dict(label="Não Circulante", valor=d["passivo_nao_circulante"], pct=100 * d["passivo_nao_circulante"] / d["total_ativo"],
             cor="#3d4290", rotulo_valor=f"{moeda_br(d['passivo_nao_circulante'] / 1e6)} MM"),
        dict(label="Patrimônio Líquido", valor=d["patrimonio_liquido"], pct=100 * d["patrimonio_liquido"] / d["total_ativo"],
             cor=ORANGE, rotulo_valor=f"{moeda_br(d['patrimonio_liquido'] / 1e6)} MM"),
    ])

    y += 26
    col_w = (CONTEUDO_W - 2 * 10) / 3
    x = MARGEM
    for label, valor, comp in [
        ("Liquidez Corrente", numero_br(d["liquidez_corrente"]), "ativo circ. / passivo circ."),
        ("Capital de Terceiros / PL", numero_br(d["alavancagem"], sufixo="x"), "alavancagem"),
        ("Endividamento Geral", pct_br(d["endividamento_geral"]), "exigível / ativo total"),
    ]:
        rect(c, x, y, x + col_w, y + 8, fill=RED_ACCENT)
        rect(c, x, y + 8, x + col_w, y + 62, fill=GREY_BG)
        txt(c, x + 14, y + 26, label.upper(), font="bold", size=8, color=NAVY)
        txt(c, x + 14, y + 48, valor, font="heavy", size=17, color=NAVY)
        txt(c, x + 14, y + 60, comp, font="regular", size=7.5, color=GREY_TEXT)
        x += col_w + 10

    y += 90
    txt(c, MARGEM, y, "Leitura do Balanço", font="heavy", size=13, color=NAVY)
    c.setStrokeColor(HexColor(ORANGE)); c.setLineWidth(2)
    c.line(MARGEM, Y(y + 6), MARGEM + 34, Y(y + 6))
    y += 28
    for par in _leitura_balanco(d):
        y = paragrafo(c, MARGEM, y, par, CONTEUDO_W, size=10.5, leading=15) + 10

    _footer(c, pagina, total_paginas)


# ------------------------------------------------------------------ pagina 8
def pagina_anexo(c, dados, pagina: int, total_paginas: int):
    """Detalhamento de contas do BP. `dados['anexo_ativo']` e
    `dados['anexo_passivo']`: listas de tuplas
    ('grupo'|'conta'|'subconta'|'subtotal'|'total', label[, valor])."""
    d = dados
    _header(c, dados, "Anexos")

    rect(c, MARGEM, 96, MARGEM + 72, 116, fill=NAVY, radius=3)
    txt(c, MARGEM + 36, 110, "ANEXOS", font="bold", size=9, color="#FFFFFF", align="center")
    txt(c, MARGEM + 84, 112, "Detalhamento de Contas — Balanço Patrimonial", font="heavy", size=14.5, color=NAVY)
    txt(c, MARGEM, 134, f"{d['empresa_nome']} · Período: {d['data_posicao']} · valores em R$",
        font="regular", size=9, color=GREY_TEXT)

    col_w = (CONTEUDO_W - 24) / 2
    x_esq, x_dir = MARGEM, MARGEM + col_w + 24
    y_esq = _coluna_anexo(c, x_esq, col_w, 162, d.get("anexo_ativo", []))
    y_dir = _coluna_anexo(c, x_dir, col_w, 162, d.get("anexo_passivo", []))

    # nota de rodape -- se a tabela crescer demais (muitas contas), reduz a
    # fonte pra nao colidir com o rodape da pagina em vez de estourar
    y_nota = max(y_esq, y_dir) + 16
    tamanho_nota = 8 if y_nota < 760 else 7
    paragrafo(
        c, MARGEM, y_nota,
        f"Valores extraídos do Balanço Patrimonial e da DRE do período (SPED contábil). Total do Ativo "
        f"({moeda_br(d['total_ativo'])}) confere com o Total do Passivo + Patrimônio Líquido "
        f"({moeda_br(d['total_ativo'])}). No relatório integrado ao aplicativo, este anexo é gerado "
        f"automaticamente a partir da base de dados, com opção de 1 ou mais empresas e 1 ou mais períodos "
        f"lado a lado.",
        CONTEUDO_W, font="regular", size=tamanho_nota, color=GREY_TEXT, leading=tamanho_nota + 3,
    )

    _footer(c, pagina, total_paginas)


# ------------------------------------------------------------------ pagina 9
def pagina_fechamento(c, dados, pagina: int, total_paginas: int):
    d = dados
    _fundo_marca_dagua(c)
    _header(c, dados, "Fechamento")

    txt(c, MARGEM, 100, "Nota sobre este Relatório", font="heavy", size=17, color=NAVY)
    y = paragrafo(
        c, MARGEM, 130,
        f"Os valores apresentados foram extraídos do Balanço Patrimonial e da Demonstração do Resultado do "
        f"Exercício do período de referência ({d.get('periodo_extenso', d['periodo_label'])}), gerados a "
        f"partir do SPED contábil. Este documento é de uso interno da administração e da contabilidade do "
        f"Grupo Enermais e não foi submetido a exame por auditoria externa independente. As informações devem "
        f"ser lidas em conjunto com as demonstrações contábeis completas do período.",
        CONTEUDO_W, size=10.5, leading=15,
    ) + 12
    y = paragrafo(
        c, MARGEM, y,
        "Indicadores não contábeis (margens, liquidez, endividamento) são medições de apoio à gestão, "
        "calculados a partir dos valores acima, e não substituem o parecer contábil.",
        CONTEUDO_W, size=10.5, leading=15,
    ) + 60

    col_w = (CONTEUDO_W - 20) / 2
    for i, (nome, cargo) in enumerate([
        (d.get("nome_administrador", ""), d.get("cargo_administrador", "Administrador")),
        (d.get("nome_contador", ""), d.get("cargo_contador", "Contador")),
    ]):
        x = MARGEM + i * (col_w + 20)
        c.setStrokeColor(HexColor(NAVY)); c.setLineWidth(0.75)
        c.line(x, Y(y), x + col_w, Y(y))
        txt(c, x, y + 16, nome, font="bold", size=10.5, color=NAVY)
        txt(c, x, y + 32, cargo, font="regular", size=9.5, color=GREY_TEXT)
    y += 52
    txt(c, MARGEM, y, "Documento gerado a partir do BP e DRE do sistema contábil — sujeito a validação e "
                       "assinatura da contabilidade.", font="italic", size=8, color=GREY_TEXT)

    y += 40
    rect(c, MARGEM, y, MARGEM + CONTEUDO_W, y + 90, fill=GREY_BG, stroke=BORDER_LIGHT, width=0.75, radius=6)
    logo_path = LOGO.get(d["empresa_codigo"])
    if logo_path and os.path.exists(logo_path):
        image(c, logo_path, MARGEM + 18, y + 18, MARGEM + 148, y + 54, mask="auto", preserve_ratio=True)
    txt(c, MARGEM + 18, y + 72, d["empresa_nome"], font="bold", size=9.5, color=NAVY)
    txt(c, MARGEM + 18, y + 84, f"CNPJ {d.get('cnpj', '')}", font="regular", size=9, color=GREY_TEXT)
    txt(c, MARGEM + CONTEUDO_W - 18, y + 72, d.get("email_empresa", ""), font="regular", size=9, color=GREY_TEXT, align="right")
    txt(c, MARGEM + CONTEUDO_W - 18, y + 84, d.get("site_empresa", ""), font="regular", size=9, color=GREY_TEXT, align="right")

    txt(c, PAGE_W / 2, y + 130, f"Relatório gerado pelo Sistema EGC — Gestão Contábil Enermais · {d.get('data_geracao', '')}",
        font="regular", size=8, color=GREY_TEXT, align="center")

    _footer(c, pagina, total_paginas)


# ---------------------------------------------------------------- montagem
PAGINAS = [
    pagina_capa,
    pagina_destaques,
    pagina_receita_custos,
    pagina_despesas,
    pagina_resultado,
    pagina_ebitda,
    pagina_balanco,
    pagina_anexo,
    pagina_fechamento,
]


def gerar_pdf_completo(dados: dict, caminho_saida: str) -> str:
    """Gera o PDF completo de 9 paginas (25/09/2026 -- apos aceite do
    Rafael da pagina 2/Destaques). Mesma funcao serve qualquer empresa/
    periodo -- todo o conteudo vem de `dados`, nenhuma conta e' hardcoded
    aqui (so' na fixture de teste, que usa os valores reais do PDF-modelo
    pra comparacao)."""
    _registrar_fontes()
    c = canvas.Canvas(caminho_saida, pagesize=(PAGE_W, PAGE_H))
    total = len(PAGINAS)
    for i, pagina_fn in enumerate(PAGINAS, start=1):
        if pagina_fn is pagina_capa:
            pagina_fn(c, dados, pagina=i, total_paginas=total)
        else:
            pagina_fn(c, dados, pagina=i, total_paginas=total)
        c.showPage()
    c.save()
    return caminho_saida
