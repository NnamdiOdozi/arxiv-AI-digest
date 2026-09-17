# arXiv Digest — IFoA GI ML in Reserving Working Party

> **Note:** This repository was cloned from Doubleword's original `arxiv-daily-digest` repo and has since been substantially customized for this working party's use. See `LOCAL_CUSTOMIZATIONS.md` for the full changelog and `architecture.md` for the current pipeline design.

Fetches new papers from arXiv, scores them for relevance to general insurance (P&C) claims and loss reserving using an LLM (via Doubleword's batch API), and writes a ranked markdown digest plus a full CSV/XLSX evaluation of every paper considered.

**Why Doubleword, and why batch.** Two deliberate choices keep the running cost low. First, the pipeline uses the **batch API** rather than the ordinary real-time one. You submit all the papers as a single job and collect the answers when the provider gets to them, instead of asking one question at a time and waiting for each reply. Providers charge substantially less for this, because they can fit the work around their spare capacity. The trade is that results are not immediate — which suits a digest you run monthly or quarterly, and would not suit an interactive tool. Second, Doubleword serves **open-weight models** — Qwen3 in the 30B and 235B sizes, among others — rather than proprietary frontier models. For reading an abstract and scoring its relevance, these are more than capable, and they cost a fraction of the proprietary equivalents. The combination is what makes it practical to score several hundred papers per run.

We run this manually, monthly or quarterly, rather than on a schedule — there's no Docker, Kubernetes, cron, or Slack integration to maintain.

## What It Does

1. **Fetches** papers from arXiv matching a domain/method term query, over a configurable lookback window (days, months, or years)
2. **Evaluates** each paper's relevance against the working party's team profile using an LLM, via Doubleword's batch API
3. **Ranks** papers by relevance score
4. **Writes** a markdown digest of the top N papers, plus a CSV/XLSX of every paper scored (for manual review)
5. **Tracks** what's already been seen, so repeat runs don't re-surface the same papers

## Quick Start

### Prerequisites

- **Git**, to download the code. Check with `git --version`; if that errors, install it from [git-scm.com](https://git-scm.com/downloads).
- **A Doubleword API key**, from [doubleword.ai](https://doubleword.ai). This is what pays for the LLM scoring. Any OpenAI-compatible provider works instead — see [Swapping the LLM Provider](#swapping-the-llm-provider).
- Python 3.12. You do **not** need to install this yourself if you use `uv` in step 2, which fetches the right version for you.

### Setup

Every command below is typed into a terminal (Terminal on macOS/Linux, PowerShell on Windows).

1. **Download the code**
   ```bash
   git clone https://github.com/NnamdiOdozi/arxiv-AI-digest.git
   cd arxiv-AI-digest
   ```
   The first line copies the repository into a new folder. The second moves you inside it. **Every later command must be run from inside this folder** — if a command fails saying a file cannot be found, this is the first thing to check. `pwd` on macOS/Linux (or `cd` alone on Windows) prints where you currently are.

2. **Install `uv`** (skip if `uv --version` already works)

   `uv` is the tool that installs this project's Python packages. It does the same job as `pip`, which you may have used before, but it is dramatically faster — usually seconds rather than minutes — and it also downloads the correct version of Python for you, so you do not have to install Python 3.12 yourself. It is a drop-in replacement, not something exotic, and it will not interfere with any Python setup you already have.
   ```bash
   # macOS / Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Windows PowerShell
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Close and reopen your terminal afterwards, `cd` back into the folder from step 1, then check it worked with `uv --version`.

3. **Install the dependencies**
   ```bash
   uv sync
   ```
   This reads `pyproject.toml` and `uv.lock` and builds a self-contained environment in a hidden `.venv` folder. It does not touch your system Python. Expect it to take under a minute.

   If you would rather use `pip` and manage your own virtual environment, `requirements.txt` is kept up to date for that — but then the `uv run` prefix in the commands below does not apply, and you must activate your environment yourself.

4. **Add your API key**
   ```bash
   cp .env.example .env
   ```
   On Windows PowerShell use `copy .env.example .env` instead. Now open the new `.env` file in any text editor and fill in your own values:
   ```bash
   DW_API_KEY=your_actual_doubleword_api_key
   DW_BASE_URL=https://api.doubleword.ai/v1
   MODEL_NAME=Qwen/Qwen3-VL-235B-A22B-Instruct-FP8
   ```
   `.env` is listed in `.gitignore`, so your key will not be committed if you push changes back. Never paste a key into `config.toml` or into any source file.

5. **Do a free test run first**
   ```bash
   uv run python src/main.py --arxiv-only
   ```
   This searches arXiv and applies the filters, then stops before contacting the LLM, so it costs nothing. It proves your setup works and writes a snapshot to `runs/search/`. If you see a list of papers found, you are ready.

6. **Run it properly**
   ```bash
   uv run python src/main.py
   ```
   This scores every paper found and writes the digest. It submits a batch job and then waits for the provider, so it is not instant — allow anything from several minutes to a few hours depending on the provider's queue. Results land in `runs/results/`, and `runs/logs/` gets a full trace of the run.

> **The `uv run` prefix matters.** It runs the command inside the project's environment. If you type plain `uv run python src/main.py`, your computer uses its own system Python, which does not have the packages installed, and you get `ModuleNotFoundError: No module named 'openai'`. This is the single most common first-run problem. Either keep the `uv run` prefix on every command in this README, or activate the environment once per terminal session with `source .venv/bin/activate` (macOS/Linux) or `.venv\Scripts\activate` (Windows) and then drop the prefix.

## Customising This For Your Team

**Read this section before your first real run.**

This repository ships fully configured and working, tuned for the IFoA General Insurance Machine Learning in Reserving Working Party. That is deliberate. You can clone it and run it immediately, and the settings below double as a worked example of what a well-tuned setup actually looks like.

But those settings are ours. If your interest is something else — generative AI in reserving, machine learning in life assurance, anything at all — you must retune them before the output means anything to you. The failure mode here is unpleasant, because nothing breaks: the pipeline runs, produces a digest, and reports success. It just hands you reserving papers. Nothing errors. It quietly answers our question instead of yours.

There are three places to change. They act at different stages of the pipeline, and you need all three.

| # | What it controls | Where to edit | Stage |
|---|---|---|---|
| 1 | Which papers arXiv returns at all | `[query]` in `config.toml` — the `domain_terms` and `method_terms` lists | Before any LLM call |
| 2 | What the LLM treats as relevant and scores highly | `TEAM_PROFILE` in `src/prompt1.py` | Scoring pass |
| 3 | What the optional second pass asks about each shortlisted paper | `pipeline_data/review_questions.json` | Review pass |

**1. Search terms (`config.toml` `[query]`).** These are the literal terms sent to the arXiv API. They decide the candidate pool. A paper that does not match here is never seen by the LLM at all, no matter how relevant it is — so this is the one place where being too narrow silently costs you papers. The shipped lists are full of terms like `claims reserving` and `non-life insurance`. Replace them wholesale with your own domain vocabulary. `use_and_query = true` means a paper must match a domain term *and* a method term; set it to `false` to widen to either.

**2. Team profile (`src/prompt1.py`).** `TEAM_PROFILE` is a Python dictionary with three keys — `focus` (a prose paragraph describing who the team is and what it wants), `interests` (a list of topics to reward), and `avoid` (a list of topics to penalise). This text is injected into the prompt for every paper. It is the main lever on what counts as relevant, and it is where you say, in plain English, what your working party is for. Rewrite all three keys. Leaving the shipped reserving text here while changing only the search terms is the most common way to get confusing results: arXiv hands you generative-AI papers and the LLM scores them low because the profile still says it wants reserving.

**3. Review questions (`pipeline_data/review_questions.json`).** The 15 questions shipped here are the working party's own, asking about modelling technique, dataset type, reserving-specific keywords and so on. Rewrite them for your subject area. Use them as a format guide first: each entry is an object with a `question` string and a `keys` list naming the output fields that question produces, so a question asking for a value plus a justification declares two keys, conventionally named `something_value` and `something_explanation`. Note how the shipped questions enumerate their allowed answers in square brackets — the LLM follows an explicit list far more reliably than an open-ended instruction, so keep that pattern when you write your own. `[review] max_questions` in `config.toml` caps how many are asked; it is set to 15 to cover the current file.

Everything else — lookback window, how many papers reach the digest, which model is used — is ordinary configuration and is covered under [Configuration](#configuration-configtoml) below.

## Scoring

The scoring rubric lives in `src/evaluation.py`, inside `build_paper_evaluation_prompt()`:

```
- 9 to 10: directly about reserving and highly useful to the working party
- 7 to 8: not directly about reserving, but strongly transferable with clear practical value
- 5 to 6: adjacent and somewhat useful, but not a priority
- 0 to 4: weak relevance to reserving or little practical value
```

The LLM returns a `relevance_score` (0–10) for every paper, along with a `summary` and `key_insight`. A paper is flagged `is_relevant` if its score is **≥ 7** — that threshold is hardcoded in `src/create_batch_evaluation.py` (search for `is_relevant = score >= 7`). Only `is_relevant` papers are eligible for the digest; the top `selection.top_n` of those (set in `config.toml`, currently 100) are written out. Every scored paper, relevant or not, still appears in the CSV/XLSX evaluation output for manual review.

An optional second-pass **structured review** (`src/run_structured_review.py`) asks the LLM a fixed set of questions about each top-N paper, and folds the answers into the digest. The questions come from `pipeline_data/review_questions.json` — see [Customising This For Your Team](#customising-this-for-your-team), which explains why you will want to rewrite them.

**Only the title and abstract are sent to the LLM in this first pass** — not the paper body. The abstract comes straight from arXiv's metadata for whatever papers survive the `[query]` filter. That keeps pass 1 cheap, since it runs across every candidate paper.

The second pass is different: it **does** download the full PDF. `_download_pdf_text()` in `src/run_structured_review.py` fetches `arxiv.org/pdf/<id>`, extracts the text with `pypdf`, and passes up to 32,000 characters to the LLM, falling back to the abstract if the download or parse fails. Because that is a much heavier call, it runs only over the shortlist rather than every candidate.

## The Two Passes, and the Review Modes

Scoring runs in two stages, and `[review] mode` in `config.toml` decides how they are sequenced.

**Pass 1 (always runs)** scores every candidate paper on title and abstract, producing the 0–10 relevance score and the ranked digest. It is cheap and wide.

**Pass 2 (optional)** takes only the papers that made the digest, downloads each full PDF, and asks the LLM your fixed list of questions from `pipeline_data/review_questions.json`. It is expensive and narrow, so it is kept separate rather than folded into pass 1.

| `mode` | What happens | When to use it |
|---|---|---|
| `"separate"` | `main.py` runs pass 1 and stops. You run pass 2 yourself afterwards, against the digest that already exists. | **The default, and usually right.** You can look at the digest before committing to the PDF downloads, and re-run pass 2 with different questions without re-scoring anything. |
| `"inline"` | `main.py` runs pass 1, then pass 2 immediately, in one command. | Unattended or scheduled runs where nobody is around to trigger the second step. |
| `"off"` | Pass 2 never runs. Pass 1 only. | You only want the ranked digest and do not need the structured answers. |

**Overriding the mode for one run.** The table above describes `config.toml`, which is your permanent default. To change it for a single run without editing the file:

```bash
uv run python src/main.py --review-mode inline      # this run only
uv run python src/main.py                            # back to the config default
```

This matters because the config setting is sticky: edit it for a one-off, forget to change it back, and a later full run quietly does pass 2 on every shortlisted paper. The flag leaves `config.toml` untouched and logs the override in the run log. It also sets `enabled` to match, so `--review-mode inline` works even if the config has `enabled = false`.

In `"separate"` mode, run pass 2 like this:

```bash
uv run python src/run_structured_review.py
```

It picks the most recent `digest_*.md` in your results directory automatically. Use `--digest <path>` to choose a specific one, or `--config <path>` to read a different config file.

Two things to know. `enabled = true` must also be set, alongside the mode. And if you have already run pass 1, do **not** switch to `"inline"` to get pass 2 — that re-runs pass 1 from scratch and pays for the scoring twice. Use `"separate"` and run the script.

## Controlling the seen-papers registry

`pipeline_data/seen_papers.json` is a plain list of arXiv IDs, one per line, that the pipeline has already processed. Anything in it is dropped before scoring, so repeat runs do not resurface the same papers or pay to re-score them.

There is **no setting in `config.toml`** for this. Two other controls exist:

- **`SEEN_PAPERS_FILE`** — an environment variable holding the path to the registry. Point it somewhere else and the real one is never touched:
  ```bash
  SEEN_PAPERS_FILE=/tmp/seen_test.json uv run python src/main.py
  ```
  This is the clean way to do a **test run without polluting your history**. Copy the real file to a temporary path first if you want dedup to behave realistically during the test; start from an empty file if you want every paper to come through.
- **`--no-save-seen`** — a flag on `src/batch_tools.py` that parses a completed batch without recording those papers as seen.

To make papers eligible again, delete their IDs from the file, or delete the file entirely to start fresh.

## Swapping the LLM Provider

### Another provider with a batch API (e.g. OpenAI)

The code (`src/main.py`, `src/create_batch_evaluation.py`, `src/batch_tools.py`) calls the plain OpenAI SDK's batch surface — `files.create(purpose="batch")`, `batches.create(endpoint="/v1/chat/completions", ...)`, `batches.retrieve`, `files.content`. Doubleword mirrors that same interface, so this is a config swap, not a code change:

1. `.env`: set `DW_BASE_URL` and `DW_API_KEY` to the new provider's values (e.g. `https://api.openai.com/v1` and an OpenAI key — the `DW_` naming is just leftover from the original Doubleword setup, the values are provider-agnostic).
2. `config.toml` `[inference] model`: set this to a model the new provider actually hosts (the Qwen defaults are Doubleword-hosted and will not exist elsewhere). **`config.toml` wins over `.env` here.** `MODEL_NAME` in `.env` is only a fallback used when `config.toml` omits the `model` key, so editing `.env` alone will appear to do nothing — see `src/config_loader.py:342`.
3. `config.toml` `[inference] completion_window`: OpenAI's batch API only accepts `"24h"` — Doubleword allows shorter windows like `"1h"`. Set this to `"24h"` for OpenAI or it'll be rejected at submission.

**Bear in mind**: batch endpoints are asynchronous queues, and turnaround varies with the provider's load. Doubleword typically returns within the hour when `completion_window = "1h"`. OpenAI only accepts a 24-hour window, so plan around that figure rather than expecting a particular latency. For a monthly or quarterly run neither matters much, but do not expect an immediate answer if you are testing interactively.

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
  uv run python src/batch_tools.py status
  uv run python src/batch_tools.py resume
  ```
- **Run the structured second-pass review** separately, on an existing digest:
  ```bash
  uv run python src/run_structured_review.py
  ```
- **Retry papers that failed to parse** (beyond the inline requeue rounds already attempted during a run):
  ```bash
  uv run python src/requeue_parse_failures.py
  ```
- **Score a search you already ran**, without querying arXiv again:
  ```bash
  uv run python src/main.py --from-snapshot runs/search/arxiv_search_20260917_115817.json
  ```
  This is the companion to `--arxiv-only`. Run the free search first, look at what it found, then score exactly those papers when you are happy — instead of re-querying arXiv and possibly getting a different set because new papers appeared in between.

  The seen-papers filter is re-applied against your *current* registry, not the snapshot's. A snapshot can be days old, and anything scored since is dropped and logged as `[drop_seen_since_snapshot]`, so you never pay twice for the same paper. It cannot be combined with `--arxiv-only`, which stops before any scoring.

## Resuming a run from the middle

Long batches and interrupted runs are normal, so most stages can be re-entered without repeating the ones before:

| Where you are | How to carry on |
|---|---|
| Search done, not scored yet | `uv run python src/main.py --from-snapshot runs/search/arxiv_search_*.json` |
| Batch submitted, run interrupted | `uv run python src/batch_tools.py resume --batch-id <id>` (omit `--batch-id` to use the latest from the run logs) |
| Digest exists, want the second pass | `uv run python src/run_structured_review.py` |
| Some papers failed to parse | `uv run python src/requeue_parse_failures.py` |

`resume` is the one worth remembering. It finds the run log for that batch, recovers the paper metadata from `runs/batch_requests/batch_requests_<timestamp>.jsonl`, then parses, ranks and writes the digest — so an interrupted poll never costs you the batch you already paid for.

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

None of these are tracked in git. `runs/` and the generated state in `pipeline_data/` are ignored, so a fresh clone starts empty and every directory is created automatically on the first run. There is nothing to set up by hand.

### Example outputs

Four files are committed as exceptions to that rule, so you can see what the pipeline produces before running it yourself. They are real output from past runs, kept in place so the folder layout is the one you will actually get, and all named `*_example.*`:

| Example file | What it shows you |
|---|---|
| `runs/results/digest_20260810_171603_example.md` | A complete five-paper digest — the run metadata block, then each paper with its score, `is_relevant` flag, `key_insight` and summary. This is the product; start here. |
| `runs/results/parsed/evaluation_results_20260408_example.csv` | The per-paper scoring table, header plus five rows, showing all 17 columns that go to manual review. The full version has a row for every paper scored, not just the selected ones. |
| `runs/logs/run_20260810_171015_example.log` | A run trace. The opening lines echo the whole resolved configuration, which makes this a useful known-good reference when your own run behaves unexpectedly. |
| `runs/search/arxiv_search_20260810_171603_example.json` | The arXiv search snapshot written by `--arxiv-only`, truncated to three papers. Shows the generated query string and the candidate/filter/drop counts. |

These are illustrative, not fixtures: no test or code path reads them, and deleting them breaks nothing. Because the ignore rule keys on the `_example` suffix, any file you name that way inside `runs/` will also be tracked — worth knowing before you name a real run output that way by accident.

## Troubleshooting

**`ModuleNotFoundError: No module named 'openai'`**: You ran `python` without the `uv run` prefix, so your system Python was used instead of the project's environment. Re-run the command as `uv run python src/main.py`. See the note at the end of [Setup](#setup).

**`command not found: uv`**: Step 2 of Setup was skipped, or the terminal was not restarted afterwards. Close the terminal, reopen it, `cd` back into the project folder and try `uv --version` again.

**"No such file or directory" on `config.toml` or `.env`**: You are running from the wrong folder. `cd` into the cloned `arxiv-AI-digest` folder and try again.

**No papers found on Monday**: Normal — arXiv doesn't publish on weekends, so a Monday run looks back further to catch Friday's papers (unless a custom `[lookback]` window is set).

**Batch not completing**: Batch processing takes time. Check status with `uv run python src/batch_tools.py status --batch-id <id>`. Poll interval is set by `network.poll_interval_seconds` in `config.toml`.

**`seen_papers.json` corrupted**: Delete `pipeline_data/seen_papers.json` and it will regenerate fresh on the next run.

**JSON parsing errors**: The code tolerates `<think>` tags and extra text from reasoning models, but persistent parse failures show up in `pipeline_data/parse_failures.json` — retry them with `uv run python src/requeue_parse_failures.py`.

## Further Reading

- `architecture.md` — pipeline diagram and component breakdown
- `LOCAL_CUSTOMIZATIONS.md` — full changelog of local changes vs. the upstream Doubleword repo
- `DEFERRED_WORK.md` — scoped-but-unstarted work, kept as a local note. It is gitignored, so it
  will not be in your clone; create your own if you want to track the same thing.
