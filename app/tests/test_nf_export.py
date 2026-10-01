# -*- coding: utf-8 -*-
import io
import sys
import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nf_export  # noqa: E402


def _tabela():
    base = dict(cfop="5102", fornecedor_cnpj="07393522000140", confianca=None, observacao=None,
                pendencia_status=None, atualizado_em=datetime.datetime(2026, 9, 30, 10, 0, tzinfo=datetime.timezone.utc))
    return pd.DataFrame([
        dict(base, numero_nota="22299", data_emissao=datetime.date(2026, 7, 1), valor=4266.0,
             fornecedor_nome="SOLAR MOVEIS", status="LANCADA", confianca="NUMERO_CNPJ_VALOR",
             sienge_bill_id=32034, sienge_documento="NFE 22299", sienge_valor=4266.0, registro_id=1, origem="MANIFESTO"),
        dict(base, numero_nota="500", data_emissao=datetime.date(2026, 7, 5), valor=100.5,
             fornecedor_nome="OUTRO", status="NAO_ENCONTRADA", sienge_bill_id=None, sienge_documento=None,
             sienge_valor=None, pendencia_status="PENDENTE", registro_id=2, origem="MANIFESTO"),
        dict(base, numero_nota="[Sienge] nº 77", data_emissao=datetime.date(2026, 7, 9), valor=None,
             fornecedor_nome="ORFAO", status="SIENGE_SEM_MANIFESTO", sienge_bill_id=99, sienge_documento="NFE 77",
             sienge_valor=10.0, pendencia_status="PENDENTE", registro_id=3, origem="SIENGE_ORFAO"),
    ])


def test_preparar_tabela_renomeia_e_tira_internas():
    df = nf_export.preparar_tabela(_tabela())
    assert "registro_id" not in df.columns and "origem" not in df.columns
    assert "Título Sienge" in df.columns and "Documento no Sienge" in df.columns
    assert list(df.columns)[:3] == ["Nº da nota", "Título Sienge", "Documento no Sienge"]


def test_xlsx_completo_tem_todas_as_abas_e_todas_as_linhas():
    data = nf_export.gerar_xlsx_conferencia(_tabela(), "Enermais Energia", "07/2026", "manifesto.xlsx", "01/10/2026")
    xl = pd.ExcelFile(io.BytesIO(data))
    assert xl.sheet_names == ["Resumo", "Conferência completa", "Pendências", "Sienge sem nota"]
    comp = xl.parse("Conferência completa")
    assert len(comp) == 3  # lancada + pendente + orfao (nada some)
    assert comp.loc[comp["Nº da nota"].astype(str) == "22299", "Título Sienge"].iloc[0] == 32034
    assert comp.loc[comp["Nº da nota"].astype(str) == "22299", "Documento no Sienge"].iloc[0] == "NFE 22299"
    assert len(xl.parse("Pendências")) == 2
    assert len(xl.parse("Sienge sem nota")) == 1
    resumo = xl.parse("Resumo")
    assert resumo.loc[resumo["Item"] == "Notas no manifesto", "Valor"].iloc[0] == 2
    assert resumo.loc[resumo["Item"] == "Lançadas no Sienge", "Valor"].iloc[0] == 1


def test_xlsx_sem_orfaos_nem_pendencias_nao_quebra():
    t = _tabela().iloc[[0]]
    data = nf_export.gerar_xlsx_conferencia(t, "X", "07/2026")
    xl = pd.ExcelFile(io.BytesIO(data))
    assert len(xl.parse("Pendências")) == 0 and len(xl.parse("Sienge sem nota")) == 0
