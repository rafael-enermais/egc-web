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
    assert list(df.columns)[:3] == ["Nº da nota", "CFOP", "Título Sienge"]


def test_xlsx_completo_tem_todas_as_abas_e_todas_as_linhas():
    data = nf_export.gerar_xlsx_conferencia(_tabela(), "Enermais Energia", "07/2026", "manifesto.xlsx", "01/10/2026")
    xl = pd.ExcelFile(io.BytesIO(data))
    assert xl.sheet_names == ["Resumo", "Conferência completa", "Pendências", "Sienge sem nota"]
    comp = xl.parse("Conferência completa", header=nf_export.LINHA_CABECALHO - 1)
    assert len(comp) == 3  # lancada + pendente + orfao (nada some)
    assert comp.loc[comp["Nº da nota"].astype(str) == "22299", "Título Sienge"].iloc[0] == 32034
    assert comp.loc[comp["Nº da nota"].astype(str) == "22299", "Documento no Sienge"].iloc[0] == "NFE 22299"
    assert len(xl.parse("Pendências", header=nf_export.LINHA_CABECALHO - 1)) == 2
    assert len(xl.parse("Sienge sem nota", header=nf_export.LINHA_CABECALHO - 1)) == 1
    resumo = xl.parse("Resumo", header=nf_export.LINHA_CABECALHO - 1)
    assert resumo.loc[resumo["Item"] == "Notas no manifesto", "Valor"].iloc[0] == 2
    assert resumo.loc[resumo["Item"] == "Lançadas no Sienge", "Valor"].iloc[0] == 1


def test_xlsx_sem_orfaos_nem_pendencias_nao_quebra():
    t = _tabela().iloc[[0]]
    data = nf_export.gerar_xlsx_conferencia(t, "X", "07/2026")
    xl = pd.ExcelFile(io.BytesIO(data))
    assert len(xl.parse("Pendências", header=nf_export.LINHA_CABECALHO - 1)) == 0 and len(xl.parse("Sienge sem nota", header=nf_export.LINHA_CABECALHO - 1)) == 0


def test_aba_pendencias_traz_cfop_logo_depois_do_numero_01_10():
    import io
    import openpyxl
    t = _tabela()
    xlsx = nf_export.gerar_xlsx_conferencia(t, "Energia", "06/2026")
    wb = openpyxl.load_workbook(io.BytesIO(xlsx))
    cab = [c.value for c in wb["Pendências"][nf_export.LINHA_CABECALHO]]
    assert cab[:2] == ["Nº da nota", "CFOP"], cab
    assert any(r[1] for r in wb["Pendências"].iter_rows(min_row=nf_export.LINHA_CABECALHO + 1, values_only=True)), "CFOP vazio nas pendencias"


# ───────────── v0.46.0: varios periodos numa conferencia so' ─────────────
def _tabela_2_periodos():
    import datetime as dt
    base = dict(cfop="5102", data_emissao=dt.date(2026, 7, 1), valor=10.0, fornecedor_nome="F", fornecedor_cnpj="1",
                confianca=None, sienge_bill_id=None, sienge_documento=None, sienge_valor=None, observacao=None,
                pendencia_status="PENDENTE", atualizado_em=dt.datetime(2026, 10, 1))
    L = [
        {**base, "periodo": "08/2026", "numero_nota": "3", "status": "NAO_ENCONTRADA", "registro_id": 3, "origem": "MANIFESTO"},
        {**base, "periodo": "07/2026", "numero_nota": "1", "status": "LANCADA", "registro_id": 1, "origem": "MANIFESTO"},
        {**base, "periodo": "07/2026", "numero_nota": "2", "status": "VALOR_DIVERGENTE", "registro_id": 2, "origem": "MANIFESTO"},
        {**base, "periodo": "08/2026", "numero_nota": "[Sienge] 9", "status": "SIENGE_SEM_MANIFESTO", "registro_id": 9, "origem": "SIENGE_ORFAO"},
    ]
    import pandas as pd
    return pd.DataFrame(L)


