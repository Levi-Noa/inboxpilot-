"""
LangGraph graph definition: a clean ReAct loop with a single human-review interrupt.

Graph topology:
    [orchestrator] ←─────────────────────┐
        │  LLM reasons, picks tool        │
        ↓                                 │
    [tool_executor] → result to messages ─┘
        │
        └── if tool = create_gmail_draft AND not review_granted:
                ⏸ INTERRUPT (human_review)
                   ├── approve  → tool_executor (executes create_gmail_draft) → END
                   ├── modify   → orchestrator (re-draft with feedback)
                   └── reject   → END

Uses SqliteSaver for cross-session memory persistence.
LangSmith tracing enabled via environment variables (LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY, LANGCHAIN_PROJECT).
"""

import sqlite3
import json
import re
import os
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from agent.state import AgentState
from agent.nodes import orchestrator, human_review, TOOLS, _get_llm

# Load environment variables early for LangSmith configuration
load_dotenv()

# ── LLM relevance filter ──────────────────────────────────────────────────────

def _llm_filter_results(results: list[dict], user_query: str) -> list[dict]:
    """Quick LLM call to keep only relevant results. Falls back to top 3."""
    if len(results) <= 1:
        return results
    try:
        llm = _get_llm()
        lines = [
            f"{i+1}. {r.get('subject', '')} | from {r.get('from_', '')} | {r.get('snippet', '')[:80]}"
            for i, r in enumerate(results)
        ]
        prompt = (
            f'User is looking for: "{user_query}"\n\n'
            f'These emails were already pre-filtered by Gmail search. Your job is ONLY to remove emails that are clearly from the wrong sender or completely off-topic.\n'
            f'Rules:\n'
            f'- If the user mentioned a person by name, remove emails NOT from that person.\n'
            f'- Do NOT filter based on email subject or content — any subject can be valid.\n'
            f'- When in doubt, KEEP the email.\n'
            f'- Return "none" ONLY if every single email is clearly from the wrong sender.\n\n'
            + "\n".join(lines)
            + '\n\nReturn ONLY the numbers of emails to KEEP, comma-separated (e.g. "1,2,3"). Keep at most 3.'
        )
        resp = llm.invoke(prompt)
        # Strip <think> blocks from reasoning models
        content = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
        if content.lower().strip() == "none":
            return []
        indices = [int(x.strip()) - 1 for x in content.split(",") if x.strip().isdigit()]
        filtered = [results[i] for i in indices if 0 <= i < len(results)]
        return filtered[:3] if filtered else results[:3]
    except Exception:
        return results[:3]


# ── Select email node (interrupt) ─────────────────────────────────────────────

def select_email(state: AgentState) -> dict:
    """
    Interrupt node: pauses when multiple email candidates exist.
    Waits for user to pick a number or type a new query.
    """
    ranked = state.get("ranked_emails", [])
    count = len(ranked)

    # Detect user language from the last human message
    user_query = state.get("user_query", "")
    is_hebrew = bool(re.search(r"[\u0590-\u05FF]", user_query))
    if is_hebrew:
        question = (
            f"מצאתי {count} אפשרויות רלוונטיות. בחר/י אחת מהאפשרויות למטה."
            if count > 1
            else "מצאתי אפשרות אחת רלוונטית. לחץ/י עליה למטה כדי להמשיך."
        )
    else:
        question = (
            f"I found {count} relevant options. Please select one below."
            if count > 1
            else "I found 1 relevant option. Click it below to continue."
        )
    user_input = interrupt({"question": question, "results": ranked})

    normalized = str(user_input).strip()

    # Extract a number from the input (handles "1", "Option 1", "I pick 2", etc.)
    digit_match = re.search(r'\b([1-9]\d*)\b', normalized)
    if digit_match:
        idx = int(digit_match.group(1)) - 1
        if 0 <= idx < len(ranked):
            chosen = ranked[idx]
            # Embed the message_id in the human message so the orchestrator
            # can call get_email_content with the exact correct ID
            human_msg = (
                f"Selected option {idx + 1}: {chosen.get('subject', '')}. "
                f"Calling get_email_content(message_id='{chosen.get('id', '')}') now."
            )
            return {
                "messages": [HumanMessage(content=human_msg)],
                "selected_email": {
                    "id": chosen.get("id", ""),
                    "threadId": chosen.get("threadId", ""),
                    "from_": chosen.get("from_", ""),
                    "subject": chosen.get("subject", ""),
                    "date": chosen.get("date", ""),
                    "body": "",
                },
                "selection_required": False,
                "ranked_emails": [],
                "search_results": [],
                "draft_reply": None,
                "draft_attempts": 0,
            }

    elif isinstance(user_input, str) and user_input.strip() == "_save_draft_":
        return {
            "review_granted": True,
            "force_save_draft": True,
            "messages": []
        }
    return {
        "review_granted": False,
        "force_save_draft": False,
        "messages": []
    }


