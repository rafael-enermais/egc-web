# -*- coding: utf-8 -*-
"""
Teste de integracao (Streamlit AppTest) da pagina Importar PDF
(app/telas/1_Importar_PDF.py) -- mesmo padrao de test_visao_grupo_app.py
e test_dashboard_projecao_app.py: a pagina INTEIRA roda de verdade (script
runner do Streamlit), com auth/conexao/db mockados, pra pegar erro de
runtime que so' aparece com o script rodando (a previa de upload em si
nao da' pra simular aqui sem um PDF real -- ver test_importacoes_ui.py
pra' a logica pura de ordenacao/agrupamento, testada isolada com dado
sintetico).

Cobre a secao "Importações recentes" no fim da pagina, que roda
INCONDICIONALMENTE no carregamento (sem precisar de upload) -- e' onde o
fix do item 5 (Rafael, 22/09/2026: BP+DRE gravados juntos perdiam a
mensagem do BP no historico) entra em producao de verdade via
importacoes_ui.agrupar_historico_importacoes.

Rodar: python3 tests/test_importar_pdf_app.py
"""
import sys
import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

import auth  # noqa: E402
import conexao  # noqa: E402
import db  # noqa: E402

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "1_Importar_PDF.py")

t0 = datetime.datetime(2026, 9, 22, 14, 30, 0)
MOCK_IMPORTACOES_BP_DRE_JUNTOS = [
    {
        "empresa_codigo": "SMG", "periodo": datetime.date(2026, 6, 30),
        "criado_em": t0 + datetime.timedelta(seconds=2), "usuario": "rafael",
        "tipo": "DRE", "nivel": "OK", "mensagem": "80 conta(s) gravada(s)",
    },
    {
        "empresa_codigo": "SMG", "periodo": datetime.date(2026, 6, 30),
        "criado_em": t0, "usuario": "rafael",
        "tipo": "BP", "nivel": "OK", "mensagem": "150 conta(s) gravada(s)",
    },
]


def test_importar_pdf_carrega_sem_excecao_e_combina_bp_dre_no_historico():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=MOCK_IMPORTACOES_BP_DRE_JUNTOS):

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao carregando Importar PDF: {at.exception}"

        textos = (
            " ".join(m.value for m in at.markdown)
            + " " + " ".join(w.value for w in at.warning)
            + " " + " ".join(c.value for c in at.caption)
        )
        # item 5: BP+DRE do mesmo clique devem aparecer JUNTOS numa linha
        # so' do historico, nao so' o DRE (o mais recente) sozinho. A ordem
        # exata (DRE, BP) segue a ordem de chegada em listar_importacoes_
        # recentes (DESC por criado_em) -- o que importa e' que os DOIS
        # tipos aparecem juntos, nao qual vem primeiro no rotulo.
        assert "DRE, BP" in textos or "BP, DRE" in textos, (
            f"esperava BP e DRE combinados no historico, texto renderizado: {textos!r}"
        )
        assert "150 conta(s) gravada(s)" in textos, "mensagem do BP nao deveria mais desaparecer do historico"
        assert "80 conta(s) gravada(s)" in textos, "mensagem do DRE deveria continuar aparecendo"
        print("OK: Importar PDF — carrega sem excecao e combina BP+DRE numa linha so' no historico (item 5)")


FAKE_RESULT_SEM_CNPJ = [
    {
        "arquivo": "arquivo_sem_cnpj.pdf",
        "bp_rows": [("ATIVO CIRCULANTE", "DISPONIVEL", 100.0, "PDF")],
        "dre_rows": [],
        "admin_itens": [],
        "log": [],
        "meta": [("Empresa Desconhecida", None, "30/06/2026", "arquivo_sem_cnpj.pdf", "BP", "SPED", None, None)],
    }
]


