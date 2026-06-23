"""Explain-backend executor.

Replaces the hardcoded `codex exec` invocation with a configurable command
spec. The spec comes from the global config (`[explain]`), so users can
point flip at any CLI-style model backend — zhipu GLM, openrouter-cpp,
ollama, a custom wrapper script — without code changes.

Two ways to express the command line:
  command  — a shell template string (shlex.split). Best for simple
             one-liners; placeholder `{prompt}` must be its own token.
  argv     — an explicit list of tokens. Best when flags are many, order-
             sensitive, or carry embedded quotes (codex's nested `-c`
             values). `argv` wins when non-empty.

Placeholders (same for both forms):
  {prompt}   — the explanation prompt text (always substituted)
  {model}    — resolved model id
  {outfile}  — path to a tempfile (only when output == "tempfile")

Output modes:
  "stdout"    — capture the backend's stdout
  "tempfile"  — create {outfile}, run, read it back

The command-template path uses shlex so users may quote args naturally in
the template; in both paths the prompt and outfile are passed as single
argv tokens (never re-split), avoiding injection risks from prompt content.
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
    """Substitute placeholders into the backend command template.

    Placeholder contract:
      {prompt}   — the explanation prompt text. ALWAYS required (a backend
                   with no prompt input is meaningless). Missing -> BackendError.
      {model}    — resolved model id. Substituted even if absent from the
                   template (no-op), so users can ignore it for backends that
                   don't take a model flag.
      {outfile}  — path to a tempfile. Only meaningful when the backend writes
                   its result to a file rather than stdout. If the template
                   contains {outfile} but none was passed (i.e. output mode is
                   "stdout"), we raise rather than emit a literal "{outfile}".

    Returns the fully-rendered command string. Pure function — no subprocess,
    no tempfile creation — so it's directly unit-testable.
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


def build_argv(template, *, prompt, model, outfile=None):
    """Render the template and split it into an argv list, safely.

    This is the correct way to handle prompts that may contain quotes,
    newlines, or shell metacharacters: we substitute placeholders *into the
    already-split argv tokens*, not into the raw template string before
    shlex. That way the prompt is always one atomic argv element and never
    gets re-interpreted by the shell — so a topic containing `"` or `'` can't
    break parsing (the bug behind "No closing quotation").

    A placeholder must occupy its own token (e.g. `... {prompt}`, not
    `prefix{prompt}suffix`); tokens with no placeholder are left as-is.
    """
    # Split the *template* (placeholders are plain alphanumeric-ish tokens,
    # safe for shlex). posix=True so quotes in the template itself are honored.
    try:
        argv = shlex.split(template, posix=True)
    except ValueError as exc:
        raise BackendError(f"explain.command template is malformed: {exc}")
    if not argv:
        raise BackendError("explain.command template renders to an empty command")
    if "{prompt}" not in argv:
        raise BackendError("explain.command must contain {prompt} as a separate token")
    if "{outfile}" in argv and outfile is None:
        raise BackendError(
            "explain.command uses {outfile} but output mode is not 'tempfile'"
        )
    return _substitute_placeholders(argv, prompt=prompt, model=model, outfile=outfile)


def build_argv_from_list(template_argv, *, prompt, model, outfile=None):
    """Substitute placeholders into an explicit argv list (no shlex).

    The `argv` form of `ExplainConfig` already supplies the tokens one per
    element, so there's nothing to split — we just walk the list and replace
    `{prompt}` / `{model}` / `{outfile}` in each element. This is what makes
    codex's nested `-c 'model_providers...={...}'` tokens survivable: the
    toml layer owns the quoting, no shell parser ever touches them.

    Mirrors build_argv's invariants: {prompt} required as a standalone token,
    {outfile} requires a non-None outfile.
    """
    if not template_argv:
        raise BackendError("explain.argv is empty (set explain.command instead)")
    if "{prompt}" not in template_argv:
        raise BackendError("explain.argv must contain {prompt} as a separate token")
    if "{outfile}" in template_argv and outfile is None:
        raise BackendError(
            "explain.argv uses {outfile} but output mode is not 'tempfile'"
        )
    return _substitute_placeholders(template_argv, prompt=prompt, model=model, outfile=outfile)


