"""
CLI chatbot powered by Gemini 2.5 Flash.

Supports three conversation modes (technical / learning / casual)
and maintains a rolling conversation history.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import Optional

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, CHAT_HISTORY_CAP

# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------
if not GEMINI_API_KEY:
    print("Error: GEMINI_API_KEY not set. Run: export GEMINI_API_KEY='your_key'")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
mode: str = "technical"
conversation_history: list[dict] = []
message_count: int = 0

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
BASE_PROMPT = """You are a helpful personal assistant running on a Raspberry Pi Zero 2.
You are concise, friendly, and precise.
When asked technical questions, especially about Python, ML, or Raspberry Pi,
prioritize practical and lightweight solutions."""

MODE_INSTRUCTIONS: dict[str, str] = {
    "technical": "Respond with code and commands only. Keep it short.",
    "learning": "Explain briefly with examples. Max 150 words.",
    "casual": "Be short and friendly. Max 100 words.",
}


def _build_prompt() -> str:
    today = datetime.now().strftime("%A, %d %B %Y, %H:%M")
    return f"{BASE_PROMPT}\nCurrent date and time: {today}.\n{MODE_INSTRUCTIONS[mode]}"


# ---------------------------------------------------------------------------
# Core chat function
# ---------------------------------------------------------------------------

def chat(user_input: str) -> str:
    global message_count  # <-- BUG FIX: was missing, making the increment a no-op

    conversation_history.append({"role": "user", "parts": [{"text": user_input}]})

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                config=types.GenerateContentConfig(
                    system_instruction=_build_prompt(),
                    max_output_tokens=200,
                ),
                contents=conversation_history[-CHAT_HISTORY_CAP:],
            )
            break
        except Exception as exc:
            if "503" in str(exc) and attempt < 2:
                print(f"Server busy, retrying… ({attempt + 1}/3)")
                time.sleep(2)
            else:
                raise

    reply = response.text or "No response"
    conversation_history.append({"role": "model", "parts": [{"text": reply}]})
    message_count += 1
    return reply


# ---------------------------------------------------------------------------
# History display
# ---------------------------------------------------------------------------

def show_history() -> None:
    if not conversation_history:
        print("\nNo conversation yet.\n")
        return
    print("\n--- Conversation History ---")
    for msg in conversation_history:
        role = "You" if msg["role"] == "user" else "Gemini"
        print(f"{role}: {msg['parts'][0]['text']}\n")
    print("----------------------------\n")


# ---------------------------------------------------------------------------
# CLI main loop
# ---------------------------------------------------------------------------

def main() -> None:
    global mode, message_count

    print("Commands: 'q' to exit | 'history' | 'clear' | '/mode technical|learning|casual'\n")
    print("Hello, Sir\n")

    while True:
        user_input = input(f"[{mode}] You: ").strip()

        if not user_input:
            continue

        if user_input.lower() == "q":
            print(f"Cheerio! {message_count} messages sent.")
            return

        if user_input.lower() == "clear":
            conversation_history.clear()
            message_count = 0
            print("Conversation cleared!\n")
            continue

        if user_input.lower() == "history":
            show_history()
            continue

        if user_input.startswith("/mode"):
            parts = user_input.split()
            if len(parts) < 2:
                print(f"Usage: /mode {'|'.join(MODE_INSTRUCTIONS)}\n")
                continue
            new_mode = parts[1]
            if new_mode in MODE_INSTRUCTIONS:
                mode = new_mode
                print(f"Mode changed to: {mode}\n")
            else:
                print(f"Invalid mode. Choose: {', '.join(MODE_INSTRUCTIONS)}\n")
            continue

        try:
            reply = chat(user_input)
            print(f"\nGemini: {reply}\n")
        except Exception as exc:
            print(f"Error: {exc}\n")


if __name__ == "__main__":
    main()