def test_importar_pdf_sem_cnpj_nao_defaulta_pra_energia():
    # FIX_20260928g: achado revisando egc.lancamentos -- uma linha com
    # arquivo_pdf de OUTRA empresa (Construtora) ficou gravada com
    # empresa_codigo=ENERGIA. Mecanismo mais provavel: CNPJ nao
    # identificado no PDF -> selectbox manual defaultava pra
    # EMPRESAS_FIXAS[0] (Energia) sem exigir escolha. Este teste garante
    # que o default agora e' "-- selecione --" (nao aparece pronto pra
    # gravar ate' o usuario escolher de verdade).
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=[]):
        at = AppTest.from_file(PAGE)
        at.session_state["import_resultados"] = [dict(FAKE_RESULT_SEM_CNPJ[0])]
        at.run(timeout=30)
        assert not at.exception, f"excecao com CNPJ nao identificado: {at.exception}"

        sel = at.selectbox(key="empresa_manual_0")
        assert sel.value == -1, "default deveria ser '-- selecione --' (-1), nao a 1a empresa da lista"
        assert "Nenhum arquivo pronto pra gravar ainda." in " ".join(i.value for i in at.info), (
            "sem escolher a empresa, nao deveria aparecer nenhum grupo pronto pra gravar"
        )

        # escolhe Energia (index 0) de verdade -> agora sim libera o grupo
        sel.set_value(0).run(timeout=30)
        assert not at.exception, f"excecao apos escolher empresa manualmente: {at.exception}"
        assert any("Gravar Enermais Energia Ltda" in b.label for b in at.button), (
            "apos escolha explicita, deveria aparecer o botao de gravar pra Energia"
        )
        print("OK: Importar PDF — CNPJ nao identificado nao defaulta mais silenciosamente pra Energia")


FAKE_RESULT_DRE_COM_GRANULARIDADE = [
    {
        "arquivo": "smg_dre_2trim.pdf",
        "bp_rows": [],
        "dre_rows": [("RECEITA BRUTA", 1000.0, "RECEITAS", "PDF")],
        "admin_itens": [],
        "log": [],
        "meta": [(
            "SMG Solucoes Ltda", "18.387.666/0001-00", "30/06/2026",
            "smg_dre_2trim.pdf", "DRE", "SPED", "01/04/2026", "trimestral",
        )],
    }
]


def test_importar_pdf_granularidade_editavel_default_e_override():
    # FIX_20260929c: a contadora confirma/corrige a granularidade detectada
    # antes de gravar (pedido do Rafael: "ela define e confirma se e'
    # trimestral, semestral, etc"). Cobre: (1) o selectbox aparece pra DRE
    # com o valor DETECTADO como default; (2) se ela troca pra outro valor,
    # e' ESSE que vai pro banco (nao o detectado cru).
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=[]), \
         patch.object(db, "inativar_periodo_existente", return_value=0) as mock_inativar, \
         patch.object(db, "inserir_lancamentos", return_value=1) as mock_inserir, \
         patch.object(db, "registrar_importacao", return_value=None):

        at = AppTest.from_file(PAGE)
        at.session_state["import_resultados"] = [dict(FAKE_RESULT_DRE_COM_GRANULARIDADE[0])]
        at.run(timeout=30)
        assert not at.exception, f"excecao com DRE + granularidade detectada: {at.exception}"

        sel = at.selectbox(key="granularidade_confirmada_0")
        assert sel.value == "trimestral", f"default deveria ser o detectado ('trimestral'), veio {sel.value!r}"

        # contadora corrige pra semestral (ex.: o mesmo fechamento tem os
        # dois modelos e ela decide que este PDF e' o semestral)
        sel.set_value("semestral").run(timeout=30)
        assert not at.exception, f"excecao apos corrigir granularidade: {at.exception}"

        botoes_gravar = [b for b in at.button if "Gravar" in b.label]
        assert botoes_gravar, "botao de gravar deveria estar disponivel (CNPJ resolvido, tem DRE)"
        botoes_gravar[0].click().run(timeout=30)
        assert not at.exception, f"excecao ao gravar com granularidade corrigida: {at.exception}"

        assert mock_inativar.called, "db.inativar_periodo_existente deveria ter sido chamado"
        assert mock_inativar.call_args.kwargs.get("granularidade") == "semestral", (
            f"inativar_periodo_existente deveria usar a granularidade CORRIGIDA ('semestral'), "
            f"veio {mock_inativar.call_args.kwargs.get('granularidade')!r}"
        )
        assert mock_inserir.called, "db.inserir_lancamentos deveria ter sido chamado"
        assert mock_inserir.call_args.kwargs.get("granularidade") == "semestral", (
            f"inserir_lancamentos deveria gravar a granularidade CORRIGIDA ('semestral'), "
            f"veio {mock_inserir.call_args.kwargs.get('granularidade')!r}"
        )
        print("OK: Importar PDF — granularidade editavel, default = detectada, override vai pro banco")


