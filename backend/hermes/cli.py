"""
Hermes CLI — universe verification and data tools.

Usage:
    uv run python -m hermes.cli universe list
    uv run python -m hermes.cli universe verify long_us
    uv run python -m hermes.cli universe verify intra_us --sample 10
"""
from __future__ import annotations

import argparse
import asyncio
import sys


def cmd_universe_list(args: argparse.Namespace) -> None:
    from hermes.data.universe import list_universes
    universes = list_universes()
    if not universes:
        print("No universes found.")
        return
    print(f"{'NAME':<20} {'SYMBOLS':>8}")
    print("-" * 30)
    from hermes.data.universe import load_universe
    for name in universes:
        u = load_universe(name)
        print(f"{name:<20} {len(u):>8}")


def cmd_universe_verify(args: argparse.Namespace) -> None:
    from hermes.data.universe import load_universe
    from hermes.data.yfinance_provider import YFinanceProvider

    u = load_universe(args.name)
    symbols = u.symbols
    if args.sample:
        import random
        symbols = random.sample(symbols, min(args.sample, len(symbols)))

    print(f"Universe: {u.name} ({len(u)} symbols total, verifying {len(symbols)})")
    print(f"Provider: {u.provider} | Timeframe: {u.timeframe} | Freq: {u.scan_frequency}")
    print("-" * 60)

    provider = YFinanceProvider()

    async def verify_all() -> None:
        ok, fail = [], []
        for sym in symbols:
            tradeable = await provider.is_tradeable(sym)
            status = "OK " if tradeable else "FAIL"
            print(f"  {status}  {sym}")
            (ok if tradeable else fail).append(sym)
        print("-" * 60)
        print(f"Results: {len(ok)} OK, {len(fail)} FAIL out of {len(symbols)} checked")
        if fail:
            print(f"Failed: {', '.join(fail)}")

    asyncio.run(verify_all())


def main() -> None:
    parser = argparse.ArgumentParser(prog="hermes", description="Hermes Trading Bot CLI")
    sub = parser.add_subparsers(dest="command")

    # universe subcommand
    uni = sub.add_parser("universe", help="Universe management")
    uni_sub = uni.add_subparsers(dest="subcommand")

    uni_sub.add_parser("list", help="List all available universes")

    verify_p = uni_sub.add_parser("verify", help="Verify symbols in a universe are tradeable")
    verify_p.add_argument("name", help="Universe name (e.g. long_us)")
    verify_p.add_argument("--sample", type=int, default=0,
                          help="Only verify N random symbols (0 = all)")

    args = parser.parse_args()

    if args.command == "universe":
        if args.subcommand == "list":
            cmd_universe_list(args)
        elif args.subcommand == "verify":
            cmd_universe_verify(args)
        else:
            uni.print_help()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
