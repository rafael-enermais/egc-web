# -*- coding: utf-8 -*-
"""
Tela IMPORTAR PDF — equivalente ao botao ImportarPDF do PAINEL.

Fluxo:
  1. Upload de 1+ PDFs (BP e/ou DRE, pode ser mais de 1 por vez, de
     empresas DIFERENTES no mesmo lote).
  2. Roda processar_pdf() do parser original (mesma logica, zero
     reescrita) em cada arquivo. O uploader e' limpo logo em seguida
     (key dinamica) -- os dados ja processados vivem so' em
     session_state["import_resultados"], entao arquivo velho sentado no
     uploader nao pode mais se misturar com um upload novo.
  3. Previa por arquivo, empresa resolvida automaticamente pelo CNPJ
     (conexao.empresa_por_cnpj) -- contadora confere antes de gravar.
  4. Confirmar/gravar e' POR EMPRESA (nao 1 botao pro lote inteiro):
     cada empresa resolvida no lote tem seu proprio grupo com botao
     "Gravar <empresa>". Ao gravar um grupo, so' aqueles arquivos saem
     da previa -- o resto do lote (outras empresas, ou coisas ainda nao
     confirmadas) continua ali, pronta pra fluir pro proximo.
  5. "Importações recentes" no fim da tela: historico de verdade (tabela
     egc.importacoes, nao session_state) com opcao de desfazer (arquivar)
     QUALQUER uma, nao so' a ultima da sessao atual -- sobrevive a
     logout/F5.

Mudancas de 21/09/2026 (feedback do Rafael testando ao vivo):
  - Empresa por CNPJ em vez de selecao previa na sidebar (ja' entregue
    antes desta leva).
  - BUG: uploader mantinha arquivos ja processados visiveis, causando
    reprocessamento junto com o upload seguinte -- corrigido com key
    dinamica + rerun logo apos processar.
  - Gravar virou por empresa (grupo), nao 1 botao pro lote inteiro.
  - Desfazer virou historico de verdade (db.listar_importacoes_recentes),
    nao so' "a ultima importacao desta sessao".
  - Arquivo com empresa resolvida mas ZERO contas BP/DRE extraidas (ex.:
    Balancete) nao aparece mais pronto-pra-gravar (flag _sem_dados).
  - Dedup por nome de arquivo (mesmo lote ou contra o que ja tava
    pendente) -- evita gravar copia duplicada do mesmo PDF.
"""
import sys
import tempfile
import datetime as _dt
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS, empresa_por_cnpj  # noqa: E402
import db  # noqa: E402
from parser_egc import processar_pdf, extrair_despesas_admin_itens  # noqa: E402
from validacoes import checar_fechamento_bp, formatar_br  # noqa: E402
from importacoes_ui import chave_ordenacao_previa, agrupar_historico_importacoes  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}

# Ordem de colunas de saida do parser (parser_egc.build_output, conferido
# 22/09/2026 antes deste fix) -- BP e DRE tem ordem DIFERENTE entre si.
# Usado so' pra nomear a previa (item 1 do feedback do Rafael, 22/09/2026:
# "esse nome da tabela '0, 1, 2...' nao conseguimos nomear?") -- nao muda
# a extracao nem o que e gravado no banco, so' o rotulo mostrado na tela.
COLUNAS_BP = ["Grupo", "Conta", "Valor", "Origem"]
COLUNAS_DRE = ["Conta", "Valor", "Grupo", "Origem"]

st.title("📥 Importar PDF")

usuario = usuario_atual()
sidebar_contexto(usuario)  # so' pro layout/rodape -- nao usa o retorno aqui

st.caption(
    "A empresa de cada PDF é identificada automaticamente pelo CNPJ (confirme na prévia "
    "antes de gravar) — pode subir PDFs de empresas diferentes no mesmo lote. O seletor "
    "de empresa na barra lateral não afeta esta tela, só Revisão/Correção e Arquivar/Recuperar."
)

if "uploader_key_n" not in st.session_state:
    st.session_state["uploader_key_n"] = 0

arquivos = st.file_uploader(
    "PDFs (SPED, BP e/ou DRE)", type=["pdf"], accept_multiple_files=True,
    key=f"uploader_{st.session_state['uploader_key_n']}",
)

