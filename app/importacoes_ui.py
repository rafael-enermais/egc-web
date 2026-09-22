# -*- coding: utf-8 -*-
"""
Logica pura (sem Streamlit/banco) usada pela tela Importar PDF
(app/telas/1_Importar_PDF.py) -- mesma filosofia de projecao.py/
visao_grupo.py: extraida pra modulo separado so' pra dar pra testar com
dado sintetico, sem precisar de AppTest/mock pesado de conexao+upload.

Cobre 2 fixes do feedback do Rafael testando ao vivo (22/09/2026):

  1. chave_ordenacao_previa -- item 2: reordenar a previa por CNPJ+periodo
     (BP antes de DRE) em vez da ordem crua de upload ("subi em lote, o
     SMG ficou uma DRE no topo e BP no final").

  2. agrupar_historico_importacoes -- item 5: "Importações recentes"
     grava BP e DRE como 2 linhas SEPARADAS em egc.importacoes (1 por
     tipo, no mesmo clique de "Gravar"). O dedup antigo por
     (empresa_codigo, periodo) so' guardava a linha mais recente -- como
     DRE e' gravado DEPOIS de BP no mesmo clique, a mensagem do BP
     desaparecia silenciosamente do historico. Agora combina linhas da
     MESMA empresa+periodo que aconteceram poucos segundos uma da outra
     (mesmo clique) numa unica linha com os 2 tipos -- uma reimportacao
     ANTIGA do mesmo periodo (fora dessa janela) fica de fora, pra nao
     sugerir que BP+DRE foram gravados juntos quando nao foram.
"""
from __future__ import annotations

import datetime as _dt

JANELA_MESMO_EVENTO_SEGUNDOS = 30


def chave_ordenacao_previa(r: dict):
    """
    Chave de ordenacao pra `sorted()` sobre a lista de resultados da
    previa (session_state["import_resultados"]). r e' 1 item dessa
    lista: {"arquivo": str, "bp_rows": [...], "dre_rows": [...],
    "log": [...], "meta": [(empresa, cnpj, periodo_str, nome_arq, tipo,
    fmt), ...] ou []}.

    CNPJ (nao o nome em texto livre extraido do PDF) e' a chave mais
    estavel pra agrupar o mesmo CNPJ junto -- 2 PDFs do mesmo CNPJ podem
    ter o nome da empresa grafado de formas levemente diferentes no
    texto extraido. Dentro do mesmo CNPJ+periodo, BP vem antes de DRE.
    Arquivo sem meta (erro de leitura) vai pro fim, sem quebrar a
    ordenacao dos que tem dado.
    """
    if r.get("meta"):
        empresa, cnpj, periodo, _nome_arq, tipo, _fmt = r["meta"][0]
        try:
            periodo_data = _dt.datetime.strptime(periodo, "%d/%m/%Y").date()
        except Exception:
            periodo_data = _dt.date.max
        tipo_ordem = {"BP": 0, "DRE": 1}.get(tipo, 2)
        return (cnpj or empresa or "", periodo_data, tipo_ordem, r["arquivo"])
    return ("~", _dt.date.max, 9, r["arquivo"])  # "~" fica depois de qualquer CNPJ/nome real


def agrupar_historico_importacoes(
    brutos: list[dict], limite: int = 10, janela_segundos: int = JANELA_MESMO_EVENTO_SEGUNDOS,
) -> list[dict]:
    """
    brutos: saida crua de db.listar_importacoes_recentes -- 1 linha por
    (empresa_codigo, periodo, tipo), ja ordenada por criado_em DESC (mais
    recente primeiro). Cada dict precisa ter: empresa_codigo, periodo,
    criado_em, usuario, tipo, mensagem.

    Retorna ate' `limite` eventos (1 por empresa+periodo, os mais
    recentes), cada um: {"empresa_codigo", "periodo", "criado_em"
    (do mais recente do grupo), "usuario", "tipos": [(tipo, mensagem),
    ...]} -- combinando BP/DRE do mesmo clique de "Gravar" (linhas dentro
    de `janela_segundos` uma da outra), sem juntar com uma reimportacao
    antiga do mesmo periodo que caia fora da janela.
    """
    eventos_por_chave: dict = {}
    ordem_chaves: list = []

    for row in brutos:
        chave = (row["empresa_codigo"], row["periodo"])
        if chave in eventos_por_chave:
            evento = eventos_por_chave[chave]
            criado_evento = evento["criado_em"]
            criado_row = row["criado_em"]
            if criado_evento and criado_row and abs((criado_evento - criado_row).total_seconds()) > janela_segundos:
                continue  # reimportacao antiga do mesmo periodo -- fora da janela do evento atual
        elif len(ordem_chaves) < limite:
            evento = {
                "empresa_codigo": row["empresa_codigo"],
                "periodo": row["periodo"],
                "criado_em": row["criado_em"],
                "usuario": row["usuario"],
                "tipos": [],
            }
            eventos_por_chave[chave] = evento
            ordem_chaves.append(chave)
        else:
            continue  # ja temos os `limite` eventos mais recentes, nao abre mais nenhum

        tipo = row.get("tipo")
        if tipo and all(t != tipo for t, _msg in evento["tipos"]):
            evento["tipos"].append((tipo, row.get("mensagem") or ""))

    return [eventos_por_chave[c] for c in ordem_chaves]
