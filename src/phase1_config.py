"""Phase 1: Build ZIP and city allowlists per MSA.

ZIP allowlist: HUD USPS Crosswalk type=7 (COUNTY -> ZIP), one query per county FIPS.
  Endpoint: https://www.huduser.gov/hudapi/public/usps?type=7&query=<5-digit-county-FIPS>
  Returns all ZIPs that overlap that county (with res/bus/oth/tot ratios).

City allowlist: derived from msa_config.json `notable_cities` (curated, OMB principal +
  top places). HUD does not expose city names; we use this curated list to feed
  NIH RePORTER org_cities and SBIR.gov city= filters.

Outputs:
  config/zip_allowlist.csv     (msa, zip, county_fips, state, res_ratio)
  config/city_allowlist.csv    (msa, city_normalized, state)
  data/checkpoints/phase_1.manifest.json

Rate limit: 2 req/sec to HUD (conservative).
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CONFIG_DIR, RateLimiter, http_get, load_msa_config, manifest_exists, write_manifest,
)

HUD_BASE = "https://www.huduser.gov/hudapi/public/usps"
PHASE = 1


def hud_county_to_zip(county_fips: str, token: str, limiter: RateLimiter) -> list[dict]:
    """type=7 COUNTY-ZIP. Returns list of {zip, res_ratio, ...}."""
    limiter.wait()
    r = http_get(
        HUD_BASE,
        source="hud_usps",
        params={"type": 7, "query": county_fips},
        headers={"Authorization": f"Bearer {token}"},
    )
    payload = r.json()
    return payload.get("data", {}).get("results", []) or []


def build_allowlists(force: bool = False) -> None:
    if manifest_exists(PHASE) and not force:
        print(f"Phase {PHASE} already complete. Use --force to rerun.")
        return

    token = os.getenv("HUD_API_TOKEN")
    if not token:
        print("ERROR: set HUD_API_TOKEN (free at huduser.gov).", file=sys.stderr)
        sys.exit(1)

    cfg = load_msa_config()
    limiter = RateLimiter(per_sec=2)

    zip_rows: list[dict] = []
    city_rows: list[dict] = []
    msa_zip_counts: dict[str, int] = {}
    msa_city_counts: dict[str, int] = {}

    for msa_key, msa in cfg["msas"].items():
        print(f"\n=== {msa_key} ({msa['cbsa_code']}) ===", flush=True)
        seen_zips: dict[str, dict] = {}
        seen_cities: dict[tuple[str, str], int] = {}  # (city, state) -> zip_count

        for county_fips in msa["county_fips"]:
            county_name = msa["county_names"].get(county_fips, "?")
            try:
                results = hud_county_to_zip(county_fips, token, limiter)
            except Exception as e:
                print(f"  FAIL county={county_fips} ({county_name}): {e}", file=sys.stderr, flush=True)
                continue

            kept = 0
            for row in results:
                # type=7 returns ZIP in 'geoid'; 'county' echoes the queried county FIPS
                zip5 = row.get("geoid")
                res_ratio = float(row.get("res_ratio") or 0)
                city = (row.get("city") or "").strip().lower()
                state = (row.get("state") or "").strip().upper()
                if not zip5 or res_ratio <= 0:
                    continue
                cur = seen_zips.get(zip5)
                if cur is None or res_ratio > cur["res_ratio"]:
                    seen_zips[zip5] = {
                        "msa": msa_key,
                        "zip": zip5,
                        "county_fips": county_fips,
                        "state": state,
                        "res_ratio": res_ratio,
                    }
                    if cur is None:
                        kept += 1
                if city and state:
                    key = (city, state)
                    seen_cities[key] = seen_cities.get(key, 0) + 1
            print(f"  county {county_fips} ({county_name}): +{kept} ZIPs (total {len(seen_zips)})",
                  flush=True)

        msa_zip_counts[msa_key] = len(seen_zips)
        msa_city_counts[msa_key] = len(seen_cities)
        zip_rows.extend(seen_zips.values())

        # Merge in curated principal+notable cities so we never miss an OMB city
        curated = {(c.strip().lower(), s) for c in msa.get("notable_cities", [])
                   for s in msa["state_codes"]}
        for (city, state) in curated:
            seen_cities.setdefault((city, state), 0)

        for (city, state), zcount in sorted(seen_cities.items()):
            city_rows.append({
                "msa": msa_key,
                "city_normalized": city,
                "state": state,
                "zip_count": zcount,
            })

        print(f"  → {len(seen_zips)} ZIPs, {len(seen_cities)} cities for {msa_key}", flush=True)

    # Write outputs
    zip_path = CONFIG_DIR / "zip_allowlist.csv"
    city_path = CONFIG_DIR / "city_allowlist.csv"
    with zip_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["msa", "zip", "county_fips", "state", "res_ratio"])
        w.writeheader()
        w.writerows(zip_rows)
    with city_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["msa", "city_normalized", "state", "zip_count"])
        w.writeheader()
        w.writerows(city_rows)

    write_manifest(PHASE, {
        "zip_count": len(zip_rows),
        "zip_count_by_msa": msa_zip_counts,
        "city_count": len(city_rows),
        "city_count_by_msa": msa_city_counts,
        "zip_allowlist_path": str(zip_path.relative_to(CONFIG_DIR.parent)),
        "city_allowlist_path": str(city_path.relative_to(CONFIG_DIR.parent)),
    })
    print(f"\nDone. {len(zip_rows)} ZIP rows, {len(city_rows)} city rows.")


if __name__ == "__main__":
    build_allowlists(force="--force" in sys.argv)
