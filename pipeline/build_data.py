#!/usr/bin/env python3
"""
build_data.py — Nutracare India Map data pipeline (Maharashtra phase).

Reads the source spreadsheets in ../Nutracare All Data, the Maharashtra district
GeoJSON, geocodes hospitals via OSM Nominatim (cached), and emits the /data files
the frontend consumes:

    data/districts.geojson      district polygons + joined income/population/poshan
    data/hospitals.geojson      geocoded hospital points (Government/Private/Maternity)
    data/manifest.json          dataset availability + disclaimer
    data/SOURCES.md             provenance + retrieval dates + estimated/verified flags

Usage:
    python build_data.py                 # build districts; geocode Government+Maternity
    python build_data.py --hospitals all # also geocode the (large) Private list
    python build_data.py --no-geocode    # rebuild districts only, reuse hospital cache
"""

import argparse, json, os, re, sys, time, urllib.parse, urllib.request, random
from datetime import date

# --- Paths -------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
SRC = os.path.join(os.path.dirname(ROOT), "Nutracare All Data")  # sibling source folder
GEOJSON_URL = "https://raw.githubusercontent.com/udit-001/india-maps-data/main/geojson/states/maharashtra.geojson"
GEOJSON_LOCAL = os.path.join(DATA, "maharashtra_districts_raw.geojson")
GEOCODE_CACHE = os.path.join(DATA, "geocode_cache.json")
TODAY = date.today().isoformat()
HOUSEHOLD_SIZE = 4.4  # Maharashtra avg (Census 2011); used to convert population -> households

import pandas as pd

# --- District name normalization --------------------------------------------
# Canonical key = lowercase GeoJSON district name. Aliases map data-source spellings
# (renamed districts, typos, trailing spaces) onto the canonical polygon key.
ALIAS = {
    "chhatrapati sambhajinagar": "aurangabad",
    "aurangabad (css)": "aurangabad",
    "dharashiv": "osmanabad",
    "osmanabad (dharashiv)": "osmanabad",
    "buldana": "buldhana",
    "amaravti": "amravati",
    "ahmadnagar": "ahmednagar",
    "mumbai city": "mumbai", "mumbai suburban": "mumbai",
    "greater mumbai": "mumbai", "mumbai (suburban)": "mumbai",
}
def canon(name):
    if name is None: return None
    n = re.sub(r"\s+", " ", str(name)).strip().lower()
    n = re.sub(r"\s*\(.*?\)\s*", " ", n).strip()  # drop "(CSS)" etc.
    n = re.sub(r"\s+", " ", n).strip()
    return ALIAS.get(n, n)

def num(v):
    """Parse '₹4,25,000' / '5%' / '88.48' / 0.88 -> float; None if blank."""
    if v is None: return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and v != v) else float(v)
    s = str(v).strip()
    if not s or s.lower() in ("nan", "-", "na"): return None
    s = s.replace("₹", "").replace(",", "").replace("%", "").strip()
    try: return float(s)
    except ValueError: return None

# --- Loaders -----------------------------------------------------------------
def load_income():
    f = os.path.join(SRC, "Maharashtra (Females).xlsx")
    df = pd.read_excel(f, sheet_name="District Income Brackets Full", header=3)
    df = df[df["District"].notna()]
    out = {}
    for _, r in df.iterrows():
        key = canon(r["District"])
        # skip junk legend/source rows that aren't real districts
        if not key or len(key) > 40 or key in ("economically weaker section", "lower income group",
            "middle income group a", "middle income group b", "high income group", "ultra high income group"):
            continue
        rec = {
            "name": str(r["District"]).strip(),
            "division": str(r.get("Division", "")).strip() or None,
            "strata": str(r.get("Strata", "")).strip() or None,
            "strata_label": str(r.get("Strata Label", "")).strip() or None,
            "pop_2027": num(r.get("Population (2027 Est )")),
            "women_2035": num(r.get("Est women between (20-35)Years ")),
            "pci": num(r.get("Est. PCI\n(₹ p.a.)")),
            "inc_ews": num(r.get("EWS %\n(<₹1.5L)")),
            "inc_lig": num(r.get("LIG %\n(₹1.5-3L)")),
            "inc_miga": num(r.get("MIG-A %\n(₹3-6L)")),
            "inc_migb": num(r.get("MIG-B %\n(₹6-12L)")),
            "inc_hig": num(r.get("HIG %\n(₹12-25L)")),
            "inc_ultra": num(r.get("Ultra %\n(>₹25L)")),
        }
        merge_district(out, key, rec)
    # finalize weighted merges (Mumbai City + Suburban)
    for k, v in out.items():
        finalize_income(v)
    return out

