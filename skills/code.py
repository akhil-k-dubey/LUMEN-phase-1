"""
skills/code.py — Code execution and git query skills.
"""
from __future__ import annotations

import ast
import os
import subprocess
from typing import Optional

from ._utils import _truncate


def _validate_code_ast(code: str) -> Optional[str]:
    """AST-based code validator. Returns None if safe, or an error string."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Syntax error: {e}"

    _DANGEROUS_MODULES = {
        "ctypes", "socket",
        "requests", "urllib", "code",
        "codeop", "pdb", "traceback", "inspect", "io", "tempfile",
        "threading", "multiprocessing", "signal", "atexit", "gc"
    }

    _BLOCKED_NAMES = {
        "eval", "exec", "compile", "__import__",
        "__builtins__", "__dict__", "globals", "locals",
        "getattr", "setattr", "vars", "type", "__class__",
        "__bases__", "__subclasses__", "__mro__", "__code__",
        "__globals__"
    }

    _DESTRUCTIVE_NAMES = {
        "remove", "unlink", "rmdir", "removedirs", "rmtree", "delete"
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _DANGEROUS_MODULES:
                    return f"Refused: 'import {alias.name}' is not allowed in sandbox."

        if isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in _DANGEROUS_MODULES:
                    return f"Refused: 'from {node.module}' is not allowed in sandbox."

        if isinstance(node, ast.Name):
            if node.id in _BLOCKED_NAMES:
                return f"Refused: Use of '{node.id}' is not allowed in sandbox."
            if node.id in _DESTRUCTIVE_NAMES:
                return f"Refused: Use of destructive name '{node.id}' is not allowed in sandbox."

        if isinstance(node, ast.Attribute):
            if node.attr in _BLOCKED_NAMES:
                return f"Refused: Attribute access to '{node.attr}' is not allowed in sandbox."
            if node.attr in _DESTRUCTIVE_NAMES:
                return f"Refused: Use of destructive attribute '{node.attr}' is not allowed in sandbox."

        if isinstance(node, ast.Subscript):
            slice_node = node.slice
            # In older python versions, slice might be wrapped in ast.Index
            if hasattr(slice_node, "value"):
                slice_node = getattr(slice_node, "value")
            if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                val = slice_node.value
                if val in _BLOCKED_NAMES or val in _DESTRUCTIVE_NAMES:
                    return f"Refused: Subscript access to '{val}' is not allowed in sandbox."

        if isinstance(node, ast.Call):
            # Pass 1 AST destructive command execution check
            func_name = None
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    func_name = f"{node.func.value.id}.{node.func.attr}"
                    mod = node.func.value.id
                    if mod in _DANGEROUS_MODULES or mod in _BLOCKED_NAMES:
                        return f"Refused: '{mod}.{node.func.attr}' is not allowed in sandbox."
                else:
                    func_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                func_name = node.func.id

            is_cmd_exec = False
            if func_name:
                cmd_execs = {
                    "subprocess.run", "subprocess.Popen", "subprocess.call",
                    "subprocess.check_output", "subprocess.check_call",
                    "os.system", "os.popen", "os.spawn",
                    "run", "Popen", "call", "check_output", "check_call",
                    "system", "popen",
                }
                if func_name in cmd_execs or func_name.startswith("os.spawn"):
                    is_cmd_exec = True

            if is_cmd_exec:
                import re
                shell_delete_re = re.compile(r'\b(rm|del|rd|rmdir|remove-item)\b', re.IGNORECASE)
                for arg in node.args + [kw.value for kw in node.keywords]:
                    for subnode in ast.walk(arg):
                        val = None
                        if isinstance(subnode, ast.Constant) and isinstance(subnode.value, str):
                            val = subnode.value
                        elif hasattr(subnode, 's') and isinstance(getattr(subnode, 's'), str):
                            val = getattr(subnode, 's')
                        if val and shell_delete_re.search(val):
                            return "Refused: Destructive command delete pattern found in command arguments."

    return None


def skill_run_code(args: str) -> str:
    """
    Execute a code snippet in a sandboxed subprocess.
    args: lang:code  (e.g. python:print(1+1))
    Supported: python, python3, node, js
    Security: 10s timeout, AST-based code validation, isolated subprocess.
    """
    if ":" not in args:
        return "Format: run_code:python:print('hello')"
    lang, code = args.split(":", 1)
    lang = lang.strip().lower()

    # AST-based validation replaces the old substring blacklist
    err = _validate_code_ast(code)
    if err:
        return err

    lang_map = {"python": "python", "python3": "python", "node": "node", "js": "node"}
    interpreter = lang_map.get(lang)
    if not interpreter:
        return f"Unsupported language: {lang}. Use python or node."

    try:
        # Python: -I = isolated mode, -c = command
        # Node.js: -e = evaluate (no -I equivalent, no -c)
        if lang.startswith("python"):
            wrapped_code = """
