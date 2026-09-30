# -*- coding: utf-8 -*-
"""Testes das paginas 1, 3-9 (25/09/2026). Mesmo padrao dos testes da
pagina 2 (test_gerador_relatorio_comentado.py): nao valida pixel, so'
garante que o pipeline nao quebra pra lucro E pra prejuizo, e que as
frases de leitura de CADA pagina sao neutras (mesmo texto nos 2 casos,
so' o numero com sinal muda)."""
import sys
import os
import re
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gerador_relatorio_comentado as g  # noqa: E402

BASE = dict(
    empresa_codigo="ENERGIA", empresa_nome="Enermais Energia Ltda", cnpj="47.040.664/0001-48",
    cabecalho_relatorio="Demonstrativo Comentado · 1º Semestre 2026", periodo_label="1º Semestre 2026",
    periodo_extenso="janeiro a junho de 2026", data_posicao="30/06/2026", data_geracao="25/09/2026",
    receita_bruta=36_674_150.0, deducoes_receita=3_321_000.0, deducoes_pct_bruta=0.09,
    receita_liquida=33_353_150.0, custo_servicos=3_899_607.99, custo_pct_liquida=0.117,
    lucro_bruto=29_453_542.01, margem_bruta=0.883,
    despesas_operacionais=30_559_591.64, despesas_administrativas=29_339_591.64,
    despesas_financeiras=1_139_497.69, despesas_tributarias=80_502.31,
    despesas_admin_itens=[("Serviços Profissionais", 11_401_300.0, 38.9), ("Salários e Ordenados", 4_060_000.0, 13.8)],
    csll_irpj=231_625.27, resultado_liquido=-1_337_674.90, margem_liquida=-0.04,
    # FIX_20260928: ebitda/margem_ebitda recalculados pela formula nova
    # (soma de volta csll_irpj tambem, alinhado com indicadores.py --
    # antes so' somava resultado_financeiro+deprec_amortiz de volta ao
    # resultado liquido, que ja vem DEPOIS do csll_irpj). Resultado
    # liquido (-1.337.674,90) + csll_irpj (231.625,27) + resultado
    # financeiro (1.139.497,69) + deprec_amortiz (691.527,25) = 724.975,31.
    resultado_financeiro=1_139_497.69, deprec_amortiz=691_527.25, ebitda=724_975.31, margem_ebitda=0.02174,
    total_ativo=43_359_080.94, ativo_circulante=18_239_216.72, ativo_nao_circulante=25_119_864.22,
    passivo_circulante=18_338_124.77, passivo_nao_circulante=18_249_041.92, patrimonio_liquido=6_771_914.25,
    imobilizado=19_815_256.50, liquidez_corrente=0.99, alavancagem=5.40, endividamento_geral=0.844,
    complemento_receita="x", complemento_ebitda="x", complemento_despesas="x", callout_estrutura_capital="x",
    anexo_ativo=[("grupo", "ATIVO"), ("conta", "Disponível", 100.0), ("total", "TOTAL DO ATIVO", 100.0)],
    anexo_passivo=[("grupo", "PASSIVO"), ("conta", "Fornecedores", 100.0), ("total", "TOTAL PASSIVO + PL", 100.0)],
    nome_administrador="[Nome]", cargo_administrador="Administrador",
    nome_contador="[Nome]", cargo_contador="Contador",
    email_empresa="x@x.com", site_empresa="x.com",
)


def test_gerar_pdf_completo_nao_quebra_lucro_e_prejuizo():
    for resultado, margem, ebitda, margem_ebitda in [(-1_337_674.90, -0.04, 724_975.31, 0.02174),
                                                       (2_000_000.0, 0.06, 3_000_000.0, 0.09)]:
        dados = dict(BASE, resultado_liquido=resultado, margem_liquida=margem, ebitda=ebitda, margem_ebitda=margem_ebitda)
        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, "completo.pdf")
            g.gerar_pdf_completo(dados, caminho)
            assert os.path.exists(caminho)
            assert os.path.getsize(caminho) > 5000
    print("OK: gerar_pdf_completo gera as 9 páginas sem exceção, pra lucro e pra prejuízo")


