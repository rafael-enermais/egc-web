# -*- coding: utf-8 -*-
"""Layout (30/09/2026): anexo nunca quebra uma secao no meio; capa
multi-CNPJ com "Grupo Enermais" em destaque + lista menor; Modelo B
compartilha o mesmo bloco de capa."""
import os
import sys
import tempfile
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gerador_relatorio_comentado as A  # noqa: E402
import gerador_relatorio_comparativo as B  # noqa: E402
import test_gerador_relatorio_comparativo as TB  # noqa: E402


def _secoes(prefixo, n_secoes, n_contas, total_final):
    linhas = []
    for s in range(n_secoes):
        nome = f"{prefixo} SECAO {s + 1}"
        linhas.append(("grupo", nome))
        for i in range(n_contas):
            linhas.append(("conta", f"{nome} conta {i}", 1000.0 + i, 1100.0 + i, 1200.0 + i))
        linhas.append(("subtotal", f"Total {nome}", 9000.0, 9100.0, 9200.0))
    linhas.append(("total", total_final, 99999.0, 99999.0, 99999.0))
    return linhas


def _dados_anexo(n_secoes, n_contas):
    d = dict(TB.BASE)
    d["anexo_ativo"] = _secoes("ATIVO", n_secoes, n_contas, "TOTAL DO ATIVO")
    d["anexo_passivo"] = _secoes("PASSIVO", n_secoes, n_contas, "TOTAL DO PASSIVO")
    return d


def _paginas_texto(dados, tmp):
    caminho = os.path.join(tmp, "x.pdf")
    B.gerar_pdf_comparativo(dados, caminho)
    with pdfplumber.open(caminho) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def test_anexo_nunca_quebra_uma_secao_no_meio_quando_ela_cabe_numa_pagina():
    with tempfile.TemporaryDirectory() as tmp:
        for n_contas in (6, 8, 9, 10, 11):          # varios tamanhos -> varios pontos de quebra
            dados = _dados_anexo(n_secoes=5, n_contas=n_contas)
            paginas = _paginas_texto(dados, tmp)
            for lado in ("ATIVO", "PASSIVO"):
                for s in range(5):
                    nome = f"{lado} SECAO {s + 1}"
                    pg_cab = [i for i, t in enumerate(paginas) if nome in t]
                    pg_tot = [i for i, t in enumerate(paginas) if f"Total {nome}" in t]
                    pg_ult = [i for i, t in enumerate(paginas) if f"{nome} conta {n_contas - 1}" in t]
                    assert pg_cab and pg_tot and pg_ult, f"{nome} sumiu do PDF (n_contas={n_contas})"
                    assert pg_cab[0] == pg_ult[0] == pg_tot[0], (
                        f"secao '{nome}' (n_contas={n_contas}) quebrada entre paginas: "
                        f"cabecalho pg{pg_cab[0] + 1}, ultima conta pg{pg_ult[0] + 1}, total pg{pg_tot[0] + 1}"
                    )


def test_anexo_repete_o_cabecalho_ao_abrir_a_pagina_seguinte_sem_perder_linha():
    with tempfile.TemporaryDirectory() as tmp:
        dados = _dados_anexo(n_secoes=5, n_contas=10)
        paginas = _paginas_texto(dados, tmp)
        com_anexo = [t for t in paginas if "Anexos" in t]
        assert len(com_anexo) >= 2, "esperava o anexo em 2+ paginas"
        assert any("Anexos (continuação)" in t for t in paginas)
        texto = "\n".join(paginas)
        for lado in ("ATIVO", "PASSIVO"):
            for s in range(5):
                for i in range(10):
                    assert f"{lado} SECAO {s + 1} conta {i}" in texto, "linha do anexo perdida na paginacao"
        assert "TOTAL DO ATIVO" in texto and "TOTAL DO PASSIVO" in texto


def test_secao_maior_que_uma_pagina_quebra_normalmente_sem_perder_linha():
    with tempfile.TemporaryDirectory() as tmp:
        dados = _dados_anexo(n_secoes=1, n_contas=50)       # 50 contas = ~750pt, nao cabe numa pagina
        paginas = _paginas_texto(dados, tmp)
        texto = "\n".join(paginas)
        for i in range(50):
            assert f"ATIVO SECAO 1 conta {i}" in texto
        assert A.paginas_extras_anexo(dados) >= 1


