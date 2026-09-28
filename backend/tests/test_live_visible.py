"""`/api/vessels` "ghost" rows: vessels evicted from live_positions, served from snapshots.

Ghost rows are named from the PG ``vessels`` master by MMSI. MMSI is not unique there
(66 MMSIs had two rows on 2026-09-28: reuse between hulls, and rows built from garbled
AIS static data), and a non-unique index made ``Series.map`` raise, so ``/api/vessels``
returned 500 whenever such a vessel was among the ghosts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import duckdb
from conftest import _SCHEMA, setup_pg_vessels
from fastapi.testclient import TestClient

_NOW = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
_GHOST_MMSI = 215170000


def _client(tmp_path, monkeypatch, registry_rows: list[dict]) -> TestClient:
    ais_file = tmp_path / "ais.duckdb"
    conn = duckdb.connect(str(ais_file))
    conn.execute(_SCHEMA)
    # One vessel still live, so the ghost path runs alongside a normal row.
    conn.execute(
        "INSERT INTO live_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            636000001,
            "LIVE ONE",
            1.2,
            103.8,
            11.0,
            90.0,
            90.0,
            "SGSIN",
            80,
            330.0,
            "tanker",
            "VLCC",
            "singapore_malacca",
            _NOW - timedelta(minutes=5),
            9000001,
            20.0,
            0,
            None,
        ],
    )
    # The ghost: seen 6h ago in snapshots, no longer in live_positions.
    conn.execute(
        "INSERT INTO ais_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            _NOW - timedelta(hours=6),
            _GHOST_MMSI,
            "tanker",
            "Aframax",
            "med",
            35.0,
            20.0,
            80,
            245.0,
            0.1,
            1,
            12.0,
            "PIRAEUS",
        ],
    )
    conn.close()
    setup_pg_vessels(monkeypatch, registry_rows)
    monkeypatch.setenv("AIS_POSITIONS_DB", str(ais_file))
    from app.main import app

    return TestClient(app)


def test_vessels_ghost_with_duplicate_registry_mmsi_is_served(tmp_path, monkeypatch):
    client = _client(
        tmp_path,
        monkeypatch,
        [
            # The verified hull, updated earlier.
            {
                "imo": 9240964,
                "mmsi": _GHOST_MMSI,
                "ship_name": "NEPTUNE AEGLI",
                "fetch_ok": True,
                "updated_at": _NOW - timedelta(days=60),
            },
            # A later row built from a garbled AIS IMO (8 digits): newest, but wrong.
            {
                "imo": 92409640,
                "mmsi": _GHOST_MMSI,
                "ais_name": "NEPTUNE_AEGLI",
                "updated_at": _NOW - timedelta(days=1),
            },
        ],
    )

    r = client.get("/api/vessels")

    assert r.status_code == 200
    ghost = {v["mmsi"]: v for v in r.json()}[_GHOST_MMSI]
    assert ghost["imo"] == 9240964
    assert ghost["name"] == "NEPTUNE AEGLI"


def test_vessels_ghost_prefers_verified_row_among_valid_imos(tmp_path, monkeypatch):
    client = _client(
        tmp_path,
        monkeypatch,
        [
            {
                "imo": 9374014,
                "mmsi": _GHOST_MMSI,
                "ship_name": "BOMAR VENUS",
                "fetch_ok": True,
                "updated_at": _NOW - timedelta(days=30),
            },
            {
                "imo": 9373967,
                "mmsi": _GHOST_MMSI,
                "ais_name": "BOMAR VENUS OLD",
                "updated_at": _NOW - timedelta(days=1),
            },
        ],
    )

    ghost = {v["mmsi"]: v for v in client.get("/api/vessels").json()}[_GHOST_MMSI]

    assert ghost["imo"] == 9374014


def _first_stream_event(**filters) -> list[dict]:
    """Parse one frame from the stream's event builder (TestClient cannot end an SSE loop)."""
    import json

    from app.routers.tracker import _stream_event

    frame = _stream_event(**filters)
    assert frame is not None and frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: ") :])


def test_stream_event_is_valid_json_in_the_polled_shape(tmp_path, monkeypatch):
    """The live row has cog = NaN; json.dumps used to emit a bare NaN, which no browser
    parses, so every event was dropped."""
    client = _client(tmp_path, monkeypatch, [])
    conn = duckdb.connect(str(tmp_path / "ais.duckdb"))
    conn.execute("UPDATE live_positions SET cog = 'NaN'::DOUBLE WHERE mmsi = 636000001")
    conn.close()

    event = _first_stream_event()

    polled = {v["mmsi"]: v for v in client.get("/api/vessels").json()}
    by_mmsi = {v["mmsi"]: v for v in event}
    live = by_mmsi[636000001]
    assert live["cog"] is None
    # Same fields as the poll, so a merge by MMSI loses nothing.
    assert set(live) == set(polled[636000001])
    assert live["flag_code"] == "LR"
    # The ghost's last fix is 6 h old: outside the stream's 30-minute window.
    assert _GHOST_MMSI not in by_mmsi


def test_stream_applies_the_poll_filters(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch, [])

    assert [v["mmsi"] for v in _first_stream_event(flag="LR")] == [636000001]
    assert _stream_event_or_none(flag="PA") is None


def _stream_event_or_none(**filters):
    from app.routers.tracker import _stream_event

    return _stream_event(**filters)
