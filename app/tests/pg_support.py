# -*- coding: utf-8 -*-
"""
Suporte aos testes de INTEGRACAO contra um Postgres REAL (v0.40.0).

Por que existe: ate' a v0.39.0 todos os testes de relatorio/indicadores
mockavam `db.*` ou o `conn`. O Rafael gerou PDFs reais depois da v0.39.0
com granularidade misturada (Energia "trimestral" com conteudo semestral)
e nenhum teste mockado pegou -- o erro vive na COMBINACAO
SQL real + selecao de periodos + montagem. Estes helpers sobem um banco
descartavel, aplicam o DDL real (schema.sql, blocos 12-19) e semeiam um
cenario realista (6 empresas, Energia com trimestral+semestral no mesmo
periodo_fim, etc.) com os numeros dos PDFs reais de 01/10/2026.

Descoberta do banco (nesta ordem):
  1. env EGC_TEST_DATABASE_URL (DSN de um Postgres vazio/descartavel --
     cria/derruba o proprio schema `egc`, NUNCA aponte pra producao);
  2. autostart de um cluster temporario via `initdb`/`pg_ctl` achados no
     PATH ou em /usr/lib/postgresql/*/bin (como root, roda como o usuario
     `postgres`);
  3. senao -> `pg_disponivel()` devolve False e os testes dao skip.

NUNCA toca o Supabase de producao: so' conecta no DSN de teste.
"""
from __future__ import annotations

import atexit
import glob
import os
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

SCHEMA_SQL = Path(__file__).resolve().parent.parent.parent / "schema.sql"

_estado: dict = {"dsn": None, "tentou": False, "dir": None}


def _achar_bin(nome: str):
    achado = shutil.which(nome)
    if achado:
        return achado
    for d in sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        cand = os.path.join(d, nome)
        if os.path.exists(cand):
            return cand
    return None


def _autostart() -> str | None:
    initdb, pg_ctl = _achar_bin("initdb"), _achar_bin("pg_ctl")
    if not initdb or not pg_ctl:
        return None
    raiz = tempfile.mkdtemp(prefix="egc_pg_")
    data = os.path.join(raiz, "data")
    sock = raiz
    porta = "54399"
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    prefixo: list = []
    if root:
        os.chmod(raiz, 0o777)
        prefixo = ["su", "postgres", "-c"]

    def rodar(cmd: str):
        if root:
            return subprocess.run(prefixo + [cmd], capture_output=True, text=True, timeout=120)
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)

    r = rodar(f"'{initdb}' -D '{data}' -A trust -U postgres")
    if r.returncode != 0:
        return None
    r = rodar(f"'{pg_ctl}' -D '{data}' -o '-p {porta} -k {sock} -c listen_addresses=' -l '{raiz}/pg.log' -w start")
    if r.returncode != 0:
        return None

    def parar():
        try:
            rodar(f"'{pg_ctl}' -D '{data}' -m immediate stop")
        finally:
            shutil.rmtree(raiz, ignore_errors=True)

    atexit.register(parar)
    _estado["dir"] = raiz
    return f"host={sock} port={porta} user=postgres dbname=postgres"


def dsn_teste() -> str | None:
    if _estado["tentou"]:
        return _estado["dsn"]
    _estado["tentou"] = True
    dsn = os.environ.get("EGC_TEST_DATABASE_URL")
    if not dsn:
        try:
            dsn = _autostart()
        except Exception:
            dsn = None
    _estado["dsn"] = dsn
    return dsn


def pg_disponivel() -> bool:
    try:
        import psycopg2  # noqa: F401
    except Exception:
        return False
    return dsn_teste() is not None


def conectar_limpo():
    """Conexao (autocommit, igual a producao) num schema `egc` RECEM-criado
    com o schema.sql real aplicado por inteiro."""
    import psycopg2

    conn = psycopg2.connect(dsn_teste())
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS egc CASCADE")
        # schema.sql tem GRANT CONNECT ON DATABASE postgres -- precisa que
        # o banco se chame postgres (default) ou o GRANT falha; ignoramos
        # esse erro pontual rodando o arquivo statement a statement abaixo.
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


