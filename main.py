"""
main.py — Entry point for the paper trading bot system.

Starts three strategy bots driven by WebSocket kline close events.
A background thread polls SL/TP every SL_TP_CHECK_SECS seconds.

Features
  - Per-bot daily-loss guard (pauses new entries; SL/TP still active)
  - Rich terminal dashboard (auto-refreshes every second)
  - Graceful shutdown on Ctrl+C with final stats summary
  - Optional web dashboard: pass --with-dashboard to enable (port 7000)
"""

import argparse
import logging
import signal
import threading
import time

from rich.console import Console
from rich.table import Table
from rich import box

import config
from bot_trend_pullback import TrendPullbackBot
from bot_macd           import MACDBot
from bot_rsi_vwap       import RSIVWAPBot
from bot_cvd            import CVDBot
from portfolio         import portfolio_summary
from portfolio_risk    import PortfolioRiskManager
from terminal_dashboard import Dashboard
from kline_buffer      import KlineBuffer
from kline_stream      import KlineStreamManager
from universe_scanner  import UniverseManager

console = Console()
log = logging.getLogger("Main")


# ── Startup summary ───────────────────────────────────────────────────────────

def _print_startup_summary(bots, symbols: list[str]) -> None:
    tbl = Table(
        title="[bold cyan]Paper Trading System — Startup[/bold cyan]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    tbl.add_column("Bot",        style="bold white",  min_width=12)
    tbl.add_column("Allocation", justify="right")
    tbl.add_column("Balance",    justify="right")
    tbl.add_column("Timeframe",  justify="center")
    tbl.add_column("Symbols",    justify="left")

    for bot in bots:
        alloc_pct = config.BOT_ALLOCATIONS.get(bot.name, 0) * 100
        tf        = config.TIMEFRAMES.get(bot.name, "?")
        tbl.add_row(
            bot.name,
            f"{alloc_pct:.0f}%",
            f"{bot.balance:.2f} USDT",
            tf,
            ", ".join(symbols),
        )

    console.print()
    console.print(tbl)
    console.print(
        f"  [yellow]Total capital:[/yellow] {config.INITIAL_BALANCE:,.2f} USDT  |  "
        f"[yellow]Paper trading:[/yellow] {'YES' if config.PAPER_TRADING else 'NO — DANGER'}\n"
    )


# ── Final stats ───────────────────────────────────────────────────────────────

def _print_final_stats(bots) -> None:
    console.print("\n[bold yellow]──── Final Performance ────[/bold yellow]")
    tbl = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    tbl.add_column("Bot",        style="bold white", min_width=10)
    tbl.add_column("Trades",     justify="right")
    tbl.add_column("Win %",      justify="right")
    tbl.add_column("P&L (USDT)", justify="right")
    tbl.add_column("Return",     justify="right")
    tbl.add_column("Sharpe",     justify="right")
    tbl.add_column("Balance",    justify="right")

    total_pnl = 0.0
    total_bal = 0.0
    for bot in bots:
        s = bot.get_stats()
        pnl_col = f"[{'green' if s['total_pnl'] >= 0 else 'red'}]{s['total_pnl']:+.4f}[/]"
        ret_col = f"[{'green' if s['return_pct'] >= 0 else 'red'}]{s['return_pct']:+.2f}%[/]"
        tbl.add_row(
            s["bot"],
            str(s["trades"]),
            f"{s['win_rate']:.1f}%",
            pnl_col,
            ret_col,
            f"{s['sharpe']:.2f}",
            f"{s['balance']:.2f}",
        )
        total_pnl += s["total_pnl"]
        total_bal += s["balance"]

    total_return = (total_bal - config.INITIAL_BALANCE) / config.INITIAL_BALANCE * 100
    tbl.add_section()
    pnl_col = f"[{'green' if total_pnl >= 0 else 'red'}]{total_pnl:+.4f}[/]"
    ret_col = f"[{'green' if total_return >= 0 else 'red'}]{total_return:+.2f}%[/]"
    tbl.add_row("[bold]TOTAL[/bold]", "", "", pnl_col, ret_col, "", f"[bold]{total_bal:.2f}[/bold]")

    console.print(tbl)


# ── Main ──────────────────────────────────────────────────────────────────────

def _heartbeat_loop(bots, stop_event: threading.Event) -> None:
    """Log per-bot and portfolio status every 5 minutes."""
    import logging
    portfolio_log = logging.getLogger("PORTFOLIO")

    while not stop_event.wait(300):
        summary = portfolio_summary(bots)
        for bot in bots:
            s = bot.get_stats()
            bot.log.info(
                "HEARTBEAT | kasa=%.2f equity=%.2f | "
                "gunluk=%+.4f(%+.2f%%) realized=%+.4f unrealized=%+.4f | "
                "toplam_pnl=%+.4f | trades=%d(bugun=%d) win=%.1f%% | "
                "paused=%s open=%s",
                s["balance"], s["equity"],
                s["daily_pnl"], s["daily_pnl_pct"],
                s["daily_realized_pnl"], s["unrealized_pnl"],
                s["total_pnl"],
                s["trades"], s["trades_today"], s["win_rate"],
                s["paused"],
                list(bot.positions.keys()) or [],
            )

        p = summary
        portfolio_log.info(
            "PORTFOLIO | kasa=%.2f equity=%.2f | "
            "gunluk=%+.4f(%+.2f%%) realized=%+.4f unrealized=%+.4f | "
            "toplam_pnl=%+.4f(%+.2f%%) | trades_bugun=%d acik=%d",
            p["total_balance"], p["total_equity"],
            p["daily_pnl"], p["daily_pnl_pct"],
            p["daily_realized_pnl"], p["total_unrealized"],
            p["total_pnl"], p["total_return_pct"],
            p["trades_today"], p["open_positions"],
        )


def _risk_guard_loop(bots, stop_event: threading.Event) -> None:
    """Poll SL/TP on all bots — runs even when daily-loss pause is active."""
    while not stop_event.wait(config.SL_TP_CHECK_SECS):
        for bot in bots:
            if not bot.positions:
                continue
            try:
                bot.check_stop_loss_take_profit()
            except Exception as exc:
                bot.log.error("SL/TP guard error: %s", exc, exc_info=True)


def _sync_market_data(
    bots,
    buffers: dict,
    mgr: KlineStreamManager,
    symbols: list[str],
    registered: set,
    *,
    reconnect: bool = False,
) -> None:
    """Seed buffers, attach to bots, register WS topics for new symbol×TF pairs."""
    added = False
    for bot in bots:
        for symbol in symbols:
            key = (symbol, bot.timeframe)
            if key not in buffers:
                buf = KlineBuffer(maxlen=config.WARMUP_BARS)
                df  = bot.fetch_ohlcv(symbol, bot.timeframe)
                buf.seed(df)
                buffers[key] = buf
                console.print(
                    f"  [green]✓[/green] Seeded {symbol} {bot.timeframe} ({len(df)} bars)"
                )
            bot.attach_buffer(symbol, buffers[key])
            if key not in registered:
                mgr.register(symbol, bot.timeframe, buffers[key], bot.on_candle_close)
                registered.add(key)
                added = True
    if reconnect and added:
        mgr.request_reconnect()


def _universe_loop(
    universe: UniverseManager,
    bots,
    buffers: dict,
    mgr: KlineStreamManager,
    registered: set,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(60):
        if not universe.should_rescan():
            continue
        try:
            symbols = universe.rescan()
            _sync_market_data(
                bots, buffers, mgr, symbols, registered, reconnect=True
            )
        except Exception as exc:
            log.error("Universe rescan failed: %s", exc, exc_info=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper trading bot system")
    parser.add_argument("--with-dashboard", action="store_true",
                        help="Start the web dashboard on port 7000")
    args = parser.parse_args()

    assert config.PAPER_TRADING, "Set PAPER_TRADING=True before running."

    console.print("[bold green]Initialising bots…[/bold green]")
    bots = [TrendPullbackBot(), MACDBot(), RSIVWAPBot(), CVDBot()]

    portfolio_risk = PortfolioRiskManager(lambda: bots)
    universe: UniverseManager | None = None

    if config.USE_DYNAMIC_UNIVERSE:
        console.print("[bold cyan]Scanning Bybit spot universe…[/bold cyan]")
        universe = UniverseManager(bots[0].exchange, lambda: bots)
        active_symbols = universe.initial_scan()
    else:
        active_symbols = list(config.FALLBACK_SYMBOLS)

    def get_symbols() -> list[str]:
        if universe is not None:
            return universe.get_symbols()
        return list(config.FALLBACK_SYMBOLS)

    for bot in bots:
        bot.attach_symbol_provider(get_symbols)
        bot.attach_portfolio_risk(portfolio_risk)

    _print_startup_summary(bots, active_symbols)

    # ── Seed candle buffers from REST warmup ──────────────────────────────────
    console.print("[bold cyan]Seeding candle buffers from REST…[/bold cyan]")
    buffers: dict[tuple, KlineBuffer] = {}
    registered: set[tuple] = set()
    mgr = KlineStreamManager()
    _sync_market_data(bots, buffers, mgr, get_symbols(), registered)

    # ── Wire up WebSocket kline stream ────────────────────────────────────────
    console.print("[bold cyan]Starting WebSocket kline stream…[/bold cyan]")
    mgr.start()
    console.print("  [green]✓[/green] WebSocket streams active\n")

    # Attach PORTFOLIO logger to the same handlers as bots (Coolify / file logs)
    import logging
    portfolio_log = logging.getLogger("PORTFOLIO")
    if not portfolio_log.handlers:
        for h in bots[0].log.handlers:
            portfolio_log.addHandler(h)
        portfolio_log.setLevel(logging.INFO)
        portfolio_log.propagate = False

    stop_event = threading.Event()

    # Handle Ctrl+C / SIGTERM
    def _shutdown(signum=None, frame=None):
        console.print("\n[bold red]Shutdown signal received — stopping bots…[/bold red]")
        stop_event.set()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Start terminal dashboard
    dash = Dashboard(bots)
    dash.start()

    # Optionally start web dashboard
    if args.with_dashboard:
        from dashboard.server import run as run_web, register_bots, register_universe
        register_bots(bots)
        if universe is not None:
            register_universe(universe)
        web_thread = threading.Thread(target=run_web, kwargs={"port": 7000}, daemon=True)
        web_thread.start()
        console.print("[bold cyan]Web dashboard running at http://localhost:7000[/bold cyan]")

    # Start 5-minute heartbeat (logs balance + open positions per bot)
    hb_thread = threading.Thread(
        target=_heartbeat_loop, args=(bots, stop_event), daemon=True
    )
    hb_thread.start()

    risk_thread = threading.Thread(
        target=_risk_guard_loop, args=(bots, stop_event), daemon=True
    )
    risk_thread.start()

    if universe is not None:
        uni_thread = threading.Thread(
            target=_universe_loop,
            args=(universe, bots, buffers, mgr, registered, stop_event),
            daemon=True,
            name="universe-scan",
        )
        uni_thread.start()

    console.print("[bold cyan]All bots running. Press Ctrl+C to stop.[/bold cyan]\n")

    # Wait until shutdown — signals arrive via on_candle_close callbacks
    try:
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown()

    # Graceful teardown
    stop_event.set()
    dash.stop()
    mgr.stop()

    _print_final_stats(bots)
    console.print("\n[bold green]Shutdown complete.[/bold green]")


if __name__ == "__main__":
    main()
