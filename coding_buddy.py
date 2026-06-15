import time
import re
import ast
import os
import queue
import threading
import concurrent.futures
import tempfile
import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider
from collections import Counter
from typing import Optional, List, Tuple

# Configuration Constants
JACCARD_ACTIVE_LIMIT = 0.60
JACCARD_STARING_LIMIT = 0.95
JACCARD_REPETITIVE_MIN = 0.80
JACCARD_REPETITIVE_MAX = 0.95
STARE_TIMEOUT_S = 45

# State Queue and Lock
_state_lock = threading.Lock()
coding_buddy_queue = queue.Queue()
hint_cancelled = threading.Event()

# Thread control
_running = False
_buddy_thread = None

# Stats of Jaccard
_jaccard_history = []
_stare_duration = 0.0
_last_fingerprint = None
_last_state = "ACTIVE_CODING"

def stats(values: List[float]) -> Tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    std = variance ** 0.5
    return mean, std

def compute_jaccard(S_t: Counter, S_prev: Counter) -> float:
    if not S_t and not S_prev:
        return 1.0
    if not S_t or not S_prev:
        return 0.0
    intersection = sum((S_t & S_prev).values())
    union = sum((S_t | S_prev).values())
    return intersection / union if union > 0 else 0.0

def build_fingerprint(code: str) -> Counter:
    try:
        tree = ast.parse(code)
    except Exception:
        return Counter()
    
    fingerprint = Counter()
    def walk(node, depth):
        node_type = type(node).__name__
        fingerprint[(node_type, depth)] += 1
        for child in ast.iter_child_nodes(node):
            walk(child, depth + 1)
            
    walk(tree, 0)
    return fingerprint

class CommentInserter(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, caret_line: int, comment_text: str):
        self.caret_line = caret_line
        self.comment_text = comment_text
        self.inserted = False

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        return self._insert_comment_in_body(original_node, updated_node)

    def leave_IndentedBlock(self, original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock) -> cst.IndentedBlock:
        return self._insert_comment_in_body(original_node, updated_node)

    def _insert_comment_in_body(self, original_node, updated_node):
        if self.inserted:
            return updated_node
        
        new_body = []
        for i, stmt in enumerate(original_node.body):
            if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)):
                pos = self.get_metadata(PositionProvider, stmt)
                if pos.start.line <= self.caret_line <= pos.end.line:
                    comment_line = cst.EmptyLine(comment=cst.Comment(f"# {self.comment_text}"))
                    updated_stmt = updated_node.body[i]
                    new_body.append(comment_line)
                    new_body.append(updated_stmt)
                    self.inserted = True
                    continue
            new_body.append(updated_node.body[i])
            
        return updated_node.with_changes(body=new_body)

def insert_comment_regex_fallback(code: str, caret_line: int, comment_text: str) -> str:
    lines = code.splitlines()
    total_lines = len(lines)
    target_idx = -1
    for idx in range(min(caret_line - 1, total_lines - 1), -1, -1):
        if re.match(r"^\s*(def|class)\s+\w+", lines[idx]):
            target_idx = idx
            break
            
    if target_idx != -1:
        match = re.match(r"^(\s*)", lines[target_idx])
        indent = match.group(1) if match else ""
        comment_line = f"{indent}# {comment_text}"
        lines.insert(target_idx, comment_line)
    else:
        lines.insert(0, f"# {comment_text}")
        
    result = "\n".join(lines)
    if code.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result

