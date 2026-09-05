"""The scan statistics document: how good the other two artifacts are.

`githubmetrics.csv` is the comparable table and a per-repository document is a
detail record. Neither can say **what it is missing**, and that is what this
type carries.

The problem it solves is concrete. A repository truncated at GitHub's
500-author-email ceiling produces a row and a document indistinguishable from a
complete one: one measured repository reported 396 contributors and a
`contribution_total` of 27,828, and actually has 3,310 contributor identities
and 32,005 commits. Both published numbers are correct. Both imply a census
they are not.

Every field here exists to put a bound on a number in the other artifacts.
Definitions are in `docs/METRICS.md`; the reasoning is in
`docs/adr/0008-statistics-json.md`.

Percentages are computed, never stored twice
--------------------------------------------
A percentage is derived from the two counts beside it, so a consumer can check
it and the file cannot contradict itself. The counts are the measurement; the
percentage is a convenience.

Nothing here judges a person
----------------------------
`foreign` and `adversarial` do not appear, and neither does anything derived
from them. This file supplies the denominators - and above all the
*unknown-location share* - that a separate residency stage needs to bound its
own percentages. It asserts nothing about anybody.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from github_metrics.model.software import jsonable


def percent(part: int, whole: int) -> float | None:
    """Render `part` of `whole` as a percentage.

    Args:
        part: The numerator.
        whole: The denominator.

    Returns:
        The percentage, or `None` when `whole` is zero. `None` rather than
        `0.0` for the same reason every metric field defaults to `None`: a
        percentage of nothing is unknown, not zero, and publishing `0.0` would
        say that none of something was measured when there was nothing to
        measure.
    """
    if whole <= 0:
        return None
    return round(part / whole * 100, 2)


class ExclusionReason(str, Enum):
    """Why a contributor identity is not fully represented in a document.

    The first three are exclusions from *collection*: the person is absent
    from the document entirely. The last three are exclusions from
    *geographic* analysis: the person is present, but has no usable location,
    which is what bounds any claim about where a project's work comes from.

    Keeping the two groups in one vocabulary is deliberate - they are both
    answers to "why is this number smaller than it looks" - but the split
    matters, so `is_collection_gap` reports it.
    """

    ANONYMOUS_RECOVERED_NOREPLY = "anonymous_recovered_noreply"
    """Beyond GitHub's 500-email ceiling, but publishing a
    `NNN+login@users.noreply.github.com` address carrying GitHub's own account
    id and login. Recovered and collected; this counts the rescue."""

    ANONYMOUS_NO_ACCOUNT = "anonymous_no_account"
    """Beyond the ceiling, publishing a real address. GitHub exposes no
    email-to-user lookup, so no API resolves these."""

    ACCOUNT_UNRESOLVABLE = "account_unresolvable"
    """A login GitHub listed that GraphQL could not resolve - deleted or
    suspended between the two calls."""

    NO_LOCATION_PUBLISHED = "no_location_published"
    """Collected; the account publishes no location, so nothing was asked."""

    LOCATION_UNRESOLVED = "location_unresolved"
    """Published something no gazetteer recognises."""

    GEOCODER_UNAVAILABLE = "geocoder_unavailable"
    """The lookup failed for a reason unrelated to the location. Retryable,
    and the only reason here that a later run may clear on its own."""

    @property
    def is_collection_gap(self) -> bool:
        """Whether this reason means the person is absent from the document."""
        return self in {
            ExclusionReason.ANONYMOUS_NO_ACCOUNT,
            ExclusionReason.ACCOUNT_UNRESOLVABLE,
        }


class AttributionMethod(str, Enum):
    """How a repository's contributors were determined.

    Two runs of one repository by different methods are **not comparable**:
    the deep method finds a larger population and a larger attributed total.
    This is recorded per repository precisely so a consumer can tell them
    apart rather than diffing them as though they measured the same thing.
    """

    CONTRIBUTOR_LIST = "contributor_list"
    """The REST contributors endpoint. Cheap, and bounded by GitHub's
    500-author-email ceiling."""

    COMMIT_HISTORY = "commit_history"
    """A walk of the default branch's commit history. Complete, and roughly
    35 times the cost. See `docs/adr/0010-optional-commit-history-attribution.md`."""


@dataclass(frozen=True, slots=True)
class Exclusion:
    """One reason, and what it costs, counted in both people and commits.

    Both counts are needed and neither substitutes for the other. A reason may
    account for most of the *people* and very little of the *work* - which is
    exactly what GitHub's email ceiling does - and reporting only one of them
    would make that look like either a catastrophe or a triviality.

    Attributes:
        reason: Why these identities are not fully represented.
        people: How many identities.
        commits: How many commits they hold between them, or `None` when that
            was not counted. The identity census counts *people* in one
            request; counting their commits needs every page of the anonymous
            list. `0` would claim the group contributed nothing, which is a
            different statement and usually a false one.
    """

    reason: ExclusionReason
    people: int = 0
    commits: int | None = None

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping."""
        return {
            "reason": self.reason.value,
            "people": self.people,
            "commits": self.commits,
        }