import sys
import builtins

_blocked = {"remove", "unlink", "rmdir", "removedirs", "rmtree", "delete"}

class SafeProxy:
    def __init__(self, obj):
        object.__setattr__(self, "_obj", obj)
    def __getattribute__(self, name):
        if name in _blocked:
            raise PermissionError(f"Access to '{name}' is blocked.")
        if name == "__getattribute__":
            def custom_getattribute(attr_name):
                if attr_name in _blocked:
                    raise PermissionError(f"Access to '{attr_name}' is blocked.")
                return getattr(object.__getattribute__(self, "_obj"), attr_name)
            return custom_getattribute
        return getattr(object.__getattribute__(self, "_obj"), name)
    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_obj"), name, value)
    def __delattr__(self, name):
        delattr(object.__getattribute__(self, "_obj"), name)

# Hook import
_orig_import = builtins.__import__
def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    mod = _orig_import(name, globals, locals, fromlist, level)
    if name in {"os", "sys", "pathlib", "shutil", "subprocess", "importlib"}:
        return SafeProxy(mod)
    return mod
builtins.__import__ = _safe_import

for name in list(sys.modules.keys()):
    if name in {"os", "sys", "pathlib", "shutil", "subprocess", "importlib"}:
        sys.modules[name] = SafeProxy(sys.modules[name])

# Run user code
exec(__USER_CODE__, globals())
""".replace("__USER_CODE__", repr(code))
            cmd = [interpreter, "-I", "-c", wrapped_code]
        else:
            cmd = [interpreter, "-e", code]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if err and not out:
            return _truncate(f"Error: {err}")
        if err:
            return _truncate(f"Output: {out}\nStderr: {err}")
        return _truncate(out or "(no output)")
    except subprocess.TimeoutExpired:
        return "Code execution timed out after 10 seconds."
    except FileNotFoundError:
        return f"Interpreter '{interpreter}' not found on PATH."
    except Exception as exc:
        return f"Execution error: {exc}"


def skill_git_query(args: str) -> str:
    """
    Query a git repository (read-only: status, diff, log, branch).
    args: command  (status | diff | log | branch | show)
    Blocked: push, commit, reset, checkout, merge, rebase.
    """
    cmd = args.strip().lower().split()[0] if args.strip() else "status"

    _READONLY = {"status", "diff", "log", "branch", "show", "stash"}
    _BLOCKED_GIT = {"push", "commit", "reset", "checkout", "merge",
                    "rebase", "rm", "mv", "add", "clean", "fetch", "pull"}

    if cmd in _BLOCKED_GIT:
        return f"Refused: 'git {cmd}' is not allowed via skill (write operations only via terminal)."
    if cmd not in _READONLY:
        return f"Unknown git command: {cmd}. Use: status, diff, log, branch, show."

    git_args = args.strip().split()
    # Build safe git command
    git_cmd = ["git"] + git_args
    if cmd == "log":
        # Limit log output
        if "--oneline" not in git_args:
            git_cmd += ["--oneline", "-10"]

    try:
        result = subprocess.run(
            git_cmd,
            capture_output=True, text=True,
            timeout=10, cwd=os.getcwd(),
        )
        out = result.stdout.strip() or result.stderr.strip()
        return _truncate(out or f"git {cmd}: no output.")
    except subprocess.TimeoutExpired:
        return "git command timed out."
    except FileNotFoundError:
        return "git is not installed or not on PATH."
    except Exception as exc:
        return f"git error: {exc}"
