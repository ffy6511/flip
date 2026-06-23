# Deck Manifest

Each deck lives at `~/.local/share/flip/decks/<slug>/` and carries a `manifest.toml` that makes the deck's subject-specific assumptions **explicit** instead of hardcoded in the engine.

## File

`~/.local/share/flip/decks/<slug>/manifest.toml` — TOML format.

## Example

```toml
[deck]
name = "软件工程"          # human-readable subject name
slug = "se"               # == directory name; used in `flip deck <verb> se`
source_lang = "en"        # source language of topic/options; compared against global target_lang
answer_alphabet = "ABCDE" # max option letters across this deck
max_display_options = 4   # TUI displays at most this many options; default 4

[explain]
role = "软件工程课程助教"   # injected into the AI explanation prompt
max_chars = 200           # soft cap on explanation length
default_model = "gpt-5.3-codex-spark"
model_env = "CODEX_EXPLAIN_MODEL"  # env var name; if set, overrides default_model
```

## `[deck]` fields

| Field             | Type   | Required | Notes |
|-------------------|--------|----------|-------|
| `name`            | string | yes      | Display name shown in the entry menu and stats. |
| `slug`            | string | yes      | Directory name and CLI identifier. Must match `[a-z0-9-]+`. |
| `source_lang`     | string | yes      | Language code of the raw `topic`/`options`. Translation is enabled only if `source_lang != config.target_lang`. |
| `answer_alphabet` | string | no       | Default `"ABCD"`. Letters that may appear in `answer`. Length drives `parse_answer`'s digit mapping (`1..N → A..`). |
| `max_display_options` | int | no | Default `4`. TUI question/result/review screens display only the first N options. `tiku.json` still stores all options. |
| `content_version` | string | no | Default `"0"` (unknown / pre-versioning). Monotonic content version of the tiku data; bundled decks bump it on each upstream change so the Bootstrap tab can offer an in-place update. A non-bundled deck can leave it unset. |

## `[explain]` fields

These are **deck-specific** overrides. The actual backend command, output mode,
and timeout live in the *global* `config.toml` `[explain]` section (see
`docs/import.md` and `flip config`). Only persona-like fields belong here.

| Field           | Type   | Required | Notes |
|-----------------|--------|----------|-------|
| `role`          | string | yes      | Persona prefix for the explanation prompt (replaces the legacy hardcoded "软件工程课程助教"). |
| `max_chars`     | int    | no       | Default `200`. Hint passed to the model. |
| `default_model` | string | no       | If set, overrides the global `config.explain.model` for this deck only. |
| `model_env`     | string | no       | Env var name; if set, overrides `default_model`. Defaults to `FLIP_EXPLAIN_MODEL`. |

> The shell command that invokes the model is **not** configured here — it is
> global so all decks share one provider. Edit
> `~/.local/share/flip/config.toml` `[explain]` to switch providers (codex,
> zhipu GLM, openrouter, a custom script, …).

The global `[explain]` table supports two ways to express the backend's
command line. Either is optional on its own, but at least one must carry the
`{prompt}` placeholder.

### `command` — shell template (string)

Parsed with `shlex.split`. Best for simple one-liners. `{prompt}` is required
and must be its own token; `{outfile}` is required when
`output = "tempfile"` and must also be its own token. Fixed arguments may be
quoted in the template, but prompt text is always passed as one argv element.

```toml
[explain]
command = "codex exec --skip-git-repo-check -m {model} -o {outfile} {prompt}"
output = "tempfile"
```

### `argv` — explicit token list (array of strings)

Used as-is, no `shlex` parsing. Best when flags are many, order-sensitive,
or carry embedded quotes that a string template makes unreadable (codex's
nested `-c 'key="value"'` config pairs). **When `argv` is non-empty it wins;
`command` is ignored.** The same placeholder contract applies: `{prompt}` is
required as a standalone element, `{outfile}` is required in `tempfile` mode.

```toml
[explain]
# Mirrors the se_regressor.py "fast codex" invocation: no hooks/plugins,
# low reasoning effort, OpenAI responses wire-api, read-only sandbox.
argv = [
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
output = "tempfile"
```

The bootstrapped `config.toml` (written on first run) ships this exact block
commented out — uncomment it to opt into the accelerated preset.

## Validation rules (enforced by `deck.py`)

1. `slug` must match the directory name.
2. `[deck].name`, `[deck].slug`, `[deck].source_lang`, `[explain].role` are required — missing any raises a load error.
3. `answer_alphabet`, if present, must be uppercase ASCII letters, no duplicates.
4. `max_display_options`, if present, must be a positive integer.
5. Unknown top-level tables/keys are ignored but logged as a warning (forward-compat).
