"""Desktop launcher for the project's scripts.

Tkinter only, so it adds no dependency. It runs each action as a subprocess and
streams the output into the log pane; it never talks to a provider itself.
The dashboard tab renders the local API's `/dashboard` payload with canvas-drawn
tables so individual cells (PnL, scores, change) can carry their own colours.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
API_URL = "http://127.0.0.1:8000"
REFRESH_SECONDS = 30
ALERT_REFRESH_SECONDS = 30
RADAR_REFRESH_SECONDS = 300
WATCHLIST_REFRESH_SECONDS = 300

# ---- palette -----------------------------------------------------------------
BG = "#0b0e14"
PANEL = "#12151d"
PANEL2 = "#161a24"
HOVER = "#1e2534"
BORDER = "#242b3b"
BORDER_SOFT = "#1a2030"
TEXT = "#e8eaf2"
MUTED = "#8b93a7"
FAINT = "#5d6478"
ACCENT = "#5b8cff"
ACCENT_DIM = "#33507e"
GREEN = "#3ecf8e"
GREEN_DIM = "#12331f"
RED = "#ff5c5c"
RED_DIM = "#3a1a1c"
AMBER = "#f2b544"
AMBER_DIM = "#38301a"
GOLD = "#f5c451"
GOLD_DIM = "#3a3116"
PURPLE = "#a78bfa"
PURPLE_DIM = "#2a2440"
SILVER = "#c9d1e0"
BRONZE = "#e0a370"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FONT = "Segoe UI"
FONT_BOLD = "Segoe UI Semibold"
FONT_MONO = "Consolas"


# ---- small helpers -----------------------------------------------------------

def blend(fg: str, bg: str, t: float) -> str:
    """Mix two hex colours; t=0 gives bg, t=1 gives fg. Used for pill fills."""
    a = [int(fg[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i : i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(x * t + y * (1 - t)) for x, y in zip(a, b)]
    return "#" + "".join(f"{max(0, min(255, v)):02x}" for v in mixed)


def money_compact(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if number < 0 else ""
    number = abs(number)
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if number >= div:
            scaled = number / div
            text = f"{scaled:.0f}" if scaled >= 100 else f"{scaled:.2f}".rstrip("0").rstrip(".")
            return f"{sign}${text}{suffix}"
    return f"{sign}${number:,.2f}"


def num_compact(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if number >= div:
            scaled = number / div
            text = f"{scaled:.0f}" if scaled >= 100 else f"{scaled:.2f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{number:,.0f}"


def price_text(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number == 0:
        return "$0"
    if number < 0.01:
        return f"${number:.8f}".rstrip("0").rstrip(".")
    if number < 1:
        return f"${number:.4f}".rstrip("0").rstrip(".")
    return f"${number:,.2f}"


def time_ago(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    delta = max((datetime.now(UTC) - value).total_seconds(), 0)
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"


def short_wallet(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 14:
        return text or "—"
    return f"{text[:7]}…{text[-5:]}"


def pnl_color(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return MUTED
    return GREEN if number > 0 else (RED if number < 0 else MUTED)


def score_style(score: Any) -> tuple[str, str]:
    """Colour and pill fill for a whale score."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return MUTED, PANEL2
    if s >= 85:
        return GOLD, GOLD_DIM
    if s >= 70:
        return GREEN, GREEN_DIM
    if s >= 50:
        return ACCENT, PURPLE_DIM
    if s >= 30:
        return AMBER, AMBER_DIM
    return MUTED, PANEL2


def alert_style(alert_type: str) -> tuple[str, str]:
    """Colour and pill fill for an alert type chip."""
    text = str(alert_type or "").lower()
    if "sell" in text or "sold" in text:
        return RED, RED_DIM
    if "buy" in text or "bought" in text or "smart" in text:
        return GREEN, GREEN_DIM
    if "whale" in text or "elite" in text or "gold" in text:
        return GOLD, GOLD_DIM
    if "emerging" in text:
        return PURPLE, PURPLE_DIM
    return ACCENT, ACCENT_DIM


def event_style(event: Any) -> tuple[str, str]:
    text = str(event or "").lower()
    if text == "buy":
        return GREEN, GREEN_DIM
    if text == "sell":
        return RED, RED_DIM
    if text == "received":
        return AMBER, AMBER_DIM
    return MUTED, PANEL2


ALERT_ICONS = {
    "NEW_SMART_WHALE": "🐋",
    "EMERGING_WHALE": "🚀",
    "GMGN_TRENDING": "🔥",
    "GMGN_NEW_TOKEN": "🆕",
    "GMGN_NEAR_GRADUATION": "🎓",
    "GMGN_HOT_SEARCH": "🔍",
    "FOMO_BUY": "💸",
    "FOMO_SELL": "💰",
}

CHAIN_ICONS = {"sol": "◎", "solana": "◎", "bsc": "◆", "binance-smart-chain": "◆",
               "base": "▲", "eth": "Ξ", "ethereum": "Ξ"}


def alert_icon(alert_type: Any) -> str:
    """One leading emoji per alert type for fast visual scanning."""
    return ALERT_ICONS.get(str(alert_type or "").upper(), "•")


def chain_icon(chain: Any) -> str:
    return CHAIN_ICONS.get(str(chain or "").lower(), "◇")


def trend_arrow(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "·"
    if number > 0:
        return "▲"
    if number < 0:
        return "▼"
    return "—"


def gmgn_wallet_url(wallet: Any) -> str | None:
    """Wallet explorer link; the address shape picks the chain (EVM vs Solana)."""
    address = str(wallet or "").strip()
    if not address:
        return None
    chain = "base" if address.lower().startswith("0x") else "sol"
    return f"https://gmgn.ai/{chain}/wallet/{address}"


def gmgn_token_url(chain: Any, address: Any) -> str | None:
    address = str(address or "").strip()
    if not address:
        return None
    chain_name = str(chain or "sol").lower()
    if chain_name in ("solana",):
        chain_name = "sol"
    elif chain_name in ("binance-smart-chain",):
        chain_name = "bsc"
    elif chain_name in ("ethereum",):
        chain_name = "eth"
    return f"https://gmgn.ai/{chain_name}/token/{address}"


class Tooltip:
    """A delayed hover tooltip. Works for widgets and for canvas hit regions."""

    def __init__(self, widget: tk.Misc) -> None:
        self.widget = widget
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None

    def schedule(self, text: str) -> None:
        self.cancel()
        if not text:
            return
        x = self.widget.winfo_pointerx() + 16
        y = self.widget.winfo_pointery() + 20
        self._after = self.widget.after(600, lambda: self._show(text, x, y))

    def cancel(self) -> None:
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _show(self, text: str, x: int, y: int) -> None:
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tw, text=text, bg=BORDER, fg=TEXT, font=(FONT, 8),
            padx=8, pady=5, justify="left", wraplength=420,
        ).pack()


class DetailPopup:
    """A rich hover card for a row: token stats, contract, and open links.

    Follows the same 600 ms hover-delay pattern as `Tooltip`, but renders a
    multi-line panel instead of a single text strip.
    """

    def __init__(self, widget: tk.Misc, builder: Callable[[dict], list[tuple[str, str, str]]]) -> None:
        """`builder(row)` returns (title, value, colour) triples per line."""
        self.widget = widget
        self.builder = builder
        self._after: str | None = None
        self._popup: tk.Toplevel | None = None

    def schedule(self, row: dict) -> None:
        self.cancel()
        try:
            lines = self.builder(row)
        except Exception:
            lines = []
        if not lines:
            return
        x = self.widget.winfo_pointerx() + 18
        y = self.widget.winfo_pointery() + 22
        self._after = self.widget.after(600, lambda: self._show(lines, x, y))

    def cancel(self) -> None:
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None

    def _show(self, lines: list[tuple[str, str, str]], x: int, y: int) -> None:
        self._popup = popup = tk.Toplevel(self.widget)
        popup.wm_overrideredirect(True)
        popup.wm_geometry(f"+{x}+{y}")
        panel = tk.Frame(popup, bg=BORDER, padx=1, pady=1)
        panel.pack()
        inner = tk.Frame(panel, bg=PANEL)
        inner.pack()
        for index, (title, value, colour) in enumerate(lines):
            row = tk.Frame(inner, bg=PANEL)
            row.pack(fill="x", padx=10, pady=(6 if index == 0 else 2, 2))
            tk.Label(row, text=title, bg=PANEL, fg=MUTED,
                     font=(FONT, 8), anchor="w", width=13).pack(side="left")
            tk.Label(row, text=value, bg=PANEL, fg=colour,
                     font=(FONT_BOLD, 8), anchor="w", justify="left",
                     wraplength=300).pack(side="left", fill="x", expand=True)
        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(4, 0))
        tk.Label(inner, text="  click a column to act · ✕ anywhere to dismiss  ",
                 bg=PANEL, fg=FAINT, font=(FONT, 7)).pack(pady=(2, 6))


# ---- canvas table ------------------------------------------------------------

@dataclass
class Column:
    """One column of a DataTable.

    `text` renders the cell string (defaults to `row[key]`), `color` picks the
    text colour, `pill` draws a filled rounded chip instead of plain text, and
    `sort` provides the numeric sort value (defaults to the raw cell value).
    """

    key: str
    title: str
    width: int
    align: str = "w"
    text: Callable[[dict], Any] | None = None
    color: Callable[[dict], str | None] | None = None
    pill: Callable[[dict], tuple[str, str, str]] | None = None
    bold: bool = False
    size: int = 9
    mono: bool = False
    sort: Callable[[dict], Any] | None = None
    click: bool = False
    tooltip: Callable[[dict], str] | None = None
    stretch: bool = False


