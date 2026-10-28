"""Command line entry point.

    uv run colcheck run                  # full pipeline
    uv run colcheck run --skip-pricecatcher
    uv run colcheck run --only-pricecatcher --pricecatcher-limit 3
    uv run colcheck run --offline        # rebuild artifacts from the local cache

Deliberately argparse rather than a CLI framework: the surface is one command, and a
dependency that exists to save twelve lines is not worth its supply-chain weight.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .contracts import ContractError
from .runner import FORECAST_FOLDS, RunConfig, run_pipeline

log = logging.getLogger("colcheck")

REPO_ROOT = Path(__file__).resolve().parents[3]


def _default(path: str) -> Path:
    """Resolve repo-relative defaults so the command works from anywhere."""
    return (REPO_ROOT / path).resolve()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="colcheck",
        description="Malaysia Cost-of-Living Reality Check data pipeline",
    )
    p.add_argument("--version", action="version", version=f"colcheck {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fetch, validate, transform and write data artifacts")
    run.add_argument(
        "--raw-dir",
        type=Path,
        default=_default(".cache/raw"),
        help="where downloaded parquet is cached",
    )
    run.add_argument(
        "--meta-dir",
        type=Path,
        default=_default(".cache/meta"),
        help="where catalogue metadata is cached",
    )
    run.add_argument(
        "--out-dir", type=Path, default=_default("data"), help="where artifacts are written"
    )
    run.add_argument(
        "--offline", action="store_true", help="use only cached files; never touch the network"
    )
    run.add_argument(
        "--skip-pricecatcher",
        action="store_true",
        help="skip the PriceCatcher stage (the slow one)",
    )
    run.add_argument(
        "--only-pricecatcher", action="store_true", help="run only the PriceCatcher stage"
    )
    run.add_argument(
        "--pricecatcher-limit",
        type=int,
        default=None,
        help="reduce only the N most recent PriceCatcher editions",
    )
    run.add_argument(
        "--skip-forecast",
        action="store_true",
        help="skip the forecast stage (the slowest analytical step, ~12s per series)",
    )
    run.add_argument(
        "--forecast-limit",
        type=int,
        default=None,
        help="forecast only the N highest-priority series (headline first)",
    )
    run.add_argument(
        "--forecast-folds",
        type=int,
        default=FORECAST_FOLDS,
        help="rolling-origin backtest folds per model",
    )
    run.add_argument("-v", "--verbose", action="store_true")

    bundle = sub.add_parser(
        "bundle",
        help="build the compact data bundle the Worker carries (run after `run`)",
    )
    bundle.add_argument("--data-dir", type=Path, default=_default("data"))
    bundle.add_argument(
        "--out",
        type=Path,
        default=_default("worker/src/generated/data.json"),
        help="where to write the bundle",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    if args.command == "bundle":
        from .bundle import write_bundle

        size, dropped = write_bundle(args.data_dir, args.out)
        log.info(
            "Wrote %s (%.0f KB, %d trim note(s))",
            args.out,
            size / 1024,
            dropped,
        )
        return 0

    if args.command != "run":
        return 2

    config = RunConfig(
        raw_dir=args.raw_dir,
        meta_dir=args.meta_dir,
        out_dir=args.out_dir,
        offline=args.offline,
        skip_pricecatcher=args.skip_pricecatcher,
        only_pricecatcher=args.only_pricecatcher,
        pricecatcher_limit=args.pricecatcher_limit,
        skip_forecast=args.skip_forecast,
        forecast_limit=args.forecast_limit,
        forecast_folds=args.forecast_folds,
    )

    try:
        result = run_pipeline(config)
    except ContractError as exc:
        # A contract failure means upstream changed shape underneath us. That must be loud.
        log.error("CONTRACT FAILURE: %s", exc)
        return 1

    log.info("Wrote %d artifact(s) to %s", len(result.artifacts), config.out_dir)
    for name in result.artifacts:
        log.info("  %s", name)
    if result.warnings:
        log.warning("%d data-quality warning(s):", len(result.warnings))
        for w in result.warnings:
            log.warning("  %s", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
