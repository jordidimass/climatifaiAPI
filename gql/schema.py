"""
Schema GraphQL completo — Strawberry + FastAPI.

Queries:
  ragContext(query, limit)              → RAG con ClimateBERT + Qdrant
  advisor(lat, lon, cropId, season)     → score de aptitud agricola (UC-1)
  climate(lat, lon, from, to, scenario) → serie historica + proyeccion CMIP6 (UC-3)
  geocode(q, count)                     → busqueda de regiones LATAM (UC-9)
  soil(lat, lon)                        → propiedades de suelo (stub CVA-13)
  hotspots(lat, lon, radiusKm, days)    → hotspots NASA FIRMS (UC-5)
  fireRisk(lat, lon, radiusKm)          → score de riesgo de incendio (UC-5)
  alerts                                → alertas agroclimaticas reales LATAM ex-Brasil
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Annotated, Optional

import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.scalars import JSON

from services.embedder import embed_one
from services.vector_store import QdrantVectorStore

_COLLECTION = os.getenv("QDRANT_COLLECTION", "climatifai_docs")


def _store() -> QdrantVectorStore:
    return QdrantVectorStore(collection=_COLLECTION)


# ═══════════════════════════════════════════════════════════════════
# TYPES
# ═══════════════════════════════════════════════════════════════════

# ── RAG ───────────────────────────────────────────────────────────

@strawberry.type
class RagPassage:
    text:   str
    source: str
    score:  float


# ── Advisor ───────────────────────────────────────────────────────

@strawberry.type
class AdvisorFactor:
    label:       str
    score:       float
    weight:      float
    status:      str
    description: Optional[str] = None


@strawberry.type
class AdvisorResult:
    crop_id:             str
    score:               float
    aptitude:            str          # Alta / Media / Baja
    recommendation_text: str
    factors:             list[AdvisorFactor]
    season:              str
    lat:                 float
    lon:                 float


# ── Climate ───────────────────────────────────────────────────────

@strawberry.type
class ClimateMonth:
    year:          int
    month:         int
    temp_c:        Optional[float] = None
    precip_mm:     Optional[float] = None
    soil_moisture: Optional[float] = None


@strawberry.type
class ClimateResult:
    lat:        float
    lon:        float
    scenario:   str
    historical: list[ClimateMonth]
    projected:  list[ClimateMonth]


# ── Geocode ───────────────────────────────────────────────────────

@strawberry.type
class GeocodePlace:
    name:       str
    lat:        float
    lon:        float
    country:    Optional[str] = None
    admin1:     Optional[str] = None
    elevation:  Optional[float] = None
    population: Optional[int] = None


@strawberry.type
class GeocodeResult:
    results: list[GeocodePlace]


# ── Soil ──────────────────────────────────────────────────────────

@strawberry.type
class SoilResult:
    lat:     float
    lon:     float
    ph:      Optional[float] = None
    texture: Optional[str]  = None
    note:    Optional[str]  = None


# ── Fires ─────────────────────────────────────────────────────────

@strawberry.type
class Hotspot:
    lat:        float
    lon:        float
    brightness: Optional[float] = None
    frp:        Optional[float] = None
    acq_date:   Optional[str]  = None
    acq_time:   Optional[str]  = None
    satellite:  Optional[str]  = None
    confidence: Optional[str]  = None
    source:     Optional[str]  = None


@strawberry.type
class HotspotsResult:
    lat:       float
    lon:       float
    radius_km: float
    days:      int
    count:     int
    hotspots:  list[Hotspot]


@strawberry.type
class FireRiskResult:
    lat:           float
    lon:           float
    radius_km:     float
    risk_score:    float
    risk_label:    str          # Alto / Medio / Bajo
    hotspot_count: int


# ═══════════════════════════════════════════════════════════════════
# RESOLVERS
# ═══════════════════════════════════════════════════════════════════

# ── RAG ───────────────────────────────────────────────────────────

async def resolve_rag_context(
    query: str,
    limit: Annotated[int, strawberry.argument(description="Max passages")] = 3,
) -> list[RagPassage]:
    if not query.strip():
        return []
    vector = await embed_one(query)
    hits = await _store().search(query_vector=vector, limit=limit)
    return [
        RagPassage(
            text=h.get("payload", {}).get("text", ""),
            source=h.get("payload", {}).get("source", "unknown"),
            score=float(h.get("score", 0.0)),
        )
        for h in hits
    ]


# ── Advisor ───────────────────────────────────────────────────────

async def resolve_advisor(
    lat: float,
    lon: float,
    crop_id: str,
    season: Annotated[str, strawberry.argument(description="lluvias | secas | annual")] = "annual",
) -> AdvisorResult:
    from services.read_through_agri import load_advisor_payload_with_cache

    result, _ = await load_advisor_payload_with_cache(
        None,           # db=None (optional)
        lat=lat,
        lon=lon,
        crop_id=crop_id,
        season=season,
        hist_start_year=2019,
        hist_end_year=2023,
    )

    factors = [
        AdvisorFactor(
            label=f.get("label", ""),
            score=float(f.get("score", 0)),
            weight=float(f.get("weight", 0)),
            status=f.get("status", ""),
            description=f.get("description"),
        )
        for f in result.get("factors", [])
    ]

    return AdvisorResult(
        crop_id=crop_id,
        score=float(result.get("score", 0)),
        aptitude=result.get("aptitude", ""),
        recommendation_text=result.get("recommendation_text", ""),
        factors=factors,
        season=season,
        lat=lat,
        lon=lon,
    )


# ── Climate ───────────────────────────────────────────────────────

async def resolve_climate(
    lat: float,
    lon: float,
    from_year: Annotated[int, strawberry.argument(name="from")] = 1991,
    to_year:   Annotated[int, strawberry.argument(name="to")]   = 2020,
    scenario:  str = "SSP3-7.0",
) -> ClimateResult:
    from services.read_through_agri import load_historical_with_cache, load_projection_with_cache

    hist_df, _ = await load_historical_with_cache(
        None, lat=lat, lon=lon, start_year=from_year, end_year=to_year,
    )
    proj_df, _ = await load_projection_with_cache(
        None, lat=lat, lon=lon, scenario=scenario,
    )

    def df_to_months(df) -> list[ClimateMonth]:
        return [
            ClimateMonth(
                year=int(row["year"]),
                month=int(row["month"]),
                temp_c=row.get("temp_c"),
                precip_mm=row.get("precip_mm"),
                soil_moisture=row.get("soil_moisture"),
            )
            for row in df.to_dicts()
        ]

    return ClimateResult(
        lat=lat,
        lon=lon,
        scenario=scenario,
        historical=df_to_months(hist_df),
        projected=df_to_months(proj_df),
    )


# ── Geocode ───────────────────────────────────────────────────────

async def resolve_geocode(
    q:     str,
    count: Annotated[int, strawberry.argument(description="Max resultados")] = 5,
) -> GeocodeResult:
    from services import openmeteo

    raw = await openmeteo.geocode(q, count=count)
    places = [
        GeocodePlace(
            name=r.get("name", ""),
            lat=float(r.get("lat") or 0),
            lon=float(r.get("lon") or 0),
            country=r.get("country"),
            admin1=r.get("admin1"),
            elevation=r.get("elevation"),
            population=r.get("population"),
        )
        for r in raw
    ]
    return GeocodeResult(results=places)


# ── Soil ──────────────────────────────────────────────────────────

async def resolve_soil(lat: float, lon: float) -> SoilResult:
    return SoilResult(
        lat=lat,
        lon=lon,
        ph=None,
        texture=None,
        note="SoilGrids integration pending (CVA-13)",
    )


# ── Fires: hotspots ───────────────────────────────────────────────

async def resolve_hotspots(
    lat:       float,
    lon:       float,
    radius_km: Annotated[float, strawberry.argument(name="radiusKm")] = 100.0,
    days:      Annotated[int,   strawberry.argument(description="1-5")] = 5,
    source:    str = "VIIRS_SNPP_NRT",
) -> HotspotsResult:
    from services.read_through_firms import hotspots_with_cache

    hotspots_raw, _ = await hotspots_with_cache(
        None, lat=lat, lon=lon, radius_km=radius_km, days=days, source=source,
    )

    hotspots = [
        Hotspot(
            lat=float(h.get("latitude", h.get("lat", 0))),
            lon=float(h.get("longitude", h.get("lon", 0))),
            brightness=h.get("bright_ti4") or h.get("brightness"),
            frp=h.get("frp"),
            acq_date=h.get("acq_date"),
            acq_time=str(h.get("acq_time", "")) if h.get("acq_time") is not None else None,
            satellite=h.get("satellite"),
            confidence=str(h.get("confidence", "")) if h.get("confidence") is not None else None,
            source=h.get("source_name"),
        )
        for h in hotspots_raw
    ]

    return HotspotsResult(
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        days=days,
        count=len(hotspots),
        hotspots=hotspots,
    )


# ── Fires: risk ───────────────────────────────────────────────────

async def resolve_fire_risk(
    lat:       float,
    lon:       float,
    radius_km: Annotated[float, strawberry.argument(name="radiusKm")] = 100.0,
) -> FireRiskResult:
    from services import firms
    from services.read_through_firms import hotspots_with_cache

    hotspots_raw, _ = await hotspots_with_cache(
        None, lat=lat, lon=lon, radius_km=radius_km, days=5, source="VIIRS_SNPP_NRT",
    )
    risk_score = firms.compute_fire_risk_score(hotspots_raw, radius_km=radius_km)
    risk_label = "Alto" if risk_score >= 60 else ("Medio" if risk_score >= 30 else "Bajo")

    return FireRiskResult(
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        risk_score=float(risk_score),
        risk_label=risk_label,
        hotspot_count=len(hotspots_raw),
    )


# ── Alerts ───────────────────────────────────────────────────────

@strawberry.type
class Alert:
    title:        str
    severity:     str          # alta | media | baja
    message:      str
    generated_at: str
    region:       Optional[str] = None
    alert_type:   Optional[str] = None  # fire | heat | drought | rain


@strawberry.type
class AlertsResult:
    alerts:       list[Alert]
    generated_at: str
    zones_checked: int


# Zonas agricolas clave de LATAM ex-Brasil que monitoreamos
_ALERT_ZONES = [
    {"name": "Orinoquía (Colombia/Venezuela)", "lat":  6.5,  "lon": -68.0},
    {"name": "Chaco (Paraguay/Argentina)",     "lat": -22.0, "lon": -60.0},
    {"name": "Bajío (México)",                 "lat":  20.5, "lon": -101.0},
    {"name": "Centroamérica",                  "lat":  14.0, "lon": -87.0},
    {"name": "Llanos Orientales (Colombia)",   "lat":   4.0, "lon": -72.5},
    {"name": "Pampa Húmeda (Argentina)",       "lat": -34.0, "lon": -61.0},
    {"name": "Valle Central (Chile)",          "lat": -34.5, "lon": -71.0},
    {"name": "Sierra Peruana",                 "lat": -13.0, "lon": -75.0},
]


async def _check_zone_alerts(zone: dict, client) -> list[Alert]:
    """Evalúa hotspots FIRMS + temperatura actual para una zona LATAM."""
    from services import firms
    from services.read_through_firms import hotspots_with_cache
    import httpx

    alerts: list[Alert] = []
    lat, lon, name = zone["lat"], zone["lon"], zone["name"]
    now = datetime.now(timezone.utc).isoformat()

    # 1. Incendios activos (FIRMS, radio 150km, últimos 2 días)
    try:
        hotspots, _ = await hotspots_with_cache(
            None, lat=lat, lon=lon, radius_km=150, days=2, source="VIIRS_SNPP_NRT",
        )
        count = len(hotspots)
        if count >= 10:
            alerts.append(Alert(
                title="Incendios activos",
                severity="alta",
                message=f"{count} focos de calor detectados en {name} (últimas 48h, radio 150km). Riesgo crítico para cultivos y suelo.",
                generated_at=now,
                region=name,
                alert_type="fire",
            ))
        elif count >= 3:
            alerts.append(Alert(
                title="Focos de calor",
                severity="media",
                message=f"{count} focos detectados en {name}. Monitorea calidad del aire y riesgo de propagación.",
                generated_at=now,
                region=name,
                alert_type="fire",
            ))
    except Exception:
        pass

    # 2. Temperatura actual (Open-Meteo forecast, variable temperature_2m_max)
    try:
        params = {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,precipitation_sum",
            "forecast_days": 3,
            "timezone": "auto",
        }
        async with httpx.AsyncClient() as c:
            resp = await c.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=10)
            if resp.status_code == 200:
                daily = resp.json().get("daily", {})
                temps = daily.get("temperature_2m_max", [])
                precips = daily.get("precipitation_sum", [])

                if temps and max(t for t in temps if t is not None) >= 38:
                    peak = max(t for t in temps if t is not None)
                    alerts.append(Alert(
                        title="Ola de calor",
                        severity="alta",
                        message=f"Temperatura máxima de {peak:.1f}°C prevista en {name} (próximas 72h). Adelanta riego y protege cultivos sensibles.",
                        generated_at=now,
                        region=name,
                        alert_type="heat",
                    ))
                elif temps and max(t for t in temps if t is not None) >= 34:
                    peak = max(t for t in temps if t is not None)
                    alerts.append(Alert(
                        title="Calor extremo",
                        severity="media",
                        message=f"Pico de {peak:.1f}°C esperado en {name}. Vigila estrés térmico en floración de maíz, café y tomate.",
                        generated_at=now,
                        region=name,
                        alert_type="heat",
                    ))

                if precips:
                    total = sum(p for p in precips if p is not None)
                    if total == 0:
                        alerts.append(Alert(
                            title="Déficit hídrico",
                            severity="media",
                            message=f"Sin precipitación prevista en {name} por 3 días. Activa riego si el cultivo está en etapa crítica.",
                            generated_at=now,
                            region=name,
                            alert_type="drought",
                        ))
                    elif total >= 50:
                        alerts.append(Alert(
                            title="Lluvias intensas",
                            severity="media",
                            message=f"{total:.0f} mm acumulados previstos en {name} (72h). Riesgo de encharcamiento y enfermedades fungicas.",
                            generated_at=now,
                            region=name,
                            alert_type="rain",
                        ))
    except Exception:
        pass

    return alerts


# ── Compare ───────────────────────────────────────────────────────

@strawberry.type
class CompareResult:
    lat:      float
    lon:      float
    season:   str
    crop_a:   AdvisorResult
    crop_b:   AdvisorResult
    winner:   Optional[str] = None   # crop_id del cultivo con mayor score, None si empate


async def resolve_compare(
    lat:      float,
    lon:      float,
    crop_id_a: str,
    crop_id_b: str,
    season:   Annotated[str, strawberry.argument(description="lluvias | secas | annual")] = "annual",
) -> CompareResult:
    # Corre ambos advisors en paralelo
    result_a, result_b = await asyncio.gather(
        resolve_advisor(lat=lat, lon=lon, crop_id=crop_id_a, season=season),
        resolve_advisor(lat=lat, lon=lon, crop_id=crop_id_b, season=season),
    )

    if result_a.score > result_b.score:
        winner = crop_id_a
    elif result_b.score > result_a.score:
        winner = crop_id_b
    else:
        winner = None

    return CompareResult(
        lat=lat,
        lon=lon,
        season=season,
        crop_a=result_a,
        crop_b=result_b,
        winner=winner,
    )


async def resolve_alerts() -> AlertsResult:
    now = datetime.now(timezone.utc).isoformat()

    # Consulta todas las zonas en paralelo
    import httpx
    tasks = [_check_zone_alerts(zone, None) for zone in _ALERT_ZONES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_alerts: list[Alert] = []
    for r in results:
        if isinstance(r, list):
            all_alerts.extend(r)

    # Ordenar: alta primero, luego media, luego baja
    severity_order = {"alta": 0, "media": 1, "baja": 2}
    all_alerts.sort(key=lambda a: severity_order.get(a.severity, 3))

    return AlertsResult(
        alerts=all_alerts,
        generated_at=now,
        zones_checked=len(_ALERT_ZONES),
    )


# ═══════════════════════════════════════════════════════════════════
# SCHEMA
# ═══════════════════════════════════════════════════════════════════

@strawberry.type
class Query:
    rag_context: list[RagPassage] = strawberry.field(
        resolver=resolve_rag_context,
        description="RAG: passages agroclimaticos relevantes via ClimateBERT + Qdrant.",
    )
    advisor: AdvisorResult = strawberry.field(
        resolver=resolve_advisor,
        description="UC-1: score de aptitud agricola para un cultivo en una ubicacion.",
    )
    climate: ClimateResult = strawberry.field(
        resolver=resolve_climate,
        description="UC-3: serie climatica historica + proyeccion CMIP6.",
    )
    geocode: GeocodeResult = strawberry.field(
        resolver=resolve_geocode,
        description="UC-9: busqueda de lugares LATAM via Open-Meteo Geocoding.",
    )
    soil: SoilResult = strawberry.field(
        resolver=resolve_soil,
        description="Propiedades de suelo por lat/lon (stub — SoilGrids CVA-13).",
    )
    hotspots: HotspotsResult = strawberry.field(
        resolver=resolve_hotspots,
        description="UC-5: hotspots NASA FIRMS activos cerca de una ubicacion.",
    )
    fire_risk: FireRiskResult = strawberry.field(
        resolver=resolve_fire_risk,
        description="UC-5: score de riesgo de incendio (0-100) para una ubicacion.",
    )
    compare: CompareResult = strawberry.field(
        resolver=resolve_compare,
        description="Compara la aptitud de dos cultivos en la misma ubicacion y temporada.",
    )
    alerts: AlertsResult = strawberry.field(
        resolver=resolve_alerts,
        description="Alertas agroclimaticas reales para 8 zonas clave de LATAM ex-Brasil. Fuentes: NASA FIRMS + Open-Meteo.",
    )


schema = strawberry.Schema(query=Query)
graphql_router = GraphQLRouter(schema, graphql_ide="graphiql")
