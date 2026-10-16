# -*- coding: utf-8 -*-
"""
EGC | Relatorio PDF de Creditos e Debitos de PIS/COFINS (v0.53.0, 16/10/2026) -- 5 paginas A4.

Mesmo motor visual do Demonstrativo Comentado (gerador_relatorio_comentado: ReportLab em canvas, fontes Poppins bundladas, paleta NAVY/ORANGE,
cabecalho com logo, rodape "Pagina X de Y", marca d'agua). Os graficos sao desenhados nativamente (sem matplotlib), na regra de cor do app:
azul para valores, LARANJA so' para negativo/reducao (sempre com sinal "-") e para o aviso de simulacao.

Paginas:  1 Capa  |  2 Resumo do mes  |  3 Credito  |  4 Debito e apuracao  |  5 Historico e observacoes.
Recebe o dicionario de `dados_relatorio_creditos.montar_dados`; nao fala com o banco.
SIMULACAO: se alguma versao usada nao e' a oficial, TODAS as paginas levam a faixa "SIMULACAO - NAO E A VERSAO OFICIAL".
"""
from __future__ import annotations

import io
from typing import Optional

from reportlab.lib.colors import HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import gerador_relatorio_comentado as g
from creditos_graficos import rotulo_comp
from formatacao import moeda_br, moeda_curta, pct_br

NAVY, ORANGE, GREY_TEXT, GREY_BG, BORDER = g.NAVY, g.ORANGE, g.GREY_TEXT, g.GREY_BG, g.BORDER_LIGHT
MARGEM, CONTEUDO_W, PAGE_W, PAGE_H = g.MARGEM, g.CONTEUDO_W, g.PAGE_W, g.PAGE_H
AZUL_MEDIO, AZUL_CLARO, AZUL_PALE = "#5C6091", "#9A9CC4", "#B4B9D6"
TOTAL_PAGINAS = 5


# ------------------------------------------------------------- pecas ---
def _fit(texto: str, fonte: str, size: float, largura: float, minimo: float = 6.0) -> float:
    return g._tamanho_fonte_1_linha(texto, g.FONT[fonte], size, largura, minimo=minimo)


def _txt_fit(c, x, y, s, largura, fonte="regular", size=9.0, color=NAVY, align="left", minimo=6.0):
    g.txt(c, x, y, s, font=fonte, size=_fit(s, fonte, size, largura, minimo), color=color, align=align)


def _cor_valor(v: Optional[float], padrao: str = NAVY) -> str:
    return g.NEG_TEXT if (v or 0) < 0 else padrao


def _faixa_simulacao(c, dados) -> None:
    """Aviso de que os numeros NAO sao da versao oficial (laranja, todas as paginas, logo abaixo do cabecalho)."""
    if dados["oficial"]:
        return
    g.rect(c, MARGEM, 70, MARGEM + CONTEUDO_W, 90, fill=ORANGE, radius=4)
    meses = ", ".join(dados["meses_nao_oficiais"][:4]) + ("…" if len(dados["meses_nao_oficiais"]) > 4 else "")
    _txt_fit(c, PAGE_W / 2, 83.5, f"SIMULAÇÃO — NÃO É A VERSÃO OFICIAL  ·  versão trocada em: {meses}", CONTEUDO_W - 16, fonte="bold", size=8.5, color="#FFFFFF", align="center")


def _titulo(c, dados, titulo: str, subtitulo: str, y: float = 118) -> None:
    g.txt(c, MARGEM, y, titulo, font="heavy", size=19, color=NAVY)
    g.txt(c, MARGEM, y + 20, subtitulo, font="regular", size=10, color=GREY_TEXT)


def _secao(c, y: float, titulo: str) -> float:
    g.txt(c, MARGEM, y, titulo, font="heavy", size=12.5, color=NAVY)
    c.setStrokeColor(HexColor(ORANGE))
    c.setLineWidth(2)
    c.line(MARGEM, g.Y(y + 6), MARGEM + 30, g.Y(y + 6))
    return y + 24


def _pagina_base(c, dados, pagina: int, subtitulo_header: str) -> None:
    g._fundo_marca_dagua(c)
    g._header(c, dados, subtitulo_header)
    _faixa_simulacao(c, dados)


