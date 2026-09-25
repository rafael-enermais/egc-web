# -*- coding: utf-8 -*-
"""
EGC | Gerador de Relatorio Comparativo -- "Modelo B" (multi-periodo),
irmao do Modelo A (gerador_relatorio_comentado.py, relatorio de 1
periodo). Pedido do Rafael 25-26/09/2026: em vez de 1 template so' com
`if multi:` espalhado (o que comecou a acontecer no Anexo do Modelo A),
2 modelos separados -- Modelo A responde "como fomos nesse periodo?",
Modelo B responde "como evoluimos entre os periodos?".

NAO duplica o Modelo A: importa dele toda a infraestrutura ja pronta e
testada (paleta, fontes, header/footer, logo de grupo, e o proprio motor
de tabela multi-coluna construido no Anexo em 25/09 -- que ja e' o motor
certo pra ESTE modelo inteiro, nao so' pro Anexo). So' o que e'
genuinamente novo pro "olhar comparativo" mora aqui: capa com
enquadramento de intervalo, tabela de evolucao de indicadores (com a
etiqueta FLUXO/SALDO que deixa a regra BP-nunca-soma visivel pro
leitor), e o grafico de evolucao.

Mesma regra do Modelo A: este modulo NAO fala com o Supabase, so'
recebe um dicionario `dados` ja montado e desenha.

Contrato de dados (`dados`), tudo que NAO existe no Modelo A:
  periodos_labels: list[str]      -- titulos das colunas (anos, ou
                                      "1S25"/"2S25" etc.) -- 2 a 4 recomendado,
                                      mais que isso a tabela/grafico ficam apertados
  periodo_range_label: str        -- ex. "2024 A 2026" (pilula da capa)
  kpis_fluxo: list[dict]          -- indicadores de DRE (fluxo, pode somar):
                                      {label, valores: list[float], acumulado: float|None}
  kpis_saldo: list[dict]          -- indicadores de BP (saldo, NUNCA soma):
                                      {label, valores: list[float]}
  grafico_evolucao_metricas: list[dict]  -- subconjunto p/ o grafico:
                                      {label, valores: list[float]}
  anexo_colunas, anexo_ativo, anexo_passivo, anexo_escopo_label
                                   -- EXATAMENTE o mesmo formato multi-coluna
                                      do Modelo A (dados['anexo_colunas'] = periodos_labels)
  + todos os campos "de sempre" que o header/capa/fechamento do Modelo A
    ja usam: empresa_nome, empresa_codigo, empresas_codigos (opcional),
    cnpj, cabecalho_relatorio, data_geracao, nome_administrador,
    cargo_administrador, nome_contador, cargo_contador, email_empresa,
    site_empresa.
"""
from __future__ import annotations

from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

from formatacao import moeda_br, pct_br

import gerador_relatorio_comentado as A  # Modelo A -- reaproveitado, nao duplicado

# reaproveita tudo do Modelo A em vez de redefinir
PAGE_W, PAGE_H, MARGEM, CONTEUDO_W = A.PAGE_W, A.PAGE_H, A.MARGEM, A.CONTEUDO_W
NAVY, ORANGE, GREY_TEXT, GREY_BG, RED_ACCENT, BORDER_LIGHT = (
    A.NAVY, A.ORANGE, A.GREY_TEXT, A.GREY_BG, A.RED_ACCENT, A.BORDER_LIGHT
)
FONT = A.FONT
Y, txt, rect, image, paragrafo = A.Y, A.txt, A.rect, A.image, A.paragrafo
_interp_cor = A._interp_cor
_registrar_fontes = A._registrar_fontes
_fundo_marca_dagua = A._fundo_marca_dagua
_header, _footer = A._header, A._footer
_logo_dados = A._logo_dados
_linha_identificacao_empresa = A._linha_identificacao_empresa
_coluna_anexo = A._coluna_anexo
pagina_anexo = A.pagina_anexo  # pagina inteira reaproveitada sem alteracao


