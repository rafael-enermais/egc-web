# -*- coding: utf-8 -*-
"""
Tela IMPORTAR PDF — equivalente ao botao ImportarPDF do PAINEL.

Fluxo (ver PROJETO_EGC_v3.0.md secao 8 / Modulo_Enermais):
  1. Upload de 1+ PDFs (BP e/ou DRE, pode ser mais de 1 por vez, de
     empresas DIFERENTES no mesmo lote -- nao precisa ser tudo da mesma).
  2. Roda processar_pdf() do parser original (mesma logica, zero
     reescrita) em cada arquivo.
  3. Mostra previa das contas extraidas (empresa/CNPJ/periodo detectados
     + tabela BP/DRE) ANTES de gravar — contadora confere antes.
  4. So grava no banco quando ela confirma. Se ja existirem linhas ATIVAS
     do mesmo empresa+periodo+tipo, elas sao inativadas antes (nunca
     sobrescritas/apagadas) — mesma regra do "FIX CRITICO" do VBA.

Empresa por CNPJ, nao por selecao previa (mudanca de 21/09/2026, pedido
do Rafael): antes, a empresa que definia ONDE gravar vinha da selecao
manual na sidebar (mesma pra todo o lote) -- e so' comparava o CNPJ do
PDF contra ela pra bloquear se nao batesse. Isso obrigava selecionar 1
empresa por vez mesmo quando o lote tinha PDFs de varios CNPJs
diferentes, o que nao faz sentido: quem sabe a empresa certa e' o
proprio PDF (o parser ja extrai o CNPJ). Agora cada arquivo resolve a
PROPRIA empresa automaticamente via conexao.empresa_por_cnpj(); a
contadora so' confirma visualmente antes de gravar (preview mostra
"Empresa confirmada" por arquivo). A sidebar continua existindo (outras
2 telas dependem dela pra saber o que exibir) mas nao influencia mais
onde este import grava.
"""
import sys
import tempfile
import datetime as _dt
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS, empresa_por_cnpj  # noqa: E402
import db  # noqa: E402
from parser_egc import processar_pdf  # noqa: E402
from validacoes import checar_fechamento_bp, formatar_br  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}

st.title("📥 Importar PDF")

usuario = usuario_atual()
sidebar_contexto(usuario)  # so' pro layout/rodape -- nao usa o retorno aqui

