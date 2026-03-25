# InboxPilot Backend API

FastAPI server wrapping the LangGraph email agent.

## Setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

Server will be at `http://localhost:8000`

## API Docs

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Endpoints

- `GET /api/health` - Health check
- `GET /api/gmail/status` - Check Gmail connection
- `POST /api/gmail/connect` - Start Gmail OAuth connect flow
- `POST /api/chat` - Send message to agent (supports optional `attachments` array with `filename`, `mime_type`, `content_base64`)
- `POST /api/email/select` - Select an email
- `DELETE /api/thread/{thread_id}` - Delete conversation thread
