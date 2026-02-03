#!/usr/bin/env python3
"""
Find new restaurant openings for a city using OpenStreetMap (Overpass API)
with optional reverse geocoding via Nominatim.

Outputs CSV with: name, full_address, description, tags, opening_date, source
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
from typing import Dict, Iterable, List, Optional, Tuple

import urllib.parse
import urllib.request

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

AMENITY_PATTERN = re.compile(r"^(restaurant|cafe|fast_food)$", re.IGNORECASE)

DATE_PATTERNS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y-%m",
    "%Y/%m",
    "%Y.%m",
    "%Y",
]


def subtract_months(date: dt.date, months: int) -> dt.date:
    """Subtract months from a date without external deps."""
    year = date.year
    month = date.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(date.day, _last_day_of_month(year, month))
    return dt.date(year, month, day)


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        next_month = dt.date(year + 1, 1, 1)
    else:
        next_month = dt.date(year, month + 1, 1)
    return (next_month - dt.timedelta(days=1)).day


def parse_opening_date(raw: str) -> Optional[dt.date]:
    raw = raw.strip()
    if not raw:
        return None

    # Normalize common ISO-like formats with time.
    raw = raw.replace("T", " ").split(" ")[0]

    for fmt in DATE_PATTERNS:
        try:
            parsed = dt.datetime.strptime(raw, fmt).date()
            return parsed
        except ValueError:
            continue

    # Handle ranges like "2025-06-01/2025-06-30" by taking start.
    if "/" in raw:
        start = raw.split("/")[0].strip()
        for fmt in DATE_PATTERNS:
            try:
                return dt.datetime.strptime(start, fmt).date()
            except ValueError:
                continue

    return None


def overpass_query(city: str) -> str:
    return f"""
[out:json][timeout:180];
area["name"="{city}"]["boundary"="administrative"]["admin_level"="8"]->.searchArea;
(
  nwr["amenity"~"^(restaurant|cafe|fast_food)$"]["opening_date"](area.searchArea);
  nwr["amenity"~"^(restaurant|cafe|fast_food)$"]["start_date"](area.searchArea);
);
out center tags;
"""


def fetch_overpass(city: str) -> Dict:
    query = overpass_query(city)
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(OVERPASS_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def reverse_geocode(lat: float, lon: float, user_agent: str) -> Optional[str]:
    params = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "jsonv2"})
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": user_agent},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("display_name")


def build_address(tags: Dict[str, str]) -> Optional[str]:
    if "addr:full" in tags:
        return tags["addr:full"].strip()

    parts = []
    street = tags.get("addr:street")
    housenumber = tags.get("addr:housenumber")
    if street and housenumber:
        parts.append(f"{street} {housenumber}")
    elif street:
        parts.append(street)

    postcode = tags.get("addr:postcode")
    city = tags.get("addr:city")
    if postcode and city:
        parts.append(f"{postcode} {city}")
    elif city:
        parts.append(city)

    country = tags.get("addr:country")
    if country:
        parts.append(country)

    if parts:
        return ", ".join(parts)
    return None


def build_tags(tags: Dict[str, str]) -> List[str]:
    tag_list = []

    amenity = tags.get("amenity")
    if amenity:
        tag_list.append(amenity)

    cuisine = tags.get("cuisine")
    if cuisine:
        for item in re.split(r"[;,_]", cuisine):
            item = item.strip()
            if item:
                tag_list.append(f"cuisine:{item}")

    for key in ["outdoor_seating", "delivery", "takeaway", "vegetarian", "vegan"]:
        val = tags.get(key)
        if val in ("yes", "no"):
            tag_list.append(f"{key}:{val}")

    # Include diet tags, e.g., diet:vegetarian=yes
    for k, v in tags.items():
        if k.startswith("diet:") and v:
            tag_list.append(f"{k}:{v}")

    return sorted(set(tag_list))


def extract_elements(payload: Dict) -> Iterable[Dict]:
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        if not tags:
            continue
        amenity = tags.get("amenity", "")
        if not AMENITY_PATTERN.match(amenity):
            continue
        yield el


def format_description(tags: Dict[str, str]) -> Optional[str]:
    # Prefer description tags if present.
    for key in ["description", "description:en", "short_description", "note"]:
        if key in tags and tags[key].strip():
            return tags[key].strip()

    # Fallback to concise descriptor from name + cuisine.
    cuisine = tags.get("cuisine")
    if cuisine:
        return f"{cuisine.replace(';', ', ')} cuisine"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Find new restaurant openings from OSM (Overpass).")
    parser.add_argument("--city", default="Helsinki", help="City name (default: Helsinki)")
    parser.add_argument("--months", type=int, default=6, help="Look back period in months (default: 6)")
    parser.add_argument("--output", default="data/helsinki_openings.csv", help="Output CSV path")
    parser.add_argument(
        "--reverse-geocode",
        action="store_true",
        help="Use Nominatim reverse geocoding to fill missing addresses (rate-limited)",
    )
    parser.add_argument(
        "--nominatim-user-agent",
        default=os.environ.get("NOMINATIM_USER_AGENT", "wom-new-openings-script"),
        help="User-Agent for Nominatim requests",
    )
    args = parser.parse_args()

    today = dt.date.today()
    cutoff = subtract_months(today, args.months)

    payload = fetch_overpass(args.city)
    rows = []

    for el in extract_elements(payload):
        tags = el.get("tags", {})
        opening_raw = tags.get("opening_date") or tags.get("start_date")
        if not opening_raw:
            continue

        opening_date = parse_opening_date(opening_raw)
        if not opening_date or opening_date < cutoff:
            continue

        name = tags.get("name") or ""
        address = build_address(tags)

        if not address and args.reverse_geocode:
            lat = el.get("lat") or el.get("center", {}).get("lat")
            lon = el.get("lon") or el.get("center", {}).get("lon")
            if lat is not None and lon is not None:
                try:
                    address = reverse_geocode(lat, lon, args.nominatim_user_agent)
                    # Be polite with rate limits.
                    time.sleep(1)
                except Exception:
                    address = None

        description = format_description(tags)
        tag_list = build_tags(tags)

        rows.append(
            {
                "name": name,
                "full_address": address or "",
                "description": description or "",
                "tags": ";".join(tag_list),
                "opening_date": opening_date.isoformat(),
                "source": "OpenStreetMap",
            }
        )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "full_address", "description", "tags", "opening_date", "source"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
