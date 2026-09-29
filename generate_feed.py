#!/usr/bin/env python3
"""
Rentch XML Feed Generator
Scrapes RentFaster listings for user_ID=358564 and generates
a Tenant Turner / rentch.ca compatible XML feed.

Usage:
    python generate_feed.py

Output:
    rentch_tenant_turner_full_feed.xml        - every active listing
    rentch_tenant_turner_selected_listings.xml - listings in SELECTED_LISTING_IDS
                                                 (or every listing if that list is empty)
    feed_report.md                            - human-readable check of every listing:
                                                 postal code source + booking link status
    postal_lookup_cache.json                  - postal codes found by automatic lookup
"""

import copy
import json
import os
import requests
import time
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom

# ─── CONFIG ────────────────────────────────────────────────────────────────────
RENTFASTER_USER_ID = "358564"
OUTPUT_FILE = "rentch_tenant_turner_full_feed.xml"
SELECTED_OUTPUT_FILE = "rentch_tenant_turner_selected_listings.xml"
REPORT_FILE = "feed_report.md"
POSTAL_CACHE_FILE = "postal_lookup_cache.json"

# Listing IDs to put in the "selected listings" feed.
# Leave EMPTY to make the selected feed an exact copy of the full feed.
# Example: SELECTED_LISTING_IDS = ["771176", "738281"]
SELECTED_LISTING_IDS = []

# Tenant Turner base URL — slugs are auto-generated from street address.
# If a property uses a custom slug, add it here:
#   "710 25 Street NW": "716b-hillside-west-hillhurst"
# Keyed by RentFaster address string OR listing ID (ID takes priority)
TENANT_TURNER_CUSTOM_SLUGS = {
    "930 16 Avenue SW":        "930-16-avenue-southwest-1",
    "930 16 Avenue Southwest": "930-16-avenue-southwest-1",
    "744732":                  "930-16-avenue-southwest-1",   # 930 16 Avenue SW
    "653553":                  "888-4th-ave-sw",              # 888 4th Ave SW - Solaire
    "604744":                  "2419-16th-street-sw-1",       # 2419 16th Street SW - Northumberland Place
    "640415":                  "628-56-avenue-southwest-1",   # 628 56 Avenue SW - 56 Windsor
    "748399":                  "515-17-avenue-nw-1",          # 515 17 Avenue NW - Basement Suite
    "749569":                  "227-26-avenue-ne-1",          # 227 26 Avenue NE
    "602032":                  "new-address-from-sheet",      # 108 23 Ave SW - Brookwood Manor
                                                              # (Tenant Turner property still has a
                                                              #  placeholder name; update here if renamed)
}
TENANT_TURNER_BASE = "https://app.tenantturner.com/qualify/select-time/"
TENANT_TURNER_SUFFIX = "?p=TenantTurner"

# ─── POSTAL CODE LOOKUP (keyed by RentFaster listing ID) ───────────────────────
# Postal codes are not returned by RentFaster API — maintained manually here.
# Add new listings as they come on market.
POSTAL_CODES = {
    "708680":  "T2N 5A7",  # 710 25 Street NW - Hillside Basement Suite
    "721791":  "T2G 0Y8",  # 1107 5th Street NE - 1107 Renfrew 8
    "722646":  "T2R 1S9",  # 310 15 Avenue SW - The Broward
    "702335":  "T2N 5A7",  # 712 25 Street NW - 712 Hillside Townhome
    "1502398": "T2R 0S8",  # 1111 15 Avenue SW - 501 ShyLui
    "519154":  "T3E 4L1",  # 2852 Grant Cres SW - 2852 Grant
    "519155":  "T2E 1Z1",  # 227 26th Ave NE - 101 Tuxedo 8
    "602032":  "T2S 0J1",  # 108 23 Ave SW - Brookwood Manor
    "630443":  "T2R 0V6",  # 215 13th Avenue SW - Union Square
    "604744":  "T2T 1C8",  # 2419 16th Street SW - Northumberland Place
    "640415":  "T2V 0G8",  # 628 56 Avenue SW - 56 Windsor
    "734632":  "T2E 1J6",  # 1107B 5 Street NE - Renfrew 8 Basement Suite
    "736773":  "T2E 1R1",  # 201 20 Avenue NE - 106 Tuxedo Park
    "736774":  "T2E 3T5",  # 1105 4 Street NE - 1105 4 Street NE Townhome
    "653553":  "T2P 0V2",  # 888 4th Ave SW - Solaire
    "738281":  "T2M 0P1",  # 815 17th Ave NW - Pleasant View
    "631595":  "T2G 2L7",  # 1605 17 Street SE - Konekt
    "744732":  "T2R 1C2",  # 930 16 Avenue SW
    "749569":  "T2E 1Z1",  # 227 26 Avenue NE (same address as 519155 above)
    "732110":  "T2E 1Z1",  # 227 26 Avenue NE (same address as 519155 above)
}

