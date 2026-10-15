# -*- coding: utf-8 -*-
"""v0.49.0 -- conferencia de NFS (escrituracao da contabilidade x titulos NFS do Sienge).
Parte pura (sem banco) + parte contra Postgres real (marcada pg)."""
import datetime as dt
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import nfs_escrituracao as ne  # noqa: E402
import nf_export  # noqa: E402
import nf_sienge  # noqa: E402

CNPJ_A = "11.222.333/0001-81"
CNPJ_B = "44.555.666/0001-99"
ND = "11222333000181"
NB = "44555666000199"


# ───────────────────────── leitura da planilha ─────────────────────────
def _xlsx(linhas, colunas=None):
    colunas = colunas or ["CNPJ / CPF / CNO", "Número", "Espécie", "Data Emissão", "Data Escrituração/Serviço",
                          "Razão Social", "Valor Total", "Descrição"]
    buf = io.BytesIO()
    pd.DataFrame(linhas, columns=colunas).to_excel(buf, index=False)
    return buf.getvalue()


def _linha(cnpj=CNPJ_A, num="123", esp="NFSE", emi="2026-08-05", esc="2026-08-10", nome="FORNEC A", valor=100.0, desc="SERVIÇO TOMADO"):
    return [cnpj, num, esp, emi, esc, nome, valor, desc]


def test_analisar_pega_so_servico_e_sugere_periodo():
    c = _xlsx([_linha(), _linha(num="124", esp="NFS-E"), _linha(num="9", esp="NFE"), _linha(num="8", esp="RECIBO"),
               _linha(num="125", esp="NFS")])
    r = ne.analisar_planilha(c, "ago.xlsx")
    assert r["erro"] is None
    assert len(r["df"]) == 3
    assert r["total_linhas"] == 5
    assert r["periodo"] == "08/2026"
    assert r["por_especie"]["NFE"] == 1
    assert set(r["df"]["cnpj"]) == {ND}


def test_analisar_numero_normalizado_e_repetidas():
    c = _xlsx([_linha(num="000123"), _linha(num="123")])
    r = ne.analisar_planilha(c)
    assert list(r["df"]["num"]) == ["123", "123"]
    assert r["repetidas"] == 1


def test_analisar_ignora_sem_valor_ou_cnpj():
    c = _xlsx([_linha(), _linha(num="2", valor=None), _linha(num="3", cnpj="")])
    r = ne.analisar_planilha(c)
    assert len(r["df"]) == 1 and r["sem_valor"] == 2


def test_analisar_reconhece_manifesto_da_receita_como_arquivo_errado():
    c = _xlsx([["x", "y"]], colunas=["Emissor CNPJ/CPF", "XML"])
    r = ne.analisar_planilha(c)
    assert r["erro"] and "manifesto" in r["erro"].lower() and r["df"].empty


def test_analisar_colunas_faltando_e_arquivo_invalido():
    r = ne.analisar_planilha(_xlsx([["a", "b"]], colunas=["Foo", "Bar"]))
    assert r["erro"] and "Faltam" in r["erro"]
    r2 = ne.analisar_planilha(b"nao e planilha")
    assert r2["erro"]


def test_analisar_sem_nenhuma_nfs():
    r = ne.analisar_planilha(_xlsx([_linha(esp="NFE")]))
    assert r["erro"] and "nenhuma nota de serviço" in r["erro"]


# ───────────────────────── classificacao (pura) ─────────────────────────
def _esc(linhas):
    cols = ["periodo", "num", "numero_nota", "cnpj", "cnpj_formatado", "fornecedor_nome", "data_emissao",
            "data_escrituracao", "valor", "especie", "descricao", "registro_id"]
    return pd.DataFrame([dict(zip(cols, l)) for l in linhas], columns=cols)


def _bills(linhas):
    cols = ["bill_id", "debtor_id", "tipo", "numero", "issue_date", "valor", "credor_nome", "cnpj", "num"]
    return pd.DataFrame(linhas, columns=cols)


def _e(num, cnpj=ND, valor=100.0, emi="2026-08-05", per="08/2026", rid=1):
    return [per, num, num, cnpj, cnpj, "FORN", dt.date.fromisoformat(emi), dt.date.fromisoformat(emi), valor, "NFSE", "", rid]


def _b(bid, num, cnpj=ND, valor=100.0, debtor=1, emi="2026-08-05", tipo="NFS "):
    return [bid, debtor, tipo, num, dt.date.fromisoformat(emi), valor, "FORN", cnpj, num]


def _classe(esc, bills, nfe=None, mapa=None, emp="ENERGIA"):
    esc = _esc(esc)
    return ne.classificar(esc, esc, _bills(bills), nfe if nfe is not None else pd.DataFrame(),
                          {1: "ENERGIA"} if mapa is None else mapa, emp, {"ENERGIA": "Energia", "SMG": "SMG"})


