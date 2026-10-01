# -*- coding: utf-8 -*-
"""
Testes de INTEGRACAO contra um Postgres REAL (v0.40.0).

Os testes anteriores mockavam db.* / o conn; os PDFs de teste de
01/10/2026 (Energia "trimestral" com conteudo semestral, Grupo somando
Energia semestral + demais trimestrais, Evolucao com duas colunas
identicas) passaram por todos eles. Aqui roda o caminho de producao de
verdade -- psycopg2 -> SQL real (schema.sql aplicado por inteiro, com o
indice unico por granularidade do bloco 17) -> db.* -> indicadores ->
dict -> PDF -- com o oraculo numerico conferido nos dados do Supabase.

Pula (skip) sozinho quando nao ha' Postgres disponivel (ver
tests/pg_support.py: EGC_TEST_DATABASE_URL ou initdb/pg_ctl instalados).

CONCLUSAO REPRODUZIDA (ver test_reproducao_exata_dos_pdfs_de_01_10_2026):
com os documentos corretamente rotulados o codigo da v0.39.0 ja' calcula
certo; os 4 PDFs errados saem, ate' a ultima casa decimal, quando o
documento ENERGIA 06/2026 rotulado 'trimestral' contem as linhas do
semestral. O motor confiava cegamente no rotulo -- agora desconfia
(validar_cobertura_documento / _avisar_documentos_identicos).
"""
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel (EGC_TEST_DATABASE_URL ou initdb/pg_ctl)"),
]

import dados_relatorio_comentado as drc  # noqa: E402
import db  # noqa: E402
import gerador_relatorio_comentado as g  # noqa: E402
import gerador_relatorio_comparativo as gc  # noqa: E402
import selecao_periodos as sp  # noqa: E402

P26 = pg.P_2026
P23 = pg.P_2023
P24 = pg.P_2024
TODAS = pg.TODAS


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    pg.semear_cenario(c)
    yield c
    c.close()


def _rel(conn, emp, gran, periodo=P26, **kw):
    return drc.montar_dados_relatorio(conn, emp, periodo, periodo_label="t", granularidade=gran, **kw)[0]


# ───────────────────────── banco e schema de verdade ─────────────────────────
def test_schema_real_permite_trimestral_e_semestral_ativos_no_mesmo_periodo_fim(conn):
    det = db.listar_periodos_detalhado(conn, "ENERGIA")
    assert {(d["periodo"], d["granularidade"]) for d in det} == {
        (P23, "trimestral"), (P24, "anual"), (P26, "trimestral"), (P26, "semestral"),
    }
    # e a trava do bloco 17 continua valendo DENTRO da mesma granularidade
    import psycopg2
    with pytest.raises(psycopg2.errors.UniqueViolation):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO egc.lancamentos (empresa_codigo,tipo,periodo,grupo,conta,valor,granularidade) "
                "VALUES ('ENERGIA','DRE',%s,'RESULTADO','LUCRO BRUTO',1,'trimestral')", (P26,))


def test_so_ha_par_completo_quando_tem_bp_e_dre(conn):
    # BP sem DRE no mesmo (periodo, granularidade): existe pra listar_periodos_detalhado, NAO pra o seletor
    pg.gravar_documento(conn, "ENERGIA", datetime.date(2025, 12, 31), "semestral", bp=pg._bp(*pg.CENARIO[("ENERGIA", P26, "semestral")]["bp"]))
    det = {(d["periodo"], d["granularidade"]) for d in db.listar_periodos_detalhado(conn, "ENERGIA")}
    completos = {(d["periodo"], d["granularidade"]) for d in db.listar_periodos_completos_detalhado(conn, "ENERGIA")}
    assert (datetime.date(2025, 12, 31), "semestral") in det
    assert (datetime.date(2025, 12, 31), "semestral") not in completos
    assert completos == {(P23, "trimestral"), (P24, "anual"), (P26, "trimestral"), (P26, "semestral")}


