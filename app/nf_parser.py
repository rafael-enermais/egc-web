# -*- coding: utf-8 -*-
"""
EGC | Notas Fiscais — leitura do manifesto de NF-e (planilha .xlsx que a
contadora baixa da Receita Federal, formato "Fiscal-io": colunas
Num, Tipo, DtEmi, Valor, CFOP, Emissor Nome, Emissor CNPJ/CPF, UF, Chave).

Modulo puro (sem Streamlit, sem banco) -- so pandas/openpyxl -- pra ficar
testavel sem mock nenhum. Quem chama (telas/7_Notas_Fiscais.py) decide o
que fazer com o DataFrame devolvido (gravar em egc.nf_manifesto_import).

Decisao de escopo (25/09/2026): nao reaproveita o parser do EGEN (PDF/XML
avulso, com bug conhecido e nao confirmado em constants.classificar()) --
o formato de entrada aqui e' bem mais simples (planilha ja estruturada da
Receita, 1 linha por nota), entao um parser dedicado, pequeno, e' mais
barato e mais seguro que herdar risco de outro sistema.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd

# Nomes de coluna exatamente como vem da planilha (aba "Fiscal-io" ou
# similar -- ver COLUNAS_ALTERNATIVAS pra outras variacoes ja vistas).
COLUNAS_ESPERADAS = ["Num", "Tipo", "DtEmi", "Valor", "CFOP", "Emissor Nome", "Emissor CNPJ/CPF", "UF", "Chave"]

# Caso a Receita mude o nome de alguma coluna entre exportações -- mapa de
# apelidos conhecidos pra nome canônico. Ampliar aqui se aparecer variação
# nova (nunca silenciosamente admitir uma coluna desconhecida sem log).
ALIASES = {
    "numero": "Num", "num": "Num",
    "tipo": "Tipo",
    "dtemi": "DtEmi", "data emissao": "DtEmi", "data emissão": "DtEmi",
    "valor": "Valor", "valor nf": "Valor", "valor da nota": "Valor",
    "cfop": "CFOP",
    "emissor nome": "Emissor Nome", "fornecedor": "Emissor Nome", "razao social": "Emissor Nome",
    "emissor cnpj/cpf": "Emissor CNPJ/CPF", "cnpj": "Emissor CNPJ/CPF", "cnpj fornecedor": "Emissor CNPJ/CPF",
    "uf": "UF",
    "chave": "Chave", "chave de acesso": "Chave", "chave acesso": "Chave",
}


class ManifestoInvalido(Exception):
    """Planilha sem as colunas mínimas pra decidir o que fazer (log em
    egc.eventos_sistema, origem='notas_fiscais', pelo chamador)."""


def _normalizar_nome_coluna(nome: str) -> str:
    return ALIASES.get(str(nome).strip().lower(), str(nome).strip())


def decodificar_chave_acesso(chave: str) -> Optional[dict]:
    """
    Decodifica os 44 dígitos da chave de acesso da NF-e por posição
    (layout oficial SEFAZ, confirmado batendo com dado real do Sienge em
    25/09/2026 -- ver EGC 00-handoff.md seção 62):

        UF(2) AAMM(4) CNPJ(14) modelo(2) série(3) número(9) tpEmis(1) cNF(8) DV(1)

    Retorna None se não tiver 44 dígitos (nunca lança -- chave ausente ou
    malformada vira "sem confirmação extra", não erro fatal de import).
    """
    if not chave:
        return None
    digitos = re.sub(r"\D", "", str(chave))
    if len(digitos) != 44:
        return None
    return {
        "uf": digitos[0:2],
        "aamm": digitos[2:6],
        "cnpj_emissor": digitos[6:20],
        "modelo": digitos[20:22],
        "serie": digitos[22:25],
        "numero": digitos[25:34].lstrip("0") or "0",
        "tipo_emissao": digitos[34:35],
        "codigo_numerico": digitos[35:43],
        "dv": digitos[43:44],
    }


def normalizar_cnpj(valor) -> str:
    """So os dígitos -- pra comparar CNPJ do manifesto (as vezes vem só
    número) com o do Sienge (as vezes vem formatado "00.000.000/0000-00")
    sem depender de formatação igual dos dois lados."""
    return re.sub(r"\D", "", str(valor or ""))


def normalizar_numero_nota(valor) -> str:
    """Remove zeros à esquerda e qualquer caractere não numérico -- o
    Sienge guarda documentNumber como texto livre ("10448"), a planilha
    guarda Num como inteiro (10448) ou às vezes texto com zeros
    ("00010448"); depois de normalizado os dois viram a mesma string."""
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos.lstrip("0") or "0"


def ler_manifesto_xlsx(caminho_ou_buffer) -> pd.DataFrame:
    """
    Lê a planilha e devolve um DataFrame já normalizado, 1 linha por nota,
    com as colunas extras decodificadas da chave (cnpj_emissor_chave,
    modelo, serie, numero_chave) -- usadas como confirmação extra no
    matching, nunca como único critério (só ~14% das notas do lado Sienge
    trazem a chave preenchida pra cruzar, mas a chave da planilha em si é
    sempre 100% confiável, então decodificar sempre é barato e ajuda a
    auditar).

    Levanta ManifestoInvalido se faltar alguma coluna essencial (Num,
    Valor, Emissor CNPJ/CPF) -- essas 3 são o mínimo pro matching
    funcionar; as outras (Tipo, DtEmi, CFOP, UF, Chave) são opcionais.
    """
    df = pd.read_excel(caminho_ou_buffer, dtype=str)
    df.columns = [_normalizar_nome_coluna(c) for c in df.columns]

    essenciais = ["Num", "Valor", "Emissor CNPJ/CPF"]
    faltando = [c for c in essenciais if c not in df.columns]
    if faltando:
        raise ManifestoInvalido(
            f"Planilha sem a(s) coluna(s) essencial(is): {faltando}. "
            f"Colunas encontradas: {list(df.columns)}"
        )

    for opcional in ["Tipo", "DtEmi", "CFOP", "Emissor Nome", "UF", "Chave"]:
        if opcional not in df.columns:
            df[opcional] = None

    df["_numero_normalizado"] = df["Num"].apply(normalizar_numero_nota)
    df["_cnpj_normalizado"] = df["Emissor CNPJ/CPF"].apply(normalizar_cnpj)
    df["_valor_float"] = pd.to_numeric(
        df["Valor"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        if df["Valor"].astype(str).str.contains(",").any()
        else df["Valor"],
        errors="coerce",
    )

    decodificadas = df["Chave"].apply(decodificar_chave_acesso)
    df["_chave_cnpj"] = decodificadas.apply(lambda d: d["cnpj_emissor"] if d else None)
    df["_chave_modelo"] = decodificadas.apply(lambda d: d["modelo"] if d else None)
    df["_chave_serie"] = decodificadas.apply(lambda d: d["serie"] if d else None)
    df["_chave_numero"] = decodificadas.apply(lambda d: d["numero"] if d else None)

    return df.reset_index(drop=True)
