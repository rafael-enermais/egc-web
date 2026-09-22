# -*- coding: utf-8 -*-
"""
Tela ARQUIVAR / RECUPERAR — equivalente a ArquivarImportacao/
RecuperarImportacao do VBA (PROJETO_EGC_v3.0.md secao 8).

Regra preservada: os dois botões só ALTERNAM o status (ATIVO <-> INATIVO)
da empresa+período escolhido — nunca apagam nada. "Desfazer última
importação" foi removido do sistema antigo por decisão de segurança
(risco de exclusão permanente) e não existe aqui também, de propósito.

Melhoria em relação ao VBA: como aqui pdf_original vive na mesma linha
de egc.lancamentos (não numa aba separada), um ciclo Arquivar -> Recuperar
NUNCA perde o valor original do PDF, mesmo em período com correção manual
— a limitação conhecida do sistema antigo (seção 7 do handoff) não existe
nesta versão.

Mudanca de 21/09/2026: seletor de Empresa proprio desta pagina, nao
depende mais do dropdown da sidebar (removido de conexao.sidebar_contexto
-- ver nota la' sobre o efeito colateral entre paginas que isso causava).
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import db  # noqa: E402

st.title("🗄️ Arquivar / Recuperar importação")

usuario = usuario_atual()
sidebar_contexto(usuario)  # so' rodape -- ver nota em conexao.sidebar_contexto
conn = get_conn()

nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
idx_empresa = st.selectbox(
    "Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes_emp[i], key="arquivar_empresa_sel",
)
cod_empresa, nome_empresa, cnpj_empresa = EMPRESAS_FIXAS[idx_empresa]
st.caption(cnpj_empresa)


def _rotulo_periodo(d):
    # pedido do Rafael 21/09/2026: mostrar CNPJ junto do periodo em cada
    # opcao (antes so' aparecia o mes/ano), pra confirmar de bate-pronto
    # qual empresa esta' sendo arquivada/recuperada sem depender so' do
    # dropdown da sidebar
    return f"{d.strftime('%m/%Y')} — {nome_empresa} ({cnpj_empresa})"

def _log_seguro(nivel, mensagem, periodo=None, detalhe=None):
    # log completo (task #16) -- melhor-esforco, mesmo padrao de app.py/
    # 2_Revisao_Correcao.py.
    try:
        db.registrar_evento(conn, "arquivar_recuperar", nivel, mensagem, empresa_codigo=cod_empresa,
                             periodo=periodo, usuario=usuario, detalhe=detalhe)
    except Exception:
        pass


col_arq, col_rec = st.columns(2)

with col_arq:
    st.subheader("Arquivar")
    st.caption(f"Períodos ATIVOS de **{nome_empresa}** — inativa (não apaga).")
    ativos = db.listar_periodos(conn, cod_empresa, status="ATIVO")
    if not ativos:
        st.info("Nenhum período ativo.")
    else:
        escolhidos = st.multiselect(
            "Selecione o(s) período(s) para arquivar",
            ativos,
            format_func=_rotulo_periodo,
            key="arquivar_sel",
        )
        if escolhidos and st.button("📦 Arquivar selecionado(s)", type="primary"):
            total = 0
            erros = []
            for p in escolhidos:
                try:
                    total += db.arquivar_periodo(conn, cod_empresa, p)
                except Exception as exc:
                    erros.append((p, str(exc)))
                    _log_seguro("ERRO", "Falha ao arquivar período", periodo=p, detalhe=str(exc))
            if total:
                _log_seguro("INFO", f"{total} lançamento(s) arquivado(s) em {len(escolhidos) - len(erros)} período(s)")
                st.success(f"{total} lançamento(s) arquivado(s) em {len(escolhidos) - len(erros)} período(s).")
            if erros:
                st.error(f"{len(erros)} período(s) NÃO foram arquivados (erro no banco) — ver log de eventos: "
                         + "; ".join(f"{p.strftime('%m/%Y')}: {e}" for p, e in erros))
            st.rerun()

with col_rec:
    st.subheader("Recuperar")
    st.caption(f"Períodos ARQUIVADOS de **{nome_empresa}** — reativa.")
    inativos = db.listar_periodos(conn, cod_empresa, status="INATIVO")
    if not inativos:
        st.info("Nenhum período arquivado.")
    else:
        escolhidos_r = st.multiselect(
            "Selecione o(s) período(s) para recuperar",
            inativos,
            format_func=_rotulo_periodo,
            key="recuperar_sel",
        )
        if escolhidos_r and st.button("♻️ Recuperar selecionado(s)", type="primary"):
            total = 0
            erros = []
            for p in escolhidos_r:
                try:
                    total += db.recuperar_periodo(conn, cod_empresa, p)
                except Exception as exc:
                    erros.append((p, str(exc)))
                    _log_seguro("ERRO", "Falha ao recuperar período", periodo=p, detalhe=str(exc))
            if total:
                _log_seguro("INFO", f"{total} lançamento(s) recuperado(s) em {len(escolhidos_r) - len(erros)} período(s)")
                st.success(f"{total} lançamento(s) recuperado(s) em {len(escolhidos_r) - len(erros)} período(s).")
            if erros:
                st.error(f"{len(erros)} período(s) NÃO foram recuperados (erro no banco) — ver log de eventos: "
                         + "; ".join(f"{p.strftime('%m/%Y')}: {e}" for p, e in erros))
            st.rerun()
