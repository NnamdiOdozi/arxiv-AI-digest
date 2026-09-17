import json
import os
import re
import time
from textwrap import dedent

from create_batch_evaluation import parse_evaluation_result
from network import call_with_network_retry

REVIEW_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "paper_structured_review",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "modelling_technique_values":        {"type": ["string", "null"]},
                "modelling_technique_explanation":   {"type": ["string", "null"]},
                "input_data_granularity_value":      {"type": ["string", "null"]},
                "input_data_granularity_explanation":{"type": ["string", "null"]},
                "model_data_granularity_value":      {"type": ["string", "null"]},
                "model_data_granularity_explanation":{"type": ["string", "null"]},
                "time_period_values":                {"type": ["string", "null"]},
                "time_period_explanation":           {"type": ["string", "null"]},
                "time_period_length_values":         {"type": ["string", "null"]},
                "time_period_length_explanation":    {"type": ["string", "null"]},
                "target_variable_values":            {"type": ["string", "null"]},
                "target_variable_explanation":       {"type": ["string", "null"]},
                "data_source_values":                {"type": ["string", "null"]},
                "data_source_explanation":           {"type": ["string", "null"]},
                "data_published_value":              {"type": ["string", "null"]},
                "data_published_explanation":        {"type": ["string", "null"]},
                "business_line_values":              {"type": ["string", "null"]},
                "business_line_explanation":         {"type": ["string", "null"]},
                "input_data_measures_values":        {"type": ["string", "null"]},
                "input_data_measures_explanation":   {"type": ["string", "null"]},
                "model_validation_value":            {"type": ["string", "null"]},
                "model_validation_explanation":      {"type": ["string", "null"]},
                "prediction_error_value":            {"type": ["string", "null"]},
                "prediction_error_explanation":      {"type": ["string", "null"]},
                "supremacy_value":                   {"type": ["string", "null"]},
                "supremacy_explanation":             {"type": ["string", "null"]},
                "code_available_value":              {"type": ["string", "null"]},
                "code_available_value_explanation":  {"type": ["string", "null"]},
                "keywords_value":                    {"type": ["string", "null"]},
                "keywords_explanation":              {"type": ["string", "null"]},
            },
            "required": [
                "modelling_technique_values", "modelling_technique_explanation",
                "input_data_granularity_value", "input_data_granularity_explanation",
                "model_data_granularity_value", "model_data_granularity_explanation",
                "time_period_values", "time_period_explanation",
                "time_period_length_values", "time_period_length_explanation",
                "target_variable_values", "target_variable_explanation",
                "data_source_values", "data_source_explanation",
                "data_published_value", "data_published_explanation",
                "business_line_values", "business_line_explanation",
                "input_data_measures_values", "input_data_measures_explanation",
                "model_validation_value", "model_validation_explanation",
                "prediction_error_value", "prediction_error_explanation",
                "supremacy_value", "supremacy_explanation",
                "code_available_value", "code_available_value_explanation",
                "keywords_value", "keywords_explanation",
            ],
            "additionalProperties": False,
        },
    },
}


def _compact_review_question_text(text):
    """Trim verbose answer-format instructions to reduce prompt cost."""
    normalized = " ".join(str(text).split())
    marker = "Provide your response"
    if marker in normalized:
        normalized = normalized.split(marker, 1)[0].strip()
    return normalized.strip()


_ESCAPE_VALUE_RE = re.compile(r"^(unknown|unclear|not[_ ]stated|none|n/a)$", re.I)


def _split_allowed_values(raw):
    """Split a bracketed value list on commas, ignoring commas inside parentheses."""
    values, depth, current = [], 0, []
    for ch in raw:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            values.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    values.append("".join(current).strip())
    return [v for v in values if v]


def extract_allowed_values(question_text):
    """Pull the allowed answers out of a question's [A, B, C] list.

    Returns (values, multi_select), or (None, False) when the question has no
    bracketed list - free-text questions keep the old nullable-string treatment.
    """
    match = re.search(r"\[([^\]]+)\]", question_text or "")
    if not match:
        return None, False
    values = _split_allowed_values(match.group(1))
    if not values:
        return None, False
    # An enum with no escape hatch FORCES a wrong answer when the paper is silent,
    # which is worse than a null. Guarantee one.
    if not any(_ESCAPE_VALUE_RE.match(v) for v in values):
        values.append("Unknown")
    multi_select = bool(re.search(r"one or more", question_text or "", re.I))
    return values, multi_select


