#!/usr/bin/env python3
"""
import_mastersheet.py — reusable importer that merges the hand-researched
"MATERNITY HOSPITALS - RESEARCH MASTERSHEET.xlsx" into data/hospitals.geojson.

The workbook has one sheet per state/UT, each hand-formatted with its own layout
(no two sheets are identical): some are single flat tables, most tile several
"district blocks" side by side, each block carrying its own little header row
(Name / Government-Private / Address / Phone, or similar) a few rows below a
district-name title and/or a "Private ..." / "Govt ..." section title. This
script detects those header rows generically by matching a small vocabulary of
known column labels, groups matched columns into blocks, and reads each block's
data rows independently until it runs out.

Stages (run independently; each is resumable / re-runnable):
  parse     -- read every sheet -> data/_ms_parsed.json (no network)
  classify  -- assign ownership + income_group to parsed records, and backfill
               those two fields onto the EXISTING hospitals.geojson features
               -> data/_ms_classified.json (no network)
  geocode   -- resolve lat/lon for parsed records via Nominatim (free, 1 req/s,
               disk-cached, safe to interrupt and resume)
  merge     -- dedup + merge into data/hospitals.geojson, write IMPORT_REPORT.md

Usage:
  python pipeline/import_mastersheet.py parse
  python pipeline/import_mastersheet.py classify
  python pipeline/import_mastersheet.py geocode
  python pipeline/import_mastersheet.py merge
"""
import json, os, re, sys, time, math, argparse, collections, urllib.request, urllib.parse

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
MASTERSHEET = r"F:\Valencia Nutrition Ltd\Nutracare All Data\MATERNITY HOSPITALS - RESEARCH MASTERSHEET.xlsx"

PARSED_PATH = os.path.join(DATA, "_ms_parsed.json")
CLASSIFIED_PATH = os.path.join(DATA, "_ms_classified.json")
EXISTING_BACKFILLED_PATH = os.path.join(DATA, "_ms_existing_backfilled.json")
GEOCODE_CACHE = os.path.join(DATA, "_ms_geocode_cache.json")
GEOCODED_PATH = os.path.join(DATA, "_ms_geocoded.json")
IMPORT_REPORT = os.path.join(DATA, "IMPORT_REPORT.md")
HOSPITALS_GEOJSON = os.path.join(DATA, "hospitals.geojson")

# State string -> the name Nominatim indexes best (some sheet tabs bundle UTs).
STATE_GEOCODE = {
    "Punjab & Chandigarh": "Punjab",
    "Jammu": "Jammu and Kashmir",
    "Kashmir": "Jammu and Kashmir",
    "Dadra and Nagar Haveli and Daman and Diu": "Dadra and Nagar Haveli",
}
# Common district-name variants/misspellings in the sheet -> canonical name.
DISTRICT_ALIAS = {
    "osamanabad": "Osmanabad", "sindhurg": "Sindhudurg", "ahemadnagar": "Ahmednagar",
    "rachi": "Ranchi", "jhunjhun": "Jhunjhunu", "ghaziapur": "Ghazipur",
    "bhatinda": "Bathinda", "pathlamthitta": "Pathanamthitta", "kammur": "Kannur",
    "mehsena": "Mehsana", "fazika": "Fazilka", "janjir champa": "Janjgir-Champa",
    "west singbhum": "West Singhbhum", "panch mahal": "Panchmahal",
    "jayshankar bhupalapally": "Jayashankar Bhupalpally",
    "dakshin bastar dantewada": "Dantewada", "aurangabad (css)": "Aurangabad",
    "shaheed bhagat singh nagar": "Nawanshahr", "sri muktsar sahib": "Muktsar",
    "fatehgarh sahib": "Fatehgarh Sahib", "tarn taran": "Tarn Taran",
    "ratlam elivery facilities": "Ratlam",
}

SKIP_SHEETS = {"INDEX", "Sheet33"}
MH_INCOME_SHEETS = {
    "MAHARASHTRA High Income": "HIGH",
    "MAHARASHTRA Upper Mid Income": "UPPER_MID",
    "MAHARASHTRA Mid Income": "MID",
    "MAHARASHTRA Low Income": "LOW",
}
MH_OWNERSHIP_ONLY_SHEETS = {"MAHARASHTRA PRIVATE ", "MAHARASHTRA GOVERNMENT "}
MH_SHEETS = set(MH_INCOME_SHEETS) | MH_OWNERSHIP_ONLY_SHEETS

STATE_NAME = {
    "GOA ": "Goa",
    "RAJASTHAN": "Rajasthan",
    "GUJARAT": "Gujarat",
    "Dadra & Nagar Haveli, Daman, Di": "Dadra and Nagar Haveli and Daman and Diu",
    "ANDAMAN&NICOBAR ISD.": "Andaman and Nicobar Islands",
    "ANDHRAPRADESH ": "Andhra Pradesh",
    "Karnataka": "Karnataka",
    "KERALA": "Kerala",
    "TAMILNADU": "Tamil Nadu",
    "TELANGANA": "Telangana",
    "PUDUCHERRY": "Puducherry",
    "DELHI": "Delhi",
    "HIMACHAL P.": "Himachal Pradesh",
    "jammu": "Jammu",
    "Kashmir ": "Kashmir",
    "PUNJAB & CHANDIGARH": "Punjab & Chandigarh",
    "HARYANA": "Haryana",
    "UP": "Uttar Pradesh",
    "UTTARAKHAND": "Uttarakhand",
    "Mainpur": "Manipur",
    "mizoram": "Mizoram",
    "Odisha ": "Odisha",
    "Bihar": "Bihar",
    "Assam ": "Assam",
    "WB": "West Bengal",
    "JHARKHAND": "Jharkhand",
    "ARUNACHAL P.": "Arunachal Pradesh",
    "CHHATTISHGARH": "Chhattisgarh",
    "MP": "Madhya Pradesh",
    "MAHARASHTRA PRIVATE ": "Maharashtra",
    "MAHARASHTRA GOVERNMENT ": "Maharashtra",
    "MAHARASHTRA High Income": "Maharashtra",
    "MAHARASHTRA Upper Mid Income": "Maharashtra",
    "MAHARASHTRA Mid Income": "Maharashtra",
    "MAHARASHTRA Low Income": "Maharashtra",
}

