"""Task Decomposer: Uses OpenAI API (gpt-4o) to break down vague requests into structured child tasks."""

import json
import os
import logging

logger = logging.getLogger("oscar2.task_decomposer")

SYSTEM_PROMPT = """You are a project planning AI assistant for OSCAR2, an Agent Teams monitoring system.
Your job is to decompose a user's vague or high-level request into a structured list of child tasks
that can be executed by autonomous Claude Code agents.

For each child task, provide:
1. name: Short task name (imperative form, e.g. "Fix WordPress API auth")
2. purpose: Why this task is needed (1 sentence)
3. scope: What files/areas are affected
4. completion_criteria: How to know the task is done
5. test_method: How to verify correctness
6. task_type: One of: code_fix, code_improvement, content_generation, design_build, config_change, testing

IMPORTANT:
- Return ONLY valid JSON array, no markdown fences, no explanation outside JSON
- Each task should be self-contained and executable independently where possible
- Order tasks by logical dependency (earlier tasks first)
- Be specific about file paths and function names when possible
- Keep each task focused (1-2 hours of work max)

Example output format:
[
  {
    "name": "Fix WordPress REST API authentication",
    "purpose": "Current API calls fail with 401 due to expired token refresh logic",
    "scope": "wp_publisher.py, config.json",
    "completion_criteria": "API calls return 200 with valid auth headers",
    "test_method": "Run wp_publisher.py --test and verify successful post creation",
    "task_type": "code_fix"
  }
]"""


def _get_api_key():
    """Get OpenAI API key from environment or config."""
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        key = config.get("oscar", {}).get("openai_api_key")
        if key:
            return key
    except Exception:
        pass
    return None


def decompose(request_text, project_context=""):
    """Decompose a natural language request into structured child tasks using OpenAI gpt-4o.

    Args:
        request_text: The user's request in natural language
        project_context: Optional context about the project

    Returns:
        dict with keys:
            success: bool
            tasks: list of task dicts (if success)
            raw_response: str (the AI's full response)
            error: str (if not success)
    """
    api_key = _get_api_key()
    if not api_key:
        return {
            "success": False,
            "tasks": [],
            "raw_response": "",
            "error": "OpenAI API key not configured. Set OPENAI_API_KEY environment variable.",
        }

    user_message = request_text
    if project_context:
        user_message = f"Project context: {project_context}\n\nRequest: {request_text}"

    raw_text = ""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=4096,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )

        raw_text = response.choices[0].message.content.strip()

        # Parse JSON from response (handle markdown fences if present)
        json_text = raw_text
        if "```" in json_text:
            lines = json_text.split("\n")
            in_block = False
            block_lines = []
            for line in lines:
                if line.strip().startswith("```"):
                    if in_block:
                        break
                    in_block = True
                    continue
                if in_block:
                    block_lines.append(line)
            if block_lines:
                json_text = "\n".join(block_lines)

        tasks = json.loads(json_text)

        if not isinstance(tasks, list):
            tasks = [tasks]

        # Validate task structure
        valid_types = {"code_fix", "code_improvement", "content_generation", "design_build", "config_change", "testing"}
        for task in tasks:
            if task.get("task_type") not in valid_types:
                task["task_type"] = "code_improvement"

        logger.info(f"Decomposed request into {len(tasks)} tasks")
        return {
            "success": True,
            "tasks": tasks,
            "raw_response": raw_text,
            "error": None,
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response as JSON: {e}")
        return {
            "success": False,
            "tasks": [],
            "raw_response": raw_text,
            "error": f"AI response was not valid JSON: {e}",
        }
    except Exception as e:
        logger.error(f"Task decomposition failed: {e}")
        return {
            "success": False,
            "tasks": [],
            "raw_response": raw_text,
            "error": str(e),
        }


def tasks_to_batch_text(tasks):
    """Convert structured task list into a single batch text for Agent Teams."""
    lines = ["以下の全タスクを完了するまで停止しないでください。", "各タスク完了後、自動的に次のタスクに進むこと。", ""]

    for i, task in enumerate(tasks, 1):
        lines.append(f"【タスク{i}: {task.get('name', 'Unnamed')}】")
        lines.append(f"目的: {task.get('purpose', '')}")
        lines.append(f"スコープ: {task.get('scope', '')}")
        lines.append(f"完了条件: {task.get('completion_criteria', '')}")
        lines.append(f"テスト方法: {task.get('test_method', '')}")
        lines.append("")

    return "\n".join(lines)
