"""Test of the description fix (Rentch OS, Nov 14 2026) - writes ONLY probe/test_feed_with_description.xml.

Builds the feed the daily job builds, with generate_feed.py's own build_listing_element (which now falls back to the search
result's "intro"), but: one search.json request only (no listing.json: it answers 404 for every listing), no photo work
(photo_index=None), and the output goes to a TEST file TenantTurner never reads. Then it compares that file with the real feed
and prints counts only: how many descriptions are filled, their lengths, and which XML tags differ.
"""
import os
import sys
import xml.etree.ElementTree as ET
from xml.dom import minidom

sys.path.insert(0, os.getcwd())
import generate_feed as g  # noqa: E402  (nothing runs on import: main() is behind __name__ == "__main__")

TEST_FILE = "probe/test_feed_with_description.xml"
assert TEST_FILE != g.OUTPUT_FILE

listings = g.fetch_rentfaster_listings(g.RENTFASTER_USER_ID)
# the search result's "intro", counted only (never printed): empty / the same as the address / real text
k = {"empty": 0, "same as the address": 0, "other text": 0}
for x in listings:
    t, adr = str(x.get("intro") or "").strip(), str(x.get("address") or "").strip()
    k["empty" if not t else "same as the address" if t == adr else "other text"] += 1
print(f"search.json 'intro' per listing: {k}")
root = ET.Element("Listings")
for rf_search in listings:
    lid = str(rf_search.get("ref_id", rf_search.get("id", "")))
    if lid in g.LISTING_OVERRIDES:
        rf_search = {**rf_search, **g.LISTING_OVERRIDES[lid]}
    root.append(g.build_listing_element(rf_search, {}, None, None))   # {}: the detail the daily job gets (404); None: no photos
xml = minidom.parseString(ET.tostring(root, encoding="unicode")).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
with open(TEST_FILE, "w", encoding="utf-8") as f:
    f.write(xml)
print(f"TEST file written: {TEST_FILE} ({len(listings)} listings). The real feed was not touched.")


test, real = ET.parse(TEST_FILE), ET.parse(g.OUTPUT_FILE)
descs = [(e.text or "").strip() for e in test.getroot().iter("Description")]
filled = [d for d in descs if d]
print(f"Descriptions filled: {len(filled)} of {len(descs)} (the real feed: "
      f"{sum(1 for e in real.getroot().iter('Description') if (e.text or '').strip())} of {sum(1 for _ in real.getroot().iter('Description'))})")
if filled:
    ls = sorted(len(d) for d in filled)
    print(f"Lengths (characters): shortest {ls[0]}, middle {ls[len(ls) // 2]}, longest {ls[-1]}")
    print(f"Ends cut short ('...' or '…'): {sum(1 for d in filled if d.endswith(('...', '…')))}")
t_tags, r_tags = {}, {}
for tree, acc in ((test, t_tags), (real, r_tags)):
    for l in tree.getroot():
        for e in l.iter():
            acc[e.tag] = acc.get(e.tag, 0) + 1
print("Tags whose count differs between the TEST file and the real feed:",
      {k: (r_tags.get(k, 0), t_tags.get(k, 0)) for k in sorted(set(t_tags) | set(r_tags)) if t_tags.get(k, 0) != r_tags.get(k, 0)} or "none")
def by_listing(tree):
    out = {}
    for l in tree.getroot():
        lid = next((e.text.strip() for e in l.iter() if e.tag == "ProviderListingId" and e.text), None)
        if lid:
            out[lid] = {(e.tag, i): (e.text or "").strip() for i, e in enumerate(l.iter())}
    return out


tb, rb = by_listing(test), by_listing(real)
print(f"Listings in both: {len(set(tb) & set(rb))} (only in the TEST file: {len(set(tb) - set(rb))}, only in the real feed: {len(set(rb) - set(tb))})")
changed = {}
for lid in set(tb) & set(rb):
    for key, v in tb[lid].items():
        if rb[lid].get(key) is not None and rb[lid][key] != v:
            changed[key[0]] = changed.get(key[0], 0) + 1
print("Tags whose TEXT differs, same listing (counts only):", changed or "none")
