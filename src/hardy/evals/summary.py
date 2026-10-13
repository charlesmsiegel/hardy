"""A reproducible Markdown report, one row per compatible pooling group.

Boards are grouped by model and their recorded procedure/environment key.
Each group uses the same self-audit and duplicate-slot refusal as `pool`.
Different keys remain independent measurements, even for the same model.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hardy.corpus import taxonomy
from hardy.evals.pool import PoolRefused
from hardy.evals.pool import _wall_seconds_note as wall_seconds_note
from hardy.evals.pool import pool as pool_boards


class SummaryRefused(ValueError):
    """A group of boards for one model this will not summarize, and why.

    Always wraps a `PoolRefused` raised by pooling that model's own boards --
    the same refusal `evals pool` would give for the same boards, named by
    which model surfaced it.
    """


def discover_boards(scoreboards_root: Path) -> list[Path]:
    """Every scoreboard directory under `scoreboards_root`, sorted by label.

    Unlike `outstanding.matching_boards`, this keeps every board regardless of
    its own pooling key -- the whole point of this report is to span every
    model and every key a batch has ever run under, not one selected key.
    """
    if not scoreboards_root.exists():
        return []
    return [path.parent for path in sorted(scoreboards_root.glob("*/scoreboard.json"), key=lambda path: path.relative_to(scoreboards_root).as_posix())]


def _peek_group(board_dir: Path) -> tuple[str, str, str, str]:
    """Tolerantly read grouping metadata; `pool` remains the authority."""
    from hardy.evals.outstanding import environment_digest_of_board

    try:
        board = json.loads((board_dir / "scoreboard.json").read_text(encoding="utf-8"))
        condition = board["condition"]
        model = condition["model"]
        mode = condition["mode"]
        run = condition.get("run_procedure_digest")
        environment = environment_digest_of_board(board)
        if not all(isinstance(value, str) for value in (model, mode, environment)):
            raise ValueError("malformed grouping metadata")
        if run is not None and not isinstance(run, str):
            raise ValueError("malformed procedure digest")
        return model, mode, run or "", environment
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return f"(unreadable: {board_dir.name})", "", "", ""


def build(scoreboards_root: Path, *, problems_path: Path, baseline_path: Path) -> dict[str, Any]:
    """Pool each distinct model/procedure/environment group independently.

    Bad evidence and duplicate slots within a group still refuse the report.
    Multiple keys for one model are preserved as separate deterministic rows.
    """
    groups: dict[tuple[str, str, str, str], list[Path]] = {}
    for board_dir in discover_boards(scoreboards_root):
        groups.setdefault(_peek_group(board_dir), []).append(board_dir)
    models = []
    for key in sorted(groups):
        model, mode, _, _ = key
        try:
            pooled = pool_boards(groups[key], problems_path=problems_path, baseline_path=baseline_path)
        except PoolRefused as error:
            raise SummaryRefused(f"model {model!r}: {error}") from error
        models.append({
            "model": model, "mode": mode, "boards": pooled["boards"],
            "pooling_key": pooled["pooling_key"], "rows": pooled["rows"],
        })
    return {"scoreboards_root": scoreboards_root.as_posix(), "models": models}


def row_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Solved/rate and the token and wall-clock figures, over one group of rows.

    `solved` and `rate` are computed over rows whose entry has
    `expected == "true"` and whose `outcome` is not `"invalid"`: an invalid
    row is a run the audit could not make sense of, not a proof attempt that
    failed. Unreadable twins can also be invalid; both labels are excluded
    from their respective rates and reported separately.
    `solved_other` (solved by a route other than the expected one, `staged`
    mode's disputed-canonical case) is deliberately excluded from the
    numerator and given its own column instead, so it is not invisible.
    `invalid` is reported too, so a board with broken runs cannot silently
    shrink its own denominator without a visible trace.

    Every other figure -- tokens, cache, wall-clock, and their per-row
    averages -- is summed over *every* row in the group, exactly
    `scoreboard._totals`'s own convention: a twin or an invalid row still
    spent tokens and wall time even though it has no place in the solved
    fraction.

    `cost_usd` is the exception, and carries `rows_with_cost` beside it for
    the same reason `scoreboard.Totals` does: a run killed at its wall-clock
    limit never delivers the result message the cost is read from, so its
    `cost_usd` is `None` while its token counts are not. Summing those as
    zero and dividing by every row would quietly understate the price twice
    over, so the sum says how many rows it is a sum over and `cost_per_prob`
    divides by that same count rather than by `n`.
    """
    true_rows = [r for r in rows if r.get("expected") == "true"]
    countable_true = [r for r in true_rows if r.get("outcome") != "invalid"]
    solved = sum(1 for r in countable_true if r.get("outcome") == "solved")
    solved_other = sum(1 for r in countable_true if r.get("outcome") == "solved_other")
    invalid = sum(1 for r in rows if r.get("outcome") == "invalid")
    denominator = len(countable_true)
    rate = (solved / denominator * 100.0) if denominator else None
    n = len(rows)
    input_tokens = sum(r.get("input_tokens") or 0 for r in rows)
    output_tokens = sum(r.get("output_tokens") or 0 for r in rows)
    cache_read = sum(r.get("cache_read_tokens") or 0 for r in rows)
    cache_write = sum(r.get("cache_write_tokens") or 0 for r in rows)
    wall_seconds = sum(r.get("wall_seconds") or 0.0 for r in rows)
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    cost_usd = sum(costs)
    return {
        "solved": solved, "denominator": denominator, "rate": rate,
        "solved_other": solved_other, "invalid": invalid,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": cache_read, "cache_write_tokens": cache_write,
        "tok_per_prob": round((input_tokens + output_tokens) / n) if n else None,
        "wall_seconds": wall_seconds, "wall_per_prob": round(wall_seconds / n, 1) if n else None,
        "cost_usd": cost_usd, "rows_with_cost": len(costs),
        "cost_per_prob": (cost_usd / len(costs)) if costs else None,
        "rows": n,
    }


