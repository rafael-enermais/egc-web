# -*- coding: utf-8 -*-
"""
Tela RELATORIO COMENTADO (Fase 2, 28/09/2026) — 1ª versão da UI que
chama dados_relatorio_comentado.montar_dados_relatorio() +
gerador_relatorio_comentado.gerar_pdf_completo() com dado REAL do
Supabase, fechando o objetivo original do projeto ("sistema capaz de
gerar relatorios, guardar historicos... facilitar o preenchimento").

Escopo original desta 1ª versão: 1 empresa + 1 período por geração.

FIX_20260929h (Rafael, 29/09: "por mim podemos implantar o
multi-periodos ja tb"): tela agora tem 2 modos -- "Período único"
(fluxo de sempre, Modelo A) e "Comparativo" (Modelo B, 2 a 4 períodos,
gerador_relatorio_comparativo.py + drc.montar_dados_relatorio_comparativo).

FIX_20260929i/j (Rafael, 29/09, ao conferir o 1º PDF comparativo real:
"Falta o seletor de CNPJ não? ... tem q ser possivel gerar o evolutivo
só da Enermais energia, com energia e outro (exemplo) ou com todos os
CNPJ no montante"): modo Comparativo agora tem seletor de empresas
(multiselect) -- 1 empresa sozinha, um subconjunto consolidado, ou
todas (grupo no montante), reaproveitando a infra de Visão Grupo já
testada (db.listar_lancamentos_grupo + visao_grupo.montar_pivot_grupo)
por baixo de drc.montar_dados_relatorio_comparativo(empresas_codigos=).
Os períodos oferecidos são a INTERSEÇÃO dos períodos ativos de todas as
empresas escolhidas -- período que falta numa delas some da lista antes
mesmo de tentar gerar (mais barato que deixar o botão falhar depois).

Na mesma verificação, achado um bug real de UI (não do motor de
desenho nem dos dados): as colunas do PDF real saíram com cabeçalho
"12/2023, 06/2026, 06/2026, 06/2026" -- rótulo repetido, apesar dos
valores financeiros de cada coluna estarem corretos e diferentes entre
si (prova de que só o RÓTULO da UI estava errado, não o dado). Causa:
key do text_input por índice posicional (`..._label_{i}`), que o
Streamlit reusa entre reruns mesmo quando o período daquele slot muda
(gotcha documentado: `value=` só é aplicado na 1ª vez que a key existe
em session_state). Fix: key amarrada ao conjunto exato de períodos
selecionados (`combo_key`), não ao índice -- qualquer mudança na
seleção força uma key nova e o rótulo correto.

Inputs editáveis na tela (decisão do Rafael, 24/09 e 28/09): período
(label/extenso) e administrador/contador/e-mail/site — nenhum vem de
tabela fixa nem é inferido automaticamente.

FIX_20260929k (Rafael, 29/09: "multi-CNPJ, 1 periodo so, como um unico
consolidado... tem q ser possivel gerar CNPJ e periodos a escolha"):
"Período único" agora também aceita multi-empresa (multiselect igual ao
Comparativo, mesma lógica de interseção de períodos) -- 2+ empresas
chama montar_dados_relatorio(empresa_codigo=<lista>), que consolida
("montante") via Modelo A pra 1 UNICO período. Diferente do
Comparativo (Modelo B, que é uma EVOLUÇÃO entre 2-4 períodos e não faz
sentido com 1 período só) -- decisão tomada com o Rafael: o consolidado
de 1 período usa o Modelo A (narrativo completo), não o B.

FIX_20260930b (Rafael, v0.39.0): (1) os 2 botoes por variante viraram UM
seletor de tipo de relatorio (radio horizontal: Demonstrativo Comentado /
Gerencial / Fornecedor "em breve" / Comparativo-Evolucao) + UM botao
"Gerar relatorio"; (2) o match de periodo + granularidade e' obrigatorio
em toda a cadeia de calculo (ver dados_relatorio_comentado e
indicadores.calcular_indicadores(granularidade=)) e a tela mostra, apos
gerar, qual documento (periodo + base) alimentou os numeros.

v0.40.0 -- o que mudou nesta tela (ver selecao_periodos.py):
  - itens selecionaveis = (periodo_fim, granularidade), com rotulo
    explicito ("30/06/2026 — Trimestral"); com varias empresas so' entram
    pares com BP+DRE ATIVOS em TODAS; par que nao casa BLOQUEIA a geracao
    com mensagem; item repetido e rotulo de coluna repetido sao recusados;
  - rotulo padrao de coluna por granularidade (2T/2026, 1S/2026, 2026,
    06/2026, 05-06/2026), editavel;
  - keys dos widgets levam a assinatura de empresas+opcoes (sem estado
    "fantasma" quando empresas/periodos mudam); periodos, rotulos e
    granularidades saem sempre com o mesmo tamanho e ordem;
  - 4a variante "Fornecedor" habilitada; nome do arquivo reflete a
    granularidade REALMENTE usada;
  - "periodo anterior" so' se for o periodo imediatamente anterior da
    MESMA granularidade (senao texto de fallback).
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from auth import usuario_atual  # noqa: E402
from conexao import sidebar_contexto, get_conn, EMPRESAS_FIXAS  # noqa: E402
import db  # noqa: E402
import dados_relatorio_comentado as drc  # noqa: E402
import gerador_relatorio_comentado as g  # noqa: E402
import gerador_relatorio_comparativo as gc  # noqa: E402
import formatacao  # noqa: E402
import visao_grupo  # noqa: E402
import indicadores  # noqa: E402
import selecao_periodos  # noqa: E402

NOME_POR_COD = {cod: nome for cod, nome, _cnpj in EMPRESAS_FIXAS}

st.title("📄 Relatório Comentado")

usuario = usuario_atual()
sidebar_contexto(usuario)
conn = get_conn()

st.caption(
    "Gera o Demonstrativo Comentado (PDF) a partir do BP/DRE já importado. "
    "Confira os campos abaixo antes de gerar — nenhum é preenchido sozinho pelo sistema."
)

_NOME_GRANULARIDADE = {
    "mensal": "Mensal", "bimestral": "Bimestral", "trimestral": "Trimestral", "semestral": "Semestral",
    "anual": "Anual", "outra": "Outro intervalo", "": "",
}

# ─────────── Painel de pendências / completude (movido da Início em
# 29/09/2026 -- "o painel de pendencias da pagina inicio poderia ir p
# pagina do gerador, poderia conferir quais disponivel para geracao do
# relatorio, o fluxo seria melhor tb") ───────────
# Fica AQUI (não mais na Início) porque é exatamente aqui que a
# informação importa na hora: antes de escolher empresa/período pra
# gerar, a contadora já vê quais períodos (e quais granularidades, Fase
# 3) estão prontos (BP+DRE) e quais ainda faltam alguma peça. Zero query
# nova: reaproveita db.listar_periodos_grupo_detalhado/
# listar_lancamentos_grupo_periodos, mesma infra já usada e testada na
# Início/Visão Grupo.
cods_todos = [cod for cod, _nome, _cnpj in EMPRESAS_FIXAS]
with st.expander("📋 Painel de pendências — o que está pronto pra gerar", expanded=False):
    try:
        periodos_grupo_det = db.listar_periodos_grupo_detalhado(conn, cods_todos, status="ATIVO")
        lancs_bp_grupo = db.listar_lancamentos_grupo_periodos(
            conn, [d["periodo"] for d in periodos_grupo_det], "BP", cods_todos, status="ATIVO",
        )
        lancs_dre_grupo = db.listar_lancamentos_grupo_periodos(
            conn, [d["periodo"] for d in periodos_grupo_det], "DRE", cods_todos, status="ATIVO",
        )
        completude = visao_grupo.calcular_completude_grupo(
            periodos_grupo_det, lancs_bp_grupo, lancs_dre_grupo, EMPRESAS_FIXAS,
        )
        resumo_pendencias = visao_grupo.resumir_completude_por_periodo(completude)
    except Exception as exc:
        st.warning(f"Não foi possível montar o painel de pendências: {exc}")
        resumo_pendencias = None

    if resumo_pendencias is None:
        pass
    elif resumo_pendencias.empty:
        st.caption("Sem período nenhum no grupo ainda pra avaliar pendências.")
    else:
        resumo_fmt = resumo_pendencias.assign(
            Período=resumo_pendencias["Período"].apply(lambda p: p.strftime("%m/%Y")),
            Granularidade=resumo_pendencias["Granularidade"].apply(lambda g: _NOME_GRANULARIDADE.get(g, g) or "—"),
        )
        n_incompletos = (resumo_fmt["Status"].str.startswith("⚠️")).sum()
        if n_incompletos == 0:
            st.success("Todos os períodos do grupo com BP e DRE completos nas 6 empresas — pronto pra gerar.")
        else:
            st.caption(
                f"{n_incompletos} período(s)/granularidade(s) com pelo menos 1 empresa faltando BP e/ou DRE "
                "— o relatório dessa empresa nesse período ainda não pode ser gerado."
            )
        st.dataframe(resumo_fmt, hide_index=True, use_container_width=True)

st.divider()


def _pares_completos_por_empresa(cods: list) -> dict:
    """{codigo: {(periodo, granularidade), ...}} -- so' pares com BP **e**
    DRE ATIVOS (v0.40.0; antes bastava existir QUALQUER tipo, e a tela
    oferecia periodos que a geracao depois recusava)."""
    return {
        cod: {
            (d["periodo"], d["granularidade"])
            for d in db.listar_periodos_completos_detalhado(conn, cod, status="ATIVO")
        }
        for cod in cods
    }


def _periodos_intersecao_detalhado(cods: list) -> list:
    """
    Interseção dos (período, granularidade) COMPLETOS (BP+DRE ATIVOS) das
    empresas escolhidas -- mesma lógica no Período único e no Comparativo
    (FIX_20260929k + Fase 3), agora sobre PARES e só com documentos
    gerúveis. Com 1 empresa só, é simplesmente os pares dela.
    Retorna list[tuple[date, str]], ordem CRESCENTE (a seleção default do
    selectbox de "Período único" é o índice 0 -- ver
    test_relatorio_comentado_app.py::test_trocar_periodo_no_unico...).
    """
    if not cods:
        return []
    return selecao_periodos.intersecao_pares(_pares_completos_por_empresa(cods).values())


def _explicar_pares_fora(cods: list):
    """Mostra por que alguns documentos NÃO aparecem na lista: existem
    (ou existiam) em alguma empresa mas não estão completos (BP+DRE
    ATIVOS) em TODAS as escolhidas. Evita a contadora procurar um
    período que a lista escondeu sem explicação."""
    try:
        por_empresa = _pares_completos_por_empresa(cods)
        todos = {
            (d["periodo"], d["granularidade"])
            for cod in cods for d in db.listar_periodos_detalhado(conn, cod, status="ATIVO")
        }
    except Exception:
        return
    fora = {}
    for par in sorted(todos, key=lambda pg: (pg[0], pg[1])):
        faltam = [cod for cod in cods if par not in por_empresa.get(cod, set())]
        if faltam:
            fora[par] = faltam
    if fora:
        st.caption(
            "Fora da lista (precisam de BP **e** DRE ativos em todas as empresas escolhidas): "
            + "; ".join(
                f"{selecao_periodos.rotulo_item(p, g)} — falta em {', '.join(faltam)}"
                for (p, g), faltam in fora.items()
            )
        )


# 01/10/2026 (Rafael: "o seletor de cima, Empresa, ta meio inutil... o relatorio
# e' gerado via seletor de baixo"): confirmado -- o selectbox "Empresa" so'
# servia de valor inicial do multiselect "Empresas no relatorio" (e barrava a
# tela se aquela empresa nao tivesse periodo, mesmo com outras escolhidas).
# Removido; a escolha de empresa(s) e' so' a de baixo.
cod_empresa, nome_empresa, _cnpj = EMPRESAS_FIXAS[0]  # valor inicial do multiselect (Enermais Energia)

try:
    _alguma_empresa_com_periodo = any(
        db.listar_periodos(conn, cod, status="ATIVO") for cod, _n, _c in EMPRESAS_FIXAS
    )
except Exception as exc:
    st.error(f"Não foi possível consultar os períodos: {exc}")
    st.stop()

if not _alguma_empresa_com_periodo:
    st.info("Nenhuma empresa tem período importado ainda (ou todos estão arquivados).")
    st.stop()

st.divider()
# FIX_20260929h (Rafael: "por mim podemos implantar o multi-periodos ja
# tb"): liga o Modelo B (gerador_relatorio_comparativo.py + a nova
# montar_dados_relatorio_comparativo, prontos desde 26-29/09 mas sem UI)
# na tela. Modo "Período único" é o fluxo de sempre, inalterado.
# FIX_20260930b (Rafael: "trocar os 2 botoes por UM seletor de tipo de
# relatorio + um unico botao Gerar"): um radio horizontal escolhe o tipo;
# os campos abaixo se adaptam (periodos multiplos so' no Comparativo).
# v0.40.0: o tipo "Fornecedor" foi construido (mesmo conteudo do padrao,
# capa com selo FORNECEDOR) -- sai o "(em breve)".
# 01/10/2026 (Rafael: "o demonstrativo comentado e o fornecedor sao o msm?
# ... pode apagar 1"): confirmado no codigo -- "padrao" e "fornecedor"
# geram o MESMO conteudo (sem a pagina de despesas), so' muda titulo/selo da
# capa. Sai a opcao "Demonstrativo Comentado" da tela (os 3 tipos que o
# Rafael definiu: Gerencial, Fornecedor, Comparativo); a variante interna
# "padrao" continua existindo no gerador (dados/testes antigos), so' nao e'
# mais oferecida aqui.
TIPO_GERENCIAL = "Demonstrativo Comentado Gerencial"
TIPO_FORNECEDOR = "Demonstrativo Comentado Fornecedor"
TIPO_COMPARATIVO = "Comparativo / Evolução"
_TIPOS_RELATORIO = [TIPO_GERENCIAL, TIPO_FORNECEDOR, TIPO_COMPARATIVO]
if st.session_state.get("relatorio_tipo") not in _TIPOS_RELATORIO:
    st.session_state.pop("relatorio_tipo", None)  # valor guardado de antes da remocao do "padrao"
tipo_relatorio = st.radio("Tipo de relatório", _TIPOS_RELATORIO, key="relatorio_tipo", horizontal=True)
# "modo" (Periodo unico x Comparativo) continua sendo o eixo dos campos
# abaixo; "variante" (padrao/gerencial/fornecedor) so' vale no Periodo unico.
modo = "Comparativo" if tipo_relatorio == TIPO_COMPARATIVO else "Período único"
variante_selecionada = {
    TIPO_GERENCIAL: "gerencial", TIPO_FORNECEDOR: "fornecedor",
}.get(tipo_relatorio, "gerencial")
SUFIXO_ARQUIVO_VARIANTE = {"padrao": "", "gerencial": "_GERENCIAL", "fornecedor": "_FORNECEDOR"}

periodo_sel = None
periodo_label = ""
periodo_extenso = ""
granularidade_sel = ""  # Fase 3 (29/09/2026) — granularidade do período único escolhido
periodos_multi: list = []
periodos_labels_multi: list = []
granularidades_multi: list = []  # Fase 3 — 1 granularidade por período do Comparativo
periodo_range_label = ""
empresas_multi: list = []
bloqueio_comparativo = ""  # v0.40.0 -- motivo (mensagem) que impede gerar o comparativo, se houver

empresas_unico_multi: list = []

if modo == "Período único":
    st.subheader("Empresas no relatório")
    st.caption(
        "Escolha 1 empresa (relatório dela sozinha) ou um subconjunto/todas pra gerar o "
        "CONSOLIDADO (montante) dessas empresas num único período."
    )
    empresas_unico_sel_raw = st.multiselect(
        "Empresas", [cod for cod, _n, _c in EMPRESAS_FIXAS],
        default=[cod_empresa], format_func=lambda c: f"{NOME_POR_COD.get(c, c)} ({c})",
        key="relatorio_empresas_unico_multi",
    )
    empresas_unico_multi = list(empresas_unico_sel_raw)

    periodos_disponiveis_unico: list = []  # list[tuple[date, str]]
    if not empresas_unico_multi:
        st.warning("Escolha pelo menos 1 empresa.")
    else:
        try:
            periodos_disponiveis_unico = _periodos_intersecao_detalhado(empresas_unico_multi)
        except Exception as exc:
            st.error(f"Não foi possível consultar os períodos: {exc}")
        if len(empresas_unico_multi) > 1 and not periodos_disponiveis_unico:
            st.warning(
                "Nenhum período em comum entre as empresas escolhidas (cada uma tem período ativo "
                "em datas/bases diferentes) — reduza a seleção de empresas ou confirme os períodos importados."
            )
        _explicar_pares_fora(empresas_unico_multi)

    if periodos_disponiveis_unico:
        # Cada opção = (periodo_fim, granularidade) com a base SEMPRE no
        # rótulo (v0.40.0): "30/06/2026 — Trimestral" / "— Semestral". A key
        # do widget carrega a assinatura de empresas+opções -- mudou o
        # conjunto, o widget é outro e não herda índice/valor antigo.
        sig_unico = selecao_periodos.assinatura(empresas_unico_multi, periodos_disponiveis_unico)
        periodo_granul_sel = st.selectbox(
            "Período (data de posição do BP)", periodos_disponiveis_unico,
            format_func=lambda pg: selecao_periodos.rotulo_item(pg[0], pg[1]),
            key=f"relatorio_periodo_sel_{sig_unico}",
        )
        periodo_sel, granularidade_sel = periodo_granul_sel

        st.subheader("Período — como aparece no relatório")
        dica_granul = (
            f" O PDF importado declara este período como **{_NOME_GRANULARIDADE.get(granularidade_sel, granularidade_sel)}**"
            f" — reflita isso no rótulo se fizer sentido (ex.: \"2º Trimestre 2026\")."
            if granularidade_sel else ""
        )
        st.caption(f"Confirme o texto que vai aparecer na capa e no cabeçalho do relatório.{dica_granul}")
        # FIX_20260929m: rotulo amarrado ao periodo escolhido (a key muda
        # com o periodo e com a granularidade -- as 2 opcoes do mesmo
        # periodo_fim nao compartilham rotulo default).
        periodo_key_unico = f"{periodo_sel.isoformat()}_{granularidade_sel}"
        col1, col2 = st.columns(2)
        with col1:
            periodo_label = st.text_input(
                "Rótulo do período (capa/cabeçalho)", value=periodo_sel.strftime("%m/%Y"),
                key=f"relatorio_periodo_label_{periodo_key_unico}",
                help='Ex.: "1º Semestre 2026", "Junho/2026", "Exercício 2025".',
            )
        with col2:
            periodo_extenso = st.text_input(
                "Período por extenso (texto de apoio)", value="",
                key=f"relatorio_periodo_extenso_{periodo_key_unico}",
                help='Ex.: "janeiro a junho de 2026". Pode deixar em branco.',
            )
    else:
        granularidade_sel = ""
else:
    st.subheader("Empresas no comparativo")
    st.caption(
        "Escolha 1 empresa (relatório dela sozinha), um subconjunto (consolidado dessas empresas) "
        "ou todas (grupo Enermais no montante)."
    )
    empresas_sel_raw = st.multiselect(
        "Empresas", [cod for cod, _n, _c in EMPRESAS_FIXAS],
        default=[cod_empresa], format_func=lambda c: f"{NOME_POR_COD.get(c, c)} ({c})",
        key="relatorio_empresas_multi",
    )
    empresas_multi = list(empresas_sel_raw)

    periodos_ativos_multi: list = []  # list[tuple[date, str]]
    if not empresas_multi:
        st.warning("Escolha pelo menos 1 empresa.")
    else:
        try:
            periodos_ativos_multi = _periodos_intersecao_detalhado(empresas_multi)
        except Exception as exc:
            st.error(f"Não foi possível consultar os períodos: {exc}")
        if len(empresas_multi) > 1 and not periodos_ativos_multi:
            st.warning(
                "Nenhum período em comum entre as empresas escolhidas (cada uma tem período ativo "
                "em datas/bases diferentes) — reduza a seleção de empresas ou confirme os períodos importados."
            )
        _explicar_pares_fora(empresas_multi)

    st.subheader("Períodos a comparar")
    st.caption(
        "Cada item é um documento: **data + base** (Mensal, Trimestral, Semestral, Anual...). Escolha de 2 a 4 "
        "(a ordem cronológica é ajustada automaticamente) e confirme o título de cada coluna — o sistema "
        "sugere 2T/2026, 1S/2026, 2026... e você pode editar. Só aparecem os documentos com BP e DRE ativos "
        "em TODAS as empresas escolhidas acima, e o mesmo documento não pode entrar duas vezes."
    )
    # v0.40.0: a key carrega a assinatura de empresas+opcoes. Trocar as
    # empresas (ou o banco ganhar/perder um periodo) muda a key -> o
    # Streamlit descarta a selecao antiga em vez de deixar um valor que ja
    # nao esta' nas opcoes (bug visto: multiselect, rotulos e granularidades
    # dessincronizados). A reconciliacao abaixo e' a segunda trava.
    sig_multi = selecao_periodos.assinatura(empresas_multi, periodos_ativos_multi)
    periodos_sel_raw = st.multiselect(
        "Períodos (data + base)", periodos_ativos_multi,
        format_func=lambda pg: selecao_periodos.rotulo_item(pg[0], pg[1]),
        key=f"relatorio_periodos_multi_{sig_multi}",
    )
    pares_escolhidos = selecao_periodos.reconciliar_selecao(periodos_sel_raw, periodos_ativos_multi)

    if pares_escolhidos and not (2 <= len(pares_escolhidos) <= 4):
        st.warning("Escolha de 2 a 4 períodos pra gerar o comparativo (motor de desenho aceita esse intervalo).")

    if 2 <= len(pares_escolhidos) <= 4:
        # Um text_input por ITEM (key = item, não índice nem combinação):
        # tirar/pôr outro período não apaga nem troca o título que a
        # contadora já editou dos que ficaram.
        cols_label = st.columns(len(pares_escolhidos))
        digitados: dict = {}
        for col, (periodo, gran) in zip(cols_label, pares_escolhidos):
            digitados[(periodo, gran)] = col.text_input(
                f"Título da coluna — {selecao_periodos.rotulo_item(periodo, gran)}",
                value=selecao_periodos.rotulo_coluna_padrao(periodo, gran),
                key=f"relatorio_periodo_multi_label_{sig_multi}_{periodo.isoformat()}_{gran}",
            )
        selecao = selecao_periodos.montar_selecao(pares_escolhidos, periodos_ativos_multi, digitados)
        periodos_multi = selecao["periodos"]
        granularidades_multi = selecao["granularidades"]
        periodos_labels_multi = selecao["rotulos"]
        assert len(periodos_multi) == len(granularidades_multi) == len(periodos_labels_multi)

        combo_key = "_".join(f"{p.isoformat()}-{g}" for p, g in selecao["pares"])
        periodo_range_label = st.text_input(
            "Rótulo do intervalo (capa)",
            value=f"{periodos_labels_multi[0]} A {periodos_labels_multi[-1]}",
            key=f"relatorio_periodo_range_label_{sig_multi}_{combo_key}",
        )

        repetidos = selecao_periodos.rotulos_duplicados(periodos_labels_multi)
        if repetidos:
            bloqueio_comparativo = (
                "Há títulos de coluna repetidos (" + ", ".join(sorted(repetidos)) + "). "
                "Dê um título diferente para cada coluna (ex.: 2T/2026 e 1S/2026) — senão o PDF "
                "não distingue trimestral de semestral."
            )
            st.error(bloqueio_comparativo)
        # Defesa em profundidade: toda empresa precisa ter TODOS os pares escolhidos
        # completos (as opções já são a interseção, mas o banco pode ter mudado
        # entre o carregamento da lista e o clique).
        try:
            _por_emp = _pares_completos_por_empresa(empresas_multi)
            _sem_casar = [
                f"{selecao_periodos.rotulo_item(p, g)} em {cod}"
                for (p, g) in selecao["pares"] for cod in empresas_multi if (p, g) not in _por_emp.get(cod, set())
            ]
        except Exception:
            _sem_casar = []
        if _sem_casar:
            bloqueio_comparativo = "Documento sem BP+DRE ativos: " + "; ".join(_sem_casar) + "."
            st.error(bloqueio_comparativo)


st.divider()
st.subheader("Administrador, contador e contato")


def _seletor_contato(tipo: str, cargo_padrao: str, col) -> tuple[str, str]:
    """
    FIX_20260928 (Rafael: "normalmente são as mesmas pessoas... conseguimos
    salvar com dropdown e histórico?"): dropdown carregando contatos já
    salvos (egc.contatos_relatorio, bloco 13) + opção "cadastrar novo" +
    expander pra editar o contato selecionado -- sem trocar a decisão de
    24/09 (continua editável na hora de gerar, nome/cargo digitados aqui
    são o que vai pro PDF, independente de estarem ou não salvos). Se a
    migração do bloco 13 ainda não rodou, listar_contatos_relatorio
    devolve [] e a tela cai pro comportamento antigo (só o campo "novo
    contato"), sem quebrar.

    FIX_20260929b (Rafael: "ficou separado, não seria mais fácil manter 1
    lista só pra ambos... a lista única poderia trocar facilmente os
    lados"): `salvos` agora vem da lista INTEIRA de contatos (sem filtro
    por tipo) -- os 2 seletores (ADMINISTRADOR e CONTADOR) compartilham o
    mesmo dropdown de opções, então um contato salvo de um lado aparece
    pronto pro outro também, sem recadastrar.
    NOVO_SENTINELA: opção fixa pra "não é nenhum salvo, vou digitar" --
    string improvável de colidir com nome real, nunca gravada no banco.
    """
    NOVO_SENTINELA = "➕ Cadastrar novo…"
    try:
        salvos = db.listar_contatos_relatorio(conn)
    except Exception:
        salvos = []

    opcoes = [NOVO_SENTINELA] + [f"{c['nome']} — {c['cargo']}" for c in salvos]
    sel = col.selectbox(f"{tipo.capitalize()}", opcoes, key=f"relatorio_contato_sel_{tipo}")

    if sel == NOVO_SENTINELA:
        nome = col.text_input("Nome", value="", key=f"relatorio_novo_nome_{tipo}")
        cargo = col.text_input("Cargo", value=cargo_padrao, key=f"relatorio_novo_cargo_{tipo}")
        email = col.text_input("E-mail (opcional, só cadastro)", value="", key=f"relatorio_novo_email_{tipo}")
        if col.button("💾 Salvar contato", key=f"relatorio_salvar_novo_{tipo}", disabled=not nome.strip()):
            novo_id = db.salvar_contato_relatorio(
                conn, tipo=tipo, nome=nome.strip(), cargo=cargo.strip() or cargo_padrao,
                email=email.strip() or None, usuario=usuario,
            )
            if novo_id is not None:
                col.success(f"{nome} salvo — já aparece no dropdown na próxima vez.")
            else:
                col.warning(
                    "Não consegui salvar (tabela egc.contatos_relatorio ainda não existe no banco -- "
                    "rode o bloco 13 do schema.sql). O relatório usa o nome/cargo digitados aqui normalmente."
                )
        return nome, cargo or cargo_padrao

    contato = next(c for c in salvos if f"{c['nome']} — {c['cargo']}" == sel)
    with col.expander(f"✏️ Editar {contato['nome']}"):
        novo_nome = st.text_input("Nome", value=contato["nome"], key=f"relatorio_edit_nome_{tipo}_{contato['id']}")
        novo_cargo = st.text_input("Cargo", value=contato["cargo"], key=f"relatorio_edit_cargo_{tipo}_{contato['id']}")
        novo_email = st.text_input(
            "E-mail (opcional, só cadastro)", value=contato.get("email") or "",
            key=f"relatorio_edit_email_{tipo}_{contato['id']}",
        )
        if st.button("💾 Salvar edição", key=f"relatorio_salvar_edit_{tipo}_{contato['id']}"):
            # tipo=contato["tipo"] (não o `tipo` do seletor atual): como a
            # lista agora é compartilhada (FIX_20260929b), editar um
            # contato ADMINISTRADOR a partir do seletor CONTADOR não pode
            # silenciosamente trocar o tipo gravado dele.
            db.salvar_contato_relatorio(
                conn, tipo=contato["tipo"], nome=novo_nome.strip(), cargo=novo_cargo.strip(),
                email=novo_email.strip() or None, contato_id=contato["id"], usuario=usuario,
            )
            st.success("Contato atualizado.")
            st.rerun()
    return contato["nome"], contato["cargo"]


col3, col4 = st.columns(2)
nome_administrador, cargo_administrador = _seletor_contato("ADMINISTRADOR", "Administrador", col3)
email_empresa = col3.text_input("E-mail da empresa", value="", key="relatorio_email")
nome_contador, cargo_contador = _seletor_contato("CONTADOR", "Contador", col4)
site_empresa = col4.text_input("Site da empresa", value="", key="relatorio_site")

st.divider()

pode_gerar = (
    (modo == "Período único" and bool(empresas_unico_multi) and periodo_sel is not None)
    or (modo != "Período único" and 2 <= len(periodos_multi) <= 4 and bool(empresas_multi) and not bloqueio_comparativo)
)

clicou_gerar = st.button("Gerar relatório", type="primary", key="relatorio_gerar_btn", disabled=not pode_gerar)

if clicou_gerar:
    admin = dict(
        nome_administrador=nome_administrador, cargo_administrador=cargo_administrador or "Administrador",
        nome_contador=nome_contador, cargo_contador=cargo_contador or "Contador",
        email_empresa=email_empresa, site_empresa=site_empresa,
    )
    try:
        if modo == "Período único":
            empresa_arg = empresas_unico_multi if len(empresas_unico_multi) > 1 else empresas_unico_multi[0]
            sufixo_empresas_unico = "GRUPO" if len(empresas_unico_multi) > 1 else empresas_unico_multi[0]
            with st.spinner("Buscando dados e montando o relatório..."):
                dados, incluir_pagina_resultado = drc.montar_dados_relatorio(
                    conn, empresa_codigo=empresa_arg, periodo=periodo_sel,
                    periodo_label=periodo_label.strip() or periodo_sel.strftime("%m/%Y"),
                    periodo_extenso=periodo_extenso.strip(), admin=admin,
                    granularidade=granularidade_sel, variante=variante_selecionada,
                )
                # v0.40.0: o nome do arquivo reflete a base REALMENTE usada
                # pelo motor (dados["granularidade"]), nao so' o que a tela
                # tinha no widget -- e nunca omite a base quando ela existe.
                granularidade_usada = dados.get("granularidade", granularidade_sel)
                sufixo_variante = SUFIXO_ARQUIVO_VARIANTE.get(variante_selecionada, "")
                sufixo_granul = f"_{granularidade_usada}" if granularidade_usada else ""
                nome_arquivo = (
                    f"Demonstrativo_{sufixo_empresas_unico}_{periodo_sel.strftime('%Y%m%d')}{sufixo_granul}{sufixo_variante}.pdf"
                )
                caminho = f"/tmp/{nome_arquivo}"
                g.gerar_pdf_completo(dados, caminho, incluir_pagina_resultado=incluir_pagina_resultado)
            with open(caminho, "rb") as f:
                pdf_bytes = f.read()
            # FIX_20260930: contagem de páginas do layout-base (9) desconta 2
            # páginas opcionais -- Resultado (incluir_pagina_resultado) e
            # Composição das Despesas (só na variante gerencial; padrão e
            # fornecedor não têm).
            n_paginas_layout = 9 - (0 if incluir_pagina_resultado else 1) - (0 if variante_selecionada == "gerencial" else 1)
            st.success(
                f"Relatório gerado ({len(pdf_bytes) // 1024} KB, "
                f"{n_paginas_layout} páginas"
                f"{' (variante Gerencial)' if variante_selecionada == 'gerencial' else ''}"
                f"{' (variante Fornecedor)' if variante_selecionada == 'fornecedor' else ''}"
                f"{f', {len(empresas_unico_multi)} empresas consolidadas' if len(empresas_unico_multi) > 1 else ''})."
            )
            # Fase 4 (30/09/2026): deixa explicito qual documento alimentou
            # TODOS os numeros (periodo + granularidade) e o periodo anterior
            # usado nos textos de comparacao (sempre da mesma granularidade).
            _ant = dados.get("periodo_anterior")
            _ign = dados.get("periodo_anterior_ignorado")
            if _ant:
                _txt_ant = f"Comparação com o período anterior: {_ant.strftime('%m/%Y')} (mesma base, período imediatamente anterior)."
            elif _ign:
                _txt_ant = (
                    f"Sem comparação: o último documento anterior na mesma base é {_ign.strftime('%m/%Y')}, "
                    "que não é o período imediatamente anterior."
                )
            else:
                _txt_ant = "Sem período anterior nessa base para comparação."
            st.caption(
                f"Base usada: {periodo_sel.strftime('%m/%Y')} · {indicadores.rotulo_granularidade(granularidade_usada)} — "
                "todos os valores (DRE, BP, EBITDA, margens) vêm deste mesmo documento. " + _txt_ant
            )
            for _aviso in dados.get("avisos", []):
                st.warning(_aviso)
            if not incluir_pagina_resultado:
                st.caption(
                    "Página 'Formação do Resultado' não incluída — este período não tem CSLL/IRPJ "
                    "provisionado como linha própria (regime de lucro presumido)."
                )
            periodos_para_log = [periodo_sel]
            empresas_para_log = list(empresas_unico_multi)
        else:
            range_label = periodo_range_label.strip() or f"{periodos_labels_multi[0]} A {periodos_labels_multi[-1]}"
            with st.spinner("Buscando dados e montando o relatório comparativo..."):
                dados = drc.montar_dados_relatorio_comparativo(
                    conn, empresas_codigos=empresas_multi, periodos=periodos_multi,
                    periodos_labels=[lbl.strip() or p.strftime("%m/%Y") for lbl, p in zip(periodos_labels_multi, periodos_multi)],
                    periodo_range_label=range_label, admin=admin,
                    granularidades=granularidades_multi,
                )
                sufixo_empresas = "GRUPO" if len(empresas_multi) > 1 else empresas_multi[0]
                # v0.40.0: lista a base REALMENTE usada em cada coluna (vinda
                # do dict do motor, nao dos widgets) e a reflete no nome do arquivo.
                _g_usadas = dados.get("granularidades", granularidades_multi)
                _p_usados = dados.get("periodos", periodos_multi)
                st.caption(
                    "Base de cada coluna: "
                    + " · ".join(
                        f"{lbl.strip() or p.strftime('%m/%Y')} = {p.strftime('%m/%Y')} {indicadores.rotulo_granularidade(gr)}"
                        for lbl, p, gr in zip(dados["periodos_labels"], _p_usados, _g_usadas)
                    )
                )
                nome_arquivo = selecao_periodos.nome_arquivo_evolucao(sufixo_empresas, list(_p_usados), list(_g_usadas))
                caminho = f"/tmp/{nome_arquivo}"
                gc.gerar_pdf_comparativo(dados, caminho)
            with open(caminho, "rb") as f:
                pdf_bytes = f.read()
            st.success(
                f"Relatório comparativo gerado ({len(pdf_bytes) // 1024} KB, "
                f"{len(periodos_multi)} períodos, {len(empresas_multi)} empresa(s))."
            )
            periodos_para_log = periodos_multi
            empresas_para_log = empresas_multi
        st.download_button(
            "⬇️ Baixar PDF", data=pdf_bytes, file_name=nome_arquivo, mime="application/pdf",
            key="relatorio_download_btn",
        )
        try:
            db.registrar_relatorio_gerado(
                conn, empresas_codigos=empresas_para_log, periodos=periodos_para_log,
                arquivo=nome_arquivo, usuario=usuario,
            )
        except Exception:
            pass  # log e' melhor-esforco -- o PDF ja foi gerado e entregue, nao pode quebrar por causa disso
    except Exception as exc:
        st.error(f"Não foi possível gerar o relatório: {exc}")
        # FIX_20260929l (Rafael, 29/09: consolidado multi-CNPJ + 1 período
        # travando com "unhashable type: 'list'" em produção -- 3700+
        # cenários sintéticos/AppTest não reproduziram, precisa do
        # traceback real). Diagnóstico temporário: mostra o traceback
        # completo (arquivo/linha) além da mensagem curta -- reverter
        # depois que a causa real for identificada e corrigida.
        import traceback
        with st.expander("🔍 Detalhe técnico do erro (diagnóstico temporário)"):
            st.code(traceback.format_exc())

st.divider()
with st.expander("📜 Últimas gerações"):
    try:
        historico = db.listar_relatorios_gerados(conn, limite=20)
    except Exception as exc:
        st.caption(f"Não foi possível carregar o histórico: {exc}")
        historico = []
    if not historico:
        st.caption("Nenhum relatório gerado ainda.")
    else:
        for h in historico:
            empresas_txt = ", ".join(NOME_POR_COD.get(c, c) for c in (h["empresas"] or []))
            periodos_txt = ", ".join(p.strftime("%m/%Y") for p in (h["periodos"] or []))
            quando = formatacao.hora_br(h["gerado_em"])
            st.caption(f"**{h['arquivo']}** — {empresas_txt} · {periodos_txt} · {quando} · {h['usuario'] or '—'}")