# ------------------------------------------------------------------ pagina 1
def pagina_capa_comparativa(c, dados, pagina: int, total_paginas: int):
    """Mesma identidade visual da capa do Modelo A (painel navy diagonal +
    marca d'agua real + logo), so' troca o enquadramento: titulo fala de
    evolucao, a pilula mostra o INTERVALO em vez de 1 periodo so'."""
    d = dados
    A.faixa_gradiente(c, 0, 0, PAGE_W, 5, NAVY, ORANGE)
    c.saveState()
    c.setFillColor(A.HexColor(NAVY))
    p = c.beginPath()
    topo_x = PAGE_W * 0.793
    base_x = PAGE_W * 0.667
    p.moveTo(topo_x, PAGE_H)
    p.lineTo(PAGE_W, PAGE_H)
    p.lineTo(PAGE_W, 0)
    p.lineTo(base_x, 0)
    p.close()
    c.drawPath(p, fill=1, stroke=0)
    c.clipPath(p, stroke=0, fill=0)
    c.setStrokeColor(A.HexColor("#FFFFFF"))
    c.setLineWidth(0.6)
    if hasattr(c, "setStrokeAlpha"):
        c.setStrokeAlpha(0.07)
    inclinacao = (topo_x - base_x) / PAGE_H
    passo = 34
    x = base_x - PAGE_H
    while x < PAGE_W + PAGE_H:
        c.line(x, 0, x + inclinacao * PAGE_H, PAGE_H)
        x += passo
    if hasattr(c, "setStrokeAlpha"):
        c.setStrokeAlpha(1)
    c.restoreState()
    marca_path = f"{A.ASSETS}/capa_marca_pale.png"
    import os
    if os.path.exists(marca_path):
        image(c, marca_path, 435, 630, 552, 812, mask="auto", preserve_ratio=True)
    logo_path = _logo_dados(d)
    if logo_path and os.path.exists(logo_path):
        image(c, logo_path, MARGEM, 130, MARGEM + 260, 210, mask="auto", preserve_ratio=True)
    c.setStrokeColor(A.HexColor(ORANGE))
    c.setLineWidth(3)
    c.line(MARGEM, Y(268), MARGEM + 46, Y(268))
    txt(c, MARGEM, 310, "Evolução", font="heavy", size=30, color=NAVY)
    txt(c, MARGEM, 345, "Financeira", font="heavy", size=30, color=NAVY)
    txt(c, MARGEM, 380, d["empresa_nome"], font="regular", size=12, color=GREY_TEXT)
    txt(c, MARGEM, 398, _linha_identificacao_empresa(d), font="regular", size=12, color=GREY_TEXT)
    rect(c, MARGEM, 428, MARGEM + 280, 458, fill=ORANGE, radius=15)
    txt(c, MARGEM + 20, 447, f"PERÍODO · {d['periodo_range_label'].upper()}", font="bold", size=10, color="#FFFFFF")