if arquivos and st.button("1. Processar PDFs (pré-visualizar)", type="primary"):
    # dedup por nome de arquivo -- tanto dentro do MESMO lote (selecionou
    # o msm arquivo 2x sem querer) quanto contra o que ja estava pendente
    # de uma leva anterior. Achado pelo Rafael testando (2 PDFs identicos
    # de "Construtora - Balanco Patrimonial" apareceram juntos na previa e
    # no grupo de gravacao) -- sem isso, gravar duas copias identicas nao
    # quebra (inativar_periodo_existente cobre a 1a antes da 2a entrar),
    # mas grava lixo duplicado (INATIVO) e confunde a tela.
    novos = []
    nomes_vistos_no_lote = set()
    duplicados_no_lote = []
    with tempfile.TemporaryDirectory() as tmp:
        for arq in arquivos:
            if arq.name in nomes_vistos_no_lote:
                duplicados_no_lote.append(arq.name)
                continue
            nomes_vistos_no_lote.add(arq.name)
            caminho = Path(tmp) / arq.name
            caminho.write_bytes(arq.getbuffer())
            bp_rows, dre_rows, log, meta = processar_pdf(caminho)
            # FIX_20260925b: itens (sub-contas) de Administrativas, so'
            # quando o arquivo tem DRE -- alimenta despesas_admin_itens do
            # relatorio comentado (Fase 2). Extracao aditiva e independente
            # do parser normal (ver parser_egc.extrair_despesas_admin_itens);
            # falha nela nunca deve travar a importacao do BP/DRE em si.
            admin_itens = []
            if dre_rows:
                try:
                    admin_itens = extrair_despesas_admin_itens(caminho)
                except Exception:
                    admin_itens = []
            novos.append(
                {
                    "arquivo": arq.name,
                    "bp_rows": bp_rows,
                    "dre_rows": dre_rows,
                    "admin_itens": admin_itens,
                    "log": log,
                    "meta": meta,
                }
            )

    # soma ao que ja estava pendente (nao substitui) -- assim da pra
    # processar em varias levas sem perder o que ainda nao foi gravado --
    # mas nao duplica nome ja pendente
    pendentes = st.session_state.get("import_resultados", [])
    nomes_pendentes = {r["arquivo"] for r in pendentes}
    duplicados_ja_pendentes = [n["arquivo"] for n in novos if n["arquivo"] in nomes_pendentes]
    novos_unicos = [n for n in novos if n["arquivo"] not in nomes_pendentes]
    st.session_state["import_resultados"] = pendentes + novos_unicos

    duplicados = duplicados_no_lote + duplicados_ja_pendentes
    if duplicados:
        st.session_state["import_duplicados_aviso"] = duplicados
    elif "import_duplicados_aviso" in st.session_state:
        del st.session_state["import_duplicados_aviso"]

    # limpa o uploader (key nova) pra nao ficar arquivo velho visivel
    # misturando com o proximo upload
    st.session_state["uploader_key_n"] += 1
    st.rerun()

aviso_duplicados = st.session_state.pop("import_duplicados_aviso", None)
if aviso_duplicados:
    st.warning(
        f"⚠️ {len(aviso_duplicados)} arquivo(s) com o mesmo nome já pendente não foram processados "
        f"de novo (evita gravar duplicado): {', '.join(aviso_duplicados)}"
    )