# ─────────────────────────────────────────────
#  Semeadura realista
# ─────────────────────────────────────────────

P_2023 = date(2023, 12, 31)
P_2024 = date(2024, 12, 31)
P_2026 = date(2026, 6, 30)

TODAS = ["ENERGIA", "SMG", "ENG", "RENOV", "CONST", "SOL"]


def _dre(rl, ebitda, resultado, csll_irpj=0.0, deprec=100_000.0, rec_fin=50_000.0, bruta_pct=0.9, admin_pct=0.6):
    """Linhas DRE coerentes com a convencao de sinal do banco
    (despesa/custo/deducao NEGATIVOS, receita POSITIVA) que fecham EXATAMENTE
    em RL / EBITDA / Resultado Liquido pedidos.

    EBITDA = LOL - (DESP_FIN + REC_FIN) - (DEPREC + AMORT)   (indicadores.py)
    Resultado = LOL + (CSLL + IRPJ)                          (CSLL/IRPJ <= 0)
    """
    lol = resultado - csll_irpj  # csll_irpj negativo => LOL < resultado... (resultado = lol + csll_irpj)
    da = -abs(deprec)
    rf = round((lol - ebitda) - da, 2)  # DESP_FIN + REC_FIN
    desp_fin = round(rf - rec_fin, 2)
    bruta = round(rl / bruta_pct, 2)
    deducoes = round(rl - bruta, 2)  # negativo
    lb = round(rl * 0.80, 2)
    custo = round(lb - rl, 2)  # negativo
    desp_op = round(lol - lb, 2)  # negativo
    admin = round(desp_op * admin_pct, 2)
    linhas = [
        ("RECEITAS", "RECEITA OPERACIONAL BRUTA", bruta),
        ("DEDUCOES", "DEDUCOES DA RECEITA BRUTA", deducoes),
        ("RESULTADO", "RECEITA OPERACIONAL LIQUIDA", rl),
        ("CUSTOS", "CUSTO DOS PRODUTOS/SERVICOS", custo),
        ("RESULTADO", "LUCRO BRUTO", lb),
        ("DESPESAS", "DESPESAS OPERACIONAIS", desp_op),
        ("DESPESAS", "ADMINISTRATIVAS", admin),
        ("DESPESAS", "DESPESAS FINANCEIRAS", desp_fin),
        ("RECEITAS", "RECEITAS FINANCEIRAS", rec_fin),
        ("RESULTADO", "LUCRO OPERACIONAL LIQUIDO", round(lol, 2)),
        ("RESULTADO", "LUCRO LIQUIDO DO EXERCICIO", resultado),
        ("DESPESAS", "DEPRECIACOES", da),
    ]
    if csll_irpj:
        linhas += [
            ("DESPESAS", "PROVISAO CSLL", round(csll_irpj * 0.27, 2)),
            ("DESPESAS", "PROVISAO IRPJ", round(csll_irpj - round(csll_irpj * 0.27, 2), 2)),
        ]
    return linhas, admin


def _bp(ativo, ac, pc, pnc, pl):
    """BP minima coerente: Ativo = Passivo exigivel + PL."""
    anc = round(ativo - ac, 2)
    imob = round(anc * 0.8, 2)
    invest = round(anc - imob, 2)
    clientes = round(ac * 0.3, 2)
    disp = round(ac * 0.2, 2)
    outros = round(ac - clientes - disp, 2)
    forn = round(pc * 0.4, 2)
    trib = round(pc - forn, 2)
    cap = round(pl * 0.5, 2)
    lucros = round(pl - cap, 2)
    return [
        ("ATIVO CIRCULANTE", "TOTAL CIRCULANTE ATIVO", ac),
        ("ATIVO CIRCULANTE", "CLIENTES", clientes),
        ("ATIVO CIRCULANTE", "DISPONIVEL", disp),
        ("ATIVO CIRCULANTE", "OUTROS CREDITOS", outros),
        ("ATIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE ATIVO", anc),
        ("ATIVO NAO CIRCULANTE", "IMOBILIZADO", imob),
        ("ATIVO NAO CIRCULANTE", "INVESTIMENTOS", invest),
        ("TOTAL", "TOTAL DO ATIVO", ativo),
        ("PASSIVO CIRCULANTE", "TOTAL CIRCULANTE PASSIVO", pc),
        ("PASSIVO CIRCULANTE", "FORNECEDORES", forn),
        ("PASSIVO CIRCULANTE", "OBRIGACOES TRIBUTARIAS", trib),
        ("PASSIVO NAO CIRCULANTE", "TOTAL NAO CIRCULANTE PASSIVO", pnc),
        ("PASSIVO NAO CIRCULANTE", "OBRIGACOES A LONGO PRAZO", pnc),
        ("PATRIMONIO LIQUIDO", "TOTAL PATRIMONIO LIQUIDO", pl),
        ("PATRIMONIO LIQUIDO", "CAPITAL SOCIAL", cap),
        ("PATRIMONIO LIQUIDO", "LUCROS/PREJUIZOS ACUMULADOS", lucros),
        ("TOTAL", "TOTAL DO PASSIVO", ativo),
    ]


