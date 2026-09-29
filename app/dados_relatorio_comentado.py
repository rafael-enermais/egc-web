# -*- coding: utf-8 -*-
"""
Fase 2 (28/09/2026) -- camada que MONTA o dict de ~40 campos que
`gerador_relatorio_comentado.gerar_pdf_completo()` espera, buscando dado
REAL do banco em vez da fixture de teste (BASE em
tests/test_gerador_completo.py, que continua existindo so' pros testes
do gerador).

Modulo puro na leitura (recebe `conn` ja aberto, mesmo padrao de
db.py/indicadores.py) -- nao decide UI, nao sabe de Streamlit. Quem
chama (a tela ainda a construir) passa periodo_label/periodo_extenso e
os dados de admin/contador/email/site "na mao" (decisao do Rafael,
24/09: inputs editaveis na tela em vez de tabela fixa por empresa --
ver 00-handoff.md) e le' o retorno pra chamar gerar_pdf_completo().

REGRA DE OURO (nao alterar sem novo pedido explicito): NUNCA inventa
numero. Uma conta ausente no periodo vira None/0.0 explicito (ver campo
a campo abaixo qual e' qual), nunca um valor fabricado -- mesma logica
que ja existe em parser_egc.py ("zero por ausencia e' informacao real,
nao inventar").

CONVENCAO DE SINAL (conferida em parser_egc.py: parenteses no PDF => o
valor extraido fica NEGATIVO, sinal preservado):
  - Contas de DESPESA/CUSTO/DEDUCAO no DRE (ADMINISTRATIVAS, DESPESAS
    FINANCEIRAS, DESPESAS TRIBUTARIAS, CUSTO DOS PRODUTOS/SERVICOS,
    DEDUCOES DA RECEITA BRUTA, DEPRECIACOES, AMORTIZACOES, PROVISAO
    CSLL, PROVISAO IRPJ) chegam NEGATIVAS do banco.
  - Contas de RECEITA (RECEITA OPERACIONAL BRUTA, RECEITAS OPERACIONAIS
    DIVERSAS, RECEITAS FINANCEIRAS) chegam POSITIVAS.
  - Resultados (LUCRO BRUTO, LUCRO OPERACIONAL LIQUIDO, LUCRO LIQUIDO DO
    EXERCICIO) chegam com o sinal real (negativo = prejuizo).
  - O gerador (`gerador_relatorio_comentado.py`) espera os campos de
    despesa/custo como MAGNITUDE POSITIVA nos cards de composicao (ex.:
    despesas_administrativas, despesas_tributarias) e como MAGNITUDE
    POSITIVA A SOMAR DE VOLTA nas 2 barras do EBITDA (resultado_
    financeiro, deprec_amortiz, csll_irpj) -- ver FIX_20260928 no
    proprio gerador e o fixture BASE em test_gerador_completo.py. Por
    isso este modulo inverte o sinal (nega) dessas contas ao montar o
    dict -- e' conversao de convencao de armazenamento pra convencao de
    apresentacao, nao um numero novo.

EBITDA/Margem EBITDA/Margem Bruta/Margem Liquida/Liquidez Corrente/
Alavancagem/Endividamento Geral NAO sao recalculados aqui -- vem direto
de `indicadores.calcular_indicadores()` (mesma formula ja validada e
testada, incluindo o fix de EBITDA de 28/09/2026). Reimplementar essas
formulas de novo aqui seria repetir o mesmo tipo de erro que acabou de
ser corrigido (2 modulos com a mesma conta calculada de 2 jeitos
diferentes).
"""
from __future__ import annotations

import warnings
from collections import Counter
from datetime import date
from typing import Optional

import pandas as pd

import db
import indicadores
import visao_grupo

# Contas cujo nome_saida (BP) e' o TOTALIZADOR do proprio grupo -- ver
# parser_egc.BP_TARGETS. Usado pra separar "item de linha" de "subtotal
# do grupo" ao montar o anexo.
_BP_TOTALIZADORES = {
    "TOTAL CIRCULANTE ATIVO",
    "TOTAL NAO CIRCULANTE ATIVO",
    "TOTAL DO ATIVO",
    "TOTAL CIRCULANTE PASSIVO",
    "TOTAL NAO CIRCULANTE PASSIVO",
    "TOTAL DO PASSIVO",
    "TOTAL PATRIMONIO LIQUIDO",
}

_GRUPOS_ATIVO = [
    ("ATIVO CIRCULANTE", "Ativo Circulante", "TOTAL CIRCULANTE ATIVO"),
    ("ATIVO NAO CIRCULANTE", "Ativo Não Circulante", "TOTAL NAO CIRCULANTE ATIVO"),
]
_GRUPOS_PASSIVO = [
    ("PASSIVO CIRCULANTE", "Passivo Circulante", "TOTAL CIRCULANTE PASSIVO"),
    ("PASSIVO NAO CIRCULANTE", "Passivo Não Circulante", "TOTAL NAO CIRCULANTE PASSIVO"),
    ("PATRIMONIO LIQUIDO", "Patrimônio Líquido", "TOTAL PATRIMONIO LIQUIDO"),
]

_PALAVRAS_MINUSCULAS = {"a", "as", "o", "os", "de", "da", "do", "das", "dos", "e", "em"}

