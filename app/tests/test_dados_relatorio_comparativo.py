# -*- coding: utf-8 -*-
"""
Testes da camada de dados do Modelo B (comparativo multi-período),
29/09/2026 -- pedido do Rafael: "por mim podemos implantar o
multi-periodos ja tb". O motor de DESENHO (gerador_relatorio_comparativo.py)
já tinha teste próprio desde 26/09 (test_gerador_relatorio_comparativo.py);
isto testa só a montagem do dict a partir do banco (dados_relatorio_comentado.
montar_dados_relatorio_comparativo + _montar_anexo_multi_periodo).

Mocka no limite de montar_dados_relatorio (já testado sozinho em
test_dados_relatorio_comentado.py) em vez de remockar toda a cadeia
db/indicadores pra cada período -- a integração entre as 2 funções é
uma simples passagem de campo, não vale reconstruir todo o cenário de
BP/DRE/indicadores 2-4 vezes por teste.
"""
import sys
import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dados_relatorio_comentado as drc  # noqa: E402

EMPRESAS = [
    {"codigo": "ENERGIA", "nome": "Enermais Energia Ltda", "cnpj": "47.040.664/0001-48"},
    {"codigo": "SMG", "nome": "SMG Soluções", "cnpj": "18.387.666/0001-00"},
]

P2025 = datetime.date(2025, 12, 31)
P2026 = datetime.date(2026, 6, 30)

DADOS_ENERGIA = {
    P2025: dict(receita_liquida=31_200_000.0, ebitda=1_000_000.0, resultado_liquido=-640_000.0,
                total_ativo=41_700_000.0, patrimonio_liquido=8_000_000.0),
    P2026: dict(receita_liquida=33_353_150.0, ebitda=724_975.31, resultado_liquido=-1_337_674.90,
                total_ativo=43_359_080.94, patrimonio_liquido=6_771_914.25),
}
# valores da SMG deliberadamente pequenos e redondos -- fica fácil conferir
# de cabeça que o "consolidado" é ENERGIA + SMG, não só ENERGIA sozinha.
DADOS_SMG = {
    P2025: dict(receita_liquida=5_000_000.0, ebitda=200_000.0, resultado_liquido=50_000.0,
                total_ativo=3_000_000.0, patrimonio_liquido=1_000_000.0),
    P2026: dict(receita_liquida=6_000_000.0, ebitda=300_000.0, resultado_liquido=80_000.0,
                total_ativo=3_500_000.0, patrimonio_liquido=1_200_000.0),
}
DADOS_POR_EMPRESA = {"ENERGIA": DADOS_ENERGIA, "SMG": DADOS_SMG}

BP_2025 = [
    {"grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": 100.0},
    {"grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": 40.0},
    {"grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": 100.0},
]
BP_2026 = [
    {"grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": 150.0},
    {"grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": 60.0},
    {"grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": 90.0},  # conta nova, so' existe em 2026
    {"grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": 150.0},
]

# BP "achatado" (1 linha por empresa+grupo+conta), formato de
# db.listar_lancamentos_grupo -- usado só nos testes de grupo/consolidado.
BP_GRUPO_2025 = [
    {"empresa_codigo": "ENERGIA", "grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": 40.0},
    {"empresa_codigo": "ENERGIA", "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": 100.0},
    {"empresa_codigo": "SMG", "grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": 10.0},
    {"empresa_codigo": "SMG", "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": 25.0},
]
BP_GRUPO_2026 = [
    {"empresa_codigo": "ENERGIA", "grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": 60.0},
    {"empresa_codigo": "ENERGIA", "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": 150.0},
    {"empresa_codigo": "SMG", "grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": 15.0},
    {"empresa_codigo": "SMG", "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": 30.0},
]


def _montar_dados_relatorio_fake(conn, empresa_codigo, periodo, periodo_label, **kwargs):
    return dict(DADOS_POR_EMPRESA[empresa_codigo][periodo]), True


