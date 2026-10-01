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

import unicodedata
import warnings
from collections import Counter
from datetime import date
from typing import Optional

import pandas as pd

import db
import indicadores
import selecao_periodos
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


def _montar_anexo(bp_periodo: list, lado: str, incluir_subconta: bool = True) -> list:
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
    sem par confirmado em _HIERARQUIA_BP fica solta ('conta' simples).

    `incluir_subconta=False` (FIX_20260929q, Rafael: "detalhamento de
    conta... de forma q se enquadre em 1 pag ou 2? Veja oq perde" ->
    concordou em cortar só a subconta, opção 1 das 3 avaliadas) -- omite
    a linha 'subconta' (o detalhe aninhado embaixo de 7 contas-pai:
    Disponível, Clientes, Instituições Financeiras, Outros Créditos,
    Obrigações Tributárias, Obrigações Trabalhistas, Outras Obrigações).
    A conta-pai continua com seu valor PRÓPRIO (vem direto do SPED, não é
    somado a partir dos filhos exibidos) -- nenhum total muda, só some a
    composição interna de 7 contas. Usado só por
    `_montar_anexo_multi_periodo` (Modelo B, onde Ativo+Passivo empilham
    em largura cheia e o espaço é mais apertado); o layout clássico
    (`montar_dados_relatorio`, 1 período) continua com subconta, nunca
    precisou cortar nada."""
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
            if not incluir_subconta:
                continue
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


def _consolidar_periodo(conn, empresas_codigos: list, periodo: date, tipo: str, granularidade: str = "") -> list:
    """BP ou DRE consolidado (soma aditiva, sem eliminacao entre
    empresas -- mesma premissa da Visao Grupo, confirmada com o Rafael)
    de N empresas pra 1 UNICO periodo. Formato {grupo, conta, valor},
    compativel com _mapa()/_montar_anexo() (mesmas chaves que
    db.listar_lancamentos devolve, so' sem id/origem/pdf_original/
    atualizado_em, que ninguem le aqui). Reaproveita a mesma infra ja
    testada do Modelo B/Visao Grupo (db.listar_lancamentos_grupo +
    visao_grupo.montar_pivot_grupo, coluna "VALOR CONSOLIDADO") -- soma
    nao reimplementada de novo.

    granularidade (Fase 3, 29/09/2026): SEMPRE passada explicitamente
    daqui pra baixo (default "" = sem intervalo declarado, o caso comum)
    -- desde que 2 documentos de abrangencia diferente (ex. trimestral e
    semestral) podem ficar ATIVOS ao mesmo tempo pro mesmo periodo_fim
    (ver db.inativar_periodo_existente), o relatorio NUNCA pode buscar
    "o periodo X" sem dizer qual granularidade, senao misturaria as 2
    no consolidado sem avisar."""
    lancs = db.listar_lancamentos_grupo(conn, periodo, tipo, empresas_codigos, granularidade=granularidade)
    pivot = visao_grupo.montar_pivot_grupo(lancs, empresas_codigos)
    return [
        {"grupo": row["grupo"], "conta": row["conta"], "valor": row["VALOR CONSOLIDADO"]}
        for row in pivot.to_dict("records")
    ]


def _consolidar_historico(conn, empresas_codigos: list, tipo: str, granularidade: Optional[str] = None) -> list:
    """Historico consolidado (uniao dos periodos ATIVOS de QUALQUER
    empresa do grupo, nao intersecao) no mesmo formato de
    db.listar_historico_grupo (1 empresa): {periodo, grupo, conta,
    valor, granularidade}, somado entre as empresas por (periodo, grupo,
    conta, granularidade). Alimenta indicadores.calcular_indicadores sem
    tocar no motor de formulas (mesma regra de "nao reimplementar" do
    resto do modulo).

    granularidade (Fase 4, 30/09/2026 -- bug real: o consolidado de um
    relatorio TRIMESTRAL pegava o documento SEMESTRAL so' da Energia por
    causa da "vencedora"): quando informada (inclusive ""), devolve SO' as
    linhas dessa granularidade e SO' dos periodos em que TODAS as
    empresas do grupo tem dado nela -- um periodo "consolidado" com
    empresa faltando nunca serve de base pra tendencia/periodo
    anterior. None = legado (todas as granularidades preservadas, quem
    consome resolve).

    Sem granularidade (legado), periodo que falta numa empresa do grupo
    entra como 0.0 dela (mesma premissa de
    visao_grupo.montar_serie_kpis_grupo); com granularidade, periodo
    incompleto simplesmente nao entra. A checagem ESTRITA do periodo
    PEDIDO continua em montar_dados_relatorio."""
    periodos = db.listar_periodos_grupo(conn, empresas_codigos, status="ATIVO")
    if not periodos:
        return []
    lancs = db.listar_lancamentos_grupo_periodos(conn, periodos, tipo, empresas_codigos, granularidade=granularidade)
    if not lancs:
        return []
    if granularidade is not None:
        lancs = [l for l in lancs if (l.get("granularidade") or "") == granularidade]
        if lancs and all("empresa_codigo" in l for l in lancs):
            por_periodo: dict = {}
            for l in lancs:
                por_periodo.setdefault(l["periodo"], set()).add(l["empresa_codigo"])
            exigidas = set(empresas_codigos)
            completos = {p for p, emps in por_periodo.items() if exigidas <= emps}
            lancs = [l for l in lancs if l["periodo"] in completos]
        if not lancs:
            return []
    df = pd.DataFrame(lancs)
    df["valor"] = df["valor"].astype(float)
    # Fase 3.1 (29/09/2026): mantem 'granularidade' no groupby/saida (em
    # vez de descartar a coluna) -- sem isso, indicadores.calcular_indicadores
    # nao teria como separar 2 documentos de abrangencia diferente ATIVOS
    # pro MESMO periodo_fim.
    if "granularidade" not in df.columns:
        df["granularidade"] = ""
    df["granularidade"] = df["granularidade"].fillna("")
    agrupado = df.groupby(["periodo", "grupo", "conta", "granularidade"], as_index=False)["valor"].sum()
    return agrupado.to_dict("records")


def _buscar_itens_admin(conn, cod: str, periodo: date, granularidade: str) -> list:
    """Itens de despesas administrativas de 1 empresa pro documento
    (periodo, granularidade) pedido. Primeiro a granularidade exata; se
    nao houver (linhas gravadas antes do bloco 19 do schema.sql, que nao
    tinham a coluna e ficam como "" / nao declarada), cai nas linhas
    sem granularidade -- quem chama valida a coerencia com o total do
    DRE (_itens_admin_coerentes) antes de usar."""
    itens = db.listar_despesas_admin_itens(conn, cod, periodo, granularidade=granularidade)
    if not itens and granularidade:
        itens = db.listar_despesas_admin_itens(conn, cod, periodo, granularidade="")
    return itens


# Tolerancia da checagem soma(itens) x total "ADMINISTRATIVAS" do DRE.
# Na extracao real os dois batem exatamente (validado em 4 PDFs, ver
# parser_egc.extrair_despesas_admin_itens); 2% separa folgadamente
# arredondamento/item isolado de um ranking que pertence a OUTRO
# documento do mesmo periodo_fim (ex. semestral x trimestral: ~2x).
_TOL_ITENS_ADMIN = 0.02


def _itens_admin_coerentes(itens: list, despesas_administrativas: float, avisos: list, rotulo: str) -> list:
    """Devolve `itens` so' se a soma bate com o total ADMINISTRATIVAS do
    documento usado no relatorio; senao [] + aviso (o ranking de
    despesas nunca pode vir de outro documento do mesmo periodo_fim --
    REGRA: match obrigatorio de periodo + granularidade)."""
    if not itens:
        return []
    soma = sum(abs(float(v)) for _n, v in itens)
    ref = abs(float(despesas_administrativas))
    if ref == 0 or abs(soma - ref) <= max(1.0, ref * _TOL_ITENS_ADMIN):
        return itens
    avisos.append(
        f"Ranking de despesas administrativas de {rotulo} não incluído: os itens gravados somam "
        f"R$ {soma:,.2f} e o total ADMINISTRATIVAS do documento é R$ {ref:,.2f} (provavelmente vieram de "
        "outro PDF do mesmo período). Reimporte o DRE desta granularidade para recompor o ranking."
    )
    return []


def _consolidar_despesas_admin_itens(conn, empresas_codigos: list, periodo: date, granularidade: str = "") -> list:
    """Soma os itens de despesas administrativas (Fase 2, ranking --
    tabela isolada egc.despesas_admin_itens) das empresas do grupo por
    nome de conta. Ordem: 1a aparicao entre as empresas (a ordenacao
    final por valor ja' acontece em _montar_despesas_admin_itens, que
    esta funcao alimenta do mesmo jeito que db.listar_despesas_admin_itens
    alimenta o caso de 1 empresa)."""
    somas: dict = {}
    ordem: list = []
    for cod in empresas_codigos:
        for conta, valor in _buscar_itens_admin(conn, cod, periodo, granularidade):
            if conta not in somas:
                somas[conta] = 0.0
                ordem.append(conta)
            somas[conta] += valor
    return [(conta, somas[conta]) for conta in ordem]


# FIX_20260930 (Rafael, variante "Demonstrativo Comentado Gerencial" --
# a página "Composição das Despesas Administrativas" sai do relatório
# padrão e só existe nesta variante nova). String (não bool) de proposito
# -- o mapa cresce sem reabrir a assinatura de `montar_dados_relatorio`.
# v0.40.0: "fornecedor" construida -- mesmo conteudo do "padrao" (sem a
# pagina de Composicao das Despesas), so' muda o titulo/selo da capa e o
# sufixo do arquivo. Pedir uma variante desconhecida levanta erro claro
# em vez de gerar um relatorio errado silenciosamente (mesma REGRA DE
# OURO do resto do modulo).
_VARIANTE_TITULOS = {
    "padrao": "Demonstrativo Comentado",
    "gerencial": "Demonstrativo Comentado Gerencial",
    # 01/10/2026 (Rafael): "retira 'fornecedor' da capa" -- o nome so' existe no app
    # (seletor de tipo) e no sufixo do arquivo; o documento diz so' "Demonstrativo Comentado".
    "fornecedor": "Demonstrativo Comentado",
}
VARIANTES_VALIDAS = tuple(_VARIANTE_TITULOS)



# ─────────────────────────────────────────────
#  v0.40.0 -- salvaguardas de coerencia do DOCUMENTO (periodo + granularidade)
# ─────────────────────────────────────────────
#
# Os PDFs de teste de 01/10/2026 mostraram um "trimestral" com numeros do
# SEMESTRAL (e um Grupo somando Energia semestral + demais trimestrais).
# Contra um Postgres real o pipeline de calculo esta' certo quando o
# ROTULO de granularidade gravado em egc.lancamentos corresponde ao
# conteudo -- o erro reproduzido e' um documento cujo rotulo diz
# "trimestral" mas cujas linhas sao do semestral. O motor nao tinha como
# perceber: confiava no rotulo. As 2 checagens abaixo desconfiam dele.

_GRANULARIDADES_CONHECIDAS = set(selecao_periodos.MESES_GRANULARIDADE)


def _granularidade_do_intervalo(periodo_inicio: date, periodo_fim: date) -> str:
    """Mesma classificacao do parser (parser_egc.calcular_granularidade)
    aplicada ao intervalo GRAVADO (periodo_inicio..periodo). Import tardio:
    o parser puxa dependencias de PDF que a camada de dados nao precisa
    carregar no import."""
    from parser_egc import calcular_granularidade

    return calcular_granularidade(periodo_inicio.strftime("%d/%m/%Y"), periodo_fim.strftime("%d/%m/%Y"))


def validar_cobertura_documento(conn, cod: str, periodo: date, granularidade: str) -> None:
    """Levanta ValueError se o intervalo que o PROPRIO PDF declarou
    (periodo_inicio..periodo_fim, gravado na importacao) classifica numa
    granularidade DIFERENTE da gravada em `granularidade` -- ex. DRE
    gravado como 'trimestral' mas com periodo_inicio 01/01/2026 (= 6
    meses, semestral). Nesse caso os numeros NAO sao do que o rotulo diz,
    e gerar o relatorio seria publicar semestre como trimestre.

    Silencioso quando nao ha como conferir (granularidade '' ou 'outra',
    documento sem periodo_inicio -- import anterior a 24/09/2026 --, ou
    coluna ausente)."""
    if (granularidade or "") not in _GRANULARIDADES_CONHECIDAS:
        return
    for ini in db.listar_inicios_documento(conn, cod, periodo, granularidade):
        declarada = _granularidade_do_intervalo(ini, periodo)
        if declarada in _GRANULARIDADES_CONHECIDAS and declarada != granularidade:
            raise ValueError(
                f"Granularidade inconsistente em {cod} {periodo.strftime('%m/%Y')}: o DRE está gravado como "
                f"'{granularidade}', mas o intervalo declarado no próprio PDF "
                f"({ini.strftime('%d/%m/%Y')} a {periodo.strftime('%d/%m/%Y')}) é '{declarada}'. "
                "Os números deste documento não são de um período "
                f"{selecao_periodos.rotulo_granularidade(granularidade).lower()} — provável erro de "
                "classificação na importação. Reimporte o PDF correto confirmando a granularidade "
                f"'{declarada}' (ou arquive este documento) antes de gerar o relatório."
            )


def _avisar_documentos_identicos(
    conn, cod: str, periodo: date, granularidade: str, dre_map: Optional[dict], avisos: list,
) -> None:
    """Aviso (nao bloqueia): outro documento ATIVO da MESMA empresa no
    MESMO periodo_fim, de granularidade diferente, com Receita Liquida e
    Resultado Liquido IDENTICOS ao deste. Um trimestre e um semestre so'
    coincidem se a empresa nao teve atividade no 1o trimestre -- muito
    mais provavel que um dos dois esteja rotulado errado.

    `dre_map` None = buscar o DRE deste documento so' se houver outra
    granularidade a comparar (caso comum: nao ha', zero consulta extra)."""
    outras = [
        d["granularidade"] for d in db.listar_periodos_detalhado(conn, cod, status="ATIVO")
        if d["periodo"] == periodo and d["granularidade"] != granularidade
    ]
    if not outras:
        return
    if dre_map is None:
        dre_map = _mapa(db.listar_lancamentos(conn, cod, periodo, "DRE", granularidade=granularidade))
    rl = dre_map.get("RECEITA OPERACIONAL LIQUIDA")
    ll = dre_map.get("LUCRO LIQUIDO DO EXERCICIO")
    if not rl:
        return
    for outra in outras:
        outro_map = _mapa(db.listar_lancamentos(conn, cod, periodo, "DRE", granularidade=outra))
        if outro_map.get("RECEITA OPERACIONAL LIQUIDA") == rl and outro_map.get("LUCRO LIQUIDO DO EXERCICIO") == ll:
            avisos.append(
                f"{cod} {periodo.strftime('%m/%Y')}: os documentos '{granularidade or 'não declarada'}' e "
                f"'{outra or 'não declarada'}' têm Receita Líquida e Resultado Líquido idênticos "
                f"(R$ {rl:,.2f} / R$ {ll or 0:,.2f}) — confira: é normal quando a empresa "
                "só teve movimento no último trimestre do semestre (ex.: começou a operar nele); "
                "se ela já operava antes, algum dos PDFs está gravado na base errada."
            )


def _periodo_anterior_imediato(candidatos: list, periodo: date, granularidade: str):
    """O periodo anterior de comparacao: o ultimo de `candidatos` (todos
    ja' da MESMA granularidade e < periodo) -- mas so' se for o periodo
    IMEDIATAMENTE anterior da base (trimestral: 3 meses antes; semestral:
    6; anual: 12 ...). Comparar 06/2026 trimestral com 12/2023 sob o rotulo
    "periodo anterior" (30 meses de distancia) e' enganoso. Devolve
    (anterior|None, ignorado|None): `ignorado` e' o ultimo da base que NAO
    e' adjacente (a tela explica por que nao comparou). Base sem tamanho
    conhecido ('' / 'outra') mantem o comportamento antigo (ultimo da base)."""
    if not candidatos:
        return None, None
    ultimo = max(candidatos)
    meses = selecao_periodos.MESES_GRANULARIDADE.get(granularidade or "")
    if not meses:
        return ultimo, None
    distancia = (periodo.year - ultimo.year) * 12 + (periodo.month - ultimo.month)
    if distancia == meses:
        return ultimo, None
    return None, ultimo


def montar_dados_relatorio(
    conn,
    empresa_codigo,
    periodo: date,
    periodo_label: str,
    periodo_extenso: str = "",
    data_geracao: Optional[str] = None,
    admin: Optional[dict] = None,
    granularidade: str = "",
    variante: str = "padrao",
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
    na tela (decisao do Rafael 28/09/2026).

    granularidade (Fase 3, 29/09/2026 -- "vamos estruturar e implantar a
    granularidade... podemos casar toda a estrutura com isso"): "" por
    padrao (sem intervalo declarado, o caso comum -- BP legado ou import
    anterior a 24/09/2026). Desde que 2 documentos de abrangencia
    diferente (trimestral/semestral/anual) podem ficar ATIVOS ao mesmo
    tempo pro MESMO periodo_fim (ver db.inativar_periodo_existente), a
    tela SEMPRE deve passar a granularidade exata do periodo escolhido
    (db.listar_periodos_detalhado) -- sem isso, um periodo_fim com 2
    granularidades ativas buscaria as 2 misturadas.

    admin: dict opcional com nome_administrador/cargo_administrador/
    nome_contador/cargo_contador/email_empresa/site_empresa -- tambem
    inputs editaveis na tela (decisao do Rafael, 24/09). Campo ausente
    vira string vazia (a tela decide o que exigir antes de gerar).

    variante ("padrao"/"gerencial"/"fornecedor", FIX_20260930 + v0.40.0): decide o TÍTULO ("Demonstrativo
    Comentado" vs "Demonstrativo Comentado Gerencial" -- entra em
    `dados['cabecalho_relatorio']`, cabeçalho de toda página) e é
    repassada em `dados['variante']` pra `gerador_relatorio_comentado.
    gerar_pdf_completo` decidir se inclui a página de Composição das
    Despesas Administrativas (só na "gerencial") e o texto da capa. Este
    módulo não desenha nada -- só resolve o título e guarda a variante no
    dict pro gerador ler.
    """
    admin = admin or {}
    data_geracao = data_geracao or date.today().strftime("%d/%m/%Y")
    if variante not in _VARIANTE_TITULOS:
        raise ValueError(
            f"variante '{variante}' desconhecida -- use {sorted(_VARIANTE_TITULOS)}."
        )

    codigos = [empresa_codigo] if isinstance(empresa_codigo, str) else list(empresa_codigo)
    if not codigos:
        raise ValueError("empresa_codigo precisa ter pelo menos 1 empresa")
    grupo = len(codigos) > 1

    empresas = {e["codigo"]: e for e in db.listar_empresas(conn)}

    # v0.40.0: rotulo de granularidade x intervalo declarado no PDF. Antes
    # de ler qualquer numero -- documento mal classificado nao gera relatorio.
    for cod in codigos:
        validar_cobertura_documento(conn, cod, periodo, granularidade)

    if grupo:
        for cod in codigos:
            ativos_cod = {(d["periodo"], d["granularidade"]) for d in db.listar_periodos_detalhado(conn, cod, status="ATIVO")}
            if (periodo, granularidade) not in ativos_cod:
                raise ValueError(
                    f"{cod} nao tem o periodo {periodo.strftime('%m/%Y')}"
                    f"{f' ({granularidade})' if granularidade else ''} ativo -- remova essa "
                    "empresa da selecao ou escolha outro periodo pra gerar o consolidado."
                )
        bp_periodo = _consolidar_periodo(conn, codigos, periodo, "BP", granularidade=granularidade)
        dre_periodo = _consolidar_periodo(conn, codigos, periodo, "DRE", granularidade=granularidade)
        bp_hist = _consolidar_historico(conn, codigos, "BP", granularidade=granularidade)
        dre_hist = _consolidar_historico(conn, codigos, "DRE", granularidade=granularidade)
        itens_admin = _consolidar_despesas_admin_itens(conn, codigos, periodo, granularidade=granularidade)
        empresa = {}
    else:
        cod_unico = codigos[0]
        empresa = empresas.get(cod_unico, {})
        bp_periodo = db.listar_lancamentos(conn, cod_unico, periodo, "BP", granularidade=granularidade)
        dre_periodo = db.listar_lancamentos(conn, cod_unico, periodo, "DRE", granularidade=granularidade)
        bp_hist = db.listar_historico_grupo(conn, cod_unico, "BP")
        dre_hist = db.listar_historico_grupo(conn, cod_unico, "DRE")
        itens_admin = _buscar_itens_admin(conn, cod_unico, periodo, granularidade)

    bp_map = _mapa(bp_periodo)
    dre_map = _mapa(dre_periodo)

    # FIX_20260930 (Rafael, produção: "Não foi possível gerar o relatório:
    # division by zero" ao tentar gerar pra um período/empresa cujo BP
    # não tinha sido gravado ainda -- crash em pagina_balanco, na divisão
    # por total_ativo). O painel de pendências avisa quando falta BP/DRE,
    # mas nada IMPEDIA clicar "Gerar" mesmo assim -- REGRA DE OURO: melhor
    # travar com mensagem clara do que deixar o traceback cru (ou pior,
    # gerar PDF com percentuais inventados a partir de 0.0). Cobre tanto
    # "não tem nenhum lançamento de BP" quanto "tem BP mas sem a conta
    # TOTAL DO ATIVO" (import parcial/fallback que não pegou o total).
    nome_alvo = "+".join(codigos) if grupo else empresas.get(codigos[0], {}).get("nome", codigos[0])
    periodo_fmt = periodo.strftime("%m/%Y")
    granul_fmt = f" ({granularidade})" if granularidade else ""
    if not bp_periodo:
        raise ValueError(
            f"{nome_alvo} não tem BP gravado pro período {periodo_fmt}{granul_fmt} -- "
            "grave o BP (Importar PDF) antes de gerar o relatório."
        )
    if "TOTAL DO ATIVO" not in bp_map:
        raise ValueError(
            f"BP de {nome_alvo} ({periodo_fmt}{granul_fmt}) está gravado mas sem a conta "
            "TOTAL DO ATIVO -- confira o PDF de origem (import pode ter ficado incompleto) "
            "antes de gerar o relatório."
        )
    if not dre_periodo:
        raise ValueError(
            f"{nome_alvo} não tem DRE gravado pro período {periodo_fmt}{granul_fmt} -- "
            "grave o DRE (Importar PDF) antes de gerar o relatório."
        )

    # Fase 4 (30/09/2026, BUG REAL): granularidade SEMPRE explicita -- sem
    # isso calcular_indicadores escolhia a "vencedora" por periodo_fim e
    # um relatorio TRIMESTRAL de 06/2026 ganhava EBITDA/margens do
    # SEMESTRAL (receita do trimestral, EBITDA do semestral no mesmo PDF).
    # Historico BP/DRE tambem reduzido a esta granularidade, pra o
    # "periodo anterior" abaixo nunca comparar trimestral com anual.
    indic_df = indicadores.calcular_indicadores(bp_hist, dre_hist, granularidade=granularidade)
    dre_hist = indicadores.filtrar_granularidade_exata(dre_hist, granularidade)
    avisos: list = []
    for cod in codigos:
        _avisar_documentos_identicos(conn, cod, periodo, granularidade, None if grupo else dre_map, avisos)

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
    itens_admin = _itens_admin_coerentes(itens_admin, despesas_administrativas, avisos, f"{nome_alvo} ({periodo_fmt}{granul_fmt})")
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
    # Periodo anterior = o periodo imediatamente anterior COM A MESMA
    # granularidade (indic_df e dre_hist ja so' tem essa granularidade --
    # Fase 4). Sem ele -> fallback "sem periodo anterior disponivel".
    anteriores = [d.date() for d in indic_df.index if d < pd.Timestamp(periodo)]
    # v0.40.0: alem de MESMA granularidade, o anterior tem que ser o
    # IMEDIATAMENTE anterior da base (ver _periodo_anterior_imediato).
    periodo_anterior, periodo_anterior_ignorado = _periodo_anterior_imediato(anteriores, periodo, granularidade)
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
        cabecalho_relatorio=f"{_VARIANTE_TITULOS[variante]} · {periodo_label}",
        variante=variante,
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
        dados["empresas_nomes"] = [empresas.get(cod, {}).get("nome", cod) for cod in codigos]
    if tem_csll_irpj:
        dados["csll_irpj"] = csll_irpj
    # Fase 4: metadado de auditoria (a UI mostra) -- qual documento
    # (periodo + granularidade) alimentou TODOS os numeros deste relatorio.
    dados["granularidade"] = granularidade
    dados["periodo_anterior"] = periodo_anterior
    dados["periodo_anterior_ignorado"] = periodo_anterior_ignorado  # ultimo da base que NAO e' adjacente
    dados["avisos"] = avisos

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
    aparecer.

    `incluir_subconta=False` sempre aqui (FIX_20260929q, ver docstring de
    `_montar_anexo`) -- este é o único caminho que empilha Ativo+Passivo
    em largura cheia (2+ colunas de valor por linha), o que aperta o
    espaço vertical; o layout clássico de 1 período (`_montar_anexo`
    chamado direto em `montar_dados_relatorio`) não usa esta função e
    continua com subconta, sem mudança."""
    arvores = [_montar_anexo(bp, lado, incluir_subconta=False) for bp in bp_por_periodo]
    n = len(arvores)

    # v0.40.0 (item C): a versao anterior mesclava por "ordem de primeira
    # aparicao" numa lista unica -- uma conta que so' existia num periodo
    # posterior (ex.: "Imoveis", "Fornecedores") entrava no FIM da lista,
    # depois do TOTAL DO ATIVO / TOTAL PASSIVO + PL, fora do proprio grupo.
    # Agora a mescla e' feita POR SECAO: cada secao (linha "grupo" + contas
    # + subtotal) recebe a uniao das contas dos periodos, em ordem
    # alfabetica sem acento (mesma ordem que _montar_anexo usa em 1
    # periodo), com o subtotal logo apos as contas e o total geral por
    # ultimo. Secoes em ordem canonica (Ativo Circulante -> Nao Circulante;
    # Passivo Circulante -> Nao Circulante -> PL).
    canonica = [rotulo for _bd, rotulo, _tot in (_GRUPOS_ATIVO if lado == "ATIVO" else _GRUPOS_PASSIVO)]

    def _ordem_secao(rotulo, vista_em):
        return (canonica.index(rotulo) if rotulo in canonica else len(canonica), vista_em)

    secoes: dict = {}      # rotulo do grupo -> {"contas": {label: [v]*n}, "subtotal": (label, [v]*n) | None}
    primeira_vez: dict = {}
    total_geral = None     # (label, [v]*n)
    contador = 0
    for i, arvore in enumerate(arvores):
        atual = None
        for linha in arvore:
            tipo, label = linha[0], linha[1]
            if tipo == "grupo":
                atual = secoes.setdefault(label, {"contas": {}, "subtotal": None})
                if label not in primeira_vez:
                    primeira_vez[label] = contador
                    contador += 1
            elif tipo == "conta":
                atual["contas"].setdefault(label, [0.0] * n)[i] = float(linha[2])
            elif tipo == "subtotal":
                if atual["subtotal"] is None:
                    atual["subtotal"] = (label, [0.0] * n)
                atual["subtotal"][1][i] = float(linha[2])
            elif tipo == "total":
                if total_geral is None:
                    total_geral = (label, [0.0] * n)
                total_geral[1][i] = float(linha[2])

    linhas = []
    for rotulo in sorted(secoes, key=lambda r: _ordem_secao(r, primeira_vez[r])):
        sec = secoes[rotulo]
        linhas.append(("grupo", rotulo))
        for label in sorted(sec["contas"], key=_chave_ordem_label):
            linhas.append(("conta", label, *sec["contas"][label]))
        if sec["subtotal"] is not None:
            linhas.append(("subtotal", sec["subtotal"][0], *sec["subtotal"][1]))
    if total_geral is not None:
        linhas.append(("total", total_geral[0], *total_geral[1]))
    return linhas


def _chave_ordem_label(label: str):
    """Ordem alfabetica sem acento/caixa (a mesma que o banco, que guarda
    o nome sem acento, daria)."""
    sem = "".join(ch for ch in unicodedata.normalize("NFD", label) if unicodedata.category(ch) != "Mn")
    return (sem.casefold(), label)


def montar_dados_relatorio_comparativo(
    conn,
    empresas_codigos,
    periodos: list,
    periodos_labels: list,
    periodo_range_label: str,
    data_geracao: Optional[str] = None,
    admin: Optional[dict] = None,
    granularidades: Optional[list] = None,
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

    `granularidades` (Fase 3, 29/09/2026): list[str] na MESMA ORDEM/
    tamanho de `periodos` -- uma granularidade por coluna (default: ""
    em todas, sem intervalo declarado). Mesma razão do Modelo A: 2
    documentos de abrangência diferente podem coexistir ATIVOS no MESMO
    periodo_fim, então cada coluna do comparativo precisa dizer qual
    delas quer, não só a data.

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

    granularidades = list(granularidades) if granularidades is not None else [""] * len(periodos)
    if len(granularidades) != len(periodos):
        raise ValueError("granularidades e periodos precisam ter o mesmo tamanho")
    # v0.40.0: o mesmo item (periodo_fim + granularidade) nao pode entrar 2x
    # (colunas identicas foram o sintoma dos PDFs de 01/10/2026) e os
    # rotulos das colunas precisam ser distintos entre si.
    pares_pedidos = list(zip(periodos, granularidades))
    if len(set(pares_pedidos)) != len(pares_pedidos):
        raise ValueError(
            "o mesmo período (data + granularidade) foi escolhido mais de uma vez -- "
            "cada coluna do comparativo precisa ser um documento diferente."
        )
    if selecao_periodos.rotulos_duplicados(periodos_labels):
        raise ValueError(
            "rótulos de coluna repetidos (" + ", ".join(periodos_labels) + ") -- dê um nome diferente "
            "para cada coluna (ex.: 2T/2026 e 1S/2026) para não confundir trimestral com semestral."
        )

    admin = admin or {}
    data_geracao = data_geracao or date.today().strftime("%d/%m/%Y")

    empresas_map = {e["codigo"]: e for e in db.listar_empresas(conn)}
    grupo = len(empresas_codigos) > 1

    if grupo:
        for cod in empresas_codigos:
            ativos_cod = {(d["periodo"], d["granularidade"]) for d in db.listar_periodos_detalhado(conn, cod, status="ATIVO")}
            faltando = [
                (p, g) for p, g in zip(periodos, granularidades) if (p, g) not in ativos_cod
            ]
            if faltando:
                faltando_txt = ", ".join(
                    p.strftime("%m/%Y") + (f" ({g})" if g else "") for p, g in faltando
                )
                raise ValueError(
                    f"{cod} não tem período ativo em {faltando_txt} -- remova esse(s) período(s) "
                    f"ou desmarque {cod} da seleção pra gerar o comparativo consolidado."
                )

    campos_kpi = [campo for campo, _ in _METRICAS_FLUXO + _METRICAS_SALDO]

    dados_por_periodo = []
    bp_por_periodo = []
    for periodo, granularidade in zip(periodos, granularidades):
        somas = {campo: 0.0 for campo in campos_kpi}
        for cod in empresas_codigos:
            dados_p, _incluir_resultado = montar_dados_relatorio(
                conn, cod, periodo, periodo_label=str(periodo), granularidade=granularidade,
            )
            for campo in campos_kpi:
                somas[campo] += dados_p[campo]
        dados_por_periodo.append(somas)

        if grupo:
            lancs = db.listar_lancamentos_grupo(conn, periodo, "BP", empresas_codigos, granularidade=granularidade)
            pivot = visao_grupo.montar_pivot_grupo(lancs, empresas_codigos)
            bp_periodo = [
                {"grupo": row["grupo"], "conta": row["conta"], "valor": row["VALOR CONSOLIDADO"]}
                for row in pivot.to_dict("records")
            ]
        else:
            bp_periodo = db.listar_lancamentos(conn, empresas_codigos[0], periodo, "BP", granularidade=granularidade)
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
        # FIX_20260930 (Rafael, capa multi-empresa: "quero a LISTA dos
        # nomes" -- ver gerador_relatorio_comentado._nomes_empresas_grupo,
        # reaproveitado pela capa do Modelo B). Preenchido sempre (nao só
        # quando `grupo`) -- inofensivo com 1 empresa só (o helper da capa
        # ignora a lista nesse caso).
        empresas_nomes=[empresas_map.get(cod, {}).get("nome", cod) for cod in empresas_codigos],
        empresa_nome=nome_empresa,
        cnpj=cnpj,
        cabecalho_relatorio=f"Evolução Financeira · {periodo_range_label}",
        periodos_labels=list(periodos_labels),
        # v0.40.0: auditoria -- documento (periodo + granularidade) que
        # alimentou CADA coluna, na mesma ordem de periodos_labels.
        periodos=list(periodos),
        granularidades=list(granularidades),
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
