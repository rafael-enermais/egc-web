# -*- coding: utf-8 -*-
"""v0.50.0 -- script da carga fixa do historico (original x apontado) com SPED sinteticos; o SQL gerado roda no Postgres de verdade."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import gerar_historico_creditos as gh  # noqa: E402
from creditos_fixtures import sped_sintetico  # noqa: E402


def _pasta(tmp_path):
    def grava(comp, tipo, ts, **kw):
        c, _ = sped_sintetico(competencia=comp, tipo=tipo, **kw)
        mm, aaaa = comp.split("/")
        nome = f"PISCOFINS_{aaaa}{mm}01_{aaaa}{mm}28_47040664000148_{'Original' if tipo == '0' else 'Retificadora'}_{ts}_AB.txt"
        (tmp_path / nome).write_bytes(c)
    grava("01/2026", "0", "20260312111432")                                 # original
    grava("01/2026", "1", "20260501093241", ajuste_servico=2.0)             # 1a retificadora
    grava("01/2026", "1", "20260929082841", ajuste_servico=5.0)             # ultima = apontado
    grava("03/2026", "0", "20260504080621")                                 # so' uma versao
    grava("07/2026", "0", "20260825102110")
    grava("07/2026", "1", "20260929120000", ajuste_servico=-3.0)
    grava("08/2026", "0", "20261001000000")                                 # fora da carga fixa (--ate 07/2026)
    # fev/2026: original VAZIO (so' cabecalho) + retificadora com conteudo -> a retificadora e' a unica versao com conteudo
    vazio = "\r\n".join(["|0000|006|0||01022026|28022026|ENERMAIS ENERGIA LTDA|47040664000148|MG|", "|9999|2|"]) + "\r\n"
    (tmp_path / "PISCOFINS_20260201_20260228_47040664000148_Original_20260415192716_AB.txt").write_bytes(vazio.encode("latin-1"))
    grava("02/2026", "1", "20260502144430")
    return tmp_path


def test_regra_original_primeira_apontado_ultima(tmp_path):
    linhas = gh.montar_linhas(gh.ler_pasta(_pasta(tmp_path)), "07/2026")
    por = {(l["competencia"], l["versao"]): l for l in linhas}
    assert sorted(por) == [("01/2026", "APONTADO"), ("01/2026", "ORIGINAL"), ("02/2026", "APONTADO"), ("03/2026", "APONTADO"),
                           ("07/2026", "APONTADO"), ("07/2026", "ORIGINAL")]
    assert por[("01/2026", "ORIGINAL")]["resumo"]["credito_pis"] == 127.05 and por[("01/2026", "APONTADO")]["resumo"]["credito_pis"] == 132.05
    assert por[("01/2026", "APONTADO")]["sped_tipo"] == "Retificadora" and por[("07/2026", "APONTADO")]["resumo"]["credito_pis"] == 124.05
    assert por[("02/2026", "APONTADO")]["sped_tipo"] == "Retificadora"          # original vazio ignorado; nao ha' 'ORIGINAL' de fev
    assert ("08/2026", "APONTADO") not in por


def test_dif_fornecedores_so_no_apontado(tmp_path):
    por = {(l["competencia"], l["versao"]): l for l in gh.montar_linhas(gh.ler_pasta(_pasta(tmp_path)), "07/2026")}
    d = por[("01/2026", "APONTADO")]["dif_fornecedores"]
    assert d and d[0]["dif_pis"] == 5.0 and d[0]["fornecedor"] == "FORNECEDOR DE SERVICO A"
    assert por[("01/2026", "ORIGINAL")]["dif_fornecedores"] == [] and por[("03/2026", "APONTADO")]["dif_fornecedores"] == []


def test_sql_gerado_roda_no_postgres_e_alimenta_a_serie_mensal(tmp_path):
    import pg_support as pg
    if not pg.pg_disponivel():
        pytest.skip("sem Postgres disponivel")
    import creditos_db as cdb
    linhas = gh.montar_linhas(gh.ler_pasta(_pasta(tmp_path)), "07/2026")
    sql = gh.gerar_sql("ENERGIA", linhas)
    assert "BEGIN;" in sql and sql.count("INSERT INTO egc.cred_historico") == 6 and "ON CONFLICT" in sql
    c = pg.conectar_limpo()
    with c.cursor() as cur:
        cur.execute(sql)                      # 1a vez
        cur.execute(sql)                      # idempotente
        cur.execute("SELECT count(*) FROM egc.cred_historico")
        assert cur.fetchone()[0] == 6
    s = cdb.serie_mensal(c, "ENERGIA").set_index("competencia")
    assert list(s.index) == ["01/2026", "02/2026", "03/2026", "07/2026"]
    assert s.loc["01/2026", "orig_pis"] == 127.05 and s.loc["01/2026", "cred_pis"] == 132.05 and bool(s.loc["01/2026", "tem_original"])
    assert not bool(s.loc["03/2026", "tem_original"]) and s.loc["03/2026", "recolher_pis"] == 227.95
    assert s.loc["01/2026", "dif_fornecedores"][0]["dif_pis"] == 5.0
    c.close()


def test_apostrofo_no_nome_nao_quebra_o_sql():
    assert gh._sql_str("D'Água") == "'D''Água'" and gh._sql_str(None) == "NULL"