def _patches(periodos_ativos_por_empresa=None):
    periodos_ativos_por_empresa = periodos_ativos_por_empresa or {
        "ENERGIA": [P2025, P2026], "SMG": [P2025, P2026],
    }
    return [
        patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS),
        patch("dados_relatorio_comentado.montar_dados_relatorio", side_effect=_montar_dados_relatorio_fake),
        patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO", granularidade=None:
              (BP_2025 if per == P2025 else BP_2026) if tipo == "BP" else []),
        patch("dados_relatorio_comentado.db.listar_lancamentos_grupo",
              side_effect=lambda conn, per, tipo, cods, status="ATIVO", granularidade=None:
              (BP_GRUPO_2025 if per == P2025 else BP_GRUPO_2026) if tipo == "BP" else []),
        patch("dados_relatorio_comentado.db.listar_periodos",
              side_effect=lambda conn, cod, status="ATIVO": periodos_ativos_por_empresa.get(cod, [])),
        patch("dados_relatorio_comentado.db.listar_periodos_detalhado",
              side_effect=lambda conn, cod, status="ATIVO": [
                  {"periodo": p, "granularidade": ""} for p in periodos_ativos_por_empresa.get(cod, [])
              ]),
    ]


def _chamar(empresas_codigos=("ENERGIA",), periodos_ativos_por_empresa=None, **kwargs):
    patches = _patches(periodos_ativos_por_empresa)
    ctxs = [p.start() for p in patches]
    try:
        return drc.montar_dados_relatorio_comparativo(
            conn=object(), empresas_codigos=list(empresas_codigos),
            periodos=[P2025, P2026], periodos_labels=["2025", "1S2026"],
            periodo_range_label="2025 A 1S2026", **kwargs,
        )
    finally:
        for p in patches:
            p.stop()


def test_kpis_fluxo_com_serie_e_acumulado_correto():
    dados = _chamar()
    fluxo = {k["label"]: k for k in dados["kpis_fluxo"]}
    assert fluxo["Receita Operacional Líquida"]["valores"] == [31_200_000.0, 33_353_150.0]
    assert fluxo["Receita Operacional Líquida"]["tag"] == "fluxo"
    assert fluxo["Receita Operacional Líquida"]["acumulado"] == 31_200_000.0 + 33_353_150.0
    assert fluxo["EBITDA"]["valores"] == [1_000_000.0, 724_975.31]
    assert fluxo["Resultado Líquido"]["valores"] == [-640_000.0, -1_337_674.90]
    print("OK: montar_dados_relatorio_comparativo — kpis_fluxo com série por período e acumulado somado")


def test_kpis_saldo_sem_acumulado_nunca_soma():
    dados = _chamar()
    saldo = {k["label"]: k for k in dados["kpis_saldo"]}
    assert saldo["Total do Ativo"]["valores"] == [41_700_000.0, 43_359_080.94]
    assert saldo["Total do Ativo"]["tag"] == "saldo"
    assert "acumulado" not in saldo["Total do Ativo"], "saldo (BP) nunca soma entre períodos -- não pode ter acumulado"
    print("OK: montar_dados_relatorio_comparativo — kpis_saldo não carrega acumulado (BP nunca soma)")


def test_grafico_evolucao_usa_mesmas_3_metricas_do_fluxo():
    dados = _chamar()
    labels = [m["label"] for m in dados["grafico_evolucao_metricas"]]
    assert labels == ["Receita Operacional Líquida", "EBITDA", "Resultado Líquido"]
    print("OK: montar_dados_relatorio_comparativo — gráfico de evolução usa Receita/EBITDA/Resultado")


def test_campos_de_identificacao_e_rotulos():
    dados = _chamar()
    assert dados["empresa_nome"] == "Enermais Energia Ltda"
    assert dados["periodos_labels"] == ["2025", "1S2026"]
    assert dados["anexo_colunas"] == ["2025", "1S2026"]
    assert dados["periodo_range_label"] == "2025 A 1S2026"
    assert "2025 A 1S2026" in dados["cabecalho_relatorio"]
    print("OK: montar_dados_relatorio_comparativo — identificação/rótulos passados corretamente")


def test_admin_ausente_vira_string_vazia_sem_quebrar():
    dados = _chamar()
    assert dados["nome_administrador"] == ""
    assert dados["cargo_administrador"] == "Administrador"
    print("OK: montar_dados_relatorio_comparativo — sem admin informado, cai pra default sem quebrar")


def test_rejeita_periodos_e_labels_de_tamanho_diferente():
    try:
        drc.montar_dados_relatorio_comparativo(
            conn=object(), empresas_codigos=["ENERGIA"],
            periodos=[P2025, P2026], periodos_labels=["só um rótulo"],
            periodo_range_label="x",
        )
        assert False, "deveria ter levantado ValueError"
    except ValueError:
        pass
    print("OK: montar_dados_relatorio_comparativo — rejeita periodos/periodos_labels de tamanhos diferentes")


