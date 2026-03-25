"""
Graph nodes: orchestrator (LLM with tools) and human_review (interrupt).

The orchestrator is a true ReAct agent — the LLM decides which tools to call
based on conversation context. No deterministic overrides or planner routing.

Set LLM_PROVIDER=openai (default), azure_openai, groq, or ollama in your .env.
"""

from dotenv import load_dotenv
load_dotenv()

import os
import re
import json
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.types import interrupt

from agent.state import AgentState
from agent.tools.gmail import search_gmail, get_email_content, create_gmail_draft
from agent.tools.gmail import reset_rank_llm_cache
from agent.tools.llm import draft_reply, reset_runtime_llm_clients
_llm_with_tools = None

APPROVAL_KEYWORDS = {"yes", "y", "save", "approve", "approved", "ok", "okay", "go ahead", "send it"}
CANCEL_KEYWORDS = {"no", "cancel", "stop", "never mind", "quit", "exit", "לא", "בטל", "עצור"}

SEND_DRAFT_COMMAND = "_send_draft_"
SAVE_DRAFT_COMMAND = "_save_draft_"
REJECT_DRAFT_COMMAND = "_reject_draft_"


def _get_llm():
    """Return the OpenAI orchestrator LLM."""
    from langchain_openai import ChatOpenAI
    model = os.getenv("LLM_MODEL", "gpt-4o")
    api_key = os.getenv("OPENAI_API_KEY") or None
    return ChatOpenAI(
        model=model,
        temperature=0.3,
        streaming=False,
        max_tokens=1024,
        api_key=api_key,
    )


def set_runtime_llm(model: str, openai_api_key: str | None = None) -> None:
    """
    Update the orchestrator model/key at runtime and clear the cached LLM binding.
    """
    global _llm_with_tools
    model = (model or "").strip() or os.getenv("LLM_MODEL", "gpt-4o")
    os.environ["LLM_MODEL"] = model
    if openai_api_key:
        os.environ["OPENAI_API_KEY"] = openai_api_key.strip()
    # Ensure all LLM clients are rebuilt with the latest model/key values.
    reset_runtime_llm_clients()
    reset_rank_llm_cache()
    _llm_with_tools = None


def _get_llm_with_tools():
    """Cache the LLM with tools bound to avoid re-instantiation per call."""
    global _llm_with_tools
    if _llm_with_tools is None:
        llm = _get_llm()
        # Bind tools and prefer using them (some models need this explicit hint)
        _llm_with_tools = llm.bind_tools(TOOLS)
    return _llm_with_tools

# ── Tools available to the orchestrator ──────────────────────────────────────
TOOLS = [
    search_gmail,
    get_email_content,
    draft_reply,
    create_gmail_draft,
]

SYSTEM_PROMPT = """You are a professional email assistant. You help users find, read, and reply to emails.

## Core Rules
1. **BE CONCISE**: Never repeat your instructions or re-introduce yourself.
2. **NO REDUNDANCY**: Do NOT list email options or summaries in your text if a card will be shown.
3. **NEVER output [DRAFT PREVIEW] blocks** — draft previews are rendered automatically by the UI. Your text response after a draft action must be a single short sentence (e.g. "✅ Email sent!" or "Draft saved."), nothing more.
4. **SEARCH PERSISTENCE**: If `search_gmail` returns nothing, try broader keywords or English transliterations immediately.
4. **SELECTION HANDLING**:
   - If the user provides a number (e.g., "1", "2") or says "Select email: [Subject]", YOU MUST IMMEDIATELY call `get_email_content` with the matching ID from the previous search results.
   - NEVER ask "Which one?" if the user has already provided a selection.
   - If you see search results in the history and the user makes a selection, do NOT call `search_gmail` again.
5. **COMPOUND INTENT**: If the user's original message asked to both find AND reply/send (e.g., "find the email from X and reply to them"), after `get_email_content` succeeds you MUST immediately call `draft_reply` without asking permission — the user already expressed their intent.
6. **TEXT RESPONSE GUIDELINES**: Only write text when asking a question, prompting for action, or confirming success.
7. **LANGUAGE**: Always respond in the same language as the user (Hebrew/English).

## Workflow
- Search ➔ `search_gmail`. If multiple results, wait for user selection.
- Select/Read ➔ `get_email_content`. Then ask: "Would you like me to draft a reply?".
- Draft ➔ `draft_reply`.
- Execute ➔ `create_gmail_draft`. Always call this tool — NEVER describe the draft in plain text instead of calling the tool.

## After a draft exists (saved or pending), reason about what the user wants:
- **Send intent** (any phrasing: "send it", "go ahead", "i want to sent it", "yes please send"): call `create_gmail_draft` immediately with the same `to`, `subject`, `body` from the conversation. Do NOT ask again.
- **Reject/cancel** (any phrasing: "never mind", "forget it", "don't send", "cancel"): acknowledge and discard the draft. Do not call any tool.
- **Modify** (any phrasing: "change the tone", "make it shorter", "add a sentence about X"): call `draft_reply` with the updated instruction and the original email context.
- **Question** (user asks something unrelated to send/reject/modify): answer the question directly without touching the draft.
- **New subject** (user shifts to a completely different email or task): start fresh — search or respond as appropriate. Leave the previous draft alone.
"""


