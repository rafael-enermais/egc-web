# -*- coding: utf-8 -*-
"""v0.50.0 -- Creditos PIS/COFINS no Postgres de verdade (bloco 28): envios com arquivar/restaurar, ajustes com trilha, historico e serie mensal."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_support as pg  # noqa: E402

pytestmark = [pytest.mark.pg, pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel")]

import creditos_conferencia as cf  # noqa: E402
import creditos_db as cdb  # noqa: E402
import creditos_detalhamento as cdet  # noqa: E402
import sped_contribuicoes as sc  # noqa: E402
from creditos_fixtures import detalhamento_sintetico, sped_sintetico  # noqa: E402


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    yield c
    c.close()


def _sped(c, comp="07/2026", **kw):
    cont, nome = sped_sintetico(competencia=comp, **kw)
    return cdb.gravar_sped(c, "ENERGIA", sc.ler_sped(cont, nome), nome, "t")


def _det(c, comp="07/2026", **kw):
    cont, nome = detalhamento_sintetico(competencia=comp, **kw)
    return cdb.gravar_detalhamento(c, "ENERGIA", cdet.ler_detalhamento(cont, nome), nome, "t")


def test_bloco28_existe(conn):
    assert cdb.tem_bloco28(conn)


def test_gravar_e_carregar_voltam_iguais_e_a_conferencia_fecha(conn):
    _sped(conn)
    _det(conn)
    s, d = cdb.carregar_sped(conn, "ENERGIA", "07/2026"), cdb.carregar_detalhamento(conn, "ENERGIA", "07/2026")
    assert round(s["creditos"]["pis"].sum(), 2) == 127.05 and len(s["creditos"]) == 5 and len(s["receitas"]) == 1
    assert s["resumo"]["credito_cofins"] == 585.2 and s["por_categoria"]["Aluguel"]["pis"] == 33.0
    assert round(d["linhas"]["cofins"].sum(), 2) == 494.0 and len(d["debitos"]) == 13
    L = cf.conferir_linhas(s, d)
    assert (L[L["nivel"] == "Documento"]["status"] == cf.S_OK).all()
    assert (cf.conferir_debitos(s, d)["status"] == cf.S_OK).all() and (cf.conferir_totais(s, d)["status"] == cf.S_OK).all()


def test_reenviar_arquiva_o_anterior_e_nada_e_apagado(conn):
    a = _sped(conn)
    b = _sped(conn, ajuste_servico=5.0, tipo="1")
    vig = [e for e in cdb.listar_envios(conn, "ENERGIA") if e["tipo"] == "SPED"]
    assert [e["id"] for e in vig] == [b]
    todos = [e for e in cdb.listar_envios(conn, "ENERGIA", incluir_arquivados=True) if e["tipo"] == "SPED"]
    assert {e["id"] for e in todos} == {a, b}
    assert cdb.carregar_sped(conn, "ENERGIA", "07/2026")["resumo"]["credito_pis"] == 132.05
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM egc.cred_sped_linha WHERE envio_id = %s", (a,))
        assert cur.fetchone()[0] == 5                      # linhas do envio arquivado continuam la'


def test_arquivar_e_restaurar_envio(conn):
    a = _sped(conn)
    cdb.arquivar_envio(conn, "SPED", a, "t", "teste")
    assert cdb.carregar_sped(conn, "ENERGIA", "07/2026") is None and cdb.competencias(conn, "ENERGIA") == []
    b = _sped(conn)
    with pytest.raises(ValueError):
        cdb.restaurar_envio(conn, "SPED", a, "t")            # mes ja' tem outro vigente
    cdb.arquivar_envio(conn, "SPED", b, "t")
    cdb.restaurar_envio(conn, "SPED", a, "t")
    assert cdb.carregar_sped(conn, "ENERGIA", "07/2026")["envio_id"] == a


def test_sha_vigente_e_competencias_ordenadas(conn):
    cont, nome = sped_sintetico()
    _sped(conn)
    _sped(conn, comp="08/2026")
    _det(conn, comp="06/2026")
    assert cdb.sha_vigente(conn, "SPED", "ENERGIA", "07/2026") == sc.sha256(cont)
    assert cdb.sha_vigente(conn, "SPED", "ENERGIA", "05/2026") is None
    assert cdb.competencias(conn, "ENERGIA") == ["08/2026", "07/2026", "06/2026"]


def test_ajustes_gravam_com_trilha_e_sobrevivem_a_novo_envio(conn):
    _sped(conn)
    _det(conn)
    n = cdb.salvar_ajustes(conn, "ENERGIA", "07/2026", "linhas",
                           [{"chave": "11111111000111|1001", "campo": "pis_det", "valor_original": "16.5", "valor_novo": "10"}], "ana")
    assert n == 1
    cdb.salvar_ajustes(conn, "ENERGIA", "07/2026", "linhas",
                       [{"chave": "11111111000111|1001", "campo": "pis_det", "valor_original": "16.5", "valor_novo": "12"}], "bia")
    aj = cdb.carregar_ajustes(conn, "ENERGIA", "07/2026")
    assert len(aj) == 1 and aj[0]["valor_novo"] == "12" and aj[0]["valor_original"] == "16.5" and aj[0]["editado_por"] == "bia"
    lg = cdb.log_ajustes(conn, "ENERGIA", "07/2026")
    assert list(lg["valor_depois"]) == ["12", "10"] and list(lg["valor_antes"]) == ["10", "16.5"]
    _sped(conn)                                                   # novo envio do mes: a edicao continua valendo
    assert len(cdb.carregar_ajustes(conn, "ENERGIA", "07/2026")) == 1


def test_restaurar_ajustes_remove_e_registra(conn):
    cdb.salvar_ajustes(conn, "ENERGIA", "07/2026", "linhas", [{"chave": "k", "campo": "pis_det", "valor_original": "1", "valor_novo": "2"},
                                                               {"chave": "k", "campo": "nota_contadora", "valor_original": "", "valor_novo": "x"}], "t")
    assert cdb.restaurar_ajustes(conn, "ENERGIA", "07/2026", "linhas", "k", "t") == 2
    assert cdb.carregar_ajustes(conn, "ENERGIA", "07/2026") == []
    assert set(cdb.log_ajustes(conn, "ENERGIA", "07/2026")["acao"]) == {"editar", "restaurar"}


def test_historico_e_serie_mensal(conn):
    cdb.upsert_historico(conn, "ENERGIA", [
        {"competencia": "01/2026", "versao": "ORIGINAL", "sped_tipo": "Original", "resumo": {"credito_pis": 80, "credito_cofins": 370, "debito_nc_pis": 10}},
        {"competencia": "01/2026", "versao": "APONTADO", "sped_tipo": "Retificadora", "resumo": {"credito_pis": 100, "credito_cofins": 450},
         "por_categoria": {"Aluguel": {"pis": 100, "cofins": 450, "linhas": 2}}, "dif_fornecedores": [{"fornecedor": "A", "dif_pis": 20, "dif_cofins": 80}]},
        {"competencia": "02/2026", "versao": "APONTADO", "sped_tipo": "Retificadora", "resumo": {"credito_pis": 50, "credito_cofins": 200}}])
    cdb.upsert_historico(conn, "ENERGIA", [{"competencia": "02/2026", "versao": "APONTADO", "sped_tipo": "Retificadora", "resumo": {"credito_pis": 60, "credito_cofins": 200}}])
    _sped(conn)                                                    # jul/2026 vem do envio vigente
    s = cdb.serie_mensal(conn, "ENERGIA").set_index("competencia")
    assert list(s.index) == ["01/2026", "02/2026", "07/2026"]
    assert s.loc["01/2026", "origem"] == "HISTORICO" and bool(s.loc["01/2026", "tem_original"]) and s.loc["01/2026", "orig_pis"] == 80
    assert s.loc["01/2026", "cred_pis"] == 100 and s.loc["02/2026", "cred_pis"] == 60 and not bool(s.loc["02/2026", "tem_original"])
    assert s.loc["07/2026", "origem"] == "ENVIO" and s.loc["07/2026", "cred_pis"] == 127.05 and s.loc["07/2026", "recolher_cofins"] == 1164.8
    assert s.loc["01/2026", "dif_fornecedores"][0]["fornecedor"] == "A"


def test_mes_da_carga_fixa_nao_e_sobrescrito_por_envio_do_mesmo_mes(conn):
    cdb.upsert_historico(conn, "ENERGIA", [{"competencia": "07/2026", "versao": "APONTADO", "sped_tipo": "Retificadora",
                                            "resumo": {"credito_pis": 999, "credito_cofins": 1}}])
    _sped(conn)                                                    # SPED original de jul enviado so' para conferir com o Detalhamento
    s = cdb.serie_mensal(conn, "ENERGIA").set_index("competencia")
    assert s.loc["07/2026", "origem"] == "HISTORICO" and s.loc["07/2026", "cred_pis"] == 999


def test_serie_mensal_vazia(conn):
    assert cdb.serie_mensal(conn, "ENERGIA").empty


def test_envio_com_nan_e_vazio_nao_quebra_json(conn):
    cont, nome = sped_sintetico()
    s = sc.ler_sped(cont, nome)
    s["creditos"].loc[0, "fornecedor"] = float("nan")
    cdb.gravar_sped(conn, "ENERGIA", s, nome, "t")
    assert len(cdb.carregar_sped(conn, "ENERGIA", "07/2026")["creditos"]) == 5