def build_review_schema(question_specs):
    """Build a json_schema response format with enums for every bounded field.

    Bounded values must be enums: the schema is the only thing enforced during
    token generation, so a free nullable string lets the model answer null and
    bury the real answer in the prose (observed 15/15 nulls on one paper).
    """
    properties = {}
    for spec in question_specs:
        values, multi_select = extract_allowed_values(spec.get("question", ""))
        for key in spec.get("keys", []):
            if key.endswith("_explanation") or not values:
                properties[key] = {"type": ["string", "null"]}
            elif multi_select:
                properties[key] = {
                    "type": "array",
                    "items": {"type": "string", "enum": values},
                }
            else:
                properties[key] = {"type": "string", "enum": values}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "paper_structured_review",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": sorted(properties),
                "additionalProperties": False,
            },
        },
    }


def parse_structured_review_result(content):
    """Parse a pass-2 structured review response into a plain dict.

    Pass 1's parse_evaluation_result() must NOT be used here: it requires a
    relevance_score (absent from REVIEW_SCHEMA) and rebuilds the payload as its
    own four fields, silently discarding every review answer. Pass-2 keys vary
    with whatever questions the team wrote, so accept any JSON object as-is.
    """
    text = content if isinstance(content, str) else str(content or "")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return None
    return parsed if isinstance(parsed, dict) else None


