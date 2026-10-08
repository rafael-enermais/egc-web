# -*- coding: utf-8 -*-
"""v0.48.2 -- chave geral "EBITDA Ajustado nos relatorios": PADRAO DESATIVADO. Qualquer problema (sem tabela,
sem linha, erro de banco) = desativado; so' liga quando a linha diz 'ligado'."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nao_recorrentes as nr  # noqa: E402


class _Cur:
    def __init__(self, conn):
        self.c = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.c.sqls.append(sql)
        if self.c.erro:
            raise RuntimeError("banco fora")
        self.sql = sql

    def fetchone(self):
        if "to_regclass" in self.sql:
            return (self.c.tabela,)
        return self.c.linha


class _Conn:
    def __init__(self, tabela=True, linha=None, erro=False):
        self.tabela, self.linha, self.erro, self.sqls, self.rolled = tabela, linha, erro, [], False

    def cursor(self):
        return _Cur(self)

    def rollback(self):
        self.rolled = True


def test_padrao_e_desativado():
    assert nr.ebitda_ajustado_ativo(_Conn(tabela=False)) is False      # bloco 25 nao rodado
    assert nr.ebitda_ajustado_ativo(_Conn(tabela=True, linha=None)) is False  # tabela sem a linha
    assert nr.ebitda_ajustado_ativo(None) is False                     # sem conexao


def test_liga_so_com_valor_ligado():
    assert nr.ebitda_ajustado_ativo(_Conn(linha=("ligado",))) is True
    assert nr.ebitda_ajustado_ativo(_Conn(linha=("desligado",))) is False
    assert nr.ebitda_ajustado_ativo(_Conn(linha=("qualquer coisa",))) is False


def test_erro_de_banco_vira_desativado_e_faz_rollback():
    c = _Conn(erro=True)
    assert nr.ebitda_ajustado_ativo(c) is False
    assert c.rolled is True
