# -*- coding: utf-8 -*-
"""v0.44.3 -- logica da conferencia de Notas Fiscais (sem banco).

Casos que vieram dos dados reais de 07/2026 (Energia 459 notas / Construtora 101):
  - periodo sugerido errado ("2026.07.06" lido como dia/mes trocado);
  - notas canceladas virando pendencia falsa;
  - NUMERO_DIVERGENTE falso (titulo de OUTRA nota apontado como candidato);
  - Resumo do Excel nao batendo com a aba Pendencias.
"""
import datetime as dt
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nf_export  # noqa: E402
import nf_parser  # noqa: E402
import nf_sienge  # noqa: E402
import importacoes_ui  # noqa: E402

EMPRESAS = [("ENERGIA", "Enermais Energia", "47.040.664/0001-48"),
            ("SETTE", "Sette Locacoes", "44.914.462/0001-90"),
            ("CONST", "Enermais Construtora", "55.244.465/0001-80"),
            ("SMG", "SMG Solucoes", "18.387.666/0001-00")]
UPLOADS = Path("/root/.claude/uploads/bf8110bc-4b7b-5bde-94e5-f88e20798674")


def _xlsx(linhas, colunas):
    buf = io.BytesIO()
    pd.DataFrame(linhas, columns=colunas).to_excel(buf, index=False)
    buf.seek(0)
    return buf


COLS = ["Num", "Tipo", "TipoDoc", "DtEmi", "Valor", "CFOP", "Emissor Nome", "Emissor CNPJ/CPF", "Can", "Status", "Filial", "Ano-Mês"]


# ───────────────────────── datas / periodo ─────────────────────────

@pytest.mark.parametrize("texto,esperado", [
    ("2026.07.06", dt.date(2026, 7, 6)),      # formato da Receita (o que quebrava)
    ("06/07/2026", dt.date(2026, 7, 6)),      # dia/mes/ano explicito
    ("2026-07-06", dt.date(2026, 7, 6)),
    ("2026-07-06 10:30:00", dt.date(2026, 7, 6)),
    ("", None), (None, None), ("lixo", None),
])
def test_parse_data_emissao(texto, esperado):
    assert nf_parser.parse_data_emissao(texto) == esperado


def test_periodo_sugerido_nao_troca_dia_com_mes():
    df = nf_parser.ler_manifesto_xlsx(_xlsx(
        [["1", "NF-e", "Saída", "2026.07.06", "10", "5102", "A", "07393522000140", None, "x", "47040664000148", "2026.07"],
         ["2", "NF-e", "Saída", "2026.07.20", "10", "5102", "A", "07393522000140", None, "x", "47040664000148", "2026.07"],
         ["3", "NF-e", "Saída", "2026.08.01", "10", "5102", "A", "07393522000140", None, "x", "47040664000148", "2026.08"]], COLS))
    assert nf_parser.sugerir_periodo_referencia(df) == "07/2026"
    assert list(df["_data_emissao"])[0] == dt.date(2026, 7, 6)


def test_periodo_cai_pro_ano_mes_quando_sem_dtemi():
    df = pd.DataFrame({"Ano-Mês": ["2026.09", "2026.09", "2026.08"]})
    assert nf_parser.sugerir_periodo_referencia(df) == "09/2026"


# ───────────────────────── cancelada / empresa ─────────────────────────

