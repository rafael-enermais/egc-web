# -*- coding: utf-8 -*-
"""
Acoes que a Erik.AI pode PROPOR (01/10/2026, pedido do Rafael: "edicoes com
aprovacoes ... estruturar bem pra nao alucinar nem passar informacao errada").

Modelo de seguranca (a IA NUNCA grava):
  1. A ferramenta `propor_acao` so' chama `montar_proposta` -- que VALIDA os
     parametros contra o banco (a nota/periodo/conta existe? qual o valor
     atual?) e devolve uma proposta com a pre-visualizacao. Nada e' gravado.
  2. A proposta fica na sessao (st.session_state) e a tela mostra um cartao
     com o antes x depois. So' o clique de uma pessoa em "Aprovar" chama
     `executar_proposta`.
  3. `executar_proposta` REVALIDA tudo (o banco pode ter mudado entre propor
     e aprovar), executa a MESMA funcao de db que a tela manual usa e grava a
     trilha em egc.chat_acao (APROVADA / ERRO). Rejeicao tambem fica no log.
  4. So' acoes reversiveis ou com trilha de auditoria: status de pendencia
     (reabre quando quiser), arquivar/recuperar periodo (nunca apaga),
     correcao manual de conta (pdf_original preservado). Nada de excluir,
     importar, enviar e-mail.

Modulo sem streamlit: testavel direto (tests/test_acoes_chat.py).
"""
from __future__ import annotations

import datetime
import json
import re
import uuid
from typing import Optional

import db
import nf_sienge

TIPOS_ACAO = {
    "ATUALIZAR_STATUS_PENDENCIA": "Atualizar status de pendência de nota fiscal",
    "ARQUIVAR_PERIODO": "Arquivar período (BP+DRE)",
    "RECUPERAR_PERIODO": "Recuperar período arquivado",
    "CORRIGIR_LANCAMENTO": "Corrigir valor de uma conta (BP/DRE)",
}
MAX_PROPOSTAS_PENDENTES = 3


def _periodo_data(periodo_txt) -> Optional[tuple[int, int]]:
    try:
        ano, mes = str(periodo_txt).strip().split("-")
        ano, mes = int(ano), int(mes)
        if 1 <= mes <= 12 and 2000 <= ano <= 2100:
            return ano, mes
    except (ValueError, AttributeError):
        pass
    return None


def _numero(v) -> Optional[float]:
    """Aceita 1234.5, '1.234,50', 'R$ -1.234,50'. None se nao for numero."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = re.sub(r"[^\d,.\-]", "", str(v or ""))
    if not t or t in ("-", ".", ","):
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _documentos(conn, empresa: str, status: str) -> list[dict]:
    """Documentos (periodo, granularidade) alvo da acao: ATIVOS p/ arquivar; p/ recuperar,
    so' os que de fato podem ser recuperados (sem versao ativa) -- mesma regra da tela."""
    if status == "INATIVO":
        return db.listar_periodos_recuperaveis(conn, empresa)
    return db.listar_periodos_detalhado(conn, empresa, status=status)


def _resolver_periodo(conn, empresa: str, periodo_txt: str, status: str, granularidade: Optional[str]):
    """-> (date, granularidade, erro). Resolve o documento (periodo_fim + granularidade)."""
    ym = _periodo_data(periodo_txt)
    if not ym:
        return None, None, "Período inválido — use o formato AAAA-MM (ex.: 2026-06)."
    detalhados = [d for d in _documentos(conn, empresa, status)
                  if d["periodo"].year == ym[0] and d["periodo"].month == ym[1]]
    if not detalhados:
        rotulo = "ativo" if status == "ATIVO" else "arquivado"
        return None, None, f"{empresa} não tem período {rotulo} em {periodo_txt}."
    if granularidade:
        casa = [d for d in detalhados if (d["granularidade"] or "") == granularidade]
        if not casa:
            ops = ", ".join(sorted({d["granularidade"] or "não declarada" for d in detalhados}))
            return None, None, f"Não há documento '{granularidade}' em {periodo_txt}. Opções: {ops}."
        detalhados = casa
    if len(detalhados) > 1:
        ops = ", ".join(sorted({d["granularidade"] or "não declarada" for d in detalhados}))
        return None, None, (f"{empresa} tem mais de 1 documento em {periodo_txt} ({ops}). "
                            "Pergunte ao usuário qual granularidade e informe no parâmetro 'granularidade'.")
    return detalhados[0]["periodo"], detalhados[0]["granularidade"] or "", None


