import json
import logging
from pathlib import Path

import pandas as pd

from config_loader import REVIEW_DIGEST_FIELDS


def _format_markdown_value(value):
    if value is None:
        return "unknown"
    if isinstance(value, list):
        if not value:
            return "none"
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def write_arxiv_snapshot_json(
    run_timestamp,
    output_dir,
    cutoff_date,
    max_results,
    query_text,
    search_trace,
    papers_after_combined_filter,
    papers_unseen,
    dropped_seen,
    seen_registry,
    include_search_trace=False,
):
    """Persist arXiv-only snapshot for test/debug runs without LLM cost."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = Path(output_dir) / f"arxiv_search_{run_timestamp}.json"
    seen_registry = seen_registry or set()
    combined_ids = {paper["id"] for paper in papers_after_combined_filter}
    unseen_ids = {paper["id"] for paper in papers_unseen}
    dropped_seen_ids = {paper["id"] for paper in dropped_seen}

    annotated_search_trace = []
    for idx, entry in enumerate(search_trace, 1):
        annotated = dict(entry)
        paper_id = entry.get("id")
        annotated["trace_index"] = idx
        annotated["in_combined_filter"] = paper_id in combined_ids
        annotated["dropped_as_seen"] = paper_id in dropped_seen_ids
        annotated["unseen_after_seen_filter"] = paper_id in unseen_ids
        annotated["already_seen_in_registry"] = paper_id in seen_registry
        annotated_search_trace.append(annotated)

    def _annotate_items(items, index_key):
        annotated_items = []
        for idx, item in enumerate(items, 1):
            annotated = dict(item)
            annotated[index_key] = idx
            annotated_items.append(annotated)
        return annotated_items

    lookback_window_pass_count = sum(1 for entry in search_trace if entry.get("in_lookback_window"))
    domain_prefilter_pass_count = sum(1 for entry in search_trace if entry.get("matches_required_terms"))
    seen_matched_in_trace_count = sum(
        1 for entry in annotated_search_trace if entry.get("already_seen_in_registry")
    )
    seen_matched_in_combined_filter_count = sum(
        1 for paper in papers_after_combined_filter if paper.get("id") in seen_registry
    )

    payload = {
        "run_timestamp": run_timestamp,
        "cutoff_date": cutoff_date.isoformat() if cutoff_date else None,
        "max_results": max_results,
        "query": query_text,
        "snapshot_focus": "post_filter_candidates",
        "includes_search_trace": bool(include_search_trace),
        "section_order": [
            "papers_after_combined_filter",
            "dropped_as_seen",
            "papers_unseen_after_seen_filter",
        ],
        "counts": {
            "search_trace_total": len(search_trace),
            "lookback_window_pass_count": lookback_window_pass_count,
            "domain_prefilter_pass_count": domain_prefilter_pass_count,
            "after_combined_filter": len(papers_after_combined_filter),
            "unseen_after_seen_filter": len(papers_unseen),
            "dropped_seen": len(dropped_seen),
            "seen_registry_total": len(seen_registry),
            "seen_matched_in_trace_count": seen_matched_in_trace_count,
            "seen_matched_in_combined_filter_count": seen_matched_in_combined_filter_count,
        },
        "papers_after_combined_filter": _annotate_items(papers_after_combined_filter, "combined_filter_index"),
        "dropped_as_seen": _annotate_items(dropped_seen, "dropped_seen_index"),
        "papers_unseen_after_seen_filter": _annotate_items(papers_unseen, "unseen_index"),
    }
    if include_search_trace:
        payload["search_trace"] = annotated_search_trace
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(_json_safe(payload), f, indent=2)
    return str(output_path)


def setup_run_logger(log_dir, run_timestamp):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / f"run_{run_timestamp}.log"

    logger = logging.getLogger(f"arxiv_digest_{run_timestamp}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger, str(log_path)


def write_results_markdown(
    top_results,
    paper_pool,
    all_scores,
    run_timestamp,
    output_dir,
    cutoff_date,
    max_results,
    top_n,
    run_notes=None,
):
    """Write timestamped markdown digest with selected papers."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = Path(output_dir) / f"digest_{run_timestamp}.md"
    papers_by_id = {paper["id"]: paper for paper in paper_pool}
    cutoff_text = cutoff_date.isoformat() if cutoff_date else "default weekday fallback (Mon=3, others=1)"

    lines = [
        f"# Research Digest {run_timestamp}",
        "",
        "## Run Metadata",
        f"- max_results: {max_results}",
        f"- cutoff_date: {cutoff_text}",
        f"- scored_papers: {len(all_scores)}",
        f"- selected_top_n: {top_n}",
        f"- selected_count: {len(top_results)}",
        "",
        "## Final Selection",
    ]
    if run_notes:
        lines.extend([
            "",
            "## Run Notes",
        ])
        for note in run_notes:
            lines.append(f"- {note}")
        lines.append("")

    if not top_results:
        lines.extend([
            "",
            "No papers were selected in this run.",
        ])
    else:
        for rank, result in enumerate(top_results, 1):
            paper = papers_by_id.get(result["paper_id"])
            title = paper["title"] if paper else result["paper_id"]
            url = paper["url"] if paper else ""
            authors = paper.get("authors") if paper else None
            authors_text = ", ".join(authors) if authors else "unknown"
            published_value = paper.get("published") if paper else None
            if hasattr(published_value, "isoformat"):
                published = published_value.isoformat()
            elif published_value:
                published = str(published_value)
            else:
                published = "unknown"
            summary = result.get("summary")
            if not summary and paper:
                summary = paper["abstract"]
            summary = summary or "No summary available."

            if url:
                lines.append(f"### {rank}. [{title}]({url})")
            else:
                lines.append(f"### {rank}. {title}")
            lines.extend([
                f"- score: {result.get('relevance_score')}/10",
                f"- is_relevant: {result.get('is_relevant')}",
                f"- published: {published}",
                f"- authors: {authors_text}",
                f"- key_insight: {result.get('key_insight', '')}",
                "",
                summary,
                "",
            ])
            structured_review = result.get("structured_review")
            if isinstance(structured_review, dict) and structured_review:
                lines.append("Structured profile:")
                emitted_keys = set()
                for field_key, field_label in REVIEW_DIGEST_FIELDS:
                    if field_key not in structured_review:
                        continue
                    emitted_keys.add(field_key)
                    lines.append(
                        f"- {field_label}: {_format_markdown_value(structured_review.get(field_key))}"
                    )
                for field_key in sorted(k for k in structured_review.keys() if k not in emitted_keys):
                    field_label = field_key.replace("_", " ")
                    lines.append(
                        f"- {field_label}: {_format_markdown_value(structured_review.get(field_key))}"
                    )
                lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return str(output_path)