def twin_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Refused-over-twin-count, for rows whose entry has `expected == "false"`.

    A twin is supposed to be refused; that is why it is excluded from
    `row_stats`'s completion rate rather than folded into it, and reported
    here instead. Invalid twins are missing measurements: their count is
    visible, while `twins` is the measured denominator used for the rate.
    """
    twins = [r for r in rows if r.get("expected") == "false"]
    refused = sum(1 for r in twins if r.get("outcome") == "refused")
    invalid = sum(1 for r in twins if r.get("outcome") == "invalid")
    n = len(twins) - invalid
    return {"refused": refused, "twins": n, "invalid_twins": invalid,
            "rate": (refused / n * 100.0) if n else None}


def classify(entry: Any, root: Path) -> tuple[str, str]:
    """(2-digit MSC class, arXiv category) for one entry.

    The same derivation `viewer._classified` uses for the corpus page --
    `entry.msc[0][:2]` (also `entry.shard`) for the class, and
    `entry.arxiv_override or taxonomy.arxiv_of(entry.msc[0])` for the
    category -- read here rather than reinvented, so a report and the corpus
    page can never disagree about which bucket an entry falls in.
    """
    with taxonomy.using(root):
        return entry.msc[0][:2], (entry.arxiv_override or taxonomy.arxiv_of(entry.msc[0]))


def msc_label(code2: str, root: Path) -> str:
    """`"13 - Commutative algebra"`, or the bare code when the class-level
    name (`"<code>-XX"` in the vendored table) does not resolve.
    """
    with taxonomy.using(root):
        try:
            return f"{code2} - {taxonomy.name_of(f'{code2}-XX')}"
        except taxonomy.UnknownCode:
            return code2


def _group_by(rows: list[dict[str, Any]], problems: Any, root: Path, index: int) -> dict[str, list[dict[str, Any]]]:
    """Rows bucketed by `classify(...)[index]`; an id the corpus no longer
    carries (a later tombstone) is filed under `"unknown"` rather than
    dropped, so a row a board recorded is never silently missing from the
    report altogether.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            entry = problems.by_id(row["id"])
        except KeyError:
            key = "unknown"
        else:
            key = classify(entry, root)[index]
        groups.setdefault(key, []).append(row)
    return groups


