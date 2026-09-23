# -*- coding: utf-8 -*-
"""
Motor do chat (tool-use) do Dashboard + Chat -- 6o item da fila combinada
com Rafael (Visao Grupo -> rodape -> Dashboard de Projecao -> chat).
Modulo "fino" tipo projecao.py/visao_grupo.py: sem streamlit direto
(recebe client Anthropic e conn ja prontos de quem chama), pra poder
testar mockando so' client.messages.create (sem chave de API real nos
testes -- task #13).

Padrao replicado de $HOME/mnt/Projetos/TIA.go/arquivos/app_tiago.py (app
de referencia do proprio Rafael, ja em producao): st.session_state.mensagens
guarda so' texto puro (role/content string) pra exibir no historico; a
CADA pergunta nova, mensagens_api e' reconstruido do zero a partir desse
historico de texto (perde os turnos de tool_use/tool_result de perguntas
ANTERIORES de proposito -- mantem o contexto da API enxuto, nao precisa
re-mandar toda consulta anterior de novo). O loop de tool_use roda dentro
de UMA pergunta (varias chamadas de ferramenta pra responder ESSA
pergunta), nao entre perguntas.

MODEL_ID checado ao vivo em 22/09/2026 via platform.claude.com/docs (nao
confiar em treino pra nome de modelo -- muda) -- "claude-sonnet-5" e' o
Sonnet atual recomendado (mesmo modelo desta propria sessao do Claude
Code). app_tiago.py usa "claude-sonnet-4-5" com um TODO "confirmar antes
de producao" nunca resolvido -- nao copiado aqui de proposito.

MAX_ITERACOES_TOOL_USE: protecao contra loop indefinido de chamadas de
ferramenta (custo de API) -- nao existe no app_tiago.py, adicionado aqui
por seguranca (nenhum motivo praticpara essa pergunta pontual do sistema
EGC# precisar de mais de 4-5 chamadas em sequencia).
"""
from __future__ import annotations

import datetime
from typing import Optional

import consultas_chat
import db

MODEL_ID = "claude-sonnet-5"
CONTEXTO_FISCAL_MAX_CHARS = 4000  # ver montar_system_prompt -- teto defensivo, nao existe hoje mas evita prompt gigante se a tabela crescer sem controle
MAX_TOKENS = 1024
MAX_ITERACOES_TOOL_USE = 6