def montar_proposta(conn, tipo: str, params: dict, empresas_codigos: list[str]) -> dict:
    """Valida e monta a proposta. Retorna {"proposta": {...}} ou {"erro": "..."}.
    NUNCA grava nada."""
    params = params if isinstance(params, dict) else {}
    if tipo not in TIPOS_ACAO:
        return {"erro": f"Tipo de ação não permitido: {str(tipo)[:40]!r}. Permitidos: {', '.join(TIPOS_ACAO)}."}

    base = {"id": uuid.uuid4().hex[:8], "tipo": tipo, "titulo": TIPOS_ACAO[tipo],
            "motivo": str(params.get("motivo") or "")[:300], "criada_em": datetime.datetime.now().isoformat(timespec="seconds")}

    if tipo == "ATUALIZAR_STATUS_PENDENCIA":
        origem = params.get("origem")
        if origem not in ("MANIFESTO", "SIENGE_ORFAO"):
            return {"erro": "origem deve ser MANIFESTO ou SIENGE_ORFAO (veja a coluna 'origem' de consultar_notas_pendentes)."}
        try:
            registro_id = int(params.get("registro_id"))
        except (TypeError, ValueError):
            return {"erro": "registro_id inválido — use o 'registro_id' devolvido por consultar_notas_pendentes/consultar_nota_fiscal."}
        novo = params.get("novo_status")
        if novo not in nf_sienge.STATUS_PENDENCIA_VALIDOS:
            return {"erro": f"novo_status deve ser um de: {', '.join(nf_sienge.STATUS_PENDENCIA_VALIDOS)}."}
        reg = nf_sienge.obter_registro_pendencia(conn, origem, registro_id)
        if not reg:
            return {"erro": "Registro não encontrado — confira o registro_id."}
        if reg["empresa_codigo"] not in empresas_codigos:
            return {"erro": "Registro de empresa fora do grupo."}
        base["parametros"] = {"origem": origem, "registro_id": registro_id, "novo_status": novo}
        base["resumo"] = [
            ("Empresa", reg["empresa_codigo"]), ("Nota", reg["numero_nota"]), ("Fornecedor", reg["fornecedor"]),
            ("Valor", reg["valor"]), ("Status atual", reg["pendencia_status"]), ("Novo status", novo),
        ]
        base["editaveis"] = {"novo_status": list(nf_sienge.STATUS_PENDENCIA_VALIDOS)}
        base["reversivel"] = True
        return {"proposta": base}

    empresa = params.get("empresa")
    if empresa not in empresas_codigos:
        return {"erro": f"empresa inválida. Use um destes códigos: {', '.join(empresas_codigos)}."}
    gran = (params.get("granularidade") or "").strip() or None

    if tipo in ("ARQUIVAR_PERIODO", "RECUPERAR_PERIODO"):
        status = "ATIVO" if tipo == "ARQUIVAR_PERIODO" else "INATIVO"
        data, g, erro = _resolver_periodo(conn, empresa, params.get("periodo"), status, gran)
        if erro:
            return {"erro": erro}
        base["parametros"] = {"empresa": empresa, "periodo": data.strftime("%Y-%m"), "granularidade": g}
        base["resumo"] = [("Empresa", empresa), ("Período", data.strftime("%m/%Y")),
                          ("Base", g or "não declarada"),
                          ("Efeito", "BP+DRE deixam de aparecer nos dashboards/relatórios (nada é apagado)"
                                     if tipo == "ARQUIVAR_PERIODO" else "BP+DRE voltam a valer nos dashboards/relatórios")]
        base["editaveis"] = {}
        base["reversivel"] = True
        return {"proposta": base}

    # CORRIGIR_LANCAMENTO
    tipo_dem = params.get("tipo_demonstracao") or params.get("tipo_bp_dre")
    if tipo_dem not in ("BP", "DRE"):
        return {"erro": "tipo_demonstracao deve ser BP ou DRE."}
    data, g, erro = _resolver_periodo(conn, empresa, params.get("periodo"), "ATIVO", gran)
    if erro:
        return {"erro": erro}
    conta = str(params.get("conta") or "").strip()
    if not conta:
        return {"erro": "Informe o nome da conta."}
    novo_valor = _numero(params.get("novo_valor"))
    if novo_valor is None:
        return {"erro": "novo_valor inválido — informe um número."}
    lancs = db.listar_lancamentos(conn, empresa, data, tipo_dem, "ATIVO", granularidade=g)
    iguais = [l for l in lancs if str(l["conta"]).strip().lower() == conta.lower()]
    if not iguais:
        parecidas = sorted({l["conta"] for l in lancs if conta.lower() in str(l["conta"]).lower()})[:8]
        return {"erro": f"Conta '{conta[:60]}' não encontrada em {tipo_dem} {empresa} {data.strftime('%m/%Y')}."
                        + (f" Contas parecidas: {'; '.join(parecidas)}." if parecidas else "")}
    if len(iguais) > 1:
        grupos = "; ".join(sorted({str(l["grupo"]) for l in iguais}))
        return {"erro": f"A conta '{conta[:60]}' existe em mais de um grupo ({grupos}) — não dá para escolher sozinha. "
                        "Peça ao usuário para corrigir pela tela Revisão/Correção."}
    alvo = iguais[0]
    base["parametros"] = {"empresa": empresa, "tipo_demonstracao": tipo_dem, "periodo": data.strftime("%Y-%m"),
                          "granularidade": g, "conta": alvo["conta"], "lancamento_id": int(alvo["id"]),
                          "novo_valor": novo_valor}
    base["resumo"] = [
        ("Empresa", empresa), ("Demonstração", tipo_dem), ("Período", data.strftime("%m/%Y")), ("Base", g or "não declarada"),
        ("Grupo", alvo["grupo"]), ("Conta", alvo["conta"]), ("Valor atual", float(alvo["valor"])),
        ("Valor original do PDF", float(alvo["pdf_original"]) if alvo.get("pdf_original") is not None else float(alvo["valor"])),
        ("Novo valor", novo_valor),
    ]
    base["editaveis"] = {"novo_valor": "numero"}
    base["reversivel"] = True  # pdf_original fica guardado
    return {"proposta": base}


