from dotenv import load_dotenv
import os
load_dotenv()

try:
    from backend import config  # type: ignore
except Exception:
    pass # config handled via environment variables or local import
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import re
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel
from agent.graph import build_graph
from langchain_core.messages import AIMessage, ToolMessage
from agent.nodes import set_runtime_llm
from langgraph.types import Command
from agent.tools.gmail import (
    check_gmail_token,
    ensure_gmail_authenticated,
    set_thread_attachments,
    clear_thread_attachments,
    get_oauth_status,
    reset_oauth_state,
)

app = FastAPI(title="InboxPilot API", version="1.0.0")

# Enable CORS for React frontend (Port 3000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global graph instance
graph = build_graph()

# Store active threads for conversations
threads = {}


class AttachmentInput(BaseModel):
    filename: str
    mime_type: str | None = None
    content_base64: str


class ChatRequest(BaseModel):
    message: str
    thread_id: str
    model: str | None = None
    provider: str | None = None
    openai_api_key: str | None = None
    attachments: list[AttachmentInput] | None = None


class EmailSelectRequest(BaseModel):
    email_id: str
    thread_id: str


def _last_ai_content(messages) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return str(msg.content or "")
    return ""


def _contains_create_draft_tool(messages) -> bool:
    for msg in reversed(messages[-8:]):
        if isinstance(msg, ToolMessage) and (getattr(msg, "name", "") == "create_gmail_draft"):
            return True
    return False


