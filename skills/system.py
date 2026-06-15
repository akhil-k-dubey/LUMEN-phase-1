"""
skills/system.py — System info, diagnostics, screenshot, and screen reading.
"""
from __future__ import annotations

import datetime
import os
import platform
import subprocess
import threading

from ._utils import _collect_text


def skill_system_info(args: str) -> str:
    """Get system information: CPU, RAM, disk, battery."""
    try:
        import psutil
    except ImportError:
        return _system_info_basic()

    parts = []
    cpu_pct = psutil.cpu_percent(interval=0.5)
    parts.append(f"CPU usage is {cpu_pct} percent")

    mem = psutil.virtual_memory()
    used_gb = mem.used / (1024 ** 3)
    total_gb = mem.total / (1024 ** 3)
    parts.append(f"RAM: {used_gb:.1f} of {total_gb:.1f} GB used ({mem.percent} percent)")

    disk = psutil.disk_usage("C:\\")
    free_gb = disk.free / (1024 ** 3)
    parts.append(f"C drive: {free_gb:.0f} GB free")

    bat = psutil.sensors_battery()
    if bat:
        plug = "plugged in" if bat.power_plugged else "on battery"
        parts.append(f"Battery: {bat.percent} percent, {plug}")

    # GPU / VRAM via nvidia-smi (graceful fallback if unavailable)
    try:
        nv = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if nv.returncode == 0 and nv.stdout.strip():
            gpu_line = nv.stdout.strip().split("\n")[0]
            gpu_parts = [p.strip() for p in gpu_line.split(",")]
            if len(gpu_parts) >= 4:
                parts.append(
                    f"GPU: {gpu_parts[0]}, VRAM {gpu_parts[1]} of "
                    f"{gpu_parts[2]} MB used, utilization {gpu_parts[3]} percent"
                )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # nvidia-smi not installed or failed — skip GPU info

    return ". ".join(parts) + "."


def _system_info_basic() -> str:
    """Fallback system info without psutil."""
    info = platform.uname()
    return (
        f"System: {info.system} {info.release}, "
        f"Machine: {info.machine}, "
        f"Processor: {info.processor}."
    )


def skill_system_diagnostics(args: str = "") -> str:
    """Check system health, including CPU, RAM, Disk, Battery, and GPU usage."""
    import psutil
    import shutil

    try:
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        disk = shutil.disk_usage("C:\\")

        battery_str = "N/A"
        if hasattr(psutil, "sensors_battery"):
            bat = psutil.sensors_battery()
            if bat:
                battery_str = f"{bat.percent}% ({'Plugged In' if bat.power_plugged else 'Discharging'})"

        gpu_str = "N/A"
        try:
            res = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=1)
            if res.returncode == 0:
                parts = res.stdout.strip().split(",")
                if len(parts) == 2:
                    gpu_str = f"{parts[0]}% Utilization, Temp: {parts[1]}°C"
        except Exception:
            pass

        return (
            f"System Diagnostics:\n"
            f"- CPU: {cpu}%\n"
            f"- RAM: {ram.percent}% (Used: {ram.used // (1024**2)}MB, Free: {ram.available // (1024**2)}MB)\n"
            f"- Disk C:: {disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB used ({100 - int(disk.free/disk.total*100)}%)\n"
            f"- Battery: {battery_str}\n"
            f"- GPU: {gpu_str}"
        )
    except Exception as exc:
        return f"Diagnostics failed: {exc}"


def skill_screenshot(args: str) -> str:
    """Take a screenshot and save it to Desktop."""
    try:
        from PIL import ImageGrab
    except ImportError:
        # Fallback: use PowerShell/Snipping Tool
        try:
            subprocess.Popen(["snippingtool", "/clip"], shell=True)
            return "Opened Snipping Tool for screenshot."
        except Exception:
            return "Screenshot requires Pillow: pip install Pillow"

    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(desktop, f"screenshot_{ts}.png")
        img = ImageGrab.grab()
        img.save(path)
        return f"Screenshot saved to Desktop as screenshot_{ts}.png."
    except Exception as exc:
        return f"Screenshot failed: {exc}"


# Module-level globals initialization
_last_poll_time = 0.0
_cached_hwnd = None
_cached_root = None
_cached_content = None
_harvested_identifiers = set()

def edit_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

def harvest_identifiers(text: str):
    import re
    words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{3,29}\b', text)
    _harvested_identifiers.update(words)