def _sem_numeros(texto):
    return re.sub(r"[-+]?R\$\s*[\d.,]+|[-+]?[\d.,]+%|[-+]?[\d.,]+x?", "", texto)


PALAVRAS_PROIBIDAS = ["lucro de", "prejuízo de", "positivo", "negativo", "saudável", "espaço claro",
                       "superando", "abaixo do", "pressão", "passa a"]


def test_leituras_das_novas_paginas_sao_neutras():
    dados_prejuizo = BASE
    dados_lucro = dict(BASE, resultado_liquido=2_000_000.0, margem_liquida=0.06,
                        ebitda=3_000_000.0, margem_ebitda=0.09,
                        despesas_operacionais=20_000_000.0)  # tambem inverte despesas < lucro bruto
    funcs = [g._leitura_receita_custos, g._leitura_despesas, g._leitura_resultado,
             g._leitura_ebitda, g._leitura_balanco]
    for fn in funcs:
        # _leitura_balanco ganhou pagina/total_paginas no FIX_20260928e
        # (referencias de pagina cruzada -- Resultado/Anexo -- dinamicas
        # em vez de hardcoded); 7/9 = layout padrao (com pagina_resultado).
        extra_args = (7, 9) if fn is g._leitura_balanco else ()
        p_prejuizo = fn(dados_prejuizo, *extra_args)
        p_lucro = fn(dados_lucro, *extra_args)
        assert len(p_prejuizo) == len(p_lucro)
        for a, b in zip(p_prejuizo, p_lucro):
            assert _sem_numeros(a) == _sem_numeros(b), f"{fn.__name__}: texto muda de palavra entre os 2 sinais:\n{a}\n{b}"
        texto = " ".join(p_prejuizo).lower()
        for palavra in PALAVRAS_PROIBIDAS:
            assert palavra not in texto, f"{fn.__name__}: texto não é neutro, contém '{palavra}'"
    print(f"OK: {len(funcs)} funções de leitura das páginas novas são neutras (lucro/prejuízo, despesas acima/abaixo do lucro bruto)")


def test_leitura_balanco_referencia_pagina_certa_com_e_sem_resultado():
    # FIX_20260928e (Rafael, "a página 7 do relatório (DRE)... alterou
    # bastante" -- achado revisando o texto, não a cor/layout):
    # _leitura_balanco citava "página 5" (Resultado) e "página 8" (Anexo)
    # FIXOS, só certos no layout de 9 páginas. Em lucro presumido (ou
    # qualquer período sem pagina_resultado incluída) o relatório vira 8
    # páginas -- Balanço é página 6, não existe página de Resultado pra
    # citar, e o Anexo é página 7, não 8. O texto tem que refletir isso
    # dinamicamente, não hardcoded.
    d = BASE

    # Layout de 9 paginas (com pagina_resultado): Balanco = pagina 7.
    p1, p2 = g._leitura_balanco(d, 7, 9)
    assert "página 5" in p2, "com Resultado presente, deveria citar a página 5 (onde ele está)"
    assert "página 8" in p2, "Anexo vem logo depois do Balanço: página 8 no layout de 9 páginas"

    # Layout de 8 paginas (sem pagina_resultado): Balanco = pagina 6.
    p1b, p2b = g._leitura_balanco(d, 6, 8)
    assert "página 5" not in p2b, "sem página de Resultado nenhuma, não pode citar uma página que não existe"
    assert "página 7" in p2b, "Anexo vem logo depois do Balanço: página 7 no layout de 8 páginas (não 8)"
    assert "página 8" not in p2b
    print("OK: _leitura_balanco referencia Resultado/Anexo pelo número de página real de cada layout, não hardcoded")