def merge_district(out, key, rec):
    """Population-weighted merge of multiple rows mapping to one polygon (e.g. Mumbai)."""
    if key not in out:
        out[key] = {**rec, "_wsum": rec.get("pop_2027") or 0, "_parts": [rec]}
        return
    out[key]["_parts"].append(rec)

def finalize_income(v):
    parts = v.pop("_parts", None)
    v.pop("_wsum", None)
    if not parts or len(parts) == 1:
        p = parts[0] if parts else v
        v.update(p)
        v["households"] = round(p["pop_2027"] / HOUSEHOLD_SIZE) if p.get("pop_2027") else None
        return
    tot = sum((p.get("pop_2027") or 0) for p in parts) or 1
    def wavg(field):
        s = sum((p.get(field) or 0) * (p.get("pop_2027") or 0) for p in parts)
        return round(s / tot, 2)
    v["pop_2027"] = round(sum((p.get("pop_2027") or 0) for p in parts))
    v["women_2035"] = round(sum((p.get("women_2035") or 0) for p in parts))
    v["households"] = round(v["pop_2027"] / HOUSEHOLD_SIZE)
    v["pci"] = round(wavg("pci"))
    for b in ("inc_ews","inc_lig","inc_miga","inc_migb","inc_hig","inc_ultra"):
        v[b] = wavg(b)
    # strata/label/name from the largest part
    big = max(parts, key=lambda p: p.get("pop_2027") or 0)
    v["strata"], v["strata_label"] = big["strata"], big["strata_label"]
    v["name"] = "Mumbai (City + Suburban)" if "mumbai" in canon(big["name"]) else big["name"]

def load_population_literacy():
    """Literacy per polygon; population-weighted where rows merge (Mumbai City + Suburban)."""
    f = os.path.join(SRC, "Maharashtra (Females).xlsx")
    df = pd.read_excel(f, sheet_name="Population %", header=2)
    lit_col = next((c for c in df.columns if "Literacy" in str(c)), None)
    pop_col = next((c for c in df.columns if "2027 Est Population" in str(c)), None)
    dist_col = df.columns[0]
    raw = {}
    for _, r in df.iterrows():
        key = canon(r[dist_col])
        if not key: continue
        lit = num(r.get(lit_col)) if lit_col else None
        if lit is not None and lit <= 1.5:  # stored as fraction
            lit = round(lit * 100, 2)
        w = num(r.get(pop_col)) if pop_col else None
        if lit is not None:
            raw.setdefault(key, []).append((lit, w or 0))
    out = {}
    for key, rows in raw.items():
        if len(rows) == 1:
            out[key] = rows[0][0]
        else:
            tot = sum(w for _, w in rows) or 1
            out[key] = round(sum(l * w for l, w in rows) / tot, 2)
    return out