# ---------------------------------------------------------------- header vocab
# normalized header text -> canonical role. None = recognized but not captured
# (still counts toward "this is a header row").
HEADER_VOCAB = {
    "name": "name", "hospital": "name", "clinic": "name", "clinic name": "name",
    "hospital name": "name", "hospital / clinic name": "name", "hospital/clinic name": "name",
    "government medical college / hospital": "name", "government medical college/hospital": "name",
    "government / private": "govt_private", "government/private": "govt_private",
    "type": "type", "category": "type",
    "address": "address", "full address": "address", "location": "address",
    "location / address": "address",
    "area": "area", "sub-area / locality": "area", "sub-area/locality": "area",
    "phone": "phone", "phone number": "phone",
    "district": "district",
    "rating": "rating", "reviews": "reviews",
    "website": "website",
    "google maps url": "maps_link",
    "verification status": "verification",
    "notes": "notes", "remarks": "notes",
    "lead doctor / gynecologist": "doctor",
    "sr.": None, "sr. no.": None, "sr no": None, "sr.no.": None, "sr": None,
}

OWNERSHIP_LABEL_RE = re.compile(r"^\s*(private|govt\.?|government)\b", re.I)
DISTRICT_LABEL_RE = re.compile(r"^\s*district\s*:?\s*$", re.I)
BANNER_RE = re.compile(
    r"maternity hospital|district.?wise|verified\s+entries|strata group|income strata|"
    r"directory|exhaustive", re.I)
# Words that disqualify a cell from being a district title (section headers,
# hospital/address fragments). NOTE: deliberately does NOT include "nagar" —
# several real districts contain it (Ahmednagar, Udham Singh Nagar, ...).
NOT_A_DISTRICT_RE = re.compile(
    r"hospital|clinic|maternity|nursing|presence|special[it]|directory|strata|"
    r"\broad\b|\brd\b|marg|\bward\b|centre|center|obstetric|gyna?ec|pediatr|paediatr|"
    r"neonat|ivf|fertility|entries|verified|@|\.com|www", re.I)


def looks_like_district(t):
    t = t.strip()
    if not (2 <= len(t) <= 45):
        return False
    if re.search(r"\d", t):           # districts carry no digits
        return False
    if norm_header(t) in HEADER_VOCAB:
        return False
    if DISTRICT_LABEL_RE.match(t) or OWNERSHIP_LABEL_RE.match(t):
        return False
    if BANNER_RE.search(t) or NOT_A_DISTRICT_RE.search(t):
        return False
    return True


def norm_header(v):
    v = str(v).strip().lower()
    v = re.sub(r"\s+", " ", v)
    v = v.rstrip(":")
    return v


def norm_text(v):
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return re.sub(r"\s+", " ", str(v).strip())


def clean_district_candidate(text):
    t = text
    t = re.sub(r"\(.*?\)", "", t)  # drop parentheticals
    t = re.split(r"[–\-]{1,2}\s", t)[0]  # drop " – Verified..." tails
    t = re.split(r"\bdistrict\b", t, flags=re.I)[0]
    t = re.split(r",", t)[0]  # "Alipurduar, West Bengal" -> "Alipurduar"
    t = t.strip(" \t.:|")
    return t.strip()


def is_blockish_banner(text):
    return bool(BANNER_RE.search(text))


# ----------------------------------------------------------------- Stage 1: parse
def detect_header_groups(row_cells):
    """row_cells: list of (col_idx, normalized_text). Returns list of groups
    (list of (col_idx, role_or_None)). Side-by-side blocks (e.g. a Private
    table next to a Govt table) always start a fresh 'name' column, so a
    repeated 'name' role is what actually marks a new block — a plain column
    gap is NOT reliable (adjacent blocks can sit only 1-2 columns apart)."""
    groups = []
    cur = []
    prev_idx = None
    for idx, text in row_cells:
        role = HEADER_VOCAB.get(text, "__MISS__")
        if role == "__MISS__":
            continue
        starts_new_block = (role == "name" and any(r == "name" for _, r in cur)) or \
                            (prev_idx is not None and idx - prev_idx > 6)
        if starts_new_block and cur:
            groups.append(cur)
            cur = []
        cur.append((idx, role))
        prev_idx = idx
    if cur:
        groups.append(cur)
    # a valid block needs a name column plus at least one more descriptive role
    out = []
    for g in groups:
        roles = {r for _, r in g if r}
        if "name" in roles and len(roles) >= 2:
            out.append(g)
        elif "name" in roles and len(g) >= 2:  # name + skipped sr-no column etc.
            out.append(g)
    return out