def test_rotulo_e_sufixo_de_periodos():
    import nf_export
    assert nf_export.rotulo_periodos(["07/2026"]) == "07/2026"
    assert nf_export.rotulo_periodos(["02/2026", "01/2026", "03/2026"]) == "01/2026 a 03/2026"
    assert nf_export.rotulo_periodos(["12/2025", "01/2026"]) == "12/2025 a 01/2026"       # vira o ano
    assert nf_export.rotulo_periodos(["01/2026", "03/2026"]) == "01/2026, 03/2026"        # com buraco: lista
    assert nf_export.rotulo_periodos([]) == ""
    assert nf_export.sufixo_arquivo(["07/2026"]) == "07_2026"
    assert nf_export.sufixo_arquivo(["01/2026", "02/2026"]) == "01_2026_a_02_2026"
    assert nf_export.sufixo_arquivo(["01/2026", "03/2026"]) == "01_2026_03_2026"


def test_resumo_por_periodo_soma_igual_ao_resumo_geral():
    import nf_export
    t = _tabela_2_periodos()
    rpp = nf_export.resumo_por_periodo(t)
    assert list(rpp["Período"]) == ["07/2026", "08/2026"]
    assert list(rpp["Notas no manifesto"]) == [2, 1] and list(rpp["Lançadas"]) == [1, 0]
    assert list(rpp["Sienge sem nota"]) == [0, 1] and list(rpp["Total de pendências"]) == [1, 2]
    geral = nf_export.resumo_conferencia(t)
    assert rpp["Total de pendências"].sum() == geral["pendencias"] and rpp["Notas no manifesto"].sum() == geral["total"]


def test_xlsx_de_varios_periodos_tem_resumo_por_periodo_e_coluna_periodo():
    import io
    import openpyxl
    import nf_export
    t = _tabela_2_periodos()
    wb = openpyxl.load_workbook(io.BytesIO(nf_export.gerar_xlsx_conferencia(t, "Energia", "07/2026 a 08/2026", "a.xlsx, b.xlsx", "x")))
    assert wb.sheetnames[:2] == ["Resumo", "Resumo por período"]
    ws = wb["Conferência completa"]
    cab = [c.value for c in ws[nf_export.LINHA_CABECALHO]]
    assert cab[0] == "Período" and ws.max_row == nf_export.LINHA_CABECALHO + len(t)
    assert wb["Pendências"].max_row == nf_export.LINHA_CABECALHO + 3
    resumo = {r[0].value: r[1].value for r in wb["Resumo"].iter_rows(min_row=nf_export.LINHA_CABECALHO + 1)}
    assert resumo["Período de referência"] == "07/2026 a 08/2026" and resumo["Notas no manifesto"] == 3


def test_xlsx_de_um_periodo_continua_sem_a_aba_nova():
    import io
    import openpyxl
    import nf_export
    t = _tabela_2_periodos()
    t = t[t["periodo"] == "07/2026"]
    wb = openpyxl.load_workbook(io.BytesIO(nf_export.gerar_xlsx_conferencia(t, "Energia", "07/2026", None, "x")))
    assert "Resumo por período" not in wb.sheetnames


# ───────────── v0.46.1: padrao visual Enermais nas planilhas ─────────────
def _wb(empresa_codigo, t=None, **kw):
    import io
    import openpyxl
    t = _tabela_2_periodos() if t is None else t
    return openpyxl.load_workbook(io.BytesIO(
        nf_export.gerar_xlsx_conferencia(t, "Empresa X", "07/2026 a 08/2026", "a.xlsx", "x", empresa_codigo=empresa_codigo, **kw)))


def test_planilha_tem_logo_titulo_cores_da_marca_e_filtro_em_todas_as_abas():
    wb = _wb("ENERGIA")
    for nome in wb.sheetnames:
        assert len(wb[nome]._images) == 1, nome                      # logo em TODAS as abas
        assert wb[nome].sheet_view.showGridLines is False
    ws = wb["Conferência completa"]
    cab = ws.cell(row=nf_export.LINHA_CABECALHO, column=1)
    assert cab.fill.fgColor.rgb.endswith("171C60") and cab.font.bold
    assert ws.cell(row=3, column=1).fill.fgColor.rgb.endswith("F99D20")          # filete laranja
    assert ws.auto_filter.ref.startswith(f"A{nf_export.LINHA_CABECALHO}:")
    assert ws.freeze_panes == f"C{nf_export.LINHA_CABECALHO + 1}"
    assert ws.page_setup.orientation == "landscape" and ws.print_title_rows == f"${nf_export.LINHA_CABECALHO}:${nf_export.LINHA_CABECALHO}"


