# Data Validation Report

**Validator:** `pipeline/validate_data.py` — an INDEPENDENT cross-check that re-reads the
source spreadsheets from scratch (does not import the build pipeline) and compares every value
on the map against its source. Re-run anytime with:

```
python pipeline/validate_data.py
```

## Current status: ✅ PASS — 952/952 checks

Every value displayed on the map matches the value in the source sheet / official dataset.

## What is checked
| # | Layer | Source of truth | Result |
|---|---|---|---|
| 1 | Income brackets (EWS→Ultra-HIG), PCI, population, women 20–35, households | `Maharashtra (Females).xlsx` → *District Income Brackets Full* | ✅ all districts match; brackets sum to 100% |
| 2 | Literacy | `Maharashtra (Females).xlsx` → *Population %* | ✅ match (Mumbai population-weighted) |
| 3 | Poshan: beneficiaries, pregnant, lactating, stunting/wasting/underweight % | `poshan_tracker_district_wise.xlsx` (Govt MWCD) | ✅ match |
| 4 | Political winners (party) per Assembly seat | `mh_2024_results.csv` (ECI, candidate-level) | ✅ all 288 ACs match |
| 5 | Hospitals (3,391) | Google Maps via gosom/google-maps-scraper | ✅ all have exact coords, `source:google_maps`, district matches point-in-polygon, no duplicate place_ids |
| 6 | Mumbai merge arithmetic (City + Suburban → one polygon) | derived | ✅ sums + weighted averages verified |

## Bug caught & fixed during validation
- **Mumbai Poshan undercount.** The Poshan sheet lists *Mumbai City* and *Mumbai Suburban*
  as two rows. The first build let one overwrite the other, so Mumbai showed only ~288k
  beneficiaries. Fixed to **sum counts** and **beneficiary-weight percentages**, giving the
  correct combined totals. Literacy for Mumbai is now population-weighted too.

## Honest notes on accuracy (no fabrication)
- **Income brackets are MODELED ESTIMATES**, not individual records — there is no public
  household-income register in India. They come straight from your own sheet's methodology
  (Census 2011 + SECC + NFHS-5 + PMAY/RBI/tax bands) and are labelled "estimated" in the UI.
- **Hospitals:** 3,391 real Google Maps listings with EXACT coordinates (482 Government,
  1,924 Private, 985 Maternity/Neonatal). District is assigned by point-in-polygon on the real
  coordinates; type is classified from Google's own category + the place name (raw category kept).
  15 results outside Maharashtra were dropped. Nothing about a hospital is invented.
- All counts/percentages are reproduced exactly as in the sheets; the pipeline performs no
  invented numbers — only documented merges (Mumbai) and the PCI/population join.
