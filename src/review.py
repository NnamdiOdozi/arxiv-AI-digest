import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def enrich_top_papers_with_structured_review(
    top_results,
    papers_by_id,
    question_specs,
    model_name,
    network_config,
    log,
    client,
):
    """Run a second-pass structured review for selected top papers only."""
    if not top_results or not question_specs:
        return

    def _review_one(idx_result):
        idx, result = idx_result
        paper_id = result.get("paper_id")
        paper = papers_by_id.get(paper_id)
        if not paper:
            return
        prompt = build_structured_review_prompt(paper, question_specs)
        log(
            "[dw_structured_review] request=%s/%s paper_id=%s title=%s"
            % (idx, len(top_results), paper_id, paper["title"])
        )

        def _request():
            return client.chat.completions.create(
                model=model_name,
                response_format=REVIEW_SCHEMA,
                messages=[
                    {"role": "system", "content": "Return only valid JSON. Do not wrap JSON in markdown."},
                    {"role": "user", "content": prompt},
                ],
            )

        response = call_with_network_retry(
            f"structured review for {paper_id}",
            _request,
            network_config,
            log,
        )
        content = response.choices[0].message.content if response.choices else ""
        parsed = parse_evaluation_result(content or "")
        if isinstance(parsed, dict):
            result["structured_review"] = parsed
            log(f"[dw_structured_review_ok] paper_id={paper_id} keys={len(parsed)}")
        else:
            log(f"[dw_structured_review_parse_failed] paper_id={paper_id}")

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_review_one, (idx, result)) for idx, result in enumerate(top_results, 1)]
        for future in as_completed(futures):
            future.result()