# ───────────────────────── Modelo A, 1 empresa ─────────────────────────
@pytest.mark.parametrize("gran", ["trimestral", "semestral"])
def test_energia_cada_base_traz_so_os_numeros_da_propria_base(conn, gran):
    d = _rel(conn, "ENERGIA", gran)
    esp = pg.ESPERADO[("ENERGIA", gran)]
    assert d["granularidade"] == gran
    assert d["receita_liquida"] == pytest.approx(esp["rec"], abs=0.01)
    assert d["ebitda"] == pytest.approx(esp["ebitda"], abs=0.01)
    assert d["resultado_liquido"] == pytest.approx(esp["ll"], abs=0.01)
    assert d["avisos"] == []


def test_periodo_anterior_e_da_mesma_base_e_imediato(conn):
    # Energia 2T/2026 trimestral: o unico trimestral anterior e' 12/2023 (30 meses antes) --
    # NAO e' o periodo imediatamente anterior -> sem comparacao, texto de fallback, sem inventar +902%.
    d = _rel(conn, "ENERGIA", "trimestral")
    assert d["periodo_anterior"] is None
    assert d["periodo_anterior_ignorado"] == P23
    assert "sem período anterior disponível" in d["complemento_receita"]
    assert "%" not in d["complemento_receita"]
    # semestral: nao existe semestral anterior (o trimestral 12/2023 e o anual 12/2024 nao contam)
    ds = _rel(conn, "ENERGIA", "semestral")
    assert ds["periodo_anterior"] is None and ds["periodo_anterior_ignorado"] is None
    assert "sem período anterior disponível" in ds["complemento_ebitda"]


def test_com_trimestre_anterior_adjacente_compara_com_ele(conn):
    pg.semear_trimestre_anterior(conn)
    d = _rel(conn, "ENERGIA", "trimestral")
    assert d["periodo_anterior"] == pg.P_MAR_2026
    # receita 17.380.604,41 vs 15.970.242,39 = +8,8%; EBITDA -847.960,37 vs 1.572.935,68
    assert "+8.8%" in d["complemento_receita"], d["complemento_receita"]
    assert "-153.9%" in d["complemento_ebitda"], d["complemento_ebitda"]
    # o semestral continua sem anterior (o 1T trimestral nao serve de base)
    assert _rel(conn, "ENERGIA", "semestral")["periodo_anterior"] is None


def test_ranking_de_despesas_vem_do_documento_certo(conn):
    dt, ds = _rel(conn, "ENERGIA", "trimestral"), _rel(conn, "ENERGIA", "semestral")
    assert round(sum(i[1] for i in dt["despesas_admin_itens"]), 2) == pytest.approx(16_501_467.00, abs=0.05)
    assert round(sum(i[1] for i in ds["despesas_admin_itens"]), 2) == pytest.approx(29_339_591.64, abs=0.05)


# ───────────────────────── Modelo A, Grupo ─────────────────────────
def test_grupo_6_cnpjs_trimestral_fecha_o_oraculo(conn):
    d = _rel(conn, TODAS, "trimestral")
    esp = pg.ESPERADO[("GRUPO", "trimestral")]
    assert d["receita_liquida"] == pytest.approx(esp["rec"], abs=0.01)
    assert d["ebitda"] == pytest.approx(esp["ebitda"], abs=0.01)  # -16.268,75
    assert d["resultado_liquido"] == pytest.approx(esp["ll"], abs=0.01)
    assert d["empresas_codigos"] == TODAS


def test_grupo_semestral_erra_claramente_pois_so_a_energia_tem(conn):
    with pytest.raises(ValueError) as exc:
        _rel(conn, TODAS, "semestral")
    assert "semestral" in str(exc.value) and "SMG" in str(exc.value)


def test_grupo_nao_mistura_a_energia_semestral_no_historico_consolidado(conn):
    hist = drc._consolidar_historico(conn, TODAS, "DRE", granularidade="trimestral")
    assert {r["granularidade"] for r in hist} == {"trimestral"}
    assert pg.P_2023 not in {r["periodo"] for r in hist}  # periodos incompletos (so' Energia) nao entram