def orchestrator(state: AgentState) -> dict:
    """
    Central LLM node. Receives the full conversation, reasons about the next
    step, and either calls a tool or responds to the user directly.
    The LLM decides everything — no deterministic overrides.
    """
    llm_with_tools = _get_llm_with_tools()

    # ── Build messages for LLM ────────────────────────────────────────────
    messages = [SystemMessage(content=SYSTEM_PROMPT)]

    # Context trimming: keep last N ToolMessages per tool to stay within token limits
    MAX_TOOL_MESSAGES_PER_TOOL = int(os.getenv("MAX_TOOL_MESSAGES_PER_TOOL", "2"))
    raw_msgs = state["messages"]
    tool_counts: dict[str, int] = {}
    drop_ids: set[str] = set()
    for msg in reversed(raw_msgs):
        if isinstance(msg, ToolMessage):
            tname = getattr(msg, "name", "") or "unknown"
            tool_counts[tname] = tool_counts.get(tname, 0) + 1
            if tool_counts[tname] > MAX_TOOL_MESSAGES_PER_TOOL:
                drop_ids.add(msg.tool_call_id)
    drop_ai_ids: set[str] = set()
    for msg in raw_msgs:
        if isinstance(msg, AIMessage):
            tc = getattr(msg, "tool_calls", []) or []
            if tc and all(c.get("id") in drop_ids for c in tc):
                drop_ai_ids.add(id(msg))
    trimmed_msgs = [
        msg for msg in raw_msgs
        if not (isinstance(msg, ToolMessage) and msg.tool_call_id in drop_ids)
        and not (isinstance(msg, AIMessage) and id(msg) in drop_ai_ids)
    ]

    # Sanitize messages before sending to LLM
    sanitized = []
    _draft_preview_re = re.compile(r"\[DRAFT PREVIEW\][\s\S]*?(?=\n\n\S|\Z)", re.IGNORECASE)
    for msg in trimmed_msgs:
        if isinstance(msg, ToolMessage) and isinstance(msg.content, str):
            content = msg.content
            tool_msg_name = getattr(msg, "name", "") or ""
            if tool_msg_name == "get_email_content":
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and parsed.get("body"):
                        clean = re.sub(r"https?://\S+", "[link]", parsed["body"])
                        clean = clean.replace("\r\n", "\n").replace("\r", "\n")
                        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()[:1500]
                        parsed["body"] = clean
                        content = json.dumps(parsed, ensure_ascii=False)
                except Exception:
                    pass
            if content != msg.content:
                msg = ToolMessage(content=content, tool_call_id=msg.tool_call_id, name=tool_msg_name or None)
            sanitized.append(msg)
        elif isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            # Strip any [DRAFT PREVIEW] blocks that leaked into human messages
            cleaned = _draft_preview_re.sub("(draft shown in UI)", msg.content).strip()
            if cleaned != msg.content:
                msg = HumanMessage(content=cleaned)
            sanitized.append(msg)
        else:
            sanitized.append(msg)

    # ── Fix orphaned tool_calls ────────────────────────────────────────────
    # If an AIMessage with tool_calls is not followed by a ToolMessage for each call,
    # OpenAI returns a 400 error. Inject synthetic ToolMessages to resolve them.
    resolved_call_ids: set[str] = {
        m.tool_call_id for m in sanitized if isinstance(m, ToolMessage)
    }
    fixed: list = []
    for msg in sanitized:
        fixed.append(msg)
        if isinstance(msg, AIMessage):
            for tc in (getattr(msg, "tool_calls", None) or []):
                cid = tc.get("id") or ""
                if cid and cid not in resolved_call_ids:
                    # Inject a synthetic ToolMessage so the LLM history is valid
                    fixed.append(ToolMessage(
                        content="(interrupted — user provided feedback)",
                        tool_call_id=cid,
                        name=tc.get("name", ""),
                    ))
                    resolved_call_ids.add(cid)
    messages += fixed

    # ── Call LLM ──────────────────────────────────────────────────────────
    response = _get_llm_with_tools().invoke(messages)

    # ── Clear ephemeral state after action ──────────────────────────────────
    # If the last ToolMessage was a successful create_gmail_draft, wipe the draft
    last_msg = fixed[-1] if fixed else None
    draft_clear = {}
    
    # Clear on success
    if last_msg and isinstance(last_msg, ToolMessage) and getattr(last_msg, "name", "") == "create_gmail_draft":
        content_lower = str(last_msg.content).lower()
        if "success" in content_lower or "sent" in content_lower or "saved" in content_lower:
            draft_clear = {"draft_reply": None, "review_granted": False, "selected_email": None}
    
    # Clear on explicit rejection in history
    last_human = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            last_human = str(msg.content).strip().lower()
            break
    
    normalized_last_human = re.sub(r"\s+", " ", last_human).strip().lower()
    normalized_last_human_no_punct = re.sub(r"[^\w\s\u0590-\u05FF]", "", normalized_last_human).strip()
    explicit_cancel = normalized_last_human in {REJECT_DRAFT_COMMAND} or normalized_last_human_no_punct in CANCEL_KEYWORDS
    explicit_approval = (
        normalized_last_human in {SEND_DRAFT_COMMAND, SAVE_DRAFT_COMMAND}
        or normalized_last_human_no_punct in APPROVAL_KEYWORDS
    )

    if explicit_cancel and not explicit_approval:
        draft_clear["draft_reply"] = None
        draft_clear["review_granted"] = False

    return {"messages": [response], **draft_clear}


