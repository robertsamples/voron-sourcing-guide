"""Stage 3: QA + review outputs derived from the master structure.

  review_name_clusters.csv  near-identical item names that are still separate
                            items -- candidates for data/aliases.json
  review_conflicts.csv      items used by >1 tab whose tabs point at different
                            product links for the same role
  review_unlinked.csv       item/tab combinations with no link at all
  report.md                 headline numbers
"""
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MASTER = "data/voron_sourcing_master.json"


def tokens(k):
    return set(k.split())


def spec_tokens(k):
    """Tokens carrying a number -- m3x8, 20t, 4a, 43645-0200, 6mm ...

    Two rows whose spec tokens differ are different parts (2- vs 3-position,
    4A vs 8A fuse), however similar the words around them look.
    """
    return frozenset(t for t in k.split() if any(c.isdigit() for c in t))


# words that, on their own, mark two otherwise identical names as different
# parts -- head types, genders, axes, Misumi machining codes, materials
DISTINGUISHERS = {
    "shcs", "bhcs", "fhcs", "sbhcs", "cap", "grub", "set",
    "male", "female", "plug", "receptacle", "receptical", "housing", "pin",
    "brass", "steel", "hardened", "plated", "black", "silver",
    "x", "y", "z", "a", "b", "left", "right", "top", "bottom", "upper", "lower",
    "tpw", "ltp", "rcp", "av", "ah", "bh", "primary", "secondary",
    "short", "long", "tall", "open", "closed", "loop", "idler", "pulley",
    "nozzle", "12v", "24v",
}


def cluster_candidates(items, threshold=0.72):
    """Near-duplicate search over normalised names, blocked on spec tokens."""
    by_spec = defaultdict(list)
    for i in items:
        by_spec[spec_tokens(i["name_key"])].append(i)
    pairs = {}
    for spec, group in by_spec.items():
        if len(group) < 2 or len(group) > 200:
            continue
        for a_i in range(len(group)):
            for b_i in range(a_i + 1, len(group)):
                a, b = group[a_i], group[b_i]
                wa = tokens(a["name_key"]) - set(spec)
                wb = tokens(b["name_key"]) - set(spec)
                if not wa and not wb:
                    jac = 1.0
                else:
                    jac = len(wa & wb) / float(len(wa | wb) or 1)
                ratio = SequenceMatcher(None, a["name_key"], b["name_key"]).ratio()
                if (wa ^ wb) & DISTINGUISHERS:
                    continue
                score = max(jac, ratio)
                if score >= threshold:
                    pairs[tuple(sorted((a["id"], b["id"])))] = (score, a, b)
    return sorted(pairs.values(), key=lambda p: -p[0])