resultados = st.session_state.get("import_resultados")
if resultados:
    # reordena por CNPJ+periodo (BP antes de DRE) so' pra exibicao/gravacao
    # -- mesmos objetos (sorted() nao copia os dicts), entao remover por
    # identidade (`is not r`) mais abaixo continua funcionando igual.
    resultados = sorted(resultados, key=chave_ordenacao_previa)

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
            r["_sem_dados"] = False

            if r["meta"]:
                empresa, cnpj, periodo, nome_arq, tipo, fmt, periodo_inicio, granularidade = r["meta"][0]
                st.write(f"**Empresa detectada no PDF:** {empresa} ({cnpj or 'CNPJ não identificado'}) — **Período:** {periodo} — **Tipo:** {tipo} — **Formato:** {fmt}")
                # Granularidade real (24/09/2026): so' mostra quando o proprio
                # PDF declarou o intervalo -- BP nao tem (e foto de 1 data),
                # e nunca e' inferida por calculo, so' lida do documento.
                if periodo_inicio and granularidade:
                    st.caption(f"📅 Cobertura detectada: {periodo_inicio} a {periodo} — **{granularidade}**")
                elif tipo == "DRE":
                    st.caption("📅 Cobertura: não consegui identificar o intervalo declarado no PDF (granularidade ficará em branco).")

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
                    if st.button("🗑️ Remover da lista", key=f"remover_{i}"):
                        st.session_state["import_resultados"] = [
                            x for x in st.session_state["import_resultados"] if x is not r
                        ]
                        if not st.session_state["import_resultados"]:
                            del st.session_state["import_resultados"]
                        st.rerun()
                else:
                    st.warning("CNPJ não identificado neste PDF — selecione a empresa manualmente:")
                    nomes = [nome for _cod, nome, _cnpj in EMPRESAS_FIXAS]
                    idx = st.selectbox(
                        "Empresa (manual)", range(len(EMPRESAS_FIXAS)),
                        format_func=lambda j: nomes[j], key=f"empresa_manual_{i}",
                    )
                    cod_m, nome_m, _cnpj_m = EMPRESAS_FIXAS[idx]
                    r["_cod"], r["_nome"] = cod_m, nome_m

                # arquivo com empresa resolvida mas sem NENHUMA conta BP/DRE
                # extraida (ex.: Balancete -- tipo nao suportado hoje, ou PDF
                # vazio/corrompido) nao pode aparecer como "pronto pra gravar":
                # antes disso o botao "Gravar" ficava habilitado mesmo sem ter
                # nada pra gravar de fato (achado revisando o teste do Rafael
                # com o Balancete da Construtora, 21/09/2026).
                if not r["_bloqueado"] and not r["bp_rows"] and not r["dre_rows"]:
                    r["_sem_dados"] = True
                    st.warning(
                        "⚠️ Nenhuma conta de BP ou DRE foi extraída deste arquivo (tipo de "
                        "documento não suportado, ex.: Balancete, ou PDF sem essas páginas). "
                        "Não há nada pra gravar — este arquivo não aparece na seção de gravação."
                    )
                    if st.button("🗑️ Remover da lista", key=f"remover_semdados_{i}"):
                        st.session_state["import_resultados"] = [
                            x for x in st.session_state["import_resultados"] if x is not r
                        ]
                        if not st.session_state["import_resultados"]:
                            del st.session_state["import_resultados"]
                        st.rerun()

            if r["bp_rows"]:
                st.write(f"**BP — {len(r['bp_rows'])} contas**")
                st.dataframe(pd.DataFrame(r["bp_rows"], columns=COLUNAS_BP),
                             use_container_width=True, hide_index=True)
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
                st.dataframe(pd.DataFrame(r["dre_rows"], columns=COLUNAS_DRE),
                             use_container_width=True, hide_index=True)

    if algum_erro:
        st.error("Há erros de leitura em pelo menos um PDF — corrija/confira antes de gravar.")

    # Agrupa por empresa resolvida -- gravar fica por empresa, nao 1 botao
    # pro lote inteiro (pedido do Rafael: lote com varios CNPJs deveria
    # poder confirmar/gravar empresa por empresa).
    grupos = {}
    bloqueados = [r for r in resultados if r["_bloqueado"]]
    sem_dados = [r for r in resultados if r["_sem_dados"]]
    for r in resultados:
        if r["_bloqueado"] or r["_sem_dados"] or not r["_cod"]:
            continue
        g = grupos.setdefault(r["_cod"], {"nome": r["_nome"], "itens": []})
        g["itens"].append(r)

    st.divider()
    st.subheader("3. Confirmar gravação (por empresa)")
    if bloqueados:
        st.caption(f"{len(bloqueados)} arquivo(s) não identificado(s) não aparecem aqui — veja o aviso na prévia acima.")
    if sem_dados:
        st.caption(f"{len(sem_dados)} arquivo(s) sem BP/DRE extraído não aparecem aqui — veja o aviso na prévia acima.")

    if not grupos:
        st.info("Nenhum arquivo pronto pra gravar ainda.")

    for cod_g, grupo in grupos.items():
        with st.container(border=True):
            arquivos_nomes = ", ".join(x["arquivo"] for x in grupo["itens"])
            st.markdown(f"**{grupo['nome']}** ({cod_g}) — {len(grupo['itens'])} arquivo(s): {arquivos_nomes}")
            if st.button(f"✅ Gravar {grupo['nome']}", key=f"gravar_{cod_g}", type="primary", disabled=algum_erro):
                conn = get_conn()
                total_gravado = 0
                pulados = []
                for r in grupo["itens"]:
                    if not r["meta"]:
                        continue
                    empresa_nome, cnpj, periodo, nome_arq, tipo_doc, fmt, periodo_inicio, granularidade = r["meta"][0]
                    try:
                        periodo_date = _dt.datetime.strptime(periodo, "%d/%m/%Y").date()
                    except Exception:
                        st.error(f"Não consegui interpretar o período '{periodo}' de {r['arquivo']} — pulei este arquivo.")
                        pulados.append(r["arquivo"])
                        continue

                    periodo_inicio_date = None
                    if periodo_inicio:
                        try:
                            periodo_inicio_date = _dt.datetime.strptime(periodo_inicio, "%d/%m/%Y").date()
                        except Exception:
                            periodo_inicio_date = None
                    granularidade_val = granularidade or None

                    for tipo, rows in (("BP", r["bp_rows"]), ("DRE", r["dre_rows"])):
                        if not rows:
                            continue
                        try:
                            n_inativados = db.inativar_periodo_existente(conn, cod_g, periodo_date, tipo)
                            n_gravados = db.inserir_lancamentos(
                                conn, cod_g, tipo, periodo_date, rows, r["arquivo"], usuario,
                                periodo_inicio=periodo_inicio_date, granularidade=granularidade_val,
                            )
                            total_gravado += n_gravados
                            nivel = "AVISO" if n_inativados else "OK"
                            msg = f"{n_gravados} conta(s) gravada(s)" + (
                                f" — {n_inativados} linha(s) do período anterior arquivada(s) automaticamente" if n_inativados else ""
                            )
                            db.registrar_importacao(
                                conn, cod_g, periodo_date, [r["arquivo"]], nivel, tipo, msg, usuario
                            )
                            if tipo == "DRE" and r.get("admin_itens"):
                                # FIX_20260925b: falha aqui nunca deve
                                # derrubar a gravacao do DRE em si -- e' so'
                                # o ranking auxiliar do relatorio comentado.
                                try:
                                    db.salvar_despesas_admin_itens(
                                        conn, cod_g, periodo_date, r["admin_itens"], r["arquivo"]
                                    )
                                except Exception:
                                    pass
                        except Exception as exc:
                            # log completo (task #16): falha na gravacao NAO pode travar
                            # os outros arquivos/tipos do lote -- registra e segue.
                            pulados.append(f"{r['arquivo']} ({tipo})")
                            try:
                                db.registrar_importacao(
                                    conn, cod_g, periodo_date, [r["arquivo"]], "ERRO", tipo,
                                    f"Falha ao gravar: {exc}", usuario,
                                )
                            except Exception:
                                pass

                # remove so' os itens deste grupo da lista pendente
                gravados_ids = {id(x) for x in grupo["itens"]}
                restante = [x for x in st.session_state["import_resultados"] if id(x) not in gravados_ids]
                if restante:
                    st.session_state["import_resultados"] = restante
                else:
                    del st.session_state["import_resultados"]

                msg_final = f"{grupo['nome']}: {total_gravado} lançamento(s) gravado(s)."
                if pulados:
                    msg_final += f" {len(pulados)} arquivo(s) pulado(s): {', '.join(pulados)}."
                st.success(msg_final)
                st.rerun()

