"""
Gmail API tools: search (with built-in LLM re-ranking), fetch full email, and send reply.

Authentication uses OAuth 2.0. On first run, a browser window opens for
the user to grant access. The resulting token is saved to token.json
for subsequent runs (no browser prompt needed after that).
"""

import os
import base64
import logging
import re
import threading
import time
import unicodedata
import mimetypes
from pathlib import Path

# Always resolve token/credentials relative to this file's project root,
# regardless of the working directory Streamlit is launched from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from agent.tools.retry import with_retries

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

MAX_BODY_CHARS = 2000
_gmail_service = None
_gmail_creds = None
_thread_local = threading.local()
_thread_attachments: dict[str, list[dict]] = {}
_rank_llm = None
_DEBUG_TIMING = os.getenv("GMAIL_DEBUG_TIMING", "0").lower() in {"1", "true", "yes", "on"}
_ENABLE_OLLAMA_RERANK = os.getenv("ENABLE_OLLAMA_RERANK", "0").lower() in {"1", "true", "yes", "on"}
_MAX_QUERY_ATTEMPTS = max(1, int(os.getenv("GMAIL_SEARCH_QUERY_ATTEMPTS", "3")))
_CUSTOM_RANKING_FALLBACK = os.getenv("CUSTOM_RANKING_FALLBACK", "1").lower() in {"1", "true", "yes", "on"}
_MAX_RETURN_RESULTS = min(10, max(3, int(os.getenv("GMAIL_RETURN_RESULTS", "3"))))
_AUTO_SELECT_MIN_SCORE = float(os.getenv("AUTO_SELECT_MIN_SCORE", "5.0"))
_AUTO_SELECT_RATIO = float(os.getenv("AUTO_SELECT_RATIO", "1.8"))

_STOPWORDS = {
    "the", "a", "an", "to", "for", "of", "on", "about", "please", "help", "me", "reply", "respond", "email", "mail",
    "and", "or", "in", "with", "that", "this", "it", "is", "are", "my", "your", "find", "looking",
    # Hebrew stopwords — common words and intent verbs that are noise in search queries
    "תעזור", "לי", "לענות", "למייל", "מייל", "על", "את", "של", "עם", "מה", "אפשר", "בבקשה", "רוצה", "אני",
    "שלח", "שלחה", "שלחו", "לאחרונה", "האחרון", "האחרונה", "קיבלתי", "קיבל", "ממני", "אליי",
    "היה", "יש", "האם", "כן", "לא", "כבר", "גם", "רק", "הייתי", "הייתה",
}

_HIGH_INTENT_PHRASES = ["shopping list", "grocery list", "grosery", "רשימת קניות", "קניות"]


def _is_meaningful_token(token: str) -> bool:
    """Filter out punctuation-like or very short tokens from multilingual text."""
    if not token:
        return False
    if not all(ch.isalnum() for ch in token):
        return False
    has_hebrew = any("\u0590" <= ch <= "\u05FF" for ch in token)
    min_len = 3 if has_hebrew else 2
    return len(token) >= min_len and any(unicodedata.category(ch).startswith("L") for ch in token)


def _normalize_search_query(query: str, user_query: str) -> str:
    """Prefer user_query when model-provided query is too weak/noisy."""
    base = (query or "").strip()
    fallback = (user_query or "").strip()
    base_tokens = _extract_keywords(base)
    fallback_tokens = _extract_keywords(fallback)

    # If tool argument is too weak (often from small-model tool-calling), use user text.
    if len(base_tokens) <= 1 and len(fallback_tokens) >= 2:
        return fallback
    return base or fallback