def test_filtrar_ignora_cancelada_e_entrada_e_lista_as_ignoradas():
    cols = COLS + ["Natureza"]
    df = nf_parser.ler_manifesto_xlsx(_xlsx(
        [["1", "NF-e", "Saída", "2026.07.06", "10", "5102", "A", "07393522000140", None, "Autorizado", "47040664000148", "2026.07", "Venda"],
         ["2", "NF-e", "Saída", "2026.07.06", "10", "5102", "A", "07393522000140", "X", "Autorizado", "47040664000148", "2026.07", None],
         ["3", "NF-e", "Saída", "2026.07.06", "10", "5102", "A", "07393522000140", None, "Cancelamento de NF-e homologado", "47040664000148", "2026.07", None],
         ["4", "NF-e", "Entrada", "2026.07.06", "10", "2202", "A", "07393522000140", None, "Autorizado", "47040664000148", "2026.07", "DEVOLUCAO DE VENDA"]], cols))
    filtrado, c, ign = nf_parser.filtrar_manifesto(df)
    assert list(filtrado["Num"]) == ["1"]                                  # 2 e 3 canceladas, 4 Entrada
    assert c == {"canceladas": 2, "entradas": 1}
    assert sorted(ign["numero_nota"]) == ["2", "3", "4"]
    assert ign.set_index("numero_nota").loc["4", "motivo"] == nf_parser.MOTIVO_ENTRADA
    assert ign.set_index("numero_nota").loc["4", "natureza"] == "DEVOLUCAO DE VENDA"
    assert ign.set_index("numero_nota").loc["2", "motivo"] == nf_parser.MOTIVO_CANCELADA


def test_competencia_vem_do_ano_mes_e_arquivo_com_2_meses_e_dividido():
    linhas = [[str(i), "NF-e", "Saída", d, "10", "5102", "A", "07393522000140", None, "x", "44914462000190", am]
              for i, (d, am) in enumerate([("2026.07.03", "2026.07"), ("2026.07.20", "2026.07"),
                                           ("2026.07.31", "2026.08"),   # emitida 31/07, autorizada em agosto -> competencia 08
                                           ("2026.08.05", "2026.08")])]
    df = nf_parser.ler_manifesto_xlsx(_xlsx(linhas, COLS))
    assert list(df["_competencia"]) == ["07/2026", "07/2026", "08/2026", "08/2026"]
    partes = nf_parser.dividir_por_competencia(df)
    assert [(c, len(d)) for c, d, _ in partes] == [("07/2026", 2), ("08/2026", 2)]
    assert nf_parser.analisar_upload(_xlsx(linhas, COLS).getvalue(), EMPRESAS)["competencias"] == {"07/2026": 2, "08/2026": 2}


def test_competencia_sem_ano_mes_cai_no_mes_de_emissao():
    assert nf_parser.competencia_da_linha(None, dt.date(2026, 9, 3)) == "09/2026"
    assert nf_parser.competencia_da_linha("2026.07", dt.date(2026, 9, 3)) == "07/2026"
    assert nf_parser.competencia_da_linha(None, None) is None


def test_detectar_empresa_pela_filial_e_filial_de_outra_raiz():
    base = ["1", "NF-e", "Saída", "2026.07.06", "10", "5102", "A", "07393522000140", None, "x"]
    df = nf_parser.ler_manifesto_xlsx(_xlsx([base + ["47040664000148", "2026.07"], base + ["47.040.664/0002-29", "2026.07"]], COLS))
    assert nf_parser.detectar_empresa_manifesto(df, EMPRESAS) == "ENERGIA"   # filial = mesma raiz
    df2 = nf_parser.ler_manifesto_xlsx(_xlsx([base + ["47040664000148", "2026.07"], base + ["55244465000180", "2026.07"]], COLS))
    assert nf_parser.detectar_empresa_manifesto(df2, EMPRESAS) is None        # mistura
    df3 = nf_parser.ler_manifesto_xlsx(_xlsx([base + ["99999999000199", "2026.07"]], COLS))
    assert nf_parser.detectar_empresa_manifesto(df3, EMPRESAS) is None        # CNPJ desconhecido
    assert nf_parser.analisar_upload(b"nao e xlsx", EMPRESAS)["erro"]


def test_analisar_upload_devolve_periodo_empresa_e_canceladas():
    buf = _xlsx([["1", "NF-e", "Saída", "2026.07.06", "10", "5102", "A", "07393522000140", None, "x", "55244465000180", "2026.07"],
                 ["2", "NF-e", "Saída", "2026.07.07", "10", "5102", "A", "07393522000140", "X", "x", "55244465000180", "2026.07"]], COLS)
    info = nf_parser.analisar_upload(buf.getvalue(), EMPRESAS)
    assert info["periodo"] == "07/2026" and info["empresa"] == "CONST"
    assert info["total"] == 1 and info["canceladas"] == 1 and info["raizes"] == ["55244465"]


