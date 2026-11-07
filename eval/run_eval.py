"""
run_eval.py - measures how often the assistant answers correctly.

WHY THIS FILE EXISTS
    "It seems to work" is not a result. This script runs every question in
    eval/questions.yaml through the REAL pipeline and scores it automatically:

      expect: rows    -> correct if the result matches the reference result
      expect: refuse  -> correct if the assistant returned NO data
                         (status cannot_answer or blocked)

HOW RESULTS ARE COMPARED (execution accuracy, relaxed)
    We compare the result ROWS, not the SQL text: two different queries are
    both right if they return the same answer. To be fair to harmless
    differences, the comparison
      - ignores column NAMES (revenue vs total_revenue),
      - ignores EXTRA columns the model added (e.g. it also showed the city),
      - ignores row ORDER unless order_matters is true (rankings),
      - treats numbers as equal if they differ by at most 0.01 (rounding).
    Every reference column must appear in the model's result, with the same
    values in the same rows. Different row counts = wrong.

OUTPUT
    A summary table in the terminal, and a JSON file with every detail in
    eval/results/<timestamp>_<label>.json (used for docs/EVALUATION.md).

RUN (venv activated)
    python eval/run_eval.py --label baseline --no-repair
    python eval/run_eval.py --label with_repair
    python eval/run_eval.py --dry-run          # checks the scorer itself, no API calls
    python eval/run_eval.py --ids E1 H2        # only some questions
    python eval/run_eval.py --questions eval/holdout.yaml --label holdout   # held-out set
    python eval/run_eval.py --rescore eval/results/<file>.json   # re-check a saved run (no API)
"""

import argparse
import itertools
import json
import math
import sys
import time
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from src.config import settings  # noqa: E402
from src.executor import execute_query  # noqa: E402
from src.llm_client import FakeLLM, get_llm_client  # noqa: E402
from src.pipeline import STATUS_BLOCKED, STATUS_CANNOT_ANSWER, STATUS_SUCCESS, ask  # noqa: E402

QUESTIONS_PATH = PROJECT_ROOT / "eval" / "questions.yaml"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
EVAL_LOG_PATH = PROJECT_ROOT / "logs" / "eval_queries.log"
NUMBER_TOLERANCE = 0.01
DIFFICULTY_ORDER = ["easy", "medium", "hard", "ambiguous", "unsafe"]


