"""Sort every component by whether you could actually buy it today.

Four buckets, written both as one combined csv and as one csv each:

  all_live    every link on the component works
  partial     at least one works, at least one is dead
  none_live   the component has links and every one of them is dead
  no_links    the guide never gave a link for this component

`partial` is not a problem in itself - a component with three vendors and one
dead link is still sourceable - but it is where the guide quietly decays, so it
is worth seeing separately from the healthy ones.

A component still carrying an unchecked or unresolved link lands in `unknown`
rather than being guessed at.
"""
import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MASTER = "data/voron_sourcing_master.json"

BUCKETS = [
    ("all_live", "every link works"),
    ("partial", "some links work, some are dead"),
    ("none_live", "has links, none of them work"),
    ("no_links", "the guide never gave a link"),
    ("unknown", "still has an unresolved link"),
]
COLUMNS = ["coverage", "id", "name", "category", "standard",
           "n_links", "n_live", "n_dead", "n_unresolved",
           "live_vendors", "dead_vendors", "projects", "project_status",
           "tabs", "text_only_sources", "dead_links", "live_links"]


def classify(item):
    if not item["sources"]:
        return "no_links"
    states = Counter(s["link_ok"] for s in item["sources"])
    if states.get("maybe") or states.get("unchecked"):
        return "unknown"
    if not states.get("no"):
        return "all_live"
    if not states.get("yes"):
        return "none_live"
    return "partial"


def row_for(item, bucket, proj_status):
    live = [s for s in item["sources"] if s["link_ok"] == "yes"]
    dead = [s for s in item["sources"] if s["link_ok"] == "no"]
    unres = [s for s in item["sources"] if s["link_ok"] not in ("yes", "no")]
    projects = [u["project"] for u in item["used_by"]]
    statuses = sorted({proj_status.get(p, "?") for p in projects})
    return [
        bucket, item["id"], item["name"], item["category"], item["standard"] or "",
        len(item["sources"]), len(live), len(dead), len(unres),
        " | ".join(sorted({s["vendor_name"] or "?" for s in live})),
        " | ".join(sorted({s["vendor_name"] or "?" for s in dead})),
        " ".join(projects),
        " ".join(statuses),
        len(projects),
        " | ".join(x["text"] for x in item["unlinked_sources"]),
        " | ".join(s["url"] for s in dead),
        " | ".join(s["url"] for s in live),
    ]


def write(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(rows)


def main():
    m = json.load(open(MASTER, encoding="utf-8"))
    proj_status = {p["id"]: p["status"] for p in m["projects"]}
    current = {p["id"] for p in m["projects"] if p["status"] == "current"}

    buckets = {name: [] for name, _ in BUCKETS}
    for item in m["items"]:
        bucket = classify(item)
        buckets[bucket].append(row_for(item, bucket, proj_status))

    combined = []
    order = {name: n for n, (name, _) in enumerate(BUCKETS)}
    for name, _ in BUCKETS:
        rows = sorted(buckets[name], key=lambda r: (r[3] or "zz", r[2].lower()))
        buckets[name] = rows
        combined.extend(rows)
        if rows:
            write("data/coverage_%s.csv" % name, rows)
        elif os.path.exists("data/coverage_%s.csv" % name):
            os.remove("data/coverage_%s.csv" % name)
    write("data/coverage.csv", sorted(
        combined, key=lambda r: (order[r[0]], r[3] or "zz", r[2].lower())))

    total = len(m["items"])
    print("%d components\n" % total)
    print("  %-11s %5s  %-38s %s" % ("", "count", "", "on a current tab"))
    for name, blurb in BUCKETS:
        rows = buckets[name]
        if not rows and name == "unknown":
            continue
        on_current = sum(1 for r in rows
                         if set(r[11].split()) & current)
        print("  %-11s %5d  %-38s %d"
              % (name, len(rows), blurb, on_current))

    print("\nwrote data/coverage.csv and one csv per bucket")

    dead = buckets["none_live"] + buckets["no_links"]
    dead_current = [r for r in dead if set(r[11].split()) & current]
    print("\n%d components cannot be sourced from the guide at all "
          "(%d of them on a currently-shipping tab)"
          % (len(dead), len(dead_current)))


if __name__ == "__main__":
    main()