def correct_ocr_text(ocr_text: str) -> str:
    import re
    if not _harvested_identifiers:
        return ocr_text
    words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', ocr_text)
    corrected_words = {}
    for w in set(words):
        if len(w) < 4:
            continue
        if w in _harvested_identifiers:
            continue
        best_match = None
        best_dist = 999
        for dict_w in _harvested_identifiers:
            if abs(len(dict_w) - len(w)) > 2:
                continue
            dist = edit_distance(w, dict_w)
            if dist < best_dist:
                best_dist = dist
                best_match = dict_w
        if best_match and best_dist <= 2 and best_dist <= 0.3 * len(w):
            corrected_words[w] = best_match
    result = ocr_text
    for w, corr in corrected_words.items():
        result = re.sub(r'\b' + re.escape(w) + r'\b', corr, result)
    return result

def get_ast_viewport(doc_text: str, caret_line: int) -> str:
    import ast
    lines = doc_text.splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        return ""
    caret_idx = max(1, min(caret_line, total_lines))
    start_line = max(1, caret_idx - 30)
    end_line = min(total_lines, caret_idx + 30)
    try:
        tree = ast.parse(doc_text)
        best_node = None
        best_size = 999999
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                start = getattr(node, "lineno", None)
                end = getattr(node, "end_lineno", None)
                if start is not None and end is not None:
                    if start <= caret_idx <= end:
                        size = end - start
                        if size < best_size:
                            best_size = size
                            best_node = node
        if best_node:
            start_line = best_node.lineno
            end_line = best_node.end_lineno
    except Exception:
        pass
    sliced_lines = lines[start_line - 1 : end_line]
    return "\n".join(sliced_lines)

