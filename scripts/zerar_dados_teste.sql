-- =====================================================================
-- EGC -- ZERAR DADOS (v0.45.2)   *** DESTRUTIVO E IRREVERSIVEL ***
-- Rodar no SQL Editor do Supabase (projeto radar-comercial) SO' quando
-- quiser voltar o sistema ao estado "limpo" (ex.: apos os testes, antes
-- de entregar para a contadora).
--
-- APAGA (dados de uso):
--   lancamentos (BP/DRE, ativos e arquivados, correcoes manuais)
--   importacoes (historico de uploads)        despesas_admin_itens
--   relatorios_gerados (log de PDFs)          projecoes, projecoes_ajustes
--   eventos_sistema (log de erros/eventos)
--   NF: nf_manifesto_import, nf_conciliacao, nf_import_historico,
--       nf_bills_orfaos, nf_manifesto_ignoradas, nf_anotacao  (notas importadas, conferencias e pendencias)
--   Erik.AI: chat_mensagem (memoria), chat_acao (trilha de acoes)
--   EBITDA Ajustado (v0.48.0): nao_recorrente, nao_recorrente_confirmacao, nao_recorrente_historico
--
-- MANTEM (configuracao / referencia -- nao e' "dado de teste"):
--   empresas, textos_relatorio, config_relatorio (assinaturas/CPF/etc.),
--   contexto_fiscal, nf_debtor_empresa (mapa credor->empresa),
--   nf_bills_sync e nf_creditors_sync (espelho do Sienge; so' recarrega
--   no botao "Atualizar agora" e todo dia as 04h), contatos_relatorio.
--   -> Se quiser zerar tambem algum desses, descomente a linha no
--      bloco "OPCIONAIS" logo abaixo.
--
-- Os contadores (ids) voltam a 1. As tabelas que ainda nao existirem no
-- seu banco sao ignoradas (nao da erro). Tudo roda numa transacao: se
-- algo falhar, nada e' apagado. Ao final aparece a contagem de cada
-- tabela (tem que ser tudo 0 nas apagadas).
-- =====================================================================

BEGIN;

DO $$
DECLARE
  apagar text[] := ARRAY[
    'lancamentos', 'importacoes', 'despesas_admin_itens', 'relatorios_gerados',
    'projecoes', 'projecoes_ajustes', 'eventos_sistema',
    'nf_manifesto_import', 'nf_conciliacao', 'nf_import_historico', 'nf_bills_orfaos', 'nf_manifesto_ignoradas', 'nf_anotacao',
    'chat_mensagem', 'chat_acao',
    'nao_recorrente', 'nao_recorrente_confirmacao', 'nao_recorrente_historico'
    -- OPCIONAIS (descomente colocando virgula na linha de cima):
    -- , 'contatos_relatorio'      -- assinantes cadastrados (administrador/contador)
    -- , 'nf_debtor_empresa'       -- mapa credor -> empresa (reaprende sozinho)
    -- , 'nf_bills_sync', 'nf_creditors_sync'   -- cache do Sienge (recarrega no botao)
  ];
  existentes text[] := '{}';
  t text;
BEGIN
  FOREACH t IN ARRAY apagar LOOP
    IF to_regclass('egc.' || t) IS NOT NULL THEN
      existentes := existentes || ('egc.' || t);
    END IF;
  END LOOP;
  -- 1 unico TRUNCATE (resolve as FKs entre elas, ex. conciliacao -> manifesto).
  -- Sem CASCADE de proposito: se alguma tabela MANTIDA depender destas, da erro
  -- (e a transacao inteira volta) em vez de apagar o que nao devia.
  EXECUTE 'TRUNCATE TABLE ' || array_to_string(existentes, ', ') || ' RESTART IDENTITY';
  RAISE NOTICE 'Zeradas: %', array_to_string(existentes, ', ');
END $$;

COMMIT;

-- Conferencia (v0.45.2): lista TODA tabela que existe no schema egc, com a situacao e o numero de linhas.
-- Tem que mostrar 0 em todas as APAGADAS. As MANTIDAS mostram quanto ficou. "NAO CLASSIFICADA" = tabela
-- que existe no banco mas nao esta em nenhuma das duas listas deste script (avise antes de entregar).
-- Funciona mesmo que alguma tabela ainda nao exista no seu banco.
SELECT CASE
         WHEN table_name IN ('lancamentos','importacoes','despesas_admin_itens','relatorios_gerados','projecoes',
                             'projecoes_ajustes','eventos_sistema','nf_manifesto_import','nf_conciliacao',
                             'nf_import_historico','nf_bills_orfaos','nf_manifesto_ignoradas','nf_anotacao','chat_mensagem','chat_acao',
                             'nao_recorrente','nao_recorrente_confirmacao','nao_recorrente_historico')
           THEN 'APAGADA'
         WHEN table_name IN ('empresas','textos_relatorio','config_relatorio','contexto_fiscal','contatos_relatorio',
                             'nf_debtor_empresa','nf_bills_sync','nf_creditors_sync')
           THEN 'MANTIDA'
         ELSE 'NAO CLASSIFICADA'
       END AS situacao,
       table_name AS tabela,
       (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from egc.%I', table_name), false, true, '')))[1]::text::int AS linhas
FROM information_schema.tables
WHERE table_schema = 'egc' AND table_type = 'BASE TABLE'
ORDER BY 1, 2;
