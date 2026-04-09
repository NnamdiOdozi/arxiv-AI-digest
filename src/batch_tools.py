#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from create_batch_evaluation import wait_for_batch
from get_papers import save_seen_papers
from main import write_results_markdown
from send_to_slack import get_top_papers

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


DEFAULT_CONFIG = {
    "selection": {"top_n": 10},
    "output": {"results_dir": "runs/results", "log_dir": "runs/logs"},
    "network": {
        "openai_retry_max_attempts": 8,
        "openai_retry_base_seconds": 5.0,
        "openai_retry_max_seconds": 120.0,
        "poll_interval_seconds": 30.0,
        "max_consecutive_poll_errors": 0,
    },
}


def load_runtime_config(config_path="config.toml"):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = Path(config_path)
    if not path.exists():
        return cfg

    with path.open("rb") as f:
        raw = tomllib.load(f)

    for section in ("selection", "output", "network"):
        if isinstance(raw.get(section), dict):
            cfg[section].update(raw[section])
    return cfg


def is_transient_network_error(err):
    transient_types = {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "ConnectError",
        "ReadTimeout",
        "WriteTimeout",
        "RemoteProtocolError",
        "NetworkError",
        "TimeoutException",
    }
    if err.__class__.__name__ in transient_types:
        return True

    text = str(err).lower()
    markers = [
        "connection error",
        "temporary failure in name resolution",
        "name resolution",
        "timed out",
        "timeout",
        "connection reset",
        "connection refused",
        "503",
        "502",
        "429",
    ]
    return any(marker in text for marker in markers)


def retry_call(operation_name, fn, network_cfg):
    attempts = int(network_cfg.get("openai_retry_max_attempts", 8))
    base_sleep = float(network_cfg.get("openai_retry_base_seconds", 5.0))
    max_sleep = float(network_cfg.get("openai_retry_max_seconds", 120.0))

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as err:
            if (not is_transient_network_error(err)) or attempt >= attempts:
                raise
            sleep_seconds = min(max_sleep, base_sleep * (2 ** min(attempt - 1, 6)))
            print(
                "Transient network error during %s (attempt %s/%s): %s. Retrying in %.1f seconds."
                % (operation_name, attempt, attempts, err, sleep_seconds)
            )
            time.sleep(sleep_seconds)


def get_client():
    load_dotenv()
    base_url = os.getenv("DW_BASE_URL")
    api_key = os.getenv("DW_API_KEY")
    if not base_url or not api_key:
        raise RuntimeError("Missing DW_BASE_URL or DW_API_KEY in environment/.env")
    return OpenAI(base_url=base_url, api_key=api_key)


