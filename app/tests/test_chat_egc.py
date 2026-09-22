# -*- coding: utf-8 -*-
"""
Testes unitarios de app/chat_egc.py -- mesmo padrao self-running (sem
pytest) do resto do projeto. Mocka client.messages.create com objetos
SimpleNamespace que imitam a forma real dos content blocks da SDK
Anthropic (role/type/text/name/input/id -- atributos, nao dict, igual o
SDK de verdade devolve) -- nao precisa de chave de API real nem da lib
anthropic instalada pra rodar (chat_egc.py nao importa anthropic direto,
so' recebe o client pronto de quem chama, ver docstring do modulo).

Cobre: prompt inclui data/usuario/empresas; loop de 1 chamada de
ferramenta ate' resposta final; loop de 2 chamadas em sequencia (ex.
consultar_periodos -> consultar_bp_dre); ferramenta desconhecida nao
quebra (defensivo); limite de seguranca MAX_ITERACOES_TOOL_USE corta um
loop que nunca para (bug de prompt simulado); mensagens_api reconstruido
so' do historico de texto (nao herda tool turns antigos).
"""
import sys
import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import consultas_chat  # noqa: E402
import chat_egc  # noqa: E402
import db  # noqa: E402

FALHAS = []


def checar(cond, msg):
    if not cond:
        FALHAS.append(msg)
        print(f"FALHOU: {msg}")
    else:
        print(f"OK: {msg}")


def _texto(txt):
    return SimpleNamespace(type="text", text=txt)


def _tool_use(nome, entrada, tool_id="tu_1"):
    return SimpleNamespace(type="tool_use", name=nome, input=entrada, id=tool_id)


def _resposta(stop_reason, content):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


def test_montar_system_prompt_inclui_data_usuario_empresas():
    hoje = datetime.date(2026, 9, 22)
    p = chat_egc.montar_system_prompt("teste@enermais.com.br", ["ENERGIA", "SMG"], hoje=hoje)
    checar("22/09/2026" in p, "montar_system_prompt -- data injetada aparece formatada")
    checar("teste@enermais.com.br" in p, "montar_system_prompt -- usuario logado aparece")
    checar("ENERGIA" in p and "SMG" in p, "montar_system_prompt -- codigos de empresa aparecem")
    checar("nunca invente" in p.lower(), "montar_system_prompt -- regra anti-invencao presente")


def test_montar_system_prompt_sem_conn_nao_traz_contexto_fiscal():
    # conn=None (default, mesma chamada que todos os testes acima ja
    # usavam) -- comportamento identico a antes desta mudanca, sem quebrar
    # nada que ja chamava esta funcao.
    p = chat_egc.montar_system_prompt("teste@enermais.com.br", ["ENERGIA"], hoje=datetime.date(2026, 9, 22))
    checar("CONHECIMENTO DE REFERENCIA" not in p, "montar_system_prompt -- sem conn, nenhum bloco de contexto fiscal")


def test_montar_system_prompt_com_conn_injeta_contexto_fiscal():
    linhas = [
        {"chave": "REFORMA_TRIB_CRONOGRAMA", "tema": "reforma_tributaria",
         "titulo": "Cronograma da Reforma Tributaria", "conteudo": "teste ate 2033...",
         "fonte": "x", "atualizado_em": None},
    ]
    with patch.object(db, "listar_contexto_fiscal", return_value=linhas) as m:
        p = chat_egc.montar_system_prompt("teste@enermais.com.br", ["ENERGIA"], conn="fake_conn",
                                           hoje=datetime.date(2026, 9, 22))
    checar(m.call_count == 1, "montar_system_prompt -- com conn, chama db.listar_contexto_fiscal 1 vez")
    checar("CONHECIMENTO DE REFERENCIA" in p, "montar_system_prompt -- com conn e linhas, bloco aparece")
    checar("Cronograma da Reforma Tributaria" in p and "teste ate 2033..." in p,
           "montar_system_prompt -- titulo e conteudo curado aparecem no prompt")
    checar("nao e' dado" in p.lower() or "nao e dado" in p.lower(),
           "montar_system_prompt -- bloco deixa claro que nao e' dado de BP/DRE (nao afrouxa REGRA CRITICA)")


