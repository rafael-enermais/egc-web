# -*- coding: utf-8 -*-
"""
Testes de app/dados_relatorio_comentado.py (Fase 2, 28/09/2026).

Nao bate em banco real -- mocka db.* e indicadores.calcular_indicadores
diretamente (essas 2 camadas ja tem teste proprio, aqui so' testamos a
MONTAGEM/conversao de sinal do dict pro gerador). Os valores usados sao
os mesmos da fixture BASE de test_gerador_completo.py, "de tras pra
frente" (a partir do valor ja convertido, reconstroi o valor bruto como
ele chega do banco) -- serve tambem de teste de regressao da conversao
de sinal (ver docstring de sinal em dados_relatorio_comentado.py).
"""
import sys
import datetime
import warnings
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dados_relatorio_comentado as drc  # noqa: E402
import gerador_relatorio_comentado as g  # noqa: E402

PERIODO = datetime.date(2026, 6, 30)
PERIODO_ANTERIOR = datetime.date(2025, 12, 31)

EMPRESAS = [{"codigo": "ENERGIA", "nome": "Enermais Energia Ltda", "cnpj": "47.040.664/0001-48"}]


def _lanc(grupo, conta, valor):
    return {"grupo": grupo, "conta": conta, "valor": valor}


BP_PERIODO = [
    _lanc("ATIVO CIRCULANTE", "TOTAL CIRCULANTE ATIVO", 18_239_216.72),
    _lanc("ATIVO CIRCULANTE", "DISPONIVEL", 100.0),
    _lanc("ATIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE ATIVO", 25_119_864.22),
    _lanc("ATIVO NAO CIRCULANTE", "IMOBILIZADO", 19_815_256.50),
    _lanc("TOTAL", "TOTAL DO ATIVO", 43_359_080.94),
    _lanc("PASSIVO CIRCULANTE", "TOTAL CIRCULANTE PASSIVO", 18_338_124.77),
    _lanc("PASSIVO CIRCULANTE", "FORNECEDORES", 200.0),
    _lanc("PASSIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE PASSIVO", 18_249_041.92),
    _lanc("PATRIMONIO LIQUIDO", "TOTAL PATRIMONIO LIQUIDO", 6_771_914.25),
    _lanc("TOTAL", "TOTAL DO PASSIVO", 43_359_080.94),
]

DRE_PERIODO = [
    _lanc("RECEITAS", "RECEITA OPERACIONAL BRUTA", 36_674_150.0),
    _lanc("DEDUCOES", "DEDUCOES DA RECEITA BRUTA", -3_321_000.0),
    _lanc("RESULTADO", "RECEITA OPERACIONAL LIQUIDA", 33_353_150.0),
    _lanc("CUSTOS", "CUSTO DOS PRODUTOS/SERVICOS", -3_899_607.99),
    _lanc("RESULTADO", "LUCRO BRUTO", 29_453_542.01),
    _lanc("DESPESAS", "DESPESAS OPERACIONAIS", -30_559_591.64),
    _lanc("DESPESAS", "ADMINISTRATIVAS", -29_339_591.64),
    _lanc("DESPESAS", "DESPESAS FINANCEIRAS", -1_139_497.69),
    _lanc("DESPESAS", "DESPESAS TRIBUTARIAS", -80_502.31),
    _lanc("DESPESAS", "PROVISAO CSLL", -62_900.81),
    _lanc("DESPESAS", "PROVISAO IRPJ", -168_724.46),
    _lanc("RESULTADO", "LUCRO LIQUIDO DO EXERCICIO", -1_337_674.90),
    _lanc("DESPESAS", "DEPRECIACOES", -691_527.25),
]

# Fase 4 (30/09/2026): a soma dos itens agora TEM que bater com o total
# ADMINISTRATIVAS do DRE (29_339_591.64) -- senao o ranking e' descartado
# (itens de outro documento do mesmo periodo_fim). Resto do total dividido
# em 4 itens menores (4 x 3_469_572.91) pra nao mudar a ordem dos 2 maiores.
ITENS_ADMIN = [
    ("Serviços Profissionais", -11_401_300.0), ("Salários e Ordenados", -4_060_000.0),
    ("Aluguéis", -3_469_572.91), ("Softwares", -3_469_572.91),
    ("Viagens", -3_469_572.91), ("Seguros", -3_469_572.91),
]

INDIC_ROW = {
    "Liquidez Corrente": 0.99, "Alavancagem": 5.40, "Endividamento Geral": 0.844,
    "Margem Bruta": 0.883, "Margem Líquida": -0.04, "EBITDA": 724_975.31, "Margem EBITDA": 0.02174,
}


