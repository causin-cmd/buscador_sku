#!/usr/bin/env python3
"""
Baja de ShipHero todas las ubicaciones con stock de cada SKU y las guarda en
data/ubicaciones.json para el Buscador de SKU (pagina estatica en GitHub Pages).

Lo corre GitHub Actions cada hora; no depende de Claude ni de un PC.

Formato de salida (compacto, la pagina lo carga entero):
  {
    "generado": "2026-11-06T16:30:00-03:00",
    "bodegas": ["Liquidos", ...],
    "skus": { "1992135": {"n": "DESTAPADOR ...", "b": "780...", "u": [[bodega_idx, "CD-08-42-60", 931, tipo], ...]} }
  }
  tipo: 1 = picking, 2 = almacenaje (vendible, no picking), 0 = no vendible.
  Solo se guardan ubicaciones con unidades > 0 y SKUs con al menos una.

Credenciales: SHIPHERO_USERNAME / SHIPHERO_PASSWORD (secrets del repo). En el PC, sin esas
variables, usa shiphero_picks_packs.py de la carpeta del CPO (keyring).

Uso:
  python ubicaciones_update.py            # respeta la ventana horaria (para el cron)
  python ubicaciones_update.py --force    # corre a cualquier hora
"""
import json, os, sys, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = Path(__file__).resolve().parent
OUT = BASE / "data" / "ubicaciones.json"
CL = ZoneInfo("America/Santiago")
VENTANA = ((8, 0), (20, 30))   # hora Chile; el cron corre mas amplio y esto filtra
PAGE = 50
# Solo la marca Unbranded (cuenta cliente 3PL legacy 84767; "Unbranded MX" 90165 queda fuera)
CUENTA = "QWNjb3VudDo4NDc2Nw=="

AUTH_URL = "https://public-api.shiphero.com/auth/token"
GQL_URL = "https://public-api.shiphero.com/graphql"

QUERY = """
query($after:String,$cuenta:String){
  warehouse_products(active:true, customer_account_id:$cuenta){
    complexity
    data(first:%d, after:$after){
      pageInfo{hasNextPage endCursor}
      edges{node{
        sku on_hand warehouse_identifier
        product{name barcode}
        locations(first:80){edges{node{quantity location{name pickable sellable}}}}
      }}
    }
  }
}""" % PAGE


def log(msg):
    print(f"[{datetime.now(CL):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _token():
    user, pwd = os.environ.get("SHIPHERO_USERNAME"), os.environ.get("SHIPHERO_PASSWORD")
    if user and pwd:
        r = requests.post(AUTH_URL, json={"username": user, "password": pwd}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Auth ShipHero fallo {r.status_code}: {r.text[:300]}")
        return r.json()["access_token"]
    if os.environ.get("GITHUB_ACTIONS"):
        raise SystemExit("Faltan los secrets SHIPHERO_USERNAME y/o SHIPHERO_PASSWORD del repo.")
    sys.path.insert(0, str(BASE.parent))
    from shiphero_picks_packs import _get_token
    return _get_token()


def _gql(query, variables, token, tries=6):
    for i in range(tries):
        wait = 30 * (i + 1)
        try:
            r = requests.post(GQL_URL, json={"query": query, "variables": variables},
                              headers={"Authorization": f"Bearer {token}"}, timeout=120)
            body = r.json() if r.ok else None
            if body is not None and not body.get("errors"):
                return body["data"]
            msg = str(body["errors"]) if body else f"HTTP {r.status_code}: {r.text[:300]}"
            low = msg.lower()
            retry = any(s in low for s in ("credit", "throttl", "429", "timeout", "timed out", "502", "503", "504"))
            if body and "time_remaining" in low:
                import re
                m = re.search(r"time_remaining['\"]?:\s*['\"]?(\d+)", msg)
                if m:
                    wait = int(m.group(1)) + 2
        except requests.RequestException as e:
            msg, retry = f"red: {e}", True
        if not retry or i == tries - 1:
            raise RuntimeError(f"ShipHero: {msg}")
        log(f"  reintento en {wait}s ({msg[:140]})")
        time.sleep(wait)


def descargar(token):
    bodegas, skus = [], {}
    after, paginas, productos, costo = None, 0, 0, 0
    t0 = time.time()
    while True:
        res = _gql(QUERY, {"after": after, "cuenta": CUENTA}, token)["warehouse_products"]
        costo += res.get("complexity") or 0
        data = res["data"]
        paginas += 1
        for e in data["edges"]:
            n = e["node"]
            productos += 1
            if not n or not (n.get("on_hand") or 0):
                continue
            wh = n.get("warehouse_identifier") or "Bodega"
            if wh not in bodegas:
                bodegas.append(wh)
            wi = bodegas.index(wh)
            prod = n.get("product") or {}
            u = []
            les = (n.get("locations") or {}).get("edges") or []
            if len(les) >= 80:
                log(f"  ojo: {n.get('sku')} tiene 80+ ubicaciones asociadas; pueden faltar algunas")
            for le in les:
                x = le["node"]
                q = x.get("quantity") or 0
                if q <= 0:
                    continue
                loc = x.get("location") or {}
                tipo = 1 if loc.get("pickable") else (2 if loc.get("sellable") else 0)
                u.append([wi, loc.get("name") or "?", q, tipo])
            if not u:
                continue
            key = (n.get("sku") or "").strip().upper()
            item = skus.setdefault(key, {"n": prod.get("name") or "", "u": []})
            if prod.get("barcode") and "b" not in item:
                item["b"] = str(prod["barcode"]).strip()
            item["u"].extend(u)
        if paginas % 20 == 0:
            log(f"  {paginas} paginas, {productos} productos, {len(skus)} SKU con stock, {time.time() - t0:.0f}s")
        if not data["pageInfo"]["hasNextPage"]:
            break
        after = data["pageInfo"]["endCursor"]
    for it in skus.values():
        it["u"].sort(key=lambda r: -r[2])
    log(f"Listo: {paginas} paginas, {productos} productos, {len(skus)} SKU con stock, "
        f"{time.time() - t0:.0f}s, complejidad estimada sumada {costo}")
    return bodegas, skus


def main():
    force = "--force" in sys.argv
    now = datetime.now(CL)
    if not force and not (VENTANA[0] <= (now.hour, now.minute) <= VENTANA[1]):
        log("Fuera de la ventana horaria; no se actualiza.")
        return
    bodegas, skus = descargar(_token())
    if not skus:
        raise SystemExit("ShipHero no devolvio ningun SKU con stock; no se sobrescribe el archivo.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out = {"generado": now.isoformat(timespec="seconds"), "bodegas": bodegas, "skus": skus}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Guardado {OUT.name}: {OUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