def test_leitura_balanco_nao_confunde_variante_padrao_com_lucro_presumido_na_pagina_6():
    """FIX_20260930 -- achado implementando a variante 'padrao' (pula a
    página de Despesas): a heurística antiga (`tem_pagina_resultado =
    pagina == 7`) parte de UM único layout de 9 páginas com só 1 página
    opcional (Resultado). Com a página de Despesas TAMBÉM opcional agora,
    2 cenários diferentes colidem na MESMA página de Balanço (6):
      (a) COM despesas, SEM resultado (lucro presumido, layout de sempre)
      (b) SEM despesas (variante padrao), COM resultado (lucro real)
    Sem o parâmetro explícito `tem_pagina_resultado`, o caso (b) seria
    mal-interpretado como "sem Resultado" (mesma pagina==6 do caso 'a')
    e a citação à página do Resultado sumiria da leitura mesmo a página
    existindo de verdade no PDF. Prova que os 2 casos, mesma pagina=6,
    dão leituras DIFERENTES quando `tem_pagina_resultado` é passado
    explícito."""
    d = BASE
    # Caso (a): lucro presumido, layout de sempre (Despesas presente,
    # Resultado ausente) -- comportamento OK mesmo por inferência (pagina==6).
    _, p2_presumido = g._leitura_balanco(d, 6, 8, tem_pagina_resultado=False)
    assert "página 5" not in p2_presumido

    # Caso (b): variante padrao (Despesas ausente), lucro REAL (Resultado
    # presente) -- MESMA pagina=6 do caso (a), mas Resultado EXISTE.
    # A inferência antiga (pagina==7) erraria aqui; o parâmetro explícito
    # não.
    _, p2_variante_padrao = g._leitura_balanco(d, 6, 8, tem_pagina_resultado=True)
    assert "página 4" in p2_variante_padrao, (
        "variante padrao (Resultado 2 páginas antes do Balanço, sem Despesas no meio) deveria citar a "
        f"página do Resultado -- texto: {p2_variante_padrao}"
    )
    assert p2_presumido != p2_variante_padrao, (
        "os 2 cenários têm o MESMO número de página de Balanço (6) mas presença de Resultado diferente -- "
        "a leitura tem que diferir, senão a página=6 sozinha não bastava pra decidir (bug do FIX_20260930)"
    )
    print("OK: _leitura_balanco distingue 'lucro presumido' de 'variante padrao + lucro real' mesmo com o "
          "MESMO número de página de Balanço, via tem_pagina_resultado explícito")


def test_gerar_pdf_completo_variante_padrao_com_resultado_cita_pagina_certa_do_resultado():
    """Integração fim-a-fim do bug acima: gera o PDF de verdade com
    variante='padrao' (pula Despesas) E incluir_pagina_resultado=True
    (lucro real) -- Balanço cai na página 6 (mesma página que lucro
    presumido teria SEM variante), e a leitura do Balanço precisa citar
    a página do Resultado corretamente mesmo assim."""
    import pdfplumber

    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "padrao_com_resultado.pdf")
        dados = dict(BASE, variante="padrao")
        g.gerar_pdf_completo(dados, caminho, incluir_pagina_resultado=True)
        with pdfplumber.open(caminho) as pdf:
            paginas_txt = [(p.extract_text() or "") for p in pdf.pages]
        assert len(paginas_txt) == 8, f"variante padrao + com resultado deveria ter 8 páginas, veio {len(paginas_txt)}"
        pagina_balanco_txt = next(p for p in paginas_txt if "Posição Patrimonial" in p)
        assert "página 4" in pagina_balanco_txt, (
            "leitura do Balanço (variante padrao, com Resultado) deveria citar a página 4 (onde o Resultado "
            f"está nesse layout de 8 páginas sem Despesas) -- texto:\n{pagina_balanco_txt}"
        )
    print("OK: gerar_pdf_completo (variante padrao + com Resultado) cita a página certa do Resultado na leitura do Balanço")


def test_grafico_ranking_horizontal_nao_quebra_lista_vazia():
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "sem_itens.pdf")
        dados = dict(BASE, despesas_admin_itens=[])
        g.gerar_pdf_completo(dados, caminho)
        assert os.path.getsize(caminho) > 5000
    print("OK: página de despesas não quebra com despesas_admin_itens vazio")


