# -*- coding: utf-8 -*-
"""
Testes do consolidado multi-CNPJ + 1 UNICO periodo em montar_dados_relatorio
(Modelo A) -- 29/09/2026, pedido do Rafael: "so nao sei se esse layout
ficaria a melhor demonstracao do consolidado... multi-CNPJ pra 1 periodo
so, como um unico consolidado". Decisao tomada com ele: estender o Modelo
A (narrativo, 1 periodo), nao o B (que e' estruturalmente uma EVOLUCAO
entre periodos e nao faz sentido com 1 periodo so -- min 2 periodos
hardcoded na tela e no motor de desenho).

Mocka db.*/visao_grupo real (montar_pivot_grupo NAO e' mockado -- e' codigo
puro ja testado, reaproveitado de verdade aqui, mesmo padrao de
test_dados_relatorio_comparativo.py). Dados sinteticos pequenos e redondos
pra somar de cabeça.
"""
import sys
import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dados_relatorio_comentado as drc  # noqa: E402

PERIODO = datetime.date(2026, 6, 30)
PERIODO_ANTERIOR = datetime.date(2025, 12, 31)

EMPRESAS = [
    {"codigo": "ENERGIA", "nome": "Enermais Energia Ltda", "cnpj": "47.040.664/0001-48"},
    {"codigo": "SMG", "nome": "SMG Soluções", "cnpj": "18.387.666/0001-00"},
]


def _lanc(empresa, grupo, conta, valor):
    return {"empresa_codigo": empresa, "grupo": grupo, "conta": conta, "valor": valor}


# BP "achatado" formato db.listar_lancamentos_grupo -- ENERGIA 100/40 de
# ativo circulante, SMG 25/10 -- consolidado esperado: 125/50.
BP_GRUPO = [
    _lanc("ENERGIA", "ATIVO CIRCULANTE", "TOTAL CIRCULANTE ATIVO", 100.0),
    _lanc("ENERGIA", "ATIVO CIRCULANTE", "DISPONIVEL", 40.0),
    _lanc("ENERGIA", "TOTAL", "TOTAL DO ATIVO", 100.0),
    _lanc("ENERGIA", "PASSIVO CIRCULANTE", "TOTAL CIRCULANTE PASSIVO", 60.0),
    _lanc("ENERGIA", "TOTAL", "TOTAL DO PASSIVO", 100.0),
    _lanc("SMG", "ATIVO CIRCULANTE", "TOTAL CIRCULANTE ATIVO", 25.0),
    _lanc("SMG", "ATIVO CIRCULANTE", "DISPONIVEL", 10.0),
    _lanc("SMG", "TOTAL", "TOTAL DO ATIVO", 25.0),
    _lanc("SMG", "PASSIVO CIRCULANTE", "TOTAL CIRCULANTE PASSIVO", 15.0),
    _lanc("SMG", "TOTAL", "TOTAL DO PASSIVO", 25.0),
]

# DRE "achatado" -- receita ENERGIA 1000, SMG 200 -> consolidado 1200.
DRE_GRUPO = [
    _lanc("ENERGIA", "RESULTADO", "RECEITA OPERACIONAL LIQUIDA", 1000.0),
    _lanc("ENERGIA", "RESULTADO", "LUCRO LIQUIDO DO EXERCICIO", 100.0),
    _lanc("SMG", "RESULTADO", "RECEITA OPERACIONAL LIQUIDA", 200.0),
    _lanc("SMG", "RESULTADO", "LUCRO LIQUIDO DO EXERCICIO", 20.0),
]

ITENS_ADMIN_ENERGIA = [("Serviços Profissionais", -300.0), ("Aluguel", -50.0)]
ITENS_ADMIN_SMG = [("Serviços Profissionais", -40.0), ("Softwares", -10.0)]


def _pivot(lancs, cods):
    df = pd.DataFrame(lancs)
    linhas = []
    for (grupo, conta), sub in df.groupby(["grupo", "conta"]):
        linhas.append({"grupo": grupo, "conta": conta, "VALOR CONSOLIDADO": sub["valor"].sum()})
    return pd.DataFrame(linhas)


