import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from config_loader import (
    DEFAULT_LOOKBACK_CONFIG,
    DEFAULT_MAX_RESULTS,
    DEFAULT_SEARCH_TUNING_CONFIG,
    DEFAULT_QUERY_CONFIG,
    DEFAULT_SELECTION_CONFIG,
    DEFAULT_OUTPUT_CONFIG,
    DEFAULT_INFERENCE_CONFIG,
    DEFAULT_NETWORK_CONFIG,
    DEFAULT_REVIEW_CONFIG,
    PROJECT_ROOT,
    load_runtime_config,
    build_query_and_prefilter_terms,
)
from network import call_with_network_retry, resolve_cutoff_date
from evaluation import create_batch_evaluation
from review import load_review_questions, enrich_top_papers_with_structured_review
from output import (
    write_arxiv_snapshot_json,
    write_results_markdown,
    setup_run_logger,
)
from create_batch_evaluation import wait_for_batch
from get_papers import get_daily_papers, filter_unseen_papers_with_trace, load_seen_papers, save_seen_papers
from send_to_slack import send_to_slack, get_top_papers

# Load .env file if it exists (local development)
if os.path.exists(".env"):
    load_dotenv()
    if __name__ == "__main__":
        print("Loaded .env file")

DW_API_KEY = os.getenv("DW_API_KEY")
DW_BASE_URL = os.getenv("DW_BASE_URL")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

client = OpenAI(base_url=DW_BASE_URL, api_key=DW_API_KEY)


def _persist_parse_failures(failed_paper_ids, papers_by_id, batch_id, run_timestamp, failures_file, log_fn=None):
    """Append papers that could not be parsed to the persistent failures tracker."""
    path = Path(failures_file)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    else:
        existing = {}
    for pid in failed_paper_ids:
        if pid in existing:
            continue
        paper = papers_by_id.get(pid)
        if not paper:
            continue
        entry = dict(paper)
        entry["batch_id"] = batch_id
        entry["run_timestamp"] = run_timestamp
        existing[pid] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, default=str), encoding="utf-8")
    if log_fn:
        log_fn("[parse_failures] Persisted %s unresolved failures to %s" % (len(failed_paper_ids), failures_file))


