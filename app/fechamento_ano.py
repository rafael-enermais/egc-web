# -*- coding: utf-8 -*-
"""
v0.54.0 -- FECHAMENTO DO ANO: grade de fechamentos (1T-4T, 1S, 2S, 9M, Anual) por empresa.

Logica PURA (sem Streamlit e sem banco): recebe o que existe gravado e devolve o estado de
cada celula da grade, as sugestoes de correcao de rotulo e as conferencias entre periodos.

Decisoes do Rafael (27/10/2026):
  * Cada fechamento e' um documento (empresa, periodo_fim, granularidade) -- 9 meses acumulados
    ("9meses") e' uma granularidade como qualquer outra; a contadora escolhe no import.
  * A grade mostra o que JA' foi importado e o que FALTA subir; o upload preenche lacunas.
  * Conferencias entre periodos (1T + 2T = 1S ...) sao SO' AVISO: nunca travam nem bloqueiam
    relatorio (o numero pode ter vindo errado da contabilidade).
  * Nada aqui calcula/deriva numero novo: so' compara valores que ja' estao gravados.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

# ----------------------------------------------------------------------------
#  Calendario de fechamentos do ano
# ----------------------------------------------------------------------------

ESTADO_COMPLETO = "completo"      # BP e DRE ativos
ESTADO_SO_DRE = "so_dre"          # falta o BP
ESTADO_SO_BP = "so_bp"            # falta o DRE
ESTADO_AUSENTE = "ausente"        # nada gravado

ROTULO_ESTADO = {
    ESTADO_COMPLETO: "BP + DRE",
    ESTADO_SO_DRE: "falta BP",
    ESTADO_SO_BP: "falta DRE",
    ESTADO_AUSENTE: "falta subir",
}

# Contas do DRE conferidas entre periodos (nomes normalizados do parser, valor unico por documento).
CONTAS_CONFERIDAS = (
    "RECEITA OPERACIONAL BRUTA",
    "RECEITA OPERACIONAL LIQUIDA",
    "LUCRO BRUTO",
    "LUCRO LIQUIDO DO EXERCICIO",
)

# Tolerancia da conferencia: o maior entre R$ 1,00 e 0,1% do valor do periodo maior.
TOLERANCIA_ABS = 1.00
TOLERANCIA_REL = 0.001


@dataclass(frozen=True)
class Slot:
    chave: str          # "1T", "2T", "3T", "4T", "1S", "2S", "9M", "ANUAL"
    rotulo: str         # "1º trimestre", ...
    fim: date
    granularidade: str  # "trimestral" | "semestral" | "9meses" | "anual"

    @property
    def par(self) -> tuple:
        return (self.fim, self.granularidade)


def _fim_mes(ano: int, mes: int) -> date:
    return date(ano, mes, 31 if mes in (1, 3, 5, 7, 8, 10, 12) else 30)


def slots_do_ano(ano: int) -> list:
    """Os 8 fechamentos possiveis do ano, na ordem em que aparecem na grade."""
    return [
        Slot("1T", "1º trimestre", _fim_mes(ano, 3), "trimestral"),
        Slot("2T", "2º trimestre", _fim_mes(ano, 6), "trimestral"),
        Slot("3T", "3º trimestre", _fim_mes(ano, 9), "trimestral"),
        Slot("4T", "4º trimestre", _fim_mes(ano, 12), "trimestral"),
        Slot("1S", "1º semestre", _fim_mes(ano, 6), "semestral"),
        Slot("2S", "2º semestre", _fim_mes(ano, 12), "semestral"),
        Slot("9M", "Acumulado 9 meses", _fim_mes(ano, 9), "9meses"),
        Slot("ANUAL", "Exercício (anual)", _fim_mes(ano, 12), "anual"),
    ]


# Quem compoe quem (todas as partes e o todo precisam ter DRE gravado pra conferir).
COMPOSICOES = (
    ("1S", ("1T", "2T")),
    ("2S", ("3T", "4T")),
    ("9M", ("1S", "3T")),
    ("9M", ("1T", "2T", "3T")),
    ("ANUAL", ("1S", "2S")),
    ("ANUAL", ("9M", "4T")),
)

# Atalhos de relatorio: nome -> chaves de slot (o relatorio so' usa os que estao completos).
PRESETS_RELATORIO = {
    "Os 4 trimestres do ano": ("1T", "2T", "3T", "4T"),
    "Os 2 semestres do ano": ("1S", "2S"),
    "Trimestres até hoje (acumulado)": ("1T", "2T", "3T"),
    "1º semestre x 9 meses x ano": ("1S", "9M", "ANUAL"),
}


# ----------------------------------------------------------------------------
#  Estado do que esta gravado
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Doc:
    """1 documento ATIVO gravado: (empresa, periodo_fim, granularidade gravada)."""
    empresa: str
    fim: date
    granularidade: str
    tem_bp: bool
    tem_dre: bool
    inicio: Optional[date] = None   # periodo_inicio declarado no PDF do DRE (None = nao declarado)


def docs_de_linhas(linhas: Iterable[dict]) -> list:
    """Converte as linhas de db.listar_documentos_grade em Doc."""
    out = []
    for r in linhas:
        out.append(Doc(
            empresa=r["empresa_codigo"], fim=r["periodo"], granularidade=r.get("granularidade") or "",
            tem_bp=bool(r.get("tem_bp")), tem_dre=bool(r.get("tem_dre")), inicio=r.get("inicio_dre"),
        ))
    return out


def estado_do_doc(doc: Optional[Doc]) -> str:
    if doc is None:
        return ESTADO_AUSENTE
    if doc.tem_bp and doc.tem_dre:
        return ESTADO_COMPLETO
    if doc.tem_dre:
        return ESTADO_SO_DRE
    if doc.tem_bp:
        return ESTADO_SO_BP
    return ESTADO_AUSENTE


def granularidade_declarada(doc: Doc) -> str:
    """Granularidade pelo INTERVALO que o proprio PDF declarou ('' se nao declarou)."""
    if not doc.inicio:
        return ""
    # import tardio: o parser puxa dependencias de PDF que esta camada nao precisa no import
    from parser_egc import calcular_granularidade

    return calcular_granularidade(doc.inicio.strftime("%d/%m/%Y"), doc.fim.strftime("%d/%m/%Y"))


@dataclass(frozen=True)
class Celula:
    empresa: str
    slot: Slot
    estado: str
    doc: Optional[Doc] = None
    suspeita: str = ""   # texto quando o rotulo gravado diverge do intervalo declarado no PDF


@dataclass(frozen=True)
class Sugestao:
    """Documento cujo rotulo gravado diverge do intervalo que o PDF declarou."""
    empresa: str
    fim: date
    gravada: str
    declarada: str
    inicio: date


def montar_grade(ano: int, empresas: Iterable[str], docs: Iterable[Doc]) -> dict:
    """{(empresa, slot.chave): Celula} para as empresas pedidas. Um documento so' ocupa a celula
    cuja (fim, granularidade) bate EXATAMENTE (nunca adivinha); se o rotulo gravado diverge do
    intervalo declarado no PDF, a celula ganha `suspeita` e a correcao aparece em `sugestoes`."""
    por_chave = {(d.empresa, d.fim, d.granularidade): d for d in docs}
    grade = {}
    for emp in empresas:
        for slot in slots_do_ano(ano):
            doc = por_chave.get((emp, slot.fim, slot.granularidade))
            suspeita = ""
            if doc is not None:
                declarada = granularidade_declarada(doc)
                if declarada and declarada != doc.granularidade and declarada != "outra":
                    suspeita = (
                        f"gravado como '{doc.granularidade}', mas o PDF declara "
                        f"{doc.inicio.strftime('%d/%m/%Y')} a {doc.fim.strftime('%d/%m/%Y')} ('{declarada}')"
                    )
            grade[(emp, slot.chave)] = Celula(emp, slot, estado_do_doc(doc), doc, suspeita)
    return grade


def sugestoes_correcao(ano: int, empresas: Iterable[str], docs: Iterable[Doc]) -> list:
    """Documentos do ano (por periodo_fim) cujo rotulo diverge do intervalo declarado no PDF,
    onde a granularidade DECLARADA e' um fechamento conhecido (nunca sugere 'outra' nem '')."""
    conhecidas = {"mensal", "bimestral", "trimestral", "semestral", "9meses", "anual"}
    emps = set(empresas)
    out = []
    for d in docs:
        if d.empresa not in emps or d.fim.year != ano or not d.tem_dre:
            continue
        declarada = granularidade_declarada(d)
        if declarada in conhecidas and declarada != d.granularidade:
            out.append(Sugestao(d.empresa, d.fim, d.granularidade, declarada, d.inicio))
    return sorted(out, key=lambda s: (s.empresa, s.fim))


