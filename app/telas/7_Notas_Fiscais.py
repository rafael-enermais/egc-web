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

try:
    _ultima_sync = nf_sienge.ultima_sincronizacao(conn)
except Exception:
    _ultima_sync = None

if _ultima_sync:
    st.caption(f"🔄 Última sincronização automática: {_ultima_sync.strftime('%d/%m/%Y %H:%M')} "
               "(roda sozinha todo dia às 04h -- use o botão abaixo só se precisar de dado mais fresco agora)")
else:
    st.error(
        "Ainda não há nenhuma sincronização registrada com o Sienge. "
        "Clique em \"Atualizar do Sienge\" abaixo -- o passo 2 (subir manifesto/rodar "
        "conferência) só libera depois da primeira sincronização, pra não gerar "
        "\"não encontrada\" em massa por falta de dado, não por nota realmente pendente."
    )

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

_sync_liberado = _ultima_sync is not None
if not _sync_liberado:
    st.caption("🔒 Bloqueado até a primeira sincronização com o Sienge (passo 1 acima).")

arquivo = st.file_uploader("Planilha do manifesto", type=["xlsx"], key="nf_upload", disabled=not _sync_liberado)

# FIX_20260928 (Rafael perguntou: "esse período de referência melhor
# setar manual ou conseguimos puxar isso da planilha?"): a planilha NÃO
# tem 1 período único (cada nota tem sua própria data de emissão, e um
# mesmo arquivo pode misturar competências) -- ver docstring de
# nf_parser.sugerir_periodo_referencia. Por isso continua editável e
# manual (nunca é gravado sozinho sem o clique em "Rodar conferência"),
# mas agora pré-preenche com o mês/ano que aparece na MAIORIA das notas
# do arquivo assim que o upload acontece, pra não obrigar a contadora a
# digitar a mesma coisa toda vez -- ela sempre pode apagar e trocar.
_arquivo_sig = f"{arquivo.name}:{arquivo.size}" if arquivo is not None else None
if arquivo is not None and st.session_state.get("nf_periodo_sugestao_sig") != _arquivo_sig:
    try:
        _df_peek = nf_parser.ler_manifesto_xlsx(io.BytesIO(arquivo.getvalue()))
        _sugestao = nf_parser.sugerir_periodo_referencia(_df_peek)
        if _sugestao and not st.session_state.get("nf_periodo_ref"):
            st.session_state["nf_periodo_ref"] = _sugestao
    except Exception:
        pass  # planilha invalida aqui so' significa "sem sugestao" -- o erro de verdade aparece so' no "Rodar conferência"
    st.session_state["nf_periodo_sugestao_sig"] = _arquivo_sig

periodo_referencia = st.text_input("Período de referência (ex.: 08/2026)", key="nf_periodo_ref",
                                    disabled=not _sync_liberado)
if arquivo is not None and st.session_state.get("nf_periodo_sugestao_sig") == _arquivo_sig and periodo_referencia:
    st.caption("💡 Sugerido a partir das datas de emissão do arquivo -- confira antes de rodar.")

if arquivo is not None and st.button("▶️ Rodar conferência", key="nf_btn_rodar", disabled=not _sync_liberado):
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

    msg_final = (f"Conferência concluída: {resumo['total']} nota(s) · "
                 f"{resumo['lancadas']} lançada(s) · {resumo['pendencias']} pendência(s).")
    if resumo.get("orfaos_sienge"):
        # FIX_20260928f (Rafael: "a nota q estiver no Sienge, e não tiver
        # na receita, tem q virar pendencia tb"): so' > 0 quando ja' existe
        # pelo menos 1 match LANCADA anterior desta empresa (ver docstring
        # de identificar_e_gravar_bills_orfaos) -- por isso avisa aqui em
        # vez de deixar passar batido dentro do numero de "pendencias" normal.
        msg_final += f" · ⚠️ {resumo['orfaos_sienge']} título(s) no Sienge sem nota no manifesto."
    st.success(msg_final)

st.divider()

# ─────────────────────────── 3. Resultado da última conferência ───────────────
st.subheader("3. Resultado da conferência")
import_id_atual = st.session_state.get("nf_ultimo_import_id")

if not import_id_atual:
    st.caption("Rode uma conferência acima pra ver o resultado aqui.")
