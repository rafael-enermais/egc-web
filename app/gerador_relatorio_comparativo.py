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
_nomes_empresas_grupo = A._nomes_empresas_grupo
_coluna_anexo = A._coluna_anexo
pagina_anexo = A.pagina_anexo  # pagina inteira reaproveitada sem alteracao
paginas_extras_anexo = A.paginas_extras_anexo  # FIX_20260929o -- ver Modelo A


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
    # FIX_20260930b: mesmo bloco de empresa/grupo da capa do Modelo A
    # (multi-CNPJ: "Grupo Enermais" em destaque + lista menor embaixo); a
    # pilula desce o necessario pra nunca sobrepor a lista.
    deslocamento_pilula = A.desenhar_bloco_empresa_capa(c, d)

    y_pilula0 = 428 + deslocamento_pilula
    y_pilula1 = 458 + deslocamento_pilula
    rect(c, MARGEM, y_pilula0, MARGEM + 280, y_pilula1, fill=ORANGE, radius=15)
    txt(c, MARGEM + 20, y_pilula1 - 11, f"PERÍODO · {d['periodo_range_label'].upper()}", font="bold", size=10, color="#FFFFFF")


# ------------------------------------------------------------------ pagina 2
# FIX_20260929e/f/g: geometria da tabela de evolução extraída em função
# pura (sem `canvas`), testável sem gerar PDF -- essa tabela já quebrou
# 2 vezes (etiqueta invadindo a 1ª coluna de valor, depois colunas de
# valor se sobrepondo) e as 2 vezes só apareceram na VERIFICAÇÃO VISUAL,
# nunca no teste automatizado, porque os testes existentes usavam
# rótulos curtos e números pequenos (o teste do motor de desenho é
# propositalmente "não valida pixel" -- ver docstring de
# test_gerador_relatorio_comparativo.py). Extrair a geometria permite um
# teste de regressão que confere os números de verdade (rótulo mais
# longo hoje + valor de 8 dígitos, 2 a 4 períodos) sem duplicar a
# fórmula no teste.
def _layout_tabela_evolucao(largura, n_col, linhas):
    """Calcula a geometria da tabela de evolução (larguras de coluna,
    tamanho de fonte do valor, e por linha: tamanho de fonte do rótulo e
    posição da etiqueta FLUXO/SALDO) -- tudo em pontos relativos ao x
    inicial da tabela (0.0), sem desenhar nada. `_tabela_evolucao` só
    desenha o que isto calcula."""
    col_var_w = 62.0
    gap = 6.0
    # FIX_20260929e (achado gerando o 1º PDF comparativo real, dado da
    # Energia): a reserva pro label+etiqueta (era 150pt) só bastava pros
    # rótulos mais curtos ("EBITDA") -- com "Receita Operacional Líquida"
    # (~137pt em bold 9.5) a etiqueta FLUXO/SALDO, desenhada logo após o
    # texto do label, invadia a 1ª coluna de valor sempre que havia 3 ou
    # 4 períodos (menos largura sobra por coluna, x_col(0) fica mais
    # apertado).
    #
    # FIX_20260929f (mesmo PDF real, achado NA VERIFICAÇÃO VISUAL do fix
    # acima): subir a reserva pra 230pt resolveu a etiqueta, mas encolheu
    # `valor_col_w` abaixo da largura real de valores de 8 dígitos (~R$
    # 33.353.150,00, ~79pt em regular 9.5 -- escala Enermais de verdade,
    # não os valores pequenos do teste do motor de desenho) -- em 4
    # períodos as colunas de valor ficavam mais estreitas que o texto e
    # os números de colunas vizinhas apareciam colados/sobrepostos.
    # Reserva volta pra 190pt (ainda cobre rótulo+etiqueta com folga --
    # ver label_max_w abaixo) e quem se ajusta agora é a FONTE do valor:
    # encolhe (mesmo padrão de A._tamanho_fonte_1_linha) até o maior
    # valor da tabela caber na largura real da coluna. 1 tamanho só pra
    # tabela inteira (não por célula) pra manter todas as linhas
    # alinhadas visualmente. Com 2-3 períodos cabe no tamanho padrão
    # (9.5) sem encolher; só com 4 períodos + valor de 8 dígitos encolhe
    # (~7.2pt, medido) -- conferido visualmente e em
    # test_gerador_relatorio_comparativo.py.
    RESERVA_LABEL_TAG = 190.0
    valor_col_w = (largura - RESERVA_LABEL_TAG - col_var_w - (n_col - 1) * gap) / n_col
    valor_col_w = max(valor_col_w, 50.0)

    def x_col(i):
        return largura - col_var_w - 14 - (n_col - 1 - i) * (valor_col_w + gap)

    # FIX_20260929g (2ª verificação visual do FIX_20260929f, mesmos PDFs
    # sintéticos escala Enermais): a trava antiga (`tag_x_max = x_col(0) -
    # 90`) partia de uma largura de valor fixa que não existe mais -- com
    # a fonte do valor agora dinâmica, o texto do valor da coluna 0 pode
    # ficar mais estreito (period count alto) e os 90pt de folga passam a
    # não bastar; era exatamente o oposto do problema do FIX_20260929e
    # (que resolvia isso subindo a reserva geral, mas aí reabria o
    # overlap entre colunas de valor). A área reservada pro rótulo+
    # etiqueta (RESERVA_LABEL_TAG) menos o padding de 14pt usado em
    # `x_col` é, por construção, EXATAMENTE a folga disponível entre o
    # início da tabela e a borda esquerda da 1ª coluna de valor -- e isso
    # NÃO depende de n_col (conferido numericamente e no teste de
    # regressão pra 2/3/4 períodos). Em vez de adivinhar uma folga em
    # pontos, o rótulo agora ENCOLHE (mesmo padrão de
    # A._tamanho_fonte_1_linha, usado na pág.2 do Modelo A) até rótulo +
    # etiqueta caberem dentro dessa área garantida -- funciona pra
    # qualquer rótulo futuro e qualquer n_col, sem reabrir nenhum dos 2
    # bugs anteriores.
    LABEL_TAG_GAP = 8.0
    TAG_W = 34.0
    label_tag_max_w = RESERVA_LABEL_TAG - 14.0
    label_max_w = label_tag_max_w - LABEL_TAG_GAP - TAG_W

    # FIX_20260929f: tamanho de fonte dos valores, único pra tabela toda,
    # calculado a partir do valor mais largo de fato presente nela (não
    # um tamanho fixo torcendo pra funcionar em todo caso futuro).
    valor_font_size = 9.5
    textos_valores = [
        moeda_br(v, forcar_sinal=(v < 0))
        for linha in linhas
        for v in linha.get("valores", [])
    ]
    if textos_valores:
        maior_valor_txt = max(
            textos_valores, key=lambda t: stringWidth(t, FONT["regular"], valor_font_size)
        )
        valor_font_size = A._tamanho_fonte_1_linha(
            maior_valor_txt, FONT["regular"], valor_font_size, valor_col_w, minimo=6.5,
        )

    linhas_geo = []
    for linha in linhas:
        label_size = A._tamanho_fonte_1_linha(linha["label"], FONT["bold"], 9.5, label_max_w, minimo=7.5)
        w_label = stringWidth(linha["label"], FONT["bold"], label_size)
        # min() aqui é defesa em profundidade: só entra em jogo se o
        # rótulo ainda estourar mesmo no piso mínimo de _tamanho_fonte_1_linha.
        tag_x0 = min(w_label + LABEL_TAG_GAP, label_tag_max_w - TAG_W)
        linhas_geo.append({"label_size": label_size, "w_label": w_label, "tag_x0": tag_x0})

    return {
        "valor_col_w": valor_col_w, "x_col": x_col, "col_var_w": col_var_w,
        "valor_font_size": valor_font_size, "label_max_w": label_max_w,
        "TAG_W": TAG_W, "linhas": linhas_geo,
    }


