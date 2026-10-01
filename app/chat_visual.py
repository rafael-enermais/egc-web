# -*- coding: utf-8 -*-
"""
Visualizacao e relatorio do resultado de uma consulta do chat (01/10/2026,
pedido do Rafael: "ao lado do chat, a possibilidade do dash gerado pelo chat
virar relatorio, tabela ou grafico interessante").

Funcoes puras (sem Streamlit, sem banco): recebem o dict que uma ferramenta de
consultas_chat devolveu e produzem
  - `preparar(nome, resultado)` -> tabela numerica + opcoes de grafico;
  - `tabela_exibicao(prep)` -> DataFrame formatado em pt-BR;
  - `figura(...)` -> Figure plotly (paleta do app: azul / laranja / verde-agua);
  - `excel_bytes(...)` / `pdf_bytes(...)` -> relatorio para download.
Nenhum numero e' recalculado aqui: tudo vem do resultado da ferramenta.
"""
from __future__ import annotations

import datetime
import io
import re
from typing import Optional

import pandas as pd
import plotly.graph_objects as go

import formatacao

AZUL, LARANJA, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
CORES = [AZUL, LARANJA, AQUA, "#8a8985"]

FORMATO_INDICADOR = {
    "Liquidez Corrente": "x", "Capital de Giro": "R$", "Endividamento Geral": "pct", "Margem Bruta": "pct",
    "Margem Líquida": "pct", "ROA": "pct", "ROE": "pct", "EBITDA": "R$", "Margem EBITDA": "pct",
}
TIPOS_GRAFICO = ["Barras", "Barras horizontais", "Linhas"]

TITULOS = {
    "consultar_bp_dre": "BP/DRE", "consultar_visao_grupo": "Visão Grupo", "consultar_periodos": "Períodos disponíveis",
    "consultar_indicadores": "Indicadores", "consultar_evolucao_indicadores": "Evolução dos indicadores",
    "consultar_completude": "Completude dos dados", "consultar_notas_fiscais_kpi": "Conciliação de notas fiscais",
    "consultar_notas_pendentes": "Notas pendentes", "consultar_nota_fiscal": "Nota fiscal",
    "consultar_historico_importacoes": "Histórico de importações", "consultar_correcoes_manuais": "Correções manuais",
    "consultar_relatorios_gerados": "Relatórios gerados",
}


