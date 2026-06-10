"""Card renderer — Jinja2 → Playwright for tables; matplotlib for series."""

from __future__ import annotations

import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from padres_analytics.detect.candidates import SeriesPayload, TablePayload
from padres_analytics.render.tokens import (
    BARLOW_BOLD_TTF,
    BARLOW_REGULAR_TTF,
    BARLOW_SEMIBOLD_TTF,
    BG_DEEP,
    BG_PANEL,
    DEVICE_SCALE,
    GOLD,
    GOLD_DIM,
    HIGHLIGHT_BG,
    HIGHLIGHT_EDGE,
    INTER_TTF,
    NEGATIVE,
    POSITIVE,
    ROW_ALT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    VIEWPORT_H,
    VIEWPORT_W,
    XFRIARS_LOGO_PNG,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import Browser

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)

_browser: Browser | None = None


class RenderError(RuntimeError):
    """Raised when card rendering fails. Never silently swallowed."""


@contextmanager
def _get_browser() -> Iterator[Browser]:
    """Lazily launch a Chromium browser, reused within a process.

    Yields:
        A Playwright Browser instance.

    Raises:
        RenderError: If Playwright/Chromium is not installed.
    """
    global _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RenderError(
            "playwright not installed. Run: uv run playwright install chromium"
        ) from exc

    if _browser is None:
        try:
            _pw = sync_playwright().start()
            _browser = _pw.chromium.launch()
        except Exception as exc:
            raise RenderError(
                f"Failed to launch Chromium. Run: uv run playwright install chromium\n{exc}"
            ) from exc

    yield _browser


def _render_table(
    payload: TablePayload,
    out_path: Path,
) -> None:
    """Render a TablePayload to a PNG via Playwright.

    Args:
        payload: The validated table payload.
        out_path: Destination PNG path.

    Raises:
        RenderError: On any Playwright or rendering failure.
    """
    template = _JINJA_ENV.get_template("table_card.html.j2")
    html = template.render(
        title=payload.title,
        subtitle=payload.subtitle,
        as_of=str(payload.as_of),
        columns=payload.columns,
        rows=payload.rows,
        highlight_row=payload.highlight_row,
        source=payload.source,
        is_projection=False,
        # Token values
        bg_deep=BG_DEEP,
        bg_panel=BG_PANEL,
        gold=GOLD,
        gold_dim=GOLD_DIM,
        text_primary=TEXT_PRIMARY,
        text_secondary=TEXT_SECONDARY,
        row_alt=ROW_ALT,
        highlight_bg=HIGHLIGHT_BG,
        highlight_edge=HIGHLIGHT_EDGE,
        positive=POSITIVE,
        negative=NEGATIVE,
        # Font paths (absolute, for file:// loading — no network at render time)
        inter_ttf=str(INTER_TTF),
        barlow_regular_ttf=str(BARLOW_REGULAR_TTF),
        barlow_semibold_ttf=str(BARLOW_SEMIBOLD_TTF),
        barlow_bold_ttf=str(BARLOW_BOLD_TTF),
        xfriars_logo=str(XFRIARS_LOGO_PNG),
    )

    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        tmp.write(html)
        tmp_path = tmp.name

    try:
        with _get_browser() as browser:
            page = browser.new_page(
                viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
                device_scale_factor=DEVICE_SCALE,
            )
            page.goto(f"file://{tmp_path}")
            page.screenshot(path=str(out_path), full_page=False)
            page.close()
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"Playwright rendering failed: {exc}") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def render(
    facts: TablePayload | SeriesPayload,
    out_dir: Path,
    candidate_id: str,
) -> Path:
    """Render a facts payload to ``out_dir/<candidate_id>.png``.

    Same payload → same pixels (deterministic within a Chromium/matplotlib version).

    Args:
        facts: Validated payload object.
        out_dir: Output directory. Created if absent.
        candidate_id: Used as the output filename stem.

    Returns:
        Path to the rendered PNG.

    Raises:
        RenderError: On any rendering failure. Never returns a partial file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{candidate_id}.png"

    if isinstance(facts, TablePayload):
        _render_table(facts, out_path)
    elif isinstance(facts, SeriesPayload):
        raise RenderError("SeriesPayload rendering not yet implemented (Phase 4)")
    else:
        raise RenderError(f"Unknown payload type: {type(facts)}")

    if not out_path.exists():
        raise RenderError(f"Renderer completed but output file missing: {out_path}")

    logger.info("Rendered card: %s", out_path)
    return out_path