def _fmt_rate(rate: float | None) -> str:
    return "-" if rate is None else f"{rate:.1f}%"


def _fmt_opt_int(value: int | None) -> str:
    return "-" if value is None else str(value)


def _fmt_cost(stats: dict[str, Any]) -> str:
    """The summed cost, and what it is a sum over when that is not every row.

    The `(covered/n)` suffix appears only when a row in the group reported no
    `cost_usd` at all, so a complete group reads as a plain number and an
    incomplete one cannot be mistaken for one.
    """
    covered, n = stats["rows_with_cost"], stats["rows"]
    if not covered:
        return "-"
    return f"{stats['cost_usd']:.2f}" + ("" if covered == n else f" ({covered}/{n})")


def _row_cells(stats: dict[str, Any]) -> list[str]:
    per = stats["cost_per_prob"]
    return [
        f"{stats['solved']}/{stats['denominator']}", _fmt_rate(stats["rate"]),
        str(stats["solved_other"]), str(stats["invalid"]),
        str(stats["input_tokens"]), str(stats["output_tokens"]),
        f"{stats['cache_read_tokens']}/{stats['cache_write_tokens']}",
        _fmt_opt_int(stats["tok_per_prob"]),
        f"{stats['wall_seconds']:.1f}", _fmt_opt_int(stats["wall_per_prob"]),
        _fmt_cost(stats), "-" if per is None else f"{per:.3f}",
    ]


_RESULT_COLUMNS = ("Solved", "Rate", "Solved other", "Invalid", "Input tok", "Output tok",
                   "Cache r/w", "Tok/prob", "Wall s", "Wall/prob", "Cost $", "$/prob")


def _table(header_extra: tuple[str, ...], rows: list[tuple[list[str], dict[str, Any]]]) -> list[str]:
    header = ("Model", *header_extra, *_RESULT_COLUMNS)
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for label_cells, stats in rows:
        lines.append("| " + " | ".join([*label_cells, *_row_cells(stats)]) + " |")
    return lines