def _indic_df(periodos_valores: dict) -> pd.DataFrame:
    """periodos_valores: {periodo: {coluna: valor}}."""
    idx = pd.to_datetime(sorted(periodos_valores.keys()))
    df = pd.DataFrame(
        [periodos_valores[d] for d in sorted(periodos_valores.keys())],
        index=idx,
    )
    df.index.name = "periodo"
    return df


def _patches(periodos_ativos=None, dre_hist=None, indic_df=None):
    periodos_ativos = periodos_ativos if periodos_ativos is not None else [PERIODO]
    dre_hist = dre_hist if dre_hist is not None else DRE_PERIODO
    indic_df = indic_df if indic_df is not None else _indic_df({PERIODO: INDIC_ROW})
    return [
        patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS),
        patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO", granularidade=None:
              BP_PERIODO if tipo == "BP" else DRE_PERIODO),
        patch("dados_relatorio_comentado.db.listar_historico_grupo", side_effect=lambda conn, cod, tipo, status="ATIVO":
              [dict(periodo=PERIODO, **{k: v2 for k, v2 in x.items() if k != "grupo"}, grupo=x["grupo"]) for x in BP_PERIODO] if tipo == "BP"
              else [dict(x, periodo=PERIODO) for x in dre_hist]),
        patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", return_value=ITENS_ADMIN),
        patch("dados_relatorio_comentado.db.listar_periodos", return_value=periodos_ativos),
        patch("dados_relatorio_comentado.indicadores.calcular_indicadores", return_value=indic_df),
    ]


def _montar(**kwargs):
    patches = _patches(**{k: kwargs.pop(k) for k in ("periodos_ativos", "dre_hist", "indic_df") if k in kwargs})
    ctxs = [p.start() for p in patches]
    try:
        return drc.montar_dados_relatorio(conn=object(), empresa_codigo="ENERGIA", periodo=PERIODO, periodo_label="1º Semestre 2026", **kwargs)
    finally:
        for p in patches:
            p.stop()


def test_conversao_de_sinal_bate_com_fixture_do_gerador():
    dados, incluir_resultado = _montar()
    assert incluir_resultado is True
    assert dados["receita_bruta"] == 36_674_150.0
    assert dados["deducoes_receita"] == 3_321_000.0
    assert round(dados["deducoes_pct_bruta"], 4) == round(3_321_000.0 / 36_674_150.0, 4)
    assert dados["receita_liquida"] == 33_353_150.0
    assert dados["custo_servicos"] == 3_899_607.99
    assert dados["lucro_bruto"] == 29_453_542.01
    assert dados["despesas_operacionais"] == 30_559_591.64
    assert dados["despesas_administrativas"] == 29_339_591.64
    assert round(dados["despesas_financeiras"], 2) == 1_139_497.69
    assert dados["despesas_tributarias"] == 80_502.31
    # os 3 componentes de despesa tem que fechar com despesas_operacionais
    assert round(dados["despesas_administrativas"] + dados["despesas_financeiras"] + dados["despesas_tributarias"], 2) == dados["despesas_operacionais"]
    assert round(dados["csll_irpj"], 2) == 231_625.27
    assert dados["resultado_liquido"] == -1_337_674.90
    assert round(dados["resultado_financeiro"], 2) == 1_139_497.69
    assert round(dados["deprec_amortiz"], 2) == 691_527.25
    assert dados["ebitda"] == 724_975.31
    assert dados["margem_ebitda"] == 0.02174
    assert dados["total_ativo"] == 43_359_080.94
    assert dados["imobilizado"] == 19_815_256.50
    assert dados["liquidez_corrente"] == 0.99
    assert dados["alavancagem"] == 5.40
    print("OK: conversao de sinal do banco pro gerador bate com a fixture BASE (valor a valor)")


def test_despesas_admin_itens_ordenado_e_percentual_correto():
    dados, _ = _montar()
    itens = dados["despesas_admin_itens"]
    assert itens[0][0] == "Serviços Profissionais"
    assert itens[0][1] == 11_401_300.0
    assert round(itens[0][2], 1) == 38.9
    assert round(itens[1][2], 1) == 13.8
    print("OK: despesas_admin_itens vem em magnitude positiva, ordenado desc, percentual sobre despesas_administrativas")