def _scan_window_for_district(df, header_row, lo, hi, max_up):
    """Scan upward within columns [lo..hi] for a district title. Only rows that
    are SPARSE within this window (<=2 filled cells) are considered title rows —
    this skips data rows that spill above a stacked header. Returns cleaned
    district or None."""
    lo = max(0, lo)
    hi = min(df.shape[1] - 1, hi)
    for r in range(header_row - 1, max(-1, header_row - 1 - max_up), -1):
        row = df.iloc[r]
        filled = [(c, norm_text(row.get(c))) for c in range(lo, hi + 1) if norm_text(row.get(c))]
        if not filled:
            continue
        if len(filled) > 2:        # a data row spanning this window — not a title
            continue
        for _, t in filled:
            cand = clean_district_candidate(t)
            if looks_like_district(cand):
                return cand
    return None


def scan_titles_above(df, header_row, col_lo, col_hi, max_up=10):
    """Find this block's district + ownership-section hint by scanning upward.

    District resolution:
      (a) block-local title, in the block's own column window — handles sheets
          that tile several DIFFERENT districts side by side (e.g. Karnataka);
      (b) fallback to the column-0 band title — handles sheets where one
          district in col 0 governs both the Private and Govt blocks on that
          row band (e.g. UP, Maharashtra income sheets).
    """
    district = _scan_window_for_district(df, header_row, col_lo - 1, col_hi, max_up)
    if district is None:
        district = _scan_window_for_district(df, header_row, 0, 2, max_up=80)

    # ownership hint: nearest Private/Govt section label in the block's columns
    ownership_hint = None
    lo = max(0, col_lo - 1); hi = min(df.shape[1] - 1, col_hi)
    for r in range(header_row - 1, max(-1, header_row - 1 - max_up), -1):
        row = df.iloc[r]
        for c in range(lo, hi + 1):
            t = norm_text(row.get(c))
            if t and OWNERSHIP_LABEL_RE.match(t):
                ownership_hint = "GOVERNMENT" if re.match(r"govt|government", t, re.I) else "PRIVATE"
                break
        if ownership_hint:
            break
    return district, ownership_hint


def extract_block_rows(df, header_row, role_map, col_lo, col_hi):
    """role_map: {col_idx: role}. Reads data rows below header_row until a
    fully-blank row (within col_lo..col_hi) or another header row is hit."""
    name_col = next((c for c, r in role_map.items() if r == "name"), None)
    if name_col is None:
        return []
    rows = []
    r = header_row + 1
    nrows = df.shape[0]
    blank_streak = 0
    while r < nrows:
        row = df.iloc[r]
        window = [norm_text(row.get(c)) for c in range(col_lo, col_hi + 1)]
        if not any(window):
            blank_streak += 1
            if blank_streak >= 2:  # tolerate a single blank divider row (e.g. a
                break               # sub-district label row), stop after 2 in a row
            r += 1
            continue
        blank_streak = 0
        # stop if this row is itself a header row for (part of) this range
        cells = [(c, norm_header(row.get(c))) for c in range(col_lo, col_hi + 1) if norm_text(row.get(c))]
        vocab_hits = sum(1 for _, t in cells if t in HEADER_VOCAB)
        if vocab_hits >= 2:
            break
        name_val = norm_text(row.get(name_col))
        if not name_val:
            r += 1
            continue
        rec = {}
        for c, role in role_map.items():
            if role is None:
                continue
            rec[role] = norm_text(row.get(c))
        rows.append(rec)
        r += 1
    return rows


def parse_sheet(xl, sheet_name):
    df = xl.parse(sheet_name, header=None)
    nrows, ncols = df.shape
    state = STATE_NAME.get(sheet_name, sheet_name.strip())
    records = []

    # pass 1: find every header row + its column groups
    header_hits = []  # (row_idx, groups)
    for i in range(nrows):
        row = df.iloc[i]
        cells = [(j, norm_header(v)) for j, v in row.items() if norm_text(v)]
        groups = detect_header_groups(cells)
        if groups:
            header_hits.append((i, groups))

    for header_row, groups in header_hits:
        for g in groups:
            col_lo = min(c for c, _ in g)
            col_hi = max(c for c, _ in g)
            role_map = {c: r for c, r in g if r}
            has_district_col = "district" in role_map.values()
            district_title, ownership_hint = (None, None)
            if not has_district_col:
                district_title, ownership_hint = scan_titles_above(df, header_row, col_lo, col_hi)
            else:
                _, ownership_hint = scan_titles_above(df, header_row, col_lo, col_hi)
            rows = extract_block_rows(df, header_row, role_map, col_lo, col_hi)
            for rec in rows:
                rec["state"] = state
                rec["source_sheet"] = sheet_name
                if "district" not in rec or not rec.get("district"):
                    rec["district"] = district_title or ""
                rec["ownership_hint"] = ownership_hint or ""
                records.append(rec)
    return records


# ============================================================ Stage 2: classify
# ---- type (Government / Private / Maternity) — same logic as convert_scraped.py
# so the map's EXISTING category filter keeps working for new records too.
GOV_RE = re.compile(r"\b(govt|government|municipal|civil|general hospital|district hospital|"
                    r"rural hospital|esic|esi\b|cottage hospital|sub.?district|primary health|phc|"
                    r"community health|chc|pmc|pcmc|bmc|mcgm|corporation|sarkari|zilla|sadar|"
                    r"referral hospital|taluk|taluka|medical college|aiims|jipmer|pgimer|"
                    r"military|army|naval|navy|air force|command hospital|railway|sub.?centre|"
                    r"sub.?center|state general|women hospital|women's hospital)\b", re.I)
MAT_RE = re.compile(r"\b(maternity|nursing home|prasuti|women|woman|mother|child|children|neonat|"
                    r"paediatr|pediatr|obstetri|gyna?ec|bal rugnalaya|wadia|matru|garbh|"
                    r"fertility|ivf|test tube|sncu|nicu)\b", re.I)


