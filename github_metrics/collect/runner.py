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
from github_metrics.collect.repository import RepoMetaData, get_repository
from github_metrics.errors import CollectionError
from github_metrics.sources import RepositoryRef

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS: Final = 8
"""Concurrent collections. Small on purpose: the limit is quota, not CPU."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one reference produced.

    Attributes:
        reference: The reference as the input named it.
        metadata: What GitHub reported, or `None` if it could not be read.
        error: Why there is no metadata, or `None` on success.
    """

    reference: RepositoryRef
    metadata: RepoMetaData | None = None
    error: CollectionError | None = None

    @property
    def ok(self) -> bool:
        """Whether the repository was collected."""
        return self.metadata is not None


def collect_all(
    client: GitHubClient,
    references: Sequence[RepositoryRef],
    *,
    max_workers: int | None = None,
) -> list[Outcome]:
    """Collect every reference, concurrently, in input order.

    Args:
        client: An authenticated client.
        references: What to collect, in the order to report it.
        max_workers: Concurrent collections. Defaults to
            `min(len(references), 8)`.

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
        return Outcome(reference=reference, metadata=metadata)

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
