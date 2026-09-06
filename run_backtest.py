"""
run_backtest.py — CLI entry point for the backtesting system.

Usage examples
--------------
# Single strategy
python run_backtest.py --strategy macd     --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31
python run_backtest.py --strategy rsi_vwap --symbol SOL/USDT --start 2024-06-01 --end 2024-12-31
python run_backtest.py --strategy cvd      --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31

# All strategies + combined comparison report
python run_backtest.py --all --symbol BTC/USDT --start 2024-01-01 --end 2024-12-31
"""

import argparse
import sys
import os

# Allow running from project root without install
sys.path.insert(0, os.path.dirname(__file__))

from rich.console import Console
from rich.table import Table
from rich import box

import config
from backtest import BacktestEngine, DataLoader, calculate_metrics
from backtest import generate_report, generate_comparison_report

console = Console()

# ── Strategy registry ─────────────────────────────────────────────────────────

def _make_strategy(name: str):
    """Instantiate a strategy bot by name (skips ccxt.load_markets call overhead)."""
    from bot_trend_pullback import TrendPullbackBot
    from bot_macd           import MACDBot
    from bot_rsi_vwap       import RSIVWAPBot
    from bot_cvd            import CVDBot

    mapping = {
        "trend_pullback": TrendPullbackBot,
        "supertrend":     TrendPullbackBot,
        "trend":          TrendPullbackBot,
        "macd":           MACDBot,
        "rsi_vwap":       RSIVWAPBot,
        "cvd":            CVDBot,
    }
    cls = mapping.get(name.lower())
    if cls is None:
        console.print(f"[red]Unknown strategy: {name}. Choose from: {list(mapping)}[/red]")
        sys.exit(1)
    return cls()


# ── Single run ────────────────────────────────────────────────────────────────

def run_single(
    strategy_name: str,
    symbol: str,
    start: str,
    end: str,
    initial_balance: float,
    commission_rate: float,
) -> object:
    """Download data, run backtest, calculate metrics, generate report."""
    loader   = DataLoader()
    strategy = _make_strategy(strategy_name)
    timeframe = config.TIMEFRAMES.get(strategy.name, "1h")

    console.print(
        f"\n[bold cyan]Running {strategy.name}[/bold cyan]  "
        f"{symbol}  {timeframe}  {start} -> {end}"
    )

    # ── Fetch data ────────────────────────────────────────────────────────────
    df = loader.fetch_range(symbol, timeframe, start, end)
    console.print(f"  Data: {len(df):,} bars  ({df.index[0].date()} to {df.index[-1].date()})")

    # ── Run engine ────────────────────────────────────────────────────────────
    engine = BacktestEngine(
        strategy=strategy,
        df=df,
        symbol=symbol,
        initial_balance=initial_balance,
        commission_rate=commission_rate,
    )
    result = engine.run()

    # ── Metrics ───────────────────────────────────────────────────────────────
    calculate_metrics(result)

    # ── Print summary ─────────────────────────────────────────────────────────
    _print_result(result)

    # ── HTML report ───────────────────────────────────────────────────────────
    report_path = generate_report(result)
    console.print(f"  [green]Report:[/green] {report_path}")

    return result


def _print_result(result) -> None:
    m = result.metrics
    tbl = Table(
        title=f"[bold]{result.strategy_name} — {result.symbol}[/bold]",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
    )
    tbl.add_column("Metric",        style="bold white", min_width=22)
    tbl.add_column("Value",         justify="right")

    def _pct(v):
        cls = "green" if v >= 0 else "red"
        return f"[{cls}]{v:+.2f}%[/{cls}]"

    def _usdt(v):
        cls = "green" if v >= 0 else "red"
        return f"[{cls}]{v:+.4f}[/{cls}]"

    rows = [
        ("Total Return",        _pct(m["total_return_pct"])),
        ("Annual Return",       _pct(m["annual_return_pct"])),
        ("Max Drawdown",        f"[red]-{m['max_drawdown_pct']:.2f}%[/red]"),
        ("DD Duration",         f"{m['max_dd_duration_days']:.1f} days"),
        ("Sharpe Ratio",        f"{m['sharpe']:.3f}"),
        ("Sortino Ratio",       f"{m['sortino']:.3f}"),
        ("Calmar Ratio",        f"{m['calmar']:.3f}"),
        ("Win Rate",            f"{m['win_rate_pct']:.2f}%"),
        ("Profit Factor",       f"{m['profit_factor']:.3f}×"),
        ("Avg Win",             _usdt(m["avg_win"])),
        ("Avg Loss",            _usdt(m["avg_loss"])),
        ("Largest Win",         _usdt(m["largest_win"])),
        ("Largest Loss",        _usdt(m["largest_loss"])),
        ("Avg Hold Time",       m["avg_hold_time"]),
        ("Total Trades",        str(m["total_trades"])),
        ("Wins / Losses",       f"{m['winning_trades']} / {m['losing_trades']}"),
        ("Total Commission",    f"{m['total_commission']:.4f} USDT"),
        ("Final Balance",       f"{m['final_balance']:.2f} USDT"),
    ]
    for k, v in rows:
        tbl.add_row(k, v)

    console.print()
    console.print(tbl)


