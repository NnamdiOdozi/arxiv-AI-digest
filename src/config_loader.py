import os
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MODEL_NAME = os.getenv("MODEL_NAME")

DEFAULT_LOOKBACK_CONFIG = {
    "years": 0,
    "months": 0,
    "days": 0,
}
DEFAULT_MAX_RESULTS = 100
DEFAULT_SEARCH_TUNING_CONFIG = {
    "arxiv_page_size": 0,
    "arxiv_delay_seconds": 4.0,
    "arxiv_num_retries": 6,
    "arxiv_max_attempts": 4,
    "arxiv_backoff_seconds": 20.0,
}
DEFAULT_QUERY_CONFIG = {
    "domain_terms": [
        "claims reserving",
        "loss reserving",
        "IBNR",
        "claim development",
        "run-off",
        "individual claims reserving",
        "individual loss reserving",
        "micro-level reserving",
        "insurance reserving",
        "chain ladder",
        "actuarial reserving",
    ],
    "method_terms": [
        "transformer",
        "recurrent neural network",
        "mixture density network",
        "machine learning",
        "neural network",
    ],
    "use_and_query": True,
    "enforce_domain_prefilter": True,
    "prefilter_terms": [],
    "prefilter_min_matches": 1,
    "require_anchor_match": True,
    "term_match_mode": "stem",
    "anchor_terms": [
        "claims reserving",
        "loss reserving",
        "insurance reserving",
        "actuarial reserving",
        "individual loss reserving",
        "individual claims reserving",
        "micro-level reserving",
        "IBNR",
        "chain ladder",
        "reserving",
    ],
}
DEFAULT_SELECTION_CONFIG = {
    "top_n": 10,
}
DEFAULT_OUTPUT_CONFIG = {
    "send_to_slack": False,
    "log_dir": "runs/logs",
    "results_dir": "runs/results",
    "batch_requests_dir": "runs/batch_requests",
    "search_results_dir": "runs/search",
    "arxiv_snapshot_include_search_trace": False,
}
DEFAULT_INFERENCE_CONFIG = {
    "model": _MODEL_NAME or "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8",
    "completion_window": "24h",
    "requeue_max_rounds": 0,
}
DEFAULT_NETWORK_CONFIG = {
    "openai_retry_max_attempts": 8,
    "openai_retry_base_seconds": 5.0,
    "openai_retry_max_seconds": 120.0,
    "poll_interval_seconds": 30.0,
    "max_consecutive_poll_errors": 0,
}
DEFAULT_REVIEW_CONFIG = {
    "enabled": True,
    "mode": "separate",
    "questions_file": "pipeline_data/review_questions.json",
    "max_questions": 15,
    "include_in_digest": True,
}
REVIEW_DIGEST_FIELDS = [
    ("modelling_technique_values", "modelling technique"),
    ("target_variable_values", "target variables"),
    ("input_data_granularity_value", "input data granularity"),
    ("model_data_granularity_value", "model data granularity"),
    ("time_period_values", "time period groupings"),
    ("time_period_length_values", "time period length"),
    ("data_source_values", "data source"),
    ("model_validation_value", "model validation"),
    ("prediction_error_value", "prediction error estimate"),
    ("supremacy_value", "chain-ladder superiority claim"),
]