def _st(t, num):
    return t[(t["numero_nota"] == num) & (t["origem"] == "ESCRITURACAO")].iloc[0]


def test_lancada():
    t = _classe([_e("10")], [_b(1, "10")])
    assert _st(t, "10")["status"] == "LANCADA" and _st(t, "10")["sienge_bill_id"] == 1


def test_lancada_outra_empresa():
    t = _classe([_e("10")], [_b(1, "10", debtor=2)], mapa={1: "ENERGIA", 2: "SMG"})
    assert _st(t, "10")["status"] == "LANCADA_OUTRA_EMPRESA"


def test_valor_divergente():
    t = _classe([_e("10", valor=100.0)], [_b(1, "10", valor=150.0)])
    assert _st(t, "10")["status"] == "VALOR_DIVERGENTE"


def test_tolerancia_de_um_centavo():
    t = _classe([_e("10", valor=100.0)], [_b(1, "10", valor=100.01)])
    assert _st(t, "10")["status"] == "LANCADA"


def test_numero_divergente_digito_trocado():
    t = _classe([_e("1234")], [_b(1, "1243")])
    assert _st(t, "1234")["status"] == "NUMERO_DIVERGENTE"


def test_nao_encontrada():
    t = _classe([_e("10")], [_b(1, "99", cnpj=NB)])
    assert _st(t, "10")["status"] == "NAO_ENCONTRADA"


def test_nao_encontrada_com_pista_de_nfe():
    nfe = _bills([[7, 1, "NFE ", "10", dt.date(2026, 8, 5), 100.0, "F", ND, "10"]])
    t = _classe([_e("10")], [], nfe=nfe)
    r = _st(t, "10")
    assert r["status"] == "NAO_ENCONTRADA" and "NFE" in str(r["observacao"]).upper()


def test_orfao_so_com_devedor_confirmado():
    com = _classe([_e("10")], [_b(1, "10"), _b(2, "77", cnpj=NB)])
    assert (com["status"] == "SIENGE_SEM_ESCRITURACAO").sum() == 1
    assert com[com["status"] == "SIENGE_SEM_ESCRITURACAO"].iloc[0]["origem"] == "SIENGE_ORFAO"
    sem = _classe([_e("10")], [_b(1, "10"), _b(2, "77", cnpj=NB)], mapa={})
    assert (sem["status"] == "SIENGE_SEM_ESCRITURACAO").sum() == 0


def test_repetida_casa_com_o_mesmo_titulo_e_avisa_na_observacao():
    t = _classe([_e("10", rid=1), _e("10", rid=2)], [_b(1, "10")])
    esc = t[t["origem"] == "ESCRITURACAO"]
    assert list(esc["status"]) == ["LANCADA", "LANCADA"]
    assert esc["observacao"].str.contains("repetida").all()


def test_resumo_nfs():
    t = _classe([_e("10"), _e("11", valor=5.0)], [_b(1, "10"), _b(2, "55", cnpj=NB)])
    r = ne.resumo_nfs(t)
    assert r["total"] == 2 and r["lancadas"] == 1 and r["sienge_sem_escrituracao"] == 1 and r["pendencias"] == 2
    assert r["taxa"] == 0.5
    assert ne.resumo_nfs(pd.DataFrame())["total"] == 0


def test_eh_nota_servico():
    assert nf_sienge.eh_nota_servico("NFS ") and nf_sienge.eh_nota_servico("nfs")
    assert not nf_sienge.eh_nota_servico("NFE ") and not nf_sienge.eh_nota_servico(None)


# ───────────────────────── exportacao ─────────────────────────
def _pack():
    t = _classe([_e("10"), _e("11", valor=5.0)], [_b(1, "10")])
    return {"tabela": t, "resumo": ne.resumo_nfs(t), "arquivos": ["a.xls"], "periodos": "08/2026"}


def _tab_nfe():
    return pd.DataFrame([dict(periodo="08/2026", numero_nota="1", status="LANCADA", origem="MANIFESTO", registro_id=1)])


def test_export_sem_nfs_fica_igual_e_com_nfs_ganha_abas():
    t = _tab_nfe()
    base = pd.ExcelFile(io.BytesIO(nf_export.gerar_xlsx_conferencia(t, "Energia", "08/2026", "m.xlsx", "x")))
    assert "NFS" not in base.sheet_names
    com = pd.ExcelFile(io.BytesIO(nf_export.gerar_xlsx_conferencia(t, "Energia", "08/2026", "m.xlsx", "x", nfs=_pack())))
    assert com.sheet_names == base.sheet_names + ["NFS"]       # so' acrescenta, nada muda nas outras abas
    for aba in base.sheet_names:
        pd.testing.assert_frame_equal(base.parse(aba, header=None), com.parse(aba, header=None)) if aba != "Resumo" else None
    aba = com.parse("NFS", header=nf_export.LINHA_CABECALHO - 1)
    assert len(aba) == 2 and "Nº da nota" in aba.columns
    resumo = com.parse("Resumo", header=None).astype(str).to_string()
    assert "NFS (notas de serviço)" in resumo


