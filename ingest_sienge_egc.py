# -*- coding: utf-8 -*-
"""
Ingestão automática Sienge -> Supabase (schema egc), rodando FORA do
Streamlit -- mesmo padrão já validado no TIA.go (ingest_sienge_to_supabase.py
+ .github/workflows/ingest-sienge.yml). Pedido do Rafael em 25/09/2026:
o botão "Atualizar do Sienge" (app/telas/7_Notas_Fiscais.py) demorava
~10min rodando na hora, travando a contadora olhando o spinner.

O QUE FAZ: chama as MESMAS funções que o botão já chama
(app/nf_sienge.sincronizar_bills / sincronizar_creditores) -- não duplica
lógica, só troca QUEM aciona (GitHub Actions agendado, em vez do clique).
Cobre as 6 empresas de uma vez só -- /v1/bills não filtra por empresa
(confirmado em EGC 00-handoff.md seção 62), então 1 rodada diária já
sincroniza o grupo inteiro.

O botão continua existindo no app pra forçar uma atualização fora do
horário do cron (ex.: acabou de lançar uma nota e quer conferir na hora).

Credenciais vêm de variável de ambiente (nunca hardcoded, nunca commitadas):
  SIENGE_BASE_URL, SIENGE_USER, SIENGE_PASSWORD  -- mesma credencial
    dedicada já usada pelo Streamlit Secrets.
  DATABASE_URL  -- mesma connection string do Supabase (role egc_app) já
    usada pelo Streamlit Secrets (app/conexao.py:get_conn).
  NF_DIAS_RETROATIVOS (opcional, default 60) -- quantos dias pra trás
    sincronizar de /v1/bills. 60 dias cobre reprocessamento de período
    fechado com folga sem puxar o histórico inteiro toda vez.

Uso local (fora do GitHub Actions), pra testar antes de automatizar:
  export SIENGE_BASE_URL=... SIENGE_USER=... SIENGE_PASSWORD=... DATABASE_URL=...
  python ingest_sienge_egc.py
"""
from __future__ import annotations

import os
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

import psycopg2  # noqa: E402
import nf_sienge  # noqa: E402
import db  # noqa: E402

USUARIO_LOG = "ingest-automatico"


def _env_obrigatoria(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        print(f"ERRO: variável de ambiente {nome} não configurada.", file=sys.stderr)
        sys.exit(1)
    return valor


def main() -> None:
    base_url = _env_obrigatoria("SIENGE_BASE_URL")
    sienge_user = _env_obrigatoria("SIENGE_USER")
    sienge_pass = _env_obrigatoria("SIENGE_PASSWORD")
    database_url = _env_obrigatoria("DATABASE_URL")
    dias_retroativos = int(os.environ.get("NF_DIAS_RETROATIVOS", "60"))

    hoje = dt.date.today()
    data_inicio = hoje - dt.timedelta(days=dias_retroativos)
    data_fim = hoje

    conn = psycopg2.connect(database_url)
    conn.autocommit = True

    try:
        print(f"Sincronizando /v1/bills de {data_inicio} a {data_fim}...")
        n_bills = nf_sienge.sincronizar_bills(conn, base_url, sienge_user, sienge_pass, data_inicio, data_fim)
        print(f"  {n_bills} título(s) sincronizado(s).")

        print("Sincronizando /v1/creditors...")
        n_cred = nf_sienge.sincronizar_creditores(conn, base_url, sienge_user, sienge_pass)
        print(f"  {n_cred} credor(es) sincronizado(s).")

        db.registrar_evento(
            conn, "notas_fiscais", "INFO",
            f"Sync automático (GitHub Actions): {n_bills} títulos, {n_cred} credores "
            f"({data_inicio} a {data_fim})",
            usuario=USUARIO_LOG,
        )
        print("Ingestão concluída com sucesso.")
    except Exception as exc:
        print(f"ERRO na ingestão: {exc}", file=sys.stderr)
        try:
            db.registrar_evento(conn, "notas_fiscais", "ERRO", "Falha no sync automático (GitHub Actions)",
                                 usuario=USUARIO_LOG, detalhe=str(exc))
        except Exception:
            pass
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
