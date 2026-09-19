"""Filter classification: exact/suffix/wildcard pattern matching and the
ignore/allow/watch priority rule (watch always wins, even over a
same-domain ignore/allow match)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.filters import classify_domain, matches_pattern, normalize_domain


def rule(id, list_type, pattern, pattern_type="exact", enabled=True):
    return SimpleNamespace(id=id, list_type=list_type, pattern=pattern, pattern_type=pattern_type, enabled=enabled)


@pytest.mark.parametrize(
    "domain,pattern,pattern_type,expected",
    [
        ("example.com", "example.com", "exact", True),
        ("api.example.com", "example.com", "exact", False),
        ("example.com", "example.com", "suffix", True),
        ("api.example.com", "example.com", "suffix", True),
        ("evilexample.com", "example.com", "suffix", False),
        ("api.example.com", "*.example.com", "wildcard", True),
        ("example.com", "*.example.com", "wildcard", False),
    ],
)
def test_matches_pattern(domain, pattern, pattern_type, expected):
    assert matches_pattern(domain, pattern, pattern_type) is expected


def test_classify_domain_no_rules_matches_nothing():
    v = classify_domain("example.com", [])
    assert not v.is_ignored and not v.is_allowed and not v.is_watched
    assert v.matched_rule_ids == []


def test_classify_domain_ignore():
    v = classify_domain("ads.example.com", [rule(1, "ignore", "example.com", "suffix")])
    assert v.is_ignored
    assert v.suppresses_notification
    assert v.matched_rule_ids == [1]


def test_classify_domain_watch_wins_over_ignore():
    """The core priority rule: an operator explicitly watching a domain
    must always be notified, even if it also happens to match a broader
    ignore pattern."""
    rules = [
        rule(1, "ignore", "example.com", "suffix"),
        rule(2, "watch", "ads.example.com", "exact"),
    ]
    v = classify_domain("ads.example.com", rules)
    assert v.is_ignored is True
    assert v.is_watched is True
    assert v.suppresses_notification is False  # watch overrides
    assert v.matched_rule_ids == [1, 2]


def test_classify_domain_disabled_rule_ignored():
    v = classify_domain("example.com", [rule(1, "ignore", "example.com", "exact", enabled=False)])
    assert not v.is_ignored
    assert v.matched_rule_ids == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("example.com", "example.com"),
        ("EXAMPLE.COM", "example.com"),
        ("  example.com.  ", "example.com"),
        ("", None),
        (None, None),
        ("has space.com", None),
        ("has,comma.com", None),
        ("-leadingdash.com", None),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected
