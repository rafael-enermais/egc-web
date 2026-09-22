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

import db
import visao_grupo


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
