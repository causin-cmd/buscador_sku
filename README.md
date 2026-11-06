# Buscador de SKU · Unbranded

Herramienta para encontrar un SKU dentro de cajas con productos parecidos: ingresas el SKU que
buscas, escaneas la caja y la pantalla se pone verde con sonido cuando aparece. Muestra además
en qué ubicaciones de ShipHero está el SKU y cuántas unidades hay en cada una.

- Página: https://causin-cmd.github.io/buscador_sku/
- Datos: solo la marca **Unbranded** (cuenta cliente 3PL 84767; "Unbranded MX" queda fuera).
  Se escanea por SKU o por código de barras.

## Piezas

- `index.html` — la página (estática, lee `data/ubicaciones.json` y lo recarga cada 10 minutos).
- `ubicaciones_update.py` — baja de ShipHero las ubicaciones con unidades > 0 de cada SKU
  (~4,5 min, ~0,2 MB).
- `.github/workflows/update.yml` — cron cada hora 8:00–20:30 hora Chile (copia de referencia:
  `workflow_update.yml`). Ejecutar a mano: pestaña Actions → "Actualizar ubicaciones" → Run workflow.
- Secrets del repo: `SHIPHERO_USERNAME`, `SHIPHERO_PASSWORD` (los mismos que picks_packs).

En el PC: `python ubicaciones_update.py --force` (usa las credenciales del keyring del CPO).