def test_montar_system_prompt_falha_ao_buscar_contexto_nao_quebra_prompt():
    # banco fora do ar / tabela ainda nao existe -- so' pula o bloco, o
    # resto do prompt (data/usuario/empresas/REGRA CRITICA) continua indo.
    with patch.object(db, "listar_contexto_fiscal", side_effect=Exception("relation does not exist")):
        p = chat_egc.montar_system_prompt("teste@enermais.com.br", ["ENERGIA"], conn="fake_conn",
                                           hoje=datetime.date(2026, 9, 22))
    checar("CONHECIMENTO DE REFERENCIA" not in p, "montar_system_prompt -- falha ao buscar contexto, bloco omitido")
    checar("nunca invente" in p.lower(), "montar_system_prompt -- resto do prompt intacto mesmo com falha no contexto")


def test_executar_ferramenta_desconhecida_nao_quebra():
    r = chat_egc.executar_ferramenta(conn=None, nome="nao_existe", entrada={}, empresas_codigos=["ENERGIA"])
    checar(r == {"erro": "ferramenta desconhecida: nao_existe"}, "executar_ferramenta -- nome desconhecido devolve erro, nao excecao")


def test_responder_sem_tool_use_devolve_texto_direto():
    client = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: _resposta("end_turn", [_texto("Ola! Como posso ajudar?")])
    ))
    mensagens = [{"role": "user", "content": "oi"}]
    r = chat_egc.responder(client, conn=None, mensagens_texto=mensagens, system_prompt="sp", empresas_codigos=["ENERGIA"])
    checar(r["texto"] == "Ola! Como posso ajudar?", "responder -- sem tool_use, devolve o texto direto")
    checar(r["ferramentas_usadas"] == [], "responder -- sem tool_use, nenhuma ferramenta usada")


def test_responder_com_1_chamada_de_ferramenta():
    chamadas = []

    def fake_create(**kw):
        chamadas.append(kw)
        if len(chamadas) == 1:
            return _resposta("tool_use", [_tool_use("consultar_bp_dre", {"empresa": "SMG", "tipo": "BP"})])
        return _resposta("end_turn", [_texto("O CLIENTES da SMG e' R$ 1000.")])

    client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))

    with patch.object(consultas_chat, "consultar_bp_dre", return_value={"contas": [{"conta": "CLIENTES", "valor": 1000.0}]}) as m:
        mensagens = [{"role": "user", "content": "qual o clientes da smg"}]
        r = chat_egc.responder(client, conn=None, mensagens_texto=mensagens, system_prompt="sp", empresas_codigos=["ENERGIA", "SMG"])

    checar(len(chamadas) == 2, "responder -- 1 chamada de ferramenta = 2 idas a API (pede ferramenta, devolve resultado)")
    checar(m.call_count == 1 and m.call_args[0][1] == "SMG", "responder -- executar_ferramenta chamou consultar_bp_dre com os argumentos certos")
    checar(r["texto"] == "O CLIENTES da SMG e' R$ 1000.", "responder -- texto final depois da ferramenta")
    checar(len(r["ferramentas_usadas"]) == 1 and r["ferramentas_usadas"][0]["nome"] == "consultar_bp_dre",
           "responder -- ferramentas_usadas registra a chamada (pro painel lateral)")


