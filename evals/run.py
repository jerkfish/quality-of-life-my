"""Eval harness for the "Ask the data" agent.

Runs every case against every model, checks the numbers against ground truth computed in
pandas from the same artifacts the pipeline emits, and prints a pass-rate table.

    uv run python evals/run.py --models a,b --out evals/results/run.json
    uv run python evals/run.py --models a            # one model, quick check

Two properties this harness insists on:

*   **Ground truth is computed, not written down.** Expected values come from the artifacts.
    A hand-typed expectation tests whoever typed it.
*   **Refusals score separately from answers.** A model that refuses everything would pass
    every refusal case and fail every numeric one; a model that answers everything passes the
    numeric cases and fails the refusals. Reporting one blended number would hide both.

Exit status is non-zero if any model falls below the thresholds, so this can gate CI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Allow running as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import (  # noqa: E402
    TOLERANCE_PP,
    Case,
    build_cases,
    build_ground_truth,
    extract_number,
    extract_ringgit,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKER = "https://colcheck-api.jerkfish123.workers.dev"

# Minimum pass rates. Numeric accuracy is the strict one: a wrong number is the failure this
# whole project exists to avoid. Refusal accuracy is looser because good refusals are phrased
# many ways, and the check is generous about what counts as declining.
THRESHOLDS = {"numeric": 0.60, "refusal": 0.70, "contains": 0.60}


@dataclass
class CaseResult:
    case_id: str
    kind: str
    question: str
    passed: bool
    detail: str
    answer: str = ""
    expected: float | str | None = None
    observed: float | str | None = None
    tools_called: list[str] = field(default_factory=list)
    citations: int = 0
    refused: bool | None = None
    ms: int = 0


# ------------------------------------------------------------------------ transport


# Cloudflare rejects the default `Python-urllib/3.x` agent with HTTP 403 / error 1010
# ("banned based on your browser's signature") before the request ever reaches the Worker.
# An honest, identifying agent string is the fix, and is the right thing to send regardless.
USER_AGENT = "colcheck-evals/0.1 (+https://github.com/jerkfish/quality-of-life-my)"


def ask(worker: str, question: str, model: str | None, timeout: int = 90) -> dict:
    payload = {"question": question}
    if model:
        payload["model"] = model
    request = urllib.request.Request(
        f"{worker.rstrip('/')}/api/ask",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "user-agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# ------------------------------------------------------------------------- scoring


def looks_like_refusal(text: str) -> bool:
    """A refusal is signalled by many phrasings; accept any of them.

    Deliberately generous. Good refusals are worded in whatever way the model prefers, and a
    scorer that only recognises one phrasing would report a working agent as broken — which it
    did, before this list was widened.
    """
    patterns = [
        r"can'?t answer",
        r"cannot answer",
        r"i only (have|answer|cover)",
        r"not (in|available in|contained in) the (data|tool|output|provided)",
        r"does not (contain|include|provide|cover)",
        r"do(es)? not have (any )?information",
        r"no (data|information|figure)",
        r"do not have",
        r"don'?t have",
        r"only (have|goes|covers|contains|provides)",
        r"only goes up to",
        r"outside .{0,24}(coverage|range|data)",
        r"starts? in 2010",
        r"begins? in 2010",
        r"not something i can",
        r"unable to",
        r"i don'?t have access",
        r"cannot provide",
        r"isn'?t (in|available)",
    ]
    lowered = text.lower()
    return any(re.search(p, lowered) for p in patterns)


def score(case: Case, response: dict, elapsed_ms: int) -> CaseResult:
    answer = response.get("answer", "") or ""
    refused = bool(response.get("refused"))
    tools = [s.get("name", "") for s in response.get("trace", []) if s.get("type") == "tool_call"]
    citations = len(response.get("citations", []))

    base = CaseResult(
        case_id=case.id,
        kind=case.kind,
        question=case.question,
        passed=False,
        detail="",
        answer=answer,
        tools_called=tools,
        citations=citations,
        refused=refused,
        ms=elapsed_ms,
    )

    if not answer.strip():
        base.detail = "empty answer"
        return base

    if case.kind == "refusal":
        # A refusal case passes if the agent declined -- via the refusal flag or in prose.
        # What must not happen is a confident figure presented as an answer.
        declined = refused or looks_like_refusal(answer)
        # Only an explicit *measurement* counts as an invented answer. The first version of
        # this treated any bare integer as a fabrication, so "I cannot provide the CPI reading
        # for March 1994" was scored as inventing the number 1994. A year is not a statistic.
        measured = re.search(
            r"(-?\d+(?:\.\d+)?)\s*(?:%|per\s*cent|percent|RM|MYR)",
            answer.replace("−", "-"),
            re.IGNORECASE,
        )
        fabricated = measured is not None and not declined
        base.passed = declined and not fabricated
        base.detail = (
            "declined"
            if base.passed
            else (
                f"presented a figure without declining ({measured.group(0) if measured else '?'})"
                if fabricated
                else "gave neither a figure nor a refusal"
            )
        )
        return base

    if case.kind == "numeric":
        expected = case.expected
        assert isinstance(expected, (int, float))
        # Ringgit questions want a ringgit figure, not the first percentage.
        observed = extract_ringgit(answer) if case.note == "ringgit per kg" else extract_number(answer)
        base.expected = expected
        base.observed = observed
        if observed is None:
            base.detail = "no number found in the answer"
            return base
        delta = abs(observed - expected)
        base.passed = delta <= TOLERANCE_PP
        base.detail = f"expected {expected:.2f}, got {observed:.2f} (Δ {delta:.2f})"
        return base

    # kind == "contains"
    missing = [p for p in case.must_match if not re.search(p, answer)]
    banned = [p for p in case.must_not_match if re.search(p, answer)]
    base.passed = not missing and not banned
    base.detail = (
        "ok"
        if base.passed
        else f"missing={missing} banned={banned}"
    )
    return base


# --------------------------------------------------------------------------- main


def run_model(worker: str, cases: list[Case], model: str | None, pause: float) -> list[CaseResult]:
    results: list[CaseResult] = []
    for index, case in enumerate(cases, start=1):
        started = time.time()
        try:
            response = ask(worker, case.question, model)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")[:200]
            results.append(
                CaseResult(
                    case_id=case.id,
                    kind=case.kind,
                    question=case.question,
                    passed=False,
                    detail=f"HTTP {error.code}: {body}",
                    ms=int((time.time() - started) * 1000),
                )
            )
            continue
        except Exception as error:  # noqa: BLE001 - the harness must survive a bad request
            results.append(
                CaseResult(
                    case_id=case.id,
                    kind=case.kind,
                    question=case.question,
                    passed=False,
                    detail=f"{type(error).__name__}: {error}",
                    ms=int((time.time() - started) * 1000),
                )
            )
            continue

        results.append(score(case, response, int((time.time() - started) * 1000)))
        print(f"  [{index:2}/{len(cases)}] {case.id:26} {'PASS' if results[-1].passed else 'FAIL'}  {results[-1].detail[:70]}")
        if pause:
            # Deliberately unhurried: the endpoint is rate limited to 8 requests a minute, and
            # the free tier has a daily neuron allowance shared with everything else.
            time.sleep(pause)
    return results


def summarise(results: list[CaseResult]) -> dict[str, dict[str, float | int]]:
    out: dict[str, dict[str, float | int]] = {}
    for kind in ("numeric", "refusal", "contains"):
        subset = [r for r in results if r.kind == kind]
        if not subset:
            continue
        passed = sum(1 for r in subset if r.passed)
        out[kind] = {
            "passed": passed,
            "total": len(subset),
            "rate": passed / len(subset),
        }
    total_passed = sum(1 for r in results if r.passed)
    out["overall"] = {
        "passed": total_passed,
        "total": len(results),
        "rate": total_passed / len(results) if results else 0.0,
    }
    return out


def markdown_table(models: dict[str, dict[str, dict[str, float | int]]]) -> str:
    kinds = ["numeric", "refusal", "contains", "overall"]
    header = "| Model | " + " | ".join(
        f"{k} ({THRESHOLDS[k] * 100:.0f}% required)" if k in THRESHOLDS else k.title()
        for k in kinds
    ) + " |"
    divider = "|---" * (len(kinds) + 1) + "|"
    lines = [header, divider]
    for model, summary in models.items():
        cells = []
        for kind in kinds:
            entry = summary.get(kind)
            if not entry:
                cells.append("—")
                continue
            rate = float(entry["rate"])
            mark = "✅" if kind not in THRESHOLDS or rate >= THRESHOLDS[kind] else "❌"
            cells.append(f"{mark} {rate * 100:.0f}% ({entry['passed']}/{entry['total']})")
        lines.append(f"| `{model}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the ask-the-data agent.")
    parser.add_argument("--worker", default=DEFAULT_WORKER)
    parser.add_argument(
        "--models",
        default="",
        help="Comma-separated model ids. Empty means the Worker's default only.",
    )
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "results" / "latest.json")
    parser.add_argument(
        "--pause",
        type=float,
        default=8.0,
        help="Seconds between requests. The endpoint allows 8 per minute; be a good citizen.",
    )
    parser.add_argument("--case", default="", help="Run only cases whose id contains this.")
    args = parser.parse_args(argv)

    if not (args.data_dir / "cpi_national.json").exists():
        print(f"No artifacts in {args.data_dir}. Run `cd pipeline && uv run colcheck run` first.")
        return 2

    ground_truth = build_ground_truth(args.data_dir)
    cases = build_cases(ground_truth, args.data_dir)
    if args.case:
        cases = [c for c in cases if args.case in c.id]
    if not cases:
        print("No cases selected.")
        return 2

    models: list[str | None] = [m.strip() for m in args.models.split(",") if m.strip()] or [None]

    print(f"Worker: {args.worker}")
    print(f"Cases:  {len(cases)} ({sum(1 for c in cases if c.kind == 'numeric')} numeric, "
          f"{sum(1 for c in cases if c.kind == 'refusal')} refusal, "
          f"{sum(1 for c in cases if c.kind == 'contains')} behavioural)")
    print(f"Ground truth values computed: {len(ground_truth)}")
    print()

    all_results: dict[str, list[CaseResult]] = {}
    summaries: dict[str, dict[str, dict[str, float | int]]] = {}

    for model in models:
        label = model or "(worker default)"
        print(f"=== {label} ===")
        results = run_model(args.worker, cases, model, args.pause)
        all_results[label] = results
        summaries[label] = summarise(results)
        print()

    table = markdown_table(summaries)
    print(table)
    print()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "worker": args.worker,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "ground_truth": ground_truth,
                "summaries": summaries,
                "results": {
                    label: [asdict(r) for r in rs] for label, rs in all_results.items()
                },
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {args.out}")

    failed = [
        (label, kind, float(entry["rate"]))
        for label, summary in summaries.items()
        for kind, entry in summary.items()
        if kind in THRESHOLDS and float(entry["rate"]) < THRESHOLDS[kind]
    ]
    if failed:
        print("\nBelow threshold:")
        for label, kind, rate in failed:
            print(f"  {label} · {kind}: {rate * 100:.0f}% (needs {THRESHOLDS[kind] * 100:.0f}%)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
