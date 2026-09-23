# -*- coding: utf-8 -*-
"""
AppTest de integracao pra app/app.py (pagina Início) -- mesmo padrao ja
validado em test_visao_grupo_app.py (auth/conexao/db mockados via
patch.object, 1 UNICA instancia de AppTest reaproveitada entre cenarios).

Cobre a secao nova de indicadores contabeis (22/09/2026, pedido do
Rafael): carga default com historico BP+DRE de 2 periodos (KPIs +
expander com tabela completa aparecem, sem excecao), empresa sem
historico nenhum ainda (caption informativo, sem excecao), e Decimal
real do psycopg2 nao quebra o calculo (mesma classe de bug ja encontrada
na Visao Grupo/Dashboard de Projecao).

Cobre tambem as 2 secoes novas de 23/09/2026 (KPI consolidado do grupo +
Painel de pendencias, item deferido em 22/09 -- "Salva o progresso,
amanha terminaremos com esses pontos"): carga default com
listar_periodos_grupo/listar_lancamentos_grupo_periodos mockados (grupo
completo em 1 periodo, incompleto -- so' BP -- no outro), confirmando
que o KPI consolidado soma as 2 empresas do mock e que o painel de
pendencias lista so' a combinacao incompleta.

Rodar: python3 tests/test_inicio_app.py
"""
import sys
import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402
import conexao  # noqa: E402
import db  # noqa: E402

PAGE = str(Path(__file__).resolve().parent.parent / "app.py")

P_MAI = datetime.date(2026, 5, 31)
P_JUN = datetime.date(2026, 6, 30)

HIST_BP = [
    {"periodo": P_MAI, "grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": Decimal("200000.00")},
    {"periodo": P_MAI, "grupo": "PASSIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE PASSIVO", "valor": Decimal("100000.00")},
    {"periodo": P_MAI, "grupo": "PASSIVO NAO CIRCULANTE", "conta": "TOTAL NAO CIRCULANTE PASSIVO", "valor": Decimal("50000.00")},
    {"periodo": P_MAI, "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": Decimal("1000000.00")},
    {"periodo": P_MAI, "grupo": "PATRIMONIO LIQUIDO", "conta": "TOTAL PATRIMONIO LIQUIDO", "valor": Decimal("850000.00")},
    {"periodo": P_JUN, "grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": Decimal("220000.00")},
    {"periodo": P_JUN, "grupo": "PASSIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE PASSIVO", "valor": Decimal("110000.00")},
    {"periodo": P_JUN, "grupo": "PASSIVO NAO CIRCULANTE", "conta": "TOTAL NAO CIRCULANTE PASSIVO", "valor": Decimal("40000.00")},
    {"periodo": P_JUN, "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": Decimal("1100000.00")},
    {"periodo": P_JUN, "grupo": "PATRIMONIO LIQUIDO", "conta": "TOTAL PATRIMONIO LIQUIDO", "valor": Decimal("950000.00")},
]
HIST_DRE = [
    {"periodo": P_MAI, "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": Decimal("300000.00")},
    {"periodo": P_MAI, "grupo": "RESULTADO", "conta": "LUCRO BRUTO", "valor": Decimal("110000.00")},
    {"periodo": P_MAI, "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": Decimal("50000.00")},
    {"periodo": P_JUN, "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": Decimal("320000.00")},
    {"periodo": P_JUN, "grupo": "RESULTADO", "conta": "LUCRO BRUTO", "valor": Decimal("120000.00")},
    {"periodo": P_JUN, "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": Decimal("60000.00")},
]


def _hist_side_effect(conn, empresa_codigo, tipo, status="ATIVO"):
    return HIST_BP if tipo == "BP" else HIST_DRE