# FIX_20260928 (Rafael, comparando com o modelo de referencia que a
# contadora ja aprovou): o banco guarda conta sem acento (parser_egc.py
# nao acentua), e o Title Case puro ("Depositos Bancarios a Vista") ficava
# claramente diferente do modelo. Dicionario com a grafia correta de toda
# conta que aparece em parser_egc.BP_TARGETS -- fonte unica, nunca inventa
# nome novo (so' repoe o acento do MESMO nome). Uma conta que aparecer no
# banco mas nao estiver aqui (nunca deveria acontecer, ja que so' vem do
# proprio BP_TARGETS) cai no fallback _label_conta (Title Case sem acento)
# em vez de quebrar.
_LABEL_ACENTUADO = {
    "DISPONIVEL": "Disponível",
    "DEPOSITOS BANCARIOS A VISTA": "Depósitos Bancários à Vista",
    "APLICACOES DE LIQUIDEZ IMEDIATA": "Aplicações de Liquidez Imediata",
    "CLIENTES": "Clientes",
    "DUPLICATAS A RECEBER": "Duplicatas a Receber",
    "OUTROS CREDITOS": "Outros Créditos",
    "MUTUO ENTRE EMPRESAS": "Mútuo entre Empresas",
    "TITULOS A RECEBER": "Títulos a Receber",
    "TRIBUTOS A RECUPERAR": "Tributos a Recuperar",
    "ADIANTAMENTOS A TERCEIROS": "Adiantamentos a Terceiros",
    "DESPESAS PAGAS ANTECIPADAMENTE": "Despesas Pagas Antecipadamente",
    "INVESTIMENTOS": "Investimentos",
    "IMOBILIZADO": "Imobilizado",
    "IMOVEIS": "Imóveis",
    "APLICACOES FINANCEIRAS": "Aplicações Financeiras",
    "BENS EM OPERACAO": "Bens em Operação",
    "DEPRECIACAO ACUMULADA": "Depreciação Acumulada",
    "INSTITUICOES FINANCEIRAS": "Instituições Financeiras",
    "EMPRESTIMOS": "Empréstimos",
    "FINANCIAMENTOS": "Financiamentos",
    "FORNECEDORES": "Fornecedores",
    "OBRIGACOES TRIBUTARIAS": "Obrigações Tributárias",
    "IMPOSTOS E CONTRIBUICOES A RECOLHER": "Impostos e Contribuições a Recolher",
    "TRIBUTOS RETIDOS A RECOLHER": "Tributos Retidos a Recolher",
    "OBRIGACOES TRABALHISTAS": "Obrigações Trabalhistas",
    "OBRIGACOES COM O PESSOAL": "Obrigações com o Pessoal",
    "OBRIGACOES PREVIDENCIARIAS": "Obrigações Previdenciárias",
    "CONTAS A PAGAR": "Contas a Pagar",
    "OUTRAS OBRIGACOES": "Outras Obrigações",
    "ADIANTAMENTOS DE CLIENTES": "Adiantamentos de Clientes",
    "OBRIGACOES A LONGO PRAZO": "Obrigações a Longo Prazo",
    "RECEITAS DIFERIDAS": "Receitas Diferidas",
    "CAPITAL SOCIAL": "Capital Social",
    "CAPITAL SUBSCRITO": "Capital Subscrito",
    "CAPITAL A INTEGRALIZAR": "Capital a Integralizar",
    "LUCROS/PREJUIZOS ACUMULADOS": "Lucros/Prejuízos Acumulados",
}

# Pares pai/filho verificados ARITMETICAMENTE contra dado real importado
# (28/09/2026, comparando um relatorio gerado com o modelo de referencia):
# o valor do pai bate exatamente com a soma dos filhos sempre que os 2
# existem no mesmo periodo (ex.: Disponivel = Depositos Bancarios a Vista
# + Aplicacoes de Liquidez Imediata). So' os pares confirmados entram
# aqui -- contas sem par pai/filho confirmado ficam como linha solta
# ("conta" simples), nunca um agrupamento inventado. Ampliar esta lista
# exige conferir a soma de novo, nao adivinhar pelo nome.
#
# FIX_20260928b (Rafael autorizou ampliar o parser pra paridade total com
# o modelo -- "a regra absoluta e' fazer o sistema correto"): 4 pares
# novos, cada um conferido byte a byte contra o PDF SPED REAL da
# Enermais Energia, 31/12/2025 (nao contra a planilha ilustrativa/mockup
# de referencia, que tem contas que NUNCA aparecem em nenhum dado real):
#   - OUTROS CREDITOS (5.163,00 no caso da Enermais Solucoes) =
#     ADIANTAMENTOS A TERCEIROS + TRIBUTOS A RECUPERAR nesse caso; no
#     caso da Energia soma tambem MUTUO ENTRE EMPRESAS e TITULOS A
#     RECEBER quando presentes -- os 4 filhos possiveis sao mutuamente
#     exclusivos por empresa/periodo, nunca todos ao mesmo tempo, mas
#     a soma dos que existem sempre bate com o pai.
#   - OBRIGACOES TRIBUTARIAS (1.346.005,46 na Energia) = IMPOSTOS E
#     CONTRIBUICOES A RECOLHER (1.291.057,67) + TRIBUTOS RETIDOS A
#     RECOLHER (54.947,79).
#   - OBRIGACOES TRABALHISTAS (1.799.133,16 na Energia, chamada no PDF
#     de "Obrigacoes Trabalhistas e Prividenciarias" -- typo do proprio
#     SPED, ja coberto pelo alias em parser_egc.BP_TARGETS) =
#     OBRIGACOES COM O PESSOAL (551.614,37) + OBRIGACOES PREVIDENCIARIAS
#     (1.247.518,79).
#   - OUTRAS OBRIGACOES (8.568.950,17 na Energia) = ADIANTAMENTOS DE
#     CLIENTES (6.724.607,29) + CONTAS A PAGAR (1.844.342,88).
#
# Testado e DELIBERADAMENTE NAO incluido: "Adiantamentos a Funcionarios"
# e "Mutuos a Pagar" (filhos de Outras Obrigacoes no modelo ilustrativo)
# -- busca exaustiva em todos os PDFs SPED reais das 6 empresas e em
# todo enermais_bp.tsv historico do cofre nao encontrou nenhuma ocorrencia
# de nenhum dos 2 em dado real. Nao viram par (nem existem em
# parser_egc.BP_TARGETS como alvo de captura) ate' aparecerem de fato em
# algum periodo real -- inventar a linha pra bater com o mockup seria
# quebrar a REGRA DE OURO (nunca inventar numero).
_HIERARQUIA_BP = {
    "DISPONIVEL": ["DEPOSITOS BANCARIOS A VISTA", "APLICACOES DE LIQUIDEZ IMEDIATA"],
    "CLIENTES": ["DUPLICATAS A RECEBER"],
    "INSTITUICOES FINANCEIRAS": ["EMPRESTIMOS", "FINANCIAMENTOS"],
    "OUTROS CREDITOS": [
        "MUTUO ENTRE EMPRESAS", "TITULOS A RECEBER",
        "TRIBUTOS A RECUPERAR", "ADIANTAMENTOS A TERCEIROS",
    ],
    "OBRIGACOES TRIBUTARIAS": ["IMPOSTOS E CONTRIBUICOES A RECOLHER", "TRIBUTOS RETIDOS A RECOLHER"],
    "OBRIGACOES TRABALHISTAS": ["OBRIGACOES COM O PESSOAL", "OBRIGACOES PREVIDENCIARIAS"],
    "OUTRAS OBRIGACOES": ["ADIANTAMENTOS DE CLIENTES", "CONTAS A PAGAR"],
}


