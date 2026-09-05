"""Collecting a whole inventory, one repository at a time, several at once.

Two properties matter more than throughput here.

**Every reference produces an outcome.** A repository that 404s, or that has
moved, still occupies a position in the result, because the output has one row
per accepted input row and a missing row would silently change what the file
means. The outcome carries either the metadata or the reason there is none.

**Order is the input's order.** Results come back through `Executor.map`, not
`as_completed`, so two runs of the same inventory produce byte-identical files
and a diff between them shows changed data rather than reordered rows. That is
a requirement, not a quality attribute: these files are compared over time.

The work is a network round trip per repository, which is exactly what threads
overlap well. The pool is small because the constraint is the rate limit rather
than the CPU, and because a large pool converts a token's budget into a burst
that GitHub is entitled to refuse.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final

from github_metrics.client import GitHubClient
from github_metrics.collect.anonymous import AnonymousTally, collect_anonymous
from github_metrics.collect.census import count_identities
from github_metrics.collect.contributors import (
    DEFAULT_CONTRIBUTOR_LIMIT,
    get_contributors,
)
from github_metrics.collect.exhaustion import BudgetGuard, Decision
from github_metrics.collect.repository import RepoMetaData, get_repository
from github_metrics.errors import CollectionError, ContributorCollectionError
from github_metrics.geo import Geocoder
from github_metrics.model.contributor import Contributor
from github_metrics.sources import RepositoryRef

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS: Final = 8
"""Concurrent collections. Small on purpose: the limit is quota, not CPU."""


@dataclass(frozen=True, slots=True)
class CollectionOptions:
    """How much of a repository's contributor data to gather.

    Three switches that answer one question - how complete a picture is
    worth paying for - and that a caller almost always sets together.
    Grouping them keeps `collect_all` readable as *what* to collect,
    *where* to put it and *what to do when the budget runs out*, rather
    than a list of eight positional knobs.

    Attributes:
        contributor_limit: Contributors kept per repository. `None`, the
            default, keeps every one GitHub returns.
        census: Count every contributor identity GitHub reports, anonymous
            ones included, at one extra REST request per repository. On by
            default, because without it coverage cannot be stated honestly.
        recover_anonymous: Walk the anonymous tail and collect the accounts
            whose no-reply addresses name them. Costs a page per hundred
            identities - 34 requests for a large repository against 4 -
            which is why it can be turned off for a large inventory.
    """

    contributor_limit: int | None = DEFAULT_CONTRIBUTOR_LIMIT
    census: bool = True
    recover_anonymous: bool = True


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one reference produced.

    The two failures are separate because they have different consequences. A
    repository that could not be read produces an identity-only row and no
    document. A repository that was read but whose contributor list was not
    produces a full row of measurements, and still no document - because a
    document carrying an empty contributor array and a `contribution_total`
    of zero cannot be told from a repository that genuinely has none.

    Attributes:
        reference: The reference as the input named it.
        metadata: What GitHub reported, or `None` if it could not be read.
        contributors: Its contributors, most commits first. Empty when they
            could not be collected, which `contributor_error` distinguishes
            from a repository that has none.
        error: Why there is no metadata, or `None` on success.
        contributor_error: Why there are no contributors, or `None`.
        attempted: Whether collection was tried at all. `False` only when the
            run stopped early - the budget ran out under `--on-exhaustion
            partial` - and the repository was never reached.

            It is the difference between "measured and found wanting" and
            "never looked at", and it is why a partial run still writes a row
            for every reference: a CSV row is positional, so a shorter file
            would silently change what every later row means, and a consumer
            counting rows against the inventory would see nothing wrong.
        anonymous: What the anonymous tail contained, when it was walked.
            `None` when recovery was not asked for, which is why the exclusion
            it feeds reports commits as unknown rather than zero.
        identities: Every contributor identity GitHub reports, anonymous ones
            included, or `None` when the census was skipped or failed.

            This is the honest denominator for coverage. Without it the
            fraction is `collected / collected`, which reads 100% for a
            repository where the real figure is 12% - a number that overstates
            its own completeness, which is worse than no number. One extra REST
            request buys it, whatever the repository's size.
    """

    reference: RepositoryRef
    metadata: RepoMetaData | None = None
    contributors: tuple[Contributor, ...] = ()
    error: CollectionError | None = None
    contributor_error: CollectionError | None = None
    anonymous: AnonymousTally | None = None
    identities: int | None = None
    attempted: bool = True

    @property
    def ok(self) -> bool:
        """Whether the repository was collected."""
        return self.metadata is not None

    @property
    def documented(self) -> bool:
        """Whether this repository should produce a JSON document.

        Both halves have to have succeeded. See the class docstring for why a
        partial document is worse than none.
        """
        return self.metadata is not None and self.contributor_error is None


