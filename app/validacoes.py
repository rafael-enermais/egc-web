# -*- coding: utf-8 -*-
"""
Checagens de qualidade do lado do app web -- NAO mexe no parser (regra
absoluta do projeto: nao alterar parser_egc.py/EXE/estrutura TSV). So
LE o resultado que o parser ja devolve e aplica validacoes extras antes
de gravar ou ao revisar.
"""
from __future__ import annotations

from parser_egc import br_to_float


def checar_fechamento_bp(bp_rows: list) -> tuple[float | None, float | None]:
    """
    Confere TOTAL DO ATIVO vs TOTAL DO PASSIVO num BP recem-extraido
    (bp_rows no formato do parser: [grupo, conta, valor_br, origem]).
    Retorna (total_ativo, total_passivo) como float, ou None quando a
    conta nao foi encontrada nas linhas (ex.: BP truncado).
    Nao decide nada sozinho -- quem chama decide se bloqueia ou so avisa
    (BP 05/2026 da Energia ja mostrou que a divergencia pode vir do
    proprio PDF fonte, nao e sempre bug de leitura).
    """
    total_ativo = None
    total_passivo = None
    for grupo, conta, valor_br, _origem in bp_rows:
        if conta == "TOTAL DO ATIVO":
            total_ativo = br_to_float(valor_br)
        elif conta == "TOTAL DO PASSIVO":
            total_passivo = br_to_float(valor_br)
    return total_ativo, total_passivo


def montar_detalhe_correcoes(alteracoes: list[tuple[str, float, float]]) -> str:
    """
    Monta o texto de 'detalhe' pro log de eventos (egc.eventos_sistema) de
    um lote de correções manuais de 1 combinação empresa+período+tipo --
    "CONTA: R$ x,xx → R$ y,yy" separado por "; ". Usado por
    Revisão/Correção (23/09/2026, achado real do Rafael testando ao vivo:
    editou uma conta pra testar e depois não lembrava qual foi -- o log
    até então só contava quantas contas mudaram, nunca dizia QUAL).

    alteracoes: lista de (conta, valor_antigo, valor_novo). Import de
    formatacao.moeda_br é local (dentro da função) pra evitar import
    circular no topo do módulo -- formatacao.py já importa
    validacoes.formatar_br no topo dele.
    """
    from formatacao import moeda_br
    return "; ".join(f"{conta}: {moeda_br(antigo)} → {moeda_br(novo)}" for conta, antigo, novo in alteracoes)


def formatar_br(v: float) -> str:
    """1234.5 -> "1.234,50" (mesmo estilo BR usado no resto do app)."""
    return f"{v:,.2f}".translate(str.maketrans({",": "X", ".": ","})).replace("X", ".")

