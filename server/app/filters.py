"""Domain normalization and ignore-list matching. Identical rules to the
agent's copy (app/filters.py in domain-monitor-agent) so a domain that
looks valid to the agent also looks valid to the server, and vice versa."""
from __future__ import annotations

import fnmatch
import re

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.[A-Za-z]{2,63}$"
)


def normalize_domain(raw: str) -> str | None:
    if not raw:
        return None
    value = raw.strip().strip(".").lower()
    if not value:
        return None
    if "," in value or " " in value:
        return None
    if not _HOSTNAME_RE.match(value):
        return None
    return value


def matches_ignore(domain: str, patterns: set[str]) -> bool:
    if domain in patterns:
        return True
    for pattern in patterns:
        if domain.endswith("." + pattern):
            return True
    return False


def matches_pattern(domain: str, pattern: str, pattern_type: str) -> bool:
    """Exact/suffix/wildcard matching for filter_rules.

    - exact: domain must equal pattern exactly.
    - suffix: pattern itself, or any subdomain of it (example.com also
      covers api.example.com) - same semantics as matches_ignore().
    - wildcard: fnmatch-style pattern (e.g. "*.example.com"); the caller
      decides whether that should also match the bare "example.com" by
      writing the pattern that way (fnmatch is literal here, no implicit
      subdomain inclusion, unlike suffix).
    """
    if pattern_type == "exact":
        return domain == pattern
    if pattern_type == "suffix":
        return domain == pattern or domain.endswith("." + pattern)
    if pattern_type == "wildcard":
        return fnmatch.fnmatchcase(domain, pattern)
    return False


class FilterVerdict:
    __slots__ = ("is_ignored", "is_allowed", "is_watched")

    def __init__(self, is_ignored: bool, is_allowed: bool, is_watched: bool) -> None:
        self.is_ignored = is_ignored
        self.is_allowed = is_allowed
        self.is_watched = is_watched

    @property
    def suppresses_notification(self) -> bool:
        """Watch always wins (an operator explicitly wants to know), even
        if the same domain also happens to match an ignore/allow rule."""
        return (self.is_ignored or self.is_allowed) and not self.is_watched


def classify_domain(domain: str, rules: "list") -> FilterVerdict:
    is_ignored = is_allowed = is_watched = False
    for rule in rules:
        if not matches_pattern(domain, rule.pattern, rule.pattern_type):
            continue
        if rule.list_type == "ignore":
            is_ignored = True
        elif rule.list_type == "allow":
            is_allowed = True
        elif rule.list_type == "watch":
            is_watched = True
    return FilterVerdict(is_ignored, is_allowed, is_watched)