def test_despesas_admin_itens_agrega_cauda_longa_em_demais_contas():
    # FIX_20260929c: pag.4 estourava com dezenas de contas (empresa real,
    # Energia, tem 43). Acima de top_n(6)+1, so' os 6 maiores ficam
    # individuais e o resto vira 1 linha agregada "Demais contas (N)".
    itens_admin = [(f"Conta {i}", -(100.0 - i)) for i in range(10)]  # 10 contas, 100,99,...,91
    itens = drc._montar_despesas_admin_itens(itens_admin, despesas_administrativas=955.0)
    assert len(itens) == 7, f"esperava 6 individuais + 1 agregada, veio {len(itens)} linhas"
    nomes = [i[0] for i in itens[:6]]
    assert nomes == ["Conta 0", "Conta 1", "Conta 2", "Conta 3", "Conta 4", "Conta 5"], (
        "os 6 maiores devem ficar individuais, em ordem decrescente"
    )
    assert itens[6][0] == "Demais contas (4)"
    assert itens[6][1] == 94.0 + 93.0 + 92.0 + 91.0 == 370.0  # contas 6,7,8,9 (100-i)
    assert round(sum(i[1] for i in itens), 2) == round(sum(abs(v) for _, v in itens_admin), 2), (
        "soma dos itens (individuais + agregado) tem que bater com o total original -- nada pode sumir"
    )
    print("OK: despesas_admin_itens — cauda longa (>top_n+1 contas) agrega em 'Demais contas (N)' sem perder valor")


def test_despesas_admin_itens_nao_agrega_quando_cabe_no_limite():
    # exatamente top_n(6)+1 = 7 contas -- nao compensa resumir so' 1 item,
    # mostra as 7 individualmente.
    itens_admin = [(f"Conta {i}", -(100.0 - i)) for i in range(7)]
    itens = drc._montar_despesas_admin_itens(itens_admin, despesas_administrativas=679.0)
    assert len(itens) == 7
    assert all(not i[0].startswith("Demais contas") for i in itens)
    print("OK: despesas_admin_itens — com <= top_n+1 contas, não agrega (mostra todas)")


def test_sem_csll_irpj_pula_pagina_resultado_e_nao_inventa_campo():
    dre_sem_csll = [r for r in DRE_PERIODO if r["conta"] not in ("PROVISAO CSLL", "PROVISAO IRPJ")]
    dados, incluir_resultado = _montar(dre_hist=dre_sem_csll)
    # a fixture patch de listar_lancamentos ainda devolve o DRE_PERIODO fixo
    # (com csll) -- o que importa aqui e' o comportamento quando os 2
    # PROVISAO_* nao existem NO PERIODO buscado por listar_lancamentos.
    with patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS), \
         patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO", granularidade=None:
               BP_PERIODO if tipo == "BP" else dre_sem_csll), \
         patch("dados_relatorio_comentado.db.listar_historico_grupo", side_effect=lambda conn, cod, tipo, status="ATIVO":
               [dict(x, periodo=PERIODO) for x in BP_PERIODO] if tipo == "BP" else [dict(x, periodo=PERIODO) for x in dre_sem_csll]), \
         patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", return_value=ITENS_ADMIN), \
         patch("dados_relatorio_comentado.db.listar_periodos", return_value=[PERIODO]), \
         patch("dados_relatorio_comentado.indicadores.calcular_indicadores", return_value=_indic_df({PERIODO: INDIC_ROW})):
        dados2, incluir_resultado2 = drc.montar_dados_relatorio(
            conn=object(), empresa_codigo="ENERGIA", periodo=PERIODO, periodo_label="1º Semestre 2026",
        )
    assert incluir_resultado2 is False
    assert "csll_irpj" not in dados2
    print("OK: sem PROVISAO CSLL/IRPJ no periodo, incluir_pagina_resultado=False e csll_irpj nao entra no dict (nao inventa 0)")


def test_anexo_ativo_tem_grupo_conta_subtotal_total_na_ordem():
    # FIX_20260928: agora 1 "grupo" por subgrupo (Ativo Circulante / Ativo
    # Nao Circulante), nao 1 header umbrella no topo -- ver docstring de
    # _montar_anexo.
    dados, _ = _montar()
    tipos = [linha[0] for linha in dados["anexo_ativo"]]
    assert tipos == ["grupo", "conta", "subtotal", "grupo", "conta", "subtotal", "total"]
    assert dados["anexo_ativo"][0] == ("grupo", "Ativo Circulante")
    assert dados["anexo_ativo"][-1] == ("total", "TOTAL DO ATIVO", 43_359_080.94)
    print("OK: anexo_ativo monta a arvore grupo/conta/subtotal/total (1 grupo por subgrupo) na ordem esperada pelo gerador")


