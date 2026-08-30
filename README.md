# voron-sourcing-unified

A canonical, deduplicated version of the Voron sourcing guide, plus the scripts
that build it from the published workbook.

## Why

The sourcing guide has been maintained by hand across 17 tabs for years, and
it's drifted. Three things convinced me it was worth doing something about.

**Mixed tooth profiles.** The guide recommends Gates belts next to POWGE
pulleys in several places. That mismatch chews belts — the pulleys I posted have
belt dust packed into the tooth valleys, and that's the mild version. It isn't
really a POWGE problem. It's that a recommendation made in one tab years ago
never got revisited in the other sixteen, so the advice is internally
inconsistent. Vendors read the guide too, which is how you end up buying a
Gates/POWGE motion kit off the shelf.

**Dead links.** I checked every link in the guide. 185 of 776 are dead — 24%.
89 components can't be sourced from the guide at all today, 59 of them on
printers people are actively building: the thermal fuse on 2.4 and Trident,
RaspberryPi4, SKR Mini E3 V2, six different wire gauges, both SDP-SI Gates belt
links.

**Ten names for one part.** The M3x5x4 heat-set insert appears on ten tabs
under ten different names — *M3 Threaded Insert*, *M3 Brass Heatstake Inserts*,
*M3 Heat Set Inserts (M3x5x4)*, and so on — with four different "recommended"
links between them. Somebody cross-referencing two tabs has no way to know
those are the same bag of inserts.

None of this is anyone's fault. Keeping 17 hand-maintained tabs in sync is a
chore nobody volunteered for, and it gets worse whether or not anyone touches
it. But it's fixable, and it doesn't have to stay a staff problem.

## The proposal

Make a canonical component list the source of truth, and generate each tab from
it.

One entry per physical part, with its links, quantities, and which machines use
it. Every tab becomes a view of that list rather than a separate document. Fix
the 20T pulley once and it's fixed on VS, V2, V0 and Trident at the same time —
the tabs can't disagree again, because there's only one of it.

Put that list in a repo as CSV and it stops being a bottleneck: a builder who
finds a dead link opens a PR, and it's a one-line diff someone can eyeball in a
minute. Vendors get an unambiguous answer about what belongs in a kit. Builders
can see which parts carry across printers.

Longer term the same treatment belongs on the BOM — canonicalise it, and use
that as the input future revisions and sourcing updates are generated from,
instead of maintaining the two in parallel by hand.

This repo is a working demonstration of the mechanism, not a finished proposal.
Everything in `data/` is generated; the only hand-maintained input is
`data/aliases.json`.

## What's here

`canonical.csv` — the component list. 458 components, one row each, with live
links, per-tab quantities, and which printers use them. This is the file the
proposal is about.

`Voron Sourcing Guide (revised).xlsx` — the guide rebuilt from that list, in the
original's layout and colours. Dead links removed, names harmonised, and a
"Link check" column saying what each row lost.

`Voron Sourcing Guide (diff).xlsx` — the same thing as a redline against the
published guide. Removals struck through in red, additions in blue, word by word
inside each cell.

`data/` — the master JSON, link-check results, and CSVs for the parts that need
a human decision.

## Running it

```
python tools/extract_raw.py          # xlsx -> data/raw_extract.json
python tools/unify.py                # -> data/voron_sourcing_master.json
python tools/report.py               # review csvs
python tools/validate.py             # checks nothing was dropped
python tools/coverage_report.py      # what can and can't be sourced
python tools/build_canonical_csv.py  # -> canonical.csv
python tools/build_xlsx.py           # -> the revised workbook
python tools/build_diff_xlsx.py      # -> the redline workbook
```

Needs `openpyxl`, plus `requests` and `selenium` for the link checker.

## The link checker

`tools/check_links.py` visits every link and records `yes`, `no` or `maybe`.
This was a one-off cleanup of years of accumulated backlog — nobody was going to
click 776 links by hand. It isn't meant to run on a schedule; once the list
lives in a repo, links get fixed as people hit them.

`no` is deliberately hard to earn: 404s, marketplace pages that say the listing
is gone, parked domains. Anything ambiguous is `maybe`, and every `no` is
confirmed by a second request. Results land in `data/link_status.csv` with the
date checked.

Some sites (AliExpress, Digi-Key, Bolt Depot, McMaster, Misumi) block plain HTTP
or throw captchas, so `--browser` drives a real Chrome and pauses for you to
clear a check. Links only a person could settle go in
`data/link_status_manual.json`, tagged so a hand-made call is never mistaken for
a measured one.

```
python tools/check_links.py --browser --only aliexpress   # solve checks in the window
python tools/check_links.py --status maybe                # retry the unresolved
```

## What the check found

| | |
| --- | ---: |
| links checked | 776 |
| dead | 185 (24%) |
| components | 458 |
| every link works | 286 |
| some links dead | 83 |
| all links dead | 47 |
| never had a link | 42 |

AliExpress is worst hit at 38% dead, which is what you'd expect of marketplace
listings five years on. Distributor links held up much better. Per-component
detail is in `data/coverage_*.csv`.

## How components are merged

Two rows are the same component when their normalised names match — lowercase,
punctuation collapsed, `M3 x 8` → `m3x8`. Anything close but not equal is left
alone and written to `data/review_name_clusters.csv` for a human call; confirmed
merges go in `data/aliases.json`.

Two rows sharing a product link are usually the same part named twice, so
`data/review_shared_links.csv` lists those separately. That's what caught
`F695 2RS` / `F695 2RS Bearing` / `F695 Bearing`.

Some parts stay split on purpose. Panels are specific to a printer. Beds are
shared between revisions of one machine but not between models — a 350mm
Trident bed isn't a 350mm 2.4 bed. Extrusions key on their Misumi part number,
and machining suffixes (`-AH45-BH375`, `-TPW`) stay separate line items with the
undrilled product recorded alongside.

`tools/validate.py` re-reads the workbook and the master and checks nothing
vanished: 775/775 urls, 1003/1003 component-tab pairs, 204/204 notes.

## Known limits

- 16 name pairs are still unresolved in `data/review_name_clusters.csv`, and
  `data/review_shared_links.csv` hasn't been worked through.
- Categories are the guide's own, only lightly normalised. They're inconsistent
  between tabs and deserve a real taxonomy.
- VORON 1.8 has a frame calculator below its main table with a different layout.
  It isn't represented here.
- The Cascade tab is empty in the published workbook.
- Switchwire has no `Recommended` column; its first vendor column is headed
  `Alternative Source`, so its links come through as `alternative`. That's what
  the sheet says, not an extraction bug.
