# -*- coding: utf-8 -*-
"""
EGC | Notas Fiscais — leitura do manifesto de NF-e (planilha .xlsx que a
contadora baixa da Receita Federal, formato "Fiscal-io": colunas
Num, Tipo, DtEmi, Valor, CFOP, Emissor Nome, Emissor CNPJ/CPF, UF, Chave).

Modulo puro (sem Streamlit, sem banco) -- so pandas/openpyxl -- pra ficar
testavel sem mock nenhum. Quem chama (telas/7_Notas_Fiscais.py) decide o
que fazer com o DataFrame devolvido (gravar em egc.nf_manifesto_import).

Decisao de escopo (25/09/2026): nao reaproveita o parser do EGEN (PDF/XML
avulso, com bug conhecido e nao confirmado em constants.classificar()) --
o formato de entrada aqui e' bem mais simples (planilha ja estruturada da
Receita, 1 linha por nota), entao um parser dedicado, pequeno, e' mais
barato e mais seguro que herdar risco de outro sistema.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd

# Nomes de coluna exatamente como vem da planilha (aba "Fiscal-io" ou
# similar -- ver COLUNAS_ALTERNATIVAS pra outras variacoes ja vistas).
COLUNAS_ESPERADAS = ["Num", "Tipo", "DtEmi", "Valor", "CFOP", "Emissor Nome", "Emissor CNPJ/CPF", "UF", "Chave"]

# Caso a Receita mude o nome de alguma coluna entre exportações -- mapa de
# apelidos conhecidos pra nome canônico. Ampliar aqui se aparecer variação
# nova (nunca silenciosamente admitir uma coluna desconhecida sem log).
ALIASES = {
    "numero": "Num", "num": "Num",
    "tipo": "Tipo",
    "dtemi": "DtEmi", "data emissao": "DtEmi", "data emissão": "DtEmi",
    "valor": "Valor", "valor nf": "Valor", "valor da nota": "Valor",
    "cfop": "CFOP",
    "emissor nome": "Emissor Nome", "fornecedor": "Emissor Nome", "razao social": "Emissor Nome",
    "emissor cnpj/cpf": "Emissor CNPJ/CPF", "cnpj": "Emissor CNPJ/CPF", "cnpj fornecedor": "Emissor CNPJ/CPF",
    "uf": "UF",
    "chave": "Chave", "chave de acesso": "Chave", "chave acesso": "Chave",
    # v0.44.3 -- colunas do export bruto da Receita usadas pra detectar a
    # empresa (Filial = CNPJ do destinatario) e ignorar nota cancelada.
    "filial": "Filial", "can": "Can", "tipodoc": "TipoDoc",
    "ano-mês": "Ano-Mês", "ano-mes": "Ano-Mês",
}


class ManifestoInvalido(Exception):
    """Planilha sem as colunas mínimas pra decidir o que fazer (log em
    egc.eventos_sistema, origem='notas_fiscais', pelo chamador)."""


def _normalizar_nome_coluna(nome: str) -> str:
    return ALIASES.get(str(nome).strip().lower(), str(nome).strip())


def decodificar_chave_acesso(chave: str) -> Optional[dict]:
    """
    Decodifica os 44 dígitos da chave de acesso da NF-e por posição
    (layout oficial SEFAZ, confirmado batendo com dado real do Sienge em
    25/09/2026 -- ver EGC 00-handoff.md seção 62):

        UF(2) AAMM(4) CNPJ(14) modelo(2) série(3) número(9) tpEmis(1) cNF(8) DV(1)

    Retorna None se não tiver 44 dígitos (nunca lança -- chave ausente ou
    malformada vira "sem confirmação extra", não erro fatal de import).
    """
    if not chave:
        return None
    digitos = re.sub(r"\D", "", str(chave))
    if len(digitos) != 44:
        return None
    return {
        "uf": digitos[0:2],
        "aamm": digitos[2:6],
        "cnpj_emissor": digitos[6:20],
        "modelo": digitos[20:22],
        "serie": digitos[22:25],
        "numero": digitos[25:34].lstrip("0") or "0",
        "tipo_emissao": digitos[34:35],
        "codigo_numerico": digitos[35:43],
        "dv": digitos[43:44],
    }


def normalizar_cnpj(valor) -> str:
    """So os dígitos -- pra comparar CNPJ do manifesto (as vezes vem só
    número) com o do Sienge (as vezes vem formatado "00.000.000/0000-00")
    sem depender de formatação igual dos dois lados."""
    return re.sub(r"\D", "", str(valor or ""))


def normalizar_numero_nota(valor) -> str:
    """Remove zeros à esquerda e qualquer caractere não numérico -- o
    Sienge guarda documentNumber como texto livre ("10448"), a planilha
    guarda Num como inteiro (10448) ou às vezes texto com zeros
    ("00010448"); depois de normalizado os dois viram a mesma string."""
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos.lstrip("0") or "0"


_FORMATOS_DATA = ("%Y.%m.%d", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%Y/%m/%d")


def parse_data_emissao(valor):
    """Converte a data de emissao da planilha em datetime.date (ou None).

    v0.44.3: a Receita exporta "AAAA.MM.DD" (ex.: "2026.07.06"). O parse
    antigo (`pd.to_datetime(..., dayfirst=True)`) lia isso como 2026-06-07 /
    01-2026 e sugeria o periodo errado. Aqui cada formato e' explicito --
    nunca adivinha dia/mes. Aceita tambem datetime/Timestamp e texto com
    hora ("2026-07-06 10:30:00")."""
    import datetime as _dt
    if valor is None:
        return None
    if isinstance(valor, _dt.datetime):
        return valor.date()
    if isinstance(valor, _dt.date):
        return valor
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    texto = str(valor).strip()
    if not texto:
        return None
    texto = texto.split(" ")[0].split("T")[0]
    for fmt in _FORMATOS_DATA:
        try:
            return _dt.datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None


def competencia_da_linha(ano_mes, data_emissao) -> Optional[str]:
    """"MM/AAAA" a partir de "AAAA.MM" (coluna Ano-Mês); sem ela, do mes de emissao."""
    import datetime as _dt
    if ano_mes is not None and not (isinstance(ano_mes, float) and pd.isna(ano_mes)):
        m = re.match(r"^\s*(\d{4})[.\-/](\d{2})\s*$", str(ano_mes))
        if m and 1 <= int(m.group(2)) <= 12:
            return f"{m.group(2)}/{m.group(1)}"
    if isinstance(data_emissao, _dt.date):
        return f"{data_emissao.month:02d}/{data_emissao.year}"
    return None


def raiz_cnpj(valor) -> str:
    """Primeiros 8 digitos do CNPJ (a raiz e' igual entre matriz e filiais)."""
    d = normalizar_cnpj(valor)
    return d[:8] if len(d) >= 14 else ""


def ler_manifesto_xlsx(caminho_ou_buffer) -> pd.DataFrame:
    """
    Lê a planilha e devolve um DataFrame já normalizado, 1 linha por nota,
    com as colunas extras decodificadas da chave (cnpj_emissor_chave,
    modelo, serie, numero_chave) -- usadas como confirmação extra no
    matching, nunca como único critério (só ~14% das notas do lado Sienge
    trazem a chave preenchida pra cruzar, mas a chave da planilha em si é
    sempre 100% confiável, então decodificar sempre é barato e ajuda a
    auditar).

    Levanta ManifestoInvalido se faltar alguma coluna essencial (Num,
    Valor, Emissor CNPJ/CPF) -- essas 3 são o mínimo pro matching
    funcionar; as outras (Tipo, DtEmi, CFOP, UF, Chave) são opcionais.
    """
    df = pd.read_excel(caminho_ou_buffer, dtype=str)
    df.columns = [_normalizar_nome_coluna(c) for c in df.columns]

    essenciais = ["Num", "Valor", "Emissor CNPJ/CPF"]
    faltando = [c for c in essenciais if c not in df.columns]
    if faltando:
        raise ManifestoInvalido(
            f"Planilha sem a(s) coluna(s) essencial(is): {faltando}. "
            f"Colunas encontradas: {list(df.columns)}"
        )

    for opcional in ["Tipo", "DtEmi", "CFOP", "Emissor Nome", "UF", "Chave"]:
        if opcional not in df.columns:
            df[opcional] = None
    for opcional in ["Filial", "Can", "TipoDoc", "Ano-Mês"]:
        if opcional not in df.columns:
            df[opcional] = None

    df["_numero_normalizado"] = df["Num"].apply(normalizar_numero_nota)
    df["_cnpj_normalizado"] = df["Emissor CNPJ/CPF"].apply(normalizar_cnpj)
    df["_valor_float"] = pd.to_numeric(
        df["Valor"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        if df["Valor"].astype(str).str.contains(",").any()
        else df["Valor"],
        errors="coerce",
    )

    df["_data_emissao"] = df["DtEmi"].apply(parse_data_emissao)
    df["_empresa_raiz"] = df["Filial"].apply(raiz_cnpj)
    # Competencia = "Ano-Mês" da Receita (mes de AUTORIZACAO/captura, e' como a
    # planilha de cada mes vem fatiada: nota emitida em 31/07 e autorizada em
    # 03/08 vem no arquivo de agosto). Sem a coluna, cai no mes de emissao.
    df["_competencia"] = [
        competencia_da_linha(am, de) for am, de in zip(df["Ano-Mês"], df["_data_emissao"])
    ]
    # cancelada: coluna "Can" = X, ou o 2o "Status" (pandas renomeia pra
    # "Status.1") diz "Cancelamento ... homologado"
    colunas_status = [c for c in df.columns if str(c).startswith("Status")]
    texto_status = df[colunas_status].fillna("").astype(str).agg(" ".join, axis=1) if colunas_status else ""
    cancelada_por_status = (
        texto_status.str.contains("cancelamento", case=False, na=False)
        if len(colunas_status) else pd.Series(False, index=df.index)
    )
    df["_cancelada"] = (df["Can"].fillna("").astype(str).str.strip().str.upper() == "X") | cancelada_por_status

    decodificadas = df["Chave"].apply(decodificar_chave_acesso)
    df["_chave_cnpj"] = decodificadas.apply(lambda d: d["cnpj_emissor"] if d else None)
    df["_chave_modelo"] = decodificadas.apply(lambda d: d["modelo"] if d else None)
    df["_chave_serie"] = decodificadas.apply(lambda d: d["serie"] if d else None)
    df["_chave_numero"] = decodificadas.apply(lambda d: d["numero"] if d else None)
    df["_chave_aamm"] = decodificadas.apply(lambda d: d["aamm"] if d else None)

    return df.reset_index(drop=True)


def sugerir_periodo_referencia(df: pd.DataFrame) -> Optional[str]:
    """Sugestao de "MM/AAAA" pra pre-preencher o campo "Período de
    referência" da tela -- NUNCA decide sozinho, so' pre-preenche; a
    contadora sempre confirma/troca antes de "Rodar conferência".

    FIX_20260928 (Rafael perguntou: "esse período de referência melhor
    setar manual ou conseguimos puxar isso da planilha?"): a planilha da
    Receita NÃO tem 1 campo único de período -- cada LINHA (nota) tem sua
    própria data de emissão (DtEmi), e na prática um mesmo arquivo pode
    misturar competências (nota emitida em julho, capturada só em agosto
    numa remessa "atrasada"). Por isso não dá pra simplesmente "puxar da
    planilha" como um fato -- mas dá pra SUGERIR o mês/ano que aparece na
    MAIORIA das notas (moda, não a primeira/última linha, que pode ser
    só 1 nota fora do padrão) e deixar o campo editável do mesmo jeito.
    Sem fazer isso, a contadora digita a mesma coisa toda vez à mão.

    Fonte primária: DtEmi (coluna opcional, pode vir vazia). Fallback:
    aamm decodificado da Chave de acesso (formato oficial SEFAZ, sempre
    confiável quando a chave existe -- ver decodificar_chave_acesso).
    Devolve None se não der pra determinar nada em nenhuma das 2 fontes
    (nunca inventa um período do nada)."""
    if "_competencia" in df.columns:
        comp = df["_competencia"].dropna()
        if not comp.empty:
            return comp.mode().iloc[0]
    datas = pd.Series(dtype="object")
    if "_data_emissao" in df.columns:
        datas = df["_data_emissao"].dropna()
    elif "DtEmi" in df.columns:
        datas = df["DtEmi"].apply(parse_data_emissao).dropna()
    if not datas.empty:
        competencias = datas.apply(lambda d: f"{d.month:02d}/{d.year}")
        return competencias.mode().iloc[0]

    if "Ano-Mês" in df.columns:
        am = df["Ano-Mês"].dropna().astype(str).str.extract(r"^(\d{4})[.\-/](\d{2})$").dropna()
        if not am.empty:
            return (am[1] + "/" + am[0]).mode().iloc[0]

    if "Chave" in df.columns:
        aamms = df["Chave"].apply(decodificar_chave_acesso).apply(lambda d: d["aamm"] if d else None).dropna()
        aamms = aamms[aamms.str.len() == 4]
        if not aamms.empty:
            mais_comum = aamms.mode().iloc[0]  # "AAMM" (2 digitos de ano + 2 de mes)
            return f"{mais_comum[2:4]}/20{mais_comum[0:2]}"

    return None


MOTIVO_CANCELADA = "Cancelada"
MOTIVO_ENTRADA = "Entrada (devolução/retorno)"


def filtrar_manifesto(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Tira da conferencia o que nao gera titulo a pagar:

      - CANCELADAS (coluna Can = X ou status "Cancelamento ... homologado");
      - ENTRADA (TipoDoc = Entrada): nos dados reais de 07/2026 as 7 sao
        devolucoes de venda, retorno de bem locado/remessa e "outras
        entradas" -- sao notas que o proprio fornecedor emite como entrada,
        nao compra nossa; a contadora tirava todas a mao.

    Devolve (df_filtrado, contagens, ignoradas). `ignoradas` = DataFrame das
    notas retiradas (colunas: numero_nota, data_emissao, valor, cfop,
    fornecedor_nome, fornecedor_cnpj, cnpj_normalizado, tipo_doc, natureza,
    motivo, competencia) -- gravado junto da conferencia e listado na tela e
    na planilha pra a contadora confirmar que nada saiu por engano."""
    idx = df.index
    canceladas = df["_cancelada"].fillna(False).astype(bool) if "_cancelada" in df.columns else pd.Series(False, index=idx)
    entradas = (df["TipoDoc"].fillna("").astype(str).str.strip().str.lower().str.startswith("entrada")
                if "TipoDoc" in df.columns else pd.Series(False, index=idx))
    entradas = entradas & ~canceladas
    ignorar = canceladas | entradas
    contagens = {"canceladas": int(canceladas.sum()), "entradas": int(entradas.sum())}

    ign = df[ignorar]
    natureza = ign["Natureza"] if "Natureza" in ign.columns else pd.Series(None, index=ign.index)
    ignoradas = pd.DataFrame({
        "numero_nota": ign["Num"].astype(str).str.lstrip("0").replace("", "0"),
        "data_emissao": ign["_data_emissao"] if "_data_emissao" in ign.columns else None,
        "valor": ign["_valor_float"],
        "cfop": ign["CFOP"],
        "fornecedor_nome": ign["Emissor Nome"],
        "fornecedor_cnpj": ign["Emissor CNPJ/CPF"],
        "cnpj_normalizado": ign["_cnpj_normalizado"],
        "tipo_doc": ign["TipoDoc"] if "TipoDoc" in ign.columns else None,
        "natureza": natureza,
        "motivo": [MOTIVO_CANCELADA if c else MOTIVO_ENTRADA for c in canceladas[ignorar]],
        "competencia": ign["_competencia"] if "_competencia" in ign.columns else None,
    }).reset_index(drop=True)
    return df[~ignorar].reset_index(drop=True), contagens, ignoradas


def dividir_por_competencia(df: pd.DataFrame, ignoradas: Optional[pd.DataFrame] = None) -> list:
    """Quebra o manifesto por competencia (Ano-Mês). Um arquivo com 2 meses
    (ex.: "SETTE 07-08") vira 2 conferencias separadas, cada uma com o seu
    periodo -- antes o arquivo todo ficava com UM periodo e metade das notas
    com o rotulo errado. Devolve [(competencia, df_do_mes, ignoradas_do_mes)]
    em ordem cronologica; competencia None = linhas sem como determinar."""
    def ordem(c):
        return (c is None, c[3:] + c[:2] if c else "")
    comps = sorted({c if isinstance(c, str) else None for c in df["_competencia"]}, key=ordem)
    saida = []
    for c in comps:
        mask = df["_competencia"] == c if c is not None else df["_competencia"].isna()
        ign = None
        if ignoradas is not None and "competencia" in ignoradas.columns:
            ign = ignoradas[(ignoradas["competencia"] == c) if c is not None else ignoradas["competencia"].isna()].reset_index(drop=True)
        saida.append((c, df[mask].reset_index(drop=True), ign))
    return saida


def detectar_empresa_manifesto(df: pd.DataFrame, empresas_fixas) -> Optional[str]:
    """Codigo da empresa dona do manifesto, pela raiz do CNPJ da coluna
    Filial (destinatario). So' devolve quando TODAS as notas com Filial
    apontam pra UMA unica empresa conhecida; misto/desconhecido -> None
    (a tela cai pra escolha manual). `empresas_fixas` = [(codigo, nome, cnpj)]."""
    if "_empresa_raiz" not in df.columns:
        return None
    raizes = {r for r in df["_empresa_raiz"].dropna() if r}
    if len(raizes) != 1:
        return None
    raiz = next(iter(raizes))
    achadas = [cod for cod, _nome, cnpj in empresas_fixas if raiz_cnpj(cnpj) == raiz]
    return achadas[0] if len(achadas) == 1 else None


def analisar_upload(conteudo: bytes, empresas_fixas) -> dict:
    """Le o arquivo recem-enviado e devolve o que a tela precisa pra
    pre-preencher e validar ANTES de rodar a conferencia:
      periodo (MM/AAAA sugerido), empresa (codigo detectado pela Filial ou
      None), raizes (raizes de CNPJ distintas em Filial), total,
      canceladas, entradas, erro (texto, quando a planilha nao abre).
    Nunca levanta -- planilha invalida vira {'erro': ...}."""
    import io
    try:
        df = ler_manifesto_xlsx(io.BytesIO(conteudo))
    except Exception as exc:  # planilha invalida -> o erro de verdade aparece no "Rodar"
        return {"erro": str(exc), "periodo": None, "empresa": None, "raizes": [],
                "total": 0, "canceladas": 0, "entradas": 0, "competencias": {}}
    filtrado, contagens, _ign = filtrar_manifesto(df)
    base = filtrado if len(filtrado) else df
    return {
        "erro": None,
        "periodo": sugerir_periodo_referencia(base),
        "empresa": detectar_empresa_manifesto(base, empresas_fixas),
        "raizes": sorted({r for r in base["_empresa_raiz"].dropna() if r}),
        "total": len(filtrado),
        "competencias": {c: len(d) for c, d, _i in dividir_por_competencia(filtrado) if c} if len(filtrado) else {},
        **contagens,
    }
