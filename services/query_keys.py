"""Claves deterministas por fuente (query_key estable para deduplicación en raw_payloads)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_prefixed(canonical: str, prefix: str) -> str:
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}:{h}"


def openmeteo_historical_key(lat: float, lon: float, start_year: int, end_year: int, archive_host: str) -> tuple[dict[str, Any], str]:
    lat_r, lon_r = round(lat, 4), round(lon, 4)
    params = {
        "resource": "historical_archive",
        "latitude": lat_r,
        "longitude": lon_r,
        "start_year": start_year,
        "end_year": end_year,
        "archive_host": archive_host,
    }
    return params, _sha256_prefixed(_canonical_json(params), "om_hist")


def openmeteo_projection_key(
    lat: float,
    lon: float,
    scenario: str,
    start_year: int,
    end_year: int,
    model: str = "MRI_AGCM3_2_S",
) -> tuple[dict[str, Any], str]:
    lat_r, lon_r = round(lat, 4), round(lon, 4)
    params = {
        "resource": "climate_projection_cmip6",
        "latitude": lat_r,
        "longitude": lon_r,
        "scenario": scenario,
        "start_year": start_year,
        "end_year": end_year,
        "models": model,
    }
    return params, _sha256_prefixed(_canonical_json(params), "om_proj")


def agri_advisor_key(
    lat: float,
    lon: float,
    crop_id: str,
    season: str,
    hist_start_year: int,
    hist_end_year: int,
) -> tuple[dict[str, Any], str]:
    lat_r, lon_r = round(lat, 4), round(lon, 4)
    params = {
        "resource": "agri_advisor_v1",
        "latitude": lat_r,
        "longitude": lon_r,
        "crop_id": crop_id.strip().lower(),
        "season": season.strip().lower(),
        "hist_start_year": hist_start_year,
        "hist_end_year": hist_end_year,
    }
    return params, _sha256_prefixed(_canonical_json(params), "agri_adv")


def firms_hotspots_key(
    lat: float,
    lon: float,
    radius_km: float,
    days: int,
    source: str,
) -> tuple[dict[str, Any], str]:
    lat_r, lon_r = round(lat, 4), round(lon, 4)
    params = {
        "resource": "firms_hotspots_csv",
        "latitude": lat_r,
        "longitude": lon_r,
        "radius_km": round(float(radius_km), 2),
        "days": int(days),
        "source": source,
    }
    return params, _sha256_prefixed(_canonical_json(params), "firms_hs")


def hf_artifact_shard_key(
    slug: str,
    dataset_repo: str,
    revision: str | None,
    shard_id: str,
) -> tuple[dict[str, Any], str]:
    params = {
        "resource": "hf_shard",
        "source_slug": slug,
        "repo": dataset_repo,
        "revision": revision or "",
        "shard_id": shard_id,
    }
    return params, _sha256_prefixed(_canonical_json(params), "hf_art")