def _rodape(c, dados, pagina: int) -> None:
    g._footer(c, pagina, TOTAL_PAGINAS)
    g.txt(c, MARGEM, g.PAGE_H - 24, f"Gerado em {dados['gerado_em']}" + (f" por {dados['gerado_por']}" if dados["gerado_por"] else ""),
          font="regular", size=7.5, color=GREY_TEXT)
    g.txt(c, PAGE_W - MARGEM, g.PAGE_H - 24, "Versão oficial" if dados["oficial"] else "Simulação (não oficial)", font="regular", size=7.5,
          color=GREY_TEXT if dados["oficial"] else g.NEG_TEXT, align="right")


def _kpi_grande(c, x0, x1, y0, y1, label, valor, comp, invertido=False, negativo=False) -> None:
    if invertido:
        g.rect(c, x0, y0, x1, y1, fill=NAVY, radius=6)
        cl, cv, cc = "#FFFFFF", (ORANGE if negativo else "#FFFFFF"), "#C9CEE8"
    else:
        g.rect(c, x0, y0, x1, y1, fill="#FFFFFF", stroke=NAVY, width=1.1, radius=6)
        cl, cv, cc = ORANGE, (g.NEG_TEXT if negativo else NAVY), GREY_TEXT
    pad = 16
    g.txt(c, x0 + pad, y0 + 22, label.upper(), font="bold", size=9, color=cl)
    _txt_fit(c, x0 + pad, y0 + 52, valor, x1 - x0 - 2 * pad, fonte="heavy", size=24, color=cv)
    g.paragrafo(c, x0 + pad, y0 + 68, comp, x1 - x0 - 2 * pad, size=8.5, color=cc, leading=11)


def _kpi_pequeno(c, x0, x1, y0, y1, label, valor, comp, negativo=False) -> None:
    g.rect(c, x0, y0, x0 + 3, y1, fill=NAVY)
    g.rect(c, x0 + 3, y0, x1, y1, fill=GREY_BG)
    pad = 12
    _txt_fit(c, x0 + pad, y0 + 16, label.upper(), x1 - x0 - 2 * pad, fonte="bold", size=7.6, color=NAVY)
    _txt_fit(c, x0 + pad, y0 + 40, valor, x1 - x0 - 2 * pad, fonte="heavy", size=14.5, color=(g.NEG_TEXT if negativo else NAVY))
    g.paragrafo(c, x0 + pad, y0 + 55, comp, x1 - x0 - 2 * pad, size=7.5, color=GREY_TEXT, leading=9.5)


def _dif(v: Optional[float]) -> str:
    """Variacao em R$ com sinal explicito (+/-); zero sai sem sinal."""
    return moeda_br(0.0) if v is None or abs(v) < 0.005 else moeda_br(v, forcar_sinal=True)


def _variacao_txt(item: dict, mes_ant: Optional[str]) -> str:
    if item["anterior"] is None:
        return "sem mês anterior no período"
    if item["pct"] is None:
        return f"{_dif(item['dif'])} em relação a {mes_ant}"
    return f"{pct_br(item['pct'], forcar_sinal=True)} em relação a {mes_ant}"


# ------------------------------------------------------------- tabela ---
def _tabela(c, x, y, larguras: list, cab: list, linhas: list, alinh: list, altura: float = 17.0, size: float = 8.0,
            negativo_cols: tuple = (), destaque_ultima: bool = False) -> float:
    """Tabela simples: cabecalho navy, linhas zebradas. `linhas` = listas de strings (ou (str, valor_numerico) para colorir negativo).
    Devolve o y logo abaixo da tabela."""
    total_w = sum(larguras)
    g.rect(c, x, y, x + total_w, y + altura, fill=NAVY, radius=0)
    xa = x
    for w, t, a in zip(larguras, cab, alinh):
        px = xa + (w - 6 if a == "right" else 6)
        _txt_fit(c, px, y + altura - 5.5, t, w - 10, fonte="bold", size=size, color="#FFFFFF", align=a)
        xa += w
    y += altura
    for n, lin in enumerate(linhas):
        ultima = destaque_ultima and n == len(linhas) - 1
        g.rect(c, x, y, x + total_w, y + altura, fill=("#E6E9F2" if ultima else (GREY_BG if n % 2 == 0 else "#FFFFFF")))
        xa = x
        for j, (w, cel, a) in enumerate(zip(larguras, lin, alinh)):
            val = None
            if isinstance(cel, tuple):
                cel, val = cel
            px = xa + (w - 6 if a == "right" else 6)
            _txt_fit(c, px, y + altura - 5.5, str(cel), w - 10, fonte=("bold" if ultima else "regular"), size=size,
                     color=(g.NEG_TEXT if (val is not None and val < 0) else NAVY), align=a)
            xa += w
        y += altura
    c.setStrokeColor(HexColor(BORDER))
    c.setLineWidth(0.5)
    c.line(x, g.Y(y), x + total_w, g.Y(y))
    return y


