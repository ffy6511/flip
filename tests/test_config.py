"""Config-layer coverage for the explain-backend precedence rules.

The explain backend resolves in this order (see load_config):
  1. A non-empty user `argv`     → use it.
  2. A user-overridden `command` → drop argv, use command.
  3. Neither                      → default to CODEX_FAST_ARGV.
These tests pin each branch plus the bootstrapped config file's shape.
"""

import pytest

from flip.config import (
    Config,
    ExplainConfig,
    DEFAULT_EXPLAIN_COMMAND,
    CODEX_FAST_ARGV,
    load_config,
    _bootstrap_default_config,
)


class TestExplainConfigValidate:
    def test_argv_path_valid(self):
        cfg = ExplainConfig(
            argv=["codex", "-o", "{outfile}", "{prompt}"],
            output="tempfile",
        )
        assert cfg.validate() == []

    def test_argv_missing_prompt(self):
        cfg = ExplainConfig(argv=["codex", "exec"], output="stdout")
        errs = cfg.validate()
        assert any("argv" in e and "prompt" in e for e in errs)

    def test_argv_tempfile_without_outfile(self):
        cfg = ExplainConfig(argv=["codex", "{prompt}"], output="tempfile")
        errs = cfg.validate()
        assert any("argv" in e and "outfile" in e for e in errs)

    def test_command_ignored_for_prompt_check_when_argv_set(self):
        # command has no {prompt}, but argv does — argv wins, so it's valid.
        cfg = ExplainConfig(
            command="codex exec",  # would be invalid alone
            argv=["codex", "{prompt}"],
            output="stdout",
        )
        assert cfg.validate() == []

    def test_uses_argv_flag(self):
        assert ExplainConfig(argv=["x", "{prompt}"]).uses_argv() is True
        assert ExplainConfig(command="x {prompt}").uses_argv() is False