def find_latest_batch_id(log_dir):
    log_root = Path(log_dir)
    if not log_root.exists():
        return None, None

    log_files = sorted(log_root.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    batch_pattern = re.compile(r"Batch submitted:\s*([a-zA-Z0-9-]+)")
    for log_file in log_files:
        text = log_file.read_text(encoding="utf-8", errors="replace")
        matches = batch_pattern.findall(text)
        if matches:
            return matches[-1], str(log_file)
    return None, None


def find_run_log_for_batch(batch_id, log_dir):
    log_root = Path(log_dir)
    if not log_root.exists():
        return None

    log_files = sorted(log_root.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    marker = f"Batch submitted: {batch_id}"
    for log_file in log_files:
        text = log_file.read_text(encoding="utf-8", errors="replace")
        if marker in text:
            return str(log_file)
    return None


def extract_run_timestamp_from_log_path(log_path):
    if not log_path:
        return None
    name = Path(log_path).name
    match = re.match(r"run_(\d{8}_\d{6})\.log$", name)
    if match:
        return match.group(1)
    return None


def resolve_batch_id(explicit_batch_id, config):
    if explicit_batch_id:
        return explicit_batch_id
    latest_id, source_log = find_latest_batch_id(config["output"]["log_dir"])
    if latest_id:
        print(f"Using latest batch id from log {source_log}: {latest_id}")
        return latest_id
    raise RuntimeError("No --batch-id provided and no batch id found in run logs.")


def parse_prompt_title_abstract(prompt_text):
    title_match = re.search(r"^\s*TITLE:\s*\n\s*(.+?)\s*(?:\n|$)", prompt_text, flags=re.MULTILINE)
    abstract_match = re.search(
        r"^\s*ABSTRACT:\s*\n(.*?)^\s*INSTRUCTIONS:\s*$",
        prompt_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    title = title_match.group(1).strip() if title_match else ""
    abstract = abstract_match.group(1).strip() if abstract_match else ""
    return title, abstract


def parse_batch_request_file(batch_request_path):
    """Build paper metadata map from batch_requests_*.jsonl file."""
    papers = {}
    path = Path(batch_request_path)
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent / path).resolve()
    if not path.exists():
        return papers

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            paper_id = item.get("custom_id")
            if not paper_id:
                continue
            content = ""
            messages = item.get("body", {}).get("messages", [])
            for m in messages:
                if m.get("role") == "user":
                    content = m.get("content", "")
                    break
            title, abstract = parse_prompt_title_abstract(content)
            papers[paper_id] = {
                "id": paper_id,
                "title": title or paper_id,
                "abstract": abstract,
                "url": f"http://arxiv.org/abs/{paper_id}",
                "published": None,
            }
    return papers


def parse_log_context(log_path):
    """Extract run metadata + paper metadata from run log."""
    context = {
        "max_results": None,
        "cutoff_date": None,
        "batch_request_file": None,
        "papers_by_id": {},
    }
    if not log_path:
        return context

    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    max_results_match = re.search(r"Search max_results:\s*(\d+)", text)
    if max_results_match:
        context["max_results"] = int(max_results_match.group(1))

    batch_file_match = re.search(r"Batch request file created:\s*([^\s]+\.jsonl)", text)
    if batch_file_match:
        context["batch_request_file"] = batch_file_match.group(1)

    # Parse arXiv trace lines to recover published/title fields.
    pattern = re.compile(r"\[arxiv_raw\].*id=([^\s]+).*published=([^\s]+)\s+title=(.*)")
    for line in lines:
        m = pattern.search(line)
        if not m:
            continue
        paper_id, published, title = m.group(1), m.group(2), m.group(3).strip()
        context["papers_by_id"][paper_id] = {
            "id": paper_id,
            "title": title or paper_id,
            "url": f"http://arxiv.org/abs/{paper_id}",
            "published": published,
            "abstract": "",
        }

    return context


def merge_paper_metadata(log_context):
    papers = {}
    papers.update(log_context.get("papers_by_id", {}))
    batch_file = log_context.get("batch_request_file")
    if batch_file:
        from_batch = parse_batch_request_file(batch_file)
        for paper_id, info in from_batch.items():
            existing = papers.get(paper_id, {})
            merged = {**info, **existing}
            # Preserve abstract from batch file even if log context exists.
            merged["abstract"] = info.get("abstract", existing.get("abstract", ""))
            papers[paper_id] = merged
    return papers


def build_paper_pool_from_results(results, papers_by_id):
    pool = []
    seen = set()
    for result in results:
        paper_id = result.get("paper_id")
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)
        info = papers_by_id.get(
            paper_id,
            {
                "id": paper_id,
                "title": paper_id,
                "abstract": "",
                "url": f"http://arxiv.org/abs/{paper_id}",
                "published": None,
            },
        )
        pool.append(info)
    return pool


def cmd_status(args, config):
    batch_id = resolve_batch_id(args.batch_id, config)
    client = get_client()

    batch = retry_call("batch status retrieve", lambda: client.batches.retrieve(batch_id), config["network"])
    print(f"batch_id: {batch.id}")
    print(f"status: {batch.status}")
    print(f"completed: {batch.request_counts.completed}/{batch.request_counts.total}")
    print(f"output_file_id: {batch.output_file_id}")
    return 0


def cmd_resume(args, config):
    batch_id = resolve_batch_id(args.batch_id, config)
    log_path = find_run_log_for_batch(batch_id, config["output"]["log_dir"])
    if log_path:
        print(f"Matched run log: {log_path}")
    else:
        print("No matching run log found for this batch id. Continuing with minimal metadata.")

    client = get_client()

    poll_interval = float(config["network"]["poll_interval_seconds"])
    results, _ = wait_for_batch(
        client,
        batch_id,
        check_interval=poll_interval,
        network_retry_base_seconds=float(config["network"]["openai_retry_base_seconds"]),
        network_retry_max_seconds=float(config["network"]["openai_retry_max_seconds"]),
        max_consecutive_poll_errors=int(config["network"]["max_consecutive_poll_errors"]),
    )

    if not results:
        print("No results returned yet (batch still running or network still unstable).")
        return 0

    output_dir = config["output"]["results_dir"]
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    stamp = extract_run_timestamp_from_log_path(log_path) or datetime.now().strftime("%Y%m%d_%H%M%S")

    parsed_dir = Path(output_dir) / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    parsed_path = parsed_dir / f"batch_{batch_id}_{stamp}_parsed.json"
    with parsed_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Parsed results saved to: {parsed_path}")

    top_n = args.top_n if args.top_n is not None else int(config["selection"]["top_n"])
    top_results = get_top_papers(results, top_n=top_n)

    log_context = parse_log_context(log_path)
    papers_by_id = merge_paper_metadata(log_context)
    paper_pool = build_paper_pool_from_results(results, papers_by_id)

    digest_path = write_results_markdown(
        top_results=top_results,
        paper_pool=paper_pool,
        all_scores=results,
        run_timestamp=stamp,
        output_dir=output_dir,
        cutoff_date=log_context.get("cutoff_date"),
        max_results=log_context.get("max_results") or len(results),
        top_n=top_n,
    )
    print(f"Digest markdown saved to: {digest_path}")

    if not args.no_save_seen:
        save_seen_papers([r.get("paper_id") for r in results if r.get("paper_id")])
        print("seen_papers.json updated.")
    else:
        print("Skipped seen_papers update (--no-save-seen).")

    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Check and resume existing Doubleword batch runs.")
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: config.toml)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show current status for a batch.")
    status.add_argument("--batch-id", help="Existing batch ID. If omitted, uses latest from run logs.")

    resume = subparsers.add_parser("resume", help="Wait for a submitted batch to finish and write outputs.")
    resume.add_argument("--batch-id", help="Existing batch ID. If omitted, uses latest from run logs.")
    resume.add_argument("--top-n", type=int, default=None, help="Override top_n for digest output.")
    resume.add_argument(
        "--no-save-seen",
        action="store_true",
        help="Do not update seen_papers.json after parsing results.",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    config = load_runtime_config(args.config)

    if args.command == "status":
        return cmd_status(args, config)
    if args.command == "resume":
        return cmd_resume(args, config)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
