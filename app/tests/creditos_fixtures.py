# -*- coding: utf-8 -*-
"""
Arquivos SINTETICOS para os testes de Creditos PIS/COFINS (v0.50.0): um SPED EFD-Contribuicoes e um Detalhamento da contadora
montados em memoria, com valores inventados (nenhum dado real). Os offsets de campo seguem o Guia Pratico da EFD-Contribuicoes
(os mesmos lidos por sped_contribuicoes.ler_sped).

Mes: 07/2026, empresa Energia (CNPJ 47.040.664/0001-48). Credito: PIS 127,05 / COFINS 585,20 (1 servico, 1 aluguel, 1 combustivel,
1 energia, 1 depreciacao). O Detalhamento traz tudo MENOS a depreciacao (como no arquivo real: o F120 nao tem nota).
"""
from __future__ import annotations

import io

import openpyxl

CNPJ_ENERGIA = "47040664000148"
FORN_A, FORN_B, FORN_C, FORN_D = "11111111000111", "22222222000122", "33333333000133", "44444444000144"


def _reg(codigo: str, **campos) -> str:
    """Monta '|CODIGO|c1|c2|...|' -- campos por indice (c1=1, ...); lacunas ficam vazias."""
    n = max([int(k[1:]) for k in campos] + [0])
    v = [""] * (n + 1)
    for k, val in campos.items():
        v[int(k[1:])] = str(val)
    return "|" + codigo + "|" + "|".join(v[1:]) + "|"