def classify_type(*texts):
    blob = " ".join(t for t in texts if t)
    if MAT_RE.search(blob):
        return "Maternity"
    if GOV_RE.search(blob):
        return "Government"
    return "Private"


# ---- ownership (PRIVATE/GOVERNMENT/TRUST/MISSION/DEFENCE/RAILWAY/ESIC/MUNICIPAL/NGO/OTHER)
DEFENCE_RE = re.compile(r"\b(military|army|naval|navy|air ?force|command hospital|cantonment|"
                        r"armed forces|base hospital|\bmh\b|inhs|sainik)\b", re.I)
RAILWAY_RE = re.compile(r"\b(railway|divisional railway|central railway|zonal railway|\bdrh\b)\b", re.I)
ESIC_RE = re.compile(r"\b(esic|e\.s\.i\.c|\besi\b|employees.?state insurance)\b", re.I)
MUNI_RE = re.compile(r"\b(municipal|mcgm|bmc|pmc|pcmc|nmmc|corporation|nagar nigam|nagar palika|"
                     r"mahanagar|cantonment board)\b", re.I)
MISSION_RE = re.compile(r"\b(mission|christian|c\.s\.i|csi\b|cmc\b|catholic|holy family|holy cross|"
                        r"lutheran|baptist|methodist|evangel|st\.? ?(mary|john|joseph|thomas|"
                        r"elizabeth|luke|george)|marthoma|emmanuel|nazareth)\b", re.I)
TRUST_RE = re.compile(r"\b(trust|charitable|charity|seva|seva|foundation|sansthan|sanstha|"
                      r"memorial|relief|welfare society|dharmasth|dharmarth|wadia)\b", re.I)
GOVT_RE = re.compile(r"\b(govt|government|civil hospital|district hospital|general hospital|"
                     r"rural hospital|primary health|phc|community health|chc|sub.?district|"
                     r"sub.?centre|sub.?center|sadar|referral hospital|taluk|taluka|zilla|"
                     r"medical college|aiims|jipmer|pgimer|sarkari|state general|"
                     r"district women|women hospital|women's hospital|upgraded phc|\buphc\b|"
                     r"government medical|institute of medical sciences|\bgh\b)\b", re.I)
NGO_RE = re.compile(r"\b(ngo|non.?govern|society for|red cross|marie stopes|family planning|"
                    r"fpai|smile foundation)\b", re.I)


def classify_ownership(name, htype_hint="", type_col="", ownership_hint=""):
    blob = " ".join(t for t in (name, type_col, htype_hint) if t)
    # most specific first
    if DEFENCE_RE.search(blob):
        return "DEFENCE"
    if RAILWAY_RE.search(blob):
        return "RAILWAY"
    if ESIC_RE.search(blob):
        return "ESIC"
    if MUNI_RE.search(blob):
        return "MUNICIPAL"
    if MISSION_RE.search(blob):
        return "MISSION"
    # explicit "Government/Private" column hint is strong
    hint = (htype_hint or "").strip().lower()
    if hint.startswith("govt") or hint.startswith("government") or hint == "public":
        # still allow a trust/mission override above; here it's plain govt
        return "GOVERNMENT"
    if NGO_RE.search(blob):
        return "NGO"
    if TRUST_RE.search(blob):
        return "TRUST"
    if GOVT_RE.search(blob):
        return "GOVERNMENT"
    if hint.startswith("private"):
        return "PRIVATE"
    if ownership_hint == "GOVERNMENT":
        return "GOVERNMENT"
    if ownership_hint == "PRIVATE":
        return "PRIVATE"
    # Known private brands are private by definition.
    if HIGH_BRANDS.search(blob) or UPPERMID_BRANDS.search(blob):
        return "PRIVATE"
    # These sheets are overwhelmingly private maternity clinics/nursing homes;
    # a name with a clear private-practice shape defaults to PRIVATE, otherwise OTHER.
    if re.search(r"\b(hospitals?|clinics?|nursing home|maternity|centres?|centers?|care|"
                 r"dr\.?|multi.?special[it]\w*|polyclinic|health|medical|hosp\b|"
                 r"women'?s?|mother|child|IVF|fertility|sonography|surgical)\b", blob, re.I):
        return "PRIVATE"
    return "OTHER"


# ---- income_group (HIGH / UPPER_MID / MID / LOW)
HIGH_BRANDS = re.compile(
    r"\b(kokilaben|lilavati|breach candy|jaslok|hinduja|nanavati|saifee|bombay hospital|"
    r"wockhardt|fortis|medanta|max super|max hospital|max healthcare|max institute|"
    r"sir h\.?\s?n|reliance foundation|hn reliance|global hospital|sevenhills|seven hills|"
    r"hiranandani|bhatia hospital|jupiter hospital|kauvery|amrita institute|"
    r"gleneagles|artemis|blk|b\.l\.k|primus|indraprastha apollo|"
    r"nanavati max|hinduja healthcare|breach|p\.?d\.? hinduja)\b", re.I)
UPPERMID_BRANDS = re.compile(
    r"\b(apollo|manipal|care hospital|cloudnine|cloud nine|motherhood|rainbow|aster|"
    r"narayana|kims\b|yashoda|columbia asia|\bhcg\b|fernandez|surya|ankura|birla fertility|"
    r"ck birla|c\.k\.? birla|nova ivf|indira ivf|ferticity|milann|cocoon|sahyadri|"
    r"ruby hall|jehangir|sterling|noble hospital|d\.?y\.? patil|dr\.? d\.? y\.? patil|"
    r"ozone|lifeline|meditrina|wockhardt|star hospital|continental hospital|"
    r"rainbow children|regency|medicover|paras hospital|\bslg\b|citizen hospital|"
    r"prime hospital|maxcure|sunshine hospital)\b", re.I)


