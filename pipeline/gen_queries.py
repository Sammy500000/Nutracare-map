#!/usr/bin/env python3
"""Generate a COMPREHENSIVE Google-Maps-scraper query set for Maharashtra.

Coverage strategy (Google caps results per text search, so we vary BOTH keyword and place):
  keywords  : the facility categories requested (maternity, children, govt, private,
              natal/neonatal, infant, yoga) + strong synonyms
  locations : 35 districts + metro suburbs + taluka/major towns  (dense urban areas,
              where most hospitals are, get the finest granularity)

Output: tools/gmaps-io/queries.txt   (keywords x locations, deduplicated)
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ---- Keywords: every category the user asked for + synonyms that surface more places ----
KEYWORDS = [
    "hospital",
    "maternity hospital",
    "maternity home",
    "nursing home",
    "children hospital",
    "pediatric hospital",
    "government hospital",
    "civil hospital",
    "rural hospital primary health centre",
    "private hospital",
    "multispeciality hospital",
    "neonatal NICU hospital",
    "newborn infant care centre",
    "natal care clinic",
    "gynecologist obstetrician clinic",
    "yoga centre",
]

# ---- Districts (broad sweep) ----
DISTRICTS = []
dj = json.load(open(os.path.join(ROOT, "data", "districts.geojson"), encoding="utf-8"))
for f in dj["features"]:
    n = (f["properties"].get("name") or "").replace("(City + Suburban)", "").strip()
    if n:
        DISTRICTS.append(n)

# ---- Metro suburbs + major towns/talukas (the dense areas, curated) ----
MUMBAI = ["Mumbai", "Andheri", "Bandra", "Borivali", "Dadar", "Kurla", "Ghatkopar", "Malad",
          "Goregaon", "Mulund", "Chembur", "Sion", "Worli", "Vile Parle", "Santacruz", "Powai",
          "Bhandup", "Kandivali", "Dahisar", "Wadala", "Byculla", "Jogeshwari", "Vikhroli",
          "Mira Road", "Bhayandar", "Colaba", "Grant Road", "Mumbai Central"]
THANE = ["Thane", "Kalyan", "Dombivli", "Ulhasnagar", "Bhiwandi", "Ambernath", "Badlapur",
         "Mira-Bhayandar", "Vasai", "Virar", "Nalasopara", "Mumbra", "Kalwa"]
NAVIMUMBAI = ["Navi Mumbai", "Vashi", "Nerul", "Panvel", "CBD Belapur", "Kharghar", "Airoli",
              "Kopar Khairane", "Sanpada", "Ghansoli", "Taloja", "Kamothe", "Ulwe"]
PUNE = ["Pune", "Pimpri", "Chinchwad", "Hadapsar", "Kothrud", "Hinjewadi", "Wakad", "Aundh",
        "Baner", "Kharadi", "Viman Nagar", "Katraj", "Wagholi", "Chakan", "Talegaon", "Lonavala",
        "Baramati", "Khadki", "Bhosari", "Yerwada", "Shivajinagar", "Camp", "Wanowrie", "Pimple Saudagar"]
NAGPUR = ["Nagpur", "Kamptee", "Hingna", "Katol", "Ramtek", "Wadi", "Manish Nagar", "Dharampeth"]
NASHIK = ["Nashik", "Nashik Road", "Malegaon", "Manmad", "Sinnar", "Igatpuri", "Deolali", "Satpur", "Cidco Nashik"]
AURANGABAD = ["Chhatrapati Sambhajinagar", "Aurangabad", "Jalna", "Paithan", "Gangapur", "Cidco Aurangabad"]
# District HQs + notable towns across the rest of the state
OTHER_TOWNS = [
    "Solapur", "Kolhapur", "Sangli", "Miraj", "Ichalkaranji", "Satara", "Karad", "Ahmednagar",
    "Shirdi", "Sangamner", "Kopargaon", "Latur", "Nanded", "Amravati", "Akola", "Jalgaon",
    "Bhusawal", "Dhule", "Nandurbar", "Chandrapur", "Gondia", "Bhandara", "Gadchiroli", "Wardha",
    "Yavatmal", "Washim", "Buldhana", "Khamgaon", "Hingoli", "Parbhani", "Beed", "Osmanabad",
    "Dharashiv", "Barshi", "Pandharpur", "Ratnagiri", "Chiplun", "Sindhudurg", "Kankavli", "Sawantwadi",
    "Alibag", "Pen", "Mahad", "Roha", "Palghar", "Boisar", "Dahanu", "Wai", "Mahabaleshwar",
    "Phaltan", "Pandharpur", "Akluj", "Tasgaon", "Vita", "Gadhinglaj", "Malkapur", "Udgir",
    "Ambajogai", "Gangakhed", "Basmath", "Pusad", "Wani", "Achalpur", "Anjangaon", "Daryapur",
    "Shegaon", "Chikhli", "Mehkar", "Lonar", "Risod", "Pathri", "Selu", "Jintur", "Kalamnuri",
    "Deglur", "Biloli", "Mukhed", "Kandhar", "Hadgaon", "Kinwat", "Mangrulpir", "Karanja",
    "Murtizapur", "Balapur", "Patur", "Telhara", "Warora", "Bhadravati", "Chimur", "Brahmapuri",
    "Mul", "Ballarpur", "Rajura", "Sironcha", "Aheri", "Armori", "Desaiganj", "Tumsar", "Sakoli",
    "Pauni", "Tiroda", "Arjuni Morgaon", "Hinganghat", "Deoli", "Arvi", "Pulgaon", "Umred",
    "Saoner", "Mowad", "Narkhed", "Bhiwapur", "Kuhi", "Parshivni",
]

LOCATIONS = []
seen = set()
# Metros first (highest hospital density) so partial runs still capture the bulk.
for grp in (MUMBAI, THANE, NAVIMUMBAI, PUNE, NAGPUR, NASHIK, AURANGABAD, OTHER_TOWNS, DISTRICTS):
    for loc in grp:
        k = loc.lower()
        if k not in seen:
            seen.add(k); LOCATIONS.append(loc)

lines = [f"{kw} in {loc} Maharashtra India" for loc in LOCATIONS for kw in KEYWORDS]
out = os.path.join(ROOT, "tools", "gmaps-io", "queries.txt")
open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print(f"wrote {len(lines)} queries ({len(LOCATIONS)} locations x {len(KEYWORDS)} keywords) -> {out}")
