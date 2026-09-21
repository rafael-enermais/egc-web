# -*- coding: utf-8 -*-
"""
Checagens de qualidade do lado do app web -- NAO mexe no parser (regra
absoluta do projeto: nao alterar parser_egc.py/EXE/estrutura TSV). So
LE o resultado que o parser ja devolve e aplica validacoes extras antes
de gravar ou ao revisar.
"""
from __future__ import annotations

from parser_egc import br_to_float


def checar_fechamento_bp(bp_rows: list) -> tuple[float | None, float | None]:
    """
    Confere TOTAL DO ATIVO vs TOTAL DO PASSIVO num BP recem-extraido
    (bp_rows no formato do parser: [grupo, conta, valor_br, origem]).
    Retorna (total_ativo, total_passivo) como float, ou None quando a
    conta nao foi encontrada nas linhas (ex.: BP truncado).
    Nao decide nada sozinho -- quem chama decide se bloqueia ou so avisa
    (BP 05/2026 da Energia ja mostrou que a divergencia pode vir do
    proprio PDF fonte, nao e sempre bug de leitura).
    """
    total_ativo = None
    total_passivo = None
    for grupo, conta, valor_br, _origem in bp_rows:
        if conta == "TOTAL DO ATIVO":
            total_ativo = br_to_float(valor_br)
        elif conta == "TOTAL DO PASSIVO":
            total_passivo = br_to_float(valor_br)
    return total_ativo, total_passivo


def cnpj_bate(cnpj_detectado: str, cnpj_esperado: str) -> bool:
    """
    Compara CNPJ detectado no PDF pelo parser com o CNPJ da empresa
    selecionada na sidebar. Comparacao simples (mesma mascara nos dois
    lados -- parser usa regex \\d{2}.\\d{3}.\\d{3}/\\d{4}-\\d{2}, igual ao
    cadastro em conexao.EMPRESAS_FIXAS). CNPJ vazio (parser nao achou)
    conta como NAO bate -- melhor pedir confirmacao manual do que gravar
    sem certeza nenhuma de qual empresa e' o PDF.
    """
    return bool(cnpj_detectado) and cnpj_detectado.strip() == cnpj_esperado.strip()

def formatar_br(v: float) -> str:
    """1234.5 -> "1.234,50" (mesmo estilo BR usado no resto do app)."""
    return f"{v:,.2f}".translate(str.maketrans({",": "X", ".": ","})).replace("X", ".")

