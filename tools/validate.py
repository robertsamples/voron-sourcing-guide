"""Stage 4: prove the master lost nothing that the workbook contained.

Checks, in order of how much they would matter if they failed:
  1. every URL in the workbook survives into the master (as a source link,
     an affiliate link, or a recorded pre-canonical raw_url)
  2. every (component, tab) pair in the workbook appears in some item's used_by
  3. every note survives
  4. ids are unique, used_by projects are known, links parse
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalize import clean_text, name_key, url_key  # noqa: E402

RAW = "data/raw_extract.json"
MASTER = "data/voron_sourcing_master.json"


def main():
    raw = json.load(open(RAW, encoding="utf-8"))
    m = json.load(open(MASTER, encoding="utf-8"))
    aliases_path = "data/aliases.json"
    dropped = set()
    if os.path.exists(aliases_path):
        a = json.load(open(aliases_path, encoding="utf-8"))
        dropped = {name_key(x) for x in a.get("drop", [])}

    sheets = {p["sheet"]: p["id"] for p in m["projects"]}
    fails = []

    # 1. links -------------------------------------------------------------
    have = set()
    for i in m["items"]:
        for s in i["sources"]:
            have.add(url_key(s["url"]))
            for u in s["affiliate_urls"] + s["raw_urls"]:
                have.add(url_key(u))
    want = defaultdict(list)
    for r in raw["rows"]:
        if r["sheet"] not in sheets:
            continue
        if name_key(clean_text(r["component"]) or "") in dropped:
            continue
        for e in r["entries"]:
            if e["url"]:
                want[url_key(e["url"])].append("%s!%s%d" % (r["sheet"], e["col"], r["row"]))
    missing = {k: v for k, v in want.items() if k not in have}
    print("1. links in workbook: %d distinct, missing from master: %d"
          % (len(want), len(missing)))
    for k, v in list(missing.items())[:10]:
        fails.append("missing link %s (%s)" % (k, v[0]))

    # 2. component x tab coverage -----------------------------------------
    want_pairs = set()
    for r in raw["rows"]:
        if r["sheet"] not in sheets:
            continue
        key = name_key(clean_text(r["component"]) or "")
        if not key or key in dropped or key == "or":
            continue
        if key.startswith("as an amazon") or key.startswith("as an aliexpress"):
            continue
        want_pairs.add((key, sheets[r["sheet"]]))
    have_pairs = set()
    alias_of = {}
    for i in m["items"]:
        for n in [i["name"]] + i["aliases"]:
            alias_of[name_key(n)] = i["id"]
        for u in i["used_by"]:
            have_pairs.add((i["id"], u["project"]))
    unresolved = [(k, p) for k, p in want_pairs
                  if (alias_of.get(k), p) not in have_pairs]
    print("2. (component, tab) pairs: %d, unresolved: %d"
          % (len(want_pairs), len(unresolved)))
    for k, p in unresolved[:10]:
        fails.append("uncovered component %r in %s" % (k, p))

    # 3. notes -------------------------------------------------------------
    want_notes = set()
    for r in raw["rows"]:
        if r["sheet"] not in sheets:
            continue
        key = name_key(clean_text(r["component"]) or "")
        if not key or key in dropped or key == "or":
            continue
        for n in r["notes"]:
            c = clean_text(n)
            if c:
                want_notes.add(c)
    have_notes = {n["text"] for i in m["items"] for n in i["notes"]}
    lost = want_notes - have_notes
    print("3. notes: %d distinct, missing: %d" % (len(want_notes), len(lost)))
    for n in list(lost)[:5]:
        fails.append("missing note %r" % n[:60])

    # 4. structural --------------------------------------------------------
    ids = [i["id"] for i in m["items"]]
    if len(ids) != len(set(ids)):
        fails.append("duplicate item ids")
    known = {p["id"] for p in m["projects"]}
    for i in m["items"]:
        for u in i["used_by"]:
            if u["project"] not in known:
                fails.append("unknown project %s on %s" % (u["project"], i["id"]))
        for s in i["sources"]:
            if not s["url"].startswith("http"):
                fails.append("bad url on %s: %s" % (i["id"], s["url"]))
    print("4. structural checks: %s"
          % ("ok" if not fails else "%d problem(s)" % len(fails)))

    if fails:
        print("\nFAILURES")
        for f in fails[:40]:
            print("  -", f)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