st.divider()
st.subheader("Importações recentes")
st.caption(
    "Histórico de gravações (todas as sessões, não só a atual). \"Desfazer\" arquiva "
    "(não apaga) o período inteiro — reative depois em Arquivar/Recuperar se precisar."
)
try:
    conn = get_conn()
    brutos = db.listar_importacoes_recentes(conn, limite=50)
    # Fix (item 5 do feedback do Rafael, 22/09/2026): ver docstring de
    # agrupar_historico_importacoes (app/importacoes_ui.py) -- combina
    # BP+DRE do mesmo clique de "Gravar" numa unica linha de historico,
    # em vez de o dedup antigo perder a mensagem do BP silenciosamente.
    eventos = agrupar_historico_importacoes(brutos, limite=10)
except Exception as exc:
    eventos = []
    st.warning(f"Não consegui carregar o histórico agora: {exc}")
    try:
        db.registrar_evento(conn, "importar_pdf", "ERRO", "Falha ao carregar histórico de importações",
                             usuario=usuario, detalhe=str(exc))
    except Exception:
        pass

if not eventos:
    st.caption("Nenhuma importação registrada ainda.")
else:
    for ev in eventos:
        cod_ev = ev["empresa_codigo"]
        periodo_ev = ev["periodo"]
        nome_ev = NOME_POR_COD.get(cod_ev, cod_ev)
        quando = ev["criado_em"].strftime("%d/%m/%Y %H:%M") if ev["criado_em"] else "?"
        tipos_label = ", ".join(t for t, _msg in ev["tipos"]) or "?"
        col_a, col_b = st.columns([4, 1])
        col_a.write(
            f"**{nome_ev}** — {periodo_ev.strftime('%m/%Y')} · {tipos_label} · "
            f"gravado por {ev['usuario'] or '?'} em {quando}"
        )
        detalhes = " · ".join(f"{t}: {msg}" for t, msg in ev["tipos"] if msg)
        if detalhes:
            col_a.caption(detalhes)
        if col_b.button("↩️ Desfazer", key=f"hist_desfazer_{cod_ev}_{periodo_ev}"):
            conn = get_conn()
            total = db.arquivar_periodo(conn, cod_ev, periodo_ev)
            st.success(f"{total} lançamento(s) de {nome_ev} ({periodo_ev.strftime('%m/%Y')}) arquivado(s).")
            st.rerun()
