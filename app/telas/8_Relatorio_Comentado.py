# -*- coding: utf-8 -*-
"""
Tela RELATORIO COMENTADO (Fase 2, 28/09/2026) — 1ª versão da UI que
chama dados_relatorio_comentado.montar_dados_relatorio() +
gerador_relatorio_comentado.gerar_pdf_completo() com dado REAL do
Supabase, fechando o objetivo original do projeto ("sistema capaz de
gerar relatorios, guardar historicos... facilitar o preenchimento").

Escopo original desta 1ª versão: 1 empresa + 1 período por geração.

FIX_20260929h (Rafael, 29/09: "por mim podemos implantar o
multi-periodos ja tb"): tela agora tem 2 modos -- "Período único"
(fluxo de sempre, Modelo A) e "Comparativo" (Modelo B, 2 a 4 períodos
da MESMA empresa, gerador_relatorio_comparativo.py +
drc.montar_dados_relatorio_comparativo). Multi-EMPRESA consolidado
(mais de 1 CNPJ no mesmo relatório) o motor de desenho também suporta
via `empresas_codigos`/logo de grupo, mas fica pra quando houver pedido
concreto -- não decidir sozinho esse desenho sem o Rafael ver o
comparativo de 1 empresa rodando primeiro.

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
import gerador_relatorio_comparativo as gc  # noqa: E402
import formatacao  # noqa: E402

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

st.divider()
# FIX_20260929h (Rafael: "por mim podemos implantar o multi-periodos ja
# tb"): liga o Modelo B (gerador_relatorio_comparativo.py + a nova
# montar_dados_relatorio_comparativo, prontos desde 26-29/09 mas sem UI)
# na tela. Modo "Período único" é o fluxo de sempre, inalterado.
modo = st.radio(
    "Tipo de relatório", ["Período único", "Comparativo (evolução entre períodos)"],
    key="relatorio_modo", horizontal=True,
)

periodo_sel = None
periodo_label = ""
periodo_extenso = ""
periodos_multi: list = []
periodos_labels_multi: list = []
periodo_range_label = ""

if modo == "Período único":
    periodo_sel = st.selectbox(
        "Período (data de posição do BP)", periodos_ativos,
        format_func=lambda p: p.strftime("%d/%m/%Y"), key="relatorio_periodo_sel",
    )

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
else:
    st.subheader("Períodos a comparar")
    st.caption(
        "Escolha de 2 a 4 períodos da mesma empresa (ordem cronológica é ajustada "
        "automaticamente, não importa a ordem do clique) e confirme o rótulo de cada coluna — "
        "mesmo princípio do período único: o sistema não infere o texto sozinho."
    )
    periodos_sel_raw = st.multiselect(
        "Períodos (data de posição do BP)", periodos_ativos,
        format_func=lambda p: p.strftime("%d/%m/%Y"), key="relatorio_periodos_multi",
    )
    periodos_multi = sorted(periodos_sel_raw)

    if periodos_multi and not (2 <= len(periodos_multi) <= 4):
        st.warning("Escolha de 2 a 4 períodos pra gerar o comparativo (motor de desenho aceita esse intervalo).")

    if 2 <= len(periodos_multi) <= 4:
        cols_label = st.columns(len(periodos_multi))
        for i, (col, periodo) in enumerate(zip(cols_label, periodos_multi)):
            rotulo = col.text_input(
                periodo.strftime("%d/%m/%Y"), value=periodo.strftime("%m/%Y"),
                key=f"relatorio_periodo_multi_label_{i}",
            )
            periodos_labels_multi.append(rotulo)
        periodo_range_label = st.text_input(
            "Rótulo do intervalo (capa)",
            value=f"{periodos_labels_multi[0]} A {periodos_labels_multi[-1]}",
            key="relatorio_periodo_range_label",
        )

st.divider()
st.subheader("Administrador, contador e contato")


def _seletor_contato(tipo: str, cargo_padrao: str, col) -> tuple[str, str]:
    """
    FIX_20260928 (Rafael: "normalmente são as mesmas pessoas... conseguimos
    salvar com dropdown e histórico?"): dropdown carregando contatos já
    salvos (egc.contatos_relatorio, bloco 13) + opção "cadastrar novo" +
    expander pra editar o contato selecionado -- sem trocar a decisão de
    24/09 (continua editável na hora de gerar, nome/cargo digitados aqui
    são o que vai pro PDF, independente de estarem ou não salvos). Se a
    migração do bloco 13 ainda não rodou, listar_contatos_relatorio
    devolve [] e a tela cai pro comportamento antigo (só o campo "novo
    contato"), sem quebrar.

    FIX_20260929b (Rafael: "ficou separado, não seria mais fácil manter 1
    lista só pra ambos... a lista única poderia trocar facilmente os
    lados"): `salvos` agora vem da lista INTEIRA de contatos (sem filtro
    por tipo) -- os 2 seletores (ADMINISTRADOR e CONTADOR) compartilham o
    mesmo dropdown de opções, então um contato salvo de um lado aparece
    pronto pro outro também, sem recadastrar.
    NOVO_SENTINELA: opção fixa pra "não é nenhum salvo, vou digitar" --
    string improvável de colidir com nome real, nunca gravada no banco.
    """
    NOVO_SENTINELA = "➕ Cadastrar novo…"
    try:
        salvos = db.listar_contatos_relatorio(conn)
    except Exception:
        salvos = []

    opcoes = [NOVO_SENTINELA] + [f"{c['nome']} — {c['cargo']}" for c in salvos]
    sel = col.selectbox(f"{tipo.capitalize()}", opcoes, key=f"relatorio_contato_sel_{tipo}")

    if sel == NOVO_SENTINELA:
        nome = col.text_input("Nome", value="", key=f"relatorio_novo_nome_{tipo}")
        cargo = col.text_input("Cargo", value=cargo_padrao, key=f"relatorio_novo_cargo_{tipo}")
        email = col.text_input("E-mail (opcional, só cadastro)", value="", key=f"relatorio_novo_email_{tipo}")
        if col.button("💾 Salvar contato", key=f"relatorio_salvar_novo_{tipo}", disabled=not nome.strip()):
            novo_id = db.salvar_contato_relatorio(
                conn, tipo=tipo, nome=nome.strip(), cargo=cargo.strip() or cargo_padrao,
                email=email.strip() or None, usuario=usuario,
            )
            if novo_id is not None:
                col.success(f"{nome} salvo — já aparece no dropdown na próxima vez.")
            else:
                col.warning(
                    "Não consegui salvar (tabela egc.contatos_relatorio ainda não existe no banco -- "
                    "rode o bloco 13 do schema.sql). O relatório usa o nome/cargo digitados aqui normalmente."
                )
        return nome, cargo or cargo_padrao

    contato = next(c for c in salvos if f"{c['nome']} — {c['cargo']}" == sel)
    with col.expander(f"✏️ Editar {contato['nome']}"):
        novo_nome = st.text_input("Nome", value=contato["nome"], key=f"relatorio_edit_nome_{tipo}_{contato['id']}")
        novo_cargo = st.text_input("Cargo", value=contato["cargo"], key=f"relatorio_edit_cargo_{tipo}_{contato['id']}")
        novo_email = st.text_input(
            "E-mail (opcional, só cadastro)", value=contato.get("email") or "",
            key=f"relatorio_edit_email_{tipo}_{contato['id']}",
        )
        if st.button("💾 Salvar edição", key=f"relatorio_salvar_edit_{tipo}_{contato['id']}"):
            # tipo=contato["tipo"] (não o `tipo` do seletor atual): como a
            # lista agora é compartilhada (FIX_20260929b), editar um
            # contato ADMINISTRADOR a partir do seletor CONTADOR não pode
            # silenciosamente trocar o tipo gravado dele.
            db.salvar_contato_relatorio(
                conn, tipo=contato["tipo"], nome=novo_nome.strip(), cargo=novo_cargo.strip(),
                email=novo_email.strip() or None, contato_id=contato["id"], usuario=usuario,
            )
            st.success("Contato atualizado.")
            st.rerun()
    return contato["nome"], contato["cargo"]


col3, col4 = st.columns(2)
nome_administrador, cargo_administrador = _seletor_contato("ADMINISTRADOR", "Administrador", col3)
email_empresa = col3.text_input("E-mail da empresa", value="", key="relatorio_email")
nome_contador, cargo_contador = _seletor_contato("CONTADOR", "Contador", col4)
site_empresa = col4.text_input("Site da empresa", value="", key="relatorio_site")

st.divider()

pode_gerar = modo == "Período único" or (2 <= len(periodos_multi) <= 4)

if st.button("Gerar relatório", type="primary", key="relatorio_gerar_btn", disabled=not pode_gerar):
    admin = dict(
        nome_administrador=nome_administrador, cargo_administrador=cargo_administrador or "Administrador",
        nome_contador=nome_contador, cargo_contador=cargo_contador or "Contador",
        email_empresa=email_empresa, site_empresa=site_empresa,
    )
    try:
        if modo == "Período único":
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
            periodos_para_log = [periodo_sel]
        else:
            range_label = periodo_range_label.strip() or f"{periodos_labels_multi[0]} A {periodos_labels_multi[-1]}"
            with st.spinner("Buscando dados e montando o relatório comparativo..."):
                dados = drc.montar_dados_relatorio_comparativo(
                    conn, empresa_codigo=cod_empresa, periodos=periodos_multi,
                    periodos_labels=[lbl.strip() or p.strftime("%m/%Y") for lbl, p in zip(periodos_labels_multi, periodos_multi)],
                    periodo_range_label=range_label, admin=admin,
                )
                nome_arquivo = (
                    f"Evolucao_{cod_empresa}_{periodos_multi[0].strftime('%Y%m')}"
                    f"_{periodos_multi[-1].strftime('%Y%m')}.pdf"
                )
                caminho = f"/tmp/{nome_arquivo}"
                gc.gerar_pdf_comparativo(dados, caminho)
            with open(caminho, "rb") as f:
                pdf_bytes = f.read()
            st.success(
                f"Relatório comparativo gerado ({len(pdf_bytes) // 1024} KB, 5 páginas, "
                f"{len(periodos_multi)} períodos)."
            )
            periodos_para_log = periodos_multi
        st.download_button(
            "⬇️ Baixar PDF", data=pdf_bytes, file_name=nome_arquivo, mime="application/pdf",
            key="relatorio_download_btn",
        )
        try:
            db.registrar_relatorio_gerado(
                conn, empresas_codigos=[cod_empresa], periodos=periodos_para_log,
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
            quando = formatacao.hora_br(h["gerado_em"])
            st.caption(f"**{h['arquivo']}** — {empresas_txt} · {periodos_txt} · {quando} · {h['usuario'] or '—'}")
