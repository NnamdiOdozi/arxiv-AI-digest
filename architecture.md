# Architecture

## Overview

`arxiv-daily-digest` is a daily pipeline that fetches recent arXiv papers, scores them for relevance using an LLM, and produces a ranked markdown digest and Excel evaluation file. It is designed for the IFoA General Insurance ML in Reserving Working Party.

## End-to-End Flow

```mermaid
flowchart TD
    A["config.toml + prompt1.py"] -->|query, terms, model| B

    B["1. Fetch Papers<br/>src/get_papers.py"]
    B -->|arXiv API| C{Filters}
    C -->|lookback window + domain term match| D[Paper pool]
    C -->|already seen| X1["Dropped<br/>seen_papers.json"]
    D --> M["runs/search/arxiv_snapshot_TIMESTAMP.json"]

    D --> E["2. Score Papers<br/>src/evaluation.py<br/>src/create_batch_evaluation.py"]
    E -->|JSONL batch file| F["Doubleword / OpenAI Batch API"]
    F -->|async poll| G{Parse results}
    G -->|parsed ok| R1[Scored papers]
    G -->|parse failed| R2[Failed papers]

    R2 -->|inline resubmit<br/>up to N rounds| F
    R2 -->|max rounds reached| R3["pipeline_data/parse_failures.json"]

    R1 --> P["3. Write Evaluation Results<br/>src/output.py"]
    P --> Q["runs/results/parsed/<br/>evaluation_results_TIMESTAMP.csv/.xlsx"]

    R1 --> H["4. Select Top N<br/>src/send_to_slack.py"]
    H -->|is_relevant=true, sorted by score| I[Top N papers]

    I --> K["5. Markdown Digest<br/>src/output.py"]
    K --> L["runs/results/digest_TIMESTAMP.md"]

    I -.->|optional second pass| N["6. Structured Review<br/>src/run_structured_review.py"]
    N --> O["runs/results/detailed_review_TIMESTAMP.md"]
```

## Key Components

| File | Role |
|------|------|
| `src/main.py` | Orchestrates the full pipeline |
| `src/get_papers.py` | arXiv API fetch, date/term filtering, dedup via `seen_papers.json` |
| `src/evaluation.py` | LLM prompt + structured output schema (`relevance_score`, `summary`, `key_insight`) |
| `src/create_batch_evaluation.py` | Submits batch, polls for completion, parses results |
| `src/send_to_slack.py` | Top-N paper selection (Slack output disabled) |
| `src/requeue_parse_failures.py` | Offline retry for papers that failed to parse; appends recovered rows to evaluation results |
| `src/output.py` | Writes evaluation CSV/XLSX, markdown digest, and arXiv search snapshot JSON |
| `src/run_structured_review.py` | Optional standalone second-pass structured review of top papers |
| `src/config_loader.py` | Loads `config.toml`; holds defaults for all pipeline settings |
| `prompt1.py` | Team interest profile used to personalise LLM scoring |

## Scoring

Each paper is scored 0–10 by the LLM against the team's interest profile. Papers scoring ≥ 7 are marked relevant. The top `N` relevant papers (default 10) are written to the digest.

## Outputs

| File | Description |
|------|-------------|
| `runs/results/digest_TIMESTAMP.md` | Human-readable ranked digest of top N papers |
| `runs/results/parsed/evaluation_results_TIMESTAMP.csv/.xlsx` | Full scored paper set — all papers with `relevance_score`, `summary`, `key_insight`; Excel-ready for manual review |
| `runs/search/arxiv_snapshot_TIMESTAMP.json` | Full arXiv search trace for auditing and re-runs |
| `runs/results/detailed_review_TIMESTAMP.md` | Optional structured deep-dive (second pass) |
| `pipeline_data/seen_papers.json` | Dedup registry; prevents re-processing the same paper |
| `pipeline_data/parse_failures.json` | Papers that exhausted inline requeue rounds; consumed by offline retry |