# ───────────────────────── Modelo B (Evolucao) ─────────────────────────
def test_evolucao_energia_4_colunas_trimestral_e_semestral_distintas(conn):
    dados = drc.montar_dados_relatorio_comparativo(
        conn, ["ENERGIA"], [P23, P24, P26, P26], ["4T/2023", "2024", "2T/2026", "1S/2026"], "x",
        granularidades=["trimestral", "anual", "trimestral", "semestral"],
    )
    fluxo = {k["label"]: k["valores"] for k in dados["kpis_fluxo"]}
    assert fluxo["Receita Operacional Líquida"] == pytest.approx([3_327_615.35, 15_565_767.10, 17_380_604.41, 33_350_846.80], abs=0.01)
    assert fluxo["EBITDA"] == pytest.approx([1_299_749.49, 3_697_980.24, -847_960.37, 724_975.31], abs=0.01)
    assert fluxo["Resultado Líquido"] == pytest.approx([1_285_930.76, 3_432_098.83, -1_804_157.20, -1_337_674.90], abs=0.01)
    assert dados["granularidades"] == ["trimestral", "anual", "trimestral", "semestral"]


def test_evolucao_rejeita_item_repetido_e_rotulos_repetidos(conn):
    with pytest.raises(ValueError, match="mais de uma vez"):
        drc.montar_dados_relatorio_comparativo(conn, ["ENERGIA"], [P26, P26], ["a", "b"], "x", granularidades=["trimestral", "trimestral"])
    with pytest.raises(ValueError, match="rótulos de coluna repetidos"):
        drc.montar_dados_relatorio_comparativo(
            conn, ["ENERGIA"], [P26, P26], ["06/2026", "06/2026"], "x", granularidades=["trimestral", "semestral"])


def test_evolucao_grupo_energia_const_em_dois_trimestres(conn):
    pg.semear_trimestre_anterior(conn)
    dados = drc.montar_dados_relatorio_comparativo(
        conn, ["ENERGIA", "CONST"], [pg.P_MAR_2026, P26], ["1T/2026", "2T/2026"], "x", granularidades=["trimestral", "trimestral"],
    )
    fluxo = {k["label"]: k["valores"] for k in dados["kpis_fluxo"]}
    assert fluxo["EBITDA"] == pytest.approx([1_572_935.68 + 1_000_000.00, -847_960.37 + 2_319_740.04], abs=0.01)


def test_evolucao_grupo_com_base_que_so_a_energia_tem_falha_claro(conn):
    with pytest.raises(ValueError) as exc:
        drc.montar_dados_relatorio_comparativo(
            conn, ["ENERGIA", "CONST"], [P26, P26], ["2T", "1S"], "x", granularidades=["trimestral", "semestral"])
    assert "CONST" in str(exc.value) and "semestral" in str(exc.value)


