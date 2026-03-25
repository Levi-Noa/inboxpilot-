import os
from dotenv import load_dotenv
load_dotenv()

from agent.graph import build_graph
from langchain_core.messages import HumanMessage

print(f"DEBUG: Key length: {len(os.getenv('OPENAI_API_KEY', ''))}")
print(f"DEBUG: Key suffix: {os.getenv('OPENAI_API_KEY', '')[-4:]}")

graph = build_graph()

# Simulate a search query that triggers the 401 in the app
inputs = {
    "messages": [HumanMessage(content="מקדונלדס")]
}

config = {"configurable": {"thread_id": "test_auth"}}

try:
    print("Invoking graph...")
    for chunk in graph.stream(inputs, config=config):
        print("Chunk:", chunk)
except Exception as e:
    print("GRAPH FAILED:", str(e))
