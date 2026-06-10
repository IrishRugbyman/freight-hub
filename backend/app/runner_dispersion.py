from __future__ import annotations

import sys
from functools import lru_cache

import pandas as pd

from .project_paths import project_dir

_FD_SRC = project_dir("freight-dispersion") / "src"
if str(_FD_SRC) not in sys.path:
    sys.path.insert(0, str(_FD_SRC))

from freight_dispersion.config import DispersionConfig
from freight_dispersion.data import load_merged
from freight_dispersion.signals import build_signals, select_position

from quant_lib.backtest import compute_pnl, summary_stats

from .schemas import DispersionPoint, DispersionResponse, DispersionStats

_CONFIG = project_dir("freight-dispersion") / "configs" / "dispersion_meanrev.json"


@lru_cache(maxsize=1)
def run_dispersion_default() -> DispersionResponse:
    cfg = DispersionConfig.from_json(_CONFIG)
    df = load_merged(cfg.start_date, cfg.end_date, cfg.db_path)
    df = build_signals(df, cfg)

    position = select_position(df, cfg.strategy)
    signals = pd.DataFrame({"crack": df["price_5tc"], "position": position}).dropna()
    roll_events = pd.Series(False, index=signals.index)

    pnl_df = compute_pnl(signals, roll_events, cost_per_unit=cfg.cost_per_unit)
    stats = summary_stats(pnl_df)

    def _pts(s: pd.Series, ndigits: int = 2) -> list[DispersionPoint]:
        return [
            DispersionPoint(date=str(idx.date()), value=round(float(v), ndigits))
            for idx, v in s.items()
            if pd.notna(v)
        ]

    return DispersionResponse(
        name=cfg.name,
        strategy=cfg.strategy,
        stats=DispersionStats(
            total_return=round(float(stats["total_return"]), 2),
            ann_return=round(float(stats["ann_return"]), 3),
            ann_volatility=round(float(stats["ann_volatility"]), 3),
            sharpe=round(float(stats["sharpe"]), 3),
            max_drawdown=round(float(stats["max_drawdown"]), 2),
            n_trades=int(stats["n_trades"]),
            hit_rate=round(float(stats["hit_rate"]), 3),
            n_years=round(float(stats["n_years"]), 2),
        ),
        equity=_pts(pnl_df["cum_pnl"]),
        price_5tc=_pts(df["price_5tc"], 0),
        avg_dispersion=_pts(df["avg_dispersion"], 4),
    )
