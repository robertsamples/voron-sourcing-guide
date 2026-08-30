"""Stage 2: fold the flattened rows into one non-redundant master structure.

Items are merged across tabs on a normalised name key (plus a curated alias
file); links are merged on a canonicalised URL. Every merged fact keeps a
`seen` provenance list pointing back at sheet/row/column.
"""
import csv
import json
import os
import re
import sys
import unicodedata
from collections import Counter, OrderedDict, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalize import (clean_text, clean_size, name_key, canonical_url,
                       url_key, is_affiliate, vendor_of, clean_qty, unwrap,
                       misumi_part)

RAW = "data/raw_extract.json"
ALIASES = "data/aliases.json"
OUT_JSON = "data/voron_sourcing_master.json"
OUT_ITEMS_CSV = "data/master_items.csv"
OUT_LINKS_CSV = "data/master_links.csv"
OUT_REVIEW = "data/review_name_clusters.csv"

# sheet -> (id, display name, kind, status)
PROJECTS = {
    "VORON 2.4":            ("voron-2.4", "VORON 2.4", "printer", "current"),
    "VORON Trident":        ("voron-trident", "VORON Trident", "printer", "current"),
    "VORON 0.2":            ("voron-0.2", "VORON 0.2", "printer", "current"),
    "Voron Switchwire":     ("voron-switchwire", "VORON Switchwire", "printer", "current"),
    "VORON Cascade":        ("voron-cascade", "VORON Cascade", "printer", "empty"),
    "Voron Stealthburner":  ("stealthburner", "Stealthburner", "toolhead", "current"),
    "VORON Optional Parts": ("optional-parts", "Optional Parts", "addon", "current"),
    "VORON Tools":          ("tools", "Tools", "addon", "current"),
    "VORON Afterburner":    ("afterburner", "Afterburner", "toolhead", "legacy"),
    "VORON M4":             ("voron-m4", "VORON M4", "printer", "legacy"),
    "VORON Mobius 3dot1":   ("mobius-3.1", "Mobius 3.1", "extruder", "legacy"),
    "VORON JetPack":        ("jetpack", "JetPack", "addon", "legacy"),
    "VORON 0":              ("voron-0", "VORON 0", "printer", "legacy"),
    "VORON 0.1":            ("voron-0.1", "VORON 0.1", "printer", "legacy"),
    "VORON 1.8":            ("voron-1.8", "VORON 1.8", "printer", "legacy"),
    "VORON 1.6.2":          ("voron-1.6.2", "VORON 1.6.2", "printer", "legacy"),
    "VORON 2.2":            ("voron-2.2", "VORON 2.2", "printer", "legacy"),
}

# vendor-column header -> role
ROLES = {
    "recommended": "recommended",
    "alternative source": "alternative",
    "alternative source 2": "alternative",
    "alt source 2": "alternative",
    "budget source": "budget",
    "non- afiliate links": "non_affiliate",
    "prusa mk3(s) sourced": "prusa_salvage",
}
ROLE_ORDER = ["recommended", "alternative", "budget", "non_affiliate",
              "prusa_salvage", "unknown"]

CATEGORY_MAP = {
    "misc.": "Misc", "misc": "Misc",
    "hot end": "Hotend", "hotend": "Hotend",
    "hardware": "Hardware", "fasteners": "Fasteners",
    "kit": "Kits", "kits": "Kits",
    "frame - tophat": "Frame",
    "controller - duet family": "Controller",
    "controller - skr family": "Controller",
    "controller": "Controller",
    "adxl optional": "Electronics",
    "vibration management": "Vibration Management",
    "extruder hardware": "Extruder",
    "extruder": "Extruder",
    "cable chain": "Cables",
    "pocketwatch": "Printed Parts",
    "spares": "Misc",
    "alternative": "Misc",
}

DROP_NAME_RE = re.compile(
    r"^(or|as an (amazon|aliexpress)|\W*$)", re.I)
AFFILIATE_LABEL_RE = re.compile(r"^\W*\[?\s*affi?li?ate\s+link\s*\]?\W*$", re.I)


