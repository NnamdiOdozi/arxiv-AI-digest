"""
Retry papers that failed to parse in a previous run.

Reads pipeline_data/parse_failures.json, resubmits to DoubleWord,
parses results, writes a supplementary digest, and removes successes
from the failures file.

Usage:
    python requeue_parse_failures.py
"""
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from config_loader import PROJECT_ROOT, load_runtime_config
from evaluation import create_batch_evaluation
from create_batch_evaluation import wait_for_batch
from network import call_with_network_retry

FAILURES_FILE = Path(PROJECT_ROOT) / "pipeline_data" / "parse_failures.json"


def _setup_log(log_dir):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = Path(log_dir) / f"requeue_{stamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(str(log_path)), logging.StreamHandler()],
    )
    return logging.getLogger(__name__), stamp


def _load_failures():
    if not FAILURES_FILE.exists():
        return {}
    try:
        return json.loads(FAILURES_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not read {FAILURES_FILE}: {exc}") from exc


def _save_failures(failures):
    FAILURES_FILE.parent.mkdir(parents=True, exist_ok=True)
    FAILURES_FILE.write_text(json.dumps(failures, indent=2, default=str), encoding="utf-8")


def _write_supplementary_digest(papers, results_by_id, output_dir, stamp):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(output_dir) / f"digest_{stamp}_supplementary.md"
    relevant = [r for r in results_by_id.values() if r.get("is_relevant")]
    relevant.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)

    lines = [
        f"# Supplementary Digest {stamp}",
        "",
        f"Recovered {len(relevant)} relevant papers from previous parse failures.",
        "",
        "## Recovered Papers",
    ]
    for rank, result in enumerate(relevant, 1):
        pid = result["paper_id"]
        paper = papers.get(pid, {})
        score = result.get("relevance_score", "?")
        title = paper.get("title", pid)
        url = paper.get("url", "")
        authors = paper.get("authors", [])
        author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        published = paper.get("published", "")
        published_str = str(published)[:10] if published else ""
        summary = result.get("summary") or ""
        key_insight = result.get("key_insight") or ""
        lines += [
            "",
            f"### {rank}. [{title}]({url}) [{score}/10]",
            f"*{author_str} — {published_str}*",
        ]
        if summary:
            lines.append(f"> {summary}")
        if key_insight:
            lines.append(f"**Key insight:** {key_insight}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out_path)


def main():
    load_dotenv()
    config = load_runtime_config()
    log, stamp = _setup_log(config["output"]["log_dir"])

    failures = _load_failures()
    if not failures:
        log.info("No pending parse failures. Nothing to do.")
        return

    log.info("Found %s papers in parse_failures.json", len(failures))

    papers = {pid: entry for pid, entry in failures.items()}
    paper_list = list(papers.values())

    api_key = os.getenv("DW_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("DW_BASE_URL", "https://api.doubleword.ai/v1")
    client = OpenAI(api_key=api_key, base_url=base_url)

    inference_config = config["inference"]
    network_config = config["network"]
    model_name = inference_config["model"]
    completion_window = inference_config.get("completion_window", "24h")

    log.info("Building batch requests for %s papers", len(paper_list))
    requests = create_batch_evaluation(paper_list, model_name=model_name, log_fn=log.info)

    batch_requests_dir = Path(config["output"]["batch_requests_dir"])
    batch_requests_dir.mkdir(parents=True, exist_ok=True)
    rq_file_path = str(batch_requests_dir / f"batch_requests_{stamp}_requeue_failures.jsonl")
    with open(rq_file_path, "w", encoding="utf-8") as f:
        for req in requests:
            f.write(json.dumps(req) + "\n")
    log.info("Batch requests written to %s", rq_file_path)

    def _upload():
        with open(rq_file_path, "rb") as s:
            return client.files.create(file=s, purpose="batch")

    batch_file = call_with_network_retry("batch upload", _upload, network_config, log.info)
    log.info("Uploaded batch file: %s", batch_file.id)

    batch = call_with_network_retry(
        "batch submission",
        lambda: client.batches.create(
            input_file_id=batch_file.id,
            endpoint="/v1/chat/completions",
            completion_window=completion_window,
        ),
        network_config,
        log.info,
    )
    log.info("Batch submitted: %s", batch.id)

    batch_debug_dir = str(Path(config["output"]["results_dir"]) / "parsed")
    results, still_failed = wait_for_batch(
        client,
        batch.id,
        check_interval=network_config["poll_interval_seconds"],
        log_fn=log.info,
        network_retry_base_seconds=network_config["openai_retry_base_seconds"],
        network_retry_max_seconds=network_config["openai_retry_max_seconds"],
        max_consecutive_poll_errors=network_config["max_consecutive_poll_errors"],
        debug_output_dir=batch_debug_dir,
        run_timestamp=stamp,
    )

    log.info("Parsed %s results; %s still failed", len(results), len(still_failed))

    results_by_id = {r["paper_id"]: r for r in results if r.get("paper_id")}

    if results_by_id:
        digest_path = _write_supplementary_digest(
            papers=papers,
            results_by_id=results_by_id,
            output_dir=config["output"]["results_dir"],
            stamp=stamp,
        )
        log.info("Supplementary digest written to %s", digest_path)

    # Remove successfully parsed papers from the failures file.
    updated_failures = {pid: entry for pid, entry in failures.items() if pid in still_failed}
    removed = len(failures) - len(updated_failures)
    _save_failures(updated_failures)
    log.info("Removed %s resolved papers from parse_failures.json; %s remain", removed, len(updated_failures))


if __name__ == "__main__":
    main()
