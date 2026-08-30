# Voron sourcing guide — unified data structure

The published sourcing guide is one workbook with 17 tabs, each hand-maintained.
The same physical part appears on up to twelve tabs, under as many as ten
different names, pointing at different vendors. This repo turns that workbook into a
single non-redundant dataset, with every fact traceable back to the cell it came
from.

Nothing here changes what the guide recommends. This is the extraction and
de-duplication step only — the substantive review (which links are dead, which
recommendations are wrong) is the next step, and it is much cheaper to do once
against 480 components than seventeen times against 1,100 rows.

## What came out

| | |
| --- | ---: |
| tabs read | 17 |
| spreadsheet rows read | 1,103 |
| distinct components | 480 |
| components used by more than one tab | 207 |
| distinct product links | 606 |
| … plus affiliate variants of those links | 185 |
| components where tabs disagree on the link | 54 |
| … counting only the currently-shipping tabs | 22 |

Full numbers in [data/report.md](data/report.md).

Two examples of what "non-redundant" is worth:

* The M3×5×4 heat-set insert is on ten tabs under ten names — *M3 Threaded
  Insert*, *M3 Threaded Insert (M3x5mmx4mm)*, *M3 Threaded Inserts - short*,
  *M3 Heat Set Inserts (M3x5x4)*, *M3 Brass Heat Set Inserts - Short*,
  *M3 Brass Heatset Inserts - Short M3x5x4*, *M3 Brass heatstake inserts -
  short M3x5x4*, *M3 Brass Heatstake Inserts - Short (M3x5x4)*, *M3 Brass
  Heatstake Inserts* — with **four** different "Recommended" links between
  them. In the master it is one component with one set of options.
* *GT2 20T (6mm wide) Pulley (5mm bore)* — the part at the centre of the
  Gates/POWGE argument — is on ten tabs (two of them spelling it differently)
  with **three** different "Recommended" listings: two plain AliExpress items
  and one AliExpress affiliate short-link. Deciding once what that part should
  be is one edit here and ten edits in the workbook.

## How much work is a sourcing review, before and after?

`tools/workload.py` answers the practical question: if somebody sat down to
re-source the whole guide, how many separate things would they have to look at?

A **variant** is one distinct thing to review. Two tabs listing a part under the
same name with the same links are a perfect duplicate — one variant, one piece
of work, however many tabs repeat it. Two tabs that spell it differently, or
point at different products, are two variants: somebody has to read both, work
out they are the same part, and decide which link wins.

| | before | after |
| --- | ---: | ---: |
| things to reconcile (name + link-set variants) | 699 | **480** |
| … on the 207 parts that appear on more than one tab | 426 | **207** |
| distinct URLs to open and check | 775 | **606** |
| link cells in the workbook | 1,803 | — |

30% of all component appearances are perfect duplicates that cost nothing
either way. The real reduction is 699 → 480 overall, and 426 → 207 on the
shared parts — a bit over 2× on the half of the guide where duplication lives.

Where the remaining work sits after de-duplication:

* **344 of 480 components (72%)** are already perfectly consistent across every
  tab that lists them — one name, one set of links. Nothing to reconcile.
* **136 components (28%)** need a decision: 129 because tabs point at different
  products, 7 because tabs merely spell the part differently.
* The distribution is very long-tailed — 88 components have 2 variants, 31 have
  3, and a handful have 6 or more:

| component | tabs | variants | link-sets | names |
| --- | ---: | ---: | ---: | ---: |
| M3×5×4 heat-set insert | 10 | 10 | 8 | 8 |
| GT2 20T (6mm wide) pulley (5mm bore) | 10 | 7 | 6 | 3 |
| M3x6 BHCS | 12 | 6 | 6 | 1 |
| PEI + 3M 468P (200MP) | 8 | 6 | 6 | 1 |
| M2x10 self tapping screw | 7 | 6 | 6 | 4 |
| Printed Parts | 11 | 5 | 5 | 1 |
| M3 Nut / M3 Hexnut | 10 | 5 | 5 | 2 |