def _label_conta(nome_bd: str) -> str:
    """Grafia correta (com acento) se a conta estiver em _LABEL_ACENTUADO;
    senao cai no Title Case simples (fallback, nunca quebra por uma conta
    nao mapeada)."""
    if nome_bd in _LABEL_ACENTUADO:
        return _LABEL_ACENTUADO[nome_bd]
    partes = nome_bd.split(" ")
    saida = []
    for i, p in enumerate(partes):
        pl = p.lower()
        if i > 0 and pl in _PALAVRAS_MINUSCULAS:
            saida.append(pl)
        else:
            saida.append(pl.capitalize())
    return " ".join(saida)


def _mapa(lancamentos: list) -> dict:
    """conta -> valor (float). So' seguro pra contas com nome unico
    (todo DRE_TARGETS e' unico; no BP, os totalizadores tambem sao --
    quem colide de nome no BP sao itens de linha tipo INSTITUICOES
    FINANCEIRAS Circulante x Nao Circulante, que este mapa achatado NAO
    deve ser usado pra buscar -- ver _montar_anexo, que filtra por
    grupo).

    FIX_20260928d (Rafael, reparse de Energia 06/2026 dando EBITDA
    diferente entre gerações do mesmo relatório -- suspeita dele: "dado
    antigo (parser anterior) x novo"): egc.lancamentos NÃO TEM unique
    constraint em (empresa_codigo, tipo, periodo, grupo, conta, status) --
    nada no schema IMPEDE 2 linhas ATIVAS pra mesma conta/período (ex.:
    se `inativar_periodo_existente` não casar o período exato de uma
    reimportação anterior). Se isso acontecer, este dict comprehension
    escolhe silenciosamente a ÚLTIMA linha da lista (ordem do ORDER BY
    grupo,conta do banco, que não é garantida estável em empate) -- o
    relatório sairia com um valor ou outro sem erro nenhum, dependendo só
    da ordem física das linhas. Em vez de deixar isso silencioso, avisa
    (não quebra o relatório -- dado ruim não pode travar produção, mas
    precisa aparecer) sempre que houver conta duplicada nas linhas ATIVAS
    recebidas."""
    contagem = Counter(l["conta"] for l in lancamentos)
    duplicadas = [conta for conta, n in contagem.items() if n > 1]
    if duplicadas:
        warnings.warn(
            f"_mapa: conta(s) duplicada(s) entre os lançamentos ATIVOS recebidos: {duplicadas} -- "
            "provável linha antiga não inativada numa reimportação (ver FIX_20260928d); "
            "o valor usado é o da última linha na ordem retornada pelo banco, não determinístico.",
            stacklevel=2,
        )
    return {l["conta"]: float(l["valor"]) for l in lancamentos}


def _montar_anexo(bp_periodo: list, lado: str) -> list:
    """Monta anexo_ativo ou anexo_passivo no formato tupla-arvore que
    gerador_relatorio_comentado espera: ('grupo', label) |
    ('conta'|'subconta'|'subtotal'|'total', label, valor).

    FIX_20260928 (Rafael, comparando linha a linha com o modelo de
    referencia que a contadora ja aprovou): antes emitia 1 header
    "grupo" umbrella (\"ATIVO\"/\"PASSIVO + PL\") no topo e contas
    achatadas por baixo. O modelo usa 1 header \"grupo\" PRA CADA
    subgrupo (ex.: \"Ativo Circulante\" e soh' depois \"Ativo Nao
    Circulante\", como 2 blocos separados na mesma coluna) e aninha
    pai/filho pras contas em _HIERARQUIA_BP (ex.: Disponivel com
    Depositos Bancarios a Vista/Aplicacoes de Liquidez Imediata
    indentados por baixo, tipo 'subconta'). Continua sem inventar
    nada: subgrupo sem lancamento no periodo soh' nao aparece, conta
    sem par confirmado em _HIERARQUIA_BP fica solta ('conta' simples)."""
    if lado == "ATIVO":
        grupos, grand_nome, grand_label = _GRUPOS_ATIVO, "TOTAL DO ATIVO", "TOTAL DO ATIVO"
    else:
        grupos, grand_nome, grand_label = (
            _GRUPOS_PASSIVO, "TOTAL DO PASSIVO", "TOTAL PASSIVO + PL",
        )

    linhas = []
    for grupo_bd, grupo_label, totalizador_nome in grupos:
        rows = [r for r in bp_periodo if r["grupo"] == grupo_bd]
        if not rows:
            continue
        itens = sorted((r for r in rows if r["conta"] != totalizador_nome), key=lambda r: r["conta"])
        mapa_itens = {r["conta"]: r for r in itens}
        filhos_usados = set()
        for filhos in _HIERARQUIA_BP.values():
            for f in filhos:
                if f in mapa_itens:
                    filhos_usados.add(f)

        linhas.append(("grupo", grupo_label))
        for r in itens:
            nome = r["conta"]
            if nome in filhos_usados:
                continue  # ja' sai aninhada embaixo do pai, nao solta de novo
            linhas.append(("conta", _label_conta(nome), float(r["valor"])))
            for filho_nome in _HIERARQUIA_BP.get(nome, []):
                filho_row = mapa_itens.get(filho_nome)
                if filho_row is not None:
                    linhas.append(("subconta", _label_conta(filho_nome), float(filho_row["valor"])))
        total_row = next((r for r in rows if r["conta"] == totalizador_nome), None)
        if total_row is not None:
            linhas.append(("subtotal", f"Total {grupo_label}", float(total_row["valor"])))
    total_geral = next((r for r in bp_periodo if r["grupo"] == "TOTAL" and r["conta"] == grand_nome), None)
    if total_geral is not None:
        linhas.append(("total", grand_label, float(total_geral["valor"])))
    return linhas


