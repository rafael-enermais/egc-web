# -*- coding: utf-8 -*-
"""
Testes unitarios de app/consultas_chat.py -- mesmo padrao (sem pytest,
self-running) do resto do projeto. Mocka db.py/visao_grupo.py direto
(consultas_chat.py so' orquestra, nao teria o que testar isolado sem
dado de verdade vindo de algum lugar).

Cobre: resolucao de periodo por texto "AAAA-MM", periodo vazio ->
mais recente, periodo nao encontrado -> erro com lista de disponiveis,
BP/DRE de 1 empresa, Visao Grupo macro e especifica, saida sempre
JSON-serializavel (Decimal/numpy nunca escapam pro tool_result).
"""
import sys
import json
import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import visao_grupo  # noqa: E402
import consultas_chat as cc  # noqa: E402

FALHAS = []


def checar(cond, msg):
    if not cond:
        FALHAS.append(msg)
        print(f"FALHOU: {msg}")
    else:
        print(f"OK: {msg}")


def _p(ano, mes, dia=28):
    return datetime.date(ano, mes, dia)


def _det(*periodos_granul):
    """[(date, granularidade), ...] -> list[dict] (formato listar_periodos_detalhado)."""
    return [{"periodo": p, "granularidade": g} for p, g in periodos_granul]


def test_resolver_periodo_texto_bate():
    disponiveis = _det((_p(2026, 5), ""), (_p(2026, 6), ""), (_p(2026, 7), ""))
    periodo, granul, ambiguo = cc._resolver_periodo_detalhado(disponiveis, "2026-06")
    checar(periodo == _p(2026, 6) and ambiguo is None, "_resolver_periodo_detalhado -- casa por ano/mes, ignora dia")


def test_resolver_periodo_vazio_pega_mais_recente():
    disponiveis = _det((_p(2026, 5), ""), (_p(2026, 7), ""), (_p(2026, 6), ""))
    periodo, granul, ambiguo = cc._resolver_periodo_detalhado(disponiveis, None)
    checar(periodo == _p(2026, 7) and ambiguo is None, "_resolver_periodo_detalhado -- sem texto, pega o mais recente")


def test_resolver_periodo_nao_encontrado():
    disponiveis = _det((_p(2026, 5), ""))
    periodo, granul, ambiguo = cc._resolver_periodo_detalhado(disponiveis, "2026-12")
    checar(periodo is None and ambiguo is None, "_resolver_periodo_detalhado -- periodo nao existente devolve None sem ambiguidade")


def test_resolver_periodo_lista_vazia():
    periodo, granul, ambiguo = cc._resolver_periodo_detalhado([], "2026-06")
    checar(periodo is None and ambiguo is None, "_resolver_periodo_detalhado -- lista vazia devolve None sem quebrar")


def test_resolver_periodo_ambiguo_sem_granularidade_devolve_opcoes():
    """Fase 3.1 (29/09/2026): mesmo periodo_fim com 2 documentos ativos
    (trimestral e semestral) -- sem granularidade_texto, NAO escolhe
    sozinho, devolve as opcoes pro chat perguntar ao usuario."""
    disponiveis = _det((_p(2026, 6), "trimestral"), (_p(2026, 6), "semestral"))
    periodo, granul, ambiguo = cc._resolver_periodo_detalhado(disponiveis, "2026-06")
    checar(periodo is None and granul is None, "resolver ambiguo -- nao escolhe sozinho, periodo/granularidade vem None")
    checar(ambiguo is not None and ambiguo["ambiguo"] is True, "resolver ambiguo -- sinaliza ambiguo=True")
    checar({o["granularidade"] for o in ambiguo["opcoes"]} == {"trimestral", "semestral"},
           "resolver ambiguo -- lista as 2 opcoes reais")


def test_resolver_periodo_ambiguo_com_granularidade_resolve():
    disponiveis = _det((_p(2026, 6), "trimestral"), (_p(2026, 6), "semestral"))
    periodo, granul, ambiguo = cc._resolver_periodo_detalhado(disponiveis, "2026-06", "semestral")
    checar(periodo == _p(2026, 6) and granul == "semestral" and ambiguo is None,
           "resolver ambiguo -- com granularidade_texto certa, resolve pro documento certo")


