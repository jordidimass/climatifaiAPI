#!/usr/bin/env python3
"""
Extrae capitales LATAM (sin BR) desde dumps GeoNames.

**Modo por defecto** (con ``--download-extract``): descarga un ``{CC}.zip`` por país de la
whitelist (~cientos de KB–pocos MB cada uno), mucho más rápido que ``allCountries``.

Opcional ``--full-dump`` descarga ``allCountries.zip`` (muy pesado) por streaming con progreso.

Ejemplo rápido::

    PYTHONPATH=. .venv/bin/python scripts/build_locations_from_geonames.py \\
      --download-extract \\
      --output data/catalogs/geonames/locations.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MANIFEST_REL = Path("data/catalogs/geonames/manifest.json")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_manifest(root: Path) -> dict:
    mf = root / MANIFEST_REL
    if not mf.exists():
        return {}
    return json.loads(mf.read_text(encoding="utf-8"))


def maybe_verify_zip_sha256(zip_path: Path, expected: str | None) -> None:
    if not expected:
        return
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if digest.lower() != expected.lower():
        raise SystemExit(f"SHA256 del zip distinto del manifest ({digest} vs {expected})")


GEO_NAMES_DUMP = "https://download.geonames.org/export/dump"


def fetch_url_to_file(
    url: str,
    dest: Path,
    *,
    chunk_size: int = 1 << 20,
    timeout_s: int = 120,
    label: str = "",
) -> None:
    """Descarga HTTP(s) en bloques (evita cargar ~400MB en RAM) e imprime avance aproximado."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "climatifai-geonames-fetch/1.0"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    total: int | None = None
    try:
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 — URL construida desde ISO o manifest
            total = resp.headers.get("Content-Length")
            total_n = int(total) if total and total.isdigit() else None
            n = 0
            last_print = 0
            print_step = 5 * 1024 * 1024
            with tmp.open("wb") as out:
                while True:
                    block = resp.read(chunk_size)
                    if not block:
                        break
                    out.write(block)
                    n += len(block)
                    if n - last_print >= print_step or (total_n is not None and n >= total_n):
                        if total_n:
                            print(f"  {label}{n / 1e6:.1f} / {total_n / 1e6:.1f} MB", flush=True)
                        else:
                            print(f"  {label}{n / 1e6:.1f} MB", flush=True)
                        last_print = n
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Fallo descargando {url}: {exc}") from exc

    tmp.replace(dest)
    print(f"{'  ' + label if label else ''}listo ({dest.stat().st_size / 1e6:.1f} MB)     ", flush=True)


def extract_all_countries_txt(zip_path: Path, tmpdir: Path) -> Path:
    with zipfile.ZipFile(zip_path) as zf:
        candidates = [n for n in zf.namelist() if n.endswith("allCountries.txt")]
        if not candidates:
            raise SystemExit("allCountries.txt no encontrado dentro del ZIP")
        name = candidates[0]
        zf.extract(name, tmpdir)
        extracted = tmpdir / name
        return extracted if extracted.exists() else tmpdir / Path(name).name


def extract_country_txt(zip_path: Path, country_code: str, tmpdir: Path) -> Path | None:
    cc = country_code.strip().upper()
    expected = f"{cc}.txt"
    with zipfile.ZipFile(zip_path) as zf:
        member = next((n for n in zf.namelist() if Path(n).name.upper() == expected.upper()), None)
        if member is None:
            return None
        zf.extract(member, tmpdir)
        p = tmpdir / member
        return p if p.exists() else tmpdir / Path(member).name


def iso_allowed(
    *,
    iso: frozenset[str],
    cc: str,
    extra_allow: frozenset[str],
) -> bool:
    if cc.upper() == "BR":
        return False
    return cc.upper() in iso or cc.upper() in extra_allow


