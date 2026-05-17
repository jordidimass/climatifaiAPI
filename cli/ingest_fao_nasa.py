"""
CLI: ingest_fao_nasa
====================
Genera pasajes de aptitud climatica REALES cruzando:

  - NASA POWER ERA5  → temperatura, precipitacion, humedad, radiacion
                        para la zona agricola principal de 15 paises LATAM
  - crop_requirements.json → parametros agronomicos por cultivo
                              (mismos usados en el scoring del advisor)

Por cada par pais × cultivo calcula si el clima real es ALTO / MEDIO / BAJO
y genera un pasaje citable que explica POR QUE — exactamente lo que Claude
necesita para responder preguntas en /habla-ai con fundamento real.

Uso:
    uv run python -m cli.ingest_fao_nasa --dry-run
    uv run python -m cli.ingest_fao_nasa --years 2015-2023
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

# ── 15 paises LATAM ex-Brasil ──────────────────────────────────────────────

@dataclass
class Country:
    name_es: str
    iso2: str
    lat: float   # zona agricola representativa
    lon: float
    altitude_m: int  # altitud media zona agricola (para contexto)

COUNTRIES: list[Country] = [
    Country("Mexico",       "MX",  20.0, -100.0,  1800),
    Country("Colombia",     "CO",   4.5,  -74.5,  1500),
    Country("Argentina",    "AR", -34.0,  -63.0,   200),
    Country("Peru",         "PE", -12.0,  -76.0,  2500),
    Country("Chile",        "CL", -34.5,  -71.0,   300),
    Country("Ecuador",      "EC",  -1.5,  -78.5,  2200),
    Country("Bolivia",      "BO", -16.5,  -68.0,  3600),
    Country("Guatemala",    "GT",  14.5,  -90.5,  1400),
    Country("Honduras",     "HN",  14.0,  -87.0,   900),
    Country("Paraguay",     "PY", -23.0,  -57.5,   200),
    Country("Venezuela",    "VE",   8.0,  -66.0,   400),
    Country("Nicaragua",    "NI",  12.5,  -85.5,   300),
    Country("Costa Rica",   "CR",   9.5,  -84.0,  1000),
    Country("Panama",       "PA",   8.5,  -80.0,   200),
    Country("El Salvador",  "SV",  13.5,  -88.5,   700),
]

# ── Mapeo frontend → crop_requirements.json ───────────────────────────────
# Los IDs del frontend (types/crop.ts) se mapean a las claves del JSON backend

FRONTEND_TO_BACKEND: dict[str, str] = {
    "maize":     "maiz",
    "bean":      "frijol",
    "coffee":    "cafe",
    "cacao":     "cacao",
    "rice":      "arroz",
    "sugarcane": "cana",
    "tomato":    "tomate",
    "potato":    "papa",
    "avocado":   "aguacate",
    "chile":     "chile",
    "banana":    "banano",
    "sorghum":   "sorgo",
    "cassava":   "yuca",
    "cardamom":  "cardamomo",
}

CROP_REQ_PATH = Path(__file__).parent.parent / "data" / "crop_requirements.json"


def load_crop_requirements() -> dict:
    return json.loads(CROP_REQ_PATH.read_text(encoding="utf-8"))


# ── NASA POWER ─────────────────────────────────────────────────────────────

NASA_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"


async def fetch_nasa_power(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    year_start: int,
    year_end: int,
) -> dict[str, float]:
    params = {
        "parameters": "T2M,PRECTOTCORR,RH2M,ALLSKY_SFC_SW_DWN,T2M_MAX,T2M_MIN",
        "community":  "AG",
        "longitude":  lon,
        "latitude":   lat,
        "start":      year_start,
        "end":        year_end,
        "format":     "JSON",
    }
    resp = await client.get(NASA_URL, params=params, timeout=60)
    resp.raise_for_status()
    props = resp.json().get("properties", {}).get("parameter", {})

    def mean(key: str) -> float:
        vals = [v for v in props.get(key, {}).values() if isinstance(v, (int, float)) and v > -900]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    return {
        "temp_c":        mean("T2M"),
        "temp_max_c":    mean("T2M_MAX"),
        "temp_min_c":    mean("T2M_MIN"),
        "precip_mm_yr":  round(mean("PRECTOTCORR") * 30.44 * 12, 1),
        "humidity_pct":  mean("RH2M"),
        "solar_mj_m2_d": mean("ALLSKY_SFC_SW_DWN"),
    }


# ── Motor de aptitud ───────────────────────────────────────────────────────

def assess_aptitude(climate: dict[str, float], crop: dict) -> tuple[str, list[str]]:
    """
    Calcula ALTA / MEDIA / BAJA y lista de factores limitantes o favorables.
    Basado en la misma logica que services/scoring.py.
    """
    factors: list[str] = []
    score = 0
    total = 0

    # Temperatura media
    t = climate["temp_c"]
    t_opt = crop.get("temp_opt_c", 22)
    t_min = crop.get("temp_min_c", 10)
    t_max = crop.get("temp_max_c", 35)
    total += 2
    if t_min <= t <= t_max:
        if abs(t - t_opt) <= 4:
            score += 2
            factors.append(f"temperatura media {t}°C optima para el cultivo (rango {t_min}-{t_max}°C)")
        else:
            score += 1
            factors.append(f"temperatura media {t}°C aceptable pero alejada del optimo {t_opt}°C")
    else:
        factors.append(f"temperatura media {t}°C fuera del rango tolerable ({t_min}-{t_max}°C) — limitante critico")

    # Precipitacion
    p = climate["precip_mm_yr"]
    p_min = crop.get("precip_min_mm", 400)
    p_max = crop.get("precip_max_mm", 1200)
    total += 2
    if p_min <= p <= p_max:
        score += 2
        factors.append(f"precipitacion anual {p} mm dentro del rango optimo ({p_min}-{p_max} mm)")
    elif p < p_min:
        diff = round(p_min - p)
        score += 0 if diff > 300 else 1
        factors.append(f"precipitacion anual {p} mm por debajo del minimo ({p_min} mm) — deficit de {diff} mm, requiere riego complementario")
    else:
        score += 1
        factors.append(f"precipitacion anual {p} mm supera el maximo ({p_max} mm) — riesgo de exceso hidrico y enfermedades fungicas")

    # Estres termico
    t_max_obs = climate["temp_max_c"]
    heat_stress = crop.get("heat_stress_c", 35)
    total += 1
    if t_max_obs < heat_stress:
        score += 1
        factors.append(f"temperatura maxima media {t_max_obs}°C no supera umbral de estres termico ({heat_stress}°C)")
    else:
        factors.append(f"temperatura maxima media {t_max_obs}°C supera umbral de estres termico ({heat_stress}°C) — riesgo en floracion")

    # Heladas (si el cultivo no las tolera)
    t_min_obs = climate["temp_min_c"]
    total += 1
    frost_tolerant = crop.get("frost_tolerance", False)
    if t_min_obs < 2 and not frost_tolerant:
        factors.append(f"temperatura minima {t_min_obs}°C indica riesgo de heladas — cultivo no tolerante")
    elif t_min_obs < 2 and frost_tolerant:
        score += 1
        factors.append(f"temperatura minima {t_min_obs}°C con posible riesgo de heladas — cultivo tolerante")
    else:
        score += 1
        factors.append(f"temperatura minima {t_min_obs}°C sin riesgo de heladas")

    ratio = score / total
    if ratio >= 0.75:
        aptitude = "ALTA"
    elif ratio >= 0.45:
        aptitude = "MEDIA"
    else:
        aptitude = "BAJA"

    return aptitude, factors


# ── Generador de pasajes ───────────────────────────────────────────────────

def build_passage(country: Country, crop_key: str, crop: dict, climate: dict[str, float]) -> str:
    aptitude, factors = assess_aptitude(climate, crop)
    name_es   = crop.get("name_es", crop_key)
    sci_name  = crop.get("scientific", "")
    factors_str = "; ".join(factors)

    return (
        f"Aptitud agroclimática en {country.name_es} para {name_es} "
        f"({sci_name}): {aptitude}. "
        f"Zona agricola representativa: lat {country.lat}, lon {country.lon}, "
        f"altitud aprox. {country.altitude_m} msnm. "
        f"Factores determinantes: {factors_str}. "
        f"Clima observado (NASA POWER ERA5, promedio historico): "
        f"temperatura media {climate['temp_c']}°C, "
        f"maxima media {climate['temp_max_c']}°C, "
        f"minima media {climate['temp_min_c']}°C, "
        f"precipitacion {climate['precip_mm_yr']} mm/año, "
        f"humedad relativa {climate['humidity_pct']}%, "
        f"radiacion solar {climate['solar_mj_m2_d']} MJ/m²/día. "
        f"Fuente: NASA POWER ERA5 (clima) + parametros agronomicos FAO/GAEZ."
    )


# ── Orquestador ────────────────────────────────────────────────────────────

async def run(year_start: int, year_end: int, dry_run: bool, collection: str) -> None:
    from services.embedder import embed
    from services.vector_store import QdrantVectorStore

    store = QdrantVectorStore(collection=collection)

    # Cargar requerimientos de cultivos
    all_crops = load_crop_requirements()
    crops: dict[str, dict] = {}
    for frontend_id, backend_key in FRONTEND_TO_BACKEND.items():
        if backend_key in all_crops:
            crops[backend_key] = all_crops[backend_key]
        else:
            print(f"  ⚠ Cultivo '{backend_key}' no encontrado en crop_requirements.json — omitido")

    print(f"Cultivos cargados: {list(crops.keys())}")

    # NASA POWER para los 15 paises
    print(f"\nDescargando clima NASA POWER ({year_start}-{year_end}) para {len(COUNTRIES)} paises...")
    climate: dict[str, dict[str, float]] = {}

    async with httpx.AsyncClient() as client:
        for country in tqdm(COUNTRIES, desc="NASA POWER"):
            try:
                climate[country.iso2] = await fetch_nasa_power(
                    client, country.lat, country.lon, year_start, year_end
                )
            except Exception as exc:
                print(f"  ⚠ {country.name_es}: {exc}")
                climate[country.iso2] = {
                    "temp_c": 0, "temp_max_c": 0, "temp_min_c": 0,
                    "precip_mm_yr": 0, "humidity_pct": 0, "solar_mj_m2_d": 0,
                }

    # Generar pasajes: 15 paises × N cultivos
    passages: list[tuple[str, str, str]] = []

    for country in COUNTRIES:
        clim = climate.get(country.iso2, {})
        if not clim or clim["temp_c"] == 0:
            continue
        for crop_key, crop in crops.items():
            text   = build_passage(country, crop_key, crop, clim)
            source = f"NASA-POWER+FAO-GAEZ/{country.name_es}/{crop_key}"
            passages.append((str(uuid.uuid4()), text, source))

    total_countries = len({p[2].split("/")[1] for p in passages})
    total_crops     = len({p[2].split("/")[2] for p in passages})
    print(f"\n{len(passages)} pasajes generados — {total_countries} paises x {total_crops} cultivos")

    if dry_run:
        print("\n--- DRY RUN (6 pasajes de muestra) ---")
        step = max(1, len(passages) // 6)
        for i in range(0, min(len(passages), 6 * step), step):
            _, text, source = passages[i]
            print(f"\n[{source}]\n{text}\n")
        return

    # Embeber + upsert en Qdrant
    print("\nEmbebiendo con ClimateBERT y upsertando en Qdrant...")
    BATCH = 32
    all_vectors: dict[str, tuple[list[float], dict]] = {}

    for i in tqdm(range(0, len(passages), BATCH), desc="Embedding"):
        batch = passages[i : i + BATCH]
        texts = [t for _, t, _ in batch]
        vecs  = await embed(texts)
        for (uid, text, source), vec in zip(batch, vecs):
            all_vectors[uid] = (vec, {"text": text, "source": source})

    # Upsert en batches para no saturar el timeout de Qdrant Cloud
    UPSERT_BATCH = 50
    items = list(all_vectors.items())
    for i in tqdm(range(0, len(items), UPSERT_BATCH), desc="Upsert Qdrant"):
        batch = dict(items[i : i + UPSERT_BATCH])
        await store.upsert(batch)

    print(f"\nIngesta completada: {len(all_vectors)} vectores en coleccion '{collection}'")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years",      default="2015-2023")
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--collection", default="climatifai_docs")
    args = parser.parse_args()
    y_start, y_end = (int(y) for y in args.years.split("-"))
    asyncio.run(run(y_start, y_end, args.dry_run, args.collection))


if __name__ == "__main__":
    main()
