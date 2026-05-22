"""CSV report generation."""

from __future__ import annotations

from pathlib import Path
from typing import List

import pandas as pd

from ..models import TradeCandidate


def write_csvs(all_cands: List[TradeCandidate], top: List[TradeCandidate], out_dir: Path) -> tuple[Path, Path]:
    full_rows = [c.to_flat_dict() for c in all_cands]
    full_df = pd.DataFrame(full_rows)
    if not full_df.empty:
        full_df = full_df.sort_values("composite_score", ascending=False)
        full_df["rank"] = range(1, len(full_df) + 1)

    top_rows = [c.to_flat_dict() for c in top]
    top_df = pd.DataFrame(top_rows)
    if not top_df.empty:
        top_df["rank"] = range(1, len(top_df) + 1)

    full_path = out_dir / "all_candidates.csv"
    top_path  = out_dir / "top15_candidates.csv"
    full_df.to_csv(full_path, index=False)
    top_df.to_csv(top_path, index=False)
    return full_path, top_path