@pytest.mark.skipif(not (UPLOADS / "f16c07d6-ENERGIA-07.2026-NOVO.xlsx").exists(), reason="planilhas reais so' no ambiente de dev")
def test_planilhas_reais_de_07_2026():
    # Energia 07: 459 = 445 + 7 canceladas + 7 Entrada (iguais as 443 notas que a contadora conferiu a mao
    # + as 2 Saida normais que ela tirou: 5700 e 29968, que ficam como pendencia com observacao).
    e = nf_parser.analisar_upload((UPLOADS / "f16c07d6-ENERGIA-07.2026-NOVO.xlsx").read_bytes(), EMPRESAS)
    assert (e["periodo"], e["empresa"], e["total"], e["canceladas"], e["entradas"]) == ("07/2026", "ENERGIA", 445, 7, 7)
    c = nf_parser.analisar_upload((UPLOADS / "2342e07e-CONSTRUTORA-07.2026-NOVO.xlsx").read_bytes(), EMPRESAS)
    assert (c["periodo"], c["empresa"], c["total"], c["canceladas"]) == ("07/2026", "CONST", 101, 0)


# ───────────────────────── matcher em fases ─────────────────────────

CNPJ = "07393522000140"


def _bills(linhas):
    """linhas: (bill_id, numero, valor, debtor)"""
    return pd.DataFrame([{
        "bill_id": b, "debtor_id": d, "creditor_id": 1, "document_identification_id": "NFE ",
        "document_number": n, "issue_date": dt.date(2026, 7, 10), "total_invoice_amount": v,
        "access_key_number": "", "creditor_cnpj": CNPJ, "creditor_nome": "F",
        "cnpj_normalizado": CNPJ, "numero_normalizado": str(n).lstrip("0"),
    } for b, n, v, d in linhas])


def _manifesto(linhas, empresa="ENERGIA"):
    return pd.DataFrame([{
        "id": i + 1, "Num": n, "_numero_normalizado": str(n).lstrip("0"), "_cnpj_normalizado": CNPJ,
        "_valor_float": v, "Chave": "", "_empresa_codigo": empresa,
    } for i, (n, v) in enumerate(linhas)])


def test_titulo_de_outra_nota_nao_vira_numero_divergente():
    # nota 10 (100,00) tem o titulo 10; nota 11 (100,00) NAO foi lancada.
    # Antes: nota 11 apontava o titulo da nota 10 como NUMERO_DIVERGENTE.
    res = nf_sienge.classificar_manifesto(_manifesto([("10", 100.0), ("11", 100.0)]), _bills([(1, "10", 100.0, 1)]))
    assert res[0]["status"] == "LANCADA" and res[0]["sienge_bill_id"] == 1
    assert res[1]["status"] == "NAO_ENCONTRADA" and res[1]["sienge_bill_id"] is None


def test_numero_divergente_real_continua_aparecendo_e_so_uma_vez():
    # titulo 7 lancado com numero errado (99 em vez de 12); 2 notas (12 e 13) de 50,00 sem titulo proprio
    res = nf_sienge.classificar_manifesto(_manifesto([("12", 50.0), ("13", 50.0)]), _bills([(7, "99", 50.0, 1)]))
    status = sorted(r["status"] for r in res.values())
    assert status == ["NAO_ENCONTRADA", "NUMERO_DIVERGENTE"]       # o titulo 7 so' serve a UMA nota
    assert sorted(r["sienge_bill_id"] for r in res.values() if r["sienge_bill_id"]) == [7]


def test_ordem_do_manifesto_nao_muda_o_resultado():
    bills = _bills([(1, "10", 100.0, 1), (2, "99", 100.0, 1)])
    a = nf_sienge.classificar_manifesto(_manifesto([("10", 100.0), ("11", 100.0)]), bills)
    b = nf_sienge.classificar_manifesto(_manifesto([("11", 100.0), ("10", 100.0)]), bills)
    assert a[0]["status"] == "LANCADA" and a[1]["status"] == "NUMERO_DIVERGENTE" and a[1]["sienge_bill_id"] == 2
    assert b[1]["status"] == "LANCADA" and b[0]["status"] == "NUMERO_DIVERGENTE" and b[0]["sienge_bill_id"] == 2


