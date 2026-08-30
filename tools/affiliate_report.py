"""Compare dead AliExpress affiliate short links against the plain product link.

The guide pairs many links with an `[Affiliate Link]` cell next to them:
`s.click.aliexpress.com/e/_xxxx` beside `aliexpress.com/item/1234.html`. The two
rot independently - an affiliate code can expire while the product it points at
is still on sale, and vice versa.

That distinction matters for a fix. If the product link is fine, the repair is
to drop or regenerate one affiliate code. If both are gone, the part genuinely
needs re-sourcing.

Writes data/review_dead_affiliates.csv and the note lines that go into the
master (tools/unify.py picks them up).
"""
import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MASTER = "data/voron_sourcing_master.json"
STATUS = "data/link_status.json"
OUT = "data/review_dead_affiliates.csv"

SHORT = ("s.click.aliexpress", "a.aliexpress")


def is_short(url):
    return any(s in url for s in SHORT)


def main():
    m = json.load(open(MASTER, encoding="utf-8"))
    st = json.load(open(STATUS, encoding="utf-8"))["links"]

    def status(u):
        return (st.get(u) or {}).get("status", "unchecked")

    rows, tally = [], Counter()
    for item in m["items"]:
        for s in item["sources"]:
            base, base_st = s["url"], status(s["url"])
            dead_affiliates = [a for a in s["affiliate_urls"]
                               if is_short(a) and status(a) == "no"]
            live_affiliates = [a for a in s["affiliate_urls"]
                               if is_short(a) and status(a) == "yes"]

            if dead_affiliates and not is_short(base):
                if base_st == "yes":
                    verdict = "affiliate dead, product link fine"
                elif base_st == "no":
                    verdict = "both dead"
                else:
                    verdict = "affiliate dead, product link unresolved"
                tally[verdict] += 1
                rows.append([verdict, item["id"], item["name"], base, base_st,
                             " | ".join(dead_affiliates),
                             " | ".join(live_affiliates),
                             " ".join(s["projects"])])

            elif is_short(base) and base_st == "no":
                # the guide only ever gave an affiliate code for this one, so
                # there is no product link to fall back on and the code cannot
                # be resolved now that it is dead
                verdict = "only link is a dead affiliate code"
                tally[verdict] += 1
                rows.append([verdict, item["id"], item["name"], base, base_st,
                             base, "", " ".join(s["projects"])])

    order = {"affiliate dead, product link fine": 0,
             "affiliate dead, product link unresolved": 1,
             "only link is a dead affiliate code": 2,
             "both dead": 3}
    rows.sort(key=lambda r: (order.get(r[0], 9), r[2].lower()))

    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["verdict", "item_id", "item_name", "product_link",
                    "product_link_status", "dead_affiliate_links",
                    "live_affiliate_links", "projects"])
        w.writerows(rows)

    print("dead AliExpress affiliate codes, by what the product link says\n")
    for k in sorted(tally, key=lambda k: order.get(k, 9)):
        print("  %-42s %d" % (k, tally[k]))
    print("\nwrote %s" % OUT)

    fine = [r for r in rows if r[0] == "affiliate dead, product link fine"]
    if fine:
        print("\nproduct still on sale, only the affiliate code is dead:")
        for r in fine:
            print("  %-46s %s" % (r[2][:46], r[3][:62]))


if __name__ == "__main__":
    main()
