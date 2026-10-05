# -*- coding: utf-8 -*-
"""
Exportacao da conferencia de Notas Fiscais x Sienge (tela 7_Notas_Fiscais).

01/10/2026 (Rafael: "eu baixo o resultado mas so' conseguimos baixar as
pendencias"): alem da planilha de pendencias, a contadora baixa a
conferencia COMPLETA -- todas as notas do manifesto (lancadas e
pendentes), o titulo e o documento correspondentes no Sienge, e os titulos
do Sienge sem nota. Funcoes puras (sem Streamlit/banco), testadas com
DataFrame sintetico em tests/test_nf_export.py.
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd

import formatacao

# Ordem e rotulos das colunas na tela e nas planilhas. Colunas internas
# (registro_id, origem) nunca aparecem pra contadora.
ROTULOS = {
    "periodo": "Período",   # v0.46.0: so' existe quando a tabela junta varios periodos
    "numero_nota": "Nº da nota",
    "cfop": "CFOP",
    "sienge_bill_id": "Título Sienge",
    "sienge_documento": "Documento no Sienge",
    "status": "Status",
    "data_emissao": "Emissão",
    "valor": "Valor (manifesto)",
    "fornecedor_nome": "Fornecedor",
    "fornecedor_cnpj": "CNPJ do fornecedor",
    "confianca": "Critério do match",
    "sienge_valor": "Valor (Sienge)",
    "observacao": "Observação",
    "anotacao": "Anotação",   # v0.46.3: texto livre da contadora (tabela egc.nf_anotacao)
    "pendencia_status": "Acompanhamento",
    "atualizado_em": "Atualizado em",
}
ROTULOS_IGNORADAS = {
    "periodo": "Período",
    "numero_nota": "Nº da nota", "data_emissao": "Emissão", "valor": "Valor (manifesto)", "cfop": "CFOP",
    "fornecedor_nome": "Fornecedor", "fornecedor_cnpj": "CNPJ do fornecedor", "tipo_doc": "Tipo (Receita)",
    "natureza": "Natureza da operação", "motivo": "Motivo de ter ficado fora",
}
COLUNAS_MOEDA = ["Valor (manifesto)", "Valor (Sienge)"]
COLUNAS_DATA = ["Emissão"]
COLUNAS_INTERNAS = ["registro_id", "origem"]

# v0.44.3: rotulos amigaveis dos status (filtro, KPIs e Resumo do Excel).
# A coluna "Status" das tabelas continua com o codigo (e' o que a contadora ja' viu).
ROTULO_STATUS = {
    "LANCADA": "Lançadas",
    "LANCADA_OUTRA_EMPRESA": "Lançadas em outra empresa",
    "VALOR_DIVERGENTE": "Valor divergente",
    "NUMERO_DIVERGENTE": "Número divergente",
    "NAO_ENCONTRADA": "Não encontradas no Sienge",
    "SIENGE_SEM_MANIFESTO": "Sienge sem manifesto",
}
ORDEM_STATUS = list(ROTULO_STATUS)


def contar_por_status(tabela: pd.DataFrame) -> dict:
    """{status: n} na ordem de ORDEM_STATUS, incluindo zeros -- fonte unica
    pros KPIs da tela e pro Resumo do Excel (os dois sempre batem)."""
    contagem = tabela["status"].value_counts().to_dict() if len(tabela) and "status" in tabela.columns else {}
    resultado = {st: int(contagem.get(st, 0)) for st in ORDEM_STATUS}
    for st, n in contagem.items():  # status inesperado nunca some
        resultado.setdefault(st, int(n))
    return resultado


def resumo_conferencia(tabela: pd.DataFrame) -> dict:
    """Numeros do topo da conferencia. `pendencias` = tudo que NAO e'
    LANCADA (notas do manifesto + Sienge sem manifesto) = linhas da aba
    "Pendencias" do Excel e do botao "Baixar so as pendencias"."""
    manifesto = tabela[tabela["origem"] == "MANIFESTO"] if "origem" in tabela.columns else tabela
    orfaos = len(tabela) - len(manifesto)
    total = len(manifesto)
    lancadas = int((manifesto["status"] == "LANCADA").sum()) if total else 0
    return {
        "total": total, "lancadas": lancadas, "notas_pendentes": total - lancadas,
        "sienge_sem_manifesto": int(orfaos), "pendencias": int((tabela["status"] != "LANCADA").sum()) if len(tabela) else 0,
        "taxa": (lancadas / total) if total else None,
        "por_status": contar_por_status(tabela),
    }


def _chave_periodo(p) -> tuple:
    import re
    m = re.match(r"^\s*(\d{1,2})\s*/\s*(\d{4})\s*$", str(p or ""))
    return (int(m.group(2)), int(m.group(1))) if m else (9999, 99, str(p))


def periodos_ordenados(periodos) -> list:
    """Periodos unicos em ordem cronologica ('01/2026' < '02/2026' < ...)."""
    return sorted({str(p) for p in periodos if p}, key=_chave_periodo)


def rotulo_periodos(periodos) -> str:
    """'01/2026 a 08/2026' quando seguidos; senao '01/2026, 03/2026'; um so' -> '07/2026'."""
    ps = periodos_ordenados(periodos)
    if not ps:
        return ""
    if len(ps) == 1:
        return ps[0]
    ks = [_chave_periodo(p) for p in ps]
    if all(len(k) == 2 for k in ks):
        seguidos = all((b[0] * 12 + b[1]) - (a[0] * 12 + a[1]) == 1 for a, b in zip(ks, ks[1:]))
        if seguidos:
            return f"{ps[0]} a {ps[-1]}"
    return ", ".join(ps)


