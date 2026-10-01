# -*- coding: utf-8 -*-
"""
Ferramentas de consulta (tool-use) expostas ao chat estilo TIA.go/Viaj.ai --
5o item da fila com Rafael (Visao Grupo -> rodape -> Dashboard de Projecao ->
chat). Escopo combinado com Rafael em 22/09/2026 (AskUserQuestion): so'
BP/DRE (1 empresa) + Visao Grupo (consolidado) entram no chat nesta versao --
Dashboard de Projecao FICA DE FORA de proposito (Rafael levantou a duvida
"de onde vem essa projecao" e concordou que apresentar um "ultimo valor
repetido" (metodo flat_ultimo_valor, hoje maioria das contas por so' existir
1 periodo real no banco) como se fosse previsao respondida pelo chat seria
enganoso enquanto o historico real for tao curto).

Modulo "fino": cada funcao recebe uma conexao ja aberta (mesmo padrao de
db.py) e devolve dict/list JSON-serializavel (proibido devolver Decimal ou
date cru -- ver conversao explicita em cada funcao, mesma razao do
TypeError Decimal+float ja mapeado em projecao.py/visao_grupo.py: o texto
que volta pro modelo via tool_result precisa ser serializavel sem gambiarra
na hora de montar o content da API da Anthropic).

Reaproveita db.py (leitura) e visao_grupo.py (agregacao) -- nao duplica
nenhuma logica ja existente, so' resolve parametros (periodo em texto
"AAAA-MM" -> date real do banco) e formata a saida pro tool_result.
"""
from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

import db
import indicadores
import nf_sienge
import visao_grupo
from conexao import EMPRESAS_FIXAS


def _resolver_periodo_detalhado(
    periodos_disponiveis: list[dict], periodo_texto: Optional[str], granularidade_texto: Optional[str] = None,
):
    """
    Fase 3.1 (29/09/2026, "alinha todo o app com esse escopo... o chat tb"):
    substitui a antiga _resolver_periodo (que so' trabalhava com list[date]
    e nao tinha como saber que um periodo_fim pode ter 2 documentos de
    abrangencia diferente ATIVOS ao mesmo tempo -- ver
    db.inativar_periodo_existente). periodos_disponiveis agora e'
    list[dict] {"periodo","granularidade"} (formato de
    db.listar_periodos_detalhado/_grupo_detalhado).

    periodo_texto "AAAA-MM" (ou None -> mais recente, casando so' por
    ano/mes). granularidade_texto opcional -- so' importa quando o
    periodo casado tem MAIS de 1 documento ativo (raro: caso comum e' 1
    granularidade so').

    Retorna (periodo, granularidade, ambiguidade):
      - achou exatamente 1 -> (date, str, None)
      - nao achou nenhum -> (None, None, None)
      - achou 2+ e granularidade_texto nao resolveu -> (None, None, dict
        com "opcoes" pro modelo oferecer escolha ao usuario, REGRA CRITICA
        do chat: nunca adivinhar qual dos 2 documentos o usuario quis).
    """
    if not periodos_disponiveis:
        return None, None, None
    if not periodo_texto:
        periodo_mais_recente = max(d["periodo"] for d in periodos_disponiveis)
        candidatos = [d for d in periodos_disponiveis if d["periodo"] == periodo_mais_recente]
    else:
        try:
            ano_s, mes_s = periodo_texto.split("-")
            ano, mes = int(ano_s), int(mes_s)
        except (ValueError, AttributeError):
            return None, None, None
        candidatos = [d for d in periodos_disponiveis if d["periodo"].year == ano and d["periodo"].month == mes]
    if not candidatos:
        return None, None, None
    if len(candidatos) == 1:
        return candidatos[0]["periodo"], candidatos[0]["granularidade"], None
    if granularidade_texto:
        casa = [d for d in candidatos if d["granularidade"] == granularidade_texto]
        if len(casa) == 1:
            return casa[0]["periodo"], casa[0]["granularidade"], None
    return None, None, {
        "ambiguo": True,
        "opcoes": [
            {"periodo": d["periodo"].strftime("%Y-%m"), "granularidade": d["granularidade"] or "não declarada"}
            for d in sorted(candidatos, key=lambda d: d["granularidade"])
        ],
    }


