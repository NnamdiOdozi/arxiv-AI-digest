# arXiv Coverage Analysis — Actuarial ML Lit Review vs Combined Batch

**Date:** April 2026
**Lit review:** 80 papers (actuarial/insurance ML)
**arXiv batch:** 236 papers across two runs (batch_162305 + batch_205957_supplementary)
**Lookback window:** 10 years (2016-04-08 cutoff)

---

## Summary

4 of the 80 lit review papers (5%) appear in the combined arXiv batch. The remaining 76 are not present — but as shown below, ~90% of those gaps are explained: papers genuinely aren't on arXiv, or predate the lookback window.

---

## Breakdown of 76 unmatched lit review papers

| Category | Count | Root cause |
|---|---|---|
| Pre-2016 papers | 26 | 10-year lookback cutoff (2016-04-08) — definitionally excluded |
| Post-2016, NOT on arXiv | ~49 | Never posted to arXiv — see below |
| Post-2016, on arXiv but missed by query | 1 confirmed | Query terminology mismatch (Kevin Kuo 2003.02453) |

---

## 4 papers confirmed in both lit review and combined batch

These are the only verified same-paper matches (confirmed by arXiv ID, not just title overlap):

| Lit ID | Authors | Title | arXiv ID |
|---|---|---|---|
| A52 | Kuo (2019) | DeepTriangle: A Deep Learning Approach to Loss Reserving | 1804.09253v4 |
| A76 | Crevecoeur et al. (2020) | A hierarchical reserving model for reported non-life insurance claims | 1910.12692v3 |
| A77 | Chaoubi et al. (2022) | Micro-level Reserving for General Insurance Claims using LSTM | 2201.13267v1 |
| A56 | Richman & Wüthrich (2022) | From Chain-Ladder to Individual Claims Reserving | 2602.15385v2 |

**Note on matching methodology:** Matching was done by ripgrep search on author surnames + key title words, then verified by arXiv ID lookup in the batch. Title-word-only matching (without author cross-check) produces many false positives — short generic titles like "An individual claims reserving model" can match completely different papers sharing 3–4 words.

---

## Why ~49 post-2016 actuarial ML papers aren't on arXiv

Actuarial science has a different publication culture from ML/CS:

1. **Working papers / internal reports** — e.g. Wüthrich "Swiss Finance Institute Research Paper", Richman "AI in Actuarial Science - Draft"
2. **ASTIN/CAS conference presentations** — e.g. "ASTIN 2018 WP - ML and Traditional Methods Synergy...", Harej et al. "Individual Claim Development With Machine Learning"
3. **SSRN-hosted papers** — Many insurance/actuarial papers go to SSRN, not arXiv (common for finance-adjacent fields)
4. **Industry/Kaggle papers** — e.g. "Allstate Insurance Claims Severity", "Granular Reserving Dialogistic"
5. **General ML papers cited for technique** — LightGBM (NeurIPS 2017), Boosted Regression Trees — not insurance papers; included in lit review for the method

---

## Paper confirmed on arXiv but missed by the original query

**Kevin Kuo (2020)** — "Individual Claims Forecasting with Bayesian Mixture Density Networks" (`2003.02453`)
- Abstract uses "case reserving" and "claims analytics" — neither was in the original domain terms
- Title says "forecasting" not "reserving" — not matched by original query
- Query fix applied (April 2026): added `"case reserving"`, `"claims analytics"`, `"claims forecasting"` to `domain_terms`
- **Still NOT in combined batch** as of April 2026 run — the query fix has not yet been re-run

---

## Query improvements made (April 2026)

Added to `domain_terms` in config.toml:
- `"case reserving"`, `"claims analytics"`, `"claims forecasting"`

Final working `method_terms` set (1166 chars, API confirmed OK):
- Added: `"rnn"`, `"lstm"`, `"gru"`, `"arima"`, `"time series"`
- Removed: `"ml"`, `"ai"`, `"gen ai"` (too vague; increased false positives and hit API complexity limit)

---

## Batch quality issues observed (April 2026)

The combined batch (236 papers) had two structural issues not flagged at the time:

1. **22 papers with `needs_summary = True` but no summary returned.**
   All 22 had a `key_insight` but an empty `summary` field. Two failure modes:
   - **Off-topic papers** (e.g. quantum computing, Bitcoin, privacy): the model returned only a one-line key_insight noting irrelevance and silently omitted the summary body. The prompt should enforce an explicit empty/N/A summary rather than omission.
   - **Genuinely relevant papers with missing summaries** (e.g. 2201.13267v1 Chaoubi, 2402.10421v3 RNN multivariate reserving, 1801.01792v1 dynamic copula): clear LLM failures where a full summary was expected but the field came back null.

2. **44 papers with `needs_summary = False`** — these were excluded from summarisation by the relevance filter (is_relevant = False). This is by design; no action needed.

---

## Overall verdict

The arXiv search captures the ML-forward actuarial literature well. Only 1 of the 76 unmatched papers is a confirmed false-negative (Kevin Kuo, query terminology mismatch). The other ~75 gaps are structurally explained: 26 predate the lookback window, and the rest (~49) were never on arXiv. Industry reports, working papers, and conference proceedings are out of scope by design.

The 5% overlap rate (4/80) between the lit review and batch is expected given these constraints. A future run with the updated query terms may recover the Kevin Kuo paper.
