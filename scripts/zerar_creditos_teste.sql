-- EGC-WEB | Creditos PIS/COFINS (bloco 28) -- ZERAR OS DADOS DE TESTE
-- Rodar no SQL Editor do Supabase (projeto radar-comercial). So' mexe nas tabelas egc.cred_* .
-- NAO toca em BP/DRE, Notas Fiscais, Sienge, Nao Recorrentes, usuarios nem em nenhuma outra tabela.
-- A carga fixa jan-jul (egc.cred_historico) FICA, a nao ser que voce tire o comentario da PARTE 3.
-- Tudo roda numa transacao: se algo falhar, nada e' apagado.

-- PARTE 0 -- quanto tem hoje (so' leitura)
SELECT 'cred_sped_envio' AS tabela, count(*) AS linhas FROM egc.cred_sped_envio
UNION ALL SELECT 'cred_sped_linha',   count(*) FROM egc.cred_sped_linha
UNION ALL SELECT 'cred_sped_receita', count(*) FROM egc.cred_sped_receita
UNION ALL SELECT 'cred_det_envio',    count(*) FROM egc.cred_det_envio
UNION ALL SELECT 'cred_det_linha',    count(*) FROM egc.cred_det_linha
UNION ALL SELECT 'cred_ajuste',       count(*) FROM egc.cred_ajuste
UNION ALL SELECT 'cred_ajuste_log',   count(*) FROM egc.cred_ajuste_log
UNION ALL SELECT 'cred_historico (carga fixa jan-jul)', count(*) FROM egc.cred_historico;

BEGIN;

-- PARTE 1 -- envios (SPED e Detalhamento) e as linhas deles. Primeiro os filhos, depois os pais (chave estrangeira).
DELETE FROM egc.cred_sped_linha;
DELETE FROM egc.cred_sped_receita;
DELETE FROM egc.cred_sped_envio;
DELETE FROM egc.cred_det_linha;
DELETE FROM egc.cred_det_envio;

-- PARTE 2 -- edicoes da conferencia e a trilha de auditoria delas
DELETE FROM egc.cred_ajuste;
DELETE FROM egc.cred_ajuste_log;

-- PARTE 3 -- (OPCIONAL) carga fixa jan-jul. Deixe comentado para MANTER o historico.
-- Se apagar, e' so' rodar de novo o seed SQL-0028b (guardado no vault) para trazer de volta.
-- DELETE FROM egc.cred_historico;

-- Conferencia: tudo deve estar zerado, menos o historico (se a parte 3 ficou comentada).
SELECT 'cred_sped_envio' AS tabela, count(*) AS linhas FROM egc.cred_sped_envio
UNION ALL SELECT 'cred_sped_linha',   count(*) FROM egc.cred_sped_linha
UNION ALL SELECT 'cred_sped_receita', count(*) FROM egc.cred_sped_receita
UNION ALL SELECT 'cred_det_envio',    count(*) FROM egc.cred_det_envio
UNION ALL SELECT 'cred_det_linha',    count(*) FROM egc.cred_det_linha
UNION ALL SELECT 'cred_ajuste',       count(*) FROM egc.cred_ajuste
UNION ALL SELECT 'cred_ajuste_log',   count(*) FROM egc.cred_ajuste_log
UNION ALL SELECT 'cred_historico (carga fixa jan-jul)', count(*) FROM egc.cred_historico;

COMMIT;
