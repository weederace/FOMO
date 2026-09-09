"""robots.txt matching with longest-match precedence.

`urllib.robotparser` returns the first matching rule, so a file that opens with
`Allow: /` and then lists `Disallow: /profile/` reports `/profile/` as allowed. Real
files are written that way, including `fomo.family`'s. This module applies the
RFC 9309 rule instead: the longest matching pattern wins, and Allow wins a tie.
"""

import re
from urllib.parse import urlsplit

ROBOTS_AGENT_TOKEN = "fomowhaleintelligence"


def robots_rules(robots_text: str, agent_token: str = ROBOTS_AGENT_TOKEN) -> list[tuple[bool, str]]:
    """Collect the `(is_allow, pattern)` rules of the group that applies to one agent."""
    groups: dict[str, list[tuple[bool, str]]] = {}
    agents: list[str] = []
    naming_group = False
    for raw in robots_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if not naming_group:
                agents, naming_group = [], True
            agents.append(value.lower())
            groups.setdefault(value.lower(), [])
        elif field in {"allow", "disallow"}:
            naming_group = False
            for agent in agents:
                groups.setdefault(agent, []).append((field == "allow", value))
    # A group naming the agent replaces the wildcard group rather than adding to it.
    return groups.get(agent_token.lower()) or groups.get("*") or []


def rule_match_length(pattern: str, path: str) -> int | None:
    """Length of `pattern` when it matches `path`, else None. Empty patterns never match."""
    if not pattern:
        return None
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    expression = "".join(".*" if character == "*" else re.escape(character) for character in body)
    expression = f"^{expression}$" if anchored else f"^{expression}"
    return len(pattern) if re.match(expression, path) else None


def robots_allows(robots_text: str, url: str, agent_token: str = ROBOTS_AGENT_TOKEN) -> bool:
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    longest, allowed = -1, True
    for is_allow, pattern in robots_rules(robots_text, agent_token):
        length = rule_match_length(pattern, path)
        if length is None:
            continue
        if length > longest or (length == longest and is_allow):
            longest, allowed = length, is_allow
    return allowed
