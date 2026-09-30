# -*- coding: utf-8 -*-
"""
Teste de integracao (Streamlit AppTest) da pagina Relatório Comentado
(app/telas/8_Relatorio_Comentado.py), Fase 2 (28/09/2026).

Cobre: carga normal (sem período nenhum -> mensagem de info, sem
exceção); carga normal com período disponível (formulário aparece,
inputs com default certo); clique em "Gerar relatório" chama
dados_relatorio_comentado.montar_dados_relatorio +
gerador_relatorio_comentado.gerar_pdf_completo e não lança exceção
(PDF real pequeno, gerado de verdade -- não mocka o gerador em si,
só a busca no banco).

Rodar: python3 tests/test_relatorio_comentado_app.py
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
import dados_relatorio_comentado as drc  # noqa: E402

NOVO_SENTINELA = "➕ Cadastrar novo…"

PAGE = str(Path(__file__).resolve().parent.parent / "telas" / "8_Relatorio_Comentado.py")


def _det(periodos):
    """Fase 3 (29/09/2026): converte list[date] (formato antigo, usado
    nos fixtures deste arquivo) pro formato detalhado de
    db.listar_periodos_detalhado -- granularidade "" em todos (sem
    intervalo declarado), suficiente pros cenários destes testes de UI,
    que não exercitam a disambiguação de granularidade em si (essa tem
    teste próprio em test_db_logic.py/test_visao_grupo.py)."""
    return [{"periodo": p, "granularidade": ""} for p in periodos]


def _pg(periodo):
    """Fase 3: (periodo, granularidade) -- formato das opções dos
    seletores de período da tela agora que granularidade "" faz parte
    da identidade (mesmo par que _det() devolve, só como tupla)."""
    return (periodo, "")


PERIODO = datetime.date(2026, 6, 30)

DADOS_FIXTURE = dict(
    empresa_codigo="ENERGIA", empresa_nome="Enermais Energia Ltda", cnpj="47.040.664/0001-48",
    cabecalho_relatorio="Demonstrativo Comentado · 06/2026", periodo_label="06/2026", periodo_extenso="",
    data_posicao="30/06/2026", data_geracao="28/09/2026",
    receita_bruta=36_674_150.0, deducoes_receita=3_321_000.0, deducoes_pct_bruta=0.09,
    receita_liquida=33_353_150.0, custo_servicos=3_899_607.99, custo_pct_liquida=0.117,
    lucro_bruto=29_453_542.01, margem_bruta=0.883,
    despesas_operacionais=30_559_591.64, despesas_administrativas=29_339_591.64,
    despesas_financeiras=1_139_497.69, despesas_tributarias=80_502.31,
    despesas_admin_itens=[("Serviços Profissionais", 11_401_300.0, 38.9)],
    csll_irpj=231_625.27, resultado_liquido=-1_337_674.90, margem_liquida=-0.04,
    resultado_financeiro=1_139_497.69, deprec_amortiz=691_527.25, ebitda=724_975.31, margem_ebitda=0.02174,
    total_ativo=43_359_080.94, ativo_circulante=18_239_216.72, ativo_nao_circulante=25_119_864.22,
    passivo_circulante=18_338_124.77, passivo_nao_circulante=18_249_041.92, patrimonio_liquido=6_771_914.25,
    imobilizado=19_815_256.50, liquidez_corrente=0.99, alavancagem=5.40, endividamento_geral=0.844,
    complemento_receita="x", complemento_ebitda="x", complemento_despesas="x", callout_estrutura_capital="x",
    anexo_ativo=[("grupo", "ATIVO"), ("conta", "Disponível", 100.0), ("total", "TOTAL DO ATIVO", 100.0)],
    anexo_passivo=[("grupo", "PASSIVO"), ("conta", "Fornecedores", 100.0), ("total", "TOTAL PASSIVO + PL", 100.0)],
    nome_administrador="", cargo_administrador="Administrador",
    nome_contador="", cargo_contador="Contador",
    email_empresa="", site_empresa="",
)


def test_sem_periodo_mostra_info_sem_excecao():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[]), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det([])):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao sem periodo nenhum: {at.exception}"
        assert any("ainda não tem nenhum período" in i.value for i in at.info)
        print("OK: Relatório Comentado — sem período disponível mostra info, sem exceção")


def test_carrega_formulario_com_periodo_disponivel():
    # FIX_20260928 (dropdown+histórico de contatos, bloco 13): sem contato
    # nenhum salvo (listar_contatos_relatorio falha com conn=None, cai no
    # except -> [] -- mesmo comportamento de tabela ainda não migrada), o
    # seletor cai pro sentinela "cadastrar novo" e os campos de nome/cargo
    # viram relatorio_novo_nome_/relatorio_novo_cargo_<TIPO>.
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[PERIODO]), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det([PERIODO])):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao carregando formulario: {at.exception}"
        # FIX_20260929m: key agora amarrada ao periodo (ver docstring da tela)
        assert at.text_input(key=f"relatorio_periodo_label_{PERIODO.isoformat()}_").value == "06/2026"
        assert at.selectbox(key="relatorio_contato_sel_ADMINISTRADOR").value == NOVO_SENTINELA
        assert at.text_input(key="relatorio_novo_cargo_ADMINISTRADOR").value == "Administrador"
        assert at.selectbox(key="relatorio_contato_sel_CONTADOR").value == NOVO_SENTINELA
        assert at.text_input(key="relatorio_novo_cargo_CONTADOR").value == "Contador"
        print("OK: Relatório Comentado — formulário carrega com defaults certos (sem contato salvo, cai pro 'novo')")


def test_seletor_de_contato_salvo_preenche_nome_e_cargo():
    contatos = [{"id": 1, "tipo": "ADMINISTRADOR", "nome": "Edilson Nazário",
                 "cargo": "Diretor Financeiro", "email": "edilson@enermais.com.br"}]
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[PERIODO]), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det([PERIODO])), \
         patch.object(db, "listar_contatos_relatorio", return_value=contatos):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        sel = at.selectbox(key="relatorio_contato_sel_ADMINISTRADOR")
        assert "Edilson Nazário — Diretor Financeiro" in sel.options
        sel.set_value("Edilson Nazário — Diretor Financeiro").run(timeout=30)
        assert not at.exception, f"excecao selecionando contato salvo: {at.exception}"
        # com contato salvo selecionado, nao aparecem mais os campos de "novo cadastro"
        assert not any(ti.key == "relatorio_novo_nome_ADMINISTRADOR" for ti in at.text_input)
        print("OK: Relatório Comentado — selecionar um contato salvo preenche nome/cargo sem exceção")


def test_lista_de_contatos_e_compartilhada_entre_administrador_e_contador():
    # FIX_20260929b (Rafael: "ficou separado... a lista única poderia
    # trocar facilmente os lados") -- 1 contato salvo aparece nos 2
    # dropdowns (ADMINISTRADOR e CONTADOR), não só no lado onde foi
    # cadastrado da 1a vez.
    contatos = [{"id": 1, "tipo": "ADMINISTRADOR", "nome": "Edilson Nazário",
                 "cargo": "Diretor Financeiro", "email": "edilson@enermais.com.br"}]
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[PERIODO]), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det([PERIODO])), \
         patch.object(db, "listar_contatos_relatorio", return_value=contatos):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        opcoes_admin = at.selectbox(key="relatorio_contato_sel_ADMINISTRADOR").options
        opcoes_contador = at.selectbox(key="relatorio_contato_sel_CONTADOR").options
        assert "Edilson Nazário — Diretor Financeiro" in opcoes_admin
        assert "Edilson Nazário — Diretor Financeiro" in opcoes_contador
        print("OK: Relatório Comentado — contato salvo aparece nos 2 seletores (lista única, não mais separada)")


DADOS_FIXTURE_COMPARATIVO = dict(
    empresa_codigo="ENERGIA", empresa_nome="Enermais Energia Ltda", cnpj="47.040.664/0001-48",
    cabecalho_relatorio="Evolução Financeira · 2025 A 1S2026", data_geracao="28/09/2026",
    periodos_labels=["2025", "1S2026"], periodo_range_label="2025 A 1S2026",
    kpis_fluxo=[
        {"label": "Receita Operacional Líquida", "tag": "fluxo",
         "valores": [31_200_000.0, 33_353_150.0], "acumulado": 64_553_150.0},
    ],
    kpis_saldo=[
        {"label": "Total do Ativo", "tag": "saldo", "valores": [41_700_000.0, 43_359_080.94]},
    ],
    grafico_evolucao_metricas=[
        {"label": "Receita Operacional Líquida", "valores": [31_200_000.0, 33_353_150.0]},
    ],
    anexo_colunas=["2025", "1S2026"], anexo_escopo_label="Enermais Energia Ltda",
    anexo_ativo=[("grupo", "ATIVO"), ("conta", "Disponível", 100.0, 120.0), ("total", "TOTAL DO ATIVO", 100.0, 120.0)],
    anexo_passivo=[
        ("grupo", "PASSIVO"), ("conta", "Fornecedores", 100.0, 120.0),
        ("total", "TOTAL PASSIVO + PL", 100.0, 120.0),
    ],
    nome_administrador="", cargo_administrador="Administrador",
    nome_contador="", cargo_contador="Contador",
    email_empresa="", site_empresa="",
)

PERIODOS_COMPARATIVO = [datetime.date(2025, 12, 31), datetime.date(2026, 6, 30)]


def test_gerar_relatorio_comparativo_chama_pipeline_e_nao_quebra():
    # FIX_20260929h (Rafael: "por mim podemos implantar o multi-periodos ja
    # tb") -- modo "Comparativo" da tela: troca o radio, seleciona 2
    # períodos no multiselect e clica Gerar. Mocka só a busca no banco
    # (montar_dados_relatorio_comparativo), o motor de desenho real
    # (gerador_relatorio_comparativo.gerar_pdf_comparativo) roda de
    # verdade -- mesmo padrão do teste do modo período único.
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=PERIODOS_COMPARATIVO), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det(PERIODOS_COMPARATIVO)), \
         patch.object(drc, "montar_dados_relatorio_comparativo", return_value=DADOS_FIXTURE_COMPARATIVO) as m_montar, \
         patch.object(db, "registrar_relatorio_gerado") as m_log:
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.radio(key="relatorio_modo").set_value("Comparativo (evolução entre períodos)").run(timeout=30)
        at.multiselect(key="relatorio_periodos_multi").set_value([_pg(p) for p in PERIODOS_COMPARATIVO]).run(timeout=30)
        botao = next(b for b in at.button if b.label == "Gerar relatório")
        assert not botao.disabled, "botão Gerar não pode ficar desabilitado com 2 períodos escolhidos"
        botao.click().run(timeout=30)
        assert not at.exception, f"excecao gerando comparativo: {at.exception}"
        assert m_montar.called
        assert m_montar.call_args.kwargs["periodos"] == PERIODOS_COMPARATIVO
        assert len(m_montar.call_args.kwargs["periodos_labels"]) == 2
        # FIX_20260929i: sem mexer no multiselect de empresas, o default e'
        # so' a empresa escolhida no topo da tela (ENERGIA, 1a de EMPRESAS_FIXAS).
        assert m_montar.call_args.kwargs["empresas_codigos"] == ["ENERGIA"]
        assert m_log.called
        assert m_log.call_args.kwargs["periodos"] == PERIODOS_COMPARATIVO
        assert m_log.call_args.kwargs["empresas_codigos"] == ["ENERGIA"]
        assert any("comparativo gerado" in s.value for s in at.success)
        print("OK: Relatório Comentado — modo comparativo chama o pipeline e gera PDF real sem exceção")


def test_comparativo_com_varias_empresas_passa_lista_pro_backend_e_nome_grupo():
    # FIX_20260929i (Rafael: "com energia e outro (exemplo) ou com todos os
    # CNPJ no montante") -- escolhendo 2 empresas no multiselect novo, o
    # backend tem que receber as 2 (não só a do topo da tela) e o nome do
    # arquivo vira "GRUPO" em vez do código de 1 empresa só.
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=PERIODOS_COMPARATIVO), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det(PERIODOS_COMPARATIVO)), \
         patch.object(drc, "montar_dados_relatorio_comparativo", return_value=DADOS_FIXTURE_COMPARATIVO) as m_montar, \
         patch.object(db, "registrar_relatorio_gerado") as m_log:
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.radio(key="relatorio_modo").set_value("Comparativo (evolução entre períodos)").run(timeout=30)
        at.multiselect(key="relatorio_empresas_multi").set_value(["ENERGIA", "SMG"]).run(timeout=30)
        at.multiselect(key="relatorio_periodos_multi").set_value([_pg(p) for p in PERIODOS_COMPARATIVO]).run(timeout=30)
        botao = next(b for b in at.button if b.label == "Gerar relatório")
        assert not botao.disabled
        botao.click().run(timeout=30)
        assert not at.exception, f"excecao gerando comparativo com 2 empresas: {at.exception}"
        assert m_montar.call_args.kwargs["empresas_codigos"] == ["ENERGIA", "SMG"]
        assert m_log.call_args.kwargs["empresas_codigos"] == ["ENERGIA", "SMG"]
        assert any("2 empresa" in s.value for s in at.success)
        print("OK: Relatório Comentado — seletor de empresas do comparativo manda a lista certa pro backend")


def test_trocar_selecao_de_periodos_nao_deixa_rotulo_preso_no_slot_antigo():
    # FIX_20260929j (bug real achado no 1o PDF comparativo do Rafael:
    # cabecalho saiu "12/2023, 06/2026, 06/2026, 06/2026" -- rotulo do
    # slot 0 ficou preso no periodo antigo porque a key do text_input era
    # por indice posicional). Regressao: escolhe um par de periodos, digita
    # um rotulo custom no 1o slot, troca a SELECAO de periodos (sem
    # remontar a pagina) -- o rotulo mostrado no slot 0 tem que refletir o
    # NOVO periodo (valor default recalculado), nao o texto digitado antes
    # pro periodo velho.
    periodo_extra = datetime.date(2026, 3, 31)
    periodos_disponiveis = PERIODOS_COMPARATIVO + [periodo_extra]
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=periodos_disponiveis), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det(periodos_disponiveis)):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.radio(key="relatorio_modo").set_value("Comparativo (evolução entre períodos)").run(timeout=30)
        at.multiselect(key="relatorio_periodos_multi").set_value([_pg(p) for p in PERIODOS_COMPARATIVO]).run(timeout=30)

        rotulos = [ti for ti in at.text_input if ti.key and ti.key.startswith("relatorio_periodo_multi_label_")]
        assert len(rotulos) == 2
        rotulos[0].set_value("RÓTULO DIGITADO À MÃO").run(timeout=30)

        # Troca a seleção pra um período diferente no lugar do mais antigo
        # (simula o Rafael mudando de ideia sobre quais períodos comparar).
        nova_selecao = [_pg(PERIODOS_COMPARATIVO[1]), _pg(periodo_extra)]
        at.multiselect(key="relatorio_periodos_multi").set_value(nova_selecao).run(timeout=30)
        assert not at.exception, f"excecao trocando seleção de períodos: {at.exception}"

        rotulos_novos = sorted(
            (ti for ti in at.text_input if ti.key and ti.key.startswith("relatorio_periodo_multi_label_")),
            key=lambda ti: ti.label,
        )
        valores = {ti.label: ti.value for ti in rotulos_novos}
        # nenhum dos 2 rotulos pode ter sobrado com o texto digitado à mão
        # pro período que não está mais selecionado, nem repetir o mesmo
        # texto nos 2 slots (era exatamente o sintoma do bug real).
        assert "RÓTULO DIGITADO À MÃO" not in valores.values()
        assert len(set(valores.values())) == 2, f"rótulos repetidos após trocar seleção: {valores}"
        print("OK: Relatório Comentado — trocar a seleção de períodos não deixa rótulo preso no período antigo")


def test_botao_gerar_desabilitado_com_menos_de_2_periodos_no_comparativo():
    # Trava de UI (mesmo teto do motor de desenho, 2-4 períodos): com só 1
    # período escolhido no modo comparativo, o botão "Gerar relatório" tem
    # que ficar desabilitado -- não dá pra montar_dados_relatorio_comparativo
    # com 1 período só (levanta ValueError, ver test_dados_relatorio_comparativo.py).
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=PERIODOS_COMPARATIVO), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det(PERIODOS_COMPARATIVO)):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.radio(key="relatorio_modo").set_value("Comparativo (evolução entre períodos)").run(timeout=30)
        at.multiselect(key="relatorio_periodos_multi").set_value([_pg(PERIODOS_COMPARATIVO[0])]).run(timeout=30)
        botao = next(b for b in at.button if b.label == "Gerar relatório")
        assert botao.disabled, "botão Gerar deveria ficar desabilitado com só 1 período no modo comparativo"
        print("OK: Relatório Comentado — botão Gerar desabilitado no comparativo com menos de 2 períodos")


def test_periodo_unico_com_varias_empresas_consolida_e_passa_lista_pro_backend():
    # FIX_20260929k (Rafael: "multi-CNPJ, 1 periodo so, como um unico
    # consolidado... tem q ser possivel gerar CNPJ e periodos a escolha") --
    # escolhendo 2 empresas no multiselect NOVO do modo "Período único", o
    # backend tem que receber a LISTA (não a string de 1 empresa só) e o
    # nome do arquivo vira "GRUPO", igual já acontece no Comparativo.
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[PERIODO]), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det([PERIODO])), \
         patch.object(drc, "montar_dados_relatorio", return_value=(DADOS_FIXTURE, True)) as m_montar, \
         patch.object(db, "registrar_relatorio_gerado") as m_log:
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.multiselect(key="relatorio_empresas_unico_multi").set_value(["ENERGIA", "SMG"]).run(timeout=30)
        botao = next(b for b in at.button if b.label == "Gerar relatório")
        assert not botao.disabled
        botao.click().run(timeout=30)
        assert not at.exception, f"excecao gerando consolidado de 2 empresas (periodo unico): {at.exception}"
        assert m_montar.call_args.kwargs["empresa_codigo"] == ["ENERGIA", "SMG"]
        assert m_log.call_args.kwargs["empresas_codigos"] == ["ENERGIA", "SMG"]
        assert any("2 empresas consolidadas" in s.value for s in at.success)
        print("OK: Relatório Comentado — seletor de empresas do período único manda a lista certa pro backend (consolidado)")


def test_periodo_unico_1_empresa_continua_enviando_string_sem_regressao():
    # Regressao: com o multiselect novo (default = so' a empresa do topo),
    # o comportamento de sempre (1 empresa, string) nao pode mudar.
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[PERIODO]), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det([PERIODO])), \
         patch.object(drc, "montar_dados_relatorio", return_value=(DADOS_FIXTURE, True)) as m_montar, \
         patch.object(db, "registrar_relatorio_gerado") as m_log:
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        botao = next(b for b in at.button if b.label == "Gerar relatório")
        botao.click().run(timeout=30)
        assert not at.exception, f"excecao gerando relatorio de 1 empresa: {at.exception}"
        assert m_montar.call_args.kwargs["empresa_codigo"] == "ENERGIA"
        assert m_log.call_args.kwargs["empresas_codigos"] == ["ENERGIA"]
        print("OK: Relatório Comentado — período único com 1 empresa (default) continua mandando string, sem regressão")


def test_trocar_periodo_no_unico_nao_deixa_rotulo_preso_no_periodo_antigo():
    # FIX_20260929m: mesmo gotcha do FIX_20260929j (key de text_input por
    # slot fixo, nao pelo periodo escolhido), so' que no modo "Periodo
    # unico" -- exatamente o "12/2023" preso que apareceu no print do
    # Rafael com 31/12/2025 selecionado. Regressao: troca o periodo
    # selecionado (sem remontar a pagina) e confere que o rotulo default
    # reflete o periodo NOVO, nao o texto que ficou de quando o periodo
    # antigo estava selecionado.
    periodo_extra = datetime.date(2023, 12, 31)
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[PERIODO, periodo_extra]), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det([PERIODO, periodo_extra])):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        # _periodos_intersecao ordena crescente -- período mais antigo
        # (periodo_extra, 2023) vem selecionado por padrão (índice 0).
        assert at.selectbox(key="relatorio_periodo_sel").value == _pg(periodo_extra)

        rotulo_inicial = [ti for ti in at.text_input if ti.key and ti.key.startswith("relatorio_periodo_label_")]
        assert len(rotulo_inicial) == 1
        assert rotulo_inicial[0].value == "12/2023"

        at.selectbox(key="relatorio_periodo_sel").set_value(_pg(PERIODO)).run(timeout=30)
        assert not at.exception, f"excecao trocando periodo no modo unico: {at.exception}"

        rotulo_novo = [ti for ti in at.text_input if ti.key and ti.key.startswith("relatorio_periodo_label_")]
        assert len(rotulo_novo) == 1
        assert rotulo_novo[0].value == "06/2026", (
            f"rótulo ficou preso no período antigo: {rotulo_novo[0].value!r}"
        )
        print("OK: Relatório Comentado — trocar o período no modo único não deixa rótulo preso no período antigo")


def test_gerar_relatorio_chama_pipeline_e_nao_quebra():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[PERIODO]), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det([PERIODO])), \
         patch.object(drc, "montar_dados_relatorio", return_value=(DADOS_FIXTURE, True)) as m_montar, \
         patch.object(db, "registrar_relatorio_gerado") as m_log:
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        botao = next(b for b in at.button if b.label == "Gerar relatório")
        botao.click().run(timeout=30)
        assert not at.exception, f"excecao gerando relatorio: {at.exception}"
        assert m_montar.called
        assert m_log.called
        assert any("Relatório gerado" in s.value for s in at.success)
        print("OK: Relatório Comentado — clique em Gerar chama o pipeline completo e gera PDF real sem exceção")


# FIX_20260930 (Rafael, variante "Demonstrativo Comentado Gerencial" --
# 2º botão ao lado do "Gerar relatório" de sempre, só no modo "Período
# único").
def test_botao_gerencial_chama_pipeline_com_variante_gerencial_e_nome_de_arquivo_diferente():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[PERIODO]), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det([PERIODO])), \
         patch.object(drc, "montar_dados_relatorio", return_value=(dict(DADOS_FIXTURE, variante="gerencial"), True)) as m_montar, \
         patch.object(db, "registrar_relatorio_gerado") as m_log:
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        botoes = [b for b in at.button if b.label == "Gerar Demonstrativo Comentado Gerencial"]
        assert len(botoes) == 1, "botão 'Gerar Demonstrativo Comentado Gerencial' não encontrado (modo Período único)"
        botoes[0].click().run(timeout=30)
        assert not at.exception, f"excecao gerando relatorio gerencial: {at.exception}"
        assert m_montar.called
        assert m_montar.call_args.kwargs.get("variante") == "gerencial", (
            f"botão Gerencial deveria passar variante='gerencial' pro montar_dados_relatorio, "
            f"veio {m_montar.call_args.kwargs.get('variante')!r}"
        )
        assert any("(variante Gerencial)" in s.value for s in at.success)
        # AppTest não expõe o nome do arquivo do download_button (só id/label/
        # url mockados) -- a distinção de nome de arquivo (sufixo _GERENCIAL)
        # é conferida diretamente na string montada em 8_Relatorio_Comentado.py,
        # não dá pra inspecionar aqui sem reimplementar a lógica da tela.
        assert any(b.key == "relatorio_download_btn" for b in at.download_button)
        print("OK: Relatório Comentado — botão 'Gerar Demonstrativo Comentado Gerencial' passa variante='gerencial'")


def test_botao_padrao_continua_passando_variante_padrao_sem_regressao():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[PERIODO]), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det([PERIODO])), \
         patch.object(drc, "montar_dados_relatorio", return_value=(DADOS_FIXTURE, True)) as m_montar, \
         patch.object(db, "registrar_relatorio_gerado"):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        botao = next(b for b in at.button if b.label == "Gerar relatório")
        botao.click().run(timeout=30)
        assert not at.exception
        assert m_montar.call_args.kwargs.get("variante") == "padrao", (
            f"botão padrão deveria continuar passando variante='padrao', veio {m_montar.call_args.kwargs.get('variante')!r}"
        )
        print("OK: Relatório Comentado — botão padrão ('Gerar relatório') continua passando variante='padrao'")


def test_modo_comparativo_nao_tem_botao_gerencial():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=PERIODOS_COMPARATIVO), \
         patch.object(db, "listar_periodos_detalhado", return_value=_det(PERIODOS_COMPARATIVO)):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        at.radio(key="relatorio_modo").set_value("Comparativo (evolução entre períodos)").run(timeout=30)
        assert not any(b.label == "Gerar Demonstrativo Comentado Gerencial" for b in at.button), (
            "modo Comparativo não deveria mostrar o botão da variante Gerencial (só existe pro Período único)"
        )
        print("OK: Relatório Comentado — modo Comparativo não mostra o botão da variante Gerencial")


if __name__ == "__main__":
    # FIX_20260929h: bloco antigo não chamava
    # test_lista_de_contatos_e_compartilhada_entre_administrador_e_contador
    # (a função existia mas nunca rodava via `python3 test_....py`, só se
    # descoberta por um runner tipo pytest) -- corrigido junto com a adição
    # dos 2 testes novos do modo comparativo.
    test_sem_periodo_mostra_info_sem_excecao()
    test_carrega_formulario_com_periodo_disponivel()
    test_seletor_de_contato_salvo_preenche_nome_e_cargo()
    test_lista_de_contatos_e_compartilhada_entre_administrador_e_contador()
    test_periodo_unico_com_varias_empresas_consolida_e_passa_lista_pro_backend()
    test_periodo_unico_1_empresa_continua_enviando_string_sem_regressao()
    test_gerar_relatorio_chama_pipeline_e_nao_quebra()
    test_gerar_relatorio_comparativo_chama_pipeline_e_nao_quebra()
    test_botao_gerar_desabilitado_com_menos_de_2_periodos_no_comparativo()
    test_comparativo_com_varias_empresas_passa_lista_pro_backend_e_nome_grupo()
    test_trocar_selecao_de_periodos_nao_deixa_rotulo_preso_no_slot_antigo()
