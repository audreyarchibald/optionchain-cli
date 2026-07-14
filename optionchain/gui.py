"""
Desktop GUI for optionchain — every CLI feature, with embedded charts.

Run:
  uv run optionchain-gui
  python -m optionchain.gui
"""

from __future__ import annotations

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
from optionchain.fetcher import OptionChainError, fetch_option_chain, list_expiries
from optionchain.filters import apply_filters, select_expiries
from optionchain.history import fetch_chain_history
from optionchain.leaders import fetch_option_volume_leaders
from optionchain.metrics import compute_put_call_ratio
from optionchain.plotting import build_chain_history_figure, save_chain_history_plot
from optionchain.watchlist import export_tradingview_watchlist

# Optional matplotlib backend for embedding
try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure

    _HAS_MPL = True
except Exception:  # pragma: no cover
    _HAS_MPL = False


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

DISCLAIMER = (
    "Research tool only — not financial advice. Data from Yahoo Finance via yfinance."
)


class Worker:
    """Run blocking work off the UI thread; deliver results via a queue."""

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._poll()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "ok":
                    callback, result = payload
                    callback(result)
                elif kind == "err":
                    callback, err = payload
                    callback(err)
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
                result = fn()
                self.q.put(("ok", (on_ok, result)))
            except BaseException as exc:  # noqa: BLE001 — surface to UI
                self.q.put(("err", (on_err, exc)))

        threading.Thread(target=runner, daemon=True).start()


def _err_text(exc: BaseException) -> str:
    if isinstance(exc, OptionChainError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _tree_clear(tree: ttk.Treeview) -> None:
    for item in tree.get_children():
        tree.delete(item)


def _style_treeview() -> None:
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Treeview",
        background="#1e1e1e",
        foreground="#e8e8e8",
        fieldbackground="#1e1e1e",
        rowheight=26,
        borderwidth=0,
        font=("SF Pro Text", 12) if ctk.get_appearance_mode() else ("Segoe UI", 11),
    )
    style.configure(
        "Treeview.Heading",
        background="#2b2b2b",
        foreground="#ffffff",
        relief="flat",
        font=("SF Pro Text", 12, "bold"),
    )
    style.map("Treeview", background=[("selected", "#1f6aa5")])


class StatusBar(ctk.CTkFrame):
    def __init__(self, master: Any, **kwargs: Any) -> None:
        super().__init__(master, height=32, **kwargs)
        self.label = ctk.CTkLabel(self, text=DISCLAIMER, anchor="w")
        self.label.pack(side="left", fill="x", expand=True, padx=10, pady=4)
        self.busy = ctk.CTkProgressBar(self, width=120, mode="indeterminate")
        self._busy = False

    def set_message(self, text: str) -> None:
        self.label.configure(text=text)

    def start_busy(self, text: str = "Loading…") -> None:
        self.set_message(text)
        if not self._busy:
            self.busy.pack(side="right", padx=10, pady=6)
            self.busy.start()
            self._busy = True

    def stop_busy(self, text: str | None = None) -> None:
        if self._busy:
            self.busy.stop()
            self.busy.pack_forget()
            self._busy = False
        if text is not None:
            self.set_message(text)


class OptionChainApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"OptionChain  v{__version__}")
        self.geometry("1180x780")
        self.minsize(960, 640)

        self.worker = Worker(self)
        _style_treeview()

        # last results for export / re-plot
        self._leaders_result = None
        self._history_result = None
        self._plot_canvas: Any = None
        self._plot_toolbar: Any = None
        self._plot_fig: Figure | None = None

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            header,
            text="OptionChain",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="  Chains · Top volume · History charts · ITM vs OTM",
            text_color=("gray40", "gray70"),
        ).pack(side="left", padx=8)

        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=8)

        self.tab_chain = self.tabs.add("Option Chain")
        self.tab_top = self.tabs.add("Top Volume")
        self.tab_history = self.tabs.add("History + Chart")
        self.tab_compare = self.tabs.add("ITM vs OTM")
        self.tab_help = self.tabs.add("Help")

        self._build_chain_tab()
        self._build_top_tab()
        self._build_history_tab()
        self._build_compare_tab()
        self._build_help_tab()

        self.status = StatusBar(self)
        self.status.pack(fill="x", side="bottom")

    # ── helpers ──────────────────────────────────────────────
    def _run(
        self,
        fn: Callable[[], Any],
        on_ok: Callable[[Any], None],
        busy: str = "Loading…",
    ) -> None:
        self.status.start_busy(busy)

        def ok(result: Any) -> None:
            self.status.stop_busy("Ready.")
            on_ok(result)

        def err(exc: BaseException) -> None:
            self.status.stop_busy("Error.")
            messagebox.showerror("OptionChain", _err_text(exc))

        self.worker.submit(fn, ok, err)

    # ── Tab: Option Chain ────────────────────────────────────
    def _build_chain_tab(self) -> None:
        t = self.tab_chain
        controls = ctk.CTkFrame(t)
        controls.pack(fill="x", padx=8, pady=8)

        self.chain_symbol = ctk.CTkEntry(controls, width=100, placeholder_text="TSLA")
        self.chain_symbol.insert(0, "TSLA")
        self.chain_type = ctk.CTkOptionMenu(
            controls, values=["all", "call", "put"], width=90
        )
        self.chain_type.set("all")
        self.chain_expiry = ctk.CTkComboBox(controls, values=[""], width=130)
        self.chain_expiry.set("")
        self.chain_near = ctk.CTkEntry(controls, width=60, placeholder_text="8")
        self.chain_near.insert(0, "8")
        self.chain_smin = ctk.CTkEntry(controls, width=70, placeholder_text="min")
        self.chain_smax = ctk.CTkEntry(controls, width=70, placeholder_text="max")

        def add(lbl: str, w: Any) -> None:
            ctk.CTkLabel(controls, text=lbl).pack(side="left", padx=(8, 2))
            w.pack(side="left", padx=2)

        add("Symbol", self.chain_symbol)
        add("Type", self.chain_type)
        add("Expiry", self.chain_expiry)
        add("Near", self.chain_near)
        add("Strike≥", self.chain_smin)
        add("Strike≤", self.chain_smax)

        ctk.CTkButton(
            controls, text="Load expiries", width=110, command=self._chain_load_expiries
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            controls, text="Load chain", width=110, fg_color="#1f6aa5",
            command=self._chain_load,
        ).pack(side="left", padx=4)

        self.chain_info = ctk.CTkLabel(t, text="Enter a ticker and load the chain.", anchor="w")
        self.chain_info.pack(fill="x", padx=12)

        self.chain_pcr = ctk.CTkLabel(t, text="", anchor="w", justify="left")
        self.chain_pcr.pack(fill="x", padx=12, pady=(0, 4))

        frame = ctk.CTkFrame(t)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        cols = (
            "expiry", "type", "strike", "last", "bid", "ask", "vol", "oi", "iv", "itm",
        )
        self.chain_tree = ttk.Treeview(frame, columns=cols, show="headings", height=18)
        headings = {
            "expiry": "Expiry",
            "type": "Type",
            "strike": "Strike",
            "last": "Last",
            "bid": "Bid",
            "ask": "Ask",
            "vol": "Vol",
            "oi": "OI",
            "iv": "IV",
            "itm": "ITM",
        }
        widths = {
            "expiry": 100, "type": 55, "strike": 80, "last": 70, "bid": 70,
            "ask": 70, "vol": 80, "oi": 80, "iv": 70, "itm": 45,
        }
        for c in cols:
            self.chain_tree.heading(c, text=headings[c])
            self.chain_tree.column(c, width=widths[c], anchor="center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.chain_tree.yview)
        self.chain_tree.configure(yscrollcommand=sb.set)
        self.chain_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _chain_load_expiries(self) -> None:
        sym = self.chain_symbol.get().strip()
        if not sym:
            messagebox.showwarning("OptionChain", "Enter a stock symbol.")
            return

        def work() -> list[str]:
            _s, _n, _p, expiries = list_expiries(sym)
            return list(expiries)

        def ok(expiries: list[str]) -> None:
            self.chain_expiry.configure(values=expiries or [""])
            if expiries:
                self.chain_expiry.set(expiries[0])
            self.chain_info.configure(
                text=f"{sym.upper()}: {len(expiries)} expiries loaded. Pick one and Load chain."
            )

        self._run(work, ok, busy=f"Loading expiries for {sym.upper()}…")

    def _chain_load(self) -> None:
        sym = self.chain_symbol.get().strip()
        if not sym:
            messagebox.showwarning("OptionChain", "Enter a stock symbol.")
            return
        otype = self.chain_type.get()
        expiry = self.chain_expiry.get().strip() or None
        try:
            near = int(self.chain_near.get().strip() or "8")
        except ValueError:
            near = 8
        smin = self.chain_smin.get().strip()
        smax = self.chain_smax.get().strip()
        strike_min = float(smin) if smin else None
        strike_max = float(smax) if smax else None

        def work() -> dict[str, Any]:
            symbol, name, spot, available = list_expiries(sym)
            if expiry:
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
                "expiries": selected,
                "pcr": pcr,
                "df": filtered,
            }

        def ok(payload: dict[str, Any]) -> None:
            pcr = payload["pcr"]
            vr = "n/a" if pcr.volume_ratio is None else f"{pcr.volume_ratio:.3f}"
            oi = "n/a" if pcr.oi_ratio is None else f"{pcr.oi_ratio:.3f}"
            self.chain_info.configure(
                text=(
                    f"{payload['symbol']} — {payload['name']}  |  "
                    f"spot {payload['spot']:,.2f} {payload['currency']}  |  "
                    f"expiries: {', '.join(payload['expiries'])}"
                )
            )
            self.chain_pcr.configure(
                text=(
                    f"Put/Call ratio — volume: {vr}  "
                    f"(puts {pcr.put_volume:,} / calls {pcr.call_volume:,})   ·   "
                    f"OI: {oi}"
                )
            )
            _tree_clear(self.chain_tree)
            df = payload["df"]
            if df is None or df.empty:
                return
            for _, row in df.iterrows():
                iv = row.get("impliedVolatility", 0) or 0
                iv_s = f"{float(iv) * 100:.1f}%" if float(iv) > 1e-4 else "—"
                self.chain_tree.insert(
                    "",
                    "end",
                    values=(
                        row.get("expiry", ""),
                        str(row.get("type", "")).upper(),
                        f"{float(row.get('strike', 0)):.2f}",
                        f"{float(row.get('lastPrice', 0)):.2f}",
                        f"{float(row.get('bid', 0)):.2f}" if float(row.get("bid", 0) or 0) > 0 else "—",
                        f"{float(row.get('ask', 0)):.2f}" if float(row.get("ask", 0) or 0) > 0 else "—",
                        f"{int(row.get('volume', 0) or 0):,}",
                        f"{int(row.get('openInterest', 0) or 0):,}"
                        if int(row.get("openInterest", 0) or 0)
                        else "—",
                        iv_s,
                        "✓" if bool(row.get("inTheMoney")) else "",
                    ),
                )

        self._run(work, ok, busy=f"Loading chain for {sym.upper()}…")

    # ── Tab: Top Volume ──────────────────────────────────────
    def _build_top_tab(self) -> None:
        t = self.tab_top
        controls = ctk.CTkFrame(t)
        controls.pack(fill="x", padx=8, pady=8)
        ctk.CTkLabel(controls, text="How many").pack(side="left", padx=(8, 4))
        self.top_n = ctk.CTkEntry(controls, width=70)
        self.top_n.insert(0, "20")
        self.top_n.pack(side="left")
        ctk.CTkButton(
            controls, text="Load leaders", fg_color="#1f6aa5", command=self._top_load
        ).pack(side="left", padx=10)
        ctk.CTkButton(
            controls, text="Export TradingView watchlist…", command=self._top_export
        ).pack(side="left", padx=4)

        self.top_info = ctk.CTkLabel(
            t, text="Top underlyings by options trading volume (Yahoo most-active).",
            anchor="w",
        )
        self.top_info.pack(fill="x", padx=12)

        frame = ctk.CTkFrame(t)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        cols = ("rank", "symbol", "name", "spot", "chg", "opt_vol", "calls", "puts", "pcr")
        self.top_tree = ttk.Treeview(frame, columns=cols, show="headings")
        heads = {
            "rank": "#", "symbol": "Symbol", "name": "Name", "spot": "Spot",
            "chg": "Chg%", "opt_vol": "Opt Vol", "calls": "Calls", "puts": "Puts",
            "pcr": "PCR",
        }
        for c in cols:
            self.top_tree.heading(c, text=heads[c])
            self.top_tree.column(c, width=90 if c != "name" else 180, anchor="center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.top_tree.yview)
        self.top_tree.configure(yscrollcommand=sb.set)
        self.top_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

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
            self.top_info.configure(
                text=(
                    f"Top {len(result.leaders)} · scanned {result.contracts_scanned:,} "
                    f"contracts · {result.unique_underlyings} underlyings · "
                    f"{result.fetched_at.strftime('%Y-%m-%d %H:%M')}"
                )
            )
            _tree_clear(self.top_tree)
            for row in result.leaders:
                spot = "—" if row.spot_price is None else f"{row.spot_price:,.2f}"
                chg = "—" if row.change_pct is None else f"{row.change_pct:+.2f}%"
                pcr = "—" if row.put_call_ratio is None else f"{row.put_call_ratio:.2f}"
                self.top_tree.insert(
                    "",
                    "end",
                    values=(
                        row.rank,
                        row.symbol,
                        (row.name or "")[:40],
                        spot,
                        chg,
                        f"{row.options_volume:,}",
                        f"{row.call_volume:,}",
                        f"{row.put_volume:,}",
                        pcr,
                    ),
                )

        self._run(work, ok, busy=f"Loading top {n} options leaders…")

    def _top_export(self) -> None:
        if not self._leaders_result or not self._leaders_result.leaders:
            messagebox.showinfo(
                "OptionChain", "Load leaders first, then export a watchlist."
            )
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
            self.status.set_message(f"Watchlist exported: {saved}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("OptionChain", _err_text(exc))

    # ── Tab: History + Chart ─────────────────────────────────
    def _build_history_tab(self) -> None:
        t = self.tab_history
        controls = ctk.CTkFrame(t)
        controls.pack(fill="x", padx=8, pady=8)

        self.hist_symbol = ctk.CTkEntry(controls, width=100, placeholder_text="SPY")
        self.hist_symbol.insert(0, "SPY")
        self.hist_days = ctk.CTkEntry(controls, width=50)
        self.hist_days.insert(0, "5")
        self.hist_type = ctk.CTkOptionMenu(
            controls, values=["all", "call", "put"], width=90
        )
        self.hist_type.set("all")
        self.hist_near = ctk.CTkEntry(controls, width=50)
        self.hist_near.insert(0, "3")
        self.hist_expiry = ctk.CTkEntry(controls, width=110, placeholder_text="optional")

        for lbl, w in [
            ("Symbol", self.hist_symbol),
            ("Days", self.hist_days),
            ("Type", self.hist_type),
            ("Near", self.hist_near),
            ("Expiry", self.hist_expiry),
        ]:
            ctk.CTkLabel(controls, text=lbl).pack(side="left", padx=(8, 2))
            w.pack(side="left", padx=2)

        ctk.CTkButton(
            controls, text="Load + plot", fg_color="#1f6aa5", command=self._history_load
        ).pack(side="left", padx=10)
        ctk.CTkButton(
            controls, text="Save chart PNG…", command=self._history_save_png
        ).pack(side="left", padx=4)

        self.hist_info = ctk.CTkLabel(t, text="", anchor="w")
        self.hist_info.pack(fill="x", padx=12)

        # split: table top, chart bottom
        paned = tk.PanedWindow(t, orient=tk.VERTICAL, sashwidth=6, bg="#2b2b2b")
        paned.pack(fill="both", expand=True, padx=8, pady=8)

        table_frame = ctk.CTkFrame(paned)
        cols = ("type", "strike", "d0", "d1", "d2", "d3", "d4", "chg", "pct", "vol")
        self.hist_tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=8)
        for c in cols:
            self.hist_tree.heading(c, text=c.upper())
            self.hist_tree.column(c, width=70, anchor="center")
        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        self.hist_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        paned.add(table_frame, height=200)

        self.plot_host = ctk.CTkFrame(paned)
        paned.add(self.plot_host)
        self.plot_placeholder = ctk.CTkLabel(
            self.plot_host,
            text="Load history to see call (green) / put (red) price chart.",
            text_color="gray60",
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
        for child in self.plot_host.winfo_children():
            child.destroy()

    def _embed_plot(self, fig: Figure) -> None:
        self._clear_plot()
        if not _HAS_MPL:
            ctk.CTkLabel(
                self.plot_host, text="matplotlib Tk backend unavailable."
            ).pack(expand=True)
            return
        self._plot_fig = fig
        canvas = FigureCanvasTkAgg(fig, master=self.plot_host)
        canvas.draw()
        toolbar = NavigationToolbar2Tk(canvas, self.plot_host, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="top", fill="x")
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        self._plot_canvas = canvas
        self._plot_toolbar = toolbar

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
        otype = self.hist_type.get()
        expiry = self.hist_expiry.get().strip() or None

        def work() -> Any:
            return fetch_chain_history(
                sym,
                days=days,
                expiry=expiry,
                option_type=otype,
                near=near,
            )

        def ok(result: Any) -> None:
            self._history_result = result
            dates = result.trade_dates
            labels = [d.strftime("%m/%d") for d in dates]
            # dynamic headings for up to 5 date cols we show
            self.hist_info.configure(
                text=(
                    f"{result.symbol} — {result.company_name}  |  "
                    f"spot {result.spot_price:,.2f}  |  expiry {result.expiry}  |  "
                    f"{len(dates)} sessions"
                    + (
                        f"  |  stock Δ {result.spot_change_pct:+.2f}%"
                        if result.spot_change_pct is not None
                        else ""
                    )
                )
            )
            # rebuild columns for dates
            cols = ["type", "strike", *[f"d{i}" for i in range(min(5, len(dates)))], "chg", "pct", "vol"]
            self.hist_tree["columns"] = cols
            heads = {"type": "Type", "strike": "Strike", "chg": "Δ $", "pct": "Δ %", "vol": "Vol"}
            for i, lab in enumerate(labels[:5]):
                heads[f"d{i}"] = lab
            for c in cols:
                self.hist_tree.heading(c, text=heads.get(c, c))
                self.hist_tree.column(c, width=72, anchor="center")

            _tree_clear(self.hist_tree)
            for c in result.contracts:
                vals: list[Any] = [c.option_type.upper(), f"{c.strike:.2f}"]
                for d in dates[:5]:
                    px = c.close_on(d)
                    vals.append(f"{px:.2f}" if px is not None else "—")
                # pad if fewer than 5 date cols expected - already sized to len
                d_dollar = c.dollar_change
                d_pct = c.percent_change
                vals.append(f"{d_dollar:+.2f}" if d_dollar is not None else "—")
                vals.append(f"{d_pct:+.1f}%" if d_pct is not None else "—")
                last_vol = c.points[-1].volume if c.points else 0
                vals.append(f"{last_vol:,}" if last_vol else "—")
                self.hist_tree.insert("", "end", values=tuple(vals))

            try:
                fig = build_chain_history_figure(result, figsize=(9.5, 5.5))
                self._embed_plot(fig)
            except Exception as exc:  # noqa: BLE001
                self._clear_plot()
                ctk.CTkLabel(
                    self.plot_host, text=f"Plot error: {exc}", text_color="tomato"
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

    # ── Tab: Compare ─────────────────────────────────────────
    def _build_compare_tab(self) -> None:
        t = self.tab_compare
        controls = ctk.CTkFrame(t)
        controls.pack(fill="x", padx=8, pady=8)

        self.cmp_symbol = ctk.CTkEntry(controls, width=100)
        self.cmp_symbol.insert(0, "TSLA")
        self.cmp_side = ctk.CTkOptionMenu(controls, values=["call", "put"], width=90)
        self.cmp_side.set("call")
        self.cmp_expiry = ctk.CTkEntry(controls, width=110, placeholder_text="optional")
        self.cmp_move = ctk.CTkEntry(controls, width=60, placeholder_text="e.g. 5")
        self.cmp_budget = ctk.CTkEntry(controls, width=70, placeholder_text="e.g. 500")
        self.cmp_ifspot = ctk.CTkEntry(controls, width=80, placeholder_text="price")

        for lbl, w in [
            ("Symbol", self.cmp_symbol),
            ("Side", self.cmp_side),
            ("Expiry", self.cmp_expiry),
            ("Target move %", self.cmp_move),
            ("Budget $", self.cmp_budget),
            ("If spot", self.cmp_ifspot),
        ]:
            ctk.CTkLabel(controls, text=lbl).pack(side="left", padx=(6, 2))
            w.pack(side="left", padx=2)

        ctk.CTkButton(
            controls, text="Compare styles", fg_color="#1f6aa5", command=self._compare_load
        ).pack(side="left", padx=10)

        self.cmp_info = ctk.CTkLabel(t, text="", anchor="w")
        self.cmp_info.pack(fill="x", padx=12)
        self.cmp_guide = ctk.CTkTextbox(t, height=120)
        self.cmp_guide.pack(fill="x", padx=12, pady=4)
        self.cmp_guide.insert("1.0", "Compare long call/put styles: Deep ITM → Far OTM.")
        self.cmp_guide.configure(state="disabled")

        frame = ctk.CTkFrame(t)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        cols = (
            "style", "strike", "mny", "last", "intr", "extr", "be", "move",
            "prem", "lev", "vol", "oi",
        )
        self.cmp_tree = ttk.Treeview(frame, columns=cols, show="headings")
        heads = {
            "style": "Style", "strike": "Strike", "mny": "Moneyness", "last": "Last",
            "intr": "Intr.", "extr": "Extr.", "be": "BE", "move": "Move→BE",
            "prem": "$/ctr", "lev": "Lev~", "vol": "Vol", "oi": "OI",
        }
        for c in cols:
            self.cmp_tree.heading(c, text=heads[c])
            self.cmp_tree.column(c, width=78, anchor="center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.cmp_tree.yview)
        self.cmp_tree.configure(yscrollcommand=sb.set)
        self.cmp_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _compare_load(self) -> None:
        sym = self.cmp_symbol.get().strip()
        if not sym:
            messagebox.showwarning("OptionChain", "Enter a stock symbol.")
            return
        side = self.cmp_side.get()
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
            self.cmp_info.configure(
                text=(
                    f"{result.symbol} — {result.company_name}  |  "
                    f"spot {result.spot_price:,.2f}  |  long {result.option_type.upper()}  |  "
                    f"expiry {result.expiry}"
                    + (f" ({result.dte}d)" if result.dte is not None else "")
                )
            )
            style_names = {
                "deep_itm": "Deep ITM",
                "itm": "ITM",
                "atm": "ATM",
                "otm": "OTM",
                "far_otm": "Far OTM",
            }
            _tree_clear(self.cmp_tree)
            for r in result.rows:
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
                )
            self.cmp_guide.configure(state="normal")
            self.cmp_guide.delete("1.0", "end")
            self.cmp_guide.insert("1.0", "\n".join(f"• {line}" for line in style_guidance(result)))
            self.cmp_guide.configure(state="disabled")

        self._run(work, ok, busy=f"Comparing {side}s on {sym.upper()}…")

    # ── Help ─────────────────────────────────────────────────
    def _build_help_tab(self) -> None:
        t = self.tab_help
        box = ctk.CTkTextbox(t, wrap="word")
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert(
            "1.0",
            f"""OptionChain GUI  v{__version__}

Tabs
────
• Option Chain — live chain for a ticker (type, expiry, near-ATM strikes, PCR)
• Top Volume — stocks/ETFs with the highest options volume; export TradingView watchlist
• History + Chart — multi-day option closes + interactive call/put chart (green/red)
• ITM vs OTM — research table for long call/put styles (not a buy recommendation)

Tips
────
1. Start with Top Volume to see what’s active, then Export for TradingView.
2. Use Option Chain with “Load expiries” then pick a date.
3. History + Chart: green = calls, red = puts, strike labels on the lines.
4. ITM vs OTM: use Target move % and Budget $ to filter realistic contracts.

Data source: Yahoo Finance (yfinance). Free / delayed — research quality only.

{DISCLAIMER}

CLI (same features):
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
