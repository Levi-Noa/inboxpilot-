# InboxPilot — Email Response Agent

An AI-powered email assistant that helps users search Gmail, read emails, draft replies, and send or save them — with full human-in-the-loop control at every step.

Built as a take-home assessment for **WalkMe — AI Solution Engineer**.

> **This implementation goes beyond the assignment requirements.** In addition to the core flow, the agent includes LLM-driven reasoning (no hardcoded routing), persistent memory, multi-turn conversation across multiple emails, intelligent email retrieval, and a full React web UI.

---

## What the Agent Can Do

### ✅ Required by the Assignment
- Accept free-form natural language input to search for emails
- Search Gmail and display results (From, Subject, Date, Body)
- Generate a suggested reply using OpenAI
- Wait for user confirmation — approve / reject / modify
- Send the reply if approved
- Handle errors gracefully

### 🚀 Beyond the Assignment

| Capability | Description |
|---|---|
| **LLM reasoning over intent** | No keyword lists or hardcoded routing. The LLM reads the full conversation and decides what to do.
| **Intelligent email retrieval** | An LLM translates the user's natural language into Gmail operators, generates fallback queries, and re-ranks results for precision. |
| **Multi-turn conversation** | Full context is maintained across many turns. Switch between emails, revise a draft multiple times, or change topic mid-session. |
| **Compound intent** | "Find the McDonald's email and reply to it" triggers search → select → read → draft → review in one message, without step-by-step prompting. |
| **Persistent memory** | Conversation state is checkpointed to SQLite after every step. Close and reopen — the session resumes exactly where it left off. |
| **Iterative draft refinement** | Ask for changes in plain language ("make it shorter", "add a thank you") and the agent re-drafts until approved. |
| **File attachments** | Upload files in the UI; they are attached as MIME parts to the outgoing email. |
| **Save as draft** | Save to Gmail Drafts at the review step instead of sending. |
| **Multilingual** | Hebrew and English fully supported in the same conversation. |
| **React UI** | Full web interface with email cards, draft review cards, and file upload. Also works in CLI mode. |

---

## Setup Instructions

### Prerequisites
- Python 3.10+, Node.js 18+
- A Google Cloud project with Gmail API enabled
- An OpenAI API key

### 1. Install dependencies

```bash
# Backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux
pip install -r requirements.txt

# Frontend
cd frontend && npm install && cd ..
```

### 2. Configure Gmail credentials