@dataclass(frozen=True, slots=True)
class IdentityGaps:
    """What the contributor list did not yield, before the document was built.

    These four arrive together from the same place - the widened contributor
    count and the recovery pass over it - and mean nothing apart. Grouping
    them keeps the builder's signature honest rather than growing a
    parameter per gap.

    Attributes:
        identities: Every contributor identity GitHub reports, including
            those with no account. `None` when the census was not taken, in
            which case what was collected is all that is known.
        unrecoverable: Identities beyond GitHub's email ceiling that no API
            resolves, with their commits.
        recovered: Identities rescued from a no-reply address.
        unresolvable: Logins GitHub listed that GraphQL could not resolve.
    """

    identities: int | None = None
    unrecoverable: Exclusion | None = None
    recovered: int = 0
    unresolvable: int = 0

    def breakdown(self, collected: int) -> dict[str, int]:
        """Account for every identity, in categories needing different responses.

        A single coverage percentage hides which of two very different things
        happened. 12% coverage because GitHub's email ceiling bit is a property
        of the repository's size and nothing can be done about most of it; 12%
        because accounts were deleted is a property of its age. The components
        say which.

        Args:
            collected: Contributors that reached the document.

        Returns:
            Counts that **sum to `identities`**, so the breakdown can be
            checked rather than trusted.
        """
        recovered = min(self.recovered, collected)
        unrecoverable = self.unrecoverable.people if self.unrecoverable else 0
        return {
            # Within GitHub's 500-author-email ceiling, so an account was
            # linked and the detail query could resolve it.
            "linked_by_github": collected - recovered,
            # Beyond the ceiling, but publishing a no-reply address carrying
            # GitHub's own account id and login.
            "recovered_from_noreply": recovered,
            # Beyond the ceiling with a real email. GitHub exposes no
            # email-to-user lookup, so no API reaches these.
            "anonymous_unrecoverable": unrecoverable,
            # A login GitHub listed that GraphQL then could not resolve:
            # deleted or suspended between the two calls.
            "unresolvable_accounts": self.unresolvable,
        }


@dataclass(frozen=True, slots=True)
class BotStatistics:
    """Contributors GitHub reports as `type: "Bot"`, and what they hold.

    `contribution_total` in the document is **not** adjusted for these. Both
    figures are published and the analysis chooses; see `docs/METRICS.md`.

    Attributes:
        count: How many bots contributed.
        commits: Their commits, summed.
        logins: Their logins, so the figure is checkable rather than trusted.
        attributed_excluding_bots: Attributed commits minus `commits`.
    """

    count: int = 0
    commits: int = 0
    logins: tuple[str, ...] = ()
    attributed_excluding_bots: int = 0

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping."""
        return {
            "count": self.count,
            "commits": self.commits,
            "logins": list(self.logins),
            "contribution_excluding_bots": self.attributed_excluding_bots,
        }


@dataclass(frozen=True, slots=True)
class Concentration:
    """Where a repository's work is concentrated.

    This is what answers "where does this project's work come from" without
    naming anyone a maintainer - which is the question the dropped maintainer
    block was reaching for, answered from data that actually exists.

    Attributes:
        top_1_percent: Share of attributed commits held by the largest
            contributor.
        top_5_percent: Share held by the largest five.
        top_10_percent: Share held by the largest ten.
        bus_factor: Fewest contributors whose commits together exceed half the
            attributed total. A measure of concentration, **not** of project
            risk: it counts commits, and a prolific contributor is not
            necessarily irreplaceable.
        gini: Inequality of the distribution, 0 even to 1 concentrated.
    """

    top_1_percent: float | None = None
    top_5_percent: float | None = None
    top_10_percent: float | None = None
    bus_factor: int | None = None
    gini: float | None = None

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping."""
        return {
            "top_1_percent": self.top_1_percent,
            "top_5_percent": self.top_5_percent,
            "top_10_percent": self.top_10_percent,
            "bus_factor": self.bus_factor,
            "gini": self.gini,
        }


