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
   - We filter on `opening_date` or `start_date`, then keep only entries within the last 6 months.

2. **Increase recall with an OSM "recently edited" proxy**
   - Many venues lack `opening_date` tags, so we optionally include venues edited in the last 6 months.
   - These are marked as medium confidence, since "recently edited" does not always equal "recently opened."

3. **Add Google Places candidates**
   - Google Places (Text Search) gives wider discovery, especially for newer venues.
   - A location bias around Helsinki and broader query set improves recall.
   - These results do not include opening dates, so they are labeled low confidence and treated as candidates.

4. **Normalize the output**
   - Build a full address from `addr:*` tags or use optional reverse geocoding.
   - Derive a short description from `description` or `cuisine` tags.
   - Collect tags such as `amenity`, `cuisine`, `delivery`, `takeaway`, `diet:*`, plus confidence labels.

## Why this is automated enough for the assignment
- The script is fully automated with no manual steps besides running it.
- Data comes from APIs, not a hand-curated list.
- It is easy to schedule (cron or GitHub Actions) for fresh weekly/monthly runs.

## Limitations and next steps
- OSM opening dates are incomplete; proxy results are heuristic.
- Google Places requires an API key and billing.
- Next step: add another source (e.g., Yelp) and deduplicate/rank results by confidence.

## Deliverables
- Script: `wom_new_openings.py`
- Usage: `README.md`
- This document: `THOUGHT_PROCESS.md`
