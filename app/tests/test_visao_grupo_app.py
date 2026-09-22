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
    return [r for r in base if r["periodo"] in periodos and r["empresa_codigo"] in empresas_codigos]


def test_visao_grupo_completo_kpis_multi_periodo_macro_especifica_1_empresa_sem_excecao():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos_grupo", return_value=[P_JUN, P_MAI]), \
         patch.object(db, "listar_lancamentos_grupo_periodos", side_effect=_fake_listar_lancamentos_grupo_periodos):

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao na carga default (todas empresas, todos periodos): {at.exception}"
        # default: 2 periodos selecionados -> deveria aparecer o par de graficos, nao o aviso "selecione 2+"
        textos = " ".join(c.value for c in at.caption)
        assert "Evolução patrimonial (BP)" in textos
        assert "Evolução de resultado (DRE)" in textos
        # BP e DRE aparecem JUNTOS (sem radio de tipo) -- item 9
        markdowns = " ".join(m.value for m in at.markdown)
        assert "**BP**" in markdowns and "**DRE**" in markdowns
        assert len(at.dataframe) == 2, "esperava 2 tabelas (BP + DRE) do periodo de detalhe, juntas na mesma tela"

        # troca pra visao Especifica -- sem excecao
        at.radio(key="grupo_visao_sel").set_value("Específica (empresas abertas)")
        at.run(timeout=30)
        assert not at.exception, f"excecao na visao Especifica: {at.exception}"
        assert len(at.dataframe) == 2

        # item 9: 1 empresa so' (sem o minimo de 2 de antes) -- nao pode travar
        at.multiselect(key="grupo_empresas_sel").set_value([0])  # so' ENERGIA (indice 0 em EMPRESAS_FIXAS)
        at.run(timeout=30)
        assert not at.exception, f"excecao com 1 empresa so' selecionada: {at.exception}"

        # item 9: 1 periodo so' -- KPIs continuam, grafico vira aviso, sem excecao
        at.multiselect(key="grupo_periodos_sel").set_value([P_JUN])
        at.run(timeout=30)
        assert not at.exception, f"excecao com 1 periodo so': {at.exception}"
        textos_1periodo = " ".join(c.value for c in at.caption)
        assert "Selecione 2+ períodos" in textos_1periodo

    print(
        "OK: Visao Grupo Completo — KPIs+graficos multi-periodo, BP+DRE juntos, "
        "visao Macro/Especifica, 1 empresa e 1 periodo (sem os minimos antigos), tudo sem excecao "
        "com valor Decimal (regressao do TypeError Decimal+float)"
    )


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
