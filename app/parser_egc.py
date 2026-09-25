#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Leitor PDF Enermais V5
Suporta 3 formatos reais dos PDFs da contadora:

  FORMATO SPED  — tabela 4 cols: Descrição | Nota | Saldo Inicial | Saldo Final
  FORMATO TEXTO — texto corrido, valor no fim da linha
  FORMATO DUPLO — tabela 10 cols, ATIVO esquerda / PASSIVO direita

Fixes V5:
  Bug 1: Detecção DUPLO agora verifica primeiras 3 páginas (não só página 0)
  Bug 2: Nome da empresa — strip de traço/hífen inicial (ex: "- ENERMAIS...")
  Bug 3: 8 contas faltando adicionadas ao BP_TARGETS + DRE_TARGETS:
         APLICACOES FINANCEIRAS, CONTAS A PAGAR, IMPOSTOS E CONTRIBUICOES A RECOLHER,
         TRIBUTOS RETIDOS A RECOLHER, RECEITAS DIFERIDAS, OBRIGACOES COM O PESSOAL,
         OBRIGACOES PREVIDENCIARIAS, CAPITAL A INTEGRALIZAR, CARTAO CORPORATIVO (DRE)

Fixes V5.1 (FIX_20260721_1400) — Patrimonio Liquido negativo perdendo o sinal
e "TOTAL PATRIMONIO LIQUIDO" desaparecendo no formato DUPLO (comprovado com
os PDFs reais de Enermais Engenharia e Enermais Construtora SPED 2025):

  Bug 5 (sinal perdido, coluna partida): quando o pdfplumber extrai a tabela
  DUPLO, um valor negativo entre parenteses as vezes cai partido em duas
  colunas adjacentes, ex.: col7='(2.713.646,34' e col8=')'. O codigo antigo
  so lia col7 isolada, e br_to_float() so reconhece negativo se '(' e ')'
  estiverem na MESMA string -> sinal perdido. Fix: junta a coluna seguinte
  quando ela for so ')'.

  Bug 6 (sinal perdido, linha unica): quando uma linha inteira do PDF cai
  numa unica celula (col0 = "Patrimomio Liquido (2.513.646,34)"), a funcao
  split_mixed_line() usava BR_NUM_BARE (regex sem parenteses) para achar o
  numero, entao br_to_float() recebia so os digitos, sem o parenteses que
  indica negativo -> sinal perdido de novo. Fix: split_mixed_line() agora
  usa BR_NUM (regex COM parenteses opcionais), preservando o sinal. Efeito
  colateral positivo: corrige tambem o sinal de DEPRECIACAO ACUMULADA, que
  tinha o mesmo problema.

  Bug 7 (TOTAL PATRIMONIO LIQUIDO e CAPITAL SUBSCRITO somem inteiros): quando
  a linha "Patrimonio Liquido <valor>" (ou "Capital Subscrito <valor>")
  aparece SOZINHA numa celula (sem nada do ATIVO colado antes), split_mixed_line()
  retorna 1 par so, e o codigo antigo sempre tratava o 1o par de qualquer
  linha mista como ATIVO (`if len(pairs) >= 1: ativo_candidates.append(pairs[0])`),
  mesmo quando o conteudo era claramente do lado PASSIVO/PL. Isso jogava a
  linha pro grupo ATIVO, onde o filtro de contexto (context_ok) rejeita
  nomes de Patrimonio Liquido -> a linha era descartada por completo. Fix:
  a reclassificacao por nome pra pl_candidates agora roda tambem sobre os
  candidatos que caíram em ativo_candidates, nao só nos que caíram em
  passivo_candidates.

  Validado contra os PDFs reais: em todos os casos testados (Enermais
  Construtora, Enermais Energia, Enermais Engenharia, SMG SPED 2025 e SMG
  2023), apos o fix, CAPITAL SOCIAL + LUCROS/PREJUIZOS ACUMULADOS = TOTAL
  PATRIMONIO LIQUIDO exatamente, e TOTAL DO ATIVO = TOTAL DO PASSIVO. Antes
  do fix isso nao batia em 3 dos 6 casos. As saidas dos formatos SPED e
  TEXTO (todas as DRE, e o BP legado "2023") ficaram bit-a-bit idênticas —
  nenhuma regressao nos caminhos que já funcionavam.

Fixes V5.2 (FIX_20260721_1600) — diagnostico contra RELATÓRIO_MODELO.pdf
(relatorio feito a mao pela contadora para SMG Solucoes 2023) encontrou 2
bugs adicionais no formato DUPLO, ambos relacionados a contas do PASSIVO que
se repetem em Circulante E Nao Circulante:

  Bug 8 (conta faltando): "Despesas Pagas Antecipadamente" nao existia no
  BP_TARGETS -> a conta era descartada mesmo quando extraida corretamente do
  PDF. Fix: nova entrada em ATIVO CIRCULANTE.

  Bug 9 (2a ocorrencia de conta repetida e perdida): contas como
  "Instituicoes Financeiras", "Emprestimos", "Financiamentos", "Obrigacoes
  Tributarias", "Outras Obrigacoes" e "Contas a Pagar" podem aparecer TANTO
  no Passivo Circulante QUANTO no Passivo Nao Circulante (sao contas
  distintas, uma para cada prazo). O parser so guardava 1 valor por nome
  canonico (`found[nome]`), e o parse_duplo() marcava TODO o lado passivo
  com o mesmo bloco fixo ctx="PASSIVO" (sem distinguir Circulante de Nao
  Circulante) -> a 2a ocorrencia (normalmente a Nao Circulante) nunca era
  capturada, e o valor correto da VBA (que dependia dela) saia errado ou
  duplicado. Fix: parse_duplo() agora rastreia, linha a linha, se esta
  processando a secao "Circulante" ou "Nao Circulante" do passivo (usando os
  proprios marcadores "Circulante"/"Nao Circulante" que already aparecem na
  tabela), e o BP_TARGETS ganhou 6 entradas-espelho "<CONTA> NCIRC" com
  grupo "PASSIVO NAO CIRCULANTE", usando os mesmos aliases da conta
  Circulante. O nome de saida no TSV/planilha continua o mesmo nome "bonito"
  (sem o sufixo NCIRC) — a distincao Circulante/Nao Circulante fica só na
  coluna GRUPO, exatamente como um balanco de verdade mostra.

  Consequencia no VBA (corrigida em conjunto, ver Modulo_V2_Relatorios.bas e
  Modulo_Enermais_V2F.bas): como agora existem 2 linhas com o mesmo nome de
  conta e GRUPOs diferentes, BuscarBaseHistorica/V2BuscarPorPeriodo/
  BuscarContaBPResiliente ganharam um parametro opcional de filtro por GRUPO,
  para nao somar/misturar o valor Circulante com o Nao Circulante quando os
  dois tem o mesmo nome de conta.

Fix V5.3 (FIX_20260722_1500) — encontrado no "confere geral" da rodada com os
6 CNPJs reais (nao fazia parte do diagnostico original, achado testando
Enermais Construtora SPED 2025):

  Bug 10 (conta do PASSIVO some inteira quando o ATIVO acaba antes): quando a
  coluna ATIVO se esgota mas o PASSIVO continua (ex.: Construtora tem só 11
  linhas de Ativo e ~15 de Passivo), o pdfplumber as vezes devolve a linha
  seguinte com o texto do PASSIVO inteiro colado na coluna 0 (posicao do
  ATIVO) e todas as outras colunas vazias/None — ex.: linha bruta
  ['Outras Obrigações 2.605.982,12', None, None, ...]. O codigo antigo (regra
  "linha com 1 par so = ATIVO", usada desde o Bug 7) jogava isso pro
  ativo_candidates. Como "Outras Obrigacoes" nao e nome de conta do ATIVO,
  context_ok() rejeitava e a conta era descartada por completo — silenciosa,
  sem alerta no log. Confirmado no BP real da Construtora (SPED 2025):
  "Outras Obrigacoes" = R$ 2.605.982,12 (mutuo com a Enermais Energia)
  desaparecia 100% do TSV, mesmo com o TOTAL CIRCULANTE PASSIVO batendo
  certo (a soma inclui o valor, só a linha detalhada que sumia).
  Fix: quando a linha mista tem 1 par só, o nome e comparado contra os
  nomes conhecidos de conta do PASSIVO (_PASSIVO_NAMES/_PASSIVO_CIRC_ONLY_NAMES/
  _PASSIVO_NCIRC_ONLY_NAMES) antes de decidir o lado; se bater, vai pro
  passivo_candidates (respeitando o subbloco Circulante/Nao Circulante
  rastreado até aquele ponto), senao mantem o comportamento antigo (ATIVO).
  Mesma logica generalizada tambem cobre "Fornecedores" e qualquer outra
  conta do passivo que caia nesse padrao — nao é bug exclusivo de "Outras
  Obrigacoes".

