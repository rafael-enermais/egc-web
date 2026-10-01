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
from importacoes_ui import assinatura_item  # noqa: E402

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

        sel = at.selectbox(key=f"empresa_manual_{assinatura_item(FAKE_RESULT_SEM_CNPJ[0])}")
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

        sel = at.selectbox(key=f"granularidade_confirmada_{assinatura_item(FAKE_RESULT_DRE_COM_GRANULARIDADE[0])}")
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
        assert not any(s.key == f"granularidade_confirmada_{assinatura_item(FAKE_RESULT_SEM_CNPJ[0])}" for s in at.selectbox), (
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


FAKE_LOTE_BP_DRE_SEPARADOS_MESMO_PERIODO = [
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
    },
    {
        "arquivo": "energia_dre_2trim.pdf",
        "bp_rows": [],
        "dre_rows": [("RECEITA BRUTA", 1000.0, "RECEITAS", "PDF")],
        "admin_itens": [],
        "log": [],
        "meta": [(
            "Enermais Energia Ltda", "47.040.664/0001-48", "30/06/2026",
            "energia_dre_2trim.pdf", "DRE", "SPED", "01/04/2026", "trimestral",
        )],
    },
]


def test_importar_pdf_bp_herda_granularidade_do_dre_irmao_fix_20260930c():
    # BUG REAL em producao (30/09/2026): Rafael subiu e gravou BP+DRE do
    # MESMO fechamento (2o Trimestre 2026, 2 arquivos separados) e depois
    # o Relatorio Comentado nao achava os dados -- o seletor de periodo
    # mostrava "30/06/2026" e "30/06/2026 — Trimestral" como 2 OPCOES
    # SEPARADAS pro mesmo fechamento. Causa: BP (sem periodo_inicio
    # declarado) sempre gravava com granularidade="", enquanto o DRE
    # irmao (mesma empresa+periodo, mesmo lote) gravava com a
    # granularidade confirmada ("trimestral") -- iam pra buckets
    # diferentes na chave ativa. Fix: BP "puro" (arquivo so' com BP)
    # herda a granularidade confirmada do DRE irmao no MESMO lote.
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=[]), \
         patch.object(db, "inativar_periodo_existente", return_value=0) as mock_inativar, \
         patch.object(db, "inserir_lancamentos", return_value=1) as mock_inserir, \
         patch.object(db, "registrar_importacao", return_value=None):

        at = AppTest.from_file(PAGE)
        at.session_state["import_resultados"] = [dict(x) for x in FAKE_LOTE_BP_DRE_SEPARADOS_MESMO_PERIODO]
        at.run(timeout=30)
        assert not at.exception, f"excecao com BP+DRE separados do mesmo periodo: {at.exception}"

        # DRE mantem o default detectado (nao mexeu no seletor)
        sel = at.selectbox(key=f"granularidade_confirmada_{assinatura_item(FAKE_LOTE_BP_DRE_SEPARADOS_MESMO_PERIODO[1])}")  # DRE e' o 2o item do lote
        assert sel.value == "trimestral"

        botoes_gravar = [b for b in at.button if "Gravar" in b.label]
        assert botoes_gravar, "botao de gravar deveria estar disponivel"
        botoes_gravar[0].click().run(timeout=30)
        assert not at.exception, f"excecao ao gravar BP+DRE do mesmo periodo: {at.exception}"

        assert mock_inserir.call_count == 2, f"esperava 2 chamadas (BP e DRE), veio {mock_inserir.call_count}"
        granularidades_gravadas = {c.kwargs.get("granularidade") for c in mock_inserir.call_args_list}
        assert granularidades_gravadas == {"trimestral"}, (
            f"BP deveria herdar a granularidade do DRE irmao ('trimestral') -- os 2 tem que gravar "
            f"com a MESMA granularidade, veio {granularidades_gravadas!r}"
        )
        print("OK: Importar PDF — BP herda granularidade do DRE irmão do mesmo período/lote")


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


def _bp(nome, n):
    return {
        "arquivo": nome,
        "bp_rows": [("ATIVO CIRCULANTE", f"CONTA {i}", 100.0, "PDF") for i in range(n)],
        "dre_rows": [], "admin_itens": [], "log": [],
        "meta": [("Enermais Energia Ltda", "47.040.664/0001-48", "31/12/2025", nome, "BP", "SPED", None, None)],
    }


def test_importar_pdf_dois_bp_mesmo_periodo_bloqueia_ate_escolher_01_10():
    # Pergunta do Rafael (01/10/2026): 2 BP da Energia 31/12/2025 no mesmo lote
    # (33 e 38 contas). Antes: o ultimo substituia o primeiro em silencio.
    # Agora: erro + selectbox "manter qual", Gravar desabilitado ate escolher.
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=[]), \
         patch.object(db, "listar_documentos_ativos", return_value=[]), \
         patch.object(db, "inativar_periodo_existente", return_value=0), \
         patch.object(db, "inserir_lancamentos", return_value=1) as mock_inserir, \
         patch.object(db, "registrar_importacao", return_value=None):
        at = AppTest.from_file(PAGE)
        at.session_state["import_resultados"] = [_bp("Balanco 2025.pdf", 33), _bp("BP SPED 2025.pdf", 38)]
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert any("MESMO BP" in e.value for e in at.error), [e.value for e in at.error]
        botao = [b for b in at.button if "Gravar" in b.label][0]
        assert botao.disabled, "Gravar deveria ficar desabilitado com conflito aberto"

        sel = [x for x in at.selectbox if x.label.startswith("Manter qual BP")][0]
        sel.select("BP SPED 2025.pdf").run(timeout=30)
        assert not at.exception, at.exception
        botao = [b for b in at.button if "Gravar" in b.label][0]
        assert not botao.disabled
        botao.click().run(timeout=30)
        assert not at.exception, at.exception
        assert mock_inserir.call_count == 1
        assert mock_inserir.call_args.args[5] == "BP SPED 2025.pdf", mock_inserir.call_args
        print("OK: Importar PDF — 2 BP do mesmo periodo exigem escolher qual manter")


