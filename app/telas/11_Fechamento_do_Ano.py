# -*- coding: utf-8 -*-
"""
Tela FECHAMENTO DO ANO (v0.54.0) -- visao unica do que ja' foi importado e do que ainda falta subir
em cada fechamento do ano (1T-4T, 1S, 2S, 9 meses acumulados e Anual), por empresa.

* A grade mostra o estado de cada fechamento (BP + DRE). O upload dos PDFs continua na tela
  "Importar PDF": cada PDF e' reconhecido pela empresa (CNPJ) e pelo intervalo declarado nele, e a
  celula correspondente aqui passa a aparecer preenchida.
* Conferencias entre periodos (1T + 2T = 1S ...) sao SO' AVISO -- nunca travam nada.
* Se o rotulo gravado diverge do intervalo que o proprio PDF declarou (caso ENG 09/2026), a tela
  sugere "Corrigir granularidade" (so' troca o rotulo; os numeros nao mudam).
* Atalhos levam os fechamentos completos para o Relatorio Comentado (Comparativo).
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import db  # noqa: E402
import fechamento_ano as fa  # noqa: E402
import selecao_periodos  # noqa: E402

usuario = usuario_atual()
sidebar_contexto(usuario)
conn = get_conn()

st.title("📆 Fechamento do ano")
st.caption(
    "O que já foi importado e o que ainda falta subir em cada fechamento do ano. Os PDFs sobem em "
    "**Importar PDF** (a empresa e o período são reconhecidos pelo próprio PDF) e a grade se preenche sozinha."
)

_SIMBOLO = {
    fa.ESTADO_COMPLETO: "✅ BP + DRE",
    fa.ESTADO_SO_DRE: "◐ falta BP",
    fa.ESTADO_SO_BP: "◐ falta DRE",
    fa.ESTADO_AUSENTE: "— falta subir",
}


def _flash(nivel: str, texto: str) -> None:
    st.session_state.setdefault("_fech_msgs", []).append((nivel, texto))


for _nivel, _texto in st.session_state.pop("_fech_msgs", []):
    {"ok": st.success, "warn": st.warning, "erro": st.error}[_nivel](_texto)


def _log_seguro(nivel, mensagem, cod=None, periodo=None, detalhe=None):
    try:
        db.registrar_evento(conn, "fechamento_ano", nivel, mensagem, empresa_codigo=cod,
                            periodo=periodo, usuario=usuario, detalhe=detalhe)
    except Exception:
        pass


hoje = date.today()
col_a, col_b = st.columns([1, 3])
ano = col_a.selectbox("Ano", list(range(hoje.year, hoje.year - 5, -1)), key="fechamento_ano_sel")
cods_todos = [cod for cod, _n, _c in EMPRESAS_FIXAS]
nome_por_cod = {cod: nome for cod, nome, _c in EMPRESAS_FIXAS}
empresas = col_b.multiselect(
    "Empresas", cods_todos, default=cods_todos, format_func=lambda c: f"{nome_por_cod[c]} ({c})",
    key="fechamento_empresas_sel",
)
if not empresas:
    st.warning("Escolha pelo menos 1 empresa.")
    st.stop()

try:
    docs = fa.docs_de_linhas(db.listar_documentos_grade(conn, empresas, ano))
    valores_linhas = db.listar_valores_dre_documentos(conn, empresas, ano, list(fa.CONTAS_CONFERIDAS))
except Exception as exc:
    st.error(f"Não foi possível consultar os fechamentos: {exc}")
    _log_seguro("ERRO", "Falha ao consultar fechamentos do ano", detalhe=str(exc))
    st.stop()

grade = fa.montar_grade(ano, empresas, docs)
slots = fa.slots_do_ano(ano)

# ───────────── 1. Grade ─────────────
st.subheader("1. O que já foi importado")
linhas = []
for cod in empresas:
    linha = {"Empresa": f"{nome_por_cod[cod]} ({cod})"}
    for slot in slots:
        cel = grade[(cod, slot.chave)]
        texto = _SIMBOLO[cel.estado]
        if cel.suspeita:
            texto = "⚠️ rótulo suspeito"
        linha[slot.chave if slot.chave != "ANUAL" else "Anual"] = texto
    linhas.append(linha)
st.dataframe(pd.DataFrame(linhas).set_index("Empresa"), width="stretch")
st.caption(
    "1T-4T = trimestres · 1S/2S = semestres · 9M = acumulado de 9 meses (até setembro) · Anual = exercício. "
    "“falta subir” é só informação: nem todo fechamento precisa existir. "
    "Cada PDF é um fechamento próprio — o sistema não soma nem deriva números de um período a partir de outros."
)
resumo = fa.resumo_por_empresa(grade, empresas)
st.caption(" · ".join(
    f"{cod}: {resumo[cod]['completos']} completos, {resumo[cod]['parciais']} parciais, {resumo[cod]['faltando']} sem arquivo"
    for cod in empresas
))
if st.button("📥 Ir para Importar PDF", key="fech_ir_importar"):
    st.switch_page("telas/1_Importar_PDF.py")

# ───────────── 2. Rótulo suspeito ─────────────
sugestoes = fa.sugestoes_correcao(ano, empresas, docs)
if sugestoes:
    st.subheader("2. Rótulo diferente do que o PDF declara")
    st.caption(
        "O PDF informa o intervalo exato (ex.: 01/01 a 30/09). Quando o rótulo gravado não bate com ele, o "
        "relatório recusa o documento. A correção troca só o rótulo (BP e DRE do mesmo fechamento); os números "
        "não mudam, e fica registrada no log do sistema."
    )
    for sg in sugestoes:
        nome_g = selecao_periodos.rotulo_granularidade
        with st.container(border=True):
            st.write(
                f"**{nome_por_cod.get(sg.empresa, sg.empresa)}** — fechamento em **{sg.fim.strftime('%d/%m/%Y')}**: "
                f"gravado como **{nome_g(sg.gravada) or 'não declarada'}**, mas o PDF declara "
                f"{sg.inicio.strftime('%d/%m/%Y')} a {sg.fim.strftime('%d/%m/%Y')} → **{nome_g(sg.declarada)}**."
            )
            chave = f"{sg.empresa}_{sg.fim.isoformat()}_{sg.gravada}_{sg.declarada}"
            ok = st.checkbox(
                f"Confirmo: trocar o rótulo para {nome_g(sg.declarada)}", key=f"fech_conf_{chave}",
            )
            if st.button("Corrigir granularidade", key=f"fech_corrigir_{chave}", disabled=not ok):
                try:
                    res = db.corrigir_granularidade_documento(
                        conn, sg.empresa, sg.fim, sg.gravada, sg.declarada, usuario=usuario,
                    )
                    _flash("ok", f"{sg.empresa} {sg.fim.strftime('%d/%m/%Y')}: rótulo corrigido para "
                                 f"{nome_g(sg.declarada)} ({res['lancamentos']} linhas).")
                except Exception as exc:
                    _flash("erro", f"Não foi possível corrigir: {exc}")
                    _log_seguro("ERRO", "Falha ao corrigir granularidade", cod=sg.empresa, periodo=sg.fim, detalhe=str(exc))
                st.rerun()

# ───────────── 3. Conferências (só aviso) ─────────────
st.subheader("3. Conferências entre períodos")
avisos = fa.conferir_composicoes(ano, fa.valores_de_linhas(ano, valores_linhas))
st.caption(
    "Compara valores já gravados: 1T + 2T = 1S · 3T + 4T = 2S · 1S + 3T = 9M · 9M + 4T = Anual · 1S + 2S = Anual "
    "(Receita Bruta, Receita Líquida, Lucro Bruto e Lucro Líquido). É **só aviso**: não trava importação nem relatório "
    "(o número pode ter vindo diferente da própria contabilidade)."
)
if avisos:
    for av in avisos:
        st.warning(av.mensagem)
else:
    st.success("Nenhuma divergência entre os períodos que podem ser comparados (ou ainda não há períodos suficientes).")

# ───────────── 4. Atalhos de relatório ─────────────
st.subheader("4. Montar relatório com estes fechamentos")
st.caption("Abre o Relatório Comentado (Comparativo) já com empresas e períodos marcados — você confere e gera por lá.")
nome_preset = st.selectbox("Atalho", list(fa.PRESETS_RELATORIO), key="fech_preset_sel")
pares, faltando = fa.pares_do_preset(nome_preset, ano, empresas, grade)
if faltando:
    st.info("Fora do atalho por falta de BP+DRE: " + ", ".join(faltando) + ".")
if len(pares) < 2:
    st.warning("O Comparativo precisa de pelo menos 2 fechamentos completos nas empresas escolhidas.")
elif len(pares) > 4:
    pares = pares[:4]
if st.button("📄 Abrir no Relatório Comentado", key="fech_abrir_relatorio", disabled=len(pares) < 2):
    st.session_state["relatorio_preset"] = {"empresas": list(empresas), "pares": [tuple(p) for p in pares]}
    st.switch_page("telas/8_Relatorio_Comentado.py")