def _expand_query_with_heuristics(query: str, user_query: str) -> str:
    """Add lightweight bilingual query hints for common intents like shopping lists."""
    source = f"{query} {user_query}".lower()
    expansions = []

    shopping_markers = ["grocery", "grosery", "shopping list", "shopping", "groceries", "רשימת קניות", "קניות"]
    if any(marker in source for marker in shopping_markers):
        expansions.extend([
            'subject:("shopping list" OR "grocery list" OR "grosery" OR "רשימת קניות")',
            '"shopping list" OR "grocery" OR "רשימת קניות"',
        ])

    if not expansions:
        return query

    return f"({query}) OR ({' OR '.join(expansions)})"


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful English/Hebrew keywords from free-form user text."""
    if not text:
        return []
    tokens = re.findall(r"[A-Za-z0-9\u0590-\u05FF]+", text.lower())
    filtered = [t for t in tokens if _is_meaningful_token(t) and t not in _STOPWORDS]
    seen = set()
    keywords = []
    for token in filtered:
        if token not in seen:
            seen.add(token)
            keywords.append(token)
    return keywords


def _get_rank_llm():
    """Return the lightweight OpenAI model used for query building and ranking."""
    global _rank_llm
    if _rank_llm is None:
        from langchain_openai import ChatOpenAI
        rank_model = os.getenv("RANK_LLM_MODEL", os.getenv("LLM_MODEL", "gpt-4o-mini"))
        api_key = os.getenv("OPENAI_API_KEY") or None
        base_url = os.getenv("OPENAI_BASE_URL") or None
        _rank_llm = ChatOpenAI(model=rank_model, temperature=0, max_tokens=200,
                               base_url=base_url, api_key=api_key)
    return _rank_llm


_GMAIL_QUERY_BUILDER_SYSTEM = """You are a Gmail search query builder. Given a user's request in any language, write the optimal Gmail search query.

Gmail query operators you can use:
- from:<name or email>     — sender (use English/Latin names, transliterate if needed)
- to:<name or email>       — recipient
- subject:<words>          — subject line contains
- newer_than:<N>d/m/y      — emails newer than N days/months/years
- older_than:<N>d/m/y      — emails older than N days/months/years
- after:YYYY/MM/DD         — after specific date
- before:YYYY/MM/DD        — before specific date
- has:attachment            — has attachments
- filename:<name or ext>   — attachment filename
- is:unread                — unread only
- is:starred               — starred only
- is:important             — marked important
- label:<name>             — specific label
- in:inbox / in:sent / in:trash / in:anywhere — search scope
- cc:<name or email>       — CC recipient
- bcc:<name or email>      — BCC recipient
- category:<name>          — category (primary, social, promotions, updates, forums)
- Plain words              — full-text search in body/subject/snippet