def test_consultar_bp_dre_ok():
    with patch.object(db, "listar_periodos_detalhado", return_value=_det((_p(2026, 6), ""))), \
         patch.object(db, "listar_lancamentos", return_value=[
             {"id": 1, "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("1000.50"),
              "origem": "PDF", "pdf_original": None, "arquivo_pdf": "x.pdf", "atualizado_em": None},
         ]) as m_lanc:
        r = cc.consultar_bp_dre(conn=None, empresa_codigo="SMG", tipo="BP")
        checar("erro" not in r, "consultar_bp_dre -- sem erro quando periodo existe")
        checar(r["periodo"] == "2026-06", "consultar_bp_dre -- resolve pro periodo mais recente")
        checar(r["contas"][0]["valor"] == 1000.50 and isinstance(r["contas"][0]["valor"], float),
               "consultar_bp_dre -- valor sai como float puro (Decimal convertido)")
        checar(json.dumps(r), "consultar_bp_dre -- saida e' JSON-serializavel")
        checar(m_lanc.call_args[0][2] == _p(2026, 6), "consultar_bp_dre -- passa o periodo resolvido pro db.py")


def test_consultar_bp_dre_periodo_nao_encontrado():
    with patch.object(db, "listar_periodos_detalhado", return_value=_det((_p(2026, 5), ""))):
        r = cc.consultar_bp_dre(conn=None, empresa_codigo="SMG", tipo="BP", periodo_texto="2026-12")
        checar("erro" in r, "consultar_bp_dre -- periodo texto nao encontrado devolve erro, nao excecao")
        checar(r["periodos_disponiveis"] == ["2026-05"], "consultar_bp_dre -- erro lista os periodos disponiveis de verdade")


def test_consultar_bp_dre_ambiguo_pede_granularidade_sem_adivinhar():
    """Fase 3.1: trimestral e semestral ativos pra 06/2026 -- sem
    granularidade, consultar_bp_dre NAO deve devolver dado nenhum (REGRA
    DE OURO/REGRA CRITICA do chat: nunca inventar/adivinhar qual dos 2
    documentos o usuario quis)."""
    with patch.object(db, "listar_periodos_detalhado", return_value=_det((_p(2026, 6), "trimestral"), (_p(2026, 6), "semestral"))), \
         patch.object(db, "listar_lancamentos") as m_lanc:
        r = cc.consultar_bp_dre(conn=None, empresa_codigo="SMG", tipo="BP", periodo_texto="2026-06")
        checar(r.get("ambiguo") is True, "consultar_bp_dre ambiguo -- sinaliza ambiguo=True")
        checar(not m_lanc.called, "consultar_bp_dre ambiguo -- NAO busca lancamento nenhum sem a granularidade resolvida")
        checar(json.dumps(r), "consultar_bp_dre ambiguo -- saida e' JSON-serializavel")


def test_consultar_bp_dre_ambiguo_resolvido_com_granularidade():
    with patch.object(db, "listar_periodos_detalhado", return_value=_det((_p(2026, 6), "trimestral"), (_p(2026, 6), "semestral"))), \
         patch.object(db, "listar_lancamentos", return_value=[{"grupo": "ATIVO", "conta": "TOTAL DO ATIVO", "valor": Decimal("1.00")}]) as m_lanc:
        r = cc.consultar_bp_dre(conn=None, empresa_codigo="SMG", tipo="BP", periodo_texto="2026-06", granularidade="semestral")
        checar("ambiguo" not in r, "consultar_bp_dre -- com granularidade certa, resolve sem ambiguidade")
        checar(r["granularidade"] == "semestral", "consultar_bp_dre -- ecoa a granularidade resolvida")
        checar(m_lanc.call_args.kwargs.get("granularidade") == "semestral",
               "consultar_bp_dre -- passa a granularidade resolvida pro db.listar_lancamentos")


