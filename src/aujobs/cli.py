"""Command-line entry point: ``aujobs {extract,score,build,all}``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import anthropic
import requests

from aujobs import __version__, build, extract, score
from aujobs.config import OCCUPATIONS_CSV, OCCUPATIONS_JSON, RAW_DIR, SCORES_JSON, SITE_DATA_JSON

log = logging.getLogger("aujobs")


def configure_logging(verbosity: int) -> None:
    level = logging.DEBUG if verbosity > 0 else logging.INFO
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr)
    # The HTTP libraries are chatty at DEBUG and drown out pipeline progress.
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aujobs",
        description="AI exposure of the Australian job market — data pipeline.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "extract",
        help="download and parse JSA workbooks (needs internet access)",
    )
    extract_parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    extract_parser.add_argument("--output", type=Path, default=OCCUPATIONS_JSON)
    extract_parser.add_argument("--csv-output", type=Path, default=OCCUPATIONS_CSV)
    extract_parser.add_argument(
        "--profiles-xlsx",
        type=Path,
        help="parse a local occupation-profiles workbook instead of downloading",
    )
    extract_parser.add_argument(
        "--projections-xlsx",
        type=Path,
        help="parse a local employment-projections workbook instead of downloading",
    )

    score_parser = subparsers.add_parser(
        "score", help="score occupations on AI exposure (needs ANTHROPIC_API_KEY)"
    )
    score_parser.add_argument("--input", type=Path, default=OCCUPATIONS_JSON)
    score_parser.add_argument("--output", type=Path, default=SCORES_JSON)
    score_parser.add_argument("--model", default=score.DEFAULT_MODEL)
    score_parser.add_argument(
        "--effort",
        default=score.DEFAULT_EFFORT,
        choices=["low", "medium", "high", "xhigh", "max"],
    )
    score_parser.add_argument("--concurrency", type=int, default=score.DEFAULT_CONCURRENCY)
    score_parser.add_argument("--max-tokens", type=int, default=score.DEFAULT_MAX_TOKENS)
    score_parser.add_argument(
        "--limit", type=int, help="score at most N occupations (useful for a smoke test)"
    )

    build_parser_ = subparsers.add_parser(
        "build", help="merge stats and scores into site/data.json"
    )
    build_parser_.add_argument("--occupations", type=Path, default=OCCUPATIONS_JSON)
    build_parser_.add_argument("--scores", type=Path, default=SCORES_JSON)
    build_parser_.add_argument("--output", type=Path, default=SITE_DATA_JSON)
    build_parser_.add_argument(
        "--sample", action="store_true", help="tag the output as a demo dataset"
    )

    subparsers.add_parser("all", help="run extract, then score, then build")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)

    try:
        if args.command == "extract":
            if bool(args.profiles_xlsx) != bool(args.projections_xlsx):
                raise SystemExit("--profiles-xlsx and --projections-xlsx must be given together.")
            extract.run(
                raw_dir=args.raw_dir,
                json_path=args.output,
                csv_path=args.csv_output,
                offline_profiles=args.profiles_xlsx,
                offline_projections=args.projections_xlsx,
            )
        elif args.command == "score":
            score.run(
                occupations_path=args.input,
                scores_path=args.output,
                model=args.model,
                effort=args.effort,
                concurrency=args.concurrency,
                max_tokens=args.max_tokens,
                limit=args.limit,
            )
        elif args.command == "build":
            build.run(
                occupations_path=args.occupations,
                scores_path=args.scores,
                output_path=args.output,
                sample=args.sample,
            )
        elif args.command == "all":
            extract.run()
            score.run()
            build.run()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        log.warning("Interrupted. Progress up to the last checkpoint was saved.")
        return 130
    except requests.exceptions.RequestException as exc:
        # A raw traceback here is almost always "no route to jobsandskills.gov.au",
        # which is a environment problem rather than a bug.
        log.error("Could not reach %s: %s", extract.JSA_HOST, exc.__class__.__name__)
        log.error(
            "`aujobs extract` needs open internet access to %s — run it locally or in "
            "GitHub Actions rather than inside a network-restricted sandbox.",
            extract.JSA_HOST,
        )
        return 1
    except anthropic.AuthenticationError:
        log.error(
            "The Claude API rejected your credentials. Set ANTHROPIC_API_KEY, "
            "or sign in with `ant auth login`."
        )
        return 1
    except anthropic.APIConnectionError as exc:
        log.error("Could not reach the Claude API: %s", exc)
        return 1
    except (FileNotFoundError, ValueError, extract.ExtractError) as exc:
        log.error("%s", exc)
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