def consultar_periodos(conn, empresas_codigos: list[str]) -> dict:
    """
    Periodos ATIVOS disponiveis por empresa (uma a uma) -- usado pelo
    modelo pra saber quais periodos existem de verdade antes de chamar
    consultar_bp_dre/consultar_visao_grupo com um periodo especifico,
    evitando alucinar uma data que nao tem lancamento.

    Fase 3.1 (29/09/2026): quando um periodo_fim tem MAIS de 1 documento
    ativo (ex. trimestral e semestral fechando na mesma data -- ver
    db.inativar_periodo_existente), isso agora aparece explicitamente em
    "periodos_com_mais_de_1_documento" -- sem isso o modelo nao tinha
    como saber que precisa pedir a granularidade antes de chamar
    consultar_bp_dre/consultar_visao_grupo com aquele periodo. Caso
    comum (1 documento por periodo) nao muda nada visivel.
    """
    por_empresa = {}
    ambiguos_por_empresa = {}
    for cod in empresas_codigos:
        detalhados = db.listar_periodos_detalhado(conn, cod, status="ATIVO")
        por_empresa[cod] = [d["periodo"].strftime("%Y-%m") for d in sorted(detalhados, key=lambda d: d["periodo"], reverse=True)]
        contagem: dict = {}
        for d in detalhados:
            contagem[d["periodo"]] = contagem.get(d["periodo"], 0) + 1
        ambiguos = [
            {"periodo": d["periodo"].strftime("%Y-%m"), "granularidade": d["granularidade"] or "não declarada"}
            for d in sorted(detalhados, key=lambda d: (d["periodo"], d["granularidade"]), reverse=True)
            if contagem[d["periodo"]] > 1
        ]
        if ambiguos:
            ambiguos_por_empresa[cod] = ambiguos
    saida = {"periodos_por_empresa": por_empresa}
    if ambiguos_por_empresa:
        saida["periodos_com_mais_de_1_documento"] = ambiguos_por_empresa
    return saida


def consultar_bp_dre(
    conn, empresa_codigo: str, tipo: str, periodo_texto: Optional[str] = None,
    granularidade: Optional[str] = None,
) -> dict:
    """
    BP ou DRE de 1 empresa num periodo (o mais recente se periodo_texto
    vier vazio). Retorna as contas (grupo, conta, valor) -- mesma fonte
    que a tela Revisao/Correcao usa (db.listar_lancamentos), so' que so'
    leitura (o chat nao corrige lancamento).

    granularidade (Fase 3.1, 29/09/2026): opcional -- so' precisa vir
    quando o periodo escolhido tiver mais de 1 documento ativo (ver
    "ambiguo" no retorno de erro); nesse caso o modelo deve perguntar ao
    usuario qual granularidade (mensal/bimestral/trimestral/semestral/anual) e
    chamar de novo, em vez de adivinhar qual dos 2 documentos usar.
    """
    periodos_disponiveis = db.listar_periodos_detalhado(conn, empresa_codigo, status="ATIVO")
    periodo, granularidade_resolvida, ambiguidade = _resolver_periodo_detalhado(
        periodos_disponiveis, periodo_texto, granularidade,
    )
    if ambiguidade:
        return {
            "erro": (
                f"Esse período tem mais de 1 documento ativo pra {empresa_codigo} (abrangências "
                "diferentes fechando na mesma data) -- pergunte ao usuário qual granularidade "
                "(mensal/bimestral/trimestral/semestral/anual) e chame de novo com esse valor no parâmetro 'granularidade'."
            ),
            **ambiguidade,
        }
    if periodo is None:
        return {
            "erro": f"Nenhum periodo ativo encontrado pra {empresa_codigo}"
            + (f" em {periodo_texto}" if periodo_texto else ""),
            "periodos_disponiveis": [d["periodo"].strftime("%Y-%m") for d in sorted(periodos_disponiveis, key=lambda d: d["periodo"], reverse=True)],
        }
    linhas = db.listar_lancamentos(conn, empresa_codigo, periodo, tipo, status="ATIVO", granularidade=granularidade_resolvida)
    contas = [
        {"grupo": l["grupo"], "conta": l["conta"], "valor": float(l["valor"])}
        for l in linhas
    ]
    if not contas:
        # Periodo existe (ex.: so' o DRE foi importado) mas ESTE tipo nao tem
        # lancamento ativo nele -- devolver "0 contas" como sucesso fazia a
        # Consulta rapida do Erik.AI quebrar (KeyError 'valor') e deixava o
        # modelo sem sinal claro de que o dado nao existe.
        return {
            "erro": (
                f"{empresa_codigo} não tem {tipo} ativo em {periodo.strftime('%Y-%m')} "
                f"(só existe o outro demonstrativo nesse período, ou o {tipo} foi arquivado)."
            ),
            "periodos_disponiveis": [d["periodo"].strftime("%Y-%m") for d in sorted(periodos_disponiveis, key=lambda d: d["periodo"], reverse=True)],
        }
    return {
        "empresa_codigo": empresa_codigo,
        "tipo": tipo,
        "periodo": periodo.strftime("%Y-%m"),
        "granularidade": granularidade_resolvida or None,
        "quantidade_contas": len(contas),
        "contas": contas,
    }