def resumo_por_empresa(grade: dict, empresas: Iterable[str]) -> dict:
    """{empresa: {"completos": n, "faltando": n, "parciais": n}} (para o cabecalho da grade)."""
    res = {}
    for emp in empresas:
        cels = [c for (e, _k), c in grade.items() if e == emp]
        res[emp] = {
            "completos": sum(1 for c in cels if c.estado == ESTADO_COMPLETO),
            "parciais": sum(1 for c in cels if c.estado in (ESTADO_SO_DRE, ESTADO_SO_BP)),
            "faltando": sum(1 for c in cels if c.estado == ESTADO_AUSENTE),
        }
    return res


# ----------------------------------------------------------------------------
#  Conferencias entre periodos (SO' AVISO)
# ----------------------------------------------------------------------------

@dataclass
class Aviso:
    empresa: str
    ano: int
    todo: str            # chave do slot que deveria ser a soma (ex. "1S")
    partes: tuple        # chaves das partes (ex. ("1T", "2T"))
    conta: str
    soma: float
    declarado: float
    diferenca: float
    mensagem: str = field(default="", compare=False)


def _brl(v: float) -> str:
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {s}" if v < 0 else f"R$ {s}"


def conferir_composicoes(ano: int, valores: dict) -> list:
    """`valores`: {(empresa, slot_chave): {conta: valor}} do DRE ATIVO de cada fechamento.
    Para cada composicao em que o todo E todas as partes existem, compara a soma das partes com o
    todo nas CONTAS_CONFERIDAS e devolve um Aviso por divergencia acima da tolerancia.
    So' compara contas presentes nos dois lados. Nunca levanta nem bloqueia -- e' aviso."""
    empresas = sorted({e for (e, _k) in valores})
    rotulo = {s.chave: s.rotulo for s in slots_do_ano(ano)}
    avisos = []
    for emp in empresas:
        for todo, partes in COMPOSICOES:
            v_todo = valores.get((emp, todo))
            v_partes = [valores.get((emp, p)) for p in partes]
            if not v_todo or any(not vp for vp in v_partes):
                continue
            for conta in CONTAS_CONFERIDAS:
                if conta not in v_todo or any(conta not in vp for vp in v_partes):
                    continue
                soma = round(sum(vp[conta] for vp in v_partes), 2)
                decl = round(v_todo[conta], 2)
                dif = round(soma - decl, 2)
                tol = max(TOLERANCIA_ABS, TOLERANCIA_REL * max(abs(soma), abs(decl)))
                if abs(dif) > tol:
                    txt = (
                        f"{emp} {ano} — {conta.capitalize()}: {' + '.join(partes)} = {_brl(soma)}, "
                        f"mas {todo} ({rotulo[todo]}) declara {_brl(decl)} (diferença {_brl(dif)}). "
                        "Pode ser erro de classificação, de importação ou da contabilidade — é só aviso, "
                        "não bloqueia nada."
                    )
                    avisos.append(Aviso(emp, ano, todo, tuple(partes), conta, soma, decl, dif, txt))
    return avisos