def _patches(codigos_com_periodo=("ENERGIA", "SMG")):
    def _listar_periodos(conn, cod, status="ATIVO"):
        return [PERIODO] if cod in codigos_com_periodo else []

    def _listar_periodos_detalhado(conn, cod, status="ATIVO"):
        return [{"periodo": PERIODO, "granularidade": ""}] if cod in codigos_com_periodo else []

    def _listar_lancamentos_grupo(conn, periodo, tipo, cods, status="ATIVO", granularidade=None):
        fonte = BP_GRUPO if tipo == "BP" else DRE_GRUPO
        return [x for x in fonte if x["empresa_codigo"] in cods]

    def _listar_lancamentos_grupo_periodos(conn, periodos, tipo, cods, status="ATIVO"):
        fonte = BP_GRUPO if tipo == "BP" else DRE_GRUPO
        return [dict(x, periodo=PERIODO, granularidade="") for x in fonte if x["empresa_codigo"] in cods]

    def _listar_lancamentos(conn, cod, periodo, tipo, status="ATIVO", granularidade=None):
        # Caminho de 1 empresa (string) -- mesmo formato de db.listar_lancamentos
        # (sem empresa_codigo na chave), filtrado das mesmas fontes sinteticas.
        fonte = BP_GRUPO if tipo == "BP" else DRE_GRUPO
        return [{"grupo": x["grupo"], "conta": x["conta"], "valor": x["valor"]}
                for x in fonte if x["empresa_codigo"] == cod]

    def _listar_historico_grupo(conn, cod, tipo, status="ATIVO"):
        fonte = BP_GRUPO if tipo == "BP" else DRE_GRUPO
        return [{"periodo": PERIODO, "grupo": x["grupo"], "conta": x["conta"], "valor": x["valor"]}
                for x in fonte if x["empresa_codigo"] == cod]

    def _listar_despesas_admin_itens(conn, cod, periodo, granularidade=None):
        return ITENS_ADMIN_ENERGIA if cod == "ENERGIA" else ITENS_ADMIN_SMG

    indic_vazio = pd.DataFrame()
    indic_vazio.index = pd.DatetimeIndex([], name="periodo")

    return [
        patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS),
        patch("dados_relatorio_comentado.db.listar_periodos", side_effect=_listar_periodos),
        patch("dados_relatorio_comentado.db.listar_periodos_detalhado", side_effect=_listar_periodos_detalhado),
        patch("dados_relatorio_comentado.db.listar_periodos_grupo", return_value=[PERIODO]),
        patch("dados_relatorio_comentado.db.listar_lancamentos_grupo", side_effect=_listar_lancamentos_grupo),
        patch("dados_relatorio_comentado.db.listar_lancamentos_grupo_periodos", side_effect=_listar_lancamentos_grupo_periodos),
        patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=_listar_lancamentos),
        patch("dados_relatorio_comentado.db.listar_historico_grupo", side_effect=_listar_historico_grupo),
        patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", side_effect=_listar_despesas_admin_itens),
        patch("dados_relatorio_comentado.visao_grupo.montar_pivot_grupo", side_effect=_pivot),
        # indicadores real fica sem historico o bastante nesse cenario
        # sintetico pequeno pra dar linha valida -- mocka vazio (mesmo
        # efeito de "sem BP+DRE o bastante") pra forcar os fallbacks
        # diretos ja existentes em montar_dados_relatorio (Margem Bruta/
        # Liquida/EBITDA); nao e' o alvo destes testes (esses 3 ja' tem
        # teste proprio em test_dados_relatorio_comentado.py).
        patch("dados_relatorio_comentado.indicadores.calcular_indicadores", return_value=indic_vazio),
    ]


def _montar(empresa_codigo, **kwargs):
    codigos_com_periodo = kwargs.pop("codigos_com_periodo", ("ENERGIA", "SMG"))
    patches = _patches(codigos_com_periodo)
    ctxs = [p.start() for p in patches]
    try:
        return drc.montar_dados_relatorio(
            conn=object(), empresa_codigo=empresa_codigo, periodo=PERIODO,
            periodo_label="1º Semestre 2026", **kwargs,
        )
    finally:
        for p in patches:
            p.stop()


def test_consolidado_soma_bp_dre_entre_empresas():
    dados, _ = _montar(["ENERGIA", "SMG"])
    assert dados["receita_liquida"] == 1200.0
    assert dados["resultado_liquido"] == 120.0
    assert dados["total_ativo"] == 125.0
    print("OK: BP/DRE consolidado e' a soma aditiva das 2 empresas (1200/120/125)")