def stream_filter_capitals(
    txt_path: Path,
    *,
    latam_codes: frozenset[str],
    extra_allow: frozenset[str],
    include_admin: bool,
    country_filter: set[str],
) -> list[dict[str, object]]:
    want_codes: set[str] = {"PPLC"}
    if include_admin:
        want_codes.add("PPLA")

    out: list[dict[str, object]] = []
    with txt_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 19:
                continue
            gid_s, name, asciiname, *_rest = parts[0], parts[1], parts[2]
            lat_s, lon_s = parts[4], parts[5]
            fc, fcode, cc = parts[6], parts[7], parts[8]
            admin1 = parts[10] if len(parts) > 10 else ""
            if fc != "P" or fcode not in want_codes:
                continue
            if cc.upper() == "BR":
                continue
            if not iso_allowed(iso=latam_codes, cc=cc, extra_allow=extra_allow):
                continue
            if country_filter and cc.upper() not in country_filter:
                continue
            gid = int(gid_s)
            lat_r, lon_r = round(float(lat_s), 4), round(float(lon_s), 4)
            out.append(
                {
                    "geonames_id": gid,
                    "country_iso": cc.upper(),
                    "admin1_code": admin1 or "",
                    "feature_code": fcode,
                    "kind": "national_capital" if fcode == "PPLC" else "admin1_seat",
                    "name": name,
                    "ascii_name": asciiname,
                    "lat": lat_r,
                    "lon": lon_r,
                },
            )

    dedup_ids: dict[int, dict[str, object]] = {}
    for row in out:
        dedup_ids[int(row["geonames_id"])] = row
    return sorted(dedup_ids.values(), key=lambda r: (r["country_iso"], r["name"]))


def ordered_country_codes_to_fetch(
    *,
    latam_codes: frozenset[str],
    extra_allow: frozenset[str],
    country_filter: set[str],
) -> list[str]:
    base = set(latam_codes) | set(extra_allow)
    base.discard("BR")
    if country_filter:
        base &= country_filter
    return sorted(cc.upper() for cc in base if cc.upper() != "BR")


def collect_rows_per_country(
    tmpdir: Path,
    cache_dir: Path,
    *,
    codes: list[str],
    latam_codes: frozenset[str],
    extra_allow: frozenset[str],
    include_admin: bool,
    country_filter: set[str],
    full_dump: bool,
    zip_url_override: str | None,
    manifest_sha_full_dump: str | None,
    local_zip: Path | None,
) -> list[dict[str, object]]:
    if local_zip is not None:
        name_l = local_zip.stem.lower()
        if full_dump or name_l == "allcountries":
            maybe_verify_zip_sha256(local_zip, manifest_sha_full_dump)
            txt_path = extract_all_countries_txt(local_zip, tmpdir)
            return stream_filter_capitals(
                txt_path,
                latam_codes=latam_codes,
                extra_allow=extra_allow,
                include_admin=include_admin,
                country_filter=country_filter,
            )
        cc = local_zip.stem.upper()
        txt_path = extract_country_txt(local_zip, cc, tmpdir)
        if txt_path is None or not txt_path.exists():
            raise SystemExit(f"No se pudo extraer {cc}.txt de {local_zip}")
        return stream_filter_capitals(
            txt_path,
            latam_codes=latam_codes,
            extra_allow=extra_allow,
            include_admin=include_admin,
            country_filter=country_filter,
        )

    if full_dump:
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest_zip = cache_dir / "allCountries.zip"
        url = zip_url_override or f"{GEO_NAMES_DUMP}/allCountries.zip"
        print(f"Full dump (lento, ~400MB+) → {dest_zip}", flush=True)
        fetch_url_to_file(url, dest_zip, timeout_s=3600, label="allCountries ")
        maybe_verify_zip_sha256(dest_zip, manifest_sha_full_dump)
        txt_path = extract_all_countries_txt(dest_zip, tmpdir)
        return stream_filter_capitals(
            txt_path,
            latam_codes=latam_codes,
            extra_allow=extra_allow,
            include_admin=include_admin,
            country_filter=country_filter,
        )

    if not codes:
        raise SystemExit("No hay códigos de país que descargar (revisa --countries / whitelist).")

    # ZIP por país (rápido para PPLC/PPLA LATAM)
    all_rows: list[dict[str, object]] = []
    for i, cc in enumerate(codes, start=1):
        url = f"{GEO_NAMES_DUMP}/{cc}.zip"
        dest_zip = cache_dir / f"{cc}.zip"
        print(f"[{i}/{len(codes)}] descarga {cc}.zip …", flush=True)
        fetch_url_to_file(url, dest_zip, timeout_s=300, label=f"{cc} ")
        txt_path = extract_country_txt(dest_zip, cc, tmpdir)
        if txt_path is None or not txt_path.exists():
            print(f"  Aviso: no se extrajo {cc}.txt de {dest_zip.name}, se omite país.", flush=True)
            continue
        all_rows.extend(
            stream_filter_capitals(
                txt_path,
                latam_codes=latam_codes,
                extra_allow=extra_allow,
                include_admin=include_admin,
                country_filter=country_filter,
            ),
        )

    dedup: dict[int, dict[str, object]] = {}
    for row in all_rows:
        dedup[int(row["geonames_id"])] = row
    return sorted(dedup.values(), key=lambda r: (r["country_iso"], r["name"]))


