# flip

[English](README.md) · [中文](README.zh.md)

一个 deck 无关的终端刷题训练器。选一个 **deck**(一门学科,比如软件工程、编译原理),刷题、标记难题、让 agent 解释错题——全部用一条 `flip` 命令完成。

## 安装

**Homebrew(推荐):**

```bash
brew tap ffy6511/tap
brew install flip
```

**从源码安装(pipx):**

```bash
brew install pipx          # 还没有 pipx 的话先装
pipx install git+https://github.com/ffy6511/flip.git
```

**升级已有安装:**

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
flip deck train se --ans          # 浏览软件工程题目并直接显答案,不计分
flip deck stats se                # 按章节分布统计
flip deck mark se                 # 列出已标记题目
flip deck wrong se                # 列出错题索引题目
flip deck merge se ./new.json --dry-run  # 预览增量更新
flip deck translate se            # 补全缺失的 zh 字段
flip import se ./tiku.json        # 把一份合规 JSON 注册为新 deck
flip export se -o ./se-deck       # 导出 deck,用于备份或迁移
flip config                       # 查看配置和解释后端状态
```

> 子命令顺序是 `flip deck <动词> <slug>`(动词在前,slug 在后)。
> 章节选择器支持单章、范围、前 N 章和逗号组合:`5`、`5-10`、`-3`、`5,3-4`。
> 裸跑 `flip` 是两阶段选择:先选 deck(支持实时搜索),再选模式——
> **Train**(tiku 题库)、**Review**(错题索引)或 **List**(统计)——
> 外加 1-4 题目筛选开关和一个 **Ans 模式** 开关(直接显答案、不计分)。

## 目录结构

```
src/flip/      引擎、TUI、存储、配置、deck manifest、AI 解释
docs/          schema.md(tiku.json)、deck-manifest.md、import.md
decks/example/ 最小示例 deck(同时用作测试夹具)
skills/        flip-deck-init —— 从原始材料引导出一个 deck 的 agent skill
tests/         pytest 套件,包含聚焦的 TUI 循环回归测试
```
