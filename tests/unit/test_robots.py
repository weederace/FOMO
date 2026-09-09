from app.utils.robots import robots_allows, robots_rules

# The shape fomo.family publishes: a blanket Allow followed by the real exclusions.
ALLOW_THEN_DISALLOW = """
User-agent: *
Allow: /
Disallow: /export-key
Disallow: /profile/
Disallow: /u/
Disallow: /prices/
"""


def test_a_later_disallow_beats_an_earlier_blanket_allow() -> None:
    # urllib.robotparser returns True for these because it stops at the first match.
    assert robots_allows(ALLOW_THEN_DISALLOW, "https://fomo.family/profile/alice") is False
    assert robots_allows(ALLOW_THEN_DISALLOW, "https://fomo.family/u/alice") is False
    assert robots_allows(ALLOW_THEN_DISALLOW, "https://fomo.family/export-key") is False
    assert robots_allows(ALLOW_THEN_DISALLOW, "https://fomo.family/") is True
    assert robots_allows(ALLOW_THEN_DISALLOW, "https://fomo.family/blog") is True
    assert robots_allows(ALLOW_THEN_DISALLOW, "https://fomo.family/prices") is True


def test_missing_or_empty_rules_allow_everything() -> None:
    assert robots_allows("", "https://fomoapi.io/") is True
    assert robots_allows("User-agent: *\nDisallow:\n", "https://fomoapi.io/anything") is True
    assert robots_allows("User-agent: *\nAllow: /\n", "https://fomoapi.io/") is True


def test_a_blanket_disallow_blocks_everything() -> None:
    blocked = "User-agent: *\nDisallow: /\n"
    assert robots_allows(blocked, "https://example.invalid/") is False
    assert robots_allows(blocked, "https://example.invalid/deep/path") is False


def test_the_longest_matching_rule_wins_in_both_directions() -> None:
    rules = "User-agent: *\nDisallow: /assets/\nAllow: /assets/public/\n"
    assert robots_allows(rules, "https://example.invalid/assets/secret.js") is False
    assert robots_allows(rules, "https://example.invalid/assets/public/app.js") is True


def test_wildcards_and_end_anchors_are_honoured() -> None:
    rules = "User-agent: *\nDisallow: /*.json$\nDisallow: /api/*/private\n"
    assert robots_allows(rules, "https://example.invalid/data.json") is False
    assert robots_allows(rules, "https://example.invalid/data.json?v=1") is True
    assert robots_allows(rules, "https://example.invalid/api/v2/private") is False
    assert robots_allows(rules, "https://example.invalid/api/v2/public") is True


def test_a_named_agent_group_replaces_the_wildcard_group() -> None:
    rules = "User-agent: *\nDisallow: /\n\nUser-agent: FomoWhaleIntelligence\nAllow: /\n"
    assert robots_allows(rules, "https://example.invalid/anything") is True
    assert robots_allows(rules, "https://example.invalid/anything", agent_token="other") is False


def test_grouped_agents_share_one_rule_block() -> None:
    rules = "User-agent: alpha\nUser-agent: beta\nDisallow: /x\n\nUser-agent: *\nAllow: /\n"
    assert robots_rules(rules, "alpha") == [(False, "/x")]
    assert robots_rules(rules, "beta") == [(False, "/x")]
    assert robots_rules(rules, "gamma") == [(True, "/")]


def test_comments_and_blank_lines_are_ignored() -> None:
    rules = "# leading comment\nUser-agent: *  # everyone\nDisallow: /secret  # not for bots\n"
    assert robots_allows(rules, "https://example.invalid/secret") is False
    assert robots_allows(rules, "https://example.invalid/public") is True
