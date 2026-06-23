# flip

[中文](README.md) · [English](README_en.md)

一个 deck 无关的终端刷题训练器。选一个 **deck**(一门学科),刷题、标记难题、让 agent 解释错题——全部用一条 `flip` 命令完成。

## 特性

- **deck 无关的题库引擎** —— 一套 schema 服务任意学科(SE、编译原理……),题型与角色由 deck manifest 决定,引擎不夹带学科假设。
- **原生终端 TUI** —— deck 选择界面带实时搜索与 **Library / Bootstrap** 双 tab(←/→ 切换),章节多选,自适应终端宽度与 resize 重绘。
- **多种刷题模式** —— Train(题库计分)、Review(错题索引计分)、Ans(直接显答案、不计分浏览)、Continue(续上次暂停的练习)。
- **错题与统计** —— 自动维护错题索引、按章节分布的统计页、每题刷题次数徽章。
- **标记与笔记** —— 对单题打标记、写笔记,可按 mark/note/ai 过滤。
- **AI 错题解释** —— 通过可配置后端(codex / 智谱 GLM / ollama ……)对错题生成解释,角色文案来自 deck。
- **双语翻译** —— 全局开关:`source_lang ≠ target_lang` 时显示 `t` 键、写 `zh` 字段、AI prompt 附译文。
- **导入 / 导出 / 合并** —— 从 JSON / CSV / deck 目录导入,一键导出备份,`merge` 支持-append/-upsert/-overwrite 保留学习状态。
- **bundled deck 按需安装** —— 内置 deck 随包发布,在 Bootstrap tab 显式勾选安装;`flip deck remove` 删后不会自动回来。
- **配套 agent skill** —— `flip-deck-init` 从原始素材生成 deck,`flip-deck-maintain` 安全更新已有 deck。

## 安装

**从源码安装(pipx):**

```bash
# 还没有 pipx 的话先装
brew install pipx
pipx install git+https://github.com/ffy6511/flip.git
```


**Homebrew:**

```bash
brew tap ffy6511/tap
brew install flip
```

**更新:**

```bash
brew update && brew upgrade flip
pipx upgrade flip
```

**(可选)Cli 配套 Skills**

```bash
npx skills add ffy6511/flip/skills   # 安装配套的 skills;各 skill 作用见下文
```

**开发环境:**

```bash
git clone https://github.com/ffy6511/flip.git
cd flip
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/flip --help
```


## 支持的平台

flip 是纯 Python,无编译依赖,在以下平台均可运行:

| 平台 | 安装 | 数据目录 |
|------|------|----------|
| macOS / Linux | `brew install` 或 `pipx install` | `~/.local/share/flip/` |
| Windows | `pipx install`(需先装 [pipx](https://pypa.github.io/pipx/)) | `%APPDATA%\flip\` |

> 终端需支持 ANSI 转义序列。Windows 10 及以上默认支持(程序会在首次绘制时自动启用虚拟终端);旧版建议升级或在 Windows Terminal 中运行。

`$FLIP_HOME` 环境变量可覆盖默认数据目录,所有平台一致。


## 快速开始

直接运行 `flip`,顶部有两个 tab,用 **←/→** 切换:

- **Library** —— 你已安装的 deck。↑/↓ 选中、Enter 进入(输入字符可按 slug/名称实时过滤)；
- **Bootstrap** —— 尚未安装的内置 deck。**空格** 多选,然后 **回车** 确认安装。

内置 deck 随包发布。安装是**显式且一次性的**:`flip deck remove <slug>` 会把一个 deck 彻底删除。

## 可选:配套的 agent skill

本仓库的 `skills/` 目录里放着一组 skill,教会 AI agent(Claude Code、Cursor、ZCode,……)怎么配合 flip 工作。这些 skill 不随 pip/brew 包分发——用上面安装段里的一行命令装上,然后直接让 agent 干活即可。当前清单:

| Skill | 作用 |
|-------|------|
| [`flip-deck-init`](skills/flip-deck-init/) | 把任意题库素材(PDF / HTML / Word / 笔记)——或已有的题库 JSON——转成符合 schema 的 deck,并用 `flip import` 注册。既能从原始材料新建 deck,也能导入已结构化的 JSON。 |
| [`flip-deck-maintain`](skills/flip-deck-maintain/) | 维护已有 deck,在 `flip deck merge` 和直接编辑 `tiku.json` 之间选择合适路径,同时保留 id、标记、错题历史、笔记、翻译和 Agent Said 字段。 |



## 核心概念

- **deck** —— 一门学科(软件工程、编译原理……)。存放在 `~/.local/share/flip/decks/<slug>/`。
- **topic** —— 单道题的*题干文本*(为兼容已有数据,字段名沿用 `topic`)。
- **chapter** —— deck 的 `tiku.json` 内部分组键。

数据契约见 `docs/schema.md` 和 `docs/deck-manifest.md`。

## 用法

```bash
flip                              # 交互:先选 deck,再选模式
flip list                         # 列出已注册的 deck
flip deck train se -c 5-10        # 训练软件工程,第 5–10 章(tiku,计分)
flip deck review se               # 练习软件工程的错题索引(计分)
flip deck continue se             # 继续上次暂停的计分练习
flip deck train se --ans          # 浏览软件工程题目并直接显答案,不计分
flip deck stats se                # 按章节分布统计
flip deck clear-count se --mode all  # 只清空 train/review 刷题次数
flip deck mark se                 # 列出已标记题目
flip deck wrong se                # 列出错题索引题目
flip deck merge se ./new.json --dry-run  # 预览增量更新
flip deck repair se --dry-run     # 校验 tiku 并重建 marked 索引
flip deck translate se            # 补全缺失的 zh 字段
flip import se ./tiku.json        # 把一份合规 JSON 注册为新 deck
flip export se -o ./se-deck       # 导出 deck,用于备份或迁移
flip config                       # 查看配置和解释后端状态
```

> 子命令顺序是 `flip deck <动词> <slug>`(动词在前,slug 在后)。
> 章节选择器支持单章、范围、前 N 章和逗号组合:`5`、`5-10`、`-3`、`5,3-4`。
> 运行 `flip` 先进入 deck 选择界面(见
> [快速开始](#快速开始)):在 **Library** tab 选 deck(↑/↓ + Enter,支持实时搜索),或在 **Bootstrap** tab(←/→ 切换)安装内置 deck。选好 deck 后再选模式——**Train**(tiku 题库)、**Review**(错题索引)、**Continue**(暂停的计分练习)或 **List**(统计)——外加 1-5 筛选/显示开关、清空次数动作和一个 **Ans 模式** 开关(直接显答案、不计分)。

## 目录结构

```
src/flip/      引擎、TUI、存储、配置、deck manifest、AI 解释
docs/          schema.md(tiku.json)、deck-manifest.md、import.md
decks/example/ 最小示例 deck(同时用作测试夹具)
skills/        flip-deck-init —— 从原始材料引导出一个 deck 的 agent skill
tests/         pytest 套件,包含聚焦的 TUI 循环回归测试
```

## 致谢

- 本项目的整体灵感来自 [Zhang-Each/SE-FSE-exercise](https://github.com/Zhang-Each/SE-FSE-exercise.git)。
- flip 中部分 `tiku` deck 的原始题目数据来自该项目提供的 JSON 文件。
