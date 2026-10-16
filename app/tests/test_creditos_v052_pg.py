# -*- coding: utf-8 -*-
"""v0.52.0 -- bloco 30 (controle do mes) e fontes/simulacao da serie mensal, no Postgres de verdade."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

import creditos_db as cdb  # noqa: E402
import creditos_detalhamento as cdet  # noqa: E402
import sped_contribuicoes as sc  # noqa: E402
from creditos_fixtures import detalhamento_sintetico, sped_sintetico  # noqa: E402


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    yield c
    c.close()


def _sped(c, comp="08/2026", tipo="0", **kw):
    cont, nome = sped_sintetico(competencia=comp, tipo=tipo, **kw)
    return cdb.gravar_sped(c, "ENERGIA", sc.ler_sped(cont, nome), f"{tipo}_{nome}", "t")


def _hist(c):
    cdb.upsert_historico(c, "ENERGIA", [
        {"competencia": "01/2026", "versao": "ORIGINAL", "resumo": {"credito_pis": 80, "credito_cofins": 370}, "por_categoria": {"Aluguel": {"pis": 80, "cofins": 370}}},
        {"competencia": "01/2026", "versao": "APONTADO", "sped_tipo": "Retificadora", "resumo": {"credito_pis": 100, "credito_cofins": 450},
         "por_categoria": {"Aluguel": {"pis": 100, "cofins": 450}}, "dif_fornecedores": [{"fornecedor": "A", "dif_pis": 20, "dif_cofins": 80}]}])


def test_bloco30_existe_e_comeca_vazio(conn):
    assert cdb.tem_bloco30(conn) and cdb.carregar_mes(conn, "ENERGIA") == {}


def test_sem_bloco30_serie_funciona_e_controle_vazio(conn):
    _hist(conn)
    _sped(conn)
    with conn.cursor() as cur:
        cur.execute("DROP TABLE egc.cred_mes_log")
        cur.execute("DROP TABLE egc.cred_mes")
    assert not cdb.tem_bloco30(conn) and cdb.carregar_mes(conn, "ENERGIA") == {}
    assert cdb.log_mes(conn, "ENERGIA", "01/2026").empty
    s = cdb.serie_mensal(conn, "ENERGIA")
    assert list(s["competencia"]) == ["01/2026", "08/2026"] and set(s["modo"]) == {"padrao"}


def test_fontes_por_mes_lista_tudo_e_regra_padrao_igual_a_antiga(conn):
    _hist(conn)
    a = _sped(conn, "01/2026")
    _sped(conn, "08/2026")
    b = _sped(conn, "08/2026", tipo="1", ajuste_servico=4.0)
    f = cdb.fontes_por_mes(conn, "ENERGIA")
    assert [x["chave"] for x in f["01/2026"]] == ["HIST_ORIGINAL", "HIST_APONTADO", f"ENV:{a}"]
    assert [x["tipo"] for x in f["08/2026"]] == ["ENVIO", "ENVIO"] and sum(x["apontada"] for x in f["08/2026"]) == 1
    assert cdb.fonte_padrao(f["01/2026"])["chave"] == "HIST_APONTADO"      # historico vence o envio do mesmo mes
    assert cdb.fonte_padrao(f["08/2026"])["chave"] == f"ENV:{b}"
    assert "1ª" in f["08/2026"][0]["rotulo"] and "2ª" in f["08/2026"][1]["rotulo"] and f["08/2026"][1]["rotulo"].endswith("APONTADA")


def test_serie_padrao_tem_modo_padrao_e_qtd_fontes(conn):
    _hist(conn)
    _sped(conn, "01/2026")
    s = cdb.serie_mensal(conn, "ENERGIA").set_index("competencia")
    assert s.loc["01/2026", "modo"] == "padrao" and s.loc["01/2026", "qtd_fontes"] == 3 and s.loc["01/2026", "cred_pis"] == 100
    assert s.loc["01/2026", "fonte"] == "HIST_APONTADO" and s.loc["01/2026", "origem"] == "HISTORICO"


def test_simulacao_troca_a_versao_sem_gravar_nada(conn):
    _hist(conn)
    envio = _sped(conn, "01/2026")                                          # PIS 127,05
    s = cdb.serie_mensal(conn, "ENERGIA", escolhas={"01/2026": f"ENV:{envio}"}).iloc[0]
    assert s["modo"] == "simulada" and round(s["cred_pis"], 2) == 127.05 and s["origem"] == "ENVIO" and s["dif_fornecedores"] == []
    s2 = cdb.serie_mensal(conn, "ENERGIA", escolhas={"01/2026": "HIST_ORIGINAL"}).iloc[0]
    assert s2["cred_pis"] == 80 and not s2["tem_original"]                    # o original nao se compara com ele mesmo
    s3 = cdb.serie_mensal(conn, "ENERGIA", escolhas={"01/2026": "HIST_APONTADO"}).iloc[0]
    assert s3["modo"] == "padrao"                                            # escolher o que ja' vale nao e' simulacao
    assert cdb.carregar_mes(conn, "ENERGIA") == {}                          # nada foi gravado
    s4 = cdb.serie_mensal(conn, "ENERGIA").iloc[0]
    assert s4["cred_pis"] == 100 and s4["modo"] == "padrao"


def test_oficial_marcada_vale_na_serie_e_simulacao_vence_a_oficial(conn):
    _hist(conn)
    envio = _sped(conn, "01/2026")
    assert cdb.salvar_mes(conn, "ENERGIA", "01/2026", "rafael", fonte_oficial=f"ENV:{envio}") == 1
    s = cdb.serie_mensal(conn, "ENERGIA").iloc[0]
    assert s["modo"] == "oficial" and round(s["cred_pis"], 2) == 127.05
    sim = cdb.serie_mensal(conn, "ENERGIA", escolhas={"01/2026": "HIST_ORIGINAL"}).iloc[0]
    assert sim["modo"] == "simulada" and sim["cred_pis"] == 80
    ignorando = cdb.serie_mensal(conn, "ENERGIA", usar_oficial=False).iloc[0]
    assert ignorando["modo"] == "padrao" and ignorando["cred_pis"] == 100
    cdb.salvar_mes(conn, "ENERGIA", "01/2026", "rafael", fonte_oficial=None)       # volta para a regra padrao
    assert cdb.serie_mensal(conn, "ENERGIA").iloc[0]["modo"] == "padrao"


def test_oficial_que_nao_existe_mais_cai_na_regra_padrao(conn):
    _hist(conn)
    cdb.salvar_mes(conn, "ENERGIA", "01/2026", "t", fonte_oficial="ENV:00000000-0000-0000-0000-000000000000")
    s = cdb.serie_mensal(conn, "ENERGIA").iloc[0]
    assert s["modo"] == "padrao" and s["cred_pis"] == 100


def test_salvar_mes_parcial_log_e_conferido(conn):
    assert cdb.salvar_mes(conn, "ENERGIA", "08/2026", "ana", nota="conferir frete") == 1
    assert cdb.salvar_mes(conn, "ENERGIA", "08/2026", "ana", nota="conferir frete") == 0          # sem mudanca nao grava nem registra
    assert cdb.salvar_mes(conn, "ENERGIA", "08/2026", "bia", conferido=True) == 1
    m = cdb.carregar_mes(conn, "ENERGIA")["08/2026"]
    assert m["conferido"] and m["conferido_por"] == "bia" and m["conferido_em"] is not None and m["nota"] == "conferir frete"      # nota nao foi apagada
    assert cdb.salvar_mes(conn, "ENERGIA", "08/2026", "bia", conferido=False, nota="ok") == 2
    m = cdb.carregar_mes(conn, "ENERGIA")["08/2026"]
    assert not m["conferido"] and m["conferido_por"] is None and m["nota"] == "ok"
    lg = cdb.log_mes(conn, "ENERGIA", "08/2026")
    assert list(lg["campo"]).count("nota") == 2 and list(lg["campo"]).count("conferido") == 2
    assert lg.iloc[-1]["valor_antes"] in (None, "") and lg.iloc[-1]["usuario"] == "ana"


def test_conferido_aparece_na_serie_sem_travar(conn):
    _sped(conn, "08/2026")
    cdb.salvar_mes(conn, "ENERGIA", "08/2026", "t", conferido=True, nota="ok")
    s = cdb.serie_mensal(conn, "ENERGIA").iloc[0]
    assert s["conferido"] and s["nota"] == "ok"
    _sped(conn, "08/2026", tipo="1", ajuste_servico=1.0)                     # conferido nao impede enviar nova versao
    assert len(cdb.fontes_por_mes(conn, "ENERGIA")["08/2026"]) == 2


def test_det_por_categoria(conn):
    cont, nome = detalhamento_sintetico(competencia="08/2026")
    eid = cdb.gravar_detalhamento(conn, "ENERGIA", cdet.ler_detalhamento(cont, nome), nome, "t")
    d = cdb.det_por_categoria(conn, eid)
    assert round(sum(v["cofins"] for v in d.values()), 2) == 494.0 and cdb.det_por_categoria(conn, None) == {}
