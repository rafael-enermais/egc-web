-- EGC-WEB v0.44.3 | BLOCO 22 -- rodar UMA vez no Supabase (SQL Editor), ANTES do zerar_notas_fiscais.sql
-- Faz: (1) cadastra a empresa SETTE (so' usada no fluxo de Notas Fiscais); (2) cria a tabela das notas que o
-- upload deixa fora da conferencia (canceladas/Entrada); (3) liga o Devedor 21 do Sienge a SETTE.
-- Aditivo e idempotente (pode rodar de novo sem estragar nada).

INSERT INTO egc.empresas (codigo, nome, cnpj) VALUES
  ('SETTE', 'Sette Locacoes', '44.914.462/0001-90')
ON CONFLICT (codigo) DO NOTHING;

CREATE TABLE IF NOT EXISTS egc.nf_manifesto_ignoradas (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_id           uuid NOT NULL,
  empresa_codigo      text NOT NULL,
  periodo_referencia  text NOT NULL,
  numero_nota         text,
  data_emissao        date,
  valor               numeric(14,2),
  cfop                text,
  fornecedor_nome     text,
  fornecedor_cnpj     text,
  cnpj_normalizado    text,
  tipo_doc            text,
  natureza            text,
  motivo              text NOT NULL,
  criado_por          text,
  criado_em           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nf_ignoradas_import ON egc.nf_manifesto_ignoradas (import_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON egc.nf_manifesto_ignoradas TO egc_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA egc TO egc_app;
ALTER TABLE egc.nf_manifesto_ignoradas ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS egc_app_full_access ON egc.nf_manifesto_ignoradas;
CREATE POLICY egc_app_full_access ON egc.nf_manifesto_ignoradas FOR ALL TO egc_app USING (true) WITH CHECK (true);

INSERT INTO egc.nf_debtor_empresa (debtor_id, empresa_codigo, atualizado_por)
VALUES (21, 'SETTE', 'seed_sienge')
ON CONFLICT (debtor_id) DO NOTHING;

-- Conferencia: deve devolver SETTE (1), tabela criada (0 linhas) e 7 devedores mapeados
SELECT 'empresa SETTE' AS item, count(*) AS n FROM egc.empresas WHERE codigo = 'SETTE'
UNION ALL SELECT 'nf_manifesto_ignoradas (linhas)', count(*) FROM egc.nf_manifesto_ignoradas
UNION ALL SELECT 'devedores mapeados', count(*) FROM egc.nf_debtor_empresa;
