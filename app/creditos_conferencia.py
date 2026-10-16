# -*- coding: utf-8 -*-
"""
EGC | Creditos PIS/COFINS -- conferencia do SPED (EFD-Contribuicoes) x Detalhamento da contadora.  (v0.50.0, 16/10/2026)

Tudo aqui e' PURO (sem banco, sem tela): recebe os dicionarios de `sped_contribuicoes.ler_sped` e `creditos_detalhamento.ler_detalhamento`
e devolve tabelas prontas. Validado em 16/10/2026 com a Energia jul/2026: 627/627 documentos do SPED original casam com o Detalhamento sem
diferenca de valor; as 8 categorias fecham PIS e COFINS ao centavo.

Quatro conferencias:
  1. LINHAS      -- cada documento creditado no SPED x a(s) linha(s) do Detalhamento (chave CNPJ + numero). Compras de mercadoria consolidadas
                    (C191/C195, sem numero) sao conferidas por fornecedor. Depreciacao/imobilizado (F120/F130) por bem, sem nota.
  2. CATEGORIAS  -- credito PIS/COFINS por categoria (SPED x Detalhamento) e bases por natureza (M105/M505 x aba "Reconciliação").
  3. TOTAIS      -- credito do Bloco M (M100/M500) x soma do Detalhamento, com a parte explicada por depreciacao/imobilizado.
  4. DEBITOS     -- aba "Débitos SPED" do Detalhamento x apuracao do SPED (M100/M200/M205/M500/M600/M605).

As tabelas trazem colunas EDITAVEIS (decisao do Rafael, 16/10/2026: "tudo editavel, pra nao depender do app errar") -- os valores editados
sao guardados como ajustes (creditos_db) e reaplicados por `aplicar_ajustes`, que recalcula diferenca e status.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

import pandas as pd

import formatacao

TOL_DOC = 0.05          # diferenca aceita por documento (arredondamento por linha)
TOL_AGREGADO = 1.00     # diferenca aceita em consolidados/categorias (soma de varios arredondamentos)

S_OK = "Conferido"
S_DIF = "Valor diferente"
S_SO_SPED = "Só no SPED"
S_SO_DET = "Só no Detalhamento"
S_ESPERADO = "Esperado (sem nota)"
S_NUM = "Nº diferente"
S_MANUAL = "Linha manual"
STATUS_LINHAS = [S_OK, S_NUM, S_DIF, S_SO_SPED, S_SO_DET, S_ESPERADO, S_MANUAL]

CATEGORIA_SPED_PARA_DET = {"Demais Produtos (energia)": "Demais Produtos", "Imobilizado (aquisição)": "Imobilizados"}
CATEGORIA_DET_PARA_SPED = {v: k for k, v in CATEGORIA_SPED_PARA_DET.items()}
SERVICOS_NUMERADOS = {"A170", "C170", "C501", "D101"}

COLUNAS_LINHAS = ["chave", "nivel", "cnpj", "fornecedor", "numero", "categoria", "pis_sped", "cofins_sped", "pis_det", "cofins_det",
                  "dif_pis", "dif_cofins", "status", "observacao", "nota_contadora", "conferido"]


def _r2(v) -> float:
    return round(float(v or 0), 2)


def _sem_acento(s) -> str:
    t = unicodedata.normalize("NFKD", str(s if s is not None else ""))
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip().lower()


# ─────────────────────────────────────────────
#  1. LINHAS
# ─────────────────────────────────────────────
def _status(pis_s, cof_s, pis_d, cof_d, tol, existe_sped, existe_det) -> str:
    if existe_sped and not existe_det:
        return S_SO_SPED
    if existe_det and not existe_sped:
        return S_SO_DET
    if abs(_r2(pis_s) - _r2(pis_d)) <= tol + 1e-9 and abs(_r2(cof_s) - _r2(cof_d)) <= tol + 1e-9:
        return S_OK
    return S_DIF


def conferir_linhas(sped: dict, det: dict) -> pd.DataFrame:
    """Tabela de conferencia linha a linha (ver modulo). Colunas: COLUNAS_LINHAS."""
    cred = sped.get("creditos", pd.DataFrame()).copy()
    dl = det.get("linhas", pd.DataFrame()).copy()
    linhas: list = []
    if dl.empty:
        dl = pd.DataFrame(columns=["categoria", "cnpj", "fornecedor", "numero", "pis", "cofins", "produto", "especie"])
    com_cred = dl[(dl["pis"].abs() + dl["cofins"].abs()) > 0]
    sem_cred = dl[(dl["pis"].abs() + dl["cofins"].abs()) <= 0]
    det_idx = {k: g for k, g in com_cred.groupby(["cnpj", "numero"])}
    det_zero = {k for k in zip(sem_cred["cnpj"], sem_cred["numero"])}
    usados = set()

    # --- documentos numerados do SPED
    num = cred[cred["registro"].isin(SERVICOS_NUMERADOS) & (cred["numero"] != "")]
    for (cnpj, numero), g in num.groupby(["cnpj", "numero"], sort=False):
        d = det_idx.get((cnpj, numero))
        existe_d = d is not None
        if existe_d:
            usados.add((cnpj, numero))
        ps, cs = _r2(g["pis"].sum()), _r2(g["cofins"].sum())
        pd_, cd = (_r2(d["pis"].sum()), _r2(d["cofins"].sum())) if existe_d else (0.0, 0.0)
        st = _status(ps, cs, pd_, cd, TOL_DOC, True, existe_d)
        obs = ""
        if st == S_SO_SPED:
            obs = ("No Detalhamento esta nota existe SEM crédito." if (cnpj, numero) in det_zero
                   else "Esta nota não aparece no Detalhamento.")
        elif st == S_DIF:
            obs = "Valores diferentes entre o SPED e o Detalhamento."
        linhas.append(_linha(f"{cnpj}|{numero}", "Documento", cnpj, g["fornecedor"].iloc[0], numero, g["categoria"].iloc[0], ps, cs, pd_, cd, st, obs))

    # --- consolidados (C191/C195): por fornecedor
    cons = cred[cred["registro"].eq("C191")]
    if not cons.empty:
        # Detalhamento de NFE do mesmo fornecedor que nao casou por numero (categorias de mercadoria)
        resto = com_cred[~com_cred.apply(lambda r: (r["cnpj"], r["numero"]) in usados, axis=1)]
        for cnpj, g in cons.groupby("cnpj", sort=False):
            d = resto[resto["cnpj"] == cnpj]
            existe_d = not d.empty
            ps, cs = _r2(g["pis"].sum()), _r2(g["cofins"].sum())
            pd_, cd = (_r2(d["pis"].sum()), _r2(d["cofins"].sum())) if existe_d else (0.0, 0.0)
            st = _status(ps, cs, pd_, cd, TOL_AGREGADO, True, existe_d)
            cats = ", ".join(sorted(set(g["categoria"])))
            obs = "Compra consolidada no SPED (sem nº de nota): conferida no total do fornecedor."
            if st == S_SO_SPED:
                obs += " O fornecedor não tem crédito no Detalhamento."
            linhas.append(_linha(f"{cnpj}|*", "Fornecedor (consolidado)", cnpj, g["fornecedor"].iloc[0], "—", cats, ps, cs, pd_, cd, st, obs))
            for k in zip(d["cnpj"], d["numero"]):
                usados.add(k)

    # --- bens sem nota (F120/F130)
    for reg, nome in (("F120", "Depreciação (F120)"), ("F130", "Imobilizado (aquisição)")):
        g = cred[cred["registro"].eq(reg)]
        if g.empty:
            continue
        if reg == "F120":
            linhas.append(_linha("F120|total", "Bens (sem nota)", "", "Depreciação de bens do ativo", "—", nome, _r2(g["pis"].sum()), _r2(g["cofins"].sum()),
                                 0.0, 0.0, S_ESPERADO, "Crédito de depreciação do SPED: não vem de nota, por isso não aparece no Detalhamento."))
        else:
            for _, x in g.iterrows():
                d_cat = dl[(dl["categoria"] == "Imobilizados") & ((dl["pis"].abs() + dl["cofins"].abs()) > 0)]
                linhas.append(_linha(x["chave"], "Bens (sem nota)", "", str(x["descricao"])[:70], "—", nome, _r2(x["pis"]), _r2(x["cofins"]),
                                     0.0, 0.0, S_SO_SPED if d_cat.empty else S_ESPERADO,
                                     "Aquisição de bem do ativo creditada no SPED; no Detalhamento a categoria Imobilizados não tem crédito."))

    # --- so' no Detalhamento (com credito)
    for (cnpj, numero), g in det_idx.items():
        if (cnpj, numero) in usados:
            continue
        linhas.append(_linha(f"{cnpj}|{numero}", "Documento", cnpj, g["fornecedor"].iloc[0], numero, g["categoria"].iloc[0], 0.0, 0.0,
                             _r2(g["pis"].sum()), _r2(g["cofins"].sum()), S_SO_DET, "Tem crédito no Detalhamento e não tem no SPED."))
    linhas = _casar_por_valor(linhas)
    out = pd.DataFrame(linhas, columns=COLUNAS_LINHAS)
    if out.empty:
        return out
    ordem = {s: i for i, s in enumerate([S_DIF, S_NUM, S_SO_SPED, S_SO_DET, S_ESPERADO, S_OK, S_MANUAL])}
    out["_o"] = out["status"].map(ordem)
    out = out.sort_values(["_o", "categoria", "fornecedor", "numero"], kind="stable").drop(columns="_o").reset_index(drop=True)
    return out


def _casar_por_valor(linhas: list) -> list:
    """Segunda passada: 'Só no SPED' e 'Só no Detalhamento' do MESMO fornecedor e com o MESMO valor (PIS e COFINS) sao a mesma nota com numero
    escrito diferente (ex.: energia eletrica, onde o SPED traz o numero da fatura e a escrituracao o da nota). Casa 1 para 1."""
    so_s = [x for x in linhas if x["status"] == S_SO_SPED and x["nivel"] == "Documento"]
    so_d = [x for x in linhas if x["status"] == S_SO_DET and x["nivel"] == "Documento"]
    usados_d, removidos = set(), set()
    for s in so_s:
        for i, d in enumerate(so_d):
            if i in usados_d or d["cnpj"] != s["cnpj"]:
                continue
            if abs(s["pis_sped"] - d["pis_det"]) <= TOL_DOC + 1e-9 and abs(s["cofins_sped"] - d["cofins_det"]) <= TOL_DOC + 1e-9:
                usados_d.add(i)
                removidos.add(id(d))
                s["pis_det"], s["cofins_det"] = d["pis_det"], d["cofins_det"]
                s["dif_pis"], s["dif_cofins"] = _r2(s["pis_sped"] - d["pis_det"]), _r2(s["cofins_sped"] - d["cofins_det"])
                s["status"] = S_NUM
                s["observacao"] = f"Mesmo fornecedor e mesmo valor; número diferente: SPED {s['numero']} × Detalhamento {d['numero']}."
                s["numero"] = f"{s['numero']} / {d['numero']}"
                break
    return [x for x in linhas if id(x) not in removidos]


def _linha(chave, nivel, cnpj, fornecedor, numero, categoria, ps, cs, pdt, cdt, status, obs) -> dict:
    return {"chave": chave, "nivel": nivel, "cnpj": cnpj, "fornecedor": fornecedor, "numero": numero, "categoria": categoria,
            "pis_sped": ps, "cofins_sped": cs, "pis_det": pdt, "cofins_det": cdt, "dif_pis": _r2(ps - pdt), "dif_cofins": _r2(cs - cdt),
            "status": status, "observacao": obs, "nota_contadora": "", "conferido": False}


def recalcular_linhas(df: pd.DataFrame) -> pd.DataFrame:
    """Depois de uma edicao: recalcula diferencas e o status automatico (menos 'Linha manual' e 'Esperado (sem nota)')."""
    if df.empty:
        return df
    d = df.copy()
    for c in ("pis_sped", "cofins_sped", "pis_det", "cofins_det"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0).round(2)
    d["dif_pis"] = (d["pis_sped"] - d["pis_det"]).round(2)
    d["dif_cofins"] = (d["cofins_sped"] - d["cofins_det"]).round(2)

    def novo(r):
        if r["status"] in (S_MANUAL, S_ESPERADO):
            return r["status"]
        if r["status"] == S_NUM and abs(r["dif_pis"]) <= TOL_DOC and abs(r["dif_cofins"]) <= TOL_DOC:
            return S_NUM
        tol = TOL_AGREGADO if str(r["nivel"]).startswith("Fornecedor") else TOL_DOC
        s_ex = (r["pis_sped"] != 0) or (r["cofins_sped"] != 0)
        d_ex = (r["pis_det"] != 0) or (r["cofins_det"] != 0)
        if not s_ex and not d_ex:
            return S_OK
        return _status(r["pis_sped"], r["cofins_sped"], r["pis_det"], r["cofins_det"], tol, s_ex, d_ex)
    d["status"] = d.apply(novo, axis=1)
    return d


def resumo_linhas(df: pd.DataFrame) -> pd.DataFrame:
    """Contagem e valores por status (para KPIs e relatorio)."""
    if df.empty:
        return pd.DataFrame(columns=["status", "linhas", "pis_sped", "cofins_sped", "pis_det", "cofins_det"])
    g = df.groupby("status").agg(linhas=("chave", "count"), pis_sped=("pis_sped", "sum"), cofins_sped=("cofins_sped", "sum"),
                                 pis_det=("pis_det", "sum"), cofins_det=("cofins_det", "sum")).round(2).reset_index()
    g["_o"] = g["status"].map({s: i for i, s in enumerate(STATUS_LINHAS)})
    return g.sort_values("_o").drop(columns="_o").reset_index(drop=True)


# ─────────────────────────────────────────────
#  2. CATEGORIAS
# ─────────────────────────────────────────────
def conferir_categorias(sped: dict, det: dict) -> pd.DataFrame:
    """Credito PIS/COFINS por categoria: SPED x Detalhamento."""
    cred = sped.get("creditos", pd.DataFrame())
    dl = det.get("linhas", pd.DataFrame())
    s = cred.assign(cat=cred["categoria"].map(lambda c: CATEGORIA_SPED_PARA_DET.get(c, c))).groupby("cat").agg(
        pis_sped=("pis", "sum"), cofins_sped=("cofins", "sum")) if not cred.empty else pd.DataFrame(columns=["pis_sped", "cofins_sped"])
    d = dl.groupby("categoria").agg(pis_det=("pis", "sum"), cofins_det=("cofins", "sum"), linhas_det=("pis", "count")) \
        if not dl.empty else pd.DataFrame(columns=["pis_det", "cofins_det", "linhas_det"])
    t = s.join(d, how="outer").fillna(0.0)
    t.index.name = "categoria"
    t = t.reset_index()
    t["dif_pis"] = (t["pis_sped"] - t["pis_det"]).round(2)
    t["dif_cofins"] = (t["cofins_sped"] - t["cofins_det"]).round(2)

    def st(r):
        if abs(r["dif_pis"]) <= TOL_AGREGADO and abs(r["dif_cofins"]) <= TOL_AGREGADO:
            return S_OK
        if r["categoria"] == "Depreciação (F120)":
            return S_ESPERADO
        return S_DIF
    t["status"] = t.apply(st, axis=1)
    t["nota_contadora"] = ""
    for c in ("pis_sped", "cofins_sped", "pis_det", "cofins_det"):
        t[c] = t[c].round(2)
    ordem = {c: i for i, c in enumerate(["Combustível", "Uso ou Consumo", "Imobilizados", "Fretes", "Serviços Tomados", "Locação de Máquinas",
                                          "Aluguel", "Demais Produtos", "Depreciação (F120)"])}
    t["_o"] = t["categoria"].map(lambda c: ordem.get(c, 99))
    return t.sort_values("_o").drop(columns="_o").reset_index(drop=True)


GRUPOS_BASE = [   # (rotulo, naturezas SPED, categorias do Detalhamento)
    ("Uso ou Consumo + Combustível (NAT 02)", ("02",), ("Combustível", "Uso ou Consumo")),
    ("Energia elétrica (NAT 04)", ("04",), ("Demais Produtos",)),
    ("Aluguel (NAT 05)", ("05",), ("Aluguel",)),
    ("Locação de máquinas (NAT 06)", ("06",), ("Locação de Máquinas",)),
    ("Serviços Tomados + Fretes (NAT 03 e 07)", ("03", "07"), ("Serviços Tomados", "Fretes", "Imobilizados")),
    ("Depreciação / Imobilizado (NAT 09 e 10)", ("09", "10"), ()),
]


def conferir_bases(sped: dict, det: dict) -> pd.DataFrame:
    """Base de calculo por natureza: SPED (M105 = base do credito de PIS) x soma da base do Detalhamento por grupo."""
    from sped_contribuicoes import bases_por_natureza
    b = bases_por_natureza(sped.get("apuracao", {}))
    dl = det.get("linhas", pd.DataFrame())
    cred = sped.get("creditos", pd.DataFrame())
    linhas = []
    for rot, nats, cats in GRUPOS_BASE:
        bs = float(b[b["nat_bc"].isin(nats)]["base"].sum()) if not b.empty else 0.0
        bd = float(dl[dl["categoria"].isin(cats)]["base"].sum()) if (not dl.empty and cats) else 0.0
        dif = round(bs - bd, 2)
        pis_b = float(b[b["nat_bc"].isin(nats)]["pis"].sum()) if not b.empty else 0.0
        cof_b = float(b[b["nat_bc"].isin(nats)]["cofins"].sum()) if not b.empty else 0.0
        cs = [CATEGORIA_DET_PARA_SPED.get(c, c) for c in cats] + (["Depreciação (F120)", "Imobilizado (aquisição)"] if not cats else [])
        sub = cred[cred["categoria"].isin(cs)] if not cred.empty else cred
        pis_l = float(sub["pis"].sum()) if not sub.empty else 0.0
        cof_l = float(sub["cofins"].sum()) if not sub.empty else 0.0
        linhas.append({"grupo": rot, "base_sped": round(bs, 2), "base_det": round(bd, 2), "dif": dif,
                       "status": S_OK if abs(dif) <= TOL_AGREGADO else (S_ESPERADO if not cats else S_DIF),
                       "pis_pela_base": round(pis_b, 2), "pis_linhas": round(pis_l, 2), "dif_interna_pis": round(pis_b - pis_l, 2),
                       "cofins_pela_base": round(cof_b, 2), "cofins_linhas": round(cof_l, 2), "dif_interna_cofins": round(cof_b - cof_l, 2)})
    t = pd.DataFrame(linhas)
    t["nota_contadora"] = ""
    return t[(t["base_sped"] != 0) | (t["base_det"] != 0)].reset_index(drop=True)


# ─────────────────────────────────────────────
#  3. TOTAIS DE CREDITO
# ─────────────────────────────────────────────
def conferir_totais(sped: dict, det: dict) -> pd.DataFrame:
    """M100/M500 x soma do Detalhamento. Diferenca explicada = depreciacao (F120) + imobilizado (F130) do SPED sem linha no Detalhamento
    + inconsistencia interna do proprio SPED (base x aliquota do M105/M505 diferente da soma das linhas; ver conferir_bases)."""
    from sped_contribuicoes import resumo_apuracao
    ap = resumo_apuracao(sped.get("apuracao", {}))
    cred = sped.get("creditos", pd.DataFrame())
    dl = det.get("linhas", pd.DataFrame())
    try:
        bases = conferir_bases(sped, det)
    except Exception:
        bases = pd.DataFrame()
    linhas = []
    for nome, kcred, col in (("PIS", "credito_pis", "pis"), ("COFINS", "credito_cofins", "cofins")):
        m = ap[kcred]
        soma_doc = float(dl[col].sum()) if not dl.empty else 0.0
        expl = float(cred[cred["registro"].isin(["F120", "F130"])][col].sum()) if not cred.empty else 0.0
        interna = float(bases[f"dif_interna_{col}"].sum()) if (not bases.empty and f"dif_interna_{col}" in bases.columns) else 0.0
        res_bruto = round(m - soma_doc - expl, 2)
        res = round(res_bruto - interna, 2) + 0.0
        ok = abs(res) <= max(TOL_AGREGADO, 0.0005 * m)
        obs = ""
        if ok and abs(interna) > TOL_AGREGADO:
            obs = (f"A diferença de {formatacao.moeda_br(abs(res_bruto))} vem de uma inconsistência dentro do próprio SPED "
                   "(base x alíquota do Bloco M não bate com a soma das linhas).")
        linhas.append({"tributo": nome, "credito_bloco_m": round(m, 2), "soma_detalhamento": round(soma_doc, 2),
                       "depreciacao_imobilizado_sped": round(expl, 2), "inconsistencia_sped": round(interna, 2), "residuo": res,
                       "status": S_OK if ok else S_DIF, "observacao": obs, "nota_contadora": ""})
    return pd.DataFrame(linhas)


# ─────────────────────────────────────────────
#  4. DEBITOS (aba "Débitos SPED" do Detalhamento x apuracao do SPED)
# ─────────────────────────────────────────────
def _esperado_debitos(ap: dict) -> list:
    """(rotulo, prefixo normalizado do rotulo no Detalhamento, PIS, COFINS) -- a ordem e' a da aba."""
    m2, m6 = ap.get("m200", {}) or {}, ap.get("m600", {}) or {}
    m100 = sum(x["credito"] for x in ap.get("m100", []))
    m500 = sum(x["credito"] for x in ap.get("m500", []))
    s100 = sum(x["saldo"] for x in ap.get("m100", []))
    s500 = sum(x["saldo"] for x in ap.get("m500", []))
    ant2, ant6 = m2.get("credito_anterior", 0), m6.get("credito_anterior", 0)
    g = lambda d, k: float(d.get(k, 0) or 0)
    return [
        ("Crédito Não Cumulativo apurado no mês (M100/M500)", "credito nao cumulativo apurado", m100, m500),
        ("(+) Saldo de crédito de período anterior", "(+) saldo de credito", ant2, ant6),
        ("(=) Crédito Não Cumulativo disponível no mês", "(=) credito nao cumulativo disponivel", m100 + ant2, m500 + ant6),
        ("(-) Crédito utilizado no mês para abater débito NC", "(-) credito utilizado no mes", g(m2, "credito_descontado") + ant2, g(m6, "credito_descontado") + ant6),
        ("(=) Saldo de crédito a transportar", "(=) saldo de credito a transportar", s100, s500),
        ("Débito Não Cumulativo apurado no mês (M200/M600)", "debito nao cumulativo apurado", g(m2, "contribuicao_nc"), g(m6, "contribuicao_nc")),
        ("(=) Débito NC não coberto por crédito", "(=) debito nc nao coberto", g(m2, "nc_devida"), g(m6, "nc_devida")),
        ("(-) Retenção na fonte sobre o Não Cumulativo", "(-) retencao na fonte sobre o nao cumulativo", g(m2, "retencao_nc"), g(m6, "retencao_nc")),
        ("(=) Débito Não Cumulativo devido (após créditos e retenção)", "(=) debito nao cumulativo devido", g(m2, "nc_a_recolher"), g(m6, "nc_a_recolher")),
        ("Débito Cumulativo apurado no mês (M200/M600)", "debito cumulativo apurado", g(m2, "contribuicao_cum"), g(m6, "contribuicao_cum")),
        ("(-) Retenção na fonte sobre o Cumulativo", "(-) retencao na fonte sobre o cumulativo", g(m2, "retencao_cum"), g(m6, "retencao_cum")),
        ("(=) Débito Cumulativo a recolher (M205/M605)", "(=) debito cumulativo a recolher", g(m2, "cum_a_recolher"), g(m6, "cum_a_recolher")),
        ("TOTAL PIS/COFINS a recolher no mês (M200/M600)", "total pis/cofins a recolher", g(m2, "total_a_recolher"), g(m6, "total_a_recolher")),
    ]


