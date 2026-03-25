# InboxPilot — Email Response Agent

An AI-powered email assistant that helps users search Gmail, read emails, draft replies, and send or save them — with full human-in-the-loop control at every step.

Built as a take-home assessment for **WalkMe — AI Solution Engineer**.

---

## Features

- **Natural language email search** — search Gmail using free-form text, powered by an LLM-generated query builder that translates intent (including Hebrew) into Gmail operators (`from:`, `subject:`, `newer_than:`, etc.)
- **Multi-parameter search** — combine sender, subject keywords, date ranges, and other Gmail operators in a single query
- **Clickable email card selection** — when multiple matches are found, the UI displays card results for the user to pick from
- **AI draft generation** — generates a contextual reply matching the original email's language and formality
- **Draft modification** — user can iteratively refine the draft by providing natural-language feedback
- **Attachment support** — upload files in the UI and they are automatically attached to the reply
- **Save as draft / Send** — can save the reply to Gmail Drafts or send it directly (controlled via environment variables)
- **Multilingual support** — fully supports Hebrew and English throughout the conversation
- **Human-in-the-loop** — user must explicitly approve, modify, or reject every draft before it is sent or saved
- **Persistent memory** — conversation state checkpointed to SQLite across sessions

---

## Setup Instructions

### Prerequisites

- Python 3.10+
- Node.js 18+
- A Google Cloud project with the Gmail API enabled
- An OpenAI API key

### 1. Clone and install dependencies

```bash
# Backend (Python) — from the project root
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux
pip install -r requirements.txt

# Frontend (React / TypeScript)
cd frontend
npm install
cd ..
```

### 2. Configure Gmail API credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the **Gmail API**: APIs & Services → Library → "Gmail API" → Enable
3. Create credentials: APIs & Services → Credentials → **OAuth 2.0 Client ID** → Desktop App
4. Download the JSON file, rename it to `credentials.json`, and place it in the **project root**
5. On first run a browser window opens for OAuth — log in and grant access
6. A `token.json` file is created automatically for future sessions (no browser prompt after that)

> **Scopes requested:** `gmail.readonly` + `gmail.compose` (least-privilege)

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env   # macOS / Linux
copy .env.example .env  # Windows
```

Key variables:

| Variable | Description | Default |
|---|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key | — |
| `LLM_MODEL` | Model name (e.g. `gpt-4o`, `gpt-4o-mini`) | `gpt-4o` |
| `DRAFT_LLM_MODEL` | Override model for draft generation only (optional) | same as `LLM_MODEL` |
| `RANK_LLM_MODEL` | Override model for search ranking/query building (optional) | `gpt-4o-mini` |
| `DRY_RUN` | `true` = save as draft; `false` = allow sending | `true` |
| `ALLOWED_SEND_ADDRESSES` | Comma-separated list of allowed recipient addresses | — |
| `GMAIL_CREDENTIALS_PATH` | Path to OAuth credentials file | `credentials.json` |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing (`true`/`false`) | `false` |
| `LANGCHAIN_API_KEY` | LangSmith API key (optional) | — |
| `LANGCHAIN_PROJECT` | LangSmith project name | `walkme-email-agent` |

---

## How to Run

### Quick Start (Windows)

```bash
start-all.bat
```

This starts both the backend API and the frontend dev server. The UI opens at `http://localhost:5173`.

### Manual Start

**Backend** (terminal 1):
```bash
cd backend
python main.py
# FastAPI backend runs at http://localhost:8000
```

**Frontend** (terminal 2):
```bash
cd frontend
npm run dev
# React UI opens at http://localhost:5173
```

### CLI Mode (no UI)

```bash
python main.py
```

Interact with the agent directly in the terminal. Full agent functionality is available, except file attachments and clickable card selection (which require the UI).

---

## Example Interaction