POSHAN_MAP = {
    "poshan_awc": "Total number of AW Centers",
    "poshan_beneficiaries": "Total number of Beneficiaries",
    "poshan_pregnant": "Total number of Pregnant Women",
    "poshan_lactating": "Total number of Lactating Women",
    "poshan_stunting": "Total % of Children that are Stunting",
    "poshan_wasting": "Total % of Children that are Wasting",
    "poshan_underweight": "Total % of Children that are Underweight",
}
POSHAN_PCT = {"poshan_stunting", "poshan_wasting", "poshan_underweight"}
def load_poshan():
    """Counts are SUMMED across rows that map to one polygon (Mumbai City + Suburban).
    Percentages are weighted by beneficiary count (proxy for child population)."""
    f = os.path.join(SRC, "poshan_tracker_district_wise.xlsx")
    df = pd.read_excel(f, sheet_name="Maharashtra", header=0)
    raw = {}
    for _, r in df.iterrows():
        key = canon(r.get("District"))
        if not key: continue
        raw.setdefault(key, []).append({dst: num(r.get(src)) for dst, src in POSHAN_MAP.items()})
    out = {}
    for key, rows in raw.items():
        if len(rows) == 1:
            out[key] = rows[0]; continue
        ben = [x.get("poshan_beneficiaries") or 0 for x in rows]
        tot = sum(ben) or 1
        merged = {}
        for fld in POSHAN_MAP:
            if fld in POSHAN_PCT:
                merged[fld] = round(sum((x.get(fld) or 0) * b for x, b in zip(rows, ben)) / tot, 1)
            else:
                merged[fld] = round(sum(x.get(fld) or 0 for x in rows))
        out[key] = merged
    return out

def load_hospitals(which):
    """Return list of {name, type, district, location, services} from the source sheets."""
    recs = []
    f = os.path.join(SRC, "Maharashtra Maternity Hospital.xlsx")
    if which in ("default", "all"):
        gov = pd.read_excel(f, sheet_name="GOVERNMENT ", header=1)
        for _, r in gov.iterrows():
            name = r.get("Government Medical College / Hospital")
            if pd.isna(name) or pd.isna(r.get("District")): continue
            recs.append({"name": str(name).strip(), "type": "Government",
                         "district": str(r["District"]).strip(),
                         "location": str(r.get("Location", "") or "").strip(),
                         "services": str(r.get("Type", "") or "").strip()})
    if which == "all":
        try:
            priv = pd.read_excel(f, sheet_name="PRIVATE ", header=1)
            ncol = next((c for c in priv.columns if "ospital" in str(c) or "Name" in str(c)), priv.columns[1] if len(priv.columns)>1 else priv.columns[0])
            for _, r in priv.iterrows():
                name = r.get(ncol)
                if pd.isna(name) or pd.isna(r.get("District")): continue
                recs.append({"name": str(name).strip(), "type": "Private",
                             "district": str(r["District"]).strip(),
                             "location": str(r.get("Location", "") or "").strip(),
                             "services": str(r.get("Type", "") or "").strip()})
        except Exception as e:
            print("  ! private sheet skipped:", e)
    # ICDS Mumbai maternity homes
    if which in ("default", "all"):
        try:
            icds = pd.read_excel(os.path.join(SRC, "ICDS.xlsx"), sheet_name="Hospitals ", header=2)
            ncol = next((c for c in icds.columns if "Hospital Name" in str(c)), None)
            lcol = next((c for c in icds.columns if "Location" in str(c) or "Ward" in str(c)), None)
            scol = next((c for c in icds.columns if "Service" in str(c)), None)
            for _, r in icds.iterrows():
                name = r.get(ncol) if ncol else None
                if pd.isna(name): continue
                recs.append({"name": str(name).strip(), "type": "Maternity",
                             "district": "Mumbai",
                             "location": str(r.get(lcol, "") or "").strip() if lcol else "",
                             "services": str(r.get(scol, "") or "").strip() if scol else ""})
        except Exception as e:
            print("  ! ICDS sheet skipped:", e)
    return recs

# --- Geocoding (Nominatim, cached, 1 req/sec) --------------------------------
def load_cache():
    if os.path.exists(GEOCODE_CACHE):
        return json.load(open(GEOCODE_CACHE, encoding="utf-8"))
    return {}