# ─── MANUAL OVERRIDES (for listings where RentFaster API returns incomplete data) ──
# Keyed by listing ID. Any field here replaces what the API returns.
LISTING_OVERRIDES = {
    "604744": {
        "price":    "1350",
        "type":     "Apartment",
        "sq_feet":  "850",
        "bedrooms": "1",
        "baths":    "1",
    },
}

# ─── HELPERS ───────────────────────────────────────────────────────────────────

def address_to_slug(address: str) -> str:
    """Convert a street address to a Tenant Turner URL slug."""
    slug = address.lower()
    slug = re.sub(r'\s+', '-', slug)
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug


def build_tenant_turner_url(address: str, listing_id: str = "") -> str:
    """Return the Tenant Turner scheduling URL for a given address/ID."""
    slug = (TENANT_TURNER_CUSTOM_SLUGS.get(listing_id)
            or TENANT_TURNER_CUSTOM_SLUGS.get(address)
            or address_to_slug(address))
    return f"{TENANT_TURNER_BASE}{slug}{TENANT_TURNER_SUFFIX}"


def fetch_rentfaster_listings(user_id: str) -> list:
    """
    Pull all active listings for a given user from RentFaster's API.
    Filters strictly to user_ID and paginates until done.
    """
    all_listings = []
    page = 0
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; RentchFeedGenerator/1.0)"
    }

    print(f"Fetching listings for user_ID={user_id}...")

    while True:
        url = f"https://www.rentfaster.ca/api/search.json?cur_page={page}&user_ID={user_id}"

        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"  Error on page {page}: {e}")
            break

        page_listings = data.get("listings", [])
        if not page_listings:
            print(f"  No more listings at page {page}. Done.")
            break

        # Filter strictly to our user_ID just in case
        filtered = [l for l in page_listings if str(l.get("userId", "")) == str(user_id)]
        print(f"  Page {page}: {len(page_listings)} returned, {len(filtered)} belong to user_ID={user_id}")
        all_listings.extend(filtered)

        # If the page returned fewer than 20, we're on the last page
        if len(page_listings) < 20:
            break

        page += 1
        time.sleep(1.5)  # be polite to RentFaster

    return all_listings


def fetch_listing_detail(listing_id: str, headers: dict) -> dict:
    """Fetch full listing detail including all photos and description."""
    url = f"https://www.rentfaster.ca/api/listing.json?id={listing_id}"
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("listing", data)
    except Exception as e:
        print(f"  Could not fetch detail for listing {listing_id}: {e}")
        return {}


def map_property_type(rf_type: str) -> str:
    """Map RentFaster property types to XML-expected values."""
    mapping = {
        "apartment": "Apartment",
        "condo unit": "Condo",
        "condo": "Condo",
        "house": "House",
        "townhouse": "Townhouse",
        "basement": "Apartment",
        "shared": "Apartment",
        "main floor": "Apartment",
    }
    return mapping.get(rf_type.lower(), "Apartment")


def map_pets(rf: dict) -> dict:
    """Extract pet policy. RentFaster returns cats/dogs as booleans."""
    cats_ok = rf.get("cats") is True or str(rf.get("cats", "")).lower() in ("1", "yes", "true")
    dogs_ok = rf.get("dogs") is True or str(rf.get("dogs", "")).lower() in ("1", "yes", "true")
    no_pets = not cats_ok and not dogs_ok
    return {
        "NoPets": "Yes" if no_pets else "No",
        "Cats": "Yes" if cats_ok else "No",
        "SmallDogs": "Yes" if dogs_ok else "No",
        "LargeDogs": "No",
    }


