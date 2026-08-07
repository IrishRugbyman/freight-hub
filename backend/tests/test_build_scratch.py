"""Scratch-file lifecycle for the analytics build.

The build writes to `freight_analytics.new.duckdb` and promotes it with
`os.replace`. A DuckDB write-ahead log is bound to a database *path*, not an
inode, so every rename or unlink has to move the `.wal` in step with its
`.duckdb`. Two crashed runs (2026-08-05, 2026-08-07) each stranded a scratch WAL
that would have replayed a dead run's writes into the next build's fresh copy.
"""

from __future__ import annotations

import duckdb
import pytest
from analytics import build


@pytest.fixture
def scratch_paths(tmp_path, monkeypatch):
    """Point the build's live/scratch paths into a temp dir."""
    live = tmp_path / "freight_analytics.duckdb"
    new = tmp_path / "freight_analytics.new.duckdb"
    monkeypatch.setattr(build, "ANALYTICS_DB", live)
    monkeypatch.setattr(build, "_ANALYTICS_NEW", new)
    return live, new


def test_wal_for_appends_suffix_without_replacing_it(tmp_path):
    # .with_suffix() would turn foo.new.duckdb into foo.new.wal and lose the pairing.
    assert build._wal_for(tmp_path / "a.duckdb").name == "a.duckdb.wal"
    assert build._wal_for(tmp_path / "a.new.duckdb").name == "a.new.duckdb.wal"


def test_commit_scratch_promotes_scratch_over_live(scratch_paths):
    live, new = scratch_paths
    live.write_bytes(b"old")
    new.write_bytes(b"fresh")

    build._commit_scratch()

    assert live.read_bytes() == b"fresh"
    assert not new.exists()


def test_commit_scratch_carries_scratch_wal_across_the_rename(scratch_paths):
    live, new = scratch_paths
    live.write_bytes(b"old")
    new.write_bytes(b"fresh")
    build._wal_for(new).write_bytes(b"pending-writes")

    build._commit_scratch()

    # The WAL holds committed-but-uncheckpointed data: it must follow its DB.
    assert build._wal_for(live).read_bytes() == b"pending-writes"
    assert not build._wal_for(new).exists()


def test_commit_scratch_drops_stale_live_wal_when_scratch_has_none(scratch_paths):
    live, new = scratch_paths
    live.write_bytes(b"old")
    build._wal_for(live).write_bytes(b"stale-from-a-previous-file")
    new.write_bytes(b"fresh")

    build._commit_scratch()

    # A cleanly closed scratch has no WAL; any WAL beside the live path now
    # refers to a replaced inode and would replay into the wrong database.
    assert not build._wal_for(live).exists()


def test_commit_scratch_is_a_noop_without_a_scratch_file(scratch_paths):
    live, _ = scratch_paths
    live.write_bytes(b"old")

    build._commit_scratch()

    assert live.read_bytes() == b"old"


def test_open_analytics_scratch_discards_a_stranded_scratch_wal(scratch_paths):
    live, new = scratch_paths
    duckdb.connect(str(live)).close()
    new.write_bytes(b"leftover-from-a-killed-run")
    build._wal_for(new).write_bytes(b"writes-from-a-killed-run")

    conn = build._open_analytics_scratch()
    try:
        # Opening the scratch starts a WAL of its own, so presence proves nothing -
        # what matters is that the dead run's bytes are not in it, and so cannot
        # replay into the fresh copy.
        surviving = build._wal_for(new)
        assert not surviving.exists() or b"killed-run" not in surviving.read_bytes()
    finally:
        conn.close()


def test_open_analytics_scratch_applies_the_memory_ceiling(scratch_paths):
    live, _ = scratch_paths
    duckdb.connect(str(live)).close()

    conn = build._open_analytics_scratch()
    try:
        limit = conn.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    finally:
        conn.close()

    # DuckDB normalises "2GB" to its own units, e.g. "1.8 GiB" - assert the ceiling
    # lands under the unit's MemoryMax=5G rather than matching a formatting quirk.
    # Unset, this would be 80% of system RAM, which on the 7.6 GB host is above it.
    value, unit = limit.split()
    assert unit.upper() == "GIB"
    assert 0 < float(value) < 4