def _extract_draft_tool_args(messages) -> dict | None:
    """Return the args (to, subject, body) the LLM passed to create_gmail_draft, if present."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            for tc in (getattr(msg, "tool_calls", None) or []):
                if tc.get("name") == "create_gmail_draft":
                    return tc.get("args", {})
    return None


def _is_numerical_selection(text: str) -> bool:
    """True if message is just a number like '1', '2', etc."""
    return bool(re.fullmatch(r"\d+", text.strip()))

def _is_search_query(text: str) -> bool:
    """True if message looks like a search or question."""
    t = text.strip()
    if not t or _is_numerical_selection(t) or t.startswith("_"):
        return False
    # If it's more than 3 words or contains common question words
    words = t.split()
    if len(words) > 3: return True
    question_words = ["search", "find", "get", "show", "מייל", "חפש", "מצא", "איפה", "יש", "email", "?", "האם"]
    return any(qw in t.lower() for qw in question_words)


def _extract_recipient_override(text: str) -> str | None:
    """Extract an explicit recipient override from user text when intent indicates changing target address."""
    if not text:
        return None

    match = re.search(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", text)
    if not match:
        return None

    normalized = text.strip().lower()
    change_markers = [
        "send to", "reply to", "recipient", "address", "instead",
        "לכתובת", "למייל", "תשנה", "שנה", "במקום", "שלח ל",
    ]
    if any(marker in normalized for marker in change_markers):
        return match.group(0)
    return None


def _is_selection_turn(snap) -> bool:
    """True if the graph is currently waiting at the select_email interrupt."""
    tasks = getattr(snap, "tasks", []) or []
    for task in tasks:
        interrupts = getattr(task, "interrupts", []) or []
        for intr in interrupts:
            val = getattr(intr, "value", intr)
            if isinstance(val, dict) and "results" in val:
                return True
    return False


def _is_human_review_turn(snap) -> bool:
    """True if the graph is currently waiting at the human_review interrupt."""
    tasks = getattr(snap, "tasks", []) or []
    for task in tasks:
        interrupts = getattr(task, "interrupts", []) or []
        for intr in interrupts:
            val = getattr(intr, "value", intr)
            if isinstance(val, dict) and "question" in val and "results" not in val:
                return True
    return False


# Patterns to strip raw draft/email blocks from LLM text output
# Matches LLM-generated ---[DRAFT PREVIEW]---**To:** ... format
_DRAFT_BLOCK_PATTERN = re.compile(
    r"---\s*\[DRAFT PREVIEW\][\s\S]*?---",
    re.IGNORECASE
)
# Matches nodes.py human_review interrupt preview: [DRAFT PREVIEW]\n────...
_DRAFT_INTERRUPT_PATTERN = re.compile(
    r"\[DRAFT PREVIEW\][\s\S]*?(?=\n\n|$)",
    re.IGNORECASE
)
# Match **From:** / **Subject:** email block (from get_email_content response in LLM)
_EMAIL_BLOCK_PATTERN = re.compile(
    r"\*\*From:\*\*\s*(.*?)\s*\n\s*\*\*Subject:\*\*\s*(.*?)\s*\n\s*\*\*Date:\*\*\s*(.*?)\s*\n\s*\*\*Body:\*\*\s*([\s\S]+?)(?=\n\s*\*\*|$)",
    re.IGNORECASE
)


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}


@app.get("/api/gmail/status")
async def gmail_status():
    """Check if Gmail is connected — checks live service, not just token file."""
    try:
        # First try: check if we already have an active service (fastest path)
        from agent.tools.gmail import _gmail_service, check_gmail_token
        if _gmail_service is not None:
            return {"connected": True, "message": "Gmail is connected"}
        connected = check_gmail_token()
        return {
            "connected": connected,
            "message": "Gmail is connected" if connected else "Gmail is not connected"
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


@app.post("/api/gmail/connect")
async def gmail_connect(background_tasks: BackgroundTasks):
    """Start Gmail OAuth flow from explicit user action in background."""
    try:
        background_tasks.add_task(ensure_gmail_authenticated)
        return {
            "connected": False,
            "message": "Gmail OAuth flow started in background."
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """
    Send a message to the AI agent
    """
    try:
        # Select model and provider (Defaults to OpenAI/gpt-4o)
        requested_model = (req.model or "").strip() or os.getenv("LLM_MODEL", "gpt-4o")
        requested_provider = (req.provider or "").strip().lower() or os.getenv("LLM_PROVIDER", "openai").lower()

        set_runtime_llm(
            model=requested_model,
            openai_api_key=req.openai_api_key,
        )

        # Initialize thread state if not exists
        if req.thread_id not in threads:
            threads[req.thread_id] = {
                "messages": [],
                "search_results": [],
                "waiting_for_interrupt": False,
                "last_email_card_id": None,
                "recipient_override": None,
                "selected_email": None,
            }

        attachments_payload = [a.model_dump() for a in (req.attachments or [])]
        if attachments_payload:
            set_thread_attachments(req.thread_id, attachments_payload)
            attachment_note = ", ".join(a.get("filename", "file") for a in attachments_payload)
            user_message = f"{req.message}\n\nAttached files available for the next draft/send: {attachment_note}. Include them when creating the email draft."
        else:
            user_message = req.message

        config = {"configurable": {"thread_id": req.thread_id}}

        # Ensure keys present for older in-memory threads
        threads[req.thread_id].setdefault("waiting_for_interrupt", False)
        threads[req.thread_id].setdefault("last_email_card_id", None)
        threads[req.thread_id].setdefault("recipient_override", None)
        threads[req.thread_id].setdefault("selected_email", None)

        # Capture explicit recipient override requests (e.g., "send to noa@example.com instead").
        override_to = _extract_recipient_override(user_message)
        if override_to:
            threads[req.thread_id]["recipient_override"] = override_to

        # ── Route: resume interrupt or start new turn ─────────────────────────
        if threads[req.thread_id].get("waiting_for_interrupt"):
            threads[req.thread_id]["waiting_for_interrupt"] = False
            update_payload = {}
            selected_for_update = threads[req.thread_id].get("selected_email")
            if threads[req.thread_id].get("recipient_override") and selected_for_update:
                updated_selected = dict(selected_for_update)
                updated_selected["from_"] = threads[req.thread_id]["recipient_override"]
                update_payload["selected_email"] = updated_selected
            if "_save_draft_" in user_message:
                update_payload.update({"force_save_draft": True, "review_granted": True})
            result = graph.invoke(Command(resume=user_message, update=update_payload), config)
        else:
            if "_save_draft_" in user_message:
                graph_input = {
                    "messages": [("human", user_message.replace("_save_draft_", "save as draft please"))],
                    "force_save_draft": True,
                    "review_granted": True,
                }
            else:
                threads[req.thread_id]["messages"].append(("human", user_message))
                graph_input = {"messages": threads[req.thread_id]["messages"]}

            selected_for_input = threads[req.thread_id].get("selected_email")
            if selected_for_input:
                graph_input["selected_email"] = selected_for_input
            if threads[req.thread_id].get("recipient_override") and graph_input.get("selected_email"):
                graph_input["selected_email"] = dict(graph_input["selected_email"])
                graph_input["selected_email"]["from_"] = threads[req.thread_id]["recipient_override"]

            result = graph.invoke(graph_input, config)

        # Check if the graph paused at an interrupt after this turn
        snap = graph.get_state(config)
        interrupt_question = None
        is_human_review_turn = _is_human_review_turn(snap)
        is_selection_turn = _is_selection_turn(snap)

        for task in (snap.tasks or []):
            for intr in (getattr(task, "interrupts", None) or []):
                val = intr.value if hasattr(intr, "value") else intr
                if isinstance(val, dict) and "question" in val:
                    interrupt_question = val["question"]
                elif isinstance(val, str):
                    interrupt_question = val
                
                if interrupt_question:
                    threads[req.thread_id]["waiting_for_interrupt"] = True
                    break
            if interrupt_question:
                break

        # ── Detect special card UI payloads ───────────────────────────────────
        state = snap.values if hasattr(snap, "values") else {}
        search_results = state.get("ranked_emails") or state.get("search_results", [])
        email_card = state.get("selected_email")

        # Track latest selected email snapshot for later recipient overrides.
        if email_card:
            threads[req.thread_id]["selected_email"] = email_card

        # Apply recipient override to displayed card/review data when present.
        recipient_override = threads[req.thread_id].get("recipient_override")
        if recipient_override and email_card:
            email_card = dict(email_card)
            email_card["from_"] = recipient_override

        # ── Build review_data payload ─────────────────────────────────────────
        review_data = None
        has_draft = bool(state.get("draft_reply"))
        has_review = state.get("review_granted", False)
        
        # Suppress review_data after a successful save/send action
        draft_was_executed = "_save_draft_" in user_message or "_send_draft_" in user_message

        if (is_human_review_turn or (has_draft and not has_review)) and not draft_was_executed:
            selected = email_card or {}
            # Gather attachment names for the review card
            from agent.tools.gmail import _get_thread_attachments
            pending_atts = _get_thread_attachments(req.thread_id)
            att_names = [a.get("filename", "file") for a in pending_atts if a.get("filename")]
            # Prefer the actual args the LLM passed to create_gmail_draft so the
            # review card reflects the real recipient/subject/body, not defaults.
            tool_args = _extract_draft_tool_args(result.get("messages", [])) or {}
            review_data = {
                "draft": tool_args.get("body") or state.get("draft_reply") or "",
                "to": tool_args.get("to") or selected.get("from_", ""),
                "subject": tool_args.get("subject") or selected.get("subject", ""),
                "threadId": req.thread_id,
                "originalBody": selected.get("body", ""),
                "originalFrom": selected.get("from_", ""),
                "originalDate": selected.get("date", ""),
                "attachments": att_names,
            }

        # ── Build clean ai_response ───────────────────────────────────────────
        if is_human_review_turn:
            # Empty so the frontend shows only the ReviewCard, not a text bubble
            ai_response = ""
            interrupt_question = None  # Don't leak [DRAFT PREVIEW] into content field
        else:
            # Get raw LLM output
            raw_ai = _last_ai_content(result.get("messages", []))

            # Strip any leaked [DRAFT PREVIEW] blocks from the LLM output
            raw_ai = re.sub(r"\[DRAFT PREVIEW\][\s\S]*", "", raw_ai, flags=re.IGNORECASE).strip()
            raw_ai = re.sub(r"---\s*\[DRAFT PREVIEW\][\s\S]*?---", "", raw_ai, flags=re.IGNORECASE).strip()

            # If we have an email card, strip the redundant header text
            if email_card and email_card.get("body"):
                ai_response = interrupt_question or raw_ai
                ai_response = re.sub(r"(?s)\*\*From:\*\*.*?\*\*Body:\*\*.*?\n\n", "", ai_response).strip()
            else:
                ai_response = interrupt_question or raw_ai

            # If user explicitly saved/sent, provide a concrete message if AI didn't
            if "_save_draft_" in user_message and not ai_response:
                ai_response = "✅ Draft successfully saved to your Gmail!"
            elif "_send_draft_" in user_message and not ai_response:
                ai_response = "✅ Email sent successfully!"

        is_selection = _is_numerical_selection(user_message) or user_message.lower().startswith("select")
        is_action = user_message.startswith("_") or any(confirm in ai_response.lower() for confirm in ["saved", "sent", "נשלח", "נשמר"])
        
        if is_selection or is_action:
            ai_response = re.sub(r"I found \d+ relevant options?\.?", "", ai_response, flags=re.IGNORECASE).strip()
            if not ai_response and is_selection:
                ai_response = "מתבצע..." if bool(re.search(r"[\u0590-\u05FF]", user_message)) else "Processing..."

        threads[req.thread_id]["messages"] = result["messages"]
        threads[req.thread_id]["search_results"] = search_results
        
        # ── Handle Email Card Dedup ───────────────────────────────────────────
        email_card_data = None
        if email_card and email_card.get("body"):
            email_id_key = email_card.get("id") or email_card.get("from_", "")
            if email_id_key != threads[req.thread_id].get("last_email_card_id"):
                threads[req.thread_id]["last_email_card_id"] = email_id_key
                email_card_data = {
                    "from": email_card.get("from_", ""),
                    "subject": email_card.get("subject", ""),
                    "date": email_card.get("date", ""),
                    "body": email_card.get("body", ""),
                }

        # ── Final Payload ─────────────────────────────────────────────────────
        assistant_text = ai_response or interrupt_question or _last_ai_content(result.get("messages", []))
        
        # Clear one-shot attachments after any tool run
        if _contains_create_draft_tool(result.get("messages", [])):
            clear_thread_attachments(req.thread_id)
            threads[req.thread_id]["recipient_override"] = None

        return {
            "response": ai_response,
            "role": "assistant",
            "content": assistant_text,
            "searchResults": format_search_results(search_results),
            "reviewData": review_data,
            "emailCard": email_card_data,
            "isSelection": is_selection_turn and not is_selection,
            "isHumanReview": is_human_review_turn,
            "thread_id": req.thread_id,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "response": f"Error: {str(e)}",
            "error": True,
            "thread_id": req.thread_id
        }


@app.post("/api/email/select")
async def select_email(req: EmailSelectRequest):
    """
    Select an email and get its content
    """
    try:
        # Get search results from thread
        if req.thread_id not in threads:
            return {"error": "Thread not found"}

        search_results = threads[req.thread_id]["search_results"]
        
        # Find the selected email
        email = next((e for e in search_results if e.get("id") == req.email_id), None)
        
        if not email:
            return {"error": "Email not found"}

        # Send a command to continue with this email
        message = f"Select email: {email.get('subject', 'Unknown')}"
        
        # Add to thread
        threads[req.thread_id]["messages"].append(("human", message))

        # Run graph
        config = {"configurable": {"thread_id": req.thread_id}}
        
        # Check if we should resume or invoke
        if threads[req.thread_id].get("waiting_for_interrupt"):
            threads[req.thread_id]["waiting_for_interrupt"] = False
            result = graph.invoke(Command(resume=message), config)
        else:
            graph_input = {
                "messages": threads[req.thread_id]["messages"]
            }
            result = graph.invoke(graph_input, config)

        # Get response
        ai_response = _last_ai_content(result.get("messages", []))

        return {
            "response": ai_response or "Email selected",
            "email": email,
            "thread_id": req.thread_id
        }

    except Exception as e:
        return {
            "error": str(e),
            "thread_id": req.thread_id
        }


@app.delete("/api/thread/{thread_id}")
async def delete_thread(thread_id: str):
    """Delete a thread/conversation"""
    if thread_id in threads:
        del threads[thread_id]
        return {"message": "Thread deleted"}
    return {"error": "Thread not found"}


def format_search_results(results):
    """Format search results for API response"""
    if not results:
        return []
    
    return [
        {
            "id": r.get("id", ""),
            "threadId": r.get("threadId", ""),
            "from_": r.get("from_", ""),
            "subject": r.get("subject", ""),
            "date": r.get("date", ""),
            "snippet": r.get("snippet", "")
        }
        for r in results
    ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)