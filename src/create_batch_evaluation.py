from openai import OpenAI
import json
import time
import re
from pathlib import Path

def _log(log_fn, message):
    if log_fn:
        log_fn(message)
    else:
        print(message)


def _is_transient_network_error(err):
    """Best-effort classifier for temporary transport/API connectivity issues."""
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
    name = err.__class__.__name__
    if name in transient_types:
        return True

    text = str(err).lower()
    network_markers = [
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
    return any(marker in text for marker in network_markers)


def check_batch_status(client, batch_id, log_fn=None):
    """Check if batch is complete"""
    batch = client.batches.retrieve(batch_id)
    _log(log_fn, f"Status: {batch.status}")
    _log(log_fn, f"Completed: {batch.request_counts.completed}/{batch.request_counts.total}")
    return batch

def parse_evaluation_result(content):
    """Extract and normalize evaluation JSON from potentially noisy model output."""

    def _to_text(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text_part = item.get("text")
                    if isinstance(text_part, str):
                        parts.append(text_part)
                        continue
                    content_part = item.get("content")
                    if isinstance(content_part, str):
                        parts.append(content_part)
            return "\n".join(parts)
        return str(value)

    def _coerce_bool(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return None

    def _normalize_evaluation(payload):
        if not isinstance(payload, dict):
            return None

        if "relevance_score" not in payload:
            return None

        try:
            score = int(float(payload.get("relevance_score")))
        except (TypeError, ValueError):
            return None
        score = max(0, min(10, score))

        is_relevant = _coerce_bool(payload.get("is_relevant"))
        if is_relevant is None:
            is_relevant = score >= 7

        needs_summary = _coerce_bool(payload.get("needs_summary"))
        if needs_summary is None:
            summary_probe = payload.get("summary")
            needs_summary = bool(summary_probe and str(summary_probe).strip().lower() != "null")

        summary = payload.get("summary")
        if summary is None:
            summary_value = None
        elif isinstance(summary, str):
            stripped = summary.strip()
            summary_value = stripped if stripped and stripped.lower() != "null" else None
        else:
            summary_value = str(summary)

        key_insight = payload.get("key_insight")
        if key_insight is None:
            key_insight_value = ""
        else:
            key_insight_value = str(key_insight).strip()

        return {
            "relevance_score": score,
            "is_relevant": is_relevant,
            "needs_summary": needs_summary,
            "summary": summary_value,
            "key_insight": key_insight_value,
        }

    content_text = _to_text(content)
    content_text = re.sub(r"<think>.*?</think>", "", content_text, flags=re.DOTALL | re.IGNORECASE)
    content_text = re.sub(r"```(?:json)?", "", content_text, flags=re.IGNORECASE)
    content_text = content_text.strip()

    # Fast path: response is already a JSON object.
    if content_text.startswith("{"):
        try:
            parsed = json.loads(content_text)
            normalized = _normalize_evaluation(parsed)
            if normalized is not None:
                return normalized
        except Exception:
            pass

    # Robust path: scan for any JSON object and pick the first valid evaluation payload.
    decoder = json.JSONDecoder()
    candidates = []
    for idx, ch in enumerate(content_text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(content_text[idx:])
            candidates.append(obj)
        except Exception:
            continue

    for candidate in candidates:
        normalized = _normalize_evaluation(candidate)
        if normalized is not None:
            return normalized

    # Fallback regex block extraction.
    json_match = re.search(r"\{[\s\S]*\}", content_text)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            normalized = _normalize_evaluation(parsed)
            if normalized is not None:
                return normalized
        except Exception:
            pass

    # Repair pass for near-JSON responses (missing commas/braces or unquoted keys).
    repaired = content_text
    if repaired:
        # Quote bare keys at line starts: key: value -> "key": value
        repaired = re.sub(r'(?m)^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', repaired)
        # Insert missing comma pattern: ... null  "next_key": ...
        repaired = re.sub(r'(\bnull)\s+"', r'\1, "', repaired)
        # Close unmatched opening braces.
        open_braces = repaired.count("{")
        close_braces = repaired.count("}")
        if open_braces > close_braces:
            repaired = repaired + ("}" * (open_braces - close_braces))
        try:
            parsed = json.loads(repaired)
            normalized = _normalize_evaluation(parsed)
            if normalized is not None:
                return normalized
        except Exception:
            pass

    # Last-resort regex field extraction for malformed outputs.
    def _extract_first(patterns, text, flags=re.IGNORECASE | re.DOTALL):
        for pattern in patterns:
            match = re.search(pattern, text, flags)
            if match:
                return match.group(1)
        return None

    def _parse_bool_token(value):
        if value is None:
            return None
        token = str(value).strip().lower()
        if token in {"true", "1", "yes", "on"}:
            return True
        if token in {"false", "0", "no", "off"}:
            return False
        return None

    def _parse_json_string_or_null(value):
        if value is None:
            return None
        token = value.strip()
        if token.lower() == "null":
            return None
        if token.startswith('"'):
            try:
                return json.loads(token)
            except Exception:
                return token.strip('"')
        return token

    score_token = _extract_first(
        [
            r'"(?:relevance_score|levance_score|levance)"\s*:\s*(-?\d+(?:\.\d+)?)',
            r'(?m)^\s*(?:relevance_score|levance_score|levance)\s*:\s*(-?\d+(?:\.\d+)?)',
            # Catches garbled outputs where the score is embedded in the key name:
            # e.g. {"relevance_score 2is_relevant": ...} or {"relevance_score 3,
            r'"relevance_score\s+(-?\d+)',
            r'"relevance_score(-?\d+)',
        ],
        content_text,
        flags=re.IGNORECASE,
    )
    if score_token is not None:
        try:
            score = int(float(score_token))
        except (TypeError, ValueError):
            score = None
        if score is not None:
            score = max(0, min(10, score))
            is_relevant_token = _extract_first(
                [
                    r'"(?:is_relevant|_relevant|relevant)"\s*:\s*(true|false|1|0)',
                    r'(?m)^\s*(?:is_relevant|_relevant|relevant)\s*:\s*(true|false|1|0)',
                ],
                content_text,
                flags=re.IGNORECASE,
            )
            needs_summary_token = _extract_first(
                [
                    r'"(?:needs_summary|needs)"\s*:\s*(true|false|1|0)',
                    r'(?m)^\s*(?:needs_summary|needs)\s*:\s*(true|false|1|0)',
                ],
                content_text,
                flags=re.IGNORECASE,
            )
            summary_token = _extract_first(
                [
                    r'"summary"\s*:\s*(null|"(?:\\.|[^"\\])*")',
                    r'(?m)^\s*summary\s*:\s*(null|"(?:\\.|[^"\\])*")',
                ],
                content_text,
            )
            key_insight_token = _extract_first(
                [
                    r'"(?:key_insight|_insight|insight|_ins)"\s*:\s*("(?:\\.|[^"\\])*")',
                    r'(?m)^\s*(?:key_insight|_insight|insight|_ins|key_ins)\s*:\s*("(?:\\.|[^"\\])*")',
                ],
                content_text,
            )

            parsed_summary = _parse_json_string_or_null(summary_token)
            parsed_key_insight = _parse_json_string_or_null(key_insight_token) or ""
            parsed_is_relevant = _parse_bool_token(is_relevant_token)
            if parsed_is_relevant is None:
                parsed_is_relevant = score >= 7
            parsed_needs_summary = _parse_bool_token(needs_summary_token)
            if parsed_needs_summary is None:
                parsed_needs_summary = parsed_summary is not None

            return {
                "relevance_score": score,
                "is_relevant": parsed_is_relevant,
                "needs_summary": parsed_needs_summary,
                "summary": parsed_summary,
                "key_insight": parsed_key_insight,
            }

    return None


def get_batch_results(client, batch_id, log_fn=None, debug_output_dir=None, run_timestamp=None):
    """Retrieve and parse batch results"""
    
    batch = client.batches.retrieve(batch_id)
    
    if batch.status != "completed":
        _log(log_fn, f"Batch not ready. Status: {batch.status}")
        return None
    
    result_file_id = batch.output_file_id
    result = client.files.content(result_file_id)
    
    results = []
    raw_records = []
    failed_records = []
    parsed_records = []
    lines = result.text.strip().split('\n')
    
    for i, line in enumerate(lines):
        try:
            data = json.loads(line)
            paper_id = data['custom_id']
            response_body = ((data.get('response') or {}).get('body') or {})
            choices = response_body.get('choices') or []
            message = (choices[0].get('message') if choices else {}) if isinstance(choices, list) else {}
            content = (message or {}).get('content')
            content_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            raw_records.append({
                "line_index": i,
                "paper_id": paper_id,
                "content": content_text,
                "response_error": data.get("error"),
            })
            
            # Parse the evaluation JSON from content
            evaluation = parse_evaluation_result(content)
            
            if evaluation:
                evaluation['paper_id'] = paper_id
                results.append(evaluation)
                parsed_records.append(evaluation)
                _log(log_fn, f"Parsed {paper_id}: score={evaluation.get('relevance_score')}")
            else:
                _log(log_fn, f"Failed to parse JSON for {paper_id}")
                failed_records.append({
                    "line_index": i,
                    "paper_id": paper_id,
                    "content": content_text,
                    "response_error": data.get("error"),
                })
                
        except Exception as e:
            _log(log_fn, f"Error on line {i}: {e}")
            failed_records.append({
                "line_index": i,
                "paper_id": None,
                "content": line,
                "response_error": str(e),
            })

    if debug_output_dir:
        debug_dir = Path(debug_output_dir)
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = run_timestamp or time.strftime("%Y%m%d_%H%M%S")
        raw_path = debug_dir / f"batch_{batch_id}_{stamp}_raw.jsonl"
        parsed_path = debug_dir / f"batch_{batch_id}_{stamp}_parsed_success.jsonl"
        failed_path = debug_dir / f"batch_{batch_id}_{stamp}_parse_failures.jsonl"
        with raw_path.open("w", encoding="utf-8") as f:
            for item in raw_records:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        with parsed_path.open("w", encoding="utf-8") as f:
            for item in parsed_records:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        with failed_path.open("w", encoding="utf-8") as f:
            for item in failed_records:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        _log(log_fn, f"Saved raw batch responses to {raw_path}")
        _log(log_fn, f"Saved parsed evaluations to {parsed_path} ({len(parsed_records)} entries)")
        _log(log_fn, f"Saved parse failures to {failed_path} ({len(failed_records)} entries)")

    failed_paper_ids = [r["paper_id"] for r in failed_records if r.get("paper_id")]
    return results, failed_paper_ids

def wait_for_batch(
    client,
    batch_id,
    check_interval=30,
    log_fn=None,
    network_retry_base_seconds=5,
    network_retry_max_seconds=120,
    max_consecutive_poll_errors=0,
    debug_output_dir=None,
    run_timestamp=None,
):
    """Wait for batch to complete"""
    
    _log(log_fn, "Waiting for batch to complete...")
    consecutive_poll_errors = 0
    while True:
        try:
            batch = check_batch_status(client, batch_id, log_fn=log_fn)
            consecutive_poll_errors = 0
        except Exception as err:
            if _is_transient_network_error(err):
                consecutive_poll_errors += 1
                if max_consecutive_poll_errors and consecutive_poll_errors >= max_consecutive_poll_errors:
                    _log(
                        log_fn,
                        "Exceeded max consecutive network poll errors (%s). Returning without crashing."
                        % max_consecutive_poll_errors,
                    )
                    return [], []
                backoff = min(
                    network_retry_max_seconds,
                    network_retry_base_seconds * (2 ** min(consecutive_poll_errors - 1, 6)),
                )
                _log(
                    log_fn,
                    "Transient network issue while polling batch status (%s). Retrying in %.1f seconds."
                    % (err, backoff),
                )
                time.sleep(backoff)
                continue
            raise
        
        if batch.status == "completed":
            _log(log_fn, "Batch completed!")
            try:
                results, failed_ids = get_batch_results(
                    client,
                    batch_id,
                    log_fn=log_fn,
                    debug_output_dir=debug_output_dir,
                    run_timestamp=run_timestamp,
                )
                return results, failed_ids
            except Exception as err:
                if _is_transient_network_error(err):
                    consecutive_poll_errors += 1
                    if max_consecutive_poll_errors and consecutive_poll_errors >= max_consecutive_poll_errors:
                        _log(
                            log_fn,
                            "Exceeded max consecutive network errors while fetching results (%s). Returning without crashing."
                            % max_consecutive_poll_errors,
                        )
                        return [], []
                    backoff = min(
                        network_retry_max_seconds,
                        network_retry_base_seconds * (2 ** min(consecutive_poll_errors - 1, 6)),
                    )
                    _log(
                        log_fn,
                        "Transient network issue while fetching completed batch results (%s). Retrying in %.1f seconds."
                        % (err, backoff),
                    )
                    time.sleep(backoff)
                    continue
                raise
        elif batch.status == "failed":
            _log(log_fn, "Batch failed")
            return [], []
        
        time.sleep(check_interval)