def _tabela_evolucao(c, x, largura, y_top, titulos_colunas, linhas, mostrar_variacao=True):
    """Tabela de evolucao de indicadores -- N colunas (1 por periodo) +
    coluna de Variacao (ultimo vs 1o periodo). Cada `linha`: {label,
    valores (list[float]), tag ('fluxo'|'saldo'), acumulado (float|None)}.

    Nao reusa `_linha_anexo`/`_coluna_anexo` (aquele motor foi desenhado
    pra arvore de conta contabil com grupo/subconta -- aqui e' so' uma
    lista chata de indicadores, layout mais simples de tabela normal).
    A geometria (larguras, tamanhos de fonte) mora em
    `_layout_tabela_evolucao` -- aqui só desenha."""
    n_col = len(titulos_colunas)
    geo = _layout_tabela_evolucao(largura, n_col, linhas)
    valor_font_size = geo["valor_font_size"]
    TAG_W = geo["TAG_W"]

    def x_col(i):
        return x + geo["x_col"](i)

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

    for linha, linha_geo in zip(linhas, geo["linhas"]):
        vals = linha["valores"]
        label_size = linha_geo["label_size"]
        txt(c, x, y, linha["label"], font="bold", size=label_size, color="#1c1c1a")
        tag_txt = "FLUXO" if linha["tag"] == "fluxo" else "SALDO"
        tag_cor = NAVY if linha["tag"] == "fluxo" else RED_ACCENT
        tag_x0 = x + linha_geo["tag_x0"]
        rect(c, tag_x0, y - 8, tag_x0 + TAG_W, y + 2, fill=tag_cor, radius=2)
        txt(c, tag_x0 + TAG_W / 2, y - 1, tag_txt, font="bold", size=6.5, color="#FFFFFF", align="center")
        for i, v in enumerate(vals):
            cor_v = A.NEG_TEXT if v < 0 else NAVY
            txt(c, x_col(i), y, moeda_br(v, forcar_sinal=(v < 0)), font="regular", size=valor_font_size, align="right", color=cor_v)
        if mostrar_variacao and vals[0]:
            delta = (vals[-1] - vals[0]) / abs(vals[0])
            cor_delta = A.NEG_TEXT if delta < 0 else "#1a7a3c"
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
# v0.40.0 (item E) -- paleta do grafico "Evolucao dos Principais
# Indicadores". REGRA UNICA (vale p/ comentario e legenda):
#     COR  = recencia do periodo + sinal do valor
#     TAMANHO DA BARRA = valor (|v| / maior |v| da metrica)
#   - valores positivos: rampa de NAVY -- o periodo mais antigo no tom
#     mais claro/dessaturado e o mais recente no NAVY pleno (#171C60);
#   - valores negativos: OUTRO matiz, em rampa propria -- familia laranja
#     da marca (ORANGE #EA9527 pleno no periodo mais recente, tons mais
#     claros nos anteriores). "teal" (verde-azulado escuro) e' a
#     alternativa avaliada no preview paleta_preview_v2.png; troca por
#     `dados["paleta_evolucao"]` (default "laranja").
# (Historico: v0.39 usava "A" = anteriores em navy + mais recente em
# ORANGE, e negativo em vermelho. Mudou: o laranja deixou de significar
# "mais recente" e passou a significar "valor abaixo de zero".)
_POSITIVO_CLARO = "#B4B9D6"   # periodo mais antigo (navy dessaturado)
# nome -> (tom do periodo mais antigo, tom do periodo mais recente, cor do texto do valor)
PALETAS_NEGATIVO = {
    "laranja": ("#F7D6A8", ORANGE, "#9A5200"),
    "teal": ("#A9D4D5", "#0F6B6E", "#0B5558"),
}
PALETA_EVOLUCAO_PADRAO = "laranja"


