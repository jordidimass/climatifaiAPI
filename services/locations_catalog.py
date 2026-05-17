"""Whitelist ISO 3166-1 alpha-2 para LATAm (sin Brasil) y utilidades de redondeo/coords."""

from __future__ import annotations

import hashlib
import os


# Lista soberanos + dependencias habitualmente clasificadas en América Latina / Caribe.
# Brasil (BR) excluido explícitamente por producto.
LATAM_ISO_WITHOUT_BR: frozenset[str] = frozenset(
    {
        "MX",
        "BZ",
        "GT",
        "SV",
        "HN",
        "NI",
        "CR",
        "PA",
        "CU",
        "DO",
        "HT",
        "JM",
        "TT",
        "BB",
        "BS",
        "AG",
        "DM",
        "GD",
        "KN",
        "LC",
        "VC",
        "AR",
        "BO",
        "CL",
        "CO",
        "EC",
        "FK",
        "GF",
        "GY",
        "PY",
        "PE",
        "SR",
        "UY",
        "VE",
    }
)


def is_whitelisted_country(iso_cc: str) -> bool:
    cc = iso_cc.strip().upper()
    if cc == "BR":
        return False
    return cc in LATAM_ISO_WITHOUT_BR


def round_lat_lon(lat: float, lon: float) -> tuple[float, float]:
    """Igual que query_keys.OpenMeteo: 4 decimales."""
    return round(lat, 4), round(lon, 4)


def synthetic_geonames_id(country_iso: str, lat: float, lon: float) -> int:
    """ID negativo estable para ubicaciones sintéticas (API on-demand)."""
    lat_r, lon_r = round_lat_lon(lat, lon)
    blob = f"{country_iso.upper()}|{lat_r}|{lon_r}".encode()
    digest = hashlib.sha256(blob).digest()
    negative = -(int.from_bytes(digest[:6], "big") % (2**31 - 1) + 1)
    return negative


def lazy_upsert_enabled() -> bool:
    return os.getenv("LOCATION_LAZY_UPSERT", "").strip().lower() in {"1", "true", "yes", "on"}