def sufixo_arquivo(periodos) -> str:
    """Parte do nome do arquivo: '07_2026' (um) | '01_2026_a_08_2026' (seguidos) | '01_2026_03_2026' (soltos)."""
    r = rotulo_periodos(periodos)
    if not r:
        return "periodo"
    return r.replace(" a ", "_a_").replace(", ", "_").replace("/", "_")


def resumo_por_periodo(tabela: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por periodo (cronologico): notas, lancadas, pendentes, Sienge sem nota, total de pendencias, taxa.
    Vazio se a tabela nao tem a coluna `periodo`."""
    cols = ["Período", "Notas no manifesto", "Lançadas", "Notas com pendência", "Sienge sem nota",
            "Total de pendências", "Taxa de conciliação"]
    if tabela is None or tabela.empty or "periodo" not in tabela.columns:
        return pd.DataFrame(columns=cols)
    linhas = []
    for per in periodos_ordenados(tabela["periodo"]):
        r = resumo_conferencia(tabela[tabela["periodo"] == per])
        linhas.append([per, r["total"], r["lancadas"], r["notas_pendentes"], r["sienge_sem_manifesto"],
                       r["pendencias"], r["taxa"]])
    return pd.DataFrame(linhas, columns=cols)


def preparar_tabela(tabela: pd.DataFrame) -> pd.DataFrame:
    """Tira colunas internas, mantem a ordem de ROTULOS e renomeia pros
    rotulos de exibicao. Colunas desconhecidas sao descartadas."""
    cols = [c for c in ROTULOS if c in tabela.columns]
    return tabela[cols].rename(columns=ROTULOS).copy()


def preparar_ignoradas(ignoradas: pd.DataFrame) -> pd.DataFrame:
    """Notas fora da conferencia com rotulos de exibicao (colunas internas descartadas)."""
    cols = [c for c in ROTULOS_IGNORADAS if c in ignoradas.columns]
    return ignoradas[cols].rename(columns=ROTULOS_IGNORADAS).copy()


# ─────────────────────────────────────────────
#  PLANILHAS (padrao visual Enermais -- v0.46.1)
# ─────────────────────────────────────────────
# Cores da marca (MARCA-ENERMAIS/00-handoff.md, extraidas por pixel dos logos oficiais):
# grupo = azul #171C60 + laranja #F99D20; SMG tem paleta propria (azul #1D2954 + verde #95C94C).
PALETA_GRUPO = {"primaria": "171C60", "destaque": "F99D20"}
PALETA_SMG = {"primaria": "1D2954", "destaque": "95C94C"}
CINZA_ZEBRA = "F4F5FA"
CINZA_BORDA = "D9DCE8"
COR_STATUS = {          # preenchimento suave por situacao (a leitura da planilha comeca pela cor)
    "LANCADA": "E3F4E1",
    "LANCADA_OUTRA_EMPRESA": "FFF1D6",
    "VALOR_DIVERGENTE": "FFF1D6",
    "NUMERO_DIVERGENTE": "FFF1D6",
    "NAO_ENCONTRADA": "FADCDC",
    "SIENGE_SEM_MANIFESTO": "DDE3F5",
}
ROTULO_ACOMPANHAMENTO = {"PENDENTE": "Pendente", "ENVIADO_SUPRIMENTOS": "Enviado ao Suprimentos",
                         "RESOLVIDO": "Resolvido", "DESCARTADO": "Descartado"}
ROTULO_CRITERIO = {"CHAVE": "Chave de acesso", "NUMERO_CNPJ_VALOR": "Nº + CNPJ + valor",
                   "NUMERO_CNPJ": "Nº + CNPJ (valor diferente)", "CNPJ_VALOR": "CNPJ + valor (nº diferente)"}
LARGURA_COLUNA = {"Período": 10, "Nº da nota": 14, "CFOP": 8, "Título Sienge": 12, "Documento no Sienge": 20,
                  "Status": 26, "Emissão": 12, "Valor (manifesto)": 17, "Fornecedor": 52, "CNPJ do fornecedor": 20,
                  "Critério do match": 26, "Valor (Sienge)": 17, "Observação": 60, "Anotação": 40, "Acompanhamento": 22,
                  "Atualizado em": 17}
LINHA_CABECALHO = 5     # linhas 1-3: logo + titulo, 4: respiro, 5: cabecalho da tabela
_LOGOS_DIR = Path(__file__).resolve().parent / "assets_relatorio" / "logos"
ALTURA_LOGO_PX = 46
SLOGAN = "Energia que Integra"


def paleta_da_empresa(empresa_codigo: Optional[str]) -> dict:
    return PALETA_SMG if (empresa_codigo or "").upper() == "SMG" else PALETA_GRUPO


def _bytes_logo(empresa_codigo: Optional[str]) -> Optional[bytes]:
    """PNG do logo da empresa reduzido para a planilha (os originais tem 4600px -- pesariam ~200 KB por aba).
    Empresa sem logo proprio (ex.: SETTE) usa o do grupo. None se nao houver arquivo/Pillow."""
    return _logo_reduzido((empresa_codigo or "").upper())


@lru_cache(maxsize=16)
def _logo_reduzido(codigo: str) -> Optional[bytes]:
    arq = _LOGOS_DIR / f"{codigo}.png"
    if not arq.exists():
        arq = _LOGOS_DIR / "GRUPO.png"
    if not arq.exists():
        return None
    try:
        from PIL import Image
        im = Image.open(arq).convert("RGBA")
        alvo_h = ALTURA_LOGO_PX * 2                      # 2x para ficar nitido em tela de alta resolucao
        alvo_w = max(1, round(im.width * alvo_h / im.height))
        im = im.resize((alvo_w, alvo_h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None


def _inserir_logo(ws, empresa_codigo: Optional[str], ancora: str = "A1") -> None:
    dados = _bytes_logo(empresa_codigo)
    if not dados:
        return
    try:
        from openpyxl.drawing.image import Image as XLImage
        img = XLImage(io.BytesIO(dados))
        proporcao = img.width / img.height
        img.height = ALTURA_LOGO_PX
        img.width = round(ALTURA_LOGO_PX * proporcao)
        ws.add_image(img, ancora)
    except Exception:
        pass            # logo e' enfeite: nunca pode derrubar o download


def _topo(ws, titulo: str, subtitulo: str, empresa_codigo: Optional[str], n_colunas: int, col_titulo: int = 3) -> None:
    """Faixa do topo: logo (A1), titulo e subtitulo ao lado, filete na cor de destaque."""
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    pal = paleta_da_empresa(empresa_codigo)
    for r, h in ((1, 24), (2, 20), (3, 6), (4, 8)):
        ws.row_dimensions[r].height = h
    _inserir_logo(ws, empresa_codigo)
    c = ws.cell(row=1, column=col_titulo, value=titulo)
    c.font = Font(name="Calibri", size=15, bold=True, color=pal["primaria"])
    c.alignment = Alignment(vertical="center")
    c = ws.cell(row=2, column=col_titulo, value=subtitulo)
    c.font = Font(name="Calibri", size=10, color="6B6F8A")
    c.alignment = Alignment(vertical="top")
    for col in range(1, n_colunas + 1):
        ws.cell(row=3, column=col).fill = PatternFill("solid", fgColor=pal["destaque"])
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = pal["primaria"]


def _rodape(empresa_codigo: Optional[str]) -> str:
    return "SMG Soluções" if (empresa_codigo or "").upper() == "SMG" else f"Enermais · {SLOGAN}"


def _formatar_impressao(ws, ultima_linha_cab: int, n_colunas: int, empresa_codigo: Optional[str] = None) -> None:
    from openpyxl.utils import get_column_letter
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{ultima_linha_cab}:{ultima_linha_cab}"
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.oddFooter.left.text = _rodape(empresa_codigo)
    ws.oddFooter.right.text = "Página &P de &N"


def _humanizar(codigo, mapa: dict) -> Optional[str]:
    if codigo is None or (isinstance(codigo, float) and pd.isna(codigo)):
        return None
    return mapa.get(codigo, str(codigo).replace("_", " ").capitalize())


def _cnpj_formatado(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    d = "".join(ch for ch in str(v) if ch.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return str(v)


def _escrever_aba(wb_writer, nome: str, df: pd.DataFrame, titulo: str, subtitulo: str,
                  empresa_codigo: Optional[str] = None, codigos_status: Optional[list] = None) -> None:
    """Aba de dados no padrao Enermais: topo com logo, cabecalho da cor da marca, faixas alternadas,
    cor por situacao, moeda/data/CNPJ formatados, filtro, congelamento e configuracao de impressao.
    `codigos_status` = codigo da situacao de cada linha (alinhado ao df ANTES de virar rotulo)."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    pal = paleta_da_empresa(empresa_codigo)
    df = formatacao.remover_timezone_para_excel(df)
    if "Título Sienge" in df.columns:
        df["Título Sienge"] = df["Título Sienge"].apply(lambda v: int(v) if pd.notna(v) else None).astype("object")
    for col in COLUNAS_DATA:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    if "Status" in df.columns:
        df["Status"] = df["Status"].map(lambda c: ROTULO_STATUS.get(c, c))
    if "Acompanhamento" in df.columns:
        df["Acompanhamento"] = df["Acompanhamento"].map(lambda c: _humanizar(c, ROTULO_ACOMPANHAMENTO))
    if "Critério do match" in df.columns:
        df["Critério do match"] = df["Critério do match"].map(lambda c: _humanizar(c, ROTULO_CRITERIO))
    if "CNPJ do fornecedor" in df.columns:
        df["CNPJ do fornecedor"] = df["CNPJ do fornecedor"].map(_cnpj_formatado)

    df.to_excel(wb_writer, index=False, sheet_name=nome, startrow=LINHA_CABECALHO - 1)
    ws = wb_writer.sheets[nome]
    n_col = max(len(df.columns), 1)
    _topo(ws, titulo, subtitulo, empresa_codigo, n_col, col_titulo=min(3, n_col))

    borda = Border(bottom=Side(style="thin", color=CINZA_BORDA))
    cab_fill = PatternFill("solid", fgColor=pal["primaria"])
    cab_borda = Border(bottom=Side(style="medium", color=pal["destaque"]))
    for cell in ws[LINHA_CABECALHO]:
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        cell.fill = cab_fill
        cell.border = cab_borda
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
    ws.row_dimensions[LINHA_CABECALHO].height = 30

    fonte = Font(name="Calibri", size=10)
    zebra = PatternFill("solid", fgColor=CINZA_ZEBRA)
    col_idx = {c: i for i, c in enumerate(df.columns, start=1)}
    centrais = {"Período", "CFOP", "Título Sienge", "Emissão", "Atualizado em"}
    for k in range(len(df)):
        r = LINHA_CABECALHO + 1 + k
        faixa = zebra if k % 2 else None
        for nome_col, i in col_idx.items():
            cell = ws.cell(row=r, column=i)
            cell.font = fonte
            cell.border = borda
            if faixa is not None:
                cell.fill = faixa
            if nome_col in COLUNAS_MOEDA:
                cell.number_format = '"R$" #,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="top")
            elif nome_col == "Emissão":
                cell.number_format = "DD/MM/YYYY"
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif nome_col == "Atualizado em":
                cell.number_format = "DD/MM/YYYY HH:MM"
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif nome_col in ("Observação", "Anotação"):
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            else:
                cell.alignment = Alignment(horizontal="center" if nome_col in centrais else "left", vertical="top")
        if codigos_status is not None and "Status" in col_idx and k < len(codigos_status):
            cor = COR_STATUS.get(codigos_status[k])
            if cor:
                c = ws.cell(row=r, column=col_idx["Status"])
                c.fill = PatternFill("solid", fgColor=cor)
                c.font = Font(name="Calibri", size=10, bold=True, color=pal["primaria"])
    for nome_col, i in col_idx.items():
        ws.column_dimensions[get_column_letter(i)].width = LARGURA_COLUNA.get(nome_col, 18)
    ws.freeze_panes = ws.cell(row=LINHA_CABECALHO + 1, column=min(3, n_col) if "Nº da nota" in col_idx else 1)
    ultima = LINHA_CABECALHO + max(len(df), 1)
    ws.auto_filter.ref = f"A{LINHA_CABECALHO}:{get_column_letter(n_col)}{ultima}"
    _formatar_impressao(ws, LINHA_CABECALHO, n_col, empresa_codigo)


def _aba_resumo(wb, empresa_nome: str, empresa_codigo: Optional[str], periodo: str, linhas: list,
                por_status: dict) -> None:
    """Aba "Resumo": identificacao, numeros-chave e detalhe por situacao (com as mesmas cores das abas)."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    pal = paleta_da_empresa(empresa_codigo)
    ws = wb.active if wb.worksheets else wb.create_sheet("Resumo")
    ws.title = "Resumo"
    ws.column_dimensions["A"].width = 56
    ws.column_dimensions["B"].width = 40
    _topo(ws, "Conferência de Notas Fiscais × Sienge", f"{empresa_nome} · {periodo}", empresa_codigo, 2, col_titulo=2)
    ws.cell(row=1, column=2).alignment = Alignment(vertical="center", horizontal="left", indent=1)
    ws.cell(row=2, column=2).alignment = Alignment(vertical="top", horizontal="left", indent=1)
    r = LINHA_CABECALHO
    for j, txt in enumerate(("Item", "Valor"), start=1):
        c = ws.cell(row=r, column=j, value=txt)
        c.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=pal["primaria"])
        c.border = Border(bottom=Side(style="medium", color=pal["destaque"]))
        c.alignment = Alignment(horizontal="center" if j == 2 else "left", vertical="center", indent=0 if j == 2 else 1)
    ws.row_dimensions[r].height = 24
    borda = Border(bottom=Side(style="thin", color=CINZA_BORDA))
    codigos_por_rotulo = {v: k for k, v in ROTULO_STATUS.items()}
    destaques = {"Notas no manifesto", "Lançadas no Sienge", "Taxa de conciliação", "Total de pendências (aba Pendências)"}
    for item, valor in linhas:
        r += 1
        a, b = ws.cell(row=r, column=1, value=item), ws.cell(row=r, column=2, value=valor)
        secao = valor is None and not item.startswith("  ")
        rot_status = item.strip().lstrip("· ").strip()
        cod = codigos_por_rotulo.get(rot_status)
        for c in (a, b):
            c.font = Font(name="Calibri", size=10, bold=(secao or item in destaques),
                          color=pal["primaria"] if (secao or item in destaques) else "000000")
            c.border = borda
        a.alignment = Alignment(vertical="center", indent=1)
        b.alignment = Alignment(horizontal="right", vertical="center")
        if isinstance(valor, float) and item == "Taxa de conciliação":
            b.number_format = "0.0%"
        elif isinstance(valor, (int, float)):
            b.number_format = "#,##0"
        if secao:
            for c in (a, b):
                c.fill = PatternFill("solid", fgColor=CINZA_ZEBRA)
        if item.startswith("  ·") and cod in COR_STATUS:
            for c in (a, b):
                c.fill = PatternFill("solid", fgColor=COR_STATUS[cod])
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.oddFooter.left.text = _rodape(empresa_codigo)


def _aba_resumo_periodos(writer, df: pd.DataFrame, empresa_nome: str, empresa_codigo: Optional[str], periodo: str) -> None:
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    pal = paleta_da_empresa(empresa_codigo)
    nome = "Resumo por período"
    df = df.copy()
    total = {"Período": "Total", "Notas no manifesto": int(df["Notas no manifesto"].sum()),
             "Lançadas": int(df["Lançadas"].sum()), "Notas com pendência": int(df["Notas com pendência"].sum()),
             "Sienge sem nota": int(df["Sienge sem nota"].sum()), "Total de pendências": int(df["Total de pendências"].sum())}
    total["Taxa de conciliação"] = (total["Lançadas"] / total["Notas no manifesto"]) if total["Notas no manifesto"] else None
    df = pd.concat([df, pd.DataFrame([total])], ignore_index=True)
    df.to_excel(writer, index=False, sheet_name=nome, startrow=LINHA_CABECALHO - 1)
    ws = writer.sheets[nome]
    n = len(df.columns)
    _topo(ws, "Resumo por período", f"{empresa_nome} · {periodo}", empresa_codigo, n, col_titulo=3)
    for cell in ws[LINHA_CABECALHO]:
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=pal["primaria"])
        cell.border = Border(bottom=Side(style="medium", color=pal["destaque"]))
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[LINHA_CABECALHO].height = 30
    ultima = LINHA_CABECALHO + len(df)
    for r in range(LINHA_CABECALHO + 1, ultima + 1):
        eh_total = r == ultima
        for c in range(1, n + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = Font(name="Calibri", size=10, bold=eh_total, color=pal["primaria"] if eh_total else "000000")
            cell.border = Border(bottom=Side(style="thin", color=CINZA_BORDA),
                                 top=Side(style="medium", color=pal["destaque"]) if eh_total else None)
            if eh_total:
                cell.fill = PatternFill("solid", fgColor=CINZA_ZEBRA)
            elif (r - LINHA_CABECALHO) % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=CINZA_ZEBRA)
            cell.alignment = Alignment(horizontal="center" if c == 1 else "right")
            if c == n:
                cell.number_format = "0.0%"
            elif c > 1:
                cell.number_format = "#,##0"
    for i, larg in enumerate((12, 20, 12, 22, 18, 20, 20), start=1):
        ws.column_dimensions[get_column_letter(i)].width = larg
    ws.freeze_panes = ws.cell(row=LINHA_CABECALHO + 1, column=2)
    _formatar_impressao(ws, LINHA_CABECALHO, n, empresa_codigo)


def _status_de(tabela: pd.DataFrame) -> list:
    return list(tabela["status"]) if "status" in tabela.columns else []


def gerar_xlsx_conferencia(
    tabela: pd.DataFrame, empresa_nome: str, periodo_referencia: str,
    arquivo_manifesto: Optional[str] = None, gerado_em: Optional[str] = None,
    ignoradas: Optional[pd.DataFrame] = None, empresa_codigo: Optional[str] = None,
) -> bytes:
    """
    Planilha completa da conferencia, no padrao visual Enermais (logo e cores da empresa).
    `tabela` = saida de nf_sienge.tabela_conferencia / listar_conciliacao (+ listar_orfaos_sienge, com
    registro_id/origem). Abas: Resumo, Resumo por período (so' com 2+ periodos), Conferência completa
    (todas as notas do manifesto + titulos do Sienge sem nota), Pendências (tudo que nao e' LANCADA),
    Sienge sem nota e Ignoradas. `periodo_referencia` e' o rotulo do conjunto (ex.: "01/2026 a 08/2026").
    """
    from openpyxl import Workbook
    orfaos = tabela[tabela["origem"] == "SIENGE_ORFAO"] if "origem" in tabela.columns else tabela.iloc[0:0]
    pend = tabela[tabela["status"] != "LANCADA"]
    r = resumo_conferencia(tabela)
    por_periodo = resumo_por_periodo(tabela)

    resumo_linhas = [
        ("Empresa", empresa_nome),
        ("Período de referência", periodo_referencia),
        ("Arquivo do manifesto", arquivo_manifesto or "—"),
        ("Gerado em", gerado_em or "—"),
        ("Notas no manifesto", r["total"]),
        ("Lançadas no Sienge", r["lancadas"]),
        ("Taxa de conciliação", r["taxa"]),
        ("Notas do manifesto com pendência", r["notas_pendentes"]),
        ("Títulos no Sienge sem nota no manifesto", r["sienge_sem_manifesto"]),
        ("Total de pendências (aba Pendências)", r["pendencias"]),
        ("Detalhe por status", None),
    ] + [(f"  · {ROTULO_STATUS.get(st, st)}", n) for st, n in r["por_status"].items()]
    if ignoradas is not None and len(ignoradas):
        por_motivo = ignoradas["motivo"].value_counts().to_dict()
        resumo_linhas += [("Notas do arquivo que ficaram FORA da conferência (aba Ignoradas)", len(ignoradas))] + [
            (f"  · {m}", int(n)) for m, n in sorted(por_motivo.items())]

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        wb = writer.book
        _aba_resumo(wb, empresa_nome, empresa_codigo, periodo_referencia, resumo_linhas, r["por_status"])
        if len(por_periodo) > 1:
            _aba_resumo_periodos(writer, por_periodo, empresa_nome, empresa_codigo, periodo_referencia)
        sub = f"{empresa_nome} · {periodo_referencia}"
        _escrever_aba(writer, "Conferência completa", preparar_tabela(tabela), "Conferência completa", sub,
                      empresa_codigo, _status_de(tabela))
        _escrever_aba(writer, "Pendências", preparar_tabela(pend), "Pendências a tratar", sub,
                      empresa_codigo, _status_de(pend))
        _escrever_aba(writer, "Sienge sem nota", preparar_tabela(orfaos), "Títulos do Sienge sem nota no manifesto", sub,
                      empresa_codigo, _status_de(orfaos))
        if ignoradas is not None and len(ignoradas):
            _escrever_aba(writer, "Ignoradas", preparar_ignoradas(ignoradas), "Notas fora da conferência", sub, empresa_codigo)
    return buf.getvalue()


def gerar_xlsx_pendencias(tabela: pd.DataFrame, empresa_nome: str, periodo_referencia: str,
                          empresa_codigo: Optional[str] = None) -> bytes:
    """Planilha so' das pendencias (a que vai para o Suprimentos), no mesmo padrao visual."""
    pend = tabela[tabela["status"] != "LANCADA"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _escrever_aba(writer, "Pendências", preparar_tabela(pend), "Pendências a tratar",
                      f"{empresa_nome} · {periodo_referencia}", empresa_codigo, _status_de(pend))
    return buf.getvalue()
