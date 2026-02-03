# WoM New Openings Finder (Helsinki)

This repo contains a small, automated pipeline to discover **recent restaurant openings** in a given city using **OpenStreetMap (Overpass API)** and output them as CSV.

## Why this approach
- **Automated**: Overpass provides structured data (name, address, tags) in a machine-friendly format.
- **Explainable**: We can trace each result back to OSM tags like `opening_date` or `start_date`.
- **Extensible**: Additional providers (Google Places, Yelp, local food blogs) can be layered in later.

## Usage

```bash
python wom_new_openings.py --city Helsinki --months 6 --output data/helsinki_openings.csv
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
- `source`

## Notes
- Only OSM elements with `opening_date` or `start_date` are included.
- If OSM lacks address fields, reverse geocoding can fill them, but is rate-limited.
- If you want to broaden coverage, add a second provider (e.g., Google Places API) and merge results.