def _tabela_indicadores(conn, empresas_codigos: list[str], granularidade: Optional[str] = None):
    """Carrega BP/DRE e calcula a tabela de indicadores (1 linha por periodo)
    numa UNICA base. Devolve (tabela, g_usada, disponiveis, exigidas, hist_bp,
    hist_dre, erro); `erro` e' um dict pronto pra devolver ao modelo (ou None)."""
    if len(empresas_codigos) == 1:
        cod = empresas_codigos[0]
        hist_bp = db.listar_historico_grupo(conn, cod, "BP", status="ATIVO")
        hist_dre = db.listar_historico_grupo(conn, cod, "DRE", status="ATIVO")
        exigidas = None
    else:
        periodos = db.listar_periodos_grupo(conn, empresas_codigos, status="ATIVO")
        hist_bp = db.listar_lancamentos_grupo_periodos(conn, periodos, "BP", empresas_codigos, status="ATIVO")
        hist_dre = db.listar_lancamentos_grupo_periodos(conn, periodos, "DRE", empresas_codigos, status="ATIVO")
        exigidas = list(empresas_codigos)

    disponiveis = indicadores.granularidades_disponiveis(hist_bp, hist_dre)
    if granularidade:
        if granularidade not in disponiveis:
            return None, None, disponiveis, exigidas, hist_bp, hist_dre, {
                "erro": (
                    f"Nao ha BP/DRE com granularidade '{granularidade}' pra essa(s) empresa(s). "
                    "Escolha uma das disponiveis (ou omita o parametro pra usar a padrao)."
                ),
                "granularidades_disponiveis": [indicadores.rotulo_granularidade(g) for g in disponiveis],
                "empresas_incluidas": empresas_codigos,
            }
        g_usada = granularidade
    else:
        g_usada = indicadores.granularidade_padrao(hist_bp, hist_dre, exigidas)

    tabela = indicadores.calcular_indicadores(hist_bp, hist_dre, granularidade=g_usada)
    if tabela.empty:
        return None, g_usada, disponiveis, exigidas, hist_bp, hist_dre, {
            "erro": "Sem BP/DRE suficiente pra calcular indicadores pra essa(s) empresa(s) ainda.",
            "empresas_incluidas": empresas_codigos,
        }
    return tabela, g_usada, disponiveis, exigidas, hist_bp, hist_dre, None


def consultar_evolucao_indicadores(conn, empresas_codigos: list[str], granularidade: Optional[str] = None,
                                   indicadores_pedidos: Optional[list[str]] = None) -> dict:
    """Serie historica dos indicadores (1 linha por periodo) numa UNICA base --
    pra perguntas de evolucao/tendencia ("a margem melhorou?", "como evoluiu o
    EBITDA do grupo?"). Mesma fonte de consultar_indicadores; nunca mistura bases."""
    tabela, g_usada, disponiveis, _exigidas, _bp, _dre, erro = _tabela_indicadores(conn, empresas_codigos, granularidade)
    if erro:
        return erro
    if indicadores_pedidos:
        cols = [c for c in tabela.columns if c in indicadores_pedidos]
        if cols:
            tabela = tabela[cols]
    linhas = []
    for periodo, linha in tabela.sort_index().iterrows():
        d = {"periodo": periodo.strftime("%Y-%m")}
        d.update({col: (None if pd.isna(v) else float(v)) for col, v in linha.items()})
        linhas.append(d)
    return {
        "empresas_incluidas": empresas_codigos,
        "base_do_periodo": indicadores.rotulo_granularidade(g_usada),
        "granularidades_disponiveis": [indicadores.rotulo_granularidade(g) for g in disponiveis],
        "quantidade_periodos": len(linhas),
        "evolucao": linhas,
    }


