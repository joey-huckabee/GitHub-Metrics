# Conformance test plan

## What this is for

Every other test in this repository checks a **decision**: that a band maps a
value, that a failure raises the right code, that a duplicate is dropped. This
one checks the **contract** — that a known input keeps producing byte-identical
artifacts.

That is a different question, and the unit suite cannot answer it. A column
renamed, a key reordered, a `null` that became a `0`, a percentage whose
denominator quietly changed: each of those passes every focused test in this
repository and changes what every downstream consumer reads.

The gap is not hypothetical. Four defects in v0.5.0 and v0.6.0 were found by
running against the live API and none by the unit suite:

| Defect | What the unit suite saw |
|---|---|
| A bot in a contributor list ended an entire scan | green — every stub returned a clean payload |
| `check_budget` read a `/rate_limit` figure that never moves | green — the stub returned what it was told |
| A deep run reported zero bots while carrying four | green — no test compared the two routes |
| The coverage breakdown summed to 3,282 against 3,310 | green — no test summed it |

The last one is the clearest case. It was arithmetic drift in a published
number, and only a golden file would have shown it without someone thinking to
look.

## The design, and the two things it trades off

**Real data, no network.** Fixtures are recorded from the live API rather than
written from documentation — a stub written from docs proves only that the code
agrees with what someone believed, which is exactly the gap above. But a suite
that *reaches* GitHub is slow, rate-limited, and fails for reasons unrelated to
the change under test.

So the traffic is recorded once by hand and replayed for ever after. The suite
runs offline, in about two seconds, and is safe to run in CI on every push.

**Replay refuses what it has not seen.** A request the recording does not cover
raises rather than returning an empty answer. A silent fallback would produce
artifacts that are subtly wrong with nothing to say why — which is the failure
mode this suite exists to catch, occurring inside the suite itself.

## The fixture set

| Path | What it is |
|---|---|
| `tests/conformance/inventory.csv` | The input. A real two-column inventory. |
| `tests/conformance/recording.json` | Every API exchange, captured from the live API (~18 KB). |
| `tests/conformance/geocode.json` | A pre-populated geocode cache, so no lookup reaches Nominatim. |
| `tests/conformance/expected/` | The three artifacts, byte for byte. |

### Why these repositories

| Repository | Exercises |
|---|---|
| `octocat/Hello-World` | The ordinary path: 3 contributor identities, several with locations, a document written |
| `octocat/Spoon-Knife` | The minimum: a single identity, a single-page census with no `rel="last"` |
| `ghost/no-such-repository-conformance` | A **valid reference to an absent repository**: identity-only row, no document, exit 4 |

They are GitHub's own demonstration repositories — a handful of contributors,
last pushed in 2024, and about as unlikely to change as a public repository
gets. Size matters here: the whole recording is 18 KB, so it can be read in a
review rather than trusted.

The third is not an afterthought. **Which repositories get a document is half
the output contract**, and a suite that only scanned healthy repositories would
not notice if that rule inverted.

## What is asserted

| Test | Asserts |
|---|---|
| `test_the_tabular_artifact_is_unchanged` | `githubmetrics.csv` byte for byte — 20 columns, one row per reference, input order |
| `test_every_document_is_unchanged` | Each per-repository document byte for byte, **and that no extra one appeared** |
| `test_the_statistics_artifact_is_unchanged` | Every bound the scan publishes about its own data |
| `test_the_exit_status_is_unchanged` | Exit 4 — the status is part of the contract |
| `test_no_document_is_written_for_a_repository_that_was_not_collected` | The absent file, and the row that survives it |
| `test_two_runs_of_one_input_are_identical` | Determinism, which is a requirement rather than a quality attribute |
| `test_the_replay_covers_every_request_the_scan_makes` | No hole in the recording |
| `test_the_run_reaches_no_network_at_all` | `geocoding.lookups == 0` — the artifact proves its own isolation |

## What is normalised, and why only that

Three values are pinned rather than compared, and one is blanked:

| Value | Treatment | Why |
|---|---|---|
| `scan_id` | pinned to a fixed UUID | A property of the run, not the data. Every artifact carries it, so leaving it random would make all three differ every time. |
| `scan_date` | pinned | As above. |
| `duration_seconds` | normalised | Changes on every execution and says nothing about the contract. |
| `tool_version` | normalised | Changes at every release; otherwise the golden files churn for reasons no reviewer should read past. |

**Nothing else is normalised.** Every measured value, every key, every ordering
and the exit status are compared exactly.

The geocode cache's clock is also frozen to the scan date, which is the
timestamp every fixture entry carries. Without that the fixture would age and,
one year after recording, the suite would quietly start geocoding over the
network — passing, slowly, for the wrong reason.

## Running it

```bash
make test                 # included; it is an ordinary offline test
poetry run pytest tests/test_conformance.py -q
```

## When it fails

**The output contract moved.** Two possibilities, and telling them apart is the
whole point:

1. **That was the intent.** Regenerate, *read the diff*, and record the change
   in `CHANGELOG.md`:

   ```bash
   make conformance                        # rewrites the golden files
   git diff tests/conformance/expected     # then read what moved
   ```

   Regeneration is the test itself running with `CONFORMANCE_REGENERATE=1`,
   rather than a script of its own. A script would have to reproduce the replay
   setup exactly and would drift from it the first time either changed.

   A golden file regenerated without reading the diff is a test that agrees
   with whatever the code now does, which is no test at all.

2. **It was not.** The diff is the bug report, and it is already minimal.

## Re-recording

Rarer, and it needs a token:

```bash
poetry run python scripts/record-conformance.py   # needs GITHUB_TOKEN
make conformance
```

Re-record when a new code path needs covering, or when a fixture repository has
changed enough that the recording no longer represents it. **Expect the golden
artifacts to change**, because the upstream data will have — that diff is
*data* drift rather than contract drift, and the two should not be committed
together. Re-record in its own commit.

## Known limits

- **It exercises the default path only.** `--deep-attribution`,
  `--on-exhaustion partial` and `--no-recover-anonymous` have unit tests but no
  golden artifacts. Adding a second inventory for them is the obvious next
  extension.
- **No repository in the set has a bot**, which is the one thing that has caused
  two defects. `octocat`'s repositories predate GitHub Apps. A third fixture
  repository with a bot contributor would close that.
- **The recording freezes upstream data**, so a change in what GitHub returns
  for these repositories is invisible until someone re-records. That is the
  deliberate trade for determinism, and it is why the one live `integration`
  test still exists.