def main() -> None:
    root = repo_root()
    # Import here to avoid circular tooling when script runs standalone
    from services.locations_catalog import LATAM_ISO_WITHOUT_BR

    manifest = load_manifest(root)
    default_all_countries_url = manifest.get("base_url", f"{GEO_NAMES_DUMP}/allCountries.zip")

    parser = argparse.ArgumentParser(
        description="GeoNames → CSV locations LATAM sin BR (descarga por país por defecto)",
    )
    parser.add_argument(
        "--download-extract",
        action="store_true",
        help="Descarga ZIPs GeoNames (por país, o allCountries con --full-dump), genera CSV",
    )
    parser.add_argument(
        "--full-dump",
        action="store_true",
        help="Usar allCountries.zip (~400MB+, lento). Por defecto descarga solo los {CC}.zip de la whitelist",
    )
    parser.add_argument(
        "--zip-url",
        default=default_all_countries_url,
        help="URL para allCountries.zip (solo tiene efecto con --full-dump o zip local allCountries)",
    )
    parser.add_argument(
        "--zip-path",
        default=None,
        help="ZIP local: allCountries.zip o p.ej. MX.zip (sin descargar)",
    )
    parser.add_argument("--output", default="data/catalogs/geonames/locations.csv")
    parser.add_argument(
        "--include-admin-capitals",
        action="store_true",
        help="Incluye PPLA además de PPLC (más localidades capitales subdivisión nivel 1)",
    )
    parser.add_argument(
        "--countries",
        default="",
        help="Lista opcional CSV de ISO dos letras permitidos adicionales dentro del extractor (filtro dentro de LATAM+extra)",
    )
    parser.add_argument(
        "--extra-country",
        default="",
        help="Comma-separated ISO extras beyond LATAM_WITHOUT_BR whitelist (ej. territorios franceses GF)",
    )
    args = parser.parse_args()

    country_filter_upper = {c.strip().upper() for c in args.countries.split(",") if c.strip()}
    extra_allow = frozenset({c.strip().upper() for c in args.extra_country.split(",") if c.strip()})

    csv_path = (root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_version = datetime.now(UTC).strftime("geonames-csv-%Y%m%d-%H%MUTC")

    if not args.download_extract and not args.zip_path:
        print("Indique --download-extract (red) o --zip-path a un .zip GeoNames local.", flush=True)
        raise SystemExit(2)

    codes = ordered_country_codes_to_fetch(
        latam_codes=LATAM_ISO_WITHOUT_BR,
        extra_allow=extra_allow,
        country_filter=country_filter_upper,
    )
    cache_dir = (root / "data/catalogs/geonames/.cache").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory() as tmpdir:
        tmpdir_p = Path(tmpdir)
        zip_local = Path(args.zip_path).resolve() if args.zip_path else None
        if zip_local is not None and not zip_local.exists():
            raise SystemExit(f"No existe ZIP: {zip_local}")
        manifest_sha = manifest.get("verified_sha256")

        rows = collect_rows_per_country(
            tmpdir_p,
            cache_dir,
            codes=codes,
            latam_codes=LATAM_ISO_WITHOUT_BR,
            extra_allow=extra_allow,
            include_admin=args.include_admin_capitals,
            country_filter=country_filter_upper if country_filter_upper else set(),
            full_dump=args.full_dump,
            zip_url_override=args.zip_url,
            manifest_sha_full_dump=manifest_sha if args.full_dump else None,
            local_zip=zip_local,
        )
        fieldnames = [
            "geonames_id",
            "country_iso",
            "admin1_code",
            "feature_code",
            "kind",
            "name",
            "ascii_name",
            "lat",
            "lon",
            "catalog_version",
        ]

        tmp_out = csv_path.with_suffix(csv_path.suffix + ".partial")
        with tmp_out.open("w", newline="", encoding="utf-8") as out_f:
            w = csv.DictWriter(out_f, fieldnames=fieldnames)
            w.writeheader()
            for row in rows:
                row_copy = dict(row)
                row_copy["catalog_version"] = catalog_version
                w.writerow(row_copy)

        tmp_out.replace(csv_path)

    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest() if csv_path.exists() else ""
    print(
        f"Hecho: {csv_path.relative_to(root) if csv_path.is_relative_to(root) else csv_path}"
        f" — locs={len(rows)} sha256={digest[:16]}…",
        flush=True,
    )


if __name__ == "__main__":
    main()
