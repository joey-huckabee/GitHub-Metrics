---
status: accepted
date: 2026-09-05
decision-makers: Joey
---

# A geocode cache that survives the run, and what expires in it

## Context and Problem Statement

Geocoding is paced at one request per second by Nominatim's usage policy, and
the cache that made that survivable lived on the `Geocoder` and died with the
process. A second scan over the same inventory paid a full second again for
every place that had not moved.

At the old limit of 25 contributors per repository that was tolerable.
[ADR-0006](0006-collect-every-contributor.md) removes the limit, and the
distinct-location count rises with it — a serious portfolio moves from roughly
one or two thousand distinct locations to something closer to ten thousand.
At one second each, an unbounded scan without a persistent cache is an
overnight job **every time it runs**, not just the first time.

`ROADMAP.md` had this queued behind v0.7.0's store, on the reasoning that a
store which already holds addresses is the obvious home for one and doing it
first would design the same cache twice. ADR-0006 makes that ordering
untenable: unbounded collection is not usable without it.

## Decision Drivers

* A re-run over a stable inventory must pay only for locations never seen
* A transient outage must not permanently poison a cached answer
* The cache must not become a second persistence mechanism competing with
  v0.7.0's store
* Cache state must never be mistaken for measurement — a cached miss and a
  fresh miss must produce identical output

## Decision Outcome

Chosen: **an on-disk JSON cache, keyed on the normalised case-folded location,
with expiry that differs by outcome.**

JSON rather than SQLite for now, because the store v0.7.0 brings is where this
belongs permanently, and building a second database first would design the same
thing twice — the exact argument that deferred this work, now applied to its
shape rather than its schedule. The threshold at which that stops being true is
recorded below and in `ROADMAP.md`.

The file lives under the platform cache directory
(`%LOCALAPPDATA%` on Windows, `$XDG_CACHE_HOME` or `~/.cache` elsewhere) and
`GEOCODE_CACHE_PATH` overrides it. It is a cache: deleting it costs time and
loses no measurement.

## Expiry: three outcomes, three policies

The important decision here is that **"does this expire" has three answers, not
one**, and collapsing them is what would make the cache wrong.

| Outcome | Persisted | Expiry | Why |
|---|---|---|---|
| **Matched** | yes | 365 days | Place data is effectively static |
| **No match** | yes | 30 days | Gazetteer coverage improves |
| **Service error** | **no** | — | Says nothing about the location |

### Matched results do not need a TTL for correctness

A matched entry maps a location string to a place. The mapping from
`Austin, TX` to Austin, Travis County, Texas is not a volatile fact. What does
change is rare: country renamings arrive perhaps a few times a decade
(Swaziland to Eswatini, Macedonia to North Macedonia, Turkey to Türkiye), and
OpenStreetMap's own coverage improves so an old match may have fewer components
than the same query would return today.

Neither is a correctness threat to what this cache feeds. The field a residency
rule should key on is `country_code` — ISO 3166-1 alpha-2 — which is the most
stable field in the record and does not move when a country's *name* changes in
one dataset before another. A 365-day expiry is therefore chosen to pick up
data improvements, **not** because a stale entry is dangerous. Setting no
expiry at all would also have been defensible; a year is the cheaper
insurance, since it costs one second per location per year.

### A miss expires sooner, because a miss is a statement about coverage

`she/her`, `earth`, `127.0.0.1` and `the moon` are things accounts genuinely
publish, and they are permanent non-places. Not caching them would be the
expensive mistake: junk locations are common enough on GitHub that re-asking
them every run is a large share of the cache's whole benefit.

But a miss is not only junk. It is also every real place Nominatim did not
cover *yet*, and OSM coverage grows continuously. Thirty days keeps re-runs
cheap while letting a location that becomes resolvable be picked up within a
month.

### A service error is never written to disk

This is the one that would have been a defect. A Nominatim outage, a timeout
or a blocked user agent produces no information about the location — but the
current code returns the same `Address(query=key)` for a service failure as
for a genuine no-match, so the two are indistinguishable at the call site.