def skill_read_screen(args: str = "") -> str:
    print("[screen-debug] skill_read_screen CALLED")
    from web_agent import get_web_agent
    agent = get_web_agent()
    print(f"[screen-debug] agent={agent}, available={agent.available if agent else None}")
    """Reads visible text content from the currently focused window
    using a robust five-tier screen vision pipeline with caching, debouncing, and AST bounding.
    """
    import time
    import win32gui
    import win32process
    import psutil
    import uiautomation as auto
    import os
    import pathlib
    import threading

    global _last_poll_time, _cached_hwnd, _cached_root, _cached_content

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return "No focused window active."
    title = win32gui.GetWindowText(hwnd)

    current_time = time.time()
    if hwnd == _cached_hwnd and current_time - _last_poll_time < 10.0 and _cached_content is not None:
        return _cached_content

    _last_poll_time = current_time

    # ── Tier 0: Playwright DOM extraction (prefixed with [SCREEN_STRUCTURED]). Ensure browser active window. ──
    try:
        from web_agent import get_web_agent
        web_agent = get_web_agent()
        if web_agent:
            res = web_agent.extract_page_content()
            if res and (res.get("raw") or res.get("content")):
                content = res.get("raw", res.get("content", ""))
                url = res.get("url", "")
                res_title = res.get("title", "")
                _cached_content = f"[SCREEN_STRUCTURED]\nUrl: {url}\nTitle: {res_title}\nContent:\n{content}"
                return _cached_content
    except Exception:
        pass

    # ── Tier 1: uiautomation UIA Tree Walk / 5-pattern chain ──
    try:
        control = auto.ControlFromHandle(hwnd)
        _cached_hwnd = hwnd
        _cached_root = control

        # 1. Focused control query (0.15s timeout)
        focused_text = [None]
        caret_line = [1]
        def run_focused():
            try:
                focused = auto.GetFocusedControl()
                if focused:
                    try:
                        text_pat = focused.GetTextPattern()
                        if text_pat:
                            focused_text[0] = text_pat.DocumentRange.GetText(-1)
                            try:
                                sel_ranges = focused.GetSelection()
                                if sel_ranges:
                                    doc_range = focused.GetDocumentRange()
                                    doc_range.MoveEndpointByRange(
                                        auto.TextPatternRangeEndpoint.End,
                                        sel_ranges[0],
                                        auto.TextPatternRangeEndpoint.Start
                                    )
                                    text_before = doc_range.GetText(-1)
                                    caret_line[0] = text_before.count('\n') + 1
                            except Exception:
                                pass
                            return
                    except Exception: pass
                    try:
                        val_pat = focused.GetValuePattern()
                        if val_pat:
                            focused_text[0] = val_pat.Value
                            return
                    except Exception: pass
            except Exception: pass
            
        t_focused = threading.Thread(target=run_focused)
        t_focused.daemon = True
        t_focused.start()
        t_focused.join(timeout=0.15)
        
        if focused_text[0]:
            text_content = focused_text[0]
            harvest_identifiers(text_content)
            bounded_text = get_ast_viewport(text_content, caret_line[0])
            _cached_content = f"Screen content from '{title}':\n{bounded_text}"
            return _cached_content

        # 2. Text/Value patterns (0.5s timeout)
        tv_text = [None]
        def run_tv():
            try:
                doc = auto.DocumentControl(searchFromControl=control, searchDepth=12)
                if doc:
                    try:
                        text_pat = doc.GetTextPattern()
                        if text_pat:
                            tv_text[0] = text_pat.DocumentRange.GetText(-1)
                            return
                    except Exception: pass
                    try:
                        val = doc.GetValuePattern().Value
                        if val:
                            tv_text[0] = val
                            return
                    except Exception: pass
                try:
                    text_pat = control.GetTextPattern()
                    if text_pat:
                        tv_text[0] = text_pat.DocumentRange.GetText(-1)
                        return
                except Exception: pass
                try:
                    val_pat = control.GetValuePattern()
                    if val_pat:
                        tv_text[0] = val_pat.Value
                        return
                except Exception: pass
            except Exception: pass
            
        t_tv = threading.Thread(target=run_tv)
        t_tv.daemon = True
        t_tv.start()
        t_tv.join(timeout=0.5)
        
        if tv_text[0]:
            text_content = tv_text[0]
            harvest_identifiers(text_content)
            bounded_text = get_ast_viewport(text_content, 1)
            _cached_content = f"Screen content from '{title}':\n{bounded_text}"
            return _cached_content

        # 3. Tree walk (1.5s timeout)
        walk_text = [None]
        def run_walk():
            try:
                texts = []
                start_node = auto.DocumentControl(searchFromControl=control, searchDepth=12) or control
                def recurse(ctrl, depth=0):
                    if depth > 6:
                        return
                    name = ctrl.Name
                    if name:
                        texts.append(name)
                    try:
                        val = ctrl.GetValuePattern().Value
                        if val:
                            texts.append(val)
                    except Exception:
                        pass
                    for child in ctrl.GetChildren():
                        recurse(child, depth + 1)
                recurse(start_node)
                if texts:
                    walk_text[0] = "\n".join(texts)
            except Exception: pass
            
        t_walk = threading.Thread(target=run_walk)
        t_walk.daemon = True
        t_walk.start()
        t_walk.join(timeout=1.5)
        
        if walk_text[0]:
            text_content = walk_text[0]
            harvest_identifiers(text_content)
            bounded_text = get_ast_viewport(text_content, 1)
            _cached_content = f"Screen content from '{title}':\n{bounded_text}"
            return _cached_content
    except Exception:
        pass

    # ── Tier 2: Process Open Files ──
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
        open_files = proc.open_files()
        file_content = None
        for f in open_files:
            filename = os.path.basename(f.path)
            if filename and filename.lower() in title.lower():
                try:
                    file_content = pathlib.Path(f.path).read_text(encoding="utf-8", errors="ignore")
                    break
                except Exception:
                    pass

        if file_content:
            harvest_identifiers(file_content)
            caret_line = [1]
            def _get_caret():
                try:
                    focused = auto.GetFocusedControl()
                    if focused:
                        sel_ranges = focused.GetSelection()
                        if sel_ranges:
                            doc_range = focused.GetDocumentRange()
                            doc_range.MoveEndpointByRange(
                                auto.TextPatternRangeEndpoint.End,
                                sel_ranges[0],
                                auto.TextPatternRangeEndpoint.Start
                            )
                            text_before = doc_range.GetText(-1)
                            caret_line[0] = text_before.count('\n') + 1
                except Exception:
                    pass

            t_caret = threading.Thread(target=_get_caret)
            t_caret.daemon = True
            t_caret.start()
            t_caret.join(timeout=0.15)

            bounded_text = get_ast_viewport(file_content, caret_line[0])
            _cached_content = f"Screen content from '{title}':\n{bounded_text}"
            return _cached_content
    except Exception:
        pass

    # ── Tier 3: Viewport Capture & OCR ──
    try:
        import mss
        import pytesseract
        from PIL import Image
        rect = win32gui.GetWindowRect(hwnd)
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        if width > 0 and height > 0:
            with mss.mss() as sct:
                monitor = {"top": top, "left": left, "width": width, "height": height}
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                ocr_text = pytesseract.image_to_string(img, config="--psm 6")
                if ocr_text and ocr_text.strip():
                    corrected_text = correct_ocr_text(ocr_text)
                    _cached_content = f"Screen content from '{title}' (captured via OCR):\n{corrected_text}"
                    return _cached_content
    except Exception:
        pass

    # ── Tier 4: Degradation Fallback ──
    degrade_msg = f"Sir, I see you are focused on {title}, but I'm currently unable to read your editor contents directly. Could you copy the code or paste the error?"
    _cached_content = degrade_msg
    return degrade_msg

def get_active_file_path() -> Optional[str]:
    import win32gui
    import win32process
    import psutil
    import os
    
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    title = win32gui.GetWindowText(hwnd)
    
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
        open_files = proc.open_files()
        for f in open_files:
            filename = os.path.basename(f.path)
            if filename and filename.lower() in title.lower():
                return f.path
    except Exception:
        pass
    return None