def test_valor_divergente_nao_rouba_titulo_de_nota_exata():
    # duas notas de mesmo numero/CNPJ? nao: nota 20 (valor 100) e titulo 20 (valor 90) -> VALOR_DIVERGENTE
    res = nf_sienge.classificar_manifesto(_manifesto([("20", 100.0)]), _bills([(5, "20", 90.0, 1)]))
    assert res[0]["status"] == "VALOR_DIVERGENTE" and res[0]["sienge_bill_id"] == 5


def test_empate_entre_titulos_iguais_prefere_devedor_da_empresa():
    bills = _bills([(1, "30", 10.0, 5), (2, "30", 10.0, 1)])   # mesmo titulo lancado em CONST(5) e ENERGIA(1)
    res = nf_sienge.classificar_manifesto(_manifesto([("30", 10.0)], "ENERGIA"), bills, {1: "ENERGIA", 5: "CONST"})
    assert res[0]["sienge_bill_id"] == 2
    res = nf_sienge.classificar_manifesto(_manifesto([("30", 10.0)], "CONST"), bills, {1: "ENERGIA", 5: "CONST"})
    assert res[0]["sienge_bill_id"] == 1  # o titulo 1 e' do devedor 5 = CONST


def test_nota_duplicada_no_manifesto_nao_vira_pendencia_falsa():
    res = nf_sienge.classificar_manifesto(_manifesto([("10", 100.0), ("10", 100.0)]), _bills([(1, "10", 100.0, 1)]))
    assert [r["status"] for r in res.values()] == ["LANCADA", "LANCADA"]


def test_bills_vazio_e_manifesto_vazio_nao_quebram():
    res = nf_sienge.classificar_manifesto(_manifesto([("1", 1.0)]), pd.DataFrame())
    assert res[0]["status"] == "NAO_ENCONTRADA"
    assert nf_sienge.classificar_manifesto(_manifesto([]), _bills([(1, "1", 1.0, 1)])) == {}


# ───────────────────────── export / KPIs ─────────────────────────

def _tabela():
    base = dict(cfop=None, data_emissao=None, valor=1.0, fornecedor_nome="F", fornecedor_cnpj=CNPJ, confianca=None,
                sienge_bill_id=None, sienge_documento=None, sienge_valor=None, observacao=None,
                pendencia_status=None, atualizado_em=None)
    linhas = []
    for i, st_ in enumerate(["LANCADA", "LANCADA", "LANCADA_OUTRA_EMPRESA", "VALOR_DIVERGENTE", "NUMERO_DIVERGENTE",
                             "NAO_ENCONTRADA", "NAO_ENCONTRADA"]):
        linhas.append({**base, "numero_nota": str(i), "status": st_, "registro_id": i, "origem": "MANIFESTO"})
    for j in range(3):
        linhas.append({**base, "numero_nota": f"[Sienge] nº {j}", "status": "SIENGE_SEM_MANIFESTO",
                       "registro_id": 100 + j, "origem": "SIENGE_ORFAO"})
    return pd.DataFrame(linhas)


def test_resumo_e_contagem_por_status():
    r = nf_export.resumo_conferencia(_tabela())
    assert r["total"] == 7 and r["lancadas"] == 2 and r["notas_pendentes"] == 5
    assert r["sienge_sem_manifesto"] == 3 and r["pendencias"] == 8
    assert r["por_status"] == {"LANCADA": 2, "LANCADA_OUTRA_EMPRESA": 1, "VALOR_DIVERGENTE": 1,
                               "NUMERO_DIVERGENTE": 1, "NAO_ENCONTRADA": 2, "SIENGE_SEM_MANIFESTO": 3}
    assert sum(r["por_status"].values()) == 10 and r["taxa"] == pytest.approx(2 / 7)


