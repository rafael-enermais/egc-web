# -*- coding: utf-8 -*-
"""
Regressao dos 2 bugs reais achados/corrigidos em 24/09/2026 auditando os
20 DRE unicos da pasta do vault (EGC/arquivos):

1. "Resultado Antes da CS e IR" sequestrava o valor de "Lucro Liquido do
   Exercicio" quando o DRE tinha CSLL/IRPJ provisionados como linha
   propria entre os dois (achado real: Enermais Energia 05/2026 e
   Enermais Construtora 2T2026, R$ 231k e R$ 204k de diferenca real).
2. Depreciacao/Amortizacao do periodo pode vir em MAIS DE 1 linha no
   mesmo DRE (achado real: 2024.12 Enermais Energia tem "Depreciacoes"
   E "Depreciacao de Veiculos" separadas) -- extrair_deprec_amortiz()
   precisa somar todas, nao so' a 1a.

Usa process_candidates/extrair_deprec_amortiz direto com candidatos
sinteticos (desc, valor, bloco) -- mesmo formato que os parsers reais
(parse_sped/parse_duplo/parse_texto) produzem internamente -- sem
depender de PDF real (esses ficam so' em test_parser_fidelidade.py,
que roda contra a pasta AMOSTRAS_NOVOS do vault, fora do repo).

Rodar: python3 tests/test_parser_dre_fixes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from parser_egc import process_candidates, extrair_deprec_amortiz, DRE_TARGETS


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    return condicao


def main():
    ok = True

    # --- Bug 1: Resultado Antes da CS e IR != Lucro Liquido do Exercicio
    # quando ha provisao de CSLL/IRPJ entre os dois (layout real:
    # Construtora 2T2026) ---
    candidatos_com_provisao = [
        ("Receita Operacional Liquida", 6004883.69, "QUALQUER"),
        ("Lucro Bruto", 5860968.05, "QUALQUER"),
        ("Resultado Antes da CS e IR", 2310074.21, "QUALQUER"),
        ("Provisao para Contribuicao Social", -73742.65, "QUALQUER"),
        ("Provisao para Imposto de Renda", -130560.47, "QUALQUER"),
        ("Lucro Liquido do Exercicio", 2105771.09, "QUALQUER"),
    ]
    rows = process_candidates(candidatos_com_provisao, DRE_TARGETS, "DRE", "teste")
    mapa = {r[0]: r[1] for r in rows}
    ok &= checar(
        "Resultado Antes da CS e IR nao sequestra Lucro Liquido do Exercicio "
        "(bug real: Construtora 2T2026)",
        mapa.get("LUCRO LIQUIDO DO EXERCICIO") == "R$ 2.105.771,09",
    )

    # --- Regressao: SEM provisao (maioria dos casos reais), os 2 valores
    # sao iguais mesmo -- garantir que continua extraindo certo (nao
    # quebrou o caso comum ao tirar o alias) ---
    candidatos_sem_provisao = [
        ("Lucro Operacional Liquido", 164867.79, "QUALQUER"),
        ("Resultado Antes da Cs e Ir", 164867.79, "QUALQUER"),
        ("Lucro Liquido do Exercicio", 164867.79, "QUALQUER"),
    ]
    rows2 = process_candidates(candidatos_sem_provisao, DRE_TARGETS, "DRE", "teste")
    mapa2 = {r[0]: r[1] for r in rows2}
    ok &= checar(
        "Sem provisao CSLL/IR, Lucro Liquido do Exercicio continua extraido certo "
        "(regressao: SMG 2023)",
        mapa2.get("LUCRO LIQUIDO DO EXERCICIO") == "R$ 164.867,79",
    )

    # --- Bug 2: soma multiplas linhas de depreciacao/amortizacao ---
    candidatos_2_linhas = [
        ("Depreciacoes", -96965.67, "QUALQUER"),
        ("Depreciacao de Veiculos", -167490.27, "QUALQUER"),
    ]
    extra = extrair_deprec_amortiz(candidatos_2_linhas)
    ok &= checar(
        "extrair_deprec_amortiz soma 2 linhas de depreciacao separadas "
        "(bug real: 2024.12 Enermais Energia, 96.965,67 + 167.490,27)",
        abs(extra.get("DEPRECIACOES", 0) - (-264455.94)) < 0.01,
    )

    # --- Amortizacoes contada separado de Depreciacoes ---
    candidatos_dep_e_amort = [
        ("Depreciacoes", -3504.54, "QUALQUER"),
        ("Amortizacoes", -3382.00, "QUALQUER"),
    ]
    extra2 = extrair_deprec_amortiz(candidatos_dep_e_amort)
    ok &= checar(
        "Depreciacoes e Amortizacoes ficam em chaves separadas (nao somam junto)",
        abs(extra2["DEPRECIACOES"] - (-3504.54)) < 0.01
        and abs(extra2["AMORTIZACOES"] - (-3382.00)) < 0.01,
    )

    # --- Sem depreciacao no periodo: nao inventa linha (regra "nunca
    # inventa numero") ---
    extra3 = extrair_deprec_amortiz([("Administrativas", -100.0, "QUALQUER")])
    ok &= checar(
        "Sem depreciacao/amortizacao no periodo, nao cria a chave (nao inventa valor)",
        "DEPRECIACOES" not in extra3 and "AMORTIZACOES" not in extra3,
    )

    print("\nRESULTADO:", "TODOS OS TESTES PASSARAM" if ok else "HA DIVERGENCIA")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
