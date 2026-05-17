-- QA coverage: raw Open-Meteo historical vs normalized monthly cells
-- Ejecutar en Postgres (Supabase SQL editor o ``psql``).

-- Payloads Archive por país (solo los que tienen location_id FK poblado tras backfill CLI)
SELECT l.country_iso,
       COUNT(DISTINCT rp.id) AS raw_payload_archive_rows,
       COUNT(DISTINCT CONCAT_WS(',', l.lat::text, l.lon::text)) AS distinct_points_linked
FROM raw_payloads rp
JOIN locations l ON l.id = rp.location_id
JOIN data_sources ds ON ds.id = rp.source_id
WHERE ds.slug = 'open_meteo'
  AND rp.params ->> 'resource' = 'historical_archive'
GROUP BY l.country_iso
ORDER BY l.country_iso;

-- Filas mensuales normalizadas por ``source_key`` (clave CLI ``normalize-climate``)
SELECT source_key, COUNT(*) AS cell_rows FROM climate_monthly_cell GROUP BY source_key ORDER BY source_key;

-- Comprobar año/mes mínimos y máximos por punto (primeras 500 agg)
SELECT lat_round::float, lon_round::float,
       MIN(year) AS y_min, MAX(year) AS y_max,
       COUNT(*) AS month_rows
FROM climate_monthly_cell
WHERE source_key = 'openmeteo_monthly_v1'
GROUP BY lat_round, lon_round
ORDER BY month_rows DESC
LIMIT 500;
