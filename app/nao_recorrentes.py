# -*- coding: utf-8 -*-
"""
EGC | Itens NAO RECORRENTES do EBITDA Ajustado (v0.48.0, pedido do Rafael 06-07/10/2026).

O QUE E': a lista de itens "pontuais" (rescisoes, honorarios juridicos pontuais, multas,
equivalencia patrimonial...) que a administracao quer somar (ou subtrair) ao EBITDA contabil
para chegar ao EBITDA AJUSTADO. Fica gravada POR empresa + periodo + granularidade (a identidade
de um documento do app -- nunca se mistura trimestral com semestral).

REGRAS (decididas com o Rafael):
  1. Nada e' classificado sozinho. A regra automatica (conta da DRE -> categoria) so' SUGERE; a pessoa
     adiciona item por item e a lista so' vale no relatorio depois de CONFIRMADA.
  2. Alterar qualquer item DEPOIS de confirmar derruba a confirmacao (assinatura diferente) -- o
     relatorio volta a mostrar so' ate o EBITDA contabil ate nova confirmacao.
  3. Nunca apaga: "remover" so' marca ativo=false; tudo (criar/editar/remover/restaurar/confirmar)
     vai pra trilha de auditoria (nao_recorrente_historico) com antes/depois, quem e quando.
  4. Sem lista confirmada (ou sem a tabela do bloco 25) -> o relatorio NAO inventa ajuste: mostra
     so' a reconciliacao ate o EBITDA contabil. Confirmar uma lista VAZIA e' valido ("revisei: nao ha
     itens nao recorrentes") e e' diferente de "ainda nao revisado".
  5. Consolidado (2+ empresas): so' ajusta se TODAS as empresas do periodo estiverem confirmadas.

Convencao de sinal: `valor` e' sempre >= 0 (modulo); `sinal` = +1 soma ao EBITDA (despesa/perda
pontual que reduziu o resultado) ou -1 subtrai (ganho pontual que aumentou o resultado).

Este modulo nao importa Streamlit (testavel com cursor mockado, igual db.py).
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import date
from typing import Optional

# Categorias AMPLAS (a contadora nao digita nome livre de conta: escolhe uma categoria; o detalhe vai
# em "descricao"). Ordem = ordem de exibicao no relatorio.
CATEGORIAS = [
    "Rescisões e indenizações",
    "Jurídico pontual",
    "Multas e penalidades",
    "Equivalência patrimonial",
    "Baixas e perdas de ativos",
    "Reestruturação",
    "Outros não recorrentes",
]

# Regras de SUGESTAO (nunca aplicadas sozinhas): trecho do nome da conta (sem acento, maiusculo)
# -> categoria. So' olham contas que ficam ACIMA da linha do EBITDA (itens das despesas
# administrativas e a equivalencia patrimonial); contas de resultado financeiro/tributario abaixo da
# linha (ex.: Multas Indedutiveis dentro de DESPESAS FINANCEIRAS) ja' estao fora do EBITDA e NAO
# podem ser somadas de novo.
REGRAS_SUGESTAO = [
    (("INDENIZ", "AVISO PREVIO", "RESCIS"), "Rescisões e indenizações"),
    (("HONORARIOS ADVOCAT", "ADVOCAT", "JURIDIC"), "Jurídico pontual"),
    (("MULTA",), "Multas e penalidades"),
    (("EQUIVALENCIA PATRIMONIAL",), "Equivalência patrimonial"),
]

_COLUNAS = ("id", "empresa_codigo", "periodo", "granularidade", "categoria", "descricao", "contas_dre",
            "valor", "sinal", "justificativa", "documento", "sugerido_regra", "ativo",
            "criado_por", "criado_em", "atualizado_por", "atualizado_em")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).upper().strip()


def valor_assinado(item: dict) -> float:
    return float(item["valor"]) * int(item.get("sinal", 1))


def assinatura_lista(itens: list[dict]) -> str:
    """Impressao digital da lista ATIVA (o que o relatorio vai usar). Muda se qualquer item
    for criado, editado (categoria/valor/sinal/descricao) ou removido."""
    base = sorted(
        (int(i["id"]), i["categoria"], f"{float(i['valor']):.2f}", int(i.get("sinal", 1)), (i.get("descricao") or "").strip())
        for i in itens if i.get("ativo", True)
    )
    return hashlib.sha1(json.dumps(base, ensure_ascii=False).encode("utf-8")).hexdigest()


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _historico(cur, empresa, periodo, gran, item_id, acao, antes, depois, usuario):
    cur.execute(
        """INSERT INTO egc.nao_recorrente_historico
               (empresa_codigo, periodo, granularidade, item_id, acao, antes, depois, por)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (empresa, periodo, gran or "", item_id, acao,
         json.dumps(antes, default=str, ensure_ascii=False) if antes is not None else None,
         json.dumps(depois, default=str, ensure_ascii=False) if depois is not None else None,
         usuario),
    )