def test_consultar_bp_dre_tipo_sem_lancamento_no_periodo_devolve_erro_claro():
    # v0.44.2: periodo existe (so' DRE importado) mas BP nao -- antes devolvia
    # "0 contas" como sucesso e a Consulta rapida do Erik.AI quebrava (KeyError 'valor').
    with patch.object(db, "listar_periodos_detalhado", return_value=_det((_p(2025, 12), "anual"))), \
         patch.object(db, "listar_lancamentos", return_value=[]):
        r = cc.consultar_bp_dre(conn=None, empresa_codigo="CONST", tipo="BP")
        checar("erro" in r and "BP" in r["erro"], "consultar_bp_dre -- tipo sem lancamento no periodo vira erro claro")
        checar(r["periodos_disponiveis"] == ["2025-12"], "consultar_bp_dre -- lista periodos disponiveis no erro")
        checar("contas" not in r, "consultar_bp_dre -- sem 'contas' vazio disfarçado de sucesso")


def test_consultar_visao_grupo_macro():
    lancs = [
        {"empresa_codigo": "ENERGIA", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("100000.00")},
        {"empresa_codigo": "SMG", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("50000.00")},
    ]
    with patch.object(db, "listar_periodos_grupo_detalhado", return_value=_det((_p(2026, 6), ""))), \
         patch.object(db, "listar_lancamentos_grupo", return_value=lancs):
        r = cc.consultar_visao_grupo(conn=None, tipo="BP", empresas_codigos=["ENERGIA", "SMG"])
        checar("erro" not in r, "consultar_visao_grupo macro -- sem erro")
        checar(r["visao"] == "macro", "consultar_visao_grupo -- default e' macro")
        conta = r["contas"][0]
        checar(conta["VALOR CONSOLIDADO"] == 150000.0, "consultar_visao_grupo macro -- consolida certo")
        checar(all(not hasattr(v, "item") for v in conta.values()), "consultar_visao_grupo macro -- sem numpy scalar sobrando")
        checar(json.dumps(r), "consultar_visao_grupo macro -- saida e' JSON-serializavel")


def test_consultar_visao_grupo_especifica():
    lancs = [
        {"empresa_codigo": "ENERGIA", "grupo": "ATIVO CIRCULANTE", "conta": "CAIXA", "valor": Decimal("500.00")},
    ]
    with patch.object(db, "listar_periodos_grupo_detalhado", return_value=_det((_p(2026, 6), ""))), \
         patch.object(db, "listar_lancamentos_grupo", return_value=lancs):
        r = cc.consultar_visao_grupo(conn=None, tipo="BP", empresas_codigos=["ENERGIA"], visao="especifica")
        checar(r["visao"] == "especifica", "consultar_visao_grupo especifica -- visao respeitada")
        checar("ENERGIA" in r["contas"][0], "consultar_visao_grupo especifica -- coluna por empresa presente")
        checar(json.dumps(r), "consultar_visao_grupo especifica -- saida e' JSON-serializavel")


def test_consultar_visao_grupo_ambiguo_pede_granularidade_sem_adivinhar():
    with patch.object(db, "listar_periodos_grupo_detalhado", return_value=_det((_p(2026, 6), "trimestral"), (_p(2026, 6), "semestral"))), \
         patch.object(db, "listar_lancamentos_grupo") as m_lanc:
        r = cc.consultar_visao_grupo(conn=None, tipo="BP", empresas_codigos=["ENERGIA", "SMG"])
        checar(r.get("ambiguo") is True, "consultar_visao_grupo ambiguo -- sinaliza ambiguo=True")
        checar(not m_lanc.called, "consultar_visao_grupo ambiguo -- NAO busca lancamento nenhum sem a granularidade resolvida")


def test_consultar_periodos():
    with patch.object(db, "listar_periodos_detalhado", side_effect=[
        _det((_p(2026, 6), ""), (_p(2026, 5), "")), _det((_p(2026, 6), "")),
    ]):
        r = cc.consultar_periodos(conn=None, empresas_codigos=["ENERGIA", "SMG"])
        checar(r["periodos_por_empresa"]["ENERGIA"] == ["2026-06", "2026-05"], "consultar_periodos -- ordenado desc, formato AAAA-MM")
        checar(r["periodos_por_empresa"]["SMG"] == ["2026-06"], "consultar_periodos -- por empresa, independente")
        checar("periodos_com_mais_de_1_documento" not in r, "consultar_periodos -- sem ambiguidade, chave extra nem aparece")


