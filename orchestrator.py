#!/usr/bin/env python3
"""Orchestrator: Automated relay between claude.ai advisor chat and Claude Code GUI.

Uses Playwright to control Chrome browser with two tabs:
- Tab 1: Advisor chat (claude.ai/chat/xxxxx) — strategic analysis
- Tab 2: Claude Code GUI (claude.ai/code) — implementation

The orchestrator relays messages between them in a continuous loop:
1. Wait for Claude Code to finish
2. Copy output to advisor chat
3. Wait for advisor response
4. Copy advisor instructions to Claude Code
5. Repeat

Usage:
    python orchestrator.py
    python orchestrator.py --config orchestrator_config.json
    python orchestrator.py --test  (test mode: opens browser, verifies elements)
"""

import sys
import os
import time
import json
import logging
import traceback
import argparse
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


class Orchestrator:
    """Automated message relay between claude.ai advisor and Claude Code."""

    def __init__(self, config_path="orchestrator_config.json"):
        self.config = self._load_config(config_path)
        self.playwright = None
        self.browser = None
        self.context = None
        self.advisor_page = None
        self.claude_code_page = None
        self._last_advisor_msg_count = 0
        self._last_code_msg_count = 0
        self._last_activity = datetime.now()
        self._cycle_count = 0

        # Setup logging
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
        """Load config from JSON file."""
        path = Path(config_path)
        if not path.exists():
            # Look in script directory
            path = Path(__file__).parent / config_path
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        self.log(f"Config not found at {config_path}, using defaults", "warning")
        return {}

    def log(self, message, level="info"):
        """Log to file and console."""
        getattr(self.logger, level, self.logger.info)(message)

    # ================================================================
    # Browser Setup
    # ================================================================

    def setup_browser(self):
        """Launch Chrome with persistent profile (logged-in state) and open two tabs."""
        chrome_profile = self.config.get(
            "chrome_profile_path",
            r"C:\Users\koji3\AppData\Local\Google\Chrome\User Data"
        )

        self.log(f"Launching Chrome with profile: {chrome_profile}")
        self.playwright = sync_playwright().start()

        # Use a separate profile directory to avoid Chrome lock conflicts
        # Copy approach: use playwright's own user data dir with Chrome cookies
        profile_dir = Path(chrome_profile)
        if not profile_dir.exists():
            self.log(f"Chrome profile not found: {chrome_profile}", "warning")

        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                channel="chrome",  # Use installed Chrome
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                ],
                viewport={"width": 1400, "height": 900},
                slow_mo=100,  # Small delay for stability
            )
        except Exception as e:
            self.log(f"Chrome launch with profile failed: {e}", "warning")
            self.log("Trying with temporary profile (you may need to log in manually)")
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(Path.home() / ".orchestrator_chrome_profile"),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1400, "height": 900},
                slow_mo=100,
            )

        # Open advisor chat tab
        advisor_url = self.config.get("advisor_chat_url", "")
        if not advisor_url:
            self.log("advisor_chat_url not set in config! Set it before running.", "error")
            raise ValueError("advisor_chat_url is required in orchestrator_config.json")

        if self.context.pages:
            self.advisor_page = self.context.pages[0]
        else:
            self.advisor_page = self.context.new_page()

        self.log(f"Opening advisor chat: {advisor_url}")
        self.advisor_page.goto(advisor_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        # Open Claude Code tab
        code_url = self.config.get("claude_code_url", "https://claude.ai/code")
        self.claude_code_page = self.context.new_page()
        self.log(f"Opening Claude Code: {code_url}")
        self.claude_code_page.goto(code_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        self.log("Browser setup complete — 2 tabs open")

    # ================================================================
    # DOM Interaction Helpers
    # ================================================================

    def _find_input_field(self, page):
        """Find the chat input field on claude.ai.

        Tries multiple selectors for robustness against DOM changes.
        """
        selectors = [
            # contenteditable div (claude.ai primary)
            'div[contenteditable="true"]',
            # ProseMirror editor
            'div.ProseMirror[contenteditable="true"]',
            # textarea fallback
            "textarea",
            # data-testid based
            '[data-testid="chat-input"]',
            '[data-testid="message-input"]',
            # role based
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
        """Find the Stop button (visible during response generation)."""
        selectors = [
            'button:has-text("Stop")',
            '[aria-label="Stop"]',
            '[data-testid="stop-button"]',
            'button:has-text("stop")',
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
        """Get all message blocks from the chat."""
        selectors = [
            '[data-testid*="message"]',
            '[class*="Message"]',
            '[class*="message"]',
            'div[data-is-streaming]',
            # Generic: divs inside the conversation area
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

    # ================================================================
    # Core Operations
    # ================================================================

    def send_message(self, page, text):
        """Send a message in the chat input field.

        Uses clipboard paste for long texts (>1000 chars).
        """
        if not text or not text.strip():
            self.log("Empty message, skipping send", "warning")
            return False

        input_field = self._find_input_field(page)
        if not input_field:
            self.log("Could not find input field!", "error")
            return False

        try:
            input_field.click()
            time.sleep(0.5)

            if len(text) > 1000:
                # Clipboard paste for long text
                self.log(f"Pasting long text ({len(text)} chars) via clipboard")
                page.evaluate(f"navigator.clipboard.writeText({json.dumps(text)})")
                time.sleep(0.3)
                page.keyboard.press("Control+v")
                time.sleep(0.5)
            else:
                # Direct type for short text
                input_field.fill(text)
                time.sleep(0.3)

            # Send with Enter
            page.keyboard.press("Enter")
            self.log(f"Message sent ({len(text)} chars): {text[:150]}...")
            self._last_activity = datetime.now()
            time.sleep(2)  # Wait for send to register
            return True

        except Exception as e:
            self.log(f"Failed to send message: {e}", "error")
            return False

    def get_latest_response(self, page):
        """Get the text of the latest assistant response."""
        messages = self._get_message_elements(page)
        if not messages:
            self.log("No messages found on page", "warning")
            return ""

        try:
            # Get text from the last message element
            last = messages[-1]
            text = last.inner_text(timeout=5000)
            return text.strip() if text else ""
        except Exception as e:
            self.log(f"Failed to get latest response: {e}", "warning")
            return ""

    def wait_for_response_complete(self, page, timeout_seconds=None):
        """Wait until the AI response generation is complete.

        Detection methods:
        1. Stop button disappears
        2. Input field becomes active
        3. Message count stabilizes
        """
        if timeout_seconds is None:
            timeout_seconds = self.config.get("cycle_timeout_minutes", 60) * 60

        self.log(f"Waiting for response (timeout={timeout_seconds}s)...")
        start = time.time()
        check_interval = 10  # seconds

        # Get initial message count
        initial_msgs = len(self._get_message_elements(page))
        stable_count = 0

        while time.time() - start < timeout_seconds:
            # Method 1: Check if Stop button is gone
            stop_btn = self._find_stop_button(page)
            if stop_btn is None:
                # No stop button — might be done. Verify with input field check.
                input_field = self._find_input_field(page)
                if input_field:
                    try:
                        if input_field.is_enabled(timeout=2000):
                            # Double-check: wait a moment and verify message count is stable
                            time.sleep(3)
                            current_msgs = len(self._get_message_elements(page))
                            if current_msgs > initial_msgs:
                                self.log(f"Response complete (messages: {initial_msgs} -> {current_msgs})")
                                return True
                            stable_count += 1
                            if stable_count >= 2:
                                self.log("Response appears complete (stable)")
                                return True
                    except Exception:
                        pass

            elapsed = int(time.time() - start)
            if elapsed % 60 == 0 and elapsed > 0:
                self.log(f"Still waiting... ({elapsed}s elapsed)")

            time.sleep(check_interval)

        self.log(f"Response wait timed out after {timeout_seconds}s", "warning")
        return False

    def check_if_idle(self, page):
        """Check if the page is idle (no active generation)."""
        stop_btn = self._find_stop_button(page)
        if stop_btn:
            return False
        input_field = self._find_input_field(page)
        if input_field:
            try:
                return input_field.is_enabled(timeout=2000)
            except Exception:
                return False
        return True

    def nudge_if_stalled(self, page, stall_minutes=None):
        """Send a nudge message if no activity for stall_minutes."""
        if stall_minutes is None:
            stall_minutes = self.config.get("stall_timeout_minutes", 30)

        elapsed = (datetime.now() - self._last_activity).total_seconds() / 60
        if elapsed >= stall_minutes:
            self.log(f"Stall detected ({elapsed:.0f} min). Sending nudge.")
            self.send_message(
                page,
                "作業は完了しましたか？完了していれば結果を報告してください。"
                "まだ作業中であれば現在のステータスを教えてください。"
            )
            return True
        return False

    # ================================================================
    # Main Cycle
    # ================================================================

    def run_cycle(self):
        """Execute one relay cycle: Code -> Advisor -> Code."""
        self.log("--- Cycle start ---")

        # Step 1: Wait for Claude Code to finish
        self.log("Step 1: Waiting for Claude Code response...")
        code_done = self.wait_for_response_complete(self.claude_code_page)
        if not code_done:
            self.nudge_if_stalled(self.claude_code_page)
            return

        # Step 2: Get Claude Code output
        output = self.get_latest_response(self.claude_code_page)
        if not output:
            self.log("No output from Claude Code, skipping cycle", "warning")
            time.sleep(30)
            return
        self.log(f"Claude Code output ({len(output)} chars): {output[:200]}...")

        # Step 3: Send to advisor
        self.log("Step 2: Sending output to advisor...")
        sent = self.send_message(self.advisor_page, output)
        if not sent:
            self.log("Failed to send to advisor, retrying next cycle", "error")
            time.sleep(30)
            return

        # Step 4: Wait for advisor response
        self.log("Step 3: Waiting for advisor response...")
        advisor_done = self.wait_for_response_complete(self.advisor_page)
        if not advisor_done:
            self.nudge_if_stalled(self.advisor_page)
            return

        # Step 5: Get advisor response
        response = self.get_latest_response(self.advisor_page)
        if not response:
            self.log("No response from advisor, skipping", "warning")
            time.sleep(30)
            return
        self.log(f"Advisor response ({len(response)} chars): {response[:200]}...")

        # Step 6: Send to Claude Code
        self.log("Step 4: Sending advisor instructions to Claude Code...")
        sent = self.send_message(self.claude_code_page, response)
        if not sent:
            self.log("Failed to send to Claude Code", "error")
            time.sleep(30)
            return

        self.log("--- Cycle complete ---")

    def run(self):
        """Main loop. Runs cycles continuously until interrupted."""
        self.setup_browser()
        self.log("=== Orchestrator started ===")
        self.log(f"Advisor: {self.config.get('advisor_chat_url', 'N/A')}")
        self.log(f"Claude Code: {self.config.get('claude_code_url', 'N/A')}")

        retry_delay = self.config.get("retry_delay_seconds", 30)
        max_retries = self.config.get("retry_max", 3)
        consecutive_errors = 0

        try:
            while True:
                try:
                    self.run_cycle()
                    self._cycle_count += 1
                    consecutive_errors = 0
                    self.log(f"Cycle {self._cycle_count} completed successfully")
                except PlaywrightTimeout as e:
                    consecutive_errors += 1
                    self.log(f"Timeout in cycle: {e}", "warning")
                    if consecutive_errors >= max_retries:
                        self.log(f"{max_retries} consecutive errors, reloading pages", "warning")
                        self._reload_pages()
                        consecutive_errors = 0
                    time.sleep(retry_delay)
                except Exception as e:
                    consecutive_errors += 1
                    self.log(f"Error in cycle {self._cycle_count}: {e}", "error")
                    self.log(traceback.format_exc(), "error")
                    if consecutive_errors >= max_retries:
                        self.log("Too many errors, reloading pages", "warning")
                        self._reload_pages()
                        consecutive_errors = 0
                    time.sleep(retry_delay)
        except KeyboardInterrupt:
            self.log("Shutdown requested (Ctrl+C)")
        finally:
            self.cleanup()

    def _reload_pages(self):
        """Reload both pages to recover from errors."""
        try:
            if self.advisor_page:
                self.advisor_page.reload(wait_until="domcontentloaded", timeout=30000)
            if self.claude_code_page:
                self.claude_code_page.reload(wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            self.log("Pages reloaded")
        except Exception as e:
            self.log(f"Page reload failed: {e}", "error")

    def cleanup(self):
        """Close browser and playwright."""
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
        """Test mode: open browser, verify elements, send test message to a new chat."""
        self.log("=== TEST MODE ===")

        # Use a temp profile for testing
        self.playwright = sync_playwright().start()
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(Path.home() / ".orchestrator_test_profile"),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1200, "height": 800},
        )

        page = self.context.new_page()
        self.log("Opening claude.ai...")
        page.goto("https://claude.ai", wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        # Check if we can find input field
        self.log("Looking for input field...")
        input_field = self._find_input_field(page)
        if input_field:
            self.log("[PASS] Input field found")
        else:
            self.log("[INFO] Input field not found - you may need to log in first")

        # Check page title
        title = page.title()
        self.log(f"Page title: {title}")

        # Check if login is required
        if "login" in page.url.lower() or "sign" in page.url.lower():
            self.log("[INFO] Login page detected. Please log in manually in the browser window.")
            self.log("[INFO] After logging in, press Enter in this terminal to continue...")
            input("Press Enter after logging in...")

            # Re-check input field after login
            input_field = self._find_input_field(page)
            if input_field:
                self.log("[PASS] Input field found after login")
            else:
                self.log("[WARN] Input field still not found")

        self.log("=== TEST COMPLETE ===")
        self.log("Press Enter to close the browser...")
        input()
        self.cleanup()


# ================================================================
# CLI Entry Point
# ================================================================

def main():
    parser = argparse.ArgumentParser(description="OSCAR2 Orchestrator - Advisor/Code relay")
    parser.add_argument("--config", "-c", default="orchestrator_config.json",
                        help="Path to config file")
    parser.add_argument("--test", "-t", action="store_true",
                        help="Test mode: verify browser and elements")
    args = parser.parse_args()

    orch = Orchestrator(config_path=args.config)

    if args.test:
        orch.test()
    else:
        orch.run()


if __name__ == "__main__":
    main()
