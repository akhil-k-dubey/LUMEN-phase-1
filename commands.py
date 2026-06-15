"""
commands.py — Wake word and shutdown command detection.

Upgrades:
  - More natural shutdown phrases
  - More wake word variants
  - Fuzzy matching with better thresholds
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re


def is_shutdown_command(text: str) -> bool:
    """Detect shutdown intent from user input.

    Whisper frequently appends trailing punctuation to short commands
    (e.g. "Shut down." instead of "Shut down"). Strip it before matching
    so these are never silently missed.
    """
    # Strip trailing punctuation that Whisper/STT may append
    normalized = re.sub(r'[.!?,;:]+$', '', text.strip().lower()).strip()
    # If wake up is in the input, avoid shutting down (multi-clause ambiguity)
    if "wake up" in normalized:
        return False
    # Exact matches (excluding sleep commands, which are handled separately)
    if normalized in {
        "exit", "quit", "shutdown", "shut down",
        "power off", "power down", "goodbye", "good bye",
        "bye lumen", "bye bye",
        "turn off", "end session",
        # Common voice variants
        "shut down lumen", "shutdown lumen",
        "goodbye lumen", "stop lumen",
    }:
        return True
    # Partial matches — use clause-boundary detection instead of word count.
    shutdown_phrases = {"shutdown", "shut down", "power off", "power down"}
    clause_delimiters = r'(?:^|[.!?,;:]|\b(?:and|but|or|so|then)\b)\s*'
    for phrase in shutdown_phrases:
        # Require phrase at end-of-string (optionally followed by "lumen")
        pattern = clause_delimiters + re.escape(phrase) + r'(?:\s+lumen)?\s*$'
        if re.search(pattern, normalized, re.IGNORECASE):
            return True
    return False


def is_sleep_command(text: str) -> bool:
    """Detect sleep/standby intent from user input."""
    normalized = re.sub(r'[.!?,;:]+$', '', text.strip().lower()).strip()
    return normalized in {
        "go to sleep", "sleep", "standby", "stand by",
        "stop listening", "sleep lumen", "go to sleep lumen"
    }


def is_wake_command(text: str, assistant_name: str) -> bool:
    """Detect wake word from user input.

    Checks if a wake verb (wake, hey, hello, hi, yo, okay, wakeup)
    is followed by a name alias (lumen, loomin, human, etc.) within 3 tokens.
    """
    normalized = text.strip().lower()
    cleaned = re.sub(r"[^a-z0-9\s']", " ", normalized)
    tokens = re.sub(r"\s+", " ", cleaned).strip().split()
    if not tokens:
        return False

    name = assistant_name.lower()
    wake_verbs = {"wake", "hey", "hello", "hi", "yo", "okay", "wakeup"}
    
    # ── Known Whisper misheard aliases for "Lumen" ──
    _WHISPER_ALIASES = {
        "human", "loomin", "loom in", "loomian", "lumon", "lumine",
        "lumin", "looming", "illuminate", "blooming", "lemon", "loam",
        "lumen's", "lumens", "luma", "lumina",
    }

    # ── Bare "wake up" (no name) — always treated as wake command ──
    if len(tokens) == 1 and tokens[0] in {"wakeup", "wake"}:
        return True
    if len(tokens) == 2 and tokens[0] == "wake" and tokens[1] == "up":
        return True

    def is_alias(idx: int) -> bool:
        if idx < 0 or idx >= len(tokens):
            return False
        word = tokens[idx]
        if word in _WHISPER_ALIASES or word == name:
            return True
        if word.endswith("'s") and word[:-2] in {"lumen", "human", "loomin", "lumon", "lumin"}:
            return True
        if SequenceMatcher(None, word, name).ratio() >= 0.60:
            return True
        # Check for multi-word alias "loom in"
        if word == "loom" and idx + 1 < len(tokens) and tokens[idx+1] == "in":
            return True
        return False

    for i in range(len(tokens)):
        if tokens[i] in wake_verbs:
            for offset in range(1, 4):
                if is_alias(i + offset):
                    return True

    return False
