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
from importacoes_ui import (  # noqa: E402
    chave_ordenacao_previa, agrupar_historico_importacoes, detectar_conflitos_lote, chave_documento_item,
    assinatura_item, inferir_granularidade_nome,
)
import formatacao  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}

# Ordem de colunas de saida do parser (parser_egc.build_output, conferido
# 22/09/2026 antes deste fix) -- BP e DRE tem ordem DIFERENTE entre si.
# Usado so' pra nomear a previa (item 1 do feedback do Rafael, 22/09/2026:
# "esse nome da tabela '0, 1, 2...' nao conseguimos nomear?") -- nao muda
# a extracao nem o que e gravado no banco, so' o rotulo mostrado na tela.
COLUNAS_BP = ["Grupo", "Conta", "Valor", "Origem"]
COLUNAS_DRE = ["Conta", "Valor", "Grupo", "Origem"]

# Editavel pela contadora (FIX_20260929c) -- ela confirma/corrige a
# granularidade detectada antes de gravar. "" = "não declarada" (mesmo
# rotulo usado em 2_Revisao_Correcao.py/3_Arquivar_Recuperar.py/
# 4_Visao_Grupo.py/8_Relatorio_Comentado.py pra consistencia visual).
_OPCOES_GRANULARIDADE = {
    "": "Não declarada", "mensal": "Mensal", "bimestral": "Bimestral", "trimestral": "Trimestral",
    "semestral": "Semestral", "anual": "Anual", "outra": "Outro intervalo",
}

