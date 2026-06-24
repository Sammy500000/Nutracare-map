@echo off
REM Run the Google Maps scraper (Docker) over tools\gmaps-io\queries.txt
REM First: python pipeline\gen_queries.py
cd /d "%~dp0gmaps-io"
if "%DEPTH%"=="" set DEPTH=8
if "%CONC%"=="" set CONC=4
echo Scraping with depth=%DEPTH% concurrency=%CONC% ...
docker run --rm ^
  -v gmaps-playwright-cache:/opt ^
  -v "%cd%\queries.txt:/queries.txt:ro" ^
  -v "%cd%\out:/out" ^
  gosom/google-maps-scraper ^
  -input /queries.txt -results /out/results.json -json ^
  -depth %DEPTH% -c %CONC% -lang en -exit-on-inactivity 4m
echo Done. Now run: python pipeline\convert_scraped.py
