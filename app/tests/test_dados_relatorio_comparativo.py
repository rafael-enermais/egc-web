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

EMPRESAS = [{"codigo": "ENERGIA", "nome": "Enermais Energia Ltda", "cnpj": "47.040.664/0001-48"}]

P2025 = datetime.date(2025, 12, 31)
P2026 = datetime.date(2026, 6, 30)

DADOS_2025 = dict(
    receita_liquida=31_200_000.0, ebitda=1_000_000.0, resultado_liquido=-640_000.0,
    total_ativo=41_700_000.0, patrimonio_liquido=8_000_000.0,
)
DADOS_2026 = dict(
    receita_liquida=33_353_150.0, ebitda=724_975.31, resultado_liquido=-1_337_674.90,
    total_ativo=43_359_080.94, patrimonio_liquido=6_771_914.25,
)

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


def _montar_dados_relatorio_fake(conn, empresa_codigo, periodo, periodo_label, **kwargs):
    base = DADOS_2025 if periodo == P2025 else DADOS_2026
    return dict(base), True


def _patches():
    return [
        patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS),
        patch("dados_relatorio_comentado.montar_dados_relatorio", side_effect=_montar_dados_relatorio_fake),
        patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO":
              (BP_2025 if per == P2025 else BP_2026) if tipo == "BP" else []),
    ]


def _chamar(**kwargs):
    patches = _patches()
    ctxs = [p.start() for p in patches]
    try:
        return drc.montar_dados_relatorio_comparativo(
            conn=object(), empresa_codigo="ENERGIA",
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
            conn=object(), empresa_codigo="ENERGIA",
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
                conn=object(), empresa_codigo="ENERGIA",
                periodos=periodos, periodos_labels=["x"] * len(periodos),
                periodo_range_label="x",
            )
            assert False, f"deveria ter levantado ValueError pra {len(periodos)} período(s)"
        except ValueError:
            pass
    print("OK: montar_dados_relatorio_comparativo — aceita só de 2 a 4 períodos")


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