# ── Comparison summary ────────────────────────────────────────────────────────

def _print_comparison(results: list) -> None:
    tbl = Table(
        title="[bold yellow]Strategy Comparison[/bold yellow]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    tbl.add_column("Strategy",    style="bold white", min_width=12)
    tbl.add_column("Symbol",      min_width=10)
    tbl.add_column("Return %",    justify="right")
    tbl.add_column("Annual %",    justify="right")
    tbl.add_column("Max DD %",    justify="right")
    tbl.add_column("Sharpe",      justify="right")
    tbl.add_column("Sortino",     justify="right")
    tbl.add_column("Win %",       justify="right")
    tbl.add_column("Trades",      justify="right")
    tbl.add_column("Final Bal",   justify="right")

    for r in results:
        m       = r.metrics
        ret     = m["total_return_pct"]
        ret_cls = "green" if ret >= 0 else "red"
        tbl.add_row(
            r.strategy_name,
            r.symbol,
            f"[{ret_cls}]{ret:+.2f}%[/{ret_cls}]",
            f"[{ret_cls}]{m['annual_return_pct']:+.2f}%[/{ret_cls}]",
            f"[red]-{m['max_drawdown_pct']:.2f}%[/red]",
            f"{m['sharpe']:.2f}",
            f"{m['sortino']:.2f}",
            f"{m['win_rate_pct']:.1f}%",
            str(m["total_trades"]),
            f"{m['final_balance']:.2f}",
        )

    console.print()
    console.print(tbl)


# ── Argument parser ────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Crypto backtesting system — Bybit USDT perpetual futures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--strategy", "-s",
        choices=["trend_pullback", "supertrend", "macd", "rsi_vwap", "cvd"],
        help="Strategy to backtest (ignored when --all is set)",
    )
    p.add_argument(
        "--all", "-a",
        action="store_true",
        help="Run all strategies and generate comparison report",
    )
    p.add_argument("--symbol",  default="BTC/USDT", help="Trading pair (default: BTC/USDT)")
    p.add_argument("--start",   required=True,       help="Start date YYYY-MM-DD")
    p.add_argument("--end",     required=True,       help="End date YYYY-MM-DD")
    p.add_argument(
        "--balance",
        type=float,
        default=config.INITIAL_BALANCE,
        help=f"Total capital in USDT (default: {config.INITIAL_BALANCE})",
    )
    p.add_argument(
        "--commission",
        type=float,
        default=0.00055,
        help="Commission rate per trade side (default: 0.00055 = Bybit taker)",
    )
    return p


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    if not args.all and args.strategy is None:
        parser.error("Specify --strategy or --all.")

    strategies = (
        ["trend_pullback", "macd", "rsi_vwap", "cvd"] if args.all
        else [args.strategy]
    )

    console.print(
        f"\n[bold green]Backtest System[/bold green]  "
        f"symbol={args.symbol}  {args.start} -> {args.end}  "
        f"balance={args.balance:,.0f} USDT"
    )

    results = []
    for strat_name in strategies:
        allocation = config.BOT_ALLOCATIONS.get(strat_name.upper(), 1 / len(strategies))
        balance    = args.balance * allocation

        result = run_single(
            strategy_name=strat_name,
            symbol=args.symbol,
            start=args.start,
            end=args.end,
            initial_balance=balance,
            commission_rate=args.commission,
        )
        results.append(result)

    if args.all and len(results) > 1:
        _print_comparison(results)
        comp_path = generate_comparison_report(results)
        console.print(f"\n[bold green]Comparison report:[/bold green] {comp_path}")

    console.print("\n[bold green]Done.[/bold green]")


if __name__ == "__main__":
    main()
