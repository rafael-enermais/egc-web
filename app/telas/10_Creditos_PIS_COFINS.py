# -*- coding: utf-8 -*-
"""
Tela CRÉDITOS PIS/COFINS (v0.50.0, 16/10/2026) -- controle do crédito e do débito de PIS/COFINS a partir do SPED EFD-Contribuições.

Fluxo combinado com o Rafael (16/10/2026):
  1. Sobe o SPED "original" do mês (já com os apontamentos) e o Detalhamento de Débito e Crédito feito pela contadora (.xlsx).
  2. O app cruza os dois (nota a nota, por categoria, por base e na apuração de débitos) e mostra o que bate e o que não bate.
  3. TUDO é editável ("pra não depender se o app erra algo"): cada edição fica salva, com quem/quando e o valor antes, e dá para
     restaurar a linha ao valor calculado. Reenviar um arquivo ARQUIVA o anterior (nada é apagado).
  4. Dashboard: crédito por mês/categoria, diferença original × apontado (jan–jul, carga fixa), débito, crédito utilizado, saldo,
     retenções, a recolher e receita por regime.
Funções puras ficam em sped_contribuicoes / creditos_detalhamento / creditos_conferencia / creditos_graficos / creditos_export;
o banco em creditos_db (bloco 28 do schema.sql).
"""
import sys
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS, empresa_por_cnpj, flash, mostrar_flash  # noqa: E402
import creditos_conferencia as cf  # noqa: E402
import creditos_db as cdb  # noqa: E402
import creditos_detalhamento as cdet  # noqa: E402
import creditos_export as cexp  # noqa: E402
import creditos_graficos as gr  # noqa: E402
import formatacao  # noqa: E402
import nf_export  # noqa: E402
import sped_contribuicoes as sc  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _c in EMPRESAS_FIXAS}
PENDENTES = (cf.S_NUM, cf.S_DIF, cf.S_SO_SPED, cf.S_SO_DET)

usuario = usuario_atual()
sidebar_contexto(usuario)

st.title("Créditos PIS/COFINS")
st.caption("Controle do crédito e do débito de PIS/COFINS a partir do SPED (EFD-Contribuições), conferido com o Detalhamento da contadora.")
mostrar_flash()

conn = get_conn()
if not cdb.tem_bloco28(conn):
    st.warning("Esta tela ainda precisa de uma atualização no banco (bloco 28 do schema.sql — tabelas de créditos PIS/COFINS). "
               "Rode o arquivo `scripts/bloco28_creditos.txt` no SQL Editor do Supabase e recarregue a página.")
    st.stop()


def _tema_escuro() -> bool:
    try:
        return st.context.theme.type != "light"
    except Exception:
        return True


DARK = _tema_escuro()


@st.cache_data(show_spinner="Lendo o SPED...", max_entries=6)
def _ler_sped(conteudo: bytes, nome: str) -> dict:
    return sc.ler_sped(conteudo, nome)


@st.cache_data(show_spinner="Lendo o Detalhamento...", max_entries=6)
def _ler_det(conteudo: bytes, nome: str) -> dict:
    return cdet.ler_detalhamento(conteudo, nome)


def _brl(v) -> str:
    return formatacao.moeda_br(v)


def _cnpj_mask(d: str) -> str:
    return nf_export._cnpj_formatado(d) or d


_N_GRAFICO = [0]


def _grafico(fig) -> None:
    """Chave sequencial: a mesma figura pode aparecer em duas abas (Streamlit recusa elementos com o mesmo id automatico)."""
    _N_GRAFICO[0] += 1
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=f"cred_graf_{_N_GRAFICO[0]}")


codigos = [c for c, _n, _cnpj in EMPRESAS_FIXAS]
emp = st.selectbox("Empresa", codigos, index=0, format_func=lambda c: NOME_POR_COD[c], key="cred_empresa")
st.caption("A estrutura já é de várias empresas; por enquanto o uso é só da Energia.")

tab_envio, tab_conf, tab_dash = st.tabs(["📤 Enviar arquivos", "🔎 Conferência", "📊 Dashboard"])