Rules:
- ALWAYS transliterate non-Latin names to English/Latin characters. Examples: עידו→ido, נועה→noa, מירית→mirit, יוסי→yosi, דני→dani, שרה→sara, דוד→david, משה→moshe, רחל→rachel
- "recently"/"לאחרונה" → newer_than:7d
- "today"/"היום" → newer_than:1d
- "yesterday"/"אתמול" → newer_than:2d
- "this week"/"השבוע" → newer_than:7d
- "this month"/"החודש" → newer_than:30d
- Translate topic/subject keywords to English when possible
- Combine multiple operators as needed
- Output ONLY the Gmail query string, nothing else. No explanation, no quotes."""


def _llm_build_gmail_query(user_text: str) -> str | None:
    """Use LLM to convert natural language to an optimal Gmail query string.
    Returns None on failure so callers can fall back to keyword search."""
    if not user_text or not user_text.strip():
        return None
    try:
        from langchain_core.messages import SystemMessage, HumanMessage as HMsg
        llm = _get_rank_llm()
        resp = with_retries(lambda: llm.invoke([
            SystemMessage(content=_GMAIL_QUERY_BUILDER_SYSTEM),
            HMsg(content=user_text.strip()),
        ]))
        query = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
        # Strip wrapping quotes if the model added them
        if len(query) >= 2 and query[0] in ('"', "'", "`") and query[-1] == query[0]:
            query = query[1:-1].strip()
        return query if query else None
    except Exception:
        return None


def _build_query_candidates(query: str, user_query: str) -> list[str]:
    """Build multiple Gmail query candidates from natural language + keyword intent."""
    base_query = (query or "").strip()
    combined = f"{query} {user_query}".strip()
    keywords = _extract_keywords(combined)

    operator_terms = re.findall(r"(?:from|subject|newer_than|older_than):\S+", base_query)
    plain_query = re.sub(r"(?:from|subject|newer_than|older_than):\S+", " ", base_query)
    plain_query = " ".join(plain_query.split())

    candidates = []

    # LLM-generated query gets highest priority — it understands intent, transliterates names, adds operators
    llm_query = _llm_build_gmail_query(user_query or query)
    if llm_query:
        candidates.append(llm_query)

    if base_query:
        candidates.append(base_query)

    # Try operator-aware variants first, then progressively relax constraints.
    if plain_query and operator_terms:
        candidates.append(f"{plain_query} {' '.join(operator_terms[:2])}".strip())
        for term in operator_terms[:3]:
            candidates.append(f"{plain_query} {term}".strip())
        candidates.append(plain_query)

    if keywords:
        candidates.append(" ".join(keywords[:4]))
        candidates.append("(" + " OR ".join(keywords[:6]) + ")")

    expanded = _expand_query_with_heuristics(base_query or combined, combined)
    if expanded:
        candidates.append(expanded)

    # For high-intent phrases, prioritize subject-centric fallback.
    lower_combined = combined.lower()
    if any(phrase in lower_combined for phrase in _HIGH_INTENT_PHRASES):
        candidates.append('subject:("רשימת קניות" OR "shopping list" OR "grocery list" OR "grosery")')

    # De-duplicate while preserving order.
    seen = set()
    unique = []
    for item in candidates:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique[:_MAX_QUERY_ATTEMPTS]


def _structured_constraint_terms(search_constraints: str) -> list[str]:
    """Extract Gmail-query-friendly terms from clarification constraints."""
    if not search_constraints:
        return []

    parts = [p.strip() for p in search_constraints.split("|") if p.strip()]
    terms = []
    for part in parts:
        low = part.lower()
        if low.startswith("from:") or low.startswith("newer_than:") or low.startswith("older_than:") or low.startswith("subject:"):
            terms.append(part)
            continue

        # Convert natural hints to query modifiers when possible.
        if low in {"today", "היום"}:
            terms.append("newer_than:2d")
            continue
        if low in {"yesterday", "אתמול"}:
            terms.append("newer_than:3d")
            continue

        terms.append(part)

    # Preserve order, remove duplicates.
    seen = set()
    unique = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            unique.append(term)
    return unique


def _rank_results_lexical(query: str, user_query: str, results: list[dict]) -> list[dict]:
    """Fast lexical relevance ranking with subject-first weighting and recency tie-break."""
    if len(results) <= 1:
        return results

    intent_text = f"{query} {user_query}".lower()
    intent_tokens = _extract_keywords(intent_text)
    phrase_hits = [p for p in _HIGH_INTENT_PHRASES if p in intent_text]

    scored = []
    total = len(results)
    for idx, item in enumerate(results):
        subject = (item.get("subject") or "").lower()
        snippet = (item.get("snippet") or "").lower()
        sender = (item.get("from_") or "").lower()

        score = 0.0
        for phrase in phrase_hits:
            if phrase in subject:
                score += 8.0
            elif phrase in snippet:
                score += 4.0

        for token in intent_tokens:
            if token in subject:
                score += 2.5
            elif token in snippet:
                score += 1.0
            elif token in sender:
                score += 0.5

        # Preserve recency as a tie-breaker using Gmail list order.
        score += (total - idx) * 0.2

        scored.append((score, idx, item))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [entry[2] for entry in scored]


def _candidate_relevance_score(query: str, user_query: str, item: dict) -> float:
    """Compute deterministic relevance score for a single candidate."""
    intent_text = f"{query} {user_query}".lower()
    intent_tokens = _extract_keywords(intent_text)
    phrase_hits = [p for p in _HIGH_INTENT_PHRASES if p in intent_text]

    subject = (item.get("subject") or "").lower()
    snippet = (item.get("snippet") or "").lower()
    sender = (item.get("from_") or "").lower()

    score = 0.0
    for phrase in phrase_hits:
        if phrase in subject:
            score += 8.0
        elif phrase in snippet:
            score += 4.0

    for token in intent_tokens:
        if token in subject:
            score += 2.5
        elif token in snippet:
            score += 1.0
        elif token in sender:
            score += 0.5

    return score


def _candidate_reason_breakdown(query: str, user_query: str, item: dict) -> dict:
    """Return score components and short reasons for one candidate."""
    intent_text = f"{query} {user_query}".lower()
    intent_tokens = _extract_keywords(intent_text)
    phrase_hits = [p for p in _HIGH_INTENT_PHRASES if p in intent_text]

    subject = (item.get("subject") or "").lower()
    snippet = (item.get("snippet") or "").lower()
    sender = (item.get("from_") or "").lower()

    phrase_score = 0.0
    token_score = 0.0
    sender_score = 0.0
    matched = []

    for phrase in phrase_hits:
        if phrase in subject:
            phrase_score += 8.0
            matched.append(f"subject phrase '{phrase}'")
        elif phrase in snippet:
            phrase_score += 4.0
            matched.append(f"snippet phrase '{phrase}'")

    for token in intent_tokens:
        if token in subject:
            token_score += 2.5
            matched.append(f"subject token '{token}'")
        elif token in snippet:
            token_score += 1.0
            matched.append(f"snippet token '{token}'")
        elif token in sender:
            sender_score += 0.5
            matched.append(f"sender token '{token}'")

    total = phrase_score + token_score + sender_score
    return {
        "score": round(total, 3),
        "phrase_score": round(phrase_score, 3),
        "token_score": round(token_score, 3),
        "sender_score": round(sender_score, 3),
        "matched_signals": matched[:6],
    }


def _auto_select_candidate(query: str, user_query: str, ranked: list[dict]) -> dict | None:
    """Return best candidate when confidence is clearly above alternatives."""
    if len(ranked) <= 1:
        return ranked[0] if ranked else None

    top = ranked[0]
    second = ranked[1]
    top_score = _candidate_relevance_score(query, user_query, top)
    second_score = _candidate_relevance_score(query, user_query, second)
    ratio = top_score / max(0.1, second_score)

    if top_score >= _AUTO_SELECT_MIN_SCORE and ratio >= _AUTO_SELECT_RATIO:
        return top
    return None


def _ranking_confidence(query: str, user_query: str, ranked: list[dict]) -> tuple[float, str]:
    """Return confidence ratio and explanation for current ranked list."""
    if not ranked:
        return 0.0, "no_candidates"
    if len(ranked) == 1:
        return 10.0, "single_candidate"

    top_score = _candidate_relevance_score(query, user_query, ranked[0])
    second_score = _candidate_relevance_score(query, user_query, ranked[1])
    ratio = top_score / max(0.1, second_score)
    if top_score >= _AUTO_SELECT_MIN_SCORE and ratio >= _AUTO_SELECT_RATIO:
        return ratio, "clear_top_candidate"
    if top_score <= 0.5:
        return ratio, "weak_signal"
    return ratio, "ambiguous_top_candidates"


def _should_use_custom_ranking(query: str, user_query: str, results: list[dict]) -> bool:
    """Use custom ranking only when built-in order likely misses intent relevance."""
    if not _CUSTOM_RANKING_FALLBACK or len(results) <= 1:
        return False

    # If the query already filters by sender (from:), Gmail returns results newest-first.
    # Custom ranking would penalize emails whose snippet/subject doesn't repeat the sender name,
    # so trust Gmail's date order in this case.
    if re.search(r'\bfrom:', query, re.IGNORECASE):
        return False

    intent_text = f"{query} {user_query}".lower()
    tokens = _extract_keywords(intent_text)
    top_results = results[:3]
    haystack = " ".join(
        f"{(r.get('subject') or '').lower()} {(r.get('snippet') or '').lower()}"
        for r in top_results
    )

    # If the user query has strong intent phrases but top results miss them, use fallback ranking.
    for phrase in _HIGH_INTENT_PHRASES:
        if phrase in intent_text and phrase not in haystack:
            return True

    # If almost none of the user intent tokens appear in top results, built-in order is likely weak.
    if tokens:
        overlap = sum(1 for t in tokens[:6] if t in haystack)
        if overlap <= 1:
            return True

    return False


def _log_timing(stage: str, elapsed_seconds: float) -> None:
    """Emit timing logs only when explicitly enabled."""
    if _DEBUG_TIMING:
        print(f"[gmail-timing] {stage}: {elapsed_seconds:.3f}s", flush=True)


def _safe_error(message: str, exc: Exception | None = None) -> str:
    """Return plain-English errors; include exception detail only in debug mode."""
    if _DEBUG_TIMING and exc is not None:
        return f"{message} Details: {exc}"
    return message


def set_thread_attachments(thread_id: str, attachments: list[dict]) -> None:
    """Store uploaded attachments for a thread until the next draft/send action."""
    if not thread_id:
        return
    if attachments:
        _thread_attachments[thread_id] = attachments
    else:
        _thread_attachments.pop(thread_id, None)


def clear_thread_attachments(thread_id: str) -> None:
    """Clear pending attachments for a thread."""
    if not thread_id:
        return
    _thread_attachments.pop(thread_id, None)


def _get_thread_attachments(thread_id: str) -> list[dict]:
    """Return pending attachments for a thread."""
    if not thread_id:
        return []
    return _thread_attachments.get(thread_id, [])


def _attach_files_to_message(message: MIMEMultipart, attachments: list[dict]) -> int:
    """Attach uploaded files (base64 payloads) to MIME message."""
    attached_count = 0
    for item in attachments:
        try:
            filename = (item.get("filename") or "attachment").strip() or "attachment"
            mime_type = (item.get("mime_type") or "").strip()
            content_b64 = (item.get("content_base64") or "").strip()
            if not content_b64:
                continue

            file_bytes = base64.b64decode(content_b64, validate=True)
            guessed = mimetypes.guess_type(filename)[0]
            final_mime = mime_type or guessed or "application/octet-stream"
            maintype, subtype = (final_mime.split("/", 1) + ["octet-stream"])[:2]

            part = MIMEBase(maintype, subtype)
            part.set_payload(file_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            message.attach(part)
            attached_count += 1
        except Exception:
            # Skip malformed attachment payloads without failing the whole draft/send.
            continue

    return attached_count


def ensure_gmail_authenticated() -> bool:
    """Trigger Gmail OAuth flow if not already authenticated. Returns True on success."""
    try:
        _get_gmail_service()
        return True
    except Exception:
        return False


def check_gmail_token() -> bool:
    """
    Silently check if a valid Gmail token exists with the required scopes.
    Never opens a browser — safe to call during Streamlit page render.
    Returns True only if token is present, valid, and has all required scopes.
    """
    token_path = str(_PROJECT_ROOT / "token.json")
    if not os.path.exists(token_path):
        return False
    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds or not creds.valid:
            return False
        if creds.scopes and not all(s in creds.scopes for s in SCOPES):
            return False
        return True
    except Exception:
        return False


# ── Background OAuth state ────────────────────────────────────────────────────
_oauth_state: dict = {"status": "idle", "error": None}  # idle | running | done | error
_oauth_lock = threading.Lock()


def start_gmail_oauth_background() -> bool:
    """
    Launch the original run_local_server OAuth flow in a background thread
    so the API endpoint returns immediately while the browser window opens.
    Returns False if already in progress or credentials file is missing.
    """
    global _oauth_state, _gmail_service, _gmail_creds

    raw_creds = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    credentials_path = str(_PROJECT_ROOT / raw_creds) if not os.path.isabs(raw_creds) else raw_creds
    if not os.path.exists(credentials_path):
        print(f"[ERROR] Gmail credentials file not found at '{credentials_path}'")
        return False

    with _oauth_lock:
        if _oauth_state["status"] == "running":
            return True  # already in progress — no-op
        _oauth_state = {"status": "running", "error": None}

    # Clear old token so scopes are re-requested cleanly
    token_path = str(_PROJECT_ROOT / "token.json")
    if os.path.exists(token_path):
        os.remove(token_path)
    _gmail_service = None
    _gmail_creds = None

    def _run():
        global _oauth_state
        try:
            _get_gmail_service()   # calls run_local_server — opens browser, blocks until done
            with _oauth_lock:
                _oauth_state = {"status": "done", "error": None}
            print("[OK] Gmail OAuth completed successfully.")
        except Exception as e:
            with _oauth_lock:
                _oauth_state = {"status": "error", "error": str(e)}
            print(f"[ERROR] Gmail OAuth failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return True


def get_oauth_status() -> dict:
    """Return current OAuth flow state: idle | running | done | error."""
    with _oauth_lock:
        return dict(_oauth_state)


def reset_oauth_state() -> None:
    """Reset OAuth state back to idle (e.g. after an error)."""
    global _oauth_state
    with _oauth_lock:
        _oauth_state = {"status": "idle", "error": None}


def _get_gmail_service():
    """Authenticate and return an authorized Gmail API service client."""
    global _gmail_service, _gmail_creds
    if _gmail_service is not None:
        return _gmail_service
    creds = None
    token_path = str(_PROJECT_ROOT / "token.json")
    raw_creds = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    credentials_path = str(_PROJECT_ROOT / raw_creds) if not os.path.isabs(raw_creds) else raw_creds

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        # Force re-auth if saved token is missing required scopes
        if creds and creds.scopes and not all(s in creds.scopes for s in SCOPES):
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"Gmail credentials file not found at '{credentials_path}'.\n"
                    "Please follow the setup instructions in README.md."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())

    _gmail_creds = creds
    _gmail_service = build("gmail", "v1", credentials=creds)
    return _gmail_service


def _get_thread_gmail_service():
    """Return a per-thread Gmail service to avoid shared-client thread-safety issues."""
    if getattr(_thread_local, "gmail_service", None) is None:
        _get_gmail_service()  # Ensure creds are initialized and token is valid.
        _thread_local.gmail_service = build("gmail", "v1", credentials=_gmail_creds)
    return _thread_local.gmail_service


def _strip_html(html: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"\s+", " ", text).strip()


def _decode_body(payload: dict) -> str:
    """Recursively extract and decode plain-text body from Gmail payload."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
    if mime_type == "text/html" and body_data:
        raw = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        return _strip_html(raw)
    for part in payload.get("parts", []):
        result = _decode_body(part)
        if result:
            return result
    return ""