def conferir_debitos(sped: dict, det: dict) -> pd.DataFrame:
    """Uma linha por item da apuracao: valor do SPED, valor da contadora (se ha' aba) e diferenca."""
    esperado = _esperado_debitos(sped.get("apuracao", {}))
    dd = det.get("debitos", pd.DataFrame())
    mapa = {}
    if not dd.empty:
        for _, r in dd.iterrows():
            mapa[_sem_acento(r["descricao"])] = (float(r["pis"]), float(r["cofins"]))
    linhas = []
    for rot, pref, ps, cs in esperado:
        achou = next((v for k, v in mapa.items() if k.startswith(pref)), None)
        pd_, cd = (achou if achou else (None, None))
        if achou is None:
            st, dp, dc = ("Sem aba no Detalhamento" if dd.empty else "Item não encontrado"), None, None
        else:
            dp, dc = round(ps - pd_, 2), round(cs - cd, 2)
            st = S_OK if abs(dp) <= TOL_AGREGADO and abs(dc) <= TOL_AGREGADO else S_DIF
        linhas.append({"item": rot, "pis_sped": round(ps, 2), "pis_det": pd_, "dif_pis": dp, "cofins_sped": round(cs, 2), "cofins_det": cd,
                       "dif_cofins": dc, "status": st, "nota_contadora": ""})
    return pd.DataFrame(linhas)