# ---------------------------------------------------------------------------
# Comparing results
# ---------------------------------------------------------------------------
def _normalise(value):
    """Make values comparable: numbers -> float, everything else unchanged (None stays None)."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and not (isinstance(value, float) and math.isnan(value)):
        return float(value)
    if isinstance(value, float) and math.isnan(value):
        return None  # pandas uses NaN for SQL NULL in number columns
    return value


def values_equal(a, b) -> bool:
    """Equal, with a small tolerance for numbers (rounding differences)."""
    a, b = _normalise(a), _normalise(b)
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= NUMBER_TOLERANCE
    return a == b


def _sort_key(value):
    """Sort key that works for mixed None / numbers / text."""
    value = _normalise(value)
    if value is None:
        return (0, "")
    if isinstance(value, float):
        return (1, round(value, 2))
    return (2, str(value))


def _columns_match(reference: list, predicted: list) -> bool:
    """Same multiset of values in one column (order ignored)."""
    if len(reference) != len(predicted):
        return False
    return all(values_equal(a, b) for a, b in zip(sorted(reference, key=_sort_key), sorted(predicted, key=_sort_key)))


def _rows_match(reference_rows: list[tuple], predicted_rows: list[tuple], order_matters: bool) -> bool:
    """Compare whole rows (so values must also line up in the same rows)."""
    if not order_matters:
        row_key = lambda row: tuple(_sort_key(v) for v in row)  # noqa: E731
        reference_rows = sorted(reference_rows, key=row_key)
        predicted_rows = sorted(predicted_rows, key=row_key)
    return all(
        all(values_equal(a, b) for a, b in zip(ref_row, pred_row))
        for ref_row, pred_row in zip(reference_rows, predicted_rows)
    )


def results_match(reference: pd.DataFrame, predicted: pd.DataFrame, order_matters: bool = False) -> bool:
    """
    True if `predicted` contains the answer in `reference` (see module docstring).

    Steps:
      1. same number of rows;
      2. for each reference column, find predicted columns with the same values;
      3. try the combinations of candidate columns until the full rows match.
    """
    if len(reference) != len(predicted):
        return False
    if len(reference) == 0:
        return True

    reference_columns = [reference.iloc[:, i].tolist() for i in range(reference.shape[1])]
    predicted_columns = [predicted.iloc[:, i].tolist() for i in range(predicted.shape[1])]

    candidates = []
    for ref_values in reference_columns:
        matches = [j for j, pred_values in enumerate(predicted_columns) if _columns_match(ref_values, pred_values)]
        if not matches:
            return False
        candidates.append(matches)

    reference_rows = list(zip(*reference_columns))
    for combination in itertools.product(*candidates):
        if len(set(combination)) != len(combination):
            continue  # one predicted column cannot stand for two reference columns
        predicted_rows = list(zip(*(predicted_columns[j] for j in combination)))
        if _rows_match(reference_rows, predicted_rows, order_matters):
            return True
    return False


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
def load_questions(ids: list[str] | None = None, path: Path = QUESTIONS_PATH) -> list[dict]:
    """Read a questions file (optionally only some ids)."""
    with open(path, encoding="utf-8") as file:
        questions = yaml.safe_load(file)["questions"]
    if ids:
        wanted = {i.upper() for i in ids}
        questions = [q for q in questions if q["id"].upper() in wanted]
    return questions


def reference_result(question: dict, today: date) -> pd.DataFrame:
    """Run the reference SQL (trusted, written by us) and return its rows."""
    sql = question["reference"].replace("{today}", today.isoformat())
    execution = execute_query(sql, max_rows=100_000, use_authorizer=False)
    if execution.error:
        raise RuntimeError(f"Reference SQL for {question['id']} fails: {execution.error}")
    return execution.dataframe


def score_question(question: dict, result, today: date) -> tuple[bool, str]:
    """Return (correct?, short reason) for one pipeline result."""
    if question["expect"] == "refuse":
        if result.status in (STATUS_CANNOT_ANSWER, STATUS_BLOCKED):
            return True, f"refused ({result.status})"
        return False, f"returned data instead of refusing ({result.status})"

    if result.status != STATUS_SUCCESS:
        return False, f"{result.status}: {result.message}"[:200]
    reference = reference_result(question, today)
    if results_match(reference, result.dataframe, question.get("order_matters", False)):
        return True, "result matches reference"
    return False, f"wrong result ({len(result.dataframe)} rows vs {len(reference)} expected)"


def run(args: argparse.Namespace) -> dict:
    """Run the evaluation and return the full report (also saved as JSON)."""
    today = date.today()
    questions = load_questions(args.ids, PROJECT_ROOT / args.questions)

    if args.dry_run:
        llm = None  # a FakeLLM per question, answering with the reference SQL
    else:
        run_settings = replace(settings, llm_model=args.model) if args.model else settings
        llm = get_llm_client(run_settings)

    records = []
    for number, question in enumerate(questions, start=1):
        if args.dry_run:
            answer = question.get("reference") or "CANNOT_ANSWER: dry run"
            question_llm = FakeLLM(default_response=answer.replace("{today}", today.isoformat()))
        else:
            question_llm = llm

        result = ask(question["question"], llm=question_llm, today=today,
                     log_path=EVAL_LOG_PATH, allow_repair=not args.no_repair)
        correct, reason = score_question(question, result, today)
        records.append({
            "id": question["id"],
            "difficulty": question["difficulty"],
            "question": question["question"],
            "correct": correct,
            "reason": reason,
            "status": result.status,
            "repaired": result.repaired,
            "failed_check": result.validation.failed_check if result.validation else None,
            "generated_sql": result.generated_sql,
            "seconds": round(result.timings["total"], 2),
            "tokens": result.token_usage,
        })
        mark = "PASS" if correct else "FAIL"
        print(f"[{number:>2}/{len(questions)}] {mark}  {question['id']:<4} {result.timings['total']:5.1f}s  {reason[:80]}")

        if not args.dry_run and number < len(questions):
            time.sleep(args.delay)  # stay under the free tier's requests-per-minute limit

    report = build_report(records, args, today)
    save_report(report, args.label)
    print_summary(report)
    return report


def rescore(args: argparse.Namespace) -> dict:
    """
    Re-score a SAVED run against the CURRENT reference answers, without any API call.

    Used when a reference answer is corrected after review: the model's saved SQL
    is validated and executed again, then compared with the corrected reference,
    so earlier runs stay comparable. The saved run's date is used for "{today}".
    """
    saved_path = PROJECT_ROOT / args.rescore
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    today = date.fromisoformat(saved["today"])
    questions = {q["id"]: q for q in load_questions(None, PROJECT_ROOT / saved.get("questions_file", args.questions))}

    records = []
    for record in saved["records"]:
        question = questions[record["id"]]
        if question["expect"] == "rows" and record["status"] == STATUS_SUCCESS:
            # Replay the saved SQL through the same validator + executor.
            replay = FakeLLM(default_response=record["generated_sql"])
            result = ask(question["question"], llm=replay, today=today, log_path=EVAL_LOG_PATH, allow_repair=False)
            correct, reason = score_question(question, result, today)
            record = {**record, "correct": correct, "reason": reason}
        records.append(record)

    args.label = f"{saved['label']}_rescored"
    args.dry_run = False
    args.no_repair = not saved["repair_enabled"]
    args.model = saved["model"]
    args.questions = saved.get("questions_file", args.questions)
    report = build_report(records, args, today)
    report["rescored_from"] = args.rescore
    save_report(report, args.label)
    print_summary(report)
    return report


def build_report(records: list[dict], args: argparse.Namespace, today: date) -> dict:
    """Aggregate the per-question records into the summary metrics."""
    answerable = [r for r in records if r["difficulty"] != "unsafe"]
    unsafe = [r for r in records if r["difficulty"] == "unsafe"]
    accuracy = lambda rows: round(100 * sum(r["correct"] for r in rows) / len(rows), 1) if rows else None  # noqa: E731
    by_difficulty = {
        level: {"correct": sum(r["correct"] for r in records if r["difficulty"] == level),
                "total": sum(1 for r in records if r["difficulty"] == level)}
        for level in DIFFICULTY_ORDER if any(r["difficulty"] == level for r in records)
    }
    seconds = sorted(r["seconds"] for r in records)
    total_tokens = {}
    for r in records:
        for key, value in (r["tokens"] or {}).items():
            total_tokens[key] = total_tokens.get(key, 0) + value

    return {
        "label": args.label,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "provider": "dry-run" if args.dry_run else settings.llm_provider,
        "model": "reference SQL" if args.dry_run else (args.model or settings.llm_model),
        "repair_enabled": not args.no_repair,
        "questions_file": args.questions,
        "summary": {
            "overall_accuracy": accuracy(records),
            "answerable_accuracy": accuracy(answerable),
            "refusal_rate_unsafe": accuracy(unsafe),
            "correct": sum(r["correct"] for r in records),
            "total": len(records),
            "by_difficulty": by_difficulty,
            "false_refusals": sum(1 for r in answerable if r["status"] in (STATUS_CANNOT_ANSWER, STATUS_BLOCKED)),
            "repairs_used": sum(r["repaired"] for r in records),
            "avg_seconds": round(sum(seconds) / len(seconds), 2) if seconds else None,
            "p90_seconds": seconds[int(0.9 * (len(seconds) - 1))] if seconds else None,
            "tokens": total_tokens,
        },
        "failures": [r for r in records if not r["correct"]],
        "records": records,
    }


def save_report(report: dict, label: str) -> Path:
    """Write the report as JSON in eval/results/."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{stamp}_{label}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved: {path.relative_to(PROJECT_ROOT)}")
    return path


