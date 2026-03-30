#!/usr/bin/env python3
"""Orchestrator: Automated relay between claude.ai advisor chat and Claude Code CLI.

- Advisor chat (claude.ai/chat): controlled via Playwright (browser)
- Claude Code: controlled via subprocess (CLI stdin/stdout)

The orchestrator relays messages between them in a continuous loop:
1. Wait for Claude Code CLI to finish (stdout goes silent)
2. Copy output to advisor chat (Playwright)
3. Wait for advisor response (Playwright)
4. Copy advisor instructions to Claude Code CLI (stdin)
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
import logging
import traceback
import argparse
import subprocess
import threading
import queue
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
        # Claude Code subprocess
        self.claude_process = None
        self._stdout_queue = queue.Queue()
        self._stdout_thread = None
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
    # Browser Setup (Advisor chat only)
    # ================================================================

    def _get_profile_dir(self):
        default_dir = r"C:\Users\koji3\AppData\Local\Google\Chrome\OrchestratorProfile"
        return Path(self.config.get("chrome_profile_copy_dir", default_dir))

    def setup_browser(self):
        """Launch Chromium for advisor chat only (Claude Code uses subprocess)."""
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

        if first_time:
            page = self.context.pages[0] if self.context.pages else self.context.new_page()
            page.goto("https://claude.ai", wait_until="domcontentloaded", timeout=60000)
            self.log(">>> Please log in to claude.ai in the browser window <<<")
            self.log(">>> After logging in, press Enter here to continue <<<")
            input()
            self.log("Login saved.")

        # Open advisor chat
        advisor_url = self.config.get("advisor_chat_url", "")
        if not advisor_url:
            raise ValueError("advisor_chat_url is required in orchestrator_config.json")

        if self.context.pages:
            self.advisor_page = self.context.pages[0]
        else:
            self.advisor_page = self.context.new_page()

        self.log(f"Opening advisor: {advisor_url}")
        self.advisor_page.goto(advisor_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        self.log("Advisor chat ready.")

    # ================================================================
    # Claude Code CLI (subprocess)
    # ================================================================

    def setup_claude_code(self):
        """Start Claude Code CLI as a subprocess."""
        project_dir = self.config.get(
            "claude_code_project_dir",
            r"C:\Users\koji3\OneDrive\デスクトップ\blog_automation"
        )
        self.log(f"Starting Claude Code CLI in: {project_dir}")

        # Find claude command
        import shutil
        claude_bin = shutil.which("claude")
        if not claude_bin:
            appdata = os.environ.get("APPDATA", "")
            candidate = os.path.join(appdata, "npm", "claude.cmd")
            if os.path.isfile(candidate):
                claude_bin = candidate
        if not claude_bin:
            raise FileNotFoundError("claude CLI not found in PATH or npm global")

        self.log(f"Claude binary: {claude_bin}")

        use_shell = sys.platform == "win32" and claude_bin.endswith(".cmd")

        self.claude_process = subprocess.Popen(
            [claude_bin, "--dangerously-skip-permissions"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge stderr into stdout
            cwd=project_dir,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line buffered
            shell=use_shell,
        )

        # Start background thread to read stdout without blocking
        self._stdout_thread = threading.Thread(
            target=self._read_stdout_loop, daemon=True
        )
        self._stdout_thread.start()
        self.log(f"Claude Code CLI started (PID={self.claude_process.pid})")
        time.sleep(3)  # let it initialize

    def _read_stdout_loop(self):
        """Background thread: read lines from Claude Code stdout into a queue."""
        try:
            for line in self.claude_process.stdout:
                self._stdout_queue.put(line)
        except (ValueError, OSError):
            pass  # process closed

    def send_to_claude_code(self, text):
        """Send text to Claude Code CLI via stdin."""
        if not self.claude_process or self.claude_process.poll() is not None:
            self.log("Claude Code process is not running!", "error")
            return False
        try:
            self.claude_process.stdin.write(text + "\n")
            self.claude_process.stdin.flush()
            self.log(f"Sent to Claude Code ({len(text)} chars): {text[:150]}...")
            self._last_activity = datetime.now()
            return True
        except (BrokenPipeError, OSError) as e:
            self.log(f"Failed to send to Claude Code: {e}", "error")
            return False

    def get_claude_code_output(self, idle_timeout=30, max_timeout=None):
        """Read Claude Code CLI output until it goes silent.

        Args:
            idle_timeout: seconds of silence before considering output complete
            max_timeout: max total seconds to wait (default: cycle_timeout_minutes * 60)

        Returns:
            Collected output as a single string.
        """
        if max_timeout is None:
            max_timeout = self.config.get("cycle_timeout_minutes", 60) * 60

        self.log(f"Reading Claude Code output (idle={idle_timeout}s, max={max_timeout}s)...")
        lines = []
        start = time.time()
        last_output = time.time()

        while True:
            elapsed = time.time() - start
            idle = time.time() - last_output

            # Max timeout
            if elapsed >= max_timeout:
                self.log(f"Max timeout ({max_timeout}s) reached", "warning")
                break

            # Idle timeout — output is done
            if idle >= idle_timeout and lines:
                self.log(f"Output complete ({idle:.0f}s idle, {len(lines)} lines)")
                break

            # Try to read from queue
            try:
                line = self._stdout_queue.get(timeout=1)
                lines.append(line)
                last_output = time.time()
                # Log progress every 50 lines
                if len(lines) % 50 == 0:
                    self.log(f"  ...{len(lines)} lines read so far")
            except queue.Empty:
                # No output available — check if process died
                if self.claude_process.poll() is not None:
                    self.log("Claude Code process ended", "warning")
                    break

        output = "".join(lines).strip()
        self.log(f"Claude Code output total: {len(output)} chars, {len(lines)} lines")
        return output

    # ================================================================
    # Advisor Chat (Playwright)
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
                self.log(f"Pasting long text ({len(text)} chars) via clipboard")
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
            self.log(f"Failed to send message: {e}", "error")
            return False

    def get_latest_response(self, page):
        """Get the latest assistant response from advisor chat."""
        messages = self._get_message_elements(page)
        if not messages:
            return ""
        try:
            return messages[-1].inner_text(timeout=5000).strip()
        except Exception as e:
            self.log(f"Failed to get response: {e}", "warning")
            return ""

    def wait_for_advisor_response(self, timeout_seconds=None):
        """Wait for advisor chat to finish generating response."""
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
                                self.log(f"Advisor response complete ({initial_msgs}->{current_msgs} msgs)")
                                return True
                            stable_count += 1
                            if stable_count >= 2:
                                self.log("Advisor response appears complete (stable)")
                                return True
                    except Exception:
                        pass

            elapsed = int(time.time() - start)
            if elapsed % 60 == 0 and elapsed > 0:
                self.log(f"Still waiting for advisor... ({elapsed}s)")
            time.sleep(10)

        self.log(f"Advisor response timed out after {timeout_seconds}s", "warning")
        return False

    # ================================================================
    # Main Cycle
    # ================================================================

    def run_cycle(self):
        """Execute one relay cycle: Code output -> Advisor -> Code input."""
        self.log("--- Cycle start ---")

        # Step 1: Read Claude Code output (waits until it goes silent)
        self.log("Step 1: Reading Claude Code output...")
        output = self.get_claude_code_output()
        if not output:
            self.log("No output from Claude Code, waiting...", "warning")
            time.sleep(30)
            return
        self.log(f"Claude Code produced {len(output)} chars")

        # Step 2: Send to advisor
        self.log("Step 2: Sending to advisor chat...")
        sent = self.send_message(self.advisor_page, output)
        if not sent:
            self.log("Failed to send to advisor", "error")
            time.sleep(30)
            return

        # Step 3: Wait for advisor response
        self.log("Step 3: Waiting for advisor response...")
        done = self.wait_for_advisor_response()
        if not done:
            self.log("Advisor timeout", "warning")
            return

        # Step 4: Get advisor response
        response = self.get_latest_response(self.advisor_page)
        if not response:
            self.log("Empty advisor response", "warning")
            time.sleep(30)
            return
        self.log(f"Advisor response: {len(response)} chars")

        # Step 5: Send to Claude Code
        self.log("Step 4: Sending to Claude Code CLI...")
        sent = self.send_to_claude_code(response)
        if not sent:
            self.log("Failed to send to Claude Code", "error")
            time.sleep(30)
            return

        self.log("--- Cycle complete ---")

    def run(self):
        """Main loop."""
        self.setup_browser()
        self.setup_claude_code()
        self.log("=== Orchestrator started ===")
        self.log(f"Mode: subprocess (CLI)")
        self.log(f"Advisor: {self.config.get('advisor_chat_url', 'N/A')}")
        self.log(f"Project: {self.config.get('claude_code_project_dir', 'N/A')}")

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
        """Shut down Claude Code process and browser."""
        self.log("Cleaning up...")
        # Stop Claude Code CLI
        if self.claude_process and self.claude_process.poll() is None:
            try:
                self.claude_process.stdin.close()
                self.claude_process.terminate()
                self.claude_process.wait(timeout=10)
                self.log("Claude Code CLI terminated")
            except Exception as e:
                self.log(f"Claude Code cleanup error: {e}", "warning")
                try:
                    self.claude_process.kill()
                except Exception:
                    pass
        # Close browser
        try:
            if self.context:
                self.context.close()
            if self.playwright:
                self.playwright.stop()
        except Exception as e:
            self.log(f"Browser cleanup error: {e}", "warning")
        self.log("=== Orchestrator stopped ===")

    # ================================================================
    # Test Mode
    # ================================================================

    def test(self):
        """Test mode: verify browser + Claude Code CLI."""
        self.log("=== TEST MODE ===")

        # --- Test 1: Browser (advisor) ---
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
        page.goto("https://claude.ai", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        if "login" in page.url.lower() or "sign" in page.url.lower():
            self.log("[ACTION] Login required. Please log in, then press Enter...")
            input()
            time.sleep(3)

        input_field = self._find_input_field(page)
        self.log(f"[{'PASS' if input_field else 'WARN'}] Advisor input field: {'found' if input_field else 'not found'}")

        # --- Test 2: Claude Code CLI ---
        self.log("")
        self.log("--- Test 2: Claude Code CLI (subprocess) ---")
        try:
            self.setup_claude_code()
            self.log("[PASS] Claude Code CLI started")

            # Send a test command
            self.log("Sending test: 'echo test from orchestrator'")
            self.send_to_claude_code("echo test from orchestrator と表示してください")
            time.sleep(5)

            # Read output
            output = self.get_claude_code_output(idle_timeout=15, max_timeout=60)
            if output:
                self.log(f"[PASS] Got output ({len(output)} chars): {output[:200]}...")
            else:
                self.log("[WARN] No output received (CLI may need more time)")

        except FileNotFoundError as e:
            self.log(f"[FAIL] Claude CLI not found: {e}", "error")
        except Exception as e:
            self.log(f"[FAIL] Claude Code test error: {e}", "error")

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
