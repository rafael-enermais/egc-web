-- EGC-WEB | zerar SOMENTE a conferencia de Notas Fiscais (02/10/2026, v0.44.3)
-- Rodar DEPOIS do bloco22_sette_e_ignoradas.sql.
-- APAGA: manifestos importados, conferencias, historico de conferencias, "Sienge sem nota", notas ignoradas e status de pendencia.
-- MANTEM: nf_debtor_empresa (de-para), nf_bills_sync / nf_creditors_sync (espelho do Sienge) e TODO o resto do EGC.

BEGIN;
TRUNCATE TABLE egc.nf_conciliacao, egc.nf_manifesto_import, egc.nf_import_historico, egc.nf_bills_orfaos,
               egc.nf_manifesto_ignoradas RESTART IDENTITY;
COMMIT;

SELECT 'APAGADA' AS situacao, 'nf_manifesto_import' AS tabela, count(*) AS linhas FROM egc.nf_manifesto_import
UNION ALL SELECT 'APAGADA', 'nf_conciliacao', count(*) FROM egc.nf_conciliacao
UNION ALL SELECT 'APAGADA', 'nf_import_historico', count(*) FROM egc.nf_import_historico
UNION ALL SELECT 'APAGADA', 'nf_bills_orfaos', count(*) FROM egc.nf_bills_orfaos
UNION ALL SELECT 'APAGADA', 'nf_manifesto_ignoradas', count(*) FROM egc.nf_manifesto_ignoradas
UNION ALL SELECT 'MANTIDA', 'nf_debtor_empresa', count(*) FROM egc.nf_debtor_empresa
UNION ALL SELECT 'MANTIDA', 'nf_bills_sync', count(*) FROM egc.nf_bills_sync;