def consultar_indicadores(conn, empresas_codigos: list[str], granularidade: Optional[str] = None) -> dict:
    """
    Indicadores contabeis (Liquidez Corrente, Capital de Giro, Endividamento
    Geral, Margem Bruta/Liquida, ROA, ROE, EBITDA) no periodo mais recente
    disponivel -- 1 empresa isolada OU consolidado do grupo (soma das
    empresas em empresas_codigos), dependendo de quantas vierem na lista.
    Erik.AI, 23/09/2026 -- fecha o gap real que apareceu ao vivo (Rafael
    perguntou "insight" sobre o banco e o chat so' sabia devolver BP/DRE
    cru): MESMA fonte/logica das secoes "Indicadores contabeis"/"KPI
    consolidado do grupo" da Inicio (indicadores.calcular_indicadores),
    reaproveitada aqui sem duplicar nenhuma conta/formula.

    1 empresa -> db.listar_historico_grupo (caminho mais direto). 2+
    empresas -> db.listar_periodos_grupo + listar_lancamentos_grupo_periodos,
    o MESMO caminho do "KPI consolidado do grupo" da Inicio -- soma direta
    sem eliminacao, intercompany ja validado sem transacao material entre
    as 6 empresas (decisao tomada com o Rafael, nao reaberta aqui).

    granularidade (Fase 4, 30/09/2026 -- "o match de periodo+granularidade
    e' OBRIGATORIO em tudo"): todos os numeros vem de UMA granularidade
    (nunca mistura trimestral com semestral). Sem o parametro, usa a base
    padrao (periodo mais recente; se houver mais de uma, a mais
    abrangente; no grupo, so' considera bases em que TODAS as empresas
    tem dado). A resposta sempre informa a granularidade usada
    ("base_do_periodo") e as outras disponiveis.
    """
    tabela, g_usada, disponiveis, exigidas, hist_bp, hist_dre, erro = _tabela_indicadores(conn, empresas_codigos, granularidade)
    if erro:
        return erro

    periodo_atual = tabela.index.max()
    linha = tabela.loc[periodo_atual]
    valores = {col: (None if pd.isna(v) else float(v)) for col, v in linha.items()}

    saida = {
        "empresas_incluidas": empresas_codigos,
        "periodo": periodo_atual.strftime("%Y-%m"),
        "granularidade": g_usada or None,
        "base_do_periodo": indicadores.rotulo_granularidade(g_usada),
        "granularidades_disponiveis": [indicadores.rotulo_granularidade(g) for g in disponiveis],
        "indicadores": valores,
    }
    if exigidas:
        faltando = indicadores.empresas_faltando(hist_bp, hist_dre, exigidas, periodo_atual.date(), g_usada)
        if faltando:
            saida["aviso_consolidado_parcial"] = (
                f"Nessa base ({saida['base_do_periodo']}) falta dado de {', '.join(faltando)} no periodo; "
                "o consolidado soma so' as empresas que tem."
            )
    return saida