# ------------------------------------------------------------------ pagina 2
def _tabela_evolucao(c, x, largura, y_top, titulos_colunas, linhas, mostrar_variacao=True):
    """Tabela de evolucao de indicadores -- N colunas (1 por periodo) +
    coluna de Variacao (ultimo vs 1o periodo). Cada `linha`: {label,
    valores (list[float]), tag ('fluxo'|'saldo'), acumulado (float|None)}.

    Nao reusa `_linha_anexo`/`_coluna_anexo` (aquele motor foi desenhado
    pra arvore de conta contabil com grupo/subconta -- aqui e' so' uma
    lista chata de indicadores, layout mais simples de tabela normal)."""
    col_var_w = 62.0
    n_col = len(titulos_colunas)
    gap = 6.0
    valor_col_w = (largura - 150 - col_var_w - (n_col - 1) * gap) / n_col
    valor_col_w = max(valor_col_w, 50.0)

    def x_col(i):
        return x + largura - col_var_w - 14 - (n_col - 1 - i) * (valor_col_w + gap)

    x_var = x + largura

    y = y_top
    for i, titulo in enumerate(titulos_colunas):
        txt(c, x_col(i), y, titulo.upper(), font="bold", size=7.5, color=GREY_TEXT, align="right")
    if mostrar_variacao:
        txt(c, x_var, y, "VARIAÇÃO", font="bold", size=7.5, color=GREY_TEXT, align="right")
    c.setStrokeColor(A.HexColor(BORDER_LIGHT))
    c.setLineWidth(0.5)
    c.line(x, Y(y + 4), x + largura, Y(y + 4))
    y += 18

    for linha in linhas:
        vals = linha["valores"]
        txt(c, x, y, linha["label"], font="bold", size=9.5, color="#1c1c1a")
        tag_txt = "FLUXO" if linha["tag"] == "fluxo" else "SALDO"
        tag_cor = NAVY if linha["tag"] == "fluxo" else RED_ACCENT
        w_label = stringWidth(linha["label"], FONT["bold"], 9.5)
        rect(c, x + w_label + 8, y - 8, x + w_label + 8 + 34, y + 2, fill=tag_cor, radius=2)
        txt(c, x + w_label + 25, y - 1, tag_txt, font="bold", size=6.5, color="#FFFFFF", align="center")
        for i, v in enumerate(vals):
            cor_v = RED_ACCENT if v < 0 else NAVY
            txt(c, x_col(i), y, moeda_br(v, forcar_sinal=(v < 0)), font="regular", size=9.5, color=cor_v, align="right")
        if mostrar_variacao and vals[0]:
            delta = (vals[-1] - vals[0]) / abs(vals[0])
            cor_delta = RED_ACCENT if delta < 0 else "#1a7a3c"
            txt(c, x_var, y, pct_br(delta, forcar_sinal=True), font="bold", size=9.5, color=cor_delta, align="right")
        y += 16
        if linha.get("acumulado") is not None:
            txt(c, x + 10, y, f"Acumulado no período: {moeda_br(linha['acumulado'])} — soma válida (fluxo)",
                font="italic", size=7.5, color=GREY_TEXT)
            y += 13
        y += 4
    return y


def pagina_resumo_evolucao(c, dados, pagina: int, total_paginas: int):
    d = dados
    _header(c, dados, "Resumo Evolutivo")

    rect(c, MARGEM, 96, MARGEM + 72, 116, fill=NAVY, radius=3)
    txt(c, MARGEM + 36, 110, "RESUMO", font="bold", size=9, color="#FFFFFF", align="center")
    txt(c, MARGEM + 84, 112, "Evolução dos Indicadores-Chave", font="heavy", size=14.5, color=NAVY)
    txt(c, MARGEM, 134, f"{d.get('anexo_escopo_label', d['empresa_nome'])} · valores em R$",
        font="regular", size=9, color=GREY_TEXT)

    titulos = d["periodos_labels"]
    y = 168
    txt(c, MARGEM, y, "INDICADORES DE RESULTADO (DRE) — FLUXO DO PERÍODO, PODE SOMAR", font="bold", size=8, color=GREY_TEXT)
    y += 14
    y = _tabela_evolucao(c, MARGEM, CONTEUDO_W, y, titulos, d.get("kpis_fluxo", []))

    y += 10
    txt(c, MARGEM, y, "INDICADORES DE BALANÇO (BP) — SALDO NA DATA-BASE, NUNCA SOMA ENTRE PERÍODOS", font="bold", size=8, color=GREY_TEXT)
    y += 14
    y = _tabela_evolucao(c, MARGEM, CONTEUDO_W, y, titulos, d.get("kpis_saldo", []))

    y += 10
    paragrafo(
        c, MARGEM, y,
        "Indicadores de fluxo (Demonstração do Resultado) podem ser somados entre períodos porque medem "
        "movimento ao longo do tempo. Indicadores de saldo (Balanço Patrimonial) são uma fotografia da "
        "data-base de cada período e nunca são somados entre colunas — só comparados lado a lado.",
        CONTEUDO_W, font="regular", size=8, color=GREY_TEXT, leading=11,
    )
    _footer(c, pagina, total_paginas)


