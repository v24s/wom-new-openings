# WoM New Openings Finder (Helsinki)

This repo contains an automated pipeline to discover **recent restaurant openings** in a given city using **OpenStreetMap (Overpass API)**, plus optional **Google Places** candidates for broader coverage.

## Why this approach
- **Automated**: Overpass provides structured data (name, address, tags) in a machine-friendly format.
- **Explainable**: Results trace back to OSM tags like `opening_date` or `start_date`.
- **Extensible**: Google Places candidates provide broader discovery; additional providers can be layered in later.

## Usage

Basic (OSM with opening_date / start_date only):

```bash
python wom_new_openings.py --city Helsinki --months 6 --output data/helsinki_openings.csv
```

OSM with "recently edited" proxy (lower confidence but higher recall):

```bash
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --output data/helsinki_openings.csv
```

Restaurant-only filtering (stricter, excludes cafes/fast food):

```bash
python wom_new_openings.py --city Helsinki --months 6 --strict-restaurants --output data/helsinki_openings.csv
```

OSM + Google Places (requires key):

```bash
export GOOGLE_PLACES_API_KEY="your_key_here"
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --google-places --output data/helsinki_openings.csv
```

OSM + Google Places, restaurant-only:

```bash
export GOOGLE_PLACES_API_KEY="your_key_here"
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --google-places --strict-restaurants --output data/helsinki_openings.csv
```

OSM + PRH BIS (official registration dates as opening-date proxy):

```bash
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --prh-bis --output data/helsinki_openings.csv
```

If PRH auto-discovery fails, you can override the base URL (examples below):

```bash
python wom_new_openings.py --city Helsinki --months 6 --prh-bis --prh-base-url http://avoindata.prh.fi/opendata/bis/v1 --output data/helsinki_openings.csv
```

OSM + Google Places + PRH BIS (best coverage):

```bash
export GOOGLE_PLACES_API_KEY="your_key_here"
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --google-places --prh-bis --output data/helsinki_openings.csv
```

One-command run (recommended, includes reverse geocoding):

```bash
export GOOGLE_PLACES_API_KEY="your_key_here"
./run.sh
```

Optional reverse geocoding for missing addresses:

```bash
python wom_new_openings.py --city Helsinki --months 6 --reverse-geocode --nominatim-user-agent "wom-team" --output data/helsinki_openings.csv
```

## Output
CSV columns:
- `name`
- `full_address`
- `description`
- `tags`
- `opening_date`
- `osm_last_edit`
- `osm_last_edit_age_days`
- `source`

## Notes
- OSM `opening_date` results are **high confidence**.
- OSM `newer` proxy results are **medium confidence** (recently edited, not guaranteed to be new openings).
- Google Places results are **low confidence** (candidates without opening dates) but expand coverage.
- Google Places uses a location bias around Helsinki and a wider set of queries to increase recall.
- PRH BIS results use company **registration date** as a proxy for opening date (official, but not perfect).