def valores_de_linhas(ano: int, linhas: Iterable[dict]) -> dict:
    """Converte as linhas de db.listar_valores_dre_documentos em {(empresa, slot_chave): {conta: valor}}.
    Ignora documentos que nao casam EXATAMENTE com um fechamento do ano e contas com mais de 1 linha
    no documento (dado ambiguo: nao compara)."""
    por_par = {s.par: s.chave for s in slots_do_ano(ano)}
    out: dict = {}
    for r in linhas:
        chave = por_par.get((r["periodo"], r.get("granularidade") or ""))
        if chave is None or int(r.get("n") or 1) != 1 or r.get("valor") is None:
            continue
        out.setdefault((r["empresa_codigo"], chave), {})[r["conta"]] = float(r["valor"])
    return out


# ----------------------------------------------------------------------------
#  Atalhos de relatorio
# ----------------------------------------------------------------------------

def pares_do_preset(nome: str, ano: int, empresas: Iterable[str], grade: dict) -> tuple:
    """(pares, faltando): pares (fim, granularidade) do preset em que TODAS as empresas pedidas
    tem BP+DRE; `faltando` lista "empresa slot" do que impede incluir o fechamento."""
    chaves = PRESETS_RELATORIO[nome]
    slots = {s.chave: s for s in slots_do_ano(ano)}
    emps = list(empresas)
    pares, faltando = [], []
    for k in chaves:
        sem = [e for e in emps if grade.get((e, k)) is None or grade[(e, k)].estado != ESTADO_COMPLETO]
        if sem:
            faltando.extend(f"{e} {k}" for e in sem)
        else:
            pares.append(slots[k].par)
    return pares, faltando
