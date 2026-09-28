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
         patch.object(db, "listar_periodos", return_value=[]):
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
         patch.object(db, "listar_periodos", return_value=[PERIODO]):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        assert not at.exception, f"excecao carregando formulario: {at.exception}"
        assert at.text_input(key="relatorio_periodo_label").value == "06/2026"
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
         patch.object(db, "listar_contatos_relatorio",
                      side_effect=lambda conn, tipo: contatos if tipo == "ADMINISTRADOR" else []):
        at = AppTest.from_file(PAGE)
        at.run(timeout=30)
        sel = at.selectbox(key="relatorio_contato_sel_ADMINISTRADOR")
        assert "Edilson Nazário — Diretor Financeiro" in sel.options
        sel.set_value("Edilson Nazário — Diretor Financeiro").run(timeout=30)
        assert not at.exception, f"excecao selecionando contato salvo: {at.exception}"
        # com contato salvo selecionado, nao aparecem mais os campos de "novo cadastro"
        assert not any(ti.key == "relatorio_novo_nome_ADMINISTRADOR" for ti in at.text_input)
        print("OK: Relatório Comentado — selecionar um contato salvo preenche nome/cargo sem exceção")


def test_gerar_relatorio_chama_pipeline_e_nao_quebra():
    with patch.object(auth, "usuario_atual", return_value="teste@enermais.com.br"), \
         patch.object(conexao, "get_conn", return_value=None), \
         patch.object(db, "listar_periodos", return_value=[PERIODO]), \
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


if __name__ == "__main__":
    test_sem_periodo_mostra_info_sem_excecao()
    test_carrega_formulario_com_periodo_disponivel()
    test_seletor_de_contato_salvo_preenche_nome_e_cargo()
    test_gerar_relatorio_chama_pipeline_e_nao_quebra()