def test_consolidado_marca_empresas_codigos_e_nome_grupo():
    dados, _ = _montar(["ENERGIA", "SMG"])
    assert dados["empresas_codigos"] == ["ENERGIA", "SMG"]
    assert dados["empresa_nome"] == "Grupo Enermais"
    assert dados["cnpj"] == ""
    print("OK: dict consolidado carrega empresas_codigos (liga logo/CNPJ de grupo no gerador)")


def test_1_empresa_em_lista_de_1_nao_vira_grupo():
    """[SMG] (lista de 1) tem que dar o MESMO resultado de "SMG" (string) --
    nao e' fluxo de grupo, so' passagem alternativa de parametro."""
    dados_lista, _ = _montar(["SMG"])
    dados_str, _ = _montar("SMG")
    assert "empresas_codigos" not in dados_lista
    assert "empresas_codigos" not in dados_str
    assert dados_lista == dados_str
    print("OK: lista de 1 empresa e string sao equivalentes, nenhuma marca como grupo")


def test_periodo_faltando_numa_empresa_do_grupo_levanta_erro_explicito():
    """REGRA DE OURO: nunca soma 0.0 silencioso de uma empresa que nao
    tem o periodo -- mesma checagem estrita ja aplicada no Modelo B."""
    try:
        _montar(["ENERGIA", "SMG"], codigos_com_periodo=("ENERGIA",))
    except ValueError as exc:
        assert "SMG" in str(exc)
        print("OK: periodo faltando numa empresa do grupo levanta ValueError explicito, nao soma 0.0")
    else:
        raise AssertionError("esperava ValueError por periodo faltando na SMG")


def test_despesas_admin_itens_consolidado_soma_por_nome_de_conta():
    dados, _ = _montar(["ENERGIA", "SMG"])
    nomes = {item[0]: item[1] for item in dados["despesas_admin_itens"]}
    # "Serviços Profissionais" existe nas 2 empresas (300+40=340, ja' em
    # magnitude positiva -- _montar_despesas_admin_itens inverte o sinal).
    assert round(nomes["Serviços Profissionais"], 2) == 340.0
    print("OK: itens de despesas administrativas somam por nome de conta entre empresas do grupo")


def test_consolidar_historico_preserva_granularidade_pra_nao_somar_dobrado():
    """
    Fase 3.1 (29/09/2026): _consolidar_historico alimenta
    indicadores.calcular_indicadores (indicador de tendencia do relatorio
    consolidado) -- antes descartava a coluna 'granularidade' no groupby,
    entao se 2 documentos (ex. trimestral e semestral) estivessem ATIVOS
    pro MESMO periodo_fim, o indicador de tendencia do relatorio somaria
    os 2 silenciosamente. Agora preserva a coluna e quem consome
    (indicadores.py) resolve a ambiguidade (documento mais abrangente vence).
    """
    lancs_ambiguo = [
        {"empresa_codigo": "ENERGIA", "periodo": PERIODO, "grupo": "RESULTADO",
         "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": 320.0, "granularidade": "trimestral"},
        {"empresa_codigo": "ENERGIA", "periodo": PERIODO, "grupo": "RESULTADO",
         "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": 600.0, "granularidade": "semestral"},
    ]
    with patch("dados_relatorio_comentado.db.listar_periodos_grupo", return_value=[PERIODO]), \
         patch("dados_relatorio_comentado.db.listar_lancamentos_grupo_periodos", return_value=lancs_ambiguo):
        registros = drc._consolidar_historico(object(), ["ENERGIA"], "DRE")
    granularidades = {r["granularidade"] for r in registros}
    assert granularidades == {"trimestral", "semestral"}, (
        f"esperava a coluna 'granularidade' preservada (as 2 linhas, sem somar) -- veio {registros}"
    )
    print("OK: _consolidar_historico preserva 'granularidade' -- indicadores.py resolve a ambiguidade a jusante, sem somar dobrado")


def test_caminho_1_empresa_string_continua_sem_regressao():
    """Regressao basica: o caminho de 1 empresa (string) nao muda de
    comportamento com a extensao pro grupo -- mesmos campos de sempre,
    sem empresas_codigos, sem consolidacao."""
    dados, _ = _montar("ENERGIA")
    assert dados["receita_liquida"] == 1000.0
    assert dados["empresa_nome"] == "Enermais Energia Ltda"
    assert "empresas_codigos" not in dados
    print("OK: 1 empresa (string) sem regressao -- fluxo original intocado")