def test_importar_pdf_documento_ativo_existente_exige_confirmar_substituicao_01_10():
    existente = [{"arquivo": "antigo.pdf", "contas": 33, "gravado_em": None}]
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=[]), \
         patch.object(db, "listar_documentos_ativos", return_value=existente), \
         patch.object(db, "inativar_periodo_existente", return_value=33), \
         patch.object(db, "inserir_lancamentos", return_value=1) as mock_inserir, \
         patch.object(db, "registrar_importacao", return_value=None):
        at = AppTest.from_file(PAGE)
        at.session_state["import_resultados"] = [_bp("novo.pdf", 38)]
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert any("antigo.pdf" in w.value for w in at.warning), [w.value for w in at.warning]
        botao = [b for b in at.button if "Gravar" in b.label][0]
        assert botao.disabled, "sem confirmar a substituicao nao deveria gravar"
        [c for c in at.checkbox if c.label.startswith("Confirmo")][0].check().run(timeout=30)
        botao = [b for b in at.button if "Gravar" in b.label][0]
        assert not botao.disabled
        botao.click().run(timeout=30)
        assert mock_inserir.call_count == 1
        print("OK: Importar PDF — documento ativo existente exige confirmacao")


def _dre(nome, periodo_ini, gran, n=5):
    return {
        "arquivo": nome, "bp_rows": [],
        "dre_rows": [(f"CONTA {i}", 10.0, "RECEITAS", "PDF") for i in range(n)],
        "admin_itens": [], "log": [],
        "meta": [("Enermais Energia Ltda", "47.040.664/0001-48", "30/06/2026", nome, "DRE", "SPED", periodo_ini, gran)],
    }


def _ativos_so_trimestral(conn, cod, dt, tipo, gran):
    # banco ja' tem o TRIMESTRAL de 30/06/2026; semestral nao existe
    if tipo == "DRE" and gran == "trimestral":
        return [{"arquivo": "energia_dre_2tri.pdf", "contas": 5, "gravado_em": None}]
    return []


def test_importar_pdf_semestral_apos_trimestral_nao_herda_widget_do_lote_anterior_01_10():
    # Bug real (Rafael, 01/10/2026): gravou o trimestral, subiu o semestral
    # do MESMO periodo_fim e o seletor continuou em "Trimestral" (key
    # posicional reaproveitada) -> app achava que era substituicao e travava.
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=[]), \
         patch.object(db, "listar_documentos_ativos", side_effect=_ativos_so_trimestral), \
         patch.object(db, "inativar_periodo_existente", return_value=0), \
         patch.object(db, "inserir_lancamentos", return_value=1) as mock_inserir, \
         patch.object(db, "registrar_importacao", return_value=None):
        tri = _dre("energia_dre_2tri_novo.pdf", "01/04/2026", "trimestral")
        sem = _dre("energia_dre_1sem.pdf", "01/01/2026", "semestral")
        at = AppTest.from_file(PAGE)
        at.session_state["import_resultados"] = [tri]
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert at.selectbox(key=f"granularidade_confirmada_{assinatura_item(tri)}").value == "trimestral"
        # proximo lote: so' o semestral, na MESMA posicao 0 da lista
        at.session_state["import_resultados"] = [sem]
        at.run(timeout=30)
        assert not at.exception, at.exception
        sel = at.selectbox(key=f"granularidade_confirmada_{assinatura_item(sem)}")
        assert sel.value == "semestral", f"semestral herdou o widget do lote anterior: {sel.value!r}"
        assert not any("substituir" in c.label for c in at.checkbox), "semestral nao substitui trimestral"
        botao = [b for b in at.button if "Gravar" in b.label][0]
        assert not botao.disabled, "semestral apos trimestral nao pode travar a gravacao"
        botao.click().run(timeout=30)
        assert mock_inserir.call_args.kwargs.get("granularidade") == "semestral"
        print("OK: Importar PDF — semestral apos trimestral nao trava")


def test_importar_pdf_trimestral_e_semestral_no_mesmo_lote_nao_conflitam_01_10():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_importacoes_recentes", return_value=[]), \
         patch.object(db, "listar_documentos_ativos", return_value=[]), \
         patch.object(db, "inativar_periodo_existente", return_value=0), \
         patch.object(db, "inserir_lancamentos", return_value=1) as mock_inserir, \
         patch.object(db, "registrar_importacao", return_value=None):
        at = AppTest.from_file(PAGE)
        at.session_state["import_resultados"] = [
            _dre("a_tri.pdf", "01/04/2026", "trimestral"), _dre("b_sem.pdf", "01/01/2026", "semestral"),
        ]
        at.run(timeout=30)
        assert not at.exception, at.exception
        assert not any("MESMO" in e.value for e in at.error), [e.value for e in at.error]
        botao = [b for b in at.button if "Gravar" in b.label][0]
        assert not botao.disabled
        botao.click().run(timeout=30)
        assert mock_inserir.call_count == 2
        print("OK: Importar PDF — trimestral e semestral coexistem no lote")


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