# Grupo (KPI consolidado + painel de pendencias, 23/09/2026): ENERGIA e SMG
# com BP+DRE completos em P_MAI; em P_JUN so' ENERGIA tem BP (SMG sem
# nada, DRE nenhuma empresa) -- garante 1 linha "Completo" e pendencias
# reais no painel, sem inventar cenario totalmente feliz.
LANCS_BP_GRUPO = [
    {"empresa_codigo": "ENERGIA", "periodo": P_MAI, "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": Decimal("1000000.00")},
    {"empresa_codigo": "SMG", "periodo": P_MAI, "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": Decimal("300000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_MAI, "grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": Decimal("200000.00")},
    {"empresa_codigo": "SMG", "periodo": P_MAI, "grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": Decimal("60000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_MAI, "grupo": "PASSIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE PASSIVO", "valor": Decimal("100000.00")},
    {"empresa_codigo": "SMG", "periodo": P_MAI, "grupo": "PASSIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE PASSIVO", "valor": Decimal("30000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": Decimal("1100000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": Decimal("250000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "PASSIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE PASSIVO", "valor": Decimal("125000.00")},
]
LANCS_DRE_GRUPO = [
    {"empresa_codigo": "ENERGIA", "periodo": P_MAI, "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": Decimal("300000.00")},
    {"empresa_codigo": "SMG", "periodo": P_MAI, "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": Decimal("90000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_MAI, "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": Decimal("50000.00")},
    {"empresa_codigo": "SMG", "periodo": P_MAI, "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": Decimal("15000.00")},
]


def _lancs_grupo_side_effect(conn, periodos, tipo, empresas_codigos, status="ATIVO"):
    return LANCS_BP_GRUPO if tipo == "BP" else LANCS_DRE_GRUPO


def run():
    with patch.object(auth, "require_login", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[P_JUN, P_MAI]), \
         patch.object(db, "listar_historico_grupo", side_effect=_hist_side_effect) as m_hist, \
         patch.object(db, "listar_periodos_grupo", return_value=[P_JUN, P_MAI]), \
         patch.object(db, "listar_lancamentos_grupo_periodos", side_effect=_lancs_grupo_side_effect):

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        print("Carga default (Início, empresa ENERGIA) exception:", at.exception)
        assert not at.exception, f"FALHOU carga default: {at.exception[0] if at.exception else None}"

        # 3 KPIs (Liquidez/Capital de Giro/Endividamento) + 4 KPIs
        # (Margem Bruta/Líquida/ROA/ROE) = 7 st.metric na secao de indicadores
        # (+ 2 do bloco de periodos ativos/arquivados que ja existia = 9 total)
        # -- at.metric ja reflete a pagina INTEIRA renderizada (inclui tambem
        # os 7 metrics do KPI consolidado do grupo, secao nova de 23/09/2026,
        # checada em detalhe mais abaixo); aqui so' confere que os labels da
        # secao empresa-unica estao presentes.
        labels = {m.label for m in at.metric}
        for esperado in ["Liquidez Corrente", "Capital de Giro", "Endividamento Geral",
                          "Margem Bruta", "Margem Líquida", "ROA (Retorno s/ Ativo)", "ROE (Retorno s/ PL)"]:
            assert esperado in labels, f"faltou o indicador {esperado!r} nos metrics: {labels}"
        print("OK: 7 indicadores contábeis renderizados como st.metric, sem exceção com Decimal real")

        assert m_hist.call_count >= 2, "esperava pelo menos 2 chamadas (BP e DRE) a listar_historico_grupo"

        assert len(at.dataframe) >= 1, "esperava a tabela do expander 'Histórico completo dos indicadores'"
        tabela = at.dataframe[0].value
        assert len(tabela) == 2, f"esperava 2 períodos na tabela histórica, veio {len(tabela)}"
        print("OK: expander com histórico completo (2 períodos) renderiza")

        # --- KPI consolidado do grupo (23/09/2026) ---
        labels_todos = [m.label for m in at.metric]
        # 9 da secao empresa-unica + 7 do consolidado do grupo (mesmas 3+4)
        assert len(at.metric) == 16, f"esperava 16 metrics (9 empresa única + 7 grupo), veio {len(at.metric)}"
        assert labels_todos.count("Liquidez Corrente") == 2, "esperava 'Liquidez Corrente' 1x na secao empresa + 1x no grupo"
        # Período de referência do grupo = P_JUN (mais recente com QUALQUER
        # lançamento no grupo -- só ENERGIA/BP ali, SMG e DRE ficam NaN
        # ("—"), o que é o comportamento correto e coerente com o painel de
        # pendências abaixo marcar 06/2026 como incompleto).
        # Liquidez Corrente do grupo em P_JUN = só ENERGIA (SMG sem BP nesse
        # período): 250000/125000 = 2.00x.
        liquidez_metric = next(m for i, m in enumerate(at.metric) if m.label == "Liquidez Corrente" and i >= 9)
        assert liquidez_metric.value == "2.00x", f"Liquidez Corrente do grupo errada: {liquidez_metric.value} (esperava 2.00x)"
        roa_metric = next(m for i, m in enumerate(at.metric) if m.label == "ROA (Retorno s/ Ativo)" and i >= 9)
        assert roa_metric.value == "—", f"ROA do grupo deveria ser '—' (sem DRE em 06/2026), veio {roa_metric.value}"
        print("OK: KPI consolidado do grupo soma ENERGIA+SMG (indicadores.calcular_indicadores reaproveitado sem mudar lógica), NaN vira '—' quando falta DRE do período")

        # --- Painel de pendências (23/09/2026) ---
        textos_caption = " ".join(c.value for c in at.caption)
        assert "combinação" in textos_caption and "faltando" in textos_caption, \
            f"esperava caption com contagem de pendências, veio: {textos_caption!r}"
        # tabela de pendencias = primeiro dataframe apos os 2 de indicadores (empresa + grupo)
        assert len(at.dataframe) >= 3, f"esperava pelo menos 3 dataframes (2 expanders de histórico + painel de pendências), veio {len(at.dataframe)}"
        tabela_pendentes = at.dataframe[1].value
        assert set(tabela_pendentes["Status"]) == {"⚠️ Só BP", "❌ Faltando"}, \
            f"esperava só linhas incompletas no painel, veio status: {set(tabela_pendentes['Status'])}"
        assert "✅ Completo" not in set(tabela_pendentes["Status"]), "painel de pendências não deve listar combinações completas"
        print("OK: painel de pendências lista só empresa×período incompleto (ENERGIA/SMG em 06/2026)")

        # --- troca de empresa (mock ignora o código recebido, mas exercita o rerender) ---
        at.selectbox(key="inicio_empresa_sel").set_value(1)
        at.run(timeout=30)
        print("Troca de empresa exception:", at.exception)
        assert not at.exception, f"FALHOU troca de empresa: {at.exception[0] if at.exception else None}"

    print("\n--- cenário 2: empresa sem BP/DRE nenhum ainda ---")
    with patch.object(auth, "require_login", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[]), \
         patch.object(db, "listar_historico_grupo", return_value=[]):

        at2 = AppTest.from_file(PAGE)
        at2.run(timeout=30)
        print("Carga sem histórico exception:", at2.exception)
        assert not at2.exception, f"FALHOU carga sem histórico: {at2.exception[0] if at2.exception else None}"
        textos = " ".join(c.value for c in at2.caption)
        assert "Sem BP/DRE suficiente" in textos, f"esperava aviso informativo, veio: {textos!r}"
        print("OK: empresa sem BP/DRE nenhum mostra aviso informativo, sem exceção")

    print("\nTODOS OS CENÁRIOS OK (indicadores contábeis na Início: carga, troca de "
          "empresa, empresa sem dado, Decimal real não quebra)")


if __name__ == "__main__":
    try:
        run()
        sys.exit(0)
    except AssertionError as e:
        print(f"FALHOU: {e}")
        sys.exit(1)