Per-component numbers in [data/workload_by_component.csv](data/workload_by_component.csv).

The headline is not that de-duplication halves the work once. It is that after
this pass the work is **done once and stays done** — 480 rows to maintain
instead of 1,051, and a part can no longer drift apart between tabs, because
there is only one of it.

## Layout

```
Published Voron Development Team Sourcing Guide.xlsx   source of truth (read-only)
tools/
  extract_raw.py    stage 1  xlsx  -> data/raw_extract.json      (flatten, no merging)
  normalize.py               names, vendors, URLs, quantities
  unify.py          stage 2  raw   -> data/voron_sourcing_master.json + CSV views
  report.py         stage 3  master-> review CSVs + report.md
  validate.py       stage 4  proves nothing was dropped
  workload.py       how much review work the workbook costs vs the master
data/
  raw_extract.json            every row, every cell, with sheet/row/column
  voron_sourcing_master.json  THE unified structure
  master_items.csv            one line per component, qty per tab
  master_links.csv            one line per component × link
  aliases.json                curated name merges (hand-edited)
  review_name_clusters.csv    near-duplicate names still unmerged
  review_conflicts.csv        tabs that disagree about what to buy
  review_unlinked.csv         component/tab pairs with no link at all
  report.md                   headline numbers
  workload_by_component.csv   variants per component, worst first
```

Regenerate everything:

```
python tools/extract_raw.py && python tools/unify.py && python tools/report.py && python tools/validate.py
```

Only `data/aliases.json` is hand-edited. Everything else is generated, so a
corrected workbook or a corrected alias file re-derives the whole dataset.

## The master structure

`data/voron_sourcing_master.json`:

```jsonc
{
  "schema_version": "0.1",
  "projects": [ { "id": "voron-2.4", "name": "VORON 2.4", "kind": "printer",
                  "status": "current", "sheet": "VORON 2.4" } ],
  "vendors":  [ { "id": "boltdepot", "name": "Bolt Depot", "links": 46 } ],
  "roles":    ["recommended", "alternative", "budget", "non_affiliate",
               "prusa_salvage", "unknown"],
  "items":    [ /* see below */ ]
}
```

One item:

```jsonc
{
  "id": "motion.gt2-20t-6mm-wide-pulley-5mm-bore",
  "name": "GT2 20T (6mm wide) Pulley (5mm bore)",
  "name_key": "gt2 20t 6mm wide pulley 5mm bore",   // normalised merge key
  "aliases": ["GT2 20T Pulley (6mm wide, 5mm bore)"], // other tabs' spellings
  "category": "Motion",
  "categories": ["Motion"],                          // every category seen
  "standard": "ISO 4762 / DIN 912",                  // null when not specified

  // which builds need it, how many, and at which build size
  "used_by": [
    { "project": "voron-2.4", "sheet": "VORON 2.4", "rows": [31],
      "qty": [ { "size": "All", "qty": "3", "qty_num": 3 } ],
      "choice_group": null }
  ],

  // one entry per distinct product, NOT per spreadsheet cell
  "sources": [
    { "url": "https://www.aliexpress.com/item/32226562320.html",
      "url_key": "https://aliexpress.com/item/32226562320.html",
      "vendor": "aliexpress", "vendor_name": "AliExpress",
      "labels": ["POWGE"],                     // link texts used across tabs
      "roles": ["recommended"],                // which columns it appeared in
      "projects": ["voron-2.4", "voron-m4", "voron-2.2"],
      "affiliate": false,
      "affiliate_urls": ["https://s.click.aliexpress.com/e/E1Y0mkUu"],
      "raw_urls": ["https://www.aliexpress.com/item/Sale-10pcs-GT2-…/32226562320.html"],
      "seen": [ { "project": "voron-2.4", "sheet": "VORON 2.4",
                  "row": 31, "cols": ["F","G"], "slot": "Recommended" } ] }
  ],

  "unlinked_sources": [ { "text": "Print Yourself", "roles": ["recommended"],
                          "projects": ["voron-2.4"] } ],
  "notes": [ { "text": "…", "projects": ["voron-2.4"] } ]
}
```

