# -*- coding: utf-8 -*-
"""
EGC | Creditos PIS/COFINS -- leitura contabil do dashboard: SUGESTOES, comparativos e ponte.  (v0.52.0, 16/10/2026)

Funcoes PURAS (sem Streamlit, sem banco). Principio combinado com o Rafael: o app AJUDA A ORGANIZAR e GERIR, nunca trava nem manda.
Tudo aqui sao SUGESTOES ("vale conferir") -- a contadora decide. Nenhuma regra bloqueia envio, edicao, troca de versao ou geracao do PDF.

Trabalham sobre a serie ja preparada por `creditos_graficos.preparar_serie` (colunas *_total, rotulo, ...) e sobre o `resumo` / `por_categoria`
guardados em cada versao do SPED.
"""
from __future__ import annotations

import pandas as pd

import formatacao

# limites das sugestoes (ajustaveis; so' decidem quando MOSTRAR uma dica)
LIM_VARIACAO = 0.30        # credito do mes x media dos meses anteriores
LIM_RAZAO = 0.30           # credito / receita nao cumulativa x media dos meses anteriores
LIM_DIF_ORIGINAL = 0.05    # versao em uso x SPED original
LIM_CONCENTRACAO = 0.60    # uma categoria com mais de 60% do credito
MIN_MESES_MEDIA = 3        # historico minimo para falar em "media dos meses anteriores"

_ITENS_RESUMO = [("Crédito apurado no mês", "credito"), ("Débito não cumulativo", "debito_nc"), ("Débito cumulativo", "debito_cum"),
                 ("Crédito utilizado", "usado"), ("Retenção na fonte", "retencao_nc"), ("Saldo de crédito a transportar", "saldo"),
                 ("Total a recolher", "a_recolher")]


def _f(v) -> float:
    try:
        x = float(v)
        return 0.0 if x != x else x
    except (TypeError, ValueError):
        return 0.0


def _por_tributo(e: dict | None, tributo: str) -> float:
    e = e or {}
    return _f(e.get("pis")) if tributo == "pis" else _f(e.get("cofins")) if tributo == "cofins" else _f(e.get("pis")) + _f(e.get("cofins"))


def _resumo_item(resumo: dict, item: str, tributo: str) -> float:
    r = resumo or {}

    def v(trib: str) -> float:
        if item == "usado":
            return _f(r.get(f"credito_usado_{trib}")) + _f(r.get(f"credito_anterior_{trib}"))
        return _f(r.get(f"{item}_{trib}"))
    return v("pis") if tributo == "pis" else v("cofins") if tributo == "cofins" else v("pis") + v("cofins")


# ─────────────────────────────────────────────
#  COMPOSICAO E VARIACAO POR CATEGORIA
# ─────────────────────────────────────────────
def composicao(por_cat: dict, tributo: str = "total") -> pd.DataFrame:
    """Credito por categoria no mes com % do total: colunas categoria, valor, pct (0-1). Maior primeiro."""
    itens = [(c, _por_tributo(e, tributo)) for c, e in (por_cat or {}).items()]
    tot = sum(v for _c, v in itens)
    df = pd.DataFrame([{"categoria": c, "valor": v, "pct": (v / tot if tot else 0.0)} for c, v in itens], columns=["categoria", "valor", "pct"])
    return df.sort_values("valor", ascending=False).reset_index(drop=True)


def variacao_categorias(atual: dict, anterior: dict, tributo: str = "total") -> pd.DataFrame:
    """Categoria a categoria: credito no mes, no mes anterior, diferenca e % (None quando o anterior e' zero). Maior mudanca (em modulo) primeiro."""
    cats = list(dict.fromkeys(list((atual or {}).keys()) + list((anterior or {}).keys())))
    linhas = []
    for c in cats:
        a, b = _por_tributo((atual or {}).get(c), tributo), _por_tributo((anterior or {}).get(c), tributo)
        linhas.append({"categoria": c, "atual": a, "anterior": b, "dif": a - b, "pct": ((a - b) / b) if b else None})
    df = pd.DataFrame(linhas, columns=["categoria", "atual", "anterior", "dif", "pct"])
    if df.empty:
        return df
    return df.reindex(df["dif"].abs().sort_values(ascending=False).index).reset_index(drop=True)


# ─────────────────────────────────────────────
#  COMPARAR DUAS VERSOES (fontes) DO MESMO MES
# ─────────────────────────────────────────────
def comparar_fontes(fa: dict, fb: dict, tributo: str = "total") -> pd.DataFrame:
    """Compara duas fontes (de creditos_db.fontes_por_mes): primeiro a apuracao, depois o credito por categoria.
    Colunas: grupo ('Apuração'|'Categoria'), item, a, b, dif (b - a), pct (dif / a, None se a = 0)."""
    linhas = []
    for rot, chave in _ITENS_RESUMO:
        a, b = _resumo_item(fa.get("resumo"), chave, tributo), _resumo_item(fb.get("resumo"), chave, tributo)
        linhas.append({"grupo": "Apuração", "item": rot, "a": a, "b": b, "dif": b - a, "pct": ((b - a) / a) if a else None})
    pa, pb = fa.get("por_categoria") or {}, fb.get("por_categoria") or {}
    for c in dict.fromkeys(list(pa) + list(pb)):
        a, b = _por_tributo(pa.get(c), tributo), _por_tributo(pb.get(c), tributo)
        linhas.append({"grupo": "Categoria", "item": c, "a": a, "b": b, "dif": b - a, "pct": ((b - a) / a) if a else None})
    return pd.DataFrame(linhas, columns=["grupo", "item", "a", "b", "dif", "pct"])


