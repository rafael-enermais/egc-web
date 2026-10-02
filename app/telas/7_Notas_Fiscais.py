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
from conexao import sidebar_contexto, get_conn, EMPRESAS_NF, flash, mostrar_flash  # noqa: E402
import db  # noqa: E402
import nf_parser  # noqa: E402
import nf_sienge  # noqa: E402
import nf_export  # noqa: E402
import formatacao  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_NF}

usuario = usuario_atual()
sidebar_contexto(usuario)

st.title("Notas Fiscais × Sienge")
st.caption("Concilia o manifesto de NF-e (planilha da Receita Federal) contra o Contas a Pagar do Sienge.")
mostrar_flash()

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


def _ao_subir_arquivo():
    """on_change do uploader (roda ANTES do script, por isso pode mexer nas
    chaves dos widgets de empresa e periodo). v0.44.3: cada arquivo novo
    refaz a sugestao de periodo (antes so' a 1a vez -- subir outra planilha
    mantinha o periodo antigo) e seleciona a empresa pelo CNPJ da coluna
    Filial do manifesto."""
    f = st.session_state.get("nf_upload")
    if f is None:
        st.session_state["nf_upload_info"] = None
        return
    info = nf_parser.analisar_upload(f.getvalue(), EMPRESAS_NF)
    info["arquivo"] = f.name
    st.session_state["nf_upload_info"] = info
    st.session_state["nf_periodo_ref"] = info.get("periodo") or ""
    if info.get("empresa"):
        codigos = [c for c, _n, _c in EMPRESAS_NF]
        st.session_state["nf_empresa_sel"] = codigos.index(info["empresa"])


nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_NF]
idx = st.selectbox("Empresa", range(len(EMPRESAS_NF)), format_func=lambda i: nomes_emp[i], key="nf_empresa_sel")
cod_empresa, nome_empresa, _cnpj_empresa = EMPRESAS_NF[idx]

# ─────────────────────────── 1. Atualizar do Sienge ───────────────────────────
st.subheader("1. Atualizar dados do Sienge")

try:
    _ultima_sync = nf_sienge.ultima_sincronizacao(conn)
except Exception:
    _ultima_sync = None

