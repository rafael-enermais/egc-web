# -*- coding: utf-8 -*-
"""
Teste de integracao (Streamlit AppTest) da pagina Revisao/Correcao
(app/telas/2_Revisao_Correcao.py) -- mesmo padrao das outras paginas.
Cobre o fix de clareza do item 8 (Rafael, 22/09/2026: "o 'corrigido' nao
da pra marcar, ta inutil kk... qual seria o fluxo pra correcao?") -- so'
verifica que a pagina carrega sem excecao com o `help=` novo na coluna
"Corrigido" e o caption extra explicando o fluxo (editar a celula Valor
direto na tabela).

Cobre tambem o "Log de eventos" novo (23/09/2026, achado real do Rafael:
editou uma conta pra testar e nao lembrava qual foi, perguntou se o log
consegue puxar -- ate' entao so' dava pra ver via SQL Editor do Supabase):
2 cenarios -- SEM eventos ainda (caption "Nenhum evento registrado") e
COM eventos mockados (tabela renderiza com Empresa/Periodo/Detalhe
formatados, incluindo o "conta: valor antigo -> novo" que o fix de log
detalhado passou a gravar).

Rodar: python3 tests/test_revisao_correcao_app.py
"""
import sys
import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

import datetime as _dt

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


def test_log_de_eventos_sem_eventos_mostra_aviso_e_com_eventos_mostra_tabela():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[datetime.date(2026, 6, 30)]), \
         patch.object(db, "listar_lancamentos", side_effect=lambda conn, cod, periodo, tipo, status="ATIVO": (
             MOCK_LANCAMENTOS_BP if tipo == "BP" else []
         )), \
         patch.object(db, "listar_eventos_recentes", return_value=[]):

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao sem eventos: {at.exception}"
        textos = " ".join(c.value for c in at.caption)
        assert "Nenhum evento registrado" in textos, f"esperava aviso de log vazio, veio: {textos!r}"
        print("OK: Log de eventos — sem evento nenhum ainda, aviso informativo, sem exceção")

    eventos_mock = [
        {
            "id": 1, "origem": "revisao_correcao", "nivel": "INFO",
            "mensagem": "1 conta(s) corrigida(s) manualmente (BP)",
            "detalhe": "CLIENTES: R$ 1.000,00 → R$ 1.200,50",
            "empresa_codigo": "ENERGIA", "periodo": datetime.date(2026, 6, 30),
            "usuario": "teste@enermais.com.br", "criado_em": _dt.datetime(2026, 9, 23, 10, 0, 0),
        },
    ]
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[datetime.date(2026, 6, 30)]), \
         patch.object(db, "listar_lancamentos", side_effect=lambda conn, cod, periodo, tipo, status="ATIVO": (
             MOCK_LANCAMENTOS_BP if tipo == "BP" else []
         )), \
         patch.object(db, "listar_eventos_recentes", return_value=eventos_mock):

        at2 = AppTest.from_file(PAGE)
        at2.run(timeout=30)
        assert not at2.exception, f"excecao com eventos: {at2.exception}"
        # a tabela do log de eventos e' a ULTIMA (depois da tabela BP da
        # combinacao aberta -- ver docstring da pagina, "1 combinacao aberta por default")
        tabela_log = at2.dataframe[-1].value
        assert "Detalhe (conta: valor antigo → novo)" in tabela_log.columns
        assert tabela_log["Detalhe (conta: valor antigo → novo)"].iloc[0] == "CLIENTES: R$ 1.000,00 → R$ 1.200,50"
        assert tabela_log["Empresa"].iloc[0] == "Enermais Energia Ltda"
        print("OK: Log de eventos — com evento mockado, tabela mostra Empresa/Período/Detalhe formatados certos")


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
