from __future__ import annotations
import threading
import os
import sys
from typing import Optional

_playwright_module = None
_sync_playwright = None

def _try_import_playwright():
    global _playwright_module, _sync_playwright
    if _sync_playwright is not None:
        return True
    try:
        from playwright.sync_api import sync_playwright
        _sync_playwright = sync_playwright
        return True
    except ImportError:
        return False

class WebAgent:
    _PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".lumen", "browser_profile_edge")

    def __init__(self, debug: bool = False):
        self._pw = None
        self._context = None
        self._lock = threading.Lock()
        self._debug = debug
        self._ready = False

    def _log(self, msg: str) -> None:
        if self._debug:
            print(f"[web-agent] {msg}")

    def start(self) -> bool:
        if not _try_import_playwright():
            self._log("Playwright not installed or could not be imported.")
            return False
        try:
            os.makedirs(self._PROFILE_DIR, exist_ok=True)
            self._pw = _sync_playwright().start()

            # Dynamic Browser detection
            # Priority: Edge (where the authenticated session lives) > Brave > default Chromium
            channel = "msedge"
            executable_path = None

            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=self._PROFILE_DIR,
                headless=False,
                channel=channel,
                executable_path=executable_path,
                args=[
                    "--no-default-browser-check", 
                    "--no-first-run",
                    "--disable-blink-features=AutomationControlled"
                ],
                slow_mo=30,
                timeout=20_000,
            )
            self._ready = True
            self._log("Persistent context launched successfully.")
            return True
        except Exception as e:
            self._log(f"Failed to start persistent context (Edge fallback active): {e}")
            # Fallback to Brave / Chrome
            try:
                brave_paths = [
                    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
                    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"BraveSoftware\Brave-Browser\Application\brave.exe")
                ]
                for p in brave_paths:
                    if os.path.exists(p):
                        executable_path = p
                        break
                
                self._context = self._pw.chromium.launch_persistent_context(
                    user_data_dir=self._PROFILE_DIR,
                    headless=False,
                    channel=None if executable_path else "chrome",
                    executable_path=executable_path,
                    args=[
                        "--no-default-browser-check", 
                        "--no-first-run",
                        "--disable-blink-features=AutomationControlled"
                    ],
                    slow_mo=30,
                    timeout=20_000,
                )
                self._ready = True
                return True
            except Exception as e2:
                self._log(f"Fallback context launch failed: {e2}")
                if self._pw:
                    try:
                        self._pw.stop()
                    except Exception:
                        pass
                    self._pw = None
                return False

    def stop(self) -> None:
        try:
            if self._context and not self._context.is_closed():
                self._context.close()
            if self._pw:
                self._pw.stop()
        except Exception as e:
            self._log(f"Error during stop: {e}")
        self._context = None
        self._pw = None
        self._ready = False

    @property
    def available(self) -> bool:
        return self._ready and self._context is not None

    def get_page(self, url_contains: str, navigate_to: str, wait_selector: str = None):
        if not self.available:
            self._log("Get page failed: WebAgent is not available.")
            return None
        
        # Check active pages
        for page in self._context.pages:
            if url_contains in page.url:
                try:
                    page.bring_to_front()
                    return page
                except Exception as e:
                    self._log(f"Failed to bring page to front: {e}")
        
        # Open a new one if not found
        try:
            page = self._context.new_page()
            page.goto(navigate_to, wait_until="domcontentloaded", timeout=20_000)
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=15_000)
            return page
        except Exception as e:
            self._log(f"Failed to navigate/open new page: {e}")
            return None

    def whatsapp_send(self, contact: str, message: str) -> str:
        with self._lock:
            try:
                page = self.get_page(
                    "web.whatsapp.com",
                    "https://web.whatsapp.com",
                    wait_selector=None,
                )
                if page is None:
                    return "WebAgent browser context is not available."

                # Wait for WhatsApp Web to load
                try:
                    page.wait_for_selector(
                        'input[placeholder="Search or start new chat"], input[data-tab="3"], div[contenteditable="true"][data-tab="3"], [placeholder="Search or start new chat"], [placeholder="Search"], div.lexical-rich-text-input div[contenteditable="true"], [data-tab="3"]',
                        timeout=15_000,
                    )
                except Exception:
                    # Check if QR code scanner is visible
                    if page.locator('canvas[aria-label="Scan me!"]').count() > 0 or page.locator('canvas').count() > 0:
                        return "WhatsApp Web is not logged in. Please scan the QR code on the browser screen."
                    return "WhatsApp Web timed out loading. Please make sure the browser has an active connection."

                search = page.locator(
                    'input[placeholder="Search or start new chat"], '
                    'input[data-tab="3"], '
                    'div[contenteditable="true"][data-tab="3"], '
                    '[placeholder="Search or start new chat"], '
                    '[placeholder="Search"], '
                    'div.lexical-rich-text-input div[contenteditable="true"]'
                ).first
                search.click()
                page.wait_for_timeout(200)
                
                # Select all and delete to clear previous searches cleanly
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.wait_for_timeout(200)
                
                # Type the contact name
                search.type(contact, delay=60)
                page.wait_for_timeout(1500)  # Wait for search results list to filter

                # Locate the contact item in the list
                contact_result = page.locator(f'span[title="{contact}"]').first
                try:
                    contact_result.wait_for(timeout=6_000)
                except Exception:
                    # Fallback to broader text match if exact title match wasn't found
                    contact_result = page.locator(f'span:has-text("{contact}")').first
                    try:
                        contact_result.wait_for(timeout=4_000)
                    except Exception:
                        return f"Could not find contact '{contact}' in WhatsApp search results."

                contact_result.click()
                page.wait_for_timeout(800)  # Wait for chat pane to open and focus

                # Locate message input box
                msg_box = page.locator(
                    'div[contenteditable="true"][data-tab="10"], '
                    'footer div[contenteditable="true"]'
                ).first
                try:
                    msg_box.wait_for(timeout=5_000)
                except Exception:
                    return "Could not focus the message input box."

                msg_box.click()
                page.wait_for_timeout(200)
                msg_box.fill("")
                page.wait_for_timeout(200)
                msg_box.type(message, delay=40)
                page.wait_for_timeout(200)
                page.keyboard.press("Enter")

                return f"Sent WhatsApp message to {contact}: '{message}'"
            except Exception as exc:
                return f"WhatsApp send failed: {exc}"

    def whatsapp_check(self) -> str:
        with self._lock:
            try:
                page = self.get_page(
                    "web.whatsapp.com",
                    "https://web.whatsapp.com",
                    wait_selector=None,
                )
                if page is None:
                    return "WebAgent browser context is not available."

                # Wait for WhatsApp Web to load
                try:
                    page.wait_for_selector(
                        'input[placeholder="Search or start new chat"], input[data-tab="3"], div[contenteditable="true"][data-tab="3"], [placeholder="Search or start new chat"], [placeholder="Search"], div.lexical-rich-text-input div[contenteditable="true"], [data-tab="3"]',
                        timeout=15_000,
                    )
                except Exception:
                    if page.locator('canvas[aria-label="Scan me!"]').count() > 0 or page.locator('canvas').count() > 0:
                        return "WhatsApp Web is not logged in. Please scan the QR code on the browser screen."
                    return "WhatsApp Web timed out loading."

                # Locate unread badges
                unread = page.locator('[aria-label*="unread"], [data-icon="unread-count"]').all()
                if not unread:
                    return "No unread WhatsApp messages detected."

                names = []
                for badge in unread[:5]:
                    try:
                        # Climb up to get the contact name element
                        name_el = badge.locator('xpath=../../../../..').locator('span[title]').first
                        if name_el.count() > 0:
                            title = name_el.get_attribute("title")
                            if title and title not in names:
                                names.append(title)
                    except Exception:
                        pass
                
                if names:
                    return f"Unread messages from: {', '.join(names)}"
                return f"{len(unread)} unread conversations detected."
            except Exception as exc:
                return f"WhatsApp check failed: {exc}"

    def navigate(self, url: str) -> str:
        with self._lock:
            try:
                if not self.available:
                    return "WebAgent not available."
                pages = self._context.pages
                page = pages[-1] if pages else self._context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                page.bring_to_front()
                return f"Navigated to {url}"
            except Exception as exc:
                return f"Navigation failed: {exc}"

    def read_page_text(self, url_contains: str = None) -> str:
        with self._lock:
            try:
                if not self.available:
                    return "WebAgent not available."
                pages = self._context.pages
                if not pages:
                    return "No pages open."
                page = pages[-1]
                if url_contains:
                    for p in pages:
                        if url_contains in p.url:
                            page = p
                            break
                text = page.evaluate("() => document.body.innerText")
                return text[:3000] if text else "No text found."
            except Exception as exc:
                return f"Read page failed: {exc}"

    def _wait_for_content(self, page, min_chars=500, timeout_ms=3000):
        try:
            page.wait_for_function(
                f"() => document.body.innerText.length >= {min_chars}",
                timeout=timeout_ms
            )
        except Exception:
            self._log(f"Timed out waiting for {min_chars} chars of content.")

    def _audit_selectors(self, page, site: str) -> None:
        if not self._debug:
            return
        selector_map = {
            "claude.ai": [
                '[data-testid="human-turn"]',
                '[data-testid="assistant-turn"]',
                '[data-testid="artifact-content"]',
            ],
            "leetcode.com": [
                '[data-track-load="description_content"]',
                '.view-line',
                '[data-cy="lang-select"]',
            ]
        }
        for sel in selector_map.get(site, []):
            try:
                count = page.locator(sel).count()
                self._log(f"[selector-audit] {site} '{sel}': {count} matches")
            except Exception as e:
                self._log(f"[selector-audit] {site} '{sel}' query failed: {e}")

    def _extract_claude(self, page, url: str) -> dict:
        self._wait_for_content(page, min_chars=500)
        self._audit_selectors(page, "claude.ai")
        return page.evaluate("""() => {
            const blocks = [];
            const humanMsgs = document.querySelectorAll(
                '[data-testid="human-turn"], .human-turn, ' +
                '[class*="HumanTurn"], [class*="human_turn"]'
            );
            const asstMsgs = document.querySelectorAll(
                '[data-testid="assistant-turn"], .assistant-turn, ' +
                '[class*="AssistantTurn"], [class*="assistant_turn"]'
            );

            const allTurns = [
                ...Array.from(humanMsgs).map(el => ({
                    role: "user",
                    text: el.innerText.trim(),
                    order: el.getBoundingClientRect().top
                })),
                ...Array.from(asstMsgs).map(el => ({
                    role: "assistant", 
                    text: el.innerText.trim(),
                    order: el.getBoundingClientRect().top
                }))
            ].sort((a, b) => a.order - b.order);

            const recent = allTurns.slice(-6);
            recent.forEach(t => blocks.push({
                label: t.role === "user" ? "User said" : "Assistant said",
                text: t.text.slice(0, 1500)
            }));

            const artifact = document.querySelector(
                '[data-testid="artifact-content"], ' +
                '[class*="artifact"], [class*="Artifact"]'
            );
            if (artifact) {
                blocks.push({
                    label: "Open document/artifact",
                    text: artifact.innerText.trim().slice(0, 2000)
                });
            }

            return {
                title: document.title,
                blocks: blocks
            };
        }""")

    def _extract_leetcode(self, page, url: str) -> dict:
        self._wait_for_content(page, min_chars=800)
        self._audit_selectors(page, "leetcode.com")
        return page.evaluate("""() => {
            const blocks = [];

            const title = document.querySelector(
                '[data-cy="question-title"], ' +
                '.text-title-large, [class*="title__"]'
            );
            if (title) blocks.push({
                label: "Problem title",
                text: title.innerText.trim()
            });

            const diff = document.querySelector(
                '[diff], .text-difficulty-easy, ' +
                '.text-difficulty-medium, .text-difficulty-hard, ' +
                '[class*="difficulty"]'
            );
            if (diff) blocks.push({
                label: "Difficulty", 
                text: diff.innerText.trim()
            });

            const desc = document.querySelector(
                '[data-track-load="description_content"], ' +
                '.elfjS, [class*="content__"], ' +
                '[class*="description__"]'
            );
            if (desc) blocks.push({
                label: "Problem statement",
                text: desc.innerText.trim()
            });

            const editorLines = document.querySelectorAll('.view-line');
            if (editorLines.length > 0) {
                const code = Array.from(editorLines)
                    .map(l => l.innerText)
                    .join('\\n');
                blocks.push({
                    label: "Current code in editor",
                    text: code.trim()
                });
            }

            const lang = document.querySelector(
                '[data-cy="lang-select"], ' +
                'button[id*="headlessui"], ' +
                '.ant-select-selection-item'
            );
            if (lang) blocks.push({
                label: "Selected language",
                text: lang.innerText.trim()
            });

            const testResult = document.querySelector(
                '[data-cy="result-state"], ' +
                '[class*="result__"], [class*="testcase"]'
            );
            if (testResult) blocks.push({
                label: "Test result",
                text: testResult.innerText.trim().slice(0, 300)
            });

            return {
                title: document.title,
                blocks: blocks
            };
        }""")

    def _extract_github(self, page, url: str) -> dict:
        self._wait_for_content(page, min_chars=500)
        self._audit_selectors(page, "github.com")
        return page.evaluate("""() => {
            const blocks = [];

            const readme = document.querySelector(
                '#readme article, .markdown-body, ' +
                '[data-target="readme-toc.content"]'
            );
            if (readme) blocks.push({
                label: "README / file content",
                text: readme.innerText.trim().slice(0, 3000)
            });

            const codeTable = document.querySelector(
                '.js-file-line-container, table.highlight'
            );
            if (codeTable) blocks.push({
                label: "Code file",
                text: codeTable.innerText.trim().slice(0, 2000)
            });

            const issueTitle = document.querySelector(
                '.js-issue-title, [class*="Title__"]'
            );
            const issueBody = document.querySelector(
                '.comment-body:first-of-type'
            );
            if (issueTitle) blocks.push({
                label: "Issue/PR title",
                text: issueTitle.innerText.trim()
            });
            if (issueBody) blocks.push({
                label: "Issue/PR description",
                text: issueBody.innerText.trim().slice(0, 1000)
            });

            return { title: document.title, blocks: blocks };
        }""")

    def _extract_stackoverflow(self, page, url: str) -> dict:
        self._wait_for_content(page, min_chars=500)
        self._audit_selectors(page, "stackoverflow.com")
        return page.evaluate("""() => {
            const blocks = [];

            const title = document.querySelector('#question-header, .question');
            if (title) blocks.push({
                label: "Question title/body",
                text: title.innerText.trim().slice(0, 2000)
            });

            const answers = Array.from(document.querySelectorAll('.answer'));
            answers.forEach((ans, i) => {
                blocks.push({
                    label: `Answer ${i + 1}`,
                    text: ans.innerText.trim().slice(0, 1500)
                });
            });

            return { title: document.title, blocks: blocks };
        }""")

    def _extract_generic(self, page, url: str) -> dict:
        self._wait_for_content(page, min_chars=500)
        self._audit_selectors(page, "generic")
        return page.evaluate("""() => {
            const isCodeBlock = el => {
                const style = window.getComputedStyle(el);
                const font = style.fontFamily || "";
                return font.includes('monospace') || font.includes('Courier') 
                    || font.includes('Monaco') || el.closest('pre') || el.closest('code');
            };

            const selectors = [
                'main', 'article', '[role="main"]',
                '#content', '#main', '.content', '.main'
            ];

            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 300) {
                    const lines = el.innerText.split('\\n');
                    const filtered = lines
                        .filter(l => isCodeBlock(el) || l.trim().length > 40)
                        .join('\\n');
                    if (filtered.length > 200) {
                        return {
                            title: document.title,
                            blocks: [{ label: "Page content", text: filtered.slice(0, 3000) }]
                        };
                    }
                }
            }

            const lines = document.body.innerText.split('\\n');
            const filtered = lines
                .filter(l => l.trim().length > 40)
                .join('\\n')
                .slice(0, 3000);
            
            return {
                title: document.title,
                blocks: [{ label: "Page content", text: filtered }]
            };
        }""")

    def extract_page_content(self) -> dict:
        with self._lock:
            if not self.available:
                return {}
            try:
                import win32gui
                import win32process
                import psutil
                hwnd = win32gui.GetForegroundWindow()
                if not hwnd:
                    return {}
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc_name = psutil.Process(pid).name().lower()
            except Exception:
                return {}
            allowed_browsers = {"msedge.exe", "chrome.exe", "brave.exe", "firefox.exe", "opera.exe"}
            if proc_name not in allowed_browsers:
                return {}
            pages = self._context.pages
            if not pages:
                return {}
            active_page = None
            for p in reversed(pages):
                try:
                    if p.evaluate("() => document.visibilityState") == "visible":
                        active_page = p
                        break
                except Exception:
                    pass
            if not active_page:
                active_page = pages[-1]
            url = active_page.url
            if "claude.ai" in url:
                data = self._extract_claude(active_page, url)
                site = "claude.ai"
            elif "leetcode.com" in url:
                data = self._extract_leetcode(active_page, url)
                site = "leetcode.com"
            elif "github.com" in url:
                data = self._extract_github(active_page, url)
                site = "github.com"
            elif "stackoverflow.com" in url:
                data = self._extract_stackoverflow(active_page, url)
                site = "stackoverflow.com"
            else:
                data = self._extract_generic(active_page, url)
                site = "generic"

            if not data or not isinstance(data, dict):
                return {}
            blocks = data.get("blocks", [])
            raw = "\n\n".join(f"{b['label']}:\n{b['text']}" for b in blocks)
            return {
                "site": site,
                "url": url,
                "title": data.get("title", ""),
                "content_blocks": blocks,
                "raw": raw
            }

    def leetcode_insert_code(self, code: str) -> bool:
        with self._lock:
            if not self.available:
                return False
            pages = self._context.pages
            if not pages:
                return False
            active_page = None
            for p in pages:
                try:
                    if p.evaluate("() => document.visibilityState") == "visible":
                        active_page = p
                        break
                except Exception:
                    pass
            if not active_page:
                active_page = pages[-1]
            try:
                success = active_page.evaluate("""(code) => {
                    try {
                        if (window.monaco && window.monaco.editor) {
                            const models = window.monaco.editor.getModels();
                            if (models && models.length > 0) {
                                models[0].setValue(code);
                                return true;
                            }
                        }
                        return false;
                    } catch (e) {
                        return false;
                    }
                }""", code)
                return bool(success)
            except Exception as e:
                self._log(f"Failed to insert code into LeetCode: {e}")
                return False

_web_agent: Optional[WebAgent] = None

def get_web_agent() -> Optional[WebAgent]:
    return _web_agent

def init_web_agent(debug: bool = False) -> bool:
    global _web_agent
    _web_agent = WebAgent(debug=debug)
    return _web_agent.start()
