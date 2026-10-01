# -*- coding: utf-8 -*-
"""
Memoria da conversa da Erik.AI POR USUARIO, no Supabase (01/10/2026; padrao do
Viaj.AI: tabela por usuario + janela fixa de mensagens).

- Tudo e' gravado em egc.chat_mensagem (log completo, por usuario/e-mail).
- Pra API vai so' a JANELA das ultimas JANELA_HISTORICO_CHAT mensagens -- evita
  estourar contexto/custo. O historico e' so' TEXTO: numeros de BP/DRE nunca sao
  "lembrados" daqui -- o prompt manda reconsultar a ferramenta (nao alucinar
  com dado velho).
- Sem a tabela (bloco 20 do schema.sql ainda nao rodou) ou banco fora do ar:
  devolve vazio / False e o chat segue so' com a sessao, sem quebrar.
"""
from __future__ import annotations

from typing import Optional

JANELA_HISTORICO_CHAT = 24          # mensagens que voltam ao abrir o chat e vao pra API
MAX_CHARS_MENSAGEM_SALVA = 6000


def carregar_historico(conn, usuario: str, limite: int = JANELA_HISTORICO_CHAT) -> list[dict]:
    """Ultimas `limite` mensagens do usuario, em ordem cronologica. [] se indisponivel."""
    if conn is None or not usuario:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT papel, conteudo FROM egc.chat_mensagem WHERE usuario = %s ORDER BY id DESC LIMIT %s",
                (usuario, int(limite)),
            )
            linhas = cur.fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    msgs = [{"role": r[0], "content": r[1]} for r in reversed(linhas)]
    while msgs and msgs[0]["role"] != "user":  # a API exige comecar por "user"
        msgs = msgs[1:]
    while msgs and msgs[-1]["role"] == "user":  # pergunta sem resposta (sessao caiu): nao responder sozinha ao abrir
        msgs = msgs[:-1]
    return msgs


def salvar_mensagem(conn, usuario: str, papel: str, conteudo: str, ferramentas: list[str] | None = None) -> bool:
    if conn is None or not usuario or papel not in ("user", "assistant") or not str(conteudo or "").strip():
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO egc.chat_mensagem (usuario, papel, conteudo, ferramentas) VALUES (%s,%s,%s,%s)",
                (usuario, papel, str(conteudo)[:MAX_CHARS_MENSAGEM_SALVA], ferramentas or None),
            )
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def apagar_historico(conn, usuario: str) -> int:
    """Apaga TODO o historico do proprio usuario (botao 'Apagar meu histórico')."""
    if conn is None or not usuario:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM egc.chat_mensagem WHERE usuario = %s", (usuario,))
            return cur.rowcount
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def disponivel(conn) -> Optional[bool]:
    """True/False se a tabela de memoria existe; None se nao ha conexao."""
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('egc.chat_mensagem') IS NOT NULL")
            return bool(cur.fetchone()[0])
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
