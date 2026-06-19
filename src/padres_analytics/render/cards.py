"""Card renderer — Jinja2 → Playwright for tables and role-typed datasets."""

from __future__ import annotations

import contextlib
import json
import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from padres_analytics.detect.candidates import ChartDataset, SeriesPayload, TablePayload
from padres_analytics.render.select import IMPLEMENTED_CARDS, select_card
from padres_analytics.render.tokens import (
    BARLOW_BOLD_TTF,
    BARLOW_REGULAR_TTF,
    BARLOW_SEMIBOLD_TTF,
    BEBAS_NEUE_TTF,
    BG_DEEP,
    BG_PANEL,
    BIG_SHOULDERS_TTF,
    BROWN,
    BROWN_DIM,
    CARD_VIEWPORT_H,
    CARD_VIEWPORT_W,
    D3_JS,
    DEVICE_SCALE,
    DM_SANS_TTF,
    GOLD,
    GOLD_DIM,
    HAIRLINE,
    HIGHLIGHT_BG,
    HIGHLIGHT_EDGE,
    HOT,
    INK,
    INK_SOFT,
    INTER_TTF,
    NEGATIVE,
    PAPER,
    PAPER_PANEL,
    PLOT_JS,
    POSITIVE,
    ROW_ALT,
    SPACE_GROTESK_TTF,
    TEXT_MUTED,
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


class RenderError(RuntimeError):
    """Raised when card rendering fails. Never silently swallowed."""


@contextmanager
def _get_browser() -> Iterator[Browser]:
    """Launch a short-lived Chromium browser for one render call.

    A fresh browser is created per call so sync_playwright stays on the calling
    thread — avoids greenlet conflicts when called from FastAPI's threadpool.

    Yields:
        A Playwright Browser instance.

    Raises:
        RenderError: If Playwright/Chromium is not installed or fails to launch.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RenderError(
            "playwright not installed. Run: uv run playwright install chromium"
        ) from exc

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                yield browser
            finally:
                browser.close()
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(
            f"Failed to launch Chromium. Run: uv run playwright install chromium\n{exc}"
        ) from exc


_VISUALS = ("table", "bars")


_SUFFIX_MULTIPLIERS = {"K": 1e3, "M": 1e6, "B": 1e9}


def _parse_numeric(raw: str) -> float:
    """Parse display-formatted numbers like '$4.25M', '47.1', '2,345' to float."""
    s = raw.strip().replace("$", "").replace("%", "").replace(",", "")
    if s and s[-1].upper() in _SUFFIX_MULTIPLIERS:
        try:
            return float(s[:-1]) * _SUFFIX_MULTIPLIERS[s[-1].upper()]
        except ValueError:
            pass
    try:
        return float(s)
    except ValueError:
        return 0.0


def _bar_rows(payload: TablePayload) -> list[dict[str, str | float]]:
    """Shape table rows into bar-chart rows, scaled to the max numeric final column."""
    from padres_analytics.render.mlb_assets import team_logo_path

    values = [_parse_numeric(str(r[-1])) for r in payload.rows]
    max_v = max(values) if values else 1.0
    if max_v <= 0:
        max_v = 1.0
    rows = []
    for r, v in zip(payload.rows, values, strict=True):
        sub = " · ".join(str(c) for c in r[2:-1]) if len(r) > 3 else ""
        label = str(r[1])
        logo = team_logo_path(label)
        rows.append(
            {
                "rank": str(r[0]),
                "label": label,
                "sub": sub,
                "value": str(r[-1]),
                "pct": round(max(v / max_v * 100, 3.0), 1),
                "logo": str(logo) if logo else "",
            }
        )
    return rows


def _token_kwargs() -> dict[str, str]:
    return {
        "bg_deep": BG_DEEP,
        "bg_panel": BG_PANEL,
        "paper": PAPER,
        "paper_panel": PAPER_PANEL,
        "ink": INK,
        "ink_soft": INK_SOFT,
        "brown": BROWN,
        "brown_dim": BROWN_DIM,
        "hairline": HAIRLINE,
        "hot": HOT,
        "text_muted": TEXT_MUTED,
        "gold": GOLD,
        "gold_dim": GOLD_DIM,
        "text_primary": TEXT_PRIMARY,
        "text_secondary": TEXT_SECONDARY,
        "row_alt": ROW_ALT,
        "highlight_bg": HIGHLIGHT_BG,
        "highlight_edge": HIGHLIGHT_EDGE,
        "positive": POSITIVE,
        "negative": NEGATIVE,
        "inter_ttf": str(INTER_TTF),
        "barlow_regular_ttf": str(BARLOW_REGULAR_TTF),
        "barlow_semibold_ttf": str(BARLOW_SEMIBOLD_TTF),
        "barlow_bold_ttf": str(BARLOW_BOLD_TTF),
        "xfriars_logo": str(XFRIARS_LOGO_PNG),
        "d3_js": str(D3_JS),
        "plot_js": str(PLOT_JS),
        "bebas_neue_ttf": str(BEBAS_NEUE_TTF),
        "dm_sans_ttf": str(DM_SANS_TTF),
        "big_shoulders_ttf": str(BIG_SHOULDERS_TTF),
        "space_grotesk_ttf": str(SPACE_GROTESK_TTF),
    }


def _render_table(
    payload: TablePayload,
    out_path: Path,
    visual: str = "table",
) -> None:
    """Render a TablePayload to a PNG via Playwright.

    Args:
        payload: The validated table payload.
        out_path: Destination PNG path.
        visual: "table" (default) or "bars".

    Raises:
        RenderError: On any Playwright or rendering failure, or unknown visual.
    """
    if visual not in _VISUALS:
        raise RenderError(f"Unknown visual {visual!r}. Available: {', '.join(_VISUALS)}")

    if visual == "bars":
        template = _JINJA_ENV.get_template("bar_card.html.j2")
        extra: dict[str, object] = {"bar_rows": _bar_rows(payload)}
    else:
        template = _JINJA_ENV.get_template("table_card.html.j2")
        extra = {
            "columns": payload.columns,
            "rows": payload.rows,
            "is_projection": False,
        }

    html = template.render(
        title=payload.title,
        subtitle=payload.subtitle,
        as_of=str(payload.as_of),
        highlight_row=payload.highlight_row,
        source=payload.source,
        **extra,
        **_token_kwargs(),
    )

    _html_to_png(html, out_path, VIEWPORT_W, VIEWPORT_H)


def _html_to_png(html: str, out_path: Path, viewport_w: int, viewport_h: int) -> None:
    """Screenshot a rendered HTML string to a PNG via headless Chromium.

    The HTML is written to a temp file and loaded over ``file://`` so vendored
    fonts/JS resolve. Templates using D3/Plot signal completion via the
    ``#chart-ready`` sentinel; CSS-only cards simply have no sentinel.

    Args:
        html: Fully rendered HTML.
        out_path: Destination PNG path.
        viewport_w: CSS-pixel viewport width (PNG width = this x DEVICE_SCALE).
        viewport_h: CSS-pixel viewport height.

    Raises:
        RenderError: On any Playwright failure.
    """
    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        tmp.write(html)
        tmp_path = tmp.name

    try:
        with _get_browser() as browser:
            page = browser.new_page(
                viewport={"width": viewport_w, "height": viewport_h},
                device_scale_factor=DEVICE_SCALE,
            )
            page.goto(f"file://{tmp_path}", wait_until="domcontentloaded")
            # D3/Plot-rendered templates signal completion via #chart-ready sentinel
            with contextlib.suppress(Exception):
                page.wait_for_selector("#chart-ready", timeout=3000)
            page.screenshot(path=str(out_path), full_page=False)
            page.close()
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"Playwright rendering failed: {exc}") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)


_CARD_TEMPLATES: dict[str, str] = {
    "hero": "card_hero.html.j2",
    "slider": "card_slider.html.j2",
    "scatter": "card_scatter.html.j2",
    "bar": "card_bar.html.j2",
}


_PLAYER_ID_KEYS = ("padre_player_id", "player_id", "subject_id")


def _resolve_headshot(dataset: ChartDataset) -> str | None:
    """Resolve an absolute headshot path from the dataset's audited player id.

    Looks through the dataset's flat ``facts`` for a known player-id key and
    downloads/caches the MLB headshot. Returns None when no id is present or the
    fetch fails — the templates degrade gracefully to no photo.

    Args:
        dataset: The dataset being rendered.

    Returns:
        Absolute filesystem path to the headshot PNG, or None.
    """
    from padres_analytics.render.mlb_assets import player_photo_path

    facts = dataset.facts or {}
    for key in _PLAYER_ID_KEYS:
        raw = facts.get(key)
        if raw is None:
            continue
        try:
            path = player_photo_path(int(raw))
        except (ValueError, TypeError):
            return None
        return str(path) if path else None
    return None


def _render_dataset(
    dataset: ChartDataset,
    out_path: Path,
    card: str | None = None,
) -> str:
    """Render a ChartDataset to a portrait PNG, picking the card from data shape.

    Args:
        dataset: The validated, role-typed dataset.
        out_path: Destination PNG path.
        card: Explicit card-type override; defaults to the selector's choice.

    Returns:
        The card type that was rendered.

    Raises:
        RenderError: If the chosen card has no template yet, or rendering fails.
    """
    chosen = card or select_card(dataset)
    if chosen not in IMPLEMENTED_CARDS or chosen not in _CARD_TEMPLATES:
        raise RenderError(
            f"Card type {chosen!r} not renderable yet. "
            f"Implemented: {', '.join(sorted(_CARD_TEMPLATES))}"
        )

    # Resolve the protagonist's headshot from the audited player id, if any.
    photo = _resolve_headshot(dataset)
    hero = dict(dataset.hero) if dataset.hero else None
    if hero is not None and photo and not hero.get("photo"):
        hero["photo"] = photo

    template = _JINJA_ENV.get_template(_CARD_TEMPLATES[chosen])
    html = template.render(
        title=dataset.title,
        subtitle=dataset.subtitle,
        as_of=str(dataset.as_of),
        source=dataset.source,
        hero=hero,
        photo=photo,
        framing=dataset.framing,
        population_label=dataset.population_label,
        n=dataset.n,
        dataset=json.dumps(dataset.model_dump(mode="json"), default=str),
        **_token_kwargs(),
    )

    _html_to_png(html, out_path, CARD_VIEWPORT_W, CARD_VIEWPORT_H)
    return chosen


def render(
    facts: TablePayload | SeriesPayload | ChartDataset,
    out_dir: Path,
    candidate_id: str,
    visual: str = "table",
    card: str | None = None,
) -> Path:
    """Render a facts payload to ``out_dir/<candidate_id>.png``.

    Same payload → same pixels (deterministic within a Chromium version).

    Args:
        facts: Validated payload object.
        out_dir: Output directory. Created if absent.
        candidate_id: Used as the output filename stem.
        visual: Legacy TablePayload card type — "table" or "bars".
        card: ChartDataset card-type override; defaults to the data-shape selector.

    Returns:
        Path to the rendered PNG.

    Raises:
        RenderError: On any rendering failure. Never returns a partial file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{candidate_id}.png"

    if isinstance(facts, ChartDataset):
        _render_dataset(facts, out_path, card=card)
    elif isinstance(facts, TablePayload):
        _render_table(facts, out_path, visual=visual)
    elif isinstance(facts, SeriesPayload):
        raise RenderError("SeriesPayload rendering not yet implemented (Phase 4)")
    else:
        raise RenderError(f"Unknown payload type: {type(facts)}")

    if not out_path.exists():
        raise RenderError(f"Renderer completed but output file missing: {out_path}")

    logger.info("Rendered card: %s", out_path)
    return out_path
