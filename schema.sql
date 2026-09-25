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

-- =====================================================================
-- 8. Projecao (BP/DRE) — adicionado 22/09/2026, fila combinada com o
--    Rafael (Visao Grupo -> rodape -> dashboard de projecao -> chat).
--    2 tabelas: ajustes manuais (input) + projecoes materializadas
--    (baseline+ajuste+total), gravadas no banco pra ficarem disponiveis
--    a qualquer consumidor externo (ex. futura integracao com o app
--    TIA.go) sem precisar rodar o modelo de novo. `tipo` ja aceita
--    FLUXO_CAIXA na constraint (nao implementado ainda -- so' destravado
--    pra nao exigir migracao de novo quando entrada/saida for decidido).
--    IF NOT EXISTS pra este bloco poder ser rodado sozinho (nao precisa
--    rodar o arquivo inteiro de novo, as tabelas 1-7 ja existem em
--    producao).
-- =====================================================================

CREATE TABLE IF NOT EXISTS egc.projecoes_ajustes (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_codigo text NOT NULL REFERENCES egc.empresas(codigo),
  tipo           text NOT NULL CHECK (tipo IN ('BP','DRE','FLUXO_CAIXA')),
  periodo        date NOT NULL,
  grupo          text NOT NULL,
  conta          text NOT NULL,
  valor_ajuste   numeric(18,2) NOT NULL,
  descricao      text NOT NULL,
  status         text NOT NULL DEFAULT 'ATIVO' CHECK (status IN ('ATIVO','INATIVO')),
  usuario        text,
  criado_em      timestamptz NOT NULL DEFAULT now(),
  atualizado_em  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projecoes_ajustes_busca
  ON egc.projecoes_ajustes (empresa_codigo, periodo, tipo, grupo, conta) WHERE status = 'ATIVO';

CREATE TABLE IF NOT EXISTS egc.projecoes (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  empresa_codigo      text NOT NULL REFERENCES egc.empresas(codigo),
  tipo                text NOT NULL CHECK (tipo IN ('BP','DRE','FLUXO_CAIXA')),
  periodo             date NOT NULL,
  grupo               text NOT NULL,
  conta               text NOT NULL,
  valor_base          numeric(18,2) NOT NULL,
  valor_ajuste        numeric(18,2) NOT NULL DEFAULT 0,
  valor_projetado     numeric(18,2) NOT NULL,
  metodo              text NOT NULL,
  periodos_historico  integer NOT NULL,
  gerado_em           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (empresa_codigo, tipo, periodo, grupo, conta)
);

CREATE INDEX IF NOT EXISTS idx_projecoes_busca
  ON egc.projecoes (empresa_codigo, periodo, tipo, grupo, conta);

GRANT SELECT, INSERT, UPDATE, DELETE ON egc.projecoes_ajustes, egc.projecoes TO egc_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA egc TO egc_app;

ALTER TABLE egc.projecoes_ajustes ENABLE ROW LEVEL SECURITY;
ALTER TABLE egc.projecoes         ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS egc_app_full_access ON egc.projecoes_ajustes;
CREATE POLICY egc_app_full_access ON egc.projecoes_ajustes FOR ALL TO egc_app USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS egc_app_full_access ON egc.projecoes;
CREATE POLICY egc_app_full_access ON egc.projecoes FOR ALL TO egc_app USING (true) WITH CHECK (true);

-- Fim do bloco 8. Rodar so' este bloco (da linha "-- 8. Projecao" ate aqui)
-- no SQL Editor do Supabase (projeto radar-comercial) -- nao precisa
-- rodar o arquivo inteiro de novo.

-- =====================================================================
-- 9. Eventos do sistema — log completo (task #16, pedido do Rafael
--    22/09/2026: "deixa pronto pra ter logs e msm sistematica, de forma
--    q se der erro conseguimos arrumar facil via log"). Generico e
--    aditivo -- NAO substitui egc.importacoes nem egc.relatorios_gerados
--    (continuam do jeito que estao, ja usados de verdade pelo Importar
--    PDF); cobre tudo que ainda nao tinha lugar nenhum pra registrar erro
--    (Revisao/Correcao, Arquivar/Recuperar, chat, Inicio) -- ver
--    db.registrar_evento() e os pontos de captura em app.py/telas/*.py.
--    origem e' string livre (nome da pagina/fluxo), sem CHECK fechado de
--    proposito -- fluxos novos (ex. geracao de relatorio, quando
--    existir) so' passam uma origem nova, sem migracao.
-- =====================================================================

CREATE TABLE IF NOT EXISTS egc.eventos_sistema (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  origem         text NOT NULL,
  nivel          text NOT NULL CHECK (nivel IN ('INFO','AVISO','ERRO')),
  mensagem       text NOT NULL,
  detalhe        text,
  empresa_codigo text,
  periodo        date,
  usuario        text,
  criado_em      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_eventos_sistema_recentes ON egc.eventos_sistema (criado_em DESC);
CREATE INDEX IF NOT EXISTS idx_eventos_sistema_nivel     ON egc.eventos_sistema (nivel, criado_em DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON egc.eventos_sistema TO egc_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA egc TO egc_app;

ALTER TABLE egc.eventos_sistema ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS egc_app_full_access ON egc.eventos_sistema;
CREATE POLICY egc_app_full_access ON egc.eventos_sistema FOR ALL TO egc_app USING (true) WITH CHECK (true);

-- =====================================================================
-- 10. Contexto fiscal — conhecimento de referencia curado (task da
--     Reforma Tributaria, pedido do Rafael 22/09/2026: "queria imputar
--     de alguma forma, pelo menos p saber ou ter ciencia dessas
--     informacoes... futuramente o chat conseguira trabalhar com base
--     nos dados existentes e cruzamento dessas novas informacoes").
--
--     NAO e' dado financeiro (BP/DRE) nem substitui a REGRA CRITICA do
--     chat ("responda so' com dado que veio de verdade das ferramentas"
--     -- ver chat_egc.montar_system_prompt) -- e' contexto factual FIXO,
--     pesquisado e datado por nos (nao pelo modelo), injetado no system
--     prompt como referencia, claramente rotulado como tal. Atualizacao
--     e' edicao de linha (UPDATE), sem precisar de deploy de codigo --
--     e' exatamente o ponto: a reforma muda em degraus ate' 2033, o
--     conteudo muda, o codigo que injeta nao.
-- =====================================================================

CREATE TABLE IF NOT EXISTS egc.contexto_fiscal (
  chave          text PRIMARY KEY,
  tema           text NOT NULL,
  titulo         text NOT NULL,
  conteudo       text NOT NULL,
  fonte          text,
  ativo          boolean NOT NULL DEFAULT true,
  atualizado_por text,
  atualizado_em  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO egc.contexto_fiscal (chave, tema, titulo, conteudo, fonte, atualizado_por) VALUES
('REFORMA_TRIB_CRONOGRAMA', 'reforma_tributaria', 'Cronograma da Reforma Tributaria (EC 132/2023 + LC 214/2025)',
 'Fase de teste teve inicio em 01/01/2026: aliquota-teste CBS (federal) 0,9% + IBS (estadual/municipal) 0,1% (total 1%), destaque obrigatorio em nota fiscal desde 03/08/2026. Esse valor e integralmente compensavel com PIS/COFINS do mesmo periodo -- empresa em dia com as obrigacoes acessorias nao desembolsa nada de fato em 2026. A partir de 2027: CBS entra em vigor plena, PIS/COFINS sao extintos, Imposto Seletivo inicia. 2028: ajustes/consolidacao. 2029-2032: substituicao gradual de ICMS/ISS por IBS, em degraus anuais. 2033: extincao total de ICMS e ISS -- sistema pleno IBS+CBS+Imposto Seletivo.',
 'Pesquisa datada de 22/09/2026 (Tax Group, CGIBS, LegisWeb) -- ver detalhe completo no chat EGC #26/#27 do Rafael.', 'claude'),
('REFORMA_TRIB_CONTABILIZACAO_2026', 'reforma_tributaria', 'Como contabilizar CBS/IBS em 2026',
 'O CFC publicou a Orientacao Tecnica CFC no 1/2026 (jul/2026), que NAO obriga um tratamento unico -- permite 2 abordagens validas: (a) registrar IBS/CBS em contas NOVAS no Balanco (ativo = credito fiscal; passivo circulante = a recolher), preparando o modelo definitivo; ou (b) tratar como passivo contingente, sem lancar conta nova, so divulgando em nota explicativa -- valido pra empresa em dia com as obrigacoes acessorias. A receita, quando reconhecida, e sempre LIQUIDA de IBS/CBS (tributo "por fora", nao transita pelo resultado). As contas antigas (ICMS, PIS, COFINS, ISS "a recolher") continuam normais e integrais em 2026, sem reducao nenhuma ainda -- mudanca estrutural real so comeca em 2027.',
 'Pesquisa datada de 22/09/2026 (Contabeis.com.br, Jettax) -- OT CFC no 1/2026.', 'claude'),
('REFORMA_TRIB_CONSTRUCAO_CIVIL', 'reforma_tributaria', 'Regra especifica pra construcao civil/EPC (LC 214/2025 arts. 252-270)',
 'Operacoes com bens imoveis/construcao (obras, incorporacao, loteamento, locacao) tem regra propria: base unica substituindo ISS/ICMS/PIS/COFINS fragmentados, apuracao POR EMPREENDIMENTO (cada obra/canteiro com controle segregado), fato gerador em momentos especificos (venda na alienacao; servico no pagamento; obra na entrega), credito amplo sobre materiais/equipamentos/servicos contratados. Nao ha confirmacao de aliquota reduzida especifica pra EPC industrial (diferente do redutor do setor imobiliario residencial) -- ponto a validar com um tributarista se for relevante pro grupo, nao e algo que o assistente deva afirmar sozinho.',
 'Pesquisa datada de 22/09/2026 (Contabeis.com.br, 08/09/2025) -- LC 214/2025 arts. 252-270.', 'claude'),
('REFORMA_TRIB_PARSER_IMPACTO', 'reforma_tributaria', 'Impacto no parser do EGC (BP_TARGETS/DRE_TARGETS)',
 'O parser do EGC-WEB (app/parser_egc.py) captura tributos de forma AGREGADA, nao item a item: "IMPOSTOS E CONTRIBUICOES A RECOLHER" e "TRIBUTOS RETIDOS A RECOLHER" no BP, "DEDUCOES DA RECEITA BRUTA" no DRE -- nao existe conta separada de ICMS/PIS/COFINS/ISS hoje. Se a contadora nao lancar conta nova em 2026 (opcao b da OT CFC 1/2026), nada precisa mudar no parser. Se lancar conta nova (ex. "IBS A RECOLHER"), e necessario 1 entrada nova em BP_TARGETS/DRE_TARGETS -- baixo risco, mas so deve ser escrita olhando um PDF real com esse nome (nunca chutar alias sem fonte, mesma regra de sempre do projeto).',
 'Inspecao direta de app/parser_egc.py em 22/09/2026.', 'claude')
ON CONFLICT (chave) DO NOTHING;

GRANT SELECT, INSERT, UPDATE, DELETE ON egc.contexto_fiscal TO egc_app;
ALTER TABLE egc.contexto_fiscal ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS egc_app_full_access ON egc.contexto_fiscal;
CREATE POLICY egc_app_full_access ON egc.contexto_fiscal FOR ALL TO egc_app USING (true) WITH CHECK (true);

-- Fim dos blocos 9-10. Rodar so' esses 2 blocos no SQL Editor do Supabase
-- (projeto radar-comercial) -- nao precisa rodar o arquivo inteiro de novo.

-- =====================================================================
-- 11. Notas Fiscais x Sienge — conciliação (pedido do Rafael 25/09/2026:
--     "a contadora precisa comparar com oq ta lançado no Sienge, pra ver
--     quais notas faltam subir e pq"). Import 100% separado do fluxo
--     BP/DRE existente (egc.lancamentos) -- nenhuma tabela abaixo é lida
--     nem escrita por parser_egc.py/db.py do fluxo de Importar PDF.
--
--     Log de erro reaproveita egc.eventos_sistema (bloco 9) com
--     origem='notas_fiscais' -- não cria tabela de log nova.
--
--     Validado contra a API real do Sienge antes de desenhar este schema
--     (ver EGC 00-handoff.md seção 62): accessKeyNumber só vem
--     preenchido em ~14% dos títulos, por isso NÃO é chave única de
--     match -- o critério principal é CNPJ+número+valor, com a chave
--     como confirmação extra quando presente.
-- =====================================================================

-- Snapshot local dos títulos do Sienge (Contas a Pagar) — sincronizado
-- sob demanda (botão "Atualizar do Sienge" na tela), não em tempo real.
-- Guarda TODOS os tipos de documento no período (não só NFE/NF) pra
-- permitir busca de 2º passe (fallback) sem restringir tipo.
CREATE TABLE IF NOT EXISTS egc.nf_bills_sync (
  bill_id                     bigint PRIMARY KEY,
  debtor_id                   integer,
  creditor_id                 integer,
  document_identification_id text,
  document_number             text,
  issue_date                  date,
  total_invoice_amount        numeric(14,2),
  access_key_number           text,
  status                      text,
  sincronizado_em             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nf_bills_sync_creditor ON egc.nf_bills_sync (creditor_id);
CREATE INDEX IF NOT EXISTS idx_nf_bills_sync_doc      ON egc.nf_bills_sync (document_identification_id, document_number);

-- Snapshot local dos credores do Sienge — de-para creditorId -> CNPJ.
CREATE TABLE IF NOT EXISTS egc.nf_creditors_sync (
  creditor_id      integer PRIMARY KEY,
  nome             text,
  nome_fantasia    text,
  cnpj             text,
  cpf              text,
  sincronizado_em  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nf_creditors_cnpj ON egc.nf_creditors_sync (cnpj);

-- Cada linha = 1 nota da planilha da Receita, sob um import_id (1 upload
-- = 1 rodada de conferência = 1 import_id). Nunca é sobrescrita por uma
-- rodada nova -- é o histórico de "o que foi conferido, quando".
CREATE TABLE IF NOT EXISTS egc.nf_manifesto_import (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_id           uuid NOT NULL,
  empresa_codigo      text NOT NULL,
  periodo_referencia  text NOT NULL,
  numero_nota         text,
  numero_normalizado  text,
  tipo_documento      text,
  data_emissao        date,
  valor               numeric(14,2),
  cfop                text,
  fornecedor_nome     text,
  fornecedor_cnpj     text,
  cnpj_normalizado    text,
  uf                  text,
  chave_acesso        text,
  chave_modelo        text,
  chave_serie         text,
  chave_numero        text,
  arquivo_nome        text,
  criado_por          text,
  criado_em           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nf_manifesto_import_id ON egc.nf_manifesto_import (import_id);
CREATE INDEX IF NOT EXISTS idx_nf_manifesto_empresa    ON egc.nf_manifesto_import (empresa_codigo, periodo_referencia);

-- Resultado do matching, 1 linha por nota do manifesto. pendencia_status
-- é o "lastro" pedido (fica com histórico até correção/verificação no
-- Sienge, nunca é apagado -- só muda de status).
CREATE TABLE IF NOT EXISTS egc.nf_conciliacao (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_id         uuid NOT NULL,
  manifesto_id      bigint NOT NULL REFERENCES egc.nf_manifesto_import(id),
  status            text NOT NULL CHECK (status IN ('LANCADA','NAO_ENCONTRADA','VALOR_DIVERGENTE','NUMERO_DIVERGENTE')),
  sienge_bill_id    bigint,
  sienge_valor      numeric(14,2),
  confianca         text,
  observacao        text,
  pendencia_status  text CHECK (pendencia_status IS NULL OR pendencia_status IN ('PENDENTE','ENVIADO_SUPRIMENTOS','RESOLVIDO','DESCARTADO')),
  atualizado_por    text,
  atualizado_em     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nf_conciliacao_import ON egc.nf_conciliacao (import_id);
CREATE INDEX IF NOT EXISTS idx_nf_conciliacao_status  ON egc.nf_conciliacao (status, pendencia_status);

-- 1 linha por rodada de conferência -- alimenta os KPIs (quantas notas,
-- quantas no Sienge, quantas pendências) sem precisar reagregar tudo.
CREATE TABLE IF NOT EXISTS egc.nf_import_historico (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_id           uuid NOT NULL UNIQUE,
  empresa_codigo      text NOT NULL,
  periodo_referencia  text NOT NULL,
  total_notas         integer NOT NULL DEFAULT 0,
  total_lancadas      integer NOT NULL DEFAULT 0,
  total_pendencias    integer NOT NULL DEFAULT 0,
  arquivo_nome        text,
  usuario             text,
  criado_em           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nf_import_historico_empresa ON egc.nf_import_historico (empresa_codigo, criado_em DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON
  egc.nf_bills_sync, egc.nf_creditors_sync, egc.nf_manifesto_import,
  egc.nf_conciliacao, egc.nf_import_historico
TO egc_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA egc TO egc_app;

ALTER TABLE egc.nf_bills_sync        ENABLE ROW LEVEL SECURITY;
ALTER TABLE egc.nf_creditors_sync    ENABLE ROW LEVEL SECURITY;
ALTER TABLE egc.nf_manifesto_import  ENABLE ROW LEVEL SECURITY;
ALTER TABLE egc.nf_conciliacao       ENABLE ROW LEVEL SECURITY;
ALTER TABLE egc.nf_import_historico  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS egc_app_full_access ON egc.nf_bills_sync;
CREATE POLICY egc_app_full_access ON egc.nf_bills_sync FOR ALL TO egc_app USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS egc_app_full_access ON egc.nf_creditors_sync;
CREATE POLICY egc_app_full_access ON egc.nf_creditors_sync FOR ALL TO egc_app USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS egc_app_full_access ON egc.nf_manifesto_import;
CREATE POLICY egc_app_full_access ON egc.nf_manifesto_import FOR ALL TO egc_app USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS egc_app_full_access ON egc.nf_conciliacao;
CREATE POLICY egc_app_full_access ON egc.nf_conciliacao FOR ALL TO egc_app USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS egc_app_full_access ON egc.nf_import_historico;
CREATE POLICY egc_app_full_access ON egc.nf_import_historico FOR ALL TO egc_app USING (true) WITH CHECK (true);

-- Fim do bloco 11. Rodar so' este bloco no SQL Editor do Supabase
-- (projeto radar-comercial) -- nao precisa rodar o arquivo inteiro de novo.
