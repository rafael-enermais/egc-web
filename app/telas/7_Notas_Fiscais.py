# -*- coding: utf-8 -*-
"""
Tela NOTAS FISCAIS — conciliação do manifesto de NF-e (planilha da
Receita Federal) contra o Contas a Pagar do Sienge. Import 100% separado
do fluxo BP/DRE (Importar PDF) -- nenhuma tabela egc.lancamentos é lida
ou escrita aqui.

Fluxo (ver EGC 00-handoff.md seção 62 pro desenho completo):
  1. "Atualizar do Sienge" -- sincroniza egc.nf_bills_sync/nf_creditors_sync
     (chamado sob demanda, roda no servidor -- a chave do Sienge nunca
     chega ao navegador da contadora nem ao repo).
  2. Upload do .xlsx do manifesto -> nf_parser decodifica cada linha
     (inclusive a chave de acesso, quando presente) -> grava em
     egc.nf_manifesto_import sob um import_id novo.
  3. Concilia contra o snapshot sincronizado (CNPJ+número+valor, chave
     como confirmação extra) -> grava em egc.nf_conciliacao.
  4. KPIs + tabela de conferência + export .xlsx de pendências (pra
     mandar pro Suprimentos verificar no Sienge) + histórico de rodadas.

Pendências têm lastro: mudar o status (Enviado ao Suprimentos / Resolvido
/ Descartado) NUNCA apaga a linha, só atualiza egc.nf_conciliacao.pendencia_status
com quem/quando mudou -- histórico completo fica em nf_import_historico.
"""
import io
import sys
import datetime as _dt
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import db  # noqa: E402
import nf_parser  # noqa: E402
import nf_sienge  # noqa: E402
import formatacao  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}

usuario = usuario_atual()
sidebar_contexto(usuario)

st.title("Notas Fiscais × Sienge")
st.caption("Concilia o manifesto de NF-e (planilha da Receita Federal) contra o Contas a Pagar do Sienge.")

conn = get_conn()


def _log_erro(mensagem: str, detalhe: str = "", empresa_codigo: str | None = None) -> None:
    try:
        db.registrar_evento(conn, "notas_fiscais", "ERRO", mensagem, usuario=usuario,
                             detalhe=detalhe or None, empresa_codigo=empresa_codigo)
    except Exception:
        pass


def _log_info(mensagem: str, empresa_codigo: str | None = None) -> None:
    try:
        db.registrar_evento(conn, "notas_fiscais", "INFO", mensagem, usuario=usuario, empresa_codigo=empresa_codigo)
    except Exception:
        pass


nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
idx = st.selectbox("Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes_emp[i], key="nf_empresa_sel")
cod_empresa, nome_empresa, _cnpj_empresa = EMPRESAS_FIXAS[idx]

# ─────────────────────────── 1. Atualizar do Sienge ───────────────────────────
st.subheader("1. Atualizar dados do Sienge")
col_ini, col_fim, col_btn = st.columns([1, 1, 1])
hoje = _dt.date.today()
data_inicio = col_ini.date_input("De", value=hoje.replace(day=1) - _dt.timedelta(days=1), key="nf_data_ini")
data_fim = col_fim.date_input("Até", value=hoje, key="nf_data_fim")

if col_btn.button("🔄 Atualizar do Sienge", key="nf_btn_sync"):
    try:
        base_url = st.secrets["SIENGE_BASE_URL"]
        sienge_user = st.secrets["SIENGE_USER"]
        sienge_pass = st.secrets["SIENGE_PASSWORD"]
    except Exception:
        st.error(
            "Secrets `SIENGE_BASE_URL` / `SIENGE_USER` / `SIENGE_PASSWORD` não configurados. "
            "Configure em `.streamlit/secrets.toml` (local) ou Settings → Secrets (Streamlit Cloud)."
        )
        st.stop()

    with st.spinner("Sincronizando títulos e credores do Sienge..."):
        try:
            n_bills = nf_sienge.sincronizar_bills(conn, base_url, sienge_user, sienge_pass, data_inicio, data_fim)
            n_cred = nf_sienge.sincronizar_creditores(conn, base_url, sienge_user, sienge_pass)
            st.success(f"Sincronizado: {n_bills} título(s) e {n_cred} credor(es).")
            _log_info(f"Sync Sienge: {n_bills} títulos, {n_cred} credores ({data_inicio} a {data_fim})", cod_empresa)
        except Exception as exc:
            st.error(f"Falha ao sincronizar com o Sienge: {exc}")
            _log_erro("Falha na sincronização com o Sienge", detalhe=str(exc), empresa_codigo=cod_empresa)

st.divider()

# ─────────────────────────── 2. Upload do manifesto ───────────────────────────
st.subheader("2. Subir o manifesto de NF-e (.xlsx da Receita Federal)")
periodo_referencia = st.text_input("Período de referência (ex.: 08/2026)", key="nf_periodo_ref")
arquivo = st.file_uploader("Planilha do manifesto", type=["xlsx"], key="nf_upload")

if arquivo is not None and st.button("▶️ Rodar conferência", key="nf_btn_rodar"):
    if not periodo_referencia.strip():
        st.warning("Informe o período de referência antes de rodar a conferência.")
        st.stop()
    try:
        df = nf_parser.ler_manifesto_xlsx(io.BytesIO(arquivo.getvalue()))
    except nf_parser.ManifestoInvalido as exc:
        st.error(f"Planilha inválida: {exc}")
        _log_erro("Manifesto inválido (parser)", detalhe=str(exc), empresa_codigo=cod_empresa)
        st.stop()
    except Exception as exc:
        st.error(f"Não consegui ler a planilha: {exc}")
        _log_erro("Falha ao ler manifesto .xlsx", detalhe=str(exc), empresa_codigo=cod_empresa)
        st.stop()

    with st.spinner(f"Gravando {len(df)} nota(s) e conciliando contra o Sienge..."):
        try:
            import_id = nf_sienge.gravar_manifesto(conn, cod_empresa, periodo_referencia.strip(), df,
                                                     arquivo.name, usuario)
            resumo = nf_sienge.conciliar_import(conn, import_id)
            nf_sienge.gravar_historico_import(conn, import_id, cod_empresa, periodo_referencia.strip(),
                                               arquivo.name, usuario, resumo)
            st.session_state["nf_ultimo_import_id"] = import_id
            _log_info(f"Conferência rodada: {resumo['total']} notas, {resumo['lancadas']} lançadas, "
                      f"{resumo['pendencias']} pendências ({periodo_referencia})", cod_empresa)
        except Exception as exc:
            st.error(f"Falha ao rodar a conferência: {exc}")
            _log_erro("Falha ao gravar/conciliar manifesto", detalhe=str(exc), empresa_codigo=cod_empresa)
            st.stop()

    st.success(f"Conferência concluída: {resumo['total']} nota(s) · "
               f"{resumo['lancadas']} lançada(s) · {resumo['pendencias']} pendência(s).")

st.divider()

# ─────────────────────────── 3. Resultado da última conferência ───────────────
st.subheader("3. Resultado da conferência")
import_id_atual = st.session_state.get("nf_ultimo_import_id")

if not import_id_atual:
    st.caption("Rode uma conferência acima pra ver o resultado aqui.")
else:
    tabela = nf_sienge.listar_conciliacao(conn, import_id_atual)
    if tabela.empty:
        st.caption("Sem notas nesta rodada.")
    else:
        total = len(tabela)
        lancadas = int((tabela["status"] == "LANCADA").sum())
        pendentes = total - lancadas

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Notas no manifesto", total)
        k2.metric("Lançadas no Sienge", lancadas)
        k3.metric("Pendências", pendentes)
        k4.metric("Taxa de conciliação", formatacao.pct_br(lancadas / total if total else None))

        filtro_status = st.multiselect(
            "Filtrar por status", options=sorted(tabela["status"].unique()),
            default=[s for s in tabela["status"].unique() if s != "LANCADA"] or list(tabela["status"].unique()),
            key="nf_filtro_status",
        )
        tabela_filtrada = tabela[tabela["status"].isin(filtro_status)] if filtro_status else tabela

        tabela_fmt = tabela_filtrada.copy()
        tabela_fmt["valor"] = tabela_fmt["valor"].apply(formatacao.moeda_br)
        tabela_fmt["sienge_valor"] = tabela_fmt["sienge_valor"].apply(formatacao.moeda_br)
        st.dataframe(tabela_fmt, hide_index=True, use_container_width=True)

        pendencias_df = tabela[tabela["status"] != "LANCADA"]
        if not pendencias_df.empty:
            buffer = io.BytesIO()
            pendencias_df.to_excel(buffer, index=False, sheet_name="Pendencias")
            st.download_button(
                "⬇️ Baixar planilha de pendências (pra mandar ao Suprimentos)",
                data=buffer.getvalue(),
                file_name=f"pendencias_nf_{cod_empresa}_{periodo_referencia or 'periodo'}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="nf_download_pendencias",
            )

        with st.expander("Atualizar status de uma pendência (lastro até corrigir no Sienge)"):
            st.caption(
                "Marca o acompanhamento da pendência (não lança nada no Sienge, é só controle "
                "aqui) -- fica registrado quem mudou e quando."
            )
            if pendencias_df.empty:
                st.caption("Sem pendência nesta rodada.")
            else:
                nota_sel = st.selectbox(
                    "Nota", pendencias_df["numero_nota"].tolist(), key="nf_pendencia_nota_sel",
                )
                novo_status = st.selectbox(
                    "Novo status", ["PENDENTE", "ENVIADO_SUPRIMENTOS", "RESOLVIDO", "DESCARTADO"],
                    key="nf_pendencia_status_sel",
                )
                if st.button("Salvar status", key="nf_btn_salvar_status"):
                    linha = pendencias_df[pendencias_df["numero_nota"] == nota_sel].iloc[0]
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT manifesto_id FROM egc.nf_conciliacao WHERE import_id = %s AND "
                            "manifesto_id IN (SELECT id FROM egc.nf_manifesto_import WHERE numero_nota = %s AND import_id = %s)",
                            (import_id_atual, nota_sel, import_id_atual),
                        )
                        row = cur.fetchone()
                    if row:
                        nf_sienge.atualizar_status_pendencia(conn, row[0], novo_status, usuario)
                        st.success(f"Nota {nota_sel} marcada como {novo_status}.")
                        _log_info(f"Pendência nota {nota_sel} -> {novo_status}", cod_empresa)
                        st.rerun()

st.divider()

# ─────────────────────────── 4. Histórico de conferências ───────────────────────────
st.subheader("4. Histórico de conferências")
try:
    historico = nf_sienge.listar_historico_importacoes(conn, cod_empresa)
except Exception as exc:
    st.warning(f"Não foi possível carregar o histórico: {exc}")
    _log_erro("Falha ao listar histórico de conferências", detalhe=str(exc), empresa_codigo=cod_empresa)
    historico = []

if not historico:
    st.caption("Nenhuma conferência rodada ainda pra essa empresa.")
else:
    df_hist = pd.DataFrame(historico)[
        ["criado_em", "periodo_referencia", "total_notas", "total_lancadas", "total_pendencias",
         "arquivo_nome", "usuario"]
    ]
    df_hist["taxa_conciliacao"] = (df_hist["total_lancadas"] / df_hist["total_notas"]).apply(formatacao.pct_br)
    st.dataframe(df_hist, hide_index=True, use_container_width=True)
