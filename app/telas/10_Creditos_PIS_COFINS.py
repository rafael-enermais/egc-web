# -*- coding: utf-8 -*-
"""
Tela CRÉDITOS PIS/COFINS (v0.53.0, 16/10/2026) -- controle do crédito e do débito de PIS/COFINS a partir do SPED EFD-Contribuições.

Fluxo combinado com o Rafael (16/10/2026):
  1. Sobe o SPED "original" do mês (já com os apontamentos) e o Detalhamento de Débito e Crédito feito pela contadora (.xlsx).
  2. O app cruza os dois (nota a nota, por categoria, por base e na apuração de débitos) e mostra o que bate e o que não bate.
  3. TUDO é editável ("pra não depender se o app erra algo"): cada edição fica salva, com quem/quando e o valor antes, e dá para
     restaurar a linha ao valor calculado. As edições ficam presas à versão do SPED em que foram feitas.
  3b. (v0.51.0) CADA upload é uma VERSÃO do mês e nunca some: o SPED original e as retificadoras ficam todos guardados e visíveis; uma é a
     "apontada" (a que vale no dashboard). A contadora escolhe qual conferir, compara as versões e define qual é a apontada.
  3c. (v0.52.0) Dashboard FLEXÍVEL: filtros (tributo, período), simulação da versão de cada mês (sem gravar), comparação de versões, ponte,
     sugestões de conferência (só sugestões: nada trava) e controle do mês (versão oficial, conferido, nota — bloco 30; o relatório usa a oficial).
  3d. (v0.53.0) Aba Relatório: PDF de 4 páginas (créditos e débitos) a partir da versão OFICIAL do mês; se a tela estiver numa simulação, sai com o aviso
     "não é a versão oficial" (nada trava).
  4. Dashboard: crédito por mês/categoria, diferença original × apontado (jan–jul, carga fixa), débito, crédito utilizado, saldo,
     retenções, a recolher e receita por regime.
Funções puras ficam em sped_contribuicoes / creditos_detalhamento / creditos_conferencia / creditos_graficos / creditos_export;
o banco em creditos_db (blocos 28, 29 e 30 do schema.sql).
"""
import sys
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS, empresa_por_cnpj, flash, mostrar_flash  # noqa: E402
import creditos_analise as analise  # noqa: E402
import dados_relatorio_creditos as dr  # noqa: E402
import gerador_relatorio_creditos as gen  # noqa: E402
import creditos_conferencia as cf  # noqa: E402
import creditos_db as cdb  # noqa: E402
import creditos_detalhamento as cdet  # noqa: E402
import creditos_export as cexp  # noqa: E402
import creditos_graficos as gr  # noqa: E402
import formatacao  # noqa: E402
import nf_export  # noqa: E402
import sped_contribuicoes as sc  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _c in EMPRESAS_FIXAS}
CNPJ_POR_COD = {cod: cnpj for cod, _n, cnpj in EMPRESAS_FIXAS}
PENDENTES = (cf.S_NUM, cf.S_DIF, cf.S_SO_SPED, cf.S_SO_DET)

usuario = usuario_atual()
sidebar_contexto(usuario)

st.title("Créditos PIS/COFINS")
st.caption("Controle do crédito e do débito de PIS/COFINS a partir do SPED (EFD-Contribuições), conferido com o Detalhamento da contadora.")
# Recipiente SEMPRE presente (mesmo sem mensagem): o aviso aparece/some dentro dele sem empurrar os elementos de baixo.
# Sem isso, quando o aviso sumia no rerun seguinte, o Streamlit recriava as abas e a tela voltava para "Enviar arquivos"
# (e o expander de envios fechava) a cada clique na Conferência/Dashboard (v0.50.1).
with st.container():
    mostrar_flash()

conn = get_conn()
if not cdb.tem_bloco28(conn):
    st.warning("Esta tela ainda precisa de uma atualização no banco (bloco 28 do schema.sql — tabelas de créditos PIS/COFINS). "
               "Rode o arquivo `scripts/bloco28_creditos.txt` no SQL Editor do Supabase e recarregue a página.")
    st.stop()
if not cdb.tem_bloco29(conn):
    st.warning("Esta tela ainda precisa de mais uma atualização no banco (bloco 29 do schema.sql — versões de SPED por mês). "
               "Rode o arquivo `scripts/bloco29_versoes_creditos.txt` no SQL Editor do Supabase e recarregue a página.")
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


def _md(texto: str) -> str:
    """Escapa o cifrão: dois "R$" no mesmo texto viram fórmula (LaTeX) no markdown do Streamlit e saem em fonte de código."""
    return str(texto).replace("$", "\\$")


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

tab_envio, tab_conf, tab_dash, tab_rel = st.tabs(["📤 Enviar arquivos", "🔎 Conferência", "📊 Dashboard", "📄 Relatório"])


