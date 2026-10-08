# -*- coding: utf-8 -*-
"""v0.47.4 -- ajustes pedidos pela contadora (via Rafael) no Demonstrativo Comentado:
ordem das contas do anexo igual ao balanco SPED, detalhe do Passivo Nao Circulante,
periodo por extenso automatico, e os textos/quadros removidos (so' a divida fica no Gerencial)."""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dados_relatorio_comentado as drc  # noqa: E402
import gerador_relatorio_comentado as g  # noqa: E402
import gerador_relatorio_comparativo as gc  # noqa: E402
from test_gerador_completo import BASE  # noqa: E402


def _l(grupo, conta, valor):
    return {"grupo": grupo, "conta": conta, "valor": valor}


# Energia 31/12/2025 (valores do SPED)
BP = [
    _l("ATIVO CIRCULANTE", "TOTAL CIRCULANTE ATIVO", 16_000_448.52),
    _l("ATIVO CIRCULANTE", "CLIENTES", 2_182_269.54),
    _l("ATIVO CIRCULANTE", "DUPLICATAS A RECEBER", 2_182_269.54),
    _l("ATIVO CIRCULANTE", "DISPONIVEL", 4_360_509.60),
    _l("ATIVO CIRCULANTE", "OUTROS CREDITOS", 9_457_669.38),
    _l("ATIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE ATIVO", 22_897_089.73),
    _l("ATIVO NAO CIRCULANTE", "IMOBILIZADO", 18_135_272.97),
    _l("ATIVO NAO CIRCULANTE", "INVESTIMENTOS", 4_178_205.96),
    _l("ATIVO NAO CIRCULANTE", "APLICACOES FINANCEIRAS", 583_610.80),
    _l("TOTAL", "TOTAL DO ATIVO", 38_897_538.25),
    _l("PASSIVO CIRCULANTE", "TOTAL CIRCULANTE PASSIVO", 17_715_004.01),
    _l("PASSIVO CIRCULANTE", "FORNECEDORES", 3_312_392.14),
    _l("PASSIVO CIRCULANTE", "INSTITUICOES FINANCEIRAS", 2_688_523.08),
    _l("PASSIVO CIRCULANTE", "EMPRESTIMOS", 1_382_196.12),
    _l("PASSIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE PASSIVO", 12_717_981.09),
    _l("PASSIVO NAO CIRCULANTE", "OBRIGACOES A LONGO PRAZO", 12_717_981.09),
    _l("PASSIVO NAO CIRCULANTE", "OUTRAS OBRIGACOES", 8_673_895.78),
    _l("PASSIVO NAO CIRCULANTE", "INSTITUICOES FINANCEIRAS", 4_044_085.31),
    _l("PASSIVO NAO CIRCULANTE", "EMPRESTIMOS", 3_467_873.59),
    _l("PATRIMONIO LIQUIDO", "TOTAL PATRIMONIO LIQUIDO", 8_464_553.15),
    _l("PATRIMONIO LIQUIDO", "LUCROS/PREJUIZOS ACUMULADOS", 5_609_589.15),
    _l("PATRIMONIO LIQUIDO", "CAPITAL SOCIAL", 2_854_964.00),
    _l("TOTAL", "TOTAL DO PASSIVO", 38_897_538.25),
]


def _rotulos(linhas, tipos=("conta",)):
    return [l[1] for l in linhas if l[0] in tipos]


def test_ordem_do_anexo_segue_o_balanco_sped():
    ativo = drc._montar_anexo(BP, "ATIVO")
    assert _rotulos(ativo)[:2] == ["Disponível", "Clientes"]
    # Realizavel a LP -> Investimentos -> Imobilizado
    assert _rotulos(ativo)[2:] == ["Outros Créditos", "Aplicações Financeiras", "Investimentos", "Imobilizado"]
    passivo = drc._montar_anexo(BP, "PASSIVO")
    # Instituicoes Financeiras antes de Fornecedores (igual ao BP)
    assert _rotulos(passivo)[:2] == ["Instituições Financeiras", "Fornecedores"]
    assert _rotulos(passivo, ("conta",))[-2:] == ["Capital Social", "Lucros/Prejuízos Acumulados"]


def test_passivo_nao_circulante_mostra_titulo_e_composicao():
    linhas = drc._montar_anexo(BP, "PASSIVO")
    i = linhas.index(("grupo", "Passivo Não Circulante"))
    assert linhas[i + 1] == ("conta", "Passivo Não Circulante", 12_717_981.09)
    assert linhas[i + 2] == ("subconta", "Instituições Financeiras", 4_044_085.31)
    # v0.48.1: cada componente mostra os seus subtitulos (3o nivel) -- aqui Emprestimos + "Demais contas" (derivada)
    assert linhas[i + 3] == ("subconta2", "Empréstimos", 3_467_873.59)
    assert linhas[i + 4] == ("subconta2", "Demais contas", 576_211.72)
    assert linhas[i + 5] == ("subconta", "Outras Obrigações", 8_673_895.78)
    # sem subtotal repetido nem a antiga linha "Obrigacoes a Longo Prazo"
    assert linhas[i + 6][0] == "grupo" and linhas[i + 6][1] == "Patrimônio Líquido"
    assert "Obrigações a Longo Prazo" not in _rotulos(linhas, ("conta", "subconta"))


