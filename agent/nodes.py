"""
Graph nodes: orchestrator (LLM with tools) and human_review (interrupt).

The orchestrator is a true ReAct agent — the LLM decides which tools to call
based on conversation context. No deterministic overrides or planner routing.

Set LLM_PROVIDER=openai (default), azure_openai, groq, or ollama in your .env.
"""

import os
import re
import json
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.types import interrupt

from agent.state import AgentState
from agent.tools.gmail import search_gmail, get_email_content, create_gmail_draft
from agent.tools.llm import draft_reply

load_dotenv()
_llm_with_tools = None

APPROVAL_KEYWORDS = {"yes", "y", "save", "approve", "approved", "ok", "okay", "go ahead", "send it"}
CANCEL_KEYWORDS = {"no", "cancel", "stop", "never mind", "quit", "exit", "לא", "בטל", "עצור"}


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
1. **BE CONCISE**: Never repeat your instructions or re-introduce yourself. Never say "I can help you find emails..." or "I'll search for emails..." if you've already said it or the user knows.
2. **NO REDUNDANCY**: Do NOT list email options, summaries, or draft previews in your text response if a card will be shown. The UI handles the rich display. You only provide complementary text (like a question or confirmation).
3. **TEXT RESPONSE GUIDELINES**:
   - Only write text when asking a question (e.g., "Which email should I open?"), prompting for action ("Would you like me to draft a reply?"), or confirming success.
   - **SUCCESS CONFIRMATION**: After calling `create_gmail_draft` (save or send), you MUST say: "Reply sent successfully!" or "Draft saved successfully!".
4. **ALWAYS USE TOOLS**: Never describe an action without calling the corresponding tool.
5. **LANGUAGE**: Always respond in the same language as the user (Hebrew/English).

## Workflow
- Search ➔ `search_gmail`. If 1 result, immediately `get_email_content`. If multiple, wait for user selection.
- Read ➔ `get_email_content`. Then ask: "Would you like me to draft a reply?".
- Draft ➔ `draft_reply` (only after user says yes/draft/reply).
- Review ➔ Wait for user to Approve (yes/send/save) or Reject (no/stop) or Revise (provide feedback).
- Execute ➔ `create_gmail_draft` immediately upon approval.
- Final ➔ Say "Reply sent successfully!" or "Draft saved successfully!".
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

    # Sanitize ToolMessage content (strip URLs that cause format errors on some providers)
    sanitized = []
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
    response = llm_with_tools.invoke(messages)

    return {"messages": [response]}


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
    user_input = interrupt({"question": f"{preview}\n\n{action_prompt}"})

    normalized = str(user_input).strip().lower()

    # Detect "save as draft" intent — approve execution but force draft mode
    save_draft_phrases = {"save as draft", "save draft", "save to draft", "שמור כטיוטה", "save it as draft"}
    is_save_draft = any(phrase in normalized for phrase in save_draft_phrases)

    # Explicit "don't send" / "do not send" overrides any save/yes keyword
    dont_send_phrases = {"dont send", "don't send", "do not send", "אל תשלח", "לא לשלוח"}
    if any(phrase in normalized for phrase in dont_send_phrases) and not is_save_draft:
        is_approved = False
    else:
        # Substring match — so "save it and approve" / "yes please" etc. all work
        is_approved = (
            is_save_draft
            or normalized in APPROVAL_KEYWORDS
            or any(kw in normalized for kw in APPROVAL_KEYWORDS)
        )

    return {
        "messages": [], # No message here!
        "awaiting_send": False,
        "review_granted": is_approved,
        "force_save_draft": is_save_draft,
        "draft_attempts": 0,
    }