def collect_all(
    client: GitHubClient,
    references: Sequence[RepositoryRef],
    *,
    max_workers: int | None = None,
    geocoder: Geocoder | None = None,
    options: CollectionOptions | None = None,
    guard: BudgetGuard | None = None,
) -> list[Outcome]:
    """Collect every reference, concurrently, in input order.

    Args:
        client: An authenticated client.
        references: What to collect, in the order to report it.
        max_workers: Concurrent collections. Defaults to
            `min(len(references), 8)`.
        geocoder: Resolves contributor locations. One per run, shared across
            the workers, because its cache and its one-request-per-second pace
            are both properties of the run rather than of a repository.
        options: How complete a contributor picture to gather. Defaults to
            everything the cheap paths can reach.
        guard: Decides what happens when the hourly budget runs out. `None`
            spends without checking, which is what a caller collecting a
            handful of repositories wants.

    Returns:
        One outcome per reference, in the order given.
    """
    if not references:
        return []

    options = options or CollectionOptions()
    workers = max_workers or min(len(references), DEFAULT_MAX_WORKERS)
    LOGGER.info("Collecting %d repositories with %d workers", len(references), workers)

    def one(reference: RepositoryRef) -> Outcome:
        if guard is not None and guard.before(reference.full_name) is Decision.SKIP:
            # The run stopped before reaching this one. Recorded rather than
            # omitted, so the row count still matches the inventory.
            return Outcome(reference=reference, attempted=False)

        try:
            metadata = get_repository(client, reference.owner, reference.repoid)
        except CollectionError as exc:
            # Every reference produces an outcome. Letting this propagate would
            # abandon the repositories after it and lose the ones before it.
            LOGGER.warning("%s could not be collected: %s", reference.full_name, exc)
            return Outcome(reference=reference, error=exc)

        tally: AnonymousTally | None = None
        try:
            if options.recover_anonymous:
                tally = collect_anonymous(client, reference.owner, reference.repoid)
            contributors = get_contributors(
                client,
                reference.owner,
                reference.repoid,
                geocoder=geocoder,
                limit=options.contributor_limit,
                extra=tally.recovered if tally else (),
            )
        except ContributorCollectionError as exc:
            # The measurements survive; only the document is lost. Warned
            # rather than swallowed, because the missing file would otherwise
            # read as "this repository was never named".
            LOGGER.warning(
                "%s was measured but its contributors could not be read, so no "
                "document is written for it: %s",
                reference.full_name,
                exc,
            )
            return Outcome(reference=reference, metadata=metadata, contributor_error=exc)

        # The census is deliberately not inside the try above: a repository
        # whose contributors were read is fully collected, and losing only the
        # denominator should cost the coverage figure rather than the document.
        identities = _census(client, reference, enabled=options.census)

        return Outcome(
            reference=reference,
            metadata=metadata,
            contributors=tuple(contributors),
            anonymous=tally,
            identities=identities,
        )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="collect") as pool:
        outcomes = list(pool.map(one, references))

    skipped = [outcome for outcome in outcomes if not outcome.attempted]
    if skipped:
        LOGGER.warning(
            "%d of %d repositories were never attempted because the budget ran out",
            len(skipped),
            len(outcomes),
        )

    failed = [outcome for outcome in outcomes if outcome.attempted and not outcome.ok]
    if failed:
        LOGGER.warning(
            "%d of %d repositories could not be collected: %s",
            len(failed),
            len(outcomes),
            ", ".join(outcome.reference.full_name for outcome in failed),
        )
    else:
        LOGGER.info("Collected %d repositories", len(outcomes) - len(skipped))

    return outcomes


def _census(client: GitHubClient, reference: RepositoryRef, *, enabled: bool) -> int | None:
    """Count contributor identities, or return `None` and carry on.

    A failure here costs the coverage figure and nothing else, so it is warned
    about rather than raised: the repository was collected, its measurements
    are good, and refusing the document over a missing denominator would throw
    away far more than it protects.

    Args:
        client: An authenticated client.
        reference: The repository being collected.
        enabled: Whether the census was asked for.

    Returns:
        The identity count, or `None` when skipped or unreadable.
    """
    if not enabled:
        return None
    try:
        return count_identities(client, reference.owner, reference.repoid)
    except CollectionError as exc:
        LOGGER.warning(
            "%s: contributor identities could not be counted, so its coverage is unknown: %s",
            reference.full_name,
            exc,
        )
        return None