def test_passivo_nao_circulante_dado_antigo_so_com_obrigacoes_a_longo_prazo():
    bp = [r for r in BP if r["conta"] != "TOTAL NAO CIRCULANTE PASSIVO"]
    linhas = drc._montar_anexo(bp, "PASSIVO")
    i = linhas.index(("grupo", "Passivo Não Circulante"))
    assert linhas[i + 1] == ("conta", "Passivo Não Circulante", 12_717_981.09)


def test_passivo_nao_circulante_sem_composicao_mantem_desenho_antigo():
    bp = [r for r in BP if r["grupo"] != "PASSIVO NAO CIRCULANTE"] + [
        _l("PASSIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE PASSIVO", 0.0)]
    linhas = drc._montar_anexo(bp, "PASSIVO")
    i = linhas.index(("grupo", "Passivo Não Circulante"))
    assert linhas[i + 1] == ("subtotal", "Total Passivo Não Circulante", 0.0)


def test_anexo_multi_periodo_tem_a_mesma_ordem_e_o_detalhe_do_pnc():
    passivo = drc._montar_anexo_multi_periodo([BP, BP], "PASSIVO")
    assert _rotulos(passivo)[:2] == ["Instituições Financeiras", "Fornecedores"]
    i = passivo.index(("conta", "Passivo Não Circulante", 12_717_981.09, 12_717_981.09))
    assert passivo[i + 1] == ("subconta", "Instituições Financeiras", 4_044_085.31, 4_044_085.31)
    assert passivo[i + 2] == ("subconta", "Outras Obrigações", 8_673_895.78, 8_673_895.78)


def test_periodo_extenso_padrao():
    assert drc.periodo_extenso_padrao(date(2025, 12, 31), "mensal") == "dezembro de 2025"
    assert drc.periodo_extenso_padrao(date(2025, 12, 31), "") == "dezembro de 2025"
    assert drc.periodo_extenso_padrao(date(2026, 6, 30), "trimestral") == "2º trimestre de 2026"
    assert drc.periodo_extenso_padrao(date(2026, 6, 30), "semestral") == "1º semestre de 2026"
    assert drc.periodo_extenso_padrao(date(2025, 12, 31), "anual") == "exercício de 2025"


def _texto_pdf(dados, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "r.pdf")
        g.gerar_pdf_completo(dados, caminho, **kw)
        with pdfplumber.open(caminho) as pdf:
            return [(p.extract_text() or "") for p in pdf.pages]


def _dados(variante):
    return dict(BASE, variante=variante, periodo_extenso="", periodo_extenso_texto="exercício de 2025",
                complemento_receita="COMP_RECEITA", complemento_despesas="COMP_DESPESAS",
                callout_estrutura_capital="CALLOUT_DIVIDA", cabecalho_relatorio="Demonstrativo Comentado · 12/2025")


def test_fornecedor_sem_quadro_de_divida_sem_descritivos_e_sem_textos_removidos():
    paginas = _texto_pdf(_dados("fornecedor"), incluir_pagina_resultado=False)
    todo = "\n".join(paginas)
    assert "CALLOUT_DIVIDA" not in todo
    assert "COMP_RECEITA" not in todo and "COMP_DESPESAS" not in todo
    assert "ENDIVIDAMENTO GERAL" in todo.upper()  # v0.48.1: o KPI passou a aparecer em todas as variantes
    assert "submetido" not in todo and "Relatório gerado pelo" not in todo
    assert "()" not in todo and "gerados a partir" not in todo  # v0.48.1: frase sobre o SPED removida
    assert "driver" not in todo
    # destaques: nome + CNPJ (sem o "—" solto)
    assert "Enermais Energia Ltda · CNPJ 47.040.664/0001-48" in paginas[1]
    # fornecedor nao tem pagina de despesas -> nao pode apontar pra ela
    assert "detalhamento das despesas operacionais" not in todo
    assert "detalhamento completo de contas está na página seguinte" in todo.replace("\n", " ")


def test_gerencial_mantem_divida_e_referencia_as_despesas():
    paginas = _texto_pdf(_dados("gerencial"), incluir_pagina_resultado=False)
    todo = "\n".join(paginas)
    assert "CALLOUT_DIVIDA" in todo
    assert "ENDIVIDAMENTO GERAL" in todo.upper()
    assert "COMP_RECEITA" not in todo and "COMP_DESPESAS" not in todo  # descritivos saem em todas as variantes
    assert "submetido" not in todo and "Relatório gerado pelo" not in todo
    assert "detalhamento das despesas operacionais está na página seguinte" in todo.replace("\n", " ")
    assert "lido em conjunto com as despesas detalhadas na página anterior" in todo.replace("\n", " ")


def test_destaques_de_grupo_mostra_so_o_nome_do_grupo():
    d = dict(_dados("fornecedor"), empresa_nome="Grupo Enermais", cnpj="", empresas_codigos=["ENERGIA", "SMG"])
    assert g._linha_empresa_destaques(d) == "Grupo Enermais"


def test_comparativo_fechamento_sem_auditoria_e_sem_linha_do_sistema():
    from test_gerador_relatorio_comparativo import BASE as BASE_C
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "c.pdf")
        gc.gerar_pdf_comparativo(dict(BASE_C), caminho)
        with pdfplumber.open(caminho) as pdf:
            todo = "\n".join((p.extract_text() or "") for p in pdf.pages)
    assert "submetido" not in todo and "Relatório gerado pelo" not in todo
    assert "uso interno da" in todo.replace("\n", " ") and "administração e da contabilidade" in todo.replace("\n", " ")
