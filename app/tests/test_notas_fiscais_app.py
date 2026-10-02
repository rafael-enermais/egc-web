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


def _tabela_completa(import_id):
    base = dict(cfop="5102", data_emissao=datetime.date(2026, 7, 1), valor=10.0, fornecedor_nome="F",
                fornecedor_cnpj="1", confianca=None, sienge_bill_id=None, sienge_documento=None, sienge_valor=None,
                observacao=None, pendencia_status="PENDENTE",
                atualizado_em=datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc))
    linhas = []
    for i, st_ in enumerate(["LANCADA", "LANCADA", "LANCADA_OUTRA_EMPRESA", "VALOR_DIVERGENTE",
                             "NUMERO_DIVERGENTE", "NAO_ENCONTRADA", "NAO_ENCONTRADA"]):
        linhas.append({**base, "numero_nota": str(100 + i), "status": st_, "registro_id": i, "origem": "MANIFESTO"})
    for j in range(2):
        linhas.append({**base, "numero_nota": f"[Sienge] nº {j}", "status": "SIENGE_SEM_MANIFESTO",
                       "registro_id": 50 + j, "origem": "SIENGE_ORFAO"})
    return pd.DataFrame(linhas)


def test_nf_tela_kpis_por_situacao_filtro_e_avisos():
    with patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(nf_sienge, "ultima_sincronizacao", return_value=datetime.datetime(2026, 10, 1, 4, 0)), \
         patch.object(nf_sienge, "listar_historico_importacoes", return_value=HIST), \
         patch.object(nf_sienge, "listar_conciliacao", side_effect=lambda c, i: _tabela_completa(i)), \
         patch.object(nf_sienge, "listar_orfaos_sienge", return_value=pd.DataFrame()), \
         patch.object(nf_sienge, "avisos_conciliacao", return_value=["AVISO DE COBERTURA TESTE"]), \
         patch.object(db, "registrar_evento", return_value=None):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        m = {x.label: x.value for x in at.metric}
        assert m["Notas no manifesto"] == "7"
        assert m["Pendências a tratar"] == "7"          # 5 notas pendentes + 2 Sienge sem manifesto
        assert (m["Lançadas"], m["Lançadas em outra empresa"], m["Valor divergente"], m["Número divergente"],
                m["Não encontradas"], m["Sienge sem manifesto"]) == ("2", "1", "1", "1", "2", "2")
        assert any("AVISO DE COBERTURA TESTE" in w.value for w in at.warning)
        # filtro por situacao: so' "Lançadas em outra empresa" -> 1 linha
        at.multiselect(key="nf_filtro_status").set_value(["LANCADA_OUTRA_EMPRESA"]).run(timeout=30)
        assert not at.exception, at.exception
        assert len(at.dataframe[0].value) == 1


class _ArquivoFake:
    def __init__(self, conteudo, nome="m.xlsx"):
        self._c, self.name, self.size = conteudo, nome, len(conteudo)

    def getvalue(self):
        return self._c


def _xlsx_bytes(filial, canceladas=1, entrada=False, meses=("2026.07",)):
    import io
    linhas = []
    for k, am in enumerate(meses):
        d = f"{am}.0{k + 6}"
        linhas.append([f"{1 + k * 10}", "NF-e", "Saída", d, "10", "5102", "A", "07393522000140", None, "Autorizado", filial, am])
        linhas.append([f"{2 + k * 10}", "NF-e", "Saída", d, "20", "5102", "A", "07393522000140", None, "Autorizado", filial, am])
    for k in range(canceladas):
        linhas.append([str(90 + k), "NF-e", "Saída", f"{meses[0]}.08", "30", "5102", "A", "07393522000140", "X",
                       "Cancelamento de NF-e homologado", filial, meses[0]])
    if entrada:
        linhas.append(["70", "NF-e", "Entrada", f"{meses[0]}.09", "40", "2202", "A", "07393522000140", None,
                       "Autorizado", filial, meses[0]])
    buf = io.BytesIO()
    pd.DataFrame(linhas, columns=["Num", "Tipo", "TipoDoc", "DtEmi", "Valor", "CFOP", "Emissor Nome", "Emissor CNPJ/CPF",
                                  "Can", "Status", "Filial", "Ano-Mês"]).to_excel(buf, index=False)
    return buf.getvalue()


def _rodar(conteudo, empresa_idx=0, periodo="07/2026"):
    import streamlit as st
    gravados = []
    ignorados = {}

    def _gravar(conn, empresa, periodo_, df, nome, usuario):
        gravados.append(dict(empresa=empresa, periodo=periodo_, notas=list(df["Num"])))
        return f"imp-{len(gravados)}"

    def _gravar_ign(conn, import_id, empresa, periodo_, ign, usuario):
        ignorados[periodo_] = [] if ign is None else sorted(ign["numero_nota"])
        return 0

    with patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(st, "file_uploader", return_value=_ArquivoFake(conteudo)), \
         patch.object(nf_sienge, "ultima_sincronizacao", return_value=datetime.datetime(2026, 10, 1, 4, 0)), \
         patch.object(nf_sienge, "listar_historico_importacoes", return_value=[]), \
         patch.object(nf_sienge, "gravar_manifesto", side_effect=_gravar), \
         patch.object(nf_sienge, "gravar_ignoradas", side_effect=_gravar_ign), \
         patch.object(nf_sienge, "conciliar_import", return_value=dict(total=2, lancadas=1, pendencias=1, orfaos_sienge=0)), \
         patch.object(nf_sienge, "gravar_historico_import", return_value=None), \
         patch.object(db, "registrar_evento", return_value=None):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.selectbox(key="nf_empresa_sel").set_value(empresa_idx)
        at.text_input(key="nf_periodo_ref").set_value(periodo)
        at.button(key="nf_btn_rodar").click().run(timeout=30)
    return at, gravados, ignorados


def test_nf_rodar_deixa_cancelada_e_entrada_fora_e_avisa():
    at, gravados, ignorados = _rodar(_xlsx_bytes("47040664000148", canceladas=1, entrada=True))   # Energia = 1a da lista
    assert not at.exception, at.exception
    assert [g["notas"] for g in gravados] == [["1", "2"]]   # cancelada e Entrada nao entram na conferencia
    assert ignorados["07/2026"] == ["70", "90"]              # mas ficam registradas pra conferir
    assert any("1 cancelada(s) e 1 de Entrada" in i.value for i in at.info)


def test_nf_rodar_bloqueia_arquivo_de_outra_empresa():
    at, gravados, _ = _rodar(_xlsx_bytes("55244465000180"))   # Filial = Construtora, empresa selecionada = Energia
    assert not at.exception, at.exception
    assert gravados == []                                  # nada gravado na empresa errada
    assert any("Construtora" in e.value and "Selecione a empresa certa" in e.value for e in at.error)


def test_nf_rodar_arquivo_com_2_meses_vira_2_conferencias_e_aceita_sette():
    from conexao import EMPRESAS_NF
    idx_sette = [c for c, _n, _c in EMPRESAS_NF].index("SETTE")
    at, gravados, _ = _rodar(_xlsx_bytes("44914462000190", canceladas=0, meses=("2026.07", "2026.08")),
                             empresa_idx=idx_sette, periodo="")      # campo vazio: o arquivo traz os 2 meses
    assert not at.exception, at.exception
    assert [(g["empresa"], g["periodo"], g["notas"]) for g in gravados] == [
        ("SETTE", "07/2026", ["1", "2"]), ("SETTE", "08/2026", ["11", "12"])]
    assert len([s for s in at.success if "concluída" in s.value]) == 2
