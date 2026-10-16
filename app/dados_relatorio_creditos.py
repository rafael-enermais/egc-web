# -*- coding: utf-8 -*-
"""
EGC | Relatorio de Creditos e Debitos de PIS/COFINS -- camada de DADOS (v0.53.0, 16/10/2026).

Funcao pura: recebe a serie mensal ja preparada (creditos_graficos.preparar_serie com tributo='total') e monta o dicionario `dados` que o
gerador_relatorio_creditos desenha. Sem Streamlit e sem banco -- quem busca e' a tela / creditos_db.

Regras combinadas com o Rafael (16/10/2026):
  * o relatorio sai da versao OFICIAL do mes (a marcada pela contadora ou, sem marcacao, a apontada). Se a tela estiver numa SIMULACAO
    (outra versao), o PDF ainda pode ser gerado -- nada trava -- mas leva o aviso "SIMULACAO - NAO E A VERSAO OFICIAL" em todas as paginas;
  * texto analitico = frase-modelo com numero real (neutro, sem adjetivo; comparacao vira numero com +/-), igual ao Demonstrativo Comentado;
  * os "pontos para acompanhar" (sugestoes) so' entram se a contadora pedir.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

import creditos_analise as an
import formatacao
from creditos_graficos import rotulo_comp

JANELA_PADRAO = 12                   # quantos meses (ate' o mes do relatorio) entram nos graficos e na tabela do historico
MODOS_NAO_OFICIAIS = ("simulada",)
_SUGESTOES_FORA = ("Mês ainda não marcado como conferido", "Você está vendo uma simulação", "Este mês tem mais de uma versão")


def _f(v) -> float:
    try:
        x = float(v)
        return 0.0 if x != x else x
    except (TypeError, ValueError):
        return 0.0


def _ou_none(v) -> Optional[float]:
    return None if v is None or (isinstance(v, float) and v != v) or pd.isna(v) else float(v)


def _linha_apuracao(r: pd.Series, p: Optional[pd.Series], rotulo: str, col_pis: str, col_cofins: str, usado: bool = False) -> dict:
    def v(row, trib):
        if row is None:
            return None
        if usado:
            return _f(row[f"usado_{trib}"]) + _f(row[f"anterior_{trib}"])
        return _f(row[f"{col_pis if trib == 'pis' else col_cofins}"])
    pis, cof = v(r, "pis"), v(r, "cofins")
    ant = None if p is None else v(p, "pis") + v(p, "cofins")
    tot = pis + cof
    return {"item": rotulo, "pis": pis, "cofins": cof, "total": tot, "anterior": ant, "dif": None if ant is None else tot - ant,
            "pct": None if not ant else (tot - ant) / ant}


def _leitura(d: dict) -> list:
    """Frases-modelo neutras (mesmo principio do Demonstrativo Comentado): numero real, comparacao com +/-, sem adjetivo."""
    br, pc = formatacao.moeda_br, formatacao.pct_br
    ap = {x["item"]: x for x in d["apuracao"]}
    cred, deb_nc, deb_cum = ap["Crédito apurado no mês"], ap["Débito não cumulativo"], ap["Débito cumulativo"]
    usado, ret, saldo, rec = ap["Crédito utilizado"], ap["Retenção na fonte (não cumulativo)"], ap["Saldo de crédito a transportar"], ap["Total a recolher"]
    f1 = f"Em {d['rotulo']}, o crédito de PIS/COFINS apurado foi de {br(cred['total'])} (PIS {br(cred['pis'])}; COFINS {br(cred['cofins'])})"
    if cred["anterior"] is not None:
        f1 += f", {pc(cred['pct'], forcar_sinal=True) if cred['pct'] is not None else 'sem base de comparação'} em relação a {d['rotulo_anterior']} ({br(cred['anterior'])})."
    else:
        f1 += ". Não há mês anterior no período para comparação."
    f2 = (f"Os débitos do mês somaram {br(deb_nc['total'] + deb_cum['total'])} (não cumulativo {br(deb_nc['total'])}; cumulativo {br(deb_cum['total'])}) "
          f"e o crédito utilizado foi de {br(usado['total'])}.")
    f3 = (f"O total a recolher foi de {br(rec['total'])}, com retenções na fonte de {br(ret['total'])}. "
          f"O saldo de crédito a transportar para o mês seguinte é de {br(saldo['total'])}.")
    out = [f1, f2, f3]
    if d.get("dif_original") is not None:
        out.append(f"A versão usada tem {br(d['dif_original'], forcar_sinal=True)} de crédito em relação ao SPED original do mês ({br(d['credito_original'])}).")
    return out


def montar_dados(serie: pd.DataFrame, competencia: str, *, empresa_codigo: str, empresa_nome: str, cnpj: str = "", controle_mes: Optional[dict] = None,
                 usuario: str = "", nome_contador: str = "", janela: int = JANELA_PADRAO, incluir_sugestoes: bool = False,
                 gerado_em: Optional[str] = None) -> dict:
    """Monta o dicionario do relatorio de um mes. `serie` = preparar_serie(..., 'total') da empresa (todos os meses disponiveis; so' os ate' `competencia` contam).
    Levanta ValueError se o mes nao esta na serie (nada para relatar)."""
    if serie is None or serie.empty or competencia not in set(serie["competencia"]):
        raise ValueError(f"Não há dados de {competencia} para gerar o relatório.")
    i_fim = int(serie.index[serie["competencia"] == competencia][0])
    jan = serie.iloc[max(0, i_fim - janela + 1): i_fim + 1].reset_index(drop=True)
    i = len(jan) - 1
    r = jan.iloc[i]
    p = jan.iloc[i - 1] if i >= 1 else None
    ctrl = controle_mes or {}
    nao_oficiais = [x["rotulo"] for _i, x in jan.iterrows() if str(x["modo"]) in MODOS_NAO_OFICIAIS]

    apuracao = [
        _linha_apuracao(r, p, "Crédito apurado no mês", "cred_pis", "cred_cofins"),
        _linha_apuracao(r, p, "Débito não cumulativo", "deb_nc_pis", "deb_nc_cofins"),
        _linha_apuracao(r, p, "Débito cumulativo", "deb_cum_pis", "deb_cum_cofins"),
        _linha_apuracao(r, p, "Crédito utilizado", "", "", usado=True),
        _linha_apuracao(r, p, "Retenção na fonte (não cumulativo)", "ret_pis", "ret_cofins"),
        _linha_apuracao(r, p, "Saldo de crédito a transportar", "saldo_pis", "saldo_cofins"),
        _linha_apuracao(r, p, "Total a recolher", "recolher_pis", "recolher_cofins"),
    ]
    comp = an.composicao(r["por_categoria"], "total")
    var = an.variacao_categorias(r["por_categoria"], p["por_categoria"], "total") if p is not None else pd.DataFrame(columns=["categoria", "atual", "anterior", "dif", "pct"])
    tem_orig = bool(r["tem_original"]) and _ou_none(r["dif_total"]) is not None

    meses = []
    for _i, x in jan.iterrows():
        meses.append({"competencia": x["competencia"], "rotulo": x["rotulo"], "modo": str(x["modo"]), "sped_tipo": x["sped_tipo"] or "",
                      "credito": _f(x["cred_total"]), "original": _ou_none(x["orig_total"]), "dif": _ou_none(x["dif_total"]),
                      "deb_nc": _f(x["deb_nc_total"]), "deb_cum": _f(x["deb_cum_total"]), "usado": _f(x["usado_total"]),
                      "saldo": _f(x["saldo_total"]), "recolher": _f(x["recolher_total"]), "retencao": _f(x["ret_total"])})

    dados = {
        "tipo": "creditos",
        "cabecalho_relatorio": f"Créditos e Débitos PIS/COFINS · {competencia}",
        "capa_titulo": ("Créditos e Débitos", "PIS/COFINS"), "periodo_extenso": "",
        "empresa_codigo": empresa_codigo, "empresa_nome": empresa_nome, "cnpj": cnpj,
        "competencia": competencia, "rotulo": r["rotulo"], "rotulo_anterior": None if p is None else p["rotulo"],
        "periodo_label": competencia,
        "oficial": not nao_oficiais, "meses_nao_oficiais": nao_oficiais, "modo": str(r["modo"]),
        "fonte_rotulo": r["fonte_rotulo"] or "", "sped_tipo": r["sped_tipo"] or "",
        "conferido": bool(ctrl.get("conferido", r["conferido"])), "conferido_por": ctrl.get("conferido_por") or "", "conferido_em": ctrl.get("conferido_em"),
        "nota": (ctrl.get("nota") if ctrl.get("nota") is not None else r["nota"]) or "",
        "apuracao": apuracao,
        "composicao": [{"label": x.categoria, "pct": float(x.pct) * 100, "valor": float(x.valor), "rotulo_valor": f"{formatacao.moeda_br(x.valor)} · {formatacao.pct_br(x.pct)}"}
                       for x in comp.itertuples(index=False) if x.valor > 0],
        "variacao_categorias": [{"categoria": x.categoria, "atual": float(x.atual), "anterior": float(x.anterior), "dif": float(x.dif)}
                                for x in var.head(6).itertuples(index=False) if abs(x.dif) >= 0.005],
        "meses": meses, "tem_original": tem_orig, "dif_original": _ou_none(r["dif_total"]) if tem_orig else None,
        "credito_original": _ou_none(r["orig_total"]) if tem_orig else None,
        "sugestoes": [], "incluir_sugestoes": bool(incluir_sugestoes),
        "nome_contador": nome_contador, "gerado_por": usuario, "gerado_em": gerado_em or formatacao.agora_br(),
    }
    if incluir_sugestoes:
        dados["sugestoes"] = [x for x in an.sugestoes_mes(jan, i, "total") if x["titulo"] not in _SUGESTOES_FORA]
    dados["capa_selo"] = "" if not nao_oficiais else "SIMULAÇÃO"
    dados["leitura"] = _leitura(dados)
    return dados


def nome_arquivo(dados: dict) -> str:
    base = f"creditos_pis_cofins_{dados['empresa_codigo']}_{dados['competencia'].replace('/', '-')}"
    return base + ("" if dados["oficial"] else "_SIMULACAO") + ".pdf"
