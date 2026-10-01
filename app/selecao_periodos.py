# -*- coding: utf-8 -*-
"""
v0.40.0 -- logica PURA (sem Streamlit/banco) dos seletores de periodo do
Relatorio Comentado.

Identidade de um item selecionavel = (periodo_fim, granularidade). Nada
aqui sabe de widget: a tela chama estas funcoes e so' desenha. Isolado
para ser testado sem AppTest e para garantir, em um lugar so', as
invariantes que os PDFs de 01/10/2026 violaram:

  - rotulo de lista sempre explicito ("30/06/2026 — Trimestral"), nunca
    so' a data (duas opcoes com a mesma data eram indistinguiveis);
  - rotulo de coluna do PDF distingue granularidade ("2T/2026" x
    "1S/2026" em vez de "06/2026" duas vezes);
  - com varias empresas, so' entram pares presentes em TODAS;
  - a selecao guardada e' sempre reconciliada com as opcoes atuais
    (nada de valor "fantasma" de uma selecao anterior), sem repeticao e
    em ordem cronologica, e periodos / rotulos / granularidades saem
    SEMPRE com o mesmo tamanho e a mesma ordem.
"""
from __future__ import annotations

import hashlib
import unicodedata
from datetime import date
from typing import Iterable, Optional

NOME_GRANULARIDADE = {
    "mensal": "Mensal", "bimestral": "Bimestral", "trimestral": "Trimestral",
    "semestral": "Semestral", "anual": "Anual", "outra": "Outro intervalo",
    "": "Base não declarada",
}

# meses de cada granularidade (para checar adjacencia do "periodo anterior")
MESES_GRANULARIDADE = {"mensal": 1, "bimestral": 2, "trimestral": 3, "semestral": 6, "anual": 12}

Par = tuple  # (periodo_fim: date, granularidade: str)


def rotulo_granularidade(g: Optional[str]) -> str:
    g = g or ""
    return NOME_GRANULARIDADE.get(g, g)


def rotulo_item(periodo: date, granularidade: Optional[str]) -> str:
    """"30/06/2026 — Trimestral". Sempre com a granularidade (a tela nao
    decide mais 'so' mostra se ambiguo' -- ambiguidade e' justamente o
    que gerou os PDFs misturados)."""
    return f"{periodo.strftime('%d/%m/%Y')} — {rotulo_granularidade(granularidade)}"


def rotulo_coluna_padrao(periodo: date, granularidade: Optional[str]) -> str:
    """Rotulo DEFAULT da coluna do PDF (editavel na tela):
    trimestral "2T/2026" | semestral "1S/2026" | anual "2026" |
    mensal "06/2026" | bimestral "05-06/2026" | demais "06/2026".
    Distingue granularidade mesmo quando periodo_fim se repete."""
    g = granularidade or ""
    m, a = periodo.month, periodo.year
    if g == "trimestral":
        return f"{(m - 1) // 3 + 1}T/{a}"
    if g == "semestral":
        return f"{1 if m <= 6 else 2}S/{a}"
    if g == "anual":
        return f"{a}"
    if g == "bimestral":
        ini_m = m - 1 if m > 1 else 12
        ini_a = a if m > 1 else a - 1
        if ini_a == a:
            return f"{ini_m:02d}-{m:02d}/{a}"
        return f"{ini_m:02d}/{ini_a}-{m:02d}/{a}"
    return f"{m:02d}/{a}"


def intersecao_pares(pares_por_empresa: Iterable[Iterable[Par]]) -> list:
    """Pares (periodo, granularidade) presentes em TODAS as empresas,
    ordem cronologica crescente (desempate pela granularidade). Sem
    empresas -> []. Cada item de `pares_por_empresa` e' o conjunto de
    pares COMPLETOS (BP+DRE ATIVOS) de uma empresa."""
    conjuntos = [set(p) for p in pares_por_empresa]
    if not conjuntos:
        return []
    comuns = set.intersection(*conjuntos)
    return sorted(comuns, key=lambda pg: (pg[0], pg[1] or ""))


