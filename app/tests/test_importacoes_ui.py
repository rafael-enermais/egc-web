# -*- coding: utf-8 -*-
"""
Testes de app/importacoes_ui.py -- modulo puro, sem banco nem Streamlit,
com dado SINTETICO. Cobre os 2 fixes do feedback do Rafael testando ao
vivo em 22/09/2026 (item 2: reordenar previa por CNPJ+periodo; item 5:
combinar BP+DRE no historico de "Importações recentes").

Rodar: python3 tests/test_importacoes_ui.py
"""
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import importacoes_ui  # noqa: E402


def _resultado(arquivo, cnpj, periodo_str, tipo, empresa="Empresa X"):
    return {
        "arquivo": arquivo,
        "bp_rows": [],
        "dre_rows": [],
        "log": [],
        "meta": [(empresa, cnpj, periodo_str, arquivo, tipo, "SIMPLES")],
    }


# ───────────────────────── chave_ordenacao_previa (item 2) ──────────────

def test_ordenacao_agrupa_mesmo_cnpj_mesmo_periodo_bp_antes_dre():
    # reproduz o caso real do Rafael: upload em lote da SMG saiu com DRE
    # no topo e BP no final -- a ordenacao deve colocar BP antes de DRE
    # dentro do mesmo CNPJ+periodo.
    dre_smg = _resultado("SMG_DRE.pdf", "18.387.666/0001-00", "30/06/2026", "DRE")
    bp_smg = _resultado("SMG_BP.pdf", "18.387.666/0001-00", "30/06/2026", "BP")
    resultados = [dre_smg, bp_smg]  # ordem crua de upload: DRE antes de BP
    ordenados = sorted(resultados, key=importacoes_ui.chave_ordenacao_previa)
    assert ordenados[0] is bp_smg, "BP deveria vir antes de DRE dentro do mesmo CNPJ+periodo"
    assert ordenados[1] is dre_smg
    print("OK: chave_ordenacao_previa — BP antes de DRE dentro do mesmo CNPJ+periodo")


def test_ordenacao_agrupa_por_cnpj_mesmo_com_lote_intercalado():
    # lote intercalado de 2 CNPJs diferentes -- depois de ordenar, os 2
    # arquivos de cada CNPJ devem ficar adjacentes (agrupados), nao mais
    # espalhados na ordem crua de upload.
    bp_a = _resultado("A_BP.pdf", "11.111.111/0001-11", "31/05/2026", "BP", empresa="Empresa A")
    bp_b = _resultado("B_BP.pdf", "22.222.222/0001-22", "31/05/2026", "BP", empresa="Empresa B")
    dre_a = _resultado("A_DRE.pdf", "11.111.111/0001-11", "31/05/2026", "DRE", empresa="Empresa A")
    dre_b = _resultado("B_DRE.pdf", "22.222.222/0001-22", "31/05/2026", "DRE", empresa="Empresa B")
    resultados = [bp_a, bp_b, dre_a, dre_b]  # intercalado, nao agrupado
    ordenados = sorted(resultados, key=importacoes_ui.chave_ordenacao_previa)
    cnpjs_na_ordem = [r["meta"][0][1] for r in ordenados]
    assert cnpjs_na_ordem == [
        "11.111.111/0001-11", "11.111.111/0001-11",
        "22.222.222/0001-22", "22.222.222/0001-22",
    ], f"esperava os 2 arquivos de cada CNPJ adjacentes, veio {cnpjs_na_ordem}"
    print("OK: chave_ordenacao_previa — agrupa por CNPJ mesmo com lote intercalado")


def test_ordenacao_arquivo_sem_meta_vai_pro_fim():
    com_meta = _resultado("OK.pdf", "11.111.111/0001-11", "31/05/2026", "BP")
    sem_meta = {"arquivo": "ERRO.pdf", "bp_rows": [], "dre_rows": [], "log": [], "meta": []}
    ordenados = sorted([sem_meta, com_meta], key=importacoes_ui.chave_ordenacao_previa)
    assert ordenados[-1] is sem_meta, "arquivo sem meta (erro de leitura) deveria ir pro fim"
    print("OK: chave_ordenacao_previa — arquivo sem meta vai pro fim, nao quebra a ordenacao")


