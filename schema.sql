-- =====================================================================
-- EGC — Gestao Contabil EnerMais
-- Schema `egc`, isolado dentro do projeto Supabase radar-comercial.
-- Sem troca de dado com o schema `public` do RADAR (ver EGC/00-handoff.md,
-- secao 14 do vault). Role dedicada `egc_app`, sem acesso a outros schemas.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS egc;

-- ---------------------------------------------------------------------
-- Role dedicada — CRIAR SENHA MANUALMENTE DEPOIS (nao commitar senha):
--   ALTER ROLE egc_app WITH PASSWORD 'sua-senha-forte-aqui';
-- A connection string resultante vai só no Secrets do Streamlit Cloud.
-- ---------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'egc_app') THEN
    CREATE ROLE egc_app LOGIN;
  END IF;
END $$;

GRANT CONNECT ON DATABASE postgres TO egc_app;
GRANT USAGE ON SCHEMA egc TO egc_app;
ALTER ROLE egc_app SET search_path = egc;
REVOKE ALL ON SCHEMA public FROM egc_app;

-- ---------------------------------------------------------------------
-- 1. Empresas (6 fixas)
-- ---------------------------------------------------------------------
CREATE TABLE egc.empresas (
  codigo  text PRIMARY KEY,
  nome    text NOT NULL,
  cnpj    text NOT NULL UNIQUE,
  ativo   boolean NOT NULL DEFAULT true
);

INSERT INTO egc.empresas (codigo, nome, cnpj) VALUES
  ('ENERGIA', 'Enermais Energia Ltda',     '47.040.664/0001-48'),
  ('SMG',     'SMG Solucoes Ltda',         '18.387.666/0001-00'),
  ('ENG',     'Enermais Engenharia Ltda',  '50.337.899/0001-00'),
  ('RENOV',   'Enermais Renovaveis Ltda',  '51.671.106/0001-58'),
  ('CONST',   'Enermais Construtora Ltda', '55.244.465/0001-80'),
  ('SOL',     'Enermais Solucoes Ltda',    '60.353.219/0001-04')
ON CONFLICT (codigo) DO NOTHING;

