"""
AgentState – the single source of truth passed between all graph nodes.
"""

from typing import Optional, Annotated
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class EmailResult(TypedDict):
    """A single Gmail search result (metadata only)."""
    id: str
    threadId: str
    from_: str
    subject: str
    date: str
    snippet: str


class SelectedEmail(TypedDict):
    """A fully fetched email with body."""
    id: str
    threadId: str
    from_: str
    subject: str
    date: str
    body: str


class AgentState(TypedDict):
    # Full conversation history (human + AI + tool results).
    # add_messages reducer appends new messages rather than replacing the list.
    messages: Annotated[list[BaseMessage], add_messages]

    # The user's raw search query (e.g. "project proposal follow-up")
    user_query: str

    # Up to 10 raw results from Gmail (metadata only)
    search_results: list[EmailResult]

    # Top results after relevance re-ranking
    ranked_emails: list[EmailResult]

    # The single email the user selected (or auto-selected if only 1 result)
    selected_email: Optional[SelectedEmail]

    # The current LLM-generated draft reply
    draft_reply: Optional[str]

    # True when the graph has paused for human confirmation before sending
    awaiting_send: bool

    # True only for the immediate turn after explicit approval in human_review
    # and consumed once create_gmail_draft runs.
    review_granted: bool

    # Number of re-draft iterations (guard against infinite modify loops)
    draft_attempts: int

    # Last error message (shown to user in plain English)
    error: Optional[str]

    # Global API retry counter
    retry_count: int

    # True when multiple search results exist and the graph should pause for selection
    selection_required: bool

    # True when user explicitly chose "Save as Draft" (don't send, just save to Gmail Drafts)
    force_save_draft: bool
