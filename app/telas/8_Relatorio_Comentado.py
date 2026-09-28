# -*- coding: utf-8 -*-
"""
Tela RELATORIO COMENTADO (Fase 2, 28/09/2026) — 1ª versão da UI que
chama dados_relatorio_comentado.montar_dados_relatorio() +
gerador_relatorio_comentado.gerar_pdf_completo() com dado REAL do
Supabase, fechando o objetivo original do projeto ("sistema capaz de
gerar relatorios, guardar historicos... facilitar o preenchimento").

Escopo desta 1ª versão: 1 empresa + 1 período por geração (o módulo de
mapeamento já suporta isso; multi-empresa/multi-período do gerador em si
já existe — ver test_gerador_completo.py::test_anexo_multi_coluna... —
mas plugar isso na tela fica pra próxima leva, não decidir sozinho o
desenho de comparação ano-a-ano/grupo sem o Rafael ver a v1 rodando
primeiro).

Inputs editáveis na tela (decisão do Rafael, 24/09 e 28/09): período
(label/extenso) e administrador/contador/e-mail/site — nenhum vem de
tabela fixa nem é inferido automaticamente.
"""
import sys
from pathlib import Path
from datetime import date, datetime

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import db  # noqa: E402
import dados_relatorio_comentado as drc  # noqa: E402
import gerador_relatorio_comentado as g  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}

st.title("📄 Relatório Comentado")

usuario = usuario_atual()
sidebar_contexto(usuario)
conn = get_conn()

st.caption(
    "Gera o Demonstrativo Comentado (PDF) a partir do BP/DRE já importado. "
    "Confira os campos abaixo antes de gerar — nenhum é preenchido sozinho pelo sistema."
)

nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
idx_emp = st.selectbox(
    "Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes_emp[i], key="relatorio_empresa_sel",
)
cod_empresa, nome_empresa, _cnpj = EMPRESAS_FIXAS[idx_emp]

try:
    periodos_ativos = db.listar_periodos(conn, cod_empresa, status="ATIVO")
except Exception as exc:
    st.error(f"Não foi possível consultar os períodos: {exc}")
    st.stop()

if not periodos_ativos:
    st.info(f"{nome_empresa} ainda não tem nenhum período importado (ou todos estão arquivados).")
    st.stop()

periodo_sel = st.selectbox(
    "Período (data de posição do BP)", periodos_ativos,
    format_func=lambda p: p.strftime("%d/%m/%Y"), key="relatorio_periodo_sel",
)

st.divider()
st.subheader("Período — como aparece no relatório")
st.caption(
    "Sem coluna confiável de granularidade no banco pra inferir isso sozinho — confirme o texto "
    "que vai aparecer na capa e no cabeçalho do relatório."
)
col1, col2 = st.columns(2)
with col1:
    periodo_label = st.text_input(
        "Rótulo do período (capa/cabeçalho)", value=periodo_sel.strftime("%m/%Y"),
        key="relatorio_periodo_label", help='Ex.: "1º Semestre 2026", "Junho/2026", "Exercício 2025".',
    )
with col2:
    periodo_extenso = st.text_input(
        "Período por extenso (texto de apoio)", value="", key="relatorio_periodo_extenso",
        help='Ex.: "janeiro a junho de 2026". Pode deixar em branco.',
    )

st.subheader("Administrador, contador e contato")
col3, col4 = st.columns(2)
with col3:
    nome_administrador = st.text_input("Nome do administrador", value="", key="relatorio_nome_admin")
    cargo_administrador = st.text_input("Cargo do administrador", value="Administrador", key="relatorio_cargo_admin")
    email_empresa = st.text_input("E-mail da empresa", value="", key="relatorio_email")
with col4:
    nome_contador = st.text_input("Nome do contador", value="", key="relatorio_nome_contador")
    cargo_contador = st.text_input("Cargo do contador", value="Contador", key="relatorio_cargo_contador")
    site_empresa = st.text_input("Site da empresa", value="", key="relatorio_site")

st.divider()

if st.button("Gerar relatório", type="primary", key="relatorio_gerar_btn"):
    admin = dict(
        nome_administrador=nome_administrador, cargo_administrador=cargo_administrador or "Administrador",
        nome_contador=nome_contador, cargo_contador=cargo_contador or "Contador",
        email_empresa=email_empresa, site_empresa=site_empresa,
    )
    try:
        with st.spinner("Buscando dados e montando o relatório..."):
            dados, incluir_pagina_resultado = drc.montar_dados_relatorio(
                conn, empresa_codigo=cod_empresa, periodo=periodo_sel,
                periodo_label=periodo_label.strip() or periodo_sel.strftime("%m/%Y"),
                periodo_extenso=periodo_extenso.strip(), admin=admin,
            )
            nome_arquivo = f"Demonstrativo_{cod_empresa}_{periodo_sel.strftime('%Y%m%d')}.pdf"
            caminho = f"/tmp/{nome_arquivo}"
            g.gerar_pdf_completo(dados, caminho, incluir_pagina_resultado=incluir_pagina_resultado)
        with open(caminho, "rb") as f:
            pdf_bytes = f.read()
        st.success(
            f"Relatório gerado ({len(pdf_bytes) // 1024} KB, "
            f"{'9' if incluir_pagina_resultado else '8'} páginas)."
        )
        if not incluir_pagina_resultado:
            st.caption(
                "Página 'Formação do Resultado' não incluída — este período não tem CSLL/IRPJ "
                "provisionado como linha própria (regime de lucro presumido)."
            )
        st.download_button(
            "⬇️ Baixar PDF", data=pdf_bytes, file_name=nome_arquivo, mime="application/pdf",
            key="relatorio_download_btn",
        )
        try:
            db.registrar_relatorio_gerado(
                conn, empresas_codigos=[cod_empresa], periodos=[periodo_sel],
                arquivo=nome_arquivo, usuario=usuario,
            )
        except Exception:
            pass  # log e' melhor-esforco -- o PDF ja foi gerado e entregue, nao pode quebrar por causa disso
    except Exception as exc:
        st.error(f"Não foi possível gerar o relatório: {exc}")

st.divider()
with st.expander("📜 Últimas gerações"):
    try:
        historico = db.listar_relatorios_gerados(conn, limite=20)
    except Exception as exc:
        st.caption(f"Não foi possível carregar o histórico: {exc}")
        historico = []
    if not historico:
        st.caption("Nenhum relatório gerado ainda.")
    else:
        for h in historico:
            empresas_txt = ", ".join(NOME_POR_COD.get(c, c) for c in (h["empresas"] or []))
            periodos_txt = ", ".join(p.strftime("%m/%Y") for p in (h["periodos"] or []))
            quando = h["gerado_em"].strftime("%d/%m/%Y %H:%M") if h["gerado_em"] else "—"
            st.caption(f"**{h['arquivo']}** — {empresas_txt} · {periodos_txt} · {quando} · {h['usuario'] or '—'}")