def test_consultar_periodos_marca_periodo_com_mais_de_1_documento():
    """Fase 3.1: 06/2026 tem trimestral e semestral ativos -- consultar_periodos
    precisa expor isso explicitamente pro modelo saber que precisa perguntar
    a granularidade antes de consultar esse periodo."""
    with patch.object(db, "listar_periodos_detalhado", return_value=_det(
        (_p(2026, 6), "trimestral"), (_p(2026, 6), "semestral"), (_p(2026, 5), "mensal"),
    )):
        r = cc.consultar_periodos(conn=None, empresas_codigos=["ENERGIA"])
        checar("periodos_com_mais_de_1_documento" in r, "consultar_periodos -- sinaliza periodo com 2 documentos")
        opcoes = r["periodos_com_mais_de_1_documento"]["ENERGIA"]
        checar({o["granularidade"] for o in opcoes} == {"trimestral", "semestral"},
               "consultar_periodos -- so' lista as granularidades do periodo ambiguo, nao o mensal isolado")


# ── Erik.AI (23/09/2026) -- consultar_indicadores / consultar_completude ──

LANCS_BP_1EMPRESA = [
    {"periodo": _p(2026, 6), "grupo": "ATIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE ATIVO", "valor": Decimal("200000.00")},
    {"periodo": _p(2026, 6), "grupo": "PASSIVO CIRCULANTE", "conta": "TOTAL CIRCULANTE PASSIVO", "valor": Decimal("100000.00")},
    {"periodo": _p(2026, 6), "grupo": "PASSIVO NAO CIRCULANTE", "conta": "TOTAL NAO CIRCULANTE PASSIVO", "valor": Decimal("50000.00")},
    {"periodo": _p(2026, 6), "grupo": "TOTAL", "conta": "TOTAL DO ATIVO", "valor": Decimal("1000000.00")},
    {"periodo": _p(2026, 6), "grupo": "PATRIMONIO LIQUIDO", "conta": "TOTAL PATRIMONIO LIQUIDO", "valor": Decimal("850000.00")},
]
LANCS_DRE_1EMPRESA = [
    {"periodo": _p(2026, 6), "grupo": "RESULTADO", "conta": "RECEITA OPERACIONAL LIQUIDA", "valor": Decimal("300000.00")},
    {"periodo": _p(2026, 6), "grupo": "RESULTADO", "conta": "LUCRO BRUTO", "valor": Decimal("110000.00")},
    {"periodo": _p(2026, 6), "grupo": "RESULTADO", "conta": "LUCRO LIQUIDO DO EXERCICIO", "valor": Decimal("50000.00")},
]


def test_consultar_indicadores_1_empresa_usa_historico_direto():
    with patch.object(db, "listar_historico_grupo", side_effect=[LANCS_BP_1EMPRESA, LANCS_DRE_1EMPRESA]) as m_hist,          patch.object(db, "listar_periodos_grupo") as m_grupo:
        r = cc.consultar_indicadores(conn=None, empresas_codigos=["SMG"])
        checar("erro" not in r, "consultar_indicadores 1 empresa -- sem erro")
        checar(r["periodo"] == "2026-06", "consultar_indicadores 1 empresa -- periodo mais recente certo")
        checar(r["indicadores"]["Liquidez Corrente"] == 2.0, "consultar_indicadores 1 empresa -- Liquidez Corrente calculada certa (200k/100k)")
        checar(isinstance(r["indicadores"]["Liquidez Corrente"], float), "consultar_indicadores -- valor sai como float puro")
        checar(m_hist.call_count == 2, "consultar_indicadores 1 empresa -- usa listar_historico_grupo (caminho direto)")
        checar(not m_grupo.called, "consultar_indicadores 1 empresa -- NAO usa o caminho multi-empresa")
        checar(json.dumps(r), "consultar_indicadores 1 empresa -- saida e' JSON-serializavel")


def test_consultar_indicadores_multi_empresa_usa_caminho_do_grupo():
    with patch.object(db, "listar_periodos_grupo", return_value=[_p(2026, 6)]) as m_periodos,          patch.object(db, "listar_lancamentos_grupo_periodos", side_effect=[LANCS_BP_1EMPRESA, LANCS_DRE_1EMPRESA]) as m_lancs:
        r = cc.consultar_indicadores(conn=None, empresas_codigos=["ENERGIA", "SMG"])
        checar("erro" not in r, "consultar_indicadores multi-empresa -- sem erro")
        checar(m_periodos.called and m_lancs.call_count == 2,
               "consultar_indicadores multi-empresa -- usa listar_periodos_grupo + listar_lancamentos_grupo_periodos (mesmo caminho do KPI consolidado da Início)")
        checar(json.dumps(r), "consultar_indicadores multi-empresa -- saida e' JSON-serializavel")