TOOLS = [
    {
        "name": "consultar_periodos",
        "description": (
            "Lista os periodos (mes/ano) com lancamento ATIVO por empresa. Use "
            "ANTES de consultar_bp_dre ou consultar_visao_grupo com um periodo "
            "especifico, pra confirmar que aquele periodo existe de verdade -- "
            "nunca invente um periodo sem checar aqui primeiro."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "empresas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Codigos das empresas (ex. ['ENERGIA','SMG']) -- vazio ou omitido consulta todas as 6 do grupo.",
                }
            },
        },
    },
    {
        "name": "consultar_bp_dre",
        "description": (
            "Balanco Patrimonial (BP) ou DRE de UMA empresa num periodo. Sem "
            "'periodo' informado, usa o periodo mais recente disponivel pra essa "
            "empresa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "empresa": {"type": "string", "description": "Codigo da empresa: ENERGIA, SMG, ENG, RENOV, CONST ou SOL."},
                "tipo": {"type": "string", "enum": ["BP", "DRE"], "description": "BP (Balanco Patrimonial) ou DRE."},
                "periodo": {"type": "string", "description": "Periodo no formato 'AAAA-MM' (ex. '2026-06') -- opcional, sem isso usa o mais recente."},
            },
            "required": ["empresa", "tipo"],
        },
    },
    {
        "name": "consultar_visao_grupo",
        "description": (
            "Visao CONSOLIDADA de varias empresas do grupo EnerMais num periodo, BP "
            "ou DRE. 'macro' separa Enermais Energia x demais empresas consolidadoras "
            "(com % de participacao); 'especifica' abre empresa por empresa, sem "
            "agrupar. Use pra perguntas sobre o grupo todo ou comparando empresas -- "
            "pra 1 empresa isolada, use consultar_bp_dre."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tipo": {"type": "string", "enum": ["BP", "DRE"]},
                "empresas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Codigos das empresas a incluir -- vazio ou omitido inclui todas as 6.",
                },
                "periodo": {"type": "string", "description": "Periodo 'AAAA-MM' -- opcional, sem isso usa o mais recente entre as empresas escolhidas."},
                "visao": {"type": "string", "enum": ["macro", "especifica"], "description": "macro (padrao) ou especifica."},
            },
            "required": ["tipo"],
        },
    },
    {
        "name": "consultar_indicadores",
        "description": (
            "Indicadores contabeis (Liquidez Corrente, Capital de Giro, Endividamento "
            "Geral, Margem Bruta, Margem Liquida, ROA, ROE) no periodo mais recente "
            "disponivel. 1 empresa em 'empresas' -> indicadores so' dela; 2+ empresas "
            "(ou vazio/omitido, que usa todas as 6) -> indicadores CONSOLIDADOS do "
            "grupo (soma antes de calcular os indices). Use pra perguntas tipo "
            "'como esta a liquidez da SMG', 'a margem do grupo melhorou ou piorou', "
            "'qual o endividamento geral' -- nao serve pra pegar 1 conta especifica "
            "(pra isso, consultar_bp_dre)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "empresas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Codigos das empresas -- vazio/omitido ou 2+ calcula consolidado do grupo; 1 codigo calcula so' dessa empresa.",
                },
            },
        },
    },
    {
        "name": "consultar_completude",
        "description": (
            "Pra cada periodo (mes/ano) com QUALQUER lancamento ativo entre as "
            "empresas escolhidas, mostra se esta' Completo (BP+DRE de TODAS as "
            "empresas) ou quais empresas ainda faltam. Use pra perguntas tipo "
            "'quais periodos/empresas estao faltando dado', 'o que falta importar "
            "pra fechar o grupo', 'esta tudo completo?' -- NAO rastreia PDF/upload em "
            "si, so' ausencia de lancamento no banco (na pratica o mesmo sinal: PDF "
            "nao importado ou importacao falhou)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "empresas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Codigos das empresas a checar -- vazio ou omitido checa todas as 6.",
                },
            },
        },
    },
]


