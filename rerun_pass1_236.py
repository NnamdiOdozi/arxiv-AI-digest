#!/usr/bin/env python3
"""
Re-run pass 1 for all 236 papers with EVALUATION_SCHEMA structured output.
Patches the combined_batch CSV and XLSX with fresh scores, summaries, and key insights.
Run from project root: python rerun_pass1_236.py
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent / "src"))
from evaluation import create_batch_evaluation
from create_batch_evaluation import wait_for_batch

load_dotenv()
client = OpenAI(base_url=os.getenv("DW_BASE_URL"), api_key=os.getenv("DW_API_KEY"))
MODEL = "Qwen/Qwen3-VL-235B-A22B-Instruct-FP8"

SEARCH_JSON = "runs/search/arxiv_search_20260408_162305.json"
CSV_PATH = "runs/results/parsed/combined_batch_162305_205957_parsed_success.csv"
XLSX_PATH = "runs/results/parsed/combined_batch_162305_205957_parsed_success.xlsx"

# --- 1. Load papers ---
data = json.loads(Path(SEARCH_JSON).read_text())
papers = data["papers_after_combined_filter"]
print(f"Loaded {len(papers)} papers from search snapshot")

# --- 2. Build and submit batch ---
requests = create_batch_evaluation(papers, model_name=MODEL)
batch_file_path = "runs/batch_requests/rerun_pass1_236.jsonl"
Path(batch_file_path).parent.mkdir(parents=True, exist_ok=True)
with open(batch_file_path, "w") as f:
    for req in requests:
        f.write(json.dumps(req) + "\n")

with open(batch_file_path, "rb") as f:
    uploaded = client.files.create(file=f, purpose="batch")
batch = client.batches.create(
    input_file_id=uploaded.id,
    endpoint="/v1/chat/completions",
    completion_window="1h",
)
print(f"Batch submitted: {batch.id}")

# --- 3. Poll ---
results, failed_ids = wait_for_batch(client, batch.id, check_interval=30)
print(f"Batch complete: {len(results)} parsed, {len(failed_ids)} failed")
if failed_ids:
    print(f"Failed IDs: {failed_ids}")

# --- 4. Patch CSV and XLSX ---
df = pd.read_csv(CSV_PATH)
results_by_id = {r["paper_id"]: r for r in results}

for idx, row in df.iterrows():
    pid = row["paper_id"]
    if pid in results_by_id:
        r = results_by_id[pid]
        df.at[idx, "relevance_score"] = r["relevance_score"]
        df.at[idx, "is_relevant"] = r["is_relevant"]
        df.at[idx, "summary"] = r.get("summary")
        df.at[idx, "key_insight"] = r.get("key_insight", "")

df.drop(columns=["needs_summary"], errors="ignore", inplace=True)
df.to_csv(CSV_PATH, index=False)
df.to_excel(XLSX_PATH, index=False)
print(f"Patched {len(results_by_id)} rows in CSV and XLSX")