def get_utilities(rf: dict) -> str:
    """
    Build utilities string from the utilities_included list.
    RentFaster returns e.g. ["Heat", "Water"] or false.
    """
    raw = rf.get("utilities_included", False)
    if not raw or raw is False:
        return "Not included"
    if isinstance(raw, list) and raw:
        return " | ".join(raw) + " included"
    return "Not included"


# Only used when every other method fails. The report flags every listing that
# ends up with this, because it is almost certainly the WRONG postal code.
FALLBACK_POSTAL = "T2S 0J1"

POSTAL_RE = re.compile(r'\b([A-Za-z]\d[A-Za-z])\s?(\d[A-Za-z]\d)\b')
LOOKUP_HEADERS = {"User-Agent": "RentchFeedGenerator/1.0 (https://rentch.ca)"}


def normalize_postal(text: str) -> str:
    """Return 'T2R 1S9' style postal code found in text, or '' if none."""
    match = POSTAL_RE.search(text or "")
    if not match:
        return ""
    return f"{match.group(1)} {match.group(2)}".upper()


def load_postal_cache() -> dict:
    try:
        with open(POSTAL_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_postal_cache(cache: dict):
    with open(POSTAL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
        f.write("\n")


def lookup_postal_online(address: str, city: str, prov: str) -> tuple:
    """
    Look up a postal code from a street address using free geocoders.
    Returns (postal, source) or ("", "") if nothing trustworthy was found.
    Only accepts full Alberta postal codes (starting with T).
    """
    query = f"{address}, {city}, {prov}, Canada"

    # 1. geocoder.ca (Canadian geocoder, returns full postal codes)
    try:
        r = requests.get("https://geocoder.ca/",
                         params={"locate": query, "json": 1},
                         headers=LOOKUP_HEADERS, timeout=15)
        data = r.json()
        postal = normalize_postal(str(data.get("postal", "")))
        if postal.startswith("T"):
            return postal, "geocoder.ca"
    except Exception as e:
        print(f"    geocoder.ca lookup failed for {address}: {e}")
    time.sleep(1.5)

    # 2. OpenStreetMap Nominatim (max 1 request/second)
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": query, "format": "jsonv2",
                                 "addressdetails": 1, "countrycodes": "ca", "limit": 1},
                         headers=LOOKUP_HEADERS, timeout=15)
        results = r.json()
        if results:
            postal = normalize_postal(results[0].get("address", {}).get("postcode", ""))
            if postal.startswith("T"):
                return postal, "OpenStreetMap"
    except Exception as e:
        print(f"    OpenStreetMap lookup failed for {address}: {e}")
    time.sleep(1.5)

    return "", ""


def extract_postal(rf_search: dict, rf_detail: dict, listing_id: str, cache: dict) -> tuple:
    """
    Returns (postal_code, source). Priority:
    1. Manual lookup table (POSTAL_CODES) - verified by hand
    2. Postal code written in the RentFaster description
    3. Automatic online lookup (cached in postal_lookup_cache.json) - unverified
    4. Fallback filler postal code - almost certainly wrong, flagged in report
    """
    # 1. Manual lookup
    if listing_id in POSTAL_CODES:
        return POSTAL_CODES[listing_id], "manual table"

    # 2. Scan description for a Canadian postal code (e.g. T2R 1S9 or T2R1S9)
    description = rf_detail.get("intro", "") or rf_detail.get("desc", "") or ""
    postal = normalize_postal(description)
    if postal:
        return postal, "listing description"

    # 3. Automatic lookup, cached by address so each address is only looked up once
    address = rf_search.get("address", "").strip()
    city = rf_search.get("city", "Calgary") or "Calgary"
    prov = str(rf_search.get("prov", "ab")).upper()
    cache_key = f"{address}, {city}".lower()
    if address:
        if cache_key in cache:
            entry = cache[cache_key]
            return entry["postal"], f"auto lookup ({entry['source']}, cached)"
        postal, source = lookup_postal_online(address, city, prov)
        if postal:
            cache[cache_key] = {
                "address": address,
                "postal": postal,
                "source": source,
                "looked_up": time.strftime("%Y-%m-%d"),
            }
            return postal, f"auto lookup ({source})"

    # 4. Fallback
    return FALLBACK_POSTAL, "FALLBACK (not found)"