def recalcular_debitos(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    d = df.copy()
    for c in ("pis_det", "cofins_det"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["dif_pis"] = (d["pis_sped"] - d["pis_det"]).round(2)
    d["dif_cofins"] = (d["cofins_sped"] - d["cofins_det"]).round(2)

    def st(r):
        if pd.isna(r["pis_det"]) or pd.isna(r["cofins_det"]):
            return r["status"] if r["status"] not in (S_OK, S_DIF) else "Item não encontrado"
        return S_OK if abs(r["dif_pis"]) <= TOL_AGREGADO and abs(r["dif_cofins"]) <= TOL_AGREGADO else S_DIF
    d["status"] = d.apply(st, axis=1)
    return d


def recalcular_categorias(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    d = df.copy()
    for c in ("pis_sped", "cofins_sped", "pis_det", "cofins_det"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0).round(2)
    d["dif_pis"] = (d["pis_sped"] - d["pis_det"]).round(2)
    d["dif_cofins"] = (d["cofins_sped"] - d["cofins_det"]).round(2)
    d["status"] = [S_OK if abs(a) <= TOL_AGREGADO and abs(b) <= TOL_AGREGADO else (S_ESPERADO if c == "Depreciação (F120)" else S_DIF)
                   for a, b, c in zip(d["dif_pis"], d["dif_cofins"], d["categoria"])]
    return d


def recalcular_bases(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    d = df.copy()
    for c in ("base_sped", "base_det"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0).round(2)
    d["dif"] = (d["base_sped"] - d["base_det"]).round(2)
    esperado = d["grupo"].str.startswith("Depreciação")
    d["status"] = [S_OK if abs(x) <= TOL_AGREGADO else (S_ESPERADO if e else S_DIF) for x, e in zip(d["dif"], esperado)]
    return d


def recalcular_totais(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    d = df.copy()
    for c in ("credito_bloco_m", "soma_detalhamento", "depreciacao_imobilizado_sped", "inconsistencia_sped"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0).round(2)
    d["residuo"] = (d["credito_bloco_m"] - d["soma_detalhamento"] - d["depreciacao_imobilizado_sped"] - d["inconsistencia_sped"]).round(2) + 0.0
    d["status"] = [S_OK if abs(r) <= max(TOL_AGREGADO, 0.0005 * m) else S_DIF for r, m in zip(d["residuo"], d["credito_bloco_m"])]
    return d


# aba -> (coluna-chave, campos editaveis, funcao que recalcula diferencas/status)
ABAS = {
    "linhas": ("chave", ["fornecedor", "numero", "categoria", "pis_sped", "cofins_sped", "pis_det", "cofins_det", "status", "observacao",
                         "nota_contadora", "conferido"], recalcular_linhas),
    "categorias": ("categoria", ["pis_sped", "cofins_sped", "pis_det", "cofins_det", "nota_contadora"], recalcular_categorias),
    "bases": ("grupo", ["base_sped", "base_det", "nota_contadora"], recalcular_bases),
    "totais": ("tributo", ["credito_bloco_m", "soma_detalhamento", "depreciacao_imobilizado_sped", "inconsistencia_sped", "nota_contadora"],
               recalcular_totais),
    "debitos": ("item", ["pis_sped", "pis_det", "cofins_sped", "cofins_det", "nota_contadora"], None),
}


def aplicar_aba(aba: str, base: pd.DataFrame, ajustes: list) -> pd.DataFrame:
    """Tabela calculada + edicoes salvas da contadora, ja' recalculada. 'status' editado a mao vale por cima do automatico.
    Acrescenta a coluna `editado` (True nas linhas com alguma edicao ou manuais)."""
    chave, _campos, recalc = ABAS[aba]
    mine = [a for a in ajustes if a["aba"] == aba]
    sem_status = [a for a in mine if a["campo"] != "status"]
    d = aplicar_ajustes(base, sem_status, aba, coluna_chave=chave)
    if aba == "debitos":
        d = recalcular_debitos(d)
    elif recalc is not None:
        d = recalc(d)
    for a in mine:
        if a["campo"] == "status" and not d.empty:
            d.loc[d[chave] == a["chave"], "status"] = a["valor_novo"]
    if d.empty:
        d["editado"] = pd.Series([], dtype=bool)
        return d
    ed = {a["chave"] for a in mine if a["campo"] != "__excluir__"}
    d["editado"] = d[chave].isin(ed) | d["status"].eq(S_MANUAL)
    return d


# ─────────────────────────────────────────────
#  AJUSTES (edicoes da contadora) -- aplicados sobre as tabelas calculadas
# ─────────────────────────────────────────────
CAMPOS_NUMERICOS = {"pis_sped", "cofins_sped", "pis_det", "cofins_det", "base_sped", "base_det", "credito_bloco_m", "soma_detalhamento",
                    "depreciacao_imobilizado_sped", "inconsistencia_sped"}


def aplicar_ajustes(df: pd.DataFrame, ajustes: list, aba: str, coluna_chave: str = "chave") -> pd.DataFrame:
    """ajustes = [{'aba','chave','campo','valor_novo'}]. Campos '__excluir__' removem a linha; chave 'MANUAL|...' que nao existe na tabela
    base vira linha nova (status 'Linha manual'). Devolve a tabela com as edicoes aplicadas (sem recalcular -- chame recalcular_*)."""
    d = df.copy()
    mine = [a for a in ajustes if a["aba"] == aba]
    if not mine:
        return d
    por_chave: dict = {}
    for a in mine:
        por_chave.setdefault(a["chave"], {})[a["campo"]] = a["valor_novo"]
    for chave, campos in por_chave.items():
        existe = (d[coluna_chave] == chave) if not d.empty else pd.Series([], dtype=bool)
        if not existe.any():
            if not str(chave).startswith("MANUAL|"):
                continue
            nova = {c: (0.0 if c in CAMPOS_NUMERICOS or c.startswith(("dif", "pis", "cofins")) else "") for c in d.columns}
            nova.update({coluna_chave: chave, "status": S_MANUAL, "nivel": "Manual", "conferido": False})
            d = pd.concat([d, pd.DataFrame([nova])], ignore_index=True)
            existe = d[coluna_chave] == chave
        if campos.get("__excluir__") == "true":
            d = d[~existe].reset_index(drop=True)
            continue
        for campo, val in campos.items():
            if campo not in d.columns:
                continue
            if campo in CAMPOS_NUMERICOS:
                try:
                    val = float(str(val).replace(",", "."))
                except ValueError:
                    continue
            elif campo == "conferido":
                val = str(val).lower() in ("true", "1", "sim")
            d.loc[existe, campo] = val
    return d


def diferencas_edicao(base: pd.DataFrame, editado: pd.DataFrame, coluna_chave: str, campos: list) -> list:
    """Compara a tabela base (antes) com a que saiu do data_editor (depois). Devolve [{'chave','campo','valor_original','valor_novo'}]
    so' com o que mudou; linha que sumiu vira '__excluir__'."""
    mud: list = []
    b = base.set_index(coluna_chave) if not base.empty else pd.DataFrame()
    e = editado.set_index(coluna_chave) if not editado.empty else pd.DataFrame()
    for chave in b.index:
        if chave not in e.index:
            mud.append({"chave": chave, "campo": "__excluir__", "valor_original": "", "valor_novo": "true"})
            continue
        for c in campos:
            if c not in b.columns or c not in e.columns:
                continue
            x, y = b.at[chave, c], e.at[chave, c]
            if (pd.isna(x) and pd.isna(y)):
                continue
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not pd.isna(x) and not pd.isna(y):
                if abs(float(x) - float(y)) < 0.005:
                    continue
            elif str(x) == str(y):
                continue
            mud.append({"chave": chave, "campo": c, "valor_original": "" if pd.isna(x) else str(x), "valor_novo": "" if pd.isna(y) else str(y)})
    for chave in e.index:
        if chave not in b.index and str(chave).startswith("MANUAL|"):
            for c in campos:
                if c in e.columns and not pd.isna(e.at[chave, c]) and str(e.at[chave, c]) not in ("", "0", "0.0", "False"):
                    mud.append({"chave": chave, "campo": c, "valor_original": "", "valor_novo": str(e.at[chave, c])})
    return mud
