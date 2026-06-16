**LUMEN** (Local User Machine Executive Network) is an advanced, 100% local AI assistant designed to run locally with high-performance voice processing, screen vision, and workflow automation. 

Here are the key abilities and components that define LUMEN:

---

### 1. Ultra-Low Latency Voice Pipeline
* **Local Neural STT**: Uses a custom Silero VAD (Voice Activity Detection) stream combined with `faster-whisper` for fast local speech-to-text.
* **Kokoro Neural TTS**: Uses a local `kokoro-v1.0.onnx` backend to generate high-fidelity speech (50–150ms latency) without external API dependencies. Supports custom emotional voice blending.
* **Barge-in Interruption**: A dual-gate monitor (Silero VAD + dynamic RMS baseline tracking) lets you interrupt LUMEN mid-sentence. When you start speaking, she cuts her audio playback instantly and captures your new command.
* See: [voice.py](file:///C:/Users/akhil/Downloads/LUMEN/voice.py) and [ear.py](file:///C:/Users/akhil/Downloads/LUMEN/ear.py).

### 2. Multi-Tier Screen Vision
LUMEN can "see" and scrape your active window context using a 5-tier pipeline:
* **Tier 0 (Playwright DOM Scraping)**: Extract clean, structured text, chat histories, or code blocks from active browser tabs (Edge, Chrome, Brave, Firefox) for supported sites like Claude.ai, LeetCode, GitHub, and StackOverflow.
* **Tier 1 (Windows UIA)**: Directly queries Windows UI Automation, searching for `DocumentControl` elements and using `TextPattern` or UIA tree walks to read text from active desktop applications.
* **Tier 2 (Process File Tracking)**: Inspects the active process via `psutil` to resolve open file handles, reading the source code of the active file directly from the filesystem.
* **Tier 3 (MSS viewport + OCR)**: Takes a crop of the focused window coordinates and uses `pytesseract` OCR with edit-distance spelling correction.
* See: [skills/system.py](file:///C:/Users/akhil/Downloads/LUMEN/skills/system.py) and [web_agent.py](file:///C:/Users/akhil/Downloads/LUMEN/web_agent.py).

### 3. Proactive Coding Buddy
* **State Detection**: Monitors your active editor workspace (VS Code, PyCharm, etc.) and performs AST-based fingerprinting.
* **Jaccard Similarity Tracking**: Computes similarity scores between code snapshots over time to detect if you are stuck (*Staring State*) or struggling (*Repetitive Edits*).
* **Comment Injection**: Generates proactive tips and uses `libcst` (Concrete Syntax Trees) or regex fallbacks to write hints directly into your source code as comments, speaking guidance concurrently.
* See: [coding_buddy.py](file:///C:/Users/akhil/Downloads/LUMEN/coding_buddy.py) and [coding_companion.py](file:///C:/Users/akhil/Downloads/LUMEN/coding_companion.py).

### 4. Zero-Latency Skill Engine & Intent Router
LUMEN bypasses LLM latency entirely for clear desktop commands using a regex-based intent matcher. These tasks execute instantly:
* **WhatsApp Integration**: Sends messages and checks unread notifications using a Playwright bridge.
* **App Control**: Opens or closes apps/tabs (including smart routing of website links).
* **Memory Management**: Remembers specific facts ("Remember my keys are on the desk") and recalls them on demand.
* **System Actions**: Screenshotting, system diagnostics, volume adjustment, timers, reminders, and clipboard manipulation.
* See: [skills/intent.py](file:///C:/Users/akhil/Downloads/LUMEN/skills/intent.py) and [commands.py](file:///C:/Users/akhil/Downloads/LUMEN/commands.py).

### 5. Agentic Multi-Step Task Loop
* For complex requests, LUMEN shifts from single-turn chat into an autonomous **Plan $\rightarrow$ Execute $\rightarrow$ Verify $\rightarrow$ Done** loop. She writes and runs Python scripts locally, issues PowerShell commands within a security sandbox, inspects state changes, and coordinates with you for validation before performing destructive actions.
* See: [agentic.py](file:///C:/Users/akhil/Downloads/LUMEN/agentic.py).

### 6. Event Bus & Proactive Greetings
* **Session Topic Greeting**: Computes recency-weighted TF-IDF terms from your past conversation turns. Upon startup, she extracts your last active task topic and greets you proactively, asking if you want to resume it.
* **Priority Event Bus**: An asynchronous event loop handles passive environment observations, struggle events, and alarms in strict priority order (preventing data races or comparison errors).
* See: [memory.py](file:///C:/Users/akhil/Downloads/LUMEN/memory.py) and [event_bus.py](file:///C:/Users/akhil/Downloads/LUMEN/event_bus.py).
