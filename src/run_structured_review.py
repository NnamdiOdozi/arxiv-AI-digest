#!/usr/bin/env python3
import argparse
import io
import json
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path

import arxiv
from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader

from config_loader import PROJECT_ROOT, REVIEW_DIGEST_FIELDS, load_runtime_config
from review import load_review_questions, enrich_top_papers_with_structured_review

if os.path.exists(".env"):
    load_dotenv()

_client = OpenAI(base_url=os.getenv("DW_BASE_URL"), api_key=os.getenv("DW_API_KEY"))


def _log(message):
    print(message)


def _resolve_path(base_dir, path_text):
    path = Path(path_text)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _find_latest_digest(results_dir):
    pattern = re.compile(r"^digest_(\d{8}_\d{6})\.md$")
    candidates = []
    for p in results_dir.glob("digest_*.md"):
        match = pattern.match(p.name)
        if match:
            candidates.append((p.stat().st_mtime, p))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _extract_run_timestamp_from_digest(digest_path):
    match = re.match(r"^digest_(\d{8}_\d{6})\.md$", digest_path.name)
    if not match:
        return None
    return match.group(1)


def _extract_paper_ids_from_digest(digest_path):
    text = digest_path.read_text(encoding="utf-8", errors="replace")
    ids = re.findall(r"https?://arxiv\.org/abs/([^\)\s]+)", text)
    ordered = []
    seen = set()
    for paper_id in ids:
        if paper_id in seen:
            continue
        seen.add(paper_id)
        ordered.append(paper_id)
    return ordered


