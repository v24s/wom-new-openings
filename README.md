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

OSM + Google Places + Place Details (richer tags, higher cost):

```bash
export GOOGLE_PLACES_API_KEY="your_key_here"
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --google-places --google-details --output data/helsinki_openings.csv
```

Limit Place Details calls (reduce cost):

```bash
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --google-places --google-details --google-details-limit 50 --output data/helsinki_openings.csv
```

OSM + Google Places, restaurant-only:

```bash
export GOOGLE_PLACES_API_KEY="your_key_here"
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --google-places --strict-restaurants --output data/helsinki_openings.csv
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

OSM history (adds `osm_first_added`, slow and rate-limited):

```bash
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --strict-restaurants --osm-history --output data/helsinki_openings.csv
```

Google first-seen + earliest review (proxy added dates):

```bash
export GOOGLE_PLACES_API_KEY="your_key_here"
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --google-places --google-details --output data/helsinki_openings.csv
```
Filter to first-added in 2025 or later:

```bash
python wom_new_openings.py --city Helsinki --months 6 --use-newer-proxy --strict-restaurants --osm-history --min-first-added 2025-01-01 --output data/helsinki_openings.csv
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
- `osm_first_added`
- `google_place_id`
- `google_first_seen`
- `google_first_review_date`
- `source`

## Notes
- OSM `opening_date` results are **high confidence**.
- OSM `newer` proxy results are **medium confidence** (recently edited, not guaranteed to be new openings).
- Google Places results are **low confidence** (candidates without opening dates) but expand coverage.
- Google Places uses a location bias around Helsinki and a wider set of queries to increase recall.

## Scaling Automation (Quality Filter)
Use `quality_filter.py` to classify large recommendation datasets into Keep / Remove / Needs more information / Needs editing.

Example:

```bash
python quality_filter.py --input data/helsinki_openings.csv --output data/helsinki_openings_quality.csv
```

Optional LLM batch export:

```bash
python quality_filter.py --input data/helsinki_openings.csv --output data/helsinki_openings_quality.csv --emit-llm-batch data/llm_batch.jsonl
```