def write_atomically(file_path: str, content: str):
    dir_name = os.path.dirname(file_path)
    fd, temp_path = tempfile.mkstemp(dir=dir_name, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(temp_path, file_path)
    except Exception as e:
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise e

def insert_proactive_comment(file_path: str, caret_line: int, comment_text: str) -> bool:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            original_code = f.read()
    except Exception:
        return False

    try:
        # Try CST Insertion (Primary)
        wrapper = MetadataWrapper(cst.parse_module(original_code))
        inserter = CommentInserter(caret_line, comment_text)
        new_module = wrapper.visit(inserter)
        if inserter.inserted:
            write_atomically(file_path, new_module.code)
            return True
    except Exception:
        pass

    # Regex Fallback (Error Recovery)
    try:
        new_code = insert_comment_regex_fallback(original_code, caret_line, comment_text)
        write_atomically(file_path, new_code)
        return True
    except Exception:
        pass

    return False

def get_caret_line_safe(focused_control=None) -> int:
    import uiautomation as auto
    caret_line = [1]
    def _worker():
        try:
            ctrl = focused_control
            if ctrl is None:
                ctrl = auto.GetFocusedControl()
            if ctrl:
                sel_ranges = ctrl.GetSelection()
                if sel_ranges:
                    doc_range = ctrl.GetDocumentRange()
                    doc_range.MoveEndpointByRange(
                        auto.TextPatternRangeEndpoint.End,
                        sel_ranges[0],
                        auto.TextPatternRangeEndpoint.Start
                    )
                    text_before = doc_range.GetText(-1)
                    caret_line[0] = text_before.count('\n') + 1
        except Exception:
            pass

    t = threading.Thread(target=_worker)
    t.daemon = True
    t.start()
    t.join(timeout=0.15)
    return caret_line[0]

def is_editor_focused(title: str) -> bool:
    title_lower = title.lower()
    editor_keywords = ["visual studio code", "vs code", "pycharm", "notepad", "sublime text", "eclipse", "code"]
    file_exts = [".py", ".txt", ".json", ".md", ".html", ".css", ".js", ".ts", ".c", ".cpp", ".java", ".go", ".rs"]
    return any(k in title_lower for k in editor_keywords) or any(ext in title_lower for ext in file_exts)

def poll_coding_buddy(brain, voice):
    global _jaccard_history, _stare_duration, _last_fingerprint, _last_state, hint_cancelled
    import win32gui
    import win32process
    import psutil
    from skills.system import skill_read_screen, get_active_file_path
    
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return
    title = win32gui.GetWindowText(hwnd)
    
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc_name = psutil.Process(pid).name()
    except Exception:
        proc_name = ""
        
    _CODING_PROCS = {"Code.exe", "code.exe", "pycharm64.exe", "idea64.exe",
                     "devenv.exe", "sublime_text.exe", "notepad++.exe"}
    is_coding = proc_name in _CODING_PROCS
    active_file = get_active_file_path()
    
    print(f"[coding-buddy-debug] file={active_file!r} window={proc_name!r} is_coding={is_coding}")
    
    if not is_coding:
        with _state_lock:
            _stare_duration = 0.0
            _last_state = "IDLE"
            _jaccard_history.clear()
            _last_fingerprint = None
        return

    screen_text = skill_read_screen()
    
    prefix_match = re.match(r"^Screen content from '.*?(?:\(captured via OCR\))?':\n", screen_text)
    if prefix_match:
        code = screen_text[prefix_match.end():]
    else:
        code = ""
        
    if not code.strip():
        return

    fingerprint = build_fingerprint(code)
    
    with _state_lock:
        if _last_fingerprint is None:
            _last_fingerprint = fingerprint
            return
            
        jaccard = compute_jaccard(fingerprint, _last_fingerprint)
        _last_fingerprint = fingerprint
        
        print(f"[coding-buddy] Calculated Jaccard: {jaccard:.4f} (stare duration: {_stare_duration:.1f}s, last state: {_last_state})")
        
        if jaccard < 0.90:
            hint_cancelled.set()
            
        _jaccard_history.append(jaccard)
        if len(_jaccard_history) > 5:
            _jaccard_history.pop(0)
            
        if jaccard >= JACCARD_STARING_LIMIT:
            _stare_duration += 10.0
        else:
            _stare_duration = 0.0

        new_state = "ACTIVE_CODING"
        
        if jaccard >= JACCARD_STARING_LIMIT and _stare_duration >= STARE_TIMEOUT_S:
            new_state = "STARING_STATE"
        elif len(_jaccard_history) == 5:
            m, s_val = stats(_jaccard_history)
            intervals_in_range = sum(1 for j in _jaccard_history if JACCARD_REPETITIVE_MIN <= j <= JACCARD_REPETITIVE_MAX)
            if s_val < 0.04 and 0.80 <= m <= 0.95 and intervals_in_range >= 3:
                new_state = "REPETITIVE_EDITS"
        elif jaccard < JACCARD_ACTIVE_LIMIT:
            new_state = "ACTIVE_CODING"
            
        if new_state in ("STARING_STATE", "REPETITIVE_EDITS") and _last_state != new_state:
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(_generate_hint_worker, brain, new_state, code)
            
            try:
                hint = future.result(timeout=8.0)
                if hint and not hint_cancelled.is_set():
                    file_path = get_active_file_path()
                    caret_line = get_caret_line_safe(None)
                    
                    if file_path:
                        insert_proactive_comment(file_path, caret_line, f"PROACTIVE HINT: {hint}")
                    
                    coding_buddy_queue.put(hint)
            except concurrent.futures.TimeoutError:
                coding_buddy_queue.put("Sir, I am reviewing your code...")
                
                def _speak_delayed():
                    try:
                        hint = future.result()
                        if hint and not hint_cancelled.is_set():
                            coding_buddy_queue.put(hint)
                    except Exception:
                        pass
                threading.Thread(target=_speak_delayed, daemon=True).start()
                
        _last_state = new_state

def _generate_hint_worker(brain, state_name: str, code_snippet: str) -> Optional[str]:
    prompt = (
        f"Provide a brief, single-sentence suggestion or helpful question "
        f"for a programmer who has been in state '{state_name}' (staring or doing repetitive edits) "
        f"on the following Python snippet. Keep it extremely short (10-15 words max), supportive, and clear. "
        f"Do not include any formatting or system tags. Snippet:\n{code_snippet[:1000]}"
    )
    system = "You are Lumen's proactive coding companion. Be concise, supportive, and conversational."
    try:
        return brain.generate_simple(prompt, system=system)
    except Exception:
        return None

def start_coding_buddy(brain, voice):
    global _running, _buddy_thread
    if _running:
        return
        
    _running = True
    def _loop():
        while _running:
            try:
                poll_coding_buddy(brain, voice)
            except Exception:
                pass
            time.sleep(10.0)
            
    _buddy_thread = threading.Thread(target=_loop, daemon=True, name="coding-buddy")
    _buddy_thread.start()

def stop_coding_buddy():
    global _running
    _running = False