def consultar_completude(conn, empresas_codigos: list[str]) -> dict:
    """
    Pra cada periodo com QUALQUER lancamento ativo entre as empresas
    escolhidas, mostra se esta' Completo (BP+DRE de todas as empresas) ou
    quais empresas ainda faltam. Erik.AI, 23/09/2026 -- responde DIRETO a
    pergunta real que ja apareceu ao vivo ("quais PDFs faltam pra
    completar todos os CNPJs?"): antes so' dava pra inferir isso chamando
    consultar_periodos empresa por empresa e comparando na mao (o que so'
    mostra o que JA' existe, nao o que falta). MESMA fonte/logica do
    "Painel de pendencias" da Inicio (visao_grupo.calcular_completude_grupo
    + resumir_completude_por_periodo), reaproveitada sem duplicar.

    Nao rastreia upload de PDF em si (isso continua fora de escopo -- ver
    SYSTEM_PROMPT): mostra ausencia de LANCAMENTO ativo no banco, que na
    pratica e' o mesmo sinal (PDF nao importado ou importacao falhou).
    """
    empresas_tuplas = [(cod, nome, cnpj) for cod, nome, cnpj in EMPRESAS_FIXAS if cod in empresas_codigos]
    # Fase 3.1 (29/09/2026): _grupo_detalhado em vez de listar_periodos_grupo
    # (date-only) -- sem isso, um trimestral e um semestral fechando na
    # mesma data se misturavam numa unica linha de completude, mesmo bug
    # ja corrigido no painel de pendencias da tela Relatorio Comentado.
    periodos_detalhados = db.listar_periodos_grupo_detalhado(conn, empresas_codigos, status="ATIVO")
    periodos_datas = [d["periodo"] for d in periodos_detalhados]
    lancs_bp = db.listar_lancamentos_grupo_periodos(conn, periodos_datas, "BP", empresas_codigos, status="ATIVO")
    lancs_dre = db.listar_lancamentos_grupo_periodos(conn, periodos_datas, "DRE", empresas_codigos, status="ATIVO")
    completude = visao_grupo.calcular_completude_grupo(periodos_detalhados, lancs_bp, lancs_dre, empresas_tuplas)
    resumo = visao_grupo.resumir_completude_por_periodo(completude)

    linhas = resumo.to_dict(orient="records")
    for linha in linhas:
        p = linha.get("Período")
        if hasattr(p, "strftime"):
            linha["Período"] = p.strftime("%Y-%m")
        if not linha.get("Granularidade"):
            linha["Granularidade"] = "não declarada"

    return {"empresas_incluidas": empresas_codigos, "completude_por_periodo": linhas}


def consultar_notas_fiscais_kpi(conn, empresa_codigo: Optional[str] = None) -> dict:
    """
    KPI da conferência de Notas Fiscais x Sienge (feature nova, 25/09/2026):
    quantas notas na última rodada de cada empresa (ou só de uma, se
    empresa_codigo vier), quantas foram encontradas no Sienge, quantas
    ficaram pendentes, e a evolução (histórico de rodadas). Fonte:
    egc.nf_import_historico via nf_sienge.listar_historico_importacoes --
    zero query nova, so' reaproveita o que a tela "Notas Fiscais" ja usa.
    """
    historico = nf_sienge.listar_historico_importacoes(conn, empresa_codigo)
    if not historico:
        return {
            "erro": "Nenhuma conferência de notas fiscais rodada ainda"
            + (f" pra {empresa_codigo}" if empresa_codigo else " pra nenhuma empresa"),
        }
    linhas = []
    for h in historico:
        linhas.append({
            "empresa_codigo": h["empresa_codigo"],
            "periodo_referencia": h["periodo_referencia"],
            "total_notas": h["total_notas"],
            "total_lancadas": h["total_lancadas"],
            "total_pendencias": h["total_pendencias"],
            "taxa_conciliacao": round(h["total_lancadas"] / h["total_notas"], 4) if h["total_notas"] else None,
            "criado_em": h["criado_em"].strftime("%Y-%m-%d %H:%M") if hasattr(h["criado_em"], "strftime") else str(h["criado_em"]),
        })
    return {"rodadas": linhas, "total_rodadas": len(linhas)}