def montar_system_prompt(
    usuario_email: str, empresas_codigos: list[str], conn=None, hoje: Optional[datetime.date] = None,
) -> str:
    """
    hoje injetavel (nao datetime.date.today() direto) pra dar pra testar
    determinismo -- mesma razao do TIA.go ter tido um bug real de data
    relativa mal calculada antes de fixar a data de hoje no prompt (visto
    ao vivo pelo Rafael, corrigido na v0.8.1 de la).

    conn (opcional, default None): quando informada, busca
    egc.contexto_fiscal e anexa como bloco de CONHECIMENTO DE REFERENCIA
    no fim do prompt (pedido do Rafael 22/09/2026 sobre a Reforma
    Tributaria: "queria imputar de alguma forma, pelo menos p saber ou
    ter ciencia dessas informacoes"). None (default, e' o que os testes
    existentes usam) simplesmente pula esse bloco -- nao quebra nada que
    ja chamava esta funcao sem conn. Falha ao buscar (banco fora do ar)
    tambem so' pula o bloco, nunca quebra o prompt inteiro -- ver
    try/except abaixo.

    Importante: este bloco NAO afrouxa a REGRA CRITICA abaixo (so'
    responder com dado vindo de ferramenta) -- e' conteudo FIXO, curado
    por nos (nao pelo modelo), claramente rotulado como referencia, nunca
    como se fosse consulta ao BP/DRE.
    """
    hoje = hoje or datetime.date.today()
    bloco_contexto_fiscal = ""
    if conn is not None:
        try:
            linhas = db.listar_contexto_fiscal(conn)
        except Exception:
            linhas = []
        if linhas:
            texto = "\n\n".join(f"- {l['titulo']}: {l['conteudo']}" for l in linhas)
            if len(texto) > CONTEXTO_FISCAL_MAX_CHARS:
                texto = texto[:CONTEXTO_FISCAL_MAX_CHARS] + " [...]"
            bloco_contexto_fiscal = (
                "\n\nCONHECIMENTO DE REFERENCIA (contexto fixo, curado por nos -- NAO e' dado "
                "de BP/DRE, nao veio de nenhuma ferramenta, e nao deve ser tratado como se "
                "fosse consulta ao banco). Use isso so' pra dar contexto/explicacao quando "
                "fizer sentido (ex. o usuario perguntar sobre a Reforma Tributaria, ou "
                "especular se uma variacao de margem pode estar ligada a ela) -- nunca pra "
                "calcular ou afirmar um valor financeiro que devia vir de consultar_bp_dre/"
                "consultar_visao_grupo:\n" + texto
            )
    return (
        "Voce e' o Erik.AI, assistente da EnerMais -- ajuda a contadora e a gerencia a "
        "consultar Balanco Patrimonial (BP), DRE, Visao Grupo (consolidado das 6 "
        "empresas) e indicadores contabeis/completude de dados ja importados no "
        "sistema.\n"
        f"Hoje e' {hoje.strftime('%d/%m/%Y')}. Use essa data como referencia pra "
        "qualquer pergunta relativa ('mes passado', 'ultimo periodo', etc) -- nunca "
        "calcule 'hoje' sozinho.\n"
        f"Usuario logado: {usuario_email}.\n"
        f"Empresas do grupo (codigo): {', '.join(empresas_codigos)}.\n\n"
        "REGRA CRITICA: responda SO' com dado que veio de verdade das ferramentas -- "
        "nunca invente numero, conta ou periodo. Se uma ferramenta devolver 'erro' "
        "(ex. periodo nao encontrado), diga isso pro usuario e mostre os periodos "
        "disponiveis que a propria ferramenta retornou, em vez de tentar adivinhar.\n\n"
        "Fora de escopo por enquanto (nao existe ferramenta pra isso -- se "
        "perguntarem, explique que ainda nao esta disponivel no chat): projecao/"
        "previsao futura de BP/DRE (existe um Dashboard de Projecao separado no "
        "menu, mas com pouco historico real o metodo hoje e' basico e ainda nao "
        "esta ligado ao chat), correcao de lancamento, upload/processamento de PDF "
        "em si (consultar_completude mostra o que falta em termos de lancamento no "
        "banco, que e' o sinal mais proximo disso -- mas nao sabe se um PDF foi "
        "enviado e falhou vs nunca foi enviado).\n\n"
        "Guia de escolha de ferramenta:\n"
        "- Pergunta sobre 1 empresa especifica (ex. 'qual o CLIENTES da SMG', "
        "'total do Ativo Circulante da Energia') -> consultar_bp_dre.\n"
        "- Pergunta sobre o grupo todo, comparando empresas, ou 'quanto a Energia "
        "representa do total' -> consultar_visao_grupo (visao='macro' pra Energia x "
        "Consolidadoras, 'especifica' pra abrir empresa por empresa).\n"
        "- Antes de usar um periodo especifico que o usuario mencionou (ex. 'em "
        "maio'), confirme com consultar_periodos que aquele periodo existe pra(s) "
        "empresa(s) certa(s) -- sem periodo especifico mencionado, so' chame "
        "consultar_bp_dre/consultar_visao_grupo direto (eles ja' usam o mais recente "
        "sozinhos).\n"
        "- Pergunta sobre saude financeira/indice (liquidez, endividamento, margem, "
        "ROA, ROE), de 1 empresa ou do grupo consolidado -> consultar_indicadores.\n"
        "- Pergunta sobre o que falta, o que esta incompleto, quais periodos/empresas "
        "sem dado -> consultar_completude."
        + bloco_contexto_fiscal
    )