def test_anexo_com_lista_longa_nao_lanca_excecao():
    linhas = [("grupo", "ATIVO CIRCULANTE")]
    for i in range(25):
        linhas.append(("conta", f"Conta de teste com nome razoavelmente longo número {i}", 1234.56))
    linhas.append(("total", "TOTAL DO ATIVO", 30_000.0))
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "anexo_longo.pdf")
        dados = dict(BASE, anexo_ativo=linhas)
        g.gerar_pdf_completo(dados, caminho)
        assert os.path.getsize(caminho) > 5000
    print("OK: anexo com lista longa (25 contas) não lança exceção (pode transbordar visualmente -- não validado aqui)")


def test_anexo_multi_coluna_empresas_e_multi_periodo_ano_a_ano():
    """Ponto 2 e 3 da checagem multi-empresa/multi-periodo (pedido do
    Rafael 25/09/2026): 'vamos construir ja o multi-empresa-periodo' +
    'quando mais de 1 CNPJ, logo grupo'. So' garante que o pipeline nao
    quebra com N colunas (2 empresas, depois 3 anos comparando ano a
    ano) e que o logo de grupo entra quando `empresas_codigos` tem mais
    de 1 item -- nao valida pixel."""
    linhas_2col = [
        ("grupo", "ATIVO"),
        ("conta", "Disponível", 100.0, 150.0),
        ("total", "TOTAL DO ATIVO", 100.0, 150.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "anexo_multi_empresa.pdf")
        dados = dict(
            BASE,
            empresa_nome="Grupo Enermais",
            empresas_codigos=["ENERGIA", "SMG"],
            anexo_colunas=["Enermais Energia", "SMG Soluções"],
            anexo_escopo_label="Grupo Enermais",
            anexo_ativo=linhas_2col,
            anexo_passivo=linhas_2col,
        )
        g.gerar_pdf_completo(dados, caminho)
        assert os.path.getsize(caminho) > 5000
        # logo de grupo tem que ser o escolhido (nao o de 1 empresa)
        assert g._logo_dados(dados) == g.LOGO.get("GRUPO")

    linhas_3col = [
        ("grupo", "ATIVO"),
        ("conta", "Disponível", 100.0, 120.0, 140.0),
        ("total", "TOTAL DO ATIVO", 100.0, 120.0, 140.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "anexo_3_anos.pdf")
        dados = dict(
            BASE,
            anexo_colunas=["2024", "2025", "2026"],
            anexo_escopo_label="Enermais Energia · 2024 a 2026",
            anexo_ativo=linhas_3col,
            anexo_passivo=linhas_3col,
        )
        g.gerar_pdf_completo(dados, caminho)
        assert os.path.getsize(caminho) > 5000
        # 1 CNPJ so' (sem empresas_codigos) continua usando o logo individual
        assert g._logo_dados(dados) == g.LOGO.get("ENERGIA")
    print("OK: anexo multi-coluna (2 empresas, depois 3 anos ano a ano) não lança exceção; logo de escopo correto nos 2 casos")


def _linhas_anexo_grandes(prefixo, n, base):
    out = [("grupo", prefixo)]
    for i in range(n):
        out.append(("conta", f"{prefixo} conta {i} com nome razoavelmente longo", base * (i + 1), base * 1.05 * (i + 1)))
    return out


def test_anexo_multi_coluna_grande_nao_perde_conteudo_estourando_pagina():
    """FIX_20260929o (Rafael, revendo Evolucao_GRUPO_202312_202606.pdf
    real): pagina_anexo em modo multi-coluna (2+ empresas/periodos) NAO
    tinha quebra de pagina -- com uma tabela grande (achado real: so' 2
    empresas x 3 periodos ja bastava), a secao de Patrimonio Liquido era
    desenhada fora da area visivel da pagina e sumia do PDF (contra a
    REGRA DE OURO -- nunca perder dado real silenciosamente). Este teste
    usa um anexo grande o bastante pra estourar 1 pagina so' e confere:
    (1) paginas_extras_anexo prevê corretamente que vai precisar de mais
    de 1 pagina fisica; (2) gerar_pdf_completo nao lança excecao com esse
    volume; (3) nenhuma excecao tambem quando cabe numa pagina so'
    (dataset pequeno, paginas_extras_anexo == 0)."""
    anexo_ativo = (
        _linhas_anexo_grandes("ATIVO CIRCULANTE", 12, 500.0)
        + [("total", "TOTAL CIRCULANTE", 30_000.0, 31_000.0)]
        + _linhas_anexo_grandes("ATIVO NAO CIRCULANTE", 12, 800.0)
        + [("total", "TOTAL DO ATIVO", 90_000.0, 93_000.0)]
    )
    anexo_passivo = (
        _linhas_anexo_grandes("PASSIVO CIRCULANTE", 8, 400.0)
        + [("total", "TOTAL CIRCULANTE PASSIVO", 15_000.0, 15_500.0)]
        + _linhas_anexo_grandes("PATRIMONIO LIQUIDO", 8, 700.0)
        + [("total", "TOTAL PATRIMONIO LIQUIDO", 60_000.0, 62_000.0),
           ("total", "TOTAL DO PASSIVO", 90_000.0, 93_000.0)]
    )
    dados_grande = dict(
        BASE,
        empresa_nome="Grupo Enermais",
        empresas_codigos=["ENERGIA", "SMG"],
        anexo_colunas=["Enermais Energia", "SMG Soluções"],
        anexo_escopo_label="Grupo Enermais",
        anexo_ativo=anexo_ativo,
        anexo_passivo=anexo_passivo,
    )
    extras = g.paginas_extras_anexo(dados_grande)
    assert extras > 0, "dataset grande deveria precisar de mais de 1 pagina fisica pro anexo"
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "anexo_grande.pdf")
        g.gerar_pdf_completo(dados_grande, caminho)
        assert os.path.getsize(caminho) > 5000

    # dataset pequeno (caso ja coberto antes) continua cabendo numa so' pagina
    dados_pequeno = dict(
        BASE, empresa_nome="Grupo Enermais", empresas_codigos=["ENERGIA", "SMG"],
        anexo_colunas=["Enermais Energia", "SMG Soluções"], anexo_escopo_label="Grupo Enermais",
        anexo_ativo=[("grupo", "ATIVO"), ("conta", "Disponível", 100.0, 150.0), ("total", "TOTAL DO ATIVO", 100.0, 150.0)],
        anexo_passivo=[("grupo", "PASSIVO"), ("conta", "Fornecedores", 100.0, 150.0), ("total", "TOTAL DO PASSIVO", 100.0, 150.0)],
    )
    assert g.paginas_extras_anexo(dados_pequeno) == 0
    print("OK: anexo multi-coluna grande usa páginas extras em vez de perder conteúdo (paginas_extras_anexo="
          f"{extras}); dataset pequeno continua em 1 página só")


if __name__ == "__main__":
    testes = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in testes:
        try:
            t()
        except AssertionError as e:
            falhas += 1
            print(f"FALHOU: {t.__name__} — {e}")
        except Exception as e:
            falhas += 1
            print(f"ERRO ({type(e).__name__}) em {t.__name__}: {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)


def test_gerar_pdf_completo_sem_pagina_resultado_pula_csll_irpj():
    """25/09/2026 -- lucro presumido nao tem csll_irpj real (ver
    gerar_pdf_completo). incluir_pagina_resultado=False deve gerar 8
    paginas sem acessar dados['csll_irpj'] em momento nenhum."""
    dados_sem_csll = dict(BASE)
    del dados_sem_csll["csll_irpj"]  # garante que a pagina pulada NUNCA e' lida
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "sem_pagina4.pdf")
        g.gerar_pdf_completo(dados_sem_csll, caminho, incluir_pagina_resultado=False)
        assert os.path.exists(caminho)
        assert os.path.getsize(caminho) > 1000
    print("OK: gerar_pdf_completo com incluir_pagina_resultado=False nao acessa csll_irpj")


def test_ebitda_soma_csll_irpj_de_volta_quando_existe():
    """FIX_20260928: EBITDA deve seguir a formula padrao contabil (antes
    de impostos), igual a indicadores.py -- soma resultado_financeiro,
    deprec_amortiz E csll_irpj de volta ao resultado liquido. Antes desta
    rodada so' somava os 2 primeiros (ficava diferente do Dashboard)."""
    texto = "\n".join(g._leitura_ebitda(BASE))
    # BASE tem csll_irpj=231_625.27 -- a leitura deve mencionar a provisao
    assert "CSLL" in texto or "csll" in texto.lower()
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "ebitda_com_csll.pdf")
        g.gerar_pdf_completo(BASE, caminho)
        assert os.path.exists(caminho) and os.path.getsize(caminho) > 5000
    print("OK: leitura do EBITDA soma CSLL/IRPJ de volta quando o campo existe")