# ------------------------------------------------------------ graficos ---
def _legenda(c, x, y, itens: list) -> None:
    """itens = [(nome, cor, 'barra'|'linha')]"""
    for nome, cor, tipo in itens:
        if tipo == "linha":
            c.setStrokeColor(HexColor(cor))
            c.setLineWidth(1.6)
            c.line(x, g.Y(y - 3), x + 10, g.Y(y - 3))
            c.setFillColor(HexColor(cor))
            c.circle(x + 5, g.Y(y - 3), 2.2, fill=1, stroke=0)
        else:
            g.rect(c, x, y - 8, x + 8, y, fill=cor)
        g.txt(c, x + 13, y - 0.5, nome, font="regular", size=8.5, color=NAVY)
        x += 13 + stringWidth(nome, g.FONT["regular"], 8.5) + 16


def grafico_barras_mensal(c, x0, x1, y0_top, y1_top, rotulos: list, series: list, linha: Optional[dict] = None) -> None:
    """Barras agrupadas por mes (+ uma linha opcional com marcadores). series = [{'nome','valores','cor'}]; linha = {'nome','valores'(None = sem dado),'cor'}.
    Eixo unico em R$ (nunca eixo duplo); rotulo de valor so' quando cabe; negativo desenhado para baixo em laranja com '-'."""
    n = len(rotulos)
    if n == 0:
        return
    eixo_w, base_h = 46, 16
    px0, px1 = x0 + eixo_w, x1
    py0, py1 = y0_top + 8, y1_top - base_h
    todos = [v for s in series for v in s["valores"]] + ([v for v in linha["valores"] if v is not None] if linha else [])
    vmax = max([0.0] + [v for v in todos if v is not None]) * 1.18 or 1.0
    vmin = min([0.0] + [v for v in todos if v is not None]) * 1.18

    def yv(v: float) -> float:
        return py0 + (py1 - py0) * (vmax - v) / (vmax - vmin)

    c.setLineWidth(0.5)
    for k in range(5):
        v = vmin + (vmax - vmin) * k / 4
        c.setStrokeColor(HexColor("#E4E7EE"))
        c.line(px0, g.Y(yv(v)), px1, g.Y(yv(v)))
        g.txt(c, px0 - 5, yv(v) + 2.5, moeda_curta(v), font="regular", size=6.8, color=GREY_TEXT, align="right")
    c.setStrokeColor(HexColor("#9AA0B5"))
    c.setLineWidth(0.8)
    c.line(px0, g.Y(yv(0)), px1, g.Y(yv(0)))
    grupo_w = (px1 - px0) / n
    ns = len(series)
    barra_w = min(26.0, grupo_w * 0.74 / ns)
    rotular = n * ns <= 14
    for i, rot in enumerate(rotulos):
        cx = px0 + grupo_w * (i + 0.5)
        xb = cx - barra_w * ns / 2
        for s in series:
            v = s["valores"][i] or 0.0
            ya, yb = yv(max(v, 0.0)), yv(min(v, 0.0))
            g.rect(c, xb, ya, xb + barra_w, max(yb, ya + 0.6), fill=(g.NEG_FILL if v < 0 else s["cor"]))
            if rotular and abs(v) >= 0.005:
                g.txt(c, xb + barra_w / 2, (ya - 3) if v >= 0 else (yb + 8), moeda_curta(v), font="regular", size=6.4,
                      color=(g.NEG_TEXT if v < 0 else NAVY), align="center")
            xb += barra_w
        g.txt(c, cx, y1_top - 3, rotulo_comp(rot), font="regular", size=7.2, color=GREY_TEXT, align="center")
    if linha:
        pts = [(px0 + grupo_w * (i + 0.5), yv(v)) for i, v in enumerate(linha["valores"]) if v is not None]
        c.setStrokeColor(HexColor(linha["cor"]))
        c.setLineWidth(1.6)
        for (xa, ya), (xb_, yb_) in zip(pts, pts[1:]):
            c.line(xa, g.Y(ya), xb_, g.Y(yb_))
        c.setFillColor(HexColor(linha["cor"]))
        for xa, ya in pts:
            c.circle(xa, g.Y(ya), 2.6, fill=1, stroke=0)