# ─────────────────── agrupar_historico_importacoes (item 5) ─────────────

def _linha(empresa_codigo, periodo, criado_em, tipo, mensagem, usuario="rafael"):
    return {
        "empresa_codigo": empresa_codigo,
        "periodo": periodo,
        "criado_em": criado_em,
        "usuario": usuario,
        "tipo": tipo,
        "mensagem": mensagem,
    }


def test_combina_bp_e_dre_do_mesmo_clique_numa_linha_so():
    # reproduz o bug real: BP gravado primeiro, DRE alguns segundos depois
    # (mesmo clique de "Gravar <empresa>"), ORDENADOS DESC por criado_em
    # (DRE primeiro na lista, como o banco devolve de verdade).
    t0 = datetime.datetime(2026, 9, 22, 14, 30, 0)
    brutos = [
        _linha("SMG", datetime.date(2026, 6, 30), t0 + datetime.timedelta(seconds=2), "DRE", "80 conta(s) gravada(s)"),
        _linha("SMG", datetime.date(2026, 6, 30), t0, "BP", "150 conta(s) gravada(s)"),
    ]
    eventos = importacoes_ui.agrupar_historico_importacoes(brutos)
    assert len(eventos) == 1, "BP+DRE do mesmo clique deveriam virar 1 evento so'"
    tipos = dict(eventos[0]["tipos"])
    assert tipos == {"DRE": "80 conta(s) gravada(s)", "BP": "150 conta(s) gravada(s)"}, (
        f"esperava BP e DRE combinados com as mensagens originais, veio {tipos}"
    )
    print("OK: agrupar_historico_importacoes — BP+DRE do mesmo clique combinam numa linha so' (bug real corrigido)")


def test_nao_combina_reimportacao_antiga_fora_da_janela():
    # BP gravado as 10:00, DRE do MESMO periodo gravado 2 DIAS depois (fora
    # da janela de 30s) -- nao deveria aparecer como se tivessem sido
    # gravados juntos (evita sugerir um evento que nao aconteceu).
    t_novo = datetime.datetime(2026, 9, 22, 10, 0, 0)
    t_antigo = t_novo - datetime.timedelta(days=2)
    brutos = [
        _linha("ENERGIA", datetime.date(2026, 5, 31), t_novo, "DRE", "40 conta(s) gravada(s)"),
        _linha("ENERGIA", datetime.date(2026, 5, 31), t_antigo, "BP", "90 conta(s) gravada(s) (antigo)"),
    ]
    eventos = importacoes_ui.agrupar_historico_importacoes(brutos)
    assert len(eventos) == 1
    tipos = dict(eventos[0]["tipos"])
    assert tipos == {"DRE": "40 conta(s) gravada(s)"}, (
        f"BP antigo (fora da janela) nao deveria entrar no evento atual, veio {tipos}"
    )
    print("OK: agrupar_historico_importacoes — reimportacao antiga fora da janela nao se mistura com o evento atual")


def test_limite_de_eventos_respeitado_mesmo_com_muitas_linhas():
    # 15 empresas diferentes, 1 linha cada -- so' os primeiros `limite`
    # (mais recentes, ja que a entrada vem ordenada DESC) devem aparecer.
    t0 = datetime.datetime(2026, 9, 22, 12, 0, 0)
    brutos = [
        _linha(f"EMP{i}", datetime.date(2026, 6, 30), t0 - datetime.timedelta(minutes=i), "BP", f"{i} conta(s)")
        for i in range(15)
    ]
    eventos = importacoes_ui.agrupar_historico_importacoes(brutos, limite=10)
    assert len(eventos) == 10, f"esperava exatamente 10 eventos (limite), veio {len(eventos)}"
    assert eventos[0]["empresa_codigo"] == "EMP0", "deveria manter os mais recentes primeiro"
    print("OK: agrupar_historico_importacoes — respeita o limite de eventos mesmo com muitas linhas")


def test_evento_vazio_sem_linhas_brutas():
    assert importacoes_ui.agrupar_historico_importacoes([]) == []
    print("OK: agrupar_historico_importacoes — lista vazia devolve lista vazia")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in testes:
        try:
            t()
        except AssertionError as e:
            falhas += 1
            print(f"FALHOU: {t.__name__} — {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
