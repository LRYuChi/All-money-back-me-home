"""CLI: bulk-load / validate strategy YAML files (round 33).

Usage:
    # Validate every YAML in a directory without touching DB
    python -m strategy_engine.cli.loader validate --dir config/strategies

    # Validate a single file
    python -m strategy_engine.cli.loader validate --file config/strategies/foo.yaml

    # Bulk-upsert into the registry
    python -m strategy_engine.cli.loader load --dir config/strategies

    # Dry-run: parse + check what would change, but don't write
    python -m strategy_engine.cli.loader load --dir config/strategies --dry-run

Phase E gap closed: previously each strategy YAML had to be upserted via
the python REPL or a one-off script. This loader walks a directory,
validates every file (so a malformed one doesn't half-succeed), and
upserts in a deterministic order (alphabetical by filename).

Exit codes:
    0  — all files OK (or dry-run completed without errors)
    1  — at least one file failed to parse / upsert
    2  — invalid args / no files found
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from smart_money.config import settings
from strategy_engine.dsl import DSLError, load_strategy_str
from strategy_engine.registry import (
    StrategyNotFound,
    StrategyRegistry,
    build_registry,
)

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m strategy_engine.cli.loader",
        description="Bulk-load or validate strategy YAML files.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    for cmd in ("validate", "load"):
        sp = sub.add_parser(cmd, help=f"{cmd.capitalize()} strategy YAMLs")
        src = sp.add_mutually_exclusive_group(required=True)
        src.add_argument("--file", type=Path, help="One YAML file.")
        src.add_argument("--dir", type=Path,
                         help="Directory of YAML files (non-recursive).")
        sp.add_argument(
            "--pattern", default="*.yaml",
            help="Glob pattern when --dir is used (default *.yaml).",
        )
        if cmd == "load":
            sp.add_argument(
                "--dry-run", action="store_true",
                help="Parse + check what would change but don't write to registry.",
            )

    return p


def _collect_files(args: argparse.Namespace) -> list[Path]:
    """Return the YAML files to process. Sorted for deterministic order."""
    if args.file is not None:
        if not args.file.exists():
            print(f"error: file not found: {args.file}", file=sys.stderr)
            return []
        return [args.file]

    if not args.dir.exists() or not args.dir.is_dir():
        print(f"error: directory not found: {args.dir}", file=sys.stderr)
        return []
    files = sorted(args.dir.glob(args.pattern))
    return files


def _cmd_validate(files: list[Path]) -> int:
    """Parse every YAML, print a status line per file. Returns 0 iff all OK."""
    if not files:
        print("(no files matched)")
        return 2

    n_ok = 0
    n_fail = 0
    for path in files:
        try:
            text = path.read_text()
            strat = load_strategy_str(text)
        except DSLError as e:
            print(f"FAIL  {path.name}  — {e}")
            n_fail += 1
            continue
        except OSError as e:
            print(f"FAIL  {path.name}  — read error: {e}")
            n_fail += 1
            continue
        flag = "ON " if strat.enabled else "off"
        print(
            f"OK    {path.name}  → id={strat.id} mode={strat.mode} "
            f"market={strat.market} symbol={strat.symbol} tf={strat.timeframe} "
            f"[{flag}]",
        )
        n_ok += 1

    print(f"\nsummary: {n_ok} OK / {n_fail} FAIL  (total {len(files)})")
    return 0 if n_fail == 0 else 1


def _cmd_load(
    files: list[Path],
    registry: StrategyRegistry,
    *,
    dry_run: bool,
) -> int:
    """Validate every file first; if any fails, write none. Otherwise upsert."""
    if not files:
        print("(no files matched)")
        return 2

    parsed: list[tuple[Path, str]] = []
    n_fail = 0
    for path in files:
        try:
            text = path.read_text()
            load_strategy_str(text)   # parse-only validation
            parsed.append((path, text))
        except DSLError as e:
            print(f"FAIL  {path.name}  — {e}")
            n_fail += 1
        except OSError as e:
            print(f"FAIL  {path.name}  — read error: {e}")
            n_fail += 1

    if n_fail > 0:
        print(
            f"\nsummary: aborting; {n_fail} file(s) failed validation. "
            f"Registry not modified.",
        )
        return 1

    if dry_run:
        for path, _text in parsed:
            print(f"DRY   {path.name}  — would upsert")
        print(f"\nsummary: dry-run OK; {len(parsed)} file(s) would be upserted.")
        return 0

    n_new = 0
    n_updated = 0
    n_upsert_fail = 0
    for path, text in parsed:
        # Decide new vs updated by checking existence first.
        # Defensive: if the registry's get raises something other than
        # StrategyNotFound (e.g. DB outage), treat as updated to avoid
        # mis-counting; the upsert below will surface the real error.
        sid = load_strategy_str(text).id
        existed = True
        try:
            registry.get(sid)
        except StrategyNotFound:
            existed = False
        except Exception:
            pass

        try:
            registry.upsert(text)
        except Exception as e:
            print(f"FAIL  {path.name}  — upsert error: {e}")
            n_upsert_fail += 1
            continue

        if existed:
            print(f"UPD   {path.name}  → id={sid}")
            n_updated += 1
        else:
            print(f"NEW   {path.name}  → id={sid}")
            n_new += 1

    print(
        f"\nsummary: {n_new} new / {n_updated} updated / {n_upsert_fail} failed "
        f"(total {len(parsed)})",
    )
    return 0 if n_upsert_fail == 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    files = _collect_files(args)
    if not files and (args.file or args.dir):
        return 2

    if args.cmd == "validate":
        return _cmd_validate(files)
    if args.cmd == "load":
        registry = build_registry(settings)
        return _cmd_load(files, registry, dry_run=args.dry_run)
    return 2


if __name__ == "__main__":
    sys.exit(main())
