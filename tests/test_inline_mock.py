#!/usr/bin/env python3
"""End-to-end test of INLINE review mode, with the Doubleword batch API mocked.

WHY THIS EXISTS
---------------
A real pass-1 batch can sit in Doubleword's queue for over an hour (measured:
~70 minutes for a 2-request job on 2026-09-17). That made the inline code path
effectively untestable, and a bug lived there unnoticed as a result: inline mode
ran its structured review on the title and abstract only, never downloading the
PDF, so it asked 15 detailed questions of evidence that could not answer them.

This test replaces ONLY the batch surface (`files` and `batches`) with a mock
that replays a real recorded response after a short fake delay. Everything else
runs for real, deliberately:

  * the arXiv search and all filtering
  * pass-1 result parsing, scoring, and digest selection
  * pass-2 PDF download and text extraction
  * pass-2's LIVE `chat.completions` call to Doubleword

Keeping pass 2 live is the point. The queue is the only slow part, so it is the
only part worth faking. A mocked pass 2 would prove nothing about whether the
structured review actually works.

WHAT IT ASSERTS
---------------
  1. The pipeline completes and writes a digest.
  2. Pass 2 downloaded real PDFs (not the abstract fallback) -- this is the
     regression guard for the inline bug described above.
  3. Every question returned a populated value, i.e. the response schema's enums
     are being enforced. Before enums were added, one test paper returned null
     for 15 of 15 value fields while burying the answers in the prose.
  4. The structured profile is folded into the digest markdown, which is what
     distinguishes inline mode from separate mode.

ISOLATION
---------
Writes nothing into the repo. It builds a temporary config, a temporary
seen-papers file, and a temporary output directory, then deletes them. Your real
`pipeline_data/seen_papers.json` is never opened for writing. Pass `--keep` to
retain the output for inspection.

REQUIREMENTS
------------
Needs a working DW_API_KEY in `.env`, because pass 2 makes a real LLM call. It
downloads two PDFs from arXiv, so it needs network access. Cost is small (two
long-context calls) but not zero.

USAGE
-----
    uv run python tests/test_inline_mock.py
    uv run python tests/test_inline_mock.py --keep --delay 5
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime

REPO = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "batch_output_2papers.jsonl"

# The fixture is a real recorded Doubleword response for these two arXiv papers.
# Both scored 9/10 in the run it was captured from (batch 4affb8bb, 2026-09-17).
FIXTURE_PAPER_IDS = ["2609.16552v1", "2609.16561v1"]


# --------------------------------------------------------------------------
# Mock batch surface. Mimics only what main.py actually calls.
# --------------------------------------------------------------------------
class _FakeFile:
    def __init__(self, fid):
        self.id = fid


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeFiles:
    def __init__(self, payload):
        self._payload = payload

    def create(self, **_kwargs):
        return _FakeFile("mock-input-file")

    def content(self, _fid):
        return _FakeContent(self._payload)


class _FakeCounts:
    def __init__(self, done, total):
        self.completed, self.failed, self.total = done, 0, total


class _FakeBatch:
    def __init__(self, status, done, total):
        self.id = "mock-batch-0001"
        self.status = status
        self.request_counts = _FakeCounts(done, total)
        self.output_file_id = "mock-output-file"
        self.error_file_id = None
        self.errors = None


class _FakeBatches:
    """Reports 'validating' for `delay` seconds, then 'completed'.

    The delay exists so the polling loop is genuinely exercised rather than
    short-circuited on the first retrieve().
    """

    def __init__(self, delay, total):
        self._delay, self._total, self._t0 = delay, total, None

    def create(self, **_kwargs):
        self._t0 = time.time()
        return _FakeBatch("validating", 0, self._total)

    def retrieve(self, _bid):
        if self._t0 is None or (time.time() - self._t0) < self._delay:
            return _FakeBatch("validating", 0, self._total)
        return _FakeBatch("completed", self._total, self._total)


# --------------------------------------------------------------------------
def build_temp_config(tmp, out_dir):
    """Copy the real config.toml, overriding only what the test needs.

    Everything else (model, query terms, review questions) stays as shipped, so
    the test exercises the real configuration rather than a synthetic one.
    """
    text = (REPO / "config.toml").read_text()
    text = re.sub(r"^mode = .*$", 'mode = "inline"', text, count=1, flags=re.M)
    text = re.sub(r"^max_results = .*$", "max_results = 10", text, count=1, flags=re.M)
    for key, sub in [
        ("log_dir", "logs"),
        ("results_dir", "results"),
        ("batch_requests_dir", "batch_requests"),
        ("search_results_dir", "search"),
    ]:
        target = out_dir / sub
        target.mkdir(parents=True, exist_ok=True)
        text = re.sub(rf'^{key} = ".*"$', f'{key} = "{target}"', text, count=1, flags=re.M)
    path = tmp / "config.test.toml"
    path.write_text(text)
    return path


def check(label, ok, detail=""):
    ok = bool(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--delay", type=float, default=15.0,
                    help="Seconds the mock batch stays 'validating' (default 15).")
    ap.add_argument("--keep", action="store_true",
                    help="Keep the temporary output directory for inspection.")
    args = ap.parse_args()

    if not FIXTURE.exists():
        sys.exit(f"Missing fixture: {FIXTURE}")

    sys.path.insert(0, str(REPO / "src"))
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="arxiv_inline_test_"))
    out_dir = tmp / "out"
    exit_code = 1
    try:
        config_path = build_temp_config(tmp, out_dir)
        seen_file = tmp / "seen_papers_TEST.json"
        seen_file.write_text("")            # empty: let the fixture papers through

        # Point the pipeline at the temp copies BEFORE importing it: both paths
        # are read at module import time.
        os.environ["CONFIG_FILE"] = str(config_path)
        os.environ["SEEN_PAPERS_FILE"] = str(seen_file)

        import main
        from config_loader import load_runtime_config

        if not os.getenv("DW_API_KEY"):
            sys.exit("DW_API_KEY not set - pass 2 makes a real LLM call. Check your .env.")

        payload = FIXTURE.read_text()
        main.client.files = _FakeFiles(payload)
        main.client.batches = _FakeBatches(args.delay, len(FIXTURE_PAPER_IDS))
        print(f"  mocked batch surface (delay {args.delay:.0f}s); chat.completions left LIVE\n")

        cfg = load_runtime_config(str(config_path))
        assert cfg["review"]["mode"] == "inline", cfg["review"]["mode"]

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        logger, _ = main.setup_run_logger(cfg["output"]["log_dir"], ts)
        digest = main.daily_run(
            lookback_config=cfg["lookback"], max_results=cfg["max_results"],
            query_config=cfg["query"], selection_config=cfg["selection"],
            output_config=cfg["output"], inference_config=cfg["inference"],
            search_tuning_config=cfg["search_tuning"], network_config=cfg["network"],
            review_config=cfg["review"], run_timestamp=ts, logger=logger, arxiv_only=False,
        )

        log_text = "\n".join(
            p.read_text(errors="replace") for p in pathlib.Path(cfg["output"]["log_dir"]).glob("*.log")
        )
        digest_text = pathlib.Path(digest).read_text(errors="replace")

        print("\n  --- assertions ---")
        ok = True
        ok &= check("pipeline completed and wrote a digest", bool(digest) and bool(digest_text.strip()))

        # Regression guard: inline used to send the abstract only.
        pdf_hits = re.findall(r"\[pdf_download\].*chars=(\d+)", log_text)
        ok &= check("pass 2 downloaded real PDFs (not abstract fallback)",
                    len(pdf_hits) >= 2 and all(int(c) > 10000 for c in pdf_hits),
                    f"chars={pdf_hits}")

        # Regression guard: without enums, value fields came back null.
        key_counts = [int(m) for m in re.findall(r"dw_structured_review_ok\].*keys=(\d+)", log_text)]
        ok &= check("structured review returned a full key set",
                    len(key_counts) >= 2 and all(k >= 30 for k in key_counts),
                    f"keys={key_counts}")
        ok &= check("no parse failures in pass 2",
                    "dw_structured_review_parse_failed" not in log_text)

        # Inline folds the answers into the digest; separate mode writes its own file.
        ok &= check("structured profile folded into the digest",
                    digest_text.count("Structured profile") >= 2)

        exit_code = 0 if ok else 1
        print(f"\n  {'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}")
        if args.keep:
            print(f"  output kept at: {out_dir}")
    finally:
        # Cleanup: leave no stray files behind unless explicitly asked.
        if args.keep:
            print(f"  (temp dir retained: {tmp})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
