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
from github_metrics.collect.contributors import (
    DEFAULT_CONTRIBUTOR_LIMIT,
    get_contributors,
)
from github_metrics.collect.repository import RepoMetaData, get_repository
from github_metrics.errors import CollectionError, ContributorCollectionError
from github_metrics.geo import Geocoder
from github_metrics.model.contributor import Contributor
from github_metrics.sources import RepositoryRef

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS: Final = 8
"""Concurrent collections. Small on purpose: the limit is quota, not CPU."""


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
    """

    reference: RepositoryRef
    metadata: RepoMetaData | None = None
    contributors: tuple[Contributor, ...] = ()
    error: CollectionError | None = None
    contributor_error: CollectionError | None = None

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
    contributor_limit: int | None = DEFAULT_CONTRIBUTOR_LIMIT,
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
        contributor_limit: Contributors kept per repository. `None`, the
            default, keeps every one GitHub returns.

    Returns:
        One outcome per reference, in the order given.
    """
    if not references:
        return []

    workers = max_workers or min(len(references), DEFAULT_MAX_WORKERS)
    LOGGER.info("Collecting %d repositories with %d workers", len(references), workers)

    def one(reference: RepositoryRef) -> Outcome:
        try:
            metadata = get_repository(client, reference.owner, reference.repoid)
        except CollectionError as exc:
            # Every reference produces an outcome. Letting this propagate would
            # abandon the repositories after it and lose the ones before it.
            LOGGER.warning("%s could not be collected: %s", reference.full_name, exc)
            return Outcome(reference=reference, error=exc)

        try:
            contributors = get_contributors(
                client,
                reference.owner,
                reference.repoid,
                geocoder=geocoder,
                limit=contributor_limit,
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

        return Outcome(
            reference=reference,
            metadata=metadata,
            contributors=tuple(contributors),
        )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="collect") as pool:
        outcomes = list(pool.map(one, references))

    failed = [outcome for outcome in outcomes if not outcome.ok]
    if failed:
        LOGGER.warning(
            "%d of %d repositories could not be collected: %s",
            len(failed),
            len(outcomes),
            ", ".join(outcome.reference.full_name for outcome in failed),
        )
    else:
        LOGGER.info("Collected %d repositories", len(outcomes))

    return outcomes