def _rotulo_formato(fmt: str, v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if fmt == "R$":
        return formatacao.moeda_br(v)
    if fmt == "pct":
        return formatacao.pct_br(v)
    if fmt == "x":
        return formatacao.numero_br(v, sufixo="x")
    if fmt == "int":
        return f"{int(v):,}".replace(",", ".")
    return str(v)


def _is_pct_col(c) -> bool:
    return str(c).strip().startswith("%")


def preparar(nome: str, r: dict, nomes_empresa: Optional[dict] = None) -> Optional[dict]:
    """-> {"titulo","subtitulo","df","formatos":{col:fmt},"graficos":{nome:{"df","x","ys","formato"}}} ou None."""
    if not isinstance(r, dict) or "erro" in r:
        return None
    titulo = TITULOS.get(nome, nome)
    sub = []

    if "contas" in r and r["contas"]:
        df = pd.DataFrame(r["contas"])
        money = [c for c in df.columns if c not in ("grupo", "conta") and not _is_pct_col(c)]
        formatos = {c: ("pct" if _is_pct_col(c) else "R$") for c in df.columns if c not in ("grupo", "conta")}
        if r.get("tipo"):
            titulo = f"{r['tipo']} — " + ("Grupo" if nome == "consultar_visao_grupo" else str(r.get("empresa", "")))
        sub = [f"Período {r.get('periodo', '—')}", f"Base: {r.get('granularidade') or 'não declarada'}"]
        graficos = {}
        if money:
            col = "valor" if "valor" in money else ("VALOR CONSOLIDADO" if "VALOR CONSOLIDADO" in money else money[0])
            por_grupo = df.groupby("grupo", sort=False)[money].sum().reset_index()
            graficos["Total por grupo"] = {"df": por_grupo, "x": "grupo", "ys": [col], "formato": "R$"}
            top = df.reindex(df[col].abs().sort_values(ascending=False).index).head(15)
            graficos["15 maiores contas"] = {"df": top[["conta"] + money], "x": "conta", "ys": [col], "formato": "R$"}
        return {"titulo": titulo, "subtitulo": " · ".join(sub), "df": df, "formatos": formatos, "graficos": graficos}

    if "periodos_por_empresa" in r:
        nomes = nomes_empresa or {}
        linhas = [{"Empresa": nomes.get(cod, cod), "Períodos disponíveis": ", ".join(sorted(ps, reverse=True)) if ps else "—",
                   "Qtde": len(ps)} for cod, ps in r["periodos_por_empresa"].items()]
        df = pd.DataFrame(linhas)
        return {"titulo": titulo, "subtitulo": "", "df": df, "formatos": {"Qtde": "int"},
                "graficos": {"Períodos por empresa": {"df": df[["Empresa", "Qtde"]], "x": "Empresa",
                                                      "ys": ["Qtde"], "formato": "int"}}}

    if "indicadores" in r:
        linhas = [{"Indicador": k, "Valor": v, "_fmt": FORMATO_INDICADOR.get(k, "R$")} for k, v in r["indicadores"].items()]
        df = pd.DataFrame(linhas)
        sub = [f"Período {r.get('periodo', '—')}", f"Base: {r.get('base_do_periodo', '—')}",
               "Empresas: " + ", ".join(r.get("empresas_incluidas", []))]
        return {"titulo": titulo, "subtitulo": " · ".join(sub), "df": df.drop(columns="_fmt"),
                "formatos": {}, "linha_formato": dict(zip(df["Indicador"], df["_fmt"])), "graficos": {}}

    if "evolucao" in r and r["evolucao"]:
        df = pd.DataFrame(r["evolucao"])
        cols = [c for c in df.columns if c != "periodo"]
        formatos = {c: FORMATO_INDICADOR.get(c, "R$") for c in cols}
        graficos = {}
        for rotulo, fmt in (("Valores em R$", "R$"), ("Margens e retornos (%)", "pct"), ("Liquidez (x)", "x")):
            ys = [c for c in cols if formatos[c] == fmt]
            if ys:
                graficos[rotulo] = {"df": df[["periodo"] + ys], "x": "periodo", "ys": ys[:3], "formato": fmt}
        sub = [f"Base: {r.get('base_do_periodo', '—')}", "Empresas: " + ", ".join(r.get("empresas_incluidas", []))]
        return {"titulo": titulo, "subtitulo": " · ".join(sub), "df": df, "formatos": formatos, "graficos": graficos}

    if "completude_por_periodo" in r and r["completude_por_periodo"]:
        df = pd.DataFrame(r["completude_por_periodo"])
        graf = {}
        comp, pend, per = [], [], []
        for l in r["completude_por_periodo"]:
            m = re.search(r"\((\d+)/(\d+)\)", str(l.get("Status", "")))
            if m:
                per.append(str(l.get("Período"))); comp.append(int(m.group(1))); pend.append(int(m.group(2)) - int(m.group(1)))
        if per:
            graf["Empresas completas x pendentes"] = {
                "df": pd.DataFrame({"Período": per, "Completas": comp, "Pendentes": pend}),
                "x": "Período", "ys": ["Completas", "Pendentes"], "formato": "int", "empilhado": True}
        return {"titulo": titulo, "subtitulo": "", "df": df, "formatos": {}, "graficos": graf}

    if "rodadas" in r and r["rodadas"]:
        df = pd.DataFrame(r["rodadas"])
        formatos = {"taxa_conciliacao": "pct"}
        g = df.assign(rodada=df["empresa_codigo"] + " " + df["periodo_referencia"].astype(str))
        return {"titulo": titulo, "subtitulo": "", "df": df, "formatos": formatos, "graficos": {
            "Notas lançadas x pendentes por rodada": {"df": g[["rodada", "total_lancadas", "total_pendencias"]], "x": "rodada",
                                                       "ys": ["total_lancadas", "total_pendencias"], "formato": "int", "empilhado": True}}}

    for chave, rotulo_x, col_valor in (("pendencias", "fornecedor_nome", "valor"), ("notas", "fornecedor_nome", "valor")):
        if chave in r and r[chave]:
            df = pd.DataFrame(r[chave]).drop(columns=["registro_id"], errors="ignore")
            formatos = {c: "R$" for c in ("valor", "sienge_valor") if c in df.columns}
            graficos = {}
            if "status" in df.columns:
                cont = df["status"].value_counts().rename_axis("Status").reset_index(name="Quantidade")
                graficos["Por status"] = {"df": cont, "x": "Status", "ys": ["Quantidade"], "formato": "int"}
            if "valor" in df.columns and rotulo_x in df.columns:
                top = (df.groupby(rotulo_x)["valor"].sum().sort_values(ascending=False).head(10).reset_index())
                graficos["10 maiores fornecedores (valor)"] = {"df": top, "x": rotulo_x, "ys": ["valor"], "formato": "R$"}
            return {"titulo": titulo, "subtitulo": f"{len(df)} registro(s)", "df": df, "formatos": formatos, "graficos": graficos}

    for chave in ("importacoes", "correcoes", "relatorios"):
        if chave in r and r[chave]:
            df = pd.DataFrame(r[chave])
            for c in df.columns:
                df[c] = df[c].apply(lambda v: ", ".join(map(str, v)) if isinstance(v, (list, tuple)) else v)
            formatos = {c: "R$" for c in ("valor_atual", "valor_original_pdf") if c in df.columns}
            return {"titulo": titulo, "subtitulo": f"{len(df)} registro(s)", "df": df, "formatos": formatos, "graficos": {}}
    return None


_ROTULOS_COLUNA = {
    "periodo": "Período", "periodo_referencia": "Período de referência", "valor": "Valor", "sienge_valor": "Valor no Sienge",
    "fornecedor_nome": "Fornecedor", "fornecedor_cnpj": "CNPJ do fornecedor", "numero_nota": "Nota", "numero_normalizado": "Nota (normalizada)",
    "empresa_codigo": "Empresa", "status": "Status", "pendencia_status": "Pendência", "data_emissao": "Emissão", "cfop": "CFOP",
    "criado_em": "Criado em", "usuario": "Usuário", "arquivos": "Arquivos", "tipo": "Tipo", "mensagem": "Mensagem",
    "nivel": "Nível", "conta": "Conta", "grupo": "Grupo", "granularidade": "Base", "taxa_conciliacao": "Taxa de conciliação",
    "total_notas": "Total de notas", "total_lancadas": "Lançadas", "total_pendencias": "Pendências", "arquivo_nome": "Arquivo",
    "valor_atual": "Valor atual", "valor_original_pdf": "Valor original (PDF)", "origem": "Origem", "gerado_em": "Gerado em",
}


def _rotulo_coluna(c) -> str:
    """Nome de coluna legivel: 'fornecedor_nome' -> 'Fornecedor'. Nomes que ja' vem
    legiveis (com maiuscula/espaco, ex. 'Liquidez Corrente') ficam como estao."""
    c = str(c)
    if c in _ROTULOS_COLUNA:
        return _ROTULOS_COLUNA[c]
    if "_" in c and c == c.lower():
        c = c.replace("_", " ")
        return c[:1].upper() + c[1:]
    return c[:1].upper() + c[1:] if c == c.lower() else c


def _celula_vazia(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and v.strip().lower() in ("none", "nan", "nat"))


def tabela_exibicao(prep: dict) -> pd.DataFrame:
    """Tabela pronta para mostrar (tela e PDF): numeros em pt-BR, vazios como '—' (nunca
    'None'/'nan') e nomes de coluna legiveis."""
    df = prep["df"].copy()
    lf = prep.get("linha_formato")
    if lf is not None and "Valor" in df.columns:
        df["Valor"] = [_rotulo_formato(lf.get(i, "R$"), v) for i, v in zip(df["Indicador"], df["Valor"])]
    else:
        for c, fmt in prep["formatos"].items():
            if c in df.columns and fmt != "int":  # contagens ficam numericas (ordenaveis na tabela)
                df[c] = df[c].apply(lambda v, f=fmt: _rotulo_formato(f, v))
    df = df.astype(object)
    df = df.apply(lambda col: col.map(lambda v: "—" if _celula_vazia(v) else v))
    df.columns = [_rotulo_coluna(c) for c in df.columns]
    return df


def figura(grafico: dict, tipo: str, dark: bool = True, ys: Optional[list[str]] = None) -> go.Figure:
    """grafico = prep["graficos"][nome]; tipo em TIPOS_GRAFICO."""
    df, x, fmt = grafico["df"], grafico["x"], grafico["formato"]
    ys = [y for y in (ys or grafico["ys"]) if y in df.columns] or grafico["ys"]
    fig = go.Figure()
    horizontal = tipo == "Barras horizontais"
    for i, y in enumerate(ys):
        vals = df[y].tolist()
        plot = [None if pd.isna(v) else (v * 100.0 if fmt == "pct" else v) for v in vals]
        hover = [_rotulo_formato(fmt, v) for v in vals]
        kw = dict(name=str(y), customdata=hover, marker_color=CORES[i % len(CORES)])
        if tipo == "Linhas":
            fig.add_scatter(x=df[x].astype(str), y=plot, mode="lines+markers", line=dict(color=CORES[i % len(CORES)], width=2),
                            marker=dict(size=7), name=str(y), customdata=hover,
                            hovertemplate="<b>%{fullData.name}</b><br>%{x}: %{customdata}<extra></extra>")
        elif horizontal:
            fig.add_bar(x=plot, y=df[x].astype(str), orientation="h", hovertemplate="<b>%{y}</b><br>%{customdata}<extra></extra>", **kw)
        else:
            fig.add_bar(x=df[x].astype(str), y=plot, hovertemplate="<b>%{x}</b><br>%{customdata}<extra></extra>", **kw)
    fig.update_layout(
        height=340 if not horizontal else max(300, 30 * len(df) + 90), margin=dict(l=8, r=8, t=28, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", separators=",.",
        barmode="stack" if grafico.get("empilhado") else "group", showlegend=len(ys) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    eixo_val = dict(gridcolor="rgba(128,128,128,0.18)", zeroline=True, zerolinecolor="#8a8985")
    if fmt == "pct":
        eixo_val["ticksuffix"] = "%"
    if horizontal:
        fig.update_xaxes(**eixo_val)
        fig.update_yaxes(autorange="reversed", showgrid=False)
    else:
        fig.update_yaxes(**eixo_val)
        fig.update_xaxes(showgrid=False)
    return fig


def _meta(prep: dict, usuario: str, consulta: str) -> list[tuple[str, str]]:
    return [("Relatório", prep["titulo"]), ("Detalhes", prep.get("subtitulo") or "—"), ("Origem", consulta),
            ("Gerado por", usuario), ("Gerado em", datetime.datetime.now().strftime("%d/%m/%Y %H:%M")),
            ("Observação", "Dados consultados no sistema EGC pela Erik.AI; nenhum valor foi recalculado ou estimado.")]


def excel_bytes(prep: dict, usuario: str = "", consulta: str = "") -> bytes:
    """Planilha com a tabela (numeros reais, nao texto) + aba 'Fonte'."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df = prep["df"].copy()
        df.to_excel(xw, sheet_name="Resultado", index=False)
        ws = xw.sheets["Resultado"]
        lf = prep.get("linha_formato")
        for j, c in enumerate(df.columns, start=1):
            largura = max([len(str(c))] + [len(str(v)) for v in df[c].head(200)]) + 2
            ws.column_dimensions[ws.cell(row=1, column=j).column_letter].width = min(largura, 60)
            fmt = prep["formatos"].get(c)
            if fmt in ("R$", "pct", "x") or (lf is not None and c == "Valor"):
                for i in range(2, len(df) + 2):
                    cell = ws.cell(row=i, column=j)
                    f = fmt or lf.get(df.iloc[i - 2]["Indicador"], "R$")
                    cell.number_format = {"R$": '#,##0.00;[Red]-#,##0.00', "pct": "0.0%", "x": '0.00"x"'}.get(f, "General")
        for j, c in enumerate(df.columns, start=1):   # cabecalho legivel (depois de usar os nomes tecnicos acima)
            ws.cell(row=1, column=j).value = _rotulo_coluna(c)
        pd.DataFrame(_meta(prep, usuario, consulta), columns=["Campo", "Valor"]).to_excel(xw, sheet_name="Fonte", index=False)
        xw.sheets["Fonte"].column_dimensions["A"].width = 16
        xw.sheets["Fonte"].column_dimensions["B"].width = 90
    return buf.getvalue()


_NAVY, _ORANGE, _GREY = "#171C60", "#EA9527", "#525252"
# 1a serie navy, 2a azul medio; laranja fica pra "abaixo de zero" (mesma regra dos relatorios)
CORES_MARCA = [_NAVY, "#7F87C9", _ORANGE, "#B4B9D6", "#525252"]
_TEXTO_DA_COR = {_NAVY: _NAVY, "#7F87C9": "#4A53A8", _ORANGE: "#9A5200", "#B4B9D6": "#5E648F", "#525252": "#525252"}


def _rotulo_compacto(fmt: str, v) -> str:
    """Rotulo curto p/ colocar em cima da barra (R$ 1,2 mi / R$ 200 mil); demais formatos como na tabela."""
    if fmt != "R$" or v is None or pd.isna(v):
        return _rotulo_formato(fmt, v)
    a = abs(v)
    sinal = "-" if v < 0 else ""
    if a >= 1_000_000:
        return f"{sinal}R$ {a / 1_000_000:.1f} mi".replace(".", ",")
    if a >= 100_000:
        return f"{sinal}R$ {a / 1_000:.0f} mil"
    if a >= 1_000:
        return f"{sinal}R$ {a / 1_000:.1f} mil".replace(".", ",")
    return f"{sinal}R$ {a:.0f}"


def _png_grafico(grafico: dict, tipo: str, ys: Optional[list[str]] = None, marca: bool = False) -> Optional[bytes]:
    """PNG do grafico via matplotlib (sem kaleido), pro PDF. `marca=True`: paleta/fonte da
    EnerMais (navy; laranja = abaixo de zero), rotulos de valor nas barras."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    df, x, fmt = grafico["df"], grafico["x"], grafico["formato"]
    ys = [y for y in (ys or grafico["ys"]) if y in df.columns] or grafico["ys"]
    familia = None
    if marca:
        try:
            import os as _os
            from matplotlib import font_manager as _fm
            import gerador_relatorio_comentado as _A
            for f in ("Poppins-Regular.ttf", "Poppins-Bold.ttf"):
                _fm.fontManager.addfont(_os.path.join(_A.FONT_DIR, f))
            familia = "Poppins"
        except Exception:
            familia = None
    cores = CORES_MARCA if marca else CORES
    ctx = matplotlib.rc_context({"font.family": familia} if familia else {})
    with ctx:
        fig, ax = plt.subplots(figsize=(10.2, 3.3) if marca else (7.2, 3.2), dpi=150)
        rotulos = df[x].astype(str).tolist()
        n = len(ys)
        pos = list(range(len(rotulos)))
        base = [0.0] * len(rotulos)
        pouco = len(rotulos) * n <= 16
        for i, y in enumerate(ys):
            vals = [0.0 if pd.isna(v) else (v * 100.0 if fmt == "pct" else v) for v in df[y]]
            cor = cores[i % len(cores)]
            if tipo == "Linhas":
                ax.plot(pos, vals, marker="o", color=cor, linewidth=2, label=_rotulo_coluna(y))
                if marca and pouco:
                    for p, v in zip(pos, vals):
                        ax.annotate(_rotulo_compacto(fmt, v / 100.0 if fmt == "pct" else v), (p, v), textcoords="offset points",
                                    xytext=(0, 7), ha="center", fontsize=7, color=_TEXTO_DA_COR.get(cor, cor))
            elif grafico.get("empilhado"):
                ax.bar(pos, vals, bottom=base, color=cor, label=_rotulo_coluna(y), width=0.6 if marca else 0.8)
                base = [b + v for b, v in zip(base, vals)]
            else:
                w = min((0.6 if marca else 0.8) / n, 0.32 if marca else 9)
                xs = [p - (w * n / 2) + w * (i + 0.5) for p in pos]
                if marca and n == 1:
                    barras = ax.bar(xs, vals, width=w, color=[_NAVY if v >= 0 else _ORANGE for v in vals], label=_rotulo_coluna(y))
                else:
                    barras = ax.bar(xs, vals, width=w, color=cor, label=_rotulo_coluna(y))
                if marca and pouco:
                    for b, v in zip(barras, vals):
                        ax.annotate(_rotulo_compacto(fmt, v / 100.0 if fmt == "pct" else v), (b.get_x() + b.get_width() / 2, v),
                                    textcoords="offset points", xytext=(0, 4 if v >= 0 else -10), ha="center", fontsize=6.5,
                                    color=(_NAVY if v >= 0 else "#9A5200") if n == 1 else _TEXTO_DA_COR.get(cor, cor),
                                    fontweight="bold")
        ax.set_xticks(pos)
        ax.set_xticklabels([r[:22] for r in rotulos], rotation=30 if len(rotulos) > 4 else 0, ha="right" if len(rotulos) > 4 else "center", fontsize=7)
        ax.tick_params(axis="y", labelsize=7)
        ax.axhline(0, color="#8a8985", linewidth=0.6)
        ax.margins(y=0.15)
        if marca and tipo != "Linhas":
            ax.set_xlim(-0.75, len(rotulos) - 0.25)   # poucas barras nao esticam pra largura toda
        for lado in ("top", "right"):
            ax.spines[lado].set_visible(False)
        if marca:
            ax.yaxis.grid(True, color="#E3E7EE", linewidth=0.6)
            ax.set_axisbelow(True)
            for lado in ("left", "bottom"):
                ax.spines[lado].set_color("#DADFE8")
        if n > 1:
            ax.legend(fontsize=7, frameon=False)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _p: (f"{v:,.0f}".replace(",", ".")) + ("%" if fmt == "pct" else "")))
        fig.tight_layout()
        out = io.BytesIO()
        fig.savefig(out, format="png", transparent=False, facecolor="white")
        plt.close(fig)
    return out.getvalue()


def pdf_bytes(prep: dict, usuario: str = "", consulta: str = "", grafico: Optional[dict] = None,
              tipo_grafico: str = "Barras", ys: Optional[list[str]] = None) -> bytes:
    """Relatorio PDF no padrao visual EnerMais (mesma marca dos relatorios comentados):
    faixa navy->laranja, logo do Grupo, Poppins, tabela com cabecalho navy, grafico com a
    paleta da marca, rodape com pagina X de Y. A4 paisagem (tabelas largas)."""
    import os
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape
    import gerador_relatorio_comentado as A

    A._registrar_fontes()
    W, H = landscape(A4)
    M = 36.0
    gerado_em = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    logo = A.LOGO.get("GRUPO")

    class _Numerado(_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._paginas = []

        def showPage(self):
            self._paginas.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._paginas)
            for estado in self._paginas:
                self.__dict__.update(estado)
                self._moldura(total)
                super().showPage()
            super().save()

        def _moldura(self, total):
            # faixa navy -> laranja (igual aos relatorios)
            passos = 80
            for i in range(passos):
                self.setFillColor(HexColor(A._interp_cor(A.NAVY, A.ORANGE, i / (passos - 1))))
                self.rect(i * W / passos, H - 5, W / passos + 0.5, 5, stroke=0, fill=1)
            if logo and os.path.exists(logo):
                ir = ImageReader(logo)
                iw, ih = ir.getSize()
                esc = min(150 / iw, 40 / ih)
                self.drawImage(ir, M, H - 14 - ih * esc, iw * esc, ih * esc, mask="auto")
            self.setFillColor(HexColor(A.NAVY))
            self.setFont("Sans-Bold", 8)
            self.drawRightString(W - M, H - 28, "ERIK.AI · CONSULTA AO SISTEMA")
            self.setFont("Sans-Bold", 11)
            self.drawRightString(W - M, H - 43, str(prep["titulo"])[:70])
            self.setStrokeColor(HexColor(A.BORDER_LIGHT)); self.setLineWidth(0.75)
            self.line(M, H - 60, W - M, H - 60)
            self.line(M, 34, W - M, 34)
            self.setFillColor(HexColor(A.GREY_TEXT)); self.setFont("Sans", 7.5)
            self.drawString(M, 22, f"EGC — Gestão Contábil EnerMais · gerado por {usuario or '—'} em {gerado_em}")
            self.drawRightString(W - M, 22, f"Página {self._pageNumber} de {total}")

    doc = SimpleDocTemplate(io.BytesIO(), pagesize=(W, H), leftMargin=M, rightMargin=M, topMargin=72, bottomMargin=46,
                            title=str(prep["titulo"]), author="EnerMais EGC")
    buf = io.BytesIO()
    doc.filename = buf
    cinza = HexColor(A.GREY_TEXT)
    h1 = ParagraphStyle("h1", fontName="Sans-Bold", fontSize=17, leading=21, textColor=HexColor(A.NAVY))
    sub = ParagraphStyle("sub", fontName="Sans", fontSize=9, leading=12.5, textColor=cinza)
    nota = ParagraphStyle("nota", fontName="Sans-Italic", fontSize=7.5, leading=10.5, textColor=cinza)
    cel = ParagraphStyle("cel", fontName="Sans", fontSize=7.5, leading=9.5, textColor=HexColor("#222222"))
    celd = ParagraphStyle("celd", parent=cel, alignment=2)
    cab = ParagraphStyle("cab", fontName="Sans-Bold", fontSize=7.5, leading=9.5, textColor=HexColor("#FFFFFF"))
    cabd = ParagraphStyle("cabd", parent=cab, alignment=2)

    el = [Paragraph(escape(str(prep["titulo"])), h1)]
    if prep.get("subtitulo"):
        el.append(Paragraph(escape(prep["subtitulo"]), sub))
    el.append(Spacer(1, 6))
    caixa = Table([[Paragraph(escape(f"Fonte: {consulta or 'sistema EGC'} · consulta feita no sistema pela Erik.AI. "
                                      "Os valores foram lidos do banco, sem recálculo ou estimativa."), nota)]],
                  colWidths=[W - 2 * M])
    caixa.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(A.GREY_BG)), ("BOX", (0, 0), (-1, -1), 0.5, HexColor(A.BORDER_LIGHT)),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    el += [caixa, Spacer(1, 10)]
    if grafico is not None:
        png = _png_grafico(grafico, tipo_grafico, ys, marca=True)
        if png:
            largura = W - 2 * M
            el += [Image(io.BytesIO(png), width=largura * 0.86, height=largura * 0.86 * 3.3 / 10.2), Spacer(1, 8)]

    disp = tabela_exibicao(prep)
    MAX = 300
    cortado = len(disp) > MAX
    disp = disp.head(MAX)
    colunas = list(disp.columns)
    fm = prep.get("formatos", {})
    # alinha a direita o que e' numero (formato conhecido na tabela; no quadro de indicadores, "Valor")
    nomes_num = {_rotulo_coluna(c) for c, f in fm.items() if f in ("R$", "pct", "x", "int")}
    if prep.get("linha_formato") is not None:
        nomes_num.add("Valor")
    direita = [c in nomes_num for c in colunas]
    dados = [[Paragraph(f"<b>{escape(str(c))}</b>", cabd if d else cab) for c, d in zip(colunas, direita)]]
    for _i, row in disp.iterrows():
        dados.append([Paragraph(escape(str(v)), celd if d else cel) for v, d in zip(row, direita)])
    pesos = []
    for c in colunas:
        tam = max([len(str(c))] + [len(str(v)) for v in disp[c].head(60)])
        pesos.append(min(max(tam, 7), 38))
    total_p = float(sum(pesos)) or 1.0
    larg = [(W - 2 * M) * p / total_p for p in pesos]
    tb = Table(dados, colWidths=larg, repeatRows=1)
    tb.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(A.NAVY)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#FFFFFF"), HexColor(A.GREY_BG)]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, HexColor(A.BORDER_LIGHT)),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    el.append(tb)
    if cortado:
        el += [Spacer(1, 4), Paragraph(f"Tabela limitada às primeiras {MAX} linhas — baixe o Excel para ver tudo.", nota)]
    doc.build(el, canvasmaker=_Numerado)
    return buf.getvalue()
