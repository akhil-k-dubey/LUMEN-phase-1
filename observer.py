"""
observer.py — PassiveObserver daemon for LUMEN.

Single background thread polling:
  - 5s when user is active (keyboard/mouse input within 120s)
  - 30s when user is idle

Sub-trackers (all polled in _tick()):
  IdleDetector        — ctypes GetLastInputInfo, emits idle_start/idle_end
  WindowTracker       — win32gui foreground window, updates shared ScreenOracle
  CodingSessionTracker — detects struggle (5min same file), emits coding_struggle
  BuildWatcher        — detects error keywords in terminal titles, emits build_error
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Any

import win32gui
import win32process
import psutil


# ── Idle Detection via Win32 ──────────────────────────────────────────────

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


@dataclass
class WindowInfo:
    """Snapshot of the foreground window."""
    hwnd: int = 0
    title: str = ""
    process_name: str = ""
    is_coding: bool = False       # VSCode, PyCharm, IntelliJ, etc.
    is_browser: bool = False      # Chrome, Edge, Firefox, Brave
    is_terminal: bool = False     # cmd, PowerShell, Windows Terminal
    is_leetcode: bool = False     # LeetCode detected (title or URL)
    changed: bool = False         # True if window changed since last poll
    url: str = ""                 # Browser URL (extracted via UIA when available)

_CODING_PROCS = {"Code.exe", "code.exe", "pycharm64.exe", "idea64.exe",
                 "devenv.exe", "sublime_text.exe", "notepad++.exe"}
_BROWSER_PROCS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
                  "opera.exe", "vivaldi.exe"}
_TERMINAL_PROCS = {"cmd.exe", "powershell.exe", "WindowsTerminal.exe",
                   "pwsh.exe", "wt.exe"}


class IdleDetector:
    IDLE_THRESHOLD_S = 120  # 2 minutes

    def __init__(self, event_bus):
        self._event_bus = event_bus
        self._was_idle = False
        self._idle_start_time = 0.0

    def poll(self) -> bool:
        """Returns True if user is idle (no input for >120s)."""
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))

        tick_count = ctypes.windll.kernel32.GetTickCount()
        elapsed_ms = tick_count - lii.dwTime
        # Handle tick count wraparound (~49 days)
        if elapsed_ms < 0:
            elapsed_ms += 0xFFFFFFFF
        is_idle = elapsed_ms > (self.IDLE_THRESHOLD_S * 1000)

        # Detect transitions
        if not self._was_idle and is_idle:
            self._idle_start_time = time.time()
            from event_bus import LumenEvent
            self._event_bus.publish(LumenEvent("idle_start", {}))
        elif self._was_idle and not is_idle:
            duration = time.time() - self._idle_start_time
            from event_bus import LumenEvent
            self._event_bus.publish(LumenEvent(
                "idle_end", {"idle_duration_s": int(duration)}, priority=1
            ))

        self._was_idle = is_idle
        return is_idle


class WindowTracker:
    def __init__(self):
        self._last_hwnd = 0
        self._last_title = ""

    def poll(self) -> WindowInfo:
        """Read foreground window. ONE call per tick, shared by all trackers."""
        info = WindowInfo()
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return info
            info.hwnd = hwnd
            info.title = win32gui.GetWindowText(hwnd) or ""
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid:
                    info.process_name = psutil.Process(pid).name()
            except Exception:
                pass
        except Exception:
            return info

        # Classify window type
        info.is_coding = info.process_name in _CODING_PROCS
        info.is_browser = info.process_name in _BROWSER_PROCS
        info.is_terminal = info.process_name in _TERMINAL_PROCS

        # Detect LeetCode from title
        title_lower = info.title.lower()
        if "leetcode" in title_lower or "- leet" in title_lower:
            info.is_leetcode = True

        # Extract browser URL via UIA (for LeetCode URL detection)
        if info.is_browser:
            info.url = self._extract_browser_url(hwnd)
            if "leetcode.com/problems/" in info.url.lower():
                info.is_leetcode = True

        # Detect window change
        info.changed = (hwnd != self._last_hwnd or info.title != self._last_title)
        self._last_hwnd = hwnd
        self._last_title = info.title

        return info

    def _extract_browser_url(self, hwnd: int) -> str:
        """Extract URL from browser address bar via UI Automation."""
        try:
            import uiautomation as auto
            control = auto.ControlFromHandle(hwnd)
            if not control:
                return ""
            # Chrome/Edge: address bar is an Edit control with AutomationId "addressEditBox"
            # or name containing "Address and search bar"
            edit = control.EditControl(searchDepth=8, AutomationId="addressEditBox")
            if edit and edit.Exists(0.3):
                return edit.GetValuePattern().Value or ""
            # Firefox fallback: toolbar → combo box
            for toolbar in control.GetChildren():
                if "Navigation" in (toolbar.Name or ""):
                    combo = toolbar.ComboBoxControl(searchDepth=3)
                    if combo and combo.Exists(0.2):
                        edit2 = combo.EditControl()
                        if edit2 and edit2.Exists(0.2):
                            return edit2.GetValuePattern().Value or ""
        except Exception:
            pass
        return ""


class CodingSessionTracker:
    STRUGGLE_THRESHOLD_S = 300   # 5 minutes on same file/problem
    HINT_COOLDOWN_S = 600        # 10 minutes between hints for same context

    def __init__(self, event_bus, brain):
        self._event_bus = event_bus
        self._brain = brain
        self._last_context = ""
        self._context_start_time = 0.0
        self._last_hint_time = 0.0
        self._hint_count_for_context = 0

    def update(self, window: WindowInfo, is_idle: bool) -> None:
        if is_idle or not (window.is_coding or window.is_leetcode):
            self._reset()
            return

        current_context = window.title
        if window.is_leetcode and window.url:
            current_context = window.url  # More specific than title

        if current_context == self._last_context:
            elapsed = time.time() - self._context_start_time
            if elapsed > self.STRUGGLE_THRESHOLD_S and self._can_hint():
                self._emit_struggle(current_context, window)
        else:
            self._last_context = current_context
            self._context_start_time = time.time()
            self._hint_count_for_context = 0

    def _can_hint(self) -> bool:
        return (time.time() - self._last_hint_time) > self.HINT_COOLDOWN_S

    def _emit_struggle(self, context: str, window: WindowInfo) -> None:
        from event_bus import LumenEvent
        self._event_bus.publish(LumenEvent("coding_struggle", {
            "context": context,
            "window_title": window.title,
            "is_leetcode": window.is_leetcode,
            "hint_level": self._hint_count_for_context,
        }, priority=0))
        self._last_hint_time = time.time()
        self._hint_count_for_context += 1

    def _reset(self) -> None:
        self._last_context = ""
        self._context_start_time = 0.0


class BuildWatcher:
    ERROR_PATTERNS = ["error:", "traceback", "build failed", "compilation failed",
                      "syntaxerror", "failed to compile", "exception"]
    COOLDOWN_S = 120  # Don't repeat for same terminal within 2 min

    def __init__(self, event_bus):
        self._event_bus = event_bus
        self._last_alert_time: dict[int, float] = {}

    def update(self, window: WindowInfo) -> None:
        if not window.is_terminal:
            return
        title_lower = window.title.lower()
        if any(p in title_lower for p in self.ERROR_PATTERNS):
            now = time.time()
            last = self._last_alert_time.get(window.hwnd, 0.0)
            if now - last > self.COOLDOWN_S:
                self._last_alert_time[window.hwnd] = now
                from event_bus import LumenEvent
                self._event_bus.publish(LumenEvent("build_error", {
                    "title": window.title
                }, priority=1))


class PassiveObserver:
    """
    Unified background daemon. Polls every 5s (active) or 30s (idle).
    All sub-trackers share ONE window read per cycle.
    """
    ACTIVE_INTERVAL_S = 5
    IDLE_INTERVAL_S = 30

    def __init__(self, event_bus, brain, screen_oracle=None):
        self._event_bus = event_bus
        self._brain = brain
        self._screen_oracle = screen_oracle  # Shared with brain

        self._idle_detector = IdleDetector(event_bus)
        self._window_tracker = WindowTracker()
        self._coding_tracker = CodingSessionTracker(event_bus, brain)
        self._build_watcher = BuildWatcher(event_bus)
        self._content_ingester: Optional[Any] = None  # set externally if needed

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="lumen-observer"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                is_idle = self._tick()
            except Exception as exc:
                print(f"[observer] tick error: {exc}")
                is_idle = False

            interval = self.IDLE_INTERVAL_S if is_idle else self.ACTIVE_INTERVAL_S
            self._stop_event.wait(timeout=interval)

    def _tick(self) -> bool:
        # 1. Check idle state
        is_idle = self._idle_detector.poll()

        # 2. Get foreground window (ONE read, shared by all)
        window = self._window_tracker.poll()

        # 3. Update shared ScreenOracle (so brain.py sees current window)
        if self._screen_oracle and window.title:
            self._screen_oracle.update(window)

        # 4. Feed to coding tracker
        self._coding_tracker.update(window, is_idle)

        # 5. Check for build errors
        self._build_watcher.update(window)

        # 6. Feed to content ingester if present
        if self._content_ingester:
            self._content_ingester.update(window, is_idle)

        return is_idle