def slug(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "item"


def norm_category(c):
    c = clean_text(c)
    if not c:
        return None
    return CATEGORY_MAP.get(c.lower(), c)


def role_of(slot):
    return ROLES.get((slot or "").strip().lower(), "unknown")


def is_drop_row(comp):
    c = clean_text(comp) or ""
    if not c:
        return True
    if c.strip().upper() == "OR":
        return True
    if re.match(r"^\W*as an (amazon|aliexpress)", c, re.I):
        return True
    return False


def choice_groups(rows):
    """Rows joined by an `OR` separator row are alternatives to each other."""
    groups = {}
    by_sheet = defaultdict(list)
    for r in rows:
        by_sheet[r["sheet"]].append(r)
    for sheet, rs in by_sheet.items():
        rs.sort(key=lambda r: r["row"])
        i = 0
        while i < len(rs):
            if (clean_text(rs[i]["component"]) or "").upper() == "OR":
                prev = rs[i - 1] if i else None
                nxt = rs[i + 1] if i + 1 < len(rs) else None
                if prev is not None and nxt is not None:
                    gid = groups.get((sheet, prev["row"]))
                    if gid is None:
                        gid = "%s#or%d" % (PROJECTS.get(sheet, (sheet,))[0], prev["row"])
                        groups[(sheet, prev["row"])] = gid
                    groups[(sheet, nxt["row"])] = gid
            i += 1
    return groups


def pair_entries(entries):
    """Group a row's vendor cells into sources, folding `[Affiliate Link]`
    cells into the named link immediately to their left in the same slot."""
    out = []
    for e in sorted(entries, key=lambda e: (len(e["col"]), e["col"])):
        text = clean_text(e["text"])
        url = e["url"]
        aff_label = bool(text and AFFILIATE_LABEL_RE.match(text))
        if aff_label and out and out[-1]["slot"] == e["slot"] and out[-1]["url"]:
            if url:
                out[-1]["affiliate_urls"].append(url)
            out[-1]["cells"].append(e["col"])
            continue
        out.append({
            "slot": e["slot"], "col": e["col"], "label": text, "url": url,
            "affiliate_urls": [], "cells": [e["col"]],
        })
    return out


def load_aliases():
    """-> (merge map, preferred names, dropped keys, split rules)"""
    empty = ({}, {}, set(), {"categories": set(), "names": set()})
    if not os.path.exists(ALIASES):
        return empty
    with open(ALIASES, encoding="utf-8") as fh:
        data = json.load(fh)
    merge = {}
    for k, v in data.get("merge", {}).items():
        ak, ck = name_key(k), name_key(v)
        if ak and ck:
            merge[ak] = ck
    names = {}
    for k, v in data.get("names", {}).items():
        if name_key(k):
            names[name_key(k)] = clean_text(v)
    drop = {name_key(k) for k in data.get("drop", []) if name_key(k)}
    sp = data.get("split_by_project", {})
    split = {
        "categories": {c.strip().lower() for c in sp.get("categories", [])},
        "names": {name_key(n) for n in sp.get("names", []) if name_key(n)},
    }
    return merge, names, drop, split


def main():
    with open(RAW, encoding="utf-8") as fh:
        raw = json.load(fh)
    rows = raw["rows"]
    aliases, pref_names, dropped, split = load_aliases()
    groups = choice_groups(rows)

    items = OrderedDict()   # key -> item dict
    vendors = {}

    for r in rows:
        sheet = r["sheet"]
        if sheet not in PROJECTS:
            continue
        proj_id, proj_name, kind, status = PROJECTS[sheet]
        comp = clean_text(r["component"])
        if is_drop_row(comp):
            continue
        key = name_key(comp)
        if not key or key in dropped:
            continue
        key = aliases.get(key, key)
        # An extrusion is identified by its Misumi part number, however the tab
        # chose to write the row ("HFSB5-2020-340" vs "Misumi 2020 x 340mm -
        # HFSB5-2020-340"). Machining codes stay part of the key -- a drilled
        # length is a separate line item with its own quantity -- but the base
        # product they share is recorded so they group for sourcing.
        mis = misumi_part(comp)
        if mis:
            key = name_key(mis[0])
        base_key, scope = key, None
        cat = norm_category(r["category"])
        if (key in split["names"]
                or (cat and cat.lower() in split["categories"])):
            # machine-specific part: never share it between tabs
            scope = proj_id
            key = "%s @%s" % (key, proj_id)

        it = items.get(key)
        if it is None:
            it = items[key] = {
                "key": key,
                "base_key": base_key,
                "scope": scope,
                "base_product": mis[1] if mis else None,
                "machining": mis[2] if mis else None,
                "names": Counter(),
                "categories": Counter(),
                "standards": Counter(),
                "used_by": OrderedDict(),
                "sources": OrderedDict(),   # canonical url -> source
                "unlinked": OrderedDict(),  # label -> unlinked source
                "notes": OrderedDict(),
            }
        it["names"][comp] += 1
        if cat:
            it["categories"][cat] += 1
        std = clean_text(r["standard"])
        if std:
            it["standards"][std] += 1

        use = it["used_by"].setdefault(proj_id, {
            "project": proj_id, "sheet": sheet, "rows": [], "qty": [],
            "choice_group": None, "flags": [],
        })
        use["rows"].append(r["row"])
        qd, qn = clean_qty(r["qty"])
        size = clean_size(r["size"])
        if qd is not None:
            entry = {"size": size or "All", "qty": qd}
            if qn is not None:
                entry["qty_num"] = qn
            if entry not in use["qty"]:
                use["qty"].append(entry)
        g = groups.get((sheet, r["row"]))
        if g:
            use["choice_group"] = g

        for note in r["notes"]:
            n = clean_text(note)
            if not n:
                continue
            nn = it["notes"].setdefault(n, {"text": n, "projects": []})
            if proj_id not in nn["projects"]:
                nn["projects"].append(proj_id)

        for s in pair_entries(r["entries"]):
            role = role_of(s["slot"])
            if not s["url"]:
                if not s["label"]:
                    continue
                u = it["unlinked"].setdefault(s["label"], {
                    "text": s["label"], "roles": [], "projects": [],
                })
                if role not in u["roles"]:
                    u["roles"].append(role)
                if proj_id not in u["projects"]:
                    u["projects"].append(proj_id)
                continue

            cu = canonical_url(s["url"])
            uk = url_key(s["url"])
            src = it["sources"].get(uk)
            if src is None:
                vid, vname = vendor_of(cu, s["label"])
                src = it["sources"][uk] = {
                    "url": cu,
                    "url_key": uk,
                    "vendor": vid,
                    "vendor_name": vname,
                    "labels": [],
                    "roles": [],
                    "projects": [],
                    "affiliate_urls": [],
                    "raw_urls": [],
                    "affiliate": is_affiliate(s["url"], s["label"]),
                    "seen": [],
                }
            if s["label"] and s["label"] not in src["labels"]:
                src["labels"].append(s["label"])
            if role not in src["roles"]:
                src["roles"].append(role)
            if proj_id not in src["projects"]:
                src["projects"].append(proj_id)
            raw_u = unwrap(s["url"])
            if raw_u != cu and raw_u not in src["raw_urls"]:
                src["raw_urls"].append(raw_u)
            for a in s["affiliate_urls"]:
                ca = canonical_url(a)
                if ca and ca not in src["affiliate_urls"]:
                    src["affiliate_urls"].append(ca)
            src["seen"].append({"project": proj_id, "sheet": sheet,
                                "row": r["row"], "cols": s["cells"],
                                "slot": s["slot"]})
            if src["vendor"]:
                v = vendors.setdefault(src["vendor"], {
                    "id": src["vendor"], "name": src["vendor_name"], "links": 0})
                v["links"] += 1

    # ---- finalise ---------------------------------------------------------
    used_ids = set()
    out_items = []
    for key, it in items.items():
        name = pref_names.get(it["base_key"]) or it["names"].most_common(1)[0][0]
        cat = it["categories"].most_common(1)[0][0] if it["categories"] else None
        base = "%s.%s" % (slug(cat or "misc"), slug(name))
        if it["scope"]:
            base = "%s.%s" % (base, it["scope"])
        iid, n = base, 2
        while iid in used_ids:
            iid, n = "%s-%d" % (base, n), n + 1
        used_ids.add(iid)

        srcs = list(it["sources"].values())
        srcs.sort(key=lambda s: (ROLE_ORDER.index(min(s["roles"],
                                key=lambda r: ROLE_ORDER.index(r))),
                                 -len(s["projects"]), s["vendor_name"] or ""))
        out_items.append(OrderedDict([
            ("id", iid),
            ("name", name),
            ("name_key", key),
            ("base_key", it["base_key"]),
            ("scope", it["scope"]),
            ("base_product", it["base_product"]),
            ("machining", it["machining"]),
            ("aliases", [n for n, _ in it["names"].most_common() if n != name]),
            ("category", cat),
            ("categories", [c for c, _ in it["categories"].most_common()]),
            ("standard", it["standards"].most_common(1)[0][0] if it["standards"] else None),
            ("standards", [s for s, _ in it["standards"].most_common()]),
            ("used_by", list(it["used_by"].values())),
            ("sources", srcs),
            ("unlinked_sources", list(it["unlinked"].values())),
            ("notes", list(it["notes"].values())),
        ]))

    out_items.sort(key=lambda i: (i["category"] or "zz", i["name"].lower()))

    master = OrderedDict([
        ("schema_version", "0.1"),
        ("source_workbook", "Published Voron Development Team Sourcing Guide.xlsx"),
        ("projects", [OrderedDict([("id", p[0]), ("name", p[1]), ("kind", p[2]),
                                   ("status", p[3]), ("sheet", s)])
                      for s, p in PROJECTS.items()]),
        ("vendors", [vendors[k] for k in sorted(vendors,
                     key=lambda k: -vendors[k]["links"])]),
        ("roles", ROLE_ORDER),
        ("items", out_items),
    ])
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(master, fh, indent=1, ensure_ascii=False)

    # ---- flat CSV views ---------------------------------------------------
    proj_ids = [p[0] for p in PROJECTS.values()]
    with open(OUT_ITEMS_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "category", "name", "scope", "standard", "aliases",
                    "n_projects", "n_links", "projects"]
                   + ["qty:" + p for p in proj_ids])
        for i in out_items:
            qty = {}
            for u in i["used_by"]:
                qty[u["project"]] = "; ".join(
                    ("%s@%s" % (q["qty"], q["size"]) if q["size"] not in (None, "All")
                     else q["qty"]) for q in u["qty"])
            w.writerow([i["id"], i["category"], i["name"], i["scope"] or "",
                        i["standard"],
                        " | ".join(i["aliases"]), len(i["used_by"]),
                        len(i["sources"]),
                        " ".join(u["project"] for u in i["used_by"])]
                       + [qty.get(p, "") for p in proj_ids])

    with open(OUT_LINKS_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "item_name", "category", "roles", "vendor",
                    "label", "url", "affiliate", "affiliate_urls", "projects"])
        for i in out_items:
            for s in i["sources"]:
                w.writerow([i["id"], i["name"], i["category"],
                            "/".join(s["roles"]), s["vendor_name"],
                            " | ".join(s["labels"]), s["url"],
                            "yes" if s["affiliate"] else "",
                            " ".join(s["affiliate_urls"]),
                            " ".join(s["projects"])])

    print("items: %d   sources: %d   vendors: %d"
          % (len(out_items), sum(len(i["sources"]) for i in out_items), len(vendors)))
    print("items shared by >1 tab: %d"
          % sum(1 for i in out_items if len(i["used_by"]) > 1))
    print("run tools/report.py for the conflict and review breakdown")


if __name__ == "__main__":
    main()