def _montar_despesas_admin_itens(itens_admin: list, despesas_administrativas: float, top_n: int = 6) -> list:
    """[(nome, valor_positivo, pct_0_a_100), ...] ordenado desc pelo
    valor -- formato que gerador_relatorio_comentado espera pro grafico
    de ranking (pagina Despesas). Vem de db.listar_despesas_admin_itens
    (Fase 2/bloco 12) -- lista vazia se a migracao ainda nao rodou no
    banco ou o periodo nao tiver essa extracao (period antigo, antes do
    parser existir) -- pagina ja tem teste cobrindo lista vazia.

    FIX_20260929c (Rafael, pag.4 "estourando os gráficos pra baixo,
    temos que limitar"): grafico_ranking_horizontal desenha 1 barra por
    item SEM TETO -- empresa com dezenas de contas administrativas (ex.:
    Energia, 43 contas) empurrava o resto da pagina (callout, cards,
    total, texto de leitura) pra baixo da margem inferior. Corta em
    `top_n` itens individuais + 1 linha agregada "Demais contas (N)"
    com a SOMA exata do resto -- nada desaparece do total, só deixa de
    ser detalhado item a item. Mesmo padrão já usado no protótipo
    anterior (referência real tinha "Demais contas (43)" nessa página).
    Só agrega quando sobra mais de 1 item de cauda -- com exatamente
    top_n+1 contas não vale a pena resumir 1 item só."""
    itens = [(nome, abs(float(valor))) for nome, valor in itens_admin]
    itens.sort(key=lambda t: t[1], reverse=True)

    if len(itens) > top_n + 1:
        topo, resto = itens[:top_n], itens[top_n:]
        itens = topo + [(f"Demais contas ({len(resto)})", sum(v for _, v in resto))]

    out = []
    for nome, valor_abs in itens:
        pct = (valor_abs / despesas_administrativas * 100) if despesas_administrativas else 0.0
        out.append((nome, valor_abs, pct))
    return out


def _linha_indicador(indic_df: pd.DataFrame, periodo: date, coluna: str) -> Optional[float]:
    """Le 1 celula de indic_df (saida de indicadores.calcular_indicadores)
    pro periodo pedido. None se o periodo nao esta no indice (sem BP+DRE
    o bastante pro indicador) ou o valor e' NaN (conta faltando no
    periodo) -- nunca inventa 0."""
    ts = pd.Timestamp(periodo)
    if ts not in indic_df.index:
        return None
    val = indic_df.loc[ts, coluna]
    return None if pd.isna(val) else float(val)


def _texto_comparativo(rotulo: str, atual: Optional[float], anterior: Optional[float], unidade: str = "R$") -> str:
    """Texto neutro de comparacao com o periodo anterior (sem adjetivo
    de valor -- 'cresceu'/'caiu', nunca 'saudavel'/'preocupante', mesmo
    padrao neutro ja exigido pelas funcoes _leitura_* do gerador, ver
    test_gerador_completo.PALAVRAS_PROIBIDAS). Sem periodo anterior (1a
    importacao da empresa) ou dado faltando, cai pro fallback explicito
    -- nao inventa comparacao."""
    if atual is None or anterior is None:
        return f"{rotulo}: sem período anterior disponível para comparação."
    delta = atual - anterior
    if anterior != 0:
        pct = delta / abs(anterior) * 100
        sinal = "+" if delta >= 0 else ""
        if unidade == "R$":
            return f"{rotulo}: {sinal}{pct:.1f}% em relação ao período anterior."
        return f"{rotulo}: {sinal}{delta*100:.1f} p.p. em relação ao período anterior."
    return f"{rotulo}: período anterior com base zero, variação percentual não aplicável."


def _consolidar_periodo(conn, empresas_codigos: list, periodo: date, tipo: str) -> list:
    """BP ou DRE consolidado (soma aditiva, sem eliminacao entre
    empresas -- mesma premissa da Visao Grupo, confirmada com o Rafael)
    de N empresas pra 1 UNICO periodo. Formato {grupo, conta, valor},
    compativel com _mapa()/_montar_anexo() (mesmas chaves que
    db.listar_lancamentos devolve, so' sem id/origem/pdf_original/
    atualizado_em, que ninguem le aqui). Reaproveita a mesma infra ja
    testada do Modelo B/Visao Grupo (db.listar_lancamentos_grupo +
    visao_grupo.montar_pivot_grupo, coluna "VALOR CONSOLIDADO") -- soma
    nao reimplementada de novo."""
    lancs = db.listar_lancamentos_grupo(conn, periodo, tipo, empresas_codigos)
    pivot = visao_grupo.montar_pivot_grupo(lancs, empresas_codigos)
    return [
        {"grupo": row["grupo"], "conta": row["conta"], "valor": row["VALOR CONSOLIDADO"]}
        for row in pivot.to_dict("records")
    ]


def _consolidar_historico(conn, empresas_codigos: list, tipo: str) -> list:
    """Historico consolidado (uniao dos periodos ATIVOS de QUALQUER
    empresa do grupo, nao intersecao) no mesmo formato de
    db.listar_historico_grupo (1 empresa): {periodo, grupo, conta,
    valor}, somado entre as empresas por (periodo, grupo, conta).
    Alimenta indicadores.calcular_indicadores sem tocar no motor de
    formulas (mesma regra de "nao reimplementar" do resto do modulo).

    Periodo que falta numa empresa do grupo entra como 0.0 dela pra
    fins de TENDENCIA (mesma premissa ja aceita e testada em
    visao_grupo.montar_serie_kpis_grupo pro grafico de evolucao do
    grupo) -- diferente da checagem ESTRITA feita em
    montar_dados_relatorio pro periodo PEDIDO (o numero final do
    relatorio nunca soma 0.0 silencioso de uma empresa ausente; esta
    funcao so' alimenta indicador de tendencia/comparativo com periodo
    anterior)."""
    periodos = db.listar_periodos_grupo(conn, empresas_codigos, status="ATIVO")
    if not periodos:
        return []
    lancs = db.listar_lancamentos_grupo_periodos(conn, periodos, tipo, empresas_codigos)
    if not lancs:
        return []
    df = pd.DataFrame(lancs)
    df["valor"] = df["valor"].astype(float)
    agrupado = df.groupby(["periodo", "grupo", "conta"], as_index=False)["valor"].sum()
    return agrupado.to_dict("records")