def _paleta_negativa(paleta: str = None):
    return PALETAS_NEGATIVO.get((paleta or PALETA_EVOLUCAO_PADRAO).lower(), PALETAS_NEGATIVO[PALETA_EVOLUCAO_PADRAO])


def _cor_periodo(i: int, n_periodos: int, v: float, paleta: str = None) -> str:
    """Cor da barra do periodo `i` (0 = mais antigo) num grupo de
    `n_periodos`. Cor = recencia + sinal (ver bloco acima); o tamanho da
    barra e' decidido em `grafico_evolucao`, nao aqui. Funcao pura
    (testavel sem gerar PDF)."""
    eh_mais_recente = n_periodos <= 1 or i >= n_periodos - 1
    t = 1.0 if n_periodos <= 1 else i / (n_periodos - 1)
    if v < 0:
        claro, pleno, _txt = _paleta_negativa(paleta)
    else:
        claro, pleno = _POSITIVO_CLARO, NAVY
    return pleno if eh_mais_recente else _interp_cor(claro, pleno, t)


def _contraste_sobre_branco(cor_hex: str) -> float:
    def lin(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    h = cor_hex.lstrip("#")
    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return 1.05 / (0.2126 * r + 0.7152 * g + 0.0722 * b + 0.05)


CONTRASTE_MIN_TEXTO_VALOR = 3.5


def _cor_texto_valor(v: float, paleta: str = None, cor_barra: str = None) -> str:
    """Cor do numero ao lado da barra.

    v0.43.2 (pedido do Rafael: "deixa a letra igual a cor do grafico"): com
    `cor_barra`, o numero usa a MESMA cor da barra do periodo. Nos tons muito
    claros (periodo mais antigo) a cor exata ficaria ilegivel sobre o branco
    (ex.: #B4B9D6 = 1,9:1); ai escurece SO' o necessario, mantendo o matiz
    (multiplica o RGB por um fator), ate contraste >= 3,5:1. Barras nos tons
    medios/escuros (periodo recente) mantem a cor identica.

    Sem `cor_barra` (comportamento anterior): navy p/ valor >= 0 e versao escura
    da familia laranja/teal p/ valor < 0."""
    if not cor_barra:
        return _paleta_negativa(paleta)[2] if v < 0 else NAVY
    cor = cor_barra if cor_barra.startswith("#") else "#" + cor_barra
    fator = 1.0
    while _contraste_sobre_branco(cor) < CONTRASTE_MIN_TEXTO_VALOR and fator > 0.05:
        fator -= 0.03
        h = cor_barra.lstrip("#")
        cor = "#%02X%02X%02X" % tuple(int(int(h[i:i + 2], 16) * fator) for i in (0, 2, 4))
    return cor


def grafico_evolucao(c, x0, x1, y0_top, metricas, periodos_labels, altura_grupo=58.0, paleta=None):
    """Mini-barras horizontais por periodo, 1 grupo por metrica, escala
    PROPRIA por metrica (nao dá pra comparar Receita e EBITDA na mesma
    escala sem achatar o EBITDA a nada) -- mesma logica de pequenos
    multiplos do esboço validado com o Rafael em artifact. COR = recencia
    do periodo + sinal do valor (tom mais claro = mais antigo; navy para
    valores >= 0, familia laranja para valores < 0); TAMANHO DA BARRA =
    valor. Ver bloco de paleta acima. Devolve o y_top final."""
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
            # FIX_20260929p+r (Rafael: 1º "esse lilás claro tá mt ruim",
            # depois "a paleta me incomoda... não tem alguma melhor que
            # combine com a cara da Enermais?") + FIX_20260930 (Rafael,
            # PDF real com 3 períodos 2023/2024/2026: "as barras de 2024
            # e 2026 ficam quase idênticas, só a de 2023 se distingue" --
            # ponto de partida antigo, #2A32AC, tinha luminosidade
            # perto demais do NAVY final). Ver `_cor_periodo` pro
            # raciocínio completo (escala ordinal, matiz único, faixa de
            # luminosidade) e o teste de regressão
            # test_grafico_evolucao_cores_periodos_positivos_sao_distintas.
            cor = _cor_periodo(i, n_periodos, v, paleta)
            txt(c, x0, y + altura_barra - 2, periodos_labels[i], font="regular", size=7.5, color=GREY_TEXT)
            bx0 = x0 + col_label_w
            largura = largura_barra_max * (abs(v) / maior_abs)
            rect(c, bx0, y, bx0 + max(largura, 2), y + altura_barra, fill=cor)
            txt(c, bx0 + largura_barra_max + 8, y + altura_barra - 2, moeda_br(v, forcar_sinal=(v < 0)),
                font="bold", size=8.5, color=_cor_texto_valor(v, paleta, cor_barra=cor), align="left")
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

    paleta = (d.get("paleta_evolucao") or PALETA_EVOLUCAO_PADRAO).lower()
    y_fim = grafico_evolucao(c, MARGEM, PAGE_W - MARGEM, 168, d.get("grafico_evolucao_metricas", []),
                              d["periodos_labels"], paleta=paleta)

    # Legenda neutra (sem adjetivo avaliativo): descreve so' o codigo visual.
    nome_matiz = "verde-azulado" if paleta == "teal" else "laranja"
    paragrafo(
        c, MARGEM, y_fim + 10,
        "Como ler: o comprimento da barra representa o valor. A cor indica o período e o sinal do valor: "
        "valores iguais ou acima de zero em tons de azul; valores abaixo de zero em tons de "
        f"{nome_matiz}, com sinal de menos antes do número. Em cada cor, o tom mais claro é o período mais "
        "antigo e o tom mais escuro (ou mais intenso), o período mais recente.",
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
        f"período. Este documento é de uso interno da administração e da contabilidade do Grupo Enermais.",
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
    # FIX_20260929o: mesmo raciocinio do Modelo A -- pagina_anexo (multi-
    # coluna) pode precisar de mais de 1 pagina fisica quando o anexo nao
    # cabe numa so' (achado real: Evolucao_GRUPO_202312_202606.pdf, 2
    # empresas x 3 periodos, cortava a secao de Patrimonio Liquido). O
    # total precisa refletir isso desde a 1a pagina (rodape "Pagina X de
    # Y" de TODAS as paginas usa o mesmo total).
    _registrar_fontes()
    c = canvas.Canvas(caminho_saida, pagesize=(PAGE_W, PAGE_H))
    total = len(PAGINAS) + paginas_extras_anexo(dados)
    pagina_atual = 1
    for pagina_fn in PAGINAS:
        resultado = pagina_fn(c, dados, pagina=pagina_atual, total_paginas=total)
        pagina_atual = (resultado if isinstance(resultado, int) else pagina_atual) + 1
        c.showPage()
    c.save()
    return caminho_saida
