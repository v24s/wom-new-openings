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
import urllib.error

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
GOOGLE_PLACES_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
# PRH BIS base URLs (try multiple, API has changed over time; some legacy endpoints are HTTP)
PRH_BIS_BASE_URLS = [
    "https://avoindata.prh.fi/opendata/bis/v1",
    "http://avoindata.prh.fi/opendata/bis/v1",
    "https://avoindata.prh.fi/bis/v1",
    "http://avoindata.prh.fi/bis/v1",
    "https://avoindata.prh.fi/ytj/v1",
    "http://avoindata.prh.fi/ytj/v1",
    "http://avoindata.prh.fi/tr/v1",
]
PRH_SWAGGER_URLS = [
    "https://avoindata.prh.fi/en/ytj/swagger-ui",
    "https://avoindata.prh.fi/fi/ytj/swagger-ui",
    "https://avoindata.prh.fi/sv/ytj/swagger-ui",
]
PRH_SEARCH_PATH_CANDIDATES = ["", "/companies", "/company", "/companies/search"]
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
out center tags meta;
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


def prh_get_json(url: str, params: Optional[Dict[str, str]] = None) -> Dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def prh_get_text(url: str) -> str:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def prh_join(base: str, path: str) -> str:
    if not path:
        return base
    return urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def prh_discover_openapi_url(swagger_url: str) -> Optional[str]:
    html = prh_get_text(swagger_url)
    # Common Swagger UI config pattern: url: "..."
    match = re.search(r'url\\s*:\\s*["\\\']([^"\\\']+)["\\\']', html)
    if match:
        return urllib.parse.urljoin(swagger_url, match.group(1))

    # Alternate pattern: "urls": [{"url": "..."}]
    match = re.search(r'"urls"\\s*:\\s*\\[\\s*\\{\\s*"url"\\s*:\\s*"([^"]+)"', html)
    if match:
        return urllib.parse.urljoin(swagger_url, match.group(1))

    return None


