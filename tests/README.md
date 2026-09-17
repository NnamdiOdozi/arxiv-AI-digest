# Tests

## `test_inline_mock.py` — inline review mode, end to end

```bash
uv run python tests/test_inline_mock.py
```

Takes about two minutes and costs roughly $0.01. Needs `DW_API_KEY` in `.env` and
network access, because part of it deliberately runs for real.

It forces `service_tier = "priority"` regardless of your config. On `"flex"` the same
test takes ~15 minutes — 5 minutes compute per paper plus queue — and a test that slow
is a test nobody runs. Use `flex` for bulk work, not for anything you are waiting on.

### What problem this solves

A real pass-1 batch can sit in Doubleword's queue for a long time — measured at
roughly **70 minutes for a two-request job** on 17 September 2026. That made the
inline code path impractical to test, and a bug survived there unnoticed:
inline mode ran its structured review on the title and abstract only, never
downloading the PDF. It was asking fifteen detailed questions of evidence that
could not possibly answer them, and reporting success.

This test mocks **only** the batch queue. It replays a real recorded Doubleword
response (`fixtures/batch_output_2papers.jsonl`, captured from batch `4affb8bb`)
after a short fake delay, so the polling loop is exercised without the wait.

Everything else runs for real, on purpose:

- the arXiv search and every filter stage
- pass-1 parsing, scoring, and digest selection
- pass-2 PDF download and text extraction from arxiv.org
- **pass 2's live `chat.completions` call**

Keeping pass 2 live is the whole point. The queue was the only slow part, so it
is the only part worth faking. A mocked pass 2 would prove nothing about whether
the structured review actually works.

### What it checks

| Check | Guards against |
|---|---|
| Pipeline completes and writes a digest | General breakage |
| Pass 2 downloaded real PDFs, >10k chars each | The inline abstract-only bug returning |
| Each paper returned a full 30-key result | Value fields coming back null — before enums were added to the response schema, one test paper returned null for **15 of 15** value fields while burying the answers in the prose |
| No `dw_structured_review_parse_failed` in the log | The pass-2 parser regressing. It previously called pass 1's parser, which requires a `relevance_score` that pass-2 responses never contain, so **every** review silently returned nothing |
| `Structured profile` appears in the digest | Inline-vs-separate output routing. Inline folds answers into the digest; separate writes its own `detailed_review_*` files |

### Isolation

It writes nothing into the repo. It builds a temporary config, a temporary
seen-papers file and a temporary output directory, then deletes them. Your real
`pipeline_data/seen_papers.json` is never opened for writing.

Pass `--keep` to retain the output for inspection, and `--delay <seconds>` to
change how long the mock batch pretends to be queued (default 15).

### Reading the output

One log line is expected and is **not** a failure:

```
[warn] Parsed results cover 2/N queued papers; N-2 had parse failures
```

The fixture contains responses for two papers, but the arXiv search may return
more. The pipeline correctly declines to mark the unanswered papers as seen, so
they stay eligible for a future run. That is the behaviour you want.

### Keeping the fixture current

`fixtures/batch_output_2papers.jsonl` is a real Doubleword batch output, one JSON
object per line, keyed by `custom_id` (the arXiv paper ID). To refresh it, run a
real batch and save the output file:

```python
batch = client.batches.retrieve("<batch-id>")
open("tests/fixtures/batch_output_2papers.jsonl", "w").write(
    client.files.content(batch.output_file_id).text
)
```

If you change the fixture's papers, update `FIXTURE_PAPER_IDS` in the test to
match.