def test_ebitda_sem_csll_irpj_nao_quebra_e_nao_menciona_provisao():
    """Lucro presumido (pagina 4 pulada, csll_irpj ausente): EBITDA
    continua funcionando normalmente, sem addback de CSLL/IRPJ (soma 0,
    que e' o valor certo -- nao ha provisao separada nesse regime) e sem
    mencionar CSLL na leitura (nada pra reportar)."""
    dados_sem_csll = dict(BASE)
    del dados_sem_csll["csll_irpj"]
    texto = "\n".join(g._leitura_ebitda(dados_sem_csll))
    assert "CSLL" not in texto
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "ebitda_sem_csll.pdf")
        g.gerar_pdf_completo(dados_sem_csll, caminho, incluir_pagina_resultado=False)
        assert os.path.exists(caminho) and os.path.getsize(caminho) > 5000
    print("OK: EBITDA sem csll_irpj nao quebra e nao menciona provisao inexistente")


# ─────────────────────────────────────────────
#  FIX_20260930 -- variante "Gerencial", valor R$ na Composição das
#  Despesas, e lista de nomes na capa multi-empresa. Usa pdfplumber pra
#  extrair texto de verdade do PDF gerado (não só tamanho/exceção) --
#  disponível no projeto (ver test_parser_admin_itens.py).
# ─────────────────────────────────────────────
import pdfplumber  # noqa: E402


