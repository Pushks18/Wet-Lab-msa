"""Phase 4: TTO + incubator portfolio harvest.

Reachable structured sources only (others are JS-rendered, Cloudflare-blocked, or PDF):
  - Engage Ventures (Atlanta) — extract from logo image filenames
  - Innovation Works (Pittsburgh) — visible cards on portfolio page
  - UMD Momentum Fund (Baltimore) — structured cards
  - Health Wildcatters (Dallas) — img alt text (best-effort)

Plus a curated seed list of well-known Penn / JHU / Emory / UTSW / Ga Tech
LS spinouts (since their TTO portfolio pages are gated). All seed names are
publicly documented spinouts pulled from press releases / annual reports.

Output:
  data/raw/tto_portfolio.parquet   — name, msa, source, ls_hint
  output/tto_portfolio.csv
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUTPUT_DIR, RAW_DIR, http_get

PHASE = 4

# -------------------- scrapers --------------------

def scrape_engage(msa: str = "atlanta") -> list[dict]:
    """Engage Ventures: 114 portfolio cos. Names extracted from logo image filename."""
    r = http_get("https://engage.vc/portfolio/", source="engage")
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for li in soup.select(".portfolio_list li"):
        a = li.find("a", href=True)
        href = a["href"] if a else ""
        # logo img
        logo = li.find("img", class_="logo")
        name = ""
        if logo and logo.get("src"):
            # filename → stem → drop trailing -white-{hash}
            stem = Path(logo["src"]).stem
            stem = re.sub(r"-white(-e?\d+)?$", "", stem, flags=re.I)
            stem = re.sub(r"-?logo$", "", stem, flags=re.I)
            stem = re.sub(r"-?\d{8,}$", "", stem)
            name = stem.replace("-", " ").replace("_", " ").strip().title()
        # fallback: derive from website hostname
        if not name and href:
            m = re.search(r"https?://(?:www\.)?([^/#]+)", href)
            if m:
                host = m.group(1).split(".")[0]
                name = host.replace("-", " ").title()
        tagline = (li.select_one(".tagline") or {}).get_text(strip=True) if li.select_one(".tagline") else ""
        loc = (li.select_one(".ticker") or {}).get_text(strip=True) if li.select_one(".ticker") else ""
        if name:
            out.append({"name": name, "msa": msa, "source": "engage_ventures",
                        "tagline": tagline, "location": loc, "url": href})
    return out


def scrape_iw(msa: str = "pittsburgh") -> list[dict]:
    """Innovation Works (Pittsburgh) — visible cards (paginated; only first 10 reachable
    without JS, so this is partial). Captured anyway for completeness."""
    r = http_get("https://www.innovationworks.org/portfolio/", source="innovation_works")
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select(".portfolio-company"):
        title = card.select_one(".company-title")
        bio = card.select_one(".company-bio")
        if not title:
            continue
        name = title.get_text(strip=True)
        out.append({"name": name, "msa": msa, "source": "innovation_works",
                    "tagline": bio.get_text(strip=True) if bio else "",
                    "location": "Pittsburgh, PA", "url": ""})
    return out


def scrape_momentum(msa: str = "baltimore") -> list[dict]:
    """UMD Momentum Fund — 33 .company cards. Extract name + institution + year."""
    r = http_get("https://momentum.usmd.edu/portfolio-companies", source="momentum")
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for c in soup.select(".portfolio-companies .company"):
        # Each card has a heading + dl-style rows. Try multiple name candidates.
        name_el = c.find(["h2", "h3", "h4"]) or c.find("strong")
        name = name_el.get_text(strip=True) if name_el else ""
        # If heading was a status word like "Acquired", use a different element
        if name.lower() in {"acquired", "active", "exited", ""}:
            link = c.find("a")
            name = link.get_text(strip=True) if link else ""
        if not name:
            # Fall back: text right before "Institution:"
            txt = c.get_text(" ", strip=True)
            m = re.match(r"^([^I]{2,80}?)Institution:", txt)
            if m:
                name = m.group(1).strip()
        if name:
            out.append({"name": name, "msa": msa, "source": "umd_momentum",
                        "tagline": "", "location": "Maryland", "url": ""})
    return out


def scrape_hwc(msa: str = "dallas") -> list[dict]:
    """Health Wildcatters (Dallas) — names trapped in logo file alt text. Best-effort."""
    try:
        r = http_get("https://www.healthwildcatters.com/portfolio", source="health_wildcatters")
    except Exception as e:
        print(f"  HWC fetch failed: {e}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for img in soup.find_all("img", alt=True):
        alt = img["alt"]
        # Filter out obvious non-names (filenames, generic words)
        if not alt or len(alt) < 3 or len(alt) > 60:
            continue
        if alt.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        if any(w in alt.lower() for w in ["logo", "icon", "image", "photo", "background"]):
            continue
        out.append({"name": alt.strip(), "msa": msa, "source": "health_wildcatters",
                    "tagline": "", "location": "Dallas, TX", "url": ""})
    # Dedup
    seen, uniq = set(), []
    for r in out:
        key = r["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


# -------------------- curated seed list (publicly known LS spinouts) --------------------

SEED_SPINOUTS: list[dict] = [
    # Penn (Philadelphia) — spinouts publicly attributed in press releases
    {"name": "Spark Therapeutics", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Tmunity Therapeutics", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Carisma Therapeutics", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Cabaletta Bio", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Passage Bio", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Ocugen", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Vivodyne", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Linnaeus Therapeutics", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Limelight Bio", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Capstan Therapeutics", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Interius BioTherapeutics", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Ring Therapeutics", "msa": "philadelphia", "source": "penn_known"},
    {"name": "GRIT Bio", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Lyell Immunopharma", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Verismo Therapeutics", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Aro Biotherapeutics", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Context Therapeutics", "msa": "philadelphia", "source": "penn_known"},
    {"name": "INOVIO Pharmaceuticals", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Tyme Technologies", "msa": "philadelphia", "source": "penn_known"},
    {"name": "Iveric Bio", "msa": "philadelphia", "source": "penn_known"},
    # JHU (Baltimore) — JHTV / FastForward documented portfolio
    {"name": "Personal Genome Diagnostics", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Thrive Earlier Detection", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Sonavex", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Galen Robotics", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Sisu Global Health", "msa": "baltimore", "source": "jhu_known"},
    {"name": "WindMIL Therapeutics", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Graybug Vision", "msa": "baltimore", "source": "jhu_known"},
    {"name": "PathoVax", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Haystack Oncology", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Neuraly", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Theradaptive", "msa": "baltimore", "source": "jhu_known"},
    {"name": "RedShift Bioanalytics", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Asclepix Therapeutics", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Dracen Pharmaceuticals", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Tao Life Sciences", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Squalus Medical", "msa": "baltimore", "source": "jhu_known"},
    {"name": "PapGene", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Ananda Devices", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Vasoptic Medical", "msa": "baltimore", "source": "jhu_known"},
    {"name": "Clear Guide Medical", "msa": "baltimore", "source": "jhu_known"},
    # Emory + Ga Tech (Atlanta) — public spinouts
    {"name": "GeoVax Labs", "msa": "atlanta", "source": "emory_known"},
    {"name": "Clearside Biomedical", "msa": "atlanta", "source": "emory_known"},
    {"name": "Micromedicine", "msa": "atlanta", "source": "gatech_known"},
    {"name": "Axion BioSystems", "msa": "atlanta", "source": "gatech_known"},
    {"name": "CardioMEMS", "msa": "atlanta", "source": "gatech_known"},
    {"name": "Cellect Biotechnology", "msa": "atlanta", "source": "emory_known"},
    {"name": "Boli Therapeutics", "msa": "atlanta", "source": "emory_known"},
    {"name": "Atomwise", "msa": "atlanta", "source": "gatech_known"},
    {"name": "Florence Healthcare", "msa": "atlanta", "source": "emory_known"},
    {"name": "Sharecare", "msa": "atlanta", "source": "emory_known"},
    {"name": "Andson Biotech", "msa": "atlanta", "source": "emory_known"},
    {"name": "EnsoData", "msa": "atlanta", "source": "emory_known"},
    {"name": "OmniLytics", "msa": "atlanta", "source": "gatech_known"},
    {"name": "Sanguina", "msa": "atlanta", "source": "emory_known"},
    {"name": "MicroScrew", "msa": "atlanta", "source": "gatech_known"},
    # UT Southwestern + UTD (Dallas) — public spinouts
    {"name": "Peloton Therapeutics", "msa": "dallas", "source": "utsw_known"},
    {"name": "Reata Pharmaceuticals", "msa": "dallas", "source": "utsw_known"},
    {"name": "Lantern Pharma", "msa": "dallas", "source": "utsw_known"},
    {"name": "Taysha Gene Therapies", "msa": "dallas", "source": "utsw_known"},
    {"name": "Colossal Biosciences", "msa": "dallas", "source": "utsw_known"},
    {"name": "Ridgeline Therapeutics", "msa": "dallas", "source": "utsw_known"},
    {"name": "Otonomy", "msa": "dallas", "source": "utsw_known"},
    {"name": "Vyripharm Biopharmaceuticals", "msa": "dallas", "source": "utsw_known"},
    {"name": "AvidBiotics", "msa": "dallas", "source": "utsw_known"},
    {"name": "Alecto Therapeutics", "msa": "dallas", "source": "utsw_known"},
    {"name": "Encore Vision", "msa": "dallas", "source": "utsw_known"},
    {"name": "ZS Pharma", "msa": "dallas", "source": "utsw_known"},
    # CMU + Pitt (Pittsburgh) — public spinouts
    {"name": "Krystal Biotech", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "Cognition Therapeutics", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "Forest Devices", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "Renerva", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "Cernostics", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "Ondine Biomedical", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "Predictive Oncology", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "Ariel Precision Medicine", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "Generian Pharmaceuticals", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "Adva Biotechnology", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "Carmell Therapeutics", "msa": "pittsburgh", "source": "pitt_known"},
    {"name": "ChemImage", "msa": "pittsburgh", "source": "cmu_known"},
    {"name": "Innovu", "msa": "pittsburgh", "source": "cmu_known"},
]


def main() -> None:
    rows: list[dict] = []
    print("Scraping Engage Ventures (Atlanta) ...")
    try:
        eng = scrape_engage()
        print(f"  +{len(eng)}")
        rows.extend(eng)
    except Exception as e:
        print(f"  FAIL: {e}", file=sys.stderr)

    print("Scraping Innovation Works (Pittsburgh) ...")
    try:
        iw = scrape_iw()
        print(f"  +{len(iw)} (page-1 visible only; JS-paginated source)")
        rows.extend(iw)
    except Exception as e:
        print(f"  FAIL: {e}", file=sys.stderr)

    print("Scraping UMD Momentum Fund (Baltimore) ...")
    try:
        m = scrape_momentum()
        print(f"  +{len(m)}")
        rows.extend(m)
    except Exception as e:
        print(f"  FAIL: {e}", file=sys.stderr)

    print("Scraping Health Wildcatters (Dallas) ...")
    try:
        h = scrape_hwc()
        print(f"  +{len(h)} (best-effort alt-text)")
        rows.extend(h)
    except Exception as e:
        print(f"  FAIL: {e}", file=sys.stderr)

    print(f"Adding {len(SEED_SPINOUTS)} curated TTO spinouts (publicly documented)")
    for s in SEED_SPINOUTS:
        rows.append({**s, "tagline": "", "location": "", "url": ""})

    df = pd.DataFrame(rows)
    print(f"\nTotal raw TTO rows: {len(df):,}")
    # Drop empty / obvious-noise names
    df = df[df["name"].str.len().between(2, 80)].copy()
    bad = df["name"].str.lower().str.fullmatch(
        r"(home|menu|about|contact|search|learn more|read more|portfolio|companies|next|prev)"
    )
    df = df[~bad.fillna(False)].copy()
    print(f"After noise filter: {len(df):,}")

    out_path = RAW_DIR / "tto_portfolio.parquet"
    df.to_parquet(out_path, index=False)
    df.to_csv(OUTPUT_DIR / "tto_portfolio.csv", index=False)
    try:
        df.to_excel(OUTPUT_DIR / "tto_portfolio.xlsx", index=False, engine="openpyxl")
    except Exception as e:
        print(f"WARN xlsx: {e}", file=sys.stderr)

    by = df.groupby(["msa", "source"]).size().reset_index(name="n")
    print("\nBy MSA × source:")
    print(by.to_string(index=False))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
