# arXiv Digest — IFoA GI ML in Reserving Working Party

> **Note:** This repository was cloned from Doubleword's original `arxiv-daily-digest` repo and has since been substantially customized for this working party's use. See `LOCAL_CUSTOMIZATIONS.md` for the full changelog and `architecture.md` for the current pipeline design.

Fetches new papers from arXiv, scores them for relevance to general insurance (P&C) claims and loss reserving using an LLM (via Doubleword's batch API), and writes a ranked markdown digest plus a full CSV/XLSX evaluation of every paper considered.

We run this manually, monthly or quarterly, rather than on a schedule — there's no Docker, Kubernetes, cron, or Slack integration to maintain.

## What It Does

1. **Fetches** papers from arXiv matching a domain/method term query, over a configurable lookback window (days, months, or years)
2. **Evaluates** each paper's relevance against the working party's team profile using an LLM, via Doubleword's batch API
3. **Ranks** papers by relevance score
4. **Writes** a markdown digest of the top N papers, plus a CSV/XLSX of every paper scored (for manual review)
5. **Tracks** what's already been seen, so repeat runs don't re-surface the same papers

## Quick Start

### Prerequisites

- Python 3.12
- A [Doubleword API key](https://doubleword.ai)

### Setup

1. **Install dependencies**
   ```bash
   uv sync
   # or: pip install -r requirements.txt
   ```

2. **Set up environment variables**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` with your credentials:
   ```bash
   DW_API_KEY=your_actual_doubleword_api_key
   DW_BASE_URL=https://api.doubleword.ai/v1
   MODEL_NAME=Qwen/Qwen3-VL-235B-A22B-Instruct-FP8
   ```
   The code just calls the standard OpenAI SDK against `DW_BASE_URL`, so any OpenAI-compatible batch endpoint works — see [Swapping the LLM Provider](#swapping-the-llm-provider) below if you're not using Doubleword.

3. **Run it**
   ```bash
   python src/main.py
   ```
   Digest and evaluation files land in `runs/results/`.

   To test the arXiv search/filtering only, without spending on LLM calls:
   ```bash
   python src/main.py --arxiv-only
   ```
   This writes a search snapshot to `runs/search/` and stops before any LLM evaluation.

## Team Profile

`src/prompt1.py` defines `TEAM_PROFILE` — the `focus`, `interests`, and `avoid` lists that tell the LLM what the working party actually cares about (claims/loss reserving, RBNS/IBNR, individual-claim and triangle-based reserving, neural and non-neural methods, etc., versus generic ML or underwriting/fraud papers). This is the main lever for retuning what counts as relevant — edit the lists directly rather than the search query terms.

`config.toml` `[query]` controls the arXiv-side search terms (`domain_terms`, `method_terms`) and prefilter, which narrow down the candidate pool *before* any paper reaches the LLM.

## Scoring

The scoring rubric lives in `src/evaluation.py`, inside `build_paper_evaluation_prompt()`:

```
- 9 to 10: directly about reserving and highly useful to the working party
- 7 to 8: not directly about reserving, but strongly transferable with clear practical value
- 5 to 6: adjacent and somewhat useful, but not a priority
- 0 to 4: weak relevance to reserving or little practical value
```

The LLM returns a `relevance_score` (0–10) for every paper, along with a `summary` and `key_insight`. A paper is flagged `is_relevant` if its score is **≥ 7** — that threshold is hardcoded in `src/create_batch_evaluation.py` (search for `is_relevant = score >= 7`). Only `is_relevant` papers are eligible for the digest; the top `selection.top_n` of those (set in `config.toml`, currently 100) are written out. Every scored paper, relevant or not, still appears in the CSV/XLSX evaluation output for manual review.

An optional second-pass **structured review** (`src/run_structured_review.py`) can additionally ask the LLM a fixed set of questions (from `pipeline_data/review_questions.json`) about each top-N paper — see `config.toml` `[review]`.

**Only the title and abstract are sent to the LLM** for scoring — not the paper body. The abstract comes straight from arXiv's metadata for whatever papers survive the `[query]` filter; there's no PDF fetch or full-text extraction in the scoring path today.

*Future development idea*: sending part or all of the paper body (not just the abstract) to the LLM could improve scoring accuracy, at the cost of extra fetch/parse work and higher token spend per paper.

## Swapping the LLM Provider

### Another provider with a batch API (e.g. OpenAI)

The code (`src/main.py`, `src/create_batch_evaluation.py`, `src/batch_tools.py`) calls the plain OpenAI SDK's batch surface — `files.create(purpose="batch")`, `batches.create(endpoint="/v1/chat/completions", ...)`, `batches.retrieve`, `files.content`. Doubleword mirrors that same interface, so this is a config swap, not a code change:

1. `.env`: set `DW_BASE_URL` and `DW_API_KEY` to the new provider's values (e.g. `https://api.openai.com/v1` and an OpenAI key — the `DW_` naming is just leftover from the original Doubleword setup, the values are provider-agnostic).
2. `.env` / `config.toml` `[inference] model`: set `MODEL_NAME` to a model that provider actually hosts (the Qwen defaults in `config.toml` are Doubleword-hosted and won't exist elsewhere).
3. `config.toml` `[inference] completion_window`: OpenAI's batch API only accepts `"24h"` — Doubleword allows shorter windows like `"1h"`. Set this to `"24h"` for OpenAI or it'll be rejected at submission.

**Bear in mind**: OpenAI's batch queue is generally slower and less predictable than Doubleword's — turnaround can run well past `completion_window` under load. For a monthly/quarterly run this usually doesn't matter, but don't expect same-day results if you're testing interactively.

### A provider with only a real-time/interactive API (no batch endpoint)

This is a bigger change, not a config tweak — the whole pipeline is built around submit-a-batch-then-poll. If your provider (e.g. a self-hosted Llama endpoint, or a smaller/regional provider) only offers synchronous chat completions, you'd need to:

- Replace the batch submit-and-poll block in `src/create_batch_evaluation.py` with a loop of synchronous `client.chat.completions.create()` calls, one per paper (the `response_format`/structured-output schema in `src/evaluation.py` stays the same either way).
- Adjust `src/main.py`'s orchestration accordingly, and drop or repurpose `src/batch_tools.py`'s `status`/`resume` commands, since there's no `batch_id` to check.
- Add your own concurrency and rate-limit handling (`config.toml` `[network]` retry settings still help, but the batch-specific polling config won't apply) — otherwise scoring hundreds of papers serially will be slow.
- Expect to lose the batch pricing discount most providers give for async batch processing.

This is enough of a structural change that it's worth handing to a coding agent with the file list above as a starting point, rather than reworking it by hand.

## Configuration (`config.toml`)

| Section | Controls |
|---|---|
| `[lookback]` | Search window in years/months/days (0/0/0 = original Monday-3-day / weekday-1-day behavior) |
| `[search]` | `max_results`, arXiv page size, retry/backoff tuning for HTTP 429s |
| `[query]` | Domain/method search terms, AND/OR query strategy, prefilter and anchor-term guardrails |
| `[selection]` | `top_n` — how many relevant papers go into the digest |
| `[output]` | Output directories for logs, digests, batch requests, search snapshots; `send_to_slack` (kept as `false`) |
| `[network]` | Retry/backoff for Doubleword/OpenAI API calls and batch polling |
| `[inference]` | Model choice and batch completion window |
| `[review]` | Second-pass structured review mode (`off` / `separate` / `inline`) and question source |

Every option is commented in `config.toml` itself — that's the fastest place to look when changing behavior.

## Other Commands

- **Resume or check a batch** that's still running or was interrupted:
  ```bash
  python src/batch_tools.py status
  python src/batch_tools.py resume
  ```
- **Run the structured second-pass review** separately, on an existing digest:
  ```bash
  python src/run_structured_review.py
  ```
- **Retry papers that failed to parse** (beyond the inline requeue rounds already attempted during a run):
  ```bash
  python src/requeue_parse_failures.py
  ```

## Outputs

| File | Description |
|---|---|
| `runs/results/digest_TIMESTAMP.md` | Ranked digest of the top N relevant papers |
| `runs/results/parsed/evaluation_results_TIMESTAMP.csv/.xlsx` | Every scored paper, for manual review |
| `runs/search/arxiv_snapshot_TIMESTAMP.json` | Full arXiv search trace (candidates, filters, drops) |
| `runs/results/detailed_review_TIMESTAMP.md` | Optional structured second-pass review |
| `runs/logs/run_TIMESTAMP.log` | Full pipeline trace log for the run |
| `pipeline_data/seen_papers.json` | Dedup registry so re-runs don't resurface the same paper |
| `pipeline_data/parse_failures.json` | Papers that exhausted inline parse-retry rounds |

## Troubleshooting

**No papers found on Monday**: Normal — arXiv doesn't publish on weekends, so a Monday run looks back further to catch Friday's papers (unless a custom `[lookback]` window is set).

**Batch not completing**: Batch processing takes time. Check status with `python src/batch_tools.py status --batch-id <id>`. Poll interval is set by `network.poll_interval_seconds` in `config.toml`.

**`seen_papers.json` corrupted**: Delete `pipeline_data/seen_papers.json` and it will regenerate fresh on the next run.

**JSON parsing errors**: The code tolerates `<think>` tags and extra text from reasoning models, but persistent parse failures show up in `pipeline_data/parse_failures.json` — retry them with `python src/requeue_parse_failures.py`.

## Further Reading

- `architecture.md` — pipeline diagram and component breakdown
- `LOCAL_CUSTOMIZATIONS.md` — full changelog of local changes vs. the upstream Doubleword repo