def comparar_sped_detalhamento(por_cat_sped: dict, por_cat_det: dict, tributo: str = "total") -> pd.DataFrame:
    """Credito por categoria no SPED x no Detalhamento da contadora (colunas categoria, sped, det, dif = det - sped). Depreciacao/imobilizado nao tem nota."""
    cats = list(dict.fromkeys(list(por_cat_sped or {}) + list(por_cat_det or {})))
    df = pd.DataFrame([{"categoria": c, "sped": _por_tributo((por_cat_sped or {}).get(c), tributo), "det": _por_tributo((por_cat_det or {}).get(c), tributo)}
                       for c in cats], columns=["categoria", "sped", "det"])
    df["dif"] = df["det"] - df["sped"]
    return df


# ─────────────────────────────────────────────
#  PONTE (waterfall): do cenario oficial para o simulado
# ─────────────────────────────────────────────
def ponte(base: pd.DataFrame, cenario: pd.DataFrame, coluna: str = "cred_total") -> dict:
    """Quanto cada mes muda o total do periodo quando se troca a versao usada. base e cenario = series preparadas (mesmos meses).
    Devolve {'inicio': total do base, 'fim': total do cenario, 'itens': [(rotulo_do_mes, delta)]} so' com os meses que mudaram."""
    if base is None or base.empty or cenario is None or cenario.empty:
        return {"inicio": 0.0, "fim": 0.0, "itens": []}
    a = dict(zip(base["rotulo"], base[coluna]))
    b = dict(zip(cenario["rotulo"], cenario[coluna]))
    itens = []
    for rot in list(dict.fromkeys(list(a) + list(b))):
        delta = _f(b.get(rot)) - _f(a.get(rot))
        if abs(delta) >= 0.005:
            itens.append((rot, delta))
    return {"inicio": sum(_f(v) for v in a.values()), "fim": sum(_f(v) for v in b.values()), "itens": itens}


# ─────────────────────────────────────────────
#  SUGESTOES
# ─────────────────────────────────────────────
def _dica(nivel: str, titulo: str, texto: str) -> dict:
    return {"nivel": nivel, "titulo": titulo, "texto": texto}