def test_anexo_aninha_subconta_sob_conta_pai_quando_par_confirmado():
    # Disponivel = Depositos Bancarios a Vista + Aplicacoes de Liquidez
    # Imediata -- par confirmado em _HIERARQUIA_BP -- deve aparecer
    # ('conta', 'Disponível', total) seguido de 2 ('subconta', ..., ...),
    # sem os filhos soltos em outro lugar da lista.
    bp = [
        _lanc("ATIVO CIRCULANTE", "TOTAL CIRCULANTE ATIVO", 1_395_762.01),
        _lanc("ATIVO CIRCULANTE", "DISPONIVEL", 1_395_762.01),
        _lanc("ATIVO CIRCULANTE", "DEPOSITOS BANCARIOS A VISTA", 568_358.36),
        _lanc("ATIVO CIRCULANTE", "APLICACOES DE LIQUIDEZ IMEDIATA", 827_403.65),
        _lanc("ATIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE ATIVO", 0.0),
        _lanc("TOTAL", "TOTAL DO ATIVO", 1_395_762.01),
        _lanc("PASSIVO CIRCULANTE", "TOTAL CIRCULANTE PASSIVO", 0.0),
        _lanc("PASSIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE PASSIVO", 0.0),
        _lanc("PATRIMONIO LIQUIDO", "TOTAL PATRIMONIO LIQUIDO", 1_395_762.01),
        _lanc("TOTAL", "TOTAL DO PASSIVO", 1_395_762.01),
    ]
    linhas = drc._montar_anexo(bp, "ATIVO")
    assert linhas[0] == ("grupo", "Ativo Circulante")
    assert linhas[1] == ("conta", "Disponível", 1_395_762.01)
    assert linhas[2] == ("subconta", "Depósitos Bancários à Vista", 568_358.36)
    assert linhas[3] == ("subconta", "Aplicações de Liquidez Imediata", 827_403.65)
    tipos = [l[0] for l in linhas]
    assert tipos.count("conta") == 1, "os 2 filhos nao devem sobrar soltos como 'conta' de novo"
    print("OK: _montar_anexo aninha subconta sob a conta pai quando o par esta em _HIERARQUIA_BP")


def test_mapa_avisa_sobre_conta_duplicada_nas_linhas_ativas():
    # FIX_20260928d (Rafael, EBITDA diferente entre 2 gerações do mesmo
    # relatório de Energia 06/2026): egc.lancamentos não tem unique
    # constraint que impeça 2 linhas ATIVAS pra mesma conta/período --
    # se isso acontecer, _mapa deve pelo menos AVISAR (não pode ficar
    # silencioso), mesmo continuando a devolver um valor (não quebra o
    # relatório por causa de dado ruim já gravado).
    lancamentos = [
        _lanc("DESPESAS", "DEPRECIACOES", 0.0),
        _lanc("DESPESAS", "DEPRECIACOES", -352_902.22),
    ]
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mapa = drc._mapa(lancamentos)
    assert any("DEPRECIACOES" in str(x.message) and "duplicada" in str(x.message) for x in w), (
        "conta duplicada entre linhas ATIVAS deveria gerar aviso, não passar em silêncio"
    )
    assert mapa["DEPRECIACOES"] == -352_902.22  # comportamento preservado: usa a última da lista
    print("OK: _mapa avisa sobre conta duplicada nas linhas ATIVAS em vez de escolher valor em silêncio")


def test_mapa_sem_duplicata_nao_avisa():
    lancamentos = [_lanc("DESPESAS", "DEPRECIACOES", -352_902.22), _lanc("DESPESAS", "AMORTIZACOES", 0.0)]
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        drc._mapa(lancamentos)
    assert not any("duplicada" in str(x.message) for x in w)
    print("OK: _mapa nao avisa quando nao ha duplicata (caso normal, sem falso positivo)")