def test_export_pendencias_com_nfs_e_nfs_vazia():
    t = _tab_nfe()
    p = pd.ExcelFile(io.BytesIO(nf_export.gerar_xlsx_pendencias(t, "Energia", "08/2026", "ENERGIA", nfs=_pack())))
    assert p.sheet_names == ["Pendências", "Pendências NFS"]
    assert len(p.parse("Pendências NFS", header=nf_export.LINHA_CABECALHO - 1)) == 1     # so' a 11 (nao lancada)
    p2 = pd.ExcelFile(io.BytesIO(nf_export.gerar_xlsx_pendencias(t, "Energia", "08/2026", "ENERGIA", nfs={"tabela": pd.DataFrame()})))
    assert p2.sheet_names == ["Pendências"]


def test_export_so_nfs_com_tabela_nfe_vazia():
    vazio = pd.DataFrame({"status": pd.Series(dtype=object), "origem": pd.Series(dtype=object)})
    x = pd.ExcelFile(io.BytesIO(nf_export.gerar_xlsx_conferencia(vazio, "Energia", "08/2026", None, "x", nfs=_pack())))
    assert "NFS" in x.sheet_names


# ───────────────────────── Postgres real ─────────────────────────
import pg_support as pg  # noqa: E402

pg_only = pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    nf_sienge._COL_ARQUIVADO["ok"] = False
    nf_sienge.salvar_mapa_debtor(c, 1, "ENERGIA", "t")
    yield c
    c.close()


def _df_nfs(linhas):
    c = _xlsx(linhas)
    return ne.analisar_planilha(c, "x.xlsx")


def _creditor(c, cid=591, cnpj=CNPJ_A):
    with c.cursor() as cur:
        cur.execute("INSERT INTO egc.nf_creditors_sync (creditor_id, nome, cnpj) VALUES (%s, 'FORN', %s) ON CONFLICT DO NOTHING", (cid, cnpj))


def _bill_nfs(c, bid, num, valor, data="2026-08-05", tipo="NFS ", debtor=1, tabela="nfs_bills_sync"):
    with c.cursor() as cur:
        cur.execute(f"INSERT INTO egc.{tabela} (bill_id, debtor_id, creditor_id, document_identification_id, document_number, "
                    "issue_date, total_invoice_amount) VALUES (%s, %s, 591, %s, %s, %s, %s)", (bid, debtor, tipo, num, data, valor))


@pg_only
@pytest.mark.pg
def test_bloco27_existe_e_gravar_envio_substitui_sem_apagar(conn):
    assert ne.tem_bloco27(conn)
    a = _df_nfs([_linha(num="10"), _linha(num="11")])
    id1 = ne.gravar_envio(conn, "ENERGIA", "08/2026", "a.xlsx", a["df"], a["total_linhas"], "rafael")
    b = _df_nfs([_linha(num="10"), _linha(num="11"), _linha(num="12")])
    id2 = ne.gravar_envio(conn, "ENERGIA", "08/2026", "b.xlsx", b["df"], b["total_linhas"], "rafael")
    assert id1 != id2
    vig = ne.listar_envios(conn, "ENERGIA")
    assert [e["id"] for e in vig] == [id2] and vig[0]["total_nfs"] == 3
    todos = ne.listar_envios(conn, "ENERGIA", incluir_arquivados=True)
    assert len(todos) == 2 and any(e["id"] == id1 and e["arquivado_em"] for e in todos)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM egc.nfs_escrituracao"); assert cur.fetchone()[0] == 5   # nada apagado


@pg_only
@pytest.mark.pg
def test_arquivar_e_restaurar(conn):
    a = _df_nfs([_linha(num="10")])
    id1 = ne.gravar_envio(conn, "ENERGIA", "08/2026", "a.xlsx", a["df"], 1, "r")
    ne.arquivar_envio(conn, id1, "r", "errado")
    assert ne.listar_envios(conn, "ENERGIA") == []
    id2 = ne.gravar_envio(conn, "ENERGIA", "08/2026", "b.xlsx", a["df"], 1, "r")
    with pytest.raises(ValueError):
        ne.restaurar_envio(conn, id1, "r")          # o mes ja' tem vigente
    ne.arquivar_envio(conn, id2, "r")
    ne.restaurar_envio(conn, id1, "r")
    assert [e["id"] for e in ne.listar_envios(conn, "ENERGIA")] == [id1]


