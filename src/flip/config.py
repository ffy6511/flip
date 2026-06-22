"""Global configuration (~/.local/share/flip/config.toml).

Translation is a *global* capability, not a per-deck attribute: it is enabled
only when `source_lang != target_lang`. Decks whose source language equals the
target language simply never exercise the translation code path.

The AI-explain backend (command template + output mode) is also global — users
typically have one model provider. Decks keep only the persona/word-count that
are genuinely subject-specific.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from ._toml import load_toml


DEFAULT_HOME = Path.home() / ".local" / "share" / "flip"
DEFAULT_CONFIG_NAME = "config.toml"

# Default command template matches the legacy hardcoded codex invocation.
# Kept here (not in deck.py) so users editing config see what's available.
DEFAULT_EXPLAIN_COMMAND = "codex exec -m {model} -o {outfile} {prompt}"
DEFAULT_EXPLAIN_MODEL = "gpt-5.3-codex-spark"
DEFAULT_EXPLAIN_OUTPUT = "tempfile"   # "stdout" | "tempfile"
DEFAULT_EXPLAIN_TIMEOUT = 90

# A drop-in "fast codex" argv, mirroring the se_regressor.py invocation that
# disabled hooks/plugins, pinned the OpenAI responses wire-api, and ran at low
# reasoning effort. Empty by default — `explain.command` stays the simple
# single-line template for backward compat; users who want the accelerated
# path set `explain.argv = [...]` (or uncomment it in the bootstrapped config).
DEFAULT_EXPLAIN_ARGV = []

# The full accelerated preset. Not the default (kept opt-in), but referenced
# by the bootstrapped config comment and the legacy run_codex_explanation shim
# so there is one source of truth for "what se_regressor.py used to run".
CODEX_FAST_ARGV = [
    "codex", "exec",
    "--ignore-user-config", "--ignore-rules",
    "--disable", "hooks", "--disable", "plugins",
    "-m", "{model}",
    "-c", 'model_provider="openai_https"',
    "-c", 'model_providers.openai_https={name="OpenAI", requires_openai_auth=true, wire_api="responses", supports_websockets=false}',
    "-c", 'model_reasoning_effort="low"',
    "--ephemeral", "--skip-git-repo-check",
    "--color", "never", "--sandbox", "read-only",
    "-o", "{outfile}",
    "{prompt}",
]


@dataclass
class ExplainConfig:
    """Global AI-explain backend.

    Two ways to express the backend's command line:

    `command` — a shell-template string, split with shlex. Best for simple
                backends (zhipu GLM, ollama, a one-liner wrapper script):
                  command = "ollama run {model} {prompt}"

    `argv`    — an explicit list of argv tokens. Best when flags are many,
                order-sensitive, or carry embedded quotes that a string
                template makes unreadable (codex with nested -c values).

    If `argv` is non-empty it wins; otherwise `command` is used. At least one
    of the two must carry the {prompt} placeholder. Placeholders are shared:
      {prompt}   — the explanation prompt (always required)
      {model}    — resolved model id
      {outfile}  — path to a tempfile the backend should write to
                   (only meaningful when output == "tempfile")

    When output == "stdout", flip captures the backend's stdout.
    When output == "tempfile", flip creates the outfile, runs, then reads it.
    """
    command: str = DEFAULT_EXPLAIN_COMMAND
    argv: list = field(default_factory=lambda: list(DEFAULT_EXPLAIN_ARGV))
    model: str = DEFAULT_EXPLAIN_MODEL
    output: str = DEFAULT_EXPLAIN_OUTPUT
    timeout: int = DEFAULT_EXPLAIN_TIMEOUT

    def uses_argv(self) -> bool:
        """True when this config invokes the backend via the `argv` list."""
        return bool(self.argv)

    def validate(self):
        """Return a list of human-readable error strings (empty = valid)."""
        errs = []
        if self.uses_argv():
            if "{prompt}" not in self.argv:
                errs.append("explain.argv must contain {prompt} as one of its tokens")
        else:
            if "{prompt}" not in self.command:
                errs.append("explain.command must contain {prompt}")
        if self.output not in {"stdout", "tempfile"}:
            errs.append(f"explain.output must be 'stdout' or 'tempfile', got {self.output!r}")
        tokens = self.argv if self.uses_argv() else self.command
        if self.output == "tempfile" and "{outfile}" not in tokens:
            where = "explain.argv" if self.uses_argv() else "explain.command"
            errs.append(f"{where} must contain {{outfile}} when output = 'tempfile'")
        try:
            t = int(self.timeout)
            if t <= 0:
                errs.append(f"explain.timeout must be positive, got {t}")
        except (TypeError, ValueError):
            errs.append(f"explain.timeout must be an int, got {self.timeout!r}")
        return errs


@dataclass
class Config:
    home: Path
    source_lang: str = "en"
    target_lang: str = "zh"
    default_deck: str = ""
    explain: ExplainConfig = field(default_factory=ExplainConfig)

    @property
    def translation_enabled(self) -> bool:
        """True only when source and target languages differ."""
        return bool(self.source_lang) and bool(self.target_lang) and self.source_lang != self.target_lang

    @property
    def config_path(self) -> Path:
        return self.home / DEFAULT_CONFIG_NAME

    @property
    def decks_dir(self) -> Path:
        return self.home / "decks"

    def validate(self):
        """Validate all sub-configs; return list of error strings."""
        return self.explain.validate()


def _toml_quote(tok):
    """Quote a string as a TOML basic (double-quoted) string.

    TOML basic strings use JSON-style escapes, so a token containing both
    single and double quotes (codex's nested `-c` value) round-trips safely.
    Used only for the commented argv block in the bootstrap config — the live
    argv list is loaded directly from toml by tomllib, no quoting involved.
    """
    escaped = tok.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def _format_argv_block(argv, *, indent="  "):
    """Render an argv list as commented, multi-line TOML for the bootstrap file.

    Each token is emitted as a TOML basic string, so the whole block is valid
    TOML once the leading `# ` is stripped from every line. That keeps the
    commented preset and CODEX_FAST_ARGV in lockstep (verified by a test).
    """
    lines = ["# argv = ["]
    for tok in argv:
        lines.append("#" + indent + _toml_quote(tok) + ",")
    lines.append("# ]")
    return "\n".join(lines) + "\n"


def _bootstrap_default_config(path: Path) -> None:
    """Write a default config when none exists. Idempotent and best-effort."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    argv_comment = _format_argv_block(CODEX_FAST_ARGV)
    path.write_text(
        '# flip global config\n'
        '# Edit this to switch model providers (e.g. zhipu GLM, openrouter).\n\n'
        'source_lang = "en"\n'
        'target_lang = "zh"\n'
        'default_deck = ""\n\n'
        '[explain]\n'
        '# Shell-template with placeholders: {prompt} {model} {outfile}\n'
        '# Used when `argv` below is empty. Best for simple one-line backends.\n'
        f'command = "{DEFAULT_EXPLAIN_COMMAND}"\n'
        '\n'
        '# Explicit argv list (overrides `command` when non-empty). Best when\n'
        '# flags are many or carry embedded quotes. Uncomment for the codex\n'
        '# accelerated preset (no hooks/plugins, low reasoning, responses api).\n'
        + argv_comment +
        '\n'
        f'model = "{DEFAULT_EXPLAIN_MODEL}"\n'
        f'output = "{DEFAULT_EXPLAIN_OUTPUT}"\n'
        f'timeout = {DEFAULT_EXPLAIN_TIMEOUT}\n',
        encoding="utf-8",
    )


def load_config(home: Path = None) -> Config:
    """Load global config, creating a default on first run.

    Respects $FLIP_HOME for tests; otherwise uses ~/.local/share/flip.
    """
    env_home = os.environ.get("FLIP_HOME")
    resolved_home = Path(env_home) if env_home else (home or DEFAULT_HOME)

    config_path = resolved_home / DEFAULT_CONFIG_NAME
    source_lang = "en"
    target_lang = "zh"
    default_deck = ""
    explain = ExplainConfig()

    if config_path.exists():
        data = load_toml(config_path)
        source_lang = data.get("source_lang", source_lang)
        target_lang = data.get("target_lang", target_lang)
        default_deck = data.get("default_deck", default_deck)
        ex = data.get("explain", {})
        if isinstance(ex, dict):
            raw_argv = ex.get("argv", [])
            if raw_argv is None:
                raw_argv = []
            argv = [str(tok) for tok in raw_argv] if isinstance(raw_argv, list) else []
            explain = ExplainConfig(
                command=ex.get("command", DEFAULT_EXPLAIN_COMMAND),
                argv=argv,
                model=ex.get("model", DEFAULT_EXPLAIN_MODEL),
                output=ex.get("output", DEFAULT_EXPLAIN_OUTPUT),
                timeout=int(ex.get("timeout", DEFAULT_EXPLAIN_TIMEOUT)),
            )
    else:
        # Create the default file so users see it and can edit it.
        _bootstrap_default_config(config_path)

    cfg = Config(
        home=resolved_home,
        source_lang=source_lang,
        target_lang=target_lang,
        default_deck=default_deck,
        explain=explain,
    )
    return cfg


def save_default_deck(config: Config, slug: str) -> None:
    """Persist `default_deck = "<slug>"` back into config.toml.

    Used by the entry menu to remember the last-used deck so the picker
    cursor starts there next time. Only this one field is rewritten; the
    rest of the file is preserved byte-for-byte.
    """
    path = config.config_path
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        wrote = False
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("default_deck") and "=" in stripped:
                lines[i] = f'default_deck = "{slug}"'
                wrote = True
                break
        if not wrote:
            # Field missing in file — insert near the top-level scalar block.
            for i, line in enumerate(lines):
                if line.strip() == "" and any("source_lang" in l for l in lines[:i]):
                    lines.insert(i + 1, f'default_deck = "{slug}"')
                    wrote = True
                    break
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config.default_deck = slug