def load_review_questions(review_config, log):
    """Load structured review question specs from a local JSON file."""
    if not review_config["enabled"]:
        return []

    question_path = review_config["questions_file"]
    if not os.path.exists(question_path):
        log(f"Structured review file not found: {question_path}. Skipping structured review stage.")
        return []

    with open(question_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        log(f"Structured review file is not a JSON list: {question_path}. Skipping structured review stage.")
        return []

    question_specs = []
    compact_schema_count = 0
    legacy_schema_count = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        question_text = _compact_review_question_text(item.get("question", ""))
        if not question_text:
            continue

        # Preferred compact schema: {"question": "...", "keys": ["a", "b"]}.
        compact_keys = item.get("keys")
        if isinstance(compact_keys, list):
            output_keys = [str(k).strip() for k in compact_keys if str(k).strip()]
            if output_keys:
                question_specs.append({
                    "question": question_text,
                    "keys": output_keys,
                })
                compact_schema_count += 1
                continue

        # Backward-compatible legacy schema from *_results.json with sample answer payload.
        answer_template = parse_evaluation_result(str(item.get("answer", "")))
        if not isinstance(answer_template, dict):
            continue
        output_keys = [str(k).strip() for k in answer_template.keys() if str(k).strip()]
        if not output_keys:
            continue
        question_specs.append({
            "question": question_text,
            "keys": output_keys,
        })
        legacy_schema_count += 1

    max_questions = review_config["max_questions"]
    if max_questions > 0:
        question_specs = question_specs[:max_questions]

    log(
        "Structured review loaded: %s questions from %s (compact_schema=%s, legacy_schema=%s)"
        % (len(question_specs), question_path, compact_schema_count, legacy_schema_count)
    )
    return question_specs


def _ordered_unique(items):
    seen = set()
    ordered = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def build_structured_review_prompt(paper, question_specs):
    """Prompt for richer paper profiling from full text or abstract."""
    requested_keys = _ordered_unique(
        key for spec in question_specs for key in spec["keys"]
    )
    key_stub = ",\n".join(f'  "{key}": null' for key in requested_keys)
    numbered_questions = "\n".join(
        f"{idx}. {spec['question']} (keys: {', '.join(spec['keys'])})"
        for idx, spec in enumerate(question_specs, 1)
    )
    full_text = paper.get("full_text")
    if full_text:
        text_label = "PAPER TEXT"
        text_body = full_text
        source_note = "Use ONLY the paper text below."
    else:
        text_label = "ABSTRACT"
        text_body = paper["abstract"]
        source_note = "Use ONLY the title and abstract below."
    return dedent(
        f"""\
        You are extracting a structured actuarial paper profile for an insurance reserving research digest.

        {source_note}
        If a field cannot be inferred confidently, return null.
        Keep any *_explanation fields to one concise sentence.

        TITLE:
        {paper["title"]}

        {text_label}:
        {text_body}

        QUESTIONS TO ANSWER:
        {numbered_questions}

        Return ONLY valid JSON with exactly these keys:
        {{
        {key_stub}
        }}
        """
    )


def _review_text_format(response_format):
    """Convert the chat-completions `response_format` into Responses-API `text.format`.

    Same schema, different envelope: chat completions nests it under
    `json_schema`, the Responses API flattens `name`/`schema`/`strict` one level up.
    """
    js = response_format["json_schema"]
    return {"format": {"type": "json_schema", "name": js["name"],
                       "strict": js.get("strict", True), "schema": js["schema"]}}


def enrich_top_papers_with_structured_review(
    top_results,
    papers_by_id,
    question_specs,
    model_name,
    network_config,
    log,
    client,
    service_tier="flex",
    poll_interval_seconds=3.0,
    on_result=None,
):
    """Run a second-pass structured review for the selected top papers only.

    Uses the async Responses API: every paper is submitted with background=True
    (returning immediately), then all outstanding responses are polled together.
    `service_tier` picks the SLA -- "flex" is the cheaper 1-hour tier, "priority"
    is real-time and costs more. Submitting first and polling second means no
    thread pool: nothing blocks waiting on a single paper.
    """
    if not top_results or not question_specs:
        return

    response_format = build_review_schema(question_specs)
    text_format = _review_text_format(response_format)

    # --- phase 1: submit every paper, keep {paper_id: response_id} -------------
    pending = {}
    results_by_id = {}
    for idx, result in enumerate(top_results, 1):
        paper_id = result.get("paper_id")
        paper = papers_by_id.get(paper_id)
        if not paper:
            continue
        prompt = build_structured_review_prompt(paper, question_specs)
        log(
            "[dw_structured_review] request=%s/%s paper_id=%s tier=%s title=%s"
            % (idx, len(top_results), paper_id, service_tier, paper["title"])
        )

        def _submit(prompt=prompt):
            return client.responses.create(
                model=model_name,
                input=prompt,
                service_tier=service_tier,
                background=True,
                text=text_format,
            )

        response = call_with_network_retry(
            f"structured review submit for {paper_id}", _submit, network_config, log
        )
        pending[paper_id] = response.id
        results_by_id[paper_id] = result
        log(f"[dw_structured_review_queued] paper_id={paper_id} response_id={response.id}")

    if not pending:
        return
    log(f"[dw_structured_review] submitted {len(pending)} papers on tier={service_tier}; polling")

    # --- phase 2: poll all outstanding responses until each finishes ----------
    while pending:
        for paper_id, response_id in list(pending.items()):

            def _retrieve(response_id=response_id):
                return client.responses.retrieve(response_id)

            response = call_with_network_retry(
                f"structured review poll for {paper_id}", _retrieve, network_config, log
            )
            status = getattr(response, "status", None)
            if status in ("queued", "in_progress"):
                continue

            del pending[paper_id]
            if status != "completed":
                log(f"[dw_structured_review_failed] paper_id={paper_id} status={status}")
                continue

            content = getattr(response, "output_text", "") or ""
            if not content.strip():
                # Distinct from a parse failure: the API reported success and
                # charged for it, but handed back nothing. Log the id so it can
                # be looked up on the provider's portal.
                log(
                    "[dw_structured_review_empty] paper_id=%s response_id=%s "
                    "status=completed but output was empty - nothing to parse"
                    % (paper_id, response_id)
                )
                continue
            parsed = parse_structured_review_result(content)
            if isinstance(parsed, dict):
                results_by_id[paper_id]["structured_review"] = parsed
                log(f"[dw_structured_review_ok] paper_id={paper_id} keys={len(parsed)}")
                if on_result:
                    # Persist as each paper lands: a long run must not lose
                    # everything already paid for if it is interrupted.
                    on_result()
            else:
                log(
                    f"[dw_structured_review_parse_failed] paper_id={paper_id} "
                    f"response_id={response_id} chars={len(content)}"
                )
        if pending:
            time.sleep(poll_interval_seconds)