def tabelas_existem(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('egc.nao_recorrente') IS NOT NULL AND "
                    "to_regclass('egc.nao_recorrente_confirmacao') IS NOT NULL AND "
                    "to_regclass('egc.nao_recorrente_historico') IS NOT NULL")
        return bool(cur.fetchone()[0])


# ----------------------------------------------------------------------------- chave geral (v0.48.2)
# "Desativar" o EBITDA Ajustado nos relatorios (pedido do Rafael: a contabilidade ainda esta decidindo o criterio).
# A chave e' GLOBAL e fica gravada no banco. PADRAO = DESATIVADO: sem a tabela, sem a linha ou com qualquer erro de
# banco, os relatorios saem SEM a pagina "EBITDA Ajustado". So' liga quando alguem liga de proposito na tela
# "Nao Recorrentes". Desligar nao apaga nada (itens, confirmacoes e historico continuam guardados).
CONFIG_CHAVE_EBITDA_AJUSTADO = "ebitda_ajustado_ativo"


def config_existe(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('egc.nao_recorrente_config') IS NOT NULL")
            return bool(cur.fetchone()[0])
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def ebitda_ajustado_ativo(conn) -> bool:
    """True so' se a chave foi LIGADA de proposito. Qualquer problema (tabela ausente, erro) = False."""
    try:
        if not config_existe(conn):
            return False
        with conn.cursor() as cur:
            cur.execute("SELECT valor FROM egc.nao_recorrente_config WHERE chave = %s", (CONFIG_CHAVE_EBITDA_AJUSTADO,))
            row = cur.fetchone()
        return bool(row) and str(row[0]).strip().lower() == "ligado"
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def definir_ebitda_ajustado_ativo(conn, ativo: bool, usuario: Optional[str] = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO egc.nao_recorrente_config (chave, valor, atualizado_por, atualizado_em) "
            "VALUES (%s, %s, %s, now()) "
            "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor, atualizado_por = EXCLUDED.atualizado_por, "
            "atualizado_em = now()",
            (CONFIG_CHAVE_EBITDA_AJUSTADO, "ligado" if ativo else "desligado", usuario),
        )
    conn.commit()


# ----------------------------------------------------------------------------- leitura
def listar_itens(conn, empresa: str, periodo: date, gran: str, incluir_removidos: bool = False) -> list[dict]:
    sql = (f"SELECT {', '.join(_COLUNAS)} FROM egc.nao_recorrente "
           "WHERE empresa_codigo = %s AND periodo = %s AND COALESCE(granularidade, '') = %s")
    if not incluir_removidos:
        sql += " AND ativo"
    sql += " ORDER BY id"
    with conn.cursor() as cur:
        cur.execute(sql, (empresa, periodo, gran or ""))
        return _rows(cur)


def status_confirmacao(conn, empresa: str, periodo: date, gran: str) -> dict:
    """{'status': 'confirmado'|'alterado'|'nao_confirmado', 'confirmado_por', 'confirmado_em', 'itens', 'total'}.
    'alterado' = ja foi confirmada, mas a lista mudou depois (confirmacao nao vale mais)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT assinatura, confirmado_por, confirmado_em, itens, total
               FROM egc.nao_recorrente_confirmacao
               WHERE empresa_codigo = %s AND periodo = %s AND COALESCE(granularidade, '') = %s""",
            (empresa, periodo, gran or ""),
        )
        row = cur.fetchone()
    if not row:
        return {"status": "nao_confirmado", "confirmado_por": None, "confirmado_em": None, "itens": 0, "total": 0.0}
    atual = assinatura_lista(listar_itens(conn, empresa, periodo, gran))
    st = "confirmado" if atual == row[0] else "alterado"
    return {"status": st, "confirmado_por": row[1], "confirmado_em": row[2], "itens": row[3], "total": float(row[4])}


