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
"""
import sys
from pathlib import Path
from datetime import date, datetime

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


def _periodos_intersecao_detalhado(cods: list) -> list:
    """
    Interseção dos (período, granularidade) ATIVOS das empresas
    escolhidas -- mesma lógica já usada e testada no modo Comparativo
    (FIX_20260929k), agora sobre PARES (período, granularidade) em vez
    de só a data (Fase 3, 29/09/2026: "vamos estruturar e implantar a
    granularidade... já que o gerador tb faz essa geração com
    multi-select"). Com 1 empresa só, é simplesmente os períodos dela.
    Retorna list[tuple[date, str]], mais recente primeiro.
    """
    if not cods:
        return []
    por_empresa = [
        {(d["periodo"], d["granularidade"]) for d in db.listar_periodos_detalhado(conn, cod, status="ATIVO")}
        for cod in cods
    ]
    # Ordena CRESCENTE (período mais antigo primeiro) -- mesmo comportamento
    # de sempre da antiga _periodos_intersecao (a seleção default do
    # selectbox de "Período único" é o índice 0 -- ver
    # test_relatorio_comentado_app.py::test_trocar_periodo_no_unico...).
    return sorted(set.intersection(*por_empresa), key=lambda pg: (pg[0], pg[1]))


def _rotulo_periodo_granularidade(periodo: date, granularidade: str, ambiguo: bool) -> str:
    """
    Rótulo do seletor de período -- só mostra a granularidade quando ela
    existe E quando esse período_fim tem mais de 1 granularidade ativa
    ao mesmo tempo (`ambiguo`) -- caso comum (1 granularidade só) fica
    igual a sempre, sem ruído visual pra contadora.
    """
    base = periodo.strftime("%d/%m/%Y")
    if ambiguo and granularidade:
        return f"{base} — {_NOME_GRANULARIDADE.get(granularidade, granularidade)}"
    return base


nomes_emp = [f"{nome} ({cod})" for cod, nome, _cnpj in EMPRESAS_FIXAS]
idx_emp = st.selectbox(
    "Empresa", range(len(EMPRESAS_FIXAS)), format_func=lambda i: nomes_emp[i], key="relatorio_empresa_sel",
)
cod_empresa, nome_empresa, _cnpj = EMPRESAS_FIXAS[idx_emp]

try:
    periodos_ativos = db.listar_periodos(conn, cod_empresa, status="ATIVO")
except Exception as exc:
    st.error(f"Não foi possível consultar os períodos: {exc}")
    st.stop()

if not periodos_ativos:
    st.info(f"{nome_empresa} ainda não tem nenhum período importado (ou todos estão arquivados).")
    st.stop()

st.divider()
# FIX_20260929h (Rafael: "por mim podemos implantar o multi-periodos ja
# tb"): liga o Modelo B (gerador_relatorio_comparativo.py + a nova
# montar_dados_relatorio_comparativo, prontos desde 26-29/09 mas sem UI)
# na tela. Modo "Período único" é o fluxo de sempre, inalterado.
# FIX_20260930b (Rafael: "trocar os 2 botoes por UM seletor de tipo de
# relatorio + um unico botao Gerar"): um radio horizontal escolhe o tipo;
# os campos abaixo se adaptam (periodos multiplos so' no Comparativo). O
# tipo "Fornecedor" esta' reservado (NAO construido): aparece rotulado
# "em breve" e desabilita o botao.
TIPO_PADRAO = "Demonstrativo Comentado"
TIPO_GERENCIAL = "Demonstrativo Comentado Gerencial"
TIPO_FORNECEDOR = "Demonstrativo Comentado Fornecedor (em breve)"
TIPO_COMPARATIVO = "Comparativo / Evolução"
tipo_relatorio = st.radio(
    "Tipo de relatório", [TIPO_PADRAO, TIPO_GERENCIAL, TIPO_FORNECEDOR, TIPO_COMPARATIVO],
    key="relatorio_tipo", horizontal=True,
)
# "modo" (Periodo unico x Comparativo) continua sendo o eixo dos campos
# abaixo; "variante" (padrao/gerencial) so' vale no Periodo unico.
modo = "Comparativo" if tipo_relatorio == TIPO_COMPARATIVO else "Período único"
variante_selecionada = "gerencial" if tipo_relatorio == TIPO_GERENCIAL else "padrao"
tipo_em_breve = tipo_relatorio == TIPO_FORNECEDOR
if tipo_em_breve:
    st.info("O Demonstrativo Comentado Fornecedor ainda não foi construído — disponível em breve.")

periodo_sel = None
periodo_label = ""
periodo_extenso = ""
granularidade_sel = ""  # Fase 3 (29/09/2026) — granularidade do período único escolhido
periodos_multi: list = []
periodos_labels_multi: list = []
granularidades_multi: list = []  # Fase 3 — 1 granularidade por período do Comparativo
periodo_range_label = ""
empresas_multi: list = []

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
                "em datas diferentes) — reduza a seleção de empresas ou confirme os períodos importados."
            )

    if periodos_disponiveis_unico:
        # Fase 3 (29/09/2026): quando o MESMO periodo_fim tem 2 documentos
        # de abrangência diferente ativos (ex. trimestral e semestral
        # fechando na mesma data -- ver db.inativar_periodo_existente), os
        # 2 aparecem aqui como opções DISTINTAS, com a granularidade no
        # rótulo -- sem isso, a contadora não teria como escolher qual
        # dos dois quer no relatório.
        datas_repetidas = {d for d, _g in periodos_disponiveis_unico
                            if sum(1 for d2, _g2 in periodos_disponiveis_unico if d2 == d) > 1}
        periodo_granul_sel = st.selectbox(
            "Período (data de posição do BP)", periodos_disponiveis_unico,
            format_func=lambda pg: _rotulo_periodo_granularidade(pg[0], pg[1], pg[0] in datas_repetidas),
            key="relatorio_periodo_sel",
        )
        periodo_sel, granularidade_sel = periodo_granul_sel

        st.subheader("Período — como aparece no relatório")
        dica_granul = (
            f" O PDF importado declara este período como **{_NOME_GRANULARIDADE.get(granularidade_sel, granularidade_sel)}**"
            f" — reflita isso no rótulo se fizer sentido (ex.: \"2º Trimestre 2026\")."
            if granularidade_sel else ""
        )
        st.caption(f"Confirme o texto que vai aparecer na capa e no cabeçalho do relatório.{dica_granul}")
        # FIX_20260929m (achado revendo o mesmo gotcha ja corrigido no modo
        # Comparativo -- FIX_20260929j -- que nunca tinha sido aplicado aqui:
        # key fixa ("relatorio_periodo_label") faz o Streamlit reusar o
        # texto digitado mesmo depois de trocar o periodo selecionado,
        # porque value= so' e' aplicado na 1a vez que a key existe em
        # session_state. Rotulo amarrado ao periodo escolhido (mesmo
        # principio da combo_key do Comparativo) -- trocar o periodo forca
        # uma key nova e o value= recem-calculado volta a valer. Key agora
        # inclui a granularidade tb (Fase 3) -- as 2 opções do mesmo
        # periodo_fim não podem compartilhar rótulo default.
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
                "em datas diferentes) — reduza a seleção de empresas ou confirme os períodos importados."
            )

    st.subheader("Períodos a comparar")
    st.caption(
        "Escolha de 2 a 4 períodos (ordem cronológica é ajustada automaticamente, não importa a "
        "ordem do clique) e confirme o rótulo de cada coluna — mesmo princípio do período único: "
        "o sistema não infere o texto sozinho. Só aparecem aqui os períodos que TODAS as empresas "
        "escolhidas acima têm ativos. Quando 2 documentos de abrangência diferente (ex. trimestral e "
        "semestral) fecham na mesma data, os 2 aparecem como opções separadas."
    )
    datas_repetidas_multi = {d for d, _g in periodos_ativos_multi
                              if sum(1 for d2, _g2 in periodos_ativos_multi if d2 == d) > 1}
    periodos_sel_raw = st.multiselect(
        "Períodos (data de posição do BP)", periodos_ativos_multi,
        format_func=lambda pg: _rotulo_periodo_granularidade(pg[0], pg[1], pg[0] in datas_repetidas_multi),
        key="relatorio_periodos_multi",
    )
    periodos_pg_multi = sorted(periodos_sel_raw, key=lambda pg: pg[0])
    periodos_multi = [pg[0] for pg in periodos_pg_multi]
    granularidades_multi = [pg[1] for pg in periodos_pg_multi]

    if periodos_multi and not (2 <= len(periodos_multi) <= 4):
        st.warning("Escolha de 2 a 4 períodos pra gerar o comparativo (motor de desenho aceita esse intervalo).")

    if 2 <= len(periodos_multi) <= 4:
        # FIX_20260929j: key amarrada ao conjunto EXATO de períodos
        # selecionados, não ao índice posicional -- ver docstring do
        # módulo pro diagnóstico completo do bug (rótulo repetido no 1º
        # PDF real do Rafael). Inclui granularidade (Fase 3) -- 2 opções
        # do mesmo periodo_fim não podem compartilhar key/rótulo default.
        combo_key = "_".join(f"{p.isoformat()}-{g}" for p, g in periodos_pg_multi)
        cols_label = st.columns(len(periodos_multi))
        for i, (col, periodo) in enumerate(zip(cols_label, periodos_multi)):
            rotulo = col.text_input(
                periodo.strftime("%d/%m/%Y"), value=periodo.strftime("%m/%Y"),
                key=f"relatorio_periodo_multi_label_{combo_key}_{i}",
            )
            periodos_labels_multi.append(rotulo)
        periodo_range_label = st.text_input(
            "Rótulo do intervalo (capa)",
            value=f"{periodos_labels_multi[0]} A {periodos_labels_multi[-1]}",
            key=f"relatorio_periodo_range_label_{combo_key}",
        )

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
    or (modo != "Período único" and 2 <= len(periodos_multi) <= 4 and bool(empresas_multi))
)

pode_gerar = pode_gerar and not tipo_em_breve
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
                sufixo_variante = "" if variante_selecionada == "padrao" else "_GERENCIAL"
                sufixo_granul = f"_{granularidade_sel}" if granularidade_sel else ""
                nome_arquivo = (
                    f"Demonstrativo_{sufixo_empresas_unico}_{periodo_sel.strftime('%Y%m%d')}{sufixo_granul}{sufixo_variante}.pdf"
                )
                caminho = f"/tmp/{nome_arquivo}"
                g.gerar_pdf_completo(dados, caminho, incluir_pagina_resultado=incluir_pagina_resultado)
            with open(caminho, "rb") as f:
                pdf_bytes = f.read()
            # FIX_20260930: contagem de páginas do layout-base (9) agora
            # desconta 2 páginas opcionais, não só 1 -- Resultado
            # (incluir_pagina_resultado) E Composição das Despesas
            # (variante == "padrao"). Mensagem tinha "9"/"8" fixos.
            n_paginas_layout = 9 - (0 if incluir_pagina_resultado else 1) - (1 if variante_selecionada == "padrao" else 0)
            st.success(
                f"Relatório gerado ({len(pdf_bytes) // 1024} KB, "
                f"{n_paginas_layout} páginas"
                f"{' (variante Gerencial)' if variante_selecionada == 'gerencial' else ''}"
                f"{f', {len(empresas_unico_multi)} empresas consolidadas' if len(empresas_unico_multi) > 1 else ''})."
            )
            # Fase 4 (30/09/2026): deixa explicito qual documento alimentou
            # TODOS os numeros (periodo + granularidade) e o periodo anterior
            # usado nos textos de comparacao (sempre da mesma granularidade).
            _ant = dados.get("periodo_anterior")
            st.caption(
                f"Base usada: {periodo_sel.strftime('%m/%Y')} · {indicadores.rotulo_granularidade(granularidade_sel)} — "
                "todos os valores (DRE, BP, EBITDA, margens) vêm deste mesmo documento. "
                + (f"Comparação com o período anterior: {_ant.strftime('%m/%Y')} (mesma base)." if _ant
                   else "Sem período anterior nessa base para comparação.")
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
                st.caption(
                    "Base de cada coluna: "
                    + " · ".join(
                        f"{lbl.strip() or p.strftime('%m/%Y')} = {p.strftime('%m/%Y')} {indicadores.rotulo_granularidade(gr)}"
                        for lbl, p, gr in zip(periodos_labels_multi, periodos_multi, granularidades_multi)
                    )
                )
                nome_arquivo = (
                    f"Evolucao_{sufixo_empresas}_{periodos_multi[0].strftime('%Y%m')}"
                    f"_{periodos_multi[-1].strftime('%Y%m')}.pdf"
                )
                caminho = f"/tmp/{nome_arquivo}"
                gc.gerar_pdf_comparativo(dados, caminho)
            with open(caminho, "rb") as f:
                pdf_bytes = f.read()
            st.success(
                f"Relatório comparativo gerado ({len(pdf_bytes) // 1024} KB, 5 páginas, "
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
