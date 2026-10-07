# -*- coding: utf-8 -*-
"""
Tela NAO RECORRENTES (v0.48.0) -- alimenta a pagina "EBITDA Ajustado" dos relatorios.

Fluxo: escolhe empresa + periodo (+ base/granularidade) -> adiciona os itens (a regra automatica so'
SUGERE, a pessoa decide) -> CONFIRMA a lista. So' lista confirmada entra no relatorio; qualquer alteracao
depois derruba a confirmacao. Nada e' apagado: "remover" guarda o item e ele pode ser restaurado; tudo fica
na trilha de auditoria (quem, quando, antes/depois). Ver nao_recorrentes.py.
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS, flash, mostrar_flash  # noqa: E402
import db  # noqa: E402
import formatacao  # noqa: E402
import indicadores  # noqa: E402
import nao_recorrentes as nr  # noqa: E402

st.title("🧮 Não recorrentes (EBITDA Ajustado)")

usuario = usuario_atual()
sidebar_contexto(usuario)
conn = get_conn()
mostrar_flash()

st.caption(
    "Itens pontuais que a administração quer somar (ou subtrair) ao EBITDA contábil para chegar ao EBITDA Ajustado. "
    "O sistema **sugere**, mas nada vale até você **confirmar** a lista do período. Se alterar qualquer item depois, "
    "a confirmação cai e o relatório volta a mostrar só até o EBITDA contábil. Nada é apagado: itens removidos ficam guardados."
)

try:
    tabelas_ok = nr.tabelas_existem(conn)
except Exception:
    try:
        conn.rollback()
    except Exception:
        pass
    tabelas_ok = False
if not tabelas_ok:
    st.warning(
        "As tabelas desta tela ainda não existem no banco. Rode o **bloco 25** (arquivo `scripts/bloco25_nao_recorrentes.txt`) "
        "no SQL Editor do Supabase, uma vez, e recarregue a página."
    )
    st.stop()

nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
idx = st.selectbox("Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes_emp[i], key="nr_empresa_sel")
cod, nome_emp, _cnpj = EMPRESAS_FIXAS[idx]

periodos = db.listar_periodos_detalhado(conn, cod, status="ATIVO")
if not periodos:
    st.info("Esta empresa ainda não tem período importado.")
    st.stop()


def _rot_periodo(p):
    g = indicadores.rotulo_granularidade(p["granularidade"])
    return f"{p['periodo'].strftime('%m/%Y')} · {g}" if p["granularidade"] else p["periodo"].strftime("%m/%Y")


ip = st.selectbox("Período", range(len(periodos)), format_func=lambda i: _rot_periodo(periodos[i]), key="nr_periodo_sel")
periodo, gran = periodos[ip]["periodo"], periodos[ip]["granularidade"]

st_conf = nr.status_confirmacao(conn, cod, periodo, gran)
itens = nr.listar_itens(conn, cod, periodo, gran)
total = sum(nr.valor_assinado(i) for i in itens)

if st_conf["status"] == "confirmado":
    st.success(
        f"✅ Lista confirmada por {st_conf['confirmado_por'] or '—'} em {formatacao.hora_br(st_conf['confirmado_em'])} · "
        f"{st_conf['itens']} item(ns) · total {formatacao.moeda_br(st_conf['total'], forcar_sinal=True)}. Já entra no relatório."
    )
elif st_conf["status"] == "alterado":
    st.warning("⚠️ A lista foi alterada depois da última confirmação — **confirme de novo**. Até lá o relatório mostra só até o EBITDA contábil.")
else:
    st.info("Lista ainda **não confirmada**. Até confirmar, o relatório mostra só até o EBITDA contábil (sem ajuste).")

# ------------------------------------------------------------------ itens
st.subheader("Itens deste período")
if itens:
    df = pd.DataFrame([{
        "Categoria": i["categoria"], "Descrição": i["descricao"] or "—",
        "Efeito no EBITDA": formatacao.moeda_br(nr.valor_assinado(i), forcar_sinal=True),
        "Justificativa": i["justificativa"] or "—", "Documento": i["documento"] or "—",
        "Origem": "sugestão aceita" if i["sugerido_regra"] else "manual",
        "Por": i["atualizado_por"] or i["criado_por"] or "—",
    } for i in itens])
    st.dataframe(df, hide_index=True, width="stretch")
    st.caption(f"Total não recorrente: **{formatacao.moeda_br(total, forcar_sinal=True)}**  ·  (+ soma ao EBITDA, − subtrai)")
else:
    st.caption("Nenhum item cadastrado. Se você revisou e **não há** itens não recorrentes neste período, confirme a lista vazia.")

col_a, col_b = st.columns([1, 3])
rotulo_botao = "✅ Confirmar lista" if itens else "✅ Confirmar: não há itens não recorrentes"
if col_a.button(rotulo_botao, type="primary", key=f"nr_confirmar_{cod}_{periodo}_{gran}",
                disabled=(st_conf["status"] == "confirmado")):
    try:
        r = nr.confirmar_lista(conn, cod, periodo, gran, usuario=usuario)
        flash("ok", f"Lista confirmada ({r['itens']} item(ns), total {formatacao.moeda_br(r['total'], forcar_sinal=True)}).")
        st.rerun()
    except Exception as exc:
        st.error(f"Não foi possível confirmar: {exc}")

# editar / remover
if itens:
    with st.expander("✏️ Editar ou remover um item"):
        ie = st.selectbox(
            "Item", range(len(itens)), key="nr_item_edit",
            format_func=lambda i: f"{itens[i]['categoria']} · {itens[i]['descricao'] or '—'} · {formatacao.moeda_br(nr.valor_assinado(itens[i]), forcar_sinal=True)}",
        )
        it = itens[ie]
        e1, e2, e3 = st.columns([2, 1, 1])
        cat_e = e1.selectbox("Categoria", nr.CATEGORIAS, index=nr.CATEGORIAS.index(it["categoria"]) if it["categoria"] in nr.CATEGORIAS else 0,
                             key=f"nr_e_cat_{it['id']}")
        val_e = e2.number_input("Valor (R$)", min_value=0.0, value=float(it["valor"]), step=100.0, format="%.2f", key=f"nr_e_val_{it['id']}")
        sin_e = e3.radio("Efeito", ["Soma ao EBITDA", "Subtrai do EBITDA"], index=0 if it["sinal"] == 1 else 1, key=f"nr_e_sin_{it['id']}")
        desc_e = st.text_input("Descrição", value=it["descricao"] or "", key=f"nr_e_desc_{it['id']}")
        just_e = st.text_input("Justificativa", value=it["justificativa"] or "", key=f"nr_e_just_{it['id']}")
        doc_e = st.text_input("Documento de apoio (opcional)", value=it["documento"] or "", key=f"nr_e_doc_{it['id']}")
        b1, b2 = st.columns(2)
        if b1.button("💾 Salvar alteração", key=f"nr_e_save_{it['id']}"):
            try:
                nr.editar_item(conn, it["id"], cat_e, val_e, 1 if sin_e.startswith("Soma") else -1, descricao=desc_e,
                               contas_dre=it["contas_dre"], justificativa=just_e or None, documento=doc_e or None, usuario=usuario)
                flash("warn", "Item alterado. A confirmação da lista caiu — confirme de novo.")
                st.rerun()
            except Exception as exc:
                st.error(f"Não foi possível salvar: {exc}")
        if b2.button("🗑️ Remover (fica guardado)", key=f"nr_e_del_{it['id']}"):
            try:
                nr.remover_item(conn, it["id"], usuario=usuario)
                flash("warn", "Item removido da lista (continua guardado e pode ser restaurado). Confirme a lista de novo.")
                st.rerun()
            except Exception as exc:
                st.error(f"Não foi possível remover: {exc}")

# removidos
todos = nr.listar_itens(conn, cod, periodo, gran, incluir_removidos=True)
removidos = [i for i in todos if not i["ativo"]]
if removidos:
    with st.expander(f"♻️ Itens removidos ({len(removidos)}) — restaurar"):
        for i in removidos:
            c1, c2 = st.columns([4, 1])
            c1.write(f"{i['categoria']} · {i['descricao'] or '—'} · {formatacao.moeda_br(nr.valor_assinado(i), forcar_sinal=True)}")
            if c2.button("Restaurar", key=f"nr_rest_{i['id']}"):
                try:
                    nr.restaurar_item(conn, i["id"], usuario=usuario)
                    flash("warn", "Item restaurado. Confirme a lista de novo.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Não foi possível restaurar: {exc}")

# ------------------------------------------------------------------ sugestoes
st.subheader("Sugestões a partir da DRE deste período")
st.caption(
    "Só sugestões — nada é cadastrado sozinho. Olham contas que ficam **acima** da linha do EBITDA (despesas administrativas e "
    "equivalência patrimonial); o que já está abaixo (financeiro, tributos sobre o lucro) não pode ser somado de novo."
)
try:
    itens_admin = db.listar_despesas_admin_itens(conn, cod, periodo, granularidade=gran) or (
        db.listar_despesas_admin_itens(conn, cod, periodo, granularidade="") if gran else [])
    dre = db.listar_lancamentos(conn, cod, periodo, "DRE", granularidade=gran)
    equiv = next((float(r["valor"]) for r in dre if "EQUIVALENCIA" in nr._norm(r["conta"])), None)
    sugestoes = nr.sugerir(itens_admin, equiv)
except Exception as exc:
    st.warning(f"Não foi possível montar sugestões: {exc}")
    sugestoes = []
ja = {(i["categoria"], round(float(i["valor"]), 2), i["sinal"]) for i in itens}
sugestoes = [s_ for s_ in sugestoes if (s_["categoria"], s_["valor"], s_["sinal"]) not in ja]
if not sugestoes:
    st.caption("Nenhuma sugestão pendente para este período.")
for k, s_ in enumerate(sugestoes):
    c1, c2 = st.columns([4, 1])
    c1.write(f"**{s_['categoria']}** · {s_['conta']} · {formatacao.moeda_br(s_['valor'] * s_['sinal'], forcar_sinal=True)}")
    if c2.button("Adicionar", key=f"nr_sug_{cod}_{periodo}_{gran}_{k}"):
        try:
            nr.adicionar_item(conn, cod, periodo, gran, s_["categoria"], s_["valor"], s_["sinal"], descricao=s_["descricao"],
                              contas_dre=s_["conta"], sugerido_regra=True, usuario=usuario)
            flash("warn", "Item adicionado a partir da sugestão. Confirme a lista quando terminar.")
            st.rerun()
        except Exception as exc:
            st.error(f"Não foi possível adicionar: {exc}")

# ------------------------------------------------------------------ novo item manual
with st.expander("➕ Adicionar item manualmente", expanded=not itens):
    n1, n2, n3 = st.columns([2, 1, 1])
    cat_n = n1.selectbox("Categoria", nr.CATEGORIAS, key="nr_n_cat")
    val_n = n2.number_input("Valor (R$)", min_value=0.0, value=0.0, step=100.0, format="%.2f", key="nr_n_val")
    sin_n = n3.radio("Efeito", ["Soma ao EBITDA", "Subtrai do EBITDA"], key="nr_n_sin")
    desc_n = st.text_input("Descrição (o que é)", key="nr_n_desc")
    just_n = st.text_input("Justificativa (por que é pontual)", key="nr_n_just")
    doc_n = st.text_input("Documento de apoio (opcional)", key="nr_n_doc")
    if st.button("Adicionar item", key="nr_n_add"):
        try:
            nr.adicionar_item(conn, cod, periodo, gran, cat_n, val_n, 1 if sin_n.startswith("Soma") else -1, descricao=desc_n,
                              justificativa=just_n or None, documento=doc_n or None, usuario=usuario)
            flash("warn", "Item adicionado. Confirme a lista quando terminar.")
            st.rerun()
        except Exception as exc:
            st.error(f"Não foi possível adicionar: {exc}")

# ------------------------------------------------------------------ historico
with st.expander("📜 Histórico deste período (quem mudou o quê)"):
    try:
        hist = nr.listar_historico(conn, cod, periodo, gran, limite=100)
    except Exception as exc:
        st.caption(f"Não foi possível carregar o histórico: {exc}")
        hist = []
    if not hist:
        st.caption("Sem movimentações ainda.")
    for h in hist:
        st.caption(f"{formatacao.hora_br(h['em'])} · {h['por'] or '—'} · **{h['acao']}**"
                   + (f" (item {h['item_id']})" if h["item_id"] else "")
                   + (f" · antes: {h['antes']}" if h["antes"] else "") + (f" · depois: {h['depois']}" if h["depois"] else ""))