# ═════════════════════════════ 1. ENVIAR ARQUIVOS ═════════════════════════════
def _vigente(tipo: str, comp: str):
    return next((e for e in cdb.listar_envios(conn, emp) if e["tipo"] == tipo and e["competencia"] == comp), None)


with tab_envio:
    # um contador POR campo de envio: gravar um arquivo nao pode limpar o outro que ainda esta' esperando confirmacao
    n_up_s = st.session_state.get("cred_up_n_sped", 0)
    n_up_d = st.session_state.get("cred_up_n_det", 0)
    col_s, col_d = st.columns(2)

    with col_s:
        st.subheader("1. SPED do mês")
        st.caption("Arquivo .txt da EFD-Contribuições (o original já com os apontamentos).")
        up_s = st.file_uploader("SPED EFD-Contribuições (.txt)", type=["txt"], key=f"cred_up_sped_{n_up_s}")
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
                    versoes = cdb.listar_versoes(conn, "SPED", emp, cab["competencia"])
                    ja = cdb.envio_com_sha(conn, "SPED", emp, cab["competencia"], r["sha256"])
                    igual = ja is not None
                    atual = next((v for v in versoes if v["apontada"]), None)
                    apontar = True
                    if igual:
                        st.info(f"Este mesmo arquivo já está gravado em {cab['competencia']} (versão {ja['n']}ª" + (", a apontada" if ja["apontada"] else "") + ").")
                    elif versoes:
                        st.info(f"{cab['competencia']} já tem {len(versoes)} versão(ões) de SPED (a apontada é a {atual['n']}ª, **{atual['arquivo_nome']}**). "
                                "Nada é apagado: esta entra como mais uma versão e as outras continuam guardadas e visíveis.")
                        apontar = st.checkbox("Esta nova versão passa a ser a **apontada** do mês (a que vale no dashboard)", value=True,
                                              key=f"cred_apontar_sped_{n_up_s}")
                    if st.button("💾 Gravar este SPED", key="cred_btn_sped", type="primary", disabled=igual):
                        try:
                            cdb.gravar_sped(conn, emp, r, up_s.name, usuario, apontar=apontar)
                            flash("ok", f"SPED de {cab['competencia']} gravado.")
                            st.session_state["cred_up_n_sped"] = n_up_s + 1
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Não consegui gravar o SPED: {exc}")

    with col_d:
        st.subheader("2. Detalhamento da contadora")
        st.caption("Planilha .xlsx \"Detalhamento de Débito e Crédito PIS e COFINS\" do mês.")
        up_d = st.file_uploader("Detalhamento (.xlsx)", type=["xlsx"], key=f"cred_up_det_{n_up_d}")
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
                    versoes_d = cdb.listar_versoes(conn, "DETALHAMENTO", emp, comp_det)
                    ja_d = cdb.envio_com_sha(conn, "DETALHAMENTO", emp, comp_det, d["sha256"])
                    igual_d = ja_d is not None
                    atual_d = next((v for v in versoes_d if v["apontada"]), None)
                    apontar_d = True
                    if igual_d:
                        st.info(f"Este mesmo arquivo já está gravado em {comp_det} (versão {ja_d['n']}ª" + (", a apontada" if ja_d["apontada"] else "") + ").")
                    elif versoes_d:
                        st.info(f"{comp_det} já tem {len(versoes_d)} versão(ões) de Detalhamento (a apontada é a {atual_d['n']}ª, **{atual_d['arquivo_nome']}**). "
                                "Nada é apagado: esta entra como mais uma versão.")
                        apontar_d = st.checkbox("Esta nova versão passa a ser a **apontada** do mês", value=True, key=f"cred_apontar_det_{n_up_d}")
                    if st.button("💾 Gravar este Detalhamento", key="cred_btn_det", type="primary", disabled=igual_d):
                        try:
                            cdb.gravar_detalhamento(conn, emp, d, up_d.name, usuario, competencia=comp_det, apontar=apontar_d)
                            flash("ok", f"Detalhamento de {comp_det} gravado.")
                            st.session_state["cred_up_n_det"] = n_up_d + 1
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Não consegui gravar o Detalhamento: {exc}")

    with st.expander("🗃️ Versões enviadas (escolher a apontada)", expanded=False):
        st.caption("Todo arquivo enviado fica guardado aqui, para sempre. Em cada mês, uma versão é a **apontada** (a que vale no dashboard); "
                   "as outras continuam disponíveis para conferir e comparar na aba Conferência.")
        envios = cdb.listar_envios(conn, emp, incluir_arquivados=True)
        if not envios:
            st.caption("Nenhum arquivo enviado ainda.")
        else:
            tabela = pd.DataFrame([{"Tipo": e["tipo"], "Competência": e["competencia"], "SPED": e.get("detalhe") or "", "Arquivo": e["arquivo_nome"],
                                    "Enviado por": e["enviado_por"], "Enviado em": formatacao.hora_br(e["enviado_em"]),
                                    "Situação": "Outra versão" if e["arquivado_em"] else "Apontada"} for e in envios])
            st.dataframe(tabela, hide_index=True, width="stretch")
            opcoes = {f"{e['tipo']} · {e['competencia']} · {e['arquivo_nome']} ({'outra versão' if e['arquivado_em'] else 'apontada'})": e for e in envios}
            escolha = st.selectbox("Escolha uma versão", list(opcoes), key="cred_sel_envio")
            e = opcoes[escolha]
            if not e["arquivado_em"]:
                st.caption("Esta já é a versão apontada do mês.")
            else:
                # a confirmação vale para UMA ação: contador na chave zera o "Confirmo" depois de apontar
                n_ok = st.session_state.get("cred_ok_n", 0)
                ok = st.checkbox("Confirmo: passar esta a ser a versão apontada (a atual continua guardada)", key=f"cred_ok_envio_{n_ok}")
                if st.button("⭐ Tornar esta a versão apontada", key="cred_btn_apontar", disabled=not ok):
                    try:
                        cdb.definir_apontada(conn, e["tipo"], e["id"], usuario)
                        flash("ok", "Versão apontada trocada. A anterior continua guardada.")
                        st.session_state["cred_ok_n"] = n_ok + 1
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))