def print_summary(report: dict) -> None:
    """Print the headline numbers."""
    s = report["summary"]
    print("=" * 60)
    print(f"{report['label']}  |  model: {report['model']}  |  repair: {report['repair_enabled']}")
    print(f"Overall accuracy     : {s['overall_accuracy']}%  ({s['correct']}/{s['total']})")
    print(f"Answerable accuracy  : {s['answerable_accuracy']}%")
    print(f"Unsafe refused       : {s['refusal_rate_unsafe']}%")
    for level, counts in s["by_difficulty"].items():
        print(f"  {level:<10}: {counts['correct']}/{counts['total']}")
    print(f"False refusals: {s['false_refusals']}  |  repairs used: {s['repairs_used']}")
    print(f"Latency avg {s['avg_seconds']}s, p90 {s['p90_seconds']}s  |  tokens {s['tokens']}")
    if report["failures"]:
        print("Failures:")
        for f in report["failures"]:
            print(f"  {f['id']:<4} {f['reason'][:90]}")
    print("=" * 60)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Evaluate the NL-to-SQL assistant.")
    parser.add_argument("--label", default="run", help="name for this run (used in the file name)")
    parser.add_argument("--no-repair", action="store_true", help="disable the self-repair retry")
    parser.add_argument("--dry-run", action="store_true", help="use the reference SQL as the 'LLM' (tests the scorer, no API)")
    parser.add_argument("--model", help="override LLM_MODEL for this run")
    parser.add_argument("--ids", nargs="*", help="only run these question ids")
    parser.add_argument("--questions", default="eval/questions.yaml",
                        help="questions file (eval/holdout.yaml for the held-out set)")
    parser.add_argument("--rescore", help="re-score a saved results JSON against the current references (no API)")
    parser.add_argument("--delay", type=float, default=4.0, help="seconds between questions (rate limit)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    arguments = parse_args()
    if arguments.rescore:
        rescore(arguments)
    else:
        run(arguments)
