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
changed = {}
for tl, rl in zip(test.getroot(), real.getroot()):
    for te, re_ in zip(tl.iter(), rl.iter()):
        if te.tag == re_.tag and (te.text or "").strip() != (re_.text or "").strip():
            changed[te.tag] = changed.get(te.tag, 0) + 1
print("Tags whose TEXT differs (listing by listing, same order):", changed or "none")