def _parse_prompt_title_abstract(prompt_text):
    title_match = re.search(
        r"^\s*TITLE:\s*\n(.*?)^\s*ABSTRACT:\s*$",
        prompt_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    abstract_match = re.search(
        r"^\s*ABSTRACT:\s*\n(.*?)^\s*INSTRUCTIONS:\s*$",
        prompt_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    title = title_match.group(1).strip() if title_match else ""
    abstract = abstract_match.group(1).strip() if abstract_match else ""
    return title, abstract


def _load_paper_pool_from_batch_requests(run_timestamp, batch_requests_dir):
    batch_file = batch_requests_dir / f"batch_requests_{run_timestamp}.jsonl"
    papers_by_id = {}

    if not batch_file.exists():
        _log(f"No batch request file found for this run: {batch_file}")
        return papers_by_id

    with batch_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            paper_id = item.get("custom_id")
            if not paper_id:
                continue

            prompt_text = ""
            messages = item.get("body", {}).get("messages", [])
            for message in messages:
                if message.get("role") == "user":
                    prompt_text = message.get("content", "")
                    break

            title, abstract = _parse_prompt_title_abstract(prompt_text)
            papers_by_id[paper_id] = {
                "id": paper_id,
                "title": title or paper_id,
                "abstract": abstract or "",
                "url": f"http://arxiv.org/abs/{paper_id}",
                "authors": [],
                "published": None,
            }
    _log(f"Loaded paper metadata from batch request file: {batch_file}")
    return papers_by_id


def _enrich_papers_with_arxiv_metadata(papers_by_id, paper_ids):
    client = arxiv.Client(page_size=10, delay_seconds=4.0, num_retries=3)
    for idx, paper_id in enumerate(paper_ids, 1):
        paper = papers_by_id.setdefault(
            paper_id,
            {
                "id": paper_id,
                "title": paper_id,
                "abstract": "",
                "url": f"http://arxiv.org/abs/{paper_id}",
                "authors": [],
                "published": None,
            },
        )
        try:
            search = arxiv.Search(id_list=[paper_id], max_results=1)
            result = next(client.results(search), None)
            if result is None:
                _log(f"[arxiv_enrich] {idx}/{len(paper_ids)} id={paper_id} not found")
                continue
            if not paper.get("title") or paper["title"] == paper_id:
                paper["title"] = result.title
            if not paper.get("abstract"):
                paper["abstract"] = result.summary
            paper["url"] = result.entry_id
            paper["authors"] = [a.name for a in result.authors]
            paper["published"] = result.published.isoformat() if result.published else None
            _log(f"[arxiv_enrich] {idx}/{len(paper_ids)} id={paper_id} ok")
        except Exception as exc:
            _log(f"[arxiv_enrich] {idx}/{len(paper_ids)} id={paper_id} failed: {exc}")


def _download_pdf_text(paper_id, max_chars=32000):
    """Download arXiv PDF and extract plain text. Returns None on any failure."""
    url = f"https://arxiv.org/pdf/{paper_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "arxiv-daily-digest/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            pdf_bytes = resp.read()
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)
            if sum(len(p) for p in pages) >= max_chars:
                break
        full_text = "\n".join(pages)
        return full_text[:max_chars] if full_text.strip() else None
    except Exception:
        return None


def _enrich_papers_with_pdf_text(papers_by_id, paper_ids, log):
    """Download and extract PDF text for each paper, adding a full_text key."""
    for idx, paper_id in enumerate(paper_ids, 1):
        paper = papers_by_id.get(paper_id)
        if not paper:
            continue
        text = _download_pdf_text(paper_id)
        if text:
            paper["full_text"] = text
            log(f"[pdf_download] {idx}/{len(paper_ids)} id={paper_id} chars={len(text)}")
        else:
            log(f"[pdf_download] {idx}/{len(paper_ids)} id={paper_id} failed — using abstract")


def _format_markdown_value(value):
    if value is None:
        return "unknown"
    if isinstance(value, list):
        if not value:
            return "none"
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_detailed_markdown(results, papers_by_id, source_digest, output_path):
    lines = [
        f"# Structured Review {datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "",
        f"- source_digest: {source_digest}",
        f"- reviewed_papers: {len(results)}",
        "",
    ]

    for idx, result in enumerate(results, 1):
        paper_id = result.get("paper_id")
        paper = papers_by_id.get(paper_id, {})
        title = paper.get("title", paper_id)
        url = paper.get("url", f"http://arxiv.org/abs/{paper_id}")
        authors = paper.get("authors") or []
        published = paper.get("published") or "unknown"
        lines.extend(
            [
                f"## {idx}. [{title}]({url})",
                f"- paper_id: {paper_id}",
                f"- published: {published}",
                f"- authors: {', '.join(authors) if authors else 'unknown'}",
                "",
            ]
        )

        structured_review = result.get("structured_review")
        if not isinstance(structured_review, dict) or not structured_review:
            lines.extend(["No structured review content returned.", ""])
            continue

        lines.append("Structured profile:")
        emitted = set()
        for key, label in REVIEW_DIGEST_FIELDS:
            if key not in structured_review:
                continue
            emitted.add(key)
            lines.append(f"- {label}: {_format_markdown_value(structured_review.get(key))}")
        for key in sorted(k for k in structured_review.keys() if k not in emitted):
            lines.append(f"- {key.replace('_', ' ')}: {_format_markdown_value(structured_review.get(key))}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Run structured second-pass review on papers selected in an existing digest."
    )
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument(
        "--digest",
        default=None,
        help="Path to digest markdown (default: latest digest_YYYYMMDD_HHMMSS.md in results_dir).",
    )
    args = parser.parse_args()

    config_path = _resolve_path(PROJECT_ROOT, args.config)
    config = load_runtime_config(str(config_path))
    review_config = config["review"]
    results_dir = _resolve_path(PROJECT_ROOT, config["output"]["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)

    if not review_config["enabled"]:
        _log("Structured review is disabled in config ([review].enabled=false).")
        return 1

    digest_path = _resolve_path(PROJECT_ROOT, args.digest) if args.digest else _find_latest_digest(results_dir)
    if not digest_path or not digest_path.exists():
        _log("Could not find a digest file to review.")
        return 1

    run_timestamp = _extract_run_timestamp_from_digest(digest_path)
    if not run_timestamp:
        _log(f"Digest file name does not match expected format: {digest_path.name}")
        return 1

    paper_ids = _extract_paper_ids_from_digest(digest_path)
    if not paper_ids:
        _log(f"No arXiv paper IDs found in digest: {digest_path}")
        return 1

    _log(f"Using digest: {digest_path}")
    _log(f"Selected papers to review: {len(paper_ids)}")

    question_specs = load_review_questions(review_config, _log)
    if not question_specs:
        _log("No structured review questions available.")
        return 1

    batch_requests_dir = _resolve_path(PROJECT_ROOT, config["output"].get("batch_requests_dir", "runs/batch_requests"))
    papers_by_id = _load_paper_pool_from_batch_requests(run_timestamp, batch_requests_dir)
    _enrich_papers_with_arxiv_metadata(papers_by_id, paper_ids)
    _enrich_papers_with_pdf_text(papers_by_id, paper_ids, _log)

    top_results = [{"paper_id": paper_id} for paper_id in paper_ids]
    enrich_top_papers_with_structured_review(
        top_results=top_results,
        papers_by_id=papers_by_id,
        question_specs=question_specs,
        model_name=config["inference"]["model"],
        network_config=config["network"],
        log=_log,
        client=_client,
    )

    output_json = results_dir / f"detailed_review_{run_timestamp}.json"
    output_md = results_dir / f"detailed_review_{run_timestamp}.md"

    output_json.write_text(
        json.dumps(
            {
                "source_digest": str(digest_path),
                "generated_at": datetime.now().isoformat(),
                "paper_ids": paper_ids,
                "results": top_results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_detailed_markdown(top_results, papers_by_id, str(digest_path), output_md)

    _log(f"Structured review JSON written: {output_json}")
    _log(f"Structured review markdown written: {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
