"""
event_bus.py — Publish-subscribe event bus for LUMEN proactive subsystems.

Priority Semantics:
  0 = normal   — Queued FIFO. Coding hints, content ingested, etc.
  1 = high     — Jumps ahead of all priority-0 events in the queue.
                  Used for notifications ("new message from Jay").
  2 = urgent   — Jumps ahead of everything. Interrupts current TTS
                  playback by calling voice.stop_speaking() before
                  the handler executes. Used for system alerts
                  ("CPU at 95%", "battery at 10%").

The bus does NOT own a TTS queue. It dispatches to handler functions.
Handlers that need to speak acquire VoiceLock themselves.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Any

@dataclass(order=False)
class LumenEvent:
    type: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    priority: int = 0   # 0=normal, 1=high, 2=urgent

    def __lt__(self, other):
        """Lower priority number = higher priority in PriorityQueue."""
        # Invert: urgent (2) should come first, so compare as -priority
        if self.priority != other.priority:
            return self.priority > other.priority  # higher int = dequeued first
        return self.timestamp < other.timestamp     # FIFO within same priority

class EventBus:
    def __init__(self, voice_stop_fn: Optional[Callable] = None):
        self._handlers: dict[str, list[Callable]] = {}
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._voice_stop_fn = voice_stop_fn  # voice.stop_speaking() for urgent interrupts
        self._counter = itertools.count()
    
    def subscribe(self, event_type: str, handler: Callable[[LumenEvent], None]) -> None:
        self._handlers.setdefault(event_type, []).append(handler)
    
    def publish(self, event: LumenEvent) -> None:
        """Non-blocking enqueue. Safe to call from any thread."""
        self._queue.put((-event.priority, next(self._counter), event))
    
    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._dispatch_loop, daemon=True, name="lumen-event-bus"
        )
        self._thread.start()
    
    def stop(self) -> None:
        self._stop_event.set()
        # Unblock the queue.get() with a poison pill
        self._queue.put((-99, next(self._counter), LumenEvent("_shutdown", priority=99)))
    
    def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                _, _, event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            
            if event.type == "_shutdown":
                break
            
            # Priority 2 (urgent): interrupt current speech first
            if event.priority >= 2 and self._voice_stop_fn:
                try:
                    self._voice_stop_fn()
                except Exception:
                    pass
            
            # Dispatch to all registered handlers
            for handler in self._handlers.get(event.type, []):
                try:
                    handler(event)
                except Exception as exc:
                    print(f"[event-bus] handler error for '{event.type}': {exc}")