# ------------------------------------------------------------- paginas ---
def pagina_resumo(c, dados, pagina: int):
    _pagina_base(c, dados, pagina, "Resumo do Mês")
    _titulo(c, dados, f"Créditos e Débitos de PIS/COFINS · {dados['rotulo']}", f"{dados['empresa_nome']} · CNPJ {dados['cnpj']}" if dados["cnpj"] else dados["empresa_nome"])
    ap = {x["item"]: x for x in dados["apuracao"]}
    cred, rec = ap["Crédito apurado no mês"], ap["Total a recolher"]
    meio = MARGEM + CONTEUDO_W / 2 - 6
    _kpi_grande(c, MARGEM, meio, 158, 248, "Crédito apurado no mês", moeda_br(cred["total"]), f"PIS {moeda_br(cred['pis'])} · COFINS {moeda_br(cred['cofins'])}", negativo=cred["total"] < 0)
    _kpi_grande(c, meio + 12, MARGEM + CONTEUDO_W, 158, 248, "Total a recolher", moeda_br(rec["total"]), f"PIS {moeda_br(rec['pis'])} · COFINS {moeda_br(rec['cofins'])}",
                invertido=True, negativo=rec["total"] < 0)
    col_w = (CONTEUDO_W - 30) / 4
    pequenos = [("Débito não cumulativo", ap["Débito não cumulativo"]), ("Débito cumulativo", ap["Débito cumulativo"]),
                ("Crédito utilizado", ap["Crédito utilizado"]), ("Saldo de crédito", ap["Saldo de crédito a transportar"])]
    x = MARGEM
    for rot, it in pequenos:
        _kpi_pequeno(c, x, x + col_w, 264, 336, rot, moeda_br(it["total"]), _variacao_txt(it, dados["rotulo_anterior"]), negativo=it["total"] < 0)
        x += col_w + 10
    y = _secao(c, 372, "Leitura do mês")
    for par in dados["leitura"]:
        y = g.paragrafo(c, MARGEM, y, par, CONTEUDO_W, size=10.2, leading=14.5) + 8

    situ = {"padrao": "versão oficial (padrão: a versão apontada do mês)", "oficial": "versão oficial (marcada pela contadora)",
            "simulada": "SIMULAÇÃO — não é a versão oficial"}.get(dados["modo"], dados["modo"])
    conf = ("Conferência: marcada como conferida" + (f" por {dados['conferido_por']}" if dados["conferido_por"] else "") + ".") if dados["conferido"] \
        else "Conferência: este mês ainda não foi marcado como conferido."
    blocos = [(f"Fonte: {dados['fonte_rotulo'] or 'não informada'}", "regular", NAVY), (f"Situação: {situ}.", "regular", g.NEG_TEXT if dados["modo"] == "simulada" else NAVY),
              (conf, "regular", NAVY)]
    if dados.get("detalhamento_texto"):
        atencao = (dados.get("detalhamento") or {}).get("estado") in ("ausente", "pendencias")
        blocos.append((dados["detalhamento_texto"], "regular", g.NEG_TEXT if atencao else NAVY))
    if dados["nota"]:
        blocos.append((f"Nota da contadora: {dados['nota']}", "italic", NAVY))
    larg = CONTEUDO_W - 28
    altura = 46 + sum(len(g._wrap_text(t, g.FONT[f], 9, larg)) * 12 + 3 for t, f, _c in blocos)
    y = max(y + 10, min(560, 790 - altura - 20))
    g.rect(c, MARGEM, y, MARGEM + CONTEUDO_W, y + altura, fill=GREY_BG, stroke=BORDER, width=0.75, radius=6)
    g.txt(c, MARGEM + 14, y + 20, "DE ONDE VÊM OS NÚMEROS", font="bold", size=8.5, color=ORANGE)
    yy = y + 38
    for t, f, cor in blocos:
        yy = g.paragrafo(c, MARGEM + 14, yy, t, larg, font=f, size=9, leading=12, color=cor) + 3
    _rodape(c, dados, pagina)


