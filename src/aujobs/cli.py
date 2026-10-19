"""Command-line entry point: ``aujobs {extract,demand,score,build,validate,evaluate,all}``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import anthropic
import requests

from aujobs import __version__, build, demand, evaluate, extract, manifest, score, validate
from aujobs.config import (
    DEMAND_JSON,
    EXAMPLES_DIR,
    MANIFEST_JSON,
    OCCUPATIONS_CSV,
    OCCUPATIONS_JSON,
    OSCA_CROSSWALK_CSV,
    RAW_DIR,
    SCORES_JSON,
    SITE_DATA_JSON,
    read_json,
)

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

    demand_parser = subparsers.add_parser(
        "demand", help="read the Internet Vacancy Index (online job ads by occupation)"
    )
    demand_parser.add_argument("--xlsx", type=Path, help="use a local IVI workbook")
    demand_parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    demand_parser.add_argument("--output", type=Path, default=DEMAND_JSON)

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

    build_parser_ = subparsers.add_parser("build", help="merge everything into site/data.json")
    build_parser_.add_argument("--occupations", type=Path, default=OCCUPATIONS_JSON)
    build_parser_.add_argument("--scores", type=Path, default=SCORES_JSON)
    build_parser_.add_argument("--demand", type=Path, default=DEMAND_JSON)
    build_parser_.add_argument("--crosswalk", type=Path, default=OSCA_CROSSWALK_CSV)
    build_parser_.add_argument("--manifest", type=Path, default=MANIFEST_JSON)
    build_parser_.add_argument("--output", type=Path, default=SITE_DATA_JSON)
    build_parser_.add_argument(
        "--sample",
        action="store_true",
        help="build the reproducible demo dataset from examples/ (ignores local data)",
    )

    subparsers.add_parser("validate", help="check every data artefact against its schema")

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="compare scores with the gold set; optionally measure variance"
    )
    evaluate_parser.add_argument("--scores", type=Path, default=SCORES_JSON)
    evaluate_parser.add_argument("--gold", type=Path, default=evaluate.GOLD_CSV)
    evaluate_parser.add_argument("--input", type=Path, default=OCCUPATIONS_JSON)
    evaluate_parser.add_argument(
        "--variance-sample",
        type=int,
        default=0,
        metavar="N",
        help="score N occupations several times to measure spread (makes API calls)",
    )
    evaluate_parser.add_argument("--repeats", type=int, default=3)
    evaluate_parser.add_argument("--model", default=score.DEFAULT_MODEL)
    evaluate_parser.add_argument("--effort", default=score.DEFAULT_EFFORT)

    subparsers.add_parser("all", help="run extract, demand, score, then build")
    return parser


def _run_evaluate(args: argparse.Namespace) -> None:
    labels = evaluate.load_gold(args.gold)
    if not labels:
        print(f"No gold labels found at {args.gold}.")
    else:
        scores = score.load_existing_scores(args.scores)
        report = evaluate.agreement(scores, labels)
        print(f"Gold-set agreement (provisional labels): {report.evaluated} evaluated")
        if report.evaluated:
            print(f"  mean absolute error: {report.mean_absolute_error}")
            print(f"  within tolerance:    {report.within_tolerance:.0%}")
        for code, expected, actual in report.misses:
            print(f"  miss: {code} expected {expected}, scored {actual}")
        if report.missing:
            print(f"  not yet scored: {', '.join(report.missing)}")

    if args.variance_sample:
        occupations = score.load_occupations(args.input)
        chosen = evaluate.sample(occupations, args.variance_sample)
        scorer = score.Scorer(
            anthropic.Anthropic(max_retries=5), model=args.model, effort=args.effort
        )
        rows = evaluate.variance(scorer, chosen, args.repeats)
        print(f"\nVariance across {args.repeats} runs per occupation:")
        for row in rows:
            print(
                f"  {row.code} {row.title[:40]:<40} scores {list(row.scores)} "
                f"mean {row.mean} spread {row.spread}"
            )


def _run_validate() -> int:
    problems: list[str] = []
    if OCCUPATIONS_JSON.exists():
        problems += validate.occupation_problems(read_json(OCCUPATIONS_JSON))
    if SCORES_JSON.exists():
        problems += validate.score_problems(read_json(SCORES_JSON))
    if DEMAND_JSON.exists():
        problems += validate.demand_problems(read_json(DEMAND_JSON))
    if SITE_DATA_JSON.exists():
        problems += validate.site_problems(read_json(SITE_DATA_JSON))
    if MANIFEST_JSON.exists():
        problems += manifest.verify(MANIFEST_JSON, RAW_DIR)

    if problems:
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("All artefacts are valid.")
    return 0


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
        elif args.command == "demand":
            demand.run(xlsx_path=args.xlsx, raw_dir=args.raw_dir, output_path=args.output)
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
            if args.sample:
                # The demo build reads only checked-in examples, so its output is reproducible.
                build.run(
                    occupations_path=args.occupations,
                    scores_path=args.scores,
                    output_path=args.output,
                    demand_path=EXAMPLES_DIR / "sample_demand.json",
                    crosswalk_path=None,
                    manifest_path=None,
                    sample=True,
                )
            else:
                build.run(
                    occupations_path=args.occupations,
                    scores_path=args.scores,
                    output_path=args.output,
                    demand_path=args.demand,
                    crosswalk_path=args.crosswalk,
                    manifest_path=args.manifest,
                )
        elif args.command == "validate":
            return _run_validate()
        elif args.command == "evaluate":
            _run_evaluate(args)
        elif args.command == "all":
            extract.run()
            try:
                demand.run()
            except (extract.ExtractError, requests.exceptions.RequestException) as exc:
                # Demand is an optional signal; the rest of the pipeline should still run.
                log.warning("Skipping demand data: %s", exc)
            score.run()
            build.run()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        log.warning("Interrupted. Progress up to the last checkpoint was saved.")
        return 130
    except requests.exceptions.RequestException as exc:
        # A raw traceback here is almost always "no route to jobsandskills.gov.au",
        # which is an environment problem rather than a bug.
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