# ═════════════════════════════ 2. CONFERÊNCIA ═════════════════════════════
_REF = [""]   # versão (id do SPED) que está sendo conferida: as edições ficam presas a ela


def _editor(aba: str, df: pd.DataFrame, comp: str, ajustes: list, ordem: list, desabilitadas: list, config: dict, dinamico: bool = False):
    """data_editor + botão Salvar. Devolve True se salvou algo (a tela já foi recarregada nesse caso)."""
    chave, campos, _ = cf.ABAS[aba]
    n = st.session_state.get(f"cred_ed_n_{aba}", 0)
    view = df[[c for c in dict.fromkeys([chave] + ordem) if c in df.columns]].reset_index(drop=True)
    cfg = {chave: None, **config}
    ed = st.data_editor(view, key=f"cred_ed_{aba}_{n}_{_REF[0][:8]}", hide_index=True, width="stretch", disabled=desabilitadas,
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
            cdb.salvar_ajustes(conn, emp, comp, aba, mud, usuario, ref=_REF[0])
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
        v_sped = cdb.listar_versoes(conn, "SPED", emp, comp)
        v_det = cdb.listar_versoes(conn, "DETALHAMENTO", emp, comp)

        def _escolher(versoes: list, rotulo: str, chave: str):
            """Seletor de versão (padrão = a apontada). Só aparece quando o mês tem mais de uma."""
            if not versoes:
                return None
            if len(versoes) == 1:
                return versoes[0]["id"]
            ops = {v["rotulo"]: v["id"] for v in versoes}
            pad = next((i for i, v in enumerate(versoes) if v["apontada"]), len(versoes) - 1)
            return ops[st.selectbox(rotulo, list(ops), index=pad, key=chave)]

        cv1, cv2 = st.columns(2)
        with cv1:
            sped_id = _escolher(v_sped, "Versão do SPED que está sendo conferida", f"cred_ver_sped_{comp}")
        with cv2:
            det_id = _escolher(v_det, "Versão do Detalhamento que está sendo usada", f"cred_ver_det_{comp}")
        sped = cdb.carregar_sped(conn, emp, comp, envio_id=sped_id)
        det = cdb.carregar_detalhamento(conn, emp, comp, envio_id=det_id)
        _REF[0] = sped["envio_id"] if sped else ""
        ajustes = cdb.carregar_ajustes(conn, emp, comp, ref=_REF[0])

        for tipo_v, obj, versoes in (("SPED", sped, v_sped), ("Detalhamento", det, v_det)):
            if obj is not None and not obj["apontada"]:
                ap = next((v for v in versoes if v["apontada"]), None)
                st.warning(f"Você está vendo uma versão do {tipo_v} que **não é a apontada** do mês"
                           + (f" (a apontada é a {ap['n']}ª)" if ap else "") + ". O dashboard usa a apontada; as edições desta tela ficam presas à versão do SPED que você escolheu.")
                if st.button(f"⭐ Tornar esta a versão apontada do {tipo_v}", key=f"cred_apontar_conf_{tipo_v}_{comp}"):
                    cdb.definir_apontada(conn, "SPED" if tipo_v == "SPED" else "DETALHAMENTO", obj["envio_id"], usuario)
                    flash("ok", f"Versão apontada do {tipo_v} trocada. A anterior continua guardada.")
                    st.rerun()

        if len(v_sped) > 1:
            with st.expander(f"🔀 Comparar as {len(v_sped)} versões do SPED de {comp}"):
                cmp_ = cdb.comparar_versoes_sped(conn, emp, comp)
                apont = cmp_[cmp_["apontada"]].iloc[0] if cmp_["apontada"].any() else cmp_.iloc[-1]
                tab_cmp = pd.DataFrame({
                    "Versão": cmp_["versao"], "Arquivo": cmp_["arquivo"], "Enviado em": cmp_["enviado_em"].map(formatacao.hora_br),
                    "Crédito PIS": cmp_["credito_pis"].map(_brl), "Dif. PIS × apontada": (cmp_["credito_pis"] - apont["credito_pis"]).map(_brl),
                    "Crédito COFINS": cmp_["credito_cofins"].map(_brl), "Dif. COFINS × apontada": (cmp_["credito_cofins"] - apont["credito_cofins"]).map(_brl),
                    "Débito NC PIS": cmp_["debito_nc_pis"].map(_brl), "Débito NC COFINS": cmp_["debito_nc_cofins"].map(_brl),
                    "Saldo PIS": cmp_["saldo_pis"].map(_brl), "Saldo COFINS": cmp_["saldo_cofins"].map(_brl), "Linhas de crédito": cmp_["linhas_credito"]})
                st.dataframe(tab_cmp, hide_index=True, width="stretch")
                cat = cdb.comparar_categorias_sped(conn, emp, comp)
                if not cat.empty:
                    st.markdown("**Crédito (PIS + COFINS) por categoria em cada versão**")
                    for c_ in cat.columns[1:]:
                        cat[c_] = cat[c_].map(_brl)
                    st.dataframe(cat.rename(columns={"categoria": "Categoria"}), hide_index=True, width="stretch")

        if sped is None:
            st.warning(f"{comp} tem o Detalhamento, mas ainda não tem o SPED. Envie o SPED para ver a conferência.")
        else:
            res = sc.resumo_apuracao(sped["apuracao"])
            cab = sped["cabecalho"]
            n_v = next((v["n"] for v in v_sped if v["id"] == sped["envio_id"]), "?")
            st.caption(f"SPED {n_v}ª versão ({str(cab.get('tipo') or '').lower()}{', apontada' if sped['apontada'] else ', outra versão'}): {sped['arquivo']} · enviado por {sped['enviado_por']} em {formatacao.hora_br(sped['enviado_em'])}"
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
                                                   {"SPED": f"{sped['arquivo']} (versão {n_v}ª, {'apontada' if sped['apontada'] else 'outra versão'})",
                                                    "Detalhamento": det["arquivo"] + ("" if det["apontada"] else " (outra versão)")})
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
                        st.caption("Nenhuma edição salva nesta versão do mês.")
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
                            cdb.restaurar_ajustes(conn, emp, comp, aba_r, chave_r, usuario, ref=_REF[0])
                            for a in cf.ABAS:
                                st.session_state[f"cred_ed_n_{a}"] = st.session_state.get(f"cred_ed_n_{a}", 0) + 1
                            flash("ok", "Linha restaurada.")
                            st.rerun()
                    lg = cdb.log_ajustes(conn, emp, comp, ref=_REF[0])
                    if not lg.empty:
                        st.markdown("**Histórico de edições**")
                        lg["em"] = lg["em"].map(formatacao.hora_br)
                        st.dataframe(lg.rename(columns={"em": "Quando", "usuario": "Quem", "aba": "Aba", "chave": "Linha", "campo": "Campo", "acao": "Ação",
                                                        "valor_antes": "Antes", "valor_depois": "Depois"}), hide_index=True, width="stretch")


# ═════════════════════════════ DADOS DO DASHBOARD E DO RELATÓRIO ═════════════════════════════
fontes_all = cdb.fontes_por_mes(conn, emp)
tem30 = cdb.tem_bloco30(conn)
ctrl_all = cdb.carregar_mes(conn, emp)
base_bruta = cdb.serie_mensal(conn, emp, None, True, fontes_all, ctrl_all)
_REGRA_PADRAO = "Regra padrão (a versão apontada)"


def _limpar_simulacao() -> None:
    for k in [k for k in st.session_state if str(k).startswith(f"cred_sim_{emp}_")]:
        del st.session_state[k]


def _escolhas_sessao() -> dict:
    """Simulação em curso ({competência: chave da fonte}), lida do estado dos seletores do Dashboard. Nada disso é gravado."""
    out: dict = {}
    for c, ls in fontes_all.items():
        rot = st.session_state.get(f"cred_sim_{emp}_{c}")
        chave = next((f["chave"] for f in ls if f["rotulo"] == rot), None)
        if chave:
            out[c] = chave
    return out


@st.cache_data(show_spinner="Montando o PDF...", max_entries=8)
def _pdf_creditos(dados: dict) -> bytes:
    return gen.gerar_pdf_creditos(dados)


# ═════════════════════════════ 4. RELATÓRIO (PDF) ═════════════════════════════
with tab_rel:
    st.caption("PDF de 5 páginas (capa + 4) com os créditos e os débitos de PIS/COFINS do mês (resumo, crédito, débito e apuração, histórico e observações). "
               "Ele sai da versão **oficial** do mês — a que você marcou como oficial ou, sem marcação, a apontada.")
    if base_bruta is None or base_bruta.empty:
        st.info("O relatório fica disponível quando houver o histórico (jan–jul) carregado ou o primeiro SPED enviado.")
    else:
        esc_r = _escolhas_sessao()
        cen_r = cdb.serie_mensal(conn, emp, esc_r, True, fontes_all, ctrl_all) if esc_r else base_bruta
        meses_r = list(cen_r["competencia"])[::-1]
        r1, r2 = st.columns([1, 2])
        mes_r = r1.selectbox("Mês do relatório", meses_r, key="cred_rel_mes")
        nome_r = r2.text_input("Nome da contadora (aparece na linha de assinatura; opcional)", key="cred_rel_nome")
        sug_r = st.checkbox("Incluir no PDF os \"pontos para acompanhar\" (as sugestões de conferência do app)", value=False, key="cred_rel_sug",
                            help="Por padrão ficam só na tela, como ajuda para você. Marque se quiser que apareçam no relatório.")
        try:
            dados_r = dr.montar_dados(gr.preparar_serie(cen_r, "total"), mes_r, empresa_codigo=emp, empresa_nome=NOME_POR_COD[emp], cnpj=CNPJ_POR_COD.get(emp, ""),
                                      controle_mes=ctrl_all.get(mes_r), usuario=usuario, nome_contador=nome_r.strip(), incluir_sugestoes=sug_r)
        except ValueError as exc:
            dados_r = None
            st.warning(str(exc))
        if dados_r is not None:
            if not dados_r["oficial"]:
                st.warning("🔀 A **simulação** do Dashboard está ligada para " + ", ".join(dados_r["meses_nao_oficiais"]) + ". Você pode gerar o PDF assim — ele sai com o aviso "
                           "\"SIMULAÇÃO — NÃO É A VERSÃO OFICIAL\" em todas as páginas. Para gerar da oficial, volte a simulação.")
                st.button("↩️ Voltar tudo para a oficial", key="cred_btn_sim_limpar_rel", on_click=_limpar_simulacao)
            else:
                st.success(f"Versão oficial de {mes_r}: {dados_r['fonte_rotulo'] or 'fonte não informada'}"
                           + (" · conferido" if dados_r["conferido"] else " · ainda não marcado como conferido (é só uma marcação; você pode gerar assim mesmo)"))
            st.markdown("**Leitura do mês (como vai no PDF)**")
            for par in dados_r["leitura"]:
                st.write(_md(par))
            if dados_r["nota"]:
                st.caption(_md(f"Nota da contadora que vai no PDF: {dados_r['nota']}"))
            elif tem30:
                st.caption("Quer registrar uma observação no relatório? Escreva a nota do mês em Dashboard → Um mês em detalhe → Controle.")
            st.download_button("⬇️ Baixar o relatório em PDF", data=_pdf_creditos(dados_r), file_name=dr.nome_arquivo(dados_r), mime="application/pdf",
                               key="cred_dl_relatorio", type="primary")


# ═════════════════════════════ 3. DASHBOARD ═════════════════════════════
# (v0.52.0) Dashboard FLEXIVEL: filtros (tributo, período), escolha/simulação da versão de cada mês, comparação, ponte, sugestões de conferência e controle
# do mês (oficial / conferido / nota). Tudo é SUGESTÃO e organização: nada trava e nada é gravado numa simulação.
def _rotulo_modo(modo: str) -> str:
    return {"padrao": "oficial (padrão = apontada)", "oficial": "oficial (marcada pela contadora)", "simulada": "SIMULAÇÃO — não é a oficial"}.get(modo, modo)


with tab_dash:
    if base_bruta is None or base_bruta.empty:
        st.info("O dashboard aparece quando houver o histórico (jan–jul) carregado ou o primeiro SPED enviado.")
    else:
        todas = list(base_bruta["competencia"])

        # ---- filtros (tudo opcional) ----
        f1, f2, f3 = st.columns([2, 1, 1])
        trib = f1.radio("Tributo", ["total", "pis", "cofins"], horizontal=True, key="cred_trib", format_func={"total": "PIS + COFINS", "pis": "PIS", "cofins": "COFINS"}.get)
        de = f2.selectbox("De", todas, index=0, key=f"cred_de_{emp}")
        ate = f3.selectbox("Até", todas, index=len(todas) - 1, key=f"cred_ate_{emp}")
        i_de, i_ate = todas.index(de), todas.index(ate)
        if i_de > i_ate:
            st.warning("O mês inicial está depois do final; mostrando o intervalo invertido.")
            i_de, i_ate = i_ate, i_de
        periodo = todas[i_de:i_ate + 1]

        # ---- simulação da versão de cada mês (nada é gravado) ----
        multiplos = [c for c in periodo if len(fontes_all.get(c, [])) > 1]
        escolhas: dict = {}
        with st.expander(f"🔀 Escolher a versão de cada mês e ver o efeito ({len(multiplos)} mês(es) com mais de uma versão)", expanded=False):
            st.caption("Experimente outra versão do SPED em qualquer mês: os números e os gráficos abaixo mudam na hora. É só uma **simulação** — nada é gravado "
                       "e a versão oficial continua a mesma. Se gostar do resultado, marque a versão como oficial em \"Um mês em detalhe\".")
            if not multiplos:
                st.caption("Nenhum mês do período tem mais de uma versão guardada.")
            for c in multiplos:
                ls = fontes_all[c]
                ops = {_REGRA_PADRAO if f is None else f["rotulo"]: (None if f is None else f["chave"]) for f in [None] + ls}
                sel = st.selectbox(c, list(ops), key=f"cred_sim_{emp}_{c}")
                if ops[sel]:
                    escolhas[c] = ops[sel]
            if escolhas:
                st.button("↩️ Voltar tudo para a oficial", key="cred_btn_sim_limpar", on_click=_limpar_simulacao)
        cen_bruta = cdb.serie_mensal(conn, emp, escolhas, True, fontes_all, ctrl_all) if escolhas else base_bruta
        if escolhas and (cen_bruta["modo"] != "simulada").all():
            escolhas = {}
        simulando = bool(escolhas) and bool((cen_bruta["modo"] == "simulada").any())
        cen_f = cen_bruta[cen_bruta["competencia"].isin(periodo)].reset_index(drop=True)
        base_f = base_bruta[base_bruta["competencia"].isin(periodo)].reset_index(drop=True)
        if cen_f.empty:
            st.info("Não há dados no período escolhido.")
            st.stop()
        # o tributo escolhido vale para os KPIs, os graficos e a tabela (a receita e' base de calculo: nao depende do tributo)
        serie = gr.preparar_serie(cen_f, trib)
        serie_base = gr.preparar_serie(base_f, trib)
        if simulando:
            n_sim = int((serie["modo"] == "simulada").sum())
            st.warning(f"🔀 **Simulação ligada** em {n_sim} mês(es): os números abaixo usam versões diferentes da oficial. Nada foi gravado. "
                       "Os relatórios gerados avisam que não são da versão oficial.")
        st.caption("Mostrando: **" + {"total": "PIS + COFINS", "pis": "somente PIS", "cofins": "somente COFINS"}[trib] + f"** · período {periodo[0]} a {periodo[-1]}.")
        ult = serie.iloc[-1]
        ult_b = serie_base.iloc[-1]
        com_orig = serie[serie["dif_total"].notna()]

        def _delta(a, b):
            return None if not simulando or abs(a - b) < 0.005 else formatacao.moeda_br(a - b, forcar_sinal=True) + " vs oficial"

        k = st.columns(4)
        k[0].metric(f"Crédito apontado em {ult['rotulo']}", _brl(ult["cred_total"]), delta=_delta(ult["cred_total"], ult_b["cred_total"]))
        k[1].metric("Crédito acumulado no período", _brl(serie["cred_total"].sum()), delta=_delta(serie["cred_total"].sum(), serie_base["cred_total"].sum()))
        k[2].metric("Diferença acumulada original × apontado", _brl(com_orig["dif_total"].sum()) if not com_orig.empty else "—",
                    help=("Meses " + ", ".join(com_orig["rotulo"]) + " — jan–jul: carga fixa (original × última retificadora); depois: SPED Original enviado no app × versão em uso") if not com_orig.empty else None)
        k[3].metric(f"Saldo de crédito em {ult['rotulo']}", _brl(ult["saldo_total"]), delta=_delta(ult["saldo_total"], ult_b["saldo_total"]))
        k2 = st.columns(4)
        k2[0].metric(f"Débito não cumulativo em {ult['rotulo']}", _brl(ult["deb_nc_total"]), delta=_delta(ult["deb_nc_total"], ult_b["deb_nc_total"]))
        k2[1].metric(f"Débito cumulativo em {ult['rotulo']}", _brl(ult["deb_cum_total"]), delta=_delta(ult["deb_cum_total"], ult_b["deb_cum_total"]))
        k2[2].metric(f"Retenções em {ult['rotulo']}", _brl(ult["ret_total"]), delta=_delta(ult["ret_total"], ult_b["ret_total"]))
        k2[3].metric(f"A recolher em {ult['rotulo']}", _brl(ult["recolher_total"]), delta=_delta(ult["recolher_total"], ult_b["recolher_total"]))

        if simulando:
            st.subheader("O que a simulação muda")
            pt = analise.ponte(serie_base, serie)
            st.caption(_md(f"Crédito do período na oficial: **{_brl(pt['inicio'])}** → na simulação: **{_brl(pt['fim'])}** ({formatacao.moeda_br(pt['fim'] - pt['inicio'], forcar_sinal=True)}). "
                            "Cada barra é um mês cuja versão foi trocada."))
            _grafico(gr.fig_ponte(pt, DARK))

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

        # ---- um mês em detalhe ----
        st.subheader("Um mês em detalhe")
        meses_det = list(serie["competencia"])[::-1]
        mes = st.selectbox("Mês", meses_det, key="cred_dash_mes")
        i_mes = int(serie.index[serie["competencia"] == mes][0])
        linha_mes = serie.iloc[i_mes]
        fontes_mes = fontes_all.get(mes, [])
        ctrl_mes = ctrl_all.get(mes) or {}
        st.caption(f"**Fonte usada em {linha_mes['rotulo']}:** {linha_mes['fonte_rotulo'] or '—'}  ·  **{_rotulo_modo(str(linha_mes['modo']))}**"
                   + ("  ·  ✅ conferido" + (f" por {ctrl_mes.get('conferido_por')}" if ctrl_mes.get("conferido_por") else "") if linha_mes["conferido"] else "  ·  ainda não marcado como conferido"))

        dicas = analise.sugestoes_mes(serie, i_mes, trib)
        with st.expander(f"💡 Sugestões de conferência para {linha_mes['rotulo']} ({len(dicas)})", expanded=any(x["nivel"] == "atencao" for x in dicas)):
            st.caption("Sugestões automáticas, só para ajudar. Você decide o que fazer — nenhuma delas impede nada.")
            if not dicas:
                st.caption("Nada fora do padrão neste mês.")
            for x in dicas:
                (st.warning if x["nivel"] == "atencao" else st.info)(_md(f"**{x['titulo']}.** {x['texto']}"))

        m1, m2, m3 = st.columns(3)
        with m1:
            st.markdown("**Crédito por categoria** (com % do mês)")
            _grafico(gr.fig_composicao(analise.composicao(linha_mes["por_categoria"], trib), DARK))
        with m2:
            st.markdown("**Crédito por natureza da base**")
            _grafico(gr.fig_base_natureza(linha_mes["por_natureza"], DARK))
        with m3:
            fonte_id = str(linha_mes["fonte"] or "")[4:] if str(linha_mes["fonte"] or "").startswith("ENV:") else None
            if fonte_id:
                sp = cdb.carregar_sped(conn, emp, mes, envio_id=fonte_id)
                st.markdown("**Maiores fornecedores do mês**")
                _grafico(gr.fig_top_fornecedores(sp["creditos"] if sp else None, DARK, tributo=trib))
            else:
                st.markdown("**Quem mais mudou o crédito** (original × apontado)")
                _grafico(gr.fig_dif_fornecedores(linha_mes["dif_fornecedores"], DARK))

        # comparar duas versões do mês
        if len(fontes_mes) > 1:
            with st.expander(f"⚖️ Comparar duas versões de {linha_mes['rotulo']}", expanded=False):
                ops_f = {f["rotulo"]: f for f in fontes_mes}
                nomes_f = list(ops_f)
                idx_a = next((i for i, f in enumerate(fontes_mes) if f["chave"] == linha_mes["fonte"]), 0)
                ca, cb = st.columns(2)
                va = ops_f[ca.selectbox("Versão A", nomes_f, index=idx_a, key=f"cred_cmpA_{emp}_{mes}")]
                vb = ops_f[cb.selectbox("Versão B", nomes_f, index=(idx_a + 1) % len(nomes_f), key=f"cred_cmpB_{emp}_{mes}")]
                cmp_f = analise.comparar_fontes(va, vb, trib)
                ap_ = cmp_f[cmp_f["grupo"] == "Apuração"]
                st.dataframe(pd.DataFrame({"Item": ap_["item"], "A": ap_["a"].map(_brl), "B": ap_["b"].map(_brl),
                                           "Diferença (B − A)": ap_["dif"].map(lambda v: formatacao.moeda_br(v, forcar_sinal=True)),
                                           "%": ap_["pct"].map(lambda v: formatacao.pct_br(v, forcar_sinal=True))}), hide_index=True, width="stretch")
                ct_ = cmp_f[cmp_f["grupo"] == "Categoria"]
                if not ct_.empty:
                    st.markdown("**Crédito por categoria nas duas versões**")
                    _grafico(gr.fig_comparacao_categorias(ct_, DARK, "A", "B"))

        # SPED x Detalhamento por categoria
        v_det_m = cdb.listar_versoes(conn, "DETALHAMENTO", emp, mes)
        if v_det_m and linha_mes["por_categoria"]:
            with st.expander(f"📑 SPED × Detalhamento da contadora em {linha_mes['rotulo']}", expanded=False):
                pad_d = next((i for i, v in enumerate(v_det_m) if v["apontada"]), len(v_det_m) - 1)
                ops_d = {v["rotulo"]: v["id"] for v in v_det_m}
                nome_d = st.selectbox("Versão do Detalhamento", list(ops_d), index=pad_d, key=f"cred_dashdet_{emp}_{mes}") if len(ops_d) > 1 else list(ops_d)[0]
                cmp_d = analise.comparar_sped_detalhamento(linha_mes["por_categoria"], cdb.det_por_categoria(conn, ops_d[nome_d]), trib)
                st.dataframe(pd.DataFrame({"Categoria": cmp_d["categoria"], "SPED": cmp_d["sped"].map(_brl), "Detalhamento": cmp_d["det"].map(_brl),
                                           "Diferença (Detalhamento − SPED)": cmp_d["dif"].map(lambda v: formatacao.moeda_br(v, forcar_sinal=True))}),
                             hide_index=True, width="stretch")
                st.caption("Depreciação e imobilizado vêm do SPED e não têm nota no Detalhamento — a diferença nessas linhas é esperada. "
                           "A conferência nota a nota continua na aba Conferência.")
                _grafico(gr.fig_comparacao_categorias(pd.DataFrame({"item": cmp_d["categoria"], "a": cmp_d["sped"], "b": cmp_d["det"]}), DARK, "SPED", "Detalhamento"))

        # controle do mês: oficial / conferido / nota (organização, sem travas)
        with st.expander(f"🗂️ Controle de {linha_mes['rotulo']}: versão oficial, conferido e nota", expanded=False):
            if not tem30:
                st.info("Para guardar a versão oficial, a marcação de conferido e as notas, falta uma atualização no banco (bloco 30 do schema.sql: "
                        "rodar `scripts/bloco30_controle_creditos.txt` no SQL Editor do Supabase). O restante do dashboard funciona normalmente.")
            else:
                st.caption("Isso só organiza: a **oficial** é a versão em que o relatório em PDF se baseia; **conferido** é uma marcação sua; a **nota** aparece no relatório. "
                           "Nada aqui impede editar, trocar de versão ou gerar relatório.")
                chaves = [None] + [f["chave"] for f in fontes_mes]
                nomes = {None: _REGRA_PADRAO, **{f["chave"]: f["rotulo"] for f in fontes_mes}}
                atual_of = ctrl_mes.get("fonte_oficial") if ctrl_mes.get("fonte_oficial") in chaves else None
                inicial = escolhas.get(mes) if escolhas.get(mes) in chaves else atual_of
                sel_of = st.selectbox("Versão oficial deste mês", chaves, index=chaves.index(inicial), format_func=lambda c: nomes[c], key=f"cred_of_{emp}_{mes}_{len(escolhas)}")
                if escolhas.get(mes):
                    st.caption("A escolha acima começou na versão da sua simulação — salve para torná-la oficial.")
                conf = st.checkbox("Marcar este mês como conferido", value=bool(ctrl_mes.get("conferido")), key=f"cred_conf_{emp}_{mes}")
                nota = st.text_area("Nota do mês (aparece no relatório)", value=ctrl_mes.get("nota") or "", key=f"cred_nota_{emp}_{mes}", height=90)
                if st.button("💾 Salvar o controle deste mês", key=f"cred_btn_ctrl_{emp}_{mes}", type="primary"):
                    n = cdb.salvar_mes(conn, emp, mes, usuario, fonte_oficial=sel_of, conferido=conf, nota=nota)
                    if escolhas.get(mes) and sel_of == escolhas.get(mes):
                        st.session_state.pop(f"cred_sim_{emp}_{mes}", None)
                    flash("ok", f"Controle de {mes} salvo." if n else "Nada mudou neste mês.")
                    st.rerun()
                lm = cdb.log_mes(conn, emp, mes)
                if not lm.empty:
                    st.markdown("**Histórico deste mês**")
                    lm["em"] = lm["em"].map(formatacao.hora_br)
                    st.dataframe(lm.rename(columns={"em": "Quando", "usuario": "Quem", "campo": "Campo", "valor_antes": "Antes", "valor_depois": "Depois"}), hide_index=True, width="stretch")

        with st.expander("📋 Ver os números em tabela"):
            tab = serie[["rotulo", "origem", "sped_tipo", "modo", "cred_total", "orig_total", "dif_total", "deb_nc_total", "deb_cum_total", "usado_total",
                         "saldo_total", "ret_total", "recolher_total", "receita_nc", "receita_cum"]].rename(columns={
                "rotulo": "Mês", "origem": "Origem", "sped_tipo": "SPED", "modo": "Versão", "cred_total": "Crédito apontado", "orig_total": "Crédito no original",
                "dif_total": "Diferença", "deb_nc_total": "Débito NC", "deb_cum_total": "Débito cumulativo", "usado_total": "Crédito utilizado",
                "saldo_total": "Saldo de crédito", "ret_total": "Retenções", "recolher_total": "A recolher", "receita_nc": "Receita NC",
                "receita_cum": "Receita cumulativa"})
            st.dataframe(tab, hide_index=True, width="stretch")
        st.caption("Jan–jul: carga fixa feita a partir dos SPED já enviados (original × última retificadora). De agosto em diante vale a versão **apontada** do SPED de cada mês, "
                   "a menos que a contadora marque outra como oficial; o \"original\" é o primeiro SPED Original enviado no mês (quando a versão em uso é uma retificadora).")