1. [Google Cloud Console](https://console.cloud.google.com/) → Enable **Gmail API**
2. APIs & Services → Credentials → **OAuth 2.0 Client ID** → Desktop App → Download JSON
3. Rename the file to `credentials.json` and place it in the **project root**
4. On first run a browser opens for OAuth — grant access → `token.json` is created automatically

> **Scopes:** `gmail.readonly` + `gmail.compose` only (least-privilege)

### 3. Configure environment variables

```bash
cp .env.example .env   # macOS/Linux
copy .env.example .env  # Windows
```

| Variable | Description | Default |
|---|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key | — |
| `LLM_MODEL` | Model name (`gpt-4o`, `gpt-4o-mini`, etc.) | `gpt-4o` |
| `DRY_RUN` | `true` = save as draft; `false` = allow sending | `true` |
| `ALLOWED_SEND_ADDRESSES` | Allowlist of recipient addresses for sending | — |
| `GMAIL_CREDENTIALS_PATH` | Path to OAuth credentials file | `credentials.json` |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing (optional) | `false` |

---

## How to Run

**Quick Start (Windows):** `start-all.bat`

**Manual:**
```bash
# Terminal 1 — backend (run from project root)
PYTHONPATH=. python backend/main.py   # http://localhost:8000

# Terminal 2 — frontend
cd frontend && npm run dev             # http://localhost:3000
```

**CLI mode** (no UI): `python main.py`

---

## Example Interaction

```
User:  Did I get an email from McDonald's? Send them a reply.

Agent: [shows email card — McDonald's order confirmation]

User:  [clicks card]

Agent: [shows draft review card]
       To: noreply@mcdonalds.co.il
       Draft: Hi, I confirm and approve starting preparation. Thank you.
       [Send] [Save Draft] [Modify] [Reject]

User:  Make it more formal

Agent: [updated draft card]
       Hi, I hereby confirm my approval to begin preparation of my order. Best regards.
       [Send] [Save Draft] [Modify] [Reject]

User:  send it

Agent: ✅ Email sent to noreply@mcdonalds.co.il!
```

---

## Agent Reasoning

The agent uses a **ReAct (Reason + Act)** loop. At every step, the LLM reads the full conversation history and available tool schemas, reasons about what the user wants, and decides which tool to call — or responds directly.

**There are no hardcoded routing rules, keyword lists, or intent classifiers.** Examples:
- "find the McDonald's email and reply" → LLM chains `search_gmail` → `get_email_content` → `draft_reply` → `create_gmail_draft` automatically
- "i want to sent it" → LLM understands send intent without exact keyword matching
- "make it shorter" after a draft → LLM calls `draft_reply` again with the feedback
- "find me a different email" mid-draft → LLM starts a new search, leaving prior context intact
- ""ind the mail from last month" → LLM automatically resolves relative dates and filters Gmail metadata without manual date parsing.
---

## Graph Architecture

```
              [orchestrator]
           LLM reasons over full
           conversation + tool schemas
                    │
       ┌────────────┼────────────────────┐
   other tool    create_gmail_draft   no tool call
   call          (not yet approved)       │
       │                │                END
       │           [human_review] ⏸ user approves/modifies/rejects
       │                ├── approve  → [tool_executor] → orchestrator → END
       │                ├── modify   → orchestrator
       │                └── reject   → orchestrator
       │
  [tool_executor]
   runs tool, updates state
       │
       ├── search results found ──► [select_email] ⏸ user picks a card
       │                                  │
       │                            orchestrator
       └── otherwise ──────────────► orchestrator
```

### Why this enables true reasoning

- No node tells another what to do — **conditional routing edges** read state and decide the next node
- Interrupts pause the graph and serialize state to disk; the user's response resumes from the exact checkpoint
- The LLM adapts if the user skips a step or changes direction — it re-reads the full conversation on every pass

### Nodes

| Node | Role |
|---|---|
| `orchestrator` | Calls the LLM with full conversation + tools. Returns a tool call or a plain text response. |
| `tool_executor` | Runs the selected tool, updates state fields (`search_results`, `selected_email`, `draft_reply`, etc.) |
| `select_email` | **Interrupt** — user picks from clickable email cards; graph resumes with the chosen ID |
| `human_review` | **Interrupt** — user approves, modifies, or rejects the draft before anything is saved or sent |

---

## Intelligent Email Retrieval

`search_gmail` uses a five-stage pipeline — not simple keyword matching:

1. **LLM query builder** — translates natural language (including Hebrew) into Gmail operators (`from:`, `subject:`, `newer_than:`, etc.)
2. **Multi-candidate fallback** — generates progressively broader variants; stops at the first hit
3. **Parallel metadata fetch** — concurrent thread pool for low latency
4. **Two-stage ranking** — lexical scoring, then LLM re-rank for precision
5. **Context-aware follow-up** — preserves sender filters when the user asks for "more recent" or "earlier" emails

---

## Persistent Memory & Multi-Turn

Full `AgentState` is checkpointed to **SQLite** (`agent_memory.db`) after every node via LangGraph's `SqliteSaver`. Closing and reopening the app resumes the session from the exact checkpoint.

The `messages` field uses LangGraph's `add_messages` reducer — new messages are appended, so the LLM always has the full conversation history. This enables:

- **Cross-email sessions** — search one email, switch to another, draft replies for both in the same session
- **Iterative refinement** — multiple revision rounds, each building on the previous feedback
- **Natural topic switching** — "find me a different email" mid-conversation works without restarting

---

## Security

- **Prompt injection defense** — email bodies labeled `UNTRUSTED INPUT` in every LLM prompt
- **Two-gate sending** — `DRY_RUN=false` AND recipient in `ALLOWED_SEND_ADDRESSES` both required to send
- **OAuth least-privilege** — `gmail.readonly` + `gmail.compose` only
- **URL stripping** — tracking URLs removed before any LLM call
- **No credentials in code** — all secrets in `.env` (git-ignored); evaluator supplies their own

---

## Sending Policy

| Condition | Outcome |
|---|---|
| `DRY_RUN=true` (default) | Saved as Gmail Draft |
| `DRY_RUN=false` + recipient in allowlist | Sent after approval |
| `DRY_RUN=false` + recipient NOT in allowlist | Blocked entirely |
| User says "save as draft" at review step | Always saved, regardless of `DRY_RUN` |

---

## Project Structure

```
WalkMe-exercise/
├── agent/
│   ├── graph.py      # LangGraph graph, routing edges, tool executor
│   ├── nodes.py      # Orchestrator LLM node, human_review interrupt, system prompt
│   ├── state.py      # AgentState TypedDict — single source of truth
│   └── tools/
│       ├── gmail.py  # Gmail API: search, fetch, draft/send, attachments
│       ├── llm.py    # draft_reply tool with prompt injection defense
│       └── retry.py  # Exponential backoff
├── backend/main.py   # FastAPI server — REST API for the React frontend
├── frontend/src/     # React UI (ChatInterface, EmailCard, ReviewCard, etc.)
├── main.py           # CLI entrypoint
├── requirements.txt
├── .env.example
└── start-all.bat     # One-click startup (Windows)
```

---

## Assumptions & Design Decisions

1. **Draft-first by default** — `DRY_RUN=true` so the evaluator can review the draft in Gmail without anything being sent.
2. **No hardcoded intent detection** — all routing is LLM-driven, handling typos, paraphrases, and language mixing naturally.
3. **Always pause for email selection** — even a single result shows a confirmation card, keeping the user in control.
4. **Thread-aware replies** — replies include the original `threadId` to appear in-thread in Gmail.
5. **Recipient auto-detection** — extracted automatically from the `From` header; no manual entry needed.
6. **Hebrew support** — queries transliterated to Latin internally (Gmail's index is Latin-based); all responses mirror the user's language.
