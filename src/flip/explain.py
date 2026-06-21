"""AI explanation via the codex CLI.

The explanation prompt is built from the deck manifest's `[explain]` table —
the role persona is no longer hardcoded to "软件工程课程助教". The codex
invocation itself is preserved verbatim from se_regressor.py.
"""

import os
import subprocess
import tempfile
from pathlib import Path

from .deck import Deck


def format_question_for_prompt(chapter, q, *, with_translation=True):
    from .tui.render import has_translation
    lines = [
        "Chapter: " + str(chapter),
        "Topic: " + q.get("topic", ""),
        "Answer: " + q.get("answer", ""),
        "Options:",
    ]
    lines.extend(q.get("options", []))
    if with_translation and has_translation(q):
        lines.append("")
        lines.append("Chinese translation:")
        lines.append(q["zh"]["topic"])
        lines.extend(q["zh"]["options"])
    return "\n".join(lines)


def build_prompt(deck: Deck, chapter, q, extra_prompt="", *, with_translation=True):
    role = deck.explain.role or "课程助教"
    max_chars = deck.explain.max_chars or 200
    prompt = (
        f"你是{role}。请解释下面这道选择题。\n"
        "要求：\n"
        "1. 直接用中文回答。\n"
        "2. 先说明正确答案是什么。\n"
        "3. 简洁解释为什么正确选项正确，以及关键干扰项为什么不选。\n"
        f"4. 控制在 {max_chars} 字以内。\n"
        "5. 不要调用工具，不要修改文件。\n"
        "6. 只输出纯文本，不使用 Markdown 样式，不输出 **、标题标记或表格。\n\n"
        + format_question_for_prompt(chapter, q, with_translation=with_translation)
    )
    extra_prompt = (extra_prompt or "").strip()
    if extra_prompt:
        prompt += "\n\n用户追加要求：\n" + extra_prompt
    return prompt


def run_codex_explanation(prompt, *, model=None, timeout=90, cwd=None):
    """Invoke `codex exec` and return its plain-text output.

    Preserves the codex invocation shape from se_regressor.py. `model` is the
    resolved model id (already env-overridden by the caller).
    """
    if model is None:
        model = "gpt-5.3-codex-spark"

    with tempfile.NamedTemporaryFile("r", encoding="utf-8", delete=False) as output_file:
        output_path = output_file.name

    try:
        command = [
            "codex", "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--disable", "hooks",
            "--disable", "plugins",
            "-m", model,
            "-c", 'model_provider="openai_https"',
            "-c", 'model_providers.openai_https={name="OpenAI", requires_openai_auth=true, wire_api="responses", supports_websockets=false}',
            "-c", 'model_reasoning_effort="low"',
            "--ephemeral",
            "--skip-git-repo-check",
            "--color", "never",
            "--sandbox", "read-only",
            "-o", output_path,
            prompt,
        ]
        result = subprocess.run(
            command,
            cwd=cwd or str(Path(__file__).resolve().parent),
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            return "Agent Said 生成失败：\n" + message[-1200:]
        with open(output_path, encoding="utf-8") as f:
            explanation = f.read().strip()
        return (explanation or "Agent 没有返回内容。").replace("**", "")
    except Exception as exc:
        return "Agent Said 生成失败：" + str(exc)
    finally:
        try:
            os.remove(output_path)
        except OSError:
            pass