if _ultima_sync:
    st.caption(f"🔄 Última sincronização automática: {formatacao.hora_br(_ultima_sync)} "
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
            n_bills = sum(nf_sienge.sincronizar_bills_por_mes(conn, base_url, sienge_user, sienge_pass, data_inicio, data_fim).values())
            n_cred = nf_sienge.sincronizar_creditores(conn, base_url, sienge_user, sienge_pass)
            st.success(f"Sincronizado: {n_bills} título(s) e {n_cred} credor(es).")
            _log_info(f"Sync Sienge: {n_bills} títulos, {n_cred} credores ({data_inicio} a {data_fim})", cod_empresa)
        except Exception as exc:
            st.error(f"Falha ao sincronizar com o Sienge: {exc}")
            _log_erro("Falha na sincronização com o Sienge", detalhe=str(exc), empresa_codigo=cod_empresa)

# ───────────── Empresa de cada devedor do Sienge (confere lançamento na empresa certa) ─────────────
with st.expander("🏢 Empresa de cada devedor do Sienge — confere se a nota foi lançada na empresa certa"):
    st.caption(
        "O Sienge é uma conta só das 6 empresas: o **devedor** do título diz em qual empresa a nota foi "
        "lançada. Confirme abaixo a empresa de cada devedor (o app sugere pelo histórico). Na conferência, "
        "nota que consta no manifesto de uma empresa mas está lançada no devedor de OUTRA aparece como "
        "**LANCADA_OUTRA_EMPRESA** (pendência). Devedor sem empresa confirmada/sugerida não gera alerta."
    )
    try:
        _deb = nf_sienge.listar_debtors_sienge(conn)
    except Exception as exc:
        _deb = None
        st.info(f"Não consegui listar os devedores agora: {exc}")
    if _deb is not None and _deb.empty:
        st.caption("Nenhum título NFE/NF sincronizado ainda.")
    elif _deb is not None:
        _opcoes = ["(sem confirmação)"] + [cod for cod, _n, _c in EMPRESAS_NF]
        for _, d in _deb.iterrows():
            c1, c2, c3, c4 = st.columns([1, 1, 2, 1])
            c1.markdown(f"**Devedor {int(d['debtor_id'])}**")
            c2.caption(f"{int(d['titulos'])} título(s)")
            atual = d["empresa_confirmada"] if isinstance(d["empresa_confirmada"], str) else "(sem confirmação)"
            sug = d["empresa_aprendida"] if isinstance(d["empresa_aprendida"], str) else None
            if sug:
                c3.caption(f"Sugestão do histórico: {sug}")
            elif isinstance(d.get("historico_ambiguo"), str):
                c3.caption(f"⚠️ Histórico ambíguo: {d['historico_ambiguo']} -- confirme à mão")
            else:
                c3.caption("Sugestão do histórico: —")
            nova = c4.selectbox("Empresa", _opcoes, index=_opcoes.index(atual) if atual in _opcoes else 0,
                                key=f"nf_debtor_{int(d['debtor_id'])}", label_visibility="collapsed")
            if nova != atual:
                try:
                    nf_sienge.salvar_mapa_debtor(conn, int(d["debtor_id"]), None if nova == _opcoes[0] else nova, usuario)
                    st.success(f"Devedor {int(d['debtor_id'])} → {nova}. Rode a conferência de novo para reaplicar.")
                except Exception as exc:
                    st.error(f"Não consegui salvar (o bloco 20 do schema já foi rodado?): {exc}")

st.divider()

# ─────────────────────────── 2. Upload do manifesto ───────────────────────────
st.subheader("2. Subir o manifesto de NF-e (.xlsx da Receita Federal)")

_sync_liberado = _ultima_sync is not None
if not _sync_liberado:
    st.caption("🔒 Bloqueado até a primeira sincronização com o Sienge (passo 1 acima).")

arquivo = st.file_uploader("Planilha do manifesto", type=["xlsx"], key="nf_upload", disabled=not _sync_liberado,
                           on_change=_ao_subir_arquivo)

# O periodo NUNCA e' gravado sozinho (so' no clique em "Rodar conferência");
# a sugestao vem do mes mais frequente das datas de emissao do arquivo (ver
# nf_parser.sugerir_periodo_referencia) e a contadora pode trocar.
_info_upload = st.session_state.get("nf_upload_info") if arquivo is not None else None
if _info_upload and _info_upload.get("arquivo") != arquivo.name:
    _info_upload = None  # info de outro arquivo (ex.: sessao antiga) nao vale

periodo_referencia = st.text_input("Período de referência (ex.: 08/2026)", key="nf_periodo_ref",
                                    disabled=not _sync_liberado)
if _info_upload and not _info_upload.get("erro"):
    _linhas = []
    if _info_upload.get("periodo") and periodo_referencia == _info_upload["periodo"]:
        _linhas.append("💡 Período sugerido a partir das datas de emissão do arquivo -- confira antes de rodar.")
    if _info_upload.get("empresa"):
        _linhas.append(f"🏢 Empresa identificada pelo CNPJ do arquivo (coluna Filial): "
                       f"{NOME_POR_COD.get(_info_upload['empresa'], _info_upload['empresa'])}.")
    elif _info_upload.get("raizes"):
        _linhas.append("⚠️ O CNPJ da coluna Filial do arquivo não corresponde a UMA das empresas cadastradas "
                       f"(raízes encontradas: {', '.join(_info_upload['raizes'])}).")
    if _info_upload.get("canceladas"):
        _linhas.append(f"🚫 {_info_upload['canceladas']} nota(s) cancelada(s) no arquivo ficam fora da conferência.")
    if _info_upload.get("entradas"):
        _linhas.append(f"↩️ {_info_upload['entradas']} nota(s) de Entrada (devolução/retorno) ficam fora da conferência.")
    if _info_upload.get("canceladas") or _info_upload.get("entradas"):
        _linhas.append("As que ficaram fora aparecem numa lista no resultado e na aba \"Ignoradas\" da planilha, pra conferir.")
    _comps = _info_upload.get("competencias") or {}
    if len(_comps) > 1:
        _linhas.append("📅 O arquivo tem mais de um mês (" + ", ".join(f"{c}: {n} nota(s)" for c, n in _comps.items())
                       + "): a conferência roda separada por mês, cada um com o seu período -- o campo acima é ignorado.")
    for _l in _linhas:
        st.caption(_l)
elif _info_upload and _info_upload.get("erro"):
    st.warning(f"Não consegui ler a planilha: {_info_upload['erro']}")

if arquivo is not None and st.button("▶️ Rodar conferência", key="nf_btn_rodar", disabled=not _sync_liberado):
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

    # v0.44.3: o arquivo manda -- se a coluna Filial indica outra empresa (ou
    # mistura empresas), nao grava nada na empresa errada.
    _empresa_arquivo = nf_parser.detectar_empresa_manifesto(df, EMPRESAS_NF)
    _raizes_arquivo = sorted({r for r in df["_empresa_raiz"].dropna() if r})
    if _raizes_arquivo and _empresa_arquivo is None:
        st.error("O arquivo tem notas de mais de uma empresa, ou de um CNPJ que não é de nenhuma empresa cadastrada "
                 f"(raízes: {', '.join(_raizes_arquivo)}). Separe o manifesto por empresa e suba de novo.")
        _log_erro("Manifesto com Filial misturada/desconhecida", detalhe=str(_raizes_arquivo), empresa_codigo=cod_empresa)
        st.stop()
    if _empresa_arquivo and _empresa_arquivo != cod_empresa:
        st.error(f"Este arquivo é de {NOME_POR_COD.get(_empresa_arquivo, _empresa_arquivo)} (CNPJ da coluna Filial), "
                 f"mas a empresa selecionada é {nome_empresa}. Selecione a empresa certa e rode de novo.")
        _log_erro("Manifesto de empresa diferente da selecionada",
                  detalhe=f"arquivo={_empresa_arquivo} selecionada={cod_empresa}", empresa_codigo=cod_empresa)
        st.stop()
    df, _contagens, _ignoradas_df = nf_parser.filtrar_manifesto(df)
    if df.empty:
        st.warning("Todas as notas do arquivo ficaram fora da conferência (canceladas/Entrada) -- nada para conferir.")
        st.stop()

    # Um arquivo com varios meses (Ano-Mês da Receita) vira uma conferencia por mes.
    _grupos = nf_parser.dividir_por_competencia(df, _ignoradas_df)
    if len(_grupos) == 1 and not periodo_referencia.strip():
        st.warning("Informe o período de referência antes de rodar a conferência.")
        st.stop()

    _resumos = []
    with st.spinner(f"Gravando {len(df)} nota(s) e conciliando contra o Sienge..."):
        try:
            for _comp, _df_mes, _ign_mes in _grupos:
                _periodo = (periodo_referencia.strip() if len(_grupos) == 1 else _comp) or periodo_referencia.strip()
                if not _periodo:
                    st.error("Há notas sem mês de competência no arquivo e o período de referência está vazio.")
                    st.stop()
                import_id = nf_sienge.gravar_manifesto(conn, cod_empresa, _periodo, _df_mes, arquivo.name, usuario)
                nf_sienge.gravar_ignoradas(conn, import_id, cod_empresa, _periodo, _ign_mes, usuario)
                resumo = nf_sienge.conciliar_import(conn, import_id)
                nf_sienge.gravar_historico_import(conn, import_id, cod_empresa, _periodo, arquivo.name, usuario, resumo)
                _resumos.append((_periodo, resumo))
                st.session_state["nf_ultimo_import_id"] = import_id
                _log_info(f"Conferência rodada: {resumo['total']} notas, {resumo['lancadas']} lançadas, "
                          f"{resumo['pendencias']} pendências, {len(_ign_mes) if _ign_mes is not None else 0} fora da "
                          f"conferência ({_periodo})", cod_empresa)
        except Exception as exc:
            st.error(f"Falha ao rodar a conferência: {exc}")
            _log_erro("Falha ao gravar/conciliar manifesto", detalhe=str(exc), empresa_codigo=cod_empresa)
            st.stop()

    for _periodo, resumo in _resumos:
        msg_final = (f"Conferência de {_periodo} concluída: {resumo['total']} nota(s) · "
                     f"{resumo['lancadas']} lançada(s) · {resumo['pendencias']} pendência(s).")
        if resumo.get("orfaos_sienge"):
            # FIX_20260928f (Rafael: "a nota q estiver no Sienge, e não tiver
            # na receita, tem q virar pendencia tb"): so' > 0 quando ha' devedor
            # mapeado pra empresa (ver avisos_conciliacao) -- por isso avisa aqui
            # em vez de deixar passar batido dentro do numero de "pendencias".
            msg_final += f" · ⚠️ {resumo['orfaos_sienge']} título(s) no Sienge sem nota no manifesto."
        st.success(msg_final)
    if _contagens["canceladas"] or _contagens["entradas"]:
        st.info(f"Ficaram fora da conferência: {_contagens['canceladas']} cancelada(s) e {_contagens['entradas']} "
                "de Entrada (devolução/retorno). A lista está no resultado abaixo e na aba \"Ignoradas\" da planilha.")

st.divider()

# ─────────────────────────── 3. Resultado da última conferência ───────────────
st.subheader("3. Resultado da conferência")

# 01/10/2026: histórico carregado ANTES do resultado (e não só na seção 4)
# pra poder reabrir/baixar qualquer rodada anterior -- antes só a última
# rodada da sessão aparecia aqui, e sumia ao recarregar a página.
try:
    historico = nf_sienge.listar_historico_importacoes(conn, cod_empresa)
except Exception as exc:
    st.warning(f"Não foi possível carregar o histórico: {exc}")
    _log_erro("Falha ao listar histórico de conferências", detalhe=str(exc), empresa_codigo=cod_empresa)
    historico = []

import_id_atual = None
rodada_sel = None
if historico:
    _ids = [str(h["import_id"]) for h in historico]
    _recente = str(st.session_state.get("nf_ultimo_import_id") or "")
    if _recente in _ids and st.session_state.get("_nf_rodada_recente_aplicada") != _recente:
        st.session_state["nf_rodada_sel"] = _recente  # acabou de rodar -> abre essa
        st.session_state["_nf_rodada_recente_aplicada"] = _recente
    if st.session_state.get("nf_rodada_sel") not in _ids:
        st.session_state["nf_rodada_sel"] = _ids[0]
    _por_id = {str(h["import_id"]): h for h in historico}

    def _rotulo_rodada(i):
        h = _por_id[i]
        return (f"{formatacao.hora_br(h['criado_em'])} · período {h['periodo_referencia']} · "
                f"{h['total_notas']} notas · {h['arquivo_nome']}")

    import_id_atual = st.selectbox("Rodada de conferência", _ids, format_func=_rotulo_rodada, key="nf_rodada_sel",
                                    help="A mais recente vem selecionada. Escolha outra para rever ou baixar de novo.")
    rodada_sel = _por_id[import_id_atual]

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
        r = nf_export.resumo_conferencia(tabela)
        por_status = r["por_status"]

        # Avisos de cobertura (Sienge sem devedor da empresa / espelho que nao
        # cobre o periodo) -- "0 Sienge sem manifesto" nunca fica sem explicacao.
        try:
            for _aviso in nf_sienge.avisos_conciliacao(conn, import_id_atual, (rodada_sel or {}).get("empresa_codigo") or cod_empresa):
                st.warning(_aviso)
        except Exception:
            pass

        k1, k2, k3 = st.columns(3)
        k1.metric("Notas no manifesto", r["total"])
        k2.metric("Taxa de conciliação", formatacao.pct_br(r["taxa"]))
        k3.metric("Pendências a tratar", r["pendencias"],
                  help="Tudo que não está lançado corretamente: notas do manifesto com problema + títulos do Sienge "
                       "sem nota. É exatamente o conteúdo da aba/planilha de pendências.")

        # KPI por status = os mesmos grupos do filtro logo abaixo.
        st.caption("Por situação (os mesmos grupos do filtro abaixo)")
        _kpis = [
            ("LANCADA", "Lançadas", "Nota do manifesto com título correspondente no Sienge, na empresa certa."),
            ("LANCADA_OUTRA_EMPRESA", "Lançadas em outra empresa",
             "A nota existe no Sienge, mas no devedor de OUTRA empresa -- possível lançamento na empresa errada."),
            ("VALOR_DIVERGENTE", "Valor divergente", "Mesmo fornecedor e nº de nota, valor diferente do manifesto."),
            ("NUMERO_DIVERGENTE", "Número divergente",
             "Mesmo fornecedor e valor, nº diferente -- provável erro de digitação no Sienge."),
            ("NAO_ENCONTRADA", "Não encontradas", "Nota do manifesto sem nenhum título correspondente no Sienge."),
            ("SIENGE_SEM_MANIFESTO", "Sienge sem manifesto",
             "Título lançado no Sienge (devedor desta empresa) sem nota no manifesto da Receita "
             "-- rede de segurança, não é pra acontecer."),
        ]
        for col, (cod_st, rotulo, ajuda) in zip(st.columns(len(_kpis)), _kpis):
            col.metric(rotulo, por_status.get(cod_st, 0), help=ajuda)

        _status_presentes = [x for x in nf_export.ORDEM_STATUS if x in set(tabela["status"])] + \
            sorted(set(tabela["status"]) - set(nf_export.ORDEM_STATUS))
        filtro_status = st.multiselect(
            "Filtrar por situação", options=_status_presentes,
            format_func=lambda x: f"{nf_export.ROTULO_STATUS.get(x, x)} ({por_status.get(x, 0)})",
            # todos os status por padrao: com LANCADA escondida a coluna
            # "Título Sienge" parecia vazia (so' notas lancadas tem titulo).
            default=_status_presentes,
            key="nf_filtro_status",
        )
        tabela_filtrada = tabela[tabela["status"].isin(filtro_status)] if filtro_status else tabela
        st.caption(f"Mostrando {len(tabela_filtrada)} de {len(tabela)} linha(s).")

        # registro_id/origem sao internos (usados so' pelo "Salvar status"
        # abaixo, via pendencias_df) -- fora da tela/planilha que a
        # contadora ve, pra nao acrescentar coluna tecnica sem sentido pra ela.
        tabela_fmt = nf_export.preparar_tabela(tabela_filtrada)
        for col in nf_export.COLUNAS_MOEDA:
            tabela_fmt[col] = tabela_fmt[col].apply(formatacao.moeda_br)
        if "Título Sienge" in tabela_fmt.columns:
            tabela_fmt["Título Sienge"] = tabela_fmt["Título Sienge"].apply(
                lambda v: "" if pd.isna(v) else str(int(v)))
        st.dataframe(tabela_fmt, hide_index=True, use_container_width=True)

        pendencias_df = tabela[tabela["status"] != "LANCADA"]
        _periodo_arq = (rodada_sel or {}).get("periodo_referencia") or "periodo"
        _periodo_arq = str(_periodo_arq).replace("/", "_")
        # FIX_20260928 (Rafael, "Rodar conferência" quebrando com ValueError ao
        # baixar a planilha): atualizado_em é timestamptz -> openpyxl não aceita
        # tz. nf_export._escrever_aba usa formatacao.remover_timezone_para_excel.
        # 01/10/2026: DOIS downloads. A planilha COMPLETA (todas as notas,
        # lançadas e pendentes, + título/documento do Sienge + títulos do
        # Sienge sem nota) e a de pendências (pra mandar ao Suprimentos).
        try:
            _ign_rodada = nf_sienge.listar_ignoradas(conn, import_id_atual)
        except Exception:
            _ign_rodada = pd.DataFrame()
        try:
            xlsx_completo = nf_export.gerar_xlsx_conferencia(
                tabela, nome_empresa, (rodada_sel or {}).get("periodo_referencia") or "",
                (rodada_sel or {}).get("arquivo_nome"), formatacao.hora_br(_dt.datetime.now()),
                ignoradas=_ign_rodada,
            )
        except Exception as exc:
            xlsx_completo = None
            st.warning(f"Não consegui montar a planilha completa: {exc}")
            _log_erro("Falha ao montar xlsx completo de NF", detalhe=str(exc), empresa_codigo=cod_empresa)
        d1, d2 = st.columns(2)
        if xlsx_completo:
            d1.download_button(
                "⬇️ Baixar conferência COMPLETA (todas as notas + Sienge)",
                data=xlsx_completo,
                file_name=f"conferencia_nf_{cod_empresa}_{_periodo_arq}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="nf_download_completo", type="primary",
            )
        if not pendencias_df.empty:
            buffer = io.BytesIO()
            export_df = formatacao.remover_timezone_para_excel(nf_export.preparar_tabela(pendencias_df))
            export_df.to_excel(buffer, index=False, sheet_name="Pendencias")
            d2.download_button(
                "⬇️ Baixar só as pendências (pra mandar ao Suprimentos)",
                data=buffer.getvalue(),
                file_name=f"pendencias_nf_{cod_empresa}_{_periodo_arq}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="nf_download_pendencias",
            )

        if _ign_rodada is not None and not _ign_rodada.empty:
            with st.expander(f"Notas do arquivo que ficaram FORA da conferência ({len(_ign_rodada)}) -- confira"):
                st.caption("Canceladas e notas de Entrada (devolução/retorno do próprio fornecedor) não geram título a pagar, "
                           "por isso não entram nas pendências. Se alguma não deveria ter saído, avise para ajustarmos a regra.")
                _ign_fmt = _ign_rodada.rename(columns=nf_export.ROTULOS_IGNORADAS).copy()
                if "Valor (manifesto)" in _ign_fmt.columns:
                    _ign_fmt["Valor (manifesto)"] = _ign_fmt["Valor (manifesto)"].apply(formatacao.moeda_br)
                st.dataframe(_ign_fmt, hide_index=True, use_container_width=True)

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
                    flash("ok", f"Nota {nota_sel} marcada como {novo_status}.")
                    _log_info(f"Pendência nota {nota_sel} -> {novo_status}", cod_empresa)
                    st.rerun()

st.divider()

# ─────────────────────────── 4. Histórico de conferências ───────────────────────────
st.subheader("4. Histórico de conferências")
if not historico:
    st.caption("Nenhuma conferência rodada ainda pra essa empresa.")
else:
    df_hist = pd.DataFrame(historico)[
        ["criado_em", "periodo_referencia", "total_notas", "total_lancadas", "total_pendencias",
         "arquivo_nome", "usuario"]
    ]
    df_hist["taxa_conciliacao"] = (df_hist["total_lancadas"] / df_hist["total_notas"]).apply(formatacao.pct_br)
    st.dataframe(df_hist, hide_index=True, use_container_width=True)