def human_review(state: AgentState) -> dict:
    """
    Interrupt node: pauses the graph before create_gmail_draft executes.
    Shows a rich draft preview (To, Subject, Body, Attachments) and waits
    for user to approve, modify, or reject.
    """
    dry_run = os.getenv("DRY_RUN", "true").lower() not in {"false", "0", "no", "off"}
    allowed = [a.strip() for a in os.getenv("ALLOWED_SEND_ADDRESSES", "").split(",") if a.strip()]
    can_send = not dry_run and bool(allowed)

    # ── Build draft preview from state ───────────────────────────────────────
    selected  = state.get("selected_email") or {}
    to_addr   = selected.get("from_", "") or ""
    subject   = selected.get("subject", "") or ""
    body      = state.get("draft_reply") or ""

    # Pending attachments stored in _thread_attachments (keyed by thread_id)
    thread_id     = selected.get("threadId", "") or ""
    att_names: list[str] = []
    if thread_id:
        try:
            from agent.tools.gmail import _get_thread_attachments
            pending = _get_thread_attachments(thread_id)
            att_names = [a.get("filename", "file") for a in pending if a.get("filename")]
        except Exception:
            pass

    subject_line = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    att_display  = ", ".join(att_names) if att_names else "(none)"

    preview = (
        f"[DRAFT PREVIEW]\n"
        f"{'─' * 40}\n"
        f"To:          {to_addr}\n"
        f"Subject:     {subject_line}\n"
        f"Attachments: {att_display}\n"
        f"{'─' * 40}\n"
        f"{body}\n"
        f"{'─' * 40}"
    )

    action_prompt = (
        "Send this reply? (yes / no / describe changes)"
        if can_send else
        "Save this as a Gmail draft? (yes / no / describe changes)"
    )
    # "question" drives the UI review card; "llm_context" is a short summary
    # so the orchestrator doesn't echo the raw preview block back to the user.
    user_input = interrupt({
        "question": f"{preview}\n\n{action_prompt}",
        "llm_context": f"Draft ready — To: {to_addr}, Subject: {subject_line}. Waiting for user decision.",
    })

    normalized = re.sub(r"\s+", " ", str(user_input).strip().lower())
    normalized_no_punct = re.sub(r"[^\w\s\u0590-\u05FF]", "", normalized).strip()

    # Decisions must be explicit to avoid collisions (e.g. "noa" containing "no").
    save_draft_phrases = {"save as draft", "save draft", "save to draft", "save it as draft", "שמור כטיוטה"}
    is_save_draft = normalized in {SAVE_DRAFT_COMMAND} or normalized_no_punct in save_draft_phrases
    is_send_draft = normalized in {SEND_DRAFT_COMMAND} or normalized_no_punct in APPROVAL_KEYWORDS
    is_rejected = normalized in {REJECT_DRAFT_COMMAND} or normalized_no_punct in CANCEL_KEYWORDS
    is_approved = is_save_draft or is_send_draft

    return {
        "messages": [], # No message here!
        "awaiting_send": False,
        "review_granted": is_approved,
        "force_save_draft": is_save_draft,
        "draft_reply": None if is_rejected else body, # Clear if rejected
        "draft_attempts": 0,
    }
