#!/usr/bin/env python3
"""
Find new restaurant openings for a city using OpenStreetMap (Overpass API)
with optional reverse geocoding via Nominatim and Google Places (New).

Outputs CSV with: name, full_address, description, tags, opening_date, source
"""

import argparse
import csv
import datetime as dt
import math
import json
import os
import re
import sys
import time
from typing import Dict, Iterable, List, Optional

import urllib.parse
import urllib.request

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
GOOGLE_PLACES_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
HELSINKI_CENTER = (60.1699, 24.9384)
HELSINKI_RADIUS_KM = 30

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


def amenity_regex(amenities: List[str]) -> str:
    safe = [re.escape(a.strip()) for a in amenities if a.strip()]
    if not safe:
        return "restaurant"
    return "|".join(safe)


def overpass_query(city: str, cutoff: dt.date, use_newer_proxy: bool, amenity_re: str) -> str:
    parts = [
        f'nwr["amenity"~"^({amenity_re})$"]["opening_date"](area.searchArea);',
        f'nwr["amenity"~"^({amenity_re})$"]["start_date"](area.searchArea);',
    ]
    if use_newer_proxy:
        newer_clause = f'(newer:"{cutoff.isoformat()}T00:00:00Z")'
        parts.append(
            f'nwr["amenity"~"^({amenity_re})$"]{newer_clause}(area.searchArea);'
        )

    parts_block = "\n  ".join(parts)

    return f"""
[out:json][timeout:180];
area["name"="{city}"]["boundary"="administrative"]["admin_level"="8"]->.searchArea;
(
  {parts_block}
);
out center tags;
"""


def fetch_overpass(city: str, cutoff: dt.date, use_newer_proxy: bool, amenity_re: str) -> Dict:
    query = overpass_query(city, cutoff, use_newer_proxy, amenity_re)
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")

    last_err = None
    for url in OVERPASS_URLS:
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as err:  # noqa: BLE001
            last_err = err
            continue

    raise RuntimeError(f"All Overpass endpoints failed. Last error: {last_err}")


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


def extract_elements(payload: Dict, amenity_pattern: re.Pattern[str]) -> Iterable[Dict]:
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        if not tags:
            continue
        amenity = tags.get("amenity", "")
        if not amenity_pattern.match(amenity):
            continue
        yield el


def format_description(tags: Dict[str, str]) -> Optional[str]:
    # Prefer description tags if present.
    for key in ["description", "description:en", "short_description", "note"]:
        if key in tags and tags[key].strip():
            return tags[key].strip()

    # Fallback to concise descriptor from cuisine.
    cuisine = tags.get("cuisine")
    if cuisine:
        return f"{cuisine.replace(';', ', ')} cuisine"
    return None