Design decisions worth knowing:

* **Links are deduplicated by product, not by string.** Tracking junk (`spm`,
  `algo_pvid`, `btsid`, `utm_*`, Amazon `ref`/`psc`) is stripped, Google
  redirect wrappers are unwrapped, and AliExpress / Amazon / McMaster URLs are
  reduced to item-id form, so the same product written six different ways
  collapses to one entry. `raw_urls` keeps the originals.
* **Affiliate links hang off the product, not beside it.** The guide's
  `[Affiliate Link]` cells are folded into the link they sit next to, so an
  item has one entry per product with its affiliate variants attached, rather
  than two half-entries.
* **Roles are the guide's own column headings**, normalised. The tabs use
  seven different heading schemes (`Recommended`, `Alternative Source 2`,
  `Alt Source 2`, `Budget Source`, `NON- Afiliate Links`, `Prusa MK3(s)
  Sourced`); `roles` records which of them a link appeared under, per link.
* **Quantities are per project and per build size.** `250³`/`300³`/`350³`
  columns become `size`; ranges the spreadsheet mangled into dates
  (`2022-02-04` for a `2-4` range) are restored.
* **`choice_group`** marks rows the guide joined with an `OR` separator —
  e.g. complete BMG clone *or* BMG insides *or* Bondtech gear kit.
* **Everything keeps provenance.** Every link, note and quantity carries the
  sheet, row and column it came from, so any merge can be checked or reverted.

## How names are merged

Two rows are the same component when their normalised names match: lowercase,
punctuation collapsed, `M3 x 8` → `m3x8`, `2.0` → `2`. That handles the bulk of
it. Anything close but not equal is left separate and written to
`data/review_name_clusters.csv` for a human to judge; confirmed merges go into
`data/aliases.json` and are applied on the next run.

The review file deliberately keeps look-alikes that must **not** merge — SHCS vs
BHCS vs FHCS, 2- vs 3-position connectors, 4A vs 8A fuses, acrylic vs
polycarbonate panels. Pairs whose only difference is a number, a head type, a
gender or an axis are filtered out of the candidate list automatically. The
obvious merges are already in `aliases.json`; **16** judgement calls remain in
the review file, and none of them change what a builder would buy today.

## Verification

`tools/validate.py` re-reads the workbook and the master and checks:

1. every URL in the workbook survives into the master — **775 / 775**
   (606 product links + their affiliate and pre-canonical variants)
2. every (component, tab) pair appears in some item's `used_by` — **1003 / 1003**
3. every note survives — **204 / 204**
4. ids unique, projects known, URLs well-formed — **ok**

## Known limits

* **142 shortened links can't be compared without resolving them.**
  `s.click.aliexpress.com/e/…` and `amzn.to/…` are opaque, so a shortened link
  and the plain product link for the same item look like two different
  products. Resolving them (one HTTP round-trip each) would collapse more
  duplicates — deliberately not done here so this pass stays offline and
  reproducible.
* **No link has been checked for being alive.** That is the next pass.
* **Categories are the guide's own**, lightly normalised (`Misc.` → `Misc`,
  `Hot End` → `Hotend`, `Controller - Duet Family` → `Controller`). They are
  inconsistent between tabs and deserve a proper taxonomy.
* **The Cascade tab is empty** in the workbook — it survives as a project with
  zero components.
* **Switchwire has no `Recommended` column at all**; its first vendor column is
  headed `Alternative Source`, so its links are recorded as `alternative`. That
  is what the guide says, not a bug in the extraction.

## Next steps this enables

1. Link liveness + price check across 606 links instead of ~1,800 link cells.
2. One decision per part, applied everywhere — the Gates/POWGE tooth-profile
   mismatch is a single edit to one component, not seventeen tab edits.
3. Generate each tab's sheet from the master. `used_by` already carries
   per-project quantity, build size and row order, and `sources` carries the
   roles each tab's columns need.