def _consolidar_despesas_admin_itens(conn, empresas_codigos: list, periodo: date) -> list:
    """Soma os itens de despesas administrativas (Fase 2, ranking --
    tabela isolada egc.despesas_admin_itens) das empresas do grupo por
    nome de conta. Ordem: 1a aparicao entre as empresas (a ordenacao
    final por valor ja' acontece em _montar_despesas_admin_itens, que
    esta funcao alimenta do mesmo jeito que db.listar_despesas_admin_itens
    alimenta o caso de 1 empresa)."""
    somas: dict = {}
    ordem: list = []
    for cod in empresas_codigos:
        for conta, valor in db.listar_despesas_admin_itens(conn, cod, periodo):
            if conta not in somas:
                somas[conta] = 0.0
                ordem.append(conta)
            somas[conta] += valor
    return [(conta, somas[conta]) for conta in ordem]


def montar_dados_relatorio(
    conn,
    empresa_codigo,
    periodo: date,
    periodo_label: str,
    periodo_extenso: str = "",
    data_geracao: Optional[str] = None,
    admin: Optional[dict] = None,
) -> tuple[dict, bool]:
    """Monta (dados, incluir_pagina_resultado) prontos pra
    gerador_relatorio_comentado.gerar_pdf_completo(dados, caminho,
    incluir_pagina_resultado).

    `empresa_codigo`: str (1 empresa, comportamento de sempre) OU
    list[str] com 2+ codigos -- CONSOLIDADO ("montante") do grupo pra
    este UNICO periodo (pedido do Rafael, 29/09/2026: "multi-CNPJ, 1
    periodo so, como um unico consolidado" -- via Modelo A, nao o B,
    porque o B e' um relatorio de EVOLUCAO entre periodos e nao faz
    sentido com 1 periodo so). Mesmo principio aditivo (sem eliminacao
    entre empresas) ja usado no Modelo B/Visao Grupo, reaproveitado via
    _consolidar_periodo/_consolidar_historico/_consolidar_despesas_admin_itens
    -- nao reimplementado aqui. Com 2+ codigos, cada um precisa ter o
    periodo pedido ATIVO (mesma checagem estrita do Modelo B: um
    "consolidado" incompleto sem avisar seria pior que travar). Caminho
    de 1 empresa fica 100% intocado (mesmo codigo/mesmo retorno de
    sempre) -- so' o caminho de 2+ e' novo.

    periodo_label/periodo_extenso: texto que a contadora digita/confirma
    na tela (decisao do Rafael 28/09/2026 -- nao ha' coluna confiavel de
    granularidade no banco pra inferir isso sozinho).

    admin: dict opcional com nome_administrador/cargo_administrador/
    nome_contador/cargo_contador/email_empresa/site_empresa -- tambem
    inputs editaveis na tela (decisao do Rafael, 24/09). Campo ausente
    vira string vazia (a tela decide o que exigir antes de gerar).
    """
    admin = admin or {}
    data_geracao = data_geracao or date.today().strftime("%d/%m/%Y")

    codigos = [empresa_codigo] if isinstance(empresa_codigo, str) else list(empresa_codigo)
    if not codigos:
        raise ValueError("empresa_codigo precisa ter pelo menos 1 empresa")
    grupo = len(codigos) > 1

    empresas = {e["codigo"]: e for e in db.listar_empresas(conn)}

    if grupo:
        for cod in codigos:
            if periodo not in set(db.listar_periodos(conn, cod, status="ATIVO")):
                raise ValueError(
                    f"{cod} nao tem o periodo {periodo.strftime('%m/%Y')} ativo -- remova essa "
                    "empresa da selecao ou escolha outro periodo pra gerar o consolidado."
                )
        bp_periodo = _consolidar_periodo(conn, codigos, periodo, "BP")
        dre_periodo = _consolidar_periodo(conn, codigos, periodo, "DRE")
        bp_hist = _consolidar_historico(conn, codigos, "BP")
        dre_hist = _consolidar_historico(conn, codigos, "DRE")
        itens_admin = _consolidar_despesas_admin_itens(conn, codigos, periodo)
        periodos_ativos = db.listar_periodos_grupo(conn, codigos, status="ATIVO")
        empresa = {}
    else:
        cod_unico = codigos[0]
        empresa = empresas.get(cod_unico, {})
        bp_periodo = db.listar_lancamentos(conn, cod_unico, periodo, "BP")
        dre_periodo = db.listar_lancamentos(conn, cod_unico, periodo, "DRE")
        bp_hist = db.listar_historico_grupo(conn, cod_unico, "BP")
        dre_hist = db.listar_historico_grupo(conn, cod_unico, "DRE")
        itens_admin = db.listar_despesas_admin_itens(conn, cod_unico, periodo)
        periodos_ativos = db.listar_periodos(conn, cod_unico, status="ATIVO")

    bp_map = _mapa(bp_periodo)
    dre_map = _mapa(dre_periodo)
    indic_df = indicadores.calcular_indicadores(bp_hist, dre_hist)

    # ---- Receita / Custos (pagina 3) ----
    receita_bruta = dre_map.get("RECEITA OPERACIONAL BRUTA", 0.0) + dre_map.get("RECEITAS OPERACIONAIS DIVERSAS", 0.0)
    deducoes_receita = abs(dre_map.get("DEDUCOES DA RECEITA BRUTA", 0.0))
    deducoes_pct_bruta = (deducoes_receita / receita_bruta) if receita_bruta else 0.0
    receita_liquida = dre_map.get("RECEITA OPERACIONAL LIQUIDA", 0.0)
    custo_servicos = abs(dre_map.get("CUSTO DOS PRODUTOS/SERVICOS", 0.0))
    custo_pct_liquida = (custo_servicos / receita_liquida) if receita_liquida else 0.0
    lucro_bruto = dre_map.get("LUCRO BRUTO", 0.0)
    margem_bruta = _linha_indicador(indic_df, periodo, "Margem Bruta")
    if margem_bruta is None:
        margem_bruta = (lucro_bruto / receita_liquida) if receita_liquida else 0.0

    # ---- Despesas (pagina 4) ----
    despesas_operacionais = abs(dre_map.get("DESPESAS OPERACIONAIS", 0.0))
    despesas_administrativas = abs(dre_map.get("ADMINISTRATIVAS", 0.0))
    despesas_tributarias = abs(dre_map.get("DESPESAS TRIBUTARIAS", 0.0))
    resultado_financeiro = -(dre_map.get("DESPESAS FINANCEIRAS", 0.0) + dre_map.get("RECEITAS FINANCEIRAS", 0.0))
    despesas_financeiras = resultado_financeiro  # mesmo numero, 2 paginas diferentes (ver BASE fixture)
    despesas_admin_itens = _montar_despesas_admin_itens(itens_admin, despesas_administrativas)

    # ---- Resultado / CSLL-IRPJ (pagina 5, opcional) ----
    tem_csll_irpj = "PROVISAO CSLL" in dre_map and "PROVISAO IRPJ" in dre_map
    csll_irpj = -(dre_map.get("PROVISAO CSLL", 0.0) + dre_map.get("PROVISAO IRPJ", 0.0)) if tem_csll_irpj else None
    resultado_liquido = dre_map.get("LUCRO LIQUIDO DO EXERCICIO", 0.0)
    margem_liquida = _linha_indicador(indic_df, periodo, "Margem Líquida")
    if margem_liquida is None:
        margem_liquida = (resultado_liquido / receita_liquida) if receita_liquida else 0.0

    # ---- EBITDA (pagina 6) ----
    deprec_amortiz = -(dre_map.get("DEPRECIACOES", 0.0) + dre_map.get("AMORTIZACOES", 0.0))
    ebitda = _linha_indicador(indic_df, periodo, "EBITDA")
    margem_ebitda = _linha_indicador(indic_df, periodo, "Margem EBITDA")
    if ebitda is None:
        # fallback local, so' se o indicador nao pode ser calculado (ex.:
        # historico incompleto) -- mesma formula do indicadores.py.
        ebitda = resultado_liquido + (csll_irpj or 0.0) + resultado_financeiro + deprec_amortiz
        margem_ebitda = (ebitda / receita_liquida) if receita_liquida else 0.0

    # ---- Balanco (pagina 7) ----
    total_ativo = bp_map.get("TOTAL DO ATIVO", 0.0)
    ativo_circulante = bp_map.get("TOTAL CIRCULANTE ATIVO", 0.0)
    ativo_nao_circulante = bp_map.get("TOTAL NAO CIRCULANTE ATIVO", 0.0)
    passivo_circulante = bp_map.get("TOTAL CIRCULANTE PASSIVO", 0.0)
    passivo_nao_circulante = bp_map.get("TOTAL NAO CIRCULANTE PASSIVO", 0.0)
    patrimonio_liquido = bp_map.get("TOTAL PATRIMONIO LIQUIDO", 0.0)
    imobilizado = bp_map.get("IMOBILIZADO", 0.0)
    liquidez_corrente = _linha_indicador(indic_df, periodo, "Liquidez Corrente") or 0.0
    alavancagem = _linha_indicador(indic_df, periodo, "Alavancagem") or 0.0
    endividamento_geral = _linha_indicador(indic_df, periodo, "Endividamento Geral") or 0.0

    # ---- Comparativos com periodo anterior (textos livres) ----
    periodos_ordenados = sorted(periodos_ativos)
    periodo_anterior = None
    if periodo in periodos_ordenados:
        idx = periodos_ordenados.index(periodo)
        if idx > 0:
            periodo_anterior = periodos_ordenados[idx - 1]
    receita_anterior = _linha_indicador(indic_df, periodo_anterior, "Margem Bruta") if periodo_anterior else None
    # Receita liquida nao e' coluna de indic_df -- pega direto do historico DRE.
    receita_liquida_anterior = None
    ebitda_anterior = None
    despesas_op_anterior = None
    if periodo_anterior is not None:
        dre_ant = {r["conta"]: float(r["valor"]) for r in dre_hist if r["periodo"] == periodo_anterior}
        if "RECEITA OPERACIONAL LIQUIDA" in dre_ant:
            receita_liquida_anterior = dre_ant["RECEITA OPERACIONAL LIQUIDA"]
        if "DESPESAS OPERACIONAIS" in dre_ant:
            despesas_op_anterior = abs(dre_ant["DESPESAS OPERACIONAIS"])
        ebitda_anterior = _linha_indicador(indic_df, periodo_anterior, "EBITDA")

    complemento_receita = _texto_comparativo("Receita líquida", receita_liquida, receita_liquida_anterior)
    complemento_ebitda = _texto_comparativo("EBITDA", ebitda, ebitda_anterior)
    complemento_despesas = _texto_comparativo("Despesas operacionais", despesas_operacionais, despesas_op_anterior)
    callout_estrutura_capital = (
        f"Alavancagem de {alavancagem:.2f}x (R$ de capital de terceiros para cada R$ 1,00 de capital próprio) "
        f"e endividamento geral de {endividamento_geral*100:.1f}% do ativo total."
    )

    if grupo:
        nome_empresa = "Grupo Enermais"
        cnpj = ""
    else:
        nome_empresa = empresa.get("nome", codigos[0])
        cnpj = empresa.get("cnpj", "")

    dados = dict(
        empresa_codigo=codigos[0],
        empresa_nome=nome_empresa,
        cnpj=cnpj,
        cabecalho_relatorio=f"Demonstrativo Comentado · {periodo_label}",
        periodo_label=periodo_label,
        periodo_extenso=periodo_extenso,
        data_posicao=periodo.strftime("%d/%m/%Y"),
        data_geracao=data_geracao,
        receita_bruta=receita_bruta, deducoes_receita=deducoes_receita, deducoes_pct_bruta=deducoes_pct_bruta,
        receita_liquida=receita_liquida, custo_servicos=custo_servicos, custo_pct_liquida=custo_pct_liquida,
        lucro_bruto=lucro_bruto, margem_bruta=margem_bruta,
        despesas_operacionais=despesas_operacionais, despesas_administrativas=despesas_administrativas,
        despesas_financeiras=despesas_financeiras, despesas_tributarias=despesas_tributarias,
        despesas_admin_itens=despesas_admin_itens,
        resultado_liquido=resultado_liquido, margem_liquida=margem_liquida,
        resultado_financeiro=resultado_financeiro, deprec_amortiz=deprec_amortiz,
        ebitda=ebitda, margem_ebitda=margem_ebitda,
        total_ativo=total_ativo, ativo_circulante=ativo_circulante, ativo_nao_circulante=ativo_nao_circulante,
        passivo_circulante=passivo_circulante, passivo_nao_circulante=passivo_nao_circulante,
        patrimonio_liquido=patrimonio_liquido, imobilizado=imobilizado,
        liquidez_corrente=liquidez_corrente, alavancagem=alavancagem, endividamento_geral=endividamento_geral,
        complemento_receita=complemento_receita, complemento_ebitda=complemento_ebitda,
        complemento_despesas=complemento_despesas, callout_estrutura_capital=callout_estrutura_capital,
        anexo_ativo=_montar_anexo(bp_periodo, "ATIVO"),
        anexo_passivo=_montar_anexo(bp_periodo, "PASSIVO"),
        nome_administrador=admin.get("nome_administrador", ""), cargo_administrador=admin.get("cargo_administrador", "Administrador"),
        nome_contador=admin.get("nome_contador", ""), cargo_contador=admin.get("cargo_contador", "Contador"),
        email_empresa=admin.get("email_empresa", ""), site_empresa=admin.get("site_empresa", ""),
    )
    if grupo:
        dados["empresas_codigos"] = list(codigos)
    if tem_csll_irpj:
        dados["csll_irpj"] = csll_irpj

    return dados, tem_csll_irpj


