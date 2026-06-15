"""
voice_lock.py — Global speaking mutex for LUMEN.

Every code path that produces audio (voice.speak, voice.speak_alarm,
proactive hints, notification dialogs) MUST acquire this lock first.
This guarantees only one subsystem speaks at any given moment.
"""
import threading

class VoiceLock:
    """
    Global speaking lock. Acquire before any TTS output.
    
    Usage:
        with voice_lock.acquire_speaking():
            voice.speak("Hello sir")
    
    If another subsystem is already speaking, the caller blocks
    until the lock is released (FIFO ordering via threading.Lock).
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._current_speaker: str = ""
    
    def acquire_speaking(self, speaker: str = ""):
        """Context manager that holds the speaking lock."""
        class _SpeakContext:
            def __init__(ctx):
                ctx.speaker = speaker
            def __enter__(ctx):
                self._lock.acquire()
                self._current_speaker = ctx.speaker
                return ctx
            def __exit__(ctx, *args):
                self._current_speaker = ""
                self._lock.release()
        return _SpeakContext()
    
    @property
    def is_speaking(self) -> bool:
        return self._lock.locked()
    
    @property
    def current_speaker(self) -> str:
        return self._current_speaker

# Singleton — imported by voice.py, event handlers, coding companion, etc.
VOICE_LOCK = VoiceLock()