def gravar_documento(conn, empresa, periodo, granularidade, dre=None, bp=None, admin_itens=None, status="ATIVO",
                     periodo_inicio=None):
    """Insere 1 documento (empresa, periodo_fim, granularidade) direto na
    tabela real. `dre`/`bp` = [(grupo, conta, valor), ...]."""
    with conn.cursor() as cur:
        for tipo, linhas in (("DRE", dre), ("BP", bp)):
            for grupo, conta, valor in linhas or []:
                cur.execute(
                    """
                    INSERT INTO egc.lancamentos
                        (empresa_codigo, tipo, periodo, grupo, conta, valor, granularidade, status, origem,
                         periodo_inicio)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PDF Importado', %s)
                    """,
                    (empresa, tipo, periodo, grupo, conta, valor, granularidade, status,
                     periodo_inicio if tipo == "DRE" else None),
                )
        for i, (conta, valor) in enumerate(admin_itens or []):
            cur.execute(
                """
                INSERT INTO egc.despesas_admin_itens (empresa_codigo, periodo, ordem, conta, valor, granularidade)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (empresa, periodo, i, conta, valor, granularidade),
            )


def _itens_admin(total_admin_neg):
    """Quebra o total ADMINISTRATIVAS (negativo) em 8 itens que somam
    EXATAMENTE o total (ranking de despesas exige essa coerencia)."""
    total = abs(total_admin_neg)
    pesos = [0.30, 0.20, 0.15, 0.10, 0.08, 0.07, 0.05]
    itens = []
    acumulado = 0.0
    for i, p in enumerate(pesos):
        v = round(total * p, 2)
        acumulado += v
        itens.append((f"Conta Admin {i + 1}", -v))
    itens.append(("Conta Admin 8", -round(total - acumulado, 2)))
    return itens


# Numeros conferidos pelo Rafael nos dados brutos do Supabase / PDFs de
# 01/10/2026 (mesmo oraculo de test_granularidade_obrigatoria.py, que
# usa um banco em memoria; aqui as MESMAS linhas vao pra um Postgres de
# verdade).
#   ENERGIA trimestral 30/06/2026: rec 17.380.604,41 | EBITDA -847.960,37 | LL -1.804.157,20
#   ENERGIA semestral  30/06/2026: rec 33.350.846,80 | EBITDA  724.975,31 | LL -1.337.674,90
#   GRUPO (6 CNPJs, tudo trimestral): rec 23.874.026,45 | EBITDA -16.268,75 | LL -1.226.491,10
ESPERADO = {
    ("ENERGIA", "trimestral"): dict(rec=17_380_604.41, ebitda=-847_960.37, ll=-1_804_157.20),
    ("ENERGIA", "semestral"): dict(rec=33_350_846.80, ebitda=724_975.31, ll=-1_337_674.90),
    ("GRUPO", "trimestral"): dict(rec=23_874_026.45, ebitda=-16_268.75, ll=-1_226_491.10),
}


def _dre_explicita(rec, bruta, deducoes, custo, lb, desp_op, admin, trib, df, rf, dep, csll=0.0, irpj=0.0):
    lol = round(lb + desp_op, 2)
    ll = round(lol + csll + irpj, 2)
    linhas = [
        ("RECEITAS", "RECEITA OPERACIONAL BRUTA", bruta),
        ("DEDUCOES", "DEDUCOES DA RECEITA BRUTA", deducoes),
        ("RESULTADO", "RECEITA OPERACIONAL LIQUIDA", rec),
        ("CUSTOS", "CUSTO DOS PRODUTOS/SERVICOS", custo),
        ("RESULTADO", "LUCRO BRUTO", lb),
        ("DESPESAS", "DESPESAS OPERACIONAIS", desp_op),
        ("DESPESAS", "ADMINISTRATIVAS", admin),
        ("DESPESAS", "DESPESAS FINANCEIRAS", df),
        ("RECEITAS", "RECEITAS FINANCEIRAS", rf),
        ("DESPESAS", "DESPESAS TRIBUTARIAS", trib),
        ("RESULTADO", "LUCRO OPERACIONAL LIQUIDO", lol),
        ("RESULTADO", "LUCRO LIQUIDO DO EXERCICIO", ll),
        ("DESPESAS", "DEPRECIACOES", dep),
    ]
    if csll or irpj:
        linhas += [("DESPESAS", "PROVISAO CSLL", csll), ("DESPESAS", "PROVISAO IRPJ", irpj)]
    return linhas, admin


_BP_ENERGIA_2026 = (43_359_080.94, 18_239_216.72, 18_338_124.77, 18_249_041.92, 6_771_914.25)

CENARIO = {
    # (empresa, periodo_fim, granularidade) -> dict(dre=(linhas, admin), bp=(...))
    ("ENERGIA", P_2023, "trimestral"): dict(
        dre=_dre(3_327_615.35, 1_299_749.49, 1_285_930.76),
        bp=(3_398_875.31, 2_927_668.49, 65_238.48, 1_282_790.45, 2_050_846.38)),
    ("ENERGIA", P_2024, "anual"): dict(
        dre=_dre(15_565_767.10, 3_697_980.24, 3_432_098.83),
        bp=(13_643_036.80, 5_491_020.04, 992_453.25, 4_398_610.31, 8_251_973.24)),
    # semestral: 15 linhas (tem PROVISAO CSLL/IRPJ -> EBITDA 724.975,31)
    ("ENERGIA", P_2026, "semestral"): dict(
        dre=_dre_explicita(33_350_846.80, 36_669_119.57, -3_318_272.77, -3_897_304.79, 29_453_542.01,
                           -30_559_591.64, -29_339_591.64, -80_502.31, -1_195_046.22, 55_548.53,
                           -691_527.25, csll=-62_900.81, irpj=-168_724.46),
        bp=_BP_ENERGIA_2026),
    # trimestral: sem provisao CSLL/IRPJ -> EBITDA -847.960,37
    ("ENERGIA", P_2026, "trimestral"): dict(
        dre=_dre_explicita(17_380_604.41, 19_100_000.00, -1_719_395.59, -2_000_000.00, 15_380_604.41,
                           -17_184_761.61, -16_501_467.00, -80_000.00, -636_411.11, 33_116.50,
                           -352_902.22),
        bp=_BP_ENERGIA_2026),
}
for _emp, (_rec, _ebitda, _ll, _bp_t) in {
    "CONST": (6_004_883.69, 2_319_740.04, 2_105_771.09, (9_000_000.0, 5_000_000.0, 2_000_000.0, 1_000_000.0, 6_000_000.0)),
    "ENG": (18_266.69, -1_036_441.72, -1_040_039.18, (5_000_000.0, 3_000_000.0, 1_500_000.0, 500_000.0, 3_000_000.0)),
    "RENOV": (0.0, -191_306.52, -200_082.17, (4_000_000.0, 2_000_000.0, 1_000_000.0, 500_000.0, 2_500_000.0)),
    "SMG": (448_790.60, 73_921.45, 49_104.40, (2_000_000.0, 1_000_000.0, 500_000.0, 200_000.0, 1_300_000.0)),
    "SOL": (21_481.06, -334_221.63, -337_088.04, (1_000_000.0, 600_000.0, 300_000.0, 100_000.0, 600_000.0)),
}.items():
    CENARIO[(_emp, P_2026, "trimestral")] = dict(dre=_dre(_rec, _ebitda, _ll), bp=_bp_t)


def semear_cenario(conn, itens_admin=True):
    """Semeia o cenario completo. Retorna o dict CENARIO."""
    for (emp, per, gran), c in CENARIO.items():
        dre, admin = c["dre"]
        gravar_documento(
            conn, emp, per, gran, dre=list(dre), bp=_bp(*c["bp"]),
            admin_itens=_itens_admin(admin) if itens_admin else None,
            periodo_inicio=_inicio_declarado(per, gran),
        )
    return CENARIO


def _inicio_declarado(periodo, gran):
    """periodo_inicio coerente com a granularidade (como o importador grava
    a partir do 'Periodo: X a Y' do proprio PDF)."""
    meses = {"mensal": 1, "bimestral": 2, "trimestral": 3, "semestral": 6, "anual": 12}.get(gran)
    if not meses:
        return None
    m = periodo.month - meses + 1
    a = periodo.year
    while m <= 0:
        m += 12
        a -= 1
    return date(a, m, 1)


P_MAR_2026 = date(2026, 3, 31)

# 1o trimestre/2026 (tri) das 6 empresas: permite um Comparativo de GRUPO com
# 2 colunas validas (1T x 2T) e um "periodo anterior" ADJACENTE para o 2T.
# Energia fecha com o semestral: 1T = semestral - 2T (rec 15.970.242,39 |
# EBITDA 1.572.935,68 | LL 466.482,30).
TRIMESTRE_ANTERIOR = {
    "ENERGIA": (15_970_242.39, 1_572_935.68, 466_482.30),
    "CONST": (5_000_000.00, 1_000_000.00, 800_000.00),
    "ENG": (900_000.00, -100_000.00, -120_000.00),
    "RENOV": (200_000.00, -50_000.00, -60_000.00),
    "SMG": (300_000.00, 40_000.00, 30_000.00),
    "SOL": (50_000.00, -10_000.00, -12_000.00),
}


def semear_trimestre_anterior(conn):
    for emp, (rec, ebitda, ll) in TRIMESTRE_ANTERIOR.items():
        dre, admin = _dre(rec, ebitda, ll)
        gravar_documento(
            conn, emp, P_MAR_2026, "trimestral", dre=list(dre), bp=_bp(40_000_000.0, 20_000_000.0, 15_000_000.0, 14_000_000.0, 11_000_000.0),
            admin_itens=_itens_admin(admin), periodo_inicio=_inicio_declarado(P_MAR_2026, "trimestral"),
        )


def rotular_errado_energia_trimestral_com_dados_do_semestral(conn, com_periodo_inicio=False):
    """Reproduz o estado do banco que gerou os PDFs de 01/10/2026: o
    documento ENERGIA 06/2026 rotulado 'trimestral' contem as linhas do
    SEMESTRAL (import mal classificado). `com_periodo_inicio=True` mantem o
    intervalo que o PDF declarou (01/01/2026) -- o caso que o motor agora
    detecta e bloqueia; False simula import legado sem periodo_inicio."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM egc.lancamentos WHERE empresa_codigo='ENERGIA' AND periodo=%s AND granularidade='trimestral'", (P_2026,))
        cur.execute("DELETE FROM egc.despesas_admin_itens WHERE empresa_codigo='ENERGIA' AND periodo=%s AND granularidade='trimestral'", (P_2026,))
        cur.execute(
            """
            INSERT INTO egc.lancamentos (empresa_codigo, tipo, periodo, grupo, conta, valor, origem, granularidade, periodo_inicio)
            SELECT empresa_codigo, tipo, periodo, grupo, conta, valor, origem, 'trimestral',
                   CASE WHEN %s THEN periodo_inicio ELSE NULL END
            FROM egc.lancamentos
            WHERE empresa_codigo='ENERGIA' AND periodo=%s AND granularidade='semestral' AND status='ATIVO'
            """,
            (com_periodo_inicio, P_2026),
        )
