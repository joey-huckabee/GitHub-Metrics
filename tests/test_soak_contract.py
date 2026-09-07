"""The soak checks name keys in `statistics.json`. This binds them to the code.

`tests/test_soak.py` needs a token and minutes, so the gate never runs it and
nothing it says is checked until someone dispatches the workflow. That is fine
for its assertions - they are about live behaviour. It is not fine for the
*names* it reads: a mistyped key is a `KeyError` that costs a live run, a wait,
and a red job to discover.

One did. `budget["graphql_points_remaining"]` was invented; the published key
is `graphql_remaining`. The assertion before it had already passed, so three
live checks ran and the fourth died on a typo.

So these tests read the soak module as text and bind every subscript in it
against the mapping the code actually publishes. They are ordinary offline
tests: no token, no network, milliseconds. The technique is deliberate - the
same mechanical binding found a README example calling a constructor that does
not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from github_metrics.model.statistics import BudgetStatistics, ScanStatistics

SOURCE = (Path(__file__).parent / "test_soak.py").read_text(encoding="utf-8")

BUDGET_KEY = re.compile(r'\bbudget\["([^"]+)"\]')
"""Every `budget["..."]` the soak reads."""

STATISTICS_KEY = re.compile(r'(?:\.statistics|statistics_of\([^)]*\))\["([^"]+)"\]')
"""Every top-level key read from the artifact, by either accessor."""


def test_the_soak_reads_budget_keys_that_exist() -> None:
    published = set(BudgetStatistics().to_mapping())
    read = set(BUDGET_KEY.findall(SOURCE))

    assert read, "the pattern matched nothing - it has drifted from the source"
    assert read <= published, f"not published: {sorted(read - published)}"


def test_the_soak_reads_top_level_keys_that_exist() -> None:
    published = set(ScanStatistics().to_mapping())
    read = set(STATISTICS_KEY.findall(SOURCE))

    assert read, "the pattern matched nothing - it has drifted from the source"
    assert read <= published, f"not published: {sorted(read - published)}"


@pytest.mark.parametrize("key", ["graphql_points_remaining", "points_spent"])
def test_a_key_the_artifact_does_not_carry_is_caught(key: str) -> None:
    """Anti-vacuity: the check above is only worth having if it can fail.

    The first of these is the name that actually shipped.
    """
    assert key not in BudgetStatistics().to_mapping()