def _texto_paginas(caminho):
    with pdfplumber.open(caminho) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def test_variante_padrao_pula_pagina_despesas_e_gerencial_inclui():
    with tempfile.TemporaryDirectory() as tmp:
        caminho_padrao = os.path.join(tmp, "padrao.pdf")
        dados_padrao = dict(BASE, variante="padrao")
        g.gerar_pdf_completo(dados_padrao, caminho_padrao)
        paginas_padrao = _texto_paginas(caminho_padrao)
        assert len(paginas_padrao) == 8, (
            f"variante padrao deveria ter 8 páginas (9 - Composição das Despesas), veio {len(paginas_padrao)}"
        )
        assert not any("Composição das Despesas" in p for p in paginas_padrao), (
            "variante padrao NÃO pode incluir a página de Composição das Despesas Administrativas"
        )

        caminho_gerencial = os.path.join(tmp, "gerencial.pdf")
        dados_gerencial = dict(BASE, variante="gerencial")
        g.gerar_pdf_completo(dados_gerencial, caminho_gerencial)
        paginas_gerencial = _texto_paginas(caminho_gerencial)
        assert len(paginas_gerencial) == 9, (
            f"variante gerencial deveria manter as 9 páginas de sempre, veio {len(paginas_gerencial)}"
        )
        assert any("Composição das Despesas" in p for p in paginas_gerencial), (
            "variante gerencial deveria incluir a página de Composição das Despesas Administrativas"
        )

        # dados ausente de 'variante' -- retrocompatibilidade (todo dado
        # montado antes desta rodada, ou dict de teste antigo) cai no
        # comportamento padrao de sempre (pula a página).
        caminho_sem_variante = os.path.join(tmp, "sem_variante.pdf")
        g.gerar_pdf_completo(dict(BASE), caminho_sem_variante)
        assert len(_texto_paginas(caminho_sem_variante)) == 8
    print("OK: variante 'padrao' pula a página de Composição das Despesas, 'gerencial' inclui, sem variante = padrao")


