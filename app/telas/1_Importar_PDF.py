# -*- coding: utf-8 -*-
"""
Tela IMPORTAR PDF — equivalente ao botao ImportarPDF do PAINEL.

Fluxo (ver PROJETO_EGC_v3.0.md secao 8 / Modulo_Enermais):
  1. Upload de 1+ PDFs (BP e/ou DRE, pode ser mais de 1 por vez).
  2. Roda processar_pdf() do parser original (mesma logica, zero
     reescrita) em cada arquivo.
  3. Mostra previa das contas extraidas (empresa/CNPJ/periodo detectados
     + tabela BP/DRE) ANTES de gravar — contadora confere antes.
  4. So grava no banco quando ela confirma. Se ja existirem linhas ATIVAS
     do mesmo empresa+periodo+tipo, elas sao inativadas antes (nunca
     sobrescritas/apagadas) — mesma regra do "FIX CRITICO" do VBA.
"""
import sys
import tempfile
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import db  # noqa: E402
from parser_egc import processar_pdf  # noqa: E402
from validacoes import checar_fechamento_bp, cnpj_bate, formatar_br  # noqa: E402

st.title("📥 Importar PDF")

usuario = usuario_atual()

cod_empresa, nome_empresa, usuario = sidebar_contexto(usuario)
cnpj_esperado = next(cnpj for cod, _nome, cnpj in EMPRESAS_FIXAS if cod == cod_empresa)
st.caption(f"Empresa selecionada na barra lateral: **{nome_empresa}** — confira antes de importar.")

arquivos = st.file_uploader("PDFs (SPED, BP e/ou DRE)", type=["pdf"], accept_multiple_files=True)

if arquivos and st.button("1. Processar PDFs (pré-visualizar)", type="primary"):
    resultados = []
    with tempfile.TemporaryDirectory() as tmp:
        for arq in arquivos:
            caminho = Path(tmp) / arq.name
            caminho.write_bytes(arq.getbuffer())
            bp_rows, dre_rows, log, meta = processar_pdf(caminho)
            resultados.append(
                {
                    "arquivo": arq.name,
                    "bp_rows": bp_rows,
                    "dre_rows": dre_rows,
                    "log": log,
                    "meta": meta,
                }
            )
    st.session_state["import_resultados"] = resultados

