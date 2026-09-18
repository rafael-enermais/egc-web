"""
Valida que o parser portado (parser_egc.py, copia byte-a-byte de
leitor_pdf_enermais_v5.py) reproduz exatamente os dados que ja estao
em producao no Excel/VBA (RELATORIOS_ENERMAIS_V3.0.xlsm).

Rodar: python3 tests/test_parser_fidelidade.py <caminho para pasta AMOSTRAS_NOVOS>

Caso de referencia: Enermais Energia, periodo 31/12/2023, formato TEXTO.
Valores abaixo copiados diretamente das abas BP ENERGIA / DRE ENERGIA
vivas em 18/09/2026 (ver EGC/00-handoff.md secao 17 no vault).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from parser_egc import processar_pdf

ESPERADO_BP = {
    "TOTAL DO ATIVO": "R$ 3.398.875,31",
    "TOTAL DO PASSIVO": "R$ 3.398.875,31",
    "CLIENTES": "R$ 1.094.484,54",
    "TOTAL PATRIMONIO LIQUIDO": "R$ 2.050.846,38",
}
ESPERADO_DRE = {
    "RECEITA OPERACIONAL LIQUIDA": "R$ 3.327.615,35",
    "LUCRO BRUTO": "R$ 3.115.315,35",
    "LUCRO LIQUIDO DO EXERCICIO": "R$ 1.285.930,76",
}

def main():
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    bp_pdf = base / "2023.12 - BALANÇO - ENERMAIS ENERGIA LTDA.pdf"
    dre_pdf = base / "2023.12 - DRE - ENERMAIS ENERGIA LTDA.pdf"

    ok = True
    bp_rows, _, _, _ = processar_pdf(bp_pdf)
    bp_map = {conta: valor for _, conta, valor, _ in bp_rows}
    for conta, esperado in ESPERADO_BP.items():
        real = bp_map.get(conta)
        status = "OK" if real == esperado else "FALHOU"
        if real != esperado:
            ok = False
        print(f"[{status}] BP {conta}: esperado={esperado!r} obtido={real!r}")

    _, dre_rows, _, _ = processar_pdf(dre_pdf)
    dre_map = {conta: valor for conta, valor, _, _ in dre_rows}
    for conta, esperado in ESPERADO_DRE.items():
        real = dre_map.get(conta)
        status = "OK" if real == esperado else "FALHOU"
        if real != esperado:
            ok = False
        print(f"[{status}] DRE {conta}: esperado={esperado!r} obtido={real!r}")

    print("\nRESULTADO:", "TODOS OS TESTES PASSARAM" if ok else "HA DIVERGENCIA — NAO PROSSEGUIR SEM INVESTIGAR")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