def executar_ferramenta(conn, nome: str, entrada: dict, empresas_codigos: list[str]) -> dict:
    if nome == "consultar_periodos":
        empresas = entrada.get("empresas") or empresas_codigos
        return consultas_chat.consultar_periodos(conn, empresas)
    if nome == "consultar_bp_dre":
        return consultas_chat.consultar_bp_dre(
            conn, entrada.get("empresa"), entrada.get("tipo"), entrada.get("periodo")
        )
    if nome == "consultar_visao_grupo":
        empresas = entrada.get("empresas") or empresas_codigos
        return consultas_chat.consultar_visao_grupo(
            conn, entrada.get("tipo"), empresas, entrada.get("periodo"), entrada.get("visao", "macro")
        )
    if nome == "consultar_indicadores":
        empresas = entrada.get("empresas") or empresas_codigos
        return consultas_chat.consultar_indicadores(conn, empresas)
    if nome == "consultar_completude":
        empresas = entrada.get("empresas") or empresas_codigos
        return consultas_chat.consultar_completude(conn, empresas)
    return {"erro": f"ferramenta desconhecida: {nome}"}


def responder(client, conn, mensagens_texto: list[dict], system_prompt: str, empresas_codigos: list[str]) -> dict:
    """
    mensagens_texto: historico de EXIBICAO (role/content SEMPRE string),
    JA' incluindo a pergunta nova do usuario como ultimo elemento -- quem
    chama (a pagina Streamlit) guarda isso em st.session_state.mensagens,
    igual ao app_tiago.py.

    Reconstroi mensagens_api do zero a partir do historico de texto (ver
    docstring do modulo -- nao herda tool_use de perguntas anteriores),
    roda o loop tool_use ate' a resposta final vir so' com texto OU o
    limite de seguranca (MAX_ITERACOES_TOOL_USE) ser atingido. Retorna
    {"texto": resposta final, "ferramentas_usadas": [...]} --
    ferramentas_usadas alimenta o painel "Dashboard + chat lado a lado"
    (mostra a ultima consulta feita, sem re-perguntar ao banco).
    """
    mensagens_api = [{"role": m["role"], "content": m["content"]} for m in mensagens_texto]
    ferramentas_usadas = []

    resposta = client.messages.create(
        model=MODEL_ID, max_tokens=MAX_TOKENS, system=system_prompt, tools=TOOLS, messages=mensagens_api,
    )

    iteracoes = 0
    while resposta.stop_reason == "tool_use" and iteracoes < MAX_ITERACOES_TOOL_USE:
        iteracoes += 1
        tool_uses = [b for b in resposta.content if b.type == "tool_use"]
        resultados = []
        for tu in tool_uses:
            resultado = executar_ferramenta(conn, tu.name, tu.input, empresas_codigos)
            ferramentas_usadas.append({"nome": tu.name, "entrada": tu.input, "resultado": resultado})
            resultados.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(resultado)})
        mensagens_api.append({"role": "assistant", "content": resposta.content})
        mensagens_api.append({"role": "user", "content": resultados})
        resposta = client.messages.create(
            model=MODEL_ID, max_tokens=MAX_TOKENS, system=system_prompt, tools=TOOLS, messages=mensagens_api,
        )

    if resposta.stop_reason == "tool_use":
        # limite de seguranca atingido -- nunca deveria acontecer em uso
        # normal (perguntas do EGC nao precisam de mais de 6 chamadas em
        # sequencia), mas evita loop indefinido consumindo API por bug de
        # prompt/ferramenta em vez de travar silenciosamente.
        texto_final = (
            "Nao consegui concluir essa consulta em tempo (muitas chamadas de "
            "ferramenta em sequencia). Tenta reformular a pergunta de forma mais "
            "especifica (uma empresa/periodo por vez)."
        )
    else:
        texto_final = "".join(b.text for b in resposta.content if b.type == "text")

    return {"texto": texto_final, "ferramentas_usadas": ferramentas_usadas}