def daily_run(
    lookback_config=None,
    max_results=DEFAULT_MAX_RESULTS,
    query_config=None,
    selection_config=None,
    output_config=None,
    inference_config=None,
    search_tuning_config=None,
    network_config=None,
    review_config=None,
    arxiv_only=False,
    run_timestamp=None,
    logger=None,
):
    """Run pipeline end-to-end and return results markdown path."""
    lookback_config = lookback_config or DEFAULT_LOOKBACK_CONFIG
    query_config = query_config or DEFAULT_QUERY_CONFIG
    selection_config = selection_config or DEFAULT_SELECTION_CONFIG
    output_config = output_config or DEFAULT_OUTPUT_CONFIG
    inference_config = inference_config or DEFAULT_INFERENCE_CONFIG
    search_tuning_config = search_tuning_config or DEFAULT_SEARCH_TUNING_CONFIG
    network_config = network_config or DEFAULT_NETWORK_CONFIG
    review_config = review_config or DEFAULT_REVIEW_CONFIG
    run_timestamp = run_timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    log = logger.info if logger else print

    top_n = selection_config["top_n"]
    model_name = inference_config["model"]
    cutoff_date = resolve_cutoff_date(lookback_config)
    (
        arxiv_query,
        required_terms,
        required_min_matches,
        anchor_terms,
        term_match_mode,
    ) = build_query_and_prefilter_terms(query_config)
    log("Starting pipeline run")
    log(f"Pipeline mode: {'arxiv_only' if arxiv_only else 'full'}")
    log(f"Lookback config: {lookback_config}")
    log(f"Search max_results: {max_results}")
    log(
        "Query config: use_and_query=%s, enforce_domain_prefilter=%s, prefilter_min_matches=%s, require_anchor_match=%s, term_match_mode=%s"
        % (
            query_config["use_and_query"],
            query_config["enforce_domain_prefilter"],
            required_min_matches,
            query_config["require_anchor_match"],
            term_match_mode,
        )
    )
    log(f"Selection top_n: {top_n}")
    log(f"Inference model: {model_name}")
    log(
        "Structured review config: enabled=%s, mode=%s, file=%s, max_questions=%s, include_in_digest=%s"
        % (
            review_config["enabled"],
            review_config["mode"],
            review_config["questions_file"],
            review_config["max_questions"],
            review_config["include_in_digest"],
        )
    )
    log(f"arXiv tuning: {search_tuning_config}")
    log(f"Network retry config: {network_config}")
    log("LLM payload source: arXiv title + abstract metadata only (no PDF/full-paper download).")
    log(f"arXiv query: {arxiv_query}")
    if required_terms:
        log(f"Domain prefilter terms: {required_terms}")
    else:
        log("Domain prefilter terms: disabled")
    if anchor_terms:
        log(f"Anchor terms: {anchor_terms}")
    else:
        log("Anchor terms: disabled")

    # 1) Fetch papers
    log("Fetching papers from arXiv")
    try:
        papers_after_filters, search_trace = get_daily_papers(
            max_results=max_results,
            cutoff_date=cutoff_date,
            return_trace=True,
            query_override=arxiv_query,
            required_text_terms=required_terms,
            required_text_min_matches=required_min_matches,
            required_anchor_terms=anchor_terms,
            term_match_mode=term_match_mode,
            arxiv_page_size=search_tuning_config["arxiv_page_size"],
            arxiv_delay_seconds=search_tuning_config["arxiv_delay_seconds"],
            arxiv_num_retries=search_tuning_config["arxiv_num_retries"],
            arxiv_max_attempts=search_tuning_config["arxiv_max_attempts"],
            arxiv_backoff_seconds=search_tuning_config["arxiv_backoff_seconds"],
            log_fn=log,
        )
    except Exception as exc:
        error_note = f"arXiv fetch failed after retries: {exc}"
        log(error_note)
        results_path = write_results_markdown(
            top_results=[],
            paper_pool=[],
            all_scores=[],
            run_timestamp=run_timestamp,
            output_dir=output_config["results_dir"],
            cutoff_date=cutoff_date,
            max_results=max_results,
            top_n=top_n,
            run_notes=[error_note],
        )
        log(f"Results markdown written to {results_path}")
        return results_path
    log(f"arXiv candidates returned: {len(search_trace)}")
    for idx, entry in enumerate(search_trace, 1):
        log(
            "[arxiv_raw] %s/%s id=%s in_window=%s domain_match=%s prefilter_hits=%s anchor_hits=%s published=%s lead_author=%s title=%s"
            % (
                idx,
                len(search_trace),
                entry["id"],
                entry["in_lookback_window"],
                entry["matches_required_terms"],
                entry.get("matched_required_terms", []),
                entry.get("matched_anchor_terms", []),
                entry["published"],
                entry.get("lead_author"),
                entry["title"],
            )
        )

    lookback_pass = [entry for entry in search_trace if entry["in_lookback_window"]]
    dropped_by_domain_prefilter = [
        entry for entry in search_trace if entry["in_lookback_window"] and not entry["matches_required_terms"]
    ]
    log(f"After lookback filter: {len(lookback_pass)}")
    log(f"Dropped by domain prefilter: {len(dropped_by_domain_prefilter)}")
    for entry in dropped_by_domain_prefilter:
        log(
            "[drop_domain_prefilter] id=%s title=%s prefilter_hits=%s anchor_hits=%s"
            % (
                entry["id"],
                entry["title"],
                entry.get("matched_required_terms", []),
                entry.get("matched_anchor_terms", []),
            )
        )

    papers, dropped_seen = filter_unseen_papers_with_trace(papers_after_filters)
    seen_registry = load_seen_papers()
    log(f"After combined query+prefilter: {len(papers_after_filters)}")
    log(f"Dropped as already seen: {len(dropped_seen)}")
    log(f"Seen registry total: {len(seen_registry)}")
    for paper in dropped_seen:
        log(f"[drop_seen] id={paper['id']} title={paper['title']}")

    snapshot_path = write_arxiv_snapshot_json(
        run_timestamp=run_timestamp,
        output_dir=output_config["search_results_dir"],
        cutoff_date=cutoff_date,
        max_results=max_results,
        query_text=arxiv_query,
        search_trace=search_trace,
        papers_after_combined_filter=papers_after_filters,
        papers_unseen=papers,
        dropped_seen=dropped_seen,
        seen_registry=seen_registry,
        include_search_trace=output_config["arxiv_snapshot_include_search_trace"],
    )
    log(f"Search snapshot written to {snapshot_path}")

    if arxiv_only:
        log(f"arXiv-only snapshot written to {snapshot_path}")
        results_path = write_results_markdown(
            top_results=[],
            paper_pool=papers_after_filters,
            all_scores=[],
            run_timestamp=run_timestamp,
            output_dir=output_config["results_dir"],
            cutoff_date=cutoff_date,
            max_results=max_results,
            top_n=top_n,
            run_notes=[
                "Run executed in arXiv-only mode (no Doubleword/OpenAI inference).",
                f"Snapshot JSON: {snapshot_path}",
                f"Candidates after combined filter: {len(papers_after_filters)}",
                f"Unseen candidates after seen filter: {len(papers)}",
            ],
        )
        log(f"Results markdown written to {results_path}")
        return results_path

    if not papers:
        log("No new unseen papers after filtering")
        results_path = write_results_markdown(
            top_results=[],
            paper_pool=[],
            all_scores=[],
            run_timestamp=run_timestamp,
            output_dir=output_config["results_dir"],
            cutoff_date=cutoff_date,
            max_results=max_results,
            top_n=top_n,
            run_notes=None,
        )
        log(f"Results markdown written to {results_path}")
        return results_path

    log(f"Papers to score: {len(papers)}")

    # 2) Create batch jsonl
    requests = create_batch_evaluation(papers, model_name=model_name, log_fn=log)
    batch_requests_dir = Path(output_config["batch_requests_dir"])
    batch_requests_dir.mkdir(parents=True, exist_ok=True)
    batch_file_path = str(batch_requests_dir / f"batch_requests_{run_timestamp}.jsonl")
    with open(batch_file_path, "w", encoding="utf-8") as f:
        for req in requests:
            f.write(json.dumps(req) + "\n")
    log(f"Batch request file created: {batch_file_path}")

    # 3) Submit batch
    completion_window = inference_config.get("completion_window", "24h")

    def _upload_batch_file():
        with open(batch_file_path, "rb") as batch_stream:
            return client.files.create(
                file=batch_stream,
                purpose="batch"
            )

    batch_file = call_with_network_retry(
        "batch input upload",
        _upload_batch_file,
        network_config,
        log,
    )
    log(f"Uploaded batch input file: {batch_file.id}")

    batch = call_with_network_retry(
        "batch submission",
        lambda: client.batches.create(
            input_file_id=batch_file.id,
            endpoint="/v1/chat/completions",
            completion_window=completion_window,
        ),
        network_config,
        log,
    )
    log(f"Batch submitted: {batch.id}")
    log("This pipeline uses Doubleword/OpenAI Batch API (asynchronous batch inference)")

    # 4) Wait for results
    batch_debug_dir = str(Path(output_config["results_dir"]) / "parsed")
    results, failed_paper_ids = wait_for_batch(
        client,
        batch.id,
        check_interval=network_config["poll_interval_seconds"],
        log_fn=log,
        network_retry_base_seconds=network_config["openai_retry_base_seconds"],
        network_retry_max_seconds=network_config["openai_retry_max_seconds"],
        max_consecutive_poll_errors=network_config["max_consecutive_poll_errors"],
        debug_output_dir=batch_debug_dir,
        run_timestamp=run_timestamp,
    )
    if not results and not failed_paper_ids:
        log("No results returned from batch job")
        results_path = write_results_markdown(
            top_results=[],
            paper_pool=papers,
            all_scores=[],
            run_timestamp=run_timestamp,
            output_dir=output_config["results_dir"],
            cutoff_date=cutoff_date,
            max_results=max_results,
            top_n=top_n,
            run_notes=None,
        )
        log(f"Results markdown written to {results_path}")
        return results_path

    requeue_max_rounds = inference_config.get("requeue_max_rounds", 0)
    papers_by_id = {p["id"]: p for p in papers}
    for requeue_round in range(requeue_max_rounds):
        if not failed_paper_ids:
            break
        failed_papers = [papers_by_id[pid] for pid in failed_paper_ids if pid in papers_by_id]
        if not failed_papers:
            break
        log(
            "[requeue] Round %s/%s: resubmitting %s papers that failed to parse"
            % (requeue_round + 1, requeue_max_rounds, len(failed_papers))
        )
        rq_requests = create_batch_evaluation(failed_papers, model_name=model_name, log_fn=log)
        rq_file_path = str(
            batch_requests_dir / ("batch_requests_%s_requeue%s.jsonl" % (run_timestamp, requeue_round + 1))
        )
        with open(rq_file_path, "w", encoding="utf-8") as f:
            for req in rq_requests:
                f.write(json.dumps(req) + "\n")

        def _upload_rq_file():
            with open(rq_file_path, "rb") as s:
                return client.files.create(file=s, purpose="batch")

        rq_batch_file = call_with_network_retry("requeue batch upload", _upload_rq_file, network_config, log)
        rq_batch = call_with_network_retry(
            "requeue batch submission",
            lambda: client.batches.create(
                input_file_id=rq_batch_file.id,
                endpoint="/v1/chat/completions",
                completion_window=completion_window,
            ),
            network_config,
            log,
        )
        log("[requeue] Requeue batch submitted: %s" % rq_batch.id)
        rq_results, failed_paper_ids = wait_for_batch(
            client,
            rq_batch.id,
            check_interval=network_config["poll_interval_seconds"],
            log_fn=log,
            network_retry_base_seconds=network_config["openai_retry_base_seconds"],
            network_retry_max_seconds=network_config["openai_retry_max_seconds"],
            max_consecutive_poll_errors=network_config["max_consecutive_poll_errors"],
            debug_output_dir=batch_debug_dir,
            run_timestamp=run_timestamp,
        )
        results = results + rq_results
        log("[requeue] Recovered %s additional results" % len(rq_results))

    log(f"Parsed evaluations: {len(results)}")
    for result in sorted(results, key=lambda r: r.get("relevance_score", -1), reverse=True):
        log(
            "[score] id=%s score=%s relevant=%s needs_summary=%s"
            % (
                result.get("paper_id"),
                result.get("relevance_score"),
                result.get("is_relevant"),
                result.get("needs_summary"),
            )
        )

    queued_paper_ids = {paper["id"] for paper in papers}
    parsed_result_ids = []
    for result in results:
        paper_id = result.get("paper_id")
        if not paper_id:
            continue
        if paper_id not in queued_paper_ids:
            log(f"[warn] Parsed result paper_id not in queued set: {paper_id}")
            continue
        parsed_result_ids.append(paper_id)
    # Persist remaining parse failures for later retry via requeue_parse_failures.py
    if failed_paper_ids:
        _persist_parse_failures(
            failed_paper_ids=failed_paper_ids,
            papers_by_id=papers_by_id,
            batch_id=batch.id,
            run_timestamp=run_timestamp,
            failures_file=str(Path(PROJECT_ROOT) / "pipeline_data" / "parse_failures.json"),
            log_fn=log,
        )
    # Only mark papers as seen if they were successfully parsed by DoubleWord.
    # Parse failures are retried via requeue_parse_failures.py.
    seen_ids_to_save = sorted(set(parsed_result_ids))
    missing_result_count = len(queued_paper_ids - set(parsed_result_ids))
    if missing_result_count > 0:
        log(
            "[warn] Parsed results cover %s/%s queued papers; %s had parse failures and will NOT be marked seen (eligible for retry)."
            % (len(parsed_result_ids), len(queued_paper_ids), missing_result_count)
        )

    # 5) Select final papers
    top_papers = get_top_papers(results, top_n=top_n, log_fn=log)
    review_mode = review_config["mode"]
    if top_papers and review_config["enabled"] and review_mode == "inline":
        if review_config["include_in_digest"]:
            question_specs = load_review_questions(review_config, log)
            log("Structured review input source: title + abstract only (no PDF/full-text fetch).")
            papers_by_id = {paper["id"]: paper for paper in papers}
            enrich_top_papers_with_structured_review(
                top_results=top_papers,
                papers_by_id=papers_by_id,
                question_specs=question_specs,
                model_name=model_name,
                network_config=network_config,
                log=log,
                client=client,
            )
        else:
            log("Structured review inline mode enabled, but include_in_digest=false; skipping inline pass.")
    elif review_config["enabled"] and review_mode == "separate":
        log(
            "Structured review mode is 'separate': skipping inline pass. "
            "Run `python run_structured_review.py` after this run to enrich top papers."
        )
    elif (not review_config["enabled"]) or review_mode == "off":
        log("Structured review disabled for this run.")
    elif not top_papers:
        log("No top papers selected; skipping structured review.")

    results_path = write_results_markdown(
        top_results=top_papers,
        paper_pool=papers,
        all_scores=results,
        run_timestamp=run_timestamp,
        output_dir=output_config["results_dir"],
        cutoff_date=cutoff_date,
        max_results=max_results,
        top_n=top_n,
        run_notes=None,
    )
    log(f"Results markdown written to {results_path}")

    # Optional Slack delivery
    if output_config["send_to_slack"]:
        if SLACK_WEBHOOK_URL:
            send_to_slack(top_papers, papers, SLACK_WEBHOOK_URL)
            log("Slack delivery complete")
        else:
            log("Slack delivery requested but SLACK_WEBHOOK_URL is not set")
    else:
        log("Slack delivery disabled by config")

    # Mark all queued papers as seen (regardless of parse outcome).
    if seen_ids_to_save:
        save_seen_papers(seen_ids_to_save)
        log("Updated seen_papers tracking for %s queued papers" % len(seen_ids_to_save))
    else:
        log("Skipped seen_papers update: no papers were queued")
    return results_path


