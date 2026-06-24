#!/usr/bin/env python3
"""
convert_scraped.py — turn the Google-Maps-scraper output into hospitals.geojson.

FACTUAL ONLY: every point keeps its REAL Google Maps coordinates, name, category,
address, phone, website and place_id. Nothing is invented.
  - District is assigned by point-in-polygon on the real coordinates (districts.geojson).
  - Type (Government / Private / Maternity-Neonatal) is classified from Google's own
    `category` field and the place name — never fabricated; the raw category is kept.
  - Deduplicated by place_id (and by name+location when place_id is missing).

Input : tools/gmaps-io/out/results.json  (NDJSON; lines may be objects or arrays)
Output: data/hospitals.geojson
"""
import json, os, re, sys

import glob
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "tools", "gmaps-io", "out")
# Read EVERY results*.json in the out folder so multiple scrape runs accumulate.
RESULT_FILES = sorted(glob.glob(os.path.join(OUT_DIR, "results*.json")))

# --- point-in-polygon (ray casting) against district polygons ---
def load_districts():
    gj = json.load(open(os.path.join(DATA, "districts.geojson"), encoding="utf-8"))
    polys = []
    for f in gj["features"]:
        name = f["properties"].get("name") or f["properties"].get("key")
        geom = f["geometry"]
        rings = []
        if geom["type"] == "Polygon":
            rings.append(geom["coordinates"])
        elif geom["type"] == "MultiPolygon":
            rings.extend(geom["coordinates"])
        polys.append((name, rings))
    return polys

def pt_in_ring(x, y, ring):
    inside = False
    n = len(ring); j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside

def pt_in_poly(x, y, rings_of_poly):
    # rings_of_poly: list of polygons, each [outer, hole1, ...]
    for poly in rings_of_poly:
        if not poly: continue
        if pt_in_ring(x, y, poly[0]):
            in_hole = any(pt_in_ring(x, y, h) for h in poly[1:])
            if not in_hole:
                return True
    return False

def district_for(lon, lat, polys):
    for name, rings in polys:
        if pt_in_poly(lon, lat, rings):
            return name
    return None

# --- type classification from Google category + name (factual, keyword-based) ---
GOV = re.compile(r"\b(govt|government|municipal|civil|general hospital|district hospital|"
                 r"rural hospital|esic|cottage hospital|sub.?district|primary health|phc|"
                 r"community health|chc|pmc|pcmc|bmc|corporation|sarkari|zilla)\b", re.I)
MAT = re.compile(r"\b(maternity|maternity|nursing home|prasuti|women|woman|mother|child|"
                 r"children|neonat|paediatr|pediatr|obstetri|gyna?ec|mother and child|"
                 r"bal rugnalaya|wadia)\b", re.I)

def classify(category, title):
    blob = f"{category or ''} {title or ''}"
    if MAT.search(blob): return "Maternity"
    if GOV.search(blob): return "Government"
    return "Private"

def read_rows():
    rows = []
    for path in RESULT_FILES:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line: continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.extend(o if isinstance(o, list) else [o])
    return rows

def main():
    if not RESULT_FILES:
        print("! no scraper output in", OUT_DIR, "- run the scraper first (tools/run_scraper.*)")
        sys.exit(1)
    print(f"  reading {len(RESULT_FILES)} result file(s): {[os.path.basename(p) for p in RESULT_FILES]}")
    polys = load_districts()
    rows = read_rows()
    print(f"  read {len(rows)} scraped records")

    seen, feats = set(), []
    counts = {"Government": 0, "Private": 0, "Maternity": 0}
    no_district = 0
    for r in rows:
        lat, lon = r.get("latitude"), r.get("longitude")
        if not lat or not lon: continue
        pid = r.get("place_id") or r.get("cid") or f"{r.get('title')}|{round(lat,5)},{round(lon,5)}"
        if pid in seen: continue
        seen.add(pid)
        dist = district_for(float(lon), float(lat), polys)
        if dist is None:
            no_district += 1
            continue  # outside Maharashtra -> drop (keeps data clean & factual)
        cat = r.get("category") or (r.get("categories") or [None])[0]
        htype = classify(cat, r.get("title"))
        counts[htype] += 1
        ca = r.get("complete_address") or {}
        feats.append({"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(lon), 6), round(float(lat), 6)]},
            "properties": {
                "name": r.get("title"),
                "type": htype,
                "category": cat,
                "district": dist,
                "location": r.get("address") or "",
                "pincode": ca.get("postal_code"),
                "phone": r.get("phone") or "",
                "website": r.get("web_site") or "",
                "rating": r.get("review_rating") or None,
                "reviews": r.get("review_count") or None,
                "place_id": r.get("place_id") or "",
                "maps_link": r.get("link") or "",
                "source": "google_maps",
                "approx": False,
            }})
    out = {"type": "FeatureCollection", "features": feats}
    json.dump(out, open(os.path.join(DATA, "hospitals.geojson"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  hospitals.geojson: {len(feats)} unique places | {counts} | dropped(outside MH) {no_district}")

if __name__ == "__main__":
    main()