def test_importar_pdf_bp_nao_tem_selector_de_granularidade():
    # BP e' foto de 1 data -- nunca teve periodo_inicio/intervalo declarado,
    # entao nao teria sentido pedir pra contadora "inventar" uma cobertura
    # (REGRA DE OURO: nunca inventa dado). Confirma que so' aparece o
    # selector de granularidade pra DRE, nao pra BP.
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=[]):
        at = AppTest.from_file(PAGE)
        at.session_state["import_resultados"] = [dict(FAKE_RESULT_SEM_CNPJ[0])]  # tipo BP
        at.run(timeout=30)
        assert not at.exception, f"excecao com BP: {at.exception}"
        assert not any(s.key == "granularidade_confirmada_0" for s in at.selectbox), (
            "BP nao deveria ter selector de granularidade"
        )
        print("OK: Importar PDF — BP nao ganha selector de granularidade (so' DRE)")


FAKE_RESULT_BP_CNPJ_RESOLVIDO = [
    {
        "arquivo": "energia_bp_2trim.pdf",
        "bp_rows": [("ATIVO CIRCULANTE", "DISPONIVEL", 100.0, "PDF")],
        "dre_rows": [],
        "admin_itens": [],
        "log": [],
        "meta": [(
            "Enermais Energia Ltda", "47.040.664/0001-48", "30/06/2026",
            "energia_bp_2trim.pdf", "BP", "SPED", None, None,
        )],
    }
]


def test_importar_pdf_bp_grava_granularidade_vazia_nao_none_fix_20260930b():
    # BUG REAL em producao (30/09/2026): "Falha ao gravar: null value in
    # column granularidade... violates not-null constraint" ao gravar um
    # BP -- rastreado ate' `granularidade_confirmada or None` convertendo
    # "" (o valor normal de BP, que nunca tem selector) pra None antes de
    # chamar db.inserir_lancamentos/inativar_periodo_existente. A coluna
    # e' NOT NULL DEFAULT '' desde o bloco 16 do schema.sql -- None
    # explicito vira NULL de verdade, nao usa o DEFAULT do banco. Este
    # teste teria pegado o bug: garante que BP grava granularidade=""
    # (string vazia), nunca None, nas 2 chamadas.
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=[]), \
         patch.object(db, "inativar_periodo_existente", return_value=0) as mock_inativar, \
         patch.object(db, "inserir_lancamentos", return_value=1) as mock_inserir, \
         patch.object(db, "registrar_importacao", return_value=None):

        at = AppTest.from_file(PAGE)
        at.session_state["import_resultados"] = [dict(FAKE_RESULT_BP_CNPJ_RESOLVIDO[0])]
        at.run(timeout=30)
        assert not at.exception, f"excecao com BP + CNPJ resolvido: {at.exception}"

        botoes_gravar = [b for b in at.button if "Gravar" in b.label]
        assert botoes_gravar, "botao de gravar deveria estar disponivel (CNPJ resolvido, tem BP)"
        botoes_gravar[0].click().run(timeout=30)
        assert not at.exception, f"excecao ao gravar BP: {at.exception}"

        assert mock_inativar.called, "db.inativar_periodo_existente deveria ter sido chamado"
        assert mock_inativar.call_args.kwargs.get("granularidade") == "", (
            f"BP deveria inativar com granularidade='' (nunca None), veio "
            f"{mock_inativar.call_args.kwargs.get('granularidade')!r}"
        )
        assert mock_inserir.called, "db.inserir_lancamentos deveria ter sido chamado"
        assert mock_inserir.call_args.kwargs.get("granularidade") == "", (
            f"BP deveria gravar granularidade='' (nunca None -- quebra a constraint NOT NULL), veio "
            f"{mock_inserir.call_args.kwargs.get('granularidade')!r}"
        )
        print("OK: Importar PDF — BP grava granularidade='' (nao None), nao quebra a constraint NOT NULL")


def test_importar_pdf_historico_vazio_sem_excecao():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=[]):

        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao com historico vazio: {at.exception}"
        textos = " ".join(m.value for m in at.caption)
        assert "Nenhuma importação registrada ainda." in textos
        print("OK: Importar PDF — historico vazio nao quebra a pagina")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in testes:
        try:
            t()
        except AssertionError as e:
            falhas += 1
            print(f"FALHOU: {t.__name__} — {e}")
        except Exception as e:
            falhas += 1
            print(f"ERRO (nao AssertionError) em {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
