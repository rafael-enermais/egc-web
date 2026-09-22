# -*- coding: utf-8 -*-
"""
Teste de integracao (Streamlit AppTest) da pagina Revisao/Correcao
(app/telas/2_Revisao_Correcao.py) -- mesmo padrao das outras paginas.
Cobre o fix de clareza do item 8 (Rafael, 22/09/2026: "o 'corrigido' nao
da pra marcar, ta inutil kk... qual seria o fluxo pra correcao?") -- so'
verifica que a pagina carrega sem excecao com o `help=` novo na coluna
"Corrigido" e o caption extra explicando o fluxo (editar a celula Valor
direto na tabela).

Rodar: python3 tests/test_revisao_correcao_app.py
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

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "2_Revisao_Correcao.py")

MOCK_LANCAMENTOS_BP = [
    {
        "id": 1, "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("1000.00"),
        "origem": "PDF", "pdf_original": None, "arquivo_pdf": "teste.pdf",
        "atualizado_em": datetime.datetime(2026, 9, 1, 10, 0, 0),
    },
    {
        "id": 2, "grupo": "ATIVO CIRCULANTE", "conta": "ADIANTAMENTOS DE CLIENTES", "valor": Decimal("500.00"),
        "origem": "MANUAL 06/2026", "pdf_original": Decimal("450.00"), "arquivo_pdf": "teste.pdf",
        "atualizado_em": datetime.datetime(2026, 9, 2, 11, 0, 0),
    },
]


def test_revisao_correcao_carrega_sem_excecao_com_coluna_corrigido_e_ajuda():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[datetime.date(2026, 6, 30)]), \
         patch.object(db, "listar_lancamentos", side_effect=lambda conn, cod, periodo, tipo, status="ATIVO": (
             MOCK_LANCAMENTOS_BP if tipo == "BP" else []
         )):

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao carregando Revisão/Correção: {at.exception}"

        textos_caption = " ".join(c.value for c in at.caption)
        assert "edite direto a célula" in textos_caption, (
            "esperava a dica nova explicando que a correção é editar a célula Valor direto"
        )
        assert len(at.dataframe) == 1, "esperava a tabela de BP renderizada (1 combinação empresa+período aberta por default)"
        print(
            "OK: Revisão/Correção — carrega sem exceção com o help= novo na coluna 'Corrigido' "
            "e a dica de fluxo (item 8)"
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