```
User:  Can you help me respond to the email about the project proposal follow-up?

Agent: I'll search for that email.

Agent: I found 2 relevant options. Please select one below.
       [Card 1] Project proposal follow-up — from john@example.com
       [Card 2] Re: Proposal — from sarah@example.com

User:  [clicks Card 1]

Agent: Found an email from john@example.com:
       From: john@example.com
       Subject: Project proposal follow-up
       Date: Jan 15, 2025
       Body: Hi, I wanted to follow up on the proposal we sent last week...

       Would you like me to draft a reply?

User:  yes

Agent: Let me draft a response for you.

Agent: Here's my suggested reply:
       ---
       Hi John,
       Thank you for following up. I've reviewed the proposal and have a few questions...
       ---
       Would you like me to send this reply, save it as a draft, or modify it?

User:  Make it shorter and more casual

Agent: Here's the revised draft:
       ---
       Hi John,
       Thanks for following up! Happy to discuss — let me know a good time to connect.
       ---

User:  send it

Agent: ✅ Reply sent to john@example.com!
```

---

## Architecture & Design Decisions

### LangGraph ReAct Agent

The agent is built on **LangGraph** using a ReAct (Reason + Act) architecture:

- A central **Orchestrator** node calls an LLM with bound tools
- The LLM decides which tool to call at each step based on the conversation — no hardcoded routing rules
- Results feed back into the message history for the next reasoning pass

```
[orchestrator] ◄──────────────────────────────┐
    │ LLM reasons, picks tool                  │
    ▼                                          │
[tool_executor] ── result → messages ──────────┘
    │
    └── If search returns multiple results:
           ⏸ INTERRUPT (select_email) → user picks one

    └── If tool = create_gmail_draft AND not approved:
           ⏸ INTERRUPT (human_review)
              ├── approve  → tool_executor → END
              ├── modify   → orchestrator (re-draft with feedback)
              └── reject   → END
```

### Graph Nodes

| Node | Role |
|---|---|
| `orchestrator` | Central LLM node — decides which tool to call next and responds to the user |
| `tool_executor` | Executes the selected tool and updates graph state (search results, selected email, draft text, etc.) |
| `select_email` | **Interrupt** — pauses when multiple search results exist; user picks one via the UI or types a number |
| `human_review` | **Interrupt** — pauses before `create_gmail_draft` runs; user approves, rejects, or requests changes |

### Tools

| Tool | File | Purpose |
|---|---|---|
| `search_gmail` | `agent/tools/gmail.py` | Searches Gmail using an LLM-built query; supports multi-parameter search, fallback query candidates, and lexical + LLM re-ranking |
| `get_email_content` | `agent/tools/gmail.py` | Fetches the full email body (HTML-stripped, capped at 2000 chars) by message ID |
| `draft_reply` | `agent/tools/llm.py` | Generates a professional reply matching the email's language and formality; incorporates iterative user feedback |
| `create_gmail_draft` | `agent/tools/gmail.py` | Saves the reply as a Gmail Draft or sends it; supports file attachments |

### Advanced Search

`search_gmail` goes beyond basic keyword matching:

1. **LLM query builder** — converts free-form natural language (including Hebrew) into Gmail operators (`from:`, `subject:`, `newer_than:`, etc.)
2. **Multiple candidate queries** — generates several query variants and tries them in order; stops at the first hit to prevent result pollution
3. **Parallel metadata fetch** — retrieves email metadata concurrently using a thread pool
4. **Two-stage ranking** — lexical scoring first, then an LLM re-rank for precision
5. **Context-aware follow-up search** — preserves sender filters when the user asks for "more recent" or "anything newer"

### Attachment Support

Files uploaded in the UI are stored per thread as base64 payloads. When `create_gmail_draft` runs, they are attached as MIME parts to the outgoing message (preserving filename and MIME type). Malformed payloads are skipped silently.

### Save as Draft

The user can say "save it as a draft" (or Hebrew equivalent) at the review step. The `human_review` node detects this intent and sets `force_save_draft=True`, which overrides the `DRY_RUN` environment variable to guarantee the email is saved — not sent.