Persisting that would mean **one bad afternoon permanently poisons every
affected location**: each future run would read "unresolved" from the cache and
never ask again, and the resulting documents would report an unresolved address
for a place that resolves perfectly well. An error that looks like data
survives review — the same failure this repository refuses in `GM-COL-003`.

The two outcomes are therefore distinguished internally so the cache layer can
tell them apart, while `locate` still returns the identical `Address` for both.
A service failure remains in the per-process cache for the run — so eight
workers do not each re-ask the same failing location — and never reaches the
file.

### Consequences

* Good: a re-run over a stable inventory costs approximately nothing in
  geocoding
* Good: the expensive first run is paid once per machine, not once per run
* Good: an outage costs one run's resolution rather than every future run's
* Bad: a cache file is new state on disk that a user may need to know about;
  `GEOCODE_CACHE_PATH` and a documented default are the mitigation
* Bad: two persistence mechanisms exist until v0.7.0 folds this into the store

## When this should stop being a JSON file

A single JSON file is read entirely at startup and rewritten entirely at the
end of a run. That is the right shape while it is small and the wrong shape
once it is not, so the trigger is recorded rather than left to be noticed.

Measured on this repository's own `GeocodeCache`, filling it with
fully-populated city-level matches - every component present, which is the
worst case; a real cache holds many country-only matches and misses, which are
smaller:

| Entries | File size | Save | Load | Resident |
|---|---|---|---|---|
| 1,000 | 0.49 MB | 14 ms | 102 ms | 1.2 MB |
| 5,000 | 2.46 MB | 58 ms | 468 ms | 5.3 MB |
| 20,000 | 9.89 MB | 245 ms | 1.9 s | 20.2 MB |
| 50,000 | 24.80 MB | 677 ms | 4.8 s | 51.1 MB |
| 100,000 | 49.64 MB | 1.3 s | 9.7 s | 101.9 MB |

An entry is **about 510 bytes**, and the relationships are linear: resident
memory is 2.1x the file, and loading costs roughly four times what saving does.

**Review at 10 MB (about 20,000 distinct locations). Move by 50 MB.**

**Load time is what forces the move, not memory.** That is worth stating
plainly because the intuition runs the other way, and the first draft of this
ADR had it backwards:

1. **Every run pays the load in full, before it does anything.** At 20,000
   entries that is nearly two seconds; at 100,000 it is ten. A run needing
   forty locations parses all of them, so the tax lands hardest on exactly the
   small re-runs the cache exists to make fast. This is the constraint.
2. **Memory is mild.** 2.1x the file size, so a 10 MB cache costs about 20 MB
   resident - unremarkable in a process already running eight collection
   threads. It only becomes interesting past 50 MB.
3. **The whole file is rewritten on every save**, at about a quarter of the
   load cost. Writing to a temporary file and renaming makes that atomic, so an
   interrupted run cannot truncate the cache, and the cost is paid in full even
   by a run that added three entries.

The move, when it comes, is **into v0.7.0's SQLite store rather than into a
database of its own**. That store will already hold addresses; a second one
would be the duplicate design this ADR avoided by not starting with SQLite.
SQLite answers the first point directly and it is the one that matters: a run
reads the keys it needs and parses nothing else, so start-up stops scaling with
the cache at all.

For scale: a 200-repository inventory with unbounded contributors produces
somewhere around eight to fifteen thousand distinct locations, so a serious
portfolio lands near 4-8 MB and a half-second load - inside the JSON regime,
and within sight of the review point. A program of a few thousand repositories
crosses it.

**The location counts above are estimated; everything else is measured.** How
many distinct locations an inventory yields depends on the portfolio and has
not been observed against a real run - it belongs with the other things
`ROADMAP.md` records as never having been checked against the live API.

## More Information

* `METRICS.md`, "What geocoding is for", for what the cached data feeds
* [ADR-0006](0006-collect-every-contributor.md) for the change that made this
  necessary
* `ROADMAP.md`, v0.7.0, for the store this eventually folds into
