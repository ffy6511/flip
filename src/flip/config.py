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


@dataclass
class ExplainConfig:
    """Global AI-explain backend.

    `command` is a shell-template with placeholders:
      {prompt}  — the explanation prompt (always required)
      {model}   — resolved model id
      {outfile} — path to a tempfile the backend should write to
                  (only meaningful when output == "tempfile")

    When output == "stdout", flip captures the backend's stdout.
    When output == "tempfile", flip creates the outfile, runs, then reads it.
    """
    command: str = DEFAULT_EXPLAIN_COMMAND
    model: str = DEFAULT_EXPLAIN_MODEL
    output: str = DEFAULT_EXPLAIN_OUTPUT
    timeout: int = DEFAULT_EXPLAIN_TIMEOUT

    def validate(self):
        """Return a list of human-readable error strings (empty = valid)."""
        errs = []
        if "{prompt}" not in self.command:
            errs.append("explain.command must contain {prompt}")
        if self.output not in {"stdout", "tempfile"}:
            errs.append(f"explain.output must be 'stdout' or 'tempfile', got {self.output!r}")
        if self.output == "tempfile" and "{outfile}" not in self.command:
            errs.append("explain.command must contain {outfile} when output = 'tempfile'")
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


def _bootstrap_default_config(path: Path) -> None:
    """Write a default config when none exists. Idempotent and best-effort."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '# flip global config\n'
        '# Edit this to switch model providers (e.g. zhipu GLM, openrouter).\n\n'
        'source_lang = "en"\n'
        'target_lang = "zh"\n'
        'default_deck = ""\n\n'
        '[explain]\n'
        '# Shell-template with placeholders: {prompt} {model} {outfile}\n'
        f'command = "{DEFAULT_EXPLAIN_COMMAND}"\n'
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
            explain = ExplainConfig(
                command=ex.get("command", DEFAULT_EXPLAIN_COMMAND),
                model=ex.get("model", DEFAULT_EXPLAIN_MODEL),
                output=ex.get("output", DEFAULT_EXPLAIN_OUTPUT),
                timeout=int(ex.get("timeout", DEFAULT_EXPLAIN_TIMEOUT)),
            )
    else:
        # Create the default file so users see it and can edit it.
        _bootstrap_default_config(config_path)

    return Config(
        home=resolved_home,
        source_lang=source_lang,
        target_lang=target_lang,
        default_deck=default_deck,
        explain=explain,
    )
