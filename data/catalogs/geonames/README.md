# GeoNames — catálogo de capitales LATAM (sin Brasil)

## Atribución (obligatorio en productos públicos)

Datos derivados del proyecto **GeoNames**, licenciados bajo [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).

Citar: GeoNames geographical database (<https://www.geonames.org/>).

## Generar `locations.csv`

**Recomendado (rápido):** descarga un `https://download.geonames.org/export/dump/{CC}.zip` por cada país LATAM de la whitelist (segundos–pocos minutos en total), evitando el dump global `allCountries.zip` (~400MB+).

```bash
PYTHONPATH=. .venv/bin/python scripts/build_locations_from_geonames.py --download-extract \
  --output data/catalogs/geonames/locations.csv
```

**Dump completo (lento):** `--full-dump` descarga `allCountries.zip` por bloques con progreso (útil si prefieres un solo artefacto o depurar).

```bash
PYTHONPATH=. .venv/bin/python scripts/build_locations_from_geonames.py --download-extract --full-dump
```

Archivos grandes (`allCountries.txt`, muchos `.zip`) quedan bajo **`data/catalogs/geonames/.cache/`** (ignorados en git).

Opciones útiles:

- `--include-admin-capitals`: además de `PPLC`, incluye `PPLA` (capitales de subdivisión nivel 1).
- `--countries AR,UY`: sólo estos ISO (must not include BR).

## Manifest

[`manifest.json`](manifest.json) fija URL y opcionalmente `SHA256` del artefacto `allCountries.zip` tras verificar digest localmente (`sha256sum`).
