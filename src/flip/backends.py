"""Explain-backend executor.

Replaces the hardcoded `codex exec` invocation with a configurable shell
template. The template comes from the global config (`[explain].command`),
so users can point flip at any CLI-style model backend — zhipu GLM,
openrouter-cpp, ollama, a custom wrapper script — without code changes.

Placeholders:
  {prompt}   — the explanation prompt text (always substituted)
  {model}    — resolved model id
  {outfile}  — path to a tempfile (only when output == "tempfile")

Output modes:
  "stdout"    — capture the backend's stdout
  "tempfile"  — create {outfile}, run, read it back

The shell splitting uses shlex so users may quote args naturally in the
template; the prompt and outfile are passed as single argv tokens (never
re-split), avoiding injection risks from prompt content.
"""

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import ExplainConfig


class BackendError(Exception):
    """Raised on misconfiguration (bad template) before any subprocess runs."""


def render_command(template, *, prompt, model, outfile=None):
    """Substitute placeholders into the template. Pure function (testable).

    Raises BackendError if {prompt} is missing or {outfile} is needed but absent.
    """
    if "{prompt}" not in template:
        raise BackendError("explain.command is missing the {prompt} placeholder")
    rendered = template.replace("{prompt}", prompt)
    rendered = rendered.replace("{model}", model or "")
    if "{outfile}" in rendered:
        if outfile is None:
            raise BackendError(
                "explain.command uses {outfile} but no outfile was provided "
                "(set output = 'tempfile' or remove {outfile} from the template)"
            )
        rendered = rendered.replace("{outfile}", outfile)
    return rendered


def run_explanation(prompt, *, model, config: ExplainConfig, cwd=None):
    """Execute the configured backend and return its plain-text output.

    Returns a string (the explanation, or a "生成失败" diagnostic). Never raises
    for subprocess failures — they are reported inline so the TUI keeps working.
    """
    errs = config.validate()
    if errs:
        return "Agent Said 配置错误：\n" + "\n".join(errs)

    timeout = int(config.timeout)

    if config.output == "tempfile":
        return _run_tempfile(prompt, model=model, config=config, cwd=cwd, timeout=timeout)
    return _run_stdout(prompt, model=model, config=config, cwd=cwd, timeout=timeout)


def _run_stdout(prompt, *, model, config, cwd, timeout):
    rendered = render_command(config.command, prompt=prompt, model=model)
    try:
        argv = shlex.split(rendered)
        if not argv:
            return "Agent Said 生成失败：命令模板渲染后为空"
        result = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return f"Agent Said 生成失败：找不到命令 ({exc.filename or rendered.split()[0]})"
    except subprocess.TimeoutExpired:
        return f"Agent Said 生成失败：超时 ({timeout}s)"
    except Exception as exc:
        return "Agent Said 生成失败：" + str(exc)

    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        return "Agent Said 生成失败：\n" + message[-1200:]
    explanation = (result.stdout or "").strip()
    return (explanation or "Agent 没有返回内容。").replace("**", "")


def _run_tempfile(prompt, *, model, config, cwd, timeout):
    # Create the outfile up front so render_command has something to substitute.
    fd, outfile = tempfile.mkstemp(suffix=".txt", text=True)
    os.close(fd)
    try:
        rendered = render_command(config.command, prompt=prompt, model=model, outfile=outfile)
        try:
            argv = shlex.split(rendered)
            if not argv:
                return "Agent Said 生成失败：命令模板渲染后为空"
            result = subprocess.run(
                argv,
                cwd=cwd,
                text=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            return f"Agent Said 生成失败：找不到命令 ({exc.filename or rendered.split()[0]})"
        except subprocess.TimeoutExpired:
            return f"Agent Said 生成失败：超时 ({timeout}s)"
        except Exception as exc:
            return "Agent Said 生成失败：" + str(exc)

        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            return "Agent Said 生成失败：\n" + message[-1200:]
        with open(outfile, encoding="utf-8") as f:
            explanation = f.read().strip()
        return (explanation or "Agent 没有返回内容。").replace("**", "")
    finally:
        try:
            os.remove(outfile)
        except OSError:
            pass


def which_backend(config: ExplainConfig):
    """Return the executable name the template invokes, or None if unresolvable.

    Used by `flip config` to tell users whether their backend is on PATH.
    """
    try:
        rendered = render_command(
            config.command,
            prompt="__probe__",
            model=config.model,
            outfile="/tmp/__probe__",
        )
        argv = shlex.split(rendered)
        return argv[0] if argv else None
    except BackendError:
        return None
