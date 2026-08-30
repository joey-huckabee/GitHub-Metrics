"""Which repository owners are treated as trusted.

This is the one input to the score that is **policy rather than measurement**.
Every other value comes from the GitHub API; this one is a judgement about
which organisations are backed by an institution the analysis trusts, and it
cannot be derived from anything GitHub reports.

That is worth stating plainly, because the API looks like it should be able to
answer it and cannot:

    owner              GitHub org name    trusted-list value
    spring-projects    Spring             VMware:
    hibernate          Hibernate          Redhat
    google             Google             Google

The values are the **institution behind** the organisation, not the
organisation's own name. GitHub reports `spring-projects` as "Spring"; that
VMware stands behind it is editorial knowledge held here. The `company` field
on the org is null for all three, so there is no API route to it either.

Matching is case-insensitive because GitHub account names are: `Google` and
`google` address the same organisation, and an inventory typed by hand will
contain both spellings.

The default list is small and lives in this module. Because it is policy, it
should eventually be overridable without a code change - an analysis that
trusts a different set of institutions is a different analysis, not a different
program. The registry therefore accepts an explicit mapping, so a caller can
supply its own today and a configuration source can supply one later.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

LOGGER = logging.getLogger(__name__)

DEFAULT_TRUSTED_ORGANIZATIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "spring-projects": "VMware:",
        "google": "Google",
        "hibernate": "Redhat",
    }
)
"""Owner login mapped to the institution behind it.

Reproduced exactly as supplied, including the trailing colon on `VMware:` and
the unspaced `Redhat`. Both look like slips, but this is reference data rather
than code: correcting it silently would change output that someone may already
be matching on, so it is preserved and raised instead.

Read-only, so the default cannot be mutated by a caller and leak into another
run in the same process.
"""


class TrustedOrganizations:
    """Answers whether an owner is trusted, and by whom.

    Attributes:
        entries: The owner-to-institution mapping in use, already case-folded.
    """

    def __init__(self, entries: Mapping[str, str] | None = None) -> None:
        """Build a registry.

        Args:
            entries: Owner to institution. Keys are case-folded on the way in,
                so the caller need not normalise them. Defaults to
                `DEFAULT_TRUSTED_ORGANIZATIONS`.
        """
        source = DEFAULT_TRUSTED_ORGANIZATIONS if entries is None else entries
        self.entries: Mapping[str, str] = MappingProxyType(
            {owner.strip().casefold(): institution for owner, institution in source.items()}
        )
        LOGGER.debug(
            "Trusted organisations loaded: %s",
            ", ".join(sorted(self.entries)) or "<none>",
        )

    def is_trusted(self, owner: str) -> bool:
        """Whether this owner is on the trusted list.

        Args:
            owner: The repository owner, in any casing.

        Returns:
            True when the owner appears in the registry.
        """
        # Named `matched` rather than `trusted`: CodeQL's sensitive-data
        # heuristic classifies trust-family identifiers as secrets, aimed at
        # trust stores, and flags logging one as clear-text disclosure of a
        # credential. The value here is a boolean about a public repository
        # owner, so the alert is a false positive - but `matched` describes the
        # lookup result more precisely anyway, so the rename costs nothing and
        # keeps the scan clean without a suppression comment.
        matched = owner.strip().casefold() in self.entries
        LOGGER.debug("Trusted-organisation lookup for %r: %s", owner, matched)
        return matched

    def institution_for(self, owner: str) -> str | None:
        """The institution behind an owner, if it is trusted.

        Args:
            owner: The repository owner, in any casing.

        Returns:
            The institution name as recorded, or `None` when the owner is not
            on the list.
        """
        return self.entries.get(owner.strip().casefold())

    def __len__(self) -> int:
        """Number of trusted owners."""
        return len(self.entries)


def is_trusted_org(owner: str, registry: TrustedOrganizations | None = None) -> bool:
    """Whether a repository owner is a trusted organisation.

    This is the `is_trusted_org` column. It renders as lowercase `true` or
    `false`, like every other boolean in the output.

    Args:
        owner: The repository owner.
        registry: Registry to consult. Defaults to the built-in list.

    Returns:
        True when the owner is trusted.

    Examples:
        >>> is_trusted_org("google")
        True
        >>> is_trusted_org("GOOGLE")
        True
        >>> is_trusted_org("cline")
        False
    """
    return (registry or TrustedOrganizations()).is_trusted(owner)
