# Base de datos (Postgres + opcional Qdrant)

## Desarrollo con Docker Compose

```bash
docker compose up -d postgres
```

URL de ejemplo (usuario `postgres` como en [`docker-compose.yml`](docker-compose.yml)):

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/climatifai
```

## Postgres nativo en macOS (Homebrew)

Aquí suele **no existir** el rol `postgres`; el superusuario por defecto es **tu usuario de macOS** (`whoami`). Usa ese nombre en la URL:

```
DATABASE_URL=postgresql+asyncpg://TU_USUARIO@localhost:5432/climatifai
```

Crear base de datos si hace falta:

```bash
createdb climatifai
```

## Migraciones Alembic

[`alembic/env.py`](alembic/env.py) carga antes de todo los `.env` de **`climatifaiAPI/.env`** y, si existe, **`../climatifai/.env`**, de modo que **`DATABASE_URL` no dependa del directorio de trabajo** donde se lanzó Alembic.

```bash
PYTHONPATH=. .venv/bin/python -m alembic upgrade head
```

La revisión **`002_locations`** crea tabla `locations` y añade `raw_payloads.location_id` enlazando ingestas por catálogo; ver [**`docs/locations.md`**](docs/locations.md).

Alembic **no** tiene `--verbose`. Para volcar SQL **sin ejecutar** en BD: `upgrade head --sql`.

Comprueba DNS/conexión y aplica migraciones con la misma carga de `.env`:

```bash
PYTHONPATH=. .venv/bin/python scripts/supabase_db.py check
PYTHONPATH=. .venv/bin/python scripts/supabase_db.py migrate
```

Para listar tablas en `public` con la misma URL que el `.env` del API:

```bash
PYTHONPATH=. .venv/bin/python scripts/list_db_tables.py
```

Alembic convierte **`postgresql+asyncpg://`** a **`postgresql+psycopg://`** dentro de [`db/config.py`](db/config.py).

Si **`DATABASE_URL` no está definida**, el fallback para migraciones es `postgresql+psycopg://$USER@localhost:5432/climatifai` (encaja con Homebrew/Mac sin rol `postgres`).

Para la API FastAPI, **sin `DATABASE_URL`** no se activa Postgres (read-through desactivado). Si quieres read-through en dev, pon `DATABASE_URL` en `.env` o en el shell.

Con `DATABASE_URL`, el API persiste/leé `raw_payloads` según TTL (`OPENMETEO_CACHE_MAX_AGE_SEC`, `ADVISOR_CACHE_MAX_AGE_SEC`, `FIRMS_CACHE_MAX_AGE_SEC`).

NASA FIRMS (Area API): define `FIRMS_API_KEY` (MAP key desde [FIRMS map_key](https://firms.modaps.eosdis.nasa.gov/api/map_key/)). El parámetro de ventana temporal **solo admite 1–5 días** en la URL oficial; valores mayores provocan HTTP 400 y el cliente las limita a 5.

## Producción — Supabase (CLI + proyecto remoto)

En la raíz del API (`Untitled/climatifaiAPI/`) hay carpeta [`supabase/`](supabase/) generada con:

```bash
npx --yes supabase@latest init
```

### Autenticación

`supabase link` necesita una sesión. Elige uno:

**A) Login interactivo (navegador)** — si tienes el CLI instalado globalmente (`brew install supabase/tap/supabase`):

```bash
supabase login
```

**B) Solo token (CLI / automatización)** — crea uno en el [dashboard de tokens](https://supabase.com/dashboard/account/tokens):

```bash
export SUPABASE_ACCESS_TOKEN="tu_personal_access_token"
cd /path/to/Untitled/climatifaiAPI
npx supabase@latest link --project-ref vdtnyxepdvsavagmogft
```

(Opción equivalente: `npx supabase@latest login --token "$SUPABASE_ACCESS_TOKEN"`.)

El `link` puede pedir **la contraseña de base de datos** del proyecto (**Settings → Database → Database password**).

### Variables para la aplicación FastAPI / Alembic

En Dashboard: **Project Settings → Database → Connection string**. Para `asyncpg` suele funcionar bien el modo **transaction pooler**, puerto **6543**:

```
DATABASE_URL=postgresql+asyncpg://postgres.[REF]:[TU_PASSWORD]@aws-....pooler.supabase.com:6543/postgres
```

Para migraciones muy largas sin pooler puedes usar conexión directa **5432** (solo en admin / una sola conexión), según política Supabase.

**Notas:**

- Este repo sigue usando **Alembic** (`alembic/versions/`). Las migraciones SQL nativas del CLI pueden convivir, pero hay que coordinar orden y duplicidad; hasta decidir otro flujo, aplica **`alembic upgrade head`** contra esa URL cuando `DATABASE_URL` apunte al proyecto enlazado.
- Si ves avisos de versión Postgres al usar `supabase db ...`, revisa **`supabase/config.toml`** → `[db]` → **`major_version`** y alinea con lo que marca el proyecto en el dashboard (`Database → Postgres version`).

### Errores de DNS (`failed to resolve host`)

Si desde tu ordenador ves **`nodename nor servname provided, or not known`**, ha fallado la **resolución del hostname** (no la contraseña de Postgres):

- Ejecuta en Terminal **`dig TU_HOST +short`** (el host exacto de tu `DATABASE_URL`). Si está vacío, el problema es **DNS/red local**.
- Prueba cambiar temporalmente DNS a **8.8.8.8** / **1.1.1.1**, pausar **VPN**, filtros (**Pi-hole** / DNS corporativo) o valores erróneos de **`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`**.
- La app puede usar pooler `:6543` y Alembic la conexión directa `:5432`; igualmente debes poder resolver el servidor (ver **`scripts/supabase_db.py check`**, incluye chequeo DNS).

## Artefactos HF / archivos pesados

- `ARTIFACT_STORAGE_ROOT` (default `./data/artifacts`).
- Convención recomendada en `data_artifacts`: `hf/<repo_slug>/<revision>/...`.
- CLI: `climatifai-ingest hf-register --path ... --kind parquet --revision ...`

## Qdrant

- Env: `QDRANT_URL=http://localhost:6333`.
- Implementación en `services/vector_store.py`.

## Catálogo de ubicaciones (LATAM, sin Brasil) + ingest Open‑Meteo

Definición operativa (`PPLC`, `PPLA` opcional, exclusión `BR`), GeoNames, CSV `locations`, tabla `locations` y flujo CLI **backfill**:

- [`docs/locations.md`](docs/locations.md)

## Ingester CLI

```
climatifai-ingest openmeteo-yearly --lat -12.046 --lon -77.042 --from-year 2018 --to-year 2023
climatifai-ingest firms-hotspots --lat -12.046 --lon -77.042
# FIRMS para cada fila de `locations` (sin --lat/--lon; opc. --start-offset, --sleep-ms, --countries):
climatifai-ingest firms-hotspots --sleep-ms 2500 --start-offset 51
climatifai-ingest normalize-climate --source-key openmeteo_monthly_v1
```