def consultar_notas_pendentes(conn, empresa_codigo: Optional[str] = None, limite: int = 20) -> dict:
    """
    Lista as pendências de Notas Fiscais AINDA EM ABERTO (não resolvidas
    nem descartadas) -- pra perguntas tipo "quais notas estão faltando no
    Sienge da Energia" ou "por que essa nota não foi encontrada".

    FIX_20260928f: inclui tambem a direcao reversa (titulo do Sienge sem
    nota no manifesto, egc.nf_bills_orfaos/listar_orfaos_abertos) -- sem
    isso o chat ficaria cego pro alerta que o Rafael pediu explicitamente
    ("nao e' pra acontecer, mas... importante nao passar batido").
    """
    df = nf_sienge.listar_pendencias_abertas(conn, empresa_codigo, limite)
    df_orfaos = nf_sienge.listar_orfaos_abertos(conn, empresa_codigo, limite)
    if not df_orfaos.empty:
        df = pd.concat([df, df_orfaos], ignore_index=True) if not df.empty else df_orfaos
    if df.empty:
        return {"pendencias": [], "quantidade": 0}
    df = df.copy()
    df["valor"] = df["valor"].astype(float)
    for col in ("data_emissao", "atualizado_em"):
        df[col] = df[col].apply(lambda v: v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else v)
    return {"pendencias": df.to_dict(orient="records"), "quantidade": len(df)}


def consultar_visao_grupo(
    conn,
    tipo: str,
    empresas_codigos: list[str],
    periodo_texto: Optional[str] = None,
    visao: str = "macro",
    granularidade: Optional[str] = None,
) -> dict:
    """
    Visao consolidada do grupo (Energia x Consolidadoras, ou empresa a
    empresa) num periodo -- mesma logica de app/telas/4_Visao_Grupo.py,
    via visao_grupo.py (motivo original da refatoracao dessa manha:
    reusar aqui sem duplicar). periodo_texto vazio -> periodo mais
    recente entre as empresas escolhidas (uniao, igual a tela).

    granularidade (Fase 3.1, 29/09/2026): opcional, mesma regra de
    consultar_bp_dre -- so' precisa vir quando o periodo escolhido tiver
    mais de 1 documento ativo pra pelo menos 1 empresa do grupo.
    """
    periodos_disponiveis = db.listar_periodos_grupo_detalhado(conn, empresas_codigos, status="ATIVO")
    periodo, granularidade_resolvida, ambiguidade = _resolver_periodo_detalhado(
        periodos_disponiveis, periodo_texto, granularidade,
    )
    if ambiguidade:
        return {
            "erro": (
                "Esse período tem mais de 1 documento ativo no grupo selecionado (abrangências "
                "diferentes fechando na mesma data) -- pergunte ao usuário qual granularidade "
                "(mensal/bimestral/trimestral/semestral/anual) e chame de novo com esse valor no parâmetro 'granularidade'."
            ),
            **ambiguidade,
        }
    if periodo is None:
        return {
            "erro": "Nenhum periodo ativo encontrado pro grupo selecionado"
            + (f" em {periodo_texto}" if periodo_texto else ""),
            "periodos_disponiveis": [d["periodo"].strftime("%Y-%m") for d in sorted(periodos_disponiveis, key=lambda d: d["periodo"], reverse=True)],
        }
    lancamentos = db.listar_lancamentos_grupo(
        conn, periodo, tipo, empresas_codigos, status="ATIVO", granularidade=granularidade_resolvida,
    )
    pivot = visao_grupo.montar_pivot_grupo(lancamentos, empresas_codigos)

    if visao == "especifica":
        nome_por_cod = {cod: cod for cod in empresas_codigos}  # chat usa o codigo mesmo (sem UI pra nome longo)
        saida = visao_grupo.visao_especifica(pivot, empresas_codigos, nome_por_cod)
    else:
        visao = "macro"
        saida = visao_grupo.visao_macro(pivot, empresas_codigos)

    linhas = saida.to_dict(orient="records")
    # sanitiza pra JSON-serializavel puro (numpy.float64 nao serializa
    # direto em alguns encoders -- mesmo cuidado que db.py ja tem com Decimal)
    for l in linhas:
        for k, v in l.items():
            if hasattr(v, "item"):  # numpy scalar
                l[k] = v.item()

    return {
        "tipo": tipo,
        "periodo": periodo.strftime("%Y-%m"),
        "granularidade": granularidade_resolvida or None,
        "visao": visao,
        "empresas_incluidas": empresas_codigos,
        "quantidade_contas": len(linhas),
        "contas": linhas,
    }


# ─────────────────────────────────────────────
#  01/10/2026 (Erik.AI "consulta tudo") -- historico, notas por numero,
#  correcoes manuais e relatorios gerados. Somente LEITURA.
# ─────────────────────────────────────────────