# ── Tool node ─────────────────────────────────────────────────────────────────
tool_node = ToolNode(TOOLS)

SEND_TOOL_NAME = "create_gmail_draft"


def _last_ai_message(state: AgentState) -> AIMessage | None:
    """Return the most recent AIMessage in the conversation, or None."""
    for msg in reversed(state["messages"]):
        if not hasattr(msg, "content"):
            continue  # skip raw dicts / non-message objects from checkpoint
        if isinstance(msg, AIMessage):
            return msg
    return None


# ── Routing ───────────────────────────────────────────────────────────────────

def _last_human_message_text(state: AgentState) -> str:
    """Return the text of the most recent HumanMessage, lowercased."""
    for msg in reversed(state["messages"]):
        if not hasattr(msg, "content"):
            continue  # skip raw dicts / non-message objects from checkpoint
        if isinstance(msg, HumanMessage):
            return str(msg.content).strip().lower()
    return ""


# Exact short-form approval phrases — must be the *entire* message (stripped).
# Using full-message match prevents "ok so can you change the subject?" from bypassing review.
_APPROVAL_EXACT = {"yes", "y", "ok", "okay", "approve", "approved", "go ahead", "send it", "send", "save", "כן", "אשר", "שלח"}

def _is_approval(text: str) -> bool:
    """True only when the entire (stripped, lowercased) message is an approval phrase."""
    return text.strip().lower() in _APPROVAL_EXACT


def route_after_orchestrator(state: AgentState) -> str:
    """
    After orchestrator:
    - create_gmail_draft tool call + not approved → human_review (interrupt)
    - Any other tool call → tools
    - No tool call (plain text) → END
    """
    last = _last_ai_message(state)
    if last is None or not last.tool_calls:
        return END
    for tc in last.tool_calls:
        if tc["name"] == SEND_TOOL_NAME:
            last_human = _last_human_message_text(state)
            user_approved = _is_approval(last_human)
            if not state.get("review_granted", False) and not user_approved:
                return "human_review"
            return "tools"
        # Break AUTOMATIC draft_reply loops (prevent the LLM from re-drafting on its own).
        # Do NOT block if the last human message was a revision request (not a simple approval).
        if tc["name"] == "draft_reply" and state.get("draft_attempts", 0) >= 1:
            last_human = _last_human_message_text(state)
            user_is_revising = last_human and not _is_approval(last_human)
            if not user_is_revising:
                return END
    return "tools"


def route_after_human_review(state: AgentState) -> str:
    """After human review, route based on user decision."""
    if state.get("review_granted", False):
        return "tools"
    # User rejected or gave feedback — back to orchestrator
    return "orchestrator"


def route_after_tools(state: AgentState) -> str:
    """After tools execute, route to select_email if disambiguation needed."""
    if state.get("selection_required", False):
        return "select_email"
    return "orchestrator"


# ── Tool executor with state mutations ────────────────────────────────────────