def _fetch_metadata(msg_id: str, thread_id: str) -> dict:
    service = _get_thread_gmail_service()
    msg_data = with_retries(
        lambda: service.users().messages().get(
            userId="me",
            id=msg_id,
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
    )
    raw_headers = msg_data.get("payload", {}).get("headers", [])
    headers = {h.get("name", "").lower(): h.get("value", "") for h in raw_headers}
    return {
        "id": msg_id,
        "threadId": thread_id,
        "from_": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": msg_data.get("snippet", ""),
    }


def _rank_results_with_llm(user_query: str, results: list[dict]) -> list[dict]:
    """
    Re-rank search results by relevance to user_query using a lightweight LLM.
    Falls back to date order if LLM fails or returns unusable output.
    Returns top 3 ranked results.
    """
    if len(results) <= 1:
        return results[:_MAX_RETURN_RESULTS]

    if os.getenv("LLM_PROVIDER", "openai").lower() == "ollama" and not _ENABLE_OLLAMA_RERANK:
        return results[:_MAX_RETURN_RESULTS]

    try:
        t0 = time.perf_counter()
        llm = _get_rank_llm()

        emails_text = "\n".join(
            f"[{i+1}] From: {e.get('from_', '?')} | "
            f"Subject: {e.get('subject', '?')} | "
            f"Snippet: {e.get('snippet', '')[:80]}"
            for i, e in enumerate(results)
        )

        prompt = (
            f'User is looking for: "{user_query}"\n\n'
            f"Results:\n{emails_text}\n\n"
            f"Return ONLY the numbers of the top {_MAX_RETURN_RESULTS} most relevant, comma-separated (e.g. 2,1,3). "
            "Numbers only."
        )

        response = with_retries(lambda: llm.invoke(prompt))
        indices = []
        for part in response.content.strip().split(","):
            p = part.strip()
            if p.isdigit():
                idx = int(p) - 1
                if 0 <= idx < len(results) and idx not in indices:
                    indices.append(idx)

        ranked = [results[i] for i in indices[:_MAX_RETURN_RESULTS]]
        _log_timing("rerank_llm", time.perf_counter() - t0)
        return ranked[:_MAX_RETURN_RESULTS] if ranked else results[:_MAX_RETURN_RESULTS]

    except Exception:
        return results[:_MAX_RETURN_RESULTS]  # Fallback to date order


def _search_gmail_raw(service, query: str) -> list[dict]:
    """Run a single Gmail search query and return raw message refs."""
    response = with_retries(
        lambda q=query: service.users().messages().list(
            userId="me", q=q, maxResults=6
        ).execute()
    )
    return response.get("messages", [])


def _collect_candidate_messages(service, candidates: list[str], default_query: str) -> tuple[list[dict], str]:
    """Run multiple candidate queries and merge unique message refs in discovery order.
    If the first candidate returns results, those are used exclusively — fallback candidates
    are only tried when the primary query returns nothing, preventing result contamination."""
    messages_by_id = {}
    used_query = default_query
    first_hit_query = ""

    for i, candidate in enumerate(candidates):
        used_query = candidate
        t_list = time.perf_counter()
        messages = _search_gmail_raw(service, candidate)
        _log_timing(f"messages_list[{candidate}]", time.perf_counter() - t_list)

        for message in messages:
            msg_id = message.get("id")
            if msg_id and msg_id not in messages_by_id:
                messages_by_id[msg_id] = message

        if messages and not first_hit_query:
            first_hit_query = candidate

        # If this is the first (highest-priority / LLM-generated) candidate and it found
        # results, stop here — do not run fallback candidates that could pollute results
        # with unrelated emails.
        if i == 0 and messages_by_id:
            break

        if len(messages_by_id) >= 6:
            break

    return list(messages_by_id.values()), first_hit_query or used_query


def _fetch_metadata_batch(messages: list[dict]) -> list[dict]:
    """Fetch metadata for message refs in parallel while preserving input order."""
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_by_index = {
            index: executor.submit(_fetch_metadata, m["id"], m["threadId"])
            for index, m in enumerate(messages)
        }
        return [future_by_index[index].result() for index in range(len(messages))]


def _rank_candidates(query: str, user_query: str, results: list[dict]) -> tuple[list[dict], str]:
    """Apply built-in or fallback custom ranking strategy and return ranked results + mode."""
    ranking_mode = "builtin"
    ranked = results

    if _should_use_custom_ranking(query=query, user_query=user_query, results=results):
        ranking_mode = "fallback_custom"
        lexical_ranked = _rank_results_lexical(query=query, user_query=user_query, results=results)
        ranked = _rank_results_with_llm(user_query, lexical_ranked) if len(lexical_ranked) > 1 else lexical_ranked

    return ranked, ranking_mode


@tool
def search_gmail(query: str = "", user_query: str = "", q: str = "", search_constraints: str = "") -> dict:
    """
    Search Gmail for messages matching query. Accepts either `query` or `q`.
    Optional `search_constraints` can include sender/date/subject hints from a clarification step.
    Returns up to a configurable number of relevant results (default 7),
    ranked by relevance to user_query (falls back to newest-first if not provided).
    Each result includes: id, threadId, from_, subject, date, snippet.
    """
    try:
        structured_terms = _structured_constraint_terms(search_constraints or "")
        effective_query = " ".join(part for part in [query or q or "", *structured_terms] if part).strip()
        effective_user_query = " ".join(part for part in [(user_query or ""), (search_constraints or "")] if part).strip()

        # Auto-inject from: constraint if user_query mentions an email address
        # and the LLM forgot to add it to the query.
        if effective_user_query and "from:" not in effective_query:
            email_in_user_query = re.search(
                r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
                effective_user_query,
            )
            if email_in_user_query:
                addr = email_in_user_query.group(0)
                effective_query = f"from:{addr} {effective_query}".strip()

        normalized_query = _normalize_search_query(query=effective_query, user_query=effective_user_query)
        if not normalized_query:
            return {
                "success": False,
                "error": "Missing search query. Please provide `query` (or `q`) with email keywords.",
                "results": [],
            }

        t_total = time.perf_counter()
        service = _get_gmail_service()
        candidates = _build_query_candidates(normalized_query, effective_user_query)
        messages, used_query = _collect_candidate_messages(service, candidates, normalized_query)

        if not messages:
            _log_timing("search_total", time.perf_counter() - t_total)
            return {"success": True, "results": [], "count": 0}

        t_meta = time.perf_counter()
        results = _fetch_metadata_batch(messages)
        _log_timing("messages_get_metadata_parallel", time.perf_counter() - t_meta)

        ranked, ranking_mode = _rank_candidates(
            query=used_query,
            user_query=effective_user_query or normalized_query,
            results=results,
        )

        ranked_limited = ranked[:_MAX_RETURN_RESULTS]
        auto_selected = _auto_select_candidate(used_query, effective_user_query or normalized_query, ranked_limited)
        confidence_score, confidence_reason = _ranking_confidence(
            used_query,
            effective_user_query or normalized_query,
            ranked_limited,
        )
        candidate_reasons = [
            _candidate_reason_breakdown(used_query, effective_user_query or normalized_query, item)
            for item in ranked_limited[:5]
        ]

        _log_timing("search_total", time.perf_counter() - t_total)

        return {
            "success": True,
            "results": ranked_limited,
            "count": len(ranked_limited),
            "total_found": len(messages),
            "query_used": used_query,
            "query_candidates": candidates,
            "ranking_mode": ranking_mode,
            "auto_selected": auto_selected,
            "ranking_confidence": confidence_score,
            "ranking_reason": confidence_reason,
            "candidate_reasons": candidate_reasons,
        }

    except FileNotFoundError as e:
        return {"success": False, "error": str(e), "results": []}
    except HttpError as e:
        return {"success": False, "error": _safe_error("Gmail API error while searching emails.", e), "results": []}
    except Exception as e:
        return {"success": False, "error": _safe_error("Unexpected error during email search.", e), "results": []}


@tool
def get_email_content(message_id: str) -> dict:
    """
    Fetch the full content of a Gmail message by its ID.
    Returns: id, threadId, from_, subject, date, body (HTML-stripped, max 2000 chars).
    """
    try:
        t_total = time.perf_counter()
        service = _get_gmail_service()
        msg_data = with_retries(
            lambda: service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
        )

        payload = msg_data.get("payload", {})
        raw_headers = payload.get("headers", [])
        headers = {h.get("name", "").lower(): h.get("value", "") for h in raw_headers}
        body = _decode_body(payload)

        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "\n... [email truncated]"

        _log_timing("get_email_content_total", time.perf_counter() - t_total)

        from_addr = headers.get("from", "")
        # Triage: detect automated/no-reply senders and newsletter headers
        sender_lower = from_addr.lower()
        is_automated = any(kw in sender_lower for kw in [
            "noreply", "no-reply", "donotreply", "do-not-reply",
            "notifications@", "mailer-daemon", "bounce", "jobs-noreply"
        ])
        has_list_unsub = any(
            h.get("name", "").lower() == "list-unsubscribe"
            for h in raw_headers
        )

        return {
            "success": True,
            "id": message_id,
            "threadId": msg_data.get("threadId", ""),
            "from_": from_addr,
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "body": body,
            "is_automated": is_automated,
            "is_newsletter": has_list_unsub,
        }

    except HttpError as e:
        return {"success": False, "error": _safe_error("Gmail API error while fetching email content.", e)}
    except Exception as e:
        return {"success": False, "error": _safe_error("Unexpected error while fetching email content.", e)}


def _extract_email_address(header_value: str) -> str:
    """Extract bare email address from a header like 'Name <addr@example.com>'."""
    match = re.search(r"<([^>]+)>", header_value)
    if match:
        return match.group(1).strip().lower()
    return header_value.strip().lower()


@tool
def create_gmail_draft(thread_id: str, to: str, subject: str, body: str, config: RunnableConfig) -> dict:
    """
    Send or save a reply depending on configuration.
    - If DRY_RUN=false AND recipient is in ALLOWED_SEND_ADDRESSES: sends the email.
    - Otherwise: saves as a Gmail Draft for manual review.
    Args: thread_id (from original email), to (recipient email), subject, body (plain text).
    """
    subject_line = subject if subject.startswith("Re:") else f"Re: {subject}"
    dry_run = os.getenv("DRY_RUN", "true").lower() not in {"false", "0", "no", "off"}
    allowed_raw = os.getenv("ALLOWED_SEND_ADDRESSES", "")
    allowed = {addr.strip().lower() for addr in allowed_raw.split(",") if addr.strip()}

    recipient_addr = _extract_email_address(to)
    can_send = (not dry_run) and bool(allowed) and (recipient_addr in allowed)

    # Safety gate: never send to disallowed addresses
    if not dry_run and allowed and recipient_addr not in allowed:
        return {
            "success": False,
            "error": (
                f"Sending blocked: '{recipient_addr}' is not in the allowed send list. "
                "The reply has NOT been saved or sent. Please verify the recipient."
            ),
        }

    try:
        t_total = time.perf_counter()
        service = _get_gmail_service()
        chat_thread_id = config.get("configurable", {}).get("thread_id", "")
        pending_attachments = _get_thread_attachments(chat_thread_id)
        logging.warning(
            f"[create_gmail_draft] chat_thread_id={chat_thread_id!r}  "
            f"pending_attachments={len(pending_attachments)} items  "
            f"all_thread_ids={list(_thread_attachments.keys())}"
        )

        message = MIMEMultipart()
        message["To"] = to
        message["Subject"] = subject_line
        message.attach(MIMEText(body, "plain"))
        attachment_count = _attach_files_to_message(message, pending_attachments)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        if can_send:
            result = with_retries(
                lambda: service.users().messages().send(
                    userId="me",
                    body={"raw": raw, "threadId": thread_id}
                ).execute()
            )
            msg_id = result.get("id", "")
            _log_timing("send_email_total", time.perf_counter() - t_total)
            return {
                "success": True,
                "message_id": msg_id,
                "message": f"✅ Email sent to {to}{f' with {attachment_count} attachment(s)' if attachment_count else ''}!",
                "action": "sent",
                "attachment_count": attachment_count,
            }
        else:
            draft = with_retries(
                lambda: service.users().drafts().create(
                    userId="me",
                    body={"message": {"raw": raw, "threadId": thread_id}}
                ).execute()
            )
            draft_id = draft.get("id", "")
            _log_timing("create_gmail_draft_total", time.perf_counter() - t_total)
            return {
                "success": True,
                "draft_id": draft_id,
                "message": f"✅ Draft saved to Gmail Drafts{f' with {attachment_count} attachment(s)' if attachment_count else ''}! Open Gmail to review and send.",
                "action": "draft",
                "attachment_count": attachment_count,
            }

    except HttpError as e:
        return {"success": False, "error": _safe_error("Gmail API error while saving/sending.", e)}
    except Exception as e:
        return {"success": False, "error": _safe_error("Unexpected error while saving/sending.", e)}

