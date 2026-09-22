# -*- coding: utf-8 -*-
"""
Teste de integracao (Streamlit AppTest) da pagina Visao Grupo
(app/telas/4_Visao_Grupo.py) -- diferente de test_db_logic.py (que testa
SQL/parametros de db.py isolado), aqui a pagina INTEIRA roda de verdade
(script runner do Streamlit), com auth/conexao/db mockados, pra pegar
erro de runtime que so aparece com o script rodando (foi assim que o bug
abaixo foi encontrado -- NAO nos testes de db.py, que so injetavam
float no mock, nunca o tipo real que o psycopg2 devolve).

Bug real gravado aqui como regressao (fix 22/09/2026): psycopg2 devolve
NUMERIC do Postgres como decimal.Decimal, nao float. O pivot da Visao
Grupo fazia pivot_table + reindex(fill_value=0.0) + .sum(axis=1) direto
em cima do valor cru do banco -- com Decimal misturado a float (do
fill_value), o .sum(axis=1) quebrava com TypeError. Descoberto na
verificacao ao vivo pos-deploy (nao local), corrigido com
df['valor'] = df['valor'].astype(float) logo apos montar o DataFrame.
MOCK_LANCS abaixo usa Decimal de proposito, pra este teste falhar de
novo se algum dia essa linha for removida sem querer.

Nota tecnica (22/09/2026): os 3 cenarios (Macro/Especifica/DRE) ficam
numa UNICA funcao de teste, reaproveitando o MESMO objeto AppTest entre
as trocas (at.radio(...).set_value(...) + at.run() de novo no mesmo
'at') -- criar VARIAS instancias separadas de AppTest.from_file() dentro
do mesmo processo (1 por funcao de teste) causou um comportamento
estranho do proprio Streamlit AppTest: a 2a/3a instancia perdia os
elementos st.dataframe (at.dataframe vinha vazio, sem excecao nenhuma).
Confirmado que isso e' uma particularidade do AppTest (nao um bug desta
pagina): reusando 1 unico 'at' e trocando widget+.run() por cima dele,
os 3 cenarios funcionam certinho.

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

# Decimal de proposito (psycopg2 real nunca devolve float) -- ver nota acima.
# CLIENTES em 3 empresas (testa o pivot/soma cruzando empresas), DISPONIVEL
# so' em 1 (testa reindex com fill_value=0.0 nas outras), IMOBILIZADO so'
# em ENG (fora do bloco ENERGIA/SMG/CONST, testa EMPRESAS CONSOLIDADORAS).
MOCK_LANCS = [
    {"empresa_codigo": "ENERGIA", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("500000.00")},
    {"empresa_codigo": "SMG", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("121755.76")},
    {"empresa_codigo": "CONST", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("80.00")},
    {"empresa_codigo": "SMG", "grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": Decimal("749.70")},
    {"empresa_codigo": "ENG", "grupo": "ATIVO NAO CIRCULANTE", "conta": "IMOBILIZADO", "valor": Decimal("10000.00")},
]


def test_visao_grupo_macro_especifica_dre_sem_excecao_com_valor_decimal():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos_grupo", return_value=[datetime.date(2026, 6, 30)]), \
         patch.object(db, "listar_lancamentos_grupo", return_value=MOCK_LANCS):

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao na visao Macro (default): {at.exception}"
        assert len(at.dataframe) == 1, "esperava 1 tabela renderizada (visao Macro)"
        assert len(at.dataframe[0].value) == 3, "esperava 3 contas distintas (CLIENTES, DISPONIVEL, IMOBILIZADO)"

        at.radio(key="grupo_visao_sel").set_value("Específica (empresas abertas)")
        at.run(timeout=30)
        assert not at.exception, f"excecao na visao Especifica: {at.exception}"
        assert len(at.dataframe) == 1, "esperava 1 tabela renderizada (visao Especifica)"

        at.radio(key="grupo_tipo_sel").set_value("DRE")
        at.run(timeout=30)
        assert not at.exception, f"excecao trocando pra DRE: {at.exception}"

    print(
        "OK: Visao Grupo — Macro/Especifica/DRE, sem excecao com valor Decimal "
        "(regressao do TypeError Decimal+float)"
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
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