def prh_openapi_base_and_paths(openapi_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if not openapi_url.lower().endswith((".json", ".yaml", ".yml")):
        return None, None, None

    text = prh_get_text(openapi_url)
    try:
        spec = json.loads(text)
    except json.JSONDecodeError:
        return None, None, None

    base_url = None
    servers = spec.get("servers") or []
    if servers and isinstance(servers, list):
        base_url = servers[0].get("url")
    if base_url and base_url.startswith("/"):
        base_url = urllib.parse.urljoin(openapi_url, base_url)
    if not base_url:
        base_url = openapi_url.rsplit("/", 1)[0]

    search_path = None
    company_path = None
    paths = spec.get("paths") or {}
    for path, methods in paths.items():
        get_op = methods.get("get") if isinstance(methods, dict) else None
        if not get_op:
            continue
        params = get_op.get("parameters") or []
        param_names = {p.get("name") for p in params if isinstance(p, dict)}
        if "companyRegistrationFrom" in param_names or "companyRegistrationTo" in param_names:
            search_path = path
        if "{businessId}" in path:
            company_path = path
    return base_url, search_path, company_path


def prh_guess_company_path(search_path: str) -> str:
    if search_path.endswith("/companies") or search_path.endswith("/companies/search"):
        return "/companies/{businessId}"
    return "/{businessId}"


def prh_pick_language(items: List[Dict], key: str) -> Optional[str]:
    if not items:
        return None
    for lang in ("en", "fi", "sv"):
        for item in items:
            if item.get("language") == lang and item.get(key):
                return item.get(key)
    for item in items:
        if item.get(key):
            return item.get(key)
    return None


def prh_build_address(addresses: List[Dict]) -> str:
    if not addresses:
        return ""
    preferred = None
    for addr in addresses:
        addr_type = (addr.get("type") or "").lower()
        if addr_type in {"vis", "visit", "visiting", "postal"}:
            preferred = addr
            break
    if preferred is None:
        preferred = addresses[0]

    street = preferred.get("street") or preferred.get("streetAddress") or ""
    post_code = preferred.get("postCode") or preferred.get("postcode") or ""
    city = preferred.get("city") or ""
    country = preferred.get("country") or preferred.get("countryName") or ""

    parts = []
    if street:
        parts.append(street)
    if post_code and city:
        parts.append(f"{post_code} {city}")
    elif city:
        parts.append(city)
    if country:
        parts.append(country)
    return ", ".join(parts)


def prh_resolve_base_url(
    cutoff: dt.date,
    today: dt.date,
    registered_office: str,
    business_line_code: Optional[str],
) -> Tuple[str, str, str]:
    probe_params: Dict[str, str] = {
        "companyRegistrationFrom": cutoff.isoformat(),
        "companyRegistrationTo": today.isoformat(),
        "maxResults": "1",
        "resultsFrom": "0",
        "totalResults": "false",
    }
    if registered_office:
        probe_params["registeredOffice"] = registered_office
    if business_line_code:
        probe_params["businessLineCode"] = business_line_code

    last_err: Optional[Exception] = None

    # Try to discover via Swagger UI first
    for swagger_url in PRH_SWAGGER_URLS:
        try:
            openapi_url = prh_discover_openapi_url(swagger_url)
            if not openapi_url:
                continue
            base_url, search_path, company_path = prh_openapi_base_and_paths(openapi_url)
            if base_url and search_path:
                return base_url, search_path, (company_path or prh_guess_company_path(search_path))
        except Exception as err:  # noqa: BLE001
            last_err = err
            continue

    # Fallback: brute force base + common path candidates
    for base_url in PRH_BIS_BASE_URLS:
        for path in PRH_SEARCH_PATH_CANDIDATES:
            try:
                prh_get_json(prh_join(base_url, path), params=probe_params)
                return base_url, (path or ""), prh_guess_company_path(path or "")
            except urllib.error.HTTPError as err:
                last_err = err
                if err.code == 404:
                    continue
                return base_url, (path or ""), prh_guess_company_path(path or "")
            except Exception as err:  # noqa: BLE001
                last_err = err
                continue

    raise RuntimeError(f"PRH BIS base URL not found. Last error: {last_err}")


def prh_fetch_companies(
    base_url: str,
    search_path: str,
    cutoff: dt.date,
    today: dt.date,
    registered_office: str,
    business_line_codes: List[str],
    page_size: int,
    max_results: int,
) -> List[Dict]:
    results: List[Dict] = []
    codes = business_line_codes or [""]

    for code in codes:
        offset = 0
        while True:
            params: Dict[str, str] = {
                "companyRegistrationFrom": cutoff.isoformat(),
                "companyRegistrationTo": today.isoformat(),
                "maxResults": str(page_size),
                "resultsFrom": str(offset),
                "totalResults": "false",
            }
            if registered_office:
                params["registeredOffice"] = registered_office
            if code:
                params["businessLineCode"] = code

            payload = prh_get_json(prh_join(base_url, search_path), params=params)
            batch = payload.get("results", []) or []
            if not batch:
                break
            results.extend(batch)
            offset += len(batch)
            if max_results and len(results) >= max_results:
                return results[:max_results]
            if len(batch) < page_size:
                break

    return results


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
    parser.add_argument(
        "--prh-bis",
        action="store_true",
        help="Include PRH BIS companies registered in the lookback window (registration date as opening date)",
    )
    parser.add_argument(
        "--prh-base-url",
        default="",
        help="Override PRH BIS base URL if auto-discovery fails",
    )
    parser.add_argument(
        "--prh-registered-office",
        default="Helsinki",
        help="PRH BIS registered office filter (default: Helsinki)",
    )
    parser.add_argument(
        "--prh-business-line-codes",
        default="56",
        help="Comma-separated PRH BIS business line codes (default: 56)",
    )
    parser.add_argument(
        "--prh-page-size",
        type=int,
        default=50,
        help="PRH BIS page size (default: 50)",
    )
    parser.add_argument(
        "--prh-max-results",
        type=int,
        default=200,
        help="PRH BIS max results to fetch (default: 200)",
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
                "osm_last_edit": el.get("timestamp", ""),
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
                            "osm_last_edit": "",
                            "source": "Google Places (Text Search)",
                        }
                    )

    if args.prh_bis:
        business_line_codes = [
            c.strip() for c in args.prh_business_line_codes.split(",") if c.strip()
        ]
        if args.strict_restaurants:
            # Keep narrow by default when strict
            business_line_codes = ["56"]

        try:
            if args.prh_base_url:
                base_url = args.prh_base_url
                search_path = ""
                company_path = prh_guess_company_path(search_path)
            else:
                base_url, search_path, company_path = prh_resolve_base_url(
                    cutoff=cutoff,
                    today=today,
                    registered_office=args.prh_registered_office,
                    business_line_code=business_line_codes[0] if business_line_codes else None,
                )
            print(f"Using PRH BIS endpoint: {base_url}{search_path}", file=sys.stderr)
            companies = prh_fetch_companies(
                base_url=base_url,
                search_path=search_path,
                cutoff=cutoff,
                today=today,
                registered_office=args.prh_registered_office,
                business_line_codes=business_line_codes,
                page_size=args.prh_page_size,
                max_results=args.prh_max_results,
            )
        except Exception as err:  # noqa: BLE001
            print(f"PRH BIS error: {err}", file=sys.stderr)
            companies = []

        for company in companies:
            business_id = company.get("businessId", "")
            name = company.get("name", "") or ""
            registration_date = company.get("registrationDate", "") or ""

            details = {}
            if business_id and company_path:
                try:
                    details = prh_get_json(
                        prh_join(base_url, company_path.format(businessId=business_id))
                    )
                except Exception:
                    details = {}

            addresses = details.get("addresses") or company.get("addresses") or []
            address = prh_build_address(addresses)

            business_lines = details.get("businessLines") or []
            business_line = prh_pick_language(business_lines, "businessLine") or ""
            business_line_code = prh_pick_language(business_lines, "businessLineCode") or ""

            tag_list = ["source:prh_bis", "confidence:medium"]
            if business_line:
                tag_list.append(f"business_line:{business_line}")
            if business_line_code:
                tag_list.append(f"business_line_code:{business_line_code}")
            if business_id:
                tag_list.append(f"business_id:{business_id}")

            key = normalize_key(name, address or business_id)
            if key in seen:
                continue
            seen.add(key)

            rows.append(
                {
                    "name": name,
                    "full_address": address,
                    "description": business_line or "PRH BIS registered company",
                    "tags": ";".join(sorted(set(tag_list))),
                    "opening_date": registration_date,
                    "osm_last_edit": "",
                    "source": "PRH BIS (registration date)",
                }
            )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "name",
                "full_address",
                "description",
                "tags",
                "opening_date",
                "osm_last_edit",
                "source",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