def test_anexo_aninha_3_pares_novos_do_passivo_fix_20260928b():
    # Mesmos 3 pares novos do lado PASSIVO CIRCULANTE, com os valores
    # REAIS da Enermais Energia (SPED 31/12/2025) usados na verificacao
    # aritmetica que justificou o FIX_20260928b em _HIERARQUIA_BP:
    #   Obrigacoes Tributarias = Impostos e Contrib. a Recolher + Tributos Retidos a Recolher
    #   Obrigacoes Trabalhistas = Obrigacoes com o Pessoal + Obrigacoes Previdenciarias
    #   Outras Obrigacoes = Adiantamentos de Clientes + Contas a Pagar
    bp = [
        _lanc("ATIVO CIRCULANTE", "TOTAL CIRCULANTE ATIVO", 0.0),
        _lanc("ATIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE ATIVO", 0.0),
        _lanc("TOTAL", "TOTAL DO ATIVO", 0.0),
        _lanc("PASSIVO CIRCULANTE", "TOTAL CIRCULANTE PASSIVO", 11_714_088.79),
        _lanc("PASSIVO CIRCULANTE", "OBRIGACOES TRIBUTARIAS", 1_346_005.46),
        _lanc("PASSIVO CIRCULANTE", "IMPOSTOS E CONTRIBUICOES A RECOLHER", 1_291_057.67),
        _lanc("PASSIVO CIRCULANTE", "TRIBUTOS RETIDOS A RECOLHER", 54_947.79),
        _lanc("PASSIVO CIRCULANTE", "OBRIGACOES TRABALHISTAS", 1_799_133.16),
        _lanc("PASSIVO CIRCULANTE", "OBRIGACOES COM O PESSOAL", 551_614.37),
        _lanc("PASSIVO CIRCULANTE", "OBRIGACOES PREVIDENCIARIAS", 1_247_518.79),
        _lanc("PASSIVO CIRCULANTE", "OUTRAS OBRIGACOES", 8_568_950.17),
        _lanc("PASSIVO CIRCULANTE", "ADIANTAMENTOS DE CLIENTES", 6_724_607.29),
        _lanc("PASSIVO CIRCULANTE", "CONTAS A PAGAR", 1_844_342.88),
        _lanc("PASSIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE PASSIVO", 0.0),
        _lanc("PATRIMONIO LIQUIDO", "TOTAL PATRIMONIO LIQUIDO", 0.0),
        _lanc("TOTAL", "TOTAL DO PASSIVO", 11_714_088.79),
    ]
    linhas = drc._montar_anexo(bp, "PASSIVO")
    contas = {l[1]: l for l in linhas if l[0] in ("conta", "subconta")}
    assert contas["Obrigações Tributárias"][0] == "conta"
    assert contas["Impostos e Contribuições a Recolher"] == ("subconta", "Impostos e Contribuições a Recolher", 1_291_057.67)
    assert contas["Tributos Retidos a Recolher"] == ("subconta", "Tributos Retidos a Recolher", 54_947.79)
    assert contas["Obrigações Trabalhistas"][0] == "conta"
    assert contas["Obrigações com o Pessoal"] == ("subconta", "Obrigações com o Pessoal", 551_614.37)
    assert contas["Obrigações Previdenciárias"] == ("subconta", "Obrigações Previdenciárias", 1_247_518.79)
    assert contas["Outras Obrigações"][0] == "conta"
    assert contas["Adiantamentos de Clientes"] == ("subconta", "Adiantamentos de Clientes", 6_724_607.29)
    assert contas["Contas a Pagar"] == ("subconta", "Contas a Pagar", 1_844_342.88)
    tipos = [l[0] for l in linhas if l[0] in ("conta", "subconta")]
    assert tipos.count("conta") == 3, "os 3 pais devem aparecer 1x cada como 'conta', filhos nunca soltos"
    print("OK: _montar_anexo aninha os 3 pares novos (Obrig. Tributarias/Trabalhistas/Outras Obrigacoes) com dado real da Energia")


def test_sem_periodo_anterior_cai_no_fallback_neutro():
    dados, _ = _montar(periodos_ativos=[PERIODO])
    assert "sem período anterior" in dados["complemento_receita"]
    assert "sem período anterior" in dados["complemento_ebitda"]
    print("OK: 1a importacao (sem periodo anterior) usa fallback neutro, nao inventa comparacao")


def test_com_periodo_anterior_gera_texto_comparativo():
    dre_hist_2p = [dict(x, periodo=PERIODO) for x in DRE_PERIODO] + [
        dict(_lanc("RESULTADO", "RECEITA OPERACIONAL LIQUIDA", 30_000_000.0), periodo=PERIODO_ANTERIOR),
        dict(_lanc("DESPESAS", "DESPESAS OPERACIONAIS", -25_000_000.0), periodo=PERIODO_ANTERIOR),
    ]
    indic_df_2p = _indic_df({PERIODO: INDIC_ROW, PERIODO_ANTERIOR: {**INDIC_ROW, "EBITDA": 500_000.0}})
    with patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS), \
         patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO", granularidade=None:
               BP_PERIODO if tipo == "BP" else DRE_PERIODO), \
         patch("dados_relatorio_comentado.db.listar_historico_grupo", side_effect=lambda conn, cod, tipo, status="ATIVO":
               [dict(x, periodo=PERIODO) for x in BP_PERIODO] if tipo == "BP" else dre_hist_2p), \
         patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", return_value=ITENS_ADMIN), \
         patch("dados_relatorio_comentado.db.listar_periodos", return_value=[PERIODO_ANTERIOR, PERIODO]), \
         patch("dados_relatorio_comentado.indicadores.calcular_indicadores", return_value=indic_df_2p):
        dados, _ = drc.montar_dados_relatorio(
            conn=object(), empresa_codigo="ENERGIA", periodo=PERIODO, periodo_label="1º Semestre 2026",
        )
    assert "período anterior" not in dados["complemento_receita"] or "sem período anterior" not in dados["complemento_receita"]
    assert "%" in dados["complemento_receita"]
    assert "%" in dados["complemento_ebitda"]
    print("OK: com periodo anterior disponivel, gera texto comparativo percentual (nao o fallback)")


