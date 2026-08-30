"""Collecting release and tag counts for a repository.

What is counted
---------------
Two separate numbers, both from one GraphQL query:

- **releases** - GitHub Release objects, the published artifacts with notes
- **tags** - git tags, `refs/tags/*`

They are collected separately because they answer different questions, and
because the relationship between them is the crux of how this metric should be
defined. See `docs/METRICS.md`.

Releases are a subset of tags
-----------------------------
Creating a GitHub Release requires a tag, so every published release has a
corresponding entry in the tag list. Measured across five repositories, the
number of release tag names absent from the tag list was **zero every time**:

    urllib3/urllib3    58 releases, 108 tags, 58 of 58 release tags present
    pypa/virtualenv    98 releases, 285 tags, 98 of 98 release tags present

That is why `distinct_versions` is not `releases + tags`. Adding them counts
every release twice, and the resulting inflation is uneven - it ranges from
1.00x for a project that only tags to 1.56x for one that publishes a release
for most tags. Since publishing GitHub Releases is a workflow preference
rather than a measure of how established a project is, the sum biases the
score toward a tooling choice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from github_metrics.client import GitHubClient
from github_metrics.collect.graphql import execute
from github_metrics.errors import RepositoryNotFoundError

LOGGER = logging.getLogger(__name__)

RELEASE_COUNTS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    releases { totalCount }
    tags: refs(refPrefix: "refs/tags/") { totalCount }
  }
}
"""
"""Counts only, so the cost is one point however many releases exist."""


@dataclass(frozen=True, slots=True)
class ReleaseCounts:
    """Release and tag counts for one repository.

    Attributes:
        releases: Published GitHub Releases. Draft releases are visible only to
            a token with push access, so this number can differ between users
            scanning the same repository.
        tags: Entries under `refs/tags/`. Unlike releases, this is the same for
            everyone, which is one reason to prefer it as the scored value.
    """

    releases: int
    tags: int

    @property
    def distinct_versions(self) -> int:
        """Version markers, counting each one once.

        Every published release has a tag, so the tag count is already the
        union of the two. `max` rather than plain `tags` guards the one case
        that could break that: a draft release may name a tag that does not
        exist yet, which would put `releases` above `tags` for a token that can
        see drafts. Taking the larger never under-counts, and equals the tag
        count in every repository measured.
        """
        return max(self.releases, self.tags)

    @property
    def legacy_sum(self) -> int:
        """`releases + tags`, the original definition.

        Retained only so a log line can state what the previous definition
        would have reported, which is what makes a change in a stored score
        explainable rather than mysterious. It double-counts every release and
        is not the scored value.
        """
        return self.releases + self.tags

    @property
    def tags_without_releases(self) -> int:
        """Tags that carry no published release."""
        return max(self.tags - self.releases, 0)


def get_release_counts(client: GitHubClient, owner: str, repoid: str) -> ReleaseCounts:
    """Fetch release and tag counts for one repository.

    Costs one GraphQL point regardless of how many releases or tags exist,
    because only totals are requested.

    Args:
        client: An authenticated client.
        owner: The account owning the repository.
        repoid: The repository name.

    Returns:
        The two counts.

    Raises:
        RepositoryNotFoundError: The repository does not exist, is private to
            this token, or was renamed.
        GraphQLQueryError: The API reported some other error.
    """
    slug = f"{owner}/{repoid}"
    LOGGER.debug("Collecting release and tag counts for %s", slug)

    data = execute(
        client,
        RELEASE_COUNTS_QUERY,
        {"owner": owner, "name": repoid},
        description=f"releases for {slug}",
    )

    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise RepositoryNotFoundError(f"{slug}: the API returned no repository and no error")

    counts = ReleaseCounts(
        releases=int(repository["releases"]["totalCount"]),
        tags=int(repository["tags"]["totalCount"]),
    )

    _log_shape(slug, counts)
    return counts


def _log_shape(slug: str, counts: ReleaseCounts) -> None:
    """Narrate what the counts say about how the project publishes.

    Verbose on purpose. These two numbers are read together, and the useful
    information is in their relationship rather than in either alone - a
    reader looking at `398` cannot tell whether that is most of the tags or a
    handful of them.
    """
    LOGGER.debug(
        "%s: %d releases, %d tags, %d distinct versions",
        slug,
        counts.releases,
        counts.tags,
        counts.distinct_versions,
    )

    if counts.releases > counts.tags:
        # Only reachable for a token that can see draft releases naming tags
        # that do not exist yet. Worth saying out loud, because it means this
        # repository's number depends on who scanned it.
        LOGGER.warning(
            "%s reports more releases (%d) than tags (%d); draft releases are visible "
            "to this token, so this count is not reproducible by another user",
            slug,
            counts.releases,
            counts.tags,
        )
    elif counts.releases == 0 and counts.tags > 0:
        LOGGER.debug(
            "%s tags versions (%d) but publishes no GitHub Releases; counting releases "
            "alone would score it zero",
            slug,
            counts.tags,
        )
    elif counts.tags == 0:
        LOGGER.debug("%s has no tags and no releases; it has shipped no versioned artifact", slug)
    else:
        LOGGER.debug(
            "%s: %d of %d tags carry a release (%d tags without one)",
            slug,
            counts.releases,
            counts.tags,
            counts.tags_without_releases,
        )

    if counts.legacy_sum != counts.distinct_versions:
        inflation = counts.legacy_sum / counts.distinct_versions
        LOGGER.debug(
            "%s: the previous definition (releases + tags) would report %d, "
            "%.2fx the distinct count, because it counts every release twice",
            slug,
            counts.legacy_sum,
            inflation,
        )
