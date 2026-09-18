# Ozon Live — one-time proxy setup

The automation is already configured for Decodo Residential:
- provider: decodo
- endpoint: gate.decodo.com
- port: 7000
- country: RU
- city: selected automatically per scan
- session: sticky per city scan

Only two GitHub Actions secrets are required:

- `OZON_PROXY_USER`
- `OZON_PROXY_PASS`

Repository path:
Settings → Secrets and variables → Actions → New repository secret

The workflow fails closed when credentials are missing. It never writes a blocked/data-center response as a search position.

Before every Ozon scan, `proxy_preflight.py` verifies the residential connection for every configured city. Only after a successful preflight does the Chrome scanner run.
