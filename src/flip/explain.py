"""AI explanation: prompt assembly + backend dispatch.

The prompt is built from the deck manifest's `[explain]` table (role, max_chars)
plus the deck's default model. The actual subprocess invocation is delegated to
`flip.backends`, which honors the global `[explain]` command template — so
users can switch providers (codex, zhipu GLM, openrouter, ollama, …) by editing
config.toml without touching code.
"""

from .deck import Deck
from .config import Config, ExplainConfig
from . import backends


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


def run_explanation(prompt, *, config: Config, model=None, cwd=None):
    """Run the configured backend for one explanation.

    `model` overrides config.explain.model when set (e.g. a deck-specific
    default). Returns plain text.
    """
    resolved_model = model or config.explain.model
    return backends.run_explanation(
        prompt, model=resolved_model, config=config.explain, cwd=cwd,
    )


def run_codex_explanation(prompt, *, model=None, timeout=90, cwd=None):
    """Backward-compatible shim: invoke via a stock codex ExplainConfig.

    Kept so existing callers/tests that don't have a full Config still work.
    New code should call run_explanation(prompt, config=...) instead.
    """
    cfg = ExplainConfig(
        command="codex exec -m {model} -o {outfile} {prompt}",
        model=model or "gpt-5.3-codex-spark",
        output="tempfile",
        timeout=timeout,
    )
    return backends.run_explanation(prompt, model=cfg.model, config=cfg, cwd=cwd)