### Human-in-the-Loop (HITL)

Two LangGraph `interrupt` points gate every action:

1. **Email selection** — shown whenever Gmail returns more than one match; user selects from clickable cards in the UI
2. **Draft review** — shown after every draft is generated (new or revised); user must explicitly approve, modify, or reject before anything is saved or sent

### Security

- **Prompt injection defense** — email bodies are labeled `UNTRUSTED INPUT` in the drafting prompt; the LLM is instructed to treat them as data only
- **Two-gate sending policy** — `DRY_RUN=false` AND recipient in `ALLOWED_SEND_ADDRESSES` are both required to actually send
- **OAuth least-privilege** — only `gmail.readonly` + `gmail.compose` scopes are requested
- **URL stripping** — tracking URLs are removed from email bodies before they are passed to any LLM

### Persistence & Tracing

- **SQLite checkpointing** — full conversation state is saved to `agent_memory.db` after every node, enabling cross-session resume
- **LangSmith tracing** — optional; configure `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY` to trace every graph run

### Multi-Provider LLM Support
Uses OpenAI (`langchain-openai`). The orchestrator, draft generator, and ranking model are all OpenAI — configurable to different OpenAI models via `LLM_MODEL`, `DRAFT_LLM_MODEL`, and `RANK_LLM_MODEL`.

---

## Project Structure

```
WalkMe-exercise/
├── agent/
│   ├── graph.py          # LangGraph graph definition, routing logic, and tool executor
│   ├── nodes.py          # Orchestrator and human_review nodes; system prompt
│   ├── state.py          # AgentState TypedDict (single source of truth)
│   └── tools/
│       ├── gmail.py      # Gmail API: search, fetch, draft/send, attachment handling
│       ├── llm.py        # draft_reply tool (with prompt injection defense)
│       └── retry.py      # Exponential backoff retry utility
├── backend/
│   └── main.py           # FastAPI server — REST API for the React frontend
├── frontend/
│   └── src/
│       ├── App.tsx        # Main application shell
│       ├── components/    # ChatInterface, EmailCard, ReviewCard, FileUpload, etc.
│       └── types/         # TypeScript type definitions
├── docs/
│   └── ASSIGNMENT.md     # Original assignment specification
├── main.py               # CLI entrypoint (runs the agent in the terminal)
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variable template
├── start-all.bat         # One-click startup script (Windows)
└── README.md
```

---

## Sending Policy

| Condition | Outcome |
|---|---|
| `DRY_RUN=true` (default) | Saved as Gmail Draft |
| `DRY_RUN=false` + recipient in `ALLOWED_SEND_ADDRESSES` | Sent immediately after approval |
| `DRY_RUN=false` + recipient NOT in allowlist | Blocked — neither sent nor saved |
| User says "save as draft" at review step | Always saved as Draft, regardless of `DRY_RUN` |

---

## Assumptions & Design Decisions

1. **Draft-first by default** — `DRY_RUN=true` keeps evaluation safe; the evaluator can read the draft in Gmail without anything being sent.
2. **LLM query building** — Gmail search is inherently keyword-based; using an LLM to translate user intent into Gmail operators significantly improves recall for non-obvious searches (name transliteration, date expressions, multi-field queries).
3. **Always pause for email selection** — even when a single result is returned, the agent shows it as a card for user confirmation rather than auto-selecting, keeping the user in control.
4. **Thread-aware replies** — replies include the original `threadId` so they appear in-thread inside Gmail.
5. **Recipient auto-detection** — the reply recipient is extracted automatically from the original email's `From` header; no manual entry required.
6. **Single-user local deployment** — the agent is designed for local use with one Gmail account at a time.
7. **Hebrew support** — Hebrew user queries are fully supported; Gmail search queries are transliterated to English/Latin internally (Gmail's index is Latin-based), while all user-facing messages respond in Hebrew when the user writes in Hebrew.