@dataclass(frozen=True, slots=True)
class CountryTotals:
    """People and commits resolved to one country.

    Attributes:
        people: Contributors whose location resolved to this country.
        commits: Their commits, summed.
    """

    people: int = 0
    commits: int = 0

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping."""
        return {"people": self.people, "commits": self.commits}


@dataclass(frozen=True, slots=True)
class Geography:
    """Where the collected contributors resolved to, and how much did not.

    Attributes:
        countries: `country_code` to its totals.
        commits_with_known_location: Attributed commits whose contributor
            resolved to a country.
        commits_with_unknown_location: The complement. **This is the error bar
            on every geographic claim made from the scan** - a national share
            computed without it implies a precision the data does not have.
    """

    countries: dict[str, CountryTotals] = field(default_factory=dict)
    commits_with_known_location: int = 0
    commits_with_unknown_location: int = 0

    @property
    def total(self) -> int:
        """Attributed commits considered by this breakdown."""
        return self.commits_with_known_location + self.commits_with_unknown_location

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping, countries by descending commits."""
        ordered = sorted(self.countries.items(), key=lambda item: (-item[1].commits, item[0]))
        return {
            "countries": {code: totals.to_mapping() for code, totals in ordered},
            "distinct_countries": len(self.countries),
            "commits_with_known_location_percent": percent(
                self.commits_with_known_location, self.total
            ),
            "commits_with_unknown_location_percent": percent(
                self.commits_with_unknown_location, self.total
            ),
        }


@dataclass(frozen=True, slots=True)
class RepositoryStatistics:
    """What one repository's numbers are worth.

    Attributes:
        owner: The owner as the input named it.
        name: The repository name.
        url: Its canonical address, matching the row's.
        collected: Whether metrics were read.
        documented: Whether a document was written.
        attribution: How the contributors were determined.
        commits_total: Commits on the default branch, or `None` when it could
            not be read.
        commits_attributed: Commits belonging to collected contributors.
        contributor_identities: Every identity GitHub reports, including those
            with no account. Equals `contributors_collected` when the census
            was not taken.
        gaps: The components of the identity count, so contributor coverage can
            be read as a breakdown rather than as one opaque percentage.
        contributors_collected: How many appear in the document.
        exclusions: Who is missing and why.
        bots: Bot contributors and their commits.
        concentration: Where the work sits.
        geography: Where it comes from, and how much is unknown.
    """

    owner: str = ""
    name: str = ""
    url: str = ""
    collected: bool = False
    documented: bool = False
    attribution: AttributionMethod = AttributionMethod.CONTRIBUTOR_LIST
    commits_total: int | None = None
    commits_attributed: int = 0
    contributor_identities: int = 0
    contributors_collected: int = 0
    gaps: IdentityGaps = field(default_factory=IdentityGaps)
    exclusions: tuple[Exclusion, ...] = ()
    bots: BotStatistics = field(default_factory=BotStatistics)
    concentration: Concentration = field(default_factory=Concentration)
    geography: Geography = field(default_factory=Geography)

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping."""
        return {
            "owner": self.owner,
            "name": self.name,
            "url": self.url,
            "collected": self.collected,
            "documented": self.documented,
            "attribution": {"method": self.attribution.value},
            "commits": {
                "total": self.commits_total,
                "attributed": self.commits_attributed,
                "coverage_percent": percent(self.commits_attributed, self.commits_total or 0),
            },
            "contributors": {
                "identities": self.contributor_identities,
                "collected": self.contributors_collected,
                "coverage_percent": percent(
                    self.contributors_collected, self.contributor_identities
                ),
                "breakdown": self.gaps.breakdown(self.contributors_collected),
                # The single most useful boolean in the file: whether GitHub's
                # 500-author-email ceiling affected this repository at all.
                # False means the contributor list is everyone.
                "truncated_by_github": (self.contributor_identities > self.contributors_collected),
            },
            "exclusions": [item.to_mapping() for item in self.exclusions],
            "bots": self.bots.to_mapping(),
            "concentration": self.concentration.to_mapping(),
            "geography": self.geography.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class BudgetStatistics:
    """What the run spent, and whether it finished.

    Attributes:
        graphql_points_spent: Measured by difference against GraphQL's own
            `rateLimit` field, so it includes anything the cost model missed.
        rest_requests_spent: **Always `None`.** See the class note below.
        graphql_remaining: What was left when the run ended.
        rest_remaining: **Always `None`**, for the same reason.
        policy: The `--on-exhaustion` mode in force.
        exhausted: Whether either budget ran out during the run.
        incomplete: Whether the run stopped early as a result. **The field a
            consumer must check before treating the CSV as complete.**
        waits: How many hourly resets the run slept through.

    Why the REST figures are null rather than numbers
    -------------------------------------------------
    There is no source for them that this tool can trust, and publishing an
    untrustworthy number is worse than publishing none.

    - The REST `/rate_limit` endpoint does not track spend: measured, it
      reported 5000 remaining while the same token had 4984 REST requests and
      4988 GraphQL points left.
    - The `X-RateLimit-Remaining` response header *is* accurate, but PyGithub
      keeps only the most recent one and a **GraphQL** response overwrites it
      with the GraphQL budget - observed going 4981, then 4976 after one
      GraphQL call, then 4980 after the next REST call. A scan interleaves both
      across eight threads, so whichever arrived last is what would be read.

    Counting requests locally is not a way out either: pagination happens
    inside PyGithub, so the pages a contributor list costs are never seen here.

    `None` is this package's word for "not measured", used the same way every
    metric column uses it. GraphQL is the binding budget at two points against
    one request per repository, and it *is* measured, so what matters most is
    still reported.
    """

    graphql_points_spent: int = 0
    rest_requests_spent: int | None = None
    graphql_remaining: int = 0
    rest_remaining: int | None = None
    policy: str = "wait"
    exhausted: bool = False
    incomplete: bool = False
    waits: int = 0

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping."""
        return {
            "graphql_points_spent": self.graphql_points_spent,
            "rest_requests_spent": self.rest_requests_spent,
            "graphql_remaining": self.graphql_remaining,
            "rest_remaining": self.rest_remaining,
            "exhaustion_policy": self.policy,
            "exhausted": self.exhausted,
            "incomplete_because_exhausted": self.incomplete,
            "waits": self.waits,
        }


