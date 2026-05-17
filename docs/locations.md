# Catálogo de ubicaciones (LATAM, sin Brasil)

Este documento fija definiciones reproducibles entre **GeoNames**, la tabla Postgres `locations`, las claves de read-through [`services/query_keys.py`](../services/query_keys.py) y la ingesta Open‑Meteo.

## Objetivos

- Lista **cerrada** y **versionada** de capitales antes de lanzar ingestas masivas.
- **Exclusión sistemática de Brasil** (`ISO 3166-1 alpha-2`: `BR`) en el ETL y en la whitelist de runtime.
- Alineación con Open‑Meteo: coordenadas usadas para `query_key` se **redondean a 4 decimales**, igual que `openmeteo_historical_key`.

## ¿Qué fila cuenta como “capital” en el MVP?

Usamos GeoNames **`feature.class = P`** (lugares poblados) y:

| Modo CLI | GeoNames `feature code` | Significado |
|----------|-------------------------|-------------|
| **Por defecto** (`national-capitals-only`) | **`PPLC`** | Capital nacional del país soberano (`country code` en la lista LATAM\*). |
| **Opción ampliada** (`--include-admin-capitals`) | **`PPLA`** additionally | Sedes habitualmente usadas como capital de ** primera subdivisión administrativa** (ej. capitales departamentales / provinciales). |

\* Lista de países: ver [`LATAM_ISO_WITHOUT_BR`](../services/locations_catalog.py).

**No** entran en este MVP otros códigos (`PPL`, `PPLX`, …): el hito decidido fue “solo capitales” para acotar coste/API.

### Brasil y territorios

- Todo registro con `country code == BR` se **elimina**.
- Departamentos franceses ultramarinos u otros codificados fuera del bloque pueden requerir ampliaciones manuales; el ETL permite `--extra-country` puntual (`GF`, etc.) si producto/legal lo requiere.

## Fuente GeoNames

- Artefactos oficiales: [GeoNames Download](https://download.geonames.org/export/dump/).
- **Modo rápido (por defecto):** un **`{CC}.zip`** por cada país de la whitelist (tamaño pequeño; el script los descarga uno a uno).
- **Modo completo:** `allCountries.zip` (muy grande) solo con `--full-dump` en el script; útil si queréis un único artefacto o el `SHA256` del manifest.
- **Licencia CC BY 4.0** GeoNames — atribución en [`data/catalogs/geonames/README.md`](../data/catalogs/geonames/README.md) y [`manifest.json`](../data/catalogs/geonames/manifest.json) (el `SHA256` allí aplica sobre todo al dump global).

## Artefactos en el repo

| Ruta | Uso |
|------|-----|
| [`data/catalogs/geonames/README.md`](../data/catalogs/geonames/README.md) | Atribución y cómo generar CSV. |
| [`data/catalogs/geonames/manifest.json`](../data/catalogs/geonames/manifest.json) | URL canónica y `SHA256` del zip verificado tras descarga (`null` hasta primer run). |
| `data/catalogs/geonames/locations.csv` | **Salida gitignore** por defecto; generado con [`scripts/build_locations_from_geonames.py`](../scripts/build_locations_from_geonames.py). |

## Postgres: tabla `locations` y vínculos

- `locations.geonames_id` — identidad GeoNames (entero **positivo**). Filas sintéticas creadas vía API usan **`geonames_id < 0`** (derivado estable de país+coords) para preservar unicidad sin colisiones.
- `raw_payloads.location_id` — FK opcional; la ingesta bulk y el runtime pueden anclar un payload climático **a la fila catálogo** que originó la petición.
- Índices: ver migración Alembic `002_*`.

## Ingestión

```bash
# 1. CSV (por país, rápido). Opcional: --full-dump para allCountries.zip
PYTHONPATH=. .venv/bin/python scripts/build_locations_from_geonames.py --download-extract \
  --include-admin-capitals  # opcional

# 2. Cargar locations en Postgres (tras migraciones)
PYTHONPATH=. .venv/bin/python scripts/load_locations_csv.py --csv data/catalogs/geonames/locations.csv

# 3. Backfill histórico Open‑Meteo (rate limit configurable)
PYTHONPATH=. .venv/bin/python -m cli.ingest locations-backfill-openmeteo \
  --from-year 2015 --to-year 2024 --sleep-ms 400

# 4. Normalizar a climate_monthly_cell
PYTHONPATH=. .venv/bin/python -m cli.ingest normalize-climate --source-key openmeteo_monthly_v1
```

## Alta “on‑demand” desde la API (opcional)

- Variable `LOCATION_LAZY_UPSERT=1` para activar INSERT de `locations` sintéticas **tras un cache miss** de Open‑Meteo (cuando sí se descarga/pega nuevo `raw_payload`, no cuando el TTL todavía devuelve datos frescos en `raw_payloads`).
- Parámetro de query **`country_iso`** (`/agri/climate`, `/agri/advisor`): obligatorio cuando el lazy está activo y quieres anclar el payload a `locations`; debe ser ISO3166-1 alpha-2 dentro de LATAM y **≠ BR**. Si falta o es `BR`, no se crea ubicación nueva y los endpoints siguen respondiendo igual.
- Coordinadas se redondean a 4 decimales antes de persistir unicidad `(country_iso, lat, lon)`.

## QA de cobertura

Ver [`scripts/qa_climate_coverage.sql`](../scripts/qa_climate_coverage.sql).