def _build_eval_row(result, paper, run_timestamp):
    authors = paper.get("authors") or []
    lead_author = authors[0] if authors else ""
    lead_author_surname = lead_author.split()[-1] if lead_author else ""
    author_surnames = ", ".join(a.split()[-1] for a in authors)
    authors_full = ", ".join(authors)
    published = paper.get("published")
    if hasattr(published, "isoformat"):
        published = published.isoformat()
    return {
        "run_timestamp": run_timestamp,
        "paper_id": result.get("paper_id"),
        "title": paper.get("title", ""),
        "paper_url": paper.get("url", ""),
        "published": published,
        "lead_author": lead_author,
        "lead_author_surname": lead_author_surname,
        "author_surnames": author_surnames,
        "authors_full": authors_full,
        "relevance_score": result.get("relevance_score"),
        "is_relevant": result.get("is_relevant"),
        "summary": result.get("summary", ""),
        "key_insight": result.get("key_insight", ""),
    }


def write_evaluation_results(results, papers, run_timestamp, output_dir):
    """Write all scored papers to evaluation_results_TIMESTAMP.csv/.xlsx."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    csv_path = Path(output_dir) / f"evaluation_results_{run_timestamp}.csv"
    xlsx_path = Path(output_dir) / f"evaluation_results_{run_timestamp}.xlsx"
    papers_by_id = {p["id"]: p for p in papers}
    rows = [_build_eval_row(r, papers_by_id.get(r.get("paper_id"), {}), run_timestamp) for r in results]
    df = pd.DataFrame(rows)
    df.to_csv(str(csv_path), index=False, encoding="utf-8-sig")
    df.to_excel(str(xlsx_path), index=False)
    return str(csv_path), str(xlsx_path)


def append_evaluation_results(results, papers_by_id, output_dir):
    """Append recovered results from offline requeue to the matching evaluation CSV/XLSX.

    Each entry in papers_by_id is expected to carry a ``run_timestamp`` field
    (stored when the failure was originally persisted) so we know which file to append to.
    Returns a list of (csv_path, xlsx_path, row_count) tuples — one per original run_timestamp.
    """
    groups = {}
    for result in results:
        pid = result.get("paper_id")
        paper = papers_by_id.get(pid, {})
        ts = paper.get("run_timestamp", "unknown")
        groups.setdefault(ts, []).append((result, paper))

    written = []
    for ts, pairs in groups.items():
        csv_path = Path(output_dir) / f"evaluation_results_{ts}.csv"
        xlsx_path = Path(output_dir) / f"evaluation_results_{ts}.xlsx"
        new_rows = [_build_eval_row(r, p, ts) for r, p in pairs]
        new_df = pd.DataFrame(new_rows)
        if csv_path.exists():
            existing = pd.read_csv(str(csv_path), encoding="utf-8-sig")
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df
        combined.to_csv(str(csv_path), index=False, encoding="utf-8-sig")
        combined.to_excel(str(xlsx_path), index=False)
        written.append((str(csv_path), str(xlsx_path), len(new_rows)))
    return written
