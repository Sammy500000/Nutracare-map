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
- **Hospitals** — real Google Maps listings (Government / Private / Maternity-Neonatal) with
  coordinates, category, phone, website, rating; clustered, filterable.
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

## Expanding to all-India (later)
The engine is data-driven: drop in all-India district/constituency GeoJSON and the same
build/validate pipeline scales. Income outside Maharashtra would be modeled from Census 2011 +
SECC + NFHS-5 and flagged as estimated.