def google_places_text_search(
    query: str,
    api_key: str,
    language_code: str = "en",
    included_type: Optional[str] = None,
    location_bias: Optional[Dict] = None,
) -> List[Dict]:
    body = {
        "textQuery": query,
        "languageCode": language_code,
        "pageSize": 20,
    }
    if included_type:
        body["includedType"] = included_type
        body["strictTypeFiltering"] = True
    if location_bias:
        body["locationBias"] = location_bias

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.displayName,places.formattedAddress,"
            "places.primaryType,places.types,places.businessStatus,places.location"
        ),
    }

    req = urllib.request.Request(
        GOOGLE_PLACES_TEXT_URL,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    return payload.get("places", [])


def normalize_key(name: str, address: str) -> str:
    key = f"{name}|{address}".strip().lower()
    key = re.sub(r"\s+", " ", key)
    return key


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Radius of Earth in km
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


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
    parser.add_argument(
        "--use-newer-proxy",
        action="store_true",
        help="Include OSM venues recently edited within the lookback window (lower confidence)",
    )
    parser.add_argument(
        "--osm-amenities",
        default="restaurant,cafe,fast_food",
        help="Comma-separated list of OSM amenity types to include (default: restaurant,cafe,fast_food)",
    )
    parser.add_argument(
        "--strict-restaurants",
        action="store_true",
        help="Limit results to restaurants only (excludes cafes/fast_food from all sources)",
    )
    parser.add_argument(
        "--google-places",
        action="store_true",
        help="Include Google Places candidates (requires GOOGLE_PLACES_API_KEY)",
    )
    args = parser.parse_args()

    today = dt.date.today()
    cutoff = subtract_months(today, args.months)

    osm_amenities = [a.strip() for a in args.osm_amenities.split(",") if a.strip()]
    if args.strict_restaurants:
        osm_amenities = ["restaurant"]
    amenity_re = amenity_regex(osm_amenities)
    amenity_pattern = re.compile(rf"^({amenity_re})$", re.IGNORECASE)

    payload = fetch_overpass(args.city, cutoff, args.use_newer_proxy, amenity_re)
    rows = []
    seen = set()

    for el in extract_elements(payload, amenity_pattern):
        tags = el.get("tags", {})
        opening_raw = tags.get("opening_date") or tags.get("start_date")

        opening_date = parse_opening_date(opening_raw) if opening_raw else None

        # If this is from the newer-proxy path, allow missing opening_date
        if opening_date is None and not args.use_newer_proxy:
            continue
        if opening_date and opening_date < cutoff:
            continue

        name = tags.get("name") or ""
        address = build_address(tags) or ""

        if not address and args.reverse_geocode:
            lat = el.get("lat") or el.get("center", {}).get("lat")
            lon = el.get("lon") or el.get("center", {}).get("lon")
            if lat is not None and lon is not None:
                try:
                    address = reverse_geocode(lat, lon, args.nominatim_user_agent) or ""
                    # Be polite with rate limits.
                    time.sleep(1)
                except Exception:
                    address = ""

        description = format_description(tags) or ""
        tag_list = build_tags(tags)

        confidence = "high" if opening_date else "medium"
        tag_list.append(f"confidence:{confidence}")

        key = normalize_key(name, address)
        if key in seen:
            continue
        seen.add(key)

        rows.append(
            {
                "name": name,
                "full_address": address,
                "description": description,
                "tags": ";".join(sorted(set(tag_list))),
                "opening_date": opening_date.isoformat() if opening_date else "",
                "source": "OpenStreetMap",
            }
        )

    if args.google_places:
        api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
        if not api_key:
            print(
                "Warning: GOOGLE_PLACES_API_KEY not set; skipping Google Places.",
                file=sys.stderr,
            )
        else:
            google_allowed_types = {"restaurant", "cafe", "fast_food"}
            google_excluded_types = {
                "bar",
                "pub",
                "night_club",
                "casino",
                "lodging",
                "gas_station",
            }
            if args.strict_restaurants:
                google_allowed_types = {"restaurant"}
                google_excluded_types.update({"cafe", "fast_food"})

            queries = [
                f"restaurant in {args.city}",
                f"cafe in {args.city}",
                f"street food in {args.city}",
                f"new restaurant in {args.city}",
                f"bistro in {args.city}",
                f"food stall in {args.city}",
                f"food court in {args.city}",
            ]
            location_bias = None
            if args.city.lower() == "helsinki":
                location_bias = {
                    "circle": {
                        "center": {"latitude": HELSINKI_CENTER[0], "longitude": HELSINKI_CENTER[1]},
                        "radius": HELSINKI_RADIUS_KM * 1000,
                    }
                }
            for q in queries:
                try:
                    places = google_places_text_search(
                        q,
                        api_key=api_key,
                        language_code="en",
                        included_type=None,
                        location_bias=location_bias,
                    )
                except Exception as err:  # noqa: BLE001
                    print(f"Google Places error for query '{q}': {err}", file=sys.stderr)
                    continue

                for place in places:
                    display = place.get("displayName", {})
                    name = display.get("text", "") if isinstance(display, dict) else ""
                    address = place.get("formattedAddress", "")

                    types = place.get("types", []) or []
                    primary = place.get("primaryType")
                    type_set = set(types)
                    if primary:
                        type_set.add(primary)
                    if type_set & google_excluded_types:
                        continue
                    if not (type_set & google_allowed_types):
                        continue

                    keep = False
                    if address and args.city.lower() in address.lower():
                        keep = True

                    location = place.get("location") or {}
                    lat = location.get("latitude")
                    lon = location.get("longitude")
                    if lat is not None and lon is not None and args.city.lower() == "helsinki":
                        distance = haversine_km(lat, lon, HELSINKI_CENTER[0], HELSINKI_CENTER[1])
                        if distance <= HELSINKI_RADIUS_KM:
                            keep = True

                    if not keep:
                        continue

                    tag_list = ["source:google_places", "confidence:low"]
                    if primary:
                        tag_list.append(f"type:{primary}")
                    for t in types:
                        tag_list.append(f"type:{t}")

                    key = normalize_key(name, address)
                    if key in seen:
                        continue
                    seen.add(key)

                    rows.append(
                        {
                            "name": name,
                            "full_address": address,
                            "description": "Google Places candidate (no opening_date provided)",
                            "tags": ";".join(sorted(set(tag_list))),
                            "opening_date": "",
                            "source": "Google Places (Text Search)",
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