def test_excel_resumo_bate_com_aba_pendencias():
    t = _tabela()
    xlsx = nf_export.gerar_xlsx_conferencia(t, "Energia", "07/2026", "a.xlsx", "agora")
    res = pd.read_excel(io.BytesIO(xlsx), sheet_name="Resumo").set_index("Item")["Valor"]
    pend = pd.read_excel(io.BytesIO(xlsx), sheet_name="Pendências")
    assert int(res["Total de pendências (aba Pendências)"]) == len(pend) == 8
    assert int(res["Notas do manifesto com pendência"]) + int(res["Títulos no Sienge sem nota no manifesto"]) == len(pend)
    assert int(res["  · Lançadas em outra empresa"]) == 1 and int(res["  · Sienge sem manifesto"]) == 3


# ───────────────────────── badge Ativo (Importar PDF) ─────────────────────────

def test_evento_esta_ativo_por_instante_de_criacao():
    t_a = dt.datetime(2026, 10, 2, 9, 0, 0)
    t_b = dt.datetime(2026, 10, 2, 10, 0, 0)
    ev_b = t_b + dt.timedelta(seconds=3)       # evento gravado logo apos os lancamentos
    assert importacoes_ui.evento_esta_ativo(ev_b, [t_b]) is True
    assert importacoes_ui.evento_esta_ativo(ev_b, [t_a]) is False   # B desfeito, A reativada (mesmo nome de arquivo)
    assert importacoes_ui.evento_esta_ativo(ev_b, []) is False
    assert importacoes_ui.evento_esta_ativo(None, [t_b]) is False


# ───────────────────────── observacao dos pares (5700 / 29968) ─────────────────────────

def test_observacoes_de_ignoradas_nao_exclui_so_avisa():
    ign = pd.DataFrame([
        {"numero_nota": "5735", "valor": 4100.0, "cnpj_normalizado": CNPJ, "motivo": nf_parser.MOTIVO_ENTRADA},
        {"numero_nota": "29967", "valor": 7809.6, "cnpj_normalizado": "99999999000199", "motivo": nf_parser.MOTIVO_CANCELADA},
        {"numero_nota": "71713", "valor": 7879.5, "cnpj_normalizado": "99999999000199", "motivo": nf_parser.MOTIVO_ENTRADA},
    ])
    man = pd.DataFrame([
        {"id": 1, "_numero_normalizado": "5700", "_cnpj_normalizado": CNPJ, "_valor_float": 4100.0},
        {"id": 2, "_numero_normalizado": "29968", "_cnpj_normalizado": "99999999000199", "_valor_float": 7809.6},
        {"id": 3, "_numero_normalizado": "1", "_cnpj_normalizado": "11111111000111", "_valor_float": 5.0},
        {"id": 4, "_numero_normalizado": "2", "_cnpj_normalizado": CNPJ, "_valor_float": 90000.0},   # mesmo fornecedor, valor sem relacao
    ])
    obs = nf_sienge.observacoes_de_ignoradas(man, ign)
    assert set(obs) == {0, 1}                                   # fornecedor sem Entrada / valor sem relacao: nada
    assert "devolução/entrada nº 5735 (mesmo valor)" in obs[0]
    assert "nº 71713 (R$ 7.879,50, valor próximo)" in obs[1]
    assert "29967" not in obs[1]                                # cancelada + reemissao e' normal: nao avisa


def test_sette_esta_nas_empresas_do_fluxo_nf_e_nao_nas_do_resto():
    import conexao
    assert "SETTE" in [c for c, _n, _c in conexao.EMPRESAS_NF]
    assert "SETTE" not in [c for c, _n, _c in conexao.EMPRESAS_FIXAS]
    assert nf_parser.detectar_empresa_manifesto(
        nf_parser.ler_manifesto_xlsx(_xlsx([["1", "NF-e", "Saída", "2026.07.06", "10", "5102", "A", CNPJ, None, "x",
                                              "44914462000190", "2026.07"]], COLS)), conexao.EMPRESAS_NF) == "SETTE"


# ───────────────────────── sync do Sienge: teto por chamada ─────────────────────────