# ------------------------------------------------------------------ pagina 3
def grafico_evolucao(c, x0, x1, y0_top, metricas, periodos_labels, altura_grupo=58.0):
    """Mini-barras horizontais por periodo, 1 grupo por metrica, escala
    PROPRIA por metrica (nao dá pra comparar Receita e EBITDA na mesma
    escala sem achatar o EBITDA a nada) -- mesma logica de pequenos
    multiplos do esboço validado com o Rafael em artifact. Cor mais clara
    -> mais escura conforme o periodo fica mais recente; negativo sempre
    RED_ACCENT (mesma convenção já usada nas cascatas do Modelo A).
    Devolve o y_top final."""
    n_periodos = len(periodos_labels)
    col_label_w = 118
    col_valor_w = 78
    largura_barra_max = (x1 - x0) - col_label_w - col_valor_w - 10
    altura_barra = 11
    espaco_barra = 3

    y = y0_top
    for metrica in metricas:
        vals = metrica["valores"]
        maior_abs = max(abs(v) for v in vals) or 1.0
        txt(c, x0, y, metrica["label"], font="bold", size=9.5, color=NAVY)
        y += 13
        for i, v in enumerate(vals):
            cor = RED_ACCENT if v < 0 else _interp_cor("#C9CCEB", NAVY, i / max(1, n_periodos - 1))
            txt(c, x0, y + altura_barra - 2, periodos_labels[i], font="regular", size=7.5, color=GREY_TEXT)
            bx0 = x0 + col_label_w
            largura = largura_barra_max * (abs(v) / maior_abs)
            rect(c, bx0, y, bx0 + max(largura, 2), y + altura_barra, fill=cor)
            txt(c, bx0 + largura_barra_max + 8, y + altura_barra - 2, moeda_br(v, forcar_sinal=(v < 0)),
                font="bold", size=8.5, color=cor, align="left")
            y += altura_barra + espaco_barra
        y += 10
    return y


def pagina_grafico_evolucao(c, dados, pagina: int, total_paginas: int):
    d = dados
    _header(c, dados, "Gráfico de Evolução")

    rect(c, MARGEM, 96, MARGEM + 72, 116, fill=NAVY, radius=3)
    txt(c, MARGEM + 36, 110, "GRÁFICO", font="bold", size=9, color="#FFFFFF", align="center")
    txt(c, MARGEM + 84, 112, "Evolução dos Principais Indicadores", font="heavy", size=14.5, color=NAVY)
    txt(c, MARGEM, 134, f"{d.get('anexo_escopo_label', d['empresa_nome'])} · valores em R$",
        font="regular", size=9, color=GREY_TEXT)

    y_fim = grafico_evolucao(c, MARGEM, PAGE_W - MARGEM, 168, d.get("grafico_evolucao_metricas", []),
                              d["periodos_labels"])

    paragrafo(
        c, MARGEM, y_fim + 10,
        "Barras em tom mais escuro representam os períodos mais recentes. Valores negativos aparecem em "
        "vermelho, independentemente do período — a cor marca o sinal do número, o texto não usa adjetivo.",
        CONTEUDO_W, font="regular", size=8, color=GREY_TEXT, leading=11,
    )
    _footer(c, pagina, total_paginas)


