"""Alert when freight-api starts answering 5xx.

UptimeRobot watches ``/api/health``, which reports on this process, not on its
endpoints: on 2026-09-28 ``/api/vessels`` returned 500 for minutes while health stayed
green, and it was found by chance. This runs every 5 minutes (``freight-api-errors.timer``),
reads the service's journal since the last run, and exits 1 when it sees a 5xx signature
(normalised path + status) not already alerted in the last ``SUPPRESS_HOURS``. The unit's
``OnFailure=alert-email@%N.service`` then emails this run's output, which carries the
failing requests and the traceback.

The parsing and suppression logic is pure and unit-tested; ``main`` is the journal glue.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

UNIT = "freight-api"
SUPPRESS_HOURS = 6
FIRST_RUN_LOOKBACK = "-10 min"

# uvicorn access line: INFO:     1.2.3.4:0 - "GET /api/vessels?kind=x HTTP/1.0" 500 Internal ...
_ACCESS_RE = re.compile(
    r'"(?P<method>[A-Z]+) (?P<path>[^ ?"]+)(?:\?[^ "]*)? HTTP/[\d.]+" (?P<status>\d{3})'
)
# uvicorn prefixes every log record with its level; a traceback is everything between
# "Exception in ASGI application" and the next record.
_RECORD_RE = re.compile(r"^(INFO|WARNING|ERROR|CRITICAL|DEBUG):\s")
_TRACEBACK_START = "Exception in ASGI application"
# Numeric path segments are ids (MMSI, IMO): one signature per route, not per vessel.
_ID_SEGMENT_RE = re.compile(r"/\d+(?=/|$)")


def normalise_path(path: str) -> str:
    """Collapse numeric path segments to ``{id}`` so one broken route is one signature."""
    return _ID_SEGMENT_RE.sub("/{id}", path)


@dataclass
class Scan:
    """What one pass over the journal found."""

    errors: dict[str, int] = field(default_factory=dict)  # "500 GET /api/x" -> count
    tracebacks: list[list[str]] = field(default_factory=list)

    @property
    def exception_summaries(self) -> list[str]:
        """The final line of each traceback (the exception actually raised), deduplicated."""
        seen: list[str] = []
        for tb in self.tracebacks:
            last = next((ln for ln in reversed(tb) if ln.strip()), "")
            if last and last not in seen:
                seen.append(last)
        return seen


def scan(lines: list[str]) -> Scan:
    """Collect 5xx access lines and uvicorn tracebacks from journal message lines."""
    out = Scan()
    current: list[str] | None = None
    for line in lines:
        if current is not None:
            if _RECORD_RE.match(line):
                out.tracebacks.append(current)
                current = None
            else:
                current.append(line)
                continue
        if _TRACEBACK_START in line:
            current = []
            continue
        m = _ACCESS_RE.search(line)
        if m and m.group("status").startswith("5"):
            sig = f"{m.group('status')} {m.group('method')} {normalise_path(m.group('path'))}"
            out.errors[sig] = out.errors.get(sig, 0) + 1
    if current is not None:
        out.tracebacks.append(current)
    return out


def new_signatures(
    errors: dict[str, int], last_alerted: dict[str, str], now: datetime
) -> list[str]:
    """Signatures seen now that were not alerted within ``SUPPRESS_HOURS``, sorted."""
    cutoff = now - timedelta(hours=SUPPRESS_HOURS)
    fresh = []
    for sig in errors:
        prev = last_alerted.get(sig)
        if prev is None or datetime.fromisoformat(prev) < cutoff:
            fresh.append(sig)
    return sorted(fresh)


def report(result: Scan, fresh: list[str]) -> str:
    """The alert text: new signatures, every 5xx seen this pass, first traceback in full."""
    lines = [f"freight-api answered 5xx; new since last alert: {', '.join(fresh)}", ""]
    lines += [f"  {count:>4} x {sig}" for sig, count in sorted(result.errors.items())]
    if result.exception_summaries:
        lines += ["", "Exceptions:"] + [f"  {s.strip()}" for s in result.exception_summaries]
    if result.tracebacks:
        lines += ["", "First traceback:"] + result.tracebacks[0]
    return "\n".join(lines)


def _journal(cursor: str | None) -> tuple[list[str], str | None]:
    args = ["journalctl", "-u", UNIT, "-o", "cat", "--no-pager", "--show-cursor"]
    args += [f"--after-cursor={cursor}"] if cursor else ["--since", FIRST_RUN_LOOKBACK]
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout.splitlines()
    new_cursor = cursor
    if out and out[-1].startswith("-- cursor: "):
        new_cursor = out.pop()[len("-- cursor: ") :]
    return out, new_cursor


def main() -> int:
    """One pass: scan the journal since the saved cursor, exit 1 on new 5xx signatures."""
    state_dir = Path(
        os.environ.get("STATE_DIRECTORY", Path.home() / ".local/state/freight-api-errors")
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}

    lines, cursor = _journal(state.get("cursor"))
    result = scan(lines)
    now = datetime.now(UTC)
    last_alerted: dict[str, str] = state.get("last_alerted", {})
    fresh = new_signatures(result.errors, last_alerted, now)
    for sig in fresh:
        last_alerted[sig] = now.isoformat()
    # Forget signatures long past suppression so the state file stays small.
    horizon = now - timedelta(days=7)
    last_alerted = {s: t for s, t in last_alerted.items() if datetime.fromisoformat(t) >= horizon}

    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"cursor": cursor, "last_alerted": last_alerted}, indent=2))
    tmp.replace(state_file)

    if fresh:
        print(report(result, fresh))
        return 1
    if result.errors:
        print(f"5xx seen but all suppressed (<{SUPPRESS_HOURS}h): {result.errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