# ───────────── Reproducao dos 4 PDFs errados de 01/10/2026 ─────────────
def test_reproducao_exata_dos_pdfs_de_01_10_2026(conn):
    """O documento ENERGIA 06/2026 rotulado 'trimestral' com as linhas do
    SEMESTRAL (import legado, sem periodo_inicio) reproduz, a ultima casa,
    todos os numeros dos PDFs errados. O motor nao tem como saber pelos
    numeros -- por isso avisa (documentos identicos) em vez de calar."""
    pg.rotular_errado_energia_trimestral_com_dados_do_semestral(conn, com_periodo_inicio=False)

    # 1) Demonstrativo_ENERGIA_20260630_trimestral.pdf
    d1 = _rel(conn, "ENERGIA", "trimestral")
    assert d1["receita_liquida"] == pytest.approx(33_350_846.80, abs=0.01)
    assert d1["ebitda"] == pytest.approx(724_975.31, abs=0.01)
    assert d1["resultado_liquido"] == pytest.approx(-1_337_674.90, abs=0.01)
    assert any("idênticos" in a for a in d1["avisos"]), d1["avisos"]

    # 2) Demonstrativo_GRUPO_20260630_trimestral.pdf
    d2 = _rel(conn, TODAS, "trimestral")
    assert d2["receita_liquida"] == pytest.approx(39_844_268.84, abs=0.01)
    assert d2["ebitda"] == pytest.approx(1_556_666.93, abs=0.01)
    assert d2["resultado_liquido"] == pytest.approx(-760_008.80, abs=0.01)
    assert any("ENERGIA" in a and "idênticos" in a for a in d2["avisos"])

    # 3) Evolucao_ENERGIA_202312_202606: as duas colunas de 06/2026 identicas
    d3 = drc.montar_dados_relatorio_comparativo(
        conn, ["ENERGIA"], [P23, P24, P26, P26], ["a", "b", "c", "d"], "x",
        granularidades=["trimestral", "anual", "trimestral", "semestral"],
    )
    rec = [k for k in d3["kpis_fluxo"] if k["label"] == "Receita Operacional Líquida"][0]["valores"]
    assert rec[2] == rec[3] == pytest.approx(33_350_846.80, abs=0.01)

    # 4) Evolucao_GRUPO (ENERGIA + CONST): EBITDA 3.044.715,35
    d4 = drc.montar_dados_relatorio(conn, ["ENERGIA", "CONST"], P26, periodo_label="t", granularidade="trimestral")[0]
    assert d4["ebitda"] == pytest.approx(724_975.31 + 2_319_740.04, abs=0.01)
    assert d4["receita_liquida"] == pytest.approx(33_350_846.80 + 6_004_883.69, abs=0.01)


def test_documento_com_rotulo_incoerente_com_o_intervalo_do_pdf_e_bloqueado(conn):
    # mesma situacao, mas com o periodo_inicio (01/01/2026) que o PDF declarou:
    # 6 meses nao e' trimestral -> o relatorio NAO e' gerado.
    pg.rotular_errado_energia_trimestral_com_dados_do_semestral(conn, com_periodo_inicio=True)
    with pytest.raises(ValueError) as exc:
        _rel(conn, "ENERGIA", "trimestral")
    msg = str(exc.value)
    assert "inconsistente" in msg and "'trimestral'" in msg and "'semestral'" in msg and "01/01/2026" in msg
    # grupo e evolucao herdam a trava
    with pytest.raises(ValueError, match="inconsistente"):
        _rel(conn, TODAS, "trimestral")
    with pytest.raises(ValueError, match="inconsistente"):
        drc.montar_dados_relatorio_comparativo(
            conn, ["ENERGIA"], [P26, P26], ["2T", "1S"], "x", granularidades=["trimestral", "semestral"])
    # o semestral (rotulo correto) segue gerando normalmente
    assert _rel(conn, "ENERGIA", "semestral")["receita_liquida"] == pytest.approx(33_350_846.80, abs=0.01)


def test_intervalos_declarados_do_cenario_correto_sao_coerentes(conn):
    # sanidade: o cenario correto (periodo_inicio gravado coerente) nunca dispara a trava
    for (emp, per, gran) in pg.CENARIO:
        drc.validar_cobertura_documento(conn, emp, per, gran)


# ───────────── PDF de verdade, texto conferido ─────────────
@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext indisponivel")
def test_pdf_real_da_energia_trimestral_so_tem_numeros_do_trimestral(conn, tmp_path):
    d, incluir = drc.montar_dados_relatorio(conn, "ENERGIA", P26, periodo_label="2T/2026", granularidade="trimestral")
    caminho = str(tmp_path / "t.pdf")
    g.gerar_pdf_completo(d, caminho, incluir_pagina_resultado=incluir)
    txt = subprocess.run(["pdftotext", "-layout", caminho, "-"], capture_output=True, text=True).stdout
    assert "17.380.604,41" in txt and "847.960,37" in txt
    assert "33.350.846,80" not in txt and "724.975,31" not in txt
    assert "+902" not in txt


# ───────────── Tela 8 (AppTest) contra o banco real ─────────────
def _app(conn):
    from streamlit.testing.v1 import AppTest
    import auth
    import conexao
    page = str(Path(__file__).resolve().parent.parent / "telas" / "8_Relatorio_Comentado.py")
    patches = [
        patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"),
        patch.object(conexao, "get_conn", return_value=conn),
        patch.object(db, "registrar_relatorio_gerado"),
    ]
    for p in patches:
        p.start()
    at = AppTest.from_file(page)
    at.run(timeout=60)
    return at, patches


