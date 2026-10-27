# -*- coding: utf-8 -*-
"""v0.54.0 -- Fechamento do ano contra Postgres REAL: leituras da grade, correcao de rotulo (caso ENG 09/2026),
tela abrindo (AppTest) e atalho para o Relatorio Comentado."""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support as pg  # noqa: E402

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not pg.pg_disponivel(), reason="sem Postgres disponivel"),
]

import auth  # noqa: E402
import conexao  # noqa: E402
import dados_relatorio_comentado as drc  # noqa: E402
import db  # noqa: E402
import fechamento_ano as fa  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parent.parent
P_SET = date(2026, 9, 30)
DRE = [("RESULTADO", "RECEITA OPERACIONAL LIQUIDA", 1000.0), ("RESULTADO", "LUCRO LIQUIDO DO EXERCICIO", 100.0)]
BP = [("ATIVO", "TOTAL DO ATIVO", 10.0)]


@pytest.fixture()
def conn():
    c = pg.conectar_limpo()
    yield c
    c.close()


def _eng_gravado_como_trimestral(conn):
    # exatamente o caso real: PDF acumulado 01/01 a 30/09 gravado como 'trimestral'
    pg.gravar_documento(conn, "ENG", P_SET, "trimestral", dre=DRE, bp=BP, periodo_inicio=date(2026, 1, 1),
                        admin_itens=[("PRO-LABORE", 5.0)])


def test_grade_le_documentos_ativos_do_ano(conn):
    _eng_gravado_como_trimestral(conn)
    pg.gravar_documento(conn, "ENG", date(2025, 12, 31), "anual", dre=DRE, bp=BP, periodo_inicio=date(2025, 1, 1))
    pg.gravar_documento(conn, "ENG", date(2026, 6, 30), "semestral", dre=DRE, periodo_inicio=date(2026, 1, 1),
                        status="INATIVO")
    linhas = db.listar_documentos_grade(conn, ["ENG", "SOL"], 2026)
    assert len(linhas) == 1  # o do ano anterior e o INATIVO ficam de fora
    d = fa.docs_de_linhas(linhas)[0]
    assert (d.empresa, d.fim, d.granularidade, d.tem_bp, d.tem_dre, d.inicio) == \
        ("ENG", P_SET, "trimestral", True, True, date(2026, 1, 1))
    assert db.listar_documentos_grade(conn, [], 2026) == []


def test_corrigir_rotulo_9meses_move_lancamentos_e_derivadas_e_libera_o_relatorio(conn):
    _eng_gravado_como_trimestral(conn)
    # antes: o guard do relatorio barra o documento (mensagem agora aponta para a correcao)
    with pytest.raises(ValueError, match="Corrigir granularidade"):
        drc.validar_cobertura_documento(conn, "ENG", P_SET, "trimestral")
    res = db.corrigir_granularidade_documento(conn, "ENG", P_SET, "trimestral", "9meses", usuario="teste")
    assert res["lancamentos"] == len(DRE) + len(BP)
    assert res["derivadas"]["despesas_admin_itens"] == 1
    det = db.listar_periodos_completos_detalhado(conn, "ENG")
    assert [(d["periodo"], d["granularidade"]) for d in det] == [(P_SET, "9meses")]
    drc.validar_cobertura_documento(conn, "ENG", P_SET, "9meses")  # agora coerente: nao levanta
    with conn.cursor() as cur:
        cur.execute("SELECT granularidade FROM egc.despesas_admin_itens WHERE empresa_codigo='ENG'")
        assert [r[0] for r in cur.fetchall()] == ["9meses"]
        cur.execute("SELECT mensagem FROM egc.eventos_sistema WHERE origem='fechamento_ano'")
        assert "'trimestral' -> '9meses'" in cur.fetchone()[0]


def test_corrigir_rotulo_recusa_sem_alterar_nada(conn):
    _eng_gravado_como_trimestral(conn)
    pg.gravar_documento(conn, "ENG", P_SET, "9meses", dre=DRE)   # destino ja' ocupado (DRE)
    with pytest.raises(ValueError, match="Já existe documento ATIVO"):
        db.corrigir_granularidade_documento(conn, "ENG", P_SET, "trimestral", "9meses")
    with pytest.raises(ValueError, match="inválida"):
        db.corrigir_granularidade_documento(conn, "ENG", P_SET, "trimestral", "quinzenal")
    with pytest.raises(ValueError, match="igual"):
        db.corrigir_granularidade_documento(conn, "ENG", P_SET, "trimestral", "trimestral")
    with pytest.raises(ValueError, match="Não há documento ATIVO"):
        db.corrigir_granularidade_documento(conn, "SOL", P_SET, "trimestral", "9meses")
    with conn.cursor() as cur:
        cur.execute("SELECT granularidade, count(*) FROM egc.lancamentos WHERE empresa_codigo='ENG' "
                    "AND status='ATIVO' GROUP BY 1 ORDER BY 1")
        assert cur.fetchall() == [("9meses", len(DRE)), ("trimestral", len(DRE) + len(BP))]


