import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("OPENAI_API_KEY")
print(f"Testing key ending in: {key[-4:] if key else 'None'}")
print(f"Key length: {len(key) if key else 0}")

client = OpenAI(api_key=key)

try:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "say hi"}],
        max_tokens=5
    )
    print("SUCCESS:", response.choices[0].message.content)
except Exception as e:
    print("FAILED:", str(e))
