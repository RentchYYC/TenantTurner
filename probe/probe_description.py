"""Probe for Rentch OS (Nov 14 2026). Prints FIELD NAMES and HTTP STATUS CODES only - no values, no files written.

The same requests the daily job makes (search.json for user 358564, listing.json for one listing, the first photo), once each:
  1. Which fields does search.json give per listing (is a description among them)?
  2. Does listing.json still answer (the daily job gets 404 for every listing)? If it does, which fields does it give?
  3. The first photo: which photo fields does a search result have, and does the photo URL answer (status + size in KB only)?
"""
import requests

USER_ID = "358564"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RentchFeedGenerator/1.0)"}  # the daily job's own header
WORDS_TEXT = ("desc", "intro", "comment", "text", "about", "detail", "summary", "body")
WORDS_PHOTO = ("photo", "slide", "image", "thumb", "media", "pic")


def names(obj, prefix="", depth=0):
    """Field names and their kinds (one level into objects / lists of objects); never values."""
    out = []
    if isinstance(obj, dict):
        for k in sorted(obj):
            v = obj[k]
            kind = f"list[{len(v)}]" if isinstance(v, list) else type(v).__name__
            out.append(f"{prefix}{k} ({kind})")
            if depth < 1 and isinstance(v, dict):
                out += names(v, prefix + k + ".", depth + 1)
            if depth < 1 and isinstance(v, list) and v and isinstance(v[0], dict):
                out += names(v[0], prefix + k + "[0].", depth + 1)
    return out


r = requests.get(f"https://www.rentfaster.ca/api/search.json?cur_page=0&user_ID={USER_ID}", headers=HEADERS, timeout=15)
print(f"1. search.json -> HTTP {r.status_code}")
listings = r.json().get("listings", []) if r.ok else []
print(f"   {len(listings)} listing(s) on page 0")
if listings:
    first = listings[0]
    print("   field names of one listing:")
    for n in names(first):
        print("     ", n)
    print(f"   description-like field names: {[k for k in first if any(w in k.lower() for w in WORDS_TEXT)] or 'none'}")
    lid = str(first.get("ref_id") or first.get("id") or "")

    d = requests.get(f"https://www.rentfaster.ca/api/listing.json?id={lid}", headers=HEADERS, timeout=15)
    print(f"2. listing.json (one listing) -> HTTP {d.status_code}")
    if d.ok:
        body = d.json()
        body = body.get("listing", body) if isinstance(body, dict) else {}
        for n in names(body):
            print("     ", n)

    print(f"3. photo field names in a search result: {[k for k in first if any(w in k.lower() for w in WORDS_PHOTO)] or 'none'}")
    slide = first.get("slide") or ""
    if isinstance(slide, str) and slide.startswith("http"):
        p = requests.get(slide, headers=HEADERS, timeout=20)
        print(f"   the first photo (search 'slide') -> HTTP {p.status_code}, {len(p.content) // 1024} KB")
print("Done. Nothing was written or committed.")
