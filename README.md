# voron-sourcing-unified

Scripts that turn the published Voron sourcing guide (one xlsx, 17 tabs) into a
single deduplicated dataset.

Each tab is maintained by hand, so the same part shows up on many tabs under
different names with different links. This flattens all of that into one entry
per component, keeping a pointer back to every cell it came from.

Nothing here changes what the guide recommends. It's just extraction and dedup,
so that reviewing the guide is one pass over 485 components instead of seventeen
passes over 1051 rows.

## Running it

```
python tools/extract_raw.py    # xlsx -> data/raw_extract.json
python tools/unify.py          # -> data/voron_sourcing_master.json + csv views
python tools/report.py         # review csvs + data/report.md
python tools/validate.py       # checks nothing was dropped
python tools/workload.py       # before/after review effort

python tools/check_links.py    # visits every link, writes data/link_status.json
python tools/unify.py          # rerun to fold the verdicts into the master
```

Needs `openpyxl`. `data/aliases.json` is the only file meant to be edited by
hand; everything else in `data/` is generated.

## Files

```
tools/extract_raw.py   flatten every tab (per-tab layouts, merged cells,
                       =HYPERLINK() formulas, size-variant rows)
tools/normalize.py     name / vendor / url / quantity normalization
tools/unify.py         merge components by name key, links by canonical url
tools/report.py        review csvs + headline numbers
tools/validate.py      re-reads both sides, checks for data loss
tools/workload.py      how much review work the dupes cost
tools/check_links.py   visits every link and records whether it still resolves

data/raw_extract.json            every row and cell, with sheet/row/column
data/voron_sourcing_master.json  the unified dataset
data/master_items.csv            one line per component, qty per tab
data/master_links.csv            one line per component x link
data/aliases.json                hand-curated name merges
data/report.md                   headline numbers
data/link_status.json            link check cache, one entry per url
data/link_status.csv             the same thing, sorted worst first

data/review_name_clusters.csv    names that look alike but are separate items
data/review_shared_links.csv     separate items pointing at the same product
data/review_conflicts.csv        tabs that point at different products for the
                                 same part and the same role
data/review_unlinked.csv         one row per (component, tab) where that tab
                                 lists the part with no link at all
```

## Data format

`voron_sourcing_master.json` has `projects`, `vendors`, `roles` and `items`.
An item:

```jsonc
{
  "id": "motion.gt2-20t-6mm-wide-pulley-5mm-bore",
  "name": "GT2 20T (6mm wide) Pulley (5mm bore)",
  "name_key": "gt2 20t 6mm wide pulley 5mm bore",  // key used for merging
  "aliases": ["GT2 20T Pulley (6mm wide, 5mm bore)"],
  "category": "Motion",
  "standard": null,

  "used_by": [                                     // which builds need it
    { "project": "voron-2.4", "sheet": "VORON 2.4", "rows": [31],
      "qty": [ { "size": "All", "qty": "3", "qty_num": 3 } ],
      "choice_group": null }
  ],

  "sources": [                                     // one per product, not per cell
    { "url": "https://www.aliexpress.com/item/32226562320.html",
      "vendor": "aliexpress", "vendor_name": "AliExpress",
      "labels": ["POWGE"],                         // link text used in the sheets
      "roles": ["recommended"],                    // which column it appeared under
      "projects": ["voron-2.4", "voron-m4", "voron-2.2"],
      "affiliate": false,
      "link_ok": "yes",                            // yes / no / maybe / unchecked
      "link_checked": { "reason": "listing has a title", "http": 200,
                        "final_url": null, "at": "2026-08-30T..." },
      "affiliate_urls": ["https://s.click.aliexpress.com/e/E1Y0mkUu"],
      "raw_urls": ["...original url before cleaning..."],
      "seen": [ { "project": "voron-2.4", "sheet": "VORON 2.4",
                  "row": 31, "cols": ["F","G"], "slot": "Recommended" } ] }
  ],

  "unlinked_sources": [ { "text": "Print Yourself", "roles": ["recommended"],
                          "projects": ["voron-2.4"] } ],
  "notes": [ { "text": "...", "projects": ["voron-2.4"] } ]
}
```

How it's built:

- Links dedupe on a canonical url: tracking params stripped, Google redirect
  wrappers unwrapped, AliExpress/Amazon/McMaster reduced to item-id form.
  Originals kept in `raw_urls`.
- `[Affiliate Link]` cells get folded into the link they sit next to, so one
  product is one entry with its affiliate variants attached.