def test_responder_com_2_chamadas_em_sequencia():
    chamadas = []

    def fake_create(**kw):
        chamadas.append(kw)
        if len(chamadas) == 1:
            return _resposta("tool_use", [_tool_use("consultar_periodos", {"empresas": ["SMG"]}, tool_id="tu_a")])
        if len(chamadas) == 2:
            return _resposta("tool_use", [_tool_use("consultar_bp_dre", {"empresa": "SMG", "tipo": "BP", "periodo": "2026-06"}, tool_id="tu_b")])
        return _resposta("end_turn", [_texto("Pronto.")])

    client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))

    with patch.object(consultas_chat, "consultar_periodos", return_value={"periodos_por_empresa": {"SMG": ["2026-06"]}}), \
         patch.object(consultas_chat, "consultar_bp_dre", return_value={"contas": []}):
        mensagens = [{"role": "user", "content": "clientes da smg em junho"}]
        r = chat_egc.responder(client, conn=None, mensagens_texto=mensagens, system_prompt="sp", empresas_codigos=["SMG"])

    checar(len(chamadas) == 3, "responder -- 2 chamadas de ferramenta em sequencia = 3 idas a API")
    checar([f["nome"] for f in r["ferramentas_usadas"]] == ["consultar_periodos", "consultar_bp_dre"],
           "responder -- ferramentas_usadas na ordem certa (periodos antes de bp_dre)")
    checar(r["texto"] == "Pronto.", "responder -- texto final depois de 2 ferramentas em sequencia")


def test_responder_limite_seguranca_corta_loop_infinito():
    def fake_create(**kw):
        return _resposta("tool_use", [_tool_use("consultar_periodos", {})])  # nunca para

    client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))

    with patch.object(consultas_chat, "consultar_periodos", return_value={"periodos_por_empresa": {}}):
        mensagens = [{"role": "user", "content": "pergunta que faz o modelo repetir a ferramenta pra sempre"}]
        r = chat_egc.responder(client, conn=None, mensagens_texto=mensagens, system_prompt="sp", empresas_codigos=["SMG"])

    checar(len(r["ferramentas_usadas"]) == chat_egc.MAX_ITERACOES_TOOL_USE,
           f"responder -- limite de seguranca corta em {chat_egc.MAX_ITERACOES_TOOL_USE} chamadas, nao trava")
    checar("Nao consegui concluir" in r["texto"], "responder -- limite atingido devolve mensagem explicativa, nao excecao")


def test_responder_mensagens_api_ignora_tool_turns_antigos():
    """
    mensagens_texto so' tem role/content string (historico de EXIBICAO,
    igual o app_tiago.py monta) -- confirma que responder() nao tenta ler
    tool_use/tool_result de dentro do historico passado (eles nunca
    deveriam estar la', mas se um dict com content nao-string vazasse
    pra mensagens_texto por engano, isso quebraria a chamada real da API).
    """
    client = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: _resposta("end_turn", [_texto("ok")])
    ))
    mensagens = [
        {"role": "user", "content": "pergunta 1"},
        {"role": "assistant", "content": "resposta 1 (so texto, sem tool turns no meio)"},
        {"role": "user", "content": "pergunta 2"},
    ]
    r = chat_egc.responder(client, conn=None, mensagens_texto=mensagens, system_prompt="sp", empresas_codigos=["SMG"])
    checar(r["texto"] == "ok", "responder -- historico multi-turno de so' texto funciona sem erro")


if __name__ == "__main__":
    test_montar_system_prompt_inclui_data_usuario_empresas()
    test_montar_system_prompt_sem_conn_nao_traz_contexto_fiscal()
    test_montar_system_prompt_com_conn_injeta_contexto_fiscal()
    test_montar_system_prompt_falha_ao_buscar_contexto_nao_quebra_prompt()
    test_executar_ferramenta_desconhecida_nao_quebra()
    test_responder_sem_tool_use_devolve_texto_direto()
    test_responder_com_1_chamada_de_ferramenta()
    test_responder_com_2_chamadas_em_sequencia()
    test_responder_limite_seguranca_corta_loop_infinito()
    test_responder_mensagens_api_ignora_tool_turns_antigos()
    print()
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S)")
        sys.exit(1)
    print(f"todas as verificacoes passaram ({24} no total)")
    sys.exit(0)
