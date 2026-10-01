# -*- coding: utf-8 -*-
"""
Teste de integracao (Streamlit AppTest) da pagina Visao Grupo
(app/telas/4_Visao_Grupo.py) -- diferente de test_db_logic.py (que testa
SQL/parametros de db.py isolado), aqui a pagina INTEIRA roda de verdade
(script runner do Streamlit), com auth/conexao/db mockados, pra pegar erro
de runtime que so aparece com o script rodando (foi assim que o bug de
Decimal abaixo foi encontrado -- NAO nos testes de db.py, que so
injetavam float no mock, nunca o tipo real que o psycopg2 devolve).

Bug real gravado aqui como regressao (fix 22/09/2026): psycopg2 devolve
NUMERIC do Postgres como decimal.Decimal, nao float. O pivot da Visao
Grupo fazia pivot_table + reindex(fill_value=0.0) + .sum(axis=1) direto
em cima do valor cru do banco -- com Decimal misturado a float (do
fill_value), o .sum(axis=1) quebrava com TypeError. Corrigido com
df['valor'] = df['valor'].astype(float) logo apos montar o DataFrame.
MOCK_LANCS_BP/MOCK_LANCS_DRE abaixo usam Decimal de proposito, pra este
teste falhar de novo se algum dia essa linha for removida sem querer.

Reescrito 22/09/2026 pro escopo "Completo" (itens #6/#9 do feedback ao
vivo do Rafael, escopo escolhido por ele via pergunta de decisao):
periodo virou multiselect (nao so' 1), BP e DRE aparecem juntos (sem
radio de tipo), e tem uma secao nova de KPIs+graficos no topo
("Resumo do grupo") que so' usa db.listar_lancamentos_grupo_periodos
(consulta multi-periodo nova) -- db.listar_lancamentos_grupo (1 periodo)
nao e' mais chamada por esta pagina (continua existindo do jeito que
esta', usada pela ferramenta do chat).

Nota tecnica (herdada, ainda vale): os cenarios ficam numa UNICA funcao
de teste, reaproveitando o MESMO objeto AppTest entre as trocas
(at.widget(...).set_value(...) + at.run() de novo em cima do mesmo 'at')
-- criar VARIAS instancias separadas de AppTest.from_file() no mesmo
processo perde os elementos st.dataframe a partir da 2a instancia
(particularidade do AppTest, nao bug desta pagina).

Rodar: python3 tests/test_visao_grupo_app.py
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

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "4_Visao_Grupo.py")

P_MAI = datetime.date(2026, 5, 31)
P_JUN = datetime.date(2026, 6, 30)

# Decimal de proposito (psycopg2 real nunca devolve float) -- ver nota acima.
# CLIENTES em 3 empresas (testa o pivot/soma cruzando empresas), DISPONIVEL
# so' em 1 (testa reindex com fill_value=0.0 nas outras), IMOBILIZADO so'
# em ENG (fora do bloco ENERGIA/SMG/CONST, testa EMPRESAS CONSOLIDADORAS),
# + TOTAL DO ATIVO/PASSIVO em 2 periodos (05 e 06/2026, so' ENERGIA) pros
# KPIs/grafico do "Resumo do grupo".
MOCK_LANCS_BP = [
    {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("500000.00")},
    {"empresa_codigo": "SMG", "periodo": P_JUN, "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("121755.76")},
    {"empresa_codigo": "CONST", "periodo": P_JUN, "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("80.00")},
    {"empresa_codigo": "SMG", "periodo": P_JUN, "grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": Decimal("749.70")},
    {"empresa_codigo": "ENG", "periodo": P_JUN, "grupo": "ATIVO NAO CIRCULANTE", "conta": "IMOBILIZADO", "valor": Decimal("10000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "ATIVO", "conta": "TOTAL DO ATIVO", "valor": Decimal("1100000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "PASSIVO", "conta": "TOTAL DO PASSIVO", "valor": Decimal("1100000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_MAI, "grupo": "ATIVO", "conta": "TOTAL DO ATIVO", "valor": Decimal("1000000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_MAI, "grupo": "PASSIVO", "conta": "TOTAL DO PASSIVO", "valor": Decimal("1000000.00")},
]
MOCK_LANCS_DRE = [
    {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": Decimal("320000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "RESULTADO", "conta": "LUCRO BRUTO", "valor": Decimal("120000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": Decimal("60000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_MAI, "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": Decimal("300000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_MAI, "grupo": "RESULTADO", "conta": "LUCRO BRUTO", "valor": Decimal("110000.00")},
    {"empresa_codigo": "ENERGIA", "periodo": P_MAI, "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": Decimal("50000.00")},
]


def _fake_listar_lancamentos_grupo_periodos(conn, periodos, tipo, empresas_codigos, status="ATIVO"):
    base = MOCK_LANCS_BP if tipo == "BP" else MOCK_LANCS_DRE
    return [dict(r, granularidade="") for r in base if r["periodo"] in periodos and r["empresa_codigo"] in empresas_codigos]


def test_visao_grupo_completo_kpis_multi_periodo_macro_especifica_1_empresa_sem_excecao():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos_grupo_detalhado", return_value=[
             {"periodo": P_JUN, "granularidade": ""}, {"periodo": P_MAI, "granularidade": ""},
         ]), \
         patch.object(db, "listar_lancamentos_grupo_periodos", side_effect=_fake_listar_lancamentos_grupo_periodos):

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao na carga default (todas empresas, todos periodos): {at.exception}"
        # default: 2 periodos selecionados -> deveria aparecer o par de graficos, nao o aviso "selecione 2+"
        # 01/10/2026: dashboard novo -- graficos plotly (resultado por periodo + margens)
        assert len(at.get("plotly_chart")) >= 2, "esperava os graficos do dashboard"
        # BP e DRE aparecem JUNTOS (sem radio de tipo) -- item 9
        markdowns = " ".join(m.value for m in at.markdown)
        assert "**BP**" in markdowns and "**DRE**" in markdowns
        assert len(at.dataframe) == 3, "esperava 3 tabelas: comparativa por empresa + BP + DRE do periodo de detalhe"

        # troca pra visao Especifica -- sem excecao
        at.radio(key="grupo_visao_sel").set_value("Específica (empresas abertas)")
        at.run(timeout=30)
        assert not at.exception, f"excecao na visao Especifica: {at.exception}"
        assert len(at.dataframe) == 3

        # item 9: 1 empresa so' (sem o minimo de 2 de antes) -- nao pode travar
        at.multiselect(key="grupo_empresas_sel").set_value([0])  # so' ENERGIA (indice 0 em EMPRESAS_FIXAS)
        at.run(timeout=30)
        assert not at.exception, f"excecao com 1 empresa so' selecionada: {at.exception}"

        # item 9: 1 periodo so' -- KPIs continuam, grafico vira aviso, sem excecao
        at.multiselect(key="grupo_periodos_sel").set_value([P_JUN])
        at.run(timeout=30)
        assert not at.exception, f"excecao com 1 periodo so': {at.exception}"
        textos_1periodo = " ".join(c.value for c in at.caption)
        assert "Com 2+ períodos selecionados" in textos_1periodo

    print(
        "OK: Visao Grupo Completo — KPIs+graficos multi-periodo, BP+DRE juntos, "
        "visao Macro/Especifica, 1 empresa e 1 periodo (sem os minimos antigos), tudo sem excecao "
        "com valor Decimal (regressao do TypeError Decimal+float)"
    )


def test_2_granularidades_mesmo_periodo_fim_seletor_de_detalhe_separa():
    """
    Fase 3.1 (29/09/2026): trimestral e semestral ATIVOS pra 06/2026 --
    o seletor "Período de detalhe" tem que oferecer as 2 opções
    separadas (rótulo com a granularidade) e a tabela de detalhe filtrar
    só a escolhida, nunca misturar as contas dos 2 documentos.
    """
    lancs_trimestral = [
        {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES",
         "valor": Decimal("100.00"), "granularidade": "trimestral"},
    ]
    lancs_semestral = [
        {"empresa_codigo": "ENERGIA", "periodo": P_JUN, "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES",
         "valor": Decimal("600.00"), "granularidade": "semestral"},
    ]

    def _lancs_ambiguo(conn, periodos, tipo, empresas_codigos, status="ATIVO"):
        if tipo != "BP":
            return []
        return [r for r in (lancs_trimestral + lancs_semestral) if r["empresa_codigo"] in empresas_codigos]

    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos_grupo_detalhado", return_value=[
             {"periodo": P_JUN, "granularidade": "trimestral"}, {"periodo": P_JUN, "granularidade": "semestral"},
         ]), \
         patch.object(db, "listar_lancamentos_grupo_periodos", side_effect=_lancs_ambiguo):

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao com 2 granularidades no mesmo periodo_fim: {at.exception}"

        # Fase 4 (30/09/2026): "Base do período" explicita -- as 2 granularidades
        # viram opcoes do seletor de base (nao mais 2 periodos de detalhe), e a
        # tabela/KPIs so' usam a base escolhida.
        sel_base = at.selectbox(key="grupo_base_sel")
        assert len(sel_base.options) == 2, f"esperava 2 bases (trimestral e semestral), veio {len(sel_base.options)}"
        assert sel_base.value == "semestral", "default = base mais abrangente do periodo mais recente (so' ENERGIA selecionada aqui tem as 2)"

        sel_base.set_value("trimestral").run(timeout=30)
        assert not at.exception, f"excecao ao escolher trimestral: {at.exception}"
        tabela_bp = at.dataframe[1].value  # [0] = tabela comparativa por empresa
        assert list(tabela_bp["VALOR CONSOLIDADO"]) == ["R$ 100,00"], (
            f"esperava só a linha do trimestral (R$ 100,00) na tabela de detalhe, veio {list(tabela_bp['VALOR CONSOLIDADO'])}"
        )
        sel_base.set_value("semestral").run(timeout=30)
        tabela_bp = at.dataframe[1].value  # [0] = tabela comparativa por empresa
        assert list(tabela_bp["VALOR CONSOLIDADO"]) == ["R$ 600,00"], "semestral tem que mostrar so' a linha do semestral"
        print("OK: 2 granularidades no mesmo periodo_fim — seletor de base separa, tabela nunca mistura os 2 documentos")


def test_resumo_do_grupo_mostra_ebitda_margens_e_indicadores_da_base_escolhida():
    """v0.40.0 (item F): EBITDA, Margem EBITDA, Margem Liquida, Alavancagem,
    Capital de Giro, ROA/ROE, Liquidez e Endividamento no Resumo do grupo,
    cada um da BASE escolhida (trimestral x semestral nao se misturam)."""
    P = P_JUN
    def _l(conta, v, gran):
        return {"empresa_codigo": "ENERGIA", "periodo": P, "grupo": "X", "conta": conta, "valor": Decimal(str(v)),
                "granularidade": gran}
    dre = [_l("RECEITA OPERACIONAL LIQUIDA", 1000, "trimestral"), _l("LUCRO OPERACIONAL LIQUIDO", 100, "trimestral"),
           _l("LUCRO LIQUIDO DO EXERCICIO", 80, "trimestral"), _l("DEPRECIACOES", -20, "trimestral"),
           _l("RECEITA OPERACIONAL LIQUIDA", 3000, "semestral"), _l("LUCRO OPERACIONAL LIQUIDO", 900, "semestral"),
           _l("LUCRO LIQUIDO DO EXERCICIO", 700, "semestral")]
    bp = [_l("TOTAL DO ATIVO", 2000, "trimestral"), _l("TOTAL CIRCULANTE ATIVO", 800, "trimestral"),
          _l("TOTAL CIRCULANTE PASSIVO", 400, "trimestral"), _l("TOTAL NAO CIRCULANTE PASSIVO", 600, "trimestral"),
          _l("TOTAL PATRIMONIO LIQUIDO", 1000, "trimestral"), _l("TOTAL DO PASSIVO", 2000, "trimestral"),
          _l("TOTAL DO ATIVO", 5000, "semestral")]

    def _fake(conn, periodos, tipo, empresas_codigos, status="ATIVO", granularidade=None):
        base = bp if tipo == "BP" else dre
        return [r for r in base if r["periodo"] in periodos and r["empresa_codigo"] in empresas_codigos]

    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos_grupo_detalhado", return_value=[
             {"periodo": P, "granularidade": "trimestral"}, {"periodo": P, "granularidade": "semestral"}]), \
         patch.object(db, "listar_lancamentos_grupo_periodos", side_effect=_fake):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        at.selectbox(key="grupo_base_sel").set_value("trimestral").run(timeout=30)
        assert not at.exception, at.exception
        m = {x.label: x.value for x in at.metric}
        for rotulo in ("Receita Líquida", "Lucro Bruto", "EBITDA", "Resultado Líquido", "Margem Bruta",
                       "Margem EBITDA", "Margem Líquida", "ROA", "ROE", "Total do Ativo", "Total do Passivo",
                       "Capital de Giro", "Liquidez Corrente", "Endividamento Geral", "Alavancagem"):
            assert rotulo in m, f"KPI '{rotulo}' ausente do Resumo do grupo: {sorted(m)}"
        assert m["EBITDA"] == "R$ 120,00"              # 100 + 20 de depreciacao de volta
        assert m["Margem EBITDA"] == "12,0%"
        assert m["Margem Líquida"] == "8,0%"
        assert m["Capital de Giro"] == "R$ 400,00"     # 800 - 400
        assert m["Liquidez Corrente"] == "2,00x"
        assert m["Endividamento Geral"] == "50,0%"     # (400+600)/2000
        assert m["Alavancagem"] == "1,00x"             # 1000/1000
        assert m["ROE"] == "8,0%"
        # base semestral: numeros do OUTRO documento
        at.selectbox(key="grupo_base_sel").set_value("semestral").run(timeout=30)
        assert not at.exception, at.exception
        m2 = {x.label: x.value for x in at.metric}
        assert m2["Receita Líquida"] == "R$ 3.000,00" and m2["EBITDA"] == "R$ 900,00"
    print("OK: Resumo do grupo — EBITDA/margens/alavancagem/capital de giro/ROA/ROE/liquidez/endividamento por base")


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
