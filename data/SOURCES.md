# Data Sources & Provenance
_Retrieved / generated: 2026-06-22. Scope: Maharashtra, district level._

| Layer | Source | Type | Notes |
|---|---|---|---|
| District boundaries | udit-001/india-maps-data (Census 2011 admin) | Verified (open) | 35 districts incl. Palghar |
| Income brackets (EWS→Ultra-HIG), strata, PCI | `Maharashtra (Females).xlsx` → District Income Brackets Full | **Modeled estimate** | Census 2011 + SECC-2011 + NFHS-5 + PMAY/RBI/Income-Tax bands |
| Population 2027, women 20–35, literacy | `Maharashtra (Females).xlsx` | Estimate (projected) | From Census 2011 base |
| Poshan (beneficiaries, pregnant, lactating, stunting/wasting/underweight) | `poshan_tracker_district_wise.xlsx` (Poshan Tracker) | Verified (govt) | Official MWCD dashboard export |
| Hospitals / maternity / neonatal — Maharashtra (LIVE map layer) | **Google Maps** via gosom/google-maps-scraper | Verified (real listings) | Real coordinates, category, phone, website, rating, place_id. District by point-in-polygon; type from Google category + name. See `pipeline/convert_scraped.py`. |
| Hospitals — all-India research layer (LIVE map layer) | `MATERNITY HOSPITALS - RESEARCH MASTERSHEET.xlsx` (hand-compiled, district-wise, 29 states/UTs) | Compiled research | Name, address, type, phone where listed. Merged via `pipeline/import_mastersheet.py` (parse → classify → geocode → dedup). Points carry `source:research_sheet`; those placed at their district centroid are flagged `approx:true`. |
| Hospital income-group & ownership tags | Derived by rule from name/type/brand + the Maharashtra income-strata sheets (ground truth for MH) | **Modeled classification** | `income_group` ∈ HIGH / UPPER_MID / MID / LOW; `ownership` ∈ PRIVATE / GOVERNMENT / TRUST / MISSION / DEFENCE / RAILWAY / ESIC / MUNICIPAL / NGO / OTHER. Deterministic keyword/brand rules — read as best-effort estimates, not an official register. |
| Hospitals (reference only) | `Maharashtra Maternity Hospital.xlsx`, `ICDS.xlsx` | Compiled list | Optional `hospitals_sheets.geojson` (geocoded via pincode/OSM) — not shown on the map |

**Boundaries:** household counts use Maharashtra avg household size 4.4 (Census 2011).
There is no public individual/household income register in India; the income layer is the most
accurate *modeled* clustering possible and must be read as estimates.