class DataTable(tk.Frame):
    """A canvas-rendered table.

    The standard ttk.Treeview can only colour whole rows; a real terminal needs
    green PnL next to red next to a gold score pill. This draws each cell as its
    own canvas item, supports per-column sort, hover highlight, per-cell
    tooltips, click callbacks, and synced horizontal scrolling of the header.
    """

    HEADER_H = 34

    def __init__(
        self, master: tk.Misc,
        columns: list[Column],
        on_click: Callable[[dict, Column], None] | None = None,
        on_double: Callable[[dict, Column], None] | None = None,
        empty_text: str = "No data yet",
        row_height: int = 36,
    ) -> None:
        super().__init__(master, bg=BG)
        self.columns = columns
        self.rows: list[dict] = []
        self.filtered: list[dict] = []
        self.on_click = on_click
        self.on_double = on_double
        self.empty_text = empty_text
        self.row_height = row_height
        self.sort_key: str | None = None
        self.sort_reverse = True
        self.hover_index: int | None = None
        self._row_rects: list[int] = []
        self.tooltip = Tooltip(self.body if hasattr(self, "body") else self)

        self.header = tk.Canvas(self, bg="#0f1219", highlightthickness=0, bd=0, height=self.HEADER_H)
        self.body = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.ybar = ttk.Scrollbar(self, orient="vertical", command=self.body.yview)
        self.xbar = ttk.Scrollbar(self, orient="horizontal", command=self._xview)
        self.body.configure(yscrollcommand=self.ybar.set, xscrollcommand=self._xsync)
        self.header.configure(xscrollcommand=lambda first, last: None)

        self.header.grid(row=0, column=0, sticky="ew")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.ybar.grid(row=1, column=1, sticky="ns")
        self.xbar.grid(row=2, column=0, sticky="ew")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header.bind("<Button-1>", self._on_header_click)
        self.body.bind("<Motion>", self._on_motion)
        self.body.bind("<Leave>", self._on_leave)
        self.body.bind("<Button-1>", self._on_click)
        self.body.bind("<Double-Button-1>", self._on_double)
        self.body.bind("<MouseWheel>", self._on_wheel)
        self.body.bind("<Configure>", lambda _e: self.redraw())

    # ---- public API ----

    def set_rows(self, rows: list[dict]) -> None:
        self.rows = rows
        self._apply_sort()
        self.redraw()

    def set_empty_text(self, text: str) -> None:
        self.empty_text = text

    # ---- layout helpers ----

    def _total_width(self) -> int:
        return sum(col.width for col in self.columns)

    def _col_x(self) -> list[int]:
        xs, x = [], 0
        for col in self.columns:
            xs.append(x)
            x += col.width
        return xs

    def _xsync(self, first: float, last: float) -> None:
        self.xbar.set(first, last)
        self.header.xview_moveto(first)

    def _xview(self, *args) -> None:
        self.body.xview(*args)

    def _stretch_widths(self) -> list[int]:
        """Give the flagged column any spare horizontal space."""
        widths = [col.width for col in self.columns]
        extra = max(self.body.winfo_width() - self._total_width(), 0)
        for index, col in enumerate(self.columns):
            if col.stretch:
                widths[index] += extra
                break
        return widths

    # ---- sorting ----

    def _apply_sort(self) -> None:
        column = next((c for c in self.columns if c.key == self.sort_key), None)
        if column is None:
            self.filtered = list(self.rows)
            return
        def value(row: dict):
            getter = column.sort or column.text
            raw = getter(row) if getter else row.get(column.key)
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
        ranked = [(value(row), index, row) for index, row in enumerate(self.rows)]
        present = sorted((item for item in ranked if item[0] is not None),
                         key=lambda item: item[0], reverse=self.sort_reverse)
        missing = [item for item in ranked if item[0] is None]
        self.filtered = [row for _v, _i, row in present + missing]

    def _on_header_click(self, event) -> None:
        widths = self._stretch_widths()
        x, total = 0, self.header.canvasx(event.x)
        for col, width in zip(self.columns, widths):
            if x <= total < x + width:
                if self.sort_key == col.key:
                    self.sort_reverse = not self.sort_reverse
                else:
                    self.sort_key, self.sort_reverse = col.key, True
                self._apply_sort()
                self.redraw()
                return
            x += width

    # ---- drawing ----

    def redraw(self) -> None:
        header, body = self.header, self.body
        header.delete("all")
        body.delete("all")
        self._row_rects = []
        widths = self._stretch_widths()
        xs = []
        x = 0
        for width in widths:
            xs.append(x)
            x += width
        total = x
        header.configure(scrollregion=(0, 0, total, self.HEADER_H))

        # header row
        header.create_rectangle(0, 0, total, self.HEADER_H, fill="#0f1219", outline="")
        header.create_line(0, self.HEADER_H - 1, total, self.HEADER_H - 1, fill=BORDER)
        for col, width, x0 in zip(self.columns, widths, xs):
            title = col.title.upper()
            if col.key == self.sort_key:
                title = f"{'▼' if self.sort_reverse else '▲'} {title}"
                colour = ACCENT
            elif col.click or col.key in ("profile", "gmgn", "open", "address"):
                # Interactive columns get a tinted header so users can see at a
                # glance where clicking does something.
                colour = "#9aa7c4"
            else:
                colour = MUTED
            anchor = {"w": "w", "e": "e", "center": "center"}[col.align]
            tx = {"w": x0 + 10, "e": x0 + width - 10, "center": x0 + width / 2}[col.align]
            header.create_text(tx, self.HEADER_H / 2, text=title, anchor=anchor,
                               fill=colour, font=(FONT_BOLD, 9))

        rows = self.filtered
        if not rows:
            body.create_text(
                max(body.winfo_width() // 2, 160), max(body.winfo_height() // 2, 60),
                text=f"◇  {self.empty_text}", fill="#6b7690", font=(FONT, 11),
            )
            self._update_xbar(total)
            return

        for index, row in enumerate(rows):
            y0 = index * self.row_height
            y1 = y0 + self.row_height
            if index % 2 == 0:
                fill = PANEL2
            else:
                fill = "#11141d"
            self._row_rects.append(body.create_rectangle(0, y0, total, y1, fill=fill, outline=""))
            for col, width, x0 in zip(self.columns, widths, xs):
                self._draw_cell(body, row, col, x0, width, y0, y1)
        body.configure(scrollregion=(0, 0, total, len(rows) * self.row_height))
        self._update_xbar(total)
        if self.hover_index is not None and self.hover_index < len(rows):
            self._paint_hover(self.hover_index)

    def _draw_cell(self, body: tk.Canvas, row: dict, col: Column, x0: int, width: int, y0: int, y1: int) -> None:
        cy = (y0 + y1) / 2
        anchor = {"w": "w", "e": "e", "center": "center"}[col.align]
        tx = {"w": x0 + 10, "e": x0 + width - 10, "center": x0 + width / 2}[col.align]
        if col.pill:
            pill_value = col.pill(row)
            text, bg, fg = (list(pill_value) + ["", PANEL2, MUTED])[:3]
            if text:
                tw = max(len(text) * 7 + 20, 46)
                if col.align == "center":
                    px0 = x0 + (width - tw) / 2
                elif col.align == "e":
                    px0 = x0 + width - 10 - tw
                else:
                    px0 = x0 + 10
                body.create_polygon(
                    px0 + 4, cy - 9, px0 + tw - 4, cy - 9, px0 + tw, cy - 5, px0 + tw, cy + 5,
                    px0 + tw - 4, cy + 9, px0 + 4, cy + 9, px0, cy + 5, px0, cy - 5,
                    smooth=True, fill=bg, outline="",
                )
                body.create_text(px0 + tw / 2, cy, text=text, fill=fg, font=(FONT_BOLD, 9))
            return
        getter = col.text
        raw = getter(row) if getter else row.get(col.key)
        text = "—" if raw is None else str(raw)
        colour = col.color(row) if col.color else TEXT
        font = (FONT_MONO, col.size) if col.mono else (
            (FONT_BOLD, col.size + 1) if col.bold else (FONT, col.size + 1))
        body.create_text(tx, cy, text=text, anchor=anchor, fill=colour, font=font)

    def _update_xbar(self, total: int) -> None:
        if total > self.body.winfo_width() + 2:
            self.xbar.grid()
        else:
            self.xbar.grid_remove()

    # ---- interaction ----

    def _hit(self, event) -> tuple[int, Column] | tuple[None, None]:
        widths = self._stretch_widths()
        x = self.body.canvasx(event.x)
        y = self.body.canvasy(event.y)
        index = int(y // self.row_height)
        if index < 0 or index >= len(self.filtered):
            return None, None
        offset = 0
        for col, width in zip(self.columns, widths):
            if offset <= x < offset + width:
                return index, col
            offset += width
        return None, None

    def _on_motion(self, event) -> None:
        index, col = self._hit(event)
        if index != self.hover_index:
            self._paint_hover(index)
        if col is not None and (col.click or self.on_click or self.on_double):
            self.body.configure(cursor="hand2" if (col and (col.click or self.on_click)) else "")
        else:
            self.body.configure(cursor="")
        detail = getattr(self, "hover_detail", None)
        if detail is not None and col is not None and not col.tooltip:
            detail.schedule(self.filtered[index])
        elif detail is not None and index is None:
            detail.cancel()
        if col is not None and col.tooltip:
            self.tooltip.schedule(col.tooltip(self.filtered[index]))
        elif col is not None and col.sort:
            self.tooltip.schedule("Click to sort by this column")
        else:
            self.tooltip.cancel()

    def _on_leave(self, _event=None) -> None:
        self._paint_hover(None)
        self.tooltip.cancel()
        detail = getattr(self, "hover_detail", None)
        if detail is not None:
            detail.cancel()

    def _paint_hover(self, index: int | None) -> None:
        previous = self.hover_index
        self.hover_index = index
        for position, rect in enumerate(self._row_rects):
            if position in (previous, index):
                base = PANEL2 if position % 2 == 0 else "#11141d"
                self.body.itemconfigure(rect, fill=HOVER if position == index else base)

    def _on_click(self, event) -> None:
        index, col = self._hit(event)
        if index is None or col is None:
            return
        row = self.filtered[index]
        if col.click or self.on_click:
            self.on_click(row, col)

    def _on_double(self, event) -> None:
        index, col = self._hit(event)
        if index is None or col is None:
            return
        if self.on_double:
            self.on_double(self.filtered[index], col)

    def _on_wheel(self, event) -> None:
        self.body.yview_scroll(-1 * (event.delta // 120), "units")


# ---- launcher skeleton data --------------------------------------------------

def ensure_tcl() -> None:
    """Point Tcl at the base install.

    A Windows venv does not copy the interpreter's `tcl` directory, and Tcl's own
    search only walks up from the executable, so `Tk()` inside `.venv` fails with
    "Can't find a usable init.tcl" unless these are set.
    """
    if os.name != "nt" or sys.prefix == sys.base_prefix:
        return
    base = Path(sys.base_prefix) / "tcl"
    if not base.is_dir():
        return
    for variable, prefix, marker in (("TCL_LIBRARY", "tcl", "init.tcl"), ("TK_LIBRARY", "tk", "tk.tcl")):
        if os.environ.get(variable):
            continue
        found = sorted(p for p in base.glob(f"{prefix}*") if p.is_dir() and (p / marker).exists())
        if found:
            os.environ[variable] = str(found[-1])


def sharpen() -> None:
    """Opt into per-monitor DPI so the window is not a blurry upscale."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # older Windows, or already set by the host process
        pass


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    hint: str
    argv: tuple[str, ...] = ()
    builtin: str = ""
    long_running: bool = False
    primary: bool = False
    glyph: str = ""


@dataclass(frozen=True)
class Section:
    title: str
    actions: list[Action] = field(default_factory=list)


SECTIONS = [
    Section("Research", [
        Action("research", "Start research",
               "Scan every public widget in one pass — the leaderboard for 24h, 7d, 30d and "
               "all time, the live alert stream, and the trade theses — then write it all to "
               "a readable text report under reports\\.",
               argv=("scripts/research.py",), primary=True, glyph="✦"),
        Action("report", "Open the latest report",
               "Open the newest file in reports\\ in your default text editor.",
               builtin="open_report", glyph="▤"),
        Action("reports", "Open the reports folder",
               "Browse every report collected so far.",
               builtin="open_reports", glyph="▥"),
    ]),
    Section("Collect", [
        Action("collect", "Run one collection cycle",
               "Crawl the public page once and store traders, snapshots, and scores.",
               argv=("scripts/collect_once.py",), glyph="↓"),
        Action("worker", "Start the continuous worker",
               "Collect on a loop, score, detect emerging traders, and raise alerts.",
               argv=("scripts/run_worker.py",), long_running=True, glyph="↻"),
        Action("api", "Start the REST API",
               f"Serve the FastAPI app on {API_URL}. Runs until you press Stop.",
               argv=("-m", "uvicorn", "app.api.main:app", "--host", "127.0.0.1", "--port", "8000"),
               long_running=True, glyph="◈"),
    ]),
    Section("Open", [
        Action("docs", "API docs", f"Open {API_URL}/docs in your browser.", builtin="open_docs", glyph="↗"),
        Action("board", "Leaderboard endpoint", f"Open {API_URL}/leaderboard.", builtin="open_board", glyph="↗"),
        Action("status", "Provider status endpoint", f"Open {API_URL}/provider/status.", builtin="open_status", glyph="↗"),
        Action("env", "Edit .env", "Open the configuration file in your default editor.", builtin="open_env", glyph="✎"),
        Action("folder", "Project folder", "Open the project directory in Explorer.", builtin="open_folder", glyph="⌂"),
    ]),
    Section("Database", [
        Action("seed", "Seed mock data",
               "Fill the database with fictional traders. Needs no network or browser.",
               argv=("scripts/seed_mock_data.py",), glyph="⊕"),
        Action("scores", "Recalculate scores",
               "Rebuild Smart Whale Scores from the stored snapshots and trades.",
               argv=("scripts/recalculate_scores.py",), glyph="∑"),
        Action("migrate", "Run migrations",
               "alembic upgrade head. Needed before a PostgreSQL deployment.",
               argv=("-m", "alembic", "upgrade", "head"), glyph="↑"),
    ]),
    Section("GMGN", [
        Action("gmgnreport", "GMGN report",
               "Fetch trending tokens, newly created/graduating tokens, hot searches, "
               "and whale wallet intelligence from gmgn-cli into a text report under reports\\.",
               argv=("scripts/gmgn_report.py",), glyph="◆"),
        Action("gmgncheck", "Check GMGN setup",
               "Verify the gmgn-cli tool (installs it through npm if missing) and whether "
               "GMGN_API_KEY is configured.",
               argv=("scripts/check_gmgn.py",), glyph="✓"),
    ]),
    Section("Site discovery", [
        Action("crawlsite", "Crawl the public site",
               "Record public HTML and bundle metadata into docs/fomo_crawl.json.",
               argv=("scripts/crawl_fomo.py",), glyph="◎"),
        Action("discover", "Record public requests",
               "Log the public requests fomo.family makes into docs/discovery.json.",
               argv=("scripts/discover_fomo.py",), glyph="◎"),
    ]),
    Section("Checks", [
        Action("check", "Provider health check",
               "Render the configured source once and report its capabilities.",
               argv=("scripts/check_provider.py",), glyph="✓"),
        Action("test", "Run tests", "pytest across the unit and integration suites.",
               argv=("-m", "pytest", "-q"), glyph="✓"),
        Action("lint", "Run the linter", "ruff check across the project.",
               argv=("-m", "ruff", "check", "."), glyph="✓"),
    ]),
    Section("Maintenance", [
        Action("deps", "Reinstall dependencies",
               "Repair the Python packages in the current environment.",
               argv=("-m", "pip", "install", "-e", ".[dev]"), glyph="⚙"),
        Action("chromium", "Reinstall Chromium",
               "Re-download the browser that crawl mode drives.",
               argv=("-m", "playwright", "install", "chromium"), glyph="⚙"),
    ]),
]


def read_provider() -> str:
    """Read DATA_PROVIDER straight from .env so the launcher stays dependency-free."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line.lower().startswith("data_provider"):
                _, _, value = line.partition("=")
                if value.strip():
                    return value.strip()
    return "crawl (default)"


class ScrollArea(tk.Frame):
    """A vertically scrolling container that follows the wheel while hovered."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=BG)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self._window, width=event.width))
        self.canvas.bind("<Enter>", lambda _: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda _: self.canvas.unbind_all("<MouseWheel>"))

    def _on_body(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_wheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(-1 * (event.delta // 120), "units")


# ---- the launcher ------------------------------------------------------------

class Launcher(tk.Tk):
    def __init__(self, demo: bool = False) -> None:
        ensure_tcl()
        super().__init__()
        self.demo = demo
        self.title("FOMO Whale Intelligence — Live Dashboard")
        self.configure(bg=BG)
        self.geometry("1360x840")
        self.minsize(1120, 680)
        # Start maximized on normal launches so wide tables (radar, tokens)
        # get the full width; demo/screenshot runs keep the fixed size.
        if not demo:
            self.state("zoomed")
        self.process: subprocess.Popen | None = None
        self.api_process: subprocess.Popen | None = None
        self.worker_process: subprocess.Popen | None = None
        self.stopping = False
        self.running_key: str | None = None
        self.output: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self.dashboard_output: queue.Queue[dict | None] = queue.Queue()
        self.dashboard_fetching = False
        self.alerts_fetching = False
        self.next_alert_refresh: float | None = None
        self.radar_fetching = False
        self.next_radar_refresh: float | None = None
        self.dashboard_rows: list[dict] = []
        self.last_update: float | None = None
        self.next_refresh: float | None = None
        self.api_start_attempts = 0
        self._pulse_state = False
        self.cards: list[tuple[Action, tk.Frame, list[tk.Widget]]] = []

        self._style()
        self._header()
        self._body()
        self._status_bar()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._drain)
        self.after(1000, self._tick)
        if self.demo:
            self._load_demo()
        else:
            self.log(f"Ready. Provider: {read_provider()}   ·   Project: {ROOT}\n", "muted")
            self.after(400, self._start_dashboard_api)

    # ---------- style ----------

    def _style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Vertical.TScrollbar", background=BORDER, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED, relief="flat")
        style.configure("Horizontal.TScrollbar", background=BORDER, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED, relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", ACCENT)])
        style.map("Horizontal.TScrollbar", background=[("active", ACCENT)])

    # ---------- header ----------

    def _header(self) -> None:
        bar = tk.Frame(self, bg=PANEL, height=62)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)
        tk.Frame(self, bg=BORDER, height=1).pack(side="top", fill="x")

        left = tk.Frame(bar, bg=PANEL)
        left.pack(side="left", padx=18, pady=10)
        brand = tk.Frame(left, bg=PANEL)
        brand.pack(anchor="w")
        tk.Label(brand, text="◆", bg=PANEL, fg=ACCENT, font=(FONT_BOLD, 17)).pack(side="left")
        tk.Label(brand, text="  FOMO WHALE INTELLIGENCE", bg=PANEL, fg=TEXT,
                 font=(FONT_BOLD, 13)).pack(side="left")
        tk.Label(left, text=f"provider: {read_provider()}   ·   python {sys.version.split()[0]}",
                 bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=2)

        right = tk.Frame(bar, bg=PANEL)
        right.pack(side="right", padx=16)

        # live status pill
        pill = tk.Frame(right, bg=GREEN_DIM, cursor="hand2")
        pill.pack(side="right", padx=(0, 12), pady=4, ipadx=2)
        self.pill_dot = tk.Label(pill, text="●", bg=GREEN_DIM, fg=GREEN, font=(FONT, 9))
        self.pill_dot.pack(side="left", padx=(9, 4), pady=5)
        self.pill_text = tk.Label(pill, text="STARTING", bg=GREEN_DIM, fg=GREEN,
                                  font=(FONT_BOLD, 8))
        self.pill_text.pack(side="left", padx=(0, 9), pady=5)

        self.refresh_button = self._button(right, "⟳ Refresh", self.refresh_dashboard, PANEL2)
        self.refresh_button.pack(side="right", padx=4)
        self.worker_button = self._button(right, "▸ Start worker", self._toggle_worker, PANEL2)
        self.worker_button.pack(side="right", padx=4)
        self.stop_button = self._button(right, "■ Stop", self.stop, RED_DIM)
        self.stop_button.pack(side="right", padx=4)
        self.stop_button.configure(state="disabled")
        self._button(right, "API settings", self.open_api_settings, PANEL2).pack(side="right", padx=4)

    def _button(self, master: tk.Misc, text: str, command, colour: str) -> tk.Button:
        return tk.Button(
            master, text=text, command=command, bg=colour, fg=TEXT,
            activebackground=HOVER, activeforeground=TEXT, relief="flat",
            font=(FONT, 9), padx=12, pady=5, cursor="hand2",
            borderwidth=0, highlightthickness=0, disabledforeground=FAINT,
        )

    # ---------- body: sidebar + main ----------

    def _body(self) -> None:
        body = tk.Frame(self, bg=BG)
        body.pack(side="top", fill="both", expand=True)

        sidebar = tk.Frame(body, bg=BG, width=276)
        sidebar.pack(side="left", fill="both")
        sidebar.pack_propagate(False)
        scroller = ScrollArea(sidebar)
        scroller.pack(fill="both", expand=True, padx=(12, 4), pady=10)
        for section in SECTIONS:
            self._section(scroller.body, section)

        main = tk.Frame(body, bg=BG)
        main.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=10)
        self._kpi_row(main)
        self._tab_area(main)

    # ---------- sidebar ----------

    def _section(self, master: tk.Misc, section: Section) -> None:
        tk.Label(master, text=section.title.upper(), bg=BG, fg=FAINT,
                 font=(FONT_BOLD, 9)).pack(anchor="w", pady=(14, 5), padx=4)
        for action in section.actions:
            self._card(master, action)

    def _card(self, master: tk.Misc, action: Action) -> None:
        primary = action.primary
        card = tk.Frame(master, bg=PANEL, cursor="hand2",
                        highlightbackground=ACCENT if primary else BORDER_SOFT,
                        highlightthickness=1 if not primary else 1)
        card.pack(fill="x", pady=2)
        inner = tk.Frame(card, bg=PANEL)
        inner.pack(fill="x", padx=9, pady=6 if not primary else 8)
        glyph = tk.Label(inner, text=action.glyph, bg=PANEL,
                         fg=ACCENT if primary else MUTED, font=(FONT, 10), width=2)
        glyph.pack(side="left")
        title = tk.Label(inner, text=action.label, bg=PANEL,
                         fg=TEXT if not primary else ACCENT,
                         font=(FONT_BOLD, 10), anchor="w", justify="left")
        title.pack(side="left", padx=(6, 0))
        if action.long_running:
            tk.Label(inner, text="∞", bg=PANEL, fg=FAINT, font=(FONT, 10)).pack(side="left", padx=(5, 0))
        hint = None
        if primary:
            hint = tk.Label(card, text=action.hint, bg=PANEL, fg=MUTED, font=(FONT, 9),
                            anchor="w", justify="left", wraplength=236)
            hint.pack(fill="x", padx=10, pady=(0, 8))
        widgets = [card, inner, glyph, title] + ([hint] if hint else [])
        for widget in widgets:
            widget.bind("<Button-1>", lambda _event, item=action: self.launch(item))
        self._hover(card, widgets)
        tooltip = Tooltip(card)
        hint_target = [card, inner, glyph, title]
        for widget in hint_target:
            widget.bind("<Enter>", lambda _e, text=action.hint: tooltip.schedule(text))
            widget.bind("<Leave>", lambda _e: tooltip.cancel())
        self.cards.append((action, card, widgets))

    def _hover(self, card: tk.Frame, widgets: list[tk.Widget]) -> None:
        def enter(_e) -> None:
            if self.running_key is None:
                for widget in widgets:
                    widget.configure(bg=HOVER)
        def leave(_e) -> None:
            if self.running_key is None:
                for widget in widgets:
                    widget.configure(bg=PANEL)
        for widget in widgets:
            widget.bind("<Enter>", enter, add="+")
            widget.bind("<Leave>", leave, add="+")

    # ---------- KPI row ----------

    def _kpi_row(self, main: tk.Frame) -> None:
        row = tk.Frame(main, bg=BG)
        row.pack(fill="x", pady=(0, 10))
        self.kpi_values: dict[str, tk.Label] = {}
        cards = [
            ("volume", "LEADERBOARD VOL", "▤", ACCENT, PURPLE_DIM, "Sum of the latest volume of every tracked trader"),
            ("trades", "TRADE VOL 24H", "◆", PURPLE, PURPLE_DIM, "USD size of classified buy/sell events in the last 24h"),
            ("whale", "TOP WHALE", "★", GOLD, GOLD_DIM, "Highest live whale score right now"),
            ("traders", "TRADERS", "◉", GREEN, GREEN_DIM, "Active traders with a recent snapshot"),
            ("hot", "HOT TOKEN 2H", "▲", AMBER, AMBER_DIM, "Most frequent token in the last 2h of trade events"),
        ]
        row.grid_columnconfigure(tuple(range(len(cards))), weight=1, uniform="kpi")
        for index, (key, title, glyph, colour, dim, tip) in enumerate(cards):
            card = tk.Frame(row, bg=PANEL, highlightbackground=BORDER_SOFT, highlightthickness=1)
            card.grid(row=0, column=index, sticky="nsew", padx=3)
            head = tk.Frame(card, bg=PANEL)
            head.pack(fill="x", padx=10, pady=(8, 0))
            glyph_label = tk.Label(head, text=glyph, bg=dim, fg=colour, font=(FONT_BOLD, 11), width=3, pady=1)
            glyph_label.pack(side="left")
            title_label = tk.Label(head, text=title, bg=PANEL, fg=MUTED, font=(FONT_BOLD, 8))
            title_label.pack(side="left", padx=(7, 0))
            value = tk.Label(card, text="—", bg=PANEL, fg=TEXT,
                             font=(FONT_BOLD, 15), anchor="w")
            value.pack(anchor="w", padx=11, pady=(4, 9))
            self.kpi_values[key] = value
            tooltip = Tooltip(card)
            def kpi_enter(e, text=tip, tip_obj=tooltip, c=card, h=head, v=value, tl=title_label):
                tip_obj.schedule(text)
                c.configure(highlightbackground=BORDER, bg=HOVER)
                h.configure(bg=HOVER)
                v.configure(bg=HOVER)
                tl.configure(bg=HOVER, fg=TEXT)
            def kpi_leave(e, tip_obj=tooltip, c=card, h=head, v=value, tl=title_label):
                tip_obj.cancel()
                c.configure(highlightbackground=BORDER_SOFT, bg=PANEL)
                h.configure(bg=PANEL)
                v.configure(bg=PANEL)
                tl.configure(bg=PANEL, fg=MUTED)
            for widget in (card, head, value, glyph_label, title_label):
                widget.bind("<Enter>", kpi_enter)
                widget.bind("<Leave>", kpi_leave)

    # ---------- tabs ----------

    def _tab_area(self, main: tk.Frame) -> None:
        bar = tk.Frame(main, bg=BG)
        bar.pack(fill="x", pady=(0, 6))
        self.tab_buttons: dict[str, tk.Label] = {}
        self.tab_frames: dict[str, tk.Frame] = {}

        def on_enter(e, key):
            if key != getattr(self, "active_tab", None):
                e.widget.configure(bg=HOVER, fg=TEXT)

        def on_leave(e, key):
            if key != getattr(self, "active_tab", None):
                e.widget.configure(bg=BG, fg=MUTED)

        for key, label in (
            ("leaderboard", "Leaderboard"),
            ("movers", "Movers"),
            ("flow", "Whale Flow"),
            ("alerts", "Alerts"),
            ("tokens", "Token Rankings"),
            ("radar", "GMGN Radar"),
            ("logs", "System Logs"),
        ):
            self.tab_frames[key] = tk.Frame(main, bg=BG)
            button = tk.Label(bar, text=label, bg=BG, fg=MUTED, font=(FONT_BOLD, 10),
                              padx=14, pady=8, cursor="hand2")
            button.pack(side="left", padx=2)
            button.bind("<Button-1>", lambda _e, k=key: self._show_tab(k))
            button.bind("<Enter>", lambda e, k=key: on_enter(e, k), add="+")
            button.bind("<Leave>", lambda e, k=key: on_leave(e, k), add="+")
            self.tab_buttons[key] = button
        hint = tk.Label(bar, text="Ctrl+1…7 switch tabs", bg=BG, fg=FAINT, font=(FONT, 8))
        hint.pack(side="right", padx=6)
        tk.Frame(main, bg=BORDER_SOFT, height=1).pack(fill="x", pady=(0, 0))

        # "Whale Flow" and "Token Move" are the same dataset (volume changes are
        # derived from the flow window) — one tab, two stacked sections, so the
        # tab bar stays readable.
        self.tab_frames["token_move"] = self.tab_frames["flow"]

        self._build_leaderboard(self.tab_frames["leaderboard"])
        self._build_movers(self.tab_frames["movers"])
        self._build_flow(self.tab_frames["flow"])
        self._build_alerts(self.tab_frames["alerts"])
        self._build_tokens(self.tab_frames["tokens"])
        self._build_radar(self.tab_frames["radar"])
        self._build_logs(self.tab_frames["logs"])
        self._show_tab("leaderboard")

    def _show_tab(self, key: str) -> None:
        self.active_tab = key
        for frame in self.tab_frames.values():
            frame.pack_forget()
        self.tab_frames[key].pack(fill="both", expand=True)
        for name, button in self.tab_buttons.items():
            active = name == key
            button.configure(fg=TEXT if active else MUTED,
                             bg=PANEL if active else BG,
                             font=(FONT_BOLD, 10))
        table = getattr(self, "_tables", {}).get(self.tab_frames[key])
        if table is not None:
            self.after(30, table.redraw)

    def _bind_shortcuts(self) -> None:
        """Ctrl+1…7 jumps between tabs; Escape closes any hover card."""
        tab_order = [key for key, _label in (
            ("leaderboard", "Leaderboard"), ("movers", "Movers"), ("flow", "Whale Flow"),
            ("alerts", "Alerts"), ("tokens", "Token Rankings"),
            ("radar", "GMGN Radar"), ("logs", "System Logs"),
        )]
        for index, key in enumerate(tab_order, start=1):
            self.bind(f"<Control-Key-{index}>", lambda _e, k=key: self._show_tab(k))
        self.bind("<Escape>", self._dismiss_popups)

    def _dismiss_popups(self, _event=None) -> None:
        for table in getattr(self, "_tables", {}).values():
            table.tooltip.cancel()
            detail = getattr(table, "hover_detail", None)
            if detail is not None:
                detail.cancel()
        for popup in self.winfo_children():
            if isinstance(popup, tk.Toplevel) and popup.wm_overrideredirect():
                popup.destroy()

    def _table(self, parent: tk.Frame, columns: list[Column], empty: str,
               on_click=None, on_double=None, **kwargs) -> DataTable:
        table = DataTable(parent, columns, on_click=on_click, on_double=on_double, empty_text=empty, **kwargs)
        table.pack(fill="both", expand=True)
        if not hasattr(self, "_tables"):
            self._tables: dict[str, DataTable] = {}
        self._tables[parent] = table
        return table

    # ---------- leaderboard tab ----------

    def _build_leaderboard(self, parent: tk.Frame) -> None:
        toolbar = tk.Frame(parent, bg=BG)
        toolbar.pack(fill="x", pady=(0, 6))
        self.search_var = tk.StringVar()
        self.search_box = tk.Entry(
            toolbar, textvariable=self.search_var, bg=PANEL, fg=TEXT,
            insertbackground=TEXT, relief="flat", font=(FONT, 10), width=34,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        self.search_box.pack(side="left")
        self.search_box.insert(0, "Search trader or wallet…   (Ctrl+F)")
        self.search_box.configure(fg=FAINT)
        self.search_box.bind("<FocusIn>", self._search_focus_in)
        self.search_box.bind("<FocusOut>", self._search_focus_out)
        self.search_var.trace_add("write", lambda *_a: self._apply_filter())
        self.bind("<Control-f>", lambda _e: (self._search_focus_in(), "break")[1])
        self.bind("<Escape>", lambda _e: self._clear_search())
        tk.Label(toolbar, text="double-click a row to copy the wallet  ·  click ↗ for the public profile",
                 bg=BG, fg=FAINT, font=(FONT, 8)).pack(side="right")

        columns = [
            Column("rank", "#", 42, "center",
                   sort=lambda r: r.get("rank") if r.get("rank") is not None else None,
                   color=lambda r: {1: GOLD, 2: SILVER, 3: BRONZE}.get(r.get("rank") or 0, MUTED)),
            Column("handle", "Trader", 140, "w", bold=True),
            Column("wallet", "Wallet", 132, "w", mono=True, color=lambda r: MUTED,
                   text=lambda r: short_wallet(r.get("wallet")),
                   tooltip=lambda r: f"{r.get('handle')} — {r.get('display_name') or ''}\n{r.get('wallet') or 'no wallet'}"),
            Column("pnl", "PnL", 96, "e",
                   text=lambda r: money_compact(r.get("pnl")) if r.get("pnl") is not None else "—",
                   color=lambda r: pnl_color(r.get("pnl")),
                   sort=lambda r: r.get("pnl")),
            Column("pnl_24h", "24h PnL", 96, "e",
                   text=lambda r: f"{trend_arrow(r.get('delta', {}).get('pnl_delta'))} {money_compact(abs(r.get('delta', {}).get('pnl_delta')))}" if r.get("delta", {}).get("pnl_delta") is not None else "—",
                   color=lambda r: pnl_color(r.get("delta", {}).get("pnl_delta")),
                   sort=lambda r: r.get("delta", {}).get("pnl_delta") or 0),
            Column("volume", "Volume", 88, "e",
                   text=lambda r: money_compact(r.get("volume")),
                   sort=lambda r: r.get("volume")),
            Column("trades", "Trades", 62, "e",
                   text=lambda r: num_compact(r.get("trades")),
                   sort=lambda r: r.get("trades")),
            Column("followers", "Followers", 76, "e",
                   text=lambda r: num_compact(r.get("followers")),
                   sort=lambda r: r.get("followers")),
            Column("whale_score", "Score", 78, "center",
                   pill=lambda r: (f"{r.get('whale_score'):.1f}", *reversed(score_style(r.get("whale_score"))))
                   if r.get("whale_score") is not None else ("—", PANEL2, MUTED),
                   sort=lambda r: r.get("whale_score")),
            Column("archetype", "Class", 104, "w", size=8, color=lambda r: MUTED),
            Column("profile", "FOMO", 52, "center", text=lambda r: "↗", click=True,
                   color=lambda r: ACCENT, bold=True,
                   tooltip=lambda r: f"Open https://fomo.family/profile/{str(r.get('handle') or '').lstrip('@')}"),
            Column("gmgn", "GMGN", 56, "center", click=True,
                   text=lambda r: "↗" if r.get("wallet") else "—",
                   color=lambda r: PURPLE if r.get("wallet") else FAINT, bold=True,
                   tooltip=lambda r: "Open wallet on gmgn.ai" if r.get("wallet") else "No known wallet"),
        ]
        self.leaderboard_table = self._table(
            parent, columns, "Waiting for the first collection…",
            on_click=self._leaderboard_click, on_double=lambda row, _col: self._copy_row_wallet(row),
        )
        self.leaderboard_table.sort_key = "whale_score"
        self.leaderboard_table.sort_reverse = True
        self.leaderboard_table.hover_detail = DetailPopup(
            self.leaderboard_table.body, self._trader_detail_lines)

    def _trader_detail_lines(self, row: dict) -> list[tuple[str, str, str]]:
        """Hover card for one trader row (leaderboard + movers)."""
        handle = str(row.get("handle") or "")
        lines: list[tuple[str, str, str]] = [
            ("Trader", f"{handle or '—'}" + (f" · {row.get('display_name')}"
                                             if row.get("display_name") else ""), TEXT),
        ]
        score = row.get("whale_score")
        if score is not None:
            lines.append(("Whale score",
                          f"{score:.1f}" + (f"   Δ {row.get('score_growth'):+.1f}"
                                            if row.get("score_growth") is not None else ""),
                          score_style(score)[0]))
        pnl = row.get("pnl")
        if pnl is not None:
            lines.append(("PnL", money_compact(pnl), pnl_color(pnl)))
        volume = row.get("volume")
        if volume is not None:
            lines.append(("Volume", money_compact(volume), TEXT))
        trades = row.get("trades")
        if trades is not None:
            lines.append(("Trades", num_compact(trades), TEXT))
        followers = row.get("followers")
        if followers is not None:
            lines.append(("Followers", num_compact(followers), TEXT))
        archetype = row.get("archetype")
        if archetype:
            lines.append(("Class", str(archetype), MUTED))
        wallet = row.get("wallet")
        if wallet:
            lines.append(("Wallet", short_wallet(wallet), ACCENT))
        return lines

    def _search_focus_in(self, _e=None) -> None:
        if self.search_box.get().startswith("Search trader"):
            self.search_box.delete(0, "end")
            self.search_box.configure(fg=TEXT)

    def _search_focus_out(self, _e=None) -> None:
        if not self.search_box.get():
            self.search_box.insert(0, "Search trader or wallet…   (Ctrl+F)")
            self.search_box.configure(fg=FAINT)

    def _clear_search(self) -> None:
        self.search_var.set("")
        self._search_focus_out()

    def _apply_filter(self) -> None:
        needle = self.search_var.get().strip().lower()
        if needle.startswith("search trader"):
            needle = ""
        rows = self.dashboard_rows
        if needle:
            rows = [
                row for row in rows
                if needle in str(row.get("handle", "")).lower()
                or needle in str(row.get("display_name", "")).lower()
                or needle in str(row.get("wallet", "")).lower()
            ]
        self.leaderboard_table.set_rows(rows)

    def _leaderboard_click(self, row: dict, col: Column) -> None:
        if col.key == "profile":
            handle = str(row.get("handle") or "").lstrip("@").strip()
            if handle:
                webbrowser.open(f"https://fomo.family/profile/{quote(handle)}")
                self.log(f"Opening profile @{handle}\n", "accent")
        elif col.key == "gmgn":
            url = gmgn_wallet_url(row.get("wallet"))
            if url:
                webbrowser.open(url)
                self.log(f"Opening GMGN wallet {short_wallet(row.get('wallet'))}\n", "accent")
            else:
                self.log("No known wallet for this trader\n", "muted")
        elif col.key == "wallet":
            self._copy_row_wallet(row)

    def _copy_row_wallet(self, row: dict) -> None:
        wallet = row.get("wallet")
        if wallet:
            self.clipboard_clear()
            self.clipboard_append(wallet)
            self.log(f"Copied {short_wallet(wallet)} to clipboard\n", "ok")

    # ---------- movers tab ----------

    def _build_movers(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="SCORE GROWTH BETWEEN THE OLDEST AND LATEST SNAPSHOT · RANKED BY GROWTH",
                 bg=BG, fg=FAINT, font=(FONT_BOLD, 7)).pack(anchor="w", pady=(0, 6))
        columns = [
            Column("handle", "Trader", 170, "w", bold=True),
            Column("score_growth", "Score Δ", 110, "e",
                   text=lambda r: (f"{trend_arrow(r.get('score_growth'))} {money_compact(abs(r.get('score_growth')))}" if r.get("_is_pnl") else f"{trend_arrow(r.get('score_growth'))} {r.get('score_growth'):+.1f}")
                   if r.get("score_growth") is not None else "—",
                   color=lambda r: pnl_color(r.get("score_growth")),
                   sort=lambda r: r.get("score_growth")),
            Column("whale_score", "Current Score", 130, "center",
                   pill=lambda r: (f"{r.get('whale_score'):.1f}", *reversed(score_style(r.get("whale_score"))))
                   if r.get("whale_score") is not None else ("—", PANEL2, MUTED),
                   sort=lambda r: r.get("whale_score")),
            Column("profile", "FOMO", 52, "center", text=lambda r: "↗", click=True,
                   color=lambda r: ACCENT, bold=True,
                   tooltip=lambda r: f"Open https://fomo.family/profile/{str(r.get('handle') or '').lstrip('@')}"),
            Column("gmgn", "GMGN", 56, "center", click=True,
                   text=lambda r: "↗" if r.get("wallet") else "—",
                   color=lambda r: PURPLE if r.get("wallet") else FAINT, bold=True,
                   tooltip=lambda r: "Open wallet on gmgn.ai" if r.get("wallet") else "No known wallet"),
        ]
        self.movers_table = self._table(
            parent, columns,
            "No score history yet — movers appear once each trader has two scored snapshots",
            on_click=self._movers_click,
        )
        self.movers_table.hover_detail = DetailPopup(
            self.movers_table.body, self._trader_detail_lines)

    def _movers_click(self, row: dict, col: Column) -> None:
        if col.key == "profile":
            platform = str(row.get("platform") or "fomo").lower()
            handle = str(row.get("handle") or "").lstrip("@").strip()
            if platform not in ("fomo", "fomoapi", "fomo-crawl"):
                self.log(f"This trader is tracked on {platform}, not FOMO\n", "muted")
            elif handle and not handle.startswith("trader #"):
                webbrowser.open(f"https://fomo.family/profile/{quote(handle)}")
                self.log(f"Opening profile @{handle}\n", "accent")
            else:
                self.log("This trader has no FOMO handle yet\n", "muted")
        elif col.key == "gmgn":
            url = gmgn_wallet_url(row.get("wallet"))
            if url:
                webbrowser.open(url)
                self.log(f"Opening GMGN wallet {short_wallet(row.get('wallet'))}\n", "accent")
            else:
                self.log("No known wallet for this trader\n", "muted")

    # ---------- flow tab ----------

    def _build_flow(self, parent: tk.Frame) -> None:
        self.flow_summary_frame = tk.Frame(parent, bg=BG)
        self.flow_summary_frame.pack(fill="x", pady=(0, 8))
        self.flow_chips: dict[str, tk.Label] = {}
        for key, title, colour, dim in (
            ("recent", "EVENTS 24H", ACCENT, ACCENT_DIM),
            ("pnl", "REALIZED PNL 24H", GREEN, GREEN_DIM),
            ("fomo", "FOMO TOKENS", AMBER, AMBER_DIM),
        ):
            chip = tk.Frame(self.flow_summary_frame, bg=dim)
            chip.pack(side="left", padx=(0, 8))
            tk.Label(chip, text=f" {title} ", bg=dim, fg=MUTED, font=(FONT_BOLD, 7)).pack(side="left", padx=(7, 2), pady=4)
            value = tk.Label(chip, text="—", bg=dim, fg=colour, font=(FONT_BOLD, 9))
            value.pack(side="left", padx=(0, 7), pady=4)
            self.flow_chips[key] = value
        self.accum_frame = tk.Frame(parent, bg=BG)
        self.accum_frame.pack(fill="x", pady=(0, 8))

        tk.Label(parent, text="RECENT WHALE EVENTS", bg=BG, fg=FAINT,
                 font=(FONT_BOLD, 7)).pack(anchor="w", pady=(0, 4))
        event_columns = [
            Column("token", "Token", 130, "w", bold=True),
            Column("event", "Event", 100, "center",
                   pill=lambda r: (str(r.get("event") or "—"), *reversed(event_style(r.get("event"))))),
            Column("side", "Side", 70, "w", size=8, color=lambda r: MUTED),
            Column("size_usd", "Size", 100, "e", text=lambda r: money_compact(r.get("size_usd")),
                   sort=lambda r: r.get("size_usd")),
            Column("realized_pnl_usd", "Realized PnL", 100, "e",
                   text=lambda r: money_compact(r.get("realized_pnl_usd")) if r.get("realized_pnl_usd") is not None else "—",
                   color=lambda r: pnl_color(r.get("realized_pnl_usd")),
                   sort=lambda r: r.get("realized_pnl_usd")),
            Column("acquisition", "Acquisition", 130, "w", size=8, color=lambda r: MUTED),
            Column("when", "When", 86, "e", size=8, color=lambda r: MUTED),
        ]
        self.flow_table = self._table(
            parent, event_columns, "No whale events ≥ $100 in the last 24h")
        self.flow_table.hover_detail = DetailPopup(self.flow_table.body, self._flow_detail_lines)
        # A fixed ~7-row feed leaves room below for the volume-changes table;
        # two expand=True tables squeeze each other off the screen.
        self.flow_table.configure(height=7 * self.flow_table.row_height + DataTable.HEADER_H + 4)
        self.flow_table.pack_configure(expand=False)

        # Volume changes live on the same tab, right under the event feed.
        tk.Label(parent, text="TOKEN VOLUME CHANGES", bg=BG, fg=FAINT,
                 font=(FONT_BOLD, 7)).pack(anchor="w", pady=(8, 4))
        volume_columns = [
            Column("token", "Token", 150, "w", bold=True,
                   text=lambda r: str(r.get("token") or "—")),
            Column("volume_5m", "Vol 5m", 90, "e", text=lambda r: money_compact(r.get("volume_5m"))),
            Column("increase_5m", "Δ 5m", 90, "e",
                   text=lambda r: f"{trend_arrow(r.get('increase_5m'))} {money_compact(abs(r.get('increase_5m')))}" if r.get('increase_5m') is not None else "—",
                   color=lambda r: pnl_color(r.get("increase_5m")), sort=lambda r: r.get("increase_5m")),
            Column("volume_1h", "Vol 1h", 90, "e", text=lambda r: money_compact(r.get("volume_1h"))),
            Column("increase_1h", "Δ 1h", 90, "e",
                   text=lambda r: f"{trend_arrow(r.get('increase_1h'))} {money_compact(abs(r.get('increase_1h')))}" if r.get('increase_1h') is not None else "—",
                   color=lambda r: pnl_color(r.get("increase_1h")), sort=lambda r: r.get("increase_1h")),
        ]
        self.volume_table = self._table(parent, volume_columns, "No volume data in the flow window")
        self.volume_table.hover_detail = DetailPopup(self.volume_table.body, self._volume_detail_lines)

    def _flow_detail_lines(self, row: dict) -> list[tuple[str, str, str]]:
        """Hover card for one whale-event row (flow tab)."""
        token_row = self._radar_lookup(row.get("token_address")) or self._radar_symbol_lookup(row.get("token"))
        lines = [
            ("Token", f"{row.get('token') or '—'}  {chain_icon(row.get('chain') or token_row.get('chain'))} "
             f"{str(row.get('chain') or token_row.get('chain') or '').upper() or '—'}", TEXT),
            ("Event", str(row.get("event") or "—"), TEXT),
            ("Size", money_compact(row.get("size_usd")), TEXT),
        ]
        pnl = row.get("realized_pnl_usd")
        if pnl is not None:
            lines.append(("Realized PnL", money_compact(pnl), pnl_color(pnl)))
        if token_row.get("price_usd") is not None:
            lines.append(("Price", price_text(token_row.get("price_usd")), TEXT))
        lines.append(("When", str(row.get("when") or "—"), MUTED))
        return lines

    # ---------- alerts tab ----------

    def _build_alerts(self, parent: tk.Frame) -> None:
        columns = [
            Column("when", "When", 60, "e", size=8, color=lambda r: MUTED),
            Column("type", "Type", 176, "w",
                   pill=lambda r: (f"{alert_icon(r.get('type'))} "
                                   + str(r.get("type") or "ALERT").replace("_", " "),
                                   *reversed(alert_style(r.get("type"))))),
            Column("message", "Message", 430, "w", stretch=True,
                   tooltip=lambda r: r.get("_full") or ""),
            Column("value", "Value", 86, "e", text=lambda r: money_compact(r.get("value"))),
            Column("address", "Contract", 110, "w", mono=True, size=8,
                   text=lambda r: short_wallet(r.get("_address")) if r.get("_address") else "—",
                   click=True, color=lambda r: ACCENT if r.get("_address") else FAINT,
                   tooltip=lambda r: str(r.get("_address")) if r.get("_address") else "No contract on this alert"),
            Column("open", "GMGN", 52, "center", text=lambda r: "↗" if r.get("_address") else "—",
                   click=True, color=lambda r: PURPLE if r.get("_address") else FAINT, bold=True,
                   tooltip=lambda r: "Open token on gmgn.ai" if r.get("_address") else "No token page"),
        ]
        self.alerts_table = self._table(
            parent, columns, "No alerts yet — alerts fire on score thresholds and provider events",
            on_click=self._alerts_click,
        )
        self.alerts_table.hover_detail = DetailPopup(
            self.alerts_table.body, self._alert_detail_lines)

    def _alerts_click(self, row: dict, col: Column) -> None:
        address = row.get("_address")
        if col.key == "address" and address:
            self.clipboard_clear()
            self.clipboard_append(address)
            self.log(f"Copied contract {short_wallet(address)} to clipboard\n", "ok")
        elif col.key == "open" and address:
            url = gmgn_token_url(row.get("_chain"), address)
            if url:
                webbrowser.open(url)
                self.log(f"Opening GMGN token {row.get('_symbol') or short_wallet(address)}\n", "accent")

    def _radar_lookup(self, address: str | None) -> dict:
        """Find a token row from the radar cache by contract address."""
        if not address:
            return {}
        for rows in (self.radar_data or {}).values():
            for row in rows or []:
                if str(row.get("address") or "").lower() == str(address).lower():
                    return row
        return {}

    def _radar_symbol_lookup(self, symbol: str | None) -> dict:
        """Find a token row from the radar cache by symbol (upper-cased)."""
        needle = str(symbol or "").upper()
        if not needle or needle == "UNKNOWN":
            return {}
        for rows in (self.radar_data or {}).values():
            for row in rows or []:
                if str(row.get("symbol") or "").upper() == needle:
                    return row
        return {}

    def _alert_detail_lines(self, row: dict) -> list[tuple[str, str, str]]:
        """Build the hover card lines for one alert row."""
        address = row.get("_address")
        token_row = self._radar_lookup(address) or self._radar_symbol_lookup(row.get("_symbol"))
        lines = [
            (f"{alert_icon(row.get('type'))} {str(row.get('type') or 'ALERT').replace('_', ' ')}",
             f"{row.get('_symbol') or token_row.get('symbol') or '—'}  "
             f"{chain_icon(row.get('_chain') or token_row.get('chain'))} "
             f"{str(row.get('_chain') or token_row.get('chain') or '').upper() or '—'}", TEXT),
        ]
        address = address or token_row.get("address")
        if address:
            lines.append(("Contract", str(address), ACCENT))
            change = token_row.get("change_5m")
            lines.append(("Change 5m",
                          f"{trend_arrow(change)} {change:+.2f}%" if change is not None else "—",
                          pnl_color(change)))
            change_1h = token_row.get("change_1h")
            lines.append(("Change 1h",
                          f"{trend_arrow(change_1h)} {change_1h:+.2f}%" if change_1h is not None else "—",
                          pnl_color(change_1h)))
            lines.append(("Price", price_text(token_row.get("price_usd")), TEXT))
            lines.append(("Volume 5m", money_compact(token_row.get("volume_usd")), TEXT))
            lines.append(("Volume 1h", money_compact(
                token_row.get("volume_1h") or token_row.get("volume_usd")), TEXT))
        lines.append(("Message", str(row.get("_full") or "").split("\n")[-1], MUTED))
        return lines

    # ---------- tokens tab ----------

    def _build_tokens(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="TOP TOKENS FROM SCANNED WHALE WALLETS · LAST 24 HOURS",
                 bg=BG, fg=FAINT, font=(FONT_BOLD, 7)).pack(anchor="w", pady=(0, 6))
        columns = [
            Column("token", "Token", 130, "w", bold=True),
            Column("chain", "Chain", 70, "w", size=8, color=lambda r: MUTED),
            Column("status", "Market", 90, "center",
                   pill=lambda r: ((str(r.get("market_status") or "updating")).upper(),
                                   *((GREEN, GREEN_DIM) if r.get("market_status") == "ready" else (AMBER, AMBER_DIM)))),
            Column("price_usd", "Price", 92, "e", text=lambda r: price_text(r.get("price_usd"))),
            Column("change_24h", "24h", 80, "e",
                   text=lambda r: f"{r.get('change_24h'):+.2f}%" if r.get("change_24h") is not None else "—",
                   color=lambda r: pnl_color(r.get("change_24h")),
                   sort=lambda r: r.get("change_24h")),
            Column("market_cap_usd", "Mkt Cap", 92, "e", text=lambda r: money_compact(r.get("market_cap_usd"))),
            Column("volume_24h_usd", "Vol 24h", 92, "e", text=lambda r: money_compact(r.get("volume_24h_usd"))),
            Column("sell_events", "Sells", 58, "e", sort=lambda r: r.get("sell_events")),
            Column("sell_volume_usd", "Sell Vol", 92, "e",
                   text=lambda r: money_compact(r.get("sell_volume_usd")) if r.get("sell_volume_usd") else "—",
                   sort=lambda r: r.get("sell_volume_usd")),
            Column("transfer_events", "Xfers", 58, "e", sort=lambda r: r.get("transfer_events")),
            Column("trader_count", "Whales", 60, "e", sort=lambda r: r.get("trader_count")),
            Column("top_whale", "Top Whale", 100, "w", size=8),
        ]
        self.tokens_table = self._table(parent, columns, "No token activity yet — enable on-chain scanning or connect a trade provider")
        self.tokens_table.hover_detail = DetailPopup(self.tokens_table.body, self._token_detail_lines)

    def _token_detail_lines(self, row: dict) -> list[tuple[str, str, str]]:
        """Hover card for one token row (tokens tab)."""
        symbol = row.get("token") or "—"
        token_row = self._radar_lookup(row.get("address")) or self._radar_symbol_lookup(symbol)
        lines = [
            ("Token", f"{symbol}  {chain_icon(row.get('chain') or token_row.get('chain'))} "
             f"{str(row.get('chain') or token_row.get('chain') or '').upper() or '—'}", TEXT),
        ]
        address = row.get("address") or token_row.get("address")
        if address:
            lines.append(("Contract", str(address), ACCENT))
        change = token_row.get("change_5m")
        if change is not None:
            lines.append(("Change 5m", f"{trend_arrow(change)} {change:+.2f}%", pnl_color(change)))
        price = token_row.get("price_usd") or row.get("price_usd")
        if price is not None:
            lines.append(("Price", price_text(price), TEXT))
        top = row.get("top_whale")
        if top:
            lines.append(("Top whale",
                          f"@{top}" + (f"  score {row.get('top_whale_score'):.1f}"
                                       if row.get("top_whale_score") is not None else ""), GOLD))
        lines.append(("Sell vol 24h", money_compact(row.get("sell_volume_usd")), TEXT))
        lines.append(("Whales", str(row.get("trader_count") or "—"), TEXT))
        return lines

    # ---------- GMGN radar tab ----------

    def _build_radar(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=BG)
        header.pack(fill="x", pady=(0, 6))
        self.radar_status = tk.Label(header, text="GMGN radar — idle", bg=BG, fg=MUTED,
                                     font=(FONT, 8), anchor="w")
        self.radar_status.pack(side="left")
        self.radar_section = tk.StringVar(value="trending")
        for key, label in (("trending", "Trending 5m"), ("trenches", "New & Graduating"),
                           ("hot", "Hot Searches"), ("watchlist", "★ Watchlist")):
            button = tk.Label(header, text=label, bg=BG, fg=MUTED, font=(FONT_BOLD, 8),
                              padx=8, pady=2, cursor="hand2")
            button.pack(side="right")
            button.bind("<Button-1>", lambda _e, k=key: self._show_radar_section(k))
            self.radar_buttons = getattr(self, "radar_buttons", {})
            self.radar_buttons[key] = button

        columns = [
            Column("symbol", "Token", 120, "w", bold=True),
            Column("watch", "★", 34, "center", click=True, bold=True,
                   text=lambda r: "★" if r.get("_watched") else "☆",
                   color=lambda r: GOLD if r.get("_watched") else FAINT,
                   tooltip=lambda r: ("Remove from watchlist" if r.get("_watched")
                                      else "Pin this token — refreshed every 5 minutes")),
            Column("chain", "Chain", 70, "w", size=8,
                   text=lambda r: f"{chain_icon(r.get('chain'))} {r.get('chain') or '—'}",
                   color=lambda r: MUTED),
            Column("price_usd", "Price", 84, "e", text=lambda r: price_text(r.get("price_usd"))),
            Column("change_5m", "Δ 5m", 74, "e",
                   text=lambda r: f"{trend_arrow(r.get('change_5m'))} {r.get('change_5m'):+.2f}%"
                   if r.get("change_5m") is not None else "—",
                   color=lambda r: pnl_color(r.get("change_5m")), sort=lambda r: r.get("change_5m")),
            Column("change_1h", "Δ 1h", 74, "e",
                   text=lambda r: f"{trend_arrow(r.get('change_1h'))} {r.get('change_1h'):+.2f}%"
                   if r.get("change_1h") is not None else "—",
                   color=lambda r: pnl_color(r.get("change_1h")), sort=lambda r: r.get("change_1h")),
            Column("market_cap_usd", "Mkt Cap", 92, "e",
                   text=lambda r: money_compact(r.get("market_cap_usd")),
                   sort=lambda r: r.get("market_cap_usd")),
            Column("liquidity_usd", "Liquidity", 92, "e",
                   text=lambda r: money_compact(r.get("liquidity_usd")),
                   sort=lambda r: r.get("liquidity_usd")),
            Column("volume_usd", "Volume", 92, "e",
                   text=lambda r: money_compact(r.get("volume_usd")),
                   sort=lambda r: r.get("volume_usd")),
            Column("holders", "Holders", 70, "e",
                   text=lambda r: num_compact(r.get("holders")),
                   sort=lambda r: r.get("holders")),
            Column("smart_degen_count", "Smart", 60, "e",
                   color=lambda r: GREEN if r.get("smart_degen_count") else MUTED,
                   sort=lambda r: r.get("smart_degen_count")),
            Column("renowned_count", "KOL", 56, "e",
                   color=lambda r: PURPLE if r.get("renowned_count") else MUTED,
                   sort=lambda r: r.get("renowned_count")),
            Column("address", "Address", 150, "w", mono=True, size=8,
                   color=lambda r: MUTED, click=True,
                   text=lambda r: short_wallet(r.get("address")) if r.get("address") else "—",
                   tooltip=lambda r: f"{r.get('symbol')} on {r.get('chain')}\n{r.get('address') or ''}"
                   + ("\nclick to copy the full contract" if r.get("address") else "")),
            Column("open", "GMGN", 52, "center", text=lambda r: "↗" if r.get("address") else "—",
                   click=True, color=lambda r: PURPLE if r.get("address") else FAINT, bold=True,
                   tooltip=lambda r: "Open token on gmgn.ai" if r.get("address") else "No address"),
        ]
        self.radar_table = self._table(
            parent, columns,
            "GMGN radar is empty — enable it in the launcher actions or wait for the worker's first market cycle",
            on_click=self._radar_click,
        )
        self.radar_table.hover_detail = DetailPopup(self.radar_table.body, self._radar_detail_lines)
        self.radar_data: dict[str, list[dict]] = {"trending": [], "trenches": [], "hot": [], "watchlist": []}
        self._show_radar_section("trending")

    def _radar_click(self, row: dict, col: Column) -> None:
        if col.key == "open" and row.get("address"):
            url = gmgn_token_url(row.get("chain"), row.get("address"))
            if url:
                webbrowser.open(url)
                self.log(f"Opening GMGN token {row.get('symbol')}\n", "accent")
        elif col.key == "address" and row.get("address"):
            self.clipboard_clear()
            self.clipboard_append(row["address"])
            self.log(f"Copied contract {short_wallet(row['address'])} to clipboard\n", "ok")
        elif col.key == "watch" and row.get("address"):
            self._watchlist_toggle(row)

    def _watchlist_toggle(self, row: dict) -> None:
        """Pin/unpin a radar row. The worker re-queries pinned tokens every 5
        minutes; removal is instant."""
        chain = str(row.get("chain") or "sol")
        address = str(row.get("address") or "")
        symbol = str(row.get("symbol") or "") or None
        if row.get("_watched"):
            request = Request(f"{API_URL}/watchlist/{chain}/{address}", method="DELETE")
            action = "removed from"
        else:
            payload = json.dumps({"chain": chain, "address": address, "symbol": symbol}).encode()
            request = Request(f"{API_URL}/watchlist", data=payload,
                              headers={"Content-Type": "application/json"}, method="POST")
            action = "pinned to"
        try:
            with urlopen(request, timeout=8) as response:
                response.read()
            self.log(f"{symbol or short_wallet(address)} {action} the watchlist\n",
                     "ok" if action.startswith("pinned") else "muted")
        except Exception as exc:
            self.log(f"Watchlist update failed: {exc}\n", "fail")
        threading.Thread(target=self._fetch_watchlist, daemon=True).start()

    def _fetch_watchlist(self) -> None:
        """Pull the current watchlist (worker-refreshed every 5 minutes)."""
        try:
            with urlopen(f"{API_URL}/watchlist", timeout=8) as response:
                rows = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            self.dashboard_output.put({"_log": f"Watchlist fetch failed: {exc}\n", "_log_tag": "fail"})
            return
        self.dashboard_output.put({"_watchlist": rows})

    def _show_radar_section(self, key: str) -> None:
        self.radar_section.set(key)
        for name, button in getattr(self, "radar_buttons", {}).items():
            active = name == key
            button.configure(fg=TEXT if active else MUTED,
                             bg=PANEL if active else BG)
        self.radar_table.set_rows(self.radar_data.get(key, []))

    def _radar_detail_lines(self, row: dict) -> list[tuple[str, str, str]]:
        """Hover card for one GMGN radar / watchlist token row."""
        lines = [
            ("Token", f"{'★ ' if row.get('_watched') else ''}{row.get('symbol') or '—'}  "
             f"{chain_icon(row.get('chain'))} "
             f"{str(row.get('chain') or '').upper() or '—'}", TEXT),
            ("Price", price_text(row.get("price_usd")), TEXT),
            ("Change 5m", self._pct_text(row.get("change_5m")), pnl_color(row.get("change_5m"))),
            ("Change 1h", self._pct_text(row.get("change_1h")), pnl_color(row.get("change_1h"))),
            ("Mkt Cap", money_compact(row.get("market_cap_usd")), TEXT),
            ("Liquidity", money_compact(row.get("liquidity_usd")), TEXT),
            ("Volume", money_compact(row.get("volume_usd")), TEXT),
            ("Holders", num_compact(row.get("holders")), TEXT),
            ("Smart money", str(row.get("smart_degen_count") or 0), GREEN if row.get("smart_degen_count") else MUTED),
            ("KOLs", str(row.get("renowned_count") or 0), PURPLE if row.get("renowned_count") else MUTED),
        ]
        if row.get("launchpad"):
            lines.append(("Launchpad", str(row.get("launchpad")), MUTED))
        if row.get("address"):
            lines.append(("Contract", str(row.get("address")), ACCENT))
        return lines

    # ---------- logs tab ----------

    def _build_logs(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BORDER)
        wrap.pack(fill="both", expand=True)
        self.log_view = tk.Text(
            wrap, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
            font=(FONT_MONO, 9), wrap="word", padx=12, pady=10, borderwidth=0,
            highlightthickness=0, state="disabled",
        )
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=scroll.set)
        self.log_view.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        scroll.pack(side="right", fill="y", pady=1)
        self.log_view.tag_configure("muted", foreground=MUTED)
        self.log_view.tag_configure("accent", foreground=ACCENT)
        self.log_view.tag_configure("ok", foreground=GREEN)
        self.log_view.tag_configure("fail", foreground=RED)

    # ---------- status bar ----------

    def _status_bar(self) -> None:
        tk.Frame(self, bg=BORDER, height=1).pack(side="bottom", fill="x")
        bar = tk.Frame(self, bg=PANEL, height=30)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        self.status_dots: dict[str, tk.Label] = {}
        self.status_labels: dict[str, tk.Label] = {}
        for key, title in (("worker", "Worker"), ("database", "DB"), ("api", "API")):
            dot = tk.Label(bar, text="●", bg=PANEL, fg=FAINT, font=(FONT, 8))
            dot.pack(side="left", padx=(16, 3))
            label = tk.Label(bar, text=f"{title} —", bg=PANEL, fg=MUTED, font=(FONT, 8))
            label.pack(side="left")
            self.status_dots[key] = dot
            self.status_labels[key] = label
        self.action_status = tk.Label(bar, text="", bg=PANEL, fg=MUTED, font=(FONT, 8))
        self.action_status.pack(side="left", padx=(20, 0))
        self.refresh_label = tk.Label(bar, text="", bg=PANEL, fg=FAINT, font=(FONT, 8))
        self.refresh_label.pack(side="right", padx=16)

    # ---------- dashboard data ----------

    def _start_dashboard_api(self) -> None:
        try:
            with urlopen(f"{API_URL}/health", timeout=1):
                self._start_worker()
                self.refresh_dashboard()
                return
        except Exception:
            pass
        try:
            self.api_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.api.main:app", "--host", "127.0.0.1", "--port", "8000"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=NO_WINDOW,
            )
            threading.Thread(target=self._pump_background, args=("API", self.api_process), daemon=True).start()
            self.log("Starting local API on 127.0.0.1:8000\n", "accent")
            self.api_start_attempts = 0
            self._set_pill("CONNECTING", AMBER, AMBER_DIM)
            self.after(300, self._wait_for_api)
        except OSError as exc:
            self._set_pill("OFFLINE", RED, RED_DIM)
            self.dashboard_status_text(f"API unavailable: {exc}")

    def _wait_for_api(self) -> None:
        try:
            with urlopen(f"{API_URL}/health", timeout=1):
                self._start_worker()
                self.refresh_dashboard()
                return
        except Exception:
            self.api_start_attempts += 1
            if self.api_start_attempts < 30:
                self.after(500, self._wait_for_api)
                return
            self._set_pill("OFFLINE", RED, RED_DIM)
            self.dashboard_status_text("API did not become ready. Click Refresh to retry.")
            self.log("Local API health check timed out after 15 seconds.\n", "fail")

    def _start_worker(self) -> None:
        if self.worker_process is not None and self.worker_process.poll() is None:
            return
        try:
            self.worker_process = subprocess.Popen(
                [sys.executable, "scripts/run_worker.py"], cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=NO_WINDOW,
            )
            threading.Thread(target=self._pump_background, args=("WORKER", self.worker_process), daemon=True).start()
            self.log("Background worker started\n", "accent")
            self._refresh_worker_button()
        except OSError as exc:
            self.dashboard_status_text(f"Worker unavailable: {exc}")

    def _worker_running(self) -> bool:
        return self.worker_process is not None and self.worker_process.poll() is None

    def _refresh_worker_button(self) -> None:
        if self._worker_running():
            self.worker_button.configure(text="■ Stop worker")
        else:
            self.worker_button.configure(text="▸ Start worker")

    def _toggle_worker(self) -> None:
        if self._worker_running():
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.worker_process.pid)],
                               capture_output=True, creationflags=NO_WINDOW, check=False)
            else:
                self.worker_process.terminate()
            self.worker_process = None
            self.log("Background worker stopped.\n", "muted")
        else:
            self.log("Background worker started.\n", "accent")
            self._start_worker()
        self._refresh_worker_button()

    def dashboard_status_text(self, text: str) -> None:
        self.action_status.configure(text=text)

    def _set_pill(self, text: str, fg: str, bg: str) -> None:
        self.pill_text.configure(text=text, fg=fg, bg=bg)
        self.pill_dot.configure(fg=fg, bg=bg)
        self.pill_text.master.configure(bg=bg)

    def refresh_dashboard(self) -> None:
        if self.dashboard_fetching:
            return
        self.dashboard_fetching = True
        threading.Thread(target=self._fetch_dashboard, daemon=True).start()
        self.refresh_alerts()

    def refresh_alerts(self) -> None:
        """Fetch alerts directly so the tab stays fresh even while the worker
        is between leaderboard cycles."""
        if self.alerts_fetching:
            return
        self.alerts_fetching = True
        threading.Thread(target=self._fetch_alerts, daemon=True).start()

    def _fetch_dashboard(self) -> None:
        started = time.monotonic()
        try:
            with urlopen(f"{API_URL}/dashboard?limit=100", timeout=30) as response:
                self.dashboard_output.put(json.loads(response.read().decode("utf-8")))
            self.dashboard_output.put({"_log": f"Dashboard refresh completed in {time.monotonic() - started:.1f}s\n", "_log_tag": "muted"})
        except Exception as exc:
            self.dashboard_output.put({"error": str(exc)})
            self.dashboard_output.put({"_log": f"Dashboard refresh failed after {time.monotonic() - started:.1f}s: {exc}\n", "_log_tag": "fail"})
        finally:
            self.dashboard_fetching = False

    def _pump_background(self, name: str, process: subprocess.Popen) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            self.dashboard_output.put({"_log": f"[{name}] {line}", "_log_tag": "muted"})

    def _fetch_alerts(self) -> None:
        try:
            with urlopen(f"{API_URL}/alerts?limit=100", timeout=8) as response:
                self.dashboard_output.put({"_alerts": json.loads(response.read().decode("utf-8"))})
        except Exception as exc:
            self.dashboard_output.put({"_alerts_error": str(exc)})
        finally:
            self.alerts_fetching = False

    # ---------- GMGN radar ----------

    def refresh_radar(self) -> None:
        """Fetch the GMGN market feeds on its own clock (60s) like Alerts."""
        if getattr(self, "radar_fetching", False):
            return
        self.radar_fetching = True
        threading.Thread(target=self._fetch_radar, daemon=True).start()

    def _fetch_radar(self) -> None:
        feeds: dict[str, object] = {}
        for key, path in (
            ("trending", "/gmgn/trending?interval=5m"),
            ("trenches", "/gmgn/trenches"),
            ("hot", "/gmgn/hot-searches?interval=5m"),
        ):
            try:
                with urlopen(f"{API_URL}{path}", timeout=15) as response:
                    feeds[key] = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                feeds[key] = {"_error": str(exc)}
        self.dashboard_output.put({"_radar": feeds, "_radar_fetched_at": time.monotonic()})
        self.radar_fetching = False
        # The watchlist rides the same clock so ★ rows stay in sync.
        self._fetch_watchlist()

    def _render_radar(self, feeds: dict) -> None:
        def rows_of(key: str, payload: object) -> list[dict]:
            if not isinstance(payload, dict) or "_error" in payload:
                return self.radar_data.get(key, [])
            rows = payload.get("rows")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
            if key == "trenches":
                merged: list[dict] = []
                for kind in ("new_creation", "near_completion", "completed"):
                    merged.extend(payload.get(kind) or [])
                return merged
            return []

        self.radar_data = {key: rows_of(key, feeds.get(key)) for key in ("trending", "trenches", "hot")}
        self._stamp_watched()
        errors = sum(
            1 for payload in feeds.values() if isinstance(payload, dict) and "_error" in payload
        )
        fetched_at = feeds.get("_radar_fetched_at")
        if fetched_at is not None:
            ago = int(time.monotonic() - float(fetched_at))
            status = f"GMGN radar — updated {ago}s ago" + (f" · {errors} feed(s) offline" if errors else "")
            self.radar_status.configure(text=status, fg=MUTED if errors else GREEN)
        self._show_radar_section(self.radar_section.get())

    def _stamp_watched(self) -> None:
        """Flag radar rows that are already pinned so ★/☆ reflects state."""
        watched = {
            (str(r.get("chain")), str(r.get("address")))
            for r in self.radar_data.get("watchlist", [])
        }
        for key in ("trending", "trenches", "hot"):
            for row in self.radar_data.get(key, []):
                row["_watched"] = (str(row.get("chain")), str(row.get("address"))) in watched

    def _render_watchlist(self, rows: list[dict]) -> None:
        """Show pinned tokens; the worker refreshes their data every 5 minutes."""
        self.radar_data["watchlist"] = rows
        self._stamp_watched()
        if self.radar_section.get() == "watchlist":
            self.radar_table.set_rows(rows)
        else:
            # refresh star state on whichever section is visible
            self._show_radar_section(self.radar_section.get())

    # ---------- rendering ----------

    def _render_dashboard(self, data: dict) -> None:
        if "error" in data:
            self._set_pill("CONNECTING", AMBER, AMBER_DIM)
            self.dashboard_status_text(f"Dashboard offline: {data['error']}")
            self.last_update = time.monotonic()
            self.next_refresh = time.monotonic() + 2
            return
        self._set_pill("LIVE", GREEN, GREEN_DIM)
        self.dashboard_rows = data.get("rows", [])
        self.last_update = time.monotonic()
        self.next_refresh = time.monotonic() + REFRESH_SECONDS
        self.next_alert_refresh = time.monotonic() + ALERT_REFRESH_SECONDS
        if self.next_radar_refresh is None:
            self.next_radar_refresh = time.monotonic() + RADAR_REFRESH_SECONDS

        system_status = data.get("status", {})
        self._apply_filter()
        self._render_movers()
        self._render_flow(data.get("flow", {}))
        self._render_alerts(data.get("alerts", []))
        self._render_tokens(data.get("tokens", []))
        self._render_kpis(data.get("kpis", {}))
        self._render_system_status(system_status)

    def _render_kpis(self, kpis: dict) -> None:
        self.kpi_values["volume"].configure(text=money_compact(kpis.get("leaderboard_volume_24h")))
        self.kpi_values["trades"].configure(text=money_compact(kpis.get("volume_24h")))
        top = kpis.get("top_whale")
        score = kpis.get("top_whale_score")
        if top:
            self.kpi_values["whale"].configure(
                text=f"{top}  {score:.1f}" if isinstance(score, (int, float)) else str(top))
        self.kpi_values["traders"].configure(text=num_compact(kpis.get("traders")))
        self.kpi_values["hot"].configure(text=str(kpis.get("hot_token") or "—"))

    def _render_movers(self) -> None:
        """Rank every tracked trader by score growth, not only the top rows.

        The dashboard payload is sorted by whale score, so sorting it again by
        delta hides movers that sit outside the top-100 score list. The API's
        /traders/rankings?kind=growth endpoint returns the whale-score change
        between a trader's oldest and latest snapshots across all traders.
        """
        try:
            with urlopen(f"{API_URL}/traders/rankings?kind=growth&limit=50", timeout=10) as response:
                ranking = json.loads(response.read().decode("utf-8"))
        except Exception:
            ranking = []
        handles = {row["id"]: row.get("handle") for row in self.dashboard_rows if "id" in row}
        wallets = {row["id"]: row.get("wallet") for row in self.dashboard_rows if "id" in row}
        movers = []
        for item in ranking:
            trader_id = item.get("trader_id")
            # The rankings endpoint carries identity fields directly; the
            # dashboard-row join is only a fallback for older API responses.
            movers.append({
                "handle": item.get("handle") or handles.get(trader_id) or f"trader #{trader_id}",
                "wallet": item.get("wallet") or wallets.get(trader_id),
                "platform": item.get("platform"),
                "score_growth": item.get("score_growth"),
                "whale_score": item.get("whale_score"),
            })
        if movers:
            self.movers_table.set_rows(movers)
            return
        # Fallback: the snapshot-delta view when the rankings call fails.
        fallback_rows = []
        for row in self.dashboard_rows:
            pnl_delta = row.get("delta", {}).get("pnl_delta")
            if pnl_delta is not None:
                fallback_rows.append({
                    "handle": row.get("handle"),
                    "wallet": row.get("wallet"),
                    "platform": row.get("platform"),
                    "score_growth": pnl_delta,
                    "whale_score": row.get("whale_score"),
                    "_is_pnl": True
                })
        movers = sorted(
            fallback_rows,
            key=lambda row: abs(row.get("score_growth", 0)),
            reverse=True,
        )[:30]
        self.movers_table.set_rows(movers)

    def _render_flow(self, flow: dict) -> None:
        self.flow_chips["recent"].configure(text=str(flow.get("recent_count", 0)))
        realized = flow.get("realized_profit")
        self.flow_chips["pnl"].configure(
            text=money_compact(realized), fg=pnl_color(realized) if realized is not None else GREEN)
        fomo_tokens = flow.get("fomo_tokens", [])
        self.flow_chips["fomo"].configure(text=", ".join(fomo_tokens[:4]) or "none")

        for child in self.accum_frame.winfo_children():
            child.destroy()
        buys = flow.get("token_buys", [])[:8]
        for item in buys:
            hot = item.get("high_accumulation")
            bg = AMBER_DIM if hot else PANEL
            fg = AMBER if hot else MUTED
            chip = tk.Label(
                self.accum_frame,
                text=f" {'▲ ' if hot else ''}{item.get('token')} ×{item.get('count')} · {money_compact(item.get('volume'))} ",
                bg=bg, fg=fg, font=(FONT_BOLD, 8), padx=8, pady=4,
            )
            chip.pack(side="left", padx=(0, 6))

        events = flow.get("events", [])
        # Real per-event timestamps ("3m ago", "2h ago") instead of the old
        # positional 5-minute labels — the feed now covers a full day.
        for event in events:
            event["when"] = time_ago(event.get("at")) if event.get("at") else "—"
        self.flow_table.set_rows(events)

        # Every token traded in the window — including ones with no recent
        # change (Δ 0) — sorted by 1h volume, so the table reflects the whole
        # token mix instead of only the few movers.
        changes = sorted(
            flow.get("token_volume_changes", []),
            key=lambda item: item.get("volume_1h") or 0, reverse=True,
        )[:20]
        self.volume_table.set_rows(changes)

    def _volume_detail_lines(self, row: dict) -> list[tuple[str, str, str]]:
        """Hover card for one token-volume-changes row."""
        token = str(row.get("token") or "—")
        token_row = self._radar_symbol_lookup(token)
        lines = [
            ("Token", f"{token_row.get('symbol') or token}  {chain_icon(token_row.get('chain'))} "
             f"{str(token_row.get('chain') or '').upper() or '—'}", TEXT),
            ("Price", price_text(token_row.get("price_usd")), TEXT),
            ("Change 5m", self._pct_text(token_row.get("change_5m")), pnl_color(token_row.get("change_5m"))),
            ("Change 1h", self._pct_text(token_row.get("change_1h")), pnl_color(token_row.get("change_1h"))),
            ("Volume 5m", money_compact(row.get("volume_5m")), TEXT),
            ("Volume 1h", money_compact(row.get("volume_1h")), TEXT),
            ("Δ 5m", f"{trend_arrow(row.get('increase_5m'))} {money_compact(row.get('increase_5m'))}",
             pnl_color(row.get("increase_5m"))),
            ("Δ 1h", f"{trend_arrow(row.get('increase_1h'))} {money_compact(row.get('increase_1h'))}",
             pnl_color(row.get("increase_1h"))),
        ]
        address = token_row.get("address") or row.get("address")
        if address:
            lines.append(("Contract", str(address), ACCENT))
        return lines

    def _pct_text(self, value: Any) -> str:
        try:
            return f"{trend_arrow(value)} {float(value):+.2f}%"
        except (TypeError, ValueError):
            return "—"

    def _render_alerts(self, alerts: list[dict]) -> None:
        rows = []
        for alert in alerts:
            payload = alert.get("payload") or {}
            message = payload.get("text") or payload.get("message") or str(payload.get("type") or "event")
            rows.append({
                "when": time_ago(alert.get("created_at")),
                "type": alert.get("type") or "ALERT",
                "message": message if len(message) <= 160 else message[:157] + "…",
                "_full": f"{alert.get('type')}\n{message}",
                "value": payload.get("usdValue"),
                "_chain": payload.get("chain") or payload.get("blockchain"),
                "_address": payload.get("address") or payload.get("contract") or payload.get("token_address"),
                "_symbol": payload.get("symbol") or payload.get("token"),
            })
        self.alerts_table.set_rows(rows)

    def _render_tokens(self, tokens: list[dict]) -> None:
        self.tokens_table.set_rows(tokens)

    def _render_system_status(self, status: dict) -> None:
        worker_state = "RUNNING" if self._worker_running() else status.get("worker", "UNKNOWN")
        mapping = {
            "worker": worker_state,
            "database": status.get("database", "UNKNOWN"),
            "api": status.get("api", "UNKNOWN"),
        }
        for key, value in mapping.items():
            good = value in {"RUNNING", "CONNECTED", "LIVE", "IDLE"}
            colour = GREEN if good else RED
            self.status_dots[key].configure(fg=colour)
            self.status_labels[key].configure(text=f"{key.capitalize()} {value}", fg=MUTED)
        last_collection = status.get("last_collection") or "no collection yet"
        ago = time_ago(last_collection)
        self.dashboard_status_text(
            f"{len(self.dashboard_rows)} traders · last collection {ago} ago" if ago != "—" else "no collection yet")
        self._refresh_worker_button()

    # ---------- per-second tick ----------

    def _tick(self) -> None:
        self._pulse_state = not self._pulse_state
        if self.pill_text.cget("text") == "LIVE":
            self.pill_dot.configure(fg=GREEN if self._pulse_state else blend(GREEN, GREEN_DIM, 0.5))
        now = time.monotonic()
        if self.last_update is not None:
            ago = int(now - self.last_update)
            if self.next_refresh is not None and not self.demo:
                remaining = max(0, int(self.next_refresh - now))
                self.refresh_label.configure(text=f"updated {ago}s ago  ·  refresh in {remaining}s")
                if remaining == 0:
                    self.refresh_dashboard()
            else:
                self.refresh_label.configure(text=f"updated {ago}s ago")
        # Alerts run on their own clock so the tab stays fresh even when the
        # dashboard payload is slow or the worker is between collection cycles.
        if self.next_alert_refresh is not None and not self.demo and now >= self.next_alert_refresh:
            self.next_alert_refresh = now + ALERT_REFRESH_SECONDS
            self.refresh_alerts()
        # GMGN radar refreshes on a slower 60s clock of its own.
        if self.next_radar_refresh is not None and not self.demo and now >= self.next_radar_refresh:
            self.next_radar_refresh = now + RADAR_REFRESH_SECONDS
            self.refresh_radar()
        self.after(1000, self._tick)

    # ---------- log ----------

    def log(self, text: str, tag: str = "") -> None:
        self.log_view.configure(state="normal")
        self.log_view.insert("end", text, tag)
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.configure(state="disabled")

    # ---------- actions ----------

    def launch(self, action: Action) -> None:
        if action.builtin:
            getattr(self, action.builtin)()
            return
        if self.process is not None:
            self.log("Another action is still running. Press Stop first.\n", "fail")
            return
        if action.key == "worker" and self._worker_running():
            self.log("The live worker is already running in the background.\n", "muted")
            return
        self.log(f"$ {action.label}\n", "accent")
        self.stopping = False
        environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        try:
            self.process = subprocess.Popen(
                [sys.executable, *action.argv], cwd=ROOT, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=NO_WINDOW,
            )
        except OSError as exc:
            self.log(f"Could not start it: {exc}\n\n", "fail")
            return
        self._set_running(action)
        threading.Thread(target=self._pump, args=(self.process,), daemon=True).start()

    def _pump(self, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self.output.put(("line", line))
        self.output.put(("done", str(process.wait())))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.output.get_nowait()
                if kind == "line":
                    self.log(payload)
                else:
                    code = int(payload)
                    if self.stopping:
                        # A stopped server exits non-zero; that is not a failure to report.
                        self.log("Stopped.\n\n", "muted")
                        code = 0
                    elif code == 0:
                        self.log("Finished.\n\n", "ok")
                    else:
                        self.log(f"Exited with code {code}.\n\n", "fail")
                    self.stopping = False
                    self._set_idle(code)
        except queue.Empty:
            pass
        try:
            while True:
                data = self.dashboard_output.get_nowait()
                if data is None:
                    continue
                if "_log" in data:
                    self.log(data["_log"], data.get("_log_tag", "muted"))
                elif "_alerts" in data:
                    self._render_alerts(data["_alerts"])
                elif "_alerts_error" in data:
                    # Alerts stay as-is; the next tick retries on schedule.
                    pass
                elif "_radar" in data:
                    self._render_radar(data["_radar"] | {"_radar_fetched_at": data.get("_radar_fetched_at")})
                elif "_watchlist" in data:
                    self._render_watchlist(data["_watchlist"])
                else:
                    self._render_dashboard(data)
        except queue.Empty:
            pass
        self.after(60, self._drain)

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        self.stopping = True
        self.log("Stopping...\n", "muted")
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, creationflags=NO_WINDOW, check=False)
        else:
            process.terminate()

    def _set_running(self, action: Action) -> None:
        self.running_key = action.key
        self.stop_button.configure(state="normal")
        note = " — runs until stopped" if action.long_running else ""
        self.action_status.configure(text=f"Running: {action.label}{note}", fg=AMBER)
        for item, _card, widgets in self.cards:
            active = item.key == action.key
            for widget in widgets:
                widget.configure(bg=HOVER if active else PANEL, cursor="watch")

    def _set_idle(self, code: int) -> None:
        self.process = None
        self.running_key = None
        self.stop_button.configure(state="disabled")
        self.action_status.configure(
            text="Idle" if code == 0 else f"Last action exited with code {code}",
            fg=MUTED if code == 0 else RED,
        )
        for _item, _card, widgets in self.cards:
            for widget in widgets:
                widget.configure(bg=PANEL, cursor="hand2")

    # ---------- builtins ----------

    def open_docs(self) -> None:
        self._open(f"{API_URL}/docs")

    def open_board(self) -> None:
        self._open(f"{API_URL}/leaderboard")

    def open_status(self) -> None:
        self._open(f"{API_URL}/provider/status")

    def _open(self, url: str) -> None:
        if self.process is None and not self._worker_running():
            self.log("Note: the API is not running from here. Start it first if this fails.\n", "muted")
        self.log(f"Opening {url}\n\n", "accent")
        webbrowser.open(url)

    def open_report(self) -> None:
        reports = sorted((ROOT / "reports").glob("*.txt"), key=lambda item: item.stat().st_mtime)
        if not reports:
            self.log("No report yet. Run Start research first.\n\n", "fail")
            return
        self.log(f"Opening {reports[-1].name}\n\n", "accent")
        self._reveal(reports[-1])

    def open_reports(self) -> None:
        folder = ROOT / "reports"
        folder.mkdir(exist_ok=True)
        self.log(f"Opening {folder}\n\n", "accent")
        self._reveal(folder)

    def open_env(self) -> None:
        env_file = ROOT / ".env"
        if not env_file.exists():
            self.log("No .env yet. Copy .env.example to .env first.\n\n", "fail")
            return
        self.log(f"Opening {env_file}\n\n", "accent")
        self._reveal(env_file)

    def open_api_settings(self) -> None:
        fields = (
            ("CHAIN_SCAN_ENABLED", "Enable wallet scanning", False),
            ("ETHERSCAN_API_KEY", "Etherscan API key (ETH/Base/BSC)", True),
            ("SOLSCAN_API_KEY", "Solscan API key", True),
            ("CRYPTOAPIS_API_KEY", "CryptoAPIs key (Base/BSC)", True),
            ("COINGECKO_API_KEY", "CoinGecko API key", True),
            ("CHAIN_SCAN_LOOKBACK_BLOCKS", "EVM lookback blocks", False),
            ("CHAIN_SCAN_WALLET_LIMIT", "Wallets per cycle", False),
            ("GMGN_ENABLED", "Enable GMGN integration", False),
            ("GMGN_API_KEY", "GMGN API key (free at gmgn.ai/ai)", True),
            ("GMGN_CLI_COMMAND", "gmgn-cli command name", False),
            ("GMGN_DEFAULT_CHAIN", "GMGN default chain (sol/bsc/base/eth)", False),
        )
        env_file = ROOT / ".env"
        values: dict[str, str] = {}
        if env_file.exists():
            for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                key, separator, value = raw.partition("=")
                if separator:
                    values[key.strip()] = value.strip()
        dialog = tk.Toplevel(self)
        dialog.title("On-chain API settings")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        body = tk.Frame(dialog, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=16)
        tk.Label(body, text="ON-CHAIN PROVIDERS", bg=BG, fg=TEXT,
                 font=(FONT_BOLD, 12)).pack(anchor="w")
        tk.Label(body, text="Values are saved to .env and used after restarting the worker.",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(2, 12))
        variables: dict[str, tk.StringVar] = {}
        for key, label, secret in fields:
            row = tk.Frame(body, bg=BG)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, width=24, anchor="w", bg=BG, fg=TEXT,
                     font=(FONT, 9)).pack(side="left")
            variable = tk.StringVar(value=values.get(key, ""))
            variables[key] = variable
            entry = tk.Entry(row, textvariable=variable, width=48, show="*" if secret else "",
                             bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                             font=(FONT_MONO, 9), takefocus=True, exportselection=False,
                             highlightthickness=1, highlightbackground=BORDER)
            entry.pack(side="left")
            menu = tk.Menu(entry, tearoff=False, bg=PANEL, fg=TEXT,
                           activebackground=ACCENT, activeforeground="#ffffff")
            menu.add_command(label="Paste", command=lambda target=entry: self._paste_entry(target))
            menu.add_command(label="Copy", command=lambda target=entry: target.event_generate("<<Copy>>"))
            menu.add_command(label="Select all", command=lambda target=entry: target.select_range(0, "end"))
            entry.bind("<Button-3>", lambda event, popup=menu: popup.tk_popup(event.x_root, event.y_root))
            entry.bind("<Control-KeyPress-v>", lambda event, target=entry: (self._paste_entry(target), "break")[1])
            entry.bind("<Control-KeyPress-V>", lambda event, target=entry: (self._paste_entry(target), "break")[1])
            entry.bind("<Control-KeyPress>", lambda event, target=entry: self._paste_physical_v(event, target))
            entry.bind("<Shift-KeyPress-Insert>", lambda event, target=entry: (self._paste_entry(target), "break")[1])
            if key == "GMGN_API_KEY":
                hint = tk.Label(row, text="↳ get a free key at https://gmgn.ai/ai  (enable GMGN integration above)",
                                bg=BG, fg=FAINT, font=(FONT, 8))
                hint.pack(side="left", padx=(8, 0))
            elif key == "COINGECKO_API_KEY":
                hint = tk.Label(row, text="↳ free key: https://www.coingecko.com/en/api",
                                bg=BG, fg=FAINT, font=(FONT, 8))
                hint.pack(side="left", padx=(8, 0))
            elif key == "ETHERSCAN_API_KEY":
                hint = tk.Label(row, text="↳ free key: https://etherscan.io/apis",
                                bg=BG, fg=FAINT, font=(FONT, 8))
                hint.pack(side="left", padx=(8, 0))
            elif key == "CRYPTOAPIS_API_KEY":
                hint = tk.Label(row, text="↳ free tier: https://www.cryptoapis.io",
                                bg=BG, fg=FAINT, font=(FONT, 8))
                hint.pack(side="left", padx=(8, 0))

        def save() -> None:
            current = env_file.read_text(encoding="utf-8", errors="replace").splitlines() if env_file.exists() else []
            replacements = {key: variable.get().strip() for key, variable in variables.items()}
            seen: set[str] = set()
            output: list[str] = []
            for line in current:
                key, separator, _value = line.partition("=")
                clean = key.strip()
                if separator and clean in replacements:
                    output.append(f"{clean}={replacements[clean]}")
                    seen.add(clean)
                else:
                    output.append(line)
            for key, value in replacements.items():
                if key not in seen:
                    output.append(f"{key}={value}")
            env_file.write_text("\n".join(output) + "\n", encoding="utf-8")
            dialog.destroy()
            self._restart_runtime()

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(14, 0))
        self._button(actions, "Cancel", dialog.destroy, PANEL2).pack(side="right")
        self._button(actions, "Save settings", save, ACCENT).pack(side="right", padx=(0, 8))

    def _restart_runtime(self) -> None:
        for process in (self.worker_process, self.api_process):
            if process is not None and process.poll() is None and os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                               capture_output=True, creationflags=NO_WINDOW, check=False)
        self.worker_process = None
        self.api_process = None
        self.dashboard_status_text("Settings saved. Restarting API and worker...")
        self.after(800, self._start_dashboard_api)

    def _paste_entry(self, entry: tk.Entry) -> None:
        try:
            value = self.clipboard_get()
        except tk.TclError:
            return
        try:
            start, end = entry.index("sel.first"), entry.index("sel.last")
            entry.delete(start, end)
        except tk.TclError:
            pass
        entry.insert("insert", value)

    def _paste_physical_v(self, event: tk.Event, entry: tk.Entry) -> str | None:
        """Handle Ctrl+physical-V even when a non-Latin keyboard layout is active."""
        if getattr(event, "keycode", None) == 86:
            self._paste_entry(entry)
            return "break"
        return None

    def open_folder(self) -> None:
        self.log(f"Opening {ROOT}\n\n", "accent")
        self._reveal(ROOT)

    def _reveal(self, target: Path) -> None:
        if os.name == "nt":
            os.startfile(target)
        else:
            webbrowser.open(target.as_uri())

    # ---------- shutdown ----------

    def _on_close(self) -> None:
        if self.process is not None:
            if not messagebox.askokcancel("Quit", "An action is still running. Stop it and quit?"):
                return
            self.stop()
        for process in (self.api_process, self.worker_process):
            if process is not None and process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                                   capture_output=True, creationflags=NO_WINDOW, check=False)
                else:
                    process.terminate()
        self.destroy()

    # ---------- demo mode (for visual checks only; `launcher.py --demo`) ----------

    def _load_demo(self) -> None:
        self._set_pill("DEMO", AMBER, AMBER_DIM)
        from datetime import timedelta

        now = datetime.now(UTC)
        handles = ["solwhale", "0xSniper", "deep_liquidity", "memecoin_maxi", "arbitrage_king",
                   "patient_owl", "degen_dao", "chainReader", "whale_0042", "moon_curator",
                   "base_bull", "qty_analyst", "silk_road_tr", "vector_vault", "gem_hunter",
                   "onchain_oracle", "tidal_fund", "block_baron"]
        archetypes = ["Solid Whale", "Sniper / Insider", "HFT Bot", "Emerging Trader"]
        rows = []
        for index, handle in enumerate(handles):
            pnl = [-48_200, 1_287_400, 845_100, -12_800, 2_140_000, 96_500, 512_300,
                   -4_300, 3_402_900, 720_600, 158_400, 61_200, 990_800, -88_700,
                   44_900, 1_050_200, 233_100, 77_400][index]
            rows.append({
                "id": index + 1, "handle": handle, "display_name": handle.replace("_", " ").title(),
                "wallet": "0x8f2a" + "b4" * 18 + f"{index:02d}" if index % 3 else "9WzD" + "xK" * 20 + f"{index:02d}",
                "rank": index + 1, "pnl": pnl, "volume": abs(pnl) * 12 + 400_000,
                "trades": [34, 1204, 89, 412, 2210, 67, 980, 12, 3450, 156, 78, 45, 640, 29, 8, 1022, 233, 91][index],
                "followers": [1200, 18_400, 4300, 820, 91_200, 640, 12_900, 300, 240_000, 8800, 1500, 410, 22_000, 92, 76, 31_000, 2900, 1100][index],
                "whale_score": [42.5, 88.2, 74.9, 38.1, 91.4, 55.6, 69.3, 29.8, 95.1, 62.4, 58.2, 47.9, 81.5, 33.4, 44.0, 77.8, 60.7, 52.3][index],
                "archetype": archetypes[index % 4],
                "delta": {
                    "pnl_delta": [ -1200, 84_500, -22_300, 900, 210_400, 12_800, -8_100, -90,
                                    402_100, 33_900, -5_400, 2_100, 71_600, -18_200, 480, 96_300, 14_100, -3_200][index],
                    "trades_delta": [2, 41, -6, 8, 112, 4, -12, 1, 220, 9, -3, 2, 28, -9, 1, 44, 7, -2][index],
                    "volume_delta": [12_000, 840_000, -120_000, 8_000, 2_100_000, 90_000, -70_000, 900,
                                     4_020_000, 310_000, -44_000, 18_000, 710_000, -180_000, 3_000, 960_000, 140_000, -30_000][index],
                },
            })
        alerts = [
            {"type": "NEW_SMART_WHALE", "created_at": (now - timedelta(seconds=42)).isoformat(),
             "payload": {"text": "solwhale crossed the smart whale threshold (score 88.2, confidence 82)", "usdValue": None}},
            {"type": "GMGN_TRENDING", "created_at": (now - timedelta(seconds=95)).isoformat(),
             "payload": {"text": "◎CHAD is trending on sol · mcap $172,363 · vol $292,521 · 61 smart money",
                         "type": "GMGN_TRENDING", "chain": "sol", "address": "AvJkUdcMsGT42HBGUh1o3EFPmjocuFsy2sYsu26f843s",
                         "symbol": "CHAD", "usdValue": 292_521, "source": "gmgn"}},
            {"type": "FOMO_BUY", "created_at": (now - timedelta(minutes=3)).isoformat(),
             "payload": {"text": "0xSniper bought $WIF (+$182.4K size)", "trader": "0xSniper", "token": "WIF", "usdValue": 182_400,
                         "chain": "solana", "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"}},
            {"type": "FOMO_SELL", "created_at": (now - timedelta(minutes=7)).isoformat(),
             "payload": {"text": "deep_liquidity sold $BONK (realized +$44.2K)", "token": "BONK", "usdValue": 96_800,
                         "chain": "solana", "address": "DezXAZ8z7PjrU1J4mbuo6Z2eLmF1FSKZBJ9KQanSqYX"}},
            {"type": "WHALE_TRANSFER", "created_at": (now - timedelta(minutes=15)).isoformat(),
             "payload": {"text": "whale_0042 received 2.4M $PEPE from a CEX wallet", "token": "PEPE",
                         "chain": "ethereum", "address": "0x6982508145454ce325ddbe47a25d4ec3d2311933"}},
            {"type": "FOMO_BUY", "created_at": (now - timedelta(minutes=26)).isoformat(),
             "payload": {"text": "moon_curator bought $POPCAT (+$61.9K size)", "token": "POPCAT", "usdValue": 61_900,
                         "chain": "solana", "address": "BC5rH9kFyAqfD8nMEXrXcwnKjtVZT8XcLp9D6hFqpump"}},
            {"type": "EMERGING_WHALE", "created_at": (now - timedelta(hours=2)).isoformat(),
             "payload": {"text": "vector_vault is EMERGING: +14.2 score over 7d with positive rank momentum"}},
        ]
        tokens = [
            {"token": "WIF", "address": "EKpQGSJt…pump", "chain": "solana", "market_status": "ready",
             "price_usd": 2.4181, "change_24h": 12.42, "market_cap_usd": 2_410_000_000, "volume_24h_usd": 412_000_000,
             "sell_events": 4, "sell_volume_usd": 512_300, "transfer_events": 9, "transfer_quantity": 210_000,
             "trader_count": 6, "top_whale": "whale_0042", "top_whale_score": 95.1},
            {"token": "BONK", "address": "DezXAZ8z…BONK", "chain": "solana", "market_status": "ready",
             "price_usd": 0.00003112, "change_24h": -6.81, "market_cap_usd": 1_180_000_000, "volume_24h_usd": 187_000_000,
             "sell_events": 2, "sell_volume_usd": 96_800, "transfer_events": 4, "transfer_quantity": 12_000_000,
             "trader_count": 3, "top_whale": "deep_liquidity", "top_whale_score": 74.9},
            {"token": "PEPE", "address": "0x6982…d3Ab", "chain": "ethereum", "market_status": "ready",
             "price_usd": 0.00001102, "change_24h": 4.18, "market_cap_usd": 4_600_000_000, "volume_24h_usd": 980_000_000,
             "sell_events": 0, "sell_volume_usd": 0, "transfer_events": 2, "transfer_quantity": 2_400_000,
             "trader_count": 1, "top_whale": "whale_0042", "top_whale_score": 95.1},
            {"token": "POPCAT", "address": "BC5rH9k…pump", "chain": "solana", "market_status": "updating",
             "price_usd": None, "change_24h": None, "market_cap_usd": None, "volume_24h_usd": None,
             "sell_events": 1, "sell_volume_usd": 61_900, "transfer_events": 1, "transfer_quantity": 88_000,
             "trader_count": 2, "top_whale": "moon_curator", "top_whale_score": 62.4},
        ]
        flow = {
            "recent_count": 5,
            "realized_profit": 44_200,
            "token_buys": [
                {"token": "WIF", "count": 3, "volume": 412_800, "high_accumulation": True},
                {"token": "POPCAT", "count": 2, "volume": 89_400, "high_accumulation": True},
                {"token": "BONK", "count": 1, "volume": 52_100, "high_accumulation": False},
            ],
            "fomo_tokens": ["WIF", "POPCAT"],
            "events": [
                {"token": "WIF", "event": "BUY", "side": "buy", "size_usd": 182_400, "realized_pnl_usd": None, "acquisition": "PLATFORM_BUY",
                 "at": (now - timedelta(seconds=90)).isoformat()},
                {"token": "BONK", "event": "SELL", "side": "sell", "size_usd": 96_800, "realized_pnl_usd": 44_200, "acquisition": "PLATFORM_SELL",
                 "at": (now - timedelta(minutes=7)).isoformat()},
                {"token": "PEPE", "event": "RECEIVED", "side": "received", "size_usd": 26_400, "realized_pnl_usd": None, "acquisition": "INBOUND_TRANSFER",
                 "at": (now - timedelta(minutes=15)).isoformat()},
                {"token": "POPCAT", "event": "BUY", "side": "buy", "size_usd": 61_900, "realized_pnl_usd": None, "acquisition": "PLATFORM_BUY",
                 "at": (now - timedelta(minutes=26)).isoformat()},
                {"token": "WIF", "event": "BUY", "side": "buy", "size_usd": 118_200, "realized_pnl_usd": None, "acquisition": "PLATFORM_BUY",
                 "at": (now - timedelta(hours=3)).isoformat()},
            ],
            "token_volume_changes": [
                {"token": "WIF", "volume_5m": 118_200, "increase_5m": 64_100, "volume_1h": 300_600, "increase_1h": 182_400},
                {"token": "POPCAT", "volume_5m": 61_900, "increase_5m": 12_400, "volume_1h": 89_400, "increase_1h": 27_500},
                {"token": "BONK", "volume_5m": 0, "increase_5m": -96_800, "volume_1h": 52_100, "increase_1h": -44_700},
            ],
        }
        kpis = {
            "leaderboard_volume_24h": 128_400_000, "volume_24h": 812_400,
            "top_whale": "whale_0042", "top_whale_score": 95.1,
            "traders": len(rows), "hot_token": "WIF", "api_status": "LIVE",
        }
        status = {"worker": "RUNNING", "database": "CONNECTED", "api": "LIVE",
                  "last_collection": (now - timedelta(seconds=12)).isoformat()}
        self._render_dashboard({"rows": rows, "alerts": alerts, "tokens": tokens,
                                "flow": flow, "kpis": kpis, "status": status})

        def demo_token(symbol: str, mcap: float, liq: float, vol: float,
                       holders: int, smart: int, kol: int = 0,
                       launchpad: str | None = None, chain: str = "sol") -> dict:
            return {"symbol": symbol, "chain": chain, "address": f"{symbol.lower()}DemoAddress111111111111",
                    "market_cap_usd": mcap, "liquidity_usd": liq, "volume_usd": vol,
                    "holders": holders, "smart_degen_count": smart, "renowned_count": kol,
                    "launchpad": launchpad, "price_usd": round(mcap / max(holders, 1) * 0.001, 8),
                    "change_5m": round(hash(symbol) % 40 - 12 + 0.5, 2),
                    "change_1h": round(hash(symbol) % 120 - 30 + 0.25, 2)}

        self.radar_data = {
            "trending": [
                demo_token("WIF", 2_410_000_000, 41_200_000, 118_400_000, 214_000, 34, 5),
                demo_token("POPCAT", 980_000_000, 18_300_000, 52_900_000, 96_000, 21, 3),
                demo_token("BONK", 1_180_000_000, 22_400_000, 44_100_000, 730_000, 18, 2),
            ],
            "trenches": [
                demo_token("MOONPIG", 92_400, 24_100, 61_000, 312, 4, 2, launchpad="Pump.fun"),
                demo_token("PEPECEO", 51_200, 11_800, 28_400, 187, 2, 1, launchpad="Pump.fun"),
                demo_token("BASEDOGE", 148_000, 32_600, 74_200, 402, 5, 2,
                           launchpad="Clanker", chain="base"),
            ],
            "hot": [
                demo_token("SOL", 92_400_000_000, 0, 3_100_000_000, 0, 12, 6),
                demo_token("WIF", 2_410_000_000, 0, 412_000_000, 0, 9, 4),
            ],
        }
        self.radar_status.configure(text="GMGN radar — demo data", fg=MUTED)
        self._show_radar_section(self.radar_section.get())


def selftest() -> int:
    """Prove a window can actually open, so run.bat can fall back to its text menu."""
    ensure_tcl()
    try:
        root = tk.Tk()
        root.destroy()
    except tk.TclError as exc:
        print(f"Tk is unavailable: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    demo = "--demo" in sys.argv
    sharpen()
    try:
        Launcher(demo=demo).mainloop()
    except Exception as exc:  # a dialog beats a silent exit under pythonw
        try:
            messagebox.showerror("FOMO Whale Intelligence", f"{type(exc).__name__}: {exc}")
        except Exception:
            raise exc from None


if __name__ == "__main__":
    main()