def test_paginas_extras_anexo_bate_com_o_pdf_real_com_a_nova_regra():
    with tempfile.TemporaryDirectory() as tmp:
        dados = _dados_anexo(n_secoes=5, n_contas=9)
        paginas = _paginas_texto(dados, tmp)
        base = TB.BASE
        n_sem_anexo_extra = len(_paginas_texto(dict(base), tmp))
        assert len(paginas) - n_sem_anexo_extra == A.paginas_extras_anexo(dados) - A.paginas_extras_anexo(dict(base))


# ------------------------------------------------------------------ capa
def _textos_da_capa(modulo, funcao, dados):
    chamadas = []
    modulo_txt, A_txt = modulo.txt, A.txt
    A.txt = lambda c, x, y, s, **kw: chamadas.append((y, s, kw))
    modulo.txt = A.txt
    orig_image, orig_rect = A.image, A.rect
    A.image = lambda *a, **kw: None
    modulo.image = A.image
    try:
        from reportlab.pdfgen import canvas
        with tempfile.TemporaryDirectory() as tmp:
            c = canvas.Canvas(os.path.join(tmp, "c.pdf"))
            getattr(modulo, funcao)(c, dados, 1, 5)
    finally:
        A.txt, modulo.txt = A_txt, modulo_txt
        A.image = orig_image
        modulo.image = orig_image
    return chamadas


NOMES = ["Enermais Energia Ltda", "SMG Soluções Ltda", "Enermais Engenharia Ltda",
         "Enermais Renováveis Ltda", "Enermais Construtora Ltda", "Enermais Soluções Ltda"]


def _dados_grupo(base):
    return dict(base, empresa_nome="Grupo Enermais", cnpj="", empresas_codigos=["ENERGIA", "SMG", "ENG", "RENOV", "CONST", "SOL"],
                empresas_nomes=NOMES, periodo_label="06/2026", periodo_extenso="", periodo_range_label="2025 A 2026")


def _checar_capa_grupo(chamadas):
    titulo = next(k for k in chamadas if k[1] == "Grupo Enermais")
    assert titulo[2]["size"] >= 18, "'Grupo Enermais' tem que ser subtitulo grande"
    listados = [k for k in chamadas if k[1] in NOMES]
    assert [k[1] for k in listados] == NOMES, "lista de empresas na mesma ordem, uma por linha"
    assert all(k[2]["size"] < titulo[2]["size"] / 1.5 for k in listados), "nomes em fonte menor que o subtitulo do grupo"
    ys = [k[0] for k in listados]
    assert ys == sorted(ys) and min(ys) > titulo[0], "lista logo abaixo do subtitulo"
    pilula = next(k for k in chamadas if "PERÍODO" in k[1])
    assert pilula[0] > max(ys), "pilula do periodo nao pode sobrepor a lista"


def test_capa_modelo_a_multi_cnpj_grupo_em_destaque_e_lista_menor():
    dados = _dados_grupo(dict(empresa_codigo="ENERGIA", periodo_label="06/2026", periodo_extenso=""))
    _checar_capa_grupo(_textos_da_capa(A, "pagina_capa", dados))
    dados["variante"] = "gerencial"
    _checar_capa_grupo(_textos_da_capa(A, "pagina_capa", dados))


def test_capa_modelo_b_multi_cnpj_grupo_em_destaque_e_lista_menor():
    _checar_capa_grupo(_textos_da_capa(B, "pagina_capa_comparativa", _dados_grupo(dict(TB.BASE))))


def test_capa_1_empresa_continua_igual():
    dados = dict(empresa_codigo="ENERGIA", empresa_nome="Enermais Energia Ltda", cnpj="47.040.664/0001-48",
                 periodo_label="06/2026", periodo_extenso="")
    chamadas = _textos_da_capa(A, "pagina_capa", dados)
    nome = next(k for k in chamadas if k[1] == "Enermais Energia Ltda")
    assert nome[2]["size"] == 12
    assert any(k[1] == "CNPJ 47.040.664/0001-48" for k in chamadas)