def assinatura(codigos: Iterable[str], opcoes: Iterable[Par]) -> str:
    """Hash curto do CONJUNTO de empresas + opcoes disponiveis. Entra na
    key dos widgets de periodo/rotulo: mudou a selecao de empresas (ou o
    banco ganhou/perdeu um periodo), as keys mudam e o Streamlit descarta
    o estado antigo -- nao sobra selecao/rotulo de um conjunto anterior."""
    bruto = "|".join(sorted(codigos)) + "#" + ";".join(
        f"{p.isoformat()}={g or ''}" for p, g in opcoes
    )
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()[:10]


def reconciliar_selecao(selecionados: Optional[Iterable[Par]], opcoes: Iterable[Par]) -> list:
    """Mantem so' os itens de `selecionados` que existem em `opcoes`, sem
    repeticao, em ordem cronologica. Tolera itens vindos como lista
    (serializacao de session_state)."""
    validos = {tuple(o) for o in opcoes}
    vistos: set = set()
    saida = []
    for item in selecionados or []:
        par = tuple(item)
        if par in validos and par not in vistos:
            vistos.add(par)
            saida.append(par)
    return sorted(saida, key=lambda pg: (pg[0], pg[1] or ""))


def montar_selecao(
    selecionados: Optional[Iterable[Par]], opcoes: Iterable[Par], rotulos_digitados: Optional[dict] = None,
) -> dict:
    """Fonte unica da selecao do Comparativo. Devolve listas SEMPRE do
    mesmo tamanho e na mesma ordem (cronologica):

      pares, periodos, granularidades, rotulos (digitado -> senao padrao).

    `rotulos_digitados`: {par: texto} (so' o que a contadora editou)."""
    pares = reconciliar_selecao(selecionados, opcoes)
    digitados = rotulos_digitados or {}
    rotulos = []
    for p, g in pares:
        txt = (digitados.get((p, g)) or "").strip()
        rotulos.append(txt or rotulo_coluna_padrao(p, g))
    return {
        "pares": pares,
        "periodos": [p for p, _g in pares],
        "granularidades": [g for _p, g in pares],
        "rotulos": rotulos,
    }


def rotulos_duplicados(rotulos: Iterable[str]) -> list:
    """Rotulos de coluna repetidos (case-insensitive, sem espacos nas
    pontas) -- duas colunas com o mesmo titulo foi o sintoma do PDF
    Evolucao_ENERGIA ("06/2026, 06/2026")."""
    vistos: dict = {}
    for r in rotulos:
        chave = (r or "").strip().casefold()
        vistos[chave] = vistos.get(chave, 0) + 1
    return [k for k, n in vistos.items() if n > 1]


def _sem_acento(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")


def sufixo_granularidades(granularidades: Iterable[str]) -> str:
    """Parte do NOME DO ARQUIVO que reflete as granularidades realmente
    usadas: todas iguais -> o nome dela ("trimestral"); misturadas ->
    "mista"; nenhuma declarada -> ""."""
    unicas = {(g or "") for g in granularidades}
    if len(unicas) == 1:
        (g,) = unicas
        return _sem_acento(g) if g else ""
    return "mista"


def nome_arquivo_evolucao(
    sufixo_empresas: str, periodos: list, granularidades: list,
) -> str:
    """Evolucao_ENERGIA_202312_202606_mista.pdf -- reflete as bases
    realmente usadas (e nunca repete o mesmo nome para bases diferentes
    do mesmo intervalo de datas)."""
    base = f"Evolucao_{sufixo_empresas}_{periodos[0].strftime('%Y%m')}_{periodos[-1].strftime('%Y%m')}"
    suf = sufixo_granularidades(granularidades)
    return f"{base}_{suf}.pdf" if suf else f"{base}.pdf"
