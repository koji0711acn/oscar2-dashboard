#!/usr/bin/env python3
"""Orchestrator: Automated relay between claude.ai advisor chat and Claude Code CLI.

- Advisor chat (claude.ai/chat): controlled via Playwright (browser)
- Claude Code: executed via `claude -p` one-shot mode (subprocess.run)

Loop:
1. Advisor gives instruction (Playwright reads latest response)
2. Execute instruction via `claude -p "instruction"` (subprocess)
3. Send Claude Code output back to advisor (Playwright)
4. Wait for advisor's next instruction
5. Repeat

Usage:
    python orchestrator.py
    python orchestrator.py --config orchestrator_config.json
    python orchestrator.py --test
"""

import sys
import os
import time
import json
import shutil
import logging
import traceback
import argparse
import subprocess as sp
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


class Orchestrator:
    """Automated message relay between claude.ai advisor and Claude Code CLI."""

    def __init__(self, config_path="orchestrator_config.json"):
        self.config = self._load_config(config_path)
        self.playwright = None
        self.context = None
        self.advisor_page = None
        self._last_activity = datetime.now()
        self._cycle_count = 0

        # Logging
        log_dir = Path(self.config.get("log_dir", "logs"))
        log_dir.mkdir(exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        log_file = log_dir / f"orchestrator_{today}.log"

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )
        self.logger = logging.getLogger("orchestrator")

    def _load_config(self, config_path):
        path = Path(config_path)
        if not path.exists():
            path = Path(__file__).parent / config_path
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def log(self, message, level="info"):
        getattr(self.logger, level, self.logger.info)(message)

    # ================================================================
    # Claude Code CLI — one-shot via `claude -p`
    # ================================================================

    def _find_claude_binary(self):
        """Find the claude CLI executable."""
        found = shutil.which("claude")
        if found:
            return found
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA", "")
            for candidate in [
                os.path.join(appdata, "npm", "claude.cmd"),
                os.path.join(appdata, "npm", "claude"),
            ]:
                if os.path.isfile(candidate):
                    return candidate
        raise FileNotFoundError("claude CLI not found in PATH or npm global dirs")

    def execute_claude_code(self, instruction):
        """Execute an instruction via `claude -p` (one-shot, non-interactive).

        Returns the full stdout+stderr output as a string.
        """
        project_dir = self.config.get(
            "claude_code_project_dir",
            r"C:\Users\koji3\OneDrive\デスクトップ\blog_automation",
        )
        claude_cmd = self._find_claude_binary()
        timeout = self.config.get("cycle_timeout_minutes", 10) * 60

        self.log(f"Executing claude -p ({len(instruction)} chars): {instruction[:120]}...")

        use_shell = sys.platform == "win32" and claude_cmd.endswith(".cmd")

        try:
            result = sp.run(
                [claude_cmd, "-p", instruction, "--dangerously-skip-permissions"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=use_shell,
            )

            output = result.stdout or ""
            if result.stderr:
                output += "\n[STDERR]\n" + result.stderr

            self.log(f"Claude Code finished (exit={result.returncode}, {len(output)} chars)")
            self._last_activity = datetime.now()
            return output.strip()

        except sp.TimeoutExpired:
            self.log(f"Claude Code timed out after {timeout}s", "error")
            return "[ERROR] Claude Code execution timed out"
        except Exception as e:
            self.log(f"Claude Code execution failed: {e}", "error")
            return f"[ERROR] {e}"

    # ================================================================
    # Browser — Advisor chat (Playwright)
    # ================================================================

    def _get_profile_dir(self):
        default_dir = r"C:\Users\koji3\AppData\Local\Google\Chrome\OrchestratorProfile"
        return Path(self.config.get("chrome_profile_copy_dir", default_dir))

    def setup_browser(self):
        """Launch Chromium and navigate to the advisor chat URL."""
        profile_dir = self._get_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        first_time = not (profile_dir / "Default").exists()

        self.log(f"Browser profile: {profile_dir}")
        if first_time:
            self.log("First launch — manual login to claude.ai required.")

        self.playwright = sync_playwright().start()
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport={"width": 1400, "height": 900},
            slow_mo=100,
        )

        # First-time login flow
        if first_time:
            page = self.context.pages[0] if self.context.pages else self.context.new_page()
            page.goto("https://claude.ai", wait_until="domcontentloaded", timeout=60000)
            self.log(">>> Please log in to claude.ai in the browser window <<<")
            self.log(">>> After logging in, press Enter here to continue <<<")
            input()
            self.log("Login saved.")

        # Navigate to advisor chat
        advisor_url = self.config.get("advisor_chat_url", "")
        if not advisor_url:
            raise ValueError("advisor_chat_url is required in orchestrator_config.json")

        self.advisor_page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.log(f"Navigating to advisor: {advisor_url}")
        self.advisor_page.goto(advisor_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Verify we're on the right page
        current = self.advisor_page.url
        self.log(f"Advisor page URL: {current}")

        input_field = self._find_input_field(self.advisor_page)
        if input_field:
            self.log("Advisor chat ready (input field found)")
        else:
            self.log("Input field not found yet — chat may still be loading", "warning")

    # ================================================================
    # DOM Interaction Helpers
    # ================================================================

    def _find_input_field(self, page):
        selectors = [
            'div[contenteditable="true"]',
            'div.ProseMirror[contenteditable="true"]',
            "textarea",
            '[data-testid="chat-input"]',
            '[data-testid="message-input"]',
            '[role="textbox"]',
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    return el
            except Exception:
                continue
        return None

    def _find_stop_button(self, page):
        selectors = [
            'button:has-text("Stop")',
            '[aria-label="Stop"]',
            '[data-testid="stop-button"]',
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=1000):
                    return el
            except Exception:
                continue
        return None

    def _get_message_elements(self, page):
        selectors = [
            '[data-testid*="message"]',
            '[class*="Message"]',
            '[class*="message"]',
            'div[data-is-streaming]',
            'main div[class] > div[class]',
        ]
        for sel in selectors:
            try:
                elements = page.locator(sel).all()
                if len(elements) >= 1:
                    return elements
            except Exception:
                continue
        return []

    def send_message(self, page, text):
        """Send a message in the advisor chat (Playwright)."""
        if not text or not text.strip():
            self.log("Empty message, skipping", "warning")
            return False

        input_field = self._find_input_field(page)
        if not input_field:
            self.log("Could not find input field!", "error")
            return False

        try:
            input_field.click()
            time.sleep(0.5)

            if len(text) > 1000:
                self.log(f"Pasting via clipboard ({len(text)} chars)")
                page.evaluate(f"navigator.clipboard.writeText({json.dumps(text)})")
                time.sleep(0.3)
                page.keyboard.press("Control+v")
                time.sleep(0.5)
            else:
                input_field.fill(text)
                time.sleep(0.3)

            page.keyboard.press("Enter")
            self.log(f"Sent to advisor ({len(text)} chars): {text[:150]}...")
            self._last_activity = datetime.now()
            time.sleep(2)
            return True
        except Exception as e:
            self.log(f"Failed to send: {e}", "error")
            return False

    def get_latest_response(self, page):
        """Get the latest assistant response text."""
        messages = self._get_message_elements(page)
        if not messages:
            return ""
        try:
            return messages[-1].inner_text(timeout=5000).strip()
        except Exception as e:
            self.log(f"Failed to get response: {e}", "warning")
            return ""

    def wait_for_advisor_response(self, timeout_seconds=None):
        """Wait for advisor to finish generating a response."""
        if timeout_seconds is None:
            timeout_seconds = self.config.get("cycle_timeout_minutes", 60) * 60

        self.log(f"Waiting for advisor response (timeout={timeout_seconds}s)...")
        start = time.time()
        initial_msgs = len(self._get_message_elements(self.advisor_page))
        stable_count = 0

        while time.time() - start < timeout_seconds:
            stop_btn = self._find_stop_button(self.advisor_page)
            if stop_btn is None:
                input_field = self._find_input_field(self.advisor_page)
                if input_field:
                    try:
                        if input_field.is_enabled(timeout=2000):
                            time.sleep(3)
                            current_msgs = len(self._get_message_elements(self.advisor_page))
                            if current_msgs > initial_msgs:
                                self.log(f"Advisor done ({initial_msgs}->{current_msgs} msgs)")
                                return True
                            stable_count += 1
                            if stable_count >= 2:
                                self.log("Advisor response appears complete")
                                return True
                    except Exception:
                        pass

            elapsed = int(time.time() - start)
            if elapsed % 60 == 0 and elapsed > 0:
                self.log(f"Still waiting for advisor... ({elapsed}s)")
            time.sleep(10)

        self.log(f"Advisor timed out after {timeout_seconds}s", "warning")
        return False

    # ================================================================
    # Main Cycle
    # ================================================================

    def run_cycle(self):
        """One cycle: get advisor instruction -> execute via claude -p -> send result back."""
        self.log("--- Cycle start ---")

        # Step 1: Get the latest advisor response (= instruction for Claude Code)
        instruction = self.get_latest_response(self.advisor_page)
        if not instruction:
            self.log("No advisor instruction found, waiting...", "warning")
            time.sleep(30)
            return
        self.log(f"Advisor instruction ({len(instruction)} chars): {instruction[:200]}...")

        # Step 2: Execute via claude -p
        self.log("Executing Claude Code...")
        output = self.execute_claude_code(instruction)
        if not output:
            output = "(Claude Code returned empty output)"
        self.log(f"Claude Code result ({len(output)} chars)")

        # Step 3: Send result back to advisor
        self.log("Sending result to advisor...")
        sent = self.send_message(self.advisor_page, output)
        if not sent:
            self.log("Failed to send to advisor", "error")
            time.sleep(30)
            return

        # Step 4: Wait for advisor to process and give next instruction
        self.log("Waiting for advisor's next instruction...")
        done = self.wait_for_advisor_response()
        if not done:
            self.log("Advisor response timeout", "warning")

        self.log("--- Cycle complete ---")

    def run(self):
        """Main loop. Sets up browser, sends initial message, then cycles."""
        self.setup_browser()
        self.log("=== Orchestrator started ===")
        self.log(f"Advisor: {self.config.get('advisor_chat_url', 'N/A')}")
        self.log(f"Project: {self.config.get('claude_code_project_dir', 'N/A')}")

        # Send initial message to advisor to kick off the first cycle
        self.log("Sending startup message to advisor...")
        self.send_message(
            self.advisor_page,
            "Orchestrator起動完了。参謀からの最初の指示を待っています。\n"
            "Claude Code CLIは `claude -p` モードで実行します。"
        )

        # Wait for advisor's first instruction
        self.log("Waiting for advisor's first instruction...")
        self.wait_for_advisor_response()

        retry_delay = self.config.get("retry_delay_seconds", 30)
        max_retries = self.config.get("retry_max", 3)
        consecutive_errors = 0

        try:
            while True:
                try:
                    self.run_cycle()
                    self._cycle_count += 1
                    consecutive_errors = 0
                    self.log(f"Cycle {self._cycle_count} completed")
                except PlaywrightTimeout as e:
                    consecutive_errors += 1
                    self.log(f"Timeout: {e}", "warning")
                    if consecutive_errors >= max_retries:
                        self._reload_advisor()
                        consecutive_errors = 0
                    time.sleep(retry_delay)
                except Exception as e:
                    consecutive_errors += 1
                    self.log(f"Error in cycle {self._cycle_count}: {e}", "error")
                    self.log(traceback.format_exc(), "error")
                    if consecutive_errors >= max_retries:
                        self._reload_advisor()
                        consecutive_errors = 0
                    time.sleep(retry_delay)
        except KeyboardInterrupt:
            self.log("Shutdown requested (Ctrl+C)")
        finally:
            self.cleanup()

    def _reload_advisor(self):
        """Reload advisor page to recover from errors."""
        try:
            if self.advisor_page:
                self.advisor_page.reload(wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            self.log("Advisor page reloaded")
        except Exception as e:
            self.log(f"Advisor reload failed: {e}", "error")

    def cleanup(self):
        """Close browser."""
        self.log("Cleaning up...")
        try:
            if self.context:
                self.context.close()
            if self.playwright:
                self.playwright.stop()
        except Exception as e:
            self.log(f"Cleanup error: {e}", "warning")
        self.log("=== Orchestrator stopped ===")

    # ================================================================
    # Test Mode
    # ================================================================

    def test(self):
        """Test mode: verify browser advisor + claude -p execution."""
        self.log("=== TEST MODE ===")

        # --- Test 1: Browser ---
        self.log("--- Test 1: Browser (advisor chat) ---")
        profile_dir = self._get_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        first_time = not (profile_dir / "Default").exists()

        self.log(f"Profile: {profile_dir}")
        self.playwright = sync_playwright().start()
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
            viewport={"width": 1200, "height": 800},
        )

        page = self.context.pages[0] if self.context.pages else self.context.new_page()

        # Navigate to advisor URL (not just claude.ai)
        advisor_url = self.config.get("advisor_chat_url", "https://claude.ai")
        self.log(f"Navigating to: {advisor_url}")
        page.goto(advisor_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        if "login" in page.url.lower() or "sign" in page.url.lower():
            self.log("[ACTION] Login required. Please log in, then press Enter...")
            input()
            # After login, navigate again to the advisor URL
            page.goto(advisor_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)

        self.log(f"Current URL: {page.url}")
        input_field = self._find_input_field(page)
        self.log(f"[{'PASS' if input_field else 'WARN'}] Input field: {'found' if input_field else 'not found'}")

        # --- Test 2: claude -p ---
        self.log("")
        self.log("--- Test 2: Claude Code CLI (claude -p) ---")
        try:
            claude_bin = self._find_claude_binary()
            self.log(f"[PASS] Claude binary: {claude_bin}")

            self.log("Running: claude -p 'pwd'")
            output = self.execute_claude_code("pwd")
            if output and "[ERROR]" not in output:
                self.log(f"[PASS] Output ({len(output)} chars): {output[:300]}...")
            else:
                self.log(f"[WARN] Output: {output[:200]}", "warning")

        except FileNotFoundError as e:
            self.log(f"[FAIL] {e}", "error")
        except Exception as e:
            self.log(f"[FAIL] {e}", "error")

        self.log("")
        self.log("=== TEST COMPLETE ===")
        self.log("Press Enter to close...")
        input()
        self.cleanup()


def main():
    parser = argparse.ArgumentParser(description="OSCAR2 Orchestrator")
    parser.add_argument("--config", "-c", default="orchestrator_config.json")
    parser.add_argument("--test", "-t", action="store_true")
    args = parser.parse_args()

    orch = Orchestrator(config_path=args.config)
    if args.test:
        orch.test()
    else:
        orch.run()


if __name__ == "__main__":
    main()
