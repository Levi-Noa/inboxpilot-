try:
    from backend import config  # type: ignore  # Load path configuration first
except Exception:
    import config  # type: ignore  # Fallback for direct script execution
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
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
)

load_dotenv()

app = FastAPI(title="InboxPilot API", version="1.0.0")

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
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


_SEND_INTENT_WORDS = [
    "send", "שלח", "תשלח", "שלח את זה", "תשלח את זה", "send it", "go ahead",
    "send now", "שלח עכשיו", "send the email", "שלח את המייל", "ok send", "yes send",
    "Yes", "yes", "approve", "כן", "אשר",
]

def _is_send_intent(text: str) -> bool:
    t = text.strip().lower()
    return any(w.lower() in t for w in _SEND_INTENT_WORDS)

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


def _is_selection_turn(snap) -> bool:
    """True if the graph is currently waiting at the select_email interrupt."""
    for task in (snap.tasks or []):
        for intr in (getattr(task, "interrupts", None) or []):
            val = intr.value if hasattr(intr, "value") else intr
            if isinstance(val, dict) and "results" in val:
                return True
    return False


def _is_human_review_turn(snap) -> bool:
    """True if the graph is currently waiting at the human_review interrupt."""
    for task in (snap.tasks or []):
        for intr in (getattr(task, "interrupts", None) or []):
            val = intr.value if hasattr(intr, "value") else intr
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
        # Runtime model/provider selection (keeps Qwen while enabling OpenAI).
        requested_model = (req.model or "").strip() or os.getenv("LLM_MODEL", "qwen/qwen3-32b")
        requested_provider = (req.provider or "").strip().lower()

        if not requested_provider:
            if requested_model.startswith("qwen/"):
                requested_provider = "groq"
            elif requested_model.startswith("gpt-"):
                requested_provider = "openai"
            else:
                requested_provider = os.getenv("LLM_PROVIDER", "groq").lower()

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
                "last_email_card_id": None,   # for dedup email cards
                "last_draft": None,           # for re-send after save
            }

        attachments_payload = [a.model_dump() for a in (req.attachments or [])]
        if attachments_payload:
            set_thread_attachments(req.thread_id, attachments_payload)
            attachment_note = ", ".join(a.get("filename", "file") for a in attachments_payload)
            user_message = f"{req.message}\n\nAttached files available for the next draft/send: {attachment_note}. Include them when creating the email draft."
        else:
            user_message = req.message

        config = {"configurable": {"thread_id": req.thread_id}}

        # Ensure legacy keys present
        threads[req.thread_id].setdefault("waiting_for_interrupt", False)
        threads[req.thread_id].setdefault("last_email_card_id", None)
        threads[req.thread_id].setdefault("last_draft", None)

        # ── Re-send after draft saved ─────────────────────────────────────────
        # If interrupt is NOT active but user expressed send intent and we have
        # a cached draft from a previous Save Draft action → send it directly.
        last_draft = threads[req.thread_id].get("last_draft")
        waiting = threads[req.thread_id].get("waiting_for_interrupt", False)
        if last_draft and not waiting and _is_send_intent(req.message) and not attachments_payload:
            # Inject draft state back into graph and invoke with 'yes'
            prior_msgs = [
                m for m in threads[req.thread_id]["messages"]
                if isinstance(m, tuple)
            ]
            graph_input = {
                "messages": prior_msgs + [("human", "yes")],
                "selected_email": last_draft["selected_email"],
                "draft_reply": last_draft["draft_reply"],
                "review_granted": True,
                "force_save_draft": False,
            }
            result = graph.invoke(graph_input, config)
            threads[req.thread_id]["last_draft"] = None  # consumed
        elif threads[req.thread_id].get("waiting_for_interrupt"):
            threads[req.thread_id]["waiting_for_interrupt"] = False
            if "_save_draft_" in user_message:
                result = graph.invoke(Command(resume=user_message, update={"force_save_draft": True, "review_granted": True}), config)
            else:
                result = graph.invoke(Command(resume=user_message), config)
        else:
            if "_save_draft_" in user_message:
                graph_input = {
                    "messages": [("human", user_message.replace("_save_draft_", "save as draft please"))],
                    "force_save_draft": True,
                    "review_granted": True
                }
            else:
                threads[req.thread_id]["messages"].append(("human", user_message))
                graph_input = {
                    "messages": threads[req.thread_id]["messages"]
                }
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
                    # ── CACHE DRAFT CONTEXT DURING INTERRUPT ────────────────────
                    snap_vals = snap.values if hasattr(snap, "values") else {}
                    if snap_vals.get("draft_reply") and snap_vals.get("selected_email"):
                        threads[req.thread_id]["last_draft"] = {
                            "selected_email": snap_vals["selected_email"],
                            "draft_reply": snap_vals["draft_reply"],
                        }
                    break
            if interrupt_question:
                break

        # ── Detect special card UI payloads ───────────────────────────────────
        state = snap.values if hasattr(snap, "values") else {}
        search_results = state.get("ranked_emails") or state.get("search_results", [])
        email_card = state.get("selected_email")

        # ── Build review_data payload ─────────────────────────────────────────
        review_data = None
        has_draft = bool(state.get("draft_reply"))
        has_review = state.get("review_granted", False)

        if is_human_review_turn or (has_draft and not has_review):
            selected = email_card or {}
            # Gather attachment names for the review card
            from agent.tools.gmail import _get_thread_attachments
            pending_atts = _get_thread_attachments(req.thread_id)
            att_names = [a.get("filename", "file") for a in pending_atts if a.get("filename")]
            review_data = {
                "draft": state.get("draft_reply") or "",
                "to": selected.get("from_", ""),
                "subject": selected.get("subject", ""),
                "threadId": req.thread_id,
                "originalBody": selected.get("body", ""),
                "originalFrom": selected.get("from_", ""),
                "originalDate": selected.get("date", ""),
                "attachments": att_names,
            }

        # ── Build clean ai_response ───────────────────────────────────────────
        if is_human_review_turn:
            # We set this to empty so the frontend MessageBubble returns null (hidden)
            ai_response = ""
        else:
            # Get raw LLM output
            raw_ai = _last_ai_content(result.get("messages", []))
            
            # If we have an email card, strip the redundant header text
            if email_card and email_card.get("body"):
                ai_response = interrupt_question or raw_ai
                ai_response = re.sub(r"(?s)\*\*From:\*\*.*?\*\*Body:\*\*.*?\n\n", "", ai_response).strip()
            else:
                if user_message.startswith("_") and any(confirm in raw_ai.lower() for confirm in ["saved", "sent", "נשלח", "נשמר"]):
                    ai_response = raw_ai
                else:
                    ai_response = interrupt_question or raw_ai

        # Only override with "I found X" if we're actually in a search results state
        is_selection = _is_numerical_selection(user_message) or user_message.lower().startswith("select")
        is_action = user_message.startswith("_") or any(confirm in ai_response.lower() for confirm in ["saved", "sent", "נשלח", "נשמר"])
        
        if search_results and not is_human_review_turn and not is_selection and not is_action:
            is_hebrew_msg = bool(re.search(r"[\u0590-\u05FF]", user_message))
            n = len(search_results)
            if is_hebrew_msg:
                ai_response = f"מצאתי {n} {'אפשרורת' if n == 1 else 'אפשרויות'} רלוונטיות. בחר/י אחת מהאפשרויות למטה."
            else:
                ai_response = f"I found {n} relevant option{'s' if n != 1 else ''}. Please select one below."
        elif is_selection or is_action:
            ai_response = re.sub(r"I found \d+ relevant options?\.?", "", ai_response, flags=re.IGNORECASE).strip()
            if not ai_response:
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
        graph_input = {
            "messages": threads[req.thread_id]["messages"]
        }
        config = {"configurable": {"thread_id": req.thread_id}}
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