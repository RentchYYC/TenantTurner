#!/usr/bin/env python3
"""
Rentch XML Feed Generator
Scrapes RentFaster listings for user_ID=358564 and generates
a Tenant Turner / rentch.ca compatible XML feed.

Usage:
    python generate_feed.py

Output:
    rentch_tenant_turner_full_feed.xml
"""

import requests
import time
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom

# ─── CONFIG ────────────────────────────────────────────────────────────────────
RENTFASTER_USER_ID = "358564"
CITY = "calgary"
PROVINCE = "ab"
OUTPUT_FILE = "rentch_tenant_turner_full_feed.xml"

# Tenant Turner base URL — slugs are auto-generated from street address.
# If a property uses a custom slug, add it here:
#   "123 Main Street SW" -> "custom-slug"
TENANT_TURNER_CUSTOM_SLUGS = {
    # "710 25 Street Northwest": "716b-hillside-west-hillhurst",  # example
}
TENANT_TURNER_BASE = "https://app.tenantturner.com/qualify/select-time/"
TENANT_TURNER_SUFFIX = "?p=TenantTurner"

# ─── HELPERS ───────────────────────────────────────────────────────────────────

def address_to_slug(address: str) -> str:
    """Convert a street address to a Tenant Turner URL slug."""
    slug = address.lower()
    slug = re.sub(r'\s+', '-', slug)
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug


def build_tenant_turner_url(address: str) -> str:
    """Return the Tenant Turner scheduling URL for a given address."""
    slug = TENANT_TURNER_CUSTOM_SLUGS.get(address) or address_to_slug(address)
    return f"{TENANT_TURNER_BASE}{slug}{TENANT_TURNER_SUFFIX}"


