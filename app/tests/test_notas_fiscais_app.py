# -*- coding: utf-8 -*-
"""AppTest da tela Notas Fiscais (app/telas/7_Notas_Fiscais.py) -- 01/10/2026:
seletor de rodada (reabre qualquer conferencia) + download da planilha COMPLETA."""
import sys
import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402
import conexao  # noqa: E402
import db  # noqa: E402
import nf_sienge  # noqa: E402

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "7_Notas_Fiscais.py")

HIST = [
    {"import_id": "id-novo", "criado_em": datetime.datetime(2026, 10, 1, 9, 0), "periodo_referencia": "07/2026",
     "total_notas": 2, "total_lancadas": 1, "total_pendencias": 1, "arquivo_nome": "m_jul.xlsx", "usuario": "x"},
    {"import_id": "id-velho", "criado_em": datetime.datetime(2026, 9, 1, 9, 0), "periodo_referencia": "06/2026",
     "total_notas": 1, "total_lancadas": 1, "total_pendencias": 0, "arquivo_nome": "m_jun.xlsx", "usuario": "x"},
]


def _tabela(import_id):
    return pd.DataFrame([
        dict(numero_nota="22299", cfop="5102", data_emissao=datetime.date(2026, 7, 1), valor=4266.0,
             fornecedor_nome="SOLAR", fornecedor_cnpj="1", status="LANCADA", confianca="NUMERO_CNPJ_VALOR",
             sienge_bill_id=32034, sienge_documento="NFE 22299", sienge_valor=4266.0, observacao=None,
             pendencia_status=None, atualizado_em=datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc),
             registro_id=1, origem="MANIFESTO"),
        dict(numero_nota="500", cfop="5102", data_emissao=datetime.date(2026, 7, 5), valor=10.0,
             fornecedor_nome="OUTRO", fornecedor_cnpj="2", status="NAO_ENCONTRADA", confianca=None,
             sienge_bill_id=None, sienge_documento=None, sienge_valor=None, observacao=None,
             pendencia_status="PENDENTE", atualizado_em=datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc),
             registro_id=2, origem="MANIFESTO"),
    ])


def test_nf_tela_abre_ultima_rodada_e_oferece_download_completo():
    with patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(nf_sienge, "ultima_sincronizacao", return_value=datetime.datetime(2026, 10, 1, 4, 0)), \
         patch.object(nf_sienge, "listar_historico_importacoes", return_value=HIST), \
         patch.object(nf_sienge, "listar_conciliacao", side_effect=lambda c, i: _tabela(i)), \
         patch.object(nf_sienge, "listar_orfaos_sienge", return_value=pd.DataFrame()), \
         patch.object(db, "registrar_evento", return_value=None):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert at.selectbox(key="nf_rodada_sel").value == "id-novo"
        labels = [b.label for b in at.get("download_button")] if hasattr(at, "get") else []
        assert any("COMPLETA" in l for l in labels), labels
        # tela mostra o titulo do Sienge
        at.multiselect(key="nf_filtro_status").set_value(["LANCADA", "NAO_ENCONTRADA"]).run(timeout=30)
        df = at.dataframe[0].value
        assert "Título Sienge" in df.columns and "32034" in set(df["Título Sienge"])
        # reabrir rodada antiga
        at.selectbox(key="nf_rodada_sel").set_value("id-velho").run(timeout=30)
        assert not at.exception, at.exception