-- ---------------------------------------------------------------------
-- 2. Lancamentos — substitui BASE_HISTORICA (sem a linha RESUMO hibrida;
--    indicadores agregados viram VIEW, ver fim do arquivo)
-- ---------------------------------------------------------------------
CREATE TABLE egc.lancamentos (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_codigo text NOT NULL REFERENCES egc.empresas(codigo),
  tipo           text NOT NULL CHECK (tipo IN ('BP','DRE')),
  periodo        date NOT NULL,
  grupo          text NOT NULL,
  conta          text NOT NULL,
  valor          numeric(18,2) NOT NULL,
  origem         text NOT NULL DEFAULT 'PDF Importado',
  pdf_original   numeric(18,2),
  status         text NOT NULL DEFAULT 'ATIVO' CHECK (status IN ('ATIVO','INATIVO')),
  arquivo_pdf    text,
  usuario        text,
  criado_em      timestamptz NOT NULL DEFAULT now(),
  atualizado_em  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_lancamentos_busca   ON egc.lancamentos (empresa_codigo, periodo, tipo, grupo, conta) WHERE status = 'ATIVO';
CREATE INDEX idx_lancamentos_periodo ON egc.lancamentos (periodo);

-- ---------------------------------------------------------------------
-- 3. Importacoes — log de cada lote (substitui LOG_IMPORTACAO)
-- ---------------------------------------------------------------------
CREATE TABLE egc.importacoes (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_codigo text REFERENCES egc.empresas(codigo),
  periodo        date,
  arquivos       text[],
  nivel          text NOT NULL CHECK (nivel IN ('OK','AVISO','ALERTA','ERRO')),
  tipo           text,
  mensagem       text,
  usuario        text,
  criado_em      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 4. Relatorios gerados (substitui LOG_PDF_GERADO)
-- ---------------------------------------------------------------------
CREATE TABLE egc.relatorios_gerados (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresas   text[] NOT NULL,
  periodos   date[] NOT NULL,
  arquivo    text NOT NULL,
  usuario    text,
  gerado_em  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 5. Textos do relatorio — 13 fixos + 2 contextuais (ver secao 16 do vault)
-- ---------------------------------------------------------------------
CREATE TABLE egc.textos_relatorio (
  chave             text PRIMARY KEY,
  tipo              text NOT NULL CHECK (tipo IN ('fixo','contextual')),
  texto_fixo        text,
  texto_individual  text,
  texto_grupo       text,
  atualizado_por    text,
  atualizado_em     timestamptz NOT NULL DEFAULT now()
);

INSERT INTO egc.textos_relatorio (chave, tipo, texto_fixo) VALUES
('TEXTO_CAIXA', 'fixo', 'Os valores que compoem as contas de caixa e equivalentes incluem depositos bancarios e investimentos em aplicacoes de curto prazo e alta liquidez, com vencimentos nao superior a doze meses e prontamente conversiveis em montante de caixa e sem riscos de alteracao de valor.'),
('TEXTO_DUPLICATAS', 'fixo', 'As contas a receber representam os valores a receber de clientes pela prestacao de servicos no decurso normal das atividades da empresa e sao reconhecidas pelo valor faturado. Se o prazo de recebimento e igual ou inferior a doze meses, as contas a receber sao classificadas no circulante, em caso de prazo superior, estao apresentadas no ativo nao circulante. Ja os valores apresentados na conta de Outros Creditos a receber, representam os mutuos entre as empresas do Grupo, tributos a recuperar; gerados a partir da retencao em faturamentos e adiantamento a terceiros.'),
('TEXTO_INVEST', 'fixo', 'No grupo de investimentos ha registro dos valores imobilizados por meio de bens imoveis, moveis, veiculos e equipamentos e suas respectivas depreciacoes. Alem dos valores referentes as participacoes nas empresas do grupo, os valores incluem os gastos que sao diretamente atribuiveis a aquisicao do ativo. As depreciacoes iniciam a partir da data de aquisicao dos ativos, e sao calculadas pelo metodo linear de resultado baseado na vida util economica estimada de cada bem, aplicando as taxas usadas no mercado. Os ganhos e perdas pela alienacao de um item imobilizado, apurado pela diferenca entre os recursos da alienacao e o valor contabil do imobilizado, sao reconhecidos em conta de receita ou despesa, no resultado.'),
('TEXTO_EMPREST', 'fixo', 'Os emprestimos e financiamentos sao reconhecidos pelo valor amortizado, acrescidos dos encargos e juros proporcionais ao periodo incorrido. Classificados no passivo circulante os valores com vencimentos iguais ou inferiores a doze meses, e no passivo nao circulante os valores que deverao ser liquidados em periodo superior a este.'),
('TEXTO_OBRIG_TRIB', 'fixo', 'Os encargos sao calculados com base nas leis tributarias vigentes na data do balanco. Os valores apresentados representam liquidos, por entidade contribuinte, no passivo quando houver montantes a pagar, ou no ativo quando os montantes antecipadamente pagos excedem o total devido na data do relatorio.'),
('TEXTO_OBRIG_TRAB', 'fixo', 'Composto pelos valores a titulo de remuneracoes, e encargos trabalhistas. Os encargos trabalhistas conhecidos e/ou passiveis de apuracao estao sendo contabilizados de acordo com o regime de competencia.'),
('TEXTO_OUTRAS_OBR', 'fixo', 'Composto pelos valores de adiantamento realizados por clientes, contas a pagar que e formado por valores referente as parcelas de aquisicao dos imoveis, e mutuo entre as empresas do Grupo.'),
('TEXTO_PASSIVO', 'fixo', 'Os valores que compoem o Passivo e o Patrimonio Liquido foram extraidos do Balanco Patrimonial.'),
('TEXTO_DRE', 'fixo', 'A Demonstracao do Resultado do Exercicio apresenta as receitas auferidas, os custos incorridos e as despesas operacionais do periodo.'),
('TEXTO_CUSTOS', 'fixo', 'Os valores registrados como custos dos produtos e servicos, representam os valores diretamente atrelados a prestacao de servico das empresas, tais como mao de obra aplicada, e demais custos e encargos voltados a mao de obra, servicos terceirizados, custos com localizacao, etc.'),
('TEXTO_DESPESAS', 'fixo', 'Todos os demais valores desembolsados para operacionalizacao da empresa estao registrados nas contas de despesas operacionais.'),
('TEXTO_LUCRO', 'fixo', 'O resultado do exercicio representa o lucro ou prejuizo apurado apos todas as receitas, deducoes, custos e despesas do periodo.'),
('TEXTO_ANEXOS', 'fixo', 'Anexar nesta secao os demonstrativos contabeis assinados e demais documentos comprobatorios do periodo.')
ON CONFLICT (chave) DO NOTHING;

INSERT INTO egc.textos_relatorio (chave, tipo, texto_individual, texto_grupo) VALUES
('TEXTO_APRESENTACAO', 'contextual',
 'A {{ empresa }} pertence ao grupo empresarial Enermais, aqui denominado "Grupo Enermais", tendo suas atividades iniciadas em 2023. Inicialmente composto por tres empresas: Enermais Energia Ltda, Enermais Engenharia Ltda e Enermais Renovaveis Ltda. Em 2025 o grupo passou a ser composto por seis empresas: Enermais Energia Ltda, SMG Solucoes Ltda, Enermais Engenharia Ltda, Enermais Renovaveis Ltda, Enermais Construtora Ltda e Enermais Solucoes Ltda. O presente relatorio apresenta os principais ativos, passivos e resultados da {{ empresa }} {% if periodos|length > 1 %}nos periodos de {{ periodos_fmt }}{% else %}no periodo de {{ periodo }}{% endif %}.',
 'O Grupo Enermais e composto por {{ empresas|length }} empresas — {{ empresas_fmt }} — desde 2025 (inicialmente 3, desde 2023). O presente relatorio consolida os principais ativos, passivos e resultados dessas empresas {% if periodos|length > 1 %}nos periodos de {{ periodos_fmt }}{% else %}no periodo de {{ periodo }}{% endif %}.'),
('TEXTO_OBJETIVO', 'contextual',
 'O relatorio a seguir tem como principal objetivo apresentar os ativos, passivos e resultados da {{ empresa }}. A {{ empresa }}, junto das demais empresas que compoem o Grupo Enermais, atua mutuamente nos contratos, estando uma no quadro societario da outra, e com administracao e controle gerencial em comum.',
 'O relatorio a seguir tem como principal objetivo apresentar os ativos, passivos e resultados consolidados das {{ empresas|length }} empresas do Grupo Enermais. As empresas atuam mutuamente nos contratos, estando uma no quadro societario da outra, e com administracao e controle gerencial em comum.')
ON CONFLICT (chave) DO NOTHING;

-- ---------------------------------------------------------------------
-- 6. Config geral — assinaturas + dados padrao do relatorio
-- ---------------------------------------------------------------------
CREATE TABLE egc.config_relatorio (
  chave          text PRIMARY KEY,
  valor          text,
  atualizado_por text,
  atualizado_em  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO egc.config_relatorio (chave, valor) VALUES
  ('NOME_ADMINISTRADOR', 'Alex Troiano Rodrigues'),
  ('CARGO_ADMINISTRADOR', 'Administrador'),
  ('CPF_ADMINISTRADOR', '326.681.198-47'),
  ('NOME_CONTADOR', 'Responsavel Contabil'),
  ('CARGO_CONTADOR', NULL),
  ('CRC_CONTADOR', NULL),
  ('NOME_ASSINANTE2', 'Edilson Nazario'),
  ('CARGO_ASSINANTE2', 'Diretor Financeiro'),
  ('CPF_ASSINANTE2', NULL),
  ('MODELO_RELATORIO_PADRAO', 'MENSAL')
ON CONFLICT (chave) DO NOTHING;

-- ---------------------------------------------------------------------
-- 7. View de indicadores agregados (substitui a linha RESUMO hibrida)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW egc.v_indicadores AS
SELECT
  empresa_codigo,
  periodo,
  MAX(valor) FILTER (WHERE tipo='BP'  AND conta='TOTAL DO ATIVO')             AS ativo_total,
  MAX(valor) FILTER (WHERE tipo='BP'  AND conta='TOTAL DO PASSIVO')           AS passivo_total,
  MAX(valor) FILTER (WHERE tipo='DRE' AND conta='RECEITA OPERACIONAL LIQUIDA') AS receita_liquida,
  MAX(valor) FILTER (WHERE tipo='DRE' AND conta='LUCRO BRUTO')                AS lucro_bruto,
  MAX(valor) FILTER (WHERE tipo='DRE' AND conta='LUCRO LIQUIDO DO EXERCICIO')  AS lucro_liquido
FROM egc.lancamentos
WHERE status = 'ATIVO'
GROUP BY empresa_codigo, periodo;

-- ---------------------------------------------------------------------
-- Grants + RLS — isolamento total do schema egc
-- ---------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA egc TO egc_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA egc TO egc_app;
GRANT SELECT ON egc.v_indicadores TO egc_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA egc GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO egc_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA egc GRANT USAGE, SELECT ON SEQUENCES TO egc_app;

ALTER TABLE egc.empresas            ENABLE ROW LEVEL SECURITY;
ALTER TABLE egc.lancamentos         ENABLE ROW LEVEL SECURITY;
ALTER TABLE egc.importacoes         ENABLE ROW LEVEL SECURITY;
ALTER TABLE egc.relatorios_gerados  ENABLE ROW LEVEL SECURITY;
ALTER TABLE egc.textos_relatorio    ENABLE ROW LEVEL SECURITY;
ALTER TABLE egc.config_relatorio    ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT unnest(ARRAY['empresas','lancamentos','importacoes','relatorios_gerados','textos_relatorio','config_relatorio'])
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS egc_app_full_access ON egc.%I', t);
    EXECUTE format('CREATE POLICY egc_app_full_access ON egc.%I FOR ALL TO egc_app USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;

-- Fim. Rodar este arquivo inteiro no SQL Editor do Supabase (projeto radar-comercial).
-- Depois: ALTER ROLE egc_app WITH PASSWORD '...' (senha forte, nao commitar).