def get_photos(rf_search: dict, rf_detail: dict, title: str) -> list:
    """
    Return list of (url, caption) tuples.
    Prefers full detail photos; falls back to slide URL from search result.
    """
    photos = []

    # Try detail endpoint photos first
    raw = rf_detail.get("media", rf_detail.get("photos", []))
    if isinstance(raw, list) and raw:
        for i, p in enumerate(raw, 1):
            if isinstance(p, dict):
                url = p.get("large") or p.get("slide") or p.get("url") or p.get("thumb")
            else:
                url = str(p)
            if url and url.startswith("http"):
                photos.append((url, f"{title} - Photo {i}"))
        if photos:
            return photos

    # Fall back to slide URL from the search result
    slide = rf_search.get("slide", "")
    if slide and slide.startswith("http"):
        photos.append((slide, f"{title} - Photo 1"))

    return photos


def get_parking(rf: dict) -> list:
    """
    Parse parking from the 'parking' list field.
    RentFaster returns e.g. ["underground"] or ["garage"] or [].
    """
    raw = rf.get("parking", [])
    if not isinstance(raw, list):
        return []
    parking = []
    for p in raw:
        p_lower = str(p).lower()
        if "garage" in p_lower:
            parking.append("Garage")
        elif "underground" in p_lower:
            parking.append("Underground")
        elif "outdoor" in p_lower or "surface" in p_lower:
            parking.append("Surface")
        else:
            parking.append(p.title())
    return parking


def get_appliances(rf: dict) -> list:
    """
    Parse appliances from the 'features' list field.
    RentFaster returns e.g. ["Fridge", "Oven/Stove", "Laundry - In Suite"].
    """
    return rf.get("features", [])


# ─── XML BUILDER ───────────────────────────────────────────────────────────────

def sub(parent, tag, text=None):
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def build_listing_element(rf_search: dict, rf_detail: dict, postal_cache: dict) -> tuple:
    """
    Build a <Listing> XML element from search + detail data.
    Returns (element, report_row) where report_row feeds feed_report.md.
    """

    listing = ET.Element("Listing")
    listing_id = str(rf_search.get("ref_id", rf_search.get("id", "")))
    sub(listing, "ID", listing_id)

    # ── Location ──
    loc = sub(listing, "Location")
    address = rf_search.get("address", "")
    sub(loc, "StreetAddress", address)
    sub(loc, "City", rf_search.get("city", "Calgary"))
    prov = str(rf_search.get("prov", "ab")).upper()
    sub(loc, "State", prov)
    # Postal code: manual table → description scan → auto lookup → filler fallback
    postal, postal_source = extract_postal(rf_search, rf_detail, listing_id, postal_cache)
    sub(loc, "Zip", postal)
    sub(loc, "DisplayAddress", "Yes")

    # ── ListingDetails ──
    det = sub(listing, "ListingDetails")
    sub(det, "Status", "Active")
    sub(det, "Price", rf_search.get("price", ""))

    link = rf_search.get("link", "")
    if link and not link.startswith("http"):
        link = "https://www.rentfaster.ca" + link
    sub(det, "ListingUrl", link)
    sub(det, "ProviderListingId", listing_id)
    application_url = build_tenant_turner_url(address, listing_id)
    sub(det, "ApplicationUrl", application_url)

    # ── RentalDetails ──
    rent = sub(listing, "RentalDetails")
    sub(rent, "Availability", rf_search.get("avdate", ""))
    sub(rent, "LeaseTerm", "12 Months")
    sub(rent, "DepositFees", rf_search.get("price", ""))

    pets = map_pets(rf_search)
    pets_el = sub(rent, "PetsAllowed")
    for k, v in pets.items():
        sub(pets_el, k, v)

    # ── BasicDetails ──
    basic = sub(listing, "BasicDetails")
    sub(basic, "PropertyType", map_property_type(rf_search.get("type", "Apartment")))

    title = rf_detail.get("title") or rf_search.get("title", address)
    description = rf_detail.get("intro") or rf_detail.get("desc") or ""
    if description.strip() == address.strip():
        description = ""

    sub(basic, "Title", title)
    sub(basic, "Description", description)
    sub(basic, "Bedrooms", rf_search.get("bedrooms", ""))
    sub(basic, "Bathrooms", rf_search.get("baths", ""))
    sq = rf_search.get("sq_feet", "")
    if sq:
        sub(basic, "LivingArea", sq)

    # ── Pictures ──
    photos = get_photos(rf_search, rf_detail, title)
    if photos:
        pics_el = sub(listing, "Pictures")
        for url, caption in photos:
            pic = sub(pics_el, "Picture")
            sub(pic, "PictureUrl", url)
            sub(pic, "Caption", caption)

    # ── RichDetails ──
    rich = sub(listing, "RichDetails")

    parking = get_parking(rf_search)
    if parking:
        park_el = sub(rich, "ParkingTypes")
        for p in parking:
            sub(park_el, "ParkingType", p)

    appliances = get_appliances(rf_search)
    if appliances:
        app_el = sub(rich, "Appliances")
        for a in appliances:
            sub(app_el, "Appliance", a)

    sub(rich, "Fireplace", "No")
    sub(rich, "UtilitiesIncluded", get_utilities(rf_search))

    report_row = {
        "id": listing_id,
        "address": address,
        "price": rf_search.get("price", ""),
        "postal": postal,
        "postal_source": postal_source,
        "application_url": application_url,
        "listing_url": link,
    }
    return listing, report_row


