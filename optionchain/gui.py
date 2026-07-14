"""
Desktop GUI for optionchain — colorful, readable, every CLI feature + charts.

Run:
  uv run optionchain-gui
"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import customtkinter as ctk

# Prefer interactive backend before pyplot is imported anywhere.
try:
    import matplotlib

    matplotlib.use("TkAgg")
except Exception:  # pragma: no cover
    pass

from optionchain import __version__
from optionchain.compare import compare_itm_otm, style_guidance
from optionchain.display import pcr_sentiment_style
from optionchain.fetcher import OptionChainError, fetch_option_chain, list_expiries
from optionchain.filters import apply_filters, select_expiries
from optionchain.history import fetch_chain_history
from optionchain.leaders import fetch_option_volume_leaders
from optionchain.metrics import compute_put_call_ratio
from optionchain.plotting import build_chain_history_figure, save_chain_history_plot
from optionchain.universe import (
    BIG_CAP_SYMBOLS,
    BIG_CAPS,
    big_cap_choices,
    parse_big_cap_choice,
)
from optionchain.watchlist import export_tradingview_watchlist

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure

    _HAS_MPL = True
except Exception:  # pragma: no cover
    _HAS_MPL = False

# ── Design tokens (match CLI: cyan headers, green calls, red puts) ──────────
C = {
    "bg": "#0b1220",
    "surface": "#121a2b",
    "card": "#172033",
    "elevated": "#1e2a40",
    "border": "#2a3a55",
    "text": "#f1f5f9",
    "muted": "#94a3b8",
    "cyan": "#22d3ee",
    "cyan_dim": "#0e7490",
    "green": "#4ade80",
    "green_dim": "#166534",
    "red": "#f87171",
    "red_dim": "#991b1b",
    "amber": "#fbbf24",
    "magenta": "#e879f9",
    "blue": "#38bdf8",
    "btn": "#0ea5e9",
    "btn_hover": "#0284c7",
    "btn_secondary": "#334155",
    "btn_secondary_hover": "#475569",
    "success": "#22c55e",
    "input_bg": "#0f172a",
    "dropdown_bg": "#1e293b",
    "dropdown_hover": "#334155",
    "white": "#ffffff",
}

FONT = "Helvetica"
# On macOS SF Pro often missing in tk; Helvetica is reliable + readable
FONT_MONO = "Menlo"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

DISCLAIMER = (
    "Research only · not financial advice · data via Yahoo Finance"
)

_RECENTS_PATH = Path.home() / ".optionchain" / "recents.json"
_MAX_RECENTS = 12


def _load_recents() -> list[str]:
    try:
        data = json.loads(_RECENTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x).upper() for x in data if str(x).strip()][:_MAX_RECENTS]
    except Exception:
        pass
    return []


def _save_recent(symbol: str) -> list[str]:
    sym = symbol.strip().upper()
    if not sym:
        return _load_recents()
    items = [sym] + [s for s in _load_recents() if s != sym]
    items = items[:_MAX_RECENTS]
    try:
        _RECENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RECENTS_PATH.write_text(json.dumps(items), encoding="utf-8")
    except Exception:
        pass
    return items


def _err_text(exc: BaseException) -> str:
    if isinstance(exc, OptionChainError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _tree_clear(tree: ttk.Treeview) -> None:
    for item in tree.get_children():
        tree.delete(item)


def _font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def _style_treeview() -> None:
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "App.Treeview",
        background=C["card"],
        foreground=C["text"],
        fieldbackground=C["card"],
        rowheight=30,
        borderwidth=0,
        font=(FONT, 12),
    )
    style.configure(
        "App.Treeview.Heading",
        background=C["elevated"],
        foreground=C["cyan"],
        relief="flat",
        borderwidth=0,
        font=(FONT, 12, "bold"),
        padding=6,
    )
    style.map(
        "App.Treeview",
        background=[("selected", C["cyan_dim"])],
        foreground=[("selected", C["white"])],
    )
    style.map(
        "App.Treeview.Heading",
        background=[("active", C["border"])],
        foreground=[("active", C["cyan"])],
    )
    # Tag colors for call/put/styles
    # Applied per-tree via tag_configure after creation


def _tag_tree(tree: ttk.Treeview) -> None:
    # Calls / puts — soft (not neon) so OTM can be darker for contrast
    tree.tag_configure("call_itm", foreground="#6ee7a8")  # soft green
    tree.tag_configure("put_itm", foreground="#f0a0a0")  # soft red
    tree.tag_configure("call_otm", foreground="#4b6358")  # darker muted green
    tree.tag_configure("put_otm", foreground="#6b4545")  # darker muted red
    tree.tag_configure("call", foreground="#6ee7a8")
    tree.tag_configure("put", foreground="#f0a0a0")
    tree.tag_configure("itm", foreground="#6ee7a8")
    tree.tag_configure("atm", foreground="#d4a84b")  # softer amber
    tree.tag_configure("otm", foreground="#5c6b7a")  # darker slate highlight
    tree.tag_configure("alt", background=C["elevated"])
    tree.tag_configure("up", foreground="#6ee7a8")
    tree.tag_configure("down", foreground="#e08080")


def _pcr_hex(ratio: float | None) -> tuple[str, str]:
    """
    CLI-matching PCR gradient → (background_hex, text_hex).
    Low PCR = green (optimistic); high = red (cautious).
    """
    if ratio is None:
        return C["elevated"], C["muted"]
    style = pcr_sentiment_style(ratio)  # "bold rgb(R,G,B)"
    try:
        body = style.split("rgb(")[1].rstrip(")")
        r, g, b = (int(x.strip()) for x in body.split(","))
    except Exception:
        return C["elevated"], C["text"]
    # Darken for chip background, keep bright text
    br, bg_, bb = max(0, r // 4), max(0, g // 4), max(0, b // 4)
    return f"#{br:02x}{bg_:02x}{bb:02x}", f"#{r:02x}{g:02x}{b:02x}"


class Worker:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._poll()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "ok":
                    cb, result = payload
                    cb(result)
                else:
                    cb, err = payload
                    cb(err)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def submit(
        self,
        fn: Callable[[], Any],
        on_ok: Callable[[Any], None],
        on_err: Callable[[BaseException], None],
    ) -> None:
        def runner() -> None:
            try:
                self.q.put(("ok", (on_ok, fn())))
            except BaseException as exc:  # noqa: BLE001
                self.q.put(("err", (on_err, exc)))

        threading.Thread(target=runner, daemon=True).start()


# ── Reusable widgets ────────────────────────────────────────────────────────


def make_entry(
    master: Any,
    *,
    width: int = 120,
    placeholder: str = "",
    text: str = "",
) -> ctk.CTkEntry:
    e = ctk.CTkEntry(
        master,
        width=width,
        height=34,
        corner_radius=8,
        border_width=1,
        border_color=C["border"],
        fg_color=C["input_bg"],
        text_color=C["text"],
        placeholder_text_color=C["muted"],
        placeholder_text=placeholder,
        font=_font(13),
    )
    if text:
        e.insert(0, text)
    return e


def make_option_menu(
    master: Any,
    values: list[str],
    *,
    width: int = 120,
    default: str | None = None,
    command: Callable[[str], Any] | None = None,
) -> ctk.CTkOptionMenu:
    """Option menu with fixed width + high-contrast colors (no blank text)."""
    menu = ctk.CTkOptionMenu(
        master,
        values=values,
        width=width,
        height=34,
        corner_radius=8,
        font=_font(13, "bold"),
        dropdown_font=_font(13),
        fg_color=C["dropdown_bg"],
        button_color=C["btn_secondary"],
        button_hover_color=C["btn_secondary_hover"],
        text_color=C["white"],
        text_color_disabled=C["muted"],
        dropdown_fg_color=C["elevated"],
        dropdown_hover_color=C["cyan_dim"],
        dropdown_text_color=C["white"],
        anchor="center",
        dynamic_resizing=False,
        command=command,
    )
    menu.set(default if default is not None else values[0])
    return menu


def make_combo(
    master: Any,
    values: list[str] | None = None,
    *,
    width: int = 140,
    placeholder: str = "",
) -> ctk.CTkComboBox:
    box = ctk.CTkComboBox(
        master,
        values=values or [""],
        width=width,
        height=34,
        corner_radius=8,
        border_width=1,
        border_color=C["border"],
        fg_color=C["input_bg"],
        button_color=C["btn_secondary"],
        button_hover_color=C["btn_secondary_hover"],
        dropdown_fg_color=C["elevated"],
        dropdown_hover_color=C["cyan_dim"],
        dropdown_text_color=C["white"],
        text_color=C["text"],
        font=_font(13),
        dropdown_font=_font(13),
        state="normal",
    )
    if placeholder:
        box.set(placeholder)
    elif values:
        box.set(values[0])
    return box


class Toolbar(ctk.CTkFrame):
    """
    Card toolbar with two rows:
      row 0–1: fields (label above control) in a horizontal grid
      row 2:   full-width action buttons (always visible, never clipped)
    """

    def __init__(self, master: Any, **kwargs: Any) -> None:
        super().__init__(
            master,
            fg_color=C["card"],
            corner_radius=12,
            border_width=1,
            border_color=C["border"],
            **kwargs,
        )
        self.fields = ctk.CTkFrame(self, fg_color="transparent")
        self.fields.pack(fill="x", padx=14, pady=(12, 6))
        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.actions.pack(fill="x", padx=14, pady=(4, 12))
        self._col = 0

    def add_field(
        self,
        label: str,
        widget: ctk.CTkBaseClass,
        *,
        label_color: str | None = None,
        padx: tuple[int, int] = (0, 14),
    ) -> ctk.CTkBaseClass:
        col = self._col
        self._col += 1
        ctk.CTkLabel(
            self.fields,
            text=label.upper(),
            font=_font(10, "bold"),
            text_color=label_color or C["muted"],
            anchor="w",
        ).grid(row=0, column=col, sticky="w", padx=padx, pady=(0, 5))
        widget.grid(row=1, column=col, sticky="w", padx=padx)
        self.fields.grid_columnconfigure(col, weight=0)
        return widget

    def add_actions(self, build: Callable[[ctk.CTkFrame], None]) -> None:
        """``build(box)`` creates buttons with ``master=box`` and packs them."""
        build(self.actions)


def make_primary_btn(
    master: Any, text: str, command: Callable[[], Any], *, width: int = 130
) -> ctk.CTkButton:
    return ctk.CTkButton(
        master,
        text=text,
        command=command,
        width=width,
        height=36,
        corner_radius=8,
        font=_font(13, "bold"),
        fg_color=C["btn"],
        hover_color=C["btn_hover"],
        text_color=C["white"],
    )


def make_secondary_btn(
    master: Any, text: str, command: Callable[[], Any], *, width: int = 140
) -> ctk.CTkButton:
    return ctk.CTkButton(
        master,
        text=text,
        command=command,
        width=width,
        height=36,
        corner_radius=8,
        font=_font(12, "bold"),
        fg_color=C["btn_secondary"],
        hover_color=C["btn_secondary_hover"],
        text_color=C["text"],
        border_width=1,
        border_color=C["border"],
    )


def make_accent_btn(
    master: Any,
    text: str,
    command: Callable[[], Any],
    *,
    color: str,
    hover: str,
    width: int = 150,
) -> ctk.CTkButton:
    return ctk.CTkButton(
        master,
        text=text,
        command=command,
        width=width,
        height=36,
        corner_radius=8,
        font=_font(12, "bold"),
        fg_color=color,
        hover_color=hover,
        text_color=C["white"],
    )


class InfoCard(ctk.CTkFrame):
    """Colored left-border card for status / PCR / summaries."""

    def __init__(
        self,
        master: Any,
        *,
        accent: str = C["cyan"],
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            fg_color=C["card"],
            corner_radius=10,
            border_width=0,
            **kwargs,
        )
        self._bar = ctk.CTkFrame(self, width=5, fg_color=accent, corner_radius=0)
        self._bar.pack(side="left", fill="y")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(side="left", fill="both", expand=True, padx=12, pady=10)
        self.title_lbl = ctk.CTkLabel(
            self.body,
            text="",
            font=_font(14, "bold"),
            text_color=C["text"],
            anchor="w",
        )
        self.title_lbl.pack(fill="x")
        self.sub_lbl = ctk.CTkLabel(
            self.body,
            text="",
            font=_font(12),
            text_color=C["muted"],
            anchor="w",
            justify="left",
            wraplength=900,
        )
        self.sub_lbl.pack(fill="x", pady=(2, 0))

    def set_accent(self, color: str) -> None:
        self._bar.configure(fg_color=color)

    def set(self, title: str, subtitle: str = "") -> None:
        self.title_lbl.configure(text=title)
        self.sub_lbl.configure(text=subtitle)


class Chip(ctk.CTkFrame):
    """Small colored badge (e.g. PCR value)."""

    def __init__(
        self,
        master: Any,
        text: str,
        *,
        fg: str = C["elevated"],
        text_color: str = C["text"],
    ) -> None:
        super().__init__(master, fg_color=fg, corner_radius=6)
        ctk.CTkLabel(
            self,
            text=text,
            font=_font(11, "bold"),
            text_color=text_color,
        ).pack(padx=10, pady=5)


class StatusBar(ctk.CTkFrame):
    def __init__(self, master: Any, **kwargs: Any) -> None:
        super().__init__(
            master,
            height=36,
            fg_color=C["surface"],
            corner_radius=0,
            **kwargs,
        )
        self.dot = ctk.CTkLabel(self, text="●", text_color=C["green"], width=16)
        self.dot.pack(side="left", padx=(12, 4))
        self.label = ctk.CTkLabel(
            self,
            text=DISCLAIMER,
            font=_font(11),
            text_color=C["muted"],
            anchor="w",
        )
        self.label.pack(side="left", fill="x", expand=True, pady=6)
        self.busy = ctk.CTkProgressBar(
            self,
            width=140,
            height=8,
            mode="indeterminate",
            progress_color=C["cyan"],
            fg_color=C["elevated"],
        )
        self._busy = False

    def set_message(self, text: str, *, ok: bool = True) -> None:
        self.label.configure(text=text)
        self.dot.configure(text_color=C["green"] if ok else C["amber"])

    def start_busy(self, text: str = "Loading…") -> None:
        self.set_message(text, ok=True)
        self.dot.configure(text_color=C["cyan"])
        if not self._busy:
            self.busy.pack(side="right", padx=12, pady=10)
            self.busy.start()
            self._busy = True

    def stop_busy(self, text: str | None = None, *, ok: bool = True) -> None:
        if self._busy:
            self.busy.stop()
            self.busy.pack_forget()
            self._busy = False
        if text is not None:
            self.set_message(text, ok=ok)


class OptionChainApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"OptionChain  v{__version__}")
        self.geometry("1240x820")
        self.minsize(1020, 680)
        self.configure(fg_color=C["bg"])

        self.worker = Worker(self)
        _style_treeview()

        self._leaders_result = None
        self._history_result = None
        self._plot_canvas: Any = None
        self._plot_toolbar: Any = None
        self._plot_fig: Figure | None = None
        self._chain_busy = False
        self._suppress_chain_auto = False
        self._current_symbol = "TSLA"
        self._recents = _load_recents()

        self._build_header()
        self.tabs = ctk.CTkTabview(
            self,
            fg_color=C["surface"],
            segmented_button_fg_color=C["elevated"],
            segmented_button_selected_color=C["cyan_dim"],
            segmented_button_selected_hover_color=C["cyan"],
            segmented_button_unselected_color=C["elevated"],
            segmented_button_unselected_hover_color=C["border"],
            text_color=C["text"],
            corner_radius=12,
            border_width=1,
            border_color=C["border"],
        )
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(4, 8))
        # Larger tab labels
        try:
            self.tabs._segmented_button.configure(font=_font(13, "bold"))  # noqa: SLF001
        except Exception:
            pass

        self.tab_chain = self.tabs.add("  📊  Chain  ")
        self.tab_top = self.tabs.add("  🔥  Top Volume  ")
        self.tab_history = self.tabs.add("  📈  History + Chart  ")
        self.tab_compare = self.tabs.add("  ⚖️  ITM vs OTM  ")
        self.tab_help = self.tabs.add("  ❓  Help  ")

        for tab in (
            self.tab_chain,
            self.tab_top,
            self.tab_history,
            self.tab_compare,
            self.tab_help,
        ):
            tab.configure(fg_color=C["surface"])

        self._build_chain_tab()
        self._build_top_tab()
        self._build_history_tab()
        self._build_compare_tab()
        self._build_help_tab()

        self.status = StatusBar(self)
        self.status.pack(fill="x", side="bottom")

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=C["bg"], height=64)
        header.pack(fill="x", padx=14, pady=(12, 0))
        header.pack_propagate(False)

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", fill="y")
        title_row = ctk.CTkFrame(left, fg_color="transparent")
        title_row.pack(anchor="w")
        ctk.CTkLabel(
            title_row,
            text="OptionChain",
            font=_font(24, "bold"),
            text_color=C["cyan"],
        ).pack(side="left")
        badge = ctk.CTkFrame(title_row, fg_color=C["cyan_dim"], corner_radius=6)
        badge.pack(side="left", padx=10)
        ctk.CTkLabel(
            badge,
            text=f"v{__version__}",
            font=_font(11, "bold"),
            text_color=C["cyan"],
            padx=8,
            pady=2,
        ).pack()
        ctk.CTkLabel(
            left,
            text="Live option chains · volume leaders · multi-day charts · ITM/OTM research",
            font=_font(12),
            text_color=C["muted"],
        ).pack(anchor="w", pady=(2, 0))

        legend = ctk.CTkFrame(header, fg_color="transparent")
        legend.pack(side="right", padx=4)
        for text, color in (
            ("CALLS", C["green"]),
            ("PUTS", C["red"]),
            ("ATM", C["amber"]),
            ("PCR", C["magenta"]),
        ):
            chip = ctk.CTkFrame(legend, fg_color=C["card"], corner_radius=8)
            chip.pack(side="left", padx=4)
            ctk.CTkLabel(
                chip,
                text=f"● {text}",
                font=_font(11, "bold"),
                text_color=color,
                padx=10,
                pady=6,
            ).pack()

    def _run(
        self,
        fn: Callable[[], Any],
        on_ok: Callable[[Any], None],
        busy: str = "Loading…",
    ) -> None:
        self.status.start_busy(busy)

        def ok(result: Any) -> None:
            self.status.stop_busy("Ready · research only")
            on_ok(result)

        def err(exc: BaseException) -> None:
            self.status.stop_busy("Error", ok=False)
            messagebox.showerror("OptionChain", _err_text(exc))

        self.worker.submit(fn, ok, err)

    def _toolbar(self, parent: Any) -> Toolbar:
        bar = Toolbar(parent)
        bar.pack(fill="x", padx=10, pady=(10, 6))
        return bar

    def _table_frame(self, parent: Any) -> tuple[ctk.CTkFrame, ttk.Treeview]:
        wrap = ctk.CTkFrame(
            parent,
            fg_color=C["card"],
            corner_radius=12,
            border_width=1,
            border_color=C["border"],
        )
        wrap.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        host = tk.Frame(wrap, bg=C["card"], highlightthickness=0)
        host.pack(fill="both", expand=True, padx=8, pady=8)
        tree = ttk.Treeview(host, show="headings", style="App.Treeview")
        sb = ttk.Scrollbar(host, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        _tag_tree(tree)
        return wrap, tree

    # ── Chain ────────────────────────────────────────────────
    def _build_chain_tab(self) -> None:
        t = self.tab_chain
        bar = self._toolbar(t)

        default_sym = self._recents[0] if self._recents else "TSLA"
        self.chain_symbol = make_entry(
            bar.fields, width=110, text=default_sym, placeholder="Ticker"
        )
        self.chain_symbol.bind("<Return>", lambda _e: self._chain_load())
        self.chain_symbol.bind("<KP_Enter>", lambda _e: self._chain_load())

        self.chain_type_var = ctk.StringVar(value="all")
        self.chain_type_seg = ctk.CTkSegmentedButton(
            bar.fields,
            values=["all", "call", "put"],
            variable=self.chain_type_var,
            width=200,
            height=34,
            font=_font(12, "bold"),
            fg_color=C["elevated"],
            selected_color=C["cyan_dim"],
            selected_hover_color=C["cyan"],
            unselected_color=C["dropdown_bg"],
            unselected_hover_color=C["border"],
            text_color=C["white"],
            command=self._on_chain_type_change,
        )
        self.chain_type_seg.set("all")

        self.chain_expiry = make_combo(
            bar.fields,
            values=["Nearest"],
            width=150,
            placeholder="Nearest",
        )
        self.chain_expiry.set("Nearest")
        self.chain_expiry.configure(command=self._on_chain_expiry_change)

        self.chain_near = make_entry(bar.fields, width=72, text="8", placeholder="8")
        self.chain_smin = make_entry(bar.fields, width=80, placeholder="Min")
        self.chain_smax = make_entry(bar.fields, width=80, placeholder="Max")

        bar.add_field("Symbol", self.chain_symbol, label_color=C["cyan"])
        bar.add_field("Type", self.chain_type_seg, label_color=C["green"])
        bar.add_field("Expiry", self.chain_expiry, label_color=C["amber"])
        bar.add_field("Near ATM", self.chain_near)
        bar.add_field("Strike ≥", self.chain_smin)
        bar.add_field("Strike ≤", self.chain_smax)

        def _chain_actions(box: ctk.CTkFrame) -> None:
            make_primary_btn(box, "Load", self._chain_load, width=110).pack(
                side="left", padx=(0, 8)
            )
            self._filters_btn = make_secondary_btn(
                box, "Filters ▸", self._toggle_chain_filters, width=100
            )
            self._filters_btn.pack(side="left", padx=(0, 8))
            make_secondary_btn(
                box, "Chart 5d", self._quick_history, width=100
            ).pack(side="left", padx=(0, 8))
            make_secondary_btn(
                box, "Compare", self._quick_compare, width=100
            ).pack(side="left", padx=(0, 8))
            ctk.CTkLabel(
                box,
                text="  Enter = Load · type/expiry change reloads",
                font=_font(11),
                text_color=C["muted"],
            ).pack(side="left", padx=8)

        bar.add_actions(_chain_actions)
        self._chain_toolbar = bar
        self._chain_adv_open = True  # start open so we can snapshot grid, then hide
        self._chain_adv_grid: list[tuple[Any, dict[str, Any]]] = []
        for w in bar.fields.grid_slaves():
            info = dict(w.grid_info())
            try:
                col = int(info.get("column", -1))
            except (TypeError, ValueError):
                continue
            if col >= 3:
                self._chain_adv_grid.append((w, info))
        self._set_chain_filters_visible(False)

        # ── Two-column section: big caps (left) | status + PCR (right) ──
        two_col = ctk.CTkFrame(t, fg_color="transparent")
        two_col.pack(fill="x", padx=10, pady=(2, 4))
        two_col.grid_columnconfigure(0, weight=1, uniform="chain_top")
        two_col.grid_columnconfigure(1, weight=1, uniform="chain_top")

        # LEFT — big caps
        left = ctk.CTkFrame(
            two_col,
            fg_color=C["card"],
            corner_radius=12,
            border_width=1,
            border_color=C["border"],
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        pick_inner = ctk.CTkFrame(left, fg_color="transparent")
        pick_inner.pack(fill="both", expand=True, padx=12, pady=10)

        head = ctk.CTkFrame(pick_inner, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(
            head,
            text="BIG CAPS",
            font=_font(11, "bold"),
            text_color=C["cyan"],
        ).pack(side="left")
        ctk.CTkLabel(
            head,
            text="  ·  click to load",
            font=_font(11),
            text_color=C["muted"],
        ).pack(side="left")

        drop_row = ctk.CTkFrame(pick_inner, fg_color="transparent")
        drop_row.pack(fill="x", pady=(8, 6))
        self.bigcap_combo = make_combo(
            drop_row,
            values=big_cap_choices(),
            width=240,
            placeholder="AAPL — Apple",
        )
        default_choice = next(
            (c for c in big_cap_choices() if c.startswith(f"{default_sym} ")),
            big_cap_choices()[0],
        )
        self.bigcap_combo.set(default_choice)
        self.bigcap_combo.pack(side="left", padx=(0, 8))
        make_primary_btn(drop_row, "Open", self._on_bigcap_open, width=80).pack(
            side="left"
        )

        # Chips in a wrapping 2-row feel via horizontal scroll
        self.bigcap_chips = ctk.CTkScrollableFrame(
            pick_inner,
            height=88,
            orientation="horizontal",
            fg_color=C["elevated"],
            corner_radius=8,
            border_width=0,
        )
        self.bigcap_chips.pack(fill="x", pady=(2, 0))
        featured = [
            "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
            "TSLA", "AMD", "JPM", "V", "XOM", "UNH", "WMT", "NFLX",
        ]
        for sym in featured:
            if sym not in BIG_CAP_SYMBOLS:
                continue
            self._make_symbol_chip(
                self.bigcap_chips,
                sym,
                command=lambda s=sym: self._open_symbol_in_chain(s, auto_load=True),
                accent=C["cyan"],
            )

        # RIGHT — recents + status + PCR
        right = ctk.CTkFrame(
            two_col,
            fg_color=C["card"],
            corner_radius=12,
            border_width=1,
            border_color=C["border"],
        )
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right_inner = ctk.CTkFrame(right, fg_color="transparent")
        right_inner.pack(fill="both", expand=True, padx=12, pady=10)

        ctk.CTkLabel(
            right_inner,
            text="STATUS  ·  PCR",
            font=_font(11, "bold"),
            text_color=C["magenta"],
            anchor="w",
        ).pack(fill="x")

        self.recents_row = ctk.CTkFrame(right_inner, fg_color="transparent")
        self.recents_row.pack(fill="x", pady=(6, 6))
        self._render_recents()

        # Info card without outer pack — nested
        self.chain_card = InfoCard(right_inner, accent=C["cyan"])
        self.chain_card.pack(fill="x", pady=(4, 6))
        self.chain_card.set(
            "Pick a big cap or type a ticker",
            "Load fills nearest expiry + chain. Soft green/red = ITM · "
            "darker rows = OTM.",
        )

        self.pcr_row = ctk.CTkFrame(right_inner, fg_color="transparent")
        self.pcr_row.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(
            self.pcr_row,
            text="PCR appears after Load  ·  green=optimistic  amber=balanced  red=cautious",
            font=_font(11),
            text_color=C["muted"],
            anchor="w",
        ).pack(fill="x")

        # Chain table (no ITM column)
        _, self.chain_tree = self._table_frame(t)
        cols = (
            "expiry", "type", "strike", "last", "bid", "ask", "vol", "oi", "iv",
        )
        self.chain_tree["columns"] = cols
        heads = {
            "expiry": "Expiry", "type": "Type", "strike": "Strike", "last": "Last",
            "bid": "Bid", "ask": "Ask", "vol": "Vol", "oi": "OI", "iv": "IV",
        }
        widths = {
            "expiry": 110, "type": 70, "strike": 90, "last": 80, "bid": 75,
            "ask": 75, "vol": 90, "oi": 90, "iv": 80,
        }
        for c in cols:
            self.chain_tree.heading(c, text=heads[c])
            self.chain_tree.column(c, width=widths[c], anchor="center")

    def _set_pcr_chips(self, vol_ratio: float | None, oi_ratio: float | None, detail: str) -> None:
        for w in self.pcr_row.winfo_children():
            w.destroy()
        ctk.CTkLabel(
            self.pcr_row,
            text="PUT / CALL RATIO",
            font=_font(10, "bold"),
            text_color=C["magenta"],
        ).pack(side="left", padx=(0, 8))

        def chip(label: str, value: str, ratio: float | None) -> None:
            # Same green→amber→red gradient as the CLI
            fg, tc = _pcr_hex(ratio)
            Chip(self.pcr_row, f"{label}  {value}", fg=fg, text_color=tc).pack(
                side="left", padx=4
            )

        vr = "n/a" if vol_ratio is None else f"{vol_ratio:.3f}"
        oi = "n/a" if oi_ratio is None else f"{oi_ratio:.3f}"
        chip("Vol", vr, vol_ratio)
        chip("OI", oi, oi_ratio)
        # Mini legend matching CLI
        leg = ctk.CTkFrame(self.pcr_row, fg_color="transparent")
        leg.pack(side="left", padx=(10, 0))
        for text, ratio in (("low", 0.5), ("≈1", 1.0), ("high", 1.8)):
            fg, tc = _pcr_hex(ratio)
            Chip(leg, text, fg=fg, text_color=tc).pack(side="left", padx=2)
        ctk.CTkLabel(
            self.pcr_row,
            text=detail,
            font=_font(10),
            text_color=C["muted"],
        ).pack(side="left", padx=8)

    def _set_chain_filters_visible(self, visible: bool) -> None:
        """Show/hide Near + strike filters (grid columns 3–5)."""
        self._chain_adv_open = visible
        for w, info in getattr(self, "_chain_adv_grid", []):
            if visible:
                opts = {
                    k: v
                    for k, v in info.items()
                    if k in {"row", "column", "sticky", "padx", "pady", "rowspan", "columnspan"}
                }
                w.grid(**opts)
            else:
                w.grid_remove()
        if hasattr(self, "_filters_btn"):
            self._filters_btn.configure(
                text="Filters ▾" if visible else "Filters ▸"
            )

    def _toggle_chain_filters(self) -> None:
        self._set_chain_filters_visible(not self._chain_adv_open)

    def _make_symbol_chip(
        self,
        master: Any,
        symbol: str,
        *,
        command: Callable[[], Any],
        accent: str = C["cyan"],
    ) -> None:
        btn = ctk.CTkButton(
            master,
            text=symbol,
            width=68,
            height=30,
            corner_radius=8,
            font=_font(12, "bold"),
            fg_color=C["dropdown_bg"],
            hover_color=C["cyan_dim"],
            text_color=accent,
            border_width=1,
            border_color=C["border"],
            command=command,
        )
        btn.pack(side="left", padx=4, pady=6)

    def _render_recents(self) -> None:
        for w in self.recents_row.winfo_children():
            w.destroy()
        ctk.CTkLabel(
            self.recents_row,
            text="RECENTS",
            font=_font(10, "bold"),
            text_color=C["muted"],
        ).pack(side="left", padx=(0, 8))
        if not self._recents:
            ctk.CTkLabel(
                self.recents_row,
                text="— load a chain to build history",
                font=_font(11),
                text_color=C["muted"],
            ).pack(side="left")
            return
        for sym in self._recents[:10]:
            self._make_symbol_chip(
                self.recents_row,
                sym,
                command=lambda s=sym: self._open_symbol_in_chain(s, auto_load=True),
                accent=C["amber"],
            )

    def _on_bigcap_open(self) -> None:
        choice = self.bigcap_combo.get()
        sym = parse_big_cap_choice(choice)
        if not sym:
            messagebox.showwarning("OptionChain", "Pick a stock from the list.")
            return
        self._open_symbol_in_chain(sym, auto_load=True)

    def _open_symbol_in_chain(self, symbol: str, *, auto_load: bool = True) -> None:
        """Switch to Chain tab and optionally load the symbol."""
        self.tabs.set("  📊  Chain  ")
        sym = symbol.strip().upper()
        self.chain_symbol.delete(0, "end")
        self.chain_symbol.insert(0, sym)
        # Keep big-cap dropdown in sync when possible
        if hasattr(self, "bigcap_combo"):
            match = next(
                (c for c in big_cap_choices() if c.startswith(f"{sym} ")),
                None,
            )
            if match:
                try:
                    self.bigcap_combo.set(match)
                except Exception:
                    pass
        # Sync other tabs' symbol fields for Chart/Compare shortcuts
        if hasattr(self, "hist_symbol"):
            self.hist_symbol.delete(0, "end")
            self.hist_symbol.insert(0, sym)
        if hasattr(self, "cmp_symbol"):
            self.cmp_symbol.delete(0, "end")
            self.cmp_symbol.insert(0, sym)
        if auto_load:
            self._chain_load()

    def _on_chain_type_change(self, _value: str | None = None) -> None:
        if self._suppress_chain_auto or self._chain_busy:
            return
        if self.chain_symbol.get().strip():
            self._chain_load()

    def _on_chain_expiry_change(self, _value: str | None = None) -> None:
        if self._suppress_chain_auto or self._chain_busy:
            return
        if self.chain_symbol.get().strip():
            self._chain_load()

    def _quick_history(self) -> None:
        sym = self.chain_symbol.get().strip() or self._current_symbol
        if not sym:
            messagebox.showwarning("OptionChain", "Enter a stock symbol first.")
            return
        self.tabs.set("  📈  History + Chart  ")
        self.hist_symbol.delete(0, "end")
        self.hist_symbol.insert(0, sym.upper())
        self._history_load()

    def _quick_compare(self) -> None:
        sym = self.chain_symbol.get().strip() or self._current_symbol
        if not sym:
            messagebox.showwarning("OptionChain", "Enter a stock symbol first.")
            return
        self.tabs.set("  ⚖️  ITM vs OTM  ")
        self.cmp_symbol.delete(0, "end")
        self.cmp_symbol.insert(0, sym.upper())
        self._compare_load()

    def _chain_load(self) -> None:
        sym = self.chain_symbol.get().strip()
        if not sym:
            messagebox.showwarning("OptionChain", "Enter a stock symbol.")
            return
        if self._chain_busy:
            return
        otype = self.chain_type_var.get()
        expiry_raw = self.chain_expiry.get().strip() or "Nearest"
        expiry = None if expiry_raw in {"Nearest", "Pick expiry", ""} else expiry_raw
        try:
            near = int(self.chain_near.get().strip() or "8")
        except ValueError:
            near = 8
        smin = self.chain_smin.get().strip()
        smax = self.chain_smax.get().strip()
        strike_min = float(smin) if smin else None
        strike_max = float(smax) if smax else None

        self._chain_busy = True

        def work() -> dict[str, Any]:
            # One shot: expiries + chain
            symbol, name, spot, available = list_expiries(sym)
            if expiry and expiry in available:
                selected = select_expiries(available, expiry=expiry)
            else:
                selected = select_expiries(available, nearest=1)
            data = fetch_option_chain(symbol, expiries=selected)
            if data.spot_price > 0:
                spot = data.spot_price
            pcr = compute_put_call_ratio(data.calls, data.puts)
            filtered = apply_filters(
                data.calls,
                data.puts,
                option_type=otype,
                strike_min=strike_min,
                strike_max=strike_max,
                spot_price=spot,
                near=near if strike_min is None and strike_max is None else None,
            )
            return {
                "symbol": symbol,
                "name": name,
                "spot": spot,
                "currency": data.currency,
                "available": list(available),
                "expiries": selected,
                "pcr": pcr,
                "df": filtered,
            }

        def ok(payload: dict[str, Any]) -> None:
            self._chain_busy = False
            self._current_symbol = payload["symbol"]
            self._recents = _save_recent(payload["symbol"])
            self._render_recents()

            # Refresh expiry dropdown without re-triggering auto-load
            self._suppress_chain_auto = True
            try:
                values = ["Nearest", *payload["available"]]
                self.chain_expiry.configure(values=values)
                chosen = payload["expiries"][0] if payload["expiries"] else "Nearest"
                self.chain_expiry.set(chosen)
            finally:
                self._suppress_chain_auto = False

            pcr = payload["pcr"]
            self.chain_card.set(
                f"{payload['symbol']}  —  {payload['name']}",
                f"Spot  {payload['spot']:,.2f} {payload['currency']}   ·   "
                f"Expiry  {', '.join(payload['expiries'])}   ·   "
                f"{len(payload['df']) if payload['df'] is not None else 0} contracts  ·  "
                f"auto-updated",
            )
            self.chain_card.set_accent(C["green"])
            self._set_pcr_chips(
                pcr.volume_ratio,
                pcr.oi_ratio,
                f"puts {pcr.put_volume:,}  /  calls {pcr.call_volume:,}",
            )
            _tree_clear(self.chain_tree)
            df = payload["df"]
            if df is None or df.empty:
                return
            for i, (_, row) in enumerate(df.iterrows()):
                iv = float(row.get("impliedVolatility", 0) or 0)
                iv_s = f"{iv * 100:.1f}%" if iv > 1e-4 else "—"
                otype_s = str(row.get("type", "")).upper()
                is_call = otype_s == "CALL"
                # Prefer explicit inTheMoney; fall back to type+strike vs spot
                if "inTheMoney" in row.index and row.get("inTheMoney") is not None:
                    is_itm = bool(row.get("inTheMoney"))
                else:
                    strike = float(row.get("strike", 0) or 0)
                    spot = float(payload["spot"] or 0)
                    is_itm = (
                        (is_call and strike <= spot) or (not is_call and strike >= spot)
                    ) if spot > 0 else False
                # Darker (muted) tags for OTM; softer bright for ITM
                if is_call:
                    money_tag = "call_itm" if is_itm else "call_otm"
                else:
                    money_tag = "put_itm" if is_itm else "put_otm"
                tags = (money_tag, "alt") if i % 2 else (money_tag,)
                self.chain_tree.insert(
                    "",
                    "end",
                    values=(
                        row.get("expiry", ""),
                        otype_s,
                        f"{float(row.get('strike', 0)):.2f}",
                        f"{float(row.get('lastPrice', 0)):.2f}",
                        f"{float(row.get('bid', 0)):.2f}"
                        if float(row.get("bid", 0) or 0) > 0
                        else "—",
                        f"{float(row.get('ask', 0)):.2f}"
                        if float(row.get("ask", 0) or 0) > 0
                        else "—",
                        f"{int(row.get('volume', 0) or 0):,}",
                        f"{int(row.get('openInterest', 0) or 0):,}"
                        if int(row.get("openInterest", 0) or 0)
                        else "—",
                        iv_s,
                    ),
                    tags=tags,
                )

        def err(exc: BaseException) -> None:
            self._chain_busy = False
            self.status.stop_busy("Error", ok=False)
            messagebox.showerror("OptionChain", _err_text(exc))

        self.status.start_busy(f"Loading {sym.upper()} (expiries + chain)…")
        self.worker.submit(work, ok, err)

    # ── Top ──────────────────────────────────────────────────
    def _build_top_tab(self) -> None:
        t = self.tab_top
        bar = self._toolbar(t)
        self.top_n = make_entry(bar.fields, width=90, text="20")
        bar.add_field("How many", self.top_n, label_color=C["magenta"])

        def _top_actions(box: ctk.CTkFrame) -> None:
            make_primary_btn(box, "Load leaders", self._top_load, width=130).pack(
                side="left", padx=(0, 10)
            )
            make_accent_btn(
                box,
                "Export TradingView…",
                self._top_export,
                color="#7c3aed",
                hover="#6d28d9",
                width=180,
            ).pack(side="left")

        bar.add_actions(_top_actions)

        self.top_card = InfoCard(t, accent=C["magenta"])
        self.top_card.pack(fill="x", padx=10, pady=4)
        self.top_card.set(
            "Options volume leaders",
            "Highest options activity (Yahoo most-active contracts, rolled up by underlying).",
        )

        _, self.top_tree = self._table_frame(t)
        cols = ("rank", "symbol", "name", "spot", "chg", "opt_vol", "calls", "puts", "pcr")
        self.top_tree["columns"] = cols
        heads = {
            "rank": "#", "symbol": "Symbol", "name": "Name", "spot": "Spot",
            "chg": "Chg%", "opt_vol": "Opt Vol", "calls": "Calls", "puts": "Puts",
            "pcr": "PCR",
        }
        for c in cols:
            self.top_tree.heading(c, text=heads[c])
            w = 200 if c == "name" else (70 if c == "rank" else 95)
            self.top_tree.column(c, width=w, anchor="center")

        self.top_tree.bind("<Double-1>", self._on_top_double_click)
        tip = ctk.CTkLabel(
            t,
            text="💡 Double-click a row to open that symbol in the Chain tab (auto-loads).",
            font=_font(12),
            text_color=C["cyan"],
            anchor="w",
        )
        tip.pack(fill="x", padx=14, pady=(0, 6))

    def _on_top_double_click(self, _event: Any = None) -> None:
        sel = self.top_tree.selection()
        if not sel:
            return
        vals = self.top_tree.item(sel[0], "values")
        if not vals or len(vals) < 2:
            return
        symbol = str(vals[1]).strip()
        if symbol:
            self._open_symbol_in_chain(symbol, auto_load=True)

    def _top_load(self) -> None:
        try:
            n = int(self.top_n.get().strip() or "20")
        except ValueError:
            messagebox.showwarning("OptionChain", "Count must be a number.")
            return

        def work() -> Any:
            return fetch_option_volume_leaders(top_n=n)

        def ok(result: Any) -> None:
            self._leaders_result = result
            self.top_card.set(
                f"Top {len(result.leaders)} underlyings by options volume",
                f"Scanned {result.contracts_scanned:,} contracts · "
                f"{result.unique_underlyings} unique · "
                f"{result.fetched_at.strftime('%Y-%m-%d %H:%M')}  ·  "
                f"double-click a row to open Chain",
            )
            self.top_card.set_accent(C["magenta"])
            _tree_clear(self.top_tree)
            for i, row in enumerate(result.leaders):
                spot = "—" if row.spot_price is None else f"{row.spot_price:,.2f}"
                chg = "—" if row.change_pct is None else f"{row.change_pct:+.2f}%"
                pcr = "—" if row.put_call_ratio is None else f"{row.put_call_ratio:.2f}"
                chg_tag = (
                    "up"
                    if row.change_pct is not None and row.change_pct >= 0
                    else "down"
                    if row.change_pct is not None
                    else ""
                )
                tags = [t for t in (("alt" if i % 2 else ""), chg_tag) if t]
                self.top_tree.insert(
                    "",
                    "end",
                    values=(
                        row.rank,
                        row.symbol,
                        (row.name or "")[:42],
                        spot,
                        chg,
                        f"{row.options_volume:,}",
                        f"{row.call_volume:,}",
                        f"{row.put_volume:,}",
                        pcr,
                    ),
                    tags=tuple(tags),
                )

        self._run(work, ok, busy=f"Loading top {n} options leaders…")

    def _top_export(self) -> None:
        if not self._leaders_result or not self._leaders_result.leaders:
            messagebox.showinfo("OptionChain", "Load leaders first, then export.")
            return
        path = filedialog.asksaveasfilename(
            title="Save TradingView watchlist",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All", "*.*")],
            initialfile="optionchain_tradingview_watchlist.txt",
        )
        if not path:
            return
        try:
            saved = export_tradingview_watchlist(
                self._leaders_result, path=path, with_exchange=True
            )
            messagebox.showinfo(
                "OptionChain",
                f"Saved:\n{saved}\n\n"
                "TradingView → Watchlist → ··· → Import list of symbols",
            )
            self.status.set_message(f"Watchlist exported · {saved}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("OptionChain", _err_text(exc))

    # ── History ──────────────────────────────────────────────
    def _build_history_tab(self) -> None:
        t = self.tab_history
        bar = self._toolbar(t)
        self.hist_symbol = make_entry(bar.fields, width=100, text="SPY")
        self.hist_days = make_entry(bar.fields, width=64, text="5")
        self.hist_type_var = ctk.StringVar(value="all")
        self.hist_type_seg = ctk.CTkSegmentedButton(
            bar.fields,
            values=["all", "call", "put"],
            variable=self.hist_type_var,
            width=200,
            height=34,
            font=_font(12, "bold"),
            fg_color=C["elevated"],
            selected_color=C["green_dim"],
            selected_hover_color=C["green"],
            unselected_color=C["dropdown_bg"],
            unselected_hover_color=C["border"],
            text_color=C["white"],
        )
        self.hist_type_seg.set("all")
        self.hist_near = make_entry(bar.fields, width=64, text="3")
        self.hist_expiry = make_entry(
            bar.fields, width=140, placeholder="Optional YYYY-MM-DD"
        )

        bar.add_field("Symbol", self.hist_symbol, label_color=C["cyan"])
        bar.add_field("Days", self.hist_days)
        bar.add_field("Type", self.hist_type_seg, label_color=C["green"])
        bar.add_field("Near", self.hist_near)
        bar.add_field("Expiry", self.hist_expiry, label_color=C["amber"])

        def _hist_actions(box: ctk.CTkFrame) -> None:
            make_primary_btn(box, "Load + plot", self._history_load, width=130).pack(
                side="left", padx=(0, 10)
            )
            make_secondary_btn(
                box, "Save PNG…", self._history_save_png, width=120
            ).pack(side="left")

        bar.add_actions(_hist_actions)

        # Compact one-line status (avoid a tall empty card eating the plot)
        self.hist_card = InfoCard(t, accent=C["blue"])
        self.hist_card.pack(fill="x", padx=10, pady=(2, 2))
        self.hist_card.set(
            "Multi-day option prices",
            "Left = table · Right = chart  ·  Brighter line = closer to the money.",
        )
        # Prevent status card from expanding into empty vertical space
        try:
            self.hist_card.pack_propagate(True)
        except Exception:
            pass

        # Two columns: table (left) | plot (right) — fill all remaining space
        body = ctk.CTkFrame(t, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        body.grid_columnconfigure(0, weight=2, uniform="hist")
        body.grid_columnconfigure(1, weight=3, uniform="hist")
        body.grid_rowconfigure(0, weight=1)

        table_wrap = ctk.CTkFrame(
            body,
            fg_color=C["card"],
            corner_radius=12,
            border_width=1,
            border_color=C["border"],
        )
        table_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        ctk.CTkLabel(
            table_wrap,
            text="CONTRACT HISTORY",
            font=_font(10, "bold"),
            text_color=C["cyan"],
            anchor="w",
        ).pack(fill="x", padx=10, pady=(6, 0))
        host = tk.Frame(table_wrap, bg=C["card"])
        host.pack(fill="both", expand=True, padx=6, pady=6)
        self.hist_tree = ttk.Treeview(
            host,
            columns=("type", "strike", "d0", "d1", "d2", "d3", "d4", "chg", "pct", "vol"),
            show="headings",
            style="App.Treeview",
            height=22,
        )
        for c, h in [
            ("type", "Type"), ("strike", "Strike"), ("d0", "D1"), ("d1", "D2"),
            ("d2", "D3"), ("d3", "D4"), ("d4", "D5"), ("chg", "Δ $"),
            ("pct", "Δ %"), ("vol", "Vol"),
        ]:
            self.hist_tree.heading(c, text=h)
            self.hist_tree.column(c, width=58, anchor="center")
        sb = ttk.Scrollbar(host, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        self.hist_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        _tag_tree(self.hist_tree)

        self.plot_host = ctk.CTkFrame(
            body,
            fg_color=C["card"],
            corner_radius=12,
            border_width=1,
            border_color=C["border"],
        )
        self.plot_host.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        ctk.CTkLabel(
            self.plot_host,
            text="PRICE CHART  ·  brighter = closer to the money",
            font=_font(10, "bold"),
            text_color=C["cyan"],
            anchor="w",
        ).pack(fill="x", padx=10, pady=(6, 0))
        self.plot_body = ctk.CTkFrame(self.plot_host, fg_color="transparent")
        self.plot_body.pack(fill="both", expand=True, padx=2, pady=2)
        self.plot_placeholder = ctk.CTkLabel(
            self.plot_body,
            text="Load history to plot call (green) and put (red) prices\n"
            "ATM strikes are brightest · farther strikes are dimmer",
            font=_font(13),
            text_color=C["muted"],
            justify="center",
        )
        self.plot_placeholder.pack(expand=True)

    def _clear_plot(self) -> None:
        if self._plot_canvas is not None:
            self._plot_canvas.get_tk_widget().destroy()
            self._plot_canvas = None
        if self._plot_toolbar is not None:
            self._plot_toolbar.destroy()
            self._plot_toolbar = None
        if self._plot_fig is not None:
            try:
                import matplotlib.pyplot as plt

                plt.close(self._plot_fig)
            except Exception:
                pass
            self._plot_fig = None
        body = getattr(self, "plot_body", self.plot_host)
        for child in body.winfo_children():
            child.destroy()

    def _embed_plot(self, fig: Figure) -> None:
        self._clear_plot()
        body = getattr(self, "plot_body", self.plot_host)
        if not _HAS_MPL:
            ctk.CTkLabel(
                body, text="matplotlib Tk backend unavailable.", text_color=C["red"]
            ).pack(expand=True)
            return
        # Dark chart background to match UI
        fig.patch.set_facecolor(C["card"])
        for ax in fig.get_axes():
            ax.set_facecolor("#0f172a")
            ax.tick_params(colors=C["muted"])
            ax.xaxis.label.set_color(C["muted"])
            ax.yaxis.label.set_color(C["muted"])
            ax.title.set_color(C["text"])
            for spine in ax.spines.values():
                spine.set_color(C["border"])
            leg = ax.get_legend()
            if leg is not None:
                leg.get_frame().set_facecolor(C["elevated"])
                leg.get_frame().set_edgecolor(C["border"])
                for text in leg.get_texts():
                    text.set_color(C["text"])
        self._plot_fig = fig
        # Slightly taller option panel for side-by-side column
        try:
            fig.set_size_inches(7.2, 6.2)
        except Exception:
            pass
        canvas = FigureCanvasTkAgg(fig, master=body)
        canvas.draw()
        toolbar_frame = tk.Frame(body, bg=C["elevated"])
        toolbar_frame.pack(side="top", fill="x")
        toolbar = NavigationToolbar2Tk(canvas, toolbar_frame, pack_toolbar=True)
        toolbar.update()
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        self._plot_canvas = canvas
        self._plot_toolbar = toolbar_frame

    def _history_load(self) -> None:
        sym = self.hist_symbol.get().strip()
        if not sym:
            messagebox.showwarning("OptionChain", "Enter a stock symbol.")
            return
        try:
            days = int(self.hist_days.get().strip() or "5")
            near = int(self.hist_near.get().strip() or "3")
        except ValueError:
            messagebox.showwarning("OptionChain", "Days and Near must be numbers.")
            return
        otype = self.hist_type_var.get()
        expiry = self.hist_expiry.get().strip() or None

        def work() -> Any:
            return fetch_chain_history(
                sym, days=days, expiry=expiry, option_type=otype, near=near
            )

        def ok(result: Any) -> None:
            self._history_result = result
            dates = result.trade_dates
            labels = [d.strftime("%m/%d") for d in dates]
            spot_line = ""
            if result.spot_change_pct is not None:
                spot_line = f"  ·  stock Δ {result.spot_change_pct:+.2f}%"
            self.hist_card.set(
                f"{result.symbol}  —  {result.company_name}",
                f"Spot {result.spot_price:,.2f}  ·  expiry {result.expiry}  ·  "
                f"{len(dates)} sessions{spot_line}",
            )
            self.hist_card.set_accent(C["blue"])

            cols = [
                "type",
                "strike",
                *[f"d{i}" for i in range(min(5, len(dates)))],
                "chg",
                "pct",
                "vol",
            ]
            self.hist_tree["columns"] = cols
            heads = {"type": "Type", "strike": "Strike", "chg": "Δ $", "pct": "Δ %", "vol": "Vol"}
            for i, lab in enumerate(labels[:5]):
                heads[f"d{i}"] = lab
            for c in cols:
                self.hist_tree.heading(c, text=heads.get(c, c))
                self.hist_tree.column(c, width=72, anchor="center")

            _tree_clear(self.hist_tree)
            for i, c in enumerate(result.contracts):
                vals: list[Any] = [c.option_type.upper(), f"{c.strike:.2f}"]
                for d in dates[:5]:
                    px = c.close_on(d)
                    vals.append(f"{px:.2f}" if px is not None else "—")
                d_dollar = c.dollar_change
                d_pct = c.percent_change
                vals.append(f"{d_dollar:+.2f}" if d_dollar is not None else "—")
                vals.append(f"{d_pct:+.1f}%" if d_pct is not None else "—")
                last_vol = c.points[-1].volume if c.points else 0
                vals.append(f"{last_vol:,}" if last_vol else "—")
                tag = "call" if c.option_type == "call" else "put"
                tags = (tag, "alt") if i % 2 else (tag,)
                self.hist_tree.insert("", "end", values=tuple(vals), tags=tags)

            try:
                # Larger figure so the right column uses available space
                fig = build_chain_history_figure(result, figsize=(8.5, 7.0))
                self._embed_plot(fig)
            except Exception as exc:  # noqa: BLE001
                self._clear_plot()
                body = getattr(self, "plot_body", self.plot_host)
                ctk.CTkLabel(
                    body,
                    text=f"Plot error: {exc}",
                    text_color=C["red"],
                    font=_font(13),
                ).pack(expand=True)

        self._run(work, ok, busy=f"Loading history for {sym.upper()}…")

    def _history_save_png(self) -> None:
        if not self._history_result:
            messagebox.showinfo("OptionChain", "Load history first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save chart",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("All", "*.*")],
            initialfile=f"optionchain_{self._history_result.symbol}_history.png",
        )
        if not path:
            return
        try:
            saved = save_chain_history_plot(self._history_result, path=path)
            messagebox.showinfo("OptionChain", f"Saved chart:\n{saved}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("OptionChain", _err_text(exc))

    # ── Compare ──────────────────────────────────────────────
    def _build_compare_tab(self) -> None:
        t = self.tab_compare
        bar = self._toolbar(t)
        self.cmp_symbol = make_entry(bar.fields, width=100, text="TSLA")
        self.cmp_side_var = ctk.StringVar(value="call")
        self.cmp_side_seg = ctk.CTkSegmentedButton(
            bar.fields,
            values=["call", "put"],
            variable=self.cmp_side_var,
            width=140,
            height=34,
            font=_font(12, "bold"),
            fg_color=C["elevated"],
            selected_color=C["green_dim"],
            selected_hover_color=C["green"],
            unselected_color=C["dropdown_bg"],
            unselected_hover_color=C["border"],
            text_color=C["white"],
        )
        self.cmp_side_seg.set("call")
        self.cmp_expiry = make_entry(
            bar.fields, width=130, placeholder="Optional expiry"
        )
        self.cmp_move = make_entry(bar.fields, width=80, placeholder="e.g. 5")
        self.cmp_budget = make_entry(bar.fields, width=90, placeholder="e.g. 500")
        self.cmp_ifspot = make_entry(bar.fields, width=90, placeholder="Price")

        bar.add_field("Symbol", self.cmp_symbol, label_color=C["cyan"])
        bar.add_field("Side", self.cmp_side_seg, label_color=C["green"])
        bar.add_field("Expiry", self.cmp_expiry, label_color=C["amber"])
        bar.add_field("Target move %", self.cmp_move)
        bar.add_field("Budget $", self.cmp_budget)
        bar.add_field("If spot", self.cmp_ifspot)

        def _cmp_actions(box: ctk.CTkFrame) -> None:
            make_primary_btn(
                box, "Compare styles", self._compare_load, width=150
            ).pack(side="left")

        bar.add_actions(_cmp_actions)

        self.cmp_card = InfoCard(t, accent=C["amber"])
        self.cmp_card.pack(fill="x", padx=10, pady=4)
        self.cmp_card.set(
            "ITM vs OTM research",
            "Deep ITM → Far OTM · green styles lean conservative · red lean aggressive.",
        )

        guide_wrap = ctk.CTkFrame(
            t, fg_color=C["card"], corner_radius=10, border_width=1, border_color=C["border"]
        )
        guide_wrap.pack(fill="x", padx=10, pady=(0, 4))
        self.cmp_guide = ctk.CTkTextbox(
            guide_wrap,
            height=110,
            font=_font(12),
            fg_color=C["card"],
            text_color=C["text"],
            border_width=0,
            wrap="word",
        )
        self.cmp_guide.pack(fill="x", padx=8, pady=8)
        self.cmp_guide.insert(
            "1.0",
            "Compare long call/put styles for the same expiry.\n"
            "ITM costs more but needs less move · OTM is cheaper leverage but can expire worthless.",
        )
        self.cmp_guide.configure(state="disabled")

        _, self.cmp_tree = self._table_frame(t)
        cols = (
            "style", "strike", "mny", "last", "intr", "extr", "be", "move",
            "prem", "lev", "vol", "oi",
        )
        self.cmp_tree["columns"] = cols
        heads = {
            "style": "Style", "strike": "Strike", "mny": "Moneyness", "last": "Last",
            "intr": "Intr.", "extr": "Extr.", "be": "BE", "move": "Move→BE",
            "prem": "$/ctr", "lev": "Lev~", "vol": "Vol", "oi": "OI",
        }
        for c in cols:
            self.cmp_tree.heading(c, text=heads[c])
            self.cmp_tree.column(c, width=82, anchor="center")

    def _compare_load(self) -> None:
        sym = self.cmp_symbol.get().strip()
        if not sym:
            messagebox.showwarning("OptionChain", "Enter a stock symbol.")
            return
        side = self.cmp_side_var.get()
        expiry = self.cmp_expiry.get().strip() or None

        def _opt_float(entry: ctk.CTkEntry) -> float | None:
            t = entry.get().strip()
            if not t:
                return None
            return float(t)

        try:
            target = _opt_float(self.cmp_move)
            budget = _opt_float(self.cmp_budget)
            if_spot = _opt_float(self.cmp_ifspot)
        except ValueError:
            messagebox.showwarning("OptionChain", "Numeric fields must be numbers.")
            return

        def work() -> Any:
            return compare_itm_otm(
                sym,
                option_type=side,  # type: ignore[arg-type]
                expiry=expiry,
                target_move_pct=target,
                budget=budget,
                if_spot=if_spot,
                style_picks_only=True,
            )

        def ok(result: Any) -> None:
            accent = C["green"] if result.option_type == "call" else C["red"]
            self.cmp_card.set_accent(accent)
            self.cmp_card.set(
                f"{result.symbol}  —  LONG {result.option_type.upper()}",
                f"{result.company_name}  ·  spot {result.spot_price:,.2f}  ·  "
                f"expiry {result.expiry}"
                + (f" ({result.dte}d)" if result.dte is not None else ""),
            )
            style_names = {
                "deep_itm": "Deep ITM",
                "itm": "ITM",
                "atm": "ATM",
                "otm": "OTM",
                "far_otm": "Far OTM",
            }
            _tree_clear(self.cmp_tree)
            for i, r in enumerate(result.rows):
                if r.bucket in {"deep_itm", "itm"}:
                    tag = "itm"
                elif r.bucket == "atm":
                    tag = "atm"
                else:
                    tag = "otm"
                tags = (tag, "alt") if i % 2 else (tag,)
                self.cmp_tree.insert(
                    "",
                    "end",
                    values=(
                        style_names.get(r.bucket, r.bucket),
                        f"{r.strike:.2f}",
                        r.moneyness_label,
                        f"{r.last:.2f}" if r.last > 0 else f"{r.mid:.2f}",
                        f"{r.intrinsic:.2f}",
                        f"{r.extrinsic:.2f}",
                        f"{r.break_even:.2f}",
                        f"{r.move_to_be_pct:+.1f}%",
                        f"{r.premium_per_contract:,.0f}",
                        f"{r.leverage_proxy:.1f}×" if r.leverage_proxy else "—",
                        f"{r.volume:,}" if r.volume else "—",
                        f"{r.open_interest:,}" if r.open_interest else "—",
                    ),
                    tags=tags,
                )
            self.cmp_guide.configure(state="normal")
            self.cmp_guide.delete("1.0", "end")
            self.cmp_guide.insert(
                "1.0", "\n".join(f"•  {line}" for line in style_guidance(result))
            )
            self.cmp_guide.configure(state="disabled")

        self._run(work, ok, busy=f"Comparing {side}s on {sym.upper()}…")

    # ── Help ─────────────────────────────────────────────────
    def _build_help_tab(self) -> None:
        t = self.tab_help
        wrap = ctk.CTkFrame(
            t, fg_color=C["card"], corner_radius=12, border_width=1, border_color=C["border"]
        )
        wrap.pack(fill="both", expand=True, padx=10, pady=10)
        box = ctk.CTkTextbox(
            wrap,
            wrap="word",
            font=_font(13),
            fg_color=C["card"],
            text_color=C["text"],
            border_width=0,
        )
        box.pack(fill="both", expand=True, padx=14, pady=14)
        box.insert(
            "1.0",
            f"""OptionChain GUI  v{__version__}