def test_rejeita_menos_de_2_ou_mais_de_4_periodos():
    for periodos in ([P2025], [P2025, P2026, P2025, P2026, P2025]):
        try:
            drc.montar_dados_relatorio_comparativo(
                conn=object(), empresas_codigos=["ENERGIA"],
                periodos=periodos, periodos_labels=["x"] * len(periodos),
                periodo_range_label="x",
            )
            assert False, f"deveria ter levantado ValueError pra {len(periodos)} período(s)"
        except ValueError:
            pass
    print("OK: montar_dados_relatorio_comparativo — aceita só de 2 a 4 períodos")


def test_rejeita_lista_de_empresas_vazia():
    try:
        drc.montar_dados_relatorio_comparativo(
            conn=object(), empresas_codigos=[],
            periodos=[P2025, P2026], periodos_labels=["2025", "1S2026"],
            periodo_range_label="x",
        )
        assert False, "deveria ter levantado ValueError"
    except ValueError:
        pass
    print("OK: montar_dados_relatorio_comparativo — rejeita empresas_codigos vazio")


# ───────────────────── consolidado (2+ empresas) ─────────────────────

def test_consolidado_soma_kpis_das_empresas_selecionadas():
    # pedido do Rafael 29/09: "com energia e outro (exemplo)... no
    # montante" -- ENERGIA + SMG tem que aparecer SOMADO, não só ENERGIA.
    dados = _chamar(empresas_codigos=["ENERGIA", "SMG"])
    fluxo = {k["label"]: k for k in dados["kpis_fluxo"]}
    saldo = {k["label"]: k for k in dados["kpis_saldo"]}
    assert fluxo["Receita Operacional Líquida"]["valores"] == [
        31_200_000.0 + 5_000_000.0, 33_353_150.0 + 6_000_000.0,
    ]
    assert saldo["Total do Ativo"]["valores"] == [41_700_000.0 + 3_000_000.0, 43_359_080.94 + 3_500_000.0]
    print("OK: montar_dados_relatorio_comparativo (consolidado) — KPIs somados entre as empresas selecionadas")


def test_consolidado_nome_e_escopo_mostram_grupo_e_quais_empresas():
    dados = _chamar(empresas_codigos=["ENERGIA", "SMG"])
    assert dados["empresa_nome"] == "Grupo Enermais"
    assert dados["cnpj"] == ""
    assert dados["empresas_codigos"] == ["ENERGIA", "SMG"]
    assert "ENERGIA" in dados["anexo_escopo_label"] and "SMG" in dados["anexo_escopo_label"]
    assert "2 empresas" in dados["anexo_escopo_label"]
    print("OK: montar_dados_relatorio_comparativo (consolidado) — nome 'Grupo Enermais' + escopo lista as empresas")


def test_consolidado_anexo_usa_valor_consolidado_do_pivot():
    dados = _chamar(empresas_codigos=["ENERGIA", "SMG"])
    ativo_por_label = {l[1]: l for l in dados["anexo_ativo"] if l[0] != "grupo"}
    # Disponível: ENERGIA 40+SMG 10=50 (2025); ENERGIA 60+SMG 15=75 (2026)
    assert ativo_por_label["Disponível"] == ("conta", "Disponível", 50.0, 75.0)
    assert ativo_por_label["TOTAL DO ATIVO"] == ("total", "TOTAL DO ATIVO", 125.0, 180.0)
    print("OK: montar_dados_relatorio_comparativo (consolidado) — Anexo usa VALOR CONSOLIDADO (soma) do pivot, não 1 empresa só")


def test_consolidado_1_empresa_so_bate_com_resultado_de_sempre():
    # empresas_codigos com 1 item só continua IDÊNTICO ao comportamento
    # de antes de existir consolidado (sem regressão pro caso mais usado).
    dados_1 = _chamar(empresas_codigos=["ENERGIA"])
    assert dados_1["empresa_nome"] == "Enermais Energia Ltda"
    assert dados_1["cnpj"] == "47.040.664/0001-48"
    fluxo = {k["label"]: k for k in dados_1["kpis_fluxo"]}
    assert fluxo["Receita Operacional Líquida"]["valores"] == [31_200_000.0, 33_353_150.0]
    print("OK: montar_dados_relatorio_comparativo — 1 empresa só continua idêntico ao comportamento de sempre")


