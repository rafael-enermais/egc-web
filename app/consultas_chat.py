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


def _resolver_periodo(periodos_disponiveis: list, periodo_texto: Optional[str]):
    """
    periodo_texto no formato "AAAA-MM" (como o modelo deve mandar, ver
    SYSTEM_PROMPT) -> date real dentre os disponiveis (casa so' por
    ano/mes, nao assume dia -- period sempre e' o ultimo dia do mes, mas
    resolver por comparacao evita depender dessa premissa aqui de novo).
    Sem periodo_texto (None) -> mais recente. Retorna None se nao achar
    (deixa quem chama decidir a mensagem de erro).
    """
    if not periodos_disponiveis:
        return None
    if not periodo_texto:
        return max(periodos_disponiveis)
    try:
        ano_s, mes_s = periodo_texto.split("-")
        ano, mes = int(ano_s), int(mes_s)
    except (ValueError, AttributeError):
        return None
    for p in periodos_disponiveis:
        if p.year == ano and p.month == mes:
            return p
    return None


def consultar_periodos(conn, empresas_codigos: list[str]) -> dict:
    """
    Periodos ATIVOS disponiveis por empresa (uma a uma) -- usado pelo
    modelo pra saber quais periodos existem de verdade antes de chamar
    consultar_bp_dre/consultar_visao_grupo com um periodo especifico,
    evitando alucinar uma data que nao tem lancamento.
    """
    por_empresa = {}
    for cod in empresas_codigos:
        periodos = db.listar_periodos(conn, cod, status="ATIVO")
        por_empresa[cod] = [p.strftime("%Y-%m") for p in sorted(periodos, reverse=True)]
    return {"periodos_por_empresa": por_empresa}


def consultar_bp_dre(conn, empresa_codigo: str, tipo: str, periodo_texto: Optional[str] = None) -> dict:
    """
    BP ou DRE de 1 empresa num periodo (o mais recente se periodo_texto
    vier vazio). Retorna as contas (grupo, conta, valor) -- mesma fonte
    que a tela Revisao/Correcao usa (db.listar_lancamentos), so' que so'
    leitura (o chat nao corrige lancamento).
    """
    periodos_disponiveis = db.listar_periodos(conn, empresa_codigo, status="ATIVO")
    periodo = _resolver_periodo(periodos_disponiveis, periodo_texto)
    if periodo is None:
        return {
            "erro": f"Nenhum periodo ativo encontrado pra {empresa_codigo}"
            + (f" em {periodo_texto}" if periodo_texto else ""),
            "periodos_disponiveis": [p.strftime("%Y-%m") for p in sorted(periodos_disponiveis, reverse=True)],
        }
    linhas = db.listar_lancamentos(conn, empresa_codigo, periodo, tipo, status="ATIVO")
    contas = [
        {"grupo": l["grupo"], "conta": l["conta"], "valor": float(l["valor"])}
        for l in linhas
    ]
    return {
        "empresa_codigo": empresa_codigo,
        "tipo": tipo,
        "periodo": periodo.strftime("%Y-%m"),
        "quantidade_contas": len(contas),
        "contas": contas,
    }


def consultar_indicadores(conn, empresas_codigos: list[str]) -> dict:
    """
    Indicadores contabeis (Liquidez Corrente, Capital de Giro, Endividamento
    Geral, Margem Bruta/Liquida, ROA, ROE) no periodo mais recente
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
    """
    if len(empresas_codigos) == 1:
        cod = empresas_codigos[0]
        hist_bp = db.listar_historico_grupo(conn, cod, "BP", status="ATIVO")
        hist_dre = db.listar_historico_grupo(conn, cod, "DRE", status="ATIVO")
    else:
        periodos = db.listar_periodos_grupo(conn, empresas_codigos, status="ATIVO")
        hist_bp = db.listar_lancamentos_grupo_periodos(conn, periodos, "BP", empresas_codigos, status="ATIVO")
        hist_dre = db.listar_lancamentos_grupo_periodos(conn, periodos, "DRE", empresas_codigos, status="ATIVO")

    tabela = indicadores.calcular_indicadores(hist_bp, hist_dre)
    if tabela.empty:
        return {
            "erro": "Sem BP/DRE suficiente pra calcular indicadores pra essa(s) empresa(s) ainda.",
            "empresas_incluidas": empresas_codigos,
        }

    periodo_atual = tabela.index.max()
    linha = tabela.loc[periodo_atual]
    valores = {col: (None if pd.isna(v) else float(v)) for col, v in linha.items()}

    return {
        "empresas_incluidas": empresas_codigos,
        "periodo": periodo_atual.strftime("%Y-%m"),
        "indicadores": valores,
    }


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
    periodos = db.listar_periodos_grupo(conn, empresas_codigos, status="ATIVO")
    lancs_bp = db.listar_lancamentos_grupo_periodos(conn, periodos, "BP", empresas_codigos, status="ATIVO")
    lancs_dre = db.listar_lancamentos_grupo_periodos(conn, periodos, "DRE", empresas_codigos, status="ATIVO")
    completude = visao_grupo.calcular_completude_grupo(periodos, lancs_bp, lancs_dre, empresas_tuplas)
    resumo = visao_grupo.resumir_completude_por_periodo(completude)

    linhas = resumo.to_dict(orient="records")
    for linha in linhas:
        p = linha.get("Período")
        if hasattr(p, "strftime"):
            linha["Período"] = p.strftime("%Y-%m")

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
    """
    df = nf_sienge.listar_pendencias_abertas(conn, empresa_codigo, limite)
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
) -> dict:
    """
    Visao consolidada do grupo (Energia x Consolidadoras, ou empresa a
    empresa) num periodo -- mesma logica de app/telas/4_Visao_Grupo.py,
    via visao_grupo.py (motivo original da refatoracao dessa manha:
    reusar aqui sem duplicar). periodo_texto vazio -> periodo mais
    recente entre as empresas escolhidas (uniao, igual a tela).
    """
    periodos_disponiveis = db.listar_periodos_grupo(conn, empresas_codigos, status="ATIVO")
    periodo = _resolver_periodo(periodos_disponiveis, periodo_texto)
    if periodo is None:
        return {
            "erro": "Nenhum periodo ativo encontrado pro grupo selecionado"
            + (f" em {periodo_texto}" if periodo_texto else ""),
            "periodos_disponiveis": [p.strftime("%Y-%m") for p in sorted(periodos_disponiveis, reverse=True)],
        }
    lancamentos = db.listar_lancamentos_grupo(conn, periodo, tipo, empresas_codigos, status="ATIVO")
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
        "visao": visao,
        "empresas_incluidas": empresas_codigos,
        "quantidade_contas": len(linhas),
        "contas": linhas,
    }