# ─────────────────────────────────────────────
#  MODELO B (comparativo multi-período) -- 29/09/2026
# ─────────────────────────────────────────────
#
# Camada de dados do gerador_relatorio_comparativo.py (motor de desenho ja'
# existia e tinha teste proprio desde 26/09 -- so' faltava esta camada,
# que busca do Supabase em vez de fixture). Pedido do Rafael: "por mim
# podemos implantar o multi-periodos ja tb".
#
# Reaproveita montar_dados_relatorio() (Modelo A) PERIODO A PERIODO --
# nao reimplementa a extracao BP/DRE nem as formulas de indicador de
# novo, so' recorta os poucos campos que o comparativo precisa de cada
# periodo ja calculado. Chamado N vezes (2-4 periodos, mesmo teto do
# motor de desenho) -- redundante buscar o historico completo de novo a
# cada chamada, mas simples e correto; otimizar (1 so historico
# compartilhado) fica pra depois se a UI acusar lentidao real.

_METRICAS_FLUXO = [
    ("receita_liquida", "Receita Operacional Líquida"),
    ("ebitda", "EBITDA"),
    ("resultado_liquido", "Resultado Líquido"),
]
_METRICAS_SALDO = [
    ("total_ativo", "Total do Ativo"),
    ("patrimonio_liquido", "Patrimônio Líquido"),
]


