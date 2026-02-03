# Thought Process

## Goal
Find restaurants opened in the last 6 months in Helsinki, and output a CSV with:
- restaurant name
- full address
- description
- tags (cuisine, venue type, features, etc.)

## Approach
1. **Use OpenStreetMap (OSM) via Overpass API**
   - OSM exposes structured venue data (amenity, cuisine, address, opening date).
   - Overpass allows queries scoped to a city boundary.
   - We can filter on `opening_date` or `start_date`, then keep only entries within the last 6 months.

2. **Normalize the output**
   - Build a full address from `addr:*` tags.
   - Derive a short description from `description` or `cuisine` tags.
   - Collect tags such as `amenity`, `cuisine`, `delivery`, `takeaway`, `diet:*`.

3. **Optional reverse geocoding**
   - If an address is missing, the script can call Nominatim to fill a display address.
   - This is optional because of rate limits, but makes the output closer to “full address.”

## Why this is automated enough for the assignment
- The script is fully automated with no manual steps besides running it.
- Data comes from an API, not a hand-curated list.
- It is easy to schedule (cron or GitHub Actions) for fresh weekly/monthly runs.

## Limitations and next steps
- Not all venues in OSM have `opening_date` tags; results may be incomplete.
- To improve recall, I would add additional providers:
  - Google Places API (if a key is available)
  - Yelp Fusion API (if available)
  - Local food/restaurant listings with RSS or structured HTML
- Then deduplicate results by name + address and rank by confidence.

## Deliverables
- Script: `wom_new_openings.py`
- Usage: `README.md`
- This document: `THOUGHT_PROCESS.md`