def test_pipeline_completo_nao_quebra_gerando_pdf_de_verdade():
    import tempfile
    import os
    dados, incluir_resultado = _montar()
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "real.pdf")
        g.gerar_pdf_completo(dados, caminho, incluir_pagina_resultado=incluir_resultado)
        assert os.path.getsize(caminho) > 5000
    print("OK: dados montados a partir do 'banco' (mockado) geram PDF completo sem excecao")


def test_sem_bp_gravado_leva_erro_claro_nao_zerodivisionerror():
    # FIX_20260930: Rafael reportou em producao "Nao foi possivel gerar o
    # relatorio: division by zero" tentando gerar pra um periodo/empresa
    # cujo BP nao tinha sido gravado ainda (Construtora, 2o Trimestre
    # 2026 -- so' o BP nao foi gravado, o resto do lote nem chegou a
    # subir). Confirma que agora vem um ValueError com mensagem legivel
    # em vez do ZeroDivisionError cru de pagina_balanco.
    with patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS), \
         patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO", granularidade=None:
               [] if tipo == "BP" else DRE_PERIODO), \
         patch("dados_relatorio_comentado.db.listar_historico_grupo", side_effect=lambda conn, cod, tipo, status="ATIVO":
               [] if tipo == "BP" else [dict(x, periodo=PERIODO) for x in DRE_PERIODO]), \
         patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", return_value=ITENS_ADMIN), \
         patch("dados_relatorio_comentado.db.listar_periodos", return_value=[PERIODO]), \
         patch("dados_relatorio_comentado.indicadores.calcular_indicadores", return_value=_indic_df({PERIODO: INDIC_ROW})):
        try:
            drc.montar_dados_relatorio(
                conn=object(), empresa_codigo="ENERGIA", periodo=PERIODO, periodo_label="1º Semestre 2026",
            )
            assert False, "deveria ter levantado ValueError (sem BP gravado)"
        except ValueError as e:
            assert "não tem BP gravado" in str(e), f"mensagem nao ficou clara: {e}"
        except ZeroDivisionError:
            assert False, "regrediu pro ZeroDivisionError cru -- deveria ser ValueError com mensagem clara"
    print("OK: sem BP gravado -> ValueError claro, nao ZeroDivisionError")


def test_bp_gravado_sem_total_do_ativo_leva_erro_claro():
    # Variante: BP TEM linhas gravadas mas sem a conta TOTAL DO ATIVO
    # (import parcial/fallback que nao pegou o total) -- mesmo risco de
    # ZeroDivisionError, mensagem tem que distinguir esse caso do "sem
    # BP nenhum" acima.
    bp_sem_total = [x for x in BP_PERIODO if x["conta"] != "TOTAL DO ATIVO"]
    with patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS), \
         patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO", granularidade=None:
               bp_sem_total if tipo == "BP" else DRE_PERIODO), \
         patch("dados_relatorio_comentado.db.listar_historico_grupo", side_effect=lambda conn, cod, tipo, status="ATIVO":
               [dict(x, periodo=PERIODO) for x in bp_sem_total] if tipo == "BP" else [dict(x, periodo=PERIODO) for x in DRE_PERIODO]), \
         patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", return_value=ITENS_ADMIN), \
         patch("dados_relatorio_comentado.db.listar_periodos", return_value=[PERIODO]), \
         patch("dados_relatorio_comentado.indicadores.calcular_indicadores", return_value=_indic_df({PERIODO: INDIC_ROW})):
        try:
            drc.montar_dados_relatorio(
                conn=object(), empresa_codigo="ENERGIA", periodo=PERIODO, periodo_label="1º Semestre 2026",
            )
            assert False, "deveria ter levantado ValueError (BP sem TOTAL DO ATIVO)"
        except ValueError as e:
            assert "TOTAL DO ATIVO" in str(e), f"mensagem nao ficou clara: {e}"
    print("OK: BP sem TOTAL DO ATIVO -> ValueError claro")