class _Resp:
    def __init__(self, n):
        self._n = n

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": [{"id": i, "documentIdentificationId": "NFE ", "documentNumber": str(i)} for i in range(self._n)]}


class _CursorFake:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _ConnFake:
    def cursor(self):
        return _CursorFake()


def test_sync_levanta_erro_em_vez_de_truncar_calado(monkeypatch):
    monkeypatch.setattr(nf_sienge, "execute_values", lambda *a, **k: None)
    monkeypatch.setattr(nf_sienge, "_sessao_sienge", lambda *a: ("http://x", type("S", (), {"get": lambda self, *a, **k: _Resp(nf_sienge.LIMITE_PAGINA)})()))
    with pytest.raises(nf_sienge.SincronizacaoTruncada):
        nf_sienge.sincronizar_bills(_ConnFake(), "u", "a", "b", dt.date(2026, 1, 1), dt.date(2026, 1, 31), max_paginas=3)


def test_sync_adaptativo_divide_a_janela_quando_estoura(monkeypatch):
    chamadas = []

    def falso(conn, base, user, pwd, ini, fim, max_paginas=50):
        chamadas.append((ini, fim))
        if (fim - ini).days >= 10:           # janela grande demais -> estoura
            raise nf_sienge.SincronizacaoTruncada("x")
        return 5
    monkeypatch.setattr(nf_sienge, "sincronizar_bills", falso)
    total = nf_sienge.sincronizar_bills_adaptativo(None, "u", "a", "b", dt.date(2026, 7, 1), dt.date(2026, 7, 31))
    folhas = [c for c in chamadas if (c[1] - c[0]).days < 10]
    assert total == 5 * len(folhas)
    # as folhas cobrem 01/07..31/07 sem buraco nem sobreposicao
    folhas.sort()
    assert folhas[0][0] == dt.date(2026, 7, 1) and folhas[-1][1] == dt.date(2026, 7, 31)
    assert all(b[0] == a[1] + dt.timedelta(days=1) for a, b in zip(folhas, folhas[1:]))


def test_sync_por_mes_cobre_janeiro_ate_hoje(monkeypatch):
    vistos = []
    monkeypatch.setattr(nf_sienge, "sincronizar_bills_adaptativo",
                        lambda conn, b, u, p, ini, fim, max_paginas=50: vistos.append((ini, fim)) or 7)
    r = nf_sienge.sincronizar_bills_por_mes(None, "u", "a", "b", dt.date(2026, 1, 1), dt.date(2026, 10, 2))
    assert list(r) == [f"2026-{m:02d}" for m in range(1, 11)] and set(r.values()) == {7}
    assert vistos[0] == (dt.date(2026, 1, 1), dt.date(2026, 1, 31)) and vistos[-1] == (dt.date(2026, 10, 1), dt.date(2026, 10, 2))


def test_numero_divergente_com_14k_titulos_nao_fica_lento():
    import time
    import numpy as np
    rng = np.random.default_rng(1)
    n = 14000
    bills = pd.DataFrame({
        "bill_id": range(n), "debtor_id": rng.integers(1, 7, n), "document_identification_id": "NFE ",
        "document_number": rng.integers(1, 99999, n).astype(str), "total_invoice_amount": rng.integers(1000, 900000, n) / 100,
        "access_key_number": "", "cnpj_normalizado": [f"{c:014d}" for c in rng.integers(1, 900, n)]})
    bills["numero_normalizado"] = bills["document_number"]
    man = pd.DataFrame({"id": range(460), "Num": "1", "_numero_normalizado": rng.integers(1, 99999, 460).astype(str),
                        "_cnpj_normalizado": [f"{c:014d}" for c in rng.integers(1, 900, 460)],
                        "_valor_float": rng.integers(1000, 900000, 460) / 100, "Chave": "", "_empresa_codigo": "ENERGIA"})
    t0 = time.time()
    nf_sienge.classificar_manifesto(man, bills, {1: "ENERGIA"})
    assert time.time() - t0 < 6      # antes: ~11 s no pior caso
