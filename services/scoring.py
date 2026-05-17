"""
Scoring engine — combines climate, soil, and fire signals into an agricultural aptitude score.

Formula (from Linear doc):
  25% GAEZ suitability (proxy via lat/precip/temp envelope)
  25% soil compatibility (SoilGrids or static fallback)
  20% rainfall adequacy (vs crop precip requirements)
  15% water stress (soil moisture from ERA5)
  10% flood/fire exposure (recent FIRMS hotspot count)
   5% historical yield (always 1.0 as fallback for MVP)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import polars as pl

_CROP_REQ_PATH = Path(__file__).parent.parent / "data" / "crop_requirements.json"
_crop_requirements: dict[str, Any] = {}

_CROP_ID_ALIASES: dict[str, str] = {
    "maize": "maiz",
    "wheat": "trigo",
    "coffee": "cafe",
    "soybean": "soya",
    "vineyard": "vid",
    "bean": "frijol",
    "potato": "papa",
    "rice": "arroz",
    "sugarcane": "cana",
    "tomato": "tomate",
    "onion": "cebolla",
    "garlic": "ajo",
    "sunflower": "girasol",
    "sorghum": "sorgo",
    "cotton": "algodon",
    "quinoa": "quinua",
    "avocado": "aguacate",
}


def _load_crops() -> dict[str, Any]:
    global _crop_requirements
    if not _crop_requirements:
        _crop_requirements = json.loads(_crop_req_path_read())
    return _crop_requirements


def _crop_req_path_read() -> str:
    return _CROP_REQ_PATH.read_text()


def score_advisor(
    crop_id: str,
    historical_df: pl.DataFrame,
    hotspot_count: int = 0,
    soil_ph: float | None = None,
) -> dict[str, Any]:
    """
    Compute the aptitude score for a crop given historical climate data.

    Returns a dict with: score (0-100), aptitude (Alta/Media/Baja), factors, recommendation_text.
    """
    crops = _load_crops()
    resolved_id = _CROP_ID_ALIASES.get(crop_id, crop_id)
    req = crops.get(resolved_id)
    if req is None:
        return _unknown_crop_response(crop_id)

    factors: list[dict[str, Any]] = []

    # ── Factor 1: Rainfall adequacy (20%) ────────────────────────────────────
    annual_precip = _safe_sum(historical_df, "precip_mm")
    rain_score = _score_in_range(annual_precip, req["precip_min_mm"], req["precip_max_mm"])
    factors.append({
        "name": "Precipitación anual",
        "value": round(annual_precip, 1),
        "unit": "mm/año",
        "score": rain_score,
        "weight": 0.20,
        "status": _status(rain_score),
        "ideal": f"{req['precip_min_mm']}–{req['precip_max_mm']} mm",
    })

    # ── Factor 2: Temperature / heat stress (15% water stress proxy) ─────────
    avg_temp = _safe_mean(historical_df, "temp_c")
    temp_score = _score_in_range(avg_temp, req["temp_min_c"], req["temp_max_c"])
    factors.append({
        "name": "Temperatura media",
        "value": round(avg_temp, 1),
        "unit": "°C",
        "score": temp_score,
        "weight": 0.15,
        "status": _status(temp_score),
        "ideal": f"{req['temp_min_c']}–{req['temp_max_c']} °C",
    })

    # ── Factor 3: Water stress via soil moisture (15%) ────────────────────────
    avg_soil = _safe_mean(historical_df, "soil_moisture")
    if avg_soil is not None and not math.isnan(avg_soil):
        # soil_moisture in m³/m³; >0.2 = adequate for most crops
        soil_score = min(100, int(avg_soil * 400))
    else:
        soil_score = 60  # neutral fallback
    factors.append({
        "name": "Humedad del suelo",
        "value": round(avg_soil, 3) if (avg_soil and not math.isnan(avg_soil)) else None,
        "unit": "m³/m³",
        "score": soil_score,
        "weight": 0.15,
        "status": _status(soil_score),
        "ideal": ">0.15 m³/m³",
    })

    # ── Factor 4: GAEZ suitability proxy (25%) ────────────────────────────────
    gaez_score = _gaez_proxy(avg_temp, annual_precip, req)
    factors.append({
        "name": "Aptitud biofísica (GAEZ)",
        "value": gaez_score,
        "unit": "%",
        "score": gaez_score,
        "weight": 0.25,
        "status": _status(gaez_score),
        "ideal": "Alta aptitud en zona",
    })

    # ── Factor 5: Soil pH compatibility (part of 25% soil) ───────────────────
    if soil_ph is not None:
        ph_score = _score_in_range(soil_ph, req["soil_ph_min"], req["soil_ph_max"])
    else:
        ph_score = 65  # neutral fallback
    factors.append({
        "name": "pH del suelo",
        "value": soil_ph,
        "unit": "",
        "score": ph_score,
        "weight": 0.10,
        "status": _status(ph_score),
        "ideal": f"{req['soil_ph_min']}–{req['soil_ph_max']}",
    })

    # ── Factor 6: Fire/flood exposure (10%) ──────────────────────────────────
    fire_score = max(0, 100 - hotspot_count * 10)
    factors.append({
        "name": "Exposición a incendios",
        "value": hotspot_count,
        "unit": "hotspots/semana",
        "score": fire_score,
        "weight": 0.10,
        "status": _status(fire_score),
        "ideal": "0 hotspots",
    })

    # ── Factor 7: Historical yield proxy (5%) ────────────────────────────────
    factors.append({
        "name": "Rendimiento histórico",
        "value": None,
        "unit": "",
        "score": 70,
        "weight": 0.05,
        "status": "ok",
        "ideal": "Datos FAOSTAT no disponibles",
    })

    # ── Weighted final score ──────────────────────────────────────────────────
    final_score = int(sum(f["score"] * f["weight"] for f in factors))
    aptitude = "Alta" if final_score >= 70 else ("Media" if final_score >= 45 else "Baja")

    return {
        "crop_id": crop_id,
        "crop_name": req["name_es"],
        "score": final_score,
        "aptitude": aptitude,
        "factors": factors,
        "recommendation_text": _build_recommendation(req["name_es"], aptitude, factors, annual_precip, avg_temp),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_mean(df: pl.DataFrame, col: str) -> float:
    if col not in df.columns:
        return float("nan")
    vals = df[col].drop_nulls()
    return float(vals.mean()) if len(vals) > 0 else float("nan")


def _safe_sum(df: pl.DataFrame, col: str) -> float:
    if col not in df.columns:
        return 0.0
    vals = df[col].drop_nulls()
    annual = float(vals.mean()) * 12 if len(vals) > 0 else 0.0
    return annual


def _score_in_range(value: float, low: float, high: float) -> int:
    """Score 0-100: 100 at center of [low, high], 0 at 2× outside."""
    if math.isnan(value):
        return 50
    mid = (low + high) / 2
    half_range = (high - low) / 2
    if half_range == 0:
        return 100 if value == mid else 0
    dist = abs(value - mid)
    score = max(0, 100 - int((dist / half_range) * 60))
    if low <= value <= high:
        score = max(score, 55)
    return min(100, score)


def _gaez_proxy(avg_temp: float, annual_precip: float, req: dict) -> int:
    """Simple biophysical suitability proxy based on temp + precip envelopes."""
    t_score = _score_in_range(avg_temp, req["temp_min_c"], req["temp_max_c"])
    p_score = _score_in_range(annual_precip, req["precip_min_mm"], req["precip_max_mm"])
    return int((t_score * 0.5) + (p_score * 0.5))


def _status(score: int) -> str:
    if score >= 70:
        return "ok"
    if score >= 45:
        return "warning"
    return "risk"


def _build_recommendation(
    crop_name: str,
    aptitude: str,
    factors: list[dict],
    precip: float,
    temp: float,
) -> str:
    risks = [f["name"] for f in factors if f["status"] == "risk"]
    warnings = [f["name"] for f in factors if f["status"] == "warning"]

    if aptitude == "Alta":
        base = f"La zona tiene condiciones climáticas favorables para el cultivo de {crop_name}."
    elif aptitude == "Media":
        base = f"El cultivo de {crop_name} es viable en esta zona con manejo adecuado."
    else:
        base = f"Las condiciones climáticas representan un riesgo significativo para el cultivo de {crop_name}."

    notes = []
    if risks:
        notes.append(f"Factores críticos: {', '.join(risks)}.")
    if warnings:
        notes.append(f"Factores de atención: {', '.join(warnings)}.")
    if precip < 400:
        notes.append("Precipitación muy baja — considere irrigación suplementaria.")
    if temp > 32:
        notes.append("Temperaturas elevadas — evalúe variedades resistentes al calor.")

    return " ".join([base] + notes)


def _unknown_crop_response(crop_id: str) -> dict:
    return {
        "crop_id": crop_id,
        "crop_name": crop_id,
        "score": 0,
        "aptitude": "Desconocida",
        "factors": [],
        "recommendation_text": f"Cultivo '{crop_id}' no encontrado en la base de datos.",
    }