def build_xml(search_listings: list) -> tuple:
    """
    Fetch detail for each listing, then build the XML tree.
    Returns (root_element, report_rows).
    """
    root = ET.Element("Listings")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; RentchFeedGenerator/1.0)"}
    postal_cache = load_postal_cache()
    report_rows = []
    skipped = 0

    for rf_search in search_listings:
        listing_id = str(rf_search.get("ref_id", rf_search.get("id", "")))
        print(f"  Fetching detail for listing {listing_id} ({rf_search.get('address', '')})...")

        rf_detail = fetch_listing_detail(listing_id, headers)
        time.sleep(0.5)

        # Apply any manual overrides for this listing
        if listing_id in LISTING_OVERRIDES:
            rf_search = {**rf_search, **LISTING_OVERRIDES[listing_id]}
            print(f"    Applied manual overrides for listing {listing_id}")

        try:
            listing_el, report_row = build_listing_element(rf_search, rf_detail, postal_cache)
            root.append(listing_el)
            report_rows.append(report_row)
        except Exception as e:
            print(f"  Skipping listing {listing_id}: {e}")
            skipped += 1

    if skipped:
        print(f"  ({skipped} listings skipped due to errors)")

    save_postal_cache(postal_cache)
    return root, report_rows


def to_pretty_xml(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(raw)
    return dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


def build_selected_root(root: ET.Element) -> ET.Element:
    """Copy of the full feed, filtered to SELECTED_LISTING_IDS (all if empty)."""
    selected = ET.Element("Listings")
    wanted = {str(i) for i in SELECTED_LISTING_IDS}
    for listing in root:
        if not wanted or listing.findtext("ID") in wanted:
            selected.append(copy.deepcopy(listing))
    return selected


# ─── BOOKING LINK CHECK + REPORT ───────────────────────────────────────────────

def check_booking_link(url: str) -> tuple:
    """
    Open a Tenant Turner booking link and return (ok, note).
    A link is flagged if it errors, or if Tenant Turner sends it somewhere
    other than the property page we asked for (usually means a bad slug).
    NOTE: Tenant Turner may show a friendly "not found" page with a normal
    success code, so a pass here is a good sign, not a guarantee.
    """
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                         timeout=20, allow_redirects=True)
    except Exception as e:
        return False, f"could not open ({type(e).__name__})"

    expected_path = url.split("?")[0].replace("https://app.tenantturner.com", "").rstrip("/").lower()
    final_path = r.url.split("?")[0].replace("https://app.tenantturner.com", "").rstrip("/").lower()
    if r.status_code >= 400:
        return False, f"error {r.status_code}"
    if final_path == expected_path:
        return True, f"OK ({r.status_code})"
    # Tenant Turner sometimes skips the time picker and goes straight to its
    # contact form for the SAME property (e.g. no showing times open). That's
    # a working link, not a bad slug.
    slug = expected_path.rsplit("/", 1)[-1]
    if final_path.startswith("/qualify/") and final_path.rsplit("/", 1)[-1] == slug:
        return True, "OK (goes to contact form - no showing times open?)"
    return False, f"redirected to {r.url}"