def main():
    with open(MASTER, encoding="utf-8") as fh:
        m = json.load(fh)
    items = m["items"]
    raw_rows = len(json.load(open("data/raw_extract.json", encoding="utf-8"))["rows"])
    proj_status = {p["id"]: p["status"] for p in m["projects"]}

    # ---- near-duplicate names --------------------------------------------
    with open("data/review_name_clusters.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["score", "id_a", "name_a", "projects_a",
                    "id_b", "name_b", "projects_b", "same_category"])
        cands = cluster_candidates(items)
        n_clusters = len(cands)
        for score, a, b in cands:
            w.writerow(["%.3f" % score, a["id"], a["name"],
                        " ".join(u["project"] for u in a["used_by"]),
                        b["id"], b["name"],
                        " ".join(u["project"] for u in b["used_by"]),
                        "yes" if a["category"] == b["category"] else ""])

    # ---- tabs that disagree about what to buy -----------------------------
    # A conflict is not "this part has several links" (offering alternatives is
    # fine) -- it is "tab A and tab B, for the same part and the same role,
    # point the builder at different products".
    conflicts = []
    for i in items:
        if len(i["used_by"]) < 2:
            continue
        for role in ("recommended", "alternative"):
            by_proj = defaultdict(list)
            for s in i["sources"]:
                if role not in s["roles"]:
                    continue
                for p in s["projects"]:
                    by_proj[p].append(s)
            if len(by_proj) < 2:
                continue
            variants = defaultdict(list)   # url-set signature -> projects
            for p, ss in by_proj.items():
                variants[frozenset(s["url"] for s in ss)].append(p)
            if len(variants) > 1:
                conflicts.append((i, role, by_proj, variants))

    with open("data/review_conflicts.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "item_name", "category", "role", "n_variants",
                    "variant", "projects", "project_statuses", "vendors", "urls"])
        for i, role, by_proj, variants in conflicts:
            ordered = sorted(variants.items(), key=lambda kv: -len(kv[1]))
            for n, (sig, projs) in enumerate(ordered, 1):
                srcs = [s for s in i["sources"] if s["url"] in sig]
                w.writerow([i["id"], i["name"], i["category"], role, len(variants),
                            n, " ".join(sorted(projs)),
                            " ".join(sorted({proj_status.get(p, "?") for p in projs})),
                            " | ".join(sorted({s["vendor_name"] or "?" for s in srcs})),
                            " | ".join(sorted(sig))])

    # ---- item/tab combinations with nothing to buy ------------------------
    with open("data/review_unlinked.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "item_name", "category", "project",
                    "project_status", "text_only_sources"])
        for i in items:
            linked = {p for s in i["sources"] for p in s["projects"]}
            for u in i["used_by"]:
                if u["project"] in linked:
                    continue
                txt = [x["text"] for x in i["unlinked_sources"]
                       if u["project"] in x["projects"]]
                w.writerow([i["id"], i["name"], i["category"], u["project"],
                            proj_status.get(u["project"], ""), " | ".join(txt)])

    # ---- headline numbers -------------------------------------------------
    n_sources = sum(len(i["sources"]) for i in items)
    n_urls = len({s["url"] for i in items for s in i["sources"]})
    shared = [i for i in items if len(i["used_by"]) > 1]
    conflict_items = {c[0]["id"] for c in conflicts}
    cur = {p["id"] for p in m["projects"] if p["status"] == "current"}
    conflict_current = {c[0]["id"] for c in conflicts
                        if len({v for sig, projs in c[3].items()
                                for v in [tuple(sorted(sig))] if set(projs) & cur}) > 1}
    short_links = sum(1 for i in items for s in i["sources"]
                      if s["vendor"] in ("aliexpress", "amazon")
                      and ("s.click." in s["url"] or "amzn.to" in s["url"]
                           or "a.aliexpress.com" in s["url"]))
    aff = sum(1 for i in items for s in i["sources"] if s["affiliate"])
    dead_hosts = Counter(s["vendor_name"] for i in items for s in i["sources"])
    per_proj = Counter(u["project"] for i in items for u in i["used_by"])

    lines = []
    add = lines.append
    add("# Unified sourcing data - extraction report\n")
    add("| metric | value |")
    add("| --- | ---: |")
    add("| tabs read | %d |" % len(m["projects"]))
    add("| spreadsheet rows read | %d |" % raw_rows)
    add("| distinct components | %d |" % len(items))
    add("| components used by more than one tab | %d |" % len(shared))
    add("| distinct product links | %d |" % n_urls)
    add("| component x link entries | %d |" % n_sources)
    add("| links that are affiliate links | %d |" % aff)
    add("| components whose tabs disagree on the link | %d |" % len(conflict_items))
    add("| ... counting only the currently-shipping tabs | %d |" % len(conflict_current))
    add("| shortened links that cannot be compared without resolving | %d |" % short_links)
    add("| near-duplicate name pairs still awaiting a human call | %d |" % n_clusters)
    add("")
    add("## Components per tab\n")
    add("| tab | status | components |")
    add("| --- | --- | ---: |")
    for p in m["projects"]:
        add("| %s | %s | %d |" % (p["name"], p["status"], per_proj.get(p["id"], 0)))
    add("")
    add("## Links per vendor\n")
    add("| vendor | links |")
    add("| --- | ---: |")
    for v, n in dead_hosts.most_common(25):
        add("| %s | %d |" % (v, n))
    add("")
    with open("data/report.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print("conflicting item/role combinations: %d (items: %d, current-tab items: %d)"
          % (len(conflicts), len(conflict_items), len(conflict_current)))
    print("wrote data/review_name_clusters.csv, review_conflicts.csv, "
          "review_unlinked.csv, report.md")


if __name__ == "__main__":
    main()