def _parar(patches):
    for p in patches:
        p.stop()


def _ms(at):
    return next(m for m in at.multiselect if m.key and m.key.startswith("relatorio_periodos_multi"))


def _sb(at):
    return next(s for s in at.selectbox if s.key and s.key.startswith("relatorio_periodo_sel"))


def test_tela_periodo_unico_lista_pares_explicitos_e_gera_a_base_escolhida(conn):
    capturado = {}
    real = g.gerar_pdf_completo

    def espiao(dados, caminho, *a, **k):
        capturado["dados"], capturado["caminho"] = dados, caminho
        return real(dados, caminho, *a, **k)

    with patch.object(g, "gerar_pdf_completo", espiao):
        at, ps = _app(conn)
        try:
            sb = _sb(at)
            assert sb.options == [
                "31/12/2023 — Trimestral", "31/12/2024 — Anual",
                "30/06/2026 — Semestral", "30/06/2026 — Trimestral",
            ]
            sb.set_value((P26, "trimestral")).run(timeout=60)
            next(b for b in at.button if b.label == "Gerar relatório").click().run(timeout=60)
            assert not at.exception, at.exception
            assert capturado["dados"]["receita_liquida"] == pytest.approx(17_380_604.41, abs=0.01)
            assert capturado["dados"]["granularidade"] == "trimestral"
            assert capturado["caminho"].endswith("Demonstrativo_ENERGIA_20260630_trimestral.pdf")
            # semestral: arquivo e numeros do semestral
            _sb(at).set_value((P26, "semestral")).run(timeout=60)
            next(b for b in at.button if b.label == "Gerar relatório").click().run(timeout=60)
            assert capturado["dados"]["receita_liquida"] == pytest.approx(33_350_846.80, abs=0.01)
            assert capturado["caminho"].endswith("Demonstrativo_ENERGIA_20260630_semestral.pdf")
        finally:
            _parar(ps)


def test_tela_fornecedor_gera_com_sufixo_e_titulo(conn):
    capturado = {}
    real = g.gerar_pdf_completo

    def espiao(dados, caminho, *a, **k):
        capturado["dados"], capturado["caminho"] = dados, caminho
        return real(dados, caminho, *a, **k)

    with patch.object(g, "gerar_pdf_completo", espiao):
        at, ps = _app(conn)
        try:
            at.radio(key="relatorio_tipo").set_value("Demonstrativo Comentado Fornecedor").run(timeout=60)
            _sb(at).set_value((P26, "trimestral")).run(timeout=60)
            next(b for b in at.button if b.label == "Gerar relatório").click().run(timeout=60)
            assert not at.exception, at.exception
            assert capturado["caminho"].endswith("Demonstrativo_ENERGIA_20260630_trimestral_FORNECEDOR.pdf")
            assert capturado["dados"]["variante"] == "fornecedor"
            assert capturado["dados"]["cabecalho_relatorio"].startswith("Demonstrativo Comentado Fornecedor")
        finally:
            _parar(ps)


def test_tela_periodo_unico_grupo_so_oferece_pares_completos_em_todas(conn):
    at, ps = _app(conn)
    try:
        at.multiselect(key="relatorio_empresas_unico_multi").set_value(TODAS).run(timeout=60)
        assert _sb(at).options == ["30/06/2026 — Trimestral"]
        # explica o que ficou de fora
        assert any("Fora da lista" in c.value and "Semestral" in c.value for c in at.caption)
    finally:
        _parar(ps)


