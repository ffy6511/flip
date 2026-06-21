import pytest

from flip.backends import render_command, BackendError
from flip.config import ExplainConfig


class TestRenderCommand:
    def test_basic_substitution(self):
        out = render_command("tool run {prompt}", prompt="hi", model="m")
        assert out == "tool run hi"

    def test_model_substitution(self):
        out = render_command("tool -m {model} {prompt}", prompt="hi", model="glm-4")
        assert out == "tool -m glm-4 hi"

    def test_outfile_substitution(self):
        out = render_command("tool -o {outfile} {prompt}",
                             prompt="hi", model="m", outfile="/tmp/x")
        assert out == "tool -o /tmp/x hi"

    def test_missing_prompt_raises(self):
        with pytest.raises(BackendError, match="prompt"):
            render_command("tool run", prompt="hi", model="m")

    def test_outfile_placeholder_without_outfile_raises(self):
        with pytest.raises(BackendError, match="outfile"):
            render_command("tool -o {outfile} {prompt}", prompt="hi", model="m")

    def test_no_model_placeholder_ok(self):
        # model is optional in the template
        out = render_command("tool {prompt}", prompt="hi", model="m")
        assert out == "tool hi"


class TestExplainConfigValidate:
    def test_valid_stdout(self):
        cfg = ExplainConfig(command="tool {prompt}", output="stdout")
        assert cfg.validate() == []

    def test_valid_tempfile(self):
        cfg = ExplainConfig(command="tool -o {outfile} {prompt}", output="tempfile")
        assert cfg.validate() == []

    def test_missing_prompt(self):
        cfg = ExplainConfig(command="tool run", output="stdout")
        errs = cfg.validate()
        assert any("prompt" in e for e in errs)

    def test_tempfile_without_outfile_placeholder(self):
        cfg = ExplainConfig(command="tool {prompt}", output="tempfile")
        errs = cfg.validate()
        assert any("outfile" in e for e in errs)

    def test_bad_output_mode(self):
        cfg = ExplainConfig(command="tool {prompt}", output="weird")
        errs = cfg.validate()
        assert any("stdout" in e for e in errs)

    def test_bad_timeout(self):
        cfg = ExplainConfig(command="tool {prompt}", output="stdout", timeout=-5)
        errs = cfg.validate()
        assert any("timeout" in e for e in errs)


class TestRunExplanation:
    def test_stdout_mode_runs_command(self, tmp_path):
        # Use the shell's `printf` equivalent: python -c prints to stdout.
        cfg = ExplainConfig(
            command="python3 -c print\\(\\'hello\\'\\) {prompt}",
            output="stdout",
        )
        # The above is fragile; instead use a script file.
        script = tmp_path / "fake_backend.py"
        script.write_text(
            "import sys\n"
            "sys.stderr.write('arg=' + sys.argv[1])\n"
            "print('explanation text')\n",
            encoding="utf-8",
        )
        cfg = ExplainConfig(
            command=f"python3 {script} {{prompt}}",
            output="stdout",
            timeout=10,
        )
        from flip.backends import run_explanation
        out = run_explanation("myprompt", model="m", config=cfg)
        assert "explanation text" in out

    def test_tempfile_mode_reads_outfile(self, tmp_path):
        script = tmp_path / "fake_backend.py"
        script.write_text(
            "import sys\n"
            "# argv[1] = outfile, argv[2] = prompt\n"
            "open(sys.argv[1], 'w').write('from file: ' + sys.argv[2])\n",
            encoding="utf-8",
        )
        cfg = ExplainConfig(
            command=f"python3 {script} {{outfile}} {{prompt}}",
            output="tempfile",
            timeout=10,
        )
        from flip.backends import run_explanation
        out = run_explanation("myprompt", model="m", config=cfg)
        assert "from file: myprompt" in out

    def test_missing_executable_reports_error(self):
        cfg = ExplainConfig(
            command="definitely-not-a-real-cli {prompt}",
            output="stdout",
            timeout=5,
        )
        from flip.backends import run_explanation
        out = run_explanation("p", model="m", config=cfg)
        assert "生成失败" in out
        assert "找不到命令" in out

    def test_invalid_config_reports_error(self):
        cfg = ExplainConfig(command="no prompt placeholder", output="stdout")
        from flip.backends import run_explanation
        out = run_explanation("p", model="m", config=cfg)
        assert "配置错误" in out