def indicadores_rotulo(g: str) -> str:
    return _OPCOES_GRANULARIDADE.get(g or "", g or "Não declarada")


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
    # Pre-leitura: granularidade DETECTADA dos DREs do lote por (CNPJ, periodo) --
    # serve de sugestao para o BP do mesmo fechamento (o BP nao declara intervalo).
    _gran_dre_lote: dict = {}
    for _r in resultados:
        if _r.get("meta") and _r.get("dre_rows"):
            _m = _r["meta"][0]
            if _m[7]:
                _gran_dre_lote.setdefault((_m[1], _m[2]), set()).add(_m[7])
    for i, r in enumerate(resultados):
        with st.expander(f"📄 {r['arquivo']}", expanded=True):
            # FIX_20260929 (Rafael testando o reset: subiu por engano um
            # PDF antigo -- "Consolidado Grupo Enermais" -- misturado no
            # lote; o arquivo tem CNPJ valido e contas extraidas, entao
            # nao caia em nenhum dos 2 casos de erro automatico abaixo
            # (CNPJ nao bate / sem dados) -- nao tinha como tirar da
            # lista sem reiniciar a pagina inteira. Remover/desmarcar
            # agora fica disponivel em QUALQUER arquivo, com erro
            # detectado ou nao -- a contadora decide, o sistema nao
            # precisa "adivinhar" que aquele arquivo especifico e' ruim.
            r["_sig"] = assinatura_item(r)
            r["_i"] = i  # FIX_20260930: guarda o indice original -- usado
            # pra gerar keys unicas pros controles espelhados na secao 3
            # (Confirmar gravacao), pedido do Rafael: "queria q as funções
            # ficassem ali com o gravar tb, podendo remover ou alguma
            # forma de apagar por la tb".
            col_incl, col_rem = st.columns([3, 1])
            incluir = col_incl.checkbox(
                "Incluir nesta gravação", value=True, key=f"incluir_{r['_sig']}",
                help="Desmarque pra deixar este arquivo de fora do grupo de gravação "
                     "abaixo, sem perder o processamento (fica pendente na lista).",
            )
            r["_incluir"] = incluir
            if col_rem.button("🗑️ Remover da lista", key=f"remover_manual_{r['_sig']}"):
                st.session_state["import_resultados"] = [
                    x for x in st.session_state["import_resultados"] if x is not r
                ]
                if not st.session_state["import_resultados"]:
                    del st.session_state["import_resultados"]
                st.rerun()

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
                # Granularidade (29/09/2026, FIX_20260929c): editavel pela
                # contadora -- ela confirma ou corrige o que foi detectado
                # antes de gravar (pedido do Rafael: "ela define e confirma
                # se e' trimestral, semestral, etc"). So' aparece pra DRE --
                # BP nao tem intervalo, e' foto de 1 data (nunca teve
                # periodo_inicio pra comecar), entao nao faz sentido pedir
                # pra contadora "inventar" uma cobertura que o PDF nao
                # declara (REGRA DE OURO: nunca inventa numero/dado).
                if tipo == "DRE":
                    if periodo_inicio and granularidade:
                        st.caption(f"📅 Cobertura declarada no PDF: {periodo_inicio} a {periodo} — detectei **{granularidade}**. Confirme ou corrija abaixo:")
                    else:
                        st.caption("📅 Não consegui identificar o intervalo declarado no PDF — selecione a granularidade manualmente:")
                    granularidade_escolhida = st.selectbox(
                        "Granularidade deste DRE",
                        list(_OPCOES_GRANULARIDADE.keys()),
                        index=list(_OPCOES_GRANULARIDADE.keys()).index(granularidade if granularidade in _OPCOES_GRANULARIDADE else ""),
                        format_func=lambda g: _OPCOES_GRANULARIDADE[g],
                        key=f"granularidade_confirmada_{r['_sig']}",
                    )
                    r["_granularidade_confirmada"] = granularidade_escolhida
                else:
                    # BP: o PDF nao declara intervalo (e' foto de 1 data), mas o NOME do
                    # arquivo costuma dizer ("1º Semestre", "2º Trimestre"). Sugestao:
                    # nome do arquivo -> DRE irmao do lote (se inequivoco) -> "".
                    # A contadora confirma/corrige no seletor (pedido do Rafael 01/10/2026:
                    # "nem consigo setar o balanço patrimonial pra qual período é").
                    g_nome = inferir_granularidade_nome(r["arquivo"])
                    irmaos = _gran_dre_lote.get((cnpj, periodo), set())
                    if g_nome:
                        sugerida, origem = g_nome, "pelo nome do arquivo"
                    elif len(irmaos) == 1:
                        sugerida, origem = next(iter(irmaos)), "pelo DRE do mesmo fechamento neste lote"
                    else:
                        sugerida, origem = "", None
                    if origem:
                        st.caption(f"📅 O BP não declara o intervalo — sugeri **{indicadores_rotulo(sugerida)}** {origem}. Confirme ou corrija:")
                    else:
                        st.caption("📅 O BP não declara o intervalo e o nome do arquivo não diz — escolha a que fechamento ele pertence:")
                    r["_granularidade_confirmada"] = st.selectbox(
                        "Fechamento a que este BP pertence",
                        list(_OPCOES_GRANULARIDADE.keys()),
                        index=list(_OPCOES_GRANULARIDADE.keys()).index(sugerida if sugerida in _OPCOES_GRANULARIDADE else ""),
                        format_func=lambda g: _OPCOES_GRANULARIDADE[g],
                        key=f"granularidade_bp_{r['_sig']}",
                    )

                resolucao = empresa_por_cnpj(cnpj)
                if resolucao:
                    cod_r, nome_r = resolucao
                    r["_cod"], r["_nome"] = cod_r, nome_r
                    st.success(f"✅ Empresa confirmada automaticamente: **{nome_r}** ({cod_r})")
                elif cnpj:
                    r["_bloqueado"] = True
                    st.error(
                        f"⚠️ CNPJ {cnpj} não corresponde a nenhuma das 6 empresas cadastradas. "
                        "Este arquivo NÃO será gravado (confira se é o PDF certo) -- use "
                        "\"Remover da lista\" no topo do card pra tirá-lo."
                    )
                else:
                    st.warning("CNPJ não identificado neste PDF — selecione a empresa manualmente:")
                    # FIX_20260928g: opção -1 = "-- selecione --" como default
                    # (index=0 do widget) -- antes o selectbox pulava direto
                    # pra EMPRESAS_FIXAS[0] (Enermais Energia), sem exigir
                    # escolha nenhuma. Achado revisando o histórico de
                    # egc.lancamentos: uma linha com arquivo_pdf "Construtora -
                    # DRE - 2º Trimestre 2026.pdf" ficou gravada com
                    # empresa_codigo=ENERGIA (21/09/2026) -- mecanismo mais
                    # provável é exatamente este: CNPJ não identificado nesse
                    # PDF, e o default silencioso pra Energia (1ª da lista)
                    # passou sem ninguém notar/trocar antes de clicar "Gravar".
                    opcoes_idx = [-1] + list(range(len(EMPRESAS_FIXAS)))
                    nomes = [nome for _cod, nome, _cnpj in EMPRESAS_FIXAS]
                    idx = st.selectbox(
                        "Empresa (manual)", opcoes_idx,
                        format_func=lambda j: "-- selecione --" if j == -1 else nomes[j],
                        key=f"empresa_manual_{r['_sig']}",
                    )
                    if idx == -1:
                        r["_bloqueado"] = True
                        st.error("⚠️ Escolha a empresa antes de gravar este arquivo.")
                    else:
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
                        "Não há nada pra gravar — este arquivo não aparece na seção de gravação "
                        "(use \"Remover da lista\" no topo do card pra tirá-lo)."
                    )

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

    # FIX_20260930c (BUG REAL, Rafael em produção: relatório dá "não tem
    # BP/DRE gravado" mesmo depois de subir e gravar os 2 juntos -- o
    # seletor de período mostrava 2 opções pro MESMO PDF/fechamento --
    # ex. "30/06/2026" e "30/06/2026 — Trimestral" separados). Causa
    # raiz: BP nunca tem periodo_inicio declarado (é foto de 1 data), e
    # o código sempre fixava a granularidade confirmada do BP em "" --
    # mas quando o DRE do MESMO fechamento (mesma empresa+período, no
    # MESMO lote) tem uma granularidade != "" confirmada, BP e DRE do
    # mesmo pacote iam pra buckets DIFERENTES na chave ativa (empresa+
    # tipo+periodo+granularidade) -- nenhum relatório consegue achar os
    # 2 juntos porque, pro banco, viram 2 "documentos" desencontrados.
    # BP tem que HERDAR a granularidade confirmada do DRE irmão (mesma
    # empresa+período neste lote) -- são o mesmo fechamento. So' NAO
    # propaga se houver mais de 1 granularidade DIFERENTE confirmada pro
    # mesmo par empresa+período no lote (caso ambíguo de verdade -- ex.
    # 2 DREs de abrangência diferente pro mesmo fechamento -- nesse caso
    # avisa em vez de adivinhar).
    granularidades_por_periodo: dict = {}
    ambiguos_periodo = set()
    for r in resultados:
        if not r.get("meta") or not r.get("dre_rows"):
            continue
        _e, _c, periodo_str, _n, _t, _f, _pi, _g = r["meta"][0]
        chave = (r.get("_cod"), periodo_str)
        g_conf = r.get("_granularidade_confirmada", "")
        if not g_conf:
            continue
        existente = granularidades_por_periodo.get(chave)
        if existente is not None and existente != g_conf:
            ambiguos_periodo.add(chave)
        else:
            granularidades_por_periodo[chave] = g_conf
    for r in resultados:
        if not r.get("meta") or not r.get("bp_rows") or r.get("dre_rows"):
            continue  # so' propaga pra BP "puro" (arquivo so' com BP)
        if r.get("_granularidade_confirmada"):
            continue  # BP ja' tem fechamento definido (nome do arquivo ou escolha da contadora)
        _e, _c, periodo_str, _n, _t, _f, _pi, _g = r["meta"][0]
        chave = (r.get("_cod"), periodo_str)
        if chave in ambiguos_periodo:
            st.warning(
                f"⚠️ {r['arquivo']}: há mais de 1 DRE com granularidade diferente pra "
                f"{periodo_str} nesse lote -- não consigo decidir sozinho qual o BP "
                "acompanha. Confira manualmente antes de gravar (pode gerar período "
                "'órfão' sem relatório)."
            )
            continue
        if chave in granularidades_por_periodo and not r.get("_granularidade_confirmada"):
            r["_granularidade_confirmada"] = granularidades_por_periodo[chave]

    if algum_erro:
        st.error("Há erros de leitura em pelo menos um PDF — corrija/confira antes de gravar.")

    # Agrupa por empresa resolvida -- gravar fica por empresa, nao 1 botao
    # pro lote inteiro (pedido do Rafael: lote com varios CNPJs deveria
    # poder confirmar/gravar empresa por empresa).
    grupos = {}
    bloqueados = [r for r in resultados if r["_bloqueado"]]
    sem_dados = [r for r in resultados if r["_sem_dados"]]
    # FIX_20260929: checkbox "Incluir nesta gravação" (default True) deixa
    # fora do grupo sem precisar remover -- resolve o caso de um arquivo
    # que processa normal (CNPJ ok, tem contas) mas a contadora nao quer
    # gravar agora (ex.: PDF antigo/errado misturado no lote por engano).
    excluidos_checkbox = [
        r for r in resultados
        if not r["_bloqueado"] and not r["_sem_dados"] and r["_cod"] and not r.get("_incluir", True)
    ]
    for r in resultados:
        if r["_bloqueado"] or r["_sem_dados"] or not r["_cod"] or not r.get("_incluir", True):
            continue
        g = grupos.setdefault(r["_cod"], {"nome": r["_nome"], "itens": []})
        g["itens"].append(r)

    st.divider()
    st.subheader("3. Confirmar gravação (por empresa)")
    if bloqueados:
        st.caption(f"{len(bloqueados)} arquivo(s) não identificado(s) não aparecem aqui — veja o aviso na prévia acima.")
    if sem_dados:
        st.caption(f"{len(sem_dados)} arquivo(s) sem BP/DRE extraído não aparecem aqui — veja o aviso na prévia acima.")
    if excluidos_checkbox:
        nomes_excl = ", ".join(x["arquivo"] for x in excluidos_checkbox)
        st.caption(f"☐ {len(excluidos_checkbox)} arquivo(s) desmarcado(s) não entram na gravação: {nomes_excl}")

    if not grupos:
        st.info("Nenhum arquivo pronto pra gravar ainda.")

    for cod_g, grupo in grupos.items():
        with st.container(border=True):
            st.markdown(f"**{grupo['nome']}** ({cod_g}) — {len(grupo['itens'])} arquivo(s)")
            # FIX_20260930: incluir/remover espelhados aqui (pedido do
            # Rafael: "queria q as funções ficassem ali com o gravar tb") --
            # mesma acao dos controles la em cima (secao 2), so' que sem
            # precisar rolar a pagina. Key usa r["_i"] (indice original,
            # gravado na secao 2) pra nao colidir com os checkboxes/botoes
            # de la -- os 2 conjuntos de controles ficam sincronizados via
            # r["_incluir"]/remocao por identidade, valendo a partir do
            # proximo rerun (mesmo comportamento ja existente dos de cima).
            remover_agora = None
            for x in grupo["itens"]:
                idx = x.get("_i")
                col_nome, col_incl2, col_rem2 = st.columns([3, 1, 1])
                col_nome.caption(x["arquivo"])
                incluir2 = col_incl2.checkbox(
                    "Incluir", value=x.get("_incluir", True), key=f"incluir_grav_{x['_sig']}",
                )
                x["_incluir"] = incluir2
                if col_rem2.button("🗑️ Remover", key=f"remover_grav_{x['_sig']}"):
                    remover_agora = x
            if remover_agora is not None:
                st.session_state["import_resultados"] = [
                    y for y in st.session_state["import_resultados"] if y is not remover_agora
                ]
                if not st.session_state["import_resultados"]:
                    del st.session_state["import_resultados"]
                st.rerun()
            # 01/10/2026 (Rafael: "o que acontece se eu upar os 2 BP e DRE do
            # mesmo periodo ao mesmo tempo? ... podia gerar um aviso p
            # confirmar"). Duas travas ANTES de gravar:
            #  (1) CONFLITO NO LOTE: 2+ PDFs da mesma empresa com o mesmo
            #      documento (tipo + periodo + granularidade). Antes, o
            #      ultimo (ordem alfabetica do nome) substituia o primeiro
            #      em silencio. Agora a contadora escolhe qual manter.
            #  (2) SUBSTITUICAO: ja existe documento ATIVO igual no banco --
            #      gravar arquiva o antigo; exige confirmacao explicita.
            itens_incluidos = [x for x in grupo["itens"] if x.get("_incluir", True)]
            excluidos_conflito: set = set()  # {(arquivo, tipo)} nao escolhidos
            conflitos_abertos = 0
            for cf in detectar_conflitos_lote(itens_incluidos):
                rot_g = indicadores_rotulo(cf["granularidade"])
                st.error(
                    f"⚠️ {len(cf['arquivos'])} arquivos com o MESMO {cf['tipo']} de {cf['periodo']} ({rot_g}): "
                    + "; ".join(f"{nome} ({n} contas)" for nome, n in cf["arquivos"])
                    + ". Só um pode valer — os outros seriam arquivados."
                )
                opcoes_cf = ["-- escolha qual manter --"] + [nome for nome, _n in cf["arquivos"]]
                escolha = st.selectbox(
                    f"Manter qual {cf['tipo']} de {cf['periodo']}?", opcoes_cf,
                    key=f"conflito_{cod_g}_{cf['tipo']}_{cf['periodo']}_{cf['granularidade']}",
                )
                if escolha == opcoes_cf[0]:
                    conflitos_abertos += 1
                else:
                    for nome, _n in cf["arquivos"]:
                        if nome != escolha:
                            excluidos_conflito.add((nome, cf["tipo"]))

            substituicoes = []  # [(tipo, periodo_str, rotulo, [docs ativos], [arquivos novos])]
            try:
                _vistos = {}
                for x in itens_incluidos:
                    for tipo_k, periodo_k, gran_k, _n in chave_documento_item(x):
                        if (x["arquivo"], tipo_k) in excluidos_conflito:
                            continue
                        _vistos.setdefault((tipo_k, periodo_k, gran_k), []).append(x["arquivo"])
                for (tipo_k, periodo_k, gran_k), novos_arqs in _vistos.items():
                    _dt_k = _dt.datetime.strptime(periodo_k, "%d/%m/%Y").date()
                    existentes = db.listar_documentos_ativos(get_conn(), cod_g, _dt_k, tipo_k, gran_k)
                    if existentes:
                        substituicoes.append((tipo_k, periodo_k, indicadores_rotulo(gran_k), existentes, novos_arqs))
            except Exception:
                substituicoes = []  # nao consegue checar -> nao bloqueia (comportamento anterior)
            confirmou_substituir = True
            if substituicoes:
                # Trimestral e semestral do MESMO fim de periodo NAO se
                # substituem (granularidade faz parte da chave). Este aviso so'
                # aparece quando ja existe o MESMO documento (tipo + periodo +
                # granularidade) -- reimportacao/correcao do mesmo fechamento.
                for tipo_k, periodo_k, rot_k, existentes, novos_arqs in substituicoes:
                    st.warning(
                        f"♻️ O arquivo {', '.join(novos_arqs)} será gravado como **{tipo_k} {rot_k}** de "
                        f"{periodo_k}, e já existe um {tipo_k} {rot_k} ATIVO desse período: "
                        + "; ".join(
                            f"{e['arquivo']} ({e['contas']} contas"
                            + (f", gravado em {formatacao.hora_br(e['gravado_em'])}" if e.get("gravado_em") else "")
                            + ")" for e in existentes
                        )
                        + ". Gravar arquiva (não apaga) o atual e passa a valer o novo. "
                        "Se a granularidade acima estiver errada, corrija no seletor do card do arquivo."
                    )
                _sig_subst = "_".join(sorted(x["_sig"] for x in itens_incluidos))
                confirmou_substituir = st.checkbox(
                    "Confirmo: substituir o(s) documento(s) acima pelo(s) novo(s)",
                    key=f"confirma_subst_{cod_g}_{_sig_subst}",
                )
            bloqueado_por_duplicidade = conflitos_abertos > 0 or not confirmou_substituir

            if st.button(f"✅ Gravar {grupo['nome']}", key=f"gravar_{cod_g}", type="primary",
                         disabled=algum_erro or bloqueado_por_duplicidade):
                conn = get_conn()
                total_gravado = 0
                pulados = []
                for r in grupo["itens"]:
                    if not r["meta"]:
                        continue
                    if not r.get("_incluir", True):
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
                    # FIX_20260929c: usa a granularidade CONFIRMADA pela
                    # contadora na previa (widget por linha, secao 2) -- nao
                    # a detectada crua -- se ela corrigiu, e' essa que vale.
                    # r.get(...) com fallback pro valor detectado cobre o
                    # caso (nao deveria acontecer) de o widget nao ter
                    # rodado pra este item.
                    granularidade_confirmada = r.get("_granularidade_confirmada", granularidade or "")
                    # FIX_20260930b (BUG REAL, Rafael em produção: "Falha ao
                    # gravar: null value in column granularidade... violates
                    # not-null constraint"): "or None" convertia "" (BP, ou
                    # DRE sem intervalo declarado) pra None -- e' exatamente
                    # o caso mais comum. Desde o bloco 16 do schema.sql (já
                    # rodado em produção), a coluna é NOT NULL DEFAULT '' --
                    # mas o DEFAULT só vale quando a coluna fica DE FORA do
                    # INSERT; como db.inserir_lancamentos sempre lista a
                    # coluna, um None explícito vira NULL de verdade e
                    # quebra a constraint. "" tem que continuar "" até o
                    # banco.
                    granularidade_val = granularidade_confirmada

                    for tipo, rows in (("BP", r["bp_rows"]), ("DRE", r["dre_rows"])):
                        if not rows:
                            continue
                        if (r["arquivo"], tipo) in excluidos_conflito:
                            continue  # perdeu o desempate do conflito (nao escolhido)
                        try:
                            # Fase 3 (29/09/2026): so' inativa o que tem a MESMA
                            # granularidade -- um trimestral novo nao apaga mais
                            # um semestral ativo (ou vice-versa) que porventura
                            # feche na MESMA data (ver db.inativar_periodo_existente).
                            n_inativados = db.inativar_periodo_existente(
                                conn, cod_g, periodo_date, tipo, granularidade=granularidade_confirmada,
                            )
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
                                    # Fase 4 (30/09/2026): itens ficam amarrados ao
                                    # documento (periodo + granularidade) -- um
                                    # semestral nao apaga mais o ranking do trimestral.
                                    db.salvar_despesas_admin_itens(
                                        conn, cod_g, periodo_date, r["admin_itens"], r["arquivo"],
                                        granularidade=granularidade_val,
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
                # itens desmarcados em "Incluir" continuam pendentes na lista;
                # o resto (gravado, ou que perdeu o desempate de conflito) sai.
                gravados_ids = {id(x) for x in grupo["itens"] if x.get("_incluir", True)}
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
# FIX_20260930d (Rafael: "tentei desfazer, o desfazer não acontece nada
# la"): 2 causas reais. (1) st.success() seguido de st.rerun() na mesma
# execucao -- a mensagem e' substituida antes da pessoa conseguir ler
# (gotcha classico do Streamlit). Guarda o resultado no session_state e
# mostra ele aqui, ANTES do loop, sobrevivendo ao rerun. (2) esta lista
# vem de egc.importacoes (log de auditoria IMUTAVEL -- nunca muda depois
# que grava), entao a linha do historico continua igualzinha depois do
# Desfazer -- nao e' bug, mas sem indicacao nenhuma parecia que nada
# tinha acontecido. Por isso cada linha agora mostra a situacao ATUAL
# (Ativo/Arquivado) cruzando com egc.lancamentos de verdade.
if st.session_state.get("_desfazer_msg"):
    nivel, msg = st.session_state.pop("_desfazer_msg")
    (st.success if nivel == "ok" else st.warning)(msg)
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
    # situacao ATUAL (nao o log estatico) -- 1 consulta por empresa que
    # aparece na lista, reaproveitada em todas as linhas dela.
    periodos_ativos_por_empresa: dict = {}
    status_indisponivel = False
    for ev in eventos:
        cod_ev = ev["empresa_codigo"]
        if cod_ev not in periodos_ativos_por_empresa:
            try:
                periodos_ativos_por_empresa[cod_ev] = {
                    d["periodo"] for d in db.listar_periodos_detalhado(conn, cod_ev, status="ATIVO")
                }
            except Exception:
                # nao trava a lista inteira por isso -- so' deixa de
                # mostrar a situacao atual (fica "?" mais abaixo).
                periodos_ativos_por_empresa[cod_ev] = None
                status_indisponivel = True
    if status_indisponivel:
        st.caption("⚠️ Não consegui verificar a situação atual de todas as linhas agora.")

    for ev in eventos:
        cod_ev = ev["empresa_codigo"]
        periodo_ev = ev["periodo"]
        nome_ev = NOME_POR_COD.get(cod_ev, cod_ev)
        quando = formatacao.hora_br(ev["criado_em"], vazio="?")
        tipos_label = ", ".join(t for t, _msg in ev["tipos"]) or "?"
        _periodos_empresa = periodos_ativos_por_empresa.get(cod_ev)
        situacao_desconhecida = _periodos_empresa is None
        ainda_ativo = (not situacao_desconhecida) and (periodo_ev in _periodos_empresa)
        col_a, col_b = st.columns([4, 1])
        col_a.write(
            f"**{nome_ev}** — {periodo_ev.strftime('%m/%Y')} · {tipos_label} · "
            f"gravado por {ev['usuario'] or '?'} em {quando}"
        )
        detalhes = " · ".join(f"{t}: {msg}" for t, msg in ev["tipos"] if msg)
        if detalhes:
            col_a.caption(detalhes)
        # FIX_20260929: nome do(s) arquivo(s) de origem no historico --
        # pedido do Rafael depois do caso do PDF "Consolidado" misturado
        # por engano num lote (dado ja existia em egc.importacoes.arquivos,
        # so' nao aparecia aqui -- ver docstring de agrupar_historico_importacoes).
        if ev.get("arquivos"):
            col_a.caption("📄 " + ", ".join(ev["arquivos"]))
        if situacao_desconhecida:
            col_a.caption("❓ Situação atual indisponível agora")
        else:
            col_a.caption("🟢 Ativo" if ainda_ativo else "🗄️ Já arquivado (sem lançamentos ativos pra este período)")
        if ainda_ativo or situacao_desconhecida:
            if col_b.button("↩️ Desfazer", key=f"hist_desfazer_{cod_ev}_{periodo_ev}"):
                conn = get_conn()
                total = db.arquivar_periodo(conn, cod_ev, periodo_ev)
                if total:
                    st.session_state["_desfazer_msg"] = (
                        "ok",
                        f"✅ {total} lançamento(s) de {nome_ev} ({periodo_ev.strftime('%m/%Y')}) arquivado(s). "
                        "Reative em Arquivar/Recuperar se precisar.",
                    )
                else:
                    st.session_state["_desfazer_msg"] = (
                        "warn",
                        f"⚠️ Nenhum lançamento ATIVO encontrado pra {nome_ev} ({periodo_ev.strftime('%m/%Y')}) "
                        "-- pode já ter sido arquivado em outra sessão/aba nesse meio tempo.",
                    )
                st.rerun()
        else:
            col_b.caption("—")
