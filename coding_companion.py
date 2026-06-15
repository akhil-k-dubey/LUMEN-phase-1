"""
coding_companion.py — Progressive coding hint system for LUMEN.

Escalation levels:
  Level 0: Conceptual hint (fast model, 60 tokens) — "Think about using a hash map"
  Level 1: Pseudocode (deep model, 120 tokens)     — Step-by-step approach
  Level 2: Full solution (deep model, 200 tokens)   — Complete code with explanation

Each level has a 10-minute cooldown per problem context.
"""
from __future__ import annotations

from voice_lock import VOICE_LOCK
from observer import WindowTracker
from web_agent import get_web_agent

ESCALATION_LEVELS = [
    ("conceptual", 60,
     "The user has been stuck on this for 5+ minutes. "
     "Give ONE short conceptual hint. No code. Max 2 sentences."),
    ("pseudocode", 120,
     "The user is still stuck after a conceptual hint. "
     "Write brief pseudocode for the approach. No full implementation."),
    ("solution", 200,
     "The user has been stuck for a long time despite hints. "
     "Write a clean solution with a 1-sentence explanation."),
]


class CodingCompanion:
    def __init__(self, brain, announce_fn):
        self._brain = brain
        self._announce_fn = announce_fn

    def handle_struggle(self, event) -> None:
        """Event handler for 'coding_struggle' events."""
        # 1. Guard with window.is_browser check
        window = WindowTracker().poll()
        if not window.is_browser:
            return

        web_agent = get_web_agent()
        if not web_agent:
            return

        # 2. Use Playwright DOM bridge
        page_info = web_agent.extract_page_content()
        if not page_info:
            return
        
        page_url = page_info.get("url", "")
        hint_level = min(event.data.get("hint_level", 0), len(ESCALATION_LEVELS) - 1)
        level_name, max_tokens, instruction = ESCALATION_LEVELS[hint_level]

        blocks = page_info.get("content_blocks", [])
        if blocks:
            narrowed = self._brain.narrow_context(instruction, blocks, top_k=3)
            page_content = "\n\n".join(f"{b.get('label', 'Content')}:\n{b.get('text', '')}" for b in narrowed)
        else:
            page_content = page_info.get("raw", page_info.get("content", ""))


        # 3. Build structured prompt and query deep model
        system_prompt = (
            "You are a coding companion voice assistant. "
            "You must respond with a JSON object containing two keys:\n"
            "1. 'hint': A string containing the message to speak to the user.\n"
            "2. 'code': An optional string containing the code to be inserted into the editor. "
            "Only provide code if the hint level is 1 or 2, and keep the hint extremely concise."
        )

        prompt = (
            f"Hint Level: {hint_level} ({level_name})\n"
            f"Target Instruction: {instruction}\n"
            f"Current Page URL: {page_url}\n"
            f"Page Content:\n{page_content}\n\n"
            f"Generate a helpful JSON response."
        )

        res_json = self._brain.generate_json(prompt, system=system_prompt, force_deep=True)
        hint = res_json.get("hint", "")
        code = res_json.get("code", "")

        if hint:
            prefix = {
                "conceptual": "Sir, here's a thought.",
                "pseudocode": "Sir, let me sketch an approach.",
                "solution": "Sir, here's how I'd solve it.",
            }[level_name]

            with VOICE_LOCK.acquire_speaking("coding_companion"):
                self._announce_fn(f"{prefix} {hint}")

        # 4. Insert code for level 1+ hints if code is present
        if hint_level >= 1 and code:
            web_agent.leetcode_insert_code(code)