def pagina_credito(c, dados, pagina: int):
    _pagina_base(c, dados, pagina, "Crédito")
    _titulo(c, dados, "Crédito de PIS/COFINS", f"Evolução e composição · até {dados['rotulo']}")
    y = _secao(c, 168, "Crédito por mês")
    ms = dados["meses"]
    series = [{"nome": "Crédito na versão usada", "valores": [m["credito"] for m in ms], "cor": NAVY}]
    linha = {"nome": "Crédito no SPED original", "valores": [m["original"] for m in ms], "cor": AZUL_CLARO} if any(m["original"] is not None for m in ms) else None
    _legenda(c, MARGEM, y, [(series[0]["nome"], NAVY, "barra")] + ([(linha["nome"], AZUL_CLARO, "linha")] if linha else []))
    grafico_barras_mensal(c, MARGEM, MARGEM + CONTEUDO_W, y + 10, y + 190, [m["competencia"] for m in ms], series, linha)
    y += 206
    y = _secao(c, y + 6, f"Crédito por categoria em {dados['rotulo']}")
    comp = dados["composicao"][:8]
    if comp:
        itens = [{"label": it["label"], "pct": it["pct"], "rotulo_valor": it["rotulo_valor"], "cor": g.cor_barra_por_sinal(it["valor"], NAVY)} for it in comp]
        y = g.grafico_ranking_horizontal(c, MARGEM, MARGEM + CONTEUDO_W, y - 4, 22, itens) + 16
    else:
        g.txt(c, MARGEM, y + 6, "Sem créditos por categoria neste mês.", size=9.5, color=GREY_TEXT)
        y += 24
    if y < 735 and dados["variacao_categorias"]:
        y = _secao(c, y + 6, f"O que mudou em relação a {dados['rotulo_anterior']}")
        linhas = [[v["categoria"], moeda_br(v["anterior"]), moeda_br(v["atual"]), (_dif(v["dif"]), v["dif"])] for v in dados["variacao_categorias"][:4]]
        _tabela(c, MARGEM, y - 6, [190, 107, 107, 107], ["Categoria", dados["rotulo_anterior"], dados["rotulo"], "Variação"], linhas, ["left", "right", "right", "right"], altura=16, size=8)
    _rodape(c, dados, pagina)


def pagina_debito(c, dados, pagina: int):
    _pagina_base(c, dados, pagina, "Débito e Apuração")
    _titulo(c, dados, "Débito e apuração", f"{dados['rotulo']} · PIS, COFINS e total, com o mês anterior")
    y = _secao(c, 168, "Apuração do mês")
    ant = dados["rotulo_anterior"] or "mês anterior"
    linhas = []
    for it in dados["apuracao"]:
        linhas.append([it["item"], moeda_br(it["pis"]), moeda_br(it["cofins"]), moeda_br(it["total"]),
                       moeda_br(it["anterior"]) if it["anterior"] is not None else "—",
                       (_dif(it["dif"]), it["dif"]) if it["dif"] is not None else "—"])
    y = _tabela(c, MARGEM, y - 6, [172, 66, 66, 72, 66, 69], ["Item", "PIS", "COFINS", "Total", ant, "Variação"], linhas, ["left", "right", "right", "right", "right", "right"],
                altura=18, size=8, destaque_ultima=True)
    y = _secao(c, y + 22, "Débito × crédito utilizado, por mês")
    ms = dados["meses"]
    series = [{"nome": "Débito não cumulativo", "valores": [m["deb_nc"] for m in ms], "cor": NAVY},
              {"nome": "Débito cumulativo", "valores": [m["deb_cum"] for m in ms], "cor": AZUL_MEDIO},
              {"nome": "Crédito utilizado", "valores": [m["usado"] for m in ms], "cor": AZUL_PALE}]
    _legenda(c, MARGEM, y, [(s["nome"], s["cor"], "barra") for s in series])
    grafico_barras_mensal(c, MARGEM, MARGEM + CONTEUDO_W, y + 10, y + 168, [m["competencia"] for m in ms], series)
    y += 184
    y = _secao(c, y + 8, "Saldo de crédito a transportar e total a recolher")
    series2 = [{"nome": "Saldo de crédito", "valores": [m["saldo"] for m in ms], "cor": NAVY}, {"nome": "Total a recolher", "valores": [m["recolher"] for m in ms], "cor": AZUL_CLARO}]
    _legenda(c, MARGEM, y, [(s["nome"], s["cor"], "barra") for s in series2])
    grafico_barras_mensal(c, MARGEM, MARGEM + CONTEUDO_W, y + 10, min(y + 150, 790), [m["competencia"] for m in ms], series2)
    _rodape(c, dados, pagina)


