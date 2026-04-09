import arxiv
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEEN_PAPERS_FILE = (
    "/app/data/pipeline_data/seen_papers.json"
    if os.path.exists("/app/data")
    else str(PROJECT_ROOT / "pipeline_data" / "seen_papers.json")
)
SEEN_PAPERS_FILE = os.getenv("SEEN_PAPERS_FILE", DEFAULT_SEEN_PAPERS_FILE)

def load_seen_papers():
    """Load papers we've already processed"""
    if os.path.exists(SEEN_PAPERS_FILE):
        try:
            with open(SEEN_PAPERS_FILE, 'r') as f:
                content = f.read().strip()
                if not content:
                    return set()
                return set(json.loads(content))
        except json.JSONDecodeError:
            print("Warning: seen_papers.json is corrupted, starting fresh")
            return set()
    return set()

def save_seen_papers(paper_ids):
    """Save papers we've processed"""
    seen = load_seen_papers()
    seen.update(paper_ids)
    
    # Make sure directory exists (only if there's actually a directory in the path)
    dirname = os.path.dirname(SEEN_PAPERS_FILE)
    if dirname:  # Only create directory if path includes one
        os.makedirs(dirname, exist_ok=True)
    
    with open(SEEN_PAPERS_FILE, 'w') as f:
        json.dump(list(seen), f, indent=2)
    
    print(f"✓ Tracked {len(seen)} total papers")

def filter_unseen_papers(papers):
    """Remove papers we've already sent"""
    unseen, _ = filter_unseen_papers_with_trace(papers)
    return unseen


def filter_unseen_papers_with_trace(papers):
    """Split papers into unseen and already-seen groups."""
    seen = load_seen_papers()
    unseen = []
    dropped_seen = []
    for paper in papers:
        if paper["id"] in seen:
            dropped_seen.append(paper)
        else:
            unseen.append(paper)
    return unseen, dropped_seen

def _normalize_paper(paper):
    return {
        "id": paper.entry_id.split("/")[-1],
        "title": paper.title,
        "authors": [a.name for a in paper.authors],
        "abstract": paper.summary,
        "published": paper.published,
        "url": paper.entry_id,
    }


WORD_PATTERN = re.compile(r"[A-Za-z0-9]+")


def _tokenize_words(text):
    return [token.lower() for token in WORD_PATTERN.findall(text or "")]


def _stem_token(token):
    """Lightweight stemming for practical variant matching (predict/prediction, model/modelling)."""
    value = (token or "").lower()
    if not value:
        return value
    # Normalize common UK/US spelling variants first.
    value = (
        value
        .replace("isation", "ization")
        .replace("ising", "izing")
        .replace("ised", "ized")
    )
    suffix_rules = [
        ("ization", "ize"),
        ("ational", "ate"),
        ("ation", "ate"),
        ("ingly", ""),
        ("edly", ""),
        ("ments", ""),
        ("ment", ""),
        ("ingly", ""),
        ("ing", ""),
        ("ively", ""),
        ("ives", ""),
        ("ive", ""),
        ("ions", ""),
        ("ion", ""),
        ("ers", ""),
        ("ies", "y"),
        ("es", ""),
        ("ed", ""),
        ("s", ""),
    ]
    for suffix, replacement in suffix_rules:
        if value.endswith(suffix) and len(value) - len(suffix) >= 4:
            value = value[: -len(suffix)] + replacement
            break
    if value.endswith("e") and len(value) > 5:
        value = value[:-1]
    return value


def _token_matches(term_token, text_token, match_mode):
    if term_token == text_token:
        return True
    if match_mode == "exact":
        return False
    # Keep very short tokens (for example AI, ML, GLM) exact to avoid noisy matches.
    if len(term_token) <= 3:
        return False

    term_stem = _stem_token(term_token)
    text_stem = _stem_token(text_token)
    if term_stem == text_stem:
        return True

    # Allow close prefix overlap for derived forms (model/modelled, reserve/reserving).
    min_len = min(len(term_stem), len(text_stem))
    if min_len >= 5 and (term_stem.startswith(text_stem) or text_stem.startswith(term_stem)):
        return True
    return False


def _term_matches_tokens(text_tokens, term, match_mode):
    term_tokens = _tokenize_words(term)
    if not term_tokens:
        return False

    if len(term_tokens) == 1:
        target = term_tokens[0]
        return any(_token_matches(target, text_token, match_mode) for text_token in text_tokens)

    window = len(term_tokens)
    if len(text_tokens) < window:
        return False

    for start in range(0, len(text_tokens) - window + 1):
        if all(_token_matches(term_tokens[idx], text_tokens[start + idx], match_mode) for idx in range(window)):
            return True
    return False


