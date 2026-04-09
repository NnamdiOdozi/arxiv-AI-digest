# Local Customizations

This file tracks local changes made on top of upstream `arxiv-daily-digest` so upgrades are easier to reapply.

Last updated: 2026-04-07

## 1) Team profile and keyword customization

- Added `prompt1.py` as the active source of `TEAM_PROFILE` and `KEYWORDS`.
- Updated `main.py` to import from `prompt1.py`.
- Note: `prompt1.txt` exists but is not used by runtime code.

## 2) LLM prompt formatting cleanup

- Refactored prompt construction in `main.py` into `build_paper_evaluation_prompt(...)`.
- Kept logic the same, but normalized formatting and JSON instruction structure for stability.

## 3) Lookback and search config via TOML

- Added `config.toml` with:
- `[lookback] years, months, days`
- `[search] max_results`
- `[query] domain_terms, method_terms, use_and_query, prefilter terms`
- `[selection] top_n`
- `[output] send_to_slack, log_dir, results_dir`
- `[inference] model`
- Updated `main.py` to load `config.toml` and pass overrides to paper fetch and output selection.
- Updated `get_papers.py` to accept optional `cutoff_date` while preserving original Monday/weekday fallback when no cutoff is passed.

## 4) Output and observability upgrades

- Added timestamped per-run log files under `runs/logs`.
- Added timestamped markdown digest output files under `runs/results`.
- Batch request JSONL files are written under `runs/batch_requests`.
- arXiv-only snapshot JSON files are written under `runs/search`.
- arXiv snapshot now defaults to post-filter focus; raw `search_trace` can be toggled via
- `output.arxiv_snapshot_include_search_trace`.
- Added full pipeline trace logging to file and screen:
- all arXiv candidates returned (up to `max_results`)
- lookback pass/fail
- seen-paper drops
- queued batch requests
- parsed evaluation scores
- final selection decisions
- Slack delivery is now optional and controlled by `config.toml`.

## 5) arXiv 429 resilience and tuning

- Added configurable arXiv client controls in `config.toml` under `[search]`:
- `arxiv_page_size`
- `arxiv_delay_seconds`
- `arxiv_num_retries`
- `arxiv_max_attempts`
- `arxiv_backoff_seconds`
- Updated `get_papers.py` with explicit cross-attempt backoff for HTTP 429 responses.
- Updated `main.py` to pass arXiv tuning config and write a graceful markdown result with run notes when arXiv fetch fails after retries.

## 6) Query relevance tightening

- Added `(domain_terms) AND (method_terms)` query builder configurable in `config.toml` `[query]`.
- Added optional domain-term prefilter on title+abstract before LLM scoring to reduce irrelevant candidates.
- Extended arXiv trace logs to show `domain_match` and explicit drop reasons from domain prefilter.

## 7) Prefilter hardening against false positives

- Switched prefilter matching to whole-term regex boundaries (instead of loose substring matching).
- Added configurable strictness controls in `config.toml` `[query]`:
- `prefilter_min_matches`
- `require_anchor_match`
- `term_match_mode` (`exact` or `stem`)
- `anchor_terms`
- Default prefilter/anchor terms now prioritize insurance-reserving phrases and avoid ambiguous acronym-only matches.
- Current default `term_match_mode = "stem"` expands matching to common word variants (for example `predict`/`prediction`, `model`/`modelling`).

## 8) Network resilience for Doubleword/OpenAI calls

- Added configurable network retry controls in `config.toml` under `[network]`:
- `openai_retry_max_attempts`
- `openai_retry_base_seconds`
- `openai_retry_max_seconds`
- `poll_interval_seconds`
- `max_consecutive_poll_errors`
- Added retry-with-backoff for batch upload and submission in `main.py`.
- Hardened `wait_for_batch` in `create_batch_evaluation.py` to tolerate transient network failures (DNS/connectivity drops) without crashing.

## 9) Batch resume helper script

- Added `batch_tools.py` with:
- `status` command to check existing batch progress
- `resume` command to continue waiting on an existing batch, save parsed results, write standard `digest_*.md` output, and optionally update `seen_papers.json`
- Batch id can be passed explicitly or auto-detected from latest run logs.
- Resume now reuses the original run timestamp from matching `run_*.log` when available, so recovered outputs stay naming-consistent with the original run.
- Resume stores parsed batch JSON under `runs/results/parsed/` to keep `runs/results/` focused on canonical digest markdown files.

## 10) Dependencies added

- Added to `requirements.txt`: `python-dateutil` for calendar-safe month/year lookback math (`relativedelta`).
- Added to `requirements.txt`: `tomli` as TOML parsing fallback for Python versions without built-in `tomllib`.

## 11) Richer digest metadata and structured review

- Digest entries now include:
- paper `published` timestamp
- paper `authors`
- Added optional second-pass structured review stage for selected top papers only.
- Structured review is driven by local JSON question templates via `config.toml` `[review]`:
- `enabled`
- `mode` (`off` | `separate` | `inline`)
- `questions_file`
- `max_questions`
- `include_in_digest`
- Added `pipeline_data/review_questions.json` as a compact schema (question + output keys only) derived from `2020_Al-Mudafer_Honours-thesis (1)_results.json`.
- Current default reads `pipeline_data/review_questions.json` with `max_questions = 15` (all current questions).
- Default `review.mode = "separate"` so `main.py` runs shallow pass only unless explicitly changed to `inline`.
- Added `run_structured_review.py` for separate pass-2 processing on an existing `digest_*.md` file.
- Digest now prints all returned structured-review fields (not just a small fixed subset).

## 12) Runtime data folder cleanup

- Added `pipeline_data/` for project-specific runtime/config data.
- Moved `seen_papers.json` to `pipeline_data/seen_papers.json`.
- Moved review question schema to `pipeline_data/review_questions.json`.
- Updated code to resolve these paths relative to project root (instead of working directory assumptions).

## 13) Review workflow mode split (shallow vs deep)

- Added `review.mode` in `config.toml` with options:
- `off`: disable structured pass-2
- `separate`: run pass-1 only in `main.py`, then use `run_structured_review.py` later
- `inline`: run pass-1 and pass-2 in one `main.py` run
- Added standalone `run_structured_review.py` that enriches a selected or latest digest.

## 14) arXiv-only test mode (no LLM cost)

- Added CLI flag `--arxiv-only` to `main.py`.
- In arXiv-only mode, pipeline stops before Doubleword/OpenAI calls and writes a JSON snapshot under `runs/search`.
- Snapshot includes search trace, post-filter candidates, unseen candidates, and seen drops.

## 15) Question quality refresh from review feedback

- Updated `pipeline_data/review_questions.json` question text/options using `MLRWP_LLM_paper_review_metadata_27112025.xlsx` feedback.
- Key improvements:
- expanded modelling categories (including survey/GLM/hybrid)
- expanded granularity buckets (including medium-level and not stated)
- mixed time-length support
- richer data source/public-availability categories
- keyword matching guidance and fallback handling

## Upgrade checklist

- Re-check imports in `main.py` for `prompt1.py` and config loading.
- Re-apply `get_daily_papers(..., cutoff_date=None)` optional parameter in `get_papers.py`.
- Re-apply optional trace return in `get_daily_papers(..., return_trace=True)` if needed.
- Re-add `config.toml` if upstream does not include equivalent runtime config.
- Re-test with `python3 -m py_compile main.py get_papers.py prompt1.py`.
