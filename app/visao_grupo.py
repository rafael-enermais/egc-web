# -*- coding: utf-8 -*-
"""
Logica de agregacao da Visao Grupo -- modulo puro (sem streamlit/db, mesma
filosofia de projecao.py/parser_egc.py/validacoes.py: testavel isolado com
dado sintetico). Extraido de app/telas/4_Visao_Grupo.py em 22/09/2026 pra
ser reaproveitado tambem pela ferramenta "consultar_visao_grupo" do chat
(6_Assistente.py) -- MESMA logica testada, sem duplicar (regra do Rafael:
preservar o que ja funciona, evitar reescrever).

3 funcoes, na ordem em que sao chamadas:
  1. montar_pivot_grupo -- pivota lancamentos "achatados" (1 linha por
     empresa+grupo+conta+valor, formato de db.listar_lancamentos_grupo)
     em 1 linha por (grupo,conta) x 1 coluna por empresa + VALOR CONSOLIDADO.
  2. visao_macro -- Energia separada x Empresas Consolidadoras somadas +
     % de participacao (igual a planilha real "BALANCO GRUPO"/"DRE GRUPO").
  3. visao_especifica -- as empresas selecionadas abertas 1 a 1, sem agrupar
     (so' renomeia coluna de codigo pra nome).

Premissa (ja testada ao vivo antes da 1a versao desta tela, 21/09/2026):
pivot por igualdade EXATA de (grupo,conta) e' seguro -- nomes de conta
batem exatamente entre empresas quando compartilhados, sem colisao.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

COD_ENERGIA = "ENERGIA"
CODS_CONSOLIDADORAS = ["ENG", "CONST", "RENOV", "SOL", "SMG"]

# Contas-chave usadas como KPI do resumo da Visao Grupo "Completo" (22/09/2026,
# pedido do Rafael: "mais informacoes, detalhes, graficos, numeros prontos" +
# "a visao poder alcancar tudo de todos os periodos"). Nomes EXATOS de saida
# do parser (parser_egc.py, dicionario CONTAS_BP/CONTAS_DRE, campo nome_saida
# -- conferido no codigo antes de usar, nao assumido): TOTAL DO ATIVO/PASSIVO
# ja e' usado em validacoes.checar_fechamento_bp; as 3 linhas de DRE sao as
# que o parser SEMPRE tenta ter (calculadas se o SPED nao trouxer explicito,
# ver parser_egc.calcular_derivados_dre).
CONTAS_KPI_BP = ["TOTAL DO ATIVO", "TOTAL DO PASSIVO"]
CONTAS_KPI_DRE = ["RECEITA OPERACIONAL LIQUIDA", "LUCRO BRUTO", "LUCRO LIQUIDO DO EXERCICIO"]

# Fase 3.1 (29/09/2026) -- mesmo criterio de desempate de
# indicadores._PRIORIDADE_GRANULARIDADE (documento mais abrangente/oficial
# vence quando 2 granularidades estao ATIVAS no mesmo periodo_fim):
# anual > semestral > trimestral > bimestral > mensal > outra > "" (nao
# declarada). Fonte unica em indicadores.py (Fase 4, 30/09/2026).
import indicadores  # noqa: E402
from indicadores import _PRIORIDADE_GRANULARIDADE  # noqa: E402


def _filtrar_granularidade_vencedora(df: pd.DataFrame) -> pd.DataFrame:
    """
    Igual a indicadores._filtrar_granularidade_vencedora, mas em cima de
    um DataFrame (formato de db.listar_lancamentos_grupo_periodos) --
    evita que groupby(periodo) some 2 documentos de abrangencia diferente
    no mesmo ponto da serie temporal. Sem coluna 'granularidade' (dado
    antigo/mock de teste sem essa coluna) -- no-op.
    """
    if df.empty or "granularidade" not in df.columns:
        return df
    prioridade = df["granularidade"].fillna("").map(lambda g: _PRIORIDADE_GRANULARIDADE.get(g, -1))
    vencedora_por_periodo = (
        df.assign(_prio=prioridade)
        .sort_values("_prio", ascending=False)
        .drop_duplicates("periodo")
        .set_index("periodo")["granularidade"]
    )
    return df[df["granularidade"] == df["periodo"].map(vencedora_por_periodo)]


def montar_pivot_grupo(lancamentos: list[dict], cods_selecionados: list[str]) -> pd.DataFrame:
    """
    lancamentos: [{"empresa_codigo", "grupo", "conta", "valor"}, ...] (formato
    de db.listar_lancamentos_grupo -- valor pode vir Decimal do psycopg2).
    Retorna DataFrame com colunas: grupo, conta, 1 coluna por codigo em
    cods_selecionados, VALOR CONSOLIDADO. Lista vazia -> DataFrame vazio
    (mesmas colunas esperadas, 0 linhas) pra quem chama decidir o que fazer
    (a tela para com st.stop(), a ferramenta do chat devolve lista vazia).
    """
    if not lancamentos:
        vazio = pd.DataFrame(columns=["grupo", "conta", "VALOR CONSOLIDADO"] + list(cods_selecionados))
        return vazio

    df = pd.DataFrame(lancamentos)
    # psycopg2 devolve NUMERIC do Postgres como decimal.Decimal (nao float) --
    # sem este cast, pivot_table gera colunas dtype=object (Decimal) que o
    # reindex(fill_value=0.0) mistura com float puro, e ".sum(axis=1)" quebra
    # com TypeError. Bug real ja corrigido na 1a versao desta tela (22/09/2026).
    df["valor"] = df["valor"].astype(float)
    pivot = df.pivot_table(
        index=["grupo", "conta"], columns="empresa_codigo", values="valor", aggfunc="sum", fill_value=0.0
    )
    # reindex: garante 1 coluna por empresa selecionada mesmo quando ela nao
    # tem NENHUM lancamento neste periodo+tipo (senao a coluna nem apareceria)
    pivot = pivot.reindex(columns=cods_selecionados, fill_value=0.0).reset_index()
    pivot["VALOR CONSOLIDADO"] = pivot[cods_selecionados].sum(axis=1)
    return pivot


def _pct(numerador: pd.Series, denominador: pd.Series) -> pd.Series:
    # 0/0 -> NaN, x/0 -> inf/-inf; ambos viram 0 (sem participacao definida
    # quando o consolidado da conta e' 0)
    return (numerador / denominador).replace([float("inf"), float("-inf")], 0.0).fillna(0.0)


def visao_macro(pivot: pd.DataFrame, cods_selecionados: list[str]) -> pd.DataFrame:
    """Energia separada x Empresas Consolidadoras somadas, com % de participacao."""
    cods_consol_presentes = [c for c in CODS_CONSOLIDADORAS if c in cods_selecionados]
    tem_energia = COD_ENERGIA in cods_selecionados

    saida = pivot[["grupo", "conta", "VALOR CONSOLIDADO"]].copy()
    saida["ENERMAIS ENERGIA"] = pivot[COD_ENERGIA] if tem_energia else 0.0
    saida["% ENERGIA"] = _pct(saida["ENERMAIS ENERGIA"], saida["VALOR CONSOLIDADO"])
    saida["EMPRESAS CONSOLIDADORAS"] = (
        pivot[cods_consol_presentes].sum(axis=1) if cods_consol_presentes else 0.0
    )
    saida["% CONSOLIDADORAS"] = _pct(saida["EMPRESAS CONSOLIDADORAS"], saida["VALOR CONSOLIDADO"])
    return saida


def visao_especifica(pivot: pd.DataFrame, cods_selecionados: list[str], nome_por_cod: dict) -> pd.DataFrame:
    """As empresas selecionadas abertas 1 a 1 (colunas renomeadas de codigo pra nome), sem agrupar."""
    saida = pivot[["grupo", "conta", "VALOR CONSOLIDADO"] + cods_selecionados].copy()
    saida = saida.rename(columns=nome_por_cod)
    return saida


def resumir_completude_por_periodo(completude: pd.DataFrame) -> pd.DataFrame:
    """
    Resume calcular_completude_grupo (1 linha por empresa x periodo) pra
    1 linha por periodo -- pedido do Rafael (23/09/2026, 2a leva de
    feedback ao vivo): a versao anterior do painel de pendencias tinha 2
    tabelas parecidas (pendentes + matriz completa no expander), "parece
    repetida". Esta funcao junta tudo numa visao MACRO: por periodo, quantas
    empresas estao completas e quais ainda faltam -- suficiente pra saber o
    que falta sem precisar abrir nada.

    completude: saida de calcular_completude_grupo (colunas Período,
    Granularidade, Empresa, empresa_codigo, tem_bp, tem_dre, Status).

    Retorna 1 linha por (periodo, granularidade) -- mais recente primeiro
    -- colunas: Período, Granularidade, Status ("✅ Completo (N/N)" ou
    "⚠️ Incompleto (x/N)"), Empresas pendentes (nomes separados por
    vírgula, "—" se completo). Fase 3 (29/09/2026): agrupa por
    (Período, Granularidade) em vez de só Período -- um trimestral e um
    semestral que fecham na MESMA data agora aparecem como 2 linhas
    distintas, cada 1 com seu próprio status de completude, em vez de se
    misturarem numa só. DataFrame vazio (mesmas colunas) se completude
    vier vazio.
    """
    colunas = ["Período", "Granularidade", "Status", "Empresas pendentes"]
    if completude.empty:
        return pd.DataFrame(columns=colunas)

    linhas = []
    for (periodo, granularidade), grupo in completude.groupby(["Período", "Granularidade"], sort=False):
        total = len(grupo)
        completas = grupo[grupo["Status"] == "✅ Completo"]
        pendentes = grupo[grupo["Status"] != "✅ Completo"]
        n_completas = len(completas)
        if pendentes.empty:
            status = f"✅ Completo ({n_completas}/{total})"
            nomes_pendentes = "—"
        else:
            status = f"⚠️ Incompleto ({n_completas}/{total})"
            nomes_pendentes = ", ".join(pendentes["Empresa"])
        linhas.append({
            "Período": periodo, "Granularidade": granularidade,
            "Status": status, "Empresas pendentes": nomes_pendentes,
        })

    saida = pd.DataFrame(linhas, columns=colunas)
    return saida.sort_values(["Período", "Granularidade"], ascending=[False, True]).reset_index(drop=True)


def calcular_completude_grupo(
    periodos: list, lancs_bp: list[dict], lancs_dre: list[dict],
    empresas: list[tuple[str, str, str]],
) -> pd.DataFrame:
    """
    Matriz de completude de dados por empresa x periodo -- usada no painel
    de pendencias/completude (23/09/2026, retomando item deferido em
    22/09: "quais pendencias ainda faltam alem do gerador de
    relatorios?"; movido pra pagina do Relatorio Comentado em 29/09/2026
    -- "o painel de pendencias... poderia ir p pagina do gerador,
    poderia conferir quais disponivel para geracao"). Zero query nova:
    lancs_bp/lancs_dre vem de db.listar_lancamentos_grupo_periodos (mesma
    funcao ja usada no resumo do grupo desta tela), so' reaproveita pra
    marcar presenca/ausencia por (empresa, periodo, granularidade).

    periodos: lista de periodos a cobrir -- ACEITA tanto list[date]
    (comportamento antigo, granularidade fica sempre "") quanto
    list[dict] no formato de db.listar_periodos_grupo_detalhado
    ({"periodo","granularidade"}) -- Fase 3 (29/09/2026): granularidade
    faz parte da identidade do periodo (2 documentos de abrangencia
    diferente, ex. trimestral e semestral, podem estar ATIVOS ao mesmo
    tempo no MESMO periodo_fim -- ver db.inativar_periodo_existente) e o
    painel de completude nao pode mais colapsar os 2 numa linha so'.
    empresas: lista (codigo, nome, cnpj), mesmo formato de
    conexao.EMPRESAS_FIXAS.

    Retorna 1 linha por (periodo, granularidade, empresa) -- colunas:
    Período (date), Granularidade (str, "" = não declarada), Empresa
    (nome), empresa_codigo, tem_bp (bool), tem_dre (bool), Status
    ("Completo"/"So' BP"/"So' DRE"/"Faltando"). Lista de periodos vazia
    devolve DataFrame vazio com as mesmas colunas (nada pra iterar).
    """
    colunas = ["Período", "Granularidade", "Empresa", "empresa_codigo", "tem_bp", "tem_dre", "Status"]
    if not periodos:
        return pd.DataFrame(columns=colunas)

    periodos_norm = [
        (p["periodo"], p["granularidade"]) if isinstance(p, dict) else (p, "")
        for p in periodos
    ]

    def _chave(r: dict) -> tuple:
        return (r["empresa_codigo"], r["periodo"], r.get("granularidade", ""))

    set_bp = {_chave(r) for r in lancs_bp}
    set_dre = {_chave(r) for r in lancs_dre}

    linhas = []
    for periodo, granularidade in periodos_norm:
        for cod, nome, _cnpj in empresas:
            tem_bp = (cod, periodo, granularidade) in set_bp
            tem_dre = (cod, periodo, granularidade) in set_dre
            if tem_bp and tem_dre:
                status = "✅ Completo"
            elif tem_bp:
                status = "⚠️ Só BP"
            elif tem_dre:
                status = "⚠️ Só DRE"
            else:
                status = "❌ Faltando"
            linhas.append({
                "Período": periodo, "Granularidade": granularidade, "Empresa": nome, "empresa_codigo": cod,
                "tem_bp": tem_bp, "tem_dre": tem_dre, "Status": status,
            })
    return pd.DataFrame(linhas, columns=colunas)


def montar_serie_kpis_grupo(
    lancamentos_multi: list[dict], cods_selecionados: list[str], contas_kpi: list[str], periodos: list,
    granularidade: Optional[str] = None,
) -> pd.DataFrame:
    """
    granularidade (Fase 4, 30/09/2026): quando informada (inclusive ""),
    usa EXATAMENTE essa granularidade em todos os periodos (match
    obrigatorio de periodo+granularidade, sem "vencedora"); None mantem
    o desempate legado (documento mais abrangente por periodo_fim).

    Serie temporal das contas-chave (CONTAS_KPI_BP ou CONTAS_KPI_DRE) somadas
    entre as empresas selecionadas, 1 linha por periodo -- usada no "Resumo
    do grupo" da Visao Grupo "Completo" (KPIs + grafico de evolucao).

    lancamentos_multi: formato de db.listar_lancamentos_grupo_periodos ([{
    "empresa_codigo","periodo","grupo","conta","valor"}, ...], varios
    periodos misturados). `periodos` e' a lista COMPLETA de periodos a
    cobrir (nao inferida do dado) -- garante 1 linha por periodo pedido
    mesmo se nenhuma das contas_kpi apareceu nele (fica 0.0, nao some a
    linha).

    Indice do DataFrame retornado e' pd.DatetimeIndex de verdade (mesma
    razao do fix do grafico do Dashboard de Projecao, 22/09/2026: um
    indice de STRING faz o st.line_chart/Vega-Lite reordenar por ordem
    alfabetica em vez de cronologica -- nunca repetir esse bug aqui).
    """
    colunas = list(contas_kpi)
    if not periodos:
        vazio = pd.DataFrame(columns=colunas)
        vazio.index = pd.DatetimeIndex([], name="periodo")
        return vazio

    if lancamentos_multi:
        df = pd.DataFrame(lancamentos_multi)
        df["valor"] = df["valor"].astype(float)  # Decimal do psycopg2 -- mesma regra de sempre
        df = df[df["empresa_codigo"].isin(cods_selecionados) & df["conta"].isin(contas_kpi)]
        if granularidade is None:
            df = _filtrar_granularidade_vencedora(df)
        elif not df.empty:
            if "granularidade" in df.columns:
                df = df[df["granularidade"].fillna("") == (granularidade or "")]
            elif granularidade:  # dado sem a coluna conta como "" -- nao casa com uma granularidade declarada
                df = df.iloc[0:0]
        agrupado = df.groupby(["periodo", "conta"])["valor"].sum().unstack("conta") if not df.empty else pd.DataFrame()
    else:
        agrupado = pd.DataFrame()

    agrupado = agrupado.reindex(index=sorted(periodos), columns=colunas, fill_value=0.0)
    agrupado.index = pd.to_datetime(agrupado.index)
    agrupado.index.name = "periodo"
    return agrupado


def montar_indicadores_grupo(
    lancs_bp: list[dict], lancs_dre: list[dict], cods_selecionados: list[str], periodos: list, granularidade: str,
) -> pd.DataFrame:
    """v0.40.0 -- indicadores do GRUPO (EBITDA, Margem EBITDA, Margem
    Liquida, Alavancagem, Capital de Giro, ROA/ROE, Liquidez, Endividamento
    ...) por periodo, calculados com `indicadores.calcular_indicadores` em
    cima das contas SOMADAS das empresas selecionadas (consolidado simples,
    sem eliminacoes entre empresas -- mesma base dos KPIs do "Resumo do
    grupo"). Razoes (margens, ROA, alavancagem) sao calculadas sobre os
    totais somados, NAO a media das razoes de cada empresa.

    Match obrigatorio de granularidade (`granularidade`, inclusive "" =
    nao declarada): so' linhas dessa base entram -- nunca mistura
    trimestral com semestral. 1 linha por periodo pedido (linha de NaN
    quando o periodo nao tem nenhum dado nessa base)."""
    sel = set(cods_selecionados)
    bp = [r for r in lancs_bp if r["empresa_codigo"] in sel]
    dre = [r for r in lancs_dre if r["empresa_codigo"] in sel]
    ind = indicadores.calcular_indicadores(bp, dre, granularidade=granularidade or "")
    alvo = pd.DatetimeIndex(pd.to_datetime(sorted(periodos)), name="periodo")
    return ind.reindex(alvo)


# ─────────────────────────────────────────────────────────────────────
#  Dashboard (01/10/2026, "faz um dash com leitura boa, dinamica, fluida")
# ─────────────────────────────────────────────────────────────────────
CONTAS_ESTRUTURA_BP = ["TOTAL CIRCULANTE ATIVO", "TOTAL CIRCULANTE PASSIVO", "TOTAL NAO CIRCULANTE PASSIVO",
                       "TOTAL PATRIMONIO LIQUIDO"]


def montar_resumo_por_empresa(
    lancs_bp: list[dict], lancs_dre: list[dict], cods_selecionados: list[str], periodo, granularidade: str,
) -> pd.DataFrame:
    """1 linha por empresa (indice = codigo) no `periodo`/`granularidade`:
    Receita Líquida, EBITDA, Margem EBITDA, Resultado Líquido, Total do Ativo,
    Endividamento Geral. Mesma formula do consolidado (indicadores.
    calcular_indicadores, base unica = a granularidade escolhida); empresa
    sem documento nesse periodo/base fica com NaN -- nunca 0 inventado."""
    colunas = ["Receita Líquida", "EBITDA", "Margem EBITDA", "Resultado Líquido", "Total do Ativo",
               "Endividamento Geral"]
    ts = pd.Timestamp(periodo)
    linhas = {}
    for cod in cods_selecionados:
        bp = [r for r in lancs_bp if r["empresa_codigo"] == cod]
        dre = [r for r in lancs_dre if r["empresa_codigo"] == cod]
        ind = indicadores.calcular_indicadores(bp, dre, granularidade=granularidade or "")
        serie_dre = montar_serie_kpis_grupo(dre, [cod], CONTAS_KPI_DRE, [periodo], granularidade=granularidade or "")
        serie_bp = montar_serie_kpis_grupo(bp, [cod], CONTAS_KPI_BP, [periodo], granularidade=granularidade or "")
        tem_dre = any(r["periodo"] == periodo and (r.get("granularidade") or "") == (granularidade or "") for r in dre)
        tem_bp = any(r["periodo"] == periodo and (r.get("granularidade") or "") == (granularidade or "") for r in bp)
        nan = float("nan")
        linhas[cod] = {
            "Receita Líquida": float(serie_dre.loc[ts, "RECEITA OPERACIONAL LIQUIDA"]) if tem_dre else nan,
            "EBITDA": float(ind.loc[ts, "EBITDA"]) if ts in ind.index else nan,
            "Margem EBITDA": float(ind.loc[ts, "Margem EBITDA"]) if ts in ind.index else nan,
            "Resultado Líquido": float(serie_dre.loc[ts, "LUCRO LIQUIDO DO EXERCICIO"]) if tem_dre else nan,
            "Total do Ativo": float(serie_bp.loc[ts, "TOTAL DO ATIVO"]) if tem_bp else nan,
            "Endividamento Geral": float(ind.loc[ts, "Endividamento Geral"]) if ts in ind.index else nan,
        }
    df = pd.DataFrame.from_dict(linhas, orient="index", columns=colunas)
    df.index.name = "empresa"
    return df


def _var_pct(atual: float, anterior: float):
    if pd.isna(atual) or pd.isna(anterior) or anterior == 0:
        return None
    return (atual - anterior) / abs(anterior)


def gerar_destaques(
    ind_grupo: pd.DataFrame, serie_dre: pd.DataFrame, serie_bp: pd.DataFrame, resumo_empresas: pd.DataFrame,
    periodo, periodo_anterior, nome_por_cod: dict,
) -> list[tuple[str, str]]:
    """'Leitura rápida' do topo do dashboard: frases FACTUAIS calculadas dos
    mesmos numeros dos KPIs (nada de opiniao nem limiar inventado) -- so'
    reporta o que os dados mostram. Devolve [(nivel, texto)] com nivel
    'info' ou 'atencao', na ordem em que devem aparecer. Sem dado pra uma
    frase, a frase simplesmente nao aparece."""
    import formatacao as _f
    ts = pd.Timestamp(periodo)
    ts_ant = pd.Timestamp(periodo_anterior) if periodo_anterior is not None else None
    out: list[tuple[str, str]] = []

    rec = serie_dre.loc[ts, "RECEITA OPERACIONAL LIQUIDA"] if ts in serie_dre.index else float("nan")
    rec_ant = serie_dre.loc[ts_ant, "RECEITA OPERACIONAL LIQUIDA"] if ts_ant is not None and ts_ant in serie_dre.index else float("nan")
    var = _var_pct(rec, rec_ant)
    if var is not None:
        verbo = "cresceu" if var >= 0 else "caiu"
        out.append(("info" if var >= 0 else "atencao",
                    f"A receita líquida {verbo} {_f.pct_br(abs(var))} vs o período anterior "
                    f"({_f.moeda_curta(rec_ant)} → {_f.moeda_curta(rec)})."))

    ebitda = ind_grupo.loc[ts, "EBITDA"] if ts in ind_grupo.index else float("nan")
    m_ebitda = ind_grupo.loc[ts, "Margem EBITDA"] if ts in ind_grupo.index else float("nan")
    if pd.notna(ebitda):
        txt = ("EBITDA positivo" if ebitda >= 0 else "EBITDA negativo no consolidado")
        if pd.notna(m_ebitda):
            txt += f", margem EBITDA de {_f.pct_br(m_ebitda)}"
        out.append(("info" if ebitda >= 0 else "atencao", txt + "."))

    liq = ind_grupo.loc[ts, "Liquidez Corrente"] if ts in ind_grupo.index else float("nan")
    cg = ind_grupo.loc[ts, "Capital de Giro"] if ts in ind_grupo.index else float("nan")
    if pd.notna(liq) and liq < 1:
        out.append(("atencao", f"Liquidez corrente de {_f.numero_br(liq, sufixo='x')}: o ativo circulante não cobre o passivo circulante "
                               f"(capital de giro {_f.moeda_curta(cg)})."))

    if resumo_empresas is not None and not resumo_empresas.empty:
        rec_emp = resumo_empresas["Receita Líquida"].dropna()
        if len(rec_emp) >= 2 and rec_emp.sum() > 0:
            top = rec_emp.idxmax()
            out.append(("info", f"{nome_por_cod.get(top, top)} responde por {_f.pct_br(rec_emp[top] / rec_emp.sum())} da receita do período."))
        neg = resumo_empresas["EBITDA"].dropna()
        neg = neg[neg < 0].sort_values()
        if len(neg):
            nomes = ", ".join(nome_por_cod.get(c, c) for c in neg.index)
            out.append(("atencao", f"Empresas com EBITDA negativo no período: {nomes}."))

    ativo = serie_bp.loc[ts, "TOTAL DO ATIVO"] if ts in serie_bp.index else float("nan")
    passivo = serie_bp.loc[ts, "TOTAL DO PASSIVO"] if ts in serie_bp.index else float("nan")
    if pd.notna(ativo) and pd.notna(passivo) and abs(ativo - passivo) > 1.0:
        out.append(("atencao", f"Ativo ({_f.moeda_curta(ativo)}) diferente do Passivo ({_f.moeda_curta(passivo)}) no consolidado — "
                               "confira o BP das empresas."))
    return out