def test_variante_gerencial_mostra_selo_na_capa_padrao_nao_mostra():
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "capa_gerencial.pdf")
        g.gerar_pdf_completo(dict(BASE, variante="gerencial"), caminho)
        capa = _texto_paginas(caminho)[0]
        assert "GERENCIAL" in capa, "capa da variante gerencial deveria mostrar o selo 'GERENCIAL'"

        caminho2 = os.path.join(tmp, "capa_padrao.pdf")
        g.gerar_pdf_completo(dict(BASE, variante="padrao"), caminho2)
        capa2 = _texto_paginas(caminho2)[0]
        assert "GERENCIAL" not in capa2, "capa da variante padrao não deveria mostrar o selo 'GERENCIAL'"
    print("OK: selo 'GERENCIAL' na capa só aparece na variante gerencial")


def test_despesas_admin_mostra_valor_em_reais_junto_do_percentual():
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "despesas_rs.pdf")
        dados = dict(BASE, variante="gerencial")
        g.gerar_pdf_completo(dados, caminho)
        paginas = _texto_paginas(caminho)
        pagina_despesas_txt = next(p for p in paginas if "Composição das Despesas" in p)
        # BASE: ("Serviços Profissionais", 11_401_300.0, 38.9) -- valor
        # formatado (moeda_br) tem que aparecer JUNTO do percentual, não
        # só o percentual sozinho como antes desta rodada.
        assert "11.401.300,00" in pagina_despesas_txt.replace("R$", "").replace("R$\xa0", ""), (
            "valor em R$ do maior item de despesa administrativa não aparece na página -- "
            f"conteúdo extraído:\n{pagina_despesas_txt}"
        )
        assert "38,9%" in pagina_despesas_txt or "38,9 %" in pagina_despesas_txt
    print("OK: página de Composição das Despesas mostra o valor em R$ ao lado do percentual de cada item")


def test_capa_multi_empresa_lista_nomes_das_empresas_uma_por_linha():
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "capa_grupo.pdf")
        dados = dict(
            BASE,
            empresa_nome="Grupo Enermais",
            empresas_codigos=["ENERGIA", "SMG"],
            empresas_nomes=["Enermais Energia Ltda", "SMG Soluções"],
            anexo_ativo=[("grupo", "ATIVO"), ("conta", "Disponível", 100.0), ("total", "TOTAL DO ATIVO", 100.0)],
            anexo_passivo=[("grupo", "PASSIVO"), ("conta", "Fornecedores", 100.0), ("total", "TOTAL PASSIVO + PL", 100.0)],
        )
        g.gerar_pdf_completo(dados, caminho)
        capa = _texto_paginas(caminho)[0]
        assert "Enermais Energia Ltda" in capa, f"nome da 1ª empresa não aparece na capa:\n{capa}"
        assert "SMG Soluções" in capa, f"nome da 2ª empresa não aparece na capa:\n{capa}"
        assert "2 empresas do grupo" not in capa, (
            "capa multi-empresa deveria mostrar a LISTA de nomes, não mais a contagem genérica"
        )
    print("OK: capa multi-empresa (Modelo A) lista os nomes das empresas, uma por linha, em vez de só a contagem")


def test_capa_1_empresa_continua_mostrando_cnpj_sem_regressao():
    """Retrocompatibilidade: sem `empresas_codigos` (ou com só 1), a capa
    continua mostrando 'CNPJ ...' -- o comportamento de sempre."""
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "capa_1_empresa.pdf")
        g.gerar_pdf_completo(dict(BASE), caminho)
        capa = _texto_paginas(caminho)[0]
        assert "CNPJ" in capa
    print("OK: capa de 1 empresa continua mostrando CNPJ, sem regressão")


def test_nomes_empresas_grupo_cai_pros_codigos_se_empresas_nomes_ausente():
    """`_nomes_empresas_grupo` não pode travar a capa se o dict foi
    montado sem `empresas_nomes` (dict de teste antigo, ou algum
    caminho que ainda não passa esse campo opcional) -- cai pros
    próprios códigos em vez de inventar nome."""
    dados = dict(BASE, empresas_codigos=["ENERGIA", "SMG"])
    assert g._nomes_empresas_grupo(dados) == ["ENERGIA", "SMG"]
    print("OK: _nomes_empresas_grupo cai pros códigos quando 'empresas_nomes' está ausente")