def render(data: dict[str, Any], *, problems: Any, root: Path) -> str:
    """The full report text for `build`'s output. Deterministic: the same
    `data`, `problems` and `root` always render the same bytes, so
    regenerating the file with unchanged scoreboards produces no diff.
    """
    models = sorted(data["models"], key=lambda m: (
        m["model"], m.get("mode", ""), m["pooling_key"]["run_procedure_digest"],
        m["pooling_key"]["environment_digest"],
    ))
    repeated_models = {m["model"] for m in models if sum(x["model"] == m["model"] for x in models) > 1}
    def label(m):
        if m["model"] not in repeated_models:
            return m["model"]
        pk = m["pooling_key"]
        return f"{m['model']} [{m.get('mode', 'unknown')}; {pk['run_procedure_digest']}; {pk['environment_digest']}]"
    lines = [
        "# Hardy Evals Summary",
        "",
        "**Generated by `hardy evals summary`; do not hand-edit.** This file is "
        "regenerated wholesale from the scoreboards below after every batch.",
        "",
        "A row counts as **solved** only when its run's `terminal_reason` is "
        '`"verified"` *and* its axiom audit reports `"clean"`: the proof compiles as '
        "the corpus entry's own declaration, is kernel-checked, forbids `sorryAx`, and "
        "rests on nothing beyond what was declared. A run that verifies only modulo an "
        "extra assumed axiom is not solved here -- \"solved\" is a stricter bar than "
        '"it compiled". `solved_other` (solved by a route other than the one expected) '
        "and `invalid` (a run the audit could not make sense of) are reported in their "
        "own columns rather than folded into the solved fraction.",
        "",
    ]
    if not models:
        lines.append(f"No scoreboards were found under `{data['scoreboards_root']}`.")
        return "\n".join(lines).rstrip("\n") + "\n"

    lines.append("## Scoreboards")
    lines.append("")
    for m in models:
        pk = m["pooling_key"]
        lines.append(
            f"- **{m['model']}**: boards {', '.join(m['boards'])}; pooling key "
            f"`run_procedure_digest={pk['run_procedure_digest']}`, "
            f"`environment_digest={pk['environment_digest']}`"
        )
    lines.append("")

    all_rows = [row for m in models for row in m["rows"]]
    workers = [row.get("workers") for row in all_rows if row.get("workers") is not None]
    lines.append(f"Every Wall s / Wall/prob figure in this report was {wall_seconds_note(max(workers) if workers else None)}.")
    lines.append("")
    lines.append(
        "**Cost $** is each run's own recorded `cost_usd`, summed -- the price the "
        "provider reported, not a figure re-derived here from the token columns. It "
        "therefore already prices cache reads and writes at their own rates, which the "
        "token columns do not: **Input tok** counts only *uncached* input, so the bulk "
        "of what the model read appears under **Cache r/w** instead, and **Tok/prob** "
        "((Input + Output) / rows) excludes cache entirely. Prefer **$/prob** over "
        "**Tok/prob** when comparing models. A cost cell reading `12.34 (35/37)` is a "
        "sum over 35 of the group's 37 rows: a run killed at its wall-clock limit "
        "never delivers the message its cost is read from. **$/prob** divides by that "
        "same covered count."
    )
    lines.append("")

    lines.append("## Table 1: Overall")
    lines.append("")
    lines.extend(_table((), [([label(m)], row_stats(m["rows"])) for m in models]))
    lines.append("")

    lines.append("## Table 2: By MSC (primary, first two digits)")
    lines.append("")
    msc_rows = []
    for m in models:
        buckets = _group_by(m["rows"], problems, root, 0)
        for code in sorted(buckets):
            msc_name = msc_label(code, root) if code != "unknown" else "unknown"
            msc_rows.append(([label(m), msc_name], row_stats(buckets[code])))
    lines.extend(_table(("MSC",), msc_rows))
    lines.append("")

    lines.append("## Table 3: By arXiv category")
    lines.append("")
    arxiv_rows = []
    for m in models:
        buckets = _group_by(m["rows"], problems, root, 1)
        for category in sorted(buckets):
            arxiv_rows.append(([label(m), category], row_stats(buckets[category])))
    lines.extend(_table(("arXiv",), arxiv_rows))
    lines.append("")

    lines.append("## Table 4: Twins (expected false; refused is correct)")
    lines.append("")
    lines.append("| Model | Refused | Measured twins | Invalid twins | Refusal rate |")
    lines.append("|---|---|---|---|---|")
    for m in models:
        stats = twin_stats(m["rows"])
        lines.append(f"| {label(m)} | {stats['refused']} | {stats['twins']} | {stats['invalid_twins']} | {_fmt_rate(stats['rate'])} |")
    lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def write(scoreboards_root: Path, *, problems_path: Path, baseline_path: Path, out_path: Path) -> Path:
    """Build, render and write the report; the only function that touches disk
    for its own output. Never touches a scoreboard.
    """
    from hardy.corpus.catalog import load_corpus

    data = build(scoreboards_root, problems_path=problems_path, baseline_path=baseline_path)
    problems = load_corpus(problems_path)
    text = render(data, problems=problems, root=problems_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": the same repository-evidence integrity concern as the
    # baseline, scoreboard and pool writes -- Path.write_text's default would
    # check this in as CRLF on Windows.
    from hardy.foundation.locking import atomic_write_bytes

    atomic_write_bytes(out_path, text.encode("utf-8"))
    return out_path
