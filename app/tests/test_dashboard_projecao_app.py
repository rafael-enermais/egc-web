# -*- coding: utf-8 -*-
"""
AppTest de integracao pra app/telas/5_Dashboard_Projecao.py -- mesmo
padrao ja validado em test_visao_grupo_app.py (auth/conexao/db mockados
via patch.object, 1 UNICA instancia de AppTest reaproveitada entre
cenarios -- criar varias instancias separadas no mesmo processo faz a
2a+ perder st.dataframe silenciosamente, bug de framework ja mapeado).

Historico mockado de proposito com valor: Decimal (nao float) -- mesma
razao do teste da Visao Grupo: psycopg2 devolve NUMERIC como Decimal, e
a Visao Grupo ja teve um TypeError real (Decimal + float) que so' um
teste com Decimal de verdade capturaria. Aqui o dado passa por
projecao.gerar_baseline -> numpy.polyfit, entao o mesmo risco existe
(numpy nao mistura bem com Decimal) alem do float() que a propria pagina
ja faz nas linhas 78/87 antes de chamar o motor.

Cobre:
  1. carga default (BP, horizonte 3) com 2 contas -- uma com so' 2
     periodos (cai em flat_ultimo_valor) e outra com 6 periodos em
     tendencia linear perfeita (cai em tendencia_linear) -- confirma que
     a tabela resumo, o grafico e a escolha de metodo por conta func
     cionam juntos sem excecao.
  2. troca de tipo (BP -> DRE) -- funciona porque os mocks ignoram os
     argumentos recebidos (retornam sempre o mesmo fixture).
  3. adicionar ajuste manual (preenche motivo+valor, clica "+ Adicionar
     ajuste") -- confirma que o botao chama db.salvar_ajuste_projecao
     com os argumentos certos e sobrevive ao st.rerun() interno.
  4. remover ajuste existente (com 1 ajuste ja "ativo" no mock) -- clica
     "Remover" e confirma que db.inativar_ajuste_projecao e chamado com
     o id certo.
  5. gravar projecao no banco -- clica "Gravar projeção no banco" e
     confirma que db.gravar_projecoes recebe as linhas combinadas
     (baseline + ajuste) com empresa_codigo/tipo anexados.
"""
import sys
import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402
import conexao  # noqa: E402
import db  # noqa: E402

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "5_Dashboard_Projecao.py")


def _p(ano, mes):
    import calendar
    return datetime.date(ano, mes, calendar.monthrange(ano, mes)[1])


# CLIENTES: 6 periodos consecutivos, tendencia linear PERFEITA (+1000/mes)
# -> deve cair em "tendencia_linear" (4 <= n < 24).
HIST_CLIENTES = [
    (_p(2026, 1), Decimal("100000.00")),
    (_p(2026, 2), Decimal("101000.00")),
    (_p(2026, 3), Decimal("102000.00")),
    (_p(2026, 4), Decimal("103000.00")),
    (_p(2026, 5), Decimal("104000.00")),
    (_p(2026, 6), Decimal("105000.00")),
]
# DISPONIVEL: so' 2 periodos -> deve cair em "flat_ultimo_valor" (n < 4).
HIST_DISPONIVEL = [
    (_p(2026, 5), Decimal("749.70")),
    (_p(2026, 6), Decimal("820.15")),
]

MOCK_HISTORICO = [
    {"periodo": p, "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": v}
    for p, v in HIST_CLIENTES
] + [
    {"periodo": p, "grupo": "ATIVO CIRCULANTE", "conta": "DISPONIVEL", "valor": v}
    for p, v in HIST_DISPONIVEL
]

MOCK_AJUSTES = [
    {
        "id": 42,
        "periodo": _p(2026, 7),
        "grupo": "ATIVO CIRCULANTE",
        "conta": "CLIENTES",
        "valor_ajuste": Decimal("5000.00"),
        "descricao": "Contrato novo fechando em 07/2026",
        "usuario": "teste@enermais.com.br",
        "criado_em": datetime.datetime(2026, 6, 20, 10, 0, 0),
    }
]