def test_planilha_status_legivel_com_cor_e_valores_formatados():
    wb = _wb("ENERGIA")
    ws = wb["Conferência completa"]
    cab = {c.value: c.column for c in ws[nf_export.LINHA_CABECALHO]}
    por_nota = {}
    for r in range(nf_export.LINHA_CABECALHO + 1, ws.max_row + 1):
        por_nota[ws.cell(row=r, column=cab["Nº da nota"]).value] = r
    r_ok, r_nao = por_nota["1"], por_nota["3"]
    assert ws.cell(row=r_ok, column=cab["Status"]).value == "Lançadas"
    assert ws.cell(row=r_ok, column=cab["Status"]).fill.fgColor.rgb.endswith(nf_export.COR_STATUS["LANCADA"])
    assert ws.cell(row=r_nao, column=cab["Status"]).value == "Não encontradas no Sienge"
    assert ws.cell(row=r_nao, column=cab["Status"]).fill.fgColor.rgb.endswith(nf_export.COR_STATUS["NAO_ENCONTRADA"])
    assert ws.cell(row=r_ok, column=cab["Valor (manifesto)"]).number_format == '"R$" #,##0.00'
    assert ws.cell(row=r_ok, column=cab["Emissão"]).number_format == "DD/MM/YYYY"
    assert ws.cell(row=r_ok, column=cab["Acompanhamento"]).value == "Pendente"
    assert ws.cell(row=r_ok, column=cab["CNPJ do fornecedor"]).value in ("1", None) or "." in str(ws.cell(row=r_ok, column=cab["CNPJ do fornecedor"]).value)


def test_cnpj_formatado_e_hora_em_brasilia_na_planilha():
    import datetime as dt
    t = _tabela_2_periodos()
    t["fornecedor_cnpj"] = "22417389000108"
    t["atualizado_em"] = dt.datetime(2026, 10, 4, 13, 1, 44, tzinfo=dt.timezone.utc)      # UTC do banco
    ws = _wb("ENERGIA", t)["Conferência completa"]
    cab = {c.value: c.column for c in ws[nf_export.LINHA_CABECALHO]}
    linha = nf_export.LINHA_CABECALHO + 1
    assert ws.cell(row=linha, column=cab["CNPJ do fornecedor"]).value == "22.417.389/0001-08"
    assert ws.cell(row=linha, column=cab["Atualizado em"]).value == dt.datetime(2026, 10, 4, 10, 1, 44)    # 13:01 UTC = 10:01 BRT


def test_smg_usa_paleta_e_logo_proprios_e_empresa_sem_logo_cai_no_do_grupo():
    ws = _wb("SMG")["Conferência completa"]
    assert ws.cell(row=nf_export.LINHA_CABECALHO, column=1).fill.fgColor.rgb.endswith("1D2954")
    assert ws.cell(row=3, column=1).fill.fgColor.rgb.endswith("95C94C")
    assert nf_export._bytes_logo("SMG") != nf_export._bytes_logo("ENERGIA")
    assert nf_export._bytes_logo("SETTE") == nf_export._bytes_logo("GRUPO")          # SETTE nao tem logo proprio
    assert len(_wb("SETTE")["Resumo"]._images) == 1


def test_sem_logo_a_planilha_sai_igual_sem_quebrar():
    from unittest.mock import patch
    with patch.object(nf_export, "_bytes_logo", return_value=None):
        wb = _wb("ENERGIA")
    assert len(wb["Resumo"]._images) == 0 and "Conferência completa" in wb.sheetnames


def test_planilha_de_pendencias_tem_so_pendencias_no_mesmo_padrao():
    import io
    import openpyxl
    t = _tabela_2_periodos()
    wb = openpyxl.load_workbook(io.BytesIO(nf_export.gerar_xlsx_pendencias(t, "Empresa X", "07/2026 a 08/2026", "ENERGIA")))
    assert wb.sheetnames == ["Pendências"] and len(wb["Pendências"]._images) == 1
    assert wb["Pendências"].max_row == nf_export.LINHA_CABECALHO + 3          # 3 linhas != LANCADA


def test_planilha_grande_gera_em_tempo_razoavel():
    import time
    import pandas as pd
    t = pd.concat([_tabela_2_periodos()] * 540, ignore_index=True)        # ~2160 linhas, como a Energia 01-08
    t0 = time.time()
    nf_export.gerar_xlsx_conferencia(t, "Empresa X", "01/2026 a 08/2026", empresa_codigo="ENERGIA")
    assert time.time() - t0 < 20
