"""Golden snapshot tests for ChartDataset cards (hero + percentile slider).

Portrait 1080x1350 PNGs compared with a 1% pixel tolerance (Chromium anti-alias
drift). Regenerate references with PADRES_UPDATE_SNAPSHOTS=1.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from padres_analytics.detect.candidates import ChartDataset, Column
from padres_analytics.render.cards import render

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

_SLIDER = ChartDataset(
    title="Fernando Tatis Jr.",
    subtitle="2024 Statcast Percentile Rankings",
    as_of=date(2024, 6, 9),
    columns=[
        Column(key="metric", label="Metric", role="dimension"),
        Column(key="pct", label="Percentile", role="measure", unit="pctile", domain=(0.0, 100.0)),
    ],
    rows=[
        ["xwOBA", 88],
        ["Exit Velocity", 92],
        ["Barrel %", 95],
        ["Hard-Hit %", 90],
        ["Chase %", 71],
        ["Sprint Speed", 84],
    ],
    source="Baseball Savant",
    headline="Tatis profile",
    claim_scope="since_2015",
    facts={"avg_percentile": 87},
)

_HERO = ChartDataset(
    title="Fernando Tatis Jr.",
    subtitle="Barrel Rate · 2024",
    as_of=date(2024, 6, 9),
    columns=[Column(key="brl", label="Barrel %", role="measure", unit="%", domain=(0.0, 40.0))],
    rows=[[18.2]],
    hero={"value": "18.2%", "label": "Barrel Rate", "context": "4th in MLB"},
    framing="4th-best in MLB (Statcast era)",
    source="Baseball Savant",
    headline="Tatis barrel rate",
    claim_scope="since_2015",
    facts={"padre_value": 18.2, "padre_rank": 4},
)

_CASES = [
    ("slider", _SLIDER, "slider_card_reference.png"),
    ("hero", _HERO, "hero_card_reference.png"),
]


def _pixel_diff_pct(ref_path: Path, rendered_path: Path) -> float:
    import numpy as np
    from PIL import Image

    ref = np.asarray(Image.open(ref_path).convert("RGB"), dtype=np.int16)
    ren = np.asarray(Image.open(rendered_path).convert("RGB"), dtype=np.int16)
    assert ref.shape == ren.shape, f"Size mismatch: {ref.shape} vs {ren.shape}"
    differing = np.any(np.abs(ref - ren) > 4, axis=2).sum()
    return float(differing) / (ref.shape[0] * ref.shape[1])


@pytest.mark.parametrize(("name", "payload", "ref_name"), _CASES)
def test_dataset_card_snapshot(
    name: str, payload: ChartDataset, ref_name: str, tmp_path: Path
) -> None:
    """Rendered card must match its committed reference within pixel tolerance."""
    reference = _FIXTURES_DIR / ref_name
    if not reference.exists() and not os.getenv("PADRES_UPDATE_SNAPSHOTS"):
        pytest.skip(f"Reference {ref_name} not committed. Run with PADRES_UPDATE_SNAPSHOTS=1.")

    out_path = render(payload, tmp_path, f"snapshot_{name}")
    assert out_path.exists(), "Renderer returned path but file is missing"

    if os.getenv("PADRES_UPDATE_SNAPSHOTS"):
        import shutil

        _FIXTURES_DIR.mkdir(exist_ok=True)
        shutil.copy2(out_path, reference)
        pytest.skip(f"Reference updated: {reference}")

    pct = _pixel_diff_pct(reference, out_path)
    assert pct <= 0.01, (
        f"{name} card pixel diff {pct:.2%} exceeds 1% tolerance. "
        "If intentional, regenerate with PADRES_UPDATE_SNAPSHOTS=1."
    )


def test_dataset_cards_are_portrait(tmp_path: Path) -> None:
    """Dataset cards render at portrait 1080x1350 (mobile-first)."""
    from PIL import Image

    out_path = render(_HERO, tmp_path, "portrait_check")
    with Image.open(out_path) as img:
        assert img.size == (1080, 1350), f"expected portrait 1080x1350, got {img.size}"
