# -*- coding: utf-8 -*-
"""
EGC | Creditos PIS/COFINS -- leitura do "Detalhamento de Debito e Credito PIS e COFINS" feito pela contadora (.xlsx).  (v0.50.0)

Layout lido (07/2026, enviado pelo Rafael em 15/10/2026): abas "Resumo", "Débitos SPED (PIS-COFINS)", "Reconciliação Crédito COFINS" e uma aba
por categoria (Combustível, Uso ou Consumo, Imobilizados, Fretes, Serviços Tomados, Locação de Máquinas, Aluguel, Demais Produtos) com
cabeçalho na linha 4 ("CNPJ / CPF / CNO ...") e linha "TOTAL" no fim. O leitor e' tolerante: acha o cabeçalho de cada aba procurando a
coluna do CNPJ nas primeiras linhas, aceita categorias novas (qualquer aba com esse cabeçalho) e nunca levanta excecao por arquivo ruim.
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from typing import Optional

import pandas as pd

CATEGORIAS = ["Combustível", "Uso ou Consumo", "Imobilizados", "Fretes", "Serviços Tomados", "Locação de Máquinas", "Aluguel",
              "Demais Produtos"]
COLUNAS_LINHA = ["categoria", "cnpj", "fornecedor", "numero", "especie", "data_emissao", "natureza", "produto", "descricao",
                 "valor_total", "base", "cst_pis", "aliq_pis", "pis", "cst_cofins", "aliq_cofins", "cofins", "tipo_credito", "linha_origem"]

_MAPA = {  # nome normalizado da coluna -> campo
    "cnpj / cpf / cno": "cnpj", "numero": "numero", "especie": "especie", "data emissao": "data_emissao",
    "razao social": "fornecedor", "natureza": "natureza", "produto": "produto", "descricao": "descricao",
    "valor total": "valor_total", "base calculo": "base", "codigo situacao tributaria pis": "cst_pis", "aliquota pis": "aliq_pis",
    "valor pis": "pis", "codigo situacao tributaria cofins": "cst_cofins", "aliquota cofins": "aliq_cofins", "valor cofins": "cofins",
    "tipo credito pis": "tipo_credito",
}


def _sem_acento(s) -> str:
    t = unicodedata.normalize("NFKD", str(s if s is not None else ""))
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip().lower()


def _digitos(v) -> str:
    return re.sub(r"\D", "", str(v if v is not None else ""))


def normalizar_numero(v) -> str:
    s = str(v if v is not None else "").strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    d = re.sub(r"\D", "", s)
    return (d.lstrip("0") or "0") if d else s.upper()


def _f(v) -> float:
    x = pd.to_numeric(v, errors="coerce")
    return 0.0 if pd.isna(x) else float(x)


def _para_iso(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    d = pd.to_datetime(v, errors="coerce", format="ISO8601") if re.match(r"^\d{4}-\d{2}-\d{2}", str(v)) else pd.to_datetime(v, dayfirst=True, errors="coerce")
    return None if pd.isna(d) else d.strftime("%Y-%m-%d")


def _achar_cabecalho(raw: pd.DataFrame) -> Optional[int]:
    for i in range(min(len(raw), 15)):
        if _sem_acento(raw.iat[i, 0]) == "cnpj / cpf / cno":
            return i
    return None


def ler_detalhamento(conteudo: bytes, nome_arquivo: str = "") -> dict:
    """Devolve {erro, competencia, linhas (DataFrame), resumo (DataFrame categoria/qtde/valor/base/pis/cofins), debitos (DataFrame
    descricao/pis/cofins), reconciliacao (DataFrame), categorias ([...]), sha256, arquivo}."""
    out = {"erro": None, "competencia": None, "linhas": pd.DataFrame(columns=COLUNAS_LINHA), "resumo": pd.DataFrame(),
           "debitos": pd.DataFrame(), "reconciliacao": pd.DataFrame(), "categorias": [], "sha256": hashlib.sha256(conteudo or b"").hexdigest(),
           "arquivo": nome_arquivo}
    try:
        abas = pd.read_excel(io.BytesIO(conteudo), sheet_name=None, header=None, dtype=object)
    except Exception as exc:
        out["erro"] = f"Não consegui abrir a planilha ({type(exc).__name__}). Confira se é o Detalhamento (.xlsx) da contadora."
        return out
    linhas, competencias = [], []
    for nome, raw in abas.items():
        if raw.empty:
            continue
        h = _achar_cabecalho(raw)
        n = _sem_acento(nome)
        if h is not None:
            cols = {}
            for j in range(raw.shape[1]):
                campo = _MAPA.get(_sem_acento(raw.iat[h, j]).rstrip())
                if campo and campo not in cols.values():
                    cols[j] = campo
            if "cnpj" not in cols.values() or "numero" not in cols.values():
                continue
            m = re.search(r"(\d{2})/(\d{4})", str(raw.iat[0, 0]))
            if m:
                competencias.append(f"{m.group(1)}/{m.group(2)}")
            corpo = raw.iloc[h + 1:].copy()
            corpo.columns = range(corpo.shape[1])
            for pos, r in enumerate(corpo.itertuples(index=False), start=h + 2):
                d = {campo: r[j] for j, campo in cols.items()}
                if str(d.get("cnpj", "")).strip().upper() == "TOTAL" or pd.isna(d.get("cnpj")) and pd.isna(d.get("numero")):
                    continue
                linhas.append({
                    "categoria": str(nome).strip(), "cnpj": _digitos(d.get("cnpj")), "fornecedor": str(d.get("fornecedor") or "").strip(),
                    "numero": normalizar_numero(d.get("numero")), "especie": str(d.get("especie") or "").strip().upper(),
                    "data_emissao": _para_iso(d.get("data_emissao")), "natureza": str(d.get("natureza") or "").replace(".0", ""),
                    "produto": str(d.get("produto") or "").replace(".0", ""), "descricao": str(d.get("descricao") or "").strip(),
                    "valor_total": _f(d.get("valor_total")), "base": _f(d.get("base")), "cst_pis": str(d.get("cst_pis") or "").replace(".0", ""),
                    "aliq_pis": _f(d.get("aliq_pis")), "pis": _f(d.get("pis")), "cst_cofins": str(d.get("cst_cofins") or "").replace(".0", ""),
                    "aliq_cofins": _f(d.get("aliq_cofins")), "cofins": _f(d.get("cofins")), "tipo_credito": str(d.get("tipo_credito") or "").strip(),
                    "linha_origem": pos})
            out["categorias"].append(str(nome).strip())
        elif n.startswith("resumo"):
            m = re.search(r"(\d{2})/(\d{4})", str(raw.iat[0, 0]))
            if m:
                competencias.append(f"{m.group(1)}/{m.group(2)}")
            r0 = next((i for i in range(len(raw)) if _sem_acento(raw.iat[i, 0]) == "categoria"), None)
            if r0 is not None:
                t = raw.iloc[r0 + 1:, :6].copy()
                t.columns = ["categoria", "qtde", "valor_total", "base", "pis", "cofins"][:t.shape[1]]
                t = t[t["categoria"].notna()]
                for c in ("qtde", "valor_total", "base", "pis", "cofins"):
                    t[c] = t[c].map(_f)
                out["resumo"] = t.reset_index(drop=True)
        elif "debitos sped" in n or n.startswith("debito"):
            r0 = next((i for i in range(len(raw)) if _sem_acento(raw.iat[i, 0]) == "descricao"), None)
            m = re.search(r"(\d{2})/(\d{4})", str(raw.iat[0, 0]))
            if m:
                competencias.append(f"{m.group(1)}/{m.group(2)}")
            if r0 is not None:
                t = raw.iloc[r0 + 1:, :3].copy()
                t.columns = ["descricao", "pis", "cofins"]
                t = t[t["descricao"].notna() & ~t["descricao"].astype(str).str.lower().str.startswith("nota")]
                t["pis"] = t["pis"].map(_f)
                t["cofins"] = t["cofins"].map(_f)
                out["debitos"] = t.reset_index(drop=True)
        elif "reconcilia" in n:
            r0 = next((i for i in range(len(raw)) if _sem_acento(raw.iat[i, 0]) == "grupo"), None)
            if r0 is not None:
                t = raw.iloc[r0 + 1:, :5].copy()
                t.columns = ["grupo", "base_planilha", "base_sped", "diferenca", "status"]
                t = t[t["grupo"].notna()].copy()
                for c in ("base_planilha", "base_sped", "diferenca"):
                    t[c] = t[c].map(_f)
                out["reconciliacao"] = t.reset_index(drop=True)
    if not linhas and out["debitos"].empty:
        out["erro"] = ("Não reconheci este arquivo como o Detalhamento de Débito e Crédito PIS/COFINS "
                       "(esperava abas por categoria com a coluna \"CNPJ / CPF / CNO\").")
        return out
    out["linhas"] = pd.DataFrame(linhas, columns=COLUNAS_LINHA)
    out["competencia"] = pd.Series(competencias).mode().iat[0] if competencias else _competencia_do_nome(nome_arquivo)
    return out


def _competencia_do_nome(nome: str) -> Optional[str]:
    m = re.search(r"(\d{2})[.\-_/](\d{4})", nome or "") or None
    return f"{m.group(1)}/{m.group(2)}" if m else None