def save_cache(c):
    json.dump(c, open(GEOCODE_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

def nominatim(q):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "countrycodes": "in"})
    req = urllib.request.Request(url, headers={"User-Agent": "NutracareIndiaMap/0.1 (data pipeline)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            arr = json.load(r)
            if arr: return [float(arr[0]["lon"]), float(arr[0]["lat"])]
    except Exception:
        return None
    return None

def load_lookup(fname):
    p = os.path.join(DATA, fname)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}

PIN_RE = re.compile(r"\b([1-9]\d{5})\b")
def match_locality(text, localities):
    """Find the best locality-name hit inside a free-text location string."""
    t = text.lower()
    best = None
    for name, coord in localities.items():
        if len(name) >= 4 and name in t:
            if best is None or len(name) > best[0]:
                best = (len(name), coord)
    return best[1] if best else None

def geocode_hospitals(recs, centroids, do_geocode=True):
    cache = load_cache()
    pincodes = load_lookup("mh_pincodes.json")
    localities = load_lookup("mh_localities.json")
    feats = {"pin": 0, "loc": 0, "osm": 0, "centroid": 0, "dropped": 0}
    out = []
    for i, h in enumerate(recs):
        loc = h["location"] or ""
        blob = f"{loc} {h['name']}"
        coord, prec = None, "centroid"

        # 1) pincode in the location text -> reliable neighbourhood coord
        m = PIN_RE.search(blob)
        if m and m.group(1) in pincodes:
            coord, prec = pincodes[m.group(1)], "pin"; feats["pin"] += 1
        # 2) locality / area name match within Maharashtra
        if coord is None:
            lc = match_locality(blob, localities)
            if lc: coord, prec = lc, "loc"; feats["loc"] += 1
        # 3) OSM Nominatim on locality + district (cached)
        if coord is None and do_geocode:
            query = ", ".join(x for x in [loc.split(",")[0], h["district"], "Maharashtra, India"] if x)
            key = query.lower()
            c = cache.get(key) if key in cache else (nominatim(query) if (time.sleep(1.05) or True) else None)
            if key not in cache: cache[key] = c
            if c: coord, prec = c, "osm"; feats["osm"] += 1
        # 4) district centroid + jitter so the point still appears (flagged approx)
        if coord is None:
            cen = centroids.get(canon(h["district"]))
            if not cen: feats["dropped"] += 1; continue
            coord = [cen[0] + random.uniform(-0.04, 0.04), cen[1] + random.uniform(-0.04, 0.04)]
            feats["centroid"] += 1
        out.append({"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(coord[0]), 6), round(float(coord[1]), 6)]},
            "properties": {"name": h["name"], "type": h["type"], "district": h["district"],
                           "location": loc, "services": h["services"],
                           "approx": prec in ("centroid",), "geo": prec}})
        if (i + 1) % 100 == 0: save_cache(cache)
    save_cache(cache)
    print(f"  hospitals: {len(out)} placed | pincode {feats['pin']} | locality {feats['loc']} "
          f"| osm {feats['osm']} | centroid(approx) {feats['centroid']} | dropped {feats['dropped']}")
    return {"type": "FeatureCollection", "features": out}

# --- GeoJSON assembly --------------------------------------------------------
def fetch_boundaries():
    if not os.path.exists(GEOJSON_LOCAL):
        print("  downloading Maharashtra district boundaries…")
        req = urllib.request.Request(GEOJSON_URL, headers={"User-Agent": "NutracareIndiaMap/0.1"})
        with urllib.request.urlopen(req, timeout=60) as r:
            open(GEOJSON_LOCAL, "wb").write(r.read())
    return json.load(open(GEOJSON_LOCAL, encoding="utf-8"))

def centroid(geom):
    # simple average of all coords (good enough for fallback jitter / labels)
    xs, ys = [], []
    def walk(c):
        if isinstance(c[0], (int, float)): xs.append(c[0]); ys.append(c[1])
        else:
            for x in c: walk(x)
    walk(geom["coordinates"])
    return [sum(xs)/len(xs), sum(ys)/len(ys)]

def build_districts(income, literacy, poshan):
    gj = fetch_boundaries()
    centroids, matched, unmatched = {}, [], []
    for f in gj["features"]:
        dname = f["properties"].get("district")
        key = canon(dname)
        centroids[key] = centroid(f["geometry"])
        p = {"name": dname, "key": key}
        inc = income.get(key); lit = literacy.get(key); pos = poshan.get(key)
        if inc:
            for fld in ("division","strata","strata_label","pop_2027","women_2035","households","pci",
                        "inc_ews","inc_lig","inc_miga","inc_migb","inc_hig","inc_ultra"):
                p[fld] = inc.get(fld)
            if inc.get("inc_hig") is not None:
                p["inc_hig_ultra"] = round((inc.get("inc_hig") or 0) + (inc.get("inc_ultra") or 0), 1)
            p["name"] = inc.get("name", dname)
            matched.append(key)
        else:
            unmatched.append(key)
        if lit is not None: p["literacy"] = lit
        if pos: p.update({k: v for k, v in pos.items() if v is not None})
        f["properties"] = p
    print(f"  districts: {len(gj['features'])} polygons | income matched {len(matched)} | no-income {len(unmatched)}: {unmatched}")
    return gj, centroids

# --- Political layer (2024 Vidhan Sabha) -------------------------------------
AC_GEOJSON = os.path.join(DATA, "mh_ac_raw.json")
RESULTS_CSV = os.path.join(DATA, "mh_2024_results.csv")
PARTY_COLORS = {
    "Bharatiya Janata Party": "#ff7a00",
    "Shiv Sena": "#ffcc00",
    "Nationalist Congress Party": "#00b0f0",
    "Shiv Sena (Uddhav Balasaheb Thackeray)": "#e0560f",
    "Indian National Congress": "#1f6feb",
    "Nationalist Congress Party – Sharadchandra Pawar": "#c0392b",
    "Nationalist Congress Party - Sharadchandra Pawar": "#c0392b",
    "Samajwadi Party": "#e91e63",
    "All India Majlis-E-Ittehadul Muslimeen": "#138808",
    "Jan Surajya Shakti": "#7e57c2",
    "Independent": "#9aa5b1",
}
# Current Maharashtra cabinet (Third Fadnavis ministry, 2024). Matched to seats by
# winner NAME against the ECI results, so the AC mapping is self-verifying.
MINISTERS = [
    ("Devendra Fadnavis", "Chief Minister; Finance, Planning, Home"),
    ("Eknath Shinde", "Deputy CM; Urban Development, Housing, PWD"),
    ("Ajit Pawar", "Deputy CM; Finance (earlier)"),
    ("Radhakrishna Vikhe Patil", "Water Resources"),
    ("Chandrashekhar Bawankule", "Revenue"),
    ("Hasan Mushrif", "Medical Education"),
    ("Chandrakant Patil", "Higher & Technical Education"),
    ("Girish Mahajan", "Water Resources (Vidarbha); Disaster Management"),
    ("Ganesh Naik", "Forest"),
    ("Dadaji Bhuse", "School Education"),
    ("Uday Samant", "Industries, Marathi Language"),
    ("Pratap Sarnaik", "Transport"),
    ("Shambhuraj Desai", "Tourism, Mining"),
    ("Ashish Shelar", "Information Technology, Cultural Affairs"),
    ("Aditi Tatkare", "Women & Child Development"),
    ("Mangal Prabhat Lodha", "Skill Development"),
    ("Pankaja Munde", "Environment, Animal Husbandry"),
    ("Atul Save", "OBC, Dairy Development"),
    ("Nitesh Rane", "Fisheries, Ports"),
    ("Chhagan Bhujbal", "Food & Civil Supplies"),
    ("Sanjay Rathod", "Soil & Water Conservation"),
    ("Jayakumar Rawal", "Marketing, Protocol"),
    ("Akash Fundkar", "Labour"),
    ("Sanjay Shirsat", "Social Justice"),
    ("Prakash Abitkar", "Public Health & Family Welfare"),
    ("Narhari Zirwal", "Food & Drug Administration"),
]

def name_tokens(s):
    return set(t for t in re.sub(r"[^a-z ]", " ", str(s).lower()).split() if len(t) > 2)

def compute_winners():
    df = pd.read_csv(RESULTS_CSV)
    df["votes"] = df["evm_votes"].fillna(0) + df["postal_votes"].fillna(0)
    win = df.sort_values("votes", ascending=False).groupby("constituency_no").first().reset_index()
    out = {}
    for _, r in win.iterrows():
        out[int(r["constituency_no"])] = {"mla": str(r["candidate"]).title(), "party": str(r["party"]),
                                          "_ac_name": str(r.get("constituency", ""))}
    return out

# Disambiguation for common-surname ministers: minister name -> AC name substring.
MINISTER_AC_OVERRIDE = {
    "Chandrakant Patil": "kothrud",
    "Jayakumar Rawal": "sindkheda",
    "Radhakrishna Vikhe Patil": "shirdi",      # name spelled "Radhakrushna" in ECI data
    "Narhari Zirwal": "dindori",               # spelling variant
}

def match_ministers(winners):
    """Attach minister portfolio to a constituency by matching minister name to the winning MLA.
    Requires ALL minister tokens to appear in the candidate (subset); ambiguous matches use an
    explicit AC override; MLC ministers with no assembly seat are reported as unmatched."""
    flagged, unmatched = 0, []
    for name, portfolio in MINISTERS:
        want = name_tokens(name)
        override = MINISTER_AC_OVERRIDE.get(name)
        cands = []
        for ac, w in winners.items():
            if override:
                # match by AC handled below; collect by ac_name later
                continue
            if want.issubset(name_tokens(w["mla"])):
                cands.append(ac)
        target = None
        if override:
            target = next((ac for ac, w in winners.items()
                           if override in (w.get("_ac_name") or "").lower()), None)
        elif len(cands) == 1:
            target = cands[0]
        elif len(cands) > 1:
            unmatched.append(f"{name} (ambiguous: {len(cands)} seats)")
        if target is not None:
            winners[target]["is_minister"] = True
            winners[target]["portfolio"] = portfolio
            flagged += 1
        else:
            if not override:
                unmatched.append(name)
    print(f"  ministers matched to seats: {flagged}/{len(MINISTERS)}"
          + (f" | unresolved: {unmatched}" if unmatched else ""))
    return winners

def build_political():
    if not (os.path.exists(AC_GEOJSON) and os.path.exists(RESULTS_CSV)):
        print("  ! political sources missing — skipping constituencies.geojson")
        return False
    gj = json.load(open(AC_GEOJSON, encoding="utf-8"))
    winners = match_ministers(compute_winners())
    kept, unmatched = [], 0
    for f in gj["features"]:
        pr = f["properties"]
        try: acno = int(pr.get("AC_NO"))
        except (TypeError, ValueError): acno = None
        if not acno:  # AC_NO 0 = non-constituency coastal/water polygons -> drop
            continue
        kept.append(f)
        w = winners.get(acno, {})
        if not w: unmatched += 1
        party = w.get("party")
        f["properties"] = {
            "ac_no": acno, "ac_name": pr.get("AC_NAME"),
            "district": str(pr.get("DIST_NAME", "")).replace(" *", "").title(),
            "mla": w.get("mla"), "party": party,
            "party_color": PARTY_COLORS.get(party, "#7f8c8d"),
            "is_minister": bool(w.get("is_minister")), "portfolio": w.get("portfolio"),
        }
    out = {"type": "FeatureCollection", "features": kept}
    json.dump(out, open(os.path.join(DATA, "constituencies.geojson"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  constituencies: {len(kept)} features | unmatched winner {unmatched}")
    return True

# --- Outputs -----------------------------------------------------------------
def write_manifest(have_hosp, have_pol):
    m = {
        "generated": TODAY, "scope": "Maharashtra", "level": "district",
        "datasets": {"districts": True, "income": True, "population": True, "poshan": True,
                     "hospitals": have_hosp, "constituencies": have_pol},
        "disclaimer": ("Income brackets are MODELED ESTIMATES (Census 2011 + SECC + NFHS-5 wealth "
                       "quintiles + PMAY/RBI/Income-Tax brackets), not individual household records. "
                       "Mumbai City + Suburban merged to the single Mumbai polygon. "
                       "Hospitals are real Google Maps listings (exact coordinates) collected via the "
                       "open-source google-maps-scraper.")
    }
    json.dump(m, open(os.path.join(DATA, "manifest.json"), "w", encoding="utf-8"), indent=2, ensure_ascii=False)

def write_sources(which_hosp):
    txt = f"""# Data Sources & Provenance
_Retrieved / generated: {TODAY}. Scope: Maharashtra, district level._

| Layer | Source | Type | Notes |
|---|---|---|---|
| District boundaries | udit-001/india-maps-data (Census 2011 admin) | Verified (open) | 35 districts incl. Palghar |
| Income brackets (EWS→Ultra-HIG), strata, PCI | `Maharashtra (Females).xlsx` → District Income Brackets Full | **Modeled estimate** | Census 2011 + SECC-2011 + NFHS-5 + PMAY/RBI/Income-Tax bands |
| Population 2027, women 20–35, literacy | `Maharashtra (Females).xlsx` | Estimate (projected) | From Census 2011 base |
| Poshan (beneficiaries, pregnant, lactating, stunting/wasting/underweight) | `poshan_tracker_district_wise.xlsx` (Poshan Tracker) | Verified (govt) | Official MWCD dashboard export |
| Hospitals / maternity / neonatal (LIVE map layer) | **Google Maps** via gosom/google-maps-scraper | Verified (real listings) | Real coordinates, category, phone, website, rating, place_id. District by point-in-polygon; type from Google category + name. See `pipeline/convert_scraped.py`. |
| Hospitals (reference only) | `Maharashtra Maternity Hospital.xlsx`, `ICDS.xlsx` | Compiled list | Optional `hospitals_sheets.geojson` (geocoded via pincode/OSM) — not shown on the map |

**Boundaries:** household counts use Maharashtra avg household size {HOUSEHOLD_SIZE} (Census 2011).
There is no public individual/household income register in India; the income layer is the most
accurate *modeled* clustering possible and must be read as estimates.
"""
    open(os.path.join(DATA, "SOURCES.md"), "w", encoding="utf-8").write(txt)

# --- Main --------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hospitals", choices=["default", "all", "none"], default="none",
                    help="Sheet-based hospital REFERENCE file (hospitals_sheets.geojson). "
                         "The live map uses hospitals.geojson from the Google Maps scraper "
                         "(pipeline/convert_scraped.py). default=Govt+Mumbai maternity; all=+Private.")
    ap.add_argument("--no-geocode", action="store_true", help="reuse cache only, no new Nominatim calls")
    args = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)

    print("Loading spreadsheets…")
    income = load_income()
    literacy = load_population_literacy()
    poshan = load_poshan()

    print("Building district GeoJSON…")
    districts, centroids = build_districts(income, literacy, poshan)
    json.dump(districts, open(os.path.join(DATA, "districts.geojson"), "w", encoding="utf-8"),
              ensure_ascii=False)

    if args.hospitals != "none":
        # Sheet-based reference ONLY — written to hospitals_sheets.geojson so it never
        # clobbers the authoritative scraped hospitals.geojson.
        print(f"Loading sheet hospitals ({args.hospitals}) -> hospitals_sheets.geojson (reference)…")
        recs = load_hospitals(args.hospitals)
        print(f"  {len(recs)} hospital records; geocoding (cached)…")
        hg = geocode_hospitals(recs, centroids, do_geocode=not args.no_geocode)
        json.dump(hg, open(os.path.join(DATA, "hospitals_sheets.geojson"), "w", encoding="utf-8"), ensure_ascii=False)

    # Live hospital layer = Google Maps scraper output (real coordinates).
    have_hosp = os.path.exists(os.path.join(DATA, "hospitals.geojson"))

    print("Building political layer (2024 Vidhan Sabha)…")
    have_pol = build_political()
    write_manifest(have_hosp, have_pol)
    write_sources(args.hospitals)
    print("Done →", DATA)

if __name__ == "__main__":
    main()