def test_consolidado_rejeita_periodo_que_1_empresa_nao_tem():
    # SMG só tem 2025 ativo (sem 1S2026) -- somar esse período "no
    # montante" daria um total incompleto sem avisar (SMG entraria com
    # 0.0 silencioso). Tem que travar com erro claro, não gerar torto.
    try:
        _chamar(
            empresas_codigos=["ENERGIA", "SMG"],
            periodos_ativos_por_empresa={"ENERGIA": [P2025, P2026], "SMG": [P2025]},
        )
        assert False, "deveria ter levantado ValueError (SMG sem 1S2026 ativo)"
    except ValueError as e:
        assert "SMG" in str(e)
    print("OK: montar_dados_relatorio_comparativo (consolidado) — rejeita período que 1 das empresas não tem ativo")


# ───────────────────── _montar_anexo_multi_periodo ─────────────────────

def test_anexo_multi_periodo_uniao_com_conta_nova_e_zero_fill():
    # CLIENTES só existe em 2026 -- tem que entrar na lista final com
    # 0.0 no período de 2025 (união, não interseção; nunca inventa dado,
    # mas também não esconde conta que passou a existir).
    linhas = drc._montar_anexo_multi_periodo([BP_2025, BP_2026], "ATIVO")
    por_label = {l[1]: l for l in linhas if l[0] != "grupo"}
    assert por_label["Disponível"] == ("conta", "Disponível", 40.0, 60.0)
    assert por_label["Clientes"] == ("conta", "Clientes", 0.0, 90.0), (
        "conta nova (só em 2026) tem que aparecer com 0.0 no período onde não existia"
    )
    assert por_label["TOTAL DO ATIVO"] == ("total", "TOTAL DO ATIVO", 100.0, 150.0)
    print("OK: _montar_anexo_multi_periodo — união das contas dos N períodos, conta nova entra com 0.0 no período ausente")


def test_anexo_multi_periodo_preserva_ordem_do_primeiro_periodo():
    linhas = drc._montar_anexo_multi_periodo([BP_2025, BP_2026], "ATIVO")
    labels_na_ordem = [l[1] for l in linhas]
    # Disponível (existe nos 2 períodos, aparece em BP_2025 primeiro) vem
    # antes de Clientes (só aparece em BP_2026, entra no fim do grupo).
    assert labels_na_ordem.index("Disponível") < labels_na_ordem.index("Clientes")
    print("OK: _montar_anexo_multi_periodo — ordem das linhas segue o 1º período que tiver cada uma, novas no fim")


def test_anexo_multi_periodo_omite_subconta_mas_conta_pai_mantem_valor_proprio():
    # FIX_20260929q -- Rafael concordou em cortar só a subconta (opção 1
    # das 3 avaliadas pra encurtar o Anexo do Comparativo) pra aliviar a
    # pressão de página, sem mexer no layout clássico de 1 período.
    bp_com_par = [
        {"grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": 1000.0},
        {"grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": 1000.0},
        {"grupo": "ATIVO CIRCULANTE", "conta": "DEPOSITOS BANCARIOS A VISTA", "valor": 600.0},
        {"grupo": "ATIVO CIRCULANTE", "conta": "APLICACOES DE LIQUIDEZ IMEDIATA", "valor": 400.0},
        {"grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": 1000.0},
    ]
    linhas_multi = drc._montar_anexo_multi_periodo([bp_com_par, bp_com_par], "ATIVO")
    tipos_multi = [l[0] for l in linhas_multi]
    assert "subconta" not in tipos_multi, "Modelo B (multi-coluna) tem que omitir subconta -- pedido explícito do Rafael"
    por_label = {l[1]: l for l in linhas_multi if l[0] != "grupo"}
    assert por_label["Disponível"] == ("conta", "Disponível", 1000.0, 1000.0), (
        "a conta-pai precisa manter o valor PRÓPRIO (vem do SPED, não da soma dos filhos) mesmo sem mostrar os filhos"
    )
    assert por_label["TOTAL DO ATIVO"] == ("total", "TOTAL DO ATIVO", 1000.0, 1000.0)

    # Layout clássico (1 período, _montar_anexo chamado direto) continua
    # com subconta -- nunca precisou cortar nada, não foi tocado.
    linhas_classico = drc._montar_anexo(bp_com_par, "ATIVO")
    assert "subconta" in [l[0] for l in linhas_classico], "layout clássico (1 período) não deveria ter mudado"
    print("OK: Modelo B omite subconta no Anexo (conta-pai mantém valor próprio); layout clássico de 1 período intocado")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in testes:
        try:
            t()
        except AssertionError as e:
            falhas += 1
            print(f"FALHOU: {t.__name__} — {e}")
        except Exception as e:
            falhas += 1
            print(f"ERRO (não AssertionError) em {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
