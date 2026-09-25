# -*- coding: utf-8 -*-
"""
Testes de app/parser_egc.py::extrair_despesas_admin_itens (FIX_20260925b —
Fase 2 do relatorio comentado, campo `despesas_admin_itens`).

A validacao "de verdade" (soma dos itens = total do grupo ADMINISTRATIVAS
ja capturado pelo fluxo BP/DRE normal) foi feita a mao contra 11 DRE reais
dos 6 CNPJs + consolidado + os 2 regimes tributarios (real e presumido),
ver 00-handoff.md secao 66 -- fora do escopo de pytest (precisa dos PDFs
reais do vault, que nao vem no repo). Este arquivo cobre a LOGICA pura de
corte de bloco com texto sintetico, mockando pdfplumber, pra travar as 2
regressoes reais achadas nessa validacao:

  1. terminador nomeado ("Despesas Financeiras") corta o bloco certo;
  2. sub-grupo aninhado SEM nome conhecido (ex.: "Com Veiculos" com 2
     filhos que somam o mesmo valor, e que na pratica NAO faz parte do
     total de Administrativas) e' cortado corretamente porque a soma
     acumulada bate com o total do header antes dele -- mesmo sem
     reconhecer o nome do sub-grupo.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import parser_egc as p  # noqa: E402


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self, x_tolerance=2, y_tolerance=3):
        return self._text


class _FakePdf:
    def __init__(self, pages_text):
        self.pages = [_FakePage(t) for t in pages_text]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _rodar(texto_paginas):
    fake_pdf = _FakePdf(texto_paginas)
    with patch.object(p, "pdfplumber") as mock_pp:
        mock_pp.open.return_value = fake_pdf
        return p.extrair_despesas_admin_itens(Path("qualquer.pdf"))


def test_corta_no_terminador_nomeado_despesas_financeiras():
    texto = """
(+/-) DESPESAS OPERACIONAIS (3.550.893,84)
ADMINISTRATIVAS (150,00)
Salários e Ordenados (100,00)
Inss (50,00)
DESPESAS FINANCEIRAS (13.886,38)
Juros Pagos ou Incorridos (13.886,38)
"""
    itens = _rodar([texto])
    assert itens == [("Salários e Ordenados", -100.00), ("Inss", -50.00)]


def test_corta_em_subgrupo_aninhado_sem_nome_conhecido_pela_soma():
    """Reproduz o caso real ('Com Veiculos' na Enermais Construtora SPED
    2025): um sub-grupo sem alias conhecido, cujo valor + filhos NAO fazem
    parte do total do header -- a soma acumulada bate com o total do
    header ANTES do sub-grupo, entao para ali mesmo sem reconhecer o nome."""
    texto = """
ADMINISTRATIVAS (150,00)
Salários e Ordenados (100,00)
Inss (50,00)
Algo Desconhecido (30,00)
Filho A (15,00)
Filho B (15,00)
DESPESAS FINANCEIRAS (10,00)
"""
    itens = _rodar([texto])
    assert itens == [("Salários e Ordenados", -100.00), ("Inss", -50.00)]


def test_sem_header_administrativas_retorna_vazio():
    texto = """
RECEITA OPERACIONAL BRUTA 100,00
LUCRO BRUTO 100,00
"""
    assert _rodar([texto]) == []


def test_bloco_atravessa_quebra_de_pagina():
    pagina1 = """
ADMINISTRATIVAS (150,00)
Salários e Ordenados (100,00)
"""
    pagina2 = """
Inss (50,00)
DESPESAS FINANCEIRAS (10,00)
"""
    itens = _rodar([pagina1, pagina2])
    assert itens == [("Salários e Ordenados", -100.00), ("Inss", -50.00)]


def test_pega_so_a_primeira_ocorrencia_do_header():
    """'De Vendas' (outro sub-grupo, com seu proprio alias diferente de
    ADMINISTRATIVAS) vem antes e nao deve ser confundido; so' a partir do
    header real de Administrativas a coleta comeca."""
    texto = """
DE VENDAS (20,00)
Energia Elétrica (20,00)
ADMINISTRATIVAS (150,00)
Salários e Ordenados (100,00)
Inss (50,00)
DESPESAS FINANCEIRAS (10,00)
"""
    itens = _rodar([texto])
    assert itens == [("Salários e Ordenados", -100.00), ("Inss", -50.00)]
