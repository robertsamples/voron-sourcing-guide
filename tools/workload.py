"""How much work is a sourcing review, before and after de-duplication?

The question this answers: if somebody sat down to re-source the whole guide,
how many separate things would they have to look at?

A "variant" is one distinct thing to review. Two tabs that list a part under
the same name with the same links are a perfect duplicate -- one variant, one
piece of work, no matter how many tabs repeat it. Two tabs that spell the part
differently, or point at different products, are two variants: somebody has to
read both, work out they are the same part, and decide which link wins.

Three counts per component:

  variants_full  distinct (name as written, set of links) across tabs
                 -- everything a reviewer must reconcile
  variants_link  distinct sets of links, ignoring the naming
                 -- the actual sourcing decisions
  variants_name  distinct names, ignoring the links
                 -- the recognition problem

After de-duplication each component is 1 of each, by construction.
"""
import csv
import json
from collections import Counter, defaultdict

from unify import (PROJECTS, RAW, is_drop_row, load_aliases, norm_category,
                   pair_entries, role_of)
from normalize import canonical_url, clean_text, name_key, url_key

OUT_CSV = "data/workload_by_component.csv"


def main():
    raw = json.load(open(RAW, encoding="utf-8"))
    aliases, pref_names, dropped, split = load_aliases()

    # component key -> project -> (name as written, {(url, role)})
    comp = defaultdict(lambda: defaultdict(lambda: [None, set()]))
    rows_seen = 0
    for r in raw["rows"]:
        if r["sheet"] not in PROJECTS:
            continue
        name = clean_text(r["component"])
        if is_drop_row(name):
            continue
        key = name_key(name)
        if not key or key in dropped:
            continue
        key = aliases.get(key, key)
        rows_seen += 1
        proj = PROJECTS[r["sheet"]][0]
        cat = norm_category(r["category"])
        if key in split["names"] or (cat and cat.lower() in split["categories"]):
            key = "%s @%s" % (key, proj)   # machine-specific, never shared
        slot = comp[key][proj]
        slot[0] = slot[0] or name
        for s in pair_entries(r["entries"]):
            if s["url"]:
                slot[1].add((url_key(s["url"]), role_of(s["slot"])))
            elif s["label"]:
                slot[1].add(("text:" + s["label"].lower(), role_of(s["slot"])))

    stats = []
    for key, per_proj in comp.items():
        names = {v[0] for v in per_proj.values()}
        links = {frozenset(v[1]) for v in per_proj.values()}
        full = {(v[0], frozenset(v[1])) for v in per_proj.values()}
        stats.append({
            "key": key,
            "name": sorted(names, key=len)[0],
            "tabs": len(per_proj),
            "variants_full": len(full),
            "variants_link": len(links),
            "variants_name": len(names),
            "names": sorted(names),
            "projects": sorted(per_proj),
        })
    stats.sort(key=lambda s: (-s["variants_full"], -s["tabs"], s["name"]))

    n = len(stats)
    tabs_total = sum(s["tabs"] for s in stats)
    vf = sum(s["variants_full"] for s in stats)
    vl = sum(s["variants_link"] for s in stats)
    vn = sum(s["variants_name"] for s in stats)
    perfect_dupes = tabs_total - vf

    def pct(a, b):
        return "%.0f%%" % (100.0 * a / b)

    print("component rows read                         : %5d" % rows_seen)
    print("component x tab appearances                 : %5d" % tabs_total)
    print()
    print("BEFORE de-duplication (perfect duplicates already excluded)")
    print("  distinct (name, link-set) variants        : %5d" % vf)
    print("  distinct link-sets                        : %5d" % vl)
    print("  distinct names                            : %5d" % vn)
    print("  appearances that were perfect duplicates  : %5d  (%s of appearances)"
          % (perfect_dupes, pct(perfect_dupes, tabs_total)))
    print()
    print("AFTER de-duplication")
    print("  components                                : %5d" % n)
    print()
    print("  reduction, reconciliation work  %d -> %d   (%.1fx, %s less)"
          % (vf, n, float(vf) / n, pct(vf - n, vf)))
    print("  reduction from the raw workbook %d -> %d   (%.1fx, %s less)"
          % (tabs_total, n, float(tabs_total) / n, pct(tabs_total - n, tabs_total)))
    print()

    clean = sum(1 for s in stats if s["variants_full"] == 1)
    print("components already perfectly consistent across their tabs : %d (%s)"
          % (clean, pct(clean, n)))
    multi = [s for s in stats if s["variants_full"] > 1]
    print("components needing a reconciliation decision              : %d (%s)"
          % (len(multi), pct(len(multi), n)))
    print("  ... of those, disagreeing on links                      : %d"
          % sum(1 for s in stats if s["variants_link"] > 1))
    print("  ... of those, disagreeing on the name only              : %d"
          % sum(1 for s in stats
                if s["variants_name"] > 1 and s["variants_link"] == 1))
    print()
    print("distribution of variants per component")
    for k, c in sorted(Counter(s["variants_full"] for s in stats).items()):
        print("  %2d variant%s : %4d components" % (k, " " if k == 1 else "s", c))
    shared = [s for s in stats if s["tabs"] > 1]
    sv = sum(s["variants_full"] for s in shared)
    print()
    print("where the saving actually is")
    print("  %d components live on one tab only  -> 1 variant each, no saving"
          % (n - len(shared)))
    print("  %d components live on 2+ tabs       -> %d variants collapse to %d"
          % (len(shared), sv, len(shared)))
    print("     (%.1fx, %s less work on the shared parts)"
          % (float(sv) / len(shared), pct(sv - len(shared), sv)))

    # the concrete task: how many links must be opened and checked?
    cells = sum(1 for r in raw["rows"] if r["sheet"] in PROJECTS
                for e in r["entries"] if e["url"])
    urls = len({url_key(e["url"]) for r in raw["rows"] if r["sheet"] in PROJECTS
                for e in r["entries"] if e["url"]})
    master = json.load(open("data/voron_sourcing_master.json", encoding="utf-8"))
    products = len({s["url"] for i in master["items"] for s in i["sources"]})
    print()
    print("link checking, the other half of the job")
    print("  link cells in the workbook                : %5d" % cells)
    print("  distinct URLs behind them                 : %5d  (%s less)"
          % (urls, pct(cells - urls, cells)))
    print("  distinct products, affiliate variants     : %5d  (%s less)"
          % (products, pct(cells - products, cells)))
    print("    folded into the product they point at")
    print()
    print("worst offenders")
    print("  %-46s %4s %4s %4s %4s" % ("component", "tabs", "var", "link", "name"))
    for s in stats[:15]:
        print("  %-46s %4d %4d %4d %4d"
              % (s["name"][:46], s["tabs"], s["variants_full"],
                 s["variants_link"], s["variants_name"]))

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "tabs", "variants_full", "variants_link",
                    "variants_name", "names_as_written", "projects"])
        for s in stats:
            w.writerow([s["name"], s["tabs"], s["variants_full"],
                        s["variants_link"], s["variants_name"],
                        " | ".join(s["names"]), " ".join(s["projects"])])
    print("\nwrote %s" % OUT_CSV)


if __name__ == "__main__":
    main()