@pg_only
@pytest.mark.pg
def test_conferir_fim_a_fim(conn):
    _creditor(conn)
    a = _df_nfs([_linha(num="10", valor=100.0), _linha(num="11", valor=50.0), _linha(num="12", valor=70.0)])
    ne.gravar_envio(conn, "ENERGIA", "08/2026", "a.xlsx", a["df"], 3, "r")
    _bill_nfs(conn, 1, "10", 100.0)
    _bill_nfs(conn, 2, "11", 55.0)                   # valor diferente
    _bill_nfs(conn, 3, "99", 10.0)                   # orfao (devedor 1 = Energia)
    t = ne.conferir(conn, "ENERGIA", ["08/2026"], {"ENERGIA": "Energia"})
    por = t.groupby("status").size().to_dict()
    assert por == {"LANCADA": 1, "VALOR_DIVERGENTE": 1, "NAO_ENCONTRADA": 1, "SIENGE_SEM_ESCRITURACAO": 1}
    # NFE nao contamina: titulo NFE com mesmo numero nao vira "lancada" de NFS
    _bill_nfs(conn, 4, "12", 70.0, tipo="NFE ", tabela="nf_bills_sync")
    t2 = ne.conferir(conn, "ENERGIA", None, {})
    r12 = t2[(t2["numero_nota"] == "12") & (t2["origem"] == "ESCRITURACAO")].iloc[0]
    assert r12["status"] == "NAO_ENCONTRADA"


@pg_only
@pytest.mark.pg
def test_conferir_sem_envio_devolve_vazio_e_sem_bloco27_levanta(conn):
    assert ne.conferir(conn, "SMG", None, {}).empty
    with conn.cursor() as cur:
        cur.execute("DROP TABLE egc.nfs_escrituracao CASCADE")
    assert not ne.tem_bloco27(conn)
    with pytest.raises(ne.Bloco27Indisponivel):
        ne.conferir(conn, "ENERGIA")


@pg_only
@pytest.mark.pg
def test_sincronizar_grava_nfs_em_tabela_separada(conn, monkeypatch):
    registros = [
        {"id": 1, "debtorId": 1, "creditorId": 591, "documentIdentificationId": "NFS ", "documentNumber": "10",
         "issueDate": "2026-08-05", "totalInvoiceAmount": 100.0},
        {"id": 2, "debtorId": 1, "creditorId": 591, "documentIdentificationId": "NFE ", "documentNumber": "20",
         "issueDate": "2026-08-05", "totalInvoiceAmount": 200.0},
        {"id": 3, "debtorId": 1, "creditorId": 591, "documentIdentificationId": "PPC ", "documentNumber": "30",
         "issueDate": "2026-08-05", "totalInvoiceAmount": 300.0},
    ]

    class _R:
        def __init__(self, d): self._d = d
        def raise_for_status(self): pass
        def json(self): return self._d

    class _S:
        def get(self, url, params=None, timeout=None):
            return _R({"results": registros if params["offset"] == 0 else []})

    monkeypatch.setattr(nf_sienge, "_sessao_sienge", lambda *a: ("http://x", _S()))
    n = nf_sienge.sincronizar_bills(conn, "http://x", "u", "p", dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert n == 1                                    # retorno = so' NFE/NF (como sempre)
    with conn.cursor() as cur:
        cur.execute("SELECT bill_id FROM egc.nf_bills_sync"); assert [r[0] for r in cur.fetchall()] == [2]
        cur.execute("SELECT bill_id FROM egc.nfs_bills_sync"); assert [r[0] for r in cur.fetchall()] == [1]


@pg_only
@pytest.mark.pg
def test_sincronizar_sem_bloco27_nao_quebra(conn, monkeypatch):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE egc.nfs_bills_sync CASCADE")

    class _R:
        def raise_for_status(self): pass
        def json(self): return {"results": [{"id": 1, "debtorId": 1, "creditorId": 591, "documentIdentificationId": "NFS ",
                                              "documentNumber": "10", "issueDate": "2026-08-05", "totalInvoiceAmount": 1.0}]}

    class _S:
        def get(self, url, params=None, timeout=None):
            r = _R()
            if params["offset"]:
                r.json = lambda: {"results": []}
            return r

    monkeypatch.setattr(nf_sienge, "_sessao_sienge", lambda *a: ("http://x", _S()))
    assert nf_sienge.sincronizar_bills(conn, "http://x", "u", "p", dt.date(2026, 8, 1), dt.date(2026, 8, 31)) == 0
