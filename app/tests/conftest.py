# -*- coding: utf-8 -*-
"""
Configuracao compartilhada dos testes (v0.40.0).

1. Marker `pg`: testes de INTEGRACAO contra Postgres real (ver
   tests/pg_support.py). Rodam quando ha' Postgres disponivel
   (EGC_TEST_DATABASE_URL, ou initdb/pg_ctl instalados) e dao SKIP caso
   contrario -- nunca falham o CI por falta de banco.

2. Defaults de mock para as 2 leituras NOVAS de db que as salvaguardas de
   coerencia de granularidade (dados_relatorio_comentado.
   validar_cobertura_documento / _avisar_documentos_identicos) fazem e que
   os testes antigos, escritos com `conn=object()` e db.* mockado, nao
   conhecem. Sem banco nao ha' o que conferir -> "documento sem intervalo
   declarado" ([]) e "sem outras granularidades" ([]). Um teste que quiser
   comportamento proprio sobrepoe com seu proprio patch (patches de teste
   empilham por cima deste), e os testes `pg` NAO recebem estes defaults.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


_MODULOS_DB = {"test_db_logic", "test_db_granularidade_itens"}


def pytest_configure(config):
    config.addinivalue_line("markers", "pg: integracao contra Postgres real (skip se indisponivel)")


@pytest.fixture(autouse=True)
def _defaults_db_granularidade(request):
    # `drc.db` e' o proprio modulo db: patchar "dados_relatorio_comentado.db.x"
    # troca db.x globalmente -- os testes que exercitam o db.py de verdade
    # (cursor falso ou Postgres real) ficam de fora.
    if request.node.get_closest_marker("pg") or request.module.__name__.split(".")[-1] in _MODULOS_DB:
        yield
        return
    with patch("dados_relatorio_comentado.db.listar_inicios_documento", return_value=[]), \
         patch("dados_relatorio_comentado.db.listar_periodos_detalhado", return_value=[]):
        yield
