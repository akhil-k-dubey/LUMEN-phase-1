"""
notifications.py — Windows Action Center notification watcher.

Strategy:
  1. Copy wpndatabase.db to temp (Windows locks the original)
  2. Open copy as read-only SQLite
  3. Extract toast notifications newer than last_seen_id
  4. Parse sender + app + preview from XML payload
  5. Publish 'notification' events to event bus
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from event_bus import EventBus, LumenEvent

POLL_INTERVAL_S = 15

WATCHED_APPS = {
    "WhatsApp", "Telegram", "Discord", "Slack",
    "Microsoft.OutlookForWindows", "Mail", "Skype",
    "Microsoft Teams",
}


class NotificationWatcher:
    DB_PATH = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "Windows", "Notifications", "wpndatabase.db"
    )

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._last_seen_id = 0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Initialize last_seen_id to current max so we don't replay old notifs
        self._initialize_watermark()

    def _initialize_watermark(self) -> None:
        """Set last_seen_id to current max so startup doesn't replay old notifications."""
        try:
            if not os.path.exists(self.DB_PATH):
                return
            temp_db = os.path.join(tempfile.gettempdir(), "lumen_notif_init.db")
            shutil.copy2(self.DB_PATH, temp_db)
            conn = sqlite3.connect(f"file:{temp_db}?mode=ro", uri=True)
            cursor = conn.execute("SELECT MAX(Id) FROM Notification")
            row = cursor.fetchone()
            if row and row[0]:
                self._last_seen_id = row[0]
            conn.close()
            os.remove(temp_db)
        except Exception:
            pass

    def start(self) -> None:
        if not os.path.exists(self.DB_PATH):
            print("[notifications] wpndatabase.db not found — NotificationWatcher disabled.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="lumen-notifications"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll()
            except Exception as exc:
                print(f"[notifications] poll error: {exc}")
            self._stop_event.wait(timeout=POLL_INTERVAL_S)

    def _poll(self) -> None:
        temp_db = os.path.join(tempfile.gettempdir(), "lumen_notif.db")
        try:
            shutil.copy2(self.DB_PATH, temp_db)
        except (PermissionError, FileNotFoundError):
            return

        try:
            conn = sqlite3.connect(f"file:{temp_db}?mode=ro", uri=True)
            cursor = conn.execute(
                "SELECT Id, Type, Payload FROM Notification WHERE Id > ? ORDER BY Id",
                (self._last_seen_id,)
            )
            for row in cursor:
                notif_id, notif_type, payload_xml = row
                parsed = self._parse_toast(payload_xml)
                if parsed and parsed["app"] in WATCHED_APPS:
                    self._event_bus.publish(LumenEvent("notification", {
                        "app": parsed["app"],
                        "sender": parsed["sender"],
                        "preview": parsed["preview"],
                        "id": notif_id,
                     }, priority=1))
                self._last_seen_id = max(self._last_seen_id, notif_id)
            conn.close()
        except Exception:
            pass
        finally:
            try:
                os.remove(temp_db)
            except Exception:
                pass

    def _parse_toast(self, xml_payload: str) -> Optional[dict]:
        """Parse Windows toast XML to extract sender, app, and preview text."""
        if not xml_payload:
            return None
        try:
            root = ET.fromstring(xml_payload)
            texts = []
            for text_el in root.iter("text"):
                if text_el.text:
                    texts.append(text_el.text.strip())

            if not texts:
                return None

            # Heuristic: first text element is usually sender/title, rest is content
            sender = texts[0] if texts else "Unknown"
            preview = " ".join(texts[1:]) if len(texts) > 1 else texts[0]

            # Try to extract app name from launch attribute
            app = root.get("launch", "") or root.get("displayName", "") or "Unknown"
            for attr in ["launch", "activationType"]:
                val = root.get(attr, "")
                for watched in WATCHED_APPS:
                    if watched.lower() in val.lower():
                        app = watched
                        break

            return {"sender": sender, "preview": preview, "app": app}
        except ET.ParseError:
            return None