def run():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_historico_grupo", return_value=MOCK_HISTORICO) as m_hist, \
         patch.object(db, "listar_ajustes_projecao", return_value=MOCK_AJUSTES) as m_ajustes, \
         patch.object(db, "salvar_ajuste_projecao", return_value=99) as m_salvar, \
         patch.object(db, "inativar_ajuste_projecao", return_value=1) as m_inativar, \
         patch.object(db, "gravar_projecoes", return_value=0) as m_gravar:

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        print("Carga default (BP) exception:", at.exception)
        assert not at.exception, f"FALHOU carga default: {at.exception[0] if at.exception else None}"
        assert len(at.dataframe) >= 1, "esperava a tabela resumo renderizada"
        tabela = at.dataframe[0].value
        assert len(tabela) == 2, f"esperava 2 contas na tabela (CLIENTES + DISPONIVEL), veio {len(tabela)}"
        metodos = set(tabela["Método"])
        assert metodos == {"Tendência linear", "Flat (último valor)"}, f"metodos inesperados: {metodos}"
        print("Carga default OK - metodos por conta:", metodos)

        # historico chega em db.listar_historico_grupo, nao em projecao direto --
        # confirma que a pagina de fato usa o mock (chamado >=1x) e nao dado real
        assert m_hist.call_count >= 1

        # --- troca BP -> DRE (mocks ignoram tipo, mas exercita o rerender) ---
        at.radio(key="projecao_tipo_sel").set_value("DRE")
        at.run(timeout=30)
        print("Troca pra DRE exception:", at.exception)
        assert not at.exception, f"FALHOU troca DRE: {at.exception[0] if at.exception else None}"

        # --- adicionar ajuste manual ---
        at.text_input(key="projecao_ajuste_descricao").set_value("Evento pontual de teste")
        at.number_input(key="projecao_ajuste_valor").set_value(1500.0)
        at.button[0].click()  # "+ Adicionar ajuste" -- 1o botao na ordem de renderizacao
        at.run(timeout=30)
        print("Adicionar ajuste exception:", at.exception)
        assert not at.exception, f"FALHOU adicionar ajuste: {at.exception[0] if at.exception else None}"
        assert m_salvar.call_count == 1, f"esperava 1 chamada a salvar_ajuste_projecao, veio {m_salvar.call_count}"
        args, kwargs = m_salvar.call_args
        assert args[-3] == 1500.0, f"valor do ajuste nao bateu: {args}"
        assert args[-2] == "Evento pontual de teste", f"descricao nao bateu: {args}"
        print("Adicionar ajuste OK - salvar_ajuste_projecao chamado com:", args[2:])

        # --- remover ajuste existente (mock ja tem 1 ajuste ativo, id=42) ---
        botao_remover = next(b for b in at.button if b.key == "remover_ajuste_42")
        botao_remover.click()
        at.run(timeout=30)
        print("Remover ajuste exception:", at.exception)
        assert not at.exception, f"FALHOU remover ajuste: {at.exception[0] if at.exception else None}"
        assert m_inativar.call_count == 1
        assert m_inativar.call_args[0][1] == 42, f"id do ajuste removido nao bateu: {m_inativar.call_args}"
        print("Remover ajuste OK - inativar_ajuste_projecao chamado com id=42")

        # --- gravar projecao no banco ---
        botao_gravar = next(b for b in at.button if b.key is None and "Gravar" in (b.label or ""))
        botao_gravar.click()
        at.run(timeout=30)
        print("Gravar projecao exception:", at.exception)
        assert not at.exception, f"FALHOU gravar projecao: {at.exception[0] if at.exception else None}"
        assert m_gravar.call_count == 1
        linhas_gravadas = m_gravar.call_args[0][1]
        assert len(linhas_gravadas) > 0, "esperava pelo menos 1 linha combinada (baseline+ajuste) pra gravar"
        assert all("empresa_codigo" in l and "tipo" in l for l in linhas_gravadas), \
            "cada linha gravada precisa ter empresa_codigo/tipo anexados (materializacao pro TIA.go)"
        assert all(isinstance(l["valor_projetado"], float) for l in linhas_gravadas), \
            "valor_projetado precisa ser float puro (nao Decimal) na hora de gravar"
        print("Gravar projecao OK -", len(linhas_gravadas), "linha(s) combinadas gravadas")

    print("\nTODOS OS CENARIOS OK (carga, troca tipo, ajuste manual add/remove, gravar) -- "
          "Decimal do historico e dos ajustes nao quebra o motor de projecao (numpy.polyfit)")


if __name__ == "__main__":
    try:
        run()
        sys.exit(0)
    except AssertionError as e:
        print(f"FALHOU: {e}")
        sys.exit(1)