def parse_cli_args():
    parser = argparse.ArgumentParser(description="Run arXiv digest pipeline.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--arxiv-only",
        action="store_true",
        help="Run only arXiv search/filter stages and save snapshot outputs (no LLM calls).",
    )
    mode_group.add_argument(
        "--full",
        action="store_true",
        help="Force full pipeline mode (default behavior).",
    )
    return parser.parse_args()


def run_main():
    cli_args = parse_cli_args()
    runtime_config = load_runtime_config()
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_logger, log_file_path = setup_run_logger(runtime_config["output"]["log_dir"], run_timestamp)
    run_logger.info(f"Run log file: {log_file_path}")

    try:
        arxiv_only = bool(cli_args.arxiv_only)
        run_logger.info(f"CLI mode override: {'arxiv_only' if arxiv_only else 'full'}")
        results_file_path = daily_run(
            lookback_config=runtime_config["lookback"],
            max_results=runtime_config["max_results"],
            query_config=runtime_config["query"],
            selection_config=runtime_config["selection"],
            output_config=runtime_config["output"],
            inference_config=runtime_config["inference"],
            search_tuning_config=runtime_config["search_tuning"],
            network_config=runtime_config["network"],
            review_config=runtime_config["review"],
            arxiv_only=arxiv_only,
            run_timestamp=run_timestamp,
            logger=run_logger,
        )
        run_logger.info(f"Run complete. Results file: {results_file_path}")
    finally:
        for handler in list(run_logger.handlers):
            handler.close()
            run_logger.removeHandler(handler)


if __name__ == "__main__":
    run_main()