@dataclass(frozen=True, slots=True)
class GeocodingStatistics:
    """What the geocoder did, and what the cache saved.

    `service_failures` is the field that earns this block its place: it is the
    only record anywhere in the output distinguishing "this contributor
    published no location" from "the geocoder was unreachable when we asked",
    because both produce an identical `Address` by design.

    Attributes:
        cache_loaded: Entries read from the persistent cache at startup.
        cache_expired_on_load: Entries dropped as stale.
        cache_hits: Lookups answered without a request.
        lookups: Requests actually made.
        matched: Lookups that resolved.
        unmatched: Lookups the gazetteer had nothing for.
        service_failures: Lookups that failed for a reason unrelated to the
            location. Never cached, so a later run will ask again.
    """

    cache_loaded: int = 0
    cache_expired_on_load: int = 0
    cache_hits: int = 0
    lookups: int = 0
    matched: int = 0
    unmatched: int = 0
    service_failures: int = 0

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping."""
        return {
            "cache_loaded": self.cache_loaded,
            "cache_expired_on_load": self.cache_expired_on_load,
            "cache_hits": self.cache_hits,
            "lookups": self.lookups,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "service_failures": self.service_failures,
        }


# The attribute count is the artifact's shape, not a design problem: this is a
# record, and every field is a documented key of statistics.json.
@dataclass(frozen=True, slots=True)
class ScanStatistics:  # pylint: disable=too-many-instance-attributes
    """One run's statistics document.

    Attributes:
        scan_id: The run, identical to the CSV's and the documents'.
        scan_date: Start of the run, UTC.
        tool_version: The package version that produced it. The first place any
            artifact of this tool records one.
        duration_seconds: Wall-clock time of the whole run.
        repositories_named: References accepted from the input.
        repositories_not_attempted: Named but never attempted, because the run
            stopped early. Zero unless the budget was exhausted.
        budget: What the run spent and whether it finished.
        geocoding: What the geocoder did.
        repositories: One entry per named reference, in input order, so the
            array aligns positionally with the CSV.
        warnings: Every degradation, in run order.
    """

    scan_id: UUID | None = None
    scan_date: datetime | None = None
    tool_version: str = ""
    duration_seconds: float = 0.0
    repositories_named: int = 0
    repositories_not_attempted: int = 0
    budget: BudgetStatistics = field(default_factory=BudgetStatistics)
    geocoding: GeocodingStatistics = field(default_factory=GeocodingStatistics)
    repositories: tuple[RepositoryStatistics, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        """Render as a JSON-ready mapping."""
        collected = sum(1 for entry in self.repositories if entry.collected)
        documented = sum(1 for entry in self.repositories if entry.documented)
        attempted = len(self.repositories) - self.repositories_not_attempted
        return {
            "scan_id": jsonable(self.scan_id),
            "scan_date": jsonable(self.scan_date),
            "tool_version": self.tool_version,
            "duration_seconds": round(self.duration_seconds, 2),
            "repositories": {
                "named": self.repositories_named,
                "collected": collected,
                "documented": documented,
                "failed": attempted - collected,
                "not_attempted": self.repositories_not_attempted,
            },
            "budget": self.budget.to_mapping(),
            "geocoding": self.geocoding.to_mapping(),
            "warnings": list(self.warnings),
            "repository_statistics": [entry.to_mapping() for entry in self.repositories],
        }