def test_sem_dre_gravado_tambem_leva_erro_claro():
    with patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS), \
         patch("dados_relatorio_comentado.db.listar_lancamentos", side_effect=lambda conn, cod, per, tipo, status="ATIVO", granularidade=None:
               BP_PERIODO if tipo == "BP" else []), \
         patch("dados_relatorio_comentado.db.listar_historico_grupo", side_effect=lambda conn, cod, tipo, status="ATIVO":
               [dict(x, periodo=PERIODO) for x in BP_PERIODO] if tipo == "BP" else []), \
         patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", return_value=ITENS_ADMIN), \
         patch("dados_relatorio_comentado.db.listar_periodos", return_value=[PERIODO]), \
         patch("dados_relatorio_comentado.indicadores.calcular_indicadores", return_value=_indic_df({PERIODO: INDIC_ROW})):
        try:
            drc.montar_dados_relatorio(
                conn=object(), empresa_codigo="ENERGIA", periodo=PERIODO, periodo_label="1º Semestre 2026",
            )
            assert False, "deveria ter levantado ValueError (sem DRE gravado)"
        except ValueError as e:
            assert "não tem DRE gravado" in str(e), f"mensagem nao ficou clara: {e}"
    print("OK: sem DRE gravado -> ValueError claro")


# ─────────────────────────────────────────────
#  FIX_20260930 -- variante "padrao"/"gerencial" e empresas_nomes (capa
#  multi-empresa lista nomes em vez de só contagem).
# ─────────────────────────────────────────────
def test_variante_padrao_e_o_default_e_entra_no_cabecalho_e_no_dict():
    dados, _ = _montar()
    assert dados["variante"] == "padrao"
    assert dados["cabecalho_relatorio"] == "Demonstrativo Comentado · 1º Semestre 2026"
    print("OK: variante default ('padrao') aparece em dados['variante'] e no título/cabeçalho")


def test_variante_gerencial_muda_titulo_do_cabecalho():
    dados, _ = _montar(variante="gerencial")
    assert dados["variante"] == "gerencial"
    assert dados["cabecalho_relatorio"] == "Demonstrativo Comentado Gerencial · 1º Semestre 2026"
    print("OK: variante='gerencial' muda o título pra 'Demonstrativo Comentado Gerencial' no cabeçalho")


def test_variante_fornecedor_v040_muda_titulo_e_mantem_conteudo_do_padrao():
    # v0.40.0: "fornecedor" construida -- mesmo conteudo do padrao, so' titulo/selo mudam.
    dados, _ = _montar(variante="fornecedor")
    assert dados["variante"] == "fornecedor"
    # 01/10/2026: o documento nao leva "Fornecedor" (so' o app/sufixo do arquivo)
    assert dados["cabecalho_relatorio"] == "Demonstrativo Comentado · 1º Semestre 2026"
    base, _ = _montar()
    for campo in ("receita_liquida", "ebitda", "resultado_liquido", "total_ativo", "anexo_ativo", "anexo_passivo"):
        assert dados[campo] == base[campo]
    print("OK: variante='fornecedor' muda so' o titulo; numeros iguais ao padrao")


def test_variante_desconhecida_levanta_valueerror_claro():
    try:
        _montar(variante="inexistente")
        assert False, "deveria ter levantado ValueError -- variante desconhecida"
    except ValueError as e:
        assert "inexistente" in str(e) and "fornecedor" in str(e)  # lista as validas, inclui fornecedor
    print("OK: variante desconhecida levanta ValueError claro, listando as validas")


EMPRESAS_GRUPO = [
    {"codigo": "ENERGIA", "nome": "Enermais Energia Ltda", "cnpj": "47.040.664/0001-48"},
    {"codigo": "SMG", "nome": "SMG Soluções", "cnpj": "18.387.666/0001-00"},
]

BP_GRUPO_ACHATADO = [
    {"empresa_codigo": "ENERGIA", "grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": 100.0},
    {"empresa_codigo": "ENERGIA", "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": 100.0},
    {"empresa_codigo": "SMG", "grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": 25.0},
    {"empresa_codigo": "SMG", "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": 25.0},
]
DRE_GRUPO_ACHATADO = [
    {"empresa_codigo": "ENERGIA", "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": 100.0},
    {"empresa_codigo": "ENERGIA", "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": 10.0},
    {"empresa_codigo": "SMG", "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": 20.0},
    {"empresa_codigo": "SMG", "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": 2.0},
]


