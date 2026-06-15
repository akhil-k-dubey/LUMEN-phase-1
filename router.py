"""
router.py — Three-gate routing for LUMEN input dispatch.

Gate 0 (Zero):  detect_direct_intent()  — regex, 0ms, 0 VRAM
Gate 1 (Fast):  qwen2.5:1.5b            — ~200ms, 1.2GB VRAM
Gate 2 (Deep):  qwen3:4b                — ~1s, 2.8GB VRAM (loaded on demand)

Brain stays a pure LLM interface. Router decides WHICH model to use.
"""
from __future__ import annotations
import re
from typing import Optional

# Gate 2 trigger keywords — require deep reasoning model
_DEEP_KEYWORDS = frozenset({
    "explain", "why", "how does", "debug", "fix", "review",
    "compare", "difference", "algorithm", "optimize", "analyze",
    "architecture", "design", "implement", "proof", "calculate",
    "write a function", "write code", "refactor", "what's wrong",
    "step by step", "trace through", "complexity", "deep", "thinking",
})

# Destructive action pattern check for messages, file modifications, and code runs
_DESTRUCTIVE_PATTERNS = re.compile(
    r"\b(send|forward|reply)\b.*\b(message|text|whatsapp|sms|email)s?\b|"
    r"\b(delete|clear|remove|erase|wipe|purge)\b.*\b(message|chat|history|convo|conversation)s?\b|"
    r"\b(modify|change|write|edit|delete|remove|create|overwrite|update|rename)\b.*\bfile\b|"
    r"\b(run|execute|evaluate)\b.*\b(code|command|script|query)s?\b",
    re.IGNORECASE
)

def classify_depth(text: str) -> str:
    """
    Classify user input into routing tier.
    
    Returns:
        "zero" — Direct intent detected, bypass LLM entirely
        "fast" — Simple query, use qwen2.5:1.5b (always loaded)
        "deep" — Complex reasoning, use qwen3:4b (loaded on demand)
    """
    # Gate 0: Direct intent (0ms, 0 VRAM)
    from skills import detect_direct_intent
    if detect_direct_intent(text) is not None:
        return "zero"
    
    # Force matching destructive patterns to route to "deep"
    if _DESTRUCTIVE_PATTERNS.search(text):
        return "deep"
    
    # Gate 2: Deep reasoning keywords
    lower = text.lower()
    if any(kw in lower for kw in _DEEP_KEYWORDS):
        return "deep"
    
    # Gate 1: Everything else → fast model
    return "fast"
