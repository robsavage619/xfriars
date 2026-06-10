"""Snapshot test for the card renderer.

Renders a fixed fixture payload and compares against a committed reference PNG.
Pixel tolerance is used (not byte-compare) because Chromium versions produce
slightly different outputs across upgrades.

To generate/update the reference: run with PADRES_UPDATE_SNAPSHOTS=1.
  PADRES_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_render_snapshot.py -v
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from padres_analytics.detect.candidates import TablePayload
from padres_analytics.render.cards import render

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_REFERENCE_PNG = _FIXTURES_DIR / "table_card_reference.png"

# Fixed payload — never changes (snapshot test relies on determinism)
_FIXTURE_PAYLOAD = TablePayload(
    kind="table",
    title="Padres on Jun 9",
    subtitle="since 1990 · 3W-2L in 5 games",
    as_of=date(2024, 6, 9),
    columns=["Year", "Opp", "W/L", "Score", "H/A"],
    rows=[
        ["2023", "MIL", "W", "5-3", "H"],
        ["2022", "LAD", "L", "2-7", "A"],
        ["2021", "NYM", "W", "6-4", "H"],
        ["2019", "STL", "W", "7-3", "A"],
        ["2015", "COL", "W", "9-2", "A"],
    ],
    highlight_row=None,
    source="Baseball-Reference",
    headline="Padres are 3-2 on Jun 9 since 1990",
    claim_scope="since_1990",
)


@pytest.mark.skipif(
    not _REFERENCE_PNG.exists() and not os.getenv("PADRES_UPDATE_SNAPSHOTS"),
    reason="Reference PNG not committed yet. Run with PADRES_UPDATE_SNAPSHOTS=1 to generate.",
)
def test_table_card_snapshot(tmp_path: Path) -> None:
    """Rendered PNG must match the reference within pixel tolerance."""
    from PIL import Image

    out_path = render(_FIXTURE_PAYLOAD, tmp_path, "snapshot_test")
    assert out_path.exists(), "Renderer returned path but file is missing"

    if os.getenv("PADRES_UPDATE_SNAPSHOTS"):
        _FIXTURES_DIR.mkdir(exist_ok=True)
        import shutil

        shutil.copy2(out_path, _REFERENCE_PNG)
        pytest.skip(f"Reference updated: {_REFERENCE_PNG}")
        return

    ref = Image.open(_REFERENCE_PNG).convert("RGB")
    rendered = Image.open(out_path).convert("RGB")

    assert ref.size == rendered.size, (
        f"Size mismatch: reference={ref.size}, rendered={rendered.size}"
    )

    # Per-pixel comparison with 1% tolerance
    # (Chromium minor version bumps can shift anti-aliasing by ±2 values)

    ref_bytes = ref.tobytes()
    ren_bytes = rendered.tobytes()
    total_pixels = ref.width * ref.height
    differing = sum(
        1
        for i in range(0, len(ref_bytes), 3)
        if any(abs(ref_bytes[j] - ren_bytes[j]) > 4 for j in range(i, i + 3))
    )
    pct = differing / total_pixels
    assert pct <= 0.01, (
        f"Pixel diff {pct:.2%} exceeds 1% tolerance. "
        "If intentional, regenerate reference with PADRES_UPDATE_SNAPSHOTS=1."
    )


def test_render_produces_file(tmp_path: Path) -> None:
    """Renderer produces a non-empty PNG (no reference comparison)."""
    out_path = render(_FIXTURE_PAYLOAD, tmp_path, "basic_render_test")
    assert out_path.exists()
    assert out_path.suffix == ".png"
    assert out_path.stat().st_size > 10_000, "PNG seems too small — rendering may have failed"
