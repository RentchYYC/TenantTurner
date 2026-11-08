#!/usr/bin/env python3
"""
Rentch XML Feed Generator
Scrapes RentFaster listings for user_ID=358564 and generates
a Tenant Turner / rentch.ca compatible XML feed.

Usage:
    python generate_feed.py

Output:
    rentch_tenant_turner_full_feed.xml
    photos/<RentFaster id>.jpg   web-size copy of each ad's first photo (Rentch OS uses these; Aaron, Nov 8 2026)
    photos/index.json            which RentFaster photo each copy was made from
"""

import hashlib
import io
import json
import os
import requests
import time
import re
from datetime import datetime, timezone
from PIL import Image, ImageOps
import xml.etree.ElementTree as ET
from xml.dom import minidom

# ─── CONFIG ────────────────────────────────────────────────────────────────────
RENTFASTER_USER_ID = "358564"
OUTPUT_FILE = "rentch_tenant_turner_full_feed.xml"

# First photo per ad (Aaron, Nov 8 2026): Rentch OS takes ad photos from these copies, never from RentFaster itself.
# Web size only: max 2048 px on the long side, JPEG ~85%, camera data (EXIF/GPS) removed. Replaced only when the ad's first
# photo changes; removed when the ad leaves the feed (only after a complete fetch, so a RentFaster hiccup never empties it).
PHOTO_DIR = "photos"
PHOTO_INDEX = os.path.join(PHOTO_DIR, "index.json")
PHOTO_MAX_PX = 2048
PHOTO_QUALITY = 85
FETCH_COMPLETE = True   # set to False when a search page fails

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
            global FETCH_COMPLETE
            FETCH_COMPLETE = False
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


FALLBACK_POSTAL = "T2S 0J1"

def extract_postal(rf_detail: dict, listing_id: str) -> str:
    """
    Postal code priority:
    1. Manual lookup table (POSTAL_CODES)
    2. Canadian postal code found anywhere in the listing description
    3. Fallback filler postal code
    """
    # 1. Manual lookup
    if listing_id in POSTAL_CODES:
        return POSTAL_CODES[listing_id]

    # 2. Scan description for a Canadian postal code (e.g. T2R 1S9 or T2R1S9)
    description = rf_detail.get("intro", "") or rf_detail.get("desc", "") or ""
    match = re.search(r'\b([A-Za-z]\d[A-Za-z][\s]?\d[A-Za-z]\d)\b', description)
    if match:
        postal = match.group(1).upper().strip()
        # Ensure there's a space in the middle (e.g. T2R1S9 -> T2R 1S9)
        if len(postal) == 6:
            postal = postal[:3] + " " + postal[3:]
        return postal

    # 3. Fallback
    return FALLBACK_POSTAL


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


# ─── FIRST PHOTO (web-size copy for Rentch OS) ─────────────────────────────────

def load_photo_index() -> dict:
    try:
        with open(PHOTO_INDEX, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def web_size(data: bytes) -> tuple:
    """Original photo bytes -> (JPEG bytes, width, height): upright, max 2048 px, ~85%, no camera data."""
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    im = im.convert("RGB")
    im.thumbnail((PHOTO_MAX_PX, PHOTO_MAX_PX))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=PHOTO_QUALITY, optimize=True)   # no exif= -> camera data is not written
    return out.getvalue(), im.width, im.height


def sync_first_photo(listing_id: str, url: str, index: dict, headers: dict):
    """photos/<id>.jpg for this ad's first photo: kept when the first photo is the same, made again when it changed.
    Returns the path for the feed, or None when there is no copy."""
    path = f"{PHOTO_DIR}/{listing_id}.jpg"
    have = os.path.exists(path)
    if not url:
        return path if have else None
    if have and index.get(listing_id, {}).get("source") == url:
        return path
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        data, w, h = web_size(r.content)
    except Exception as e:
        print(f"    First photo for {listing_id} not copied: {e}")
        return path if have else None
    os.makedirs(PHOTO_DIR, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    index[listing_id] = {"source": url, "sha256": hashlib.sha256(data).hexdigest(), "width": w, "height": h,
                         "copied": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
    print(f"    First photo for {listing_id}: {'replaced' if have else 'copied'} ({w}x{h}, {len(data) // 1024} KB)")
    return path


def prune_photos(keep_ids: set, index: dict) -> None:
    """Remove the copies of ads that left the feed."""
    if not os.path.isdir(PHOTO_DIR):
        return
    for name in sorted(os.listdir(PHOTO_DIR)):
        m = re.fullmatch(r"(\d+)\.jpg", name)
        if m and m.group(1) not in keep_ids:
            os.remove(os.path.join(PHOTO_DIR, name))
            index.pop(m.group(1), None)
            print(f"  Removed {PHOTO_DIR}/{name} (the ad left the feed)")
    for k in [k for k in index if k not in keep_ids]:
        index.pop(k, None)


# ─── XML BUILDER ───────────────────────────────────────────────────────────────

def sub(parent, tag, text=None):
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def build_listing_element(rf_search: dict, rf_detail: dict, photo_index: dict = None, headers: dict = None) -> ET.Element:
    """Build a <Listing> XML element from search + detail data."""

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
    # Postal code: manual table → description scan → filler fallback
    sub(loc, "Zip", extract_postal(rf_detail, listing_id))
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
    sub(det, "ApplicationUrl", build_tenant_turner_url(address, listing_id))

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

    # ── Rentch OS: the web-size copy of the first photo, in this repo ──
    if photo_index is not None:
        rentch_photo = sync_first_photo(listing_id, photos[0][0] if photos else "", photo_index, headers or {})
        if rentch_photo:
            sub(listing, "RentchPhoto", rentch_photo)

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

    return listing


def build_xml(search_listings: list) -> str:
    """Fetch detail for each listing, then build the full XML."""
    root = ET.Element("Listings")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; RentchFeedGenerator/1.0)"}
    skipped = 0
    photo_index = load_photo_index()
    ids = set()

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
            listing_el = build_listing_element(rf_search, rf_detail, photo_index, headers)
            root.append(listing_el)
            ids.add(listing_id)
        except Exception as e:
            print(f"  Skipping listing {listing_id}: {e}")
            skipped += 1

    if skipped:
        print(f"  ({skipped} listings skipped due to errors)")

    # copies of ads that left the feed go — only after a complete fetch with nothing skipped
    if FETCH_COMPLETE and not skipped:
        prune_photos(ids, photo_index)
    else:
        print("  Fetch incomplete: no photo removed this time.")
    if os.path.isdir(PHOTO_DIR):
        with open(PHOTO_INDEX, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(photo_index.items())), f, indent=1)
            f.write("\n")

    raw = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(raw)
    # the build time, so the file changes on every run: Rentch OS judges the feed fresh by when GitHub last updated it
    dom.insertBefore(dom.createComment(" built " + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ") + " by generate_feed.py "), dom.documentElement)
    return dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    listings = fetch_rentfaster_listings(RENTFASTER_USER_ID)

    if not listings:
        print("No listings found. Check your user_ID or network connection.")
        return

    print(f"\nFound {len(listings)} listing(s). Fetching full details and building XML...")
    xml_output = build_xml(listings)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(xml_output)

    print(f"\nDone! Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