# ═════════════════════════════ 1. ENVIAR ARQUIVOS ═════════════════════════════
def _vigente(tipo: str, comp: str):
    return next((e for e in cdb.listar_envios(conn, emp) if e["tipo"] == tipo and e["competencia"] == comp), None)


with tab_envio:
    n_up = st.session_state.get("cred_up_n", 0)
    col_s, col_d = st.columns(2)

    with col_s:
        st.subheader("1. SPED do mês")
        st.caption("Arquivo .txt da EFD-Contribuições (o original já com os apontamentos).")
        up_s = st.file_uploader("SPED EFD-Contribuições (.txt)", type=["txt"], key=f"cred_up_sped_{n_up}")
        if up_s is not None:
            r = _ler_sped(up_s.getvalue(), up_s.name)
            cab = r["cabecalho"]
            if r["erro"]:
                st.error(r["erro"])
            else:
                achada = empresa_por_cnpj(_cnpj_mask(cab["cnpj"]))
                if achada is None:
                    st.error(f"O CNPJ do arquivo ({_cnpj_mask(cab['cnpj'])}) não é de nenhuma empresa cadastrada no app.")
                elif achada[0] != emp:
                    st.error(f"Este SPED é da {achada[1]}, mas a empresa escolhida acima é {NOME_POR_COD[emp]}. Troque a empresa e envie de novo.")
                else:
                    res = sc.resumo_apuracao(r["apuracao"])
                    st.success(f"SPED {cab['tipo'].lower()} de **{cab['competencia']}** — {cab['empresa_nome']}")
                    a1, a2 = st.columns(2)
                    a1.metric("Crédito PIS", _brl(res["credito_pis"]))
                    a2.metric("Crédito COFINS", _brl(res["credito_cofins"]))
                    a1.metric("A recolher PIS", _brl(res["a_recolher_pis"]))
                    a2.metric("A recolher COFINS", _brl(res["a_recolher_cofins"]))
                    st.caption(f"{len(r['creditos'])} linhas de crédito · {len(r['receitas'])} linhas de receita"
                               + (f" · retifica o recibo {cab['num_rec_anterior']}" if cab.get("num_rec_anterior") else ""))
                    atual = _vigente("SPED", cab["competencia"])
                    igual = bool(atual) and cdb.sha_vigente(conn, "SPED", emp, cab["competencia"]) == r["sha256"]
                    if igual:
                        st.info(f"Este mesmo arquivo já está gravado como o SPED vigente de {cab['competencia']}.")
                    elif atual:
                        st.warning(f"{cab['competencia']} já tem um SPED vigente (arquivo **{atual['arquivo_nome']}**). "
                                   "Ao gravar, ele será **arquivado** (nada é apagado; dá para restaurar).")
                    if st.button("💾 Gravar este SPED", key="cred_btn_sped", type="primary", disabled=igual):
                        try:
                            cdb.gravar_sped(conn, emp, r, up_s.name, usuario)
                            flash("ok", f"SPED de {cab['competencia']} gravado.")
                            st.session_state["cred_up_n"] = n_up + 1
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Não consegui gravar o SPED: {exc}")

    with col_d:
        st.subheader("2. Detalhamento da contadora")
        st.caption("Planilha .xlsx \"Detalhamento de Débito e Crédito PIS e COFINS\" do mês.")
        up_d = st.file_uploader("Detalhamento (.xlsx)", type=["xlsx"], key=f"cred_up_det_{n_up}")
        if up_d is not None:
            d = _ler_det(up_d.getvalue(), up_d.name)
            if d["erro"]:
                st.error(d["erro"])
            else:
                comp_det = d["competencia"] or st.text_input("Não achei o mês no arquivo. Competência (MM/AAAA)", key="cred_comp_det")
                lin = d["linhas"]
                st.success(f"Detalhamento de **{comp_det or '?'}** — {len(lin)} linhas em {len(d['categorias'])} abas de categoria")
                b1, b2 = st.columns(2)
                b1.metric("Crédito PIS", _brl(lin["pis"].sum()))
                b2.metric("Crédito COFINS", _brl(lin["cofins"].sum()))
                st.caption("Abas lidas: " + ", ".join(d["categorias"]) + (" · com aba de débitos" if not d["debitos"].empty else " · sem aba de débitos"))
                if comp_det:
                    if _vigente("SPED", comp_det) is None:
                        st.info(f"Ainda não há SPED de {comp_det}. Pode gravar o Detalhamento, mas a conferência só aparece depois do SPED.")
                    atual = _vigente("DETALHAMENTO", comp_det)
                    igual_d = bool(atual) and cdb.sha_vigente(conn, "DETALHAMENTO", emp, comp_det) == d["sha256"]
                    if igual_d:
                        st.info(f"Este mesmo arquivo já está gravado como o Detalhamento vigente de {comp_det}.")
                    elif atual:
                        st.warning(f"{comp_det} já tem um Detalhamento vigente (arquivo **{atual['arquivo_nome']}**). "
                                   "Ao gravar, ele será **arquivado** (nada é apagado).")
                    if st.button("💾 Gravar este Detalhamento", key="cred_btn_det", type="primary", disabled=igual_d):
                        try:
                            cdb.gravar_detalhamento(conn, emp, d, up_d.name, usuario, competencia=comp_det)
                            flash("ok", f"Detalhamento de {comp_det} gravado.")
                            st.session_state["cred_up_n"] = n_up + 1
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Não consegui gravar o Detalhamento: {exc}")

    with st.expander("🗃️ Envios gravados (arquivar / restaurar)", expanded=False):
        envios = cdb.listar_envios(conn, emp, incluir_arquivados=True)
        if not envios:
            st.caption("Nenhum arquivo enviado ainda.")
        else:
            tabela = pd.DataFrame([{"Tipo": e["tipo"], "Competência": e["competencia"], "Arquivo": e["arquivo_nome"],
                                    "Enviado por": e["enviado_por"], "Enviado em": formatacao.hora_br(e["enviado_em"]),
                                    "Situação": "Arquivado" if e["arquivado_em"] else "Vigente"} for e in envios])
            st.dataframe(tabela, hide_index=True, width="stretch")
            opcoes = {f"{e['tipo']} · {e['competencia']} · {e['arquivo_nome']} ({'arquivado' if e['arquivado_em'] else 'vigente'})": e for e in envios}
            escolha = st.selectbox("Escolha um envio", list(opcoes), key="cred_sel_envio")
            e = opcoes[escolha]
            ok = st.checkbox("Confirmo: é isso mesmo (nada é apagado)", key="cred_ok_envio")
            if e["arquivado_em"]:
                if st.button("♻️ Restaurar envio", key="cred_btn_rest", disabled=not ok):
                    try:
                        cdb.restaurar_envio(conn, e["tipo"], e["id"], usuario)
                        flash("ok", "Envio restaurado.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
            else:
                if st.button("🗃️ Arquivar envio", key="cred_btn_arq", disabled=not ok):
                    cdb.arquivar_envio(conn, e["tipo"], e["id"], usuario)
                    flash("ok", "Envio arquivado.")
                    st.rerun()


# ═════════════════════════════ 2. CONFERÊNCIA ═════════════════════════════
def _editor(aba: str, df: pd.DataFrame, comp: str, ajustes: list, ordem: list, desabilitadas: list, config: dict, dinamico: bool = False):
    """data_editor + botão Salvar. Devolve True se salvou algo (a tela já foi recarregada nesse caso)."""
    chave, campos, _ = cf.ABAS[aba]
    n = st.session_state.get(f"cred_ed_n_{aba}", 0)
    view = df[[c for c in dict.fromkeys([chave] + ordem) if c in df.columns]].reset_index(drop=True)
    cfg = {chave: None, **config}
    ed = st.data_editor(view, key=f"cred_ed_{aba}_{n}", hide_index=True, width="stretch", disabled=desabilitadas,
                        column_config=cfg, num_rows="dynamic" if dinamico else "fixed")
    c1, c2 = st.columns([1, 3])
    if c1.button("💾 Salvar edições", key=f"cred_save_{aba}", type="primary"):
        e2 = ed.copy()
        if dinamico:
            e2[chave] = e2[chave].map(lambda v: v if isinstance(v, str) and v else f"MANUAL|{uuid.uuid4()}")
        mud = cf.diferencas_edicao(view, e2, chave, [c for c in campos if c in view.columns])
        if not mud:
            st.info("Nenhuma mudança para salvar.")
        else:
            cdb.salvar_ajustes(conn, emp, comp, aba, mud, usuario)
            st.session_state[f"cred_ed_n_{aba}"] = n + 1
            flash("ok", f"{len(mud)} edição(ões) salva(s).")
            st.rerun()
    c2.caption("Clique duas vezes numa célula para editar. As diferenças e o status são recalculados ao salvar; "
               "cada edição fica registrada (quem, quando e o valor antes).")
    return ed


def _moeda_cfg(rotulo: str, editavel: bool = True, help_: str | None = None):
    return st.column_config.NumberColumn(rotulo, format="R$ %.2f", step=0.01, help=help_)


with tab_conf:
    comps = cdb.competencias(conn, emp)
    if not comps:
        st.info("Ainda não há arquivos enviados para esta empresa. Use a aba **Enviar arquivos**.")
    else:
        comp = st.selectbox("Competência", comps, key="cred_comp")
        sped = cdb.carregar_sped(conn, emp, comp)
        det = cdb.carregar_detalhamento(conn, emp, comp)
        ajustes = cdb.carregar_ajustes(conn, emp, comp)

        if sped is None:
            st.warning(f"{comp} tem o Detalhamento, mas ainda não tem o SPED. Envie o SPED para ver a conferência.")
        else:
            res = sc.resumo_apuracao(sped["apuracao"])
            cab = sped["cabecalho"]
            st.caption(f"SPED {str(cab.get('tipo') or '').lower()}: {sped['arquivo']} · enviado por {sped['enviado_por']} em {formatacao.hora_br(sped['enviado_em'])}"
                       + (f" · Detalhamento: {det['arquivo']}" if det else " · Detalhamento: ainda não enviado"))
            if det is None:
                st.info("Falta o Detalhamento da contadora deste mês — por enquanto só dá para ver os números do SPED.")
                k = st.columns(4)
                k[0].metric("Crédito PIS", _brl(res["credito_pis"]))
                k[1].metric("Crédito COFINS", _brl(res["credito_cofins"]))
                k[2].metric("A recolher PIS", _brl(res["a_recolher_pis"]))
                k[3].metric("A recolher COFINS", _brl(res["a_recolher_cofins"]))
                _grafico(gr.fig_creditos_por_categoria_mes(sped["por_categoria"], DARK))
            else:
                base_l, base_c, base_b = cf.conferir_linhas(sped, det), cf.conferir_categorias(sped, det), cf.conferir_bases(sped, det)
                base_t, base_d = cf.conferir_totais(sped, det), cf.conferir_debitos(sped, det)
                L = cf.aplicar_aba("linhas", base_l, ajustes)
                C = cf.aplicar_aba("categorias", base_c, ajustes)
                B = cf.aplicar_aba("bases", base_b, ajustes)
                T = cf.aplicar_aba("totais", base_t, ajustes)
                D = cf.aplicar_aba("debitos", base_d, ajustes)

                pend = L[L["status"].isin(PENDENTES) & ~L["conferido"].astype(bool)] if not L.empty else L
                k = st.columns(5)
                k[0].metric("Crédito PIS (SPED)", _brl(res["credito_pis"]))
                k[1].metric("Crédito COFINS (SPED)", _brl(res["credito_cofins"]))
                k[2].metric("Linhas conferidas", f"{int((L['status'] == cf.S_OK).sum())} de {len(L)}")
                k[3].metric("Pendentes", str(len(pend)), help="Linhas com valor diferente, número diferente ou que só existem de um lado, ainda não marcadas como conferidas.")
                k[4].metric("Edições salvas", str(len({(a['aba'], a['chave']) for a in ajustes})))
                bad = [n for n, t in (("Categorias", C), ("Bases", B), ("Totais", T), ("Débitos", D)) if not t.empty and (t["status"] == cf.S_DIF).any()]
                if bad:
                    st.warning("Há diferença nas abas: " + ", ".join(bad) + ".")
                elif len(pend) == 0:
                    st.success("Tudo conferido: não há pendências neste mês.")

                kpis = [("Crédito apurado no mês (M100/M500)", res["credito_pis"], res["credito_cofins"]),
                        ("Crédito utilizado", res["credito_usado_pis"] + res["credito_anterior_pis"], res["credito_usado_cofins"] + res["credito_anterior_cofins"]),
                        ("Saldo de crédito a transportar", res["saldo_pis"], res["saldo_cofins"]),
                        ("Débito não cumulativo", res["debito_nc_pis"], res["debito_nc_cofins"]),
                        ("Débito cumulativo", res["debito_cum_pis"], res["debito_cum_cofins"]),
                        ("Retenção na fonte (não cumulativo)", res["retencao_nc_pis"], res["retencao_nc_cofins"]),
                        ("Total a recolher", res["a_recolher_pis"], res["a_recolher_cofins"])]
                xlsx = cexp.gerar_xlsx_conferencia(NOME_POR_COD[emp], emp, comp, L.drop(columns=["editado"], errors="ignore"),
                                                   C.drop(columns=["editado"], errors="ignore"), B.drop(columns=["editado"], errors="ignore"),
                                                   T.drop(columns=["editado"], errors="ignore"), D.drop(columns=["editado"], errors="ignore"), kpis,
                                                   {"SPED": sped["arquivo"], "Detalhamento": det["arquivo"]})
                st.download_button("⬇️ Baixar a conferência (.xlsx)", data=xlsx, key="cred_dl_conf",
                                   file_name=f"creditos_pis_cofins_{emp}_{comp.replace('/', '-')}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

                s_lin, s_cat, s_tot, s_deb, s_log = st.tabs(["Linhas (nota a nota)", "Categorias e bases", "Totais", "Débitos e apuração", "Edições e histórico"])

                with s_lin:
                    f1, f2 = st.columns([2, 2])
                    filtro = f1.multiselect("Mostrar status", cf.STATUS_LINHAS, default=list(cf.STATUS_LINHAS), key="cred_f_status")
                    busca = f2.text_input("Buscar fornecedor / nº da nota / CNPJ", key="cred_f_busca")
                    V = L[L["status"].isin(filtro)] if filtro else L
                    if busca.strip():
                        b = busca.strip().lower()
                        V = V[V["fornecedor"].astype(str).str.lower().str.contains(b, regex=False)
                              | V["numero"].astype(str).str.lower().str.contains(b, regex=False)
                              | V["cnpj"].astype(str).str.contains(b.replace(".", "").replace("/", "").replace("-", ""), regex=False)]
                    st.caption(f"{len(V)} de {len(L)} linhas. Para incluir uma linha que não veio de nenhum arquivo, use a última linha vazia da tabela.")
                    _editor("linhas", V, comp, ajustes,
                            ["nivel", "fornecedor", "numero", "categoria", "pis_sped", "cofins_sped", "pis_det", "cofins_det", "dif_pis", "dif_cofins",
                             "status", "observacao", "nota_contadora", "conferido"],
                            ["nivel", "dif_pis", "dif_cofins"],
                            {"nivel": st.column_config.TextColumn("Nível"), "fornecedor": st.column_config.TextColumn("Fornecedor"),
                             "numero": st.column_config.TextColumn("Nº da nota"), "categoria": st.column_config.TextColumn("Categoria"),
                             "pis_sped": _moeda_cfg("PIS (SPED)"), "cofins_sped": _moeda_cfg("COFINS (SPED)"),
                             "pis_det": _moeda_cfg("PIS (Detalhamento)"), "cofins_det": _moeda_cfg("COFINS (Detalhamento)"),
                             "dif_pis": _moeda_cfg("Dif. PIS"), "dif_cofins": _moeda_cfg("Dif. COFINS"),
                             "status": st.column_config.SelectboxColumn("Status", options=cf.STATUS_LINHAS),
                             "observacao": st.column_config.TextColumn("Observação"),
                             "nota_contadora": st.column_config.TextColumn("Nota da contadora"),
                             "conferido": st.column_config.CheckboxColumn("Conferido")},
                            dinamico=True)

                with s_cat:
                    st.markdown("**Crédito por categoria** (SPED × Detalhamento)")
                    _editor("categorias", C, comp, ajustes,
                            ["categoria", "pis_sped", "pis_det", "dif_pis", "cofins_sped", "cofins_det", "dif_cofins", "status", "nota_contadora"],
                            ["categoria", "dif_pis", "dif_cofins", "status"],
                            {"categoria": st.column_config.TextColumn("Categoria"), "pis_sped": _moeda_cfg("PIS (SPED)"), "pis_det": _moeda_cfg("PIS (Detalhamento)"),
                             "dif_pis": _moeda_cfg("Dif. PIS"), "cofins_sped": _moeda_cfg("COFINS (SPED)"), "cofins_det": _moeda_cfg("COFINS (Detalhamento)"),
                             "dif_cofins": _moeda_cfg("Dif. COFINS"), "status": st.column_config.TextColumn("Status"),
                             "nota_contadora": st.column_config.TextColumn("Nota da contadora")})
                    st.markdown("**Base de cálculo por natureza**")
                    if not B.empty and (B["dif_interna_pis"].abs() > cf.TOL_AGREGADO).any():
                        st.caption("Atenção: em algum grupo a base × alíquota do Bloco M não bate com a soma das linhas do próprio SPED "
                                   "(coluna \"Dif. interna PIS\"). Isso vem do arquivo, não do Detalhamento.")
                    _editor("bases", B, comp, ajustes,
                            ["grupo", "base_sped", "base_det", "dif", "status", "pis_pela_base", "pis_linhas", "dif_interna_pis", "nota_contadora"],
                            ["grupo", "dif", "status", "pis_pela_base", "pis_linhas", "dif_interna_pis"],
                            {"grupo": st.column_config.TextColumn("Grupo"), "base_sped": _moeda_cfg("Base (SPED)"), "base_det": _moeda_cfg("Base (Detalhamento)"),
                             "dif": _moeda_cfg("Diferença"), "status": st.column_config.TextColumn("Status"),
                             "pis_pela_base": _moeda_cfg("PIS pela base"), "pis_linhas": _moeda_cfg("PIS pelas linhas"),
                             "dif_interna_pis": _moeda_cfg("Dif. interna PIS"), "nota_contadora": st.column_config.TextColumn("Nota da contadora")})

                with s_tot:
                    st.markdown("**Crédito do Bloco M × soma do Detalhamento**")
                    st.caption("Crédito no Bloco M = soma do Detalhamento + depreciação/imobilizado do SPED (não têm nota) + eventual inconsistência dentro do próprio SPED. "
                               "O resíduo deve ficar em zero.")
                    for o in T["observacao"].dropna().unique():
                        if o:
                            st.info(o)
                    _editor("totais", T, comp, ajustes,
                            ["tributo", "credito_bloco_m", "soma_detalhamento", "depreciacao_imobilizado_sped", "inconsistencia_sped", "residuo", "status", "nota_contadora"],
                            ["tributo", "residuo", "status"],
                            {"tributo": st.column_config.TextColumn("Tributo"), "credito_bloco_m": _moeda_cfg("Crédito no Bloco M"),
                             "soma_detalhamento": _moeda_cfg("Soma do Detalhamento"), "depreciacao_imobilizado_sped": _moeda_cfg("Deprec./imobilizado (SPED)"),
                             "inconsistencia_sped": _moeda_cfg("Inconsistência no SPED"), "residuo": _moeda_cfg("Resíduo"),
                             "status": st.column_config.TextColumn("Status"), "nota_contadora": st.column_config.TextColumn("Nota da contadora")})

                with s_deb:
                    st.markdown("**Apuração do mês: SPED × aba \"Débitos SPED\" do Detalhamento**")
                    _editor("debitos", D, comp, ajustes,
                            ["item", "pis_sped", "pis_det", "dif_pis", "cofins_sped", "cofins_det", "dif_cofins", "status", "nota_contadora"],
                            ["item", "dif_pis", "dif_cofins", "status"],
                            {"item": st.column_config.TextColumn("Item"), "pis_sped": _moeda_cfg("PIS (SPED)"), "pis_det": _moeda_cfg("PIS (Detalhamento)"),
                             "dif_pis": _moeda_cfg("Dif. PIS"), "cofins_sped": _moeda_cfg("COFINS (SPED)"), "cofins_det": _moeda_cfg("COFINS (Detalhamento)"),
                             "dif_cofins": _moeda_cfg("Dif. COFINS"), "status": st.column_config.TextColumn("Status"),
                             "nota_contadora": st.column_config.TextColumn("Nota da contadora")})

                with s_log:
                    if not ajustes:
                        st.caption("Nenhuma edição salva neste mês.")
                    else:
                        grupos = sorted({(a["aba"], a["chave"]) for a in ajustes})
                        st.markdown("**Linhas com edição salva**")
                        st.dataframe(pd.DataFrame([{"Aba": a["aba"], "Linha": a["chave"], "Campo": a["campo"], "Valor calculado": a["valor_original"],
                                                    "Valor editado": a["valor_novo"], "Por": a["editado_por"], "Quando": formatacao.hora_br(a["editado_em"])}
                                                   for a in ajustes]), hide_index=True, width="stretch")
                        rot = {f"{aba} · {chave}": (aba, chave) for aba, chave in grupos}
                        esc = st.selectbox("Restaurar uma linha ao valor calculado (desfaz as edições e traz de volta linhas excluídas)", list(rot), key="cred_sel_rest")
                        if st.button("♻️ Restaurar esta linha", key="cred_btn_rest_linha"):
                            aba_r, chave_r = rot[esc]
                            cdb.restaurar_ajustes(conn, emp, comp, aba_r, chave_r, usuario)
                            for a in cf.ABAS:
                                st.session_state[f"cred_ed_n_{a}"] = st.session_state.get(f"cred_ed_n_{a}", 0) + 1
                            flash("ok", "Linha restaurada.")
                            st.rerun()
                    lg = cdb.log_ajustes(conn, emp, comp)
                    if not lg.empty:
                        st.markdown("**Histórico de edições**")
                        lg["em"] = lg["em"].map(formatacao.hora_br)
                        st.dataframe(lg.rename(columns={"em": "Quando", "usuario": "Quem", "aba": "Aba", "chave": "Linha", "campo": "Campo", "acao": "Ação",
                                                        "valor_antes": "Antes", "valor_depois": "Depois"}), hide_index=True, width="stretch")


# ═════════════════════════════ 3. DASHBOARD ═════════════════════════════
with tab_dash:
    serie = gr.preparar_serie(cdb.serie_mensal(conn, emp))
    if serie.empty:
        st.info("O dashboard aparece quando houver o histórico (jan–jul) carregado ou o primeiro SPED enviado.")
    else:
        trib = st.radio("Tributo", ["total", "pis", "cofins"], horizontal=True, key="cred_trib",
                        format_func={"total": "PIS + COFINS", "pis": "PIS", "cofins": "COFINS"}.get)
        ult = serie.iloc[-1]
        com_orig = serie[serie["dif_total"].notna()]
        k = st.columns(4)
        k[0].metric(f"Crédito apontado em {ult['rotulo']}", _brl(ult["cred_total"]))
        k[1].metric("Crédito acumulado no período", _brl(serie["cred_total"].sum()))
        k[2].metric("Diferença acumulada original × apontado", _brl(com_orig["dif_total"].sum()) if not com_orig.empty else "—",
                    help=("Meses " + ", ".join(com_orig["rotulo"]) + " — carga fixa feita com os SPED enviados (original × última retificadora)") if not com_orig.empty else None)
        k[3].metric(f"Saldo de crédito em {ult['rotulo']}", _brl(ult["saldo_total"]))
        k2 = st.columns(4)
        k2[0].metric(f"Débito não cumulativo em {ult['rotulo']}", _brl(ult["deb_nc_total"]))
        k2[1].metric(f"Débito cumulativo em {ult['rotulo']}", _brl(ult["deb_cum_total"]))
        k2[2].metric(f"Retenções em {ult['rotulo']}", _brl(ult["ret_total"]))
        k2[3].metric(f"A recolher em {ult['rotulo']}", _brl(ult["recolher_total"]))

        st.subheader("Crédito")
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Crédito por mês** (apontado × SPED original)")
            _grafico(gr.fig_credito_mensal(serie, DARK, trib))
        with g2:
            st.markdown("**Diferença original × apontado** (mês e acumulada)")
            _grafico(gr.fig_diferenca(serie, DARK))
        g3, g4 = st.columns(2)
        with g3:
            st.markdown("**Crédito por categoria**")
            _grafico(gr.fig_credito_categoria(serie, DARK, trib))
        with g4:
            st.markdown("**Crédito acumulado**")
            _grafico(gr.fig_credito_acumulado(serie, DARK))

        st.subheader("Débito e apuração")
        h1, h2 = st.columns(2)
        with h1:
            st.markdown("**Débito × crédito utilizado**")
            _grafico(gr.fig_debito_credito(serie, DARK))
        with h2:
            st.markdown("**A recolher e retenções**")
            _grafico(gr.fig_a_recolher(serie, DARK))
        h3, h4 = st.columns(2)
        with h3:
            st.markdown("**Saldo de crédito a transportar**")
            _grafico(gr.fig_saldo_credito(serie, DARK))
        with h4:
            st.markdown("**Receita por regime** (não cumulativa × cumulativa)")
            _grafico(gr.fig_receita(serie, DARK))

        st.subheader("Um mês em detalhe")
        mes = st.selectbox("Mês", list(serie["competencia"])[::-1], key="cred_dash_mes")
        linha_mes = serie[serie["competencia"] == mes].iloc[0]
        m1, m2, m3 = st.columns(3)
        with m1:
            st.markdown("**Crédito por categoria**")
            _grafico(gr.fig_creditos_por_categoria_mes(linha_mes["por_categoria"], DARK, trib))
        with m2:
            st.markdown("**Crédito por natureza da base**")
            _grafico(gr.fig_base_natureza(linha_mes["por_natureza"], DARK))
        with m3:
            if linha_mes["origem"] == "ENVIO":
                sp = cdb.carregar_sped(conn, emp, mes)
                st.markdown("**Maiores fornecedores do mês**")
                _grafico(gr.fig_top_fornecedores(sp["creditos"] if sp else None, DARK, tributo=trib))
            else:
                st.markdown("**Quem mais mudou o crédito** (original × apontado)")
                _grafico(gr.fig_dif_fornecedores(linha_mes["dif_fornecedores"], DARK))

        with st.expander("📋 Ver os números em tabela"):
            tab = serie[["rotulo", "origem", "sped_tipo", "cred_total", "orig_total", "dif_total", "deb_nc_total", "deb_cum_total", "usado_total",
                         "saldo_total", "ret_total", "recolher_total", "receita_nc", "receita_cum"]].rename(columns={
                "rotulo": "Mês", "origem": "Origem", "sped_tipo": "SPED", "cred_total": "Crédito apontado", "orig_total": "Crédito no original",
                "dif_total": "Diferença", "deb_nc_total": "Débito NC", "deb_cum_total": "Débito cumulativo", "usado_total": "Crédito utilizado",
                "saldo_total": "Saldo de crédito", "ret_total": "Retenções", "recolher_total": "A recolher", "receita_nc": "Receita NC",
                "receita_cum": "Receita cumulativa"})
            st.dataframe(tab, hide_index=True, width="stretch")
        st.caption("Jan–jul: carga fixa feita a partir dos SPED já enviados (original × última retificadora). De agosto em diante o app só guarda e controla o que está no SPED, que já vem apontado.")