else:
    tabela = nf_sienge.listar_conciliacao(conn, import_id_atual)
    # FIX_20260928f (direção reversa, pedido do Rafael): órfãos do Sienge
    # entram na MESMA tabela (origem='SIENGE_ORFAO' os distingue) pra
    # herdar de graça o filtro/dataframe/export/status já existentes
    # abaixo, em vez de duplicar toda essa lógica pra uma 2ª tabela.
    try:
        orfaos = nf_sienge.listar_orfaos_sienge(conn, import_id_atual)
    except Exception:
        orfaos = pd.DataFrame()
    if not orfaos.empty:
        tabela = pd.concat([tabela, orfaos], ignore_index=True)

    if tabela.empty:
        st.caption("Sem notas nesta rodada.")
    else:
        tabela_manifesto = tabela[tabela["origem"] == "MANIFESTO"]
        total = len(tabela_manifesto)
        lancadas = int((tabela_manifesto["status"] == "LANCADA").sum())
        pendentes = total - lancadas
        orfaos_count = int((tabela["origem"] == "SIENGE_ORFAO").sum())

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Notas no manifesto", total)
        k2.metric("Lançadas no Sienge", lancadas)
        k3.metric("Pendências (nota sem Sienge)", pendentes)
        k4.metric("Sienge sem nota", orfaos_count,
                  help="Título lançado no Sienge sem nota correspondente no manifesto da Receita "
                       "(pedido do Rafael 28/09/2026 -- rede de segurança, não é pra acontecer).")
        k5.metric("Taxa de conciliação", formatacao.pct_br(lancadas / total if total else None))

        filtro_status = st.multiselect(
            "Filtrar por status", options=sorted(tabela["status"].unique()),
            default=[s for s in tabela["status"].unique() if s != "LANCADA"] or list(tabela["status"].unique()),
            key="nf_filtro_status",
        )
        tabela_filtrada = tabela[tabela["status"].isin(filtro_status)] if filtro_status else tabela

        # registro_id/origem sao internos (usados so' pelo "Salvar status"
        # abaixo, via pendencias_df) -- fora da tela/planilha que a
        # contadora ve, pra nao acrescentar coluna tecnica sem sentido pra ela.
        colunas_internas = ["registro_id", "origem"]
        tabela_fmt = tabela_filtrada.drop(columns=colunas_internas).copy()
        tabela_fmt["valor"] = tabela_fmt["valor"].apply(formatacao.moeda_br)
        tabela_fmt["sienge_valor"] = tabela_fmt["sienge_valor"].apply(formatacao.moeda_br)
        st.dataframe(tabela_fmt, hide_index=True, use_container_width=True)

        pendencias_df = tabela[tabela["status"] != "LANCADA"]
        if not pendencias_df.empty:
            buffer = io.BytesIO()
            # FIX_20260928 (Rafael, "Rodar conferência" quebrando com
            # ValueError ao baixar a planilha de pendências): atualizado_em
            # vem do banco como timestamptz (schema.sql) -> psycopg2
            # devolve datetime timezone-aware -> openpyxl nao aceita
            # datetime com timezone no .xlsx (ver docstring de
            # formatacao.remover_timezone_para_excel). Nao muda o que
            # aparece na tela (st.dataframe, linha acima, aceita tz
            # normalmente) -- so' a exportacao precisa do tratamento.
            export_df = formatacao.remover_timezone_para_excel(pendencias_df.drop(columns=colunas_internas))
            export_df.to_excel(buffer, index=False, sheet_name="Pendencias")
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
                    # FIX_20260928f: antes buscava manifesto_id via SQL
                    # casando por numero_nota (texto) -- frágil (2 notas
                    # podem ter o mesmo número) e não cobria os órfãos do
                    # Sienge (não têm manifesto_id nenhum). listar_conciliacao/
                    # listar_orfaos_sienge já trazem registro_id + origem
                    # prontos, direto da linha selecionada.
                    linha = pendencias_df[pendencias_df["numero_nota"] == nota_sel].iloc[0]
                    if linha["origem"] == "SIENGE_ORFAO":
                        nf_sienge.atualizar_status_orfao_sienge(conn, int(linha["registro_id"]), novo_status, usuario)
                    else:
                        nf_sienge.atualizar_status_pendencia(conn, int(linha["registro_id"]), novo_status, usuario)
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