def pagina_historico(c, dados, pagina: int):
    _pagina_base(c, dados, pagina, "Histórico e Observações")
    _titulo(c, dados, "Histórico e observações", f"Mês a mês até {dados['rotulo']} · valores de PIS + COFINS")
    y = _secao(c, 168, "Histórico mensal")
    cab = ["Mês", "Versão", "Crédito", "Original", "Dif.", "Déb. NC", "Déb. cum.", "Utilizado", "Saldo", "A recolher"]
    larg = [38, 70, 50, 50, 42, 52, 52, 52, 52, 53]
    larg[-1] = CONTEUDO_W - sum(larg[:-1])
    linhas = []
    for m in dados["meses"]:
        versao = {"padrao": "oficial", "oficial": "oficial", "simulada": "SIMULAÇÃO"}.get(m["modo"], m["modo"])
        linhas.append([m["rotulo"], versao, moeda_curta(m["credito"]), moeda_curta(m["original"]) if m["original"] is not None else "—",
                       (moeda_curta(m["dif"]), m["dif"]) if m["dif"] is not None else "—", moeda_curta(m["deb_nc"]), moeda_curta(m["deb_cum"]),
                       moeda_curta(m["usado"]), moeda_curta(m["saldo"]), moeda_curta(m["recolher"])])
    y = _tabela(c, MARGEM, y - 6, larg, cab, linhas, ["left", "left"] + ["right"] * 8, altura=16, size=7.4)
    y += 8
    g.txt(c, MARGEM, y + 8, "Valores em R$ abreviados (mil / mi). \"Original\" = SPED original do mês, quando existe.", size=7.5, color=GREY_TEXT)
    y += 34
    if dados["sugestoes"] and y < 640:
        y = _secao(c, y, "Pontos para acompanhar")
        for s in dados["sugestoes"][:4]:
            if y > 700:
                break
            y = g.paragrafo(c, MARGEM, y, f"• {s['titulo']}. {s['texto']}", CONTEUDO_W, size=8.8, leading=12) + 5
        y += 6
    if y < 690:
        y = _secao(c, y, "Observações da contadora")
        if dados["nota"]:
            y = g.paragrafo(c, MARGEM, y, dados["nota"], CONTEUDO_W, size=9.5, leading=13.5) + 6
        else:
            g.txt(c, MARGEM, y, "Sem observações registradas para este mês.", size=9.5, color=GREY_TEXT)
            y += 18
    y = max(y + 22, 718)
    for i, (nome, cargo) in enumerate([(dados.get("nome_contador", ""), "Contador(a)"), ("", "Administração")]):
        x = MARGEM + i * (CONTEUDO_W / 2 + 10)
        c.setStrokeColor(HexColor(NAVY))
        c.setLineWidth(0.75)
        c.line(x, g.Y(y), x + CONTEUDO_W / 2 - 20, g.Y(y))
        g.txt(c, x, y + 13, nome or cargo, font="bold" if nome else "regular", size=9, color=NAVY)
        if nome:
            g.txt(c, x, y + 25, cargo, size=8.5, color=GREY_TEXT)
    _rodape(c, dados, pagina)


def pagina_capa(c, dados, pagina: int):
    """Capa no padrao dos demais relatorios do EGC (painel navy diagonal, logo, titulo, pilula laranja com a competencia)."""
    g.pagina_capa(c, dados, pagina, TOTAL_PAGINAS)


PAGINAS = [pagina_capa, pagina_resumo, pagina_credito, pagina_debito, pagina_historico]


def gerar_pdf_creditos(dados: dict) -> bytes:
    """Gera o PDF de 5 paginas e devolve os bytes."""
    g._registrar_fontes()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Demonstrativo de Créditos e Débitos PIS/COFINS - {dados['empresa_nome']} - {dados['competencia']}")
    for n, fn in enumerate(PAGINAS, 1):
        fn(c, dados, n)
        c.showPage()
    c.save()
    return buf.getvalue()