def test_grupo_preenche_empresas_nomes_com_o_nome_real_de_cada_empresa():
    """FIX_20260930 -- capa multi-empresa (Modelo A) agora lista os NOMES
    das empresas, não só a contagem (ver gerador_relatorio_comentado.
    _nomes_empresas_grupo). Mocka no limite de _consolidar_periodo/
    _consolidar_historico/_consolidar_despesas_admin_itens (visao_grupo.
    montar_pivot_grupo direto) -- a mecânica de consolidação BP/DRE já
    tem teste próprio (test_visao_grupo.py); aqui só confere que
    `empresas_nomes` sai na MESMA ordem de `empresas_codigos`, com o
    nome de verdade (não o código)."""
    import visao_grupo

    def _pivot_fake(lancs, cods):
        df = pd.DataFrame(lancs)
        return df.groupby(["grupo", "conta"], as_index=False)["valor"].sum().rename(
            columns={"valor": "VALOR CONSOLIDADO"}
        )

    with patch("dados_relatorio_comentado.db.listar_empresas", return_value=EMPRESAS_GRUPO), \
         patch("dados_relatorio_comentado.db.listar_periodos_detalhado",
               return_value=[{"periodo": PERIODO, "granularidade": ""}]), \
         patch("dados_relatorio_comentado.db.listar_lancamentos_grupo",
               side_effect=lambda conn, per, tipo, cods, granularidade="":
               BP_GRUPO_ACHATADO if tipo == "BP" else DRE_GRUPO_ACHATADO), \
         patch("dados_relatorio_comentado.visao_grupo.montar_pivot_grupo", side_effect=_pivot_fake), \
         patch("dados_relatorio_comentado.db.listar_periodos_grupo", return_value=[PERIODO]), \
         patch("dados_relatorio_comentado.db.listar_lancamentos_grupo_periodos",
               side_effect=lambda conn, periodos, tipo, cods, status="ATIVO", granularidade=None:
               [dict(x, periodo=PERIODO, granularidade="") for x in (BP_GRUPO_ACHATADO if tipo == "BP" else DRE_GRUPO_ACHATADO)]), \
         patch("dados_relatorio_comentado.db.listar_despesas_admin_itens", return_value=[]), \
         patch("dados_relatorio_comentado.indicadores.calcular_indicadores",
               return_value=_indic_df({PERIODO: INDIC_ROW})):
        dados, _ = drc.montar_dados_relatorio(
            conn=object(), empresa_codigo=["ENERGIA", "SMG"], periodo=PERIODO,
            periodo_label="1º Semestre 2026",
        )
    assert dados["empresas_codigos"] == ["ENERGIA", "SMG"]
    assert dados["empresas_nomes"] == ["Enermais Energia Ltda", "SMG Soluções"], (
        f"empresas_nomes deveria ter o NOME de cada empresa (mesma ordem de empresas_codigos), veio {dados['empresas_nomes']}"
    )
    print("OK: montar_dados_relatorio (grupo, 2 empresas) preenche empresas_nomes com o nome real de cada uma, na ordem certa")


if __name__ == "__main__":
    test_conversao_de_sinal_bate_com_fixture_do_gerador()
    test_despesas_admin_itens_ordenado_e_percentual_correto()
    test_sem_csll_irpj_pula_pagina_resultado_e_nao_inventa_campo()
    test_anexo_ativo_tem_grupo_conta_subtotal_total_na_ordem()
    test_anexo_aninha_subconta_sob_conta_pai_quando_par_confirmado()
    test_mapa_avisa_sobre_conta_duplicada_nas_linhas_ativas()
    test_mapa_sem_duplicata_nao_avisa()
    test_anexo_aninha_3_pares_novos_do_passivo_fix_20260928b()
    test_sem_periodo_anterior_cai_no_fallback_neutro()
    test_com_periodo_anterior_gera_texto_comparativo()
    test_pipeline_completo_nao_quebra_gerando_pdf_de_verdade()
    test_sem_bp_gravado_leva_erro_claro_nao_zerodivisionerror()
    test_bp_gravado_sem_total_do_ativo_leva_erro_claro()
    test_sem_dre_gravado_tambem_leva_erro_claro()
    test_variante_padrao_e_o_default_e_entra_no_cabecalho_e_no_dict()
    test_variante_gerencial_muda_titulo_do_cabecalho()
    test_variante_desconhecida_levanta_valueerror_claro()
    test_grupo_preenche_empresas_nomes_com_o_nome_real_de_cada_empresa()
