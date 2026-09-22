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


def test_resolver_periodo_texto_bate():
    disponiveis = [_p(2026, 5), _p(2026, 6), _p(2026, 7)]
    r = cc._resolver_periodo(disponiveis, "2026-06")
    checar(r == _p(2026, 6), "_resolver_periodo -- casa por ano/mes, ignora dia")


def test_resolver_periodo_vazio_pega_mais_recente():
    disponiveis = [_p(2026, 5), _p(2026, 7), _p(2026, 6)]
    r = cc._resolver_periodo(disponiveis, None)
    checar(r == _p(2026, 7), "_resolver_periodo -- sem texto, pega o mais recente")


def test_resolver_periodo_nao_encontrado():
    disponiveis = [_p(2026, 5)]
    r = cc._resolver_periodo(disponiveis, "2026-12")
    checar(r is None, "_resolver_periodo -- periodo nao existente devolve None")


def test_resolver_periodo_lista_vazia():
    r = cc._resolver_periodo([], "2026-06")
    checar(r is None, "_resolver_periodo -- lista vazia devolve None sem quebrar")


def test_consultar_bp_dre_ok():
    with patch.object(db, "listar_periodos", return_value=[_p(2026, 6)]), \
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
    with patch.object(db, "listar_periodos", return_value=[_p(2026, 5)]):
        r = cc.consultar_bp_dre(conn=None, empresa_codigo="SMG", tipo="BP", periodo_texto="2026-12")
        checar("erro" in r, "consultar_bp_dre -- periodo texto nao encontrado devolve erro, nao excecao")
        checar(r["periodos_disponiveis"] == ["2026-05"], "consultar_bp_dre -- erro lista os periodos disponiveis de verdade")


def test_consultar_visao_grupo_macro():
    lancs = [
        {"empresa_codigo": "ENERGIA", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("100000.00")},
        {"empresa_codigo": "SMG", "grupo": "ATIVO CIRCULANTE", "conta": "CLIENTES", "valor": Decimal("50000.00")},
    ]
    with patch.object(db, "listar_periodos_grupo", return_value=[_p(2026, 6)]), \
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
    with patch.object(db, "listar_periodos_grupo", return_value=[_p(2026, 6)]), \
         patch.object(db, "listar_lancamentos_grupo", return_value=lancs):
        r = cc.consultar_visao_grupo(conn=None, tipo="BP", empresas_codigos=["ENERGIA"], visao="especifica")
        checar(r["visao"] == "especifica", "consultar_visao_grupo especifica -- visao respeitada")
        checar("ENERGIA" in r["contas"][0], "consultar_visao_grupo especifica -- coluna por empresa presente")
        checar(json.dumps(r), "consultar_visao_grupo especifica -- saida e' JSON-serializavel")


def test_consultar_periodos():
    with patch.object(db, "listar_periodos", side_effect=[[_p(2026, 6), _p(2026, 5)], [_p(2026, 6)]]):
        r = cc.consultar_periodos(conn=None, empresas_codigos=["ENERGIA", "SMG"])
        checar(r["periodos_por_empresa"]["ENERGIA"] == ["2026-06", "2026-05"], "consultar_periodos -- ordenado desc, formato AAAA-MM")
        checar(r["periodos_por_empresa"]["SMG"] == ["2026-06"], "consultar_periodos -- por empresa, independente")


if __name__ == "__main__":
    test_resolver_periodo_texto_bate()
    test_resolver_periodo_vazio_pega_mais_recente()
    test_resolver_periodo_nao_encontrado()
    test_resolver_periodo_lista_vazia()
    test_consultar_bp_dre_ok()
    test_consultar_bp_dre_periodo_nao_encontrado()
    test_consultar_visao_grupo_macro()
    test_consultar_visao_grupo_especifica()
    test_consultar_periodos()
    print()
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S)")
        sys.exit(1)
    print("9/9 testes passaram")
    sys.exit(0)