def fetch_rentfaster_listings(user_id: str, city: str, province: str) -> list:
    """
    Pull all active listings for a given user from RentFaster's API.
    Paginates until no more listings are returned.
    """
    listings = []
    page = 0
    location = f"{province.lower()}/{city.lower()}"
    cookies = {"lastcity": location}
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; RentchFeedGenerator/1.0)"
    }

    print(f"Fetching listings for user_ID={user_id}...")

    while True:
        url = (
            f"https://www.rentfaster.ca/api/search.json"
            f"?cur_page={page}&user_id={user_id}"
        )
        try:
            response = requests.get(url, cookies=cookies, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            print(f"  Error on page {page}: {e}")
            break

        page_listings = data.get("listings", [])
        if not page_listings:
            print(f"  No more listings at page {page}. Done.")
            break

        print(f"  Page {page}: got {len(page_listings)} listings")
        listings.extend(page_listings)
        page += 1
        time.sleep(1.5)  # be polite

    return listings


def map_property_type(rf_type: str) -> str:
    """Map RentFaster property types to XML-expected values."""
    mapping = {
        "apartment": "Apartment",
        "condo": "Condo",
        "house": "House",
        "townhouse": "Townhouse",
        "basement": "Apartment",
        "shared": "Apartment",
        "main floor": "Apartment",
    }
    return mapping.get(rf_type.lower(), "Apartment")


def map_lease_term(rf_term) -> str:
    """Normalise lease term field."""
    if not rf_term:
        return "12 Months"
    term = str(rf_term)
    if "month" in term.lower():
        return term
    return f"{term} Months"


def map_pets(rf_listing: dict) -> dict:
    """Extract pet policy from a RentFaster listing dict."""
    cats = str(rf_listing.get("cats", "0"))
    dogs = str(rf_listing.get("dogs", "0"))
    # RentFaster uses 1/0 or "Yes"/"No" depending on endpoint version
    cats_ok = cats not in ("0", "No", "false", "")
    dogs_ok = dogs not in ("0", "No", "false", "")
    no_pets = not cats_ok and not dogs_ok
    return {
        "NoPets": "Yes" if no_pets else "No",
        "Cats": "Yes" if cats_ok else "No",
        "SmallDogs": "Yes" if dogs_ok else "No",
        "LargeDogs": "No",  # RentFaster doesn't differentiate dog size
    }


def get_utilities(rf_listing: dict) -> str:
    """Build a utilities-included string from RentFaster fields."""
    included = []
    if rf_listing.get("electricity_included"):
        included.append("Electricity")
    if rf_listing.get("heat_included"):
        included.append("Heat")
    if rf_listing.get("water_included"):
        included.append("Water")
    if rf_listing.get("internet_included"):
        included.append("Internet")
    return " | ".join(included) + " included" if included else "Not included"


def get_photos(rf_listing: dict) -> list:
    """Return list of (url, caption) tuples for photos."""
    photos = []
    title = rf_listing.get("title", rf_listing.get("ref_id", ""))

    # RentFaster returns photos in a few different shapes
    raw_photos = rf_listing.get("media", rf_listing.get("photos", []))
    if isinstance(raw_photos, list):
        for i, p in enumerate(raw_photos, 1):
            if isinstance(p, dict):
                url = p.get("url") or p.get("thumb") or p.get("large")
            else:
                url = str(p)
            if url:
                photos.append((url, f"{title} - Photo {i}"))
    elif isinstance(raw_photos, str) and raw_photos.startswith("http"):
        photos.append((raw_photos, f"{title} - Photo 1"))

    return photos


def get_parking(rf_listing: dict) -> list:
    """Return list of parking type strings."""
    parking = []
    pt = rf_listing.get("parking_type", "")
    if "garage" in str(pt).lower():
        parking.append("Garage")
    elif "underground" in str(pt).lower():
        parking.append("Underground")
    elif "surface" in str(pt).lower() or "outdoor" in str(pt).lower():
        parking.append("Surface")
    elif str(rf_listing.get("parking", "0")) not in ("0", "", "No"):
        parking.append("Surface")
    return parking


def get_appliances(rf_listing: dict) -> list:
    """Build appliance list from RentFaster boolean fields."""
    appliances = []
    if str(rf_listing.get("laundry_suite", "0")) not in ("0", ""):
        appliances.append("Laundry - In Suite")
    elif str(rf_listing.get("laundry_shared", "0")) not in ("0", ""):
        appliances.append("Laundry - Shared")
    if str(rf_listing.get("dishwasher", "0")) not in ("0", ""):
        appliances.append("Dishwasher")
    if str(rf_listing.get("fridge", "0")) not in ("0", ""):
        appliances.append("Fridge")
    if str(rf_listing.get("stove", "0")) not in ("0", ""):
        appliances.append("Oven/Stove")
    return appliances


# ─── XML BUILDER ───────────────────────────────────────────────────────────────

def sub(parent, tag, text=None):
    """Create a subelement, optionally with text content."""
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def build_listing_element(rf: dict) -> ET.Element:
    """Convert a single RentFaster listing dict into an XML <Listing> element."""
    listing = ET.Element("Listing")

    listing_id = str(rf.get("ref_id", rf.get("id", "")))
    sub(listing, "ID", listing_id)

    # ── Location ──
    loc = sub(listing, "Location")
    address = rf.get("address", "")
    sub(loc, "StreetAddress", address)
    sub(loc, "City", rf.get("city", "Calgary"))
    sub(loc, "State", rf.get("province", "AB").upper())
    sub(loc, "Zip", rf.get("postal", ""))
    sub(loc, "DisplayAddress", "Yes")

    # ── ListingDetails ──
    det = sub(listing, "ListingDetails")
    sub(det, "Status", "Active")
    sub(det, "Price", rf.get("price", ""))

    # Build listing URL
    listing_url = rf.get("link", "")
    if listing_url and not listing_url.startswith("http"):
        listing_url = "https://www.rentfaster.ca" + listing_url
    sub(det, "ListingUrl", listing_url)
    sub(det, "ProviderListingId", listing_id)

    # Tenant Turner application URL
    app_url = build_tenant_turner_url(address)
    sub(det, "ApplicationUrl", app_url)

    # ── RentalDetails ──
    rent = sub(listing, "RentalDetails")
    avail = rf.get("availibility", rf.get("availability", ""))
    sub(rent, "Availability", avail)
    sub(rent, "LeaseTerm", map_lease_term(rf.get("lease_term")))
    sub(rent, "DepositFees", rf.get("price", ""))

    pets = map_pets(rf)
    pets_el = sub(rent, "PetsAllowed")
    for k, v in pets.items():
        sub(pets_el, k, v)

    # ── BasicDetails ──
    basic = sub(listing, "BasicDetails")
    sub(basic, "PropertyType", map_property_type(rf.get("type", "apartment")))
    sub(basic, "Title", rf.get("title", address))
    sub(basic, "Description", rf.get("intro", ""))
    sub(basic, "Bedrooms", rf.get("beds", ""))
    sub(basic, "Bathrooms", rf.get("baths", ""))
    sq = rf.get("sq_feet", rf.get("sqfeet", ""))
    if sq:
        sub(basic, "LivingArea", sq)

    # ── Pictures ──
    photos = get_photos(rf)
    if photos:
        pics_el = sub(listing, "Pictures")
        for url, caption in photos:
            pic = sub(pics_el, "Picture")
            sub(pic, "PictureUrl", url)
            sub(pic, "Caption", caption)

    # ── RichDetails ──
    rich = sub(listing, "RichDetails")

    parking = get_parking(rf)
    if parking:
        park_el = sub(rich, "ParkingTypes")
        for p in parking:
            sub(park_el, "ParkingType", p)

    appliances = get_appliances(rf)
    if appliances:
        app_el = sub(rich, "Appliances")
        for a in appliances:
            sub(app_el, "Appliance", a)

    ac = rf.get("ac", rf.get("air_conditioning", "0"))
    cooling_el = sub(rich, "CoolingSystems")
    sub(cooling_el, "CoolingSystem", "Central Air" if str(ac) not in ("0", "", "No") else "None")

    fireplace = rf.get("fireplace", "0")
    sub(rich, "Fireplace", "Yes" if str(fireplace) not in ("0", "", "No") else "No")
    sub(rich, "UtilitiesIncluded", get_utilities(rf))

    return listing


def build_xml(listings: list) -> str:
    """Build the full XML document from a list of RentFaster listing dicts."""
    root = ET.Element("Listings")
    skipped = 0

    for rf in listings:
        try:
            listing_el = build_listing_element(rf)
            root.append(listing_el)
        except Exception as e:
            print(f"  Skipping listing {rf.get('ref_id', '?')}: {e}")
            skipped += 1

    if skipped:
        print(f"  ({skipped} listings skipped due to errors)")

    # Pretty-print
    raw = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(raw)
    return dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    listings = fetch_rentfaster_listings(RENTFASTER_USER_ID, CITY, PROVINCE)

    if not listings:
        print("No listings found. Check your user_ID or network connection.")
        return

    print(f"\nBuilding XML for {len(listings)} listing(s)...")
    xml_output = build_xml(listings)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(xml_output)

    print(f"Done! Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