def _v(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def sped_sintetico(competencia: str = "07/2026", tipo: str = "0", ajuste_servico: float = 0.0, nome: str = "") -> tuple[bytes, str]:
    """Devolve (conteudo em ISO-8859-1, nome do arquivo). `ajuste_servico` soma ao PIS do servico (para criar uma divergencia)."""
    mm, aaaa = competencia.split("/")
    ini, fim = f"01{mm}{aaaa}", f"28{mm}{aaaa}"
    pis_serv = round(16.50 + ajuste_servico, 2)
    L = [
        _reg("0000", c1="006", c2=tipo, c3="", c4="", c5=ini, c6=fim, c7="ENERMAIS ENERGIA LTDA", c8=CNPJ_ENERGIA, c9="MG"),
        _reg("0150", c1="F1", c2="FORNECEDOR DE SERVICO A", c3="1058", c4=FORN_A),
        _reg("0150", c1="F2", c2="IMOBILIARIA B", c3="1058", c4=FORN_B),
        _reg("0150", c1="F3", c2="POSTO C", c3="1058", c4=FORN_C),
        _reg("0150", c1="F4", c2="CONCESSIONARIA D", c3="1058", c4=FORN_D),
        _reg("0150", c1="CL1", c2="CLIENTE X", c3="1058", c4="55555555000155"),
        _reg("0200", c1="IT1", c2="", c3="OLEO DIESEL S10"),
        # servico tomado (A170, NAT 03)
        _reg("A100", c1="0", c2="1", c3="F1", c4="00", c7="1001", c9=f"10{mm}{aaaa}"),
        _reg("A170", c1="1", c2="SRV1", c3="MANUTENCAO", c4="1000,00", c6="03", c8="50", c9="1000,00", c10="1,65", c11=_v(pis_serv),
             c12="50", c13="1000,00", c14="7,60", c15="76,00", c16="1.01"),
        # aluguel (A170, NAT 05)
        _reg("A100", c1="0", c2="1", c3="F2", c4="00", c7="55", c9=f"05{mm}{aaaa}"),
        _reg("A170", c1="1", c2="ALG1", c3="ALUGUEL GALPAO", c4="2000,00", c6="05", c8="50", c9="2000,00", c10="1,65", c11="33,00",
             c12="50", c13="2000,00", c14="7,60", c15="152,00", c16="1.02"),
        # combustivel (C100/C170, CFOP 1652)
        _reg("C100", c1="0", c2="1", c3="F3", c4="55", c5="00", c6="1", c7="777", c9=f"12{mm}{aaaa}", c10=f"12{mm}{aaaa}"),
        _reg("C170", c1="1", c2="IT1", c3="OLEO DIESEL S10", c6="500,00", c10="1652", c24="50", c25="500,00", c26="1,65", c29="8,25",
             c30="50", c31="500,00", c32="7,60", c35="38,00", c36="1.03"),
        # energia (C500/C501/C505)
        _reg("C500", c1="F4", c6="9001", c7=f"15{mm}{aaaa}"),
        _reg("C501", c1="50", c2="3000,00", c3="04", c4="3000,00", c5="1,65", c6="49,50", c7="1.04"),
        _reg("C505", c1="50", c2="3000,00", c3="04", c4="3000,00", c5="7,60", c6="228,00", c7="1.04"),
        # depreciacao (F120, sem nota)
        _reg("F120", c1="09", c2="BEM001", c7="53", c8="1200,00", c9="1,65", c10="19,80", c11="53", c12="1200,00", c13="7,60",
             c14="91,20", c15="1.05", c17="PAINEL SOLAR"),
        # venda (debito)
        _reg("A100", c1="1", c2="0", c3="CL1", c4="00", c7="5000", c9=f"20{mm}{aaaa}"),
        _reg("A170", c1="1", c2="SRVV", c3="SERVICO DE ENGENHARIA", c4="10000,00", c6="", c8="01", c9="10000,00", c10="1,65", c11="165,00",
             c12="01", c13="10000,00", c14="7,60", c15="760,00"),
        # Bloco M
        _reg("M100", c1="101", c2="0", c3="7700,00", c4="1,65", c7=_v(round(127.05 + ajuste_servico, 2)), c8="0,00", c9="0,00",
             c11=_v(round(127.05 + ajuste_servico, 2)), c12="0", c13=_v(round(127.05 + ajuste_servico, 2)), c14="0,00"),
        _reg("M105", c1="03", c2="50", c3="1000,00", c4="0,00", c5="1000,00", c6="1000,00"),
        _reg("M105", c1="05", c2="50", c3="2000,00", c4="0,00", c5="2000,00", c6="2000,00"),
        _reg("M105", c1="02", c2="50", c3="500,00", c4="0,00", c5="500,00", c6="500,00"),
        _reg("M105", c1="04", c2="50", c3="3000,00", c4="0,00", c5="3000,00", c6="3000,00"),
        _reg("M105", c1="09", c2="53", c3="1200,00", c4="0,00", c5="1200,00", c6="1200,00"),
        _reg("M200", c1="300,00", c2=_v(round(127.05 + ajuste_servico, 2)), c3="0,00", c4=_v(round(172.95 - ajuste_servico, 2)), c5="10,00",
             c6="0,00", c7=_v(round(162.95 - ajuste_servico, 2)), c8="65,00", c9="0,00", c10="0,00", c11="65,00",
             c12=_v(round(227.95 - ajuste_servico, 2))),
        _reg("M210", c1="01", c2="20000,00", c3="20000,00", c6="20000,00", c7="1,65", c10="300,00", c15="300,00"),
        _reg("M210", c1="51", c2="10000,00", c3="10000,00", c6="10000,00", c7="0,65", c10="65,00", c15="65,00"),
        _reg("M500", c1="101", c2="0", c3="7700,00", c4="7,60", c7="585,20", c8="0,00", c9="0,00", c11="585,20", c12="0", c13="585,20", c14="0,00"),
        _reg("M505", c1="03", c2="50", c3="1000,00", c4="0,00", c5="1000,00", c6="1000,00"),
        _reg("M505", c1="05", c2="50", c3="2000,00", c4="0,00", c5="2000,00", c6="2000,00"),
        _reg("M505", c1="02", c2="50", c3="500,00", c4="0,00", c5="500,00", c6="500,00"),
        _reg("M505", c1="04", c2="50", c3="3000,00", c4="0,00", c5="3000,00", c6="3000,00"),
        _reg("M505", c1="09", c2="53", c3="1200,00", c4="0,00", c5="1200,00", c6="1200,00"),
        _reg("M600", c1="1500,00", c2="585,20", c3="0,00", c4="914,80", c5="50,00", c6="0,00", c7="864,80", c8="300,00", c9="0,00",
             c10="0,00", c11="300,00", c12="1164,80"),
        _reg("M610", c1="01", c2="20000,00", c3="20000,00", c6="20000,00", c7="7,60", c10="1500,00", c15="1500,00"),
        _reg("M610", c1="51", c2="10000,00", c3="10000,00", c6="10000,00", c7="3,00", c10="300,00", c15="300,00"),
        _reg("9999", c1="45"),
    ]
    txt = "\r\n".join(L) + "\r\nASSINATURA-BINARIA-QUE-DEVE-SER-IGNORADA|\r\n"
    arq = nome or f"PISCOFINS_{aaaa}{mm}01_{aaaa}{mm}28_{CNPJ_ENERGIA}_{'Original' if tipo == '0' else 'Retificadora'}_20261016100000_ab12.txt"
    return txt.encode("latin-1"), arq


COLUNAS_DET = ["CNPJ / CPF / CNO", "Número", "Espécie", "Data Emissão", "Razão Social", "Natureza", "Produto", "Descrição", "Valor Total",
               "Base Cálculo", "Código Situação Tributária PIS", "Alíquota PIS", "Valor PIS", "Código Situação Tributária COFINS",
               "Alíquota COFINS", "Valor COFINS", "Tipo Crédito PIS"]

LINHAS_DET = {  # categoria -> [(cnpj, numero, especie, data, razao, nat, prod, desc, total, base, pis, cofins)]
    "Serviços Tomados": [(FORN_A, "1001", "NFSE", "2026-07-10", "FORNECEDOR DE SERVICO A", "", "", "MANUTENCAO", 1000.0, 1000.0, 16.50, 76.00)],
    "Aluguel": [(FORN_B, "55", "NFSE", "2026-07-05", "IMOBILIARIA B", "", "", "ALUGUEL GALPAO", 2000.0, 2000.0, 33.00, 152.00)],
    "Combustível": [(FORN_C, "777", "NFE", "2026-07-12", "POSTO C", "", "IT1", "OLEO DIESEL S10", 500.0, 500.0, 8.25, 38.00)],
    "Demais Produtos": [(FORN_D, "9001", "FATURA", "2026-07-15", "CONCESSIONARIA D", "", "", "Energia elétrica", 3000.0, 3000.0, 49.50, 228.00)],
}

DEBITOS_DET = [  # (descricao, pis, cofins) -- mesma ordem/rotulos da aba "Débitos SPED (PIS-COFINS)"
    ("Crédito Não Cumulativo apurado no mês (M100/M500)", 127.05, 585.20),
    ("(+) Saldo de crédito de período anterior", 0.0, 0.0),
    ("(=) Crédito Não Cumulativo disponível no mês", 127.05, 585.20),
    ("(-) Crédito utilizado no mês para abater débito NC", 127.05, 585.20),
    ("(=) Saldo de crédito a transportar", 0.0, 0.0),
    ("Débito Não Cumulativo apurado no mês (M200/M600)", 300.0, 1500.0),
    ("(=) Débito NC não coberto por crédito", 172.95, 914.80),
    ("(-) Retenção na fonte sobre o Não Cumulativo", 10.0, 50.0),
    ("(=) Débito Não Cumulativo devido (após créditos e retenção)", 162.95, 864.80),
    ("Débito Cumulativo apurado no mês (M200/M600)", 65.0, 300.0),
    ("(-) Retenção na fonte sobre o Cumulativo", 0.0, 0.0),
    ("(=) Débito Cumulativo a recolher (M205/M605)", 65.0, 300.0),
    ("TOTAL PIS/COFINS a recolher no mês (M200/M600)", 227.95, 1164.80),
]


def detalhamento_sintetico(competencia: str = "07/2026", alterar: dict | None = None, com_debitos: bool = True) -> tuple[bytes, str]:
    """xlsx no layout do Detalhamento da contadora. `alterar` = {numero: (pis, cofins)} troca o valor de uma nota (para criar divergencia)."""
    alterar = alterar or {}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Resumo"
    ws["A1"] = f"Detalhamento de Débito e Crédito PIS e COFINS — Não-cumulativo — {competencia}"
    ws.append([])
    ws.append([])
    ws.append(["Categoria", "Qtde", "Valor Total", "Base", "PIS", "COFINS"])
    for cat, ls in LINHAS_DET.items():
        ws.append([cat, len(ls), sum(x[8] for x in ls), sum(x[9] for x in ls), sum(x[10] for x in ls), sum(x[11] for x in ls)])
    if com_debitos:
        wd = wb.create_sheet("Débitos SPED (PIS-COFINS)")
        wd["A1"] = f"Débitos SPED — {competencia}"
        wd.append([])
        wd.append([])
        wd.append(["Descrição", "PIS", "COFINS"])
        for d in DEBITOS_DET:
            wd.append(list(d))
    for cat, ls in LINHAS_DET.items():
        w = wb.create_sheet(cat)
        w["A1"] = f"{cat} — {competencia}"
        w.append([])
        w.append([])
        w.append(COLUNAS_DET)
        for (cnpj, num, esp, dt, raz, nat, prod, desc, tot, base, pis, cof) in ls:
            pis, cof = alterar.get(num, (pis, cof))
            w.append([cnpj, num, esp, dt, raz, nat, prod, desc, tot, base, "50", 1.65, pis, "50", 7.6, cof, "Mercado Interno"])
        w.append(["TOTAL", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    mm, aaaa = competencia.split("/")
    return buf.getvalue(), f"{mm}.{aaaa}-Detalhamento de Débito e Crédito PIS e COFINS-Não-cumulativo - ORIGINAL.xlsx"
