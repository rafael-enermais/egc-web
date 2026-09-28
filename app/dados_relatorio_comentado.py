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

from datetime import date
from typing import Optional

import pandas as pd

import db
import indicadores

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
_HIERARQUIA_BP = {
    "DISPONIVEL": ["DEPOSITOS BANCARIOS A VISTA", "APLICACOES DE LIQUIDEZ IMEDIATA"],
    "CLIENTES": ["DUPLICATAS A RECEBER"],
    "INSTITUICOES FINANCEIRAS": ["EMPRESTIMOS", "FINANCIAMENTOS"],
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
    grupo)."""
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


def _montar_despesas_admin_itens(itens_admin: list, despesas_administrativas: float) -> list:
    """[(nome, valor_positivo, pct_0_a_100), ...] ordenado desc pelo
    valor -- formato que gerador_relatorio_comentado espera pro grafico
    de ranking (pagina Despesas). Vem de db.listar_despesas_admin_itens
    (Fase 2/bloco 12) -- lista vazia se a migracao ainda nao rodou no
    banco ou o periodo nao tiver essa extracao (period antigo, antes do
    parser existir) -- pagina ja tem teste cobrindo lista vazia."""
    out = []
    for nome, valor in itens_admin:
        valor_abs = abs(float(valor))
        pct = (valor_abs / despesas_administrativas * 100) if despesas_administrativas else 0.0
        out.append((nome, valor_abs, pct))
    out.sort(key=lambda t: t[1], reverse=True)
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


def montar_dados_relatorio(
    conn,
    empresa_codigo: str,
    periodo: date,
    periodo_label: str,
    periodo_extenso: str = "",
    data_geracao: Optional[str] = None,
    admin: Optional[dict] = None,
) -> tuple[dict, bool]:
    """Monta (dados, incluir_pagina_resultado) prontos pra
    gerador_relatorio_comentado.gerar_pdf_completo(dados, caminho,
    incluir_pagina_resultado).

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

    empresas = {e["codigo"]: e for e in db.listar_empresas(conn)}
    empresa = empresas.get(empresa_codigo, {})

    bp_periodo = db.listar_lancamentos(conn, empresa_codigo, periodo, "BP")
    dre_periodo = db.listar_lancamentos(conn, empresa_codigo, periodo, "DRE")
    bp_map = _mapa(bp_periodo)
    dre_map = _mapa(dre_periodo)

    bp_hist = db.listar_historico_grupo(conn, empresa_codigo, "BP")
    dre_hist = db.listar_historico_grupo(conn, empresa_codigo, "DRE")
    indic_df = indicadores.calcular_indicadores(bp_hist, dre_hist)

    itens_admin = db.listar_despesas_admin_itens(conn, empresa_codigo, periodo)

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
    periodos_ativos = db.listar_periodos(conn, empresa_codigo, status="ATIVO")
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

    dados = dict(
        empresa_codigo=empresa_codigo,
        empresa_nome=empresa.get("nome", empresa_codigo),
        cnpj=empresa.get("cnpj", ""),
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
    if tem_csll_irpj:
        dados["csll_irpj"] = csll_irpj

    return dados, tem_csll_irpj
