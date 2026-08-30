"""Write canonical.csv - one line per component, with the links that still work.

The flat view of the master: every component under its canonical name, the tabs
that need it, how many of each, and its live links. Dead links are not listed;
the coverage column says whether any were lost.

    python tools/build_canonical_csv.py
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from build_xlsx import label_for, live_sources, text_sources  # noqa: E402
from coverage_report import classify  # noqa: E402

MASTER = "data/voron_sourcing_master.json"
OUT = "canonical.csv"


def qty_for(use):
    """`3` when a component needs the same count at every build size."""
    if not use["qty"]:
        return ""
    parts = []
    for q in use["qty"]:
        size = q.get("size")
        parts.append("%s@%s" % (q["qty"], size)
                     if size and size != "All" else str(q["qty"]))
    return "; ".join(dict.fromkeys(parts))


def main():
    m = json.load(open(MASTER, encoding="utf-8"))
    projects = [p["id"] for p in m["projects"]]
    status = {p["id"]: p["status"] for p in m["projects"]}
    items = m["items"]

    widest = max((len(live_sources(i)) for i in items), default=0)
    header = (["id", "name", "category", "standard", "coverage",
               "scope", "base_product", "machining",
               "n_tabs", "tabs", "current_tabs", "n_live_links", "vendors",
               "text_only_sources", "aliases", "notes"]
              + ["qty:" + p for p in projects]
              + ["link_%d" % (n + 1) for n in range(widest)]
              + ["link_%d_url" % (n + 1) for n in range(widest)])

    rows = []
    for i in items:
        live = live_sources(i)
        tabs = [u["project"] for u in i["used_by"]]
        qty = {u["project"]: qty_for(u) for u in i["used_by"]}
        texts = sorted({t for u in i["used_by"]
                        for t in text_sources(i, u["project"])})
        rows.append(
            [i["id"], i["name"], i["category"], i["standard"] or "",
             classify(i), i["scope"] or "", i.get("base_product") or "",
             i.get("machining") or "",
             len(tabs), " ".join(tabs),
             " ".join(t for t in tabs if status.get(t) == "current"),
             len(live),
             " | ".join(dict.fromkeys(s["vendor_name"] or "?" for s in live)),
             " | ".join(texts),
             " | ".join(i["aliases"]),
             " / ".join(n["text"] for n in i["notes"])]
            + [qty.get(p, "") for p in projects]
            + [label_for(s) for s in live] + [""] * (widest - len(live))
            + [s["url"] for s in live] + [""] * (widest - len(live)))

    rows.sort(key=lambda r: ((r[2] or "zz").lower(), r[1].lower()))
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)

    live_total = sum(r[11] for r in rows)
    print("wrote %s" % OUT)
    print("  %d components, %d live links, up to %d per component"
          % (len(rows), live_total, widest))
    print("  %d components have no live link"
          % sum(1 for r in rows if r[11] == 0))


if __name__ == "__main__":
    main()
