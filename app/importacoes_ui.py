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
        # *_resto absorve periodo_inicio/granularidade (24/09/2026) sem
        # quebrar se algum meta antigo (teste, cache) ainda tiver so' 6
        # campos.
        empresa, cnpj, periodo, _nome_arq, tipo, _fmt, *_resto = r["meta"][0]
        try:
            periodo_data = _dt.datetime.strptime(periodo, "%d/%m/%Y").date()
        except Exception:
            periodo_data = _dt.date.max
        tipo_ordem = {"BP": 0, "DRE": 1}.get(tipo, 2)
        return (cnpj or empresa or "", periodo_data, tipo_ordem, r["arquivo"])
    return ("~", _dt.date.max, 9, r["arquivo"])  # "~" fica depois de qualquer CNPJ/nome real


# Janela entre o instante em que os lancamentos foram gravados e o registro do
# evento em egc.importacoes (gravado logo depois, no mesmo clique de "Gravar").
JANELA_EVENTO_APOS_LANCAMENTO_SEG = 120
JANELA_EVENTO_ANTES_LANCAMENTO_SEG = 30


def evento_esta_ativo(criado_evento, criacoes_ativas) -> bool:
    """True quando o import do evento e' a geracao ATIVA do periodo: existe
    lancamento ativo criado no instante desse import (ver
    db.geracoes_ativas_por_periodo). `criacoes_ativas` vazio = periodo sem nada
    ativo (desfeito/arquivado) -> False."""
    if not criado_evento or not criacoes_ativas:
        return False
    for t in criacoes_ativas:
        if t is None:
            continue
        delta = (criado_evento - t).total_seconds()
        if -JANELA_EVENTO_ANTES_LANCAMENTO_SEG <= delta <= JANELA_EVENTO_APOS_LANCAMENTO_SEG:
            return True
    return False


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
    ...], "arquivos": [nome, ...]} -- combinando BP/DRE do mesmo clique de
    "Gravar" (linhas dentro de `janela_segundos` uma da outra), sem juntar
    com uma reimportacao antiga do mesmo periodo que caia fora da janela.

    FIX_20260929: "arquivos" agrega (sem repetir) o nome de todo PDF que
    alimentou o evento -- pedido do Rafael depois de achar o PDF errado
    (Consolidado) misturado num lote de reimport: "esse nome tb deve
    entrar no log do fluxo, ate' no historico de upload". O dado ja'
    existia em egc.importacoes.arquivos (text[], gravado por
    db.registrar_importacao desde sempre) -- so' nao aparecia nesta tela.
    row["arquivos"] pode vir None (linha antiga de antes deste fix, ou
    teste com dict sintetico sem essa chave) -- tratado como lista vazia.
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
                "arquivos": [],
            }
            eventos_por_chave[chave] = evento
            ordem_chaves.append(chave)
        else:
            continue  # ja temos os `limite` eventos mais recentes, nao abre mais nenhum

        tipo = row.get("tipo")
        if tipo and all(t != tipo for t, _msg in evento["tipos"]):
            evento["tipos"].append((tipo, row.get("mensagem") or ""))

        for nome_arq in (row.get("arquivos") or []):
            if nome_arq not in evento["arquivos"]:
                evento["arquivos"].append(nome_arq)

    return [eventos_por_chave[c] for c in ordem_chaves]


def inferir_granularidade_nome(nome_arquivo: str) -> str:
    """Granularidade pelo NOME do arquivo ("Energia - Balanço Patrimonial - 1º
    Semestre 2026 (1).pdf" -> semestral). O BP nao declara intervalo no PDF, mas
    quase sempre o nome diz de que fechamento ele e'. Devolve '' quando o nome
    nao diz (ou diz coisas conflitantes) -- nunca adivinha."""
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", str(nome_arquivo or "")).encode("ascii", "ignore").decode().lower()
    achados = set()
    if re.search(r"\bsemestr", t):
        achados.add("semestral")
    if re.search(r"\btrimestr", t):
        achados.add("trimestral")
    if re.search(r"\bbimestr", t):
        achados.add("bimestral")
    if re.search(r"\bmensal\b|\bmes de\b", t):
        achados.add("mensal")
    if re.search(r"\banual\b|\bexercicio\b", t):
        achados.add("anual")
    return next(iter(achados)) if len(achados) == 1 else ""


def assinatura_item(r: dict) -> str:
    """Assinatura estavel de 1 PDF da previa (arquivo + periodo + tipo +
    granularidade DETECTADA + tamanho). Usada como sufixo das keys dos
    widgets do Streamlit no lugar do indice posicional.

    Bug real (Rafael, 01/10/2026): com key posicional (`..._{i}`), o
    widget do 1o arquivo do lote seguinte herdava o valor escolhido no lote
    anterior -- gravou o TRIMESTRAL, subiu o SEMESTRAL e o seletor
    continuou em "Trimestral"; o app entao achava que o semestre
    substituiria o trimestre e travava a gravacao. Com a assinatura, cada
    arquivo novo nasce com o proprio default (o detectado no PDF)."""
    import hashlib
    meta = (r.get("meta") or [None])[0]
    if meta:
        _e, cnpj, periodo, _n, tipo, _f, p_ini, gran = meta
        base = f"{r.get('arquivo')}|{cnpj}|{periodo}|{tipo}|{p_ini}|{gran}"
    else:
        base = f"{r.get('arquivo')}|sem-meta"
    base += f"|{len(r.get('bp_rows') or [])}|{len(r.get('dre_rows') or [])}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]


def chave_documento_item(r: dict):
    """(tipo, periodo_str, granularidade_confirmada) de cada tipo que o
    item `r` (1 PDF da previa) vai gravar -- usado pra detectar conflito.
    Devolve lista de tuplas (tipo, periodo_str, granularidade, n_contas)."""
    if not r.get("meta"):
        return []
    _e, _c, periodo_str, _n, *_resto = r["meta"][0]
    granularidade = r.get("_granularidade_confirmada", "") or ""
    saida = []
    if r.get("bp_rows"):
        saida.append(("BP", periodo_str, granularidade, len(r["bp_rows"])))
    if r.get("dre_rows"):
        saida.append(("DRE", periodo_str, granularidade, len(r["dre_rows"])))
    return saida


def detectar_conflitos_lote(itens: list[dict]) -> list[dict]:
    """
    Mesmo documento (tipo + periodo + granularidade) em 2+ PDFs do MESMO
    grupo de gravacao (= mesma empresa). Antes desta checagem (pergunta do
    Rafael, 01/10/2026: "o que acontece se eu upar 2 BP do mesmo periodo
    ao mesmo tempo?") o ultimo da lista substituia o primeiro SEM AVISO
    -- a ordem da lista e' a de `chave_ordenacao_previa` (desempate pelo
    nome do arquivo), entao quem "ganhava" era decidido por ordem
    alfabetica, nao pela contadora. O primeiro ficava INATIVO.

    Devolve [{"tipo","periodo","granularidade","arquivos":[(nome, n_contas)]}]
    so' com os conflitos reais (2+ arquivos). `itens` ja' deve conter so'
    os incluidos na gravacao (checkbox marcado).
    """
    por_chave: dict = {}
    for r in itens:
        for tipo, periodo_str, gran, n in chave_documento_item(r):
            por_chave.setdefault((tipo, periodo_str, gran), []).append((r["arquivo"], n))
    return [
        {"tipo": t, "periodo": p, "granularidade": g, "arquivos": arqs}
        for (t, p, g), arqs in por_chave.items() if len(arqs) > 1
    ]