def listar_historico(conn, empresa: str, periodo: date, gran: str, limite: int = 100) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT em, por, acao, item_id, antes, depois FROM egc.nao_recorrente_historico
               WHERE empresa_codigo = %s AND periodo = %s AND COALESCE(granularidade, '') = %s
               ORDER BY id DESC LIMIT %s""",
            (empresa, periodo, gran or "", limite),
        )
        return _rows(cur)


# ----------------------------------------------------------------------------- escrita
def _validar(categoria: str, valor, sinal) -> tuple[str, float, int]:
    if categoria not in CATEGORIAS:
        raise ValueError(f"categoria inválida: {categoria!r}")
    v = round(abs(float(valor)), 2)
    if v <= 0:
        raise ValueError("o valor do item precisa ser maior que zero")
    if int(sinal) not in (1, -1):
        raise ValueError("sinal precisa ser +1 (soma ao EBITDA) ou -1 (subtrai)")
    return categoria, v, int(sinal)


def adicionar_item(conn, empresa: str, periodo: date, gran: str, categoria: str, valor, sinal: int = 1,
                   descricao: str = "", contas_dre: Optional[str] = None, justificativa: Optional[str] = None,
                   documento: Optional[str] = None, sugerido_regra: bool = False, usuario: Optional[str] = None) -> int:
    categoria, valor, sinal = _validar(categoria, valor, sinal)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO egc.nao_recorrente
                   (empresa_codigo, periodo, granularidade, categoria, descricao, contas_dre, valor, sinal,
                    justificativa, documento, sugerido_regra, criado_por, atualizado_por)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (empresa, periodo, gran or "", categoria, (descricao or "").strip(), contas_dre, valor, sinal,
             justificativa, documento, bool(sugerido_regra), usuario, usuario),
        )
        item_id = cur.fetchone()[0]
        _historico(cur, empresa, periodo, gran, item_id, "criou", None,
                   dict(categoria=categoria, valor=valor, sinal=sinal, descricao=descricao, contas_dre=contas_dre,
                        justificativa=justificativa, documento=documento, sugerido_regra=bool(sugerido_regra)), usuario)
    return item_id


def _item(cur, item_id: int) -> dict:
    cur.execute(f"SELECT {', '.join(_COLUNAS)} FROM egc.nao_recorrente WHERE id = %s", (item_id,))
    rows = _rows(cur)
    if not rows:
        raise ValueError(f"item {item_id} não existe")
    return rows[0]


def editar_item(conn, item_id: int, categoria: str, valor, sinal: int, descricao: str = "",
                contas_dre: Optional[str] = None, justificativa: Optional[str] = None,
                documento: Optional[str] = None, usuario: Optional[str] = None) -> None:
    categoria, valor, sinal = _validar(categoria, valor, sinal)
    with conn.cursor() as cur:
        antes = _item(cur, item_id)
        cur.execute(
            """UPDATE egc.nao_recorrente
                  SET categoria=%s, descricao=%s, contas_dre=%s, valor=%s, sinal=%s, justificativa=%s,
                      documento=%s, atualizado_por=%s, atualizado_em=now()
                WHERE id=%s""",
            (categoria, (descricao or "").strip(), contas_dre, valor, sinal, justificativa, documento, usuario, item_id),
        )
        depois = dict(categoria=categoria, valor=valor, sinal=sinal, descricao=descricao, contas_dre=contas_dre,
                      justificativa=justificativa, documento=documento)
        ant = {k: antes[k] for k in depois}
        _historico(cur, antes["empresa_codigo"], antes["periodo"], antes["granularidade"], item_id, "editou", ant, depois, usuario)


def _trocar_ativo(conn, item_id: int, ativo: bool, usuario: Optional[str]) -> None:
    with conn.cursor() as cur:
        antes = _item(cur, item_id)
        cur.execute("UPDATE egc.nao_recorrente SET ativo=%s, atualizado_por=%s, atualizado_em=now() WHERE id=%s",
                    (ativo, usuario, item_id))
        _historico(cur, antes["empresa_codigo"], antes["periodo"], antes["granularidade"], item_id,
                   "restaurou" if ativo else "removeu", {"ativo": antes["ativo"]}, {"ativo": ativo}, usuario)


def remover_item(conn, item_id: int, usuario: Optional[str] = None) -> None:
    """NAO apaga: so' marca ativo=false (continua na trilha e pode ser restaurado)."""
    _trocar_ativo(conn, item_id, False, usuario)


def restaurar_item(conn, item_id: int, usuario: Optional[str] = None) -> None:
    _trocar_ativo(conn, item_id, True, usuario)