def _montar_anexo_multi_periodo(bp_por_periodo: list, lado: str) -> list:
    """anexo_ativo/anexo_passivo multi-coluna (Modelo B) a partir de N
    listas de lançamentos BP (1 por período, MESMA ORDEM de períodos do
    resto do relatório comparativo).

    Reusa `_montar_anexo` (Modelo A, 1 período) pra cada período e
    MESCLA as árvores por (tipo, label) em UNIÃO, não interseção: uma
    conta/subtotal que existe em QUALQUER período do intervalo entra na
    lista final. Se mesclasse só as contas comuns a todos os períodos,
    uma conta que tinha saldo num período mais antigo e zerou/sumiu no
    mais recente desapareceria silenciosamente do comparativo -- o
    oposto da regra de ouro do módulo (nunca inventa, mas também nunca
    esconde). Período sem aquela linha recebe 0.0 (ausência de
    lançamento no período = saldo 0 naquele período, nunca "sem dado").

    Ordem das linhas: a do primeiro período (na lista `bp_por_periodo`)
    que tiver a linha, na ordem em que aparece lá -- linhas que só
    aparecem em períodos seguintes (conta nova) entram no fim, na ordem
    em que forem encontradas.

    Limitação aceita: casa por (tipo, label) exato -- se a mesma conta
    aparecer como 'conta' solta num período e 'subconta' aninhada
    (dependente de _HIERARQUIA_BP) noutro, vira 2 linhas em vez de 1.
    Não tratado aqui por não ter caso real observado ainda; revisar se
    aparecer."""
    arvores = [_montar_anexo(bp, lado) for bp in bp_por_periodo]
    n = len(arvores)

    ordem = []
    vistas = set()
    valores: dict = {}
    for i, arvore in enumerate(arvores):
        for linha in arvore:
            tipo, label = linha[0], linha[1]
            chave = (tipo, label)
            if chave not in vistas:
                vistas.add(chave)
                ordem.append(chave)
                if tipo != "grupo":
                    valores[chave] = [0.0] * n
            if tipo != "grupo":
                valores[chave][i] = float(linha[2])

    linhas = []
    for tipo, label in ordem:
        if tipo == "grupo":
            linhas.append((tipo, label))
        else:
            linhas.append((tipo, label, *valores[(tipo, label)]))
    return linhas


