# -*- coding: utf-8 -*-
"""Diagnostico SOMENTE LEITURA da API do Sienge para a futura conferencia de NFS (notas de servico).
Nao grava nada no Supabase nem no Sienge. Gera um JSON com: (1) quantos titulos de cada tipo
(documentIdentificationId) existem por mes, (2) quais campos o titulo traz, (3) os titulos de tipo NFS*
com campos minimos + CNPJ do credor, (4) amostra do detalhe do titulo e das parcelas.
Rodado pelo workflow diagnostico-sienge-nfs.yml (acionado a mao). v0.48.3 / 15-10-2026."""
import collections
import json
import os
import sys
from datetime import date

import requests
from requests.auth import HTTPBasicAuth

BASE = os.environ["SIENGE_BASE_URL"].rstrip("/")
S = requests.Session()
S.auth = HTTPBasicAuth(os.environ["SIENGE_USER"], os.environ["SIENGE_PASSWORD"])
LIM = 200
INI, FIM = date(2026, 1, 1), date(2026, 8, 31)


def paginar(path, params=None, teto=60):
    out, off = [], 0
    while off < LIM * teto:
        p = dict(params or {}, limit=LIM, offset=off)
        r = S.get(f"{BASE}{path}", params=p, timeout=60)
        r.raise_for_status()
        d = r.json()
        rs = d.get("results", d) if isinstance(d, dict) else d
        if not rs:
            break
        out += rs
        if len(rs) < LIM:
            break
        off += LIM
    return out


def meses(ini, fim):
    y, m = ini.year, ini.month
    while (y, m) <= (fim.year, fim.month):
        a = date(y, m, 1)
        b = date(y + (m == 12), (m % 12) + 1, 1)
        yield a, date(b.year, b.month, 1).fromordinal(b.toordinal() - 1)
        y, m = b.year, b.month


res = {"gerado_em": date.today().isoformat(), "janela": [str(INI), str(FIM)]}
contagem, nfs, todos_tipos = {}, [], collections.Counter()
campos = set()
amostra_bruta = {}
for a, b in meses(INI, FIM):
    regs = paginar("/v1/bills", {"startDate": a.isoformat(), "endDate": b.isoformat()})
    c = collections.Counter((x.get("documentIdentificationId") or "").strip() for x in regs)
    contagem[a.strftime("%Y-%m")] = dict(c)
    todos_tipos.update(c)
    for x in regs:
        campos.update(x.keys())
        t = (x.get("documentIdentificationId") or "").strip().upper()
        if t.startswith("NFS") or t in ("NFSE", "NFS-E", "NFSE."):
            amostra_bruta.setdefault(t, []).append(x) if len(amostra_bruta.get(t, [])) < 3 else None
            nfs.append({k: x.get(k) for k in ("id", "debtorId", "creditorId", "documentIdentificationId", "documentNumber",
                                               "issueDate", "totalInvoiceAmount", "accessKeyNumber", "status")})
    print(a, len(regs), flush=True)
res["titulos_por_tipo_e_mes"] = contagem
res["tipos_total"] = dict(todos_tipos)
res["campos_do_titulo"] = sorted(campos)
res["amostra_bruta_nfs"] = amostra_bruta
res["nfs_titulos"] = nfs
res["nfs_com_chave"] = sum(1 for x in nfs if x.get("accessKeyNumber"))

cred = paginar("/v1/creditors", teto=80)
res["campos_do_credor"] = sorted({k for c in cred[:50] for k in c.keys()})
usados = {x["creditorId"] for x in nfs}
res["credores_das_nfs"] = {str(c["id"]): {"nome": c.get("name"), "cnpj": c.get("cnpj"), "cpf": c.get("cpf")}
                           for c in cred if c.get("id") in usados}

det = {}
for x in nfs[:3]:
    for nome, path in (("detalhe", f"/v1/bills/{x['id']}"), ("parcelas", f"/v1/bills/{x['id']}/installments")):
        try:
            r = S.get(f"{BASE}{path}", timeout=60)
            det[f"{x['id']}_{nome}"] = {"http": r.status_code, "corpo": (r.json() if r.ok else r.text[:300])}
        except Exception as e:  # diagnostico: nunca derruba
            det[f"{x['id']}_{nome}"] = {"erro": str(e)[:200]}
res["detalhe_amostra"] = det

os.makedirs("diag_out", exist_ok=True)
with open("diag_out/sienge_nfs_diag.json", "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=1, default=str)
print("ok", len(nfs), "titulos NFS;", dict(todos_tipos.most_common(12)))
