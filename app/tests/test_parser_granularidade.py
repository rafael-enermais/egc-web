# -*- coding: utf-8 -*-
"""
Regressao da Fase 1 de granularidade real (24/09/2026, pedido do Rafael):
o parser agora le a data de INICIO do "Periodo: X a Y" do proprio PDF
(antes so' guardava o fim) e calcular_granularidade() classifica o
intervalo REAL declarado -- nunca por delta entre 2 fechamentos
diferentes (isso seria estimativa, nao dado real).

Validado contra PDFs reais da pasta AMOSTRAS_NOVOS e BP E DRE (1) do
vault antes deste teste existir:
  - "Periodo: 01/04/2026 a 30/06/2026" (Energia 2T2026, real) -> trimestral
  - "Periodo: 01/01/2025 a 31/12/2025" (varios DRE anuais SPED 2025) -> anual
  - BP nao declara "Periodo: X a Y" nenhuma vez (e' foto de 1 data, sem
    intervalo) -> periodo_inicio sempre vazio, granularidade sempre "".

Rodar: python3 tests/test_parser_granularidade.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from parser_egc import calcular_granularidade


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    return condicao


def main():
    ok = True

    ok &= checar(
        "1 mes (01/06 a 30/06) -> mensal",
        calcular_granularidade("01/06/2026", "30/06/2026") == "mensal",
    )
    ok &= checar(
        "1 trimestre real (Energia 2T2026, 01/04 a 30/06) -> trimestral",
        calcular_granularidade("01/04/2026", "30/06/2026") == "trimestral",
    )
    ok &= checar(
        "1 semestre (01/01 a 30/06) -> semestral",
        calcular_granularidade("01/01/2026", "30/06/2026") == "semestral",
    )
    ok &= checar(
        "1 ano real (varios DRE SPED 2025, 01/01 a 31/12) -> anual",
        calcular_granularidade("01/01/2025", "31/12/2025") == "anual",
    )
    ok &= checar(
        "sem periodo_inicio (BP -- nao declara intervalo, e' foto de 1 data) -> "
        "'' (nunca inventa granularidade)",
        calcular_granularidade("", "30/06/2026") == "",
    )
    ok &= checar(
        "sem periodo_fim -> '' (nunca inventa)",
        calcular_granularidade("01/06/2026", "") == "",
    )
    ok &= checar(
        "datas invalidas -> '' (nao quebra, nao inventa)",
        calcular_granularidade("31/13/2026", "30/06/2026") == "",
    )
    ok &= checar(
        "intervalo maior que 1 ano (empresa com exercicio social atipico) -> outra",
        calcular_granularidade("01/01/2024", "31/12/2025") == "outra",
    )

    print("\nRESULTADO:", "TODOS OS TESTES PASSARAM" if ok else "HA DIVERGENCIA")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