def montar_dados_relatorio_comparativo(
    conn,
    empresas_codigos,
    periodos: list,
    periodos_labels: list,
    periodo_range_label: str,
    data_geracao: Optional[str] = None,
    admin: Optional[dict] = None,
) -> dict:
    """Monta o dict pronto pra
    gerador_relatorio_comparativo.gerar_pdf_comparativo(dados, caminho)
    -- Modelo B, evolução entre 2 a 4 períodos.

    `empresas_codigos`: list[str] com 1 ou mais códigos (ver
    conexao.EMPRESAS_FIXAS). 1 código só = comportamento de sempre (1
    empresa). 2+ códigos = CONSOLIDADO (soma) entre as empresas
    escolhidas em cada período -- pedido do Rafael 29/09/2026: "tem q
    ser possivel gerar o evolutivo só da Enermais energia, com energia e
    outro (exemplo) ou com todos os CNPJ no montante". Mesmo princípio
    já usado na Visão Grupo (visao_grupo.montar_pivot_grupo/
    montar_serie_kpis_grupo) e já testado no Modelo A
    (test_gerador_completo.py::test_anexo_multi_coluna_empresas...) --
    reaproveitado aqui, não reimplementado: os KPIs (Receita/EBITDA/
    Resultado/Ativo/PL) são a SOMA dos valores já calculados por
    `montar_dados_relatorio` de cada empresa (tudo aditivo -- sem
    eliminação entre empresas, mesma premissa da Visão Grupo); o Anexo
    consolidado vem de `db.listar_lancamentos_grupo` +
    `visao_grupo.montar_pivot_grupo` (coluna "VALOR CONSOLIDADO").

    `periodos`: list[date] em ordem cronológica (mais antigo primeiro).
    `periodos_labels`: rótulo de cada período NA MESMA ORDEM/tamanho de
    `periodos` (ex. ["2024", "2025", "2026"] ou ["1S2025", "2S2025",
    "1S2026"]) -- texto que a tela deixa a contadora digitar/confirmar,
    mesma decisão já tomada pro Modelo A (não inferir sozinho).
    `admin`: mesmo dict opcional do Modelo A (nome/cargo de
    administrador e contador, e-mail, site).

    FIX_20260929i: com 2+ empresas, cada período tem que existir (status
    ATIVO) em TODAS elas -- somar um período que 1 empresa não tem daria
    um "consolidado" incompleto sem avisar (`montar_dados_relatorio`
    NÃO levanta exceção pra BP/DRE ausente, só devolve 0.0 -- correto
    pro caso de 1 empresa só, perigoso aqui: um 0.0 silencioso de uma
    empresa que na verdade tem número real em outro lugar do sistema
    passaria despercebido no "montante"). Checagem AQUI (não só na
    tela) pra função ficar segura mesmo chamada direto."""
    if not empresas_codigos:
        raise ValueError("empresas_codigos precisa ter pelo menos 1 empresa")
    if len(periodos) != len(periodos_labels):
        raise ValueError("periodos e periodos_labels precisam ter o mesmo tamanho")
    if not (2 <= len(periodos) <= 4):
        raise ValueError("comparativo aceita de 2 a 4 períodos (mesmo teto do motor de desenho)")

    admin = admin or {}
    data_geracao = data_geracao or date.today().strftime("%d/%m/%Y")

    empresas_map = {e["codigo"]: e for e in db.listar_empresas(conn)}
    grupo = len(empresas_codigos) > 1

    if grupo:
        for cod in empresas_codigos:
            periodos_da_empresa = set(db.listar_periodos(conn, cod, status="ATIVO"))
            faltando = [p for p in periodos if p not in periodos_da_empresa]
            if faltando:
                faltando_txt = ", ".join(p.strftime("%m/%Y") for p in faltando)
                raise ValueError(
                    f"{cod} não tem período ativo em {faltando_txt} -- remova esse(s) período(s) "
                    f"ou desmarque {cod} da seleção pra gerar o comparativo consolidado."
                )

    campos_kpi = [campo for campo, _ in _METRICAS_FLUXO + _METRICAS_SALDO]

    dados_por_periodo = []
    bp_por_periodo = []
    for periodo in periodos:
        somas = {campo: 0.0 for campo in campos_kpi}
        for cod in empresas_codigos:
            dados_p, _incluir_resultado = montar_dados_relatorio(
                conn, cod, periodo, periodo_label=str(periodo),
            )
            for campo in campos_kpi:
                somas[campo] += dados_p[campo]
        dados_por_periodo.append(somas)

        if grupo:
            lancs = db.listar_lancamentos_grupo(conn, periodo, "BP", empresas_codigos)
            pivot = visao_grupo.montar_pivot_grupo(lancs, empresas_codigos)
            bp_periodo = [
                {"grupo": row["grupo"], "conta": row["conta"], "valor": row["VALOR CONSOLIDADO"]}
                for row in pivot.to_dict("records")
            ]
        else:
            bp_periodo = db.listar_lancamentos(conn, empresas_codigos[0], periodo, "BP")
        bp_por_periodo.append(bp_periodo)

    def _serie(campo):
        return [d[campo] for d in dados_por_periodo]

    kpis_fluxo = [
        dict(label=label, tag="fluxo", valores=(vals := _serie(campo)), acumulado=sum(vals))
        for campo, label in _METRICAS_FLUXO
    ]
    kpis_saldo = [
        dict(label=label, tag="saldo", valores=_serie(campo))
        for campo, label in _METRICAS_SALDO
    ]
    # grafico de evolucao usa o mesmo subconjunto do fluxo (Receita/EBITDA/
    # Resultado) -- sao os 3 indicadores que mais contam a historia de
    # "como evoluimos", sem repetir os 5 do Modelo A que nao fazem
    # sentido pra N periodos (ver docstring de gerar_pdf_comparativo).
    grafico_evolucao_metricas = [
        dict(label=label, valores=_serie(campo)) for campo, label in _METRICAS_FLUXO
    ]

    anexo_ativo = _montar_anexo_multi_periodo(bp_por_periodo, "ATIVO")
    anexo_passivo = _montar_anexo_multi_periodo(bp_por_periodo, "PASSIVO")

    if grupo:
        # "Grupo Enermais" pro nome grande (mesma convenção já usada e
        # testada no Modelo A -- test_anexo_multi_coluna_empresas..., que
        # usa esse nome mesmo pra 2 de 6 empresas, não só quando TODAS
        # estão selecionadas); a composição exata (quais/quantas) fica
        # no escopo_label, que aparece logo abaixo do título na pág. 2.
        nome_empresa = "Grupo Enermais"
        cnpj = ""
        escopo_label = (
            f"Grupo Enermais ({len(empresas_codigos)} empresas: "
            f"{' + '.join(empresas_codigos)}) · {periodo_range_label}"
        )
    else:
        empresa = empresas_map.get(empresas_codigos[0], {})
        nome_empresa = empresa.get("nome", empresas_codigos[0])
        cnpj = empresa.get("cnpj", "")
        escopo_label = f"{nome_empresa} · {periodo_range_label}"

    return dict(
        empresa_codigo=empresas_codigos[0],
        empresas_codigos=list(empresas_codigos),
        empresa_nome=nome_empresa,
        cnpj=cnpj,
        cabecalho_relatorio=f"Evolução Financeira · {periodo_range_label}",
        periodos_labels=list(periodos_labels),
        periodo_range_label=periodo_range_label,
        data_geracao=data_geracao,
        kpis_fluxo=kpis_fluxo,
        kpis_saldo=kpis_saldo,
        grafico_evolucao_metricas=grafico_evolucao_metricas,
        anexo_colunas=list(periodos_labels),
        anexo_escopo_label=escopo_label,
        anexo_ativo=anexo_ativo,
        anexo_passivo=anexo_passivo,
        nome_administrador=admin.get("nome_administrador", ""),
        cargo_administrador=admin.get("cargo_administrador", "Administrador"),
        nome_contador=admin.get("nome_contador", ""),
        cargo_contador=admin.get("cargo_contador", "Contador"),
        email_empresa=admin.get("email_empresa", ""),
        site_empresa=admin.get("site_empresa", ""),
    )
