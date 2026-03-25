import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000/api/chat"


def call(thread_id: str, message: str) -> dict:
    payload = {
        "message": message,
        "thread_id": thread_id,
        "model": "gpt-4o",
        "provider": "openai",
    }
    req = urllib.request.Request(
        BASE,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def summarize(label: str, data: dict) -> None:
    content = str((data.get("content") or data.get("response") or "")).replace("\n", " ")
    print(f"{label}: search={bool(data.get('searchResults'))} email={bool(data.get('emailCard'))} review={bool(data.get('reviewData'))} content={content[:120]}")


def run_flow(thread_id: str, search_msg: str):
    summarize("search", call(thread_id, search_msg))
    summarize("select", call(thread_id, "1"))
    summarize("draft", call(thread_id, "תנסח לי מענה"))
    summarize("modify", call(thread_id, "add noa in the end"))


if __name__ == "__main__":
    thread_a = f"reg-a-{int(time.time())}"
    run_flow(thread_a, "יש מייל מסויים על רשימת קניות?")

    summarize("save", call(thread_a, "_save_draft_"))

    thread_b = f"reg-b-{int(time.time())}"
    run_flow(thread_b, "יש מייל מסויים על רשימת קניות?")
    summarize("send", call(thread_b, "_send_draft_"))

    thread_c = f"reg-c-{int(time.time())}"
    run_flow(thread_c, "יש מייל מסויים על רשימת קניות?")
    summarize("reject", call(thread_c, "_reject_draft_"))