class TestLoadConfigArgv:
    def test_argv_read_from_toml(self, tmp_path):
        (tmp_path / "config.toml").write_text(
            'source_lang = "en"\n'
            'target_lang = "zh"\n'
            '\n'
            '[explain]\n'
            'argv = ["codex", "exec", "-m", "{model}", "-o", "{outfile}", "{prompt}"]\n'
            'output = "tempfile"\n',
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        assert cfg.explain.uses_argv()
        assert cfg.explain.argv[0] == "codex"
        assert cfg.explain.argv[-1] == "{prompt}"
        assert cfg.explain.validate() == []

    def test_command_fallback_when_no_argv(self, tmp_path):
        (tmp_path / "config.toml").write_text(
            '[explain]\n'
            'command = "ollama run {model} {prompt}"\n'
            'output = "stdout"\n',
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        assert not cfg.explain.uses_argv()
        assert cfg.explain.command == "ollama run {model} {prompt}"

    def test_argv_null_treated_as_empty(self, tmp_path):
        # A stray `argv =` with no value shouldn't crash load_config.
        (tmp_path / "config.toml").write_text(
            '[explain]\n'
            'command = "tool {prompt}"\n'
            'output = "stdout"\n'
            'argv = []\n',
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        assert not cfg.explain.uses_argv()
        assert cfg.explain.validate() == []

    def test_non_list_argv_ignored(self, tmp_path):
        # Defensive: a malformed argv (string instead of list) must not break
        # loading; it's treated as "argv not set", falling back to command.
        (tmp_path / "config.toml").write_text(
            '[explain]\n'
            'command = "tool {prompt}"\n'
            'output = "stdout"\n'
            'argv = "not a list"\n',
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        assert not cfg.explain.uses_argv()

    def test_default_argv_is_codex_fast_when_user_sets_nothing(self, tmp_path):
        # The core new behavior: a user who sets neither argv nor command gets
        # the codex accelerated preset (CODEX_FAST_ARGV) by default, including
        # --skip-git-repo-check so it runs anywhere.
        (tmp_path / "config.toml").write_text(
            '[explain]\nmodel = "gpt-5.3-codex-spark"\n',
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        assert cfg.explain.uses_argv()
        assert cfg.explain.argv == list(CODEX_FAST_ARGV)
        assert "--skip-git-repo-check" in cfg.explain.argv

    def test_overridden_command_drops_default_argv(self, tmp_path):
        # A user who sets `command` to a different backend (e.g. ollama) must
        # NOT be forced onto the codex argv preset — argv is cleared so the
        # command path takes over.
        (tmp_path / "config.toml").write_text(
            '[explain]\ncommand = "ollama run {model} {prompt}"\noutput = "stdout"\n',
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        assert not cfg.explain.uses_argv()
        assert cfg.explain.command == "ollama run {model} {prompt}"

    def test_default_command_alone_does_not_drop_argv(self, tmp_path):
        # Edge case: if the user happens to write the *default* command string
        # verbatim, that should still count as "not overridden" → argv default
        # (CODEX_FAST_ARGV) wins. Guards against the bootstrapped file's
        # commented `command = "..."` line confusing the precedence.
        from flip.config import DEFAULT_EXPLAIN_COMMAND
        (tmp_path / "config.toml").write_text(
            f'[explain]\ncommand = "{DEFAULT_EXPLAIN_COMMAND}"\n',
            encoding="utf-8",
        )
        cfg = load_config(tmp_path)
        assert cfg.explain.uses_argv()
        assert cfg.explain.argv == list(CODEX_FAST_ARGV)

    def test_does_not_auto_install_bundled_decks(self, tmp_path):
        # Regression for goal ③: load_config must NOT silently install bundled
        # decks. The only install path is the Bootstrap tab. A removed deck must
        # not reappear on the next launch.
        cfg = load_config(tmp_path)
        decks_dir = cfg.decks_dir
        if decks_dir.exists():
            assert not any(decks_dir.iterdir()), \
                "load_config created deck directories — auto-install leaked back in"
        else:
            # decks_dir not even created is also fine (and the stronger guarantee).
            assert True


class TestBootstrapDefault:
    def test_bootstrap_mentions_argv_and_codex_preset(self, tmp_path):
        path = tmp_path / "config.toml"
        _bootstrap_default_config(path)
        text = path.read_text(encoding="utf-8")
        # The argv block is commented out but documented.
        assert "# argv = [" in text
        assert "codex" in text
        # And the default command still ships as the fallback.
        assert DEFAULT_EXPLAIN_COMMAND in text

    def test_bootstrap_argv_comment_round_trips(self, tmp_path):
        """The commented argv block, if uncommented verbatim, should be valid.

        Pins that the bootstrap docstring stays in sync with CODEX_FAST_ARGV
        (no drift between the two sources of the codex preset).
        """
        path = tmp_path / "config.toml"
        _bootstrap_default_config(path)
        text = path.read_text(encoding="utf-8")
        # Strip the leading "# " from each line of the argv block, leaving TOML.
        toml_lines = []
        in_argv = False
        for line in text.splitlines():
            if line.startswith("# argv = ["):
                toml_lines.append("argv = [")
                in_argv = True
                continue
            if in_argv:
                if line.startswith("# ]"):
                    toml_lines.append("]")
                    in_argv = False
                    continue
                # "#   <token>," -> "<token>,"
                toml_lines.append(line[2:])
        # load_toml reads a file in binary mode; write the snippet out.
        snippet = tmp_path / "argv_only.toml"
        snippet.write_text("\n".join(toml_lines) + "\n", encoding="utf-8")
        from flip._toml import load_toml
        parsed = load_toml(snippet)
        assert parsed["argv"] == CODEX_FAST_ARGV


class TestCodexFastArgv:
    def test_first_token_is_codex(self):
        assert CODEX_FAST_ARGV[0] == "codex"

    def test_contains_key_acceleration_flags(self):
        joined = " ".join(CODEX_FAST_ARGV)
        # The three levers the legacy se_regressor.py pulled for speed.
        assert "--disable" in joined and "hooks" in joined and "plugins" in joined
        assert "model_reasoning_effort" in joined
        assert "wire_api" in joined  # pinned provider config

    def test_has_required_placeholders(self):
        assert "{model}" in CODEX_FAST_ARGV
        assert "{outfile}" in CODEX_FAST_ARGV
        assert "{prompt}" in CODEX_FAST_ARGV
        # And it validates as a tempfile config.
        cfg = ExplainConfig(argv=list(CODEX_FAST_ARGV), output="tempfile")
        assert cfg.validate() == []
