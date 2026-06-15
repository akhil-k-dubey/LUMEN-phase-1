"""
learning.py — Technical content ingestion engine for LUMEN.

Activates when:
  1. User is in a browser window
  2. Page has been focused for >30 seconds (dwell time gate)
  3. Content matches technical keywords

Ingestion runs in a SEPARATE THREAD (not inside observer._tick())
to avoid blocking the observer with skill_read_screen() latency.
"""
from __future__ import annotations

import threading
import time
from typing import Optional, Any

TECH_KEYWORDS = frozenset({
    "algorithm", "documentation", "api", "function", "class", "method",
    "neural", "quantum", "compiler", "database", "protocol", "architecture",
    "design pattern", "data structure", "machine learning", "deep learning",
    "python", "javascript", "rust", "golang", "kubernetes", "docker",
    "arxiv", "github", "stackoverflow", "tutorial", "reference",
})

MIN_WORDS = 200
DWELL_TIME_S = 30
INGEST_COOLDOWN_S = 300  # Max once per 5 minutes


class ContentIngester:
    def __init__(self, brain, persistent_memory, event_bus):
        self._brain = brain
        self._memory = persistent_memory
        self._event_bus = event_bus

        self._current_page = ""
        self._dwell_start = 0.0
        self._last_ingest_time = 0.0
        self._ingest_thread: Optional[threading.Thread] = None

    def update(self, window_info, is_idle: bool) -> None:
        """Lightweight update called by observer each tick. Never blocks."""
        if not window_info.is_browser or is_idle:
            self._reset_dwell()
            return

        page_id = window_info.title

        if page_id == self._current_page:
            dwell = time.time() - self._dwell_start
            if dwell > DWELL_TIME_S and self._can_ingest():
                self._spawn_ingest(page_id)
        else:
            self._current_page = page_id
            self._dwell_start = time.time()

    def _can_ingest(self) -> bool:
        if (time.time() - self._last_ingest_time) < INGEST_COOLDOWN_S:
            return False
        # Don't spawn if previous ingest is still running
        if self._ingest_thread and self._ingest_thread.is_alive():
            return False
        return True

    def _spawn_ingest(self, page_title: str) -> None:
        """Spawn a daemon thread for ingestion. NEVER blocks observer._tick()."""
        self._last_ingest_time = time.time()
        self._ingest_thread = threading.Thread(
            target=self._do_ingest,
            args=(page_title,),
            daemon=True,
            name="lumen-ingest",
        )
        self._ingest_thread.start()

    def _do_ingest(self, page_title: str) -> None:
        """Runs in separate thread. Safe to call slow functions."""
        try:
            from skills import skill_read_screen
            content = skill_read_screen("")
        except Exception:
            return

        if not content or not isinstance(content, str):
            return

        # Check technical relevance
        lower = content.lower()
        if not any(kw in lower for kw in TECH_KEYWORDS):
            return

        if len(content.split()) < MIN_WORDS:
            return

        # Summarize with fast model (blocking call OK — we're in our own thread)
        prompt = f"Summarize this technical page in 2-3 sentences:\n\n{content[:2000]}"
        try:
            summary = self._brain.quick_generate(prompt, max_tokens=80)
        except Exception:
            return

        if not summary:
            return

        # Store in memory
        self._memory.save_fact(
            f"reading_{int(time.time())}",
            f"[{page_title}] {summary}"
        )

        from event_bus import LumenEvent
        self._event_bus.publish(LumenEvent("content_ingested", {
            "title": page_title,
            "summary": summary,
        }))
        print(f"[learning] Ingested: {page_title[:60]}")

    def _reset_dwell(self) -> None:
        self._current_page = ""
        self._dwell_start = 0.0