def _parse_non_negative_int(value, key):
    """Parse integer config values and reject negatives."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid value for {key}: {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"{key} must be >= 0, got {parsed}")
    return parsed


def _parse_bool(value, key):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"Invalid boolean for {key}: {value!r}")


def _parse_path(value, key):
    if value is None:
        raise ValueError(f"{key} cannot be null")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{key} cannot be empty")
    return text


def _parse_string(value, key):
    if value is None:
        raise ValueError(f"{key} cannot be null")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{key} cannot be empty")
    return text


def _parse_term_match_mode(value):
    mode = _parse_string(value, "query.term_match_mode").lower()
    allowed = {"exact", "stem"}
    if mode not in allowed:
        raise ValueError(f"Invalid query.term_match_mode: {value!r}. Expected one of {sorted(allowed)}")
    return mode


def _parse_review_mode(value):
    mode = _parse_string(value, "review.mode").lower()
    allowed = {"off", "separate", "inline"}
    if mode not in allowed:
        raise ValueError(f"Invalid review.mode: {value!r}. Expected one of {sorted(allowed)}")
    return mode


def _parse_string_list(value, key):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list of strings")
    parsed = []
    for idx, item in enumerate(value):
        text = str(item).strip()
        if not text:
            raise ValueError(f"{key}[{idx}] cannot be empty")
        parsed.append(text)
    return parsed


def _parse_positive_float(value, key):
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid float for {key}: {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"{key} must be > 0, got {parsed}")
    return parsed


def _resolve_project_path(path_text):
    path = Path(path_text)
    if path.is_absolute():
        return str(path)
    return str((PROJECT_ROOT / path).resolve())


def load_runtime_config(config_path=None):
    """Load optional runtime overrides from config.toml."""
    if config_path is None:
        config_path = os.getenv("CONFIG_FILE", "config.toml")

    config = {
        "lookback": DEFAULT_LOOKBACK_CONFIG.copy(),
        "max_results": DEFAULT_MAX_RESULTS,
        "search_tuning": DEFAULT_SEARCH_TUNING_CONFIG.copy(),
        "query": {
            "domain_terms": DEFAULT_QUERY_CONFIG["domain_terms"][:],
            "method_terms": DEFAULT_QUERY_CONFIG["method_terms"][:],
            "use_and_query": DEFAULT_QUERY_CONFIG["use_and_query"],
            "enforce_domain_prefilter": DEFAULT_QUERY_CONFIG["enforce_domain_prefilter"],
            "prefilter_terms": DEFAULT_QUERY_CONFIG["prefilter_terms"][:],
            "prefilter_min_matches": DEFAULT_QUERY_CONFIG["prefilter_min_matches"],
            "require_anchor_match": DEFAULT_QUERY_CONFIG["require_anchor_match"],
            "term_match_mode": DEFAULT_QUERY_CONFIG["term_match_mode"],
            "anchor_terms": DEFAULT_QUERY_CONFIG["anchor_terms"][:],
        },
        "selection": DEFAULT_SELECTION_CONFIG.copy(),
        "output": DEFAULT_OUTPUT_CONFIG.copy(),
        "inference": DEFAULT_INFERENCE_CONFIG.copy(),
        "network": DEFAULT_NETWORK_CONFIG.copy(),
        "review": DEFAULT_REVIEW_CONFIG.copy(),
    }

    if not os.path.exists(config_path):
        config["review"]["questions_file"] = _resolve_project_path(config["review"]["questions_file"])
        return config

    with open(config_path, "rb") as f:
        raw_config = tomllib.load(f)

    lookback = raw_config.get("lookback", {})
    search = raw_config.get("search", {})
    query = raw_config.get("query", {})
    selection = raw_config.get("selection", {})
    output = raw_config.get("output", {})
    inference = raw_config.get("inference", {})
    network = raw_config.get("network", {})
    review = raw_config.get("review", {})

    config["lookback"] = {
        "years": _parse_non_negative_int(lookback.get("years", 0), "lookback.years"),
        "months": _parse_non_negative_int(lookback.get("months", 0), "lookback.months"),
        "days": _parse_non_negative_int(lookback.get("days", 0), "lookback.days"),
    }
    config["max_results"] = _parse_non_negative_int(
        search.get("max_results", DEFAULT_MAX_RESULTS),
        "search.max_results",
    )
    config["search_tuning"] = {
        "arxiv_page_size": _parse_non_negative_int(
            search.get("arxiv_page_size", DEFAULT_SEARCH_TUNING_CONFIG["arxiv_page_size"]),
            "search.arxiv_page_size",
        ),
        "arxiv_delay_seconds": _parse_positive_float(
            search.get("arxiv_delay_seconds", DEFAULT_SEARCH_TUNING_CONFIG["arxiv_delay_seconds"]),
            "search.arxiv_delay_seconds",
        ),
        "arxiv_num_retries": _parse_non_negative_int(
            search.get("arxiv_num_retries", DEFAULT_SEARCH_TUNING_CONFIG["arxiv_num_retries"]),
            "search.arxiv_num_retries",
        ),
        "arxiv_max_attempts": _parse_non_negative_int(
            search.get("arxiv_max_attempts", DEFAULT_SEARCH_TUNING_CONFIG["arxiv_max_attempts"]),
            "search.arxiv_max_attempts",
        ),
        "arxiv_backoff_seconds": _parse_positive_float(
            search.get("arxiv_backoff_seconds", DEFAULT_SEARCH_TUNING_CONFIG["arxiv_backoff_seconds"]),
            "search.arxiv_backoff_seconds",
        ),
    }
    config["query"] = {
        "domain_terms": _parse_string_list(
            query.get("domain_terms", DEFAULT_QUERY_CONFIG["domain_terms"]),
            "query.domain_terms",
        ),
        "method_terms": _parse_string_list(
            query.get("method_terms", DEFAULT_QUERY_CONFIG["method_terms"]),
            "query.method_terms",
        ),
        "use_and_query": _parse_bool(
            query.get("use_and_query", DEFAULT_QUERY_CONFIG["use_and_query"]),
            "query.use_and_query",
        ),
        "enforce_domain_prefilter": _parse_bool(
            query.get("enforce_domain_prefilter", DEFAULT_QUERY_CONFIG["enforce_domain_prefilter"]),
            "query.enforce_domain_prefilter",
        ),
        "prefilter_terms": _parse_string_list(
            query.get("prefilter_terms", DEFAULT_QUERY_CONFIG["prefilter_terms"]),
            "query.prefilter_terms",
        ),
        "prefilter_min_matches": _parse_non_negative_int(
            query.get("prefilter_min_matches", DEFAULT_QUERY_CONFIG["prefilter_min_matches"]),
            "query.prefilter_min_matches",
        ),
        "require_anchor_match": _parse_bool(
            query.get("require_anchor_match", DEFAULT_QUERY_CONFIG["require_anchor_match"]),
            "query.require_anchor_match",
        ),
        "term_match_mode": _parse_term_match_mode(
            query.get("term_match_mode", DEFAULT_QUERY_CONFIG["term_match_mode"]),
        ),
        "anchor_terms": _parse_string_list(
            query.get("anchor_terms", DEFAULT_QUERY_CONFIG["anchor_terms"]),
            "query.anchor_terms",
        ),
    }
    config["selection"] = {
        "top_n": _parse_non_negative_int(
            selection.get("top_n", DEFAULT_SELECTION_CONFIG["top_n"]),
            "selection.top_n",
        )
    }
    config["output"] = {
        "send_to_slack": _parse_bool(
            output.get("send_to_slack", DEFAULT_OUTPUT_CONFIG["send_to_slack"]),
            "output.send_to_slack",
        ),
        "log_dir": _parse_path(output.get("log_dir", DEFAULT_OUTPUT_CONFIG["log_dir"]), "output.log_dir"),
        "results_dir": _parse_path(
            output.get("results_dir", DEFAULT_OUTPUT_CONFIG["results_dir"]),
            "output.results_dir",
        ),
        "batch_requests_dir": _parse_path(
            output.get("batch_requests_dir", DEFAULT_OUTPUT_CONFIG["batch_requests_dir"]),
            "output.batch_requests_dir",
        ),
        "search_results_dir": _parse_path(
            output.get("search_results_dir", DEFAULT_OUTPUT_CONFIG["search_results_dir"]),
            "output.search_results_dir",
        ),
        "arxiv_snapshot_include_search_trace": _parse_bool(
            output.get(
                "arxiv_snapshot_include_search_trace",
                DEFAULT_OUTPUT_CONFIG["arxiv_snapshot_include_search_trace"],
            ),
            "output.arxiv_snapshot_include_search_trace",
        ),
    }
    config["inference"] = {
        "model": _parse_string(
            inference.get("model", DEFAULT_INFERENCE_CONFIG["model"]),
            "inference.model",
        ),
        "completion_window": _parse_string(
            inference.get("completion_window", DEFAULT_INFERENCE_CONFIG["completion_window"]),
            "inference.completion_window",
        ),
        "requeue_max_rounds": _parse_non_negative_int(
            inference.get("requeue_max_rounds", DEFAULT_INFERENCE_CONFIG["requeue_max_rounds"]),
            "inference.requeue_max_rounds",
        ),
    }
    config["network"] = {
        "openai_retry_max_attempts": max(
            1,
            _parse_non_negative_int(
                network.get("openai_retry_max_attempts", DEFAULT_NETWORK_CONFIG["openai_retry_max_attempts"]),
                "network.openai_retry_max_attempts",
            ),
        ),
        "openai_retry_base_seconds": _parse_positive_float(
            network.get("openai_retry_base_seconds", DEFAULT_NETWORK_CONFIG["openai_retry_base_seconds"]),
            "network.openai_retry_base_seconds",
        ),
        "openai_retry_max_seconds": _parse_positive_float(
            network.get("openai_retry_max_seconds", DEFAULT_NETWORK_CONFIG["openai_retry_max_seconds"]),
            "network.openai_retry_max_seconds",
        ),
        "poll_interval_seconds": _parse_positive_float(
            network.get("poll_interval_seconds", DEFAULT_NETWORK_CONFIG["poll_interval_seconds"]),
            "network.poll_interval_seconds",
        ),
        "max_consecutive_poll_errors": _parse_non_negative_int(
            network.get("max_consecutive_poll_errors", DEFAULT_NETWORK_CONFIG["max_consecutive_poll_errors"]),
            "network.max_consecutive_poll_errors",
        ),
    }
    config["review"] = {
        "enabled": _parse_bool(
            review.get("enabled", DEFAULT_REVIEW_CONFIG["enabled"]),
            "review.enabled",
        ),
        "mode": _parse_review_mode(
            review.get("mode", DEFAULT_REVIEW_CONFIG["mode"]),
        ),
        "questions_file": _parse_path(
            review.get("questions_file", DEFAULT_REVIEW_CONFIG["questions_file"]),
            "review.questions_file",
        ),
        "max_questions": _parse_non_negative_int(
            review.get("max_questions", DEFAULT_REVIEW_CONFIG["max_questions"]),
            "review.max_questions",
        ),
        "include_in_digest": _parse_bool(
            review.get("include_in_digest", DEFAULT_REVIEW_CONFIG["include_in_digest"]),
            "review.include_in_digest",
        ),
    }
    config["review"]["questions_file"] = _resolve_project_path(config["review"]["questions_file"])

    return config


def _build_or_clause(terms):
    if not terms:
        return ""
    return "(" + " OR ".join(f'"{term}"' for term in terms) + ")"


def build_query_and_prefilter_terms(query_config):
    """Build arXiv query text and prefilter settings."""
    domain_terms = query_config["domain_terms"]
    method_terms = query_config["method_terms"]
    use_and_query = query_config["use_and_query"]

    if domain_terms and method_terms and use_and_query:
        query_text = f"{_build_or_clause(domain_terms)} AND {_build_or_clause(method_terms)}"
    elif domain_terms and method_terms:
        query_text = f"{_build_or_clause(domain_terms)} OR {_build_or_clause(method_terms)}"
    elif domain_terms:
        query_text = _build_or_clause(domain_terms)
    elif method_terms:
        query_text = _build_or_clause(method_terms)
    else:
        query_text = ""

    prefilter_terms = query_config["prefilter_terms"][:] if query_config["prefilter_terms"] else domain_terms[:]
    required_terms = prefilter_terms if query_config["enforce_domain_prefilter"] else []
    required_min_matches = max(1, query_config["prefilter_min_matches"])
    anchor_terms = query_config["anchor_terms"][:] if query_config["require_anchor_match"] else []
    term_match_mode = query_config["term_match_mode"]
    return query_text, required_terms, required_min_matches, anchor_terms, term_match_mode
