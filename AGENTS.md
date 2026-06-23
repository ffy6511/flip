# flip 协作规则

本文件给所有在 flip 仓库上工作的 agent 和人类贡献者使用。优先级高于默认行为。

## 核心术语(勿混用)

- **deck** = 学科(SE、编译原理)。一个 deck = `~/.local/share/flip/decks/<slug>/` 下的一套数据。
- **topic** = 题干文本。是 `tiku.json` 里 question 对象的**字段名**,不是学科概念。
  - 讨论学科时永远用 deck,讨论题干字段时才用 topic。两个词不互换。
- **chapter** = deck 内部分组键,tiku.json 的顶层 key。

## deck 无关原则

引擎代码(`engine.py`, `tui/`)不得出现任何学科特有假设。学科相关的参数一律来自 deck manifest。具体禁项:

- 禁止在引擎里硬编码学科名、角色文案、答案字母表、翻译语言对。
- 禁止重新引入 "隐藏 E 选项" 逻辑(`_visible_options` 永远返回全部 options)。E 是正常的第 5 选项,不是缺陷也不是特性。
- 禁止在引擎里读 `SCRIPT_DIR` 定位数据。所有路径走 `store.py`。

## 引擎与 TUI 的边界

- `cli.py` 用 Typer,只做命令解析/路由/help。**禁止用 Typer/Click 实现交互式 TUI 循环**。
- TUI 主循环(`epoch`/`_prompt_answer`/`review_questions`/`_entry_menu`)的交互行为保持与 `se_regressor.py` 原版一致,除非 manifest/config 显式改变它(如翻译关闭时隐藏 `t` 键)。

## bundled deck 安装

- bundled deck 的**按需安装入口是 deck picker 的 Bootstrap tab**(`engine_loop.deck_picker` 顶部 ←/→ tab),不是首次启动的静默补装。`config.load_config` 不得调用任何安装函数。
- `bootstrap.py` 只提供纯函数(`available_bundled_slugs` / `install_bundled` / `bundled_deck_summary`),不得含 TUI 循环。
- 一个 bundled slug 是否"可装"的唯一判据是 `decks_dir/<slug>` 目录是否存在——删了就重新可装,装了就从 Bootstrap 列表消失。不需要 `.bootstrapped` 之类的持久戳。

## bundled deck 更新

- bundled deck 的更新入口是 Bootstrap tab 里的可更新项和 `flip deck update <slug>`。维护者改 shipped 内容时,先 bump `content_version`,再发布。
- update 走按 `id` 的 upsert 合并。未修改题目的 `marked`、`marked_at`、`user_note`、`ai_explanation` 和 wrong 索引记录必须保留。
- bundled deck 自带的 `user_note` 不能覆盖用户自己的 note。维护者说明只作为初始内容存在,update 时始终保留本地用户 note。
- 老的“位置 id” deck 会在 update 时自动迁移到 UUID,并同步改写 `marked.json` 与 `wrong/` 的 key。内容已改到无法桥接的题目要上报为 unmigrated,不能静默吞掉。
- update 不主动删题。上游已删除、但本地仍保留的题目,统一交给 `flip deck prune <slug>` 清理。

## schema 是 source-of-truth

- `docs/schema.md` 是 `tiku.json` 字段的权威定义。引擎读写、skill 提取、测试夹具都必须对齐它。
- 改字段语义时,先改 `schema.md`,再改代码和测试。不要反向。

## 翻译能力

- 翻译是**全局开关**,由 `~/.local/share/flip/config.toml` 的 `source_lang`/`target_lang` 决定。
- 仅当两者不同时才启用:显示 `t` 键、写 `zh` 字段、AI prompt 附带译文。
- 两者相同时(如纯中文 deck),翻译相关的 UI 入口和字段写入必须完全隐藏,不能留半残状态。

## AI 解释

- prompt 模板里的角色(如 "软件工程课程助教")来自 deck manifest 的 `[explain].role`。
- 模型选择优先级:环境变量(manifest `[explain].model_env` 指定的) > manifest `default_model`。

## 测试范围

- 纯函数(parse_answer、chapter_selector、question_key、filename、filter、manifest 加载)必须覆盖。
- TUI 交互循环只写聚焦回归测试。测试应替换终端读写函数,使用 example deck 夹具,验证具体状态变化;不做完整终端自动化。