def _json_seguro(v):
    """Decimal/date/datetime/UUID -> tipos JSON puros (o tool_result vira texto)."""
    import decimal
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.strftime("%Y-%m-%d %H:%M") if isinstance(v, datetime.datetime) else v.strftime("%Y-%m-%d")
    if isinstance(v, (list, tuple)):
        return [_json_seguro(x) for x in v]
    if hasattr(v, "item"):  # numpy
        return v.item()
    return str(v)


def _linhas_seguras(linhas: list[dict]) -> list[dict]:
    return [{k: _json_seguro(v) for k, v in l.items()} for l in linhas]


def consultar_historico_importacoes(conn, empresa_codigo: Optional[str] = None, limite: int = 20) -> dict:
    """Importacoes de PDF (BP/DRE) ja feitas: quando, quem, qual empresa/periodo,
    resultado. Pergunta tipica: "quando foi importado o BP da SMG?"."""
    limite = max(1, min(int(limite or 20), 50))
    linhas = db.listar_importacoes_recentes(conn, limite=200)
    if empresa_codigo:
        linhas = [l for l in linhas if l.get("empresa_codigo") == empresa_codigo]
    linhas = linhas[:limite]
    if not linhas:
        return {"importacoes": [], "quantidade": 0}
    saida = [{
        "empresa_codigo": l["empresa_codigo"], "periodo": _json_seguro(l["periodo"]), "tipo": l.get("tipo"),
        "nivel": l.get("nivel"), "usuario": l.get("usuario"), "criado_em": _json_seguro(l.get("criado_em")),
        "mensagem": (l.get("mensagem") or "")[:200], "arquivos": _json_seguro(l.get("arquivos") or []),
    } for l in linhas]
    return {"importacoes": saida, "quantidade": len(saida)}


def consultar_nota_fiscal(conn, numero: Optional[str] = None, fornecedor: Optional[str] = None,
                          cnpj: Optional[str] = None, empresa_codigo: Optional[str] = None) -> dict:
    """Situacao de uma nota especifica (por numero, fornecedor ou CNPJ) na
    conferencia Manifesto x Sienge, incluindo o status da pendencia."""
    if not (numero or fornecedor or cnpj):
        return {"erro": "Informe ao menos o numero da nota, o fornecedor ou o CNPJ."}
    linhas = nf_sienge.buscar_notas(conn, numero, fornecedor, cnpj, empresa_codigo, limite=20)
    if not linhas:
        return {"notas": [], "quantidade": 0, "aviso": "Nenhuma nota encontrada no manifesto com esses dados."}
    return {"notas": _linhas_seguras(linhas), "quantidade": len(linhas)}


def consultar_correcoes_manuais(conn, empresas_codigos: list[str], limite: int = 30) -> dict:
    """Contas BP/DRE corrigidas a mao (trilha de auditoria): valor atual x valor
    original do PDF, quem e quando. Pergunta tipica: "o que foi editado na Energia?"."""
    limite = max(1, min(int(limite or 30), 100))
    linhas = db.buscar_lancamentos_manuais(conn, empresas_codigos, limite=limite)
    if not linhas:
        return {"correcoes": [], "quantidade": 0}
    saida = [{
        "empresa_codigo": l["empresa_codigo"], "tipo": l["tipo"], "periodo": _json_seguro(l["periodo"]),
        "conta": l["conta"], "valor_atual": _json_seguro(l["valor"]), "valor_original_pdf": _json_seguro(l.get("pdf_original")),
        "usuario": l.get("usuario"), "atualizado_em": _json_seguro(l.get("atualizado_em")),
    } for l in linhas]
    return {"correcoes": saida, "quantidade": len(saida)}


def consultar_relatorios_gerados(conn, limite: int = 15) -> dict:
    """Ultimos relatorios PDF gerados (quando, quem, empresas e periodos)."""
    limite = max(1, min(int(limite or 15), 50))
    linhas = db.listar_relatorios_gerados(conn, limite=limite)
    if not linhas:
        return {"relatorios": [], "quantidade": 0}
    return {"relatorios": _linhas_seguras(linhas), "quantidade": len(linhas)}
