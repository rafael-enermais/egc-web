# -*- coding: utf-8 -*-
"""
Testes unitarios de app/validacoes.py -- so' montar_detalhe_correcoes
(23/09/2026, achado real do Rafael: log de correcao antes so' contava
quantas contas mudaram, nunca dizia QUAL nem o valor antigo/novo).
checar_fechamento_bp/formatar_br ja existiam sem teste dedicado (cobertos
indiretamente via test_importar_pdf_app.py) -- nao mexidos aqui.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from validacoes import montar_detalhe_correcoes  # noqa: E402


def test_montar_detalhe_correcoes_1_conta():
    detalhe = montar_detalhe_correcoes([("CLIENTES", 1000.0, 1200.5)])
    assert detalhe == "CLIENTES: R$ 1.000,00 → R$ 1.200,50"
    print("OK: montar_detalhe_correcoes — 1 conta, formatação BR certa")


def test_montar_detalhe_correcoes_varias_contas_separadas_por_ponto_e_virgula():
    detalhe = montar_detalhe_correcoes([
        ("CLIENTES", 1000.0, 1200.5),
        ("DISPONIVEL", 500.0, 480.25),
    ])
    assert detalhe == "CLIENTES: R$ 1.000,00 → R$ 1.200,50; DISPONIVEL: R$ 500,00 → R$ 480,25"
    print("OK: montar_detalhe_correcoes — várias contas, separadas por '; '")


def test_montar_detalhe_correcoes_negativo_e_lista_vazia():
    assert montar_detalhe_correcoes([("PREJUIZO", -1000.0, -1200.0)]) == "PREJUIZO: -R$ 1.000,00 → -R$ 1.200,00"
    assert montar_detalhe_correcoes([]) == ""
    print("OK: montar_detalhe_correcoes — valor negativo e lista vazia (sem quebrar)")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in testes:
        try:
            t()
        except AssertionError as e:
            falhas += 1
            print(f"FALHOU: {t.__name__} — {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
