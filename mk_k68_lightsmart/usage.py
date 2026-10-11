"""What the AI coding tools on this machine have spent.

Both Claude Code and Codex keep a transcript of every session on disk,
and both record the token usage of each turn in it. Nothing here talks
to a network or needs a login: it reads the newest transcript each tool
has written and reports what is in it.

What is available is not the same on both sides. Codex writes the plan's
own rate-limit percentages into its rollouts; Claude Code does not write
them anywhere local, so that field comes back ``None`` rather than being
guessed at.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

CLAUDE_ROOT = Path.home() / ".claude" / "projects"
CODEX_ROOT = Path.home() / ".codex" / "sessions"

#: A session not written to for this long is over, not in progress.
LIVE_SECONDS = 15 * 60

#: Claude Code's transcripts never state the context window, so it is
#: inferred: the smallest of these that holds the fullest turn the
#: session actually had. Guessing 200_000 and calling it a percentage
#: is how this first read 589K as "100%".
CLAUDE_WINDOW_STEPS = (200_000, 500_000, 1_000_000)


@dataclass(frozen=True)
class Reading:
    """One tool's current session, as far as its transcript shows."""

    tool: str
    #: what the session actually spent: new input, cache writes and
    #: output, with the cache *re-reads* left out
    tokens: int
    #: tokens in the live context window, as of the last turn
    context: int
    context_window: int | None
    #: how much of the plan's shorter window is gone, 0-100
    plan_percent: float | None
    plan_window_minutes: int | None
    #: and the longer one, where there is one
    plan_long_percent: float | None
    #: when the transcript was last written
    updated: float
    source: Path | None

    @property
    def found(self) -> bool:
        return self.source is not None

    @property
    def live(self) -> bool:
        """Whether this session looks like it is still going."""
        return self.found and (time.time() - self.updated) < LIVE_SECONDS

    @property
    def context_percent(self) -> float | None:
        if not self.context_window:
            return None
        return min(100.0, 100.0 * self.context / self.context_window)


def _newest(root: Path) -> Path | None:
    """The transcript written to most recently, anywhere under ``root``."""
    newest, when = None, -1.0
    if not root.exists():
        return None
    for path in root.rglob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > when:
            newest, when = path, mtime
    return newest


def _rows(path: Path, needle: str):
    """Parsed lines containing ``needle``, skipping anything malformed.

    A transcript is written while it is read, so a truncated last line
    is normal rather than an error.
    """
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if needle not in line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def empty(tool: str) -> Reading:
    return Reading(tool=tool, tokens=0, context=0, context_window=None,
                   plan_percent=None, plan_window_minutes=None,
                   plan_long_percent=None, updated=0.0, source=None)


def claude_reading(root: Path | None = None) -> Reading:
    """Claude Code's newest session.

    Tokens are summed over every assistant turn, counting new input,
    cache writes and output but not cache reads: the whole context is
    re-read every turn, so counting those reports a quarter of a
    billion tokens for an afternoon's work.

    The context is the last turn's, since that is what the model was
    holding when it answered.
    """
    path = _newest(root or CLAUDE_ROOT)
    if path is None:
        return empty("claude")

    total = 0
    context = 0
    fullest = 0
    for row in _rows(path, '"usage"'):
        message = row.get("message") or {}
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        held = (int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0))
        out = int(usage.get("output_tokens") or 0)
        # New tokens only. Counting cache reads as spending turns a
        # long session into hundreds of millions: the same context is
        # re-read every turn.
        total += (int(usage.get("input_tokens") or 0)
                  + int(usage.get("cache_creation_input_tokens") or 0) + out)
        context = held + out
        fullest = max(fullest, context)

    window = next((step for step in CLAUDE_WINDOW_STEPS if step >= fullest),
                  CLAUDE_WINDOW_STEPS[-1])

    return Reading(
        tool="claude", tokens=total, context=context, context_window=window,
        # Claude Code keeps no rate-limit figure on disk. Reporting one
        # would mean inventing it.
        plan_percent=None, plan_window_minutes=None, plan_long_percent=None,
        updated=path.stat().st_mtime, source=path,
    )


def codex_reading(root: Path | None = None) -> Reading:
    """Codex's newest session.

    Codex keeps running totals of its own, so this reads the last
    ``token_count`` event rather than adding anything up, and that event
    carries the plan's rate limits with it.
    """
    path = _newest(root or CODEX_ROOT)
    if path is None:
        return empty("codex")

    info: dict = {}
    limits: dict = {}
    for row in _rows(path, '"token_count"'):
        payload = row.get("payload") or {}
        if payload.get("type") != "token_count":
            continue
        info = payload.get("info") or info
        limits = payload.get("rate_limits") or limits

    totals = info.get("total_token_usage") or {}
    last = info.get("last_token_usage") or {}
    primary = limits.get("primary") or {}
    secondary = limits.get("secondary") or {}

    # The same measure as the Claude side: what was actually spent,
    # with the cache re-reads left out, so the two can be compared.
    spent = (int(totals.get("input_tokens") or 0)
             - int(totals.get("cached_input_tokens") or 0)
             + int(totals.get("cache_write_input_tokens") or 0)
             + int(totals.get("output_tokens") or 0))

    return Reading(
        tool="codex",
        tokens=max(0, spent),
        context=int(last.get("input_tokens") or 0) + int(last.get("output_tokens") or 0),
        context_window=info.get("model_context_window"),
        plan_percent=_percent(primary.get("used_percent")),
        plan_window_minutes=primary.get("window_minutes"),
        plan_long_percent=_percent(secondary.get("used_percent")),
        updated=path.stat().st_mtime, source=path,
    )


def _percent(value) -> float | None:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return None


def read_all() -> list[Reading]:
    """Both tools, in the order the meter shows them."""
    return [claude_reading(), codex_reading()]


def short(count: int) -> str:
    """A token count in four characters, which is all the strip has.

    The decimal only survives while it fits: 2.5M has room for it,
    988M does not, and 987.7M would run off the end of the panel.
    """
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if count < size:
            continue
        scaled = count / size
        if scaled < 10:
            return f"{scaled:.1f}{suffix}".replace(f".0{suffix}", suffix)
        return f"{count // size}{suffix}"
    return str(count)
