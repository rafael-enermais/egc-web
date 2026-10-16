# -*- coding: utf-8 -*-
"""
EGC | Creditos PIS/COFINS -- situacao do DETALHAMENTO da contadora num mes (v0.53.2, 16/10/2026).

Serve para avisar (nunca travar) quando um relatorio/mes sai so' com o SPED: o Detalhamento e' a planilha de apoio da contadora (confere o credito
fornecedor a fornecedor); o SPED e' o que foi declarado. Estados:
  ausente     -- nao ha Detalhamento enviado para o mes;
  sem_sped    -- ha Detalhamento, mas o mes so' tem SPED na carga fixa (sem linhas): nao da' para conferir linha a linha;
  fecha       -- Detalhamento x SPED apontado: nenhuma linha pendente;
  pendencias  -- ha linhas pendentes de conferencia (valor/numero diferente ou so' de um lado) ainda nao marcadas como conferidas.
Usa o MESMO criterio da aba Conferencia (conferir_linhas + edicoes da contadora).
"""
from __future__ import annotations

import creditos_conferencia as cf
import creditos_db as cdb

PENDENTES = (cf.S_NUM, cf.S_DIF, cf.S_SO_SPED, cf.S_SO_DET)


def status_detalhamento(conn, empresa_codigo: str, competencia: str) -> dict:
    v_det = cdb.listar_versoes(conn, "DETALHAMENTO", empresa_codigo, competencia)
    ap = next((v for v in v_det if v["apontada"]), None)
    if ap is None:
        return {"estado": "ausente"}
    base = {"arquivo": ap["arquivo_nome"], "versao_det": ap["n"]}
    v_sped = cdb.listar_versoes(conn, "SPED", empresa_codigo, competencia)
    sp_v = next((v for v in v_sped if v["apontada"]), None)
    if sp_v is None:
        return {**base, "estado": "sem_sped"}
    sped = cdb.carregar_sped(conn, empresa_codigo, competencia, envio_id=sp_v["id"])
    det = cdb.carregar_detalhamento(conn, empresa_codigo, competencia, envio_id=ap["id"])
    if sped is None or det is None:
        return {**base, "estado": "sem_sped"}
    linhas = cf.aplicar_aba("linhas", cf.conferir_linhas(sped, det), cdb.carregar_ajustes(conn, empresa_codigo, competencia, ref=sped["envio_id"]))
    pend = linhas[linhas["status"].isin(PENDENTES) & ~linhas["conferido"].astype(bool)] if not linhas.empty else linhas
    return {**base, "estado": "fecha" if len(pend) == 0 else "pendencias", "n_linhas": int(len(linhas)), "n_pendentes": int(len(pend)),
            "versao_sped": sp_v["n"], "tipo_sped": str(sp_v.get("sped_tipo") or "").strip()}