st.caption(
    "A empresa de cada PDF é identificada automaticamente pelo CNPJ (confirme na prévia "
    "antes de gravar) — pode subir PDFs de empresas diferentes no mesmo lote. O seletor "
    "de empresa na barra lateral não afeta esta tela, só Revisão/Correção e Arquivar/Recuperar."
)

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
    for i, r in enumerate(resultados):
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

            r["_cod"] = None
            r["_nome"] = None
            r["_bloqueado"] = False

            if r["meta"]:
                empresa, cnpj, periodo, nome_arq, tipo, fmt = r["meta"][0]
                st.write(f"**Empresa detectada no PDF:** {empresa} ({cnpj or 'CNPJ não identificado'}) — **Período:** {periodo} — **Tipo:** {tipo} — **Formato:** {fmt}")

                resolucao = empresa_por_cnpj(cnpj)
                if resolucao:
                    cod_r, nome_r = resolucao
                    r["_cod"], r["_nome"] = cod_r, nome_r
                    st.success(f"✅ Empresa confirmada automaticamente: **{nome_r}** ({cod_r})")
                elif cnpj:
                    r["_bloqueado"] = True
                    st.error(
                        f"⚠️ CNPJ {cnpj} não corresponde a nenhuma das 6 empresas cadastradas. "
                        "Este arquivo NÃO será gravado (confira se é o PDF certo)."
                    )
                else:
                    st.warning("CNPJ não identificado neste PDF — selecione a empresa manualmente:")
                    nomes = [nome for _cod, nome, _cnpj in EMPRESAS_FIXAS]
                    idx = st.selectbox(
                        "Empresa (manual)", range(len(EMPRESAS_FIXAS)),
                        format_func=lambda j: nomes[j], key=f"empresa_manual_{i}",
                    )
                    cod_m, nome_m, _cnpj_m = EMPRESAS_FIXAS[idx]
                    r["_cod"], r["_nome"] = cod_m, nome_m

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

    n_bloqueados = sum(1 for r in resultados if r["_bloqueado"])
    n_prontos = len(resultados) - n_bloqueados
    st.divider()
    st.subheader("3. Confirmar gravação")
    st.caption(
        f"{n_prontos} de {len(resultados)} arquivo(s) prontos pra gravar"
        + (f" — {n_bloqueados} não será(ão) gravado(s) por CNPJ não cadastrado (veja acima)." if n_bloqueados else ".")
        + " Se já existir um período ativo igual (mesma empresa/período/tipo), ele será arquivado "
        "automaticamente antes — nada é apagado."
    )
    if st.button("✅ Gravar no banco", type="primary", disabled=algum_erro):
        conn = get_conn()
        total_gravado = 0
        pulados = []
        periodos_tocados = set()
        for r in resultados:
            if not r["meta"] or r["_bloqueado"] or not r["_cod"]:
                if r["_bloqueado"]:
                    pulados.append(r["arquivo"])
                continue
            cod_r = r["_cod"]
            empresa_nome, cnpj, periodo, nome_arq, tipo_doc, fmt = r["meta"][0]
            # periodo vem como string "dd/mm/aaaa" do parser -> date
            try:
                periodo_date = _dt.datetime.strptime(periodo, "%d/%m/%Y").date()
            except Exception:
                st.error(f"Não consegui interpretar o período '{periodo}' de {r['arquivo']} — pulei este arquivo.")
                pulados.append(r["arquivo"])
                continue

            for tipo, rows in (("BP", r["bp_rows"]), ("DRE", r["dre_rows"])):
                if not rows:
                    continue
                n_inativados = db.inativar_periodo_existente(conn, cod_r, periodo_date, tipo)
                n_gravados = db.inserir_lancamentos(
                    conn, cod_r, tipo, periodo_date, rows, r["arquivo"], usuario
                )
                total_gravado += n_gravados
                periodos_tocados.add((cod_r, periodo_date))
                nivel = "AVISO" if n_inativados else "OK"
                msg = f"{n_gravados} conta(s) gravada(s)" + (
                    f" — {n_inativados} linha(s) do período anterior arquivada(s) automaticamente" if n_inativados else ""
                )
                db.registrar_importacao(
                    conn, cod_r, periodo_date, [r["arquivo"]], nivel, tipo, msg, usuario
                )

        st.session_state["import_desfazer"] = {"periodos": sorted(periodos_tocados)}
        msg_final = f"Importação concluída: {total_gravado} lançamento(s) gravado(s)."
        if pulados:
            msg_final += f" {len(pulados)} arquivo(s) pulado(s): {', '.join(pulados)}."
        st.success(msg_final)
        del st.session_state["import_resultados"]
        st.rerun()

desfazer = st.session_state.get("import_desfazer")
if desfazer and desfazer["periodos"]:
    st.divider()
    resumo = ", ".join(
        f"{NOME_POR_COD.get(cod, cod)} ({p.strftime('%m/%Y')})" for cod, p in desfazer["periodos"]
    )
    st.info(f"Última importação: {resumo}.")
    st.caption(
        "Arquiva (não apaga) o que acabou de ser gravado. Se um período anterior foi arquivado "
        "automaticamente por esta importação, ele NÃO volta sozinho — use Arquivar/Recuperar pra "
        "reativá-lo, se precisar."
    )
    if st.button("↩️ Desfazer esta importação"):
        conn = get_conn()
        total = 0
        for cod, p in desfazer["periodos"]:
            total += db.arquivar_periodo(conn, cod, p)
        st.success(f"{total} lançamento(s) arquivado(s). Desfeito.")
        del st.session_state["import_desfazer"]
        st.rerun()