def test_consultar_indicadores_sem_dado_devolve_erro():
    with patch.object(db, "listar_historico_grupo", return_value=[]):
        r = cc.consultar_indicadores(conn=None, empresas_codigos=["SMG"])
        checar("erro" in r, "consultar_indicadores -- sem BP/DRE nenhum devolve erro, nao excecao")


def test_consultar_completude():
    empresas = ["ENERGIA", "SMG"]
    lancs_bp = [{"empresa_codigo": "ENERGIA", "periodo": _p(2026, 6), "granularidade": ""}]  # so' ENERGIA tem BP -- SMG falta
    lancs_dre = [
        {"empresa_codigo": "ENERGIA", "periodo": _p(2026, 6), "granularidade": ""},
        {"empresa_codigo": "SMG", "periodo": _p(2026, 6), "granularidade": ""},
    ]
    with patch.object(db, "listar_periodos_grupo_detalhado", return_value=_det((_p(2026, 6), ""))),          patch.object(db, "listar_lancamentos_grupo_periodos", side_effect=[lancs_bp, lancs_dre]):
        r = cc.consultar_completude(conn=None, empresas_codigos=empresas)
        checar(r["empresas_incluidas"] == empresas, "consultar_completude -- ecoa as empresas checadas")
        checar(len(r["completude_por_periodo"]) == 1, "consultar_completude -- 1 periodo no resumo")
        linha = r["completude_por_periodo"][0]
        checar(linha["Período"] == "2026-06", "consultar_completude -- Período formatado AAAA-MM")
        checar("Incompleto" in linha["Status"], "consultar_completude -- SMG sem BP marca período como incompleto")
        checar("SMG" in linha["Empresas pendentes"], "consultar_completude -- SMG aparece como pendente")
        checar(json.dumps(r), "consultar_completude -- saida e' JSON-serializavel")


def test_consultar_completude_sem_periodo_nenhum():
    with patch.object(db, "listar_periodos_grupo_detalhado", return_value=[]),          patch.object(db, "listar_lancamentos_grupo_periodos", return_value=[]):
        r = cc.consultar_completude(conn=None, empresas_codigos=["ENERGIA"])
        checar(r["completude_por_periodo"] == [], "consultar_completude -- sem período nenhum devolve lista vazia, nao excecao")


def test_consultar_completude_2_granularidades_mesmo_periodo_fim_nao_se_misturam():
    """Fase 3.1: trimestral e semestral ATIVOS pra 06/2026 -- tem que virar
    2 linhas distintas de completude, cada 1 com seu status, nao 1 so'."""
    empresas = ["ENERGIA"]
    lancs_bp = [
        {"empresa_codigo": "ENERGIA", "periodo": _p(2026, 6), "granularidade": "trimestral"},
        {"empresa_codigo": "ENERGIA", "periodo": _p(2026, 6), "granularidade": "semestral"},
    ]
    lancs_dre = [{"empresa_codigo": "ENERGIA", "periodo": _p(2026, 6), "granularidade": "semestral"}]  # DRE trimestral falta
    with patch.object(db, "listar_periodos_grupo_detalhado", return_value=_det((_p(2026, 6), "trimestral"), (_p(2026, 6), "semestral"))),          patch.object(db, "listar_lancamentos_grupo_periodos", side_effect=[lancs_bp, lancs_dre]):
        r = cc.consultar_completude(conn=None, empresas_codigos=empresas)
        checar(len(r["completude_por_periodo"]) == 2, "consultar_completude -- 2 granularidades viram 2 linhas distintas")
        por_granul = {l["Granularidade"]: l for l in r["completude_por_periodo"]}
        checar("Completo" in por_granul["semestral"]["Status"], "consultar_completude -- semestral completo (BP+DRE)")
        checar("Incompleto" in por_granul["trimestral"]["Status"], "consultar_completude -- trimestral incompleto (so' BP)")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in testes:
        t()
    print()
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S)")
        sys.exit(1)
    print(f"{len(testes)}/{len(testes)} testes passaram")
    sys.exit(0)