def tool_executor(state: AgentState) -> dict:
    """Execute tools and update state fields from tool results."""
    # If user chose "Save as Draft", temporarily force DRY_RUN so create_gmail_draft saves not sends
    _old_dry_run = None
    if state.get("force_save_draft"):
        last_ai = _last_ai_message(state)
        if last_ai and any(tc["name"] == SEND_TOOL_NAME for tc in (last_ai.tool_calls or [])):
            import os as _os
            _old_dry_run = _os.environ.get("DRY_RUN", "true")
            _os.environ["DRY_RUN"] = "true"

    result = tool_node.invoke(state)

    if _old_dry_run is not None:
        import os as _os
        _os.environ["DRY_RUN"] = _old_dry_run

    messages = result.get("messages", [])
    if not messages or not isinstance(messages[-1], ToolMessage):
        return result

    last_tool = messages[-1]
    tool_name = getattr(last_tool, "name", "")

    payload = None
    try:
        parsed = json.loads(last_tool.content)
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        payload = None

    # After create_gmail_draft: consume approval gate
    if tool_name == SEND_TOOL_NAME:
        result["review_granted"] = False
        result["force_save_draft"] = False
        # Clear draft only when actually sent — keep it after a save so the LLM
        # can reason about sending it later if the user asks.
        action = (payload or {}).get("action", "")
        if action == "sent":
            result["draft_reply"] = None
            result["selected_email"] = None

    if not payload:
        return result

    # search_gmail: filter to relevant results, auto-select if single match
    if tool_name == "search_gmail" and payload.get("success"):
        candidates = payload.get("results", [])
        result["search_results"] = candidates

        user_query = state.get("user_query", "")
        if not user_query:
            for msg in reversed(state.get("messages", [])):
                if isinstance(msg, HumanMessage):
                    user_query = msg.content if isinstance(msg.content, str) else ""
                    break
        result["user_query"] = user_query
        filtered = _llm_filter_results(candidates, user_query) if candidates else []
        result["ranked_emails"] = filtered

        if filtered:
            # Always pause for user to confirm selection (even single result)
            result["selection_required"] = True
            result["selected_email"] = None
        else:
            result["selected_email"] = None
            result["selection_required"] = False

    # get_email_content: store cleaned body in selected_email
    if tool_name == "get_email_content" and payload.get("success"):
        raw_body = payload.get("body", "")
        clean_body = re.sub(r"https?://\S+", "[link]", raw_body)
        clean_body = clean_body.replace("\r\n", "\n").replace("\r", "\n")
        clean_body = re.sub(r"\n{3,}", "\n\n", clean_body).strip()
        clean_body = clean_body[:1500]
        result["selected_email"] = {
            "id": payload.get("id", ""),
            "threadId": payload.get("threadId", ""),
            "from_": payload.get("from_", ""),
            "subject": payload.get("subject", ""),
            "date": payload.get("date", ""),
            "body": clean_body,
        }

    # draft_reply: store draft text and count attempts
    if tool_name == "draft_reply" and payload.get("success"):
        result["draft_reply"] = payload.get("draft", "")
        result["draft_attempts"] = state.get("draft_attempts", 0) + 1
        # Once we have a draft, we are definitely done with previous search counts
        result["search_results"] = []
        result["ranked_emails"] = []

    # get_email_content: clear search context to avoid redundant text
    if tool_name == "get_email_content":
        result["search_results"] = []
        result["ranked_emails"] = []

    return result


# ── Graph construction ────────────────────────────────────────────────────────

def build_graph():
    """
    Build and compile the email agent graph.
    3 nodes: orchestrator, tools, human_review.
    Uses SqliteSaver for local persistence (when not in LangGraph API).
    LangSmith tracing is configured via environment variables.
    """
    builder = StateGraph(AgentState)

    builder.add_node("orchestrator", orchestrator)
    builder.add_node("tools", tool_executor)
    builder.add_node("select_email", select_email)
    builder.add_node("human_review", human_review)

    builder.set_entry_point("orchestrator")

    builder.add_conditional_edges("orchestrator", route_after_orchestrator)
    builder.add_conditional_edges("tools", route_after_tools)
    builder.add_edge("select_email", "orchestrator")
    builder.add_conditional_edges("human_review", route_after_human_review)

    # Only use local SQLite persistence for non-API usage
    # LangGraph API handles persistence automatically
    checkpointer = None
    try:
        # Check if we're running in LangGraph API context
        import sys
        if not any("langgraph_api" in m for m in sys.modules):
            conn = sqlite3.connect("agent_memory.db", check_same_thread=False)
            checkpointer = SqliteSaver(conn)
    except Exception:
        pass
    
    # Compile with LangSmith tracing (auto-configured via env vars)
    kwargs = {}
    if checkpointer:
        kwargs["checkpointer"] = checkpointer
    
    graph = builder.compile(**kwargs)
    
    # Log LangSmith configuration status
    tracing_enabled = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
    if tracing_enabled:
        project = os.getenv("LANGCHAIN_PROJECT", "default")
        print(f"[OK] LangSmith tracing enabled for project: {project}")
    
    return graph


# ── Module-level graph instance for LangGraph CLI ────────────────────────────
graph = build_graph()
