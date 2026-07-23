# Nutracare India Map

A Google-Maps-style, to-scale, zoomable map of **Maharashtra** (engine expandable to all-India)
that overlays operational + demographic data and lets you **draw a region and instantly get
consolidated stats** for everything inside it.

> **Data integrity:** every value on the map is traceable to a source and is checked by an
> independent validator (`pipeline/validate_data.py`). Income brackets are clearly labelled
> **modeled estimates** (no public household-income register exists in India). Hospitals are
> **real Google Maps listings** with exact coordinates. Nothing is fabricated. See
> [`data/VALIDATION.md`](data/VALIDATION.md) and [`data/SOURCES.md`](data/SOURCES.md).

## Layers
- **Income** — district choropleth: Est. PCI, strata S1–S4, % HIG+Ultra; click for the full
  EWS→Ultra-HIG household breakdown.
- **Population** — 2027 estimate, women 20–35, literacy.
- **Poshan** — beneficiaries, pregnant/lactating women, stunting/wasting/underweight %.
- **Hospitals** — **16,054 all-India** facilities: 5,453 exact Google Maps listings for
  Maharashtra + 10,601 from district-wise research (`source:research_sheet`, most at district
  centroid, `approx:true`). Filterable by **type** (Government /
  Private / Maternity-Neonatal), **income group** (High / Upper-mid / Mid / Low) and
  **ownership** (Private / Government / Trust / Mission / Defence / Railway / ESIC / Municipal /
  NGO / Other). Income group & ownership are modeled classifications.
- **Political** — 2024 Assembly constituencies coloured by winning party; popup MLA + minister.

## Encircle & consolidate
Click **Draw area**, click points, double-click to close. A district is counted **in full**
(not pro-rated) when its centre is inside the shape (or ≥50% covered). The right panel shows the
**consolidated totals** for all selected districts: population, households per income bracket,
Poshan figures, hospitals inside (point-in-polygon), and constituencies + parties.

## Run locally
```bash
npm install
npm start                # http://localhost:3000  (login below)
# or, no login (local dev only):
npm run preview
```
**Login:** set by `MAP_USER` / `MAP_PASS` env vars (see `.env.example`). One shared account, no signup.

## Rebuild the data
Requires Python with `pandas openpyxl` (`pip install pandas openpyxl`).
```bash
# 1) Demographic + political layers from the spreadsheets (does NOT touch hospitals.geojson)
python pipeline/build_data.py

# 2) Hospitals from Google Maps (needs Docker running)
python pipeline/gen_queries.py            # -> tools/gmaps-io/queries.txt
bash tools/run_scraper.sh                  # or: tools\run_scraper.bat   (DEPTH/CONC env optional)
python pipeline/convert_scraped.py         # -> data/hospitals.geojson (real coordinates)

# 3) Verify everything matches its source
python pipeline/validate_data.py           # exits non-zero on any mismatch
```

## Deploy to Vercel (hosted, single password) — exact steps

The repo is already Vercel-ready: `vercel.json` (static hosting + copies the 4 data files into
the web root at build) and `middleware.js` (Edge Basic Auth using `MAP_USER` / `MAP_PASS`).
Login is **`vnls` / `nutracare@1234`** (override via env vars below).

### Option A — Vercel dashboard (no CLI)
1. Put this folder in a Git repo and push to GitHub/GitLab/Bitbucket.
   ```bash
   cd NatalCare-IndiaMap
   git init && git add -A && git commit -m "Nutracare India Map"
   git branch -M main
   git remote add origin <your-repo-url> && git push -u origin main
   ```
   (`data/` is committed so the current scraped hospitals go live; `node_modules`, raw CSVs and
   `public/data/` are gitignored.)
2. On **vercel.com → Add New → Project → Import** your repo.
3. Framework Preset: **Other**. Leave Build/Output as-is (read from `vercel.json`).
4. **Settings → Environment Variables**, add for *Production*:
   - `MAP_USER` = `vnls`
   - `MAP_PASS` = `nutracare@1234`
5. Click **Deploy**. Open the URL → the browser prompts for the username/password.

### Option B — Vercel CLI
```bash
npm i -g vercel
cd NatalCare-IndiaMap
vercel            # first run: link/create the project, accept defaults
vercel env add MAP_USER production     # enter: vnls
vercel env add MAP_PASS production     # enter: nutracare@1234
vercel --prod     # deploy to production
```

### Updating the data after more scraping
Re-run the data steps locally, commit the changed files in `data/`, and push (Vercel
auto-redeploys), or run `vercel --prod` again:
```bash
python pipeline/convert_scraped.py        # refresh data/hospitals.geojson from new scrapes
git add data && git commit -m "refresh hospitals" && git push
```
The Google Maps scraper itself runs **offline as a data step** (Docker) — it is never part of the
hosted site.

### Local run (optional)
`npm install && npm start` → http://localhost:3000 (same credentials). `npm run preview` skips auth.

## Layout
- `server.js` — Express + Basic Auth, serves `public/` and `data/`.
- `public/` — `index.html`, `app.js` (MapLibre + Turf engine), `styles.css` (responsive).
- `pipeline/` — `build_data.py`, `convert_scraped.py`, `gen_queries.py`, `validate_data.py`.
- `data/` — generated GeoJSON/JSON + `SOURCES.md` + `VALIDATION.md`.
- `tools/` — `google-maps-scraper/` (built binary + clone), `run_scraper.*`, `gmaps-io/`.

## Importing more hospitals (all-India research mastersheet)
`pipeline/import_mastersheet.py` merges the hand-researched district-wise workbook into
`data/hospitals.geojson` in four resumable stages, then writes `data/IMPORT_REPORT.md`:
```bash
python pipeline/import_mastersheet.py parse       # workbook -> _ms_parsed.json
python pipeline/import_mastersheet.py classify    # + type / income_group / ownership
python pipeline/import_mastersheet.py geocode --centroids-only   # district centroids (ToS-safe)
python pipeline/import_mastersheet.py merge        # dedup + append -> hospitals.geojson
```
Dedup priority: place_id → maps_link → name+district → name+coords(50 m); existing records are
never overwritten, only enriched. New MH points get their canonical district by point-in-polygon
so counts/consolidation line up with the scraped layer. For precise per-hospital coordinates
(instead of district centroids), run `geocode` without `--centroids-only` — but note bulk
Nominatim use is subject to OSM's policy; prefer a paid geocoder/API key at scale.

## Roadmap (from the codebase audit)
Implemented: **income-group & ownership** filters, all-India hospital import, all-India
draw-&-consolidate for hospitals, and a **MapLibre GL upgrade 4.7.1 → 5.24.0** (clears the
`Unimplemented type: 4` symbol-layer errors on the district labels). Larger follow-ups:
- **Vector tiles (tippecanoe / MVT)** for hospitals + constituencies — the proper fix for
  loading nationwide data without shipping ~10 MB of flat GeoJSON, and a prerequisite for
  clickable all-India **district** boundaries (demographics outside MH need Census/SECC/NFHS
  modeling first). Would also re-enable hospital **clustering** cleanly at scale.
- **LGD district codes** to replace name-matching across 700+ districts (Bilaspur etc. collide).
- **Batch/threaded geocoding + spatial index (Shapely/R-tree)** in the Python pipeline.