def _find_matched_terms(text, terms, match_mode="exact"):
    matched = []
    text_tokens = _tokenize_words(text)
    for term in terms:
        if not term:
            continue
        if _term_matches_tokens(text_tokens, term, match_mode):
            matched.append(term)
    return matched


def _iter_arxiv_results_with_backoff(
    search,
    page_size,
    delay_seconds,
    num_retries,
    max_attempts,
    backoff_seconds,
    log_fn=None,
):
    """Yield arXiv results with explicit cross-attempt backoff for HTTP 429."""
    attempt = 1
    while True:
        client = arxiv.Client(
            page_size=page_size,
            delay_seconds=delay_seconds,
            num_retries=num_retries,
        )
        try:
            if log_fn:
                log_fn(
                    "arXiv fetch attempt %s/%s (page_size=%s, delay_seconds=%s, num_retries=%s)"
                    % (attempt, max_attempts, page_size, delay_seconds, num_retries)
                )
            yield from client.results(search)
            return
        except arxiv.HTTPError as err:
            if err.status == 429 and attempt < max_attempts:
                sleep_seconds = backoff_seconds * attempt
                if log_fn:
                    log_fn(
                        "arXiv HTTP 429 on attempt %s/%s; backing off for %.1f seconds before retry"
                        % (attempt, max_attempts, sleep_seconds)
                    )
                time.sleep(sleep_seconds)
                attempt += 1
                continue
            raise


def get_daily_papers(
    max_results=100,
    cutoff_date=None,
    return_trace=False,
    query_override=None,
    required_text_terms=None,
    required_text_min_matches=1,
    required_anchor_terms=None,
    term_match_mode="exact",
    arxiv_page_size=0,
    arxiv_delay_seconds=4.0,
    arxiv_num_retries=6,
    arxiv_max_attempts=4,
    arxiv_backoff_seconds=20.0,
    log_fn=None,
):
    """Get papers newer than cutoff_date, or use weekday default lookback."""
    if cutoff_date is None:
        # On Mondays, look back 3 days to catch Friday's papers.
        day_of_week = datetime.now().weekday()
        lookback_days = 3 if day_of_week == 0 else 1  # 0 = Monday
        cutoff_date = datetime.now() - timedelta(days=lookback_days)
    effective_page_size = arxiv_page_size if arxiv_page_size > 0 else min(max_results, 100)
    if effective_page_size <= 0:
        effective_page_size = 1
        
    # Build search query
    query = query_override or ""
    required_text_terms = required_text_terms or []
    required_anchor_terms = required_anchor_terms or []
    if term_match_mode not in {"exact", "stem"}:
        raise ValueError(f"Unsupported term_match_mode={term_match_mode!r}. Use 'exact' or 'stem'.")
    if required_text_min_matches < 1:
        required_text_min_matches = 1
    
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending
    )
    
    daily_papers = []
    search_trace = []
    
    for paper in _iter_arxiv_results_with_backoff(
        search=search,
        page_size=effective_page_size,
        delay_seconds=arxiv_delay_seconds,
        num_retries=arxiv_num_retries,
        max_attempts=arxiv_max_attempts,
        backoff_seconds=arxiv_backoff_seconds,
        log_fn=log_fn,
    ):
        normalized = _normalize_paper(paper)
        in_lookback_window = normalized["published"].replace(tzinfo=None) >= cutoff_date
        searchable_text = f"{normalized['title']} {normalized['abstract']}"
        matched_required_terms = _find_matched_terms(searchable_text, required_text_terms, match_mode=term_match_mode)
        matched_anchor_terms = _find_matched_terms(searchable_text, required_anchor_terms, match_mode=term_match_mode)
        has_required_match = (
            True if not required_text_terms else len(matched_required_terms) >= required_text_min_matches
        )
        has_anchor_match = True if not required_anchor_terms else bool(matched_anchor_terms)
        matches_required_terms = has_required_match and has_anchor_match
        search_trace.append({
            "id": normalized["id"],
            "title": normalized["title"],
            "lead_author": normalized["authors"][0] if normalized.get("authors") else None,
            "authors": normalized.get("authors", []),
            "published": normalized["published"],
            "in_lookback_window": in_lookback_window,
            "matches_required_terms": matches_required_terms,
            "matched_required_terms": matched_required_terms,
            "matched_anchor_terms": matched_anchor_terms,
        })
        if in_lookback_window and matches_required_terms:
            daily_papers.append(normalized)

    if return_trace:
        return daily_papers, search_trace

    return daily_papers