def test_tela_evolucao_energia_gera_4_colunas_com_rotulos_distintos_e_nome_de_arquivo_com_base(conn):
    capturado = {}
    real = gc.gerar_pdf_comparativo

    def espiao(dados, caminho, *a, **k):
        capturado["dados"], capturado["caminho"] = dados, caminho
        return real(dados, caminho, *a, **k)

    with patch.object(gc, "gerar_pdf_comparativo", espiao):
        at, ps = _app(conn)
        try:
            at.radio(key="relatorio_tipo").set_value("Comparativo / Evolução").run(timeout=60)
            ms = _ms(at)
            assert ms.options == [
                "31/12/2023 — Trimestral", "31/12/2024 — Anual",
                "30/06/2026 — Semestral", "30/06/2026 — Trimestral",
            ]
            ms.set_value([(P26, "semestral"), (P26, "trimestral"), (P23, "trimestral"), (P24, "anual")]).run(timeout=60)
            assert not at.exception, at.exception
            rot = {ti.label: ti.value for ti in at.text_input if ti.key and ti.key.startswith("relatorio_periodo_multi_label_")}
            assert sorted(rot.values()) == sorted(["4T/2023", "2024", "1S/2026", "2T/2026"])
            next(b for b in at.button if b.label == "Gerar relatório").click().run(timeout=60)
            assert not at.exception, at.exception
            d = capturado["dados"]
            assert d["granularidades"] == ["trimestral", "anual", "semestral", "trimestral"] or d["granularidades"] == [
                "trimestral", "anual", "trimestral", "semestral"]
            assert d["periodos_labels"][0] == "4T/2023" and len(set(d["periodos_labels"])) == 4
            rec = [k for k in d["kpis_fluxo"] if k["label"] == "Receita Operacional Líquida"][0]["valores"]
            assert len(set(rec)) == 4  # nenhuma coluna repetida
            assert capturado["caminho"].endswith("_mista.pdf")
        finally:
            _parar(ps)


def test_tela_evolucao_trocar_empresas_nao_deixa_selecao_fantasma(conn):
    at, ps = _app(conn)
    try:
        at.radio(key="relatorio_tipo").set_value("Comparativo / Evolução").run(timeout=60)
        _ms(at).set_value(_ms(at).options and [(P26, "semestral"), (P26, "trimestral")]).run(timeout=60)
        key_antes = _ms(at).key
        # adiciona CONST: a base semestral deixa de existir para todas -> opcoes e key mudam, selecao zera
        at.multiselect(key="relatorio_empresas_multi").set_value(["ENERGIA", "CONST"]).run(timeout=60)
        assert not at.exception, at.exception
        assert _ms(at).key != key_antes
        assert _ms(at).options == ["30/06/2026 — Trimestral"]
        assert _ms(at).value == []
        assert next(b for b in at.button if b.label == "Gerar relatório").disabled
    finally:
        _parar(ps)


def test_tela_evolucao_remover_periodos_mantem_listas_alinhadas(conn):
    pg.semear_trimestre_anterior(conn)
    capturado = {}
    real = gc.gerar_pdf_comparativo

    def espiao(dados, caminho, *a, **k):
        capturado["dados"] = dados
        return real(dados, caminho, *a, **k)

    with patch.object(gc, "gerar_pdf_comparativo", espiao):
        at, ps = _app(conn)
        try:
            at.radio(key="relatorio_tipo").set_value("Comparativo / Evolução").run(timeout=60)
            todos = list(_ms(at).options)
            at.multiselect(key="relatorio_empresas_multi").set_value(["ENERGIA"]).run(timeout=60)
            ms = _ms(at)
            ms.set_value(list(ms.options and [(P23, "trimestral"), (pg.P_MAR_2026, "trimestral"), (P26, "trimestral"), (P26, "semestral")])).run(timeout=60)
            # remove 2 dos 4 e confere o que vai pro backend
            _ms(at).set_value([(pg.P_MAR_2026, "trimestral"), (P26, "trimestral")]).run(timeout=60)
            next(b for b in at.button if b.label == "Gerar relatório").click().run(timeout=60)
            assert not at.exception, at.exception
            d = capturado["dados"]
            assert d["periodos"] == [pg.P_MAR_2026, P26]
            assert d["granularidades"] == ["trimestral", "trimestral"]
            assert d["periodos_labels"] == ["1T/2026", "2T/2026"]
            assert len(todos) >= 4
        finally:
            _parar(ps)