def write_report(report_rows: list, selected_count: int):
    print("\nChecking Tenant Turner booking links...")
    for row in report_rows:
        row["link_ok"], row["link_note"] = check_booking_link(row["application_url"])
        print(f"  {row['id']} {row['address']}: {row['link_note']}")
        time.sleep(1)

    postal_problems = [r for r in report_rows if r["postal_source"].startswith("FALLBACK")]
    postal_unverified = [r for r in report_rows if r["postal_source"].startswith("auto lookup")]
    link_problems = [r for r in report_rows if not r["link_ok"]]

    lines = [
        "# Rentch Feed Report",
        "",
        f"_Last run: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}_ — "
        f"{len(report_rows)} listing(s) in full feed, {selected_count} in selected feed.",
        "",
        "## Needs attention",
        "",
    ]
    if not (postal_problems or postal_unverified or link_problems):
        lines.append("Nothing — all postal codes are verified and all booking links opened.")
    for r in link_problems:
        lines.append(f"- 🔴 **Booking link problem** — {r['address']} (ID {r['id']}): "
                     f"{r['link_note']} — [open link]({r['application_url']}). "
                     f"Fix by adding the right slug to `TENANT_TURNER_CUSTOM_SLUGS`.")
    for r in postal_problems:
        lines.append(f"- 🔴 **No postal code found** — {r['address']} (ID {r['id']}) is using "
                     f"placeholder {r['postal']}. Add it to `POSTAL_CODES`.")
    for r in postal_unverified:
        lines.append(f"- 🟡 **Postal code auto-looked-up, please verify** — {r['address']} "
                     f"(ID {r['id']}): {r['postal']}. If correct, copy it into `POSTAL_CODES`; "
                     f"if wrong, add the right one to `POSTAL_CODES` (that always wins).")

    lines += [
        "",
        "## All listings",
        "",
        "| ID | Address | Price | Postal code | Postal source | Booking link | Link check | RentFaster |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report_rows:
        link_icon = "✅" if r["link_ok"] else "🔴"
        lines.append(
            f"| {r['id']} | {r['address']} | ${r['price']} | {r['postal']} | {r['postal_source']} "
            f"| [book]({r['application_url']}) | {link_icon} {r['link_note']} "
            f"| [listing]({r['listing_url']}) |"
        )
    lines += [
        "",
        "_Link check caveat: ✅ means the page opened without an error or redirect. "
        "Tenant Turner could still show a \"not found\" message on a page that opens "
        "normally, so click a few to spot-check._",
        "",
    ]
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Saved report to: {REPORT_FILE}")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    listings = fetch_rentfaster_listings(RENTFASTER_USER_ID)

    if not listings:
        print("No listings found. Check your user_ID or network connection.")
        return

    print(f"\nFound {len(listings)} listing(s). Fetching full details and building XML...")
    root, report_rows = build_xml(listings)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(to_pretty_xml(root))
    print(f"\nSaved full feed to: {OUTPUT_FILE}")

    selected_root = build_selected_root(root)
    with open(SELECTED_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(to_pretty_xml(selected_root))
    print(f"Saved selected feed ({len(selected_root)} listing(s)) to: {SELECTED_OUTPUT_FILE}")

    # The report is a nice-to-have: never let it stop the feeds from updating
    try:
        write_report(report_rows, len(selected_root))
    except Exception as e:
        print(f"Could not write report: {e}")

    print("\nDone!")


if __name__ == "__main__":
    main()