# ------------------------------------------------------------------ pagina 5
def pagina_fechamento_comparativa(c, dados, pagina: int, total_paginas: int):
    d = dados
    _fundo_marca_dagua(c)
    _header(c, dados, "Fechamento")

    txt(c, MARGEM, 100, "Nota sobre este Relatório", font="heavy", size=17, color=NAVY)
    y = paragrafo(
        c, MARGEM, 130,
        f"Os valores apresentados comparam o Balanço Patrimonial e a Demonstração do Resultado do Exercício "
        f"de cada período do intervalo {d['periodo_range_label']}, gerados a partir do SPED contábil de cada "
        f"período. Este documento é de uso interno da administração e da contabilidade do Grupo Enermais e "
        f"não foi submetido a exame por auditoria externa independente.",
        CONTEUDO_W, size=10.5, leading=15,
    ) + 12
    y = paragrafo(
        c, MARGEM, y,
        "Indicadores de Demonstração do Resultado (fluxo) podem ser somados entre períodos; indicadores de "
        "Balanço Patrimonial (saldo) refletem a data-base de cada período e nunca são somados entre colunas.",
        CONTEUDO_W, size=10.5, leading=15,
    ) + 60

    col_w = (CONTEUDO_W - 20) / 2
    for i, (nome, cargo) in enumerate([
        (d.get("nome_administrador", ""), d.get("cargo_administrador", "Administrador")),
        (d.get("nome_contador", ""), d.get("cargo_contador", "Contador")),
    ]):
        x = MARGEM + i * (col_w + 20)
        c.setStrokeColor(A.HexColor(NAVY)); c.setLineWidth(0.75)
        c.line(x, Y(y), x + col_w, Y(y))
        txt(c, x, y + 16, nome, font="bold", size=10.5, color=NAVY)
        txt(c, x, y + 32, cargo, font="regular", size=9.5, color=GREY_TEXT)
    y += 52
    txt(c, MARGEM, y, "Documento gerado a partir do BP e DRE do sistema contábil — sujeito a validação e "
                       "assinatura da contabilidade.", font="italic", size=8, color=GREY_TEXT)

    y += 40
    rect(c, MARGEM, y, MARGEM + CONTEUDO_W, y + 90, fill=GREY_BG, stroke=BORDER_LIGHT, width=0.75, radius=6)
    import os
    logo_path = _logo_dados(d)
    if logo_path and os.path.exists(logo_path):
        image(c, logo_path, MARGEM + 18, y + 18, MARGEM + 148, y + 54, mask="auto", preserve_ratio=True)
    txt(c, MARGEM + 18, y + 72, d["empresa_nome"], font="bold", size=9.5, color=NAVY)
    txt(c, MARGEM + 18, y + 84, _linha_identificacao_empresa(d), font="regular", size=9, color=GREY_TEXT)
    txt(c, MARGEM + CONTEUDO_W - 18, y + 72, d.get("email_empresa", ""), font="regular", size=9, color=GREY_TEXT, align="right")
    txt(c, MARGEM + CONTEUDO_W - 18, y + 84, d.get("site_empresa", ""), font="regular", size=9, color=GREY_TEXT, align="right")

    txt(c, PAGE_W / 2, y + 130, f"Relatório gerado pelo Sistema EGC — Gestão Contábil Enermais · {d.get('data_geracao', '')}",
        font="regular", size=8, color=GREY_TEXT, align="center")

    _footer(c, pagina, total_paginas)


# ---------------------------------------------------------------- montagem
PAGINAS = [
    pagina_capa_comparativa,
    pagina_resumo_evolucao,
    pagina_grafico_evolucao,
    pagina_anexo,  # reaproveitado do Modelo A sem alteracao -- ja e' multi-coluna
    pagina_fechamento_comparativa,
]


def gerar_pdf_comparativo(dados: dict, caminho_saida: str) -> str:
    """Gera o PDF do Modelo B (5 paginas: Capa, Resumo Evolutivo, Grafico
    de Evolucao, Anexo Comparativo, Fechamento). Deliberadamente MENOR que
    o Modelo A (9 paginas) -- as 6 paginas de "leitura" por topico
    (Receita/Custos, Despesas, Resultado, EBITDA, Balanco individual) do
    Modelo A fazem sentido pra 1 periodo so'; pra comparar N periodos,
    1 tabela de evolucao + 1 grafico cobrem o mesmo papel de forma mais
    direta, sem repetir narrativa de "leitura" por periodo (que exigiria
    reescrever as 5 funcoes de leitura pra falarem de N periodos ao mesmo
    tempo -- custo maior, valor duvidoso comparado a olhar a tabela)."""
    _registrar_fontes()
    c = canvas.Canvas(caminho_saida, pagesize=(PAGE_W, PAGE_H))
    total = len(PAGINAS)
    for i, pagina_fn in enumerate(PAGINAS, start=1):
        pagina_fn(c, dados, pagina=i, total_paginas=total)
        c.showPage()
    c.save()
    return caminho_saida