- `roles` are the guide's own column headings, normalized. The tabs use seven
  different heading schemes.
- Quantities are per project and per build size. `250³`/`300³`/`350³` columns
  become `size`. Ranges the sheet mangled into dates (`2022-02-04` for `2-4`)
  are restored.
- `choice_group` marks rows the guide joined with an `OR` separator.
- Extrusions key on their Misumi part number, so `HFSB5-2020-340` and
  `Misumi 2020 x 340mm - HFSB5-2020-340` are one item. Machining suffixes
  (`-AH45-BH375`, `-TPW`, `-LTP`) stay part of the key, because a drilled
  length is its own line item with its own quantity, but the undrilled product
  they all come from is recorded in `base_product` with the codes in
  `machining`. 33 extrusion items, 20 base products, 6 of which have more than
  one machining variant.
- Everything keeps sheet/row/column, so any merge can be checked or reverted.

## Name merging

Components merge when their normalized names match (lowercase, punctuation
collapsed, `M3 x 8` -> `m3x8`, `2.0` -> `2`). Anything close but not equal is
left alone and written to `data/review_name_clusters.csv`; confirmed merges go
into `data/aliases.json` and apply on the next run.

Pairs differing only by a number, head type, gender or axis are filtered out of
the candidate list, so SHCS/BHCS/FHCS, 2- vs 3-position and 4A vs 8A stay
separate. Pairs rejected on manual review go in `keep_separate` so they stop
coming back.

Names only catch so much. `review_shared_links.csv` is the second pass: two
separate items pointing at the same product link are usually one part named two
ways (`F695 2RS` / `F695 2RS Bearing` / `F695 Bearing`). It is sorted by name
similarity, because a low-similarity match usually means the shared link is a
catalogue page covering both parts rather than a duplicate.

Some parts are machine-specific even when two tabs give them the same name.
Those are listed under `split_by_project` in `aliases.json` and never merge
across tabs: each project keeps its own item with a `scope` field and a
project-suffixed id. Currently that covers every panel and the aluminium build
plates, giving 34 scoped items.

## What came out

| | |
| --- | ---: |
| tabs | 17 |
| rows read | 1051 |
| components | 485 |
| components on more than one tab | 197 |
| distinct product links | 606 (+185 affiliate variants) |
| components where tabs point at different products | 54 (22 on current tabs) |

Review effort, counting perfect duplicates once (`tools/workload.py`):

| | before | after |
| --- | ---: | ---: |
| distinct name + link-set variants | 709 | 485 |
| ...on the 197 shared parts | 421 | 197 |
| urls to check | 775 | 606 |

353 of 485 components (73%) are already consistent across every tab that lists
them. The work is in the other 132: 124 disagree on links, 8 only on the name.

Worst cases:

| component | tabs | variants | link sets | names |
| --- | ---: | ---: | ---: | ---: |
| M3x5x4 heat-set insert | 10 | 10 | 8 | 8 |
| GT2 20T (6mm) pulley (5mm bore) | 10 | 7 | 6 | 3 |
| M3x6 BHCS | 12 | 6 | 6 | 1 |
| PEI + 3M 468P | 8 | 6 | 6 | 1 |
| M2x10 self tapping screw | 7 | 6 | 6 | 4 |

Per-component numbers in `data/workload_by_component.csv`.

## Link checking

`tools/check_links.py` visits every link in the master and records one of:

| verdict | meaning |
| --- | --- |
| `yes` | the page loaded and looks like a live product |
| `no` | proven gone: 404/410, a marketplace page that says the listing was removed, or a redirect off the product page |
| `maybe` | could not be proven either way: bot check, login wall, rate limit, 5xx, timeout, DNS failure, or a host that refuses automated requests |
| `unchecked` | not visited yet |

`no` is deliberately hard to earn, because a wrong `no` sends somebody off to
re-source a part that was fine. Anything ambiguous falls through to `maybe`,
and every `no` is confirmed by a second request before it is recorded.

Two sites need special handling:

- **AliExpress** renders the product client-side and answers *every* item id
  with the same 200-byte-identical shell, so the HTML body proves nothing. A
  made-up item id returns exactly the page a removed listing returns. The one
  server-rendered field that differs is `og:title`: filled in for a live
  listing, empty for a dead one. That is the signal used.
- **McMaster** and **Misumi** refuse automated requests, so their links are
  reported `maybe` without being fetched rather than being hammered.

The checker is polite: one request per host at a time, 1.5s apart (3s for
AliExpress and Amazon), with a cache so reruns only visit what is new.

