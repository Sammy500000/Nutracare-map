#!/usr/bin/env bash
# Run the Google Maps scraper (Docker) over tools/gmaps-io/queries.txt.
# Generate queries first:  python pipeline/gen_queries.py
# Then:  bash tools/run_scraper.sh      (env: DEPTH=6 CONC=4)
set -e
cd "$(dirname "$0")/gmaps-io"
DEPTH="${DEPTH:-8}"
CONC="${CONC:-4}"
echo "Scraping with depth=$DEPTH concurrency=$CONC ..."
MSYS_NO_PATHCONV=1 docker run --rm \
  -v gmaps-playwright-cache:/opt \
  -v "$(pwd -W)/queries.txt:/queries.txt:ro" \
  -v "$(pwd -W)/out:/out" \
  gosom/google-maps-scraper \
  -input /queries.txt -results /out/results.json -json \
  -depth "$DEPTH" -c "$CONC" -lang en -exit-on-inactivity 4m
echo "Done -> tools/gmaps-io/out/results.json"
echo "Now run:  python pipeline/convert_scraped.py"
