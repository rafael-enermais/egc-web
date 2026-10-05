# -*- coding: utf-8 -*-
"""AppTest da tela Notas Fiscais (app/telas/7_Notas_Fiscais.py) -- 01/10/2026:
v0.45.0: tabela empresa x periodo (conferencia vigente), detalhe por periodo, download da planilha
COMPLETA, upload que reconfere a empresa inteira e "Atualizar agora" sem datas."""
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

VIG = [
    {"import_id": "id-novo", "empresa_codigo": "ENERGIA", "periodo_referencia": "07/2026", "arquivo_nome": "m_jul.xlsx",
     "enviado_em": datetime.datetime(2026, 10, 1, 9, 0), "atualizado_em": datetime.datetime(2026, 10, 1, 9, 0),
     "total_notas": 2, "total_lancadas": 1, "pendencias_notas": 1, "orfaos_sienge": 0, "total_pendencias": 1,
     "taxa": 0.5, "usuario": "x"},
    {"import_id": "id-velho", "empresa_codigo": "ENERGIA", "periodo_referencia": "06/2026", "arquivo_nome": "m_jun.xlsx",
     "enviado_em": datetime.datetime(2026, 9, 1, 9, 0), "atualizado_em": datetime.datetime(2026, 9, 1, 9, 0),
     "total_notas": 1, "total_lancadas": 1, "pendencias_notas": 0, "orfaos_sienge": 0, "total_pendencias": 0,
     "taxa": 1.0, "usuario": "x"},
]
HIST = [
    {"import_id": "id-novo", "empresa_codigo": "ENERGIA", "criado_em": datetime.datetime(2026, 10, 1, 9, 0),
     "periodo_referencia": "07/2026", "total_notas": 2, "total_lancadas": 1, "total_pendencias": 1,
     "arquivo_nome": "m_jul.xlsx", "usuario": "x", "vigente": True},
    {"import_id": "id-antigo", "empresa_codigo": "ENERGIA", "criado_em": datetime.datetime(2026, 9, 20, 9, 0),
     "periodo_referencia": "07/2026", "total_notas": 1, "total_lancadas": 0, "total_pendencias": 1,
     "arquivo_nome": "m_jul_v0.xlsx", "usuario": "x", "vigente": False},
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


def test_nf_tela_junta_todos_os_periodos_da_empresa_e_oferece_download_completo():
    with patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(nf_sienge, "ultima_sincronizacao", return_value=datetime.datetime(2026, 10, 1, 4, 0)), \
         patch.object(nf_sienge, "resumo_vigentes", return_value=VIG), \
         patch.object(nf_sienge, "listar_historico_importacoes", return_value=HIST), \
         patch.object(nf_sienge, "listar_envios", return_value=[]), \
         patch.object(nf_sienge, "listar_conciliacao", side_effect=lambda c, i: _tabela(i)), \
         patch.object(nf_sienge, "listar_orfaos_sienge", return_value=pd.DataFrame()), \
         patch.object(db, "registrar_evento", return_value=None):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert at.selectbox(key="nf_det_empresa").value == "ENERGIA"
        # padrao: TODOS os meses da empresa (cronologico) -- 2 notas por periodo mockado
        assert at.multiselect(key="nf_det_periodos_ENERGIA").value == ["06/2026", "07/2026"]
        m = {x.label: x.value for x in at.metric}
        assert m["Notas no manifesto"] == "4" and m["Pendências a tratar"] == "2"
        # tabela empresa x periodo: 1 linha por periodo vigente, com notas pendentes e Sienge sem nota separados
        _tab = at.dataframe[0].value
        assert list(_tab["Período"]) == ["07/2026", "06/2026"] and list(_tab["Pendências"]) == [1, 0]
        assert {"Notas pendentes", "Sienge sem nota"} <= set(_tab.columns)
        labels = [b.label for b in at.get("download_button")]
        assert any("COMPLETA" in l for l in labels), labels
        # detalhe tem a coluna Período e o titulo do Sienge
        df = at.dataframe[2].value if len(at.dataframe) > 2 else at.dataframe[1].value
        assert "Período" in df.columns and "Título Sienge" in df.columns and "32034" in set(df["Título Sienge"])
        # so' um mes marcado
        at.multiselect(key="nf_det_periodos_ENERGIA").set_value(["07/2026"]).run(timeout=30)
        assert not at.exception, at.exception
        assert {x.label: x.value for x in at.metric}["Notas no manifesto"] == "2"


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
         patch.object(nf_sienge, "resumo_vigentes", return_value=VIG), \
         patch.object(nf_sienge, "listar_historico_importacoes", return_value=HIST), \
         patch.object(nf_sienge, "listar_envios", return_value=[]), \
         patch.object(nf_sienge, "listar_conciliacao", side_effect=lambda c, i: _tabela_completa(i)), \
         patch.object(nf_sienge, "listar_orfaos_sienge", return_value=pd.DataFrame()), \
         patch.object(nf_sienge, "avisos_conciliacao", return_value=["AVISO DE COBERTURA TESTE"]), \
         patch.object(db, "registrar_evento", return_value=None):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        at.multiselect(key="nf_det_periodos_ENERGIA").set_value(["07/2026"]).run(timeout=30)
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
        assert len(at.dataframe[1].value) == 1


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


def _rodar(conteudo, empresa_idx=0, periodo="07/2026", vigentes_antes=None, reconferir=None, resumo_antes=None):
    import streamlit as st
    gravados = []
    ignorados = {}

    def _gravar(conn, empresa, periodo_, df, nome, usuario, lote_id=None):
        gravados.append(dict(empresa=empresa, periodo=periodo_, notas=list(df["Num"])))
        return f"imp-{len(gravados)}"

    def _gravar_ign(conn, import_id, empresa, periodo_, ign, usuario):
        ignorados[periodo_] = [] if ign is None else sorted(ign["numero_nota"])
        return 0

    with patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(st, "file_uploader", return_value=_ArquivoFake(conteudo)), \
         patch.object(nf_sienge, "ultima_sincronizacao", return_value=datetime.datetime(2026, 10, 1, 4, 0)), \
         patch.object(nf_sienge, "resumo_vigentes", return_value=resumo_antes or []), \
         patch.object(nf_sienge, "listar_vigentes", return_value=vigentes_antes or []), \
         patch.object(nf_sienge, "listar_conciliacao", return_value=pd.DataFrame()), \
         patch.object(nf_sienge, "listar_orfaos_sienge", return_value=pd.DataFrame()), \
         patch.object(nf_sienge, "listar_historico_importacoes", return_value=[]), \
         patch.object(nf_sienge, "gravar_manifesto", side_effect=_gravar), \
         patch.object(nf_sienge, "gravar_ignoradas", side_effect=_gravar_ign), \
         patch.object(nf_sienge, "reconferir_empresa",
                      side_effect=reconferir or (lambda c, e: {g["periodo"]: dict(total=2, lancadas=1, pendencias=1, orfaos_sienge=0)
                                                               for g in gravados})), \
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
    assert [s.value[:8] for s in at.success] == ["07/2026:", "08/2026:"]


def test_nf_reupload_do_mes_avisa_substituicao_e_mostra_outros_meses_que_mudaram():
    antes = [dict(VIG[0], import_id="x", empresa_codigo="ENERGIA", periodo_referencia="07/2026", arquivo_nome="velho.xlsx")]
    chamadas = []

    def _reconf(conn, empresa):
        chamadas.append(empresa)
        return {"07/2026": dict(total=2, lancadas=2, pendencias=0, orfaos_sienge=0),
                "06/2026": dict(total=1, lancadas=1, pendencias=0, orfaos_sienge=0)}

    # 06/2026 tinha 3 pendencias antes e agora 0 (o arquivo de julho resolveu notas de junho)
    at, gravados, _ = _rodar(_xlsx_bytes("47040664000148", canceladas=0), vigentes_antes=antes, reconferir=_reconf,
                             resumo_antes=[dict(VIG[1], total_pendencias=3), dict(VIG[0], total_pendencias=1)])
    assert not at.exception, at.exception
    assert chamadas == ["ENERGIA"]                                  # uma reconferencia da empresa inteira, nao uma por mes
    assert any("substituiu o arquivo anterior (velho.xlsx)" in x.value for x in at.success)
    assert any("06/2026: 3 → 0" in i.value for i in at.info)


def test_nf_atualizar_agora_nao_pede_datas_usa_365_dias_e_refaz_as_conferencias():
    janelas = []
    segredos = {"SIENGE_BASE_URL": "https://x", "SIENGE_USER": "u", "SIENGE_PASSWORD": "p"}

    def _sync(conn, base, u, p, ini, fim):
        janelas.append((ini, fim))
        return {"2026-10": 7}

    reconf = {"ENERGIA": {"07/2026": dict(total=2, lancadas=2, pendencias=0, orfaos_sienge=0)}}
    import streamlit as st
    with patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(st, "secrets", segredos), \
         patch.object(nf_sienge, "ultima_sincronizacao", return_value=datetime.datetime(2026, 10, 1, 4, 0)), \
         patch.object(nf_sienge, "resumo_vigentes", return_value=[dict(VIG[0], total_pendencias=1)]), \
         patch.object(nf_sienge, "listar_historico_importacoes", return_value=[]), \
         patch.object(nf_sienge, "listar_conciliacao", side_effect=lambda c, i: _tabela(i)), \
         patch.object(nf_sienge, "listar_orfaos_sienge", return_value=pd.DataFrame()), \
         patch.object(nf_sienge, "sincronizar_bills_por_mes", side_effect=_sync), \
         patch.object(nf_sienge, "sincronizar_creditores", return_value=3), \
         patch.object(nf_sienge, "reconferir_todas", return_value=reconf), \
         patch.object(db, "registrar_evento", return_value=None):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert len(at.get("date_input")) == 0                      # sem "De/Ate"
        at.button(key="nf_btn_sync").click().run(timeout=30)
    assert not at.exception, at.exception
    (ini, fim), = janelas
    assert (fim - ini).days == 365
    msg = " ".join(x.value for x in at.success)
    assert "7 título(s)" in msg and "07/2026: 1 → 0" in msg       # mostra o que mudou


def test_nf_empresa_e_periodo_vem_do_arquivo_sem_campos_pra_contadora():
    import streamlit as st
    import nf_parser
    from conexao import EMPRESAS_NF
    conteudo = _xlsx_bytes("47040664000148", canceladas=0, meses=("2026.07",))
    gravados = []

    def _gravar(conn, empresa, periodo_, df, nome, usuario, lote_id=None):
        gravados.append((empresa, periodo_))
        return "imp-1"

    with patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(st, "file_uploader", return_value=_ArquivoFake(conteudo)), \
         patch.object(nf_sienge, "ultima_sincronizacao", return_value=datetime.datetime(2026, 10, 1, 4, 0)), \
         patch.object(nf_sienge, "resumo_vigentes", return_value=[]), \
         patch.object(nf_sienge, "listar_vigentes", return_value=[]), \
         patch.object(nf_sienge, "listar_conciliacao", return_value=pd.DataFrame()), \
         patch.object(nf_sienge, "listar_orfaos_sienge", return_value=pd.DataFrame()), \
         patch.object(nf_sienge, "listar_historico_importacoes", return_value=[]), \
         patch.object(nf_sienge, "gravar_manifesto", side_effect=_gravar), \
         patch.object(nf_sienge, "gravar_ignoradas", return_value=0), \
         patch.object(nf_sienge, "reconferir_empresa", return_value={"07/2026": dict(total=2, lancadas=1, pendencias=1, orfaos_sienge=0)}), \
         patch.object(db, "registrar_evento", return_value=None):
        at = AppTest.from_file(PAGE)
        info = nf_parser.analisar_upload(conteudo, EMPRESAS_NF)
        info["arquivo"] = "m.xlsx"
        at.session_state["nf_upload_info"] = info
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert not any(sb.key == "nf_empresa_sel" for sb in at.selectbox)        # sem escolher empresa
        assert not any(t.key == "nf_periodo_ref" for t in at.text_input)         # sem digitar periodo
        at.button(key="nf_btn_rodar").click().run(timeout=30)
    assert not at.exception, at.exception
    assert gravados == [("ENERGIA", "07/2026")]


ENVIO_ATIVO = {"envio_id": "L:abc", "empresa_codigo": "ENERGIA", "arquivo_nome": "errado.xlsx", "usuario": "t",
               "enviado_em": datetime.datetime(2026, 10, 2, 9, 0), "import_ids": ["id-novo"], "periodos": ["07/2026"],
               "total_notas": 2, "arquivado": False, "arquivado_parcial": False, "arquivado_em": None,
               "arquivado_por": None, "arquivado_motivo": None, "periodos_vigentes": ["07/2026"], "periodos_substituidos": []}
ENVIO_ARQ = {**ENVIO_ATIVO, "envio_id": "L:def", "arquivo_nome": "velho.xlsx", "import_ids": ["id-v"], "arquivado": True,
             "arquivado_em": datetime.datetime(2026, 10, 3, 10, 0), "arquivado_por": "ana", "arquivado_motivo": "empresa errada"}


def _tela_arquivar(envios, **extra):
    chamadas = {"arquivar": [], "restaurar": []}

    def _arq(conn, ids, usuario, motivo=None):
        chamadas["arquivar"].append((list(ids), usuario, motivo))
        return {"empresas": ["ENERGIA"], "afetados": 1, "mudancas": [
            {"empresa_codigo": "ENERGIA", "periodo_referencia": "07/2026", "antes": "errado.xlsx", "depois": "certo.xlsx"}]}

    def _rest(conn, ids, usuario):
        chamadas["restaurar"].append((list(ids), usuario))
        return {}

    patches = [
        patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"),
        patch.object(conexao, "get_conn", return_value=None),
        patch.object(nf_sienge, "ultima_sincronizacao", return_value=datetime.datetime(2026, 10, 1, 4, 0)),
        patch.object(nf_sienge, "resumo_vigentes", return_value=VIG),
        patch.object(nf_sienge, "listar_historico_importacoes", return_value=HIST),
        patch.object(nf_sienge, "listar_envios", return_value=envios),
        patch.object(nf_sienge, "tem_arquivamento", return_value=extra.get("disponivel", True)),
        patch.object(nf_sienge, "previa_arquivamento", return_value=[
            {"empresa_codigo": "ENERGIA", "periodo_referencia": "07/2026", "import_id": "id-novo", "passa_a_valer": "certo.xlsx"}]),
        patch.object(nf_sienge, "arquivar_envio", side_effect=extra.get("arquivar", _arq)),
        patch.object(nf_sienge, "restaurar_envio", side_effect=_rest),
        patch.object(nf_sienge, "listar_conciliacao", side_effect=lambda c, i: _tabela(i)),
        patch.object(nf_sienge, "listar_orfaos_sienge", return_value=pd.DataFrame()),
        patch.object(db, "registrar_evento", return_value=None),
    ]
    return patches, chamadas


def _entrar(patches):
    from contextlib import ExitStack
    st_ = ExitStack()
    for p_ in patches:
        st_.enter_context(p_)
    return st_


def test_nf_arquivar_exige_confirmacao_mostra_previa_e_chama_arquivar_envio():
    patches, chamadas = _tela_arquivar([ENVIO_ATIVO, ENVIO_ARQ])
    with _entrar(patches):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert at.button(key="nf_btn_arquivar").disabled is True                      # sem confirmar, nao arquiva
        assert any("volta a valer o envio anterior (certo.xlsx)" in c.value for c in at.caption)
        at.checkbox(key="nf_arq_ok_L:abc").check()
        at.text_input(key="nf_arq_motivo_L:abc").set_value("arquivo errado")
        at.run(timeout=30)
        at.button(key="nf_btn_arquivar").click().run(timeout=30)
        assert not at.exception, at.exception
        assert chamadas["arquivar"] == [(["id-novo"], "t@enermais.com.br", "arquivo errado")]
        msg = " ".join(x.value for x in at.success)
        assert "Envio arquivado: errado.xlsx" in msg and "Nada foi apagado" in msg and "voltou a valer certo.xlsx" in msg


def test_nf_restaurar_chama_restaurar_envio():
    patches, chamadas = _tela_arquivar([ENVIO_ATIVO, ENVIO_ARQ])
    with _entrar(patches):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        at.button(key="nf_btn_restaurar").click().run(timeout=30)
        assert not at.exception, at.exception
        assert chamadas["restaurar"] == [(["id-v"], "t@enermais.com.br")]
        assert any("Envio restaurado: velho.xlsx" in x.value for x in at.success)


def test_nf_arquivar_sem_bloco_23_avisa_em_vez_de_quebrar():
    patches, _ = _tela_arquivar([ENVIO_ATIVO], disponivel=False)
    with _entrar(patches):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert any("bloco 23" in i.value for i in at.info)
        assert not any(b.key == "nf_btn_arquivar" for b in at.button)


def test_nf_erro_ao_arquivar_aparece_e_vai_para_o_log():
    registrados = []

    def _falha(conn, ids, usuario, motivo=None):
        raise RuntimeError("banco caiu")

    patches, _ = _tela_arquivar([ENVIO_ATIVO], arquivar=_falha)
    patches[-1] = patch.object(db, "registrar_evento", side_effect=lambda *a, **k: registrados.append((a, k)))
    with _entrar(patches):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.checkbox(key="nf_arq_ok_L:abc").check().run(timeout=30)
        at.button(key="nf_btn_arquivar").click().run(timeout=30)
        assert not at.exception, at.exception
        assert any("Não consegui arquivar: banco caiu" in e.value for e in at.error)
    assert any(a[3] == "Falha ao arquivar envio" and a[2] == "ERRO" for a, _k in registrados), registrados


def test_nf_log_de_eventos_aparece_na_tela_e_filtra_so_erros():
    chamadas = []
    EV = [{"id": 1, "origem": "notas_fiscais", "nivel": "ERRO", "mensagem": "Falha ao arquivar envio", "detalhe": "banco caiu",
           "empresa_codigo": "ENERGIA", "periodo": None, "usuario": "ana",
           "criado_em": datetime.datetime(2026, 10, 5, 12, 0, tzinfo=datetime.timezone.utc)}]

    def _ev(conn, limite=100, nivel=None, origem=None):
        chamadas.append((nivel, origem))
        return EV

    patches, _ = _tela_arquivar([])
    with _entrar(patches), patch.object(db, "listar_eventos_recentes", side_effect=_ev):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert chamadas[-1] == (None, "notas_fiscais")
        at.checkbox(key="nf_log_so_erros").check().run(timeout=30)
        assert chamadas[-1] == ("ERRO", "notas_fiscais")
        assert any("Falha ao arquivar envio" in str(d.value.to_dict("records")) for d in at.dataframe)


def _tela_anotacao(disponivel=True, salvar=None):
    chamadas = {"salvar": []}

    def _salvar(conn, emp, ref, texto, usuario):
        chamadas["salvar"].append((emp, ref, texto, usuario))

    patches = [
        patch.object(auth, "usuario_atual", return_value="t@enermais.com.br"),
        patch.object(conexao, "get_conn", return_value=None),
        patch.object(nf_sienge, "ultima_sincronizacao", return_value=datetime.datetime(2026, 10, 1, 4, 0)),
        patch.object(nf_sienge, "resumo_vigentes", return_value=VIG),
        patch.object(nf_sienge, "listar_historico_importacoes", return_value=HIST),
        patch.object(nf_sienge, "listar_envios", return_value=[]),
        patch.object(nf_sienge, "tem_anotacao", return_value=disponivel),
        patch.object(nf_sienge, "carregar_anotacoes", return_value={}),
        patch.object(nf_sienge, "salvar_anotacao", side_effect=salvar or _salvar),
        patch.object(nf_sienge, "listar_conciliacao", side_effect=lambda c, i: _tabela(i)),
        patch.object(nf_sienge, "listar_orfaos_sienge", return_value=pd.DataFrame()),
        patch.object(db, "registrar_evento", return_value=None),
    ]
    return patches, chamadas


def test_nf_anotar_pendencia_chama_salvar_anotacao_com_modelo_e_complemento():
    patches, chamadas = _tela_anotacao()
    with _entrar(patches):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        at.selectbox(key="nf_anot_modelo").select("Aguardando o fornecedor reemitir/corrigir a nota")
        at.text_input(key="nf_anot_texto").set_value("prometeu até sexta")
        at.run(timeout=30)
        at.button(key="nf_btn_salvar_anot").click().run(timeout=30)
        assert not at.exception, at.exception
        assert len(chamadas["salvar"]) == 1
        emp, ref, texto, usuario = chamadas["salvar"][0]
        assert emp == "ENERGIA" and ref.startswith("ENERGIA|") and usuario == "t@enermais.com.br"
        assert texto == "Aguardando o fornecedor reemitir/corrigir a nota — prometeu até sexta"


def test_nf_anotar_sem_texto_nem_modelo_avisa_e_nao_grava():
    patches, chamadas = _tela_anotacao()
    with _entrar(patches):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.button(key="nf_btn_salvar_anot").click().run(timeout=30)
        assert not at.exception, at.exception
        assert chamadas["salvar"] == [] and any("Escolha um modelo" in w.value for w in at.warning)


def test_nf_anotar_sem_bloco_24_explica_em_vez_de_quebrar():
    patches, _ = _tela_anotacao(disponivel=False)
    with _entrar(patches):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert any("BLOCO 24" in i.value for i in at.info)
        assert not any(b.key == "nf_btn_salvar_anot" for b in at.button)