def sugestoes_mes(d: pd.DataFrame, i: int, tributo: str = "total") -> list:
    """Sugestoes para o mes na posicao `i` da serie preparada `d`. Cada uma = {'nivel': 'atencao'|'info', 'titulo', 'texto'}.
    Nunca devolve algo que impeca uma acao: e' so' texto para a contadora decidir."""
    if d is None or d.empty or not (0 <= i < len(d)):
        return []
    r = d.iloc[i]
    ant = d.iloc[:i]
    out: list = []
    mes = r["rotulo"]

    # 1) credito do mes x media dos meses anteriores (+ quem mais mudou)
    prev = ant["cred_total"].tail(6)
    if len(prev) >= MIN_MESES_MEDIA and prev.mean() > 0:
        media = float(prev.mean())
        var = (float(r["cred_total"]) - media) / media
        if abs(var) > LIM_VARIACAO:
            txt = (f"O crédito de {mes} ({formatacao.moeda_br(r['cred_total'])}) está {formatacao.pct_br(abs(var), casas=0)} "
                   f"{'acima' if var > 0 else 'abaixo'} da média dos {len(prev)} meses anteriores ({formatacao.moeda_br(media)}).")
            if i >= 1:
                vc = variacao_categorias(r["por_categoria"], d.iloc[i - 1]["por_categoria"], tributo)
                if not vc.empty and abs(vc.iloc[0]["dif"]) >= 0.01:
                    txt += f" A categoria que mais mudou em relação ao mês anterior foi {vc.iloc[0]['categoria']} ({formatacao.moeda_br(vc.iloc[0]['dif'], forcar_sinal=True)})."
            out.append(_dica("atencao", "Crédito fora do padrão dos últimos meses", txt + " Vale conferir se faz sentido."))

    # 2) credito sobre a receita nao cumulativa (razoabilidade)
    if _f(r["receita_nc"]) > 0 and len(ant) >= MIN_MESES_MEDIA:
        razoes = [(_f(c) / _f(rc)) for c, rc in zip(ant["cred_total"], ant["receita_nc"]) if _f(rc) > 0]
        if len(razoes) >= MIN_MESES_MEDIA:
            media_r = sum(razoes) / len(razoes)
            atual_r = _f(r["cred_total"]) / _f(r["receita_nc"])
            if media_r > 0 and abs(atual_r - media_r) / media_r > LIM_RAZAO:
                out.append(_dica("info", "Crédito em relação à receita",
                                 f"Em {mes} o crédito equivale a {formatacao.pct_br(atual_r)} da receita não cumulativa; nos meses anteriores ficou em torno de "
                                 f"{formatacao.pct_br(media_r)}. Pode ser só mudança de mix de compras — vale um olhar."))

    # 3) saldo de credito do mes anterior sem uso
    if i >= 1:
        p = d.iloc[i - 1]
        anterior_usado = _f(r["anterior_pis"]) if tributo == "pis" else _f(r["anterior_cofins"]) if tributo == "cofins" else _f(r["anterior_pis"]) + _f(r["anterior_cofins"])
        if _f(p["saldo_total"]) > 0.5 and anterior_usado < 0.005 and _f(r["recolher_total"]) > 0.5:
            out.append(_dica("atencao", "Saldo de crédito do mês anterior sem uso",
                             f"Ficaram {formatacao.moeda_br(p['saldo_total'])} de saldo de crédito em {p['rotulo']} e {mes} tem "
                             f"{formatacao.moeda_br(r['recolher_total'])} a recolher, sem uso de crédito de meses anteriores. Vale conferir se o saldo pode ser aproveitado."))

    # 4) credito utilizado maior que o debito nao cumulativo
    if _f(r["usado_total"]) > _f(r["deb_nc_total"]) * 1.01 + 1:
        out.append(_dica("atencao", "Crédito utilizado acima do débito não cumulativo",
                         f"Em {mes} o crédito utilizado ({formatacao.moeda_br(r['usado_total'])}) passa do débito não cumulativo "
                         f"({formatacao.moeda_br(r['deb_nc_total'])}). Pode haver compensação com outros tributos — vale conferir na apuração."))

    # 5) versao em uso x SPED original
    if bool(r["tem_original"]) and r["dif_total"] is not None and not pd.isna(r["dif_total"]):
        base = _f(r["orig_total"])
        if abs(_f(r["dif_total"])) >= 0.01 and (base == 0 or abs(_f(r["dif_total"])) / abs(base) > LIM_DIF_ORIGINAL):
            out.append(_dica("info", "Versão em uso difere do SPED original",
                             f"A versão usada em {mes} tem {formatacao.moeda_br(r['dif_total'], forcar_sinal=True)} de crédito em relação ao SPED original"
                             + (f" ({formatacao.pct_br(_f(r['dif_total']) / base, forcar_sinal=True)})" if base else "")
                             + ". Se quiser, registre o motivo na nota do mês — fica no relatório."))

    # 6) concentracao em uma categoria
    comp = composicao(r["por_categoria"], tributo)
    if not comp.empty and comp.iloc[0]["pct"] > LIM_CONCENTRACAO and len(comp) > 1:
        out.append(_dica("info", "Crédito concentrado em uma categoria",
                         f"{comp.iloc[0]['categoria']} responde por {formatacao.pct_br(comp.iloc[0]['pct'], casas=0)} do crédito de {mes}."))

    # 7) varias versoes e nenhuma oficial marcada / simulacao em curso
    modo = str(r.get("modo", "padrao"))
    if modo == "simulada":
        out.append(_dica("info", "Você está vendo uma simulação",
                         f"Em {mes} os números vêm de outra versão que não é a oficial/padrão. Nada foi gravado; o relatório em PDF avisa que não é a versão oficial."))
    elif int(r.get("qtd_fontes", 1) or 1) > 1 and modo == "padrao":
        out.append(_dica("info", "Este mês tem mais de uma versão",
                         f"{mes} tem {int(r['qtd_fontes'])} versões. Está valendo a regra padrão (a apontada). Se preferir fixar uma, é só marcar como oficial."))

    # 8) mes ainda nao marcado como conferido (so' lembrete)
    if not bool(r.get("conferido", False)):
        out.append(_dica("info", "Mês ainda não marcado como conferido",
                         f"Quando terminar a conferência de {mes}, você pode marcar como conferido (é só uma marcação para organização)."))
    return out


def resumo_variacao(d: pd.DataFrame, i: int) -> dict:
    """Numeros de apoio para o relatorio: mes, mes anterior e variacao (pode nao haver anterior)."""
    if d is None or d.empty or not (0 <= i < len(d)):
        return {}
    r = d.iloc[i]
    p = d.iloc[i - 1] if i >= 1 else None
    out = {"mes": r["rotulo"], "mes_anterior": None if p is None else p["rotulo"]}
    for col in ("cred_total", "deb_nc_total", "deb_cum_total", "usado_total", "ret_total", "saldo_total", "recolher_total"):
        a = _f(r[col])
        b = None if p is None else _f(p[col])
        out[col] = {"atual": a, "anterior": b, "dif": None if b is None else a - b, "pct": None if not b else (a - b) / b}
    return out