Automation
──────────
  • Big-cap list preloaded (dropdown + chips) — one click loads chain
  • Chain: one Load button (expiries + chain together)
  • Press Enter in Symbol to load
  • Changing Type or Expiry reloads automatically
  • Double-click a Top Volume row → opens Chain for that ticker
  • Chart 5d / Compare shortcuts use the current symbol
  • Recents chips under Chain for one-click reloads
  • Filters ▸ hides Near ATM / strike range until you need them

Color guide
───────────
  ● Green   CALL rows / ITM styles / up moves
  ● Red     PUT rows / OTM styles / down moves
  ● Amber   ATM / balanced
  ● Cyan    headers, primary actions
  ● Magenta PCR & volume leaders

Tabs
────
  Chain          Live option chain + put/call ratio chips
  Top Volume     Busiest underlyings → Export TradingView watchlist
  History+Chart  Multi-day prices + interactive green/red plot
  ITM vs OTM     Research table for long call/put styles

Happy path
──────────
  1. Top Volume → Load leaders → double-click NVDA
  2. Chain auto-fills → tweak type/expiry if needed
  3. Chart 5d or Compare for the same symbol
  4. Export TradingView from Top when ready

{DISCLAIMER}

CLI twin:
  uv run optionchain --help
  uv run optionchain-gui
""",
        )
        box.configure(state="disabled")


def run_gui() -> None:
    app = OptionChainApp()
    app.mainloop()


def main() -> None:
    run_gui()


if __name__ == "__main__":
    main()