def _substitute_placeholders(tokens, *, prompt, model, outfile):
    """Replace {prompt}/{model}/{outfile} in each token of an argv list.

    Shared by the command-template path (post-shlex) and the explicit-argv
    path. Pure replacement; a placeholder must be its own token, so this
    never changes token count.
    """
    return [tok.replace("{prompt}", prompt)
              .replace("{model}", model or "")
              .replace("{outfile}", outfile or "")
            for tok in tokens]


def run_explanation(prompt, *, model, config: ExplainConfig, cwd=None):
    """Execute the configured backend and return its plain-text output.

    Never raises on subprocess failure — the TUI keeps running and shows the
    diagnostic inline ("Agent Said 生成失败：..."). The only way to get an
    exception out of here is a programmer bug, not a user config/runtime error.

    Validates config first (cheap, catches template typos before any fork).
    Then dispatches to the stdout or tempfile path based on `config.output`.
    """
    errs = config.validate()
    if errs:
        return "Agent Said 配置错误：\n" + "\n".join(errs)

    timeout = int(config.timeout)

    if config.output == "tempfile":
        return _run_tempfile(prompt, model=model, config=config, cwd=cwd, timeout=timeout)
    return _run_stdout(prompt, model=model, config=config, cwd=cwd, timeout=timeout)


def _resolve_argv(config: ExplainConfig, *, prompt, model, outfile=None):
    """Pick the right builder for this config and return the final argv.

    `argv` wins when set (no shlex — exact tokens), otherwise the command
    template is split. Both paths share `_substitute_placeholders`, so the
    prompt is always a single atomic argv element regardless of source.
    """
    if config.uses_argv():
        return build_argv_from_list(config.argv, prompt=prompt, model=model, outfile=outfile)
    return build_argv(config.command, prompt=prompt, model=model, outfile=outfile)


def _run_stdout(prompt, *, model, config, cwd, timeout):
    """Backend writes its result to stdout; we capture result.stdout.

    This is the mode for most generic CLIs (zhipu GLM, openrouter wrappers,
    ollama, custom scripts). No tempfile is created; {outfile}, if present in
    the command/argv, would have been caught by validate() as an error.
    """
    try:
        argv = _resolve_argv(config, prompt=prompt, model=model)
    except BackendError as exc:
        return "Agent Said 配置错误：" + str(exc)
    try:
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
        # Backend binary not installed / not on PATH. Surface the name so the
        # user knows which command to install, rather than a generic traceback.
        return f"Agent Said 生成失败：找不到命令 ({exc.filename or argv[0]})"
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
    """Backend writes its result to a file via {outfile}; we read it back.

    This is the mode for codex (which uses `-o <file>`). We own the tempfile's
    lifecycle: create it empty before the fork, read it after success, delete
    it in `finally` regardless of outcome.

    The command/argv MUST contain {outfile} (enforced by validate() in the
    tempfile branch) so the backend actually writes somewhere we can read.
    """
    # Create the outfile up front so _resolve_argv has something to substitute.
    fd, outfile = tempfile.mkstemp(suffix=".txt", text=True)
    os.close(fd)
    try:
        try:
            argv = _resolve_argv(config, prompt=prompt, model=model, outfile=outfile)
        except BackendError as exc:
            return "Agent Said 配置错误：" + str(exc)
        try:
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
            return f"Agent Said 生成失败：找不到命令 ({exc.filename or argv[0]})"
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
    """Return the executable name the config invokes, or None if unresolvable.

    Used by `flip config` to tell users whether their backend is on PATH.
    Honors the `argv` list when set, otherwise the `command` template.
    """
    # Placeholder path substituted into {outfile}; only argv[0] is read, so the
    # actual location is irrelevant — but use a platform-neutral tempdir token
    # rather than a hardcoded "/tmp" so the probe doesn't leak a Unix-ism.
    import tempfile
    _probe_outfile = str(Path(tempfile.gettempdir()) / "__flip_probe__")
    try:
        if config.uses_argv():
            # argv[0] is the executable; placeholders don't affect it in
            # practice (users don't put {prompt} first), but substitute anyway
            # so a leading {model} token still resolves cleanly.
            argv = _substitute_placeholders(
                config.argv, prompt="__probe__", model=config.model,
                outfile=_probe_outfile,
            )
        else:
            rendered = render_command(
                config.command,
                prompt="__probe__",
                model=config.model,
                outfile=_probe_outfile,
            )
            argv = shlex.split(rendered)
        return argv[0] if argv else None
    except BackendError:
        return None