def test_conferencia_com_banco_1t_2t_1s(conn):
    def dre(rl):
        return [("RESULTADO", "RECEITA OPERACIONAL LIQUIDA", rl)]
    pg.gravar_documento(conn, "ENG", date(2026, 3, 31), "trimestral", dre=dre(100.0))
    pg.gravar_documento(conn, "ENG", date(2026, 6, 30), "trimestral", dre=dre(200.0))
    pg.gravar_documento(conn, "ENG", date(2026, 6, 30), "semestral", dre=dre(350.0))
    linhas = db.listar_valores_dre_documentos(conn, ["ENG"], 2026, list(fa.CONTAS_CONFERIDAS))
    av = fa.conferir_composicoes(2026, fa.valores_de_linhas(2026, linhas))
    assert len(av) == 1 and av[0].todo == "1S" and av[0].diferenca == -50.0


# ───────────── tela ─────────────
def _com_banco(c):
    from contextlib import ExitStack
    st_ = ExitStack()
    st_.enter_context(patch.object(auth, "usuario_atual", return_value="contadora@enermais.com.br"))
    st_.enter_context(patch.object(conexao, "get_conn", return_value=c))
    return st_


def _marcar_ano_2026(at):
    at.selectbox(key="fechamento_ano_sel").select(2026)
    at.run(timeout=60)


def test_tela_abre_vazia_e_mostra_grade(conn):
    with _com_banco(conn):
        at = AppTest.from_file(str(APP / "telas" / "11_Fechamento_do_Ano.py"))
        at.run(timeout=60)
        assert not at.exception
        assert len(at.dataframe) == 1 and at.dataframe[0].value.shape[0] == 6
        assert any("Nenhuma divergência" in s.value for s in at.success)


def test_tela_sugere_e_corrige_rotulo_eng(conn):
    _eng_gravado_como_trimestral(conn)
    with _com_banco(conn):
        at = AppTest.from_file(str(APP / "telas" / "11_Fechamento_do_Ano.py"))
        at.run(timeout=60)
        _marcar_ano_2026(at)
        assert not at.exception
        assert any("Acumulado 9 meses" in m.value for m in at.markdown)
        botao = next(b for b in at.button if b.label == "Corrigir granularidade")
        assert botao.disabled                       # exige a confirmacao antes
        next(c for c in at.checkbox if c.label.startswith("Confirmo")).check()
        at.run(timeout=60)
        next(b for b in at.button if b.label == "Corrigir granularidade").click()
        at.run(timeout=60)
        assert not at.exception, [str(e.value) for e in at.exception]
        assert any("rótulo corrigido" in s.value for s in at.success)
    det = db.listar_periodos_completos_detalhado(conn, "ENG")
    assert [(d["periodo"], d["granularidade"]) for d in det] == [(P_SET, "9meses")]


def test_atalho_abre_o_comparativo_ja_com_os_periodos(conn):
    for m, d in ((3, 31), (6, 30)):
        pg.gravar_documento(conn, "ENG", date(2026, m, d), "trimestral", dre=DRE, bp=BP,
                            periodo_inicio=date(2026, m - 2, 1))
    with _com_banco(conn):
        at = AppTest.from_file(str(APP / "telas" / "8_Relatorio_Comentado.py"))
        at.session_state["relatorio_preset"] = {
            "empresas": ["ENG"], "pares": [(date(2026, 3, 31), "trimestral"), (date(2026, 6, 30), "trimestral")],
        }
        at.run(timeout=90)
        assert not at.exception, [str(e.value) for e in at.exception]
        assert at.radio(key="relatorio_tipo").value == "Comparativo / Evolução"
        ms = next(m for m in at.multiselect if m.label == "Períodos (data + base)")
        assert [tuple(v) if not isinstance(v, str) else v for v in ms.value] != []
        assert len(ms.value) == 2