A rerun only visits links that are unchecked or `maybe`. A settled `yes` or
`no` is an answer, so it is left alone unless you ask:

```
python tools/check_links.py                  # unchecked + previous maybes
python tools/check_links.py --max-age 30     # also redo yes/no older than 30d
python tools/check_links.py --status no      # revisit just the dead ones
python tools/check_links.py --only aliexpress --limit 50
```

It checkpoints on every progress tick, so a run that gets interrupted keeps
what it already checked.

### Bot checks

AliExpress starts serving a captcha after a handful of requests, and a blocked
page tells you nothing about the listing, so the run pauses instead of filling
the cache with `maybe`. Run it from a terminal and `--interactive` is on by
default:

```
  aliexpress.com is asking for a bot check.
  Opening the blocked page in your browser.

  1. solve the check there (slider, captcha, whatever it wants)
  2. come back here and either:
       - press Enter to retry with the same session, or
       - paste a fresh Cookie header for the site and press Enter
     or type `skip` to stop asking for aliexpress.com.
```

Pressing Enter alone often works, since solving the check clears the block for
your IP. Pasting the Cookie header (devtools > Network > any request > Request
Headers > Cookie) is the reliable version: it is reused for every remaining
link on that host, so you solve one check instead of many. Workers that were
already mid-request when you solve it are retried quietly rather than asking
again.

### Browser mode

`--browser` runs the checks through a real Chrome window instead of plain HTTP
requests. When a site puts up a check, it appears in that window, you solve it
there, press Enter in the terminal, and the run carries on with the session
you just cleared. The profile lives in `data/.link-check-profile`, so once a
site trusts it, later runs start out trusted too.

```
python tools/check_links.py --browser --only aliexpress
```

Needs `selenium` and Chrome; Selenium fetches the matching chromedriver itself.
`--browser-binary` points at chrome.exe if it is somewhere unusual, and
`--browser-headless` skips the window once the profile is established (no use
for solving checks, since there is nothing to look at).

Browser mode is sequential, one link at a time. It reads the real HTTP status
out of the devtools log, and it waits for a javascript-rendered title before
judging, so a slow page does not get called dead. As in the plain mode, no `no`
is recorded without a second load agreeing.

### Cookies instead

For an unattended run, hand it cookies up front and turn the prompt off:

```
python tools/check_links.py --cookies-file ali-cookies.txt --no-interactive
python tools/check_links.py --cookie-header "x5sec=...; aep_usuc_f=..." --cookie-host aliexpress.com
```

`--cookies-file` takes a Netscape `cookies.txt` export or a file holding a
single Cookie header line. With `--no-interactive` a blocked host is reported
once and its remaining links come back `maybe`.

Either way, the AliExpress pass is worth running on its own:

```
python tools/check_links.py --browser --only aliexpress    # solve checks in the window
python tools/check_links.py --only aliexpress --workers 1  # plain http, prompt on block
```

Verdicts land on each source as `link_ok` after the next `tools/unify.py` run,
and in `data/master_links.csv`.

## Validation

`tools/validate.py` re-reads the workbook and the master and checks nothing
vanished in the merge:

- 775/775 urls
- 1003/1003 (component, tab) pairs
- 204/204 notes
- ids unique, projects known, urls well formed

## Known limits

- 142 shortened links (`s.click.aliexpress.com`, `amzn.to`) are opaque offline,
  so a short link and the plain link for the same product still look like two
  products. Resolving them would collapse more dupes; skipped to keep this
  offline and deterministic.
- `review_shared_links.csv` has not been worked through yet. It is the main
  source of remaining merges.
- The 2.4 / Trident / Switchwire / 1.8 tabs list `JST-XH Connector Plug N
  Position` and `JST-XH Female Pin` but link to Amphenol DuPont-style parts
  (65039-035ELF, 47649-000LF) - the same links the 2.2 tab uses for the rows it
  calls `DuPont`. The Optional Parts tab has the real JST XH parts under
  `JST XHP-n Housing` and `JST Crimp Pin`. Left as-is; the names and the links
  disagree and that needs a decision, not a merge.
- Categories are the guide's own, only lightly normalized. They're inconsistent
  between tabs and could use a real taxonomy.
- The Cascade tab is empty in the workbook.
- Switchwire has no `Recommended` column at all; its first vendor column is
  headed `Alternative Source`, so its links come through as `alternative`.
  That's what the sheet says, not an extraction bug.