def confirmar_lista(conn, empresa: str, periodo: date, gran: str, usuario: Optional[str] = None) -> dict:
    """Confirma a lista ATIVA de hoje (inclusive vazia). Guarda a assinatura: qualquer alteracao
    posterior faz status_confirmacao() voltar 'alterado'."""
    itens = listar_itens(conn, empresa, periodo, gran)
    assin = assinatura_lista(itens)
    total = round(sum(valor_assinado(i) for i in itens), 2)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO egc.nao_recorrente_confirmacao
                   (empresa_codigo, periodo, granularidade, itens, total, assinatura, confirmado_por, confirmado_em)
               VALUES (%s, %s, %s, %s, %s, %s, %s, now())
               ON CONFLICT (empresa_codigo, periodo, granularidade)
               DO UPDATE SET itens=EXCLUDED.itens, total=EXCLUDED.total, assinatura=EXCLUDED.assinatura,
                             confirmado_por=EXCLUDED.confirmado_por, confirmado_em=now()""",
            (empresa, periodo, gran or "", len(itens), total, assin, usuario),
        )
        _historico(cur, empresa, periodo, gran, None, "confirmou", None,
                   dict(itens=len(itens), total=total, assinatura=assin), usuario)
    return {"itens": len(itens), "total": total}


# ----------------------------------------------------------------------------- sugestoes
def sugerir(itens_admin: list, dre_equivalencia: Optional[float] = None) -> list[dict]:
    """SUGESTOES (nada e' gravado). `itens_admin`: [(conta, valor), ...] das despesas administrativas
    do periodo (db.listar_despesas_admin_itens). `dre_equivalencia`: valor da linha de equivalencia
    patrimonial na DRE (negativo = perda), se existir. Devolve
    [{categoria, conta, valor (>=0), sinal, descricao}] -- a pessoa decide se adiciona."""
    saida = []
    for conta, valor in itens_admin or []:
        n = _norm(conta)
        for trechos, categoria in REGRAS_SUGESTAO:
            if categoria == "Equivalência patrimonial":
                continue  # equivalencia vem da DRE, nao das administrativas
            if any(t in n for t in trechos):
                v = abs(float(valor))
                if v > 0:
                    saida.append(dict(categoria=categoria, conta=conta, valor=round(v, 2), sinal=1,
                                      descricao=conta.strip()))
                break
    if dre_equivalencia:
        saida.append(dict(categoria="Equivalência patrimonial", conta="RESULTADO DA EQUIVALÊNCIA PATRIMONIAL",
                          valor=round(abs(float(dre_equivalencia)), 2),
                          sinal=1 if dre_equivalencia < 0 else -1,
                          descricao="Resultado da equivalência patrimonial (sem saída de caixa)"))
    return saida


# ----------------------------------------------------------------------------- uso no relatorio
def carregar_para_relatorio(conn, codigos: list, periodo: date, gran: str) -> dict:
    """O que o RELATORIO usa. Devolve:
      status: 'confirmado' (todas as empresas com lista confirmada e vigente)
              | 'pendente'   (alguma empresa sem confirmacao ou com lista alterada depois)
              | 'indisponivel' (tabelas do bloco 25 ainda nao existem no banco)
      itens: [(categoria, valor_assinado)] somando as empresas, na ordem de CATEGORIAS (so' se confirmado)
      total: soma assinada (so' se confirmado; senao 0.0)
      pendentes: codigos de empresa sem confirmacao vigente
    Qualquer erro de banco vira 'indisponivel' -- nunca derruba a geracao do relatorio."""
    vazio = {"status": "indisponivel", "itens": [], "total": 0.0, "pendentes": list(codigos)}
    try:
        if not tabelas_existem(conn):
            return vazio
        pendentes, por_cat = [], {}
        for cod in codigos:
            if status_confirmacao(conn, cod, periodo, gran)["status"] != "confirmado":
                pendentes.append(cod)
                continue
            for it in listar_itens(conn, cod, periodo, gran):
                por_cat[it["categoria"]] = por_cat.get(it["categoria"], 0.0) + valor_assinado(it)
        if pendentes:
            return {"status": "pendente", "itens": [], "total": 0.0, "pendentes": pendentes}
        ordem = {c: i for i, c in enumerate(CATEGORIAS)}
        itens = [(c, round(v, 2)) for c, v in sorted(por_cat.items(), key=lambda kv: ordem.get(kv[0], 99))]
        return {"status": "confirmado", "itens": itens, "total": round(sum(v for _, v in itens), 2), "pendentes": []}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return vazio