resultados = st.session_state.get("import_resultados")
if resultados:
    st.divider()
    st.subheader("2. Pré-visualização — confira antes de gravar")

    algum_erro = False
    algum_mismatch = False
    for r in resultados:
        with st.expander(f"📄 {r['arquivo']}", expanded=True):
            for nivel, arq, tipo, msg in r["log"]:
                if nivel == "ERRO":
                    algum_erro = True
                    st.error(f"[{tipo}] {msg}")
                elif nivel == "ALERTA":
                    st.warning(f"[{tipo}] {msg}")
                elif nivel == "AVISO":
                    st.info(f"[{tipo}] {msg}")
                elif nivel == "OK":
                    st.success(f"[{tipo}] {msg}")

            if r["meta"]:
                empresa, cnpj, periodo, nome_arq, tipo, fmt = r["meta"][0]
                st.write(f"**Empresa detectada:** {empresa} ({cnpj}) — **Período:** {periodo} — **Tipo:** {tipo} — **Formato:** {fmt}")
                if not cnpj_bate(cnpj, cnpj_esperado):
                    algum_mismatch = True
                    st.error(
                        f"⚠️ CNPJ do PDF ({cnpj or 'não identificado'}) não bate com a empresa "
                        f"selecionada na barra lateral (**{nome_empresa}** — {cnpj_esperado}). "
                        "Troque a empresa no menu à esquerda ou confira se é o PDF certo antes de gravar."
                    )

            if r["bp_rows"]:
                st.write(f"**BP — {len(r['bp_rows'])} contas**")
                st.dataframe(r["bp_rows"], column_config=None, use_container_width=True,
                             hide_index=True)
                total_ativo, total_passivo = checar_fechamento_bp(r["bp_rows"])
                if total_ativo is not None and total_passivo is not None:
                    diferenca = round(total_ativo - total_passivo, 2)
                    if abs(diferenca) > 0.01:
                        st.warning(
                            f"⚠️ Ativo ≠ Passivo neste BP: Ativo R$ {formatar_br(total_ativo)} × "
                            f"Passivo R$ {formatar_br(total_passivo)} (diferença R$ {formatar_br(diferenca)}). "
                            "Pode ser divergência do próprio PDF fonte (já visto antes) — não bloqueia "
                            "a gravação, mas confira antes de fechar o período."
                        )
            if r["dre_rows"]:
                st.write(f"**DRE — {len(r['dre_rows'])} contas**")
                st.dataframe(r["dre_rows"], use_container_width=True, hide_index=True)

    if algum_erro:
        st.error("Há erros de leitura em pelo menos um PDF — corrija/confira antes de gravar.")
    if algum_mismatch:
        st.error("Há PDF(s) de empresa diferente da selecionada — corrija antes de gravar (veja acima).")

    st.divider()
    st.subheader("3. Confirmar gravação")
    st.caption(
        "Se já existir um período ativo igual (mesma empresa/período/tipo), "
        "ele será arquivado automaticamente antes — nada é apagado."
    )
    if st.button("✅ Gravar no banco", type="primary", disabled=algum_erro or algum_mismatch):
        conn = get_conn()
        total_gravado = 0
        periodos_tocados = set()
        for r in resultados:
            if not r["meta"]:
                continue
            empresa_nome, cnpj, periodo, nome_arq, tipo_doc, fmt = r["meta"][0]
            # periodo vem como string "dd/mm/aaaa" do parser -> date
            import datetime as _dt
            try:
                periodo_date = _dt.datetime.strptime(periodo, "%d/%m/%Y").date()
            except Exception:
                st.error(f"Não consegui interpretar o período '{periodo}' de {r['arquivo']} — pulei este arquivo.")
                continue

            for tipo, rows in (("BP", r["bp_rows"]), ("DRE", r["dre_rows"])):
                if not rows:
                    continue
                n_inativados = db.inativar_periodo_existente(conn, cod_empresa, periodo_date, tipo)
                n_gravados = db.inserir_lancamentos(
                    conn, cod_empresa, tipo, periodo_date, rows, r["arquivo"], usuario
                )
                total_gravado += n_gravados
                periodos_tocados.add(periodo_date)
                nivel = "AVISO" if n_inativados else "OK"
                msg = f"{n_gravados} conta(s) gravada(s)" + (
                    f" — {n_inativados} linha(s) do período anterior arquivada(s) automaticamente" if n_inativados else ""
                )
                db.registrar_importacao(
                    conn, cod_empresa, periodo_date, [r["arquivo"]], nivel, tipo, msg, usuario
                )

        st.session_state["import_desfazer"] = {
            "empresa": cod_empresa,
            "nome_empresa": nome_empresa,
            "periodos": sorted(periodos_tocados),
        }
        st.success(f"Importação concluída: {total_gravado} lançamento(s) gravado(s).")
        del st.session_state["import_resultados"]
        st.rerun()

desfazer = st.session_state.get("import_desfazer")
if desfazer and desfazer["empresa"] == cod_empresa:
    st.divider()
    periodos_fmt = ", ".join(p.strftime("%m/%Y") for p in desfazer["periodos"])
    st.info(f"Última importação: **{desfazer['nome_empresa']}** — período(s) {periodos_fmt}.")
    st.caption(
        "Arquiva (não apaga) o que acabou de ser gravado nesta empresa. Se um período anterior foi "
        "arquivado automaticamente por esta importação, ele NÃO volta sozinho — use Arquivar/Recuperar "
        "pra reativá-lo, se precisar."
    )
    if st.button("↩️ Desfazer esta importação"):
        conn = get_conn()
        total = 0
        for p in desfazer["periodos"]:
            total += db.arquivar_periodo(conn, desfazer["empresa"], p)
        st.success(f"{total} lançamento(s) arquivado(s). Desfeito.")
        del st.session_state["import_desfazer"]
        st.rerun()