def classify_income(name, ownership, type_col="", forced=None):
    if forced:                       # Maharashtra income-strata sheets are ground truth
        return forced
    blob = " ".join(t for t in (name, type_col) if t)
    if ownership in ("GOVERNMENT", "MUNICIPAL", "ESIC", "RAILWAY", "DEFENCE"):
        return "LOW"
    if HIGH_BRANDS.search(blob):
        return "HIGH"
    if UPPERMID_BRANDS.search(blob):
        return "UPPER_MID"
    # Trust / mission / charitable serve mixed but generally mid/low-mid populations
    return "MID"


def cmd_classify():
    parsed = json.load(open(PARSED_PATH, encoding="utf-8"))
    recs = parsed["records"]
    ic = collections.Counter(); oc = collections.Counter(); tc = collections.Counter()
    for r in recs:
        name = r.get("name", "")
        type_col = r.get("type", "")
        govp = r.get("govt_private", "")
        oh = r.get("ownership_hint", "")
        htype = classify_type(name, type_col, r.get("notes", ""))
        own = classify_ownership(name, govp, type_col, oh)
        forced = MH_INCOME_SHEETS.get(r["source_sheet"])
        inc = classify_income(name, own, type_col, forced=forced)
        r["_type"] = htype; r["_ownership"] = own; r["_income_group"] = inc
        tc[htype] += 1; oc[own] += 1; ic[inc] += 1
    json.dump(parsed, open(CLASSIFIED_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"classified {len(recs)} new records -> {CLASSIFIED_PATH}")
    print("  type:      ", dict(tc))
    print("  ownership: ", dict(oc))
    print("  income:    ", dict(ic))

    # ---- backfill ownership + income_group onto EXISTING hospitals.geojson
    gj = json.load(open(HOSPITALS_GEOJSON, encoding="utf-8"))
    bic = collections.Counter(); boc = collections.Counter()
    for f in gj["features"]:
        p = f["properties"]
        name = p.get("name", "") or ""
        cat = p.get("category", "") or ""
        existing_type = p.get("type", "") or ""
        govp = "Government" if existing_type == "Government" else ""
        own = classify_ownership(name, govp, cat, "")
        inc = classify_income(name, own, cat)
        p["ownership"] = own
        p["income_group"] = inc
        if not p.get("state"):
            p["state"] = "Maharashtra"   # existing scraped layer is Maharashtra-only
        boc[own] += 1; bic[inc] += 1
    json.dump(gj, open(EXISTING_BACKFILLED_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\nbackfilled {len(gj['features'])} EXISTING features -> {EXISTING_BACKFILLED_PATH}")
    print("  ownership: ", dict(boc))
    print("  income:    ", dict(bic))


# ============================================================= Stage 3: geocode
# Deterministic, disk-cached, resumable geocoding via Nominatim (OpenStreetMap).
# Free service -> polite 1.1s throttle + descriptive User-Agent. Cache is keyed by
# the exact query string, so re-running only calls the API for still-missing keys.
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "NutracareIndiaMap/1.0 (hospital research geocoding; contact: vnls)"
INDIA_BOUNDS = (6.0, 38.0, 67.0, 98.5)  # lat_min, lat_max, lon_min, lon_max
CENTROID_PREFIX = "CENTROID::"


def is_nonhospital_row(r):
    """True for parsed rows that are actually district titles / section headers
    captured as data (no address/phone/maps + a bare place-name/section name)."""
    if (r.get("address") or "").strip() or (r.get("phone") or "").strip() or (r.get("maps_link") or "").strip():
        return False
    nm = (r.get("name") or "").strip()
    if not nm:
        return True
    if re.search(r"\bdistrict\b|maternity hospitals?\s*\(|^district:?$|^(private|govt|government)\b.*hospitals?",
                 nm, re.I):
        return True
    # bare place name with no hospital signal (Latin + common transliterations)
    HOSP_ANY = re.compile(r"hospital|clinic|nursing|maternity|centre|center|\bcare\b|\bdr\b|dr\.|"
                          r"health|medical|college|polyclinic|hosp|phc|chc|aspatal|aspataal|"
                          r"prasooti|prasuti|chikitsa|chikitsalaya|mahila|grih|gruh|rugnalaya|"
                          r"dawakhana|matru|matra|shishu|bal\b|garbh|ivf|fertility", re.I)
    if HOSP_ANY.search(nm):
        return False
    return looks_like_district(nm)


def _load_cache():
    if os.path.exists(GEOCODE_CACHE):
        return json.load(open(GEOCODE_CACHE, encoding="utf-8"))
    return {}


def _save_cache(cache):
    tmp = GEOCODE_CACHE + ".tmp"
    json.dump(cache, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, GEOCODE_CACHE)


def _nominatim(query):
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "in"}
    url = NOMINATIM + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.load(r)
    if not data:
        return None
    lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
    la0, la1, lo0, lo1 = INDIA_BOUNDS
    if not (la0 <= lat <= la1 and lo0 <= lon <= lo1):
        return None
    return [round(lon, 6), round(lat, 6)]  # GeoJSON order: [lon, lat]


def _clean_addr(a):
    a = re.sub(r"\s+", " ", (a or "").replace("\n", " ")).strip()
    return a


def record_query(r):
    """Best precise query for a record."""
    name = (r.get("name") or "").strip()
    addr = _clean_addr(r.get("address"))
    state = r.get("state", "")
    if addr and not re.search(r"india\s*$", addr, re.I):
        addr = addr + ", India"
    if name and addr:
        return f"{name}, {addr}"
    if addr:
        return addr
    dist = r.get("district", "")
    return ", ".join([p for p in [name, dist, state, "India"] if p])


def centroid_query(state, district):
    return f"{CENTROID_PREFIX}{state}::{district}"


def cmd_geocode(budget=None, centroids_only=False):
    data = json.load(open(CLASSIFIED_PATH, encoding="utf-8"))
    recs = [r for r in data["records"] if not is_nonhospital_row(r)]
    cache = _load_cache()
    calls = 0
    budget = budget if budget is not None else 10 ** 9

    # 1) district centroids (fast, enables fallback for every record)
    centroids = {}
    for r in recs:
        st, di = r.get("state", ""), r.get("district", "")
        if di:
            centroids.setdefault((st, di), centroid_query(st, di))
    # fetch keys not cached yet OR previously failed (value is None) — so alias /
    # state-name improvements get retried on a re-run.
    todo_c = [(k, q) for k, q in centroids.items() if not cache.get(q)]
    print(f"district centroids: {len(centroids)} unique, {len(todo_c)} to (re)fetch")
    for (st, di), q in todo_c:
        if calls >= budget:
            break
        geo_state = STATE_GEOCODE.get(st, st)
        geo_dist = DISTRICT_ALIAS.get(di.strip().lower(), di)
        real_q = f"{geo_dist}, {geo_state}, India"
        try:
            cache[q] = _nominatim(real_q)
        except Exception as e:
            cache[q] = None
            print("  ! centroid err", di, st, repr(e)[:60])
        calls += 1
        if calls % 25 == 0:
            _save_cache(cache); print(f"    ...{calls} calls")
        time.sleep(1.1)
    _save_cache(cache)

    if centroids_only:
        hits = sum(1 for v in cache.values() if v)
        print(f"centroids-only run done: {calls} API calls | cache {len(cache)} entries, {hits} resolved")
        print("  (precise per-record geocoding skipped; run without --centroids-only to enable)")
        return

    # 2) precise per-record geocoding (OPT-IN — bulk Nominatim use is subject to
    #    OSM's usage policy; prefer a paid geocoder / API key for the full 11k set)
    todo_r = []
    seen_q = set()
    for r in recs:
        q = record_query(r)
        if q and q not in cache and q not in seen_q:
            seen_q.add(q); todo_r.append(q)
    print(f"precise record queries: {len(seen_q)} unique uncached (budget left {budget - calls})")
    for q in todo_r:
        if calls >= budget:
            print("  budget reached; re-run 'geocode' to continue"); break
        try:
            cache[q] = _nominatim(q)
        except Exception as e:
            cache[q] = None
        calls += 1
        if calls % 25 == 0:
            _save_cache(cache); print(f"    ...{calls} calls, cache size {len(cache)}")
        time.sleep(1.1)
    _save_cache(cache)
    hits = sum(1 for v in cache.values() if v)
    print(f"done this run: {calls} API calls | cache {len(cache)} entries, {hits} resolved")


# =============================================================== Stage 4: merge
# Dedup priority (per spec):
#   1. same place_id   2. same maps_link   3. same name+district
#   4. same name + coords within 50 m
# On a duplicate: KEEP the existing record, fill in any fields it is missing.
def _norm_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _extract_pincode(addr):
    m = re.search(r"\b(\d{6})\b", addr or "")
    return m.group(1) if m else ""


def _place_id_from_maps(link):
    if not link:
        return ""
    m = re.search(r"place_id:([A-Za-z0-9_\-]+)", link)
    if m:
        return m.group(1)
    return ""


def _haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _jitter(state, district, name):
    """Deterministic (reproducible) small offset from a hash — NOT random, so
    re-runs place a given approx point identically."""
    h = abs(hash(f"{state}|{district}|{name}")) % 10000
    dx = ((h % 100) / 100.0 - 0.5) * 0.04         # ~ +-0.02 deg lon
    dy = ((h // 100) / 100.0 - 0.5) * 0.04         # ~ +-0.02 deg lat
    return dx, dy


# --- point-in-polygon on the Maharashtra district polygons, so new MH records
# get the SAME canonical district name the existing scraped layer uses (self-
# corrects sheet spellings + sub-areas: "Ghatkopar"/"Mumbai City" -> the merged
# "Mumbai (City + Suburban)" polygon; "Amravati" -> "Amaravti"; etc.).
def _load_mh_polys():
    gj = json.load(open(os.path.join(DATA, "districts.geojson"), encoding="utf-8"))
    polys = []
    for f in gj["features"]:
        name = f["properties"].get("name")
        geom = f["geometry"]
        rings = []
        if geom["type"] == "Polygon":
            rings.append(geom["coordinates"])
        elif geom["type"] == "MultiPolygon":
            rings.extend(geom["coordinates"])
        polys.append((name, rings))
    return polys


def _pt_in_ring(x, y, ring):
    inside = False
    n = len(ring); j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _district_at(lon, lat, polys):
    for name, rings in polys:
        for poly in rings:
            if poly and _pt_in_ring(lon, lat, poly[0]) and not any(_pt_in_ring(lon, lat, h) for h in poly[1:]):
                return name
    return None


def cmd_merge():
    existing = json.load(open(EXISTING_BACKFILLED_PATH, encoding="utf-8"))
    data = json.load(open(CLASSIFIED_PATH, encoding="utf-8"))
    cache = _load_cache() if os.path.exists(GEOCODE_CACHE) else {}
    recs = [r for r in data["records"] if not is_nonhospital_row(r)]
    dropped_nonhospital = len(data["records"]) - len(recs)

    feats = existing["features"]
    existing_count = len(feats)   # capture BEFORE we append new features
    mh_polys = _load_mh_polys()
    normalized_districts = 0
    # --- indexes over existing features for dedup ---
    by_pid, by_maps, by_namedist = {}, {}, {}
    coords_by_namekey = collections.defaultdict(list)
    for f in feats:
        p = f["properties"]
        pid = p.get("place_id") or _place_id_from_maps(p.get("maps_link"))
        if pid:
            by_pid[pid] = f
        if p.get("maps_link"):
            by_maps[p["maps_link"]] = f
        nk = _norm_key(p.get("name"))
        dk = _norm_key(p.get("district"))
        if nk:
            by_namedist[(nk, dk)] = f
            lon, lat = f["geometry"]["coordinates"]
            coords_by_namekey[nk].append((lon, lat, f))

    stats = collections.Counter()
    dup_by = collections.Counter()
    unresolved = []  # records we could not place at all

    def fill_missing(f, newp):
        added = 0
        for k, v in newp.items():
            if v not in (None, "", []) and f["properties"].get(k) in (None, "", []):
                f["properties"][k] = v
                added += 1
        return added

    for r in recs:
        name = (r.get("name") or "").strip()
        district = (r.get("district") or "").strip()
        state = r.get("state", "")
        pid = _place_id_from_maps(r.get("maps_link"))
        maps = r.get("maps_link") or ""
        nk = _norm_key(name); dk = _norm_key(district)

        # resolve coordinates: precise cache -> centroid+jitter -> None
        q = record_query(r)
        coord = cache.get(q)
        approx = False
        base = coord            # unjittered anchor used for district assignment
        if not coord:
            c = cache.get(centroid_query(state, district))
            if c:
                base = c
                dx, dy = _jitter(state, district, name)
                jittered = [round(c[0] + dx, 6), round(c[1] + dy, 6)]
                coord = jittered
                approx = True

        # Canonical district via point-in-polygon (Maharashtra only — that's the
        # polygon set the app renders + selects on). Use the unjittered anchor so
        # the assignment is stable; if the JITTERED point leaves the polygon, snap
        # it back to the anchor so approx points never drift into the sea / a
        # neighbouring district.
        if base:
            canon = _district_at(base[0], base[1], mh_polys)
            if canon:
                if canon != district:
                    normalized_districts += 1
                district = canon
                nk = _norm_key(name); dk = _norm_key(district)
                if approx and _district_at(coord[0], coord[1], mh_polys) != canon:
                    coord = [round(base[0], 6), round(base[1], 6)]

        newp = {
            "name": name,
            "type": r.get("_type", "Private"),
            "category": r.get("type", "") or "",
            "district": district,
            "state": state,
            "location": _clean_addr(r.get("address")),
            "pincode": _extract_pincode(r.get("address")),
            "phone": r.get("phone") or "",
            "website": r.get("website") or "",
            "rating": float(r["rating"]) if str(r.get("rating") or "").replace(".", "", 1).isdigit() else None,
            "reviews": int(r["reviews"]) if str(r.get("reviews") or "").isdigit() else None,
            "place_id": pid,
            "maps_link": maps,
            "ownership": r.get("_ownership", "OTHER"),
            "income_group": r.get("_income_group", "MID"),
            "source": "research_sheet",
            "approx": approx,
        }

        # --- dedup ---
        hit = None
        if pid and pid in by_pid:
            hit = by_pid[pid]; dup_by["place_id"] += 1
        elif maps and maps in by_maps:
            hit = by_maps[maps]; dup_by["maps_link"] += 1
        elif nk and (nk, dk) in by_namedist:
            hit = by_namedist[(nk, dk)]; dup_by["name+district"] += 1
        elif nk and coord and not approx:
            for lon, lat, f in coords_by_namekey.get(nk, []):
                if _haversine_m(coord[0], coord[1], lon, lat) <= 50:
                    hit = f; dup_by["name+coords50m"] += 1
                    break

        if hit is not None:
            fill_missing(hit, newp)
            stats["duplicate_merged"] += 1
            continue

        if not coord:
            unresolved.append(r)
            stats["unresolved_no_coord"] += 1
            continue

        nf = {"type": "Feature",
              "geometry": {"type": "Point", "coordinates": coord},
              "properties": newp}
        feats.append(nf)
        # index the new feature so intra-batch duplicates also collapse
        if pid: by_pid[pid] = nf
        if maps: by_maps[maps] = nf
        if nk:
            by_namedist[(nk, dk)] = nf
            coords_by_namekey[nk].append((coord[0], coord[1], nf))
        stats["appended"] += 1
        if approx:
            stats["appended_approx"] += 1
        else:
            stats["appended_precise"] += 1

    out = {"type": "FeatureCollection", "features": feats}
    json.dump(out, open(HOSPITALS_GEOJSON, "w", encoding="utf-8"), ensure_ascii=False)

    # ---- report ----
    tc = collections.Counter(f["properties"].get("type") for f in feats)
    oc = collections.Counter(f["properties"].get("ownership") for f in feats)
    ic = collections.Counter(f["properties"].get("income_group") for f in feats)
    states = collections.Counter(f["properties"].get("state") or "(existing/MH)" for f in feats)
    write_import_report(existing_count=existing_count,
                        parsed=len(data["records"]), dropped_nonhospital=dropped_nonhospital,
                        recs=len(recs), stats=stats, dup_by=dup_by,
                        final=len(feats), tc=tc, oc=oc, ic=ic, states=states,
                        unresolved=unresolved)
    print(f"MERGE COMPLETE -> {HOSPITALS_GEOJSON}")
    print(f"  final features: {len(feats)}  (was {existing_count})")
    print(f"  districts normalized to canonical MH polygons: {normalized_districts}")
    print(f"  {dict(stats)}")
    print(f"  dup breakdown: {dict(dup_by)}")
    print(f"  ownership: {dict(oc)}")
    print(f"  income:    {dict(ic)}")


def write_import_report(existing_count, parsed, dropped_nonhospital, recs, stats, dup_by,
                        final, tc, oc, ic, states, unresolved):
    L = []
    L.append("# Hospital Dataset Import Report\n")
    L.append("Merge of **MATERNITY HOSPITALS - RESEARCH MASTERSHEET.xlsx** into `data/hospitals.geojson`.\n")
    L.append("Generated by `pipeline/import_mastersheet.py` (parse -> classify -> geocode -> merge).\n")
    L.append("\n## Headline counts\n")
    L.append(f"| Metric | Count |\n|---|---|\n")
    L.append(f"| Existing hospitals (before) | {existing_count} |\n")
    L.append(f"| Rows parsed from workbook | {parsed} |\n")
    L.append(f"| Non-hospital rows skipped (district/section titles) | {dropped_nonhospital} |\n")
    L.append(f"| Candidate hospital records | {recs} |\n")
    L.append(f"| Duplicates merged into existing | {stats.get('duplicate_merged',0)} |\n")
    L.append(f"| New hospitals appended | {stats.get('appended',0)} |\n")
    L.append(f"| &nbsp;&nbsp;• appended with precise coords | {stats.get('appended_precise',0)} |\n")
    L.append(f"| &nbsp;&nbsp;• appended at district-centroid (approx) | {stats.get('appended_approx',0)} |\n")
    L.append(f"| Records unresolved (no coords yet) | {stats.get('unresolved_no_coord',0)} |\n")
    L.append(f"| **Final hospital count** | **{final}** |\n")
    L.append("\n## Duplicate detection breakdown\n")
    L.append("| Rule | Matches |\n|---|---|\n")
    for k in ["place_id", "maps_link", "name+district", "name+coords50m"]:
        L.append(f"| {k} | {dup_by.get(k,0)} |\n")
    L.append("\n## Final distribution — Type (existing map filter)\n")
    L.append("| Type | Count |\n|---|---|\n")
    for k, v in tc.most_common():
        L.append(f"| {k} | {v} |\n")
    L.append("\n## Final distribution — Ownership (new filter)\n")
    L.append("| Ownership | Count |\n|---|---|\n")
    for k, v in oc.most_common():
        L.append(f"| {k} | {v} |\n")
    L.append("\n## Final distribution — Income Group (new filter)\n")
    L.append("| Income group | Count |\n|---|---|\n")
    for k, v in ic.most_common():
        L.append(f"| {k} | {v} |\n")
    L.append("\n## Hospitals per state/UT (final)\n")
    L.append("| State/UT | Count |\n|---|---|\n")
    for k, v in states.most_common():
        L.append(f"| {k} | {v} |\n")
    if unresolved:
        L.append(f"\n## Unresolved records ({len(unresolved)})\n")
        L.append("These parsed hospital rows could not be geocoded yet (no address match and no "
                 "district centroid). Re-run `geocode` to resolve, then `merge` again.\n\n")
        for r in unresolved[:100]:
            L.append(f"- {r.get('name','')[:60]} — {r.get('district','')}, {r.get('state','')}\n")
        if len(unresolved) > 100:
            L.append(f"- …and {len(unresolved)-100} more\n")
    open(IMPORT_REPORT, "w", encoding="utf-8").write("".join(L))
    print(f"  report -> {IMPORT_REPORT}")


def cmd_parse():
    xl = pd.ExcelFile(MASTERSHEET)
    all_records = []
    per_sheet_counts = {}
    for sn in xl.sheet_names:
        if sn in SKIP_SHEETS:
            continue
        recs = parse_sheet(xl, sn)
        per_sheet_counts[sn] = len(recs)
        all_records.extend(recs)
        print(f"  {sn!r:35s} -> {len(recs):5d} records")
    print(f"\nTOTAL parsed records: {len(all_records)}")
    with open(PARSED_PATH, "w", encoding="utf-8") as f:
        json.dump({"records": all_records, "per_sheet_counts": per_sheet_counts}, f, ensure_ascii=False, indent=None)
    print(f"written -> {PARSED_PATH}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["parse", "classify", "geocode", "merge"])
    ap.add_argument("--budget", type=int, default=None,
                    help="max Nominatim API calls this run (geocode stage; for resumable batches)")
    ap.add_argument("--centroids-only", action="store_true",
                    help="geocode only the unique district centroids (ToS-safe fallback), skip per-record")
    args = ap.parse_args()
    if args.stage == "parse":
        cmd_parse()
    elif args.stage == "classify":
        cmd_classify()
    elif args.stage == "geocode":
        cmd_geocode(budget=args.budget, centroids_only=args.centroids_only)
    elif args.stage == "merge":
        cmd_merge()
    else:
        print(f"stage {args.stage!r} not implemented yet in this script version")
        sys.exit(1)
