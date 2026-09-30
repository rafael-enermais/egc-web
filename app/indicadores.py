# -*- coding: utf-8 -*-
"""
Indicadores contabeis classicos calculados em cima do BP/DRE que ja esta
importado no banco -- pedido do Rafael (22/09/2026): "poderiamos fazer
todos os calculos que forem uteis em cima com funcao contabil, tudo oq
ajudar e saber e interessante". Zero import novo, zero conta nova no
parser -- so' pivota contas que ja sao extraidas hoje (mesma fonte que
Visao Grupo e Dashboard de Projecao, que usava db.listar_historico_grupo
pra montar a serie temporal).

Modulo puro (sem banco/Streamlit), mesmo padrao de projecao.py e
visao_grupo.py: recebe as listas ja buscadas via db.listar_historico_grupo
(1 empresa, todos os periodos, BP e DRE separados) e devolve um
pd.DataFrame indexado por periodo (pd.DatetimeIndex, mesma convencao
anti-bug-de-ordenacao usada no grafico de Visao Grupo/Projecao -- ver
visao_grupo.montar_serie_kpis_grupo).

Liquidez seca (Ativo Circulante - Estoques) / Passivo Circulante NAO
entra: conferido em parser_egc.py (BP_CANDIDATES) -- as 6 empresas do
grupo sao EPC/energia, o parser nunca mapeia conta "ESTOQUES" no BP.
Sem estoque, liquidez seca fica numericamente identica a liquidez
corrente -- indicador redundante, nao construido (simplicidade
operacional).

Contas usadas (todas ja extraidas pelo parser hoje, nenhuma nova):
  BP:  TOTAL DO ATIVO, TOTAL CIRCULANTE ATIVO, TOTAL CIRCULANTE PASSIVO,
       TOTAL NAO CIRCULANTE PASSIVO, TOTAL PATRIMONIO LIQUIDO
  DRE: RECEITA OPERACIONAL LIQUIDA, LUCRO BRUTO, LUCRO LIQUIDO DO EXERCICIO,
       LUCRO OPERACIONAL LIQUIDO, DESPESAS FINANCEIRAS, RECEITAS FINANCEIRAS,
       DEPRECIACOES, AMORTIZACOES

"TOTAL DO PASSIVO" (que ja existe em egc.v_indicadores) NAO e' usado pra
endividamento -- no BP brasileiro essa linha soma Passivo exigivel +
Patrimonio Liquido (e' por isso que TOTAL DO ATIVO = TOTAL DO PASSIVO
sempre fecha). Endividamento geral usa só o passivo exigivel de verdade
(Circulante + Nao Circulante), sem o PL misturado.

EBITDA (24/09/2026 -- pedido da diretoria via Rafael, so' virou viavel
depois do fix em parser_egc.py que passou a extrair Depreciacoes/
Amortizacoes do periodo, que faltava ate 23/09/2026):
  resultado_financeiro = DESPESAS FINANCEIRAS + RECEITAS FINANCEIRAS
    (a 1a ja vem negativa, a 2a positiva -- soma direta da o liquido)
  EBIT = LUCRO OPERACIONAL LIQUIDO - resultado_financeiro
    (LUCRO OPERACIONAL LIQUIDO nesse layout de DRE ja SAI liquido do
    resultado financeiro -- subtrair de novo "desfaz" isso e devolve o
    resultado so' da operacao, antes de juros)
  D&A = DEPRECIACOES + AMORTIZACOES (ambas negativas ou 0 -- ausentes
    no periodo = 0, tratado como zero real, nao NaN: ver
    extrair_deprec_amortiz() em parser_egc.py, "nunca inventa numero"
    so' vale pra criar linha nova, zero por ausencia e' informacao real)
  EBITDA = EBIT - D&A (subtrair um numero negativo soma de volta)
  Margem EBITDA = EBITDA / RECEITA OPERACIONAL LIQUIDA

  Sem LUCRO OPERACIONAL LIQUIDO no periodo (BP/DRE incompleto), EBITDA
  fica NaN -- nao computa "meio EBITDA" com dado faltando.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

CONTAS_BP = [
    "TOTAL DO ATIVO",
    "TOTAL CIRCULANTE ATIVO",
    "TOTAL CIRCULANTE PASSIVO",
    "TOTAL NAO CIRCULANTE PASSIVO",
    "TOTAL PATRIMONIO LIQUIDO",
]
CONTAS_DRE = [
    "RECEITA OPERACIONAL LIQUIDA",
    "LUCRO BRUTO",
    "LUCRO LIQUIDO DO EXERCICIO",
    "LUCRO OPERACIONAL LIQUIDO",
    "DESPESAS FINANCEIRAS",
    "RECEITAS FINANCEIRAS",
    "DEPRECIACOES",
    "AMORTIZACOES",
]

COLUNAS_INDICADORES = [
    "Liquidez Corrente",
    "Capital de Giro",
    "Endividamento Geral",
    "Alavancagem",
    "Margem Bruta",
    "Margem Líquida",
    "ROA",
    "ROE",
    "EBITDA",
    "Margem EBITDA",
]


# Fase 3.1 (29/09/2026, "alinha todo o app com esse escopo... os kpis
# com essa funcao tb"): 2 documentos de abrangencia diferente podem estar
# ATIVOS ao mesmo tempo pro MESMO periodo_fim (ex. trimestral e semestral
# fechando 30/06/2026 -- ver db.inativar_periodo_existente). Sem
# resolucao, o groupby(periodo, conta) de _pivot somava os 2 no mesmo
# ponto da serie -- indicador errado, sem aviso.
#
# Fase 4 (30/09/2026, bug real confirmado pelo Rafael -- "o match de
# periodo+granularidade e' OBRIGATORIO em tudo"): a "vencedora" por
# periodo_fim (abaixo) IGNORAVA a granularidade que o usuario escolheu
# pro relatorio -- um relatorio TRIMESTRAL de 06/2026 da Energia mostrava
# receita do trimestral mas EBITDA/margens do SEMESTRAL. Agora
# calcular_indicadores(granularidade=...) filtra EXATAMENTE a
# granularidade pedida (sem vencedora). A vencedora so' continua valendo
# pra chamador legado que NAO informa granularidade (None).
#
# Desempate da "vencedora" (so' modo legado / escolha do DEFAULT de
# dashboard): documento mais abrangente/oficial vence -- anual >
# semestral > trimestral > bimestral > mensal > outra > "" (nao
# declarada).
_PRIORIDADE_GRANULARIDADE = {
    "anual": 5, "semestral": 4, "trimestral": 3, "bimestral": 2, "mensal": 1, "outra": 0, "": -1,
}

NOME_GRANULARIDADE = {
    "mensal": "Mensal", "bimestral": "Bimestral", "trimestral": "Trimestral",
    "semestral": "Semestral", "anual": "Anual", "outra": "Outro intervalo", "": "Não declarada",
}


def rotulo_granularidade(g) -> str:
    """Nome legivel da granularidade ('' -> 'Não declarada')."""
    g = g or ""
    return NOME_GRANULARIDADE.get(g, g)


def _g(l: dict) -> str:
    return l.get("granularidade") or ""


def _filtrar_granularidade_vencedora(lancamentos: list[dict]) -> list[dict]:
    """
    Mantem so' as linhas da granularidade vencedora de cada periodo_fim
    (ver criterio acima). Sem coluna 'granularidade' no dado (chamador
    antigo / mock de teste sem essa coluna) -- no-op, comportamento de
    sempre intacto. MODO LEGADO: so' usado quando calcular_indicadores
    e' chamado sem granularidade explicita.
    """
    if not lancamentos or "granularidade" not in lancamentos[0]:
        return lancamentos
    vencedora_por_periodo: dict = {}
    for l in lancamentos:
        p = l["periodo"]
        g = _g(l)
        atual = vencedora_por_periodo.get(p)
        if atual is None or _PRIORIDADE_GRANULARIDADE.get(g, -1) > _PRIORIDADE_GRANULARIDADE.get(atual, -1):
            vencedora_por_periodo[p] = g
    return [l for l in lancamentos if _g(l) == vencedora_por_periodo.get(l["periodo"])]


def filtrar_granularidade_exata(lancamentos: list[dict], granularidade: str) -> list[dict]:
    """
    Mantem SO' as linhas cuja granularidade e' exatamente `granularidade`
    ('' = nao declarada). Linha sem a chave 'granularidade' (mock antigo)
    conta como ''. Nunca escolhe "vencedora": e' o match obrigatorio de
    (periodo, granularidade).
    """
    granularidade = granularidade or ""
    return [l for l in lancamentos if _g(l) == granularidade]


def granularidades_disponiveis(*listas_lancamentos: list[dict]) -> list[str]:
    """Granularidades que existem de fato nas listas (BP e/ou DRE),
    da mais abrangente pra menos (anual ... mensal, outra, nao declarada)."""
    achadas = {_g(l) for lista in listas_lancamentos for l in (lista or [])}
    return sorted(achadas, key=lambda g: _PRIORIDADE_GRANULARIDADE.get(g, -1), reverse=True)


def granularidade_padrao(lancs_bp: list[dict], lancs_dre: list[dict], empresas: Optional[list] = None) -> str:
    """
    Base de periodo (granularidade) padrao pra um dashboard: a do
    periodo_fim MAIS RECENTE (se esse periodo_fim tem mais de uma, a mais
    abrangente). Com `empresas` (consolidado de grupo), so' considera
    (periodo, granularidade) em que TODAS as empresas tem dado -- senao o
    default cairia num documento que so' 1 empresa tem (ex. semestral
    so' da Energia) e o "consolidado das 6" seria enganoso; se nenhum
    par for completo, cai no mais recente/abrangente mesmo assim. Sem
    dado nenhum devolve ''.
    """
    pares: dict = {}  # (periodo, g) -> set(empresas com linha)
    for l in list(lancs_bp or []) + list(lancs_dre or []):
        pares.setdefault((l["periodo"], _g(l)), set()).add(l.get("empresa_codigo"))
    if not pares:
        return ""
    ordem = sorted(
        pares,
        key=lambda pg: (pg[0], _PRIORIDADE_GRANULARIDADE.get(pg[1], -1)),
        reverse=True,
    )
    if empresas:
        exigidas = set(empresas)
        for pg in ordem:
            if exigidas <= pares[pg]:
                return pg[1]
    return ordem[0][1]


def empresas_faltando(lancs_bp: list[dict], lancs_dre: list[dict], empresas: list, periodo, granularidade: str) -> list:
    """Codigos de `empresas` SEM nenhuma linha (BP ou DRE) em
    (periodo, granularidade) -- pra avisar consolidado parcial."""
    granularidade = granularidade or ""
    presentes = {
        l.get("empresa_codigo")
        for l in list(lancs_bp or []) + list(lancs_dre or [])
        if l["periodo"] == periodo and _g(l) == granularidade
    }
    return [e for e in empresas if e not in presentes]


def _pivot(lancamentos: list[dict], contas: list[str]) -> pd.DataFrame:
    """(periodo x conta), valor float, só as contas pedidas. Vazio (sem
    period nenhum) se não tiver dado nenhum bater."""
    if not lancamentos:
        return pd.DataFrame(columns=contas)
    df = pd.DataFrame(lancamentos)
    df = df[df["conta"].isin(contas)]
    if df.empty:
        return pd.DataFrame(columns=contas)
    df["valor"] = df["valor"].astype(float)  # psycopg2 devolve Decimal, nao float
    pivot = df.groupby(["periodo", "conta"])["valor"].sum().unstack("conta")
    return pivot.reindex(columns=contas)


def calcular_indicadores(
    lancamentos_bp: list[dict], lancamentos_dre: list[dict], granularidade: Optional[str] = None,
) -> pd.DataFrame:
    """
    granularidade (Fase 4, 30/09/2026): quando informada (inclusive ""
    = nao declarada), usa EXATAMENTE as linhas dessa granularidade --
    sem "vencedora", sem misturar. None = comportamento legado
    (vencedora por periodo_fim), so' pra chamador que ainda nao sabe
    qual granularidade quer.

    lancamentos_bp/lancamentos_dre: saida de db.listar_historico_grupo
    (1 empresa, 1 tipo, todos os periodos ATIVO) -- lista de dicts com
    periodo/grupo/conta/valor.

    Devolve DataFrame indexado por pd.DatetimeIndex (nao string -- mesma
    convencao de visao_grupo.montar_serie_kpis_grupo, evita o bug de
    ordenacao alfabetica que o grafico do Dashboard de Projecao teve),
    colunas = COLUNAS_INDICADORES. Vazio se nao tiver periodo nenhum em
    BP nem DRE.
    """
    if granularidade is None:
        lancamentos_bp = _filtrar_granularidade_vencedora(lancamentos_bp)
        lancamentos_dre = _filtrar_granularidade_vencedora(lancamentos_dre)
    else:
        lancamentos_bp = filtrar_granularidade_exata(lancamentos_bp, granularidade)
        lancamentos_dre = filtrar_granularidade_exata(lancamentos_dre, granularidade)
    bp = _pivot(lancamentos_bp, CONTAS_BP)
    dre = _pivot(lancamentos_dre, CONTAS_DRE)

    periodos = sorted(set(bp.index) | set(dre.index))
    if not periodos:
        vazio = pd.DataFrame(columns=COLUNAS_INDICADORES)
        vazio.index = pd.DatetimeIndex([], name="periodo")
        return vazio

    bp = bp.reindex(index=periodos)
    dre = dre.reindex(index=periodos)

    def _div(a: pd.Series, b: pd.Series) -> pd.Series:
        return a / b.where(b != 0)

    out = pd.DataFrame(index=periodos)
    ativo_circ = bp["TOTAL CIRCULANTE ATIVO"]
    passivo_circ = bp["TOTAL CIRCULANTE PASSIVO"]
    passivo_exigivel = bp["TOTAL CIRCULANTE PASSIVO"].fillna(0) + bp["TOTAL NAO CIRCULANTE PASSIVO"].fillna(0)
    # se as 2 partes do passivo exigivel vieram NaN nos 2, mantem NaN (sem dado), nao 0
    passivo_exigivel = passivo_exigivel.where(~(bp["TOTAL CIRCULANTE PASSIVO"].isna() & bp["TOTAL NAO CIRCULANTE PASSIVO"].isna()))

    out["Liquidez Corrente"] = _div(ativo_circ, passivo_circ)
    out["Capital de Giro"] = ativo_circ - passivo_circ
    out["Endividamento Geral"] = _div(passivo_exigivel, bp["TOTAL DO ATIVO"])
    # Alavancagem (24/09/2026, pedido junto do gerador de relatorio comentado):
    # R$ de capital de terceiros pra cada R$ 1,00 de capital proprio.
    # Mesmo passivo_exigivel do Endividamento Geral, so' muda o denominador
    # (PL em vez de Ativo total) -- e' a leitura "5,40x" que aparece nos
    # modelos de relatorio comentado (COMERCIAL/TECNICO), pagina de Balanco.
    out["Alavancagem"] = _div(passivo_exigivel, bp["TOTAL PATRIMONIO LIQUIDO"])
    out["Margem Bruta"] = _div(dre["LUCRO BRUTO"], dre["RECEITA OPERACIONAL LIQUIDA"])
    out["Margem Líquida"] = _div(dre["LUCRO LIQUIDO DO EXERCICIO"], dre["RECEITA OPERACIONAL LIQUIDA"])
    out["ROA"] = _div(dre["LUCRO LIQUIDO DO EXERCICIO"], bp["TOTAL DO ATIVO"])
    out["ROE"] = _div(dre["LUCRO LIQUIDO DO EXERCICIO"], bp["TOTAL PATRIMONIO LIQUIDO"])

    # EBITDA (24/09/2026) -- ver formula comentada no topo do modulo.
    # fillna(0) so' nos componentes aditivos (resultado financeiro e D&A
    # -- ausencia real vale 0), NUNCA no LUCRO OPERACIONAL LIQUIDO em si
    # (ausencia dele = EBITDA desconhecido nesse periodo, fica NaN).
    resultado_financeiro = dre["DESPESAS FINANCEIRAS"].fillna(0) + dre["RECEITAS FINANCEIRAS"].fillna(0)
    d_a = dre["DEPRECIACOES"].fillna(0) + dre["AMORTIZACOES"].fillna(0)
    ebit = dre["LUCRO OPERACIONAL LIQUIDO"] - resultado_financeiro
    out["EBITDA"] = ebit - d_a
    out["Margem EBITDA"] = _div(out["EBITDA"], dre["RECEITA OPERACIONAL LIQUIDA"])

    out.index = pd.to_datetime(out.index)
    out.index.name = "periodo"
    return out