def _registrar(conn, usuario: str, proposta: dict, status: str, resultado: str) -> None:
    """Trilha em egc.chat_acao -- silencioso se a tabela ainda nao existe (bloco 20)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO egc.chat_acao (usuario, tipo, parametros, status, resultado) VALUES (%s,%s,%s::jsonb,%s,%s)",
                (usuario, proposta["tipo"], json.dumps(proposta.get("parametros", {}), default=str), status, resultado[:500]),
            )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def executar_proposta(conn, proposta: dict, usuario: str, empresas_codigos: list[str],
                      edicoes: Optional[dict] = None) -> tuple[bool, str]:
    """Executa APOS aprovacao humana. `edicoes` = campos editados no cartao
    (novo_status / novo_valor). Revalida tudo antes de gravar."""
    params = dict(proposta.get("parametros", {}))
    if edicoes:
        for k, v in edicoes.items():
            if k in (proposta.get("editaveis") or {}):
                params[k] = v
    tipo = proposta["tipo"]
    # revalidacao contra o estado ATUAL do banco
    entrada = dict(params)
    if tipo == "CORRIGIR_LANCAMENTO":
        entrada.pop("lancamento_id", None)
    check = montar_proposta(conn, tipo, entrada, empresas_codigos)
    if "erro" in check:
        _registrar(conn, usuario, proposta, "ERRO", check["erro"])
        return False, "Não executei: " + check["erro"]
    p = check["proposta"]["parametros"]
    try:
        if tipo == "ATUALIZAR_STATUS_PENDENCIA":
            if p["origem"] == "SIENGE_ORFAO":
                nf_sienge.atualizar_status_orfao_sienge(conn, p["registro_id"], p["novo_status"], usuario)
            else:
                nf_sienge.atualizar_status_pendencia(conn, p["registro_id"], p["novo_status"], usuario)
            msg = f"Pendência atualizada para {p['novo_status']}."
        elif tipo in ("ARQUIVAR_PERIODO", "RECUPERAR_PERIODO"):
            ano, mes = _periodo_data(p["periodo"])
            alvo = [d["periodo"] for d in _documentos(
                conn, p["empresa"], "ATIVO" if tipo == "ARQUIVAR_PERIODO" else "INATIVO")
                if d["periodo"].year == ano and d["periodo"].month == mes][0]
            fn = db.arquivar_periodo if tipo == "ARQUIVAR_PERIODO" else db.recuperar_periodo
            n = fn(conn, p["empresa"], alvo, p["granularidade"])
            msg = f"{n} linha(s) {'arquivadas' if tipo == 'ARQUIVAR_PERIODO' else 'recuperadas'} ({p['empresa']} {p['periodo']})."
        else:  # CORRIGIR_LANCAMENTO
            ano, mes = _periodo_data(p["periodo"])
            data = [d["periodo"] for d in db.listar_periodos_detalhado(conn, p["empresa"], status="ATIVO")
                    if d["periodo"].year == ano and d["periodo"].month == mes][0]
            db.salvar_correcao_manual(conn, p["lancamento_id"], p["novo_valor"], data, usuario=usuario)
            msg = f"Conta '{p['conta']}' corrigida para {p['novo_valor']:,.2f} (valor original do PDF preservado)."
        _registrar(conn, usuario, {**proposta, "parametros": p}, "APROVADA", msg)
        return True, msg
    except Exception as exc:  # nunca derruba a tela
        _registrar(conn, usuario, proposta, "ERRO", str(exc))
        return False, "Falha ao gravar — nada foi alterado ou a alteração foi parcial; confira a tela correspondente."


def registrar_rejeicao(conn, proposta: dict, usuario: str) -> None:
    _registrar(conn, usuario, proposta, "REJEITADA", "Rejeitada pela pessoa.")