Saída TSV para o VBA:
  enermais_bp.tsv   [GRUPO, CONTA, VALOR, ORIGEM]
  enermais_dre.tsv  [CONTA, VALOR, GRUPO, ORIGEM]
  enermais_log.tsv  [NIVEL, ARQUIVO, TIPO, MENSAGEM]
  enermais_meta.tsv [EMPRESA, CNPJ, PERIODO, ARQUIVO, TIPO, FORMATO]
"""

import argparse
import csv
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    print("ERRO: pdfplumber nao instalado. Rode: pip install pdfplumber", file=sys.stderr)
    sys.exit(1)


# ─────────────────────────────────────────────
#  UTILITÁRIOS
# ─────────────────────────────────────────────

def norm(s: str) -> str:
    """
    Normaliza string para comparação:
    - Remove acentos
    - Maiúsculas
    - Colapsa letras separadas por espaço: 'B A L A N C O' → 'BALANCO'
    - Remove espaços duplos
    """
    if s is None:
        return ""
    s = str(s).replace("\xa0", " ")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.upper()
    # Colapsar sequências de letras únicas separadas por espaço
    # Ex: "B A L A N C O" → "BALANCO", "A T I V O" → "ATIVO"
    s = re.sub(r"\b([A-Z])(?: ([A-Z]))+\b", lambda m: m.group(0).replace(" ", ""), s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def br_to_float(txt: str):
    """Converte valor BR ('R$ 1.234,56' ou '(1.234,56)') para float."""
    if not txt:
        return None
    t = str(txt).strip()
    neg = ("(" in t and ")" in t) or t.startswith("-")
    t = re.sub(r"[R$\s\(\)\-]", "", t)
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        val = float(t)
        return -val if neg else val
    except Exception:
        return None


def float_to_br(val) -> str:
    """Converte float para 'R$ 1.234,56' ou '(R$ 1.234,56)'."""
    if val is None:
        return ""
    neg = val < 0
    val = abs(float(val))
    inteiro, dec = f"{val:.2f}".split(".")
    parts = []
    while len(inteiro) > 3:
        parts.append(inteiro[-3:])
        inteiro = inteiro[:-3]
    parts.append(inteiro)
    formatted = ".".join(reversed(parts)) + "," + dec
    return f"(R$ {formatted})" if neg else f"R$ {formatted}"


# Regex número BR com separador de milhar
BR_NUM = re.compile(r"\(?\s*(?:R\$\s*)?\d{1,3}(?:\.\d{3})*,\d{2}\s*\)?")
BR_NUM_BARE = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")


def extract_last_value(line: str):
    """Extrai o último número BR de uma linha."""
    vals = BR_NUM.findall(line)
    return br_to_float(vals[-1]) if vals else None


def clean_desc(raw: str) -> str:
    """Remove valores, prefixos (-)(=) e lixo de uma string de descrição."""
    s = BR_NUM.sub("", raw).strip()
    s = re.sub(r"[\(\)R\$\s,\.]+$", "", s).strip()
    s = re.sub(r"^[\s\(\)\+\-=\/]+", "", s).strip()
    return s


# ─────────────────────────────────────────────
#  DICIONÁRIO DE CONTAS
# ─────────────────────────────────────────────

BP_TARGETS = [
    # Ordem importa: aliases mais específicos primeiro dentro de cada grupo
    # ── ATIVO ────────────────────────────────────────────────────────
    ("ATIVO CIRCULANTE",     "TOTAL CIRCULANTE ATIVO",          ["CIRCULANTE"]),
    ("ATIVO CIRCULANTE",     "DISPONIVEL",                      ["DISPONIVEL"]),
    ("ATIVO CIRCULANTE",     "DEPOSITOS BANCARIOS A VISTA",     ["DEPOSITOS BANCARIOS A VISTA","DEPOSITOS BANCARIOS"]),
    ("ATIVO CIRCULANTE",     "APLICACOES DE LIQUIDEZ IMEDIATA", ["APLICACOES DE LIQUIDEZ IMEDIATA","APLICACOES DE LIQUIDEZ"]),
    ("ATIVO CIRCULANTE",     "CLIENTES",                        ["CLIENTES"]),
    ("ATIVO CIRCULANTE",     "DUPLICATAS A RECEBER",            ["DUPLICATAS A RECEBER"]),
    ("ATIVO CIRCULANTE",     "OUTROS CREDITOS",                 ["OUTROS CREDITOS"]),
    ("ATIVO CIRCULANTE",     "MUTUO ENTRE EMPRESAS",            ["MUTUO ENTRE EMPRESAS"]),
    ("ATIVO CIRCULANTE",     "TITULOS A RECEBER",               ["TITULOS A RECEBER"]),
    ("ATIVO CIRCULANTE",     "TRIBUTOS A RECUPERAR",            ["TRIBUTOS A RECUPERAR"]),
    ("ATIVO CIRCULANTE",     "ADIANTAMENTOS A TERCEIROS",       ["ADIANTAMENTOS A TERCEIROS"]),
    # FIX_20260721_1600 (bug 8): conta ausente, identificada no diagnostico
    # contra o RELATORIO_MODELO.pdf (SMG 2023) — R$ 10.715,64 nao aparecia.
    ("ATIVO CIRCULANTE",     "DESPESAS PAGAS ANTECIPADAMENTE",  ["DESPESAS PAGAS ANTECIPADAMENTE","DESPESAS DO EXERCICIO SEGUINTE",
                                                                  "DESPESAS DO EXERCICIO A APROPRIAR","DESPESAS ANTECIPADAS"]),
    ("ATIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE ATIVO",      ["NAO CIRCULANTE"]),
    ("ATIVO NAO CIRCULANTE", "INVESTIMENTOS",                   ["INVESTIMENTOS"]),
    ("ATIVO NAO CIRCULANTE", "IMOBILIZADO",                     ["IMOBILIZADO"]),
    ("ATIVO NAO CIRCULANTE", "IMOVEIS",                         ["IMOVEIS"]),
    ("ATIVO NAO CIRCULANTE", "APLICACOES FINANCEIRAS",          ["APLICACOES FINANCEIRAS"]),
    ("ATIVO NAO CIRCULANTE", "BENS EM OPERACAO",                ["BENS EM OPERACAO"]),
    ("ATIVO NAO CIRCULANTE", "DEPRECIACAO ACUMULADA",           ["DEPRECIACAO","DEPRECIACAO/AMORTIZACAO","DEPRECIACAO/AMORTIZACAO/EXAUSTAO","AMORTIZACAO"]),
    ("TOTAL",                "TOTAL DO ATIVO",                  ["TOTAL DO ATIVO", "ATIVO"]),
    # ── PASSIVO ──────────────────────────────────────────────────────
    ("PASSIVO CIRCULANTE",   "TOTAL CIRCULANTE PASSIVO",        ["CIRCULANTE"]),
    ("PASSIVO CIRCULANTE",   "INSTITUICOES FINANCEIRAS",        ["INSTITUICOES FINANCEIRAS"]),
    ("PASSIVO CIRCULANTE",   "EMPRESTIMOS",                     ["EMPRESTIMOS"]),
    ("PASSIVO CIRCULANTE",   "FINANCIAMENTOS",                  ["FINANCIAMENTOS","FINANCIAMENTOS SISTEMA FINANCEIRO NACIONAL"]),
    ("PASSIVO CIRCULANTE",   "FORNECEDORES",                    ["FORNECEDORES"]),
    ("PASSIVO CIRCULANTE",   "OBRIGACOES TRIBUTARIAS",          ["OBRIGACOES TRIBUTARIAS"]),
    ("PASSIVO CIRCULANTE",   "IMPOSTOS E CONTRIBUICOES A RECOLHER", ["IMPOSTOS E CONTRIBUICOES A RECOLHER",
                                                                      "IMPOSTOS E CONTRIBUICOES",
                                                                      "CONTRIBUICOES A RECOLHER"]),
    ("PASSIVO CIRCULANTE",   "TRIBUTOS RETIDOS A RECOLHER",    ["TRIBUTOS RETIDOS A RECOLHER",
                                                                 "TRIBUTOS RETIDOS"]),
    ("PASSIVO CIRCULANTE",   "OBRIGACOES TRABALHISTAS",        ["OBRIGACOES TRABALHISTAS",
                                                                 "OBRIGACOES TRABALHISTAS E PRIVIDENCIARIAS"]),
    ("PASSIVO CIRCULANTE",   "OBRIGACOES COM O PESSOAL",       ["OBRIGACOES COM O PESSOAL",
                                                                 "COM O PESSOAL"]),
    ("PASSIVO CIRCULANTE",   "OBRIGACOES PREVIDENCIARIAS",     ["OBRIGACOES PREVIDENCIARIAS",
                                                                 "PRIVIDENCIARIAS",
                                                                 "OBRIGACOES PRIVIDENCIARIAS"]),
    ("PASSIVO CIRCULANTE",   "CONTAS A PAGAR",                 ["CONTAS A PAGAR"]),
    ("PASSIVO CIRCULANTE",   "OUTRAS OBRIGACOES",              ["OUTRAS OBRIGACOES"]),
    ("PASSIVO CIRCULANTE",   "ADIANTAMENTOS DE CLIENTES",      ["ADIANTAMENTOS DE CLIENTES","ADIANTAMENTO DE CLIENTES"]),
    ("PASSIVO NAO CIRCULANTE","TOTAL NAO CIRCULANTE PASSIVO",  ["NAO CIRCULANTE"]),
    ("PASSIVO NAO CIRCULANTE","OBRIGACOES A LONGO PRAZO",      ["OBRIGACOES A LONGO PRAZO"]),
    ("PASSIVO NAO CIRCULANTE","RECEITAS DIFERIDAS",            ["RECEITAS DIFERIDAS"]),
    # FIX_20260721_1600 (bug 9): contraparte "Nao Circulante" das mesmas contas
    # que ja existem em PASSIVO CIRCULANTE acima. Mesmos aliases (o texto no
    # PDF é idêntico nos dois casos — o que muda é em qual bloco da tabela a
    # linha aparece). O sufixo " NCIRC" existe só internamente (para o
    # dict `found` nao sobrescrever/descartar a 2a ocorrencia); na saida
    # (TSV/planilha) o nome volta a ser o mesmo da conta Circulante — quem
    # distingue e a coluna GRUPO. Ver parse_duplo() e build_output().
    ("PASSIVO NAO CIRCULANTE","INSTITUICOES FINANCEIRAS NCIRC", ["INSTITUICOES FINANCEIRAS"]),
    ("PASSIVO NAO CIRCULANTE","EMPRESTIMOS NCIRC",              ["EMPRESTIMOS"]),
    ("PASSIVO NAO CIRCULANTE","FINANCIAMENTOS NCIRC",           ["FINANCIAMENTOS","FINANCIAMENTOS SISTEMA FINANCEIRO NACIONAL"]),
    ("PASSIVO NAO CIRCULANTE","OBRIGACOES TRIBUTARIAS NCIRC",   ["OBRIGACOES TRIBUTARIAS"]),
    ("PASSIVO NAO CIRCULANTE","OUTRAS OBRIGACOES NCIRC",        ["OUTRAS OBRIGACOES"]),
    ("PASSIVO NAO CIRCULANTE","CONTAS A PAGAR NCIRC",           ["CONTAS A PAGAR"]),
    ("PATRIMONIO LIQUIDO",   "TOTAL PATRIMONIO LIQUIDO",       ["PATRIMONIO LIQUIDO","PATRIMOMIO LIQUIDO"]),
    ("PATRIMONIO LIQUIDO",   "CAPITAL SOCIAL",                 ["CAPITAL SOCIAL"]),
    ("PATRIMONIO LIQUIDO",   "CAPITAL SUBSCRITO",              ["CAPITAL SUBSCRITO"]),
    ("PATRIMONIO LIQUIDO",   "CAPITAL A INTEGRALIZAR",         ["CAPITAL A INTEGRALIZAR","A INTEGRALIZAR"]),
    ("PATRIMONIO LIQUIDO",   "LUCROS/PREJUIZOS ACUMULADOS",    ["LUCROS/PREJUIZOS ACUMULADOS","LUCROS ACUMULADOS","PREJUIZOS ACUMULADOS"]),
    ("TOTAL",                "TOTAL DO PASSIVO",               ["TOTAL DO PASSIVO","TOTAL DO PASSIVO E PATRIMONIO", "PASSIVO"]),
]

DRE_TARGETS = [
    ("RECEITAS",  "RECEITA OPERACIONAL BRUTA",       ["RECEITA OPERACIONAL BRUTA"]),
    ("RECEITAS",  "RECEITAS OPERACIONAIS DIVERSAS",  ["RECEITAS OPERACIONAIS DIVERSAS"]),
    ("DEDUCOES",  "DEDUCOES DA RECEITA BRUTA",       ["DEDUCOES DA RECEITA BRUTA"]),
    ("RESULTADO", "RECEITA OPERACIONAL LIQUIDA",     ["RECEITA OPERACIONAL LIQUIDA"]),
    ("CUSTOS",    "CUSTO DOS PRODUTOS/SERVICOS",     ["CUSTO DOS PRODUTOS","CUSTO DOS SERVICOS",
                                                      "CUSTO DOS PRODUTOS/MERCADORIAS"]),
    ("RESULTADO", "LUCRO BRUTO",                     ["LUCRO BRUTO"]),
    ("DESPESAS",  "DESPESAS OPERACIONAIS",           ["DESPESAS OPERACIONAIS"]),
    ("DESPESAS",  "ADMINISTRATIVAS",                 ["ADMINISTRATIVAS"]),
    ("DESPESAS",  "DESPESAS FINANCEIRAS",            ["DESPESAS FINANCEIRAS"]),
    ("RECEITAS",  "RECEITAS FINANCEIRAS",            ["RECEITAS FINANCEIRAS"]),
    ("DESPESAS",  "DESPESAS TRIBUTARIAS",            ["DESPESAS TRIBUTARIAS"]),
    ("DESPESAS",  "OUTRAS DESPESAS OPERACIONAIS",    ["OUTRAS DESPESAS OPERACIONAIS","OUTRAS DESPESAS",
                                                      "CARTAO CORPORATIVO"]),
    ("RESULTADO", "LUCRO OPERACIONAL LIQUIDO",       ["LUCRO OPERACIONAL LIQUIDO",
                                                      "PREJUIZO OPERACIONAL LIQUIDO"]),
    # FIX_20260925 (pedido do Rafael): CSLL/IRPJ do periodo, quando o
    # regime e' lucro real (aparece como 2 linhas de totalizador entre
    # LUCRO OPERACIONAL LIQUIDO e LUCRO LIQUIDO DO EXERCICIO -- conferido
    # no PDF real "Enermais Energia - DRE - 05.2026", onde
    # PROVISAO PARA CONTRIBUICAO SOCIAL = -62.900,81 e
    # PROVISAO PARA IMPOSTO DE RENDA = -168.724,46, soma R$ 231.625,27,
    # o mesmo numero ja usado manualmente no relatorio Modelo A validado
    # em 00-handoff.md secao 61). Em lucro presumido (2023-2025 no grupo,
    # ver secao 61) essas 2 linhas NAO existem -- IRPJ/CSLL fica dentro
    # das DEDUCOES DA RECEITA BRUTA, sem linha propria -- e' esperado que
    # os 2 campos abaixo fiquem ausentes (found) nesses periodos, nao um
    # bug. Cada alias e' o texto EXATO do totalizador (nao dos itens
    # "Csll"/"Irpj" individuais, mesmo padrao ja usado p/ "ADMINISTRATIVAS"
    # acima -- grupo com 1 filho so', total = filho).
    ("DESPESAS",  "PROVISAO CSLL",                   ["PROVISAO PARA CONTRIBUICAO SOCIAL"]),
    ("DESPESAS",  "PROVISAO IRPJ",                   ["PROVISAO PARA IMPOSTO DE RENDA"]),
    ("RESULTADO", "LUCRO LIQUIDO DO EXERCICIO",      ["LUCRO LIQUIDO DO EXERCICIO",
                                                      "LUCRO/PREJUIZO LIQUIDO",
                                                      "PREJUIZO LIQUIDO DO EXERCICIO",
                                                      "RESULTADO DO EXERCICIO",
                                                      "LUCRO LIQUIDO"]),
    # FIX_20260924 (bug real achado auditando os 20 DRE unicos da pasta
    # do vault): "RESULTADO ANTES DA CS E IR" tinha alias pra
    # LUCRO LIQUIDO DO EXERCICIO acima -- certo quando o DRE nao
    # provisiona CSLL/IRPJ como linha propria (a maioria dos casos, os 2
    # valores saem iguais mesmo), ERRADO quando provisiona (Enermais
    # Energia 05/2026 e Enermais Construtora 2T2026 na amostra: R$ 231k e
    # R$ 204k de diferenca real). Como o "found" so' guarda o 1o match e
    # "Resultado Antes da CS e IR" sempre aparece ANTES de "(=) Lucro
    # Liquido do Exercicio" no layout, o alias sequestrava o valor errado
    # (pre-CSLL/IR) sempre que os 2 existiam separados. Alias removido:
    # os 4 aliases que sobraram acima ja cobrem 100% das 20 amostras.
    #
    # DEPRECIACOES/AMORTIZACOES NAO entram no mecanismo normal de
    # DRE_TARGETS (aliases vazios de proposito, ver process_candidates) --
    # pode haver MAIS DE 1 linha por periodo (ex.: "Depreciacoes" +
    # "Depreciacao de Veiculos" separadas, achado real em amostra real,
    # 2024.12 Enermais Energia) e o mecanismo de 1o-match-vence perderia a
    # 2a linha. extrair_deprec_amortiz() soma TODAS as linhas do periodo.
    ("DESPESAS",  "DEPRECIACOES",                    []),
    ("DESPESAS",  "AMORTIZACOES",                    []),
]

# Contas exclusivas do ATIVO (contexto)
_ATIVO_NAMES = {
    "TOTAL CIRCULANTE ATIVO","DISPONIVEL","DEPOSITOS BANCARIOS A VISTA",
    "APLICACOES DE LIQUIDEZ IMEDIATA","CLIENTES","DUPLICATAS A RECEBER",
    "OUTROS CREDITOS","MUTUO ENTRE EMPRESAS","TITULOS A RECEBER",
    "TRIBUTOS A RECUPERAR","ADIANTAMENTOS A TERCEIROS","DESPESAS PAGAS ANTECIPADAMENTE",
    "TOTAL NAO CIRCULANTE ATIVO","INVESTIMENTOS","IMOBILIZADO","IMOVEIS",
    "APLICACOES FINANCEIRAS","BENS EM OPERACAO","DEPRECIACAO ACUMULADA","TOTAL DO ATIVO",
}
_PASSIVO_NAMES = {
    "TOTAL CIRCULANTE PASSIVO","INSTITUICOES FINANCEIRAS","EMPRESTIMOS","FINANCIAMENTOS",
    "FORNECEDORES","OBRIGACOES TRIBUTARIAS","IMPOSTOS E CONTRIBUICOES A RECOLHER",
    "TRIBUTOS RETIDOS A RECOLHER","OBRIGACOES TRABALHISTAS","OBRIGACOES COM O PESSOAL",
    "OBRIGACOES PREVIDENCIARIAS","CONTAS A PAGAR","OUTRAS OBRIGACOES",
    "ADIANTAMENTOS DE CLIENTES","TOTAL NAO CIRCULANTE PASSIVO","OBRIGACOES A LONGO PRAZO",
    "RECEITAS DIFERIDAS","TOTAL DO PASSIVO",
}
# FIX_20260721_1600 (bug 9): contas que só podem vir do bloco PASSIVO
# CIRCULANTE (têm uma entrada-espelho " NCIRC" dedicada para o lado Nao
# Circulante — ver BP_TARGETS acima e parse_duplo()).
_PASSIVO_CIRC_ONLY_NAMES = {
    "INSTITUICOES FINANCEIRAS", "EMPRESTIMOS", "FINANCIAMENTOS",
    "OBRIGACOES TRIBUTARIAS", "OUTRAS OBRIGACOES", "CONTAS A PAGAR",
}
# Contraparte: só podem vir do bloco PASSIVO NAO CIRCULANTE.
_PASSIVO_NCIRC_ONLY_NAMES = {
    "INSTITUICOES FINANCEIRAS NCIRC", "EMPRESTIMOS NCIRC", "FINANCIAMENTOS NCIRC",
    "OBRIGACOES TRIBUTARIAS NCIRC", "OUTRAS OBRIGACOES NCIRC", "CONTAS A PAGAR NCIRC",
}
_PL_NAMES = {
    "TOTAL PATRIMONIO LIQUIDO","CAPITAL SOCIAL","CAPITAL SUBSCRITO",
    "CAPITAL A INTEGRALIZAR","LUCROS/PREJUIZOS ACUMULADOS",
}


# Mapeamento CNPJ → nome correto da empresa
CNPJ_EMPRESA = {
    "47.040.664/0001-48": "Enermais Energia Ltda",
    "18.387.666/0001-00": "SMG Solucoes Ltda",
    "50.337.899/0001-00": "Enermais Engenharia Ltda",
    "51.671.106/0001-58": "Enermais Renovaveis Ltda",
    "55.244.465/0001-80": "Enermais Construtora Ltda",
    "60.353.219/0001-04": "Enermais Solucoes Ltda",
}


def match_target(desc_n: str, aliases: list) -> bool:
    for alias in aliases:
        a = norm(alias)
        if desc_n == a:
            return True
        if len(a) >= 8 and desc_n.startswith(a):
            return True
    return False


def context_ok(nome: str, context: str, tipo: str) -> bool:
    if tipo != "BP":
        return True
    if nome in _ATIVO_NAMES:
        return context == "ATIVO"
    # FIX_20260721_1600 (bug 9): checar as contas com par Circulante/Nao
    # Circulante ANTES da checagem generica de _PASSIVO_NAMES, senao a versao
    # Circulante aceitaria qualquer contexto passivo (incluindo Nao
    # Circulante) e a 2a ocorrencia nunca chegaria na entrada NCIRC.
    if nome in _PASSIVO_CIRC_ONLY_NAMES:
        return context in ("PASSIVO_CIRC", "PASSIVO")
    if nome in _PASSIVO_NCIRC_ONLY_NAMES:
        return context in ("PASSIVO_NCIRC", "PASSIVO")
    if nome in _PASSIVO_NAMES:
        return context in ("PASSIVO_CIRC", "PASSIVO_NCIRC", "PASSIVO", "PATRIMONIO")
    if nome in _PL_NAMES:
        return context == "PATRIMONIO"
    return True


def update_context(desc_n: str, current: str) -> str:
    """Atualiza contexto ATIVO/PASSIVO/PATRIMONIO com base na descrição."""
    if desc_n in {"ATIVO", "TOTALATIVO", "TOTAL DO ATIVO"}:
        return "ATIVO"
    if desc_n in {"PASSIVO", "TOTALPASSIVO"}:
        return "PASSIVO"
    if ("PATRIMONIO" in desc_n or "PATRIMOMIO" in desc_n) and len(desc_n) < 30:
        return "PATRIMONIO"
    return current


def extrair_deprec_amortiz(candidates: list) -> dict:
    """
    Soma TODAS as linhas de depreciacao/amortizacao do periodo -- pode
    haver mais de 1 sublinha no mesmo DRE (achado real em amostra:
    2024.12 Enermais Energia tem "Depreciacoes" (96.965,67) E
    "Depreciacao de Veiculos" (167.490,27) como linhas SEPARADAS dentro
    de Administrativas -- as 2 sao despesa real do periodo, somar so' a
    1a subestimaria o total em ~63% nesse caso). Por isso NAO usa o
    mecanismo normal de DRE_TARGETS (found[nome] = 1o match, ver
    process_candidates) -- ali perderia a 2a linha em diante.

    candidates: mesma lista (desc, valor, bloco) que process_candidates
    recebe -- aqui sempre so' candidatos de DRE (BP nunca passa por
    process_candidates com tipo=="DRE", ver parse_sped/parse_texto/
    parse_duplo), entao nunca cruza com "Depreciacao Acumulada" do BP
    (chave diferente, contexto diferente, sem risco de mistura).

    Retorna só as chaves que de fato apareceram no período (Rafael pediu
    'nunca inventa número' -- sem depreciação real no período, não entra
    linha nenhuma, igual a qualquer outro alvo ausente de DRE_TARGETS).
    """
    total = {"DEPRECIACOES": 0.0, "AMORTIZACOES": 0.0}
    achou = {"DEPRECIACOES": False, "AMORTIZACOES": False}
    for desc_raw, val, _bloco in candidates:
        desc_n = norm(desc_raw)
        if "DEPRECIA" in desc_n:
            total["DEPRECIACOES"] += val
            achou["DEPRECIACOES"] = True
        elif "AMORTIZ" in desc_n:
            total["AMORTIZACOES"] += val
            achou["AMORTIZACOES"] = True
    return {k: v for k, v in total.items() if achou[k]}


def calcular_derivados_dre(found: dict) -> dict:
    """
    Para DRE formato SPED: calcula linhas de resultado que o PDF omite.
    Só calcula se a conta ainda não foi encontrada diretamente.
    """
    # RECEITA OPERACIONAL LIQUIDA = RECEITA BRUTA + DEDUCOES
    if "RECEITA OPERACIONAL LIQUIDA" not in found:
        rob = found.get("RECEITA OPERACIONAL BRUTA")
        ded = found.get("DEDUCOES DA RECEITA BRUTA")
        if rob is not None and ded is not None:
            found["RECEITA OPERACIONAL LIQUIDA"] = rob + ded

    # LUCRO BRUTO = RECEITA LIQUIDA + CUSTO
    if "LUCRO BRUTO" not in found:
        rol = found.get("RECEITA OPERACIONAL LIQUIDA")
        cst = found.get("CUSTO DOS PRODUTOS/SERVICOS")
        if rol is not None and cst is not None:
            found["LUCRO BRUTO"] = rol + cst

    # LUCRO OPERACIONAL LIQUIDO = LUCRO BRUTO + DESPESAS OPERACIONAIS
    if "LUCRO OPERACIONAL LIQUIDO" not in found:
        lb  = found.get("LUCRO BRUTO")
        dsp = found.get("DESPESAS OPERACIONAIS")
        if lb is not None and dsp is not None:
            found["LUCRO OPERACIONAL LIQUIDO"] = lb + dsp

    return found


def build_output(found: dict, targets: list, tipo: str, origem: str) -> list:
    rows = []
    for (grupo, nome, aliases) in targets:
        if nome in found:
            v = found[nome]
            # FIX_20260721_1600 (bug 9): as entradas-espelho "<CONTA> NCIRC"
            # existem só para o dict `found` nao descartar a 2a ocorrencia;
            # na saida (TSV/planilha) o nome volta a ser o nome "normal" da
            # conta — quem diferencia Circulante de Nao Circulante e a
            # coluna GRUPO, igual num balanco de verdade.
            nome_saida = nome[:-6].strip() if nome.endswith(" NCIRC") else nome
            if tipo == "BP":
                rows.append([grupo, nome_saida, float_to_br(v), origem])
            else:
                rows.append([nome_saida, float_to_br(v), grupo, origem])
    return rows


def process_candidates(candidates, targets, tipo, origem):
    """
    candidates: lista de (desc_str, valor_float, bloco)
    bloco: 'ATIVO' | 'PASSIVO' | 'PASSIVO_CIRC' | 'PASSIVO_NCIRC' | 'PATRIMONIO' | 'QUALQUER'
    """
    found = {}
    context = "ATIVO"

    for desc_raw, val, bloco in candidates:
        desc_n = norm(desc_raw)
        if not desc_n:
            continue

        # Atualizar contexto
        context = update_context(desc_n, context)

        # Substituir contexto pelo bloco quando informado explicitamente
        # FIX_20260721_1600: incluir PASSIVO_CIRC/PASSIVO_NCIRC como blocos
        # explicitos validos (antes só ATIVO/PASSIVO/PATRIMONIO eram
        # reconhecidos aqui, e o bloco granular do parse_duplo() acabava
        # sendo ignorado e substituido pelo `context` generico).
        if bloco in ("ATIVO", "PASSIVO", "PATRIMONIO", "PASSIVO_CIRC", "PASSIVO_NCIRC"):
            ctx = bloco
        else:
            ctx = context

        for (grupo, nome, aliases) in targets:
            if match_target(desc_n, aliases) and context_ok(nome, ctx, tipo):
                if nome not in found:
                    v_final = val
                    # FIX_20260730 (bug de sinal): no layout SPED usado pelo
                    # grupo, a linha "(=) Prejuizo Operacional Liquido" as
                    # vezes vem SEM parenteses/sinal no texto extraido (ao
                    # contrario de "Prejuizo Liquido do Exercicio", que vem
                    # sempre parenteizada) — confirmado em Engenharia,
                    # Renovaveis, Construtora e Solucoes. Isso fazia
                    # br_to_float() ler o valor como positivo e o parser
                    # gravar um "Lucro Operacional Liquido" grande e
                    # positivo quando na verdade era um Prejuizo (negativo),
                    # divergindo do Patrimonio Liquido e do Lucro Liquido do
                    # Exercicio (que saiam corretos). "LUCRO OPERACIONAL
                    # LIQUIDO" so tem "PREJUIZO OPERACIONAL LIQUIDO" como
                    # alias (ver DRE_TARGETS) — se foi essa variante que
                    # bateu, o valor SEMPRE representa perda: forcar
                    # negativo independente de como veio o sinal no PDF.
                    if nome == "LUCRO OPERACIONAL LIQUIDO" and "PREJUIZO" in desc_n:
                        v_final = -abs(val)
                    found[nome] = v_final
                break

    if tipo == "DRE":
        found = calcular_derivados_dre(found)
        found.update(extrair_deprec_amortiz(candidates))
    return build_output(found, targets, tipo, origem)


# ─────────────────────────────────────────────
#  DETECÇÃO DE FORMATO E METADADOS
# ─────────────────────────────────────────────

def detect_format_and_type(pdf_path: Path):
    with pdfplumber.open(str(pdf_path)) as pdf:
        pg0 = pdf.pages[0]
        texto = pg0.extract_text(x_tolerance=2, y_tolerance=3) or ""
        # Bug 1: checar primeiras 3 páginas para detecção DUPLO
        tables = pg0.extract_tables()
        if not tables:
            for pg in pdf.pages[1:3]:
                tables = pg.extract_tables()
                if tables:
                    break

    n = norm(texto)

    # Tipo — Bug 4b: adicionar "DRE" como keyword (cobre "DRE CONSOLIDADO")
    if any(k in n for k in ["DEMONSTRACAO DO RESULTADO", "RESULTADO DO EXERCICIO",
                             "DEMONSTRATIVO DO RESULTADO", "DEMONSTRATIVO RESULTADO"]):
        tipo = "DRE"
    elif re.search(r"\bDRE\b", n):
        tipo = "DRE"
    elif any(k in n for k in ["BALANCO PATRIMONIAL", "BALANCOPATRIMONIAL",
                               "BALANCO PATRIMONIAL CONSOLIDADO"]):
        tipo = "BP"
    else:
        tipo = "DESCONHECIDO"

    # Formato
    fmt = "TEXTO"
    if tables:
        first = tables[0]
        if len(first) > 0:
            ncols = max((len(r) for r in first if r), default=0)
            if ncols == 4:
                first_row_n = norm(str(first[0]))
                if "SALDO" in first_row_n:
                    fmt = "SPED"
                elif "ATIVO" in first_row_n or "PASSIVO" in first_row_n:
                    fmt = "DUPLO"   # Bug 4d: DUPLO de 4 colunas (Consolidado)
            elif ncols >= 8:
                fmt = "DUPLO"

    # Metadados
    empresa = "ENERMAIS ENERGIA LTDA"
    cnpj = ""
    periodo = ""

    m = re.search(r"CNPJ[:\s]+(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})", texto, re.I)
    if m:
        cnpj = m.group(1)

    # Bug 4a: usar CNPJ_EMPRESA para nome correto (cobre SMG e outros não-ENERMAIS)
    if cnpj and cnpj in CNPJ_EMPRESA:
        empresa = CNPJ_EMPRESA[cnpj]
    else:
        for line in texto.splitlines()[:15]:
            if "ENERMAIS" in line.upper() and len(line.strip()) < 80:
                candidate = re.sub(r"^\d+\s+", "", line).strip()
                candidate = re.sub(r"^Entidade:\s*", "", candidate, flags=re.I).strip()
                candidate = re.sub(r"^[\s\-–—]+", "", candidate).strip()  # Bug 2
                candidate = re.sub(r"\s+\d{2}/\d{2}/\d{4}.*$", "", candidate).strip()
                if "ENERMAIS" in candidate.upper():
                    empresa = candidate
                    break

    # Bug 4c: regex período aceita "à" além de "a"
    # periodo_inicio (24/09/2026, Rafael pediu granularidade real pro
    # seletor de mes/trimestre/semestre/ano): o PDF ja' declara "Periodo:
    # DD/MM/AAAA a DD/MM/AAAA" -- a data de INICIO sempre foi lida (grupo
    # 1 do regex) mas descartada, so' o fim (grupo 2) virava `periodo`.
    # Guardando as 2 agora pra calcular_granularidade() poder dizer se o
    # PDF cobre 1 mes, 1 trimestre, 1 semestre ou o ano inteiro -- sem
    # isso nao da pra saber, pra nenhum periodo ja' importado, se "06/2026"
    # veio de um fechamento so' de junho ou do 2o trimestre inteiro (risco
    # real que o Rafael apontou: nao inventar granularidade por delta,
    # so' usar o que o proprio PDF declara).
    periodo_inicio = ""
    m = re.search(r"Per[ií]odo[:\s]+(\d{2}/\d{2}/\d{4})\s*(?:[àa]\s*(\d{2}/\d{2}/\d{4}))?",
                  texto, re.I)
    if m:
        periodo = m.group(2) or m.group(1)
        periodo_inicio = m.group(1) if m.group(2) else ""
    else:
        m = re.search(r"(\d{2}/\d{2}/\d{4})\s*(?:[àa]\s*(\d{2}/\d{2}/\d{4}))?", texto)
        if m:
            periodo = m.group(2) or m.group(1)
            periodo_inicio = m.group(1) if m.group(2) else ""

    return fmt, tipo, empresa, cnpj, periodo, periodo_inicio


# ─────────────────────────────────────────────
#  PARSER A: SPED — posição X para casar desc+valor
# ─────────────────────────────────────────────

def parse_sped(pdf_path: Path, tipo: str, origem: str) -> list:
    targets = BP_TARGETS if tipo == "BP" else DRE_TARGETS
    candidates = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=3, y_tolerance=3)
            lines_by_y = {}
            for w in words:
                bucket = round(w['top'] / 8) * 8
                lines_by_y.setdefault(bucket, []).append(w)

            for y in sorted(lines_by_y.keys()):
                row_words = sorted(lines_by_y[y], key=lambda w: w['x0'])
                desc_words = [w for w in row_words if w['x0'] < 350]
                val_words  = [w for w in row_words if w['x0'] >= 380]

                if not desc_words or not val_words:
                    continue

                desc = " ".join(w['text'] for w in desc_words)
                desc_n = norm(desc)

                # Ignorar cabeçalhos
                if desc_n in {"DESCRICAO","NOTA","SALDO INICIAL","SALDO FINAL",
                               "SALDO ANTERIOR","SALDO ATUAL"}:
                    continue

                # Pegar o número mais à direita (Saldo Final / Saldo Atual)
                val_str = " ".join(w['text'] for w in val_words)
                nums = BR_NUM_BARE.findall(val_str)
                if not nums:
                    continue
                val = br_to_float(nums[-1])
                if val is None:
                    continue

                # Determinar bloco pelo contexto textual
                bloco = "QUALQUER"
                if tipo == "BP":
                    if desc_n in {"ATIVO"}:
                        bloco = "ATIVO"
                    elif desc_n in {"PASSIVO"}:
                        bloco = "PASSIVO"

                candidates.append((desc, val, bloco))

    return process_candidates(candidates, targets, tipo, origem)


# ─────────────────────────────────────────────
#  PARSER B: TEXTO SIMPLES
# ─────────────────────────────────────────────

def parse_texto(pdf_path: Path, tipo: str, origem: str) -> list:
    all_lines = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            all_lines.extend(txt.splitlines())

    targets = BP_TARGETS if tipo == "BP" else DRE_TARGETS
    candidates = []
    context = "ATIVO"

    for line in all_lines:
        line = line.strip()
        if not line:
            continue

        line_n = norm(line)

        # Detectar bloco para BP
        bloco = "QUALQUER"
        if tipo == "BP":
            if line_n in {"ATIVO"} or re.match(r"^ATIVO\b", line_n):
                context = "ATIVO"
                bloco = "ATIVO"
            elif line_n in {"PASSIVO"} or re.match(r"^PASSIVO\b", line_n):
                context = "PASSIVO"
                bloco = "PASSIVO"
            elif re.match(r"^PATRIMO[NM]IO LIQUIDO", line_n):
                context = "PATRIMONIO"
                bloco = "PATRIMONIO"
            else:
                bloco = context

        val = extract_last_value(line)
        if val is None:
            continue

        desc_raw = clean_desc(line)
        if not desc_raw:
            continue

        candidates.append((desc_raw, val, bloco))

    return process_candidates(candidates, targets, tipo, origem)


# ─────────────────────────────────────────────
#  PARSER C: DUPLO (2 colunas lado a lado)
#  Regra chave: c0/c2 = SEMPRE ATIVO, c5/c7 = SEMPRE PASSIVO/PL
# ─────────────────────────────────────────────

def split_mixed_line(s: str) -> list:
    """
    'CONTA_A 1.234,56 CONTA_B 7.890,12' → [(desc_a, val_a), (desc_b, val_b)]
    FIX_20260721_1400 (bug 6): usa BR_NUM (parenteses opcionais) em vez de
    BR_NUM_BARE, senao o sinal negativo de valores como '(2.513.646,34)'
    e perdido -- BR_NUM_BARE so capturava os digitos, sem o parenteses que
    br_to_float() precisa ver pra reconhecer o valor como negativo.
    """
    pairs = []
    matches = list(BR_NUM.finditer(s))
    prev_end = 0
    for m in matches:
        desc_raw = s[prev_end:m.start()].strip()
        val_raw  = m.group()
        desc_raw = re.sub(r"[\(\)R\$\s]+$", "", desc_raw).strip()
        if desc_raw:
            pairs.append((clean_desc(desc_raw), br_to_float(val_raw)))
        prev_end = m.end()
    return [(d, v) for d, v in pairs if v is not None]


def _atualizar_subbloco_passivo(desc_n: str, atual: str) -> str:
    """
    FIX_20260721_1600 (bug 9): identifica os marcadores "Circulante"/"Nao
    Circulante" que aparecem como linha propria na coluna do PASSIVO (o
    mesmo texto que já é usado como alias de "TOTAL CIRCULANTE PASSIVO" /
    "TOTAL NAO CIRCULANTE PASSIVO"), pra saber se as contas seguintes
    pertencem ao bloco Circulante ou Nao Circulante do passivo.
    """
    if desc_n == "NAO CIRCULANTE":
        return "NAO CIRCULANTE"
    if desc_n == "CIRCULANTE":
        return "CIRCULANTE"
    return atual


def parse_duplo(pdf_path: Path, tipo: str, origem: str) -> list:
    """
    Estratégia: sempre trata c0/c2 como bloco ATIVO e c5/c7 como bloco PASSIVO.
    Para linhas misturadas em c0 (c2 vazio), usa split_mixed_line e distribui
    por posição: primeiro par = ATIVO, segundo par = PASSIVO.

    FIX_20260721_1600 (bug 9): o lado PASSIVO agora rastreia, linha a linha,
    se esta no sub-bloco "CIRCULANTE" ou "NAO CIRCULANTE" (passivo_subbloco),
    pra distinguir contas que se repetem nos dois blocos (Instituicoes
    Financeiras, Emprestimos, Financiamentos, Obrigacoes Tributarias, Outras
    Obrigacoes, Contas a Pagar). Antes disso, TODO o passivo era marcado com
    o mesmo ctx="PASSIVO" fixo, e a 2a ocorrencia dessas contas era perdida.
    """
    targets = BP_TARGETS if tipo == "BP" else DRE_TARGETS

    # Para BP: candidatos separados por bloco
    ativo_candidates = []
    passivo_candidates = []          # agora tuplas (desc, val, subbloco)
    passivo_subbloco = "CIRCULANTE"  # estado inicial: passivo sempre comeca pelo Circulante
    # Para DRE (texto corrido): candidatos únicos
    dre_candidates = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()

            if tables:
                for table in tables:
                    for row in table:
                        if row is None:
                            continue
                        if len(row) < 3:
                            continue

                        c0 = str(row[0] or "").replace("\n", " ").strip()
                        c2 = str(row[2] or "").strip() if len(row) > 2 else ""
                        c5 = str(row[5] or "").replace("\n", " ").strip() if len(row) > 5 else ""
                        c7 = str(row[7] or "").strip() if len(row) > 7 else ""
                        c8 = str(row[8] or "").strip() if len(row) > 8 else ""
                        c3 = str(row[3] or "").strip() if len(row) > 3 else ""
                        # FIX_20260721_1400 (bug 5): o pdfplumber por vezes parte um
                        # valor negativo entre parenteses em duas colunas adjacentes,
                        # ex.: c7='(2.713.646,34' e c8=')'. Sem juntar, br_to_float()
                        # nunca ve o ')' e o valor vira positivo por engano.
                        if c8 == ")" and c7 and not c7.endswith(")"):
                            c7 = c7 + ")"
                        if c3 == ")" and c2 and not c2.endswith(")"):
                            c2 = c2 + ")"

                        # Bug 4d: suporte a 4 colunas (Consolidado BP)
                        if len(row) <= 5:
                            # 4-col: [ATIVO_DESC, ATIVO_VAL, PASSIVO_DESC, PASSIVO_VAL]
                            c1 = str(row[1] or "").strip() if len(row) > 1 else ""
                            c3 = str(row[3] or "").strip() if len(row) > 3 else ""
                            if c0 and c1 and BR_NUM_BARE.search(c1):
                                v = br_to_float(c1)
                                if v is not None:
                                    ativo_candidates.append((clean_desc(c0), v))
                            if c2 and c3 and BR_NUM_BARE.search(c3):
                                v = br_to_float(c3)
                                if v is not None:
                                    d2 = clean_desc(c2)
                                    passivo_subbloco = _atualizar_subbloco_passivo(norm(d2), passivo_subbloco)
                                    passivo_candidates.append((d2, v, passivo_subbloco))
                            continue

                        # ── Linha limpa 8-10 cols: cols separadas ──
                        if c0 and c2 and BR_NUM_BARE.search(c2):
                            v = br_to_float(c2)
                            if v is not None:
                                ativo_candidates.append((clean_desc(c0), v))

                        if c5 and c7 and BR_NUM_BARE.search(c7):
                            v = br_to_float(c7)
                            if v is not None:
                                d5 = clean_desc(c5)
                                passivo_subbloco = _atualizar_subbloco_passivo(norm(d5), passivo_subbloco)
                                passivo_candidates.append((d5, v, passivo_subbloco))

                        # ── Linha misturada (c0 tem ATIVO+PASSIVO juntos, c2 vazio) ──
                        if c0 and not BR_NUM_BARE.search(c2):
                            pairs = split_mixed_line(c0)
                            if len(pairs) >= 2:
                                ativo_candidates.append(pairs[0])
                                passivo_subbloco = _atualizar_subbloco_passivo(norm(pairs[1][0]), passivo_subbloco)
                                passivo_candidates.append((pairs[1][0], pairs[1][1], passivo_subbloco))
                            elif len(pairs) == 1:
                                # FIX_20260722_1500 (bug 10): linha com 1 par so pode
                                # ser do ATIVO (comportamento antigo) OU do PASSIVO
                                # (quando o ATIVO ja esgotou suas contas e o pdfplumber
                                # cola o resto do PASSIVO sozinho na coluna 0). Decide
                                # pelo nome, nao assume sempre ATIVO.
                                d0, v0 = pairs[0]
                                nome_n = norm(d0)
                                eh_passivo_conhecido = (
                                    nome_n in _PASSIVO_NAMES
                                    or nome_n in _PASSIVO_CIRC_ONLY_NAMES
                                    or nome_n in _PASSIVO_NCIRC_ONLY_NAMES
                                )
                                if eh_passivo_conhecido:
                                    passivo_subbloco = _atualizar_subbloco_passivo(nome_n, passivo_subbloco)
                                    passivo_candidates.append((d0, v0, passivo_subbloco))
                                else:
                                    ativo_candidates.append(pairs[0])

            else:
                # Fallback: texto puro (DRE vem sem tabelas no 2025)
                txt = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                for line in txt.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    v = extract_last_value(line)
                    if v is not None:
                        dre_candidates.append((clean_desc(line), v, "QUALQUER"))

    if tipo == "BP":
        # Processar ATIVO e PASSIVO separadamente, garantindo contexto correto
        ativo_with_ctx = [(d, v, "ATIVO") for d, v in ativo_candidates]
        # FIX_20260721_1600 (bug 9): ctx granular PASSIVO_CIRC/PASSIVO_NCIRC
        # em vez do antigo "PASSIVO" fixo, com base no subbloco rastreado.
        passivo_with_ctx = [
            (d, v, "PASSIVO_NCIRC" if sub == "NAO CIRCULANTE" else "PASSIVO_CIRC")
            for d, v, sub in passivo_candidates
        ]

        # PL: identificar pelo nome.
        # FIX_20260721_1400 (bug 7): quando uma linha do tipo "Patrimonio
        # Liquido <valor>" aparece SOZINHA numa celula (sem nada do ATIVO
        # colado antes), split_mixed_line() so acha 1 par, e esse par
        # sempre era jogado em ativo_candidates (regra antiga: "1o par =
        # ATIVO"). Isso fazia a linha ser descartada mais na frente, pois
        # o filtro de contexto (context_ok) so aceita nomes de Patrimonio
        # Liquido quando o contexto e "PATRIMONIO", nunca "ATIVO". Por
        # isso agora a reclassificacao por nome roda tambem sobre
        # ativo_with_ctx, nao so sobre passivo_with_ctx.
        nomes_pl = {norm(k) for k in ["PATRIMONIO LIQUIDO","PATRIMOMIO LIQUIDO",
                      "CAPITAL SOCIAL","CAPITAL SUBSCRITO","LUCROS/PREJUIZOS ACUMULADOS",
                      "LUCROS ACUMULADOS","PREJUIZOS ACUMULADOS",
                      "TOTAL PATRIMONIO LIQUIDO","TOTAL DO PASSIVO E PATRIMONIO"]}

        pl_candidates = []
        passivo_final = []
        ativo_final = []
        for d, v, ctx in passivo_with_ctx:
            if norm(d) in nomes_pl:
                pl_candidates.append((d, v, "PATRIMONIO"))
            else:
                passivo_final.append((d, v, ctx))
        for d, v, ctx in ativo_with_ctx:
            if norm(d) in nomes_pl:
                pl_candidates.append((d, v, "PATRIMONIO"))
            else:
                ativo_final.append((d, v, ctx))

        all_candidates = ativo_final + passivo_final + pl_candidates
        return process_candidates(all_candidates, targets, tipo, origem)
    else:
        return process_candidates(dre_candidates, targets, tipo, origem)


# ─────────────────────────────────────────────
#  GRANULARIDADE REAL DO PERIODO (24/09/2026)
# ─────────────────────────────────────────────

def calcular_granularidade(periodo_inicio: str, periodo_fim: str) -> str:
    """
    Classifica o INTERVALO REAL declarado no proprio PDF ("Periodo: X a
    Y") em mensal/trimestral/semestral/anual -- nunca por calculo/delta
    entre 2 fechamentos diferentes (isso e' estimativa, nao dado real;
    decisao do Rafael 24/09/2026: "sempre com valores reais e corretos").
    String vazia (sem intervalo declarado no PDF, so' 1 data) devolve ""
    -- 'nunca inventa numero' vale pra granularidade tambem.
    """
    if not periodo_inicio or not periodo_fim:
        return ""
    try:
        d_ini = datetime.strptime(periodo_inicio, "%d/%m/%Y").date()
        d_fim = datetime.strptime(periodo_fim, "%d/%m/%Y").date()
    except ValueError:
        return ""
    dias = (d_fim - d_ini).days + 1
    if dias <= 0:
        return ""
    if dias <= 32:
        return "mensal"
    if dias <= 95:
        return "trimestral"
    if dias <= 185:
        return "semestral"
    if dias <= 370:
        return "anual"
    return "outra"


# ─────────────────────────────────────────────
#  DISPATCHER PRINCIPAL
# ─────────────────────────────────────────────

def processar_pdf(pdf_path: Path):
    bp_rows, dre_rows, log, meta = [], [], [], []
    try:
        fmt, tipo, empresa, cnpj, periodo, periodo_inicio = detect_format_and_type(pdf_path)
        granularidade = calcular_granularidade(periodo_inicio, periodo)
        meta.append([empresa, cnpj, periodo, pdf_path.name, tipo, fmt, periodo_inicio, granularidade])
        origem = f"PDF {periodo}" if periodo else f"PDF {pdf_path.stem}"

        if tipo == "DESCONHECIDO":
            log.append(["ALERTA", pdf_path.name, "?",
                        f"Nao identificado como BP ou DRE (formato={fmt})"])
            return bp_rows, dre_rows, log, meta

        if fmt == "SPED":
            rows = parse_sped(pdf_path, tipo, origem)
        elif fmt == "DUPLO":
            rows = parse_duplo(pdf_path, tipo, origem)
            # FIX_20260729 (bug 11): em alguns PDFs de BP, o pdfplumber detecta
            # uma tabela espuria de 8+ colunas (extract_tables()) mesmo quando o
            # documento e, na pratica, layout de coluna unica (ATIVO inteiro,
            # depois PASSIVO inteiro, sem duas colunas lado a lado). fmt fica
            # "DUPLO" por engano, e o parse_duplo() (que so sabe ler valor nas
            # posicoes c2/c7, ou numero embutido em c0) perde linhas cujo valor
            # cai isolado numa coluna intermediaria (ex.: c5 sem c7) — sem erro,
            # sem log, silenciosamente. Confirmado no BP da Enermais Energia
            # 05/2026 (pasta BUG02): DISPONIVEL, CLIENTES, LUCROS/PREJUIZOS
            # ACUMULADOS e o proprio TOTAL DO PASSIVO desapareciam, mesmo com o
            # texto presente e legivel no PDF. parse_texto() (que le o texto
            # corrido da pagina, sem depender da tabela espuria do pdfplumber)
            # extraiu os 4 corretamente no mesmo arquivo.
            # Fix: se o BP resultante do DUPLO nao tem TOTAL DO ATIVO e/ou TOTAL
            # DO PASSIVO (sinal forte de perda de linhas), roda parse_texto()
            # como fallback e complementa so as contas que o DUPLO nao achou —
            # nao substitui nada que o DUPLO ja tenha capturado certo. Testado
            # contra os 9 BPs de referencia (Construtora, Engenharia, SMG x2,
            # Consolidado, Renovaveis, Solucoes, Energia x2): todos ja tinham os
            # dois totais, fallback nunca aciona neles — zero regressao.
            if tipo == "BP":
                nomes_ok = {(r[0], r[1]) for r in rows}
                nomes_alvo = {r[1] for r in rows}
                if "TOTAL DO ATIVO" not in nomes_alvo or "TOTAL DO PASSIVO" not in nomes_alvo:
                    rows_fallback = parse_texto(pdf_path, tipo, origem)
                    n_add = 0
                    for r in rows_fallback:
                        chave = (r[0], r[1])
                        if chave not in nomes_ok:
                            rows.append(r)
                            nomes_ok.add(chave)
                            n_add += 1
                    if n_add:
                        log.append(["AVISO", pdf_path.name, "BP",
                                    f"DUPLO incompleto (TOTAL ausente) - "
                                    f"{n_add} conta(s) complementada(s) via fallback TEXTO"])
        else:
            rows = parse_texto(pdf_path, tipo, origem)

        n = len(rows)
        if tipo == "BP":
            bp_rows.extend(rows)
            log.append(["OK", pdf_path.name, f"BP ({fmt})", f"{n} contas extraidas"])
        else:
            dre_rows.extend(rows)
            log.append(["OK", pdf_path.name, f"DRE ({fmt})", f"{n} contas extraidas"])

        if n == 0:
            log.append(["ALERTA", pdf_path.name, tipo,
                        "Nenhuma conta encontrada — verifique o PDF ou o mapeamento"])

    except Exception as exc:
        import traceback
        log.append(["ERRO", pdf_path.name, "FALHA", str(exc)])
        log.append(["DETALHE", pdf_path.name, "TRACEBACK", traceback.format_exc()])

    return bp_rows, dre_rows, log, meta


# ─────────────────────────────────────────────
#  EXTRAÇÃO DE ITENS (sub-contas) — usado pelo relatorio comentado
# ─────────────────────────────────────────────

def extrair_despesas_admin_itens(pdf_path: Path) -> list:
    """
    FIX_20260925b (pedido do Rafael — Fase 2 do gerador de relatorio
    comentado, campo `despesas_admin_itens`): extrai as sub-contas dentro
    do grupo "Administrativas" da DRE (ex.: "Salários e Ordenados",
    "Serviços Profissionais"), que o fluxo BP/DRE normal NAO captura --
    DRE_TARGETS so' guarda o TOTAL do grupo ("ADMINISTRATIVAS"), pelo
    mesmo padrao ja usado p/ CSLL/IRPJ (grupo com N filhos, so' o total
    e' alvo).

    Funcao ADITIVA e independente: nao mexe em parse_texto/parse_sped/
    parse_duplo/process_candidates, nao muda a saida do TSV nem o `found`
    usado pelo fluxo BP/DRE -- reusa so' as funcoes puras ja validadas
    (norm, extract_last_value, clean_desc, match_target) pra minimizar
    risco de regressao no que ja esta em producao.

    Confirmado nos DRE reais (Enermais Energia SPED 2025, Enermais
    Construtora SPED 2025, Construtora 2T2026, Energia 2T2026 -- ver
    00-handoff.md secao 66): toda DRE da Enermais tem fmt=TEXTO (SPED so'
    ocorre em BP), e o grupo "Administrativas" e' sempre 1 linha isolada
    (bate um alias de "ADMINISTRATIVAS" em DRE_TARGETS) seguida de N
    linhas "Nome valor" ate a proxima linha que bate com QUALQUER outro
    alias de DRE_TARGETS (normalmente "Despesas Financeiras"). Pega so' a
    1a ocorrencia do header (o grupo "De Vendas", quando existe, sempre
    vem ANTES de "Administrativas" no layout e tem seu proprio total —
    nao e' confundido, porque so comecamos a coletar apos achar o header).
    Validado: soma dos itens = valor do grupo "ADMINISTRATIVAS" já
    capturado pelo fluxo normal, nos 4 PDFs de amostra acima.
    """
    all_lines = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            all_lines.extend(txt.splitlines())

    # FIX_20260925b (achado testando "Enermais Construtora - DRE - SPED
    # 2025.pdf"): o alias "CARTAO CORPORATIVO" de OUTRAS DESPESAS
    # OPERACIONAIS existe p/ cobrir os periodos onde Cartao Corporativo
    # aparece como grupo PROPRIO da DRE (fora de Administrativas) -- mas
    # nesse PDF real ele aparece como um ITEM comum dentro de
    # Administrativas ("Cartão Corporativo (238.159,69)"), sem grupo
    # proprio. Usar esse alias como fim-de-bloco aqui cortava a extracao
    # no meio (perdendo Cartao Corporativo + tudo que vem depois dele ate'
    # Despesas Financeiras -- R$ 450.934,28 nesse caso, confirmado batendo
    # exatamente contra o total do grupo ADMINISTRATIVAS ja capturado pelo
    # fluxo normal). Por isso "CARTAO CORPORATIVO" fica de fora dos
    # terminadores aqui — os outros aliases de OUTRAS DESPESAS OPERACIONAIS
    # continuam valendo.
    admin_aliases = None
    outros_aliases = []
    for _grupo, nome_saida, aliases in DRE_TARGETS:
        if nome_saida == "ADMINISTRATIVAS":
            admin_aliases = aliases
        else:
            aliases_seguros = [a for a in aliases if norm(a) != "CARTAO CORPORATIVO"]
            if aliases_seguros:
                outros_aliases.append(aliases_seguros)
    if not admin_aliases:
        return []

    itens = []
    achou_header = False
    admin_total = None
    soma_corrente = 0.0
    for line in all_lines:
        line = line.strip()
        if not line:
            continue
        line_n = norm(line)

        if not achou_header:
            if match_target(line_n, admin_aliases):
                achou_header = True
                admin_total = extract_last_value(line)
            continue

        if any(match_target(line_n, aliases) for aliases in outros_aliases):
            break

        val = extract_last_value(line)
        if val is None:
            continue
        desc = clean_desc(line)
        if not desc:
            continue
        itens.append((desc, val))
        soma_corrente += val

        # FIX_20260925b (achado no mesmo PDF acima): alem dos terminadores
        # nomeados, existe pelo menos 1 caso real de sub-grupo ANINHADO sem
        # nome conhecido dentro de Administrativas ("Com Veiculos", com 2
        # filhos "Combustiveis e Lubrificantes"/"Manutencao e Reparos de
        # Veiculos" que somam o mesmo valor) que na verdade NAO pertence ao
        # total de Administrativas (confirmado: excluindo os 3 o total bate
        # exato). Como o valor do header ("ADMINISTRATIVAS X") e' conhecido
        # de antemao, a soma acumulada dos itens e' comparada a cada linha
        # contra esse total -- assim que bater (tolerancia de 1 centavo),
        # para ali, mesmo sem reconhecer o nome do proximo grupo. Robusto a
        # sub-grupos aninhados desconhecidos sem exigir lista fixa de nomes.
        if admin_total is not None and abs(soma_corrente - admin_total) < 0.01:
            break

    return itens


# ─────────────────────────────────────────────
#  ESCRITA TSV
# ─────────────────────────────────────────────

def write_tsv(path: Path, headers: list, rows: list):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Leitor PDF Enermais V4")
    parser.add_argument("--outdir", required=True, help="Pasta de saida dos TSVs")
    parser.add_argument("--pdfs", nargs="+", required=True, help="PDFs a processar")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    all_bp, all_dre, all_log, all_meta = [], [], [], []

    for pdf_str in args.pdfs:
        path = Path(pdf_str)
        if not path.exists():
            all_log.append(["ERRO", str(path), "?", "Arquivo nao encontrado"])
            continue
        bp, dre, log, meta = processar_pdf(path)
        all_bp.extend(bp)
        all_dre.extend(dre)
        all_log.extend(log)
        all_meta.extend(meta)

    write_tsv(outdir / "enermais_bp.tsv",
              ["GRUPO", "CONTA", "VALOR", "ORIGEM"], all_bp)
    write_tsv(outdir / "enermais_dre.tsv",
              ["CONTA", "VALOR", "GRUPO", "ORIGEM"], all_dre)
    write_tsv(outdir / "enermais_log.tsv",
              ["NIVEL", "ARQUIVO", "TIPO", "MENSAGEM"], all_log)
    write_tsv(outdir / "enermais_meta.tsv",
              ["EMPRESA", "CNPJ", "PERIODO", "ARQUIVO", "TIPO", "FORMATO",
               "PERIODO_INICIO", "GRANULARIDADE"], all_meta)

    print(f"OK: BP={len(all_bp)} DRE={len(all_dre)} LOG={len(all_log)}")
    for entry in all_log:
        if entry[0] != "DETALHE":
            print(f"  [{entry[0]}] {entry[1]}: {entry[3]}")


if __name__ == "__main__":
    main()
