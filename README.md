# CodeMethod · 多标签代码实现规划与知识库

一个用 **Python + PySide6** 写的桌面工具, 用来记录"具有特定功能的代码实现":

* 一条记录 = **功能描述 + 前置要求 + 任意数量标签 + 任意数量的语言实现**;
* 界面风格模拟 **VSCode**, 内置 **语法高亮 (22 种语言)**、行号、括号匹配、一键复制;
* **每一次修改都写入修订历史**, 任意历史版本都可以**全量回滚**, 回滚本身也被记录;
* 数据存成**自定义的可移植容器**: 二进制 `.cmdb` 与文本 `.cmj`, 两者可互相转换,
  并且可再导出为 Markdown / JSON / ZIP (含真实源码文件)。

```
┌──────── 菜单栏 ──────────────────────────────────────────────────────────┐
├──────── 工具栏 ──────────────────────────────────────────────────────────┤
│ 标签筛选 │  检索栏 + 条目列表        │  条目详情 (概览 / 每个语言一个页签) │
│ ──────── │──────────────────────────┴──────────────────────────────────── │
│ 统计信息 │  历史记录: 修订时间线  +  差异查看 (可全量回滚)                  │
├──────── 状态栏 ──────────────────────────────────────────────────────────┤
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 目录

- [快速开始](#快速开始)
- [核心概念](#核心概念)
- [功能总览](#功能总览)
- [界面说明](#界面说明)
- [多标签检索语法](#多标签检索语法)
- [修订历史与回滚](#修订历史与回滚)
- [容器格式规范](#容器格式规范)
- [代码预览与语法高亮](#代码预览与语法高亮)
- [键盘快捷键](#键盘快捷键)
- [项目结构](#项目结构)
- [打包与分发](#打包与分发)
- [测试](#测试)
- [扩展指南](#扩展指南)

---

## 快速开始

环境要求: **Python 3.10+** (开发环境使用 3.14) 与 **PySide6**。

```bash
# 1) 创建虚拟环境 (已有 .venv 可跳过)
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux / macOS

# 2) 安装依赖 (唯一必需依赖就是 PySide6)
pip install -r requirements.txt

# 3) 启动
python main.py                    # 空库启动
python main.py --demo             # 载入示例库 (4 个条目 / 4 种语言 / 9 条历史)
python main.py 我的代码库.cmdb     # 打开已有代码库
python -m codemethod --demo       # 等价写法
```

也可以先跑一遍测试确认环境正常:

```bash
python -m unittest discover -s tests -t .
```

> 无显示器环境 (CI / 容器) 下可以设置 `CODEMETHOD_OFFSCREEN=1` 或 `QT_QPA_PLATFORM=offscreen`
> 来运行界面代码与测试。

---

## 核心概念

| 概念 | 英文 | 说明 |
| --- | --- | --- |
| **条目** | Entry | 一个"功能需求"。含标题、描述、前置要求、标签、规划状态、收藏标记。 |
| **实现** | Implementation | 条目在**一种语言**下的具体代码。一个条目可以挂任意多个实现, 每个实现有独立的标题、文件名、备注、源码与版本号。 |
| **标签** | Tag | 多标签规划的核心。任意数量、大小写不敏感、可自定义颜色, 支持重命名 / 合并 / 删除。 |
| **规划状态** | Status | `构想 → 已规划 → 实现中 → 已完成 → 已归档` 五个阶段, 在列表中用左侧色条与徽标显示。 |
| **修订** | Revision | 一次修改的**全量快照**。只追加、不删除, 因此任意时刻都能无损恢复。 |
| **代码库** | Repository | 一个 `.cmdb` / `.cmj` 文件的内容: 全部条目 + 标签注册表 + 全部历史。 |

一个条目长这样:

```
标题:      TCP 回显服务器
状态:      已完成            ★ 收藏
标签:      #network  #tcp  #server  #demo
描述:      接受客户端连接, 把收到的每一行原样返回, 支持多个客户端并发…
前置要求:  Python 3.10+ / Go 1.21+；无需第三方库；本地可用的 9000 端口
实现:      ├─ Python  · 基于 asyncio
           └─ Go      · 基于 goroutine
```

---

## 功能总览

### 记录与组织

- 新建 / 编辑 / 复制 / 删除条目; 删除是**软删除**, 进回收站后可以恢复;
- 每条记录可写 **描述** 与 **通用前置要求** 两大段富文本 (支持多行、保留换行);
- 标签支持自动补全、从已有标签中挑选、颜色自定义。

### 前置要求是分语言的

同一个问题用不同语言实现时, 它们需要的东西完全不同 —— Python 版要 3.10+、Go 版要 1.21+、
Rust 版要 cargo。因此前置要求分两层:

| 层级 | 位置 | 放什么 |
| --- | --- | --- |
| **通用前置要求** | 条目上一个字段 | 与语言无关的要求, 例如「需要理解双向链表」「需要一段可用的公网地址」 |
| **各语言前置要求** | **每个语言实现各自一个字段** | 该语言的工具链 / 版本 / 依赖, 例如「Python 3.10+」「Go 1.21+, 仅标准库」「Rust 1.75+ / cargo」 |

界面上的呈现:

- **概览页**按语言逐条列出「前置要求: …」, 一眼看清每种语言各要什么;
- **每种语言的页签顶部**再显示一条该语言的前置要求信息栏, 打开就能看到;
- 编辑对话框里, 左侧是「通用前置要求 (所有语言共用)」, 右侧每个实现页签各有一个
  「前置要求」输入框, 标签会跟着语言变 (**Python 前置要求** / **Go 前置要求** …),
  占位提示也会给出该语言的常见写法;
- 导出 Markdown 时额外生成一张「语言 → 前置要求」对照表, 并在每个实现小节里
  单独写出 `**前置要求**: …`;
- 检索支持 `prereq:` 限定符, **同时**搜通用与各语言的, 例如 `prereq:cargo`;
- 修改某种语言的前置要求会进入修订历史, 差异视图里能看到 `-Python 3.8+` / `+Python 3.12+`。

> 旧版本保存的库没有该字段, 打开后一律视为空, 不会报错也不需要迁移。

### 一个问题, 多种语言实现

这是本工具的核心用法: **一条记录 = 一个问题**, 它可以同时挂载任意多种语言的解法。

- **四个随手可用的入口**: 详情页签栏右上角的「＋ 语言实现」按钮、概览页的
  「＋ 为这个问题添加一种语言实现」按钮、头部 `⋯` 菜单、以及 `Ctrl+L`;
- 每个实现独立设置**语言 / 标题 / 文件名 / 备注 / 前置要求**, 文件名留空时按语言自动生成,
  切换语言时扩展名会跟着变 (自定义的名字只换后缀, 不会被覆盖);
- 新增实现时会**自动推荐该条目还没有用过的语言**;
- 详情面板为**每种语言开一个页签**, 概览页还会列出「语言实现 (N)」清单,
  每行带 `查看 / 编辑 / 删除` 三个操作;
- 详情里的代码是**只读预览 + 「编辑」按钮**, 避免在主界面里改了代码却悄悄丢失;
- 删除某一种语言只影响该语言, 其余实现不受影响, 且整条操作可撤销 / 可回滚;
- 新增、修改、删除实现各产生一条独立的修订记录 (动作类型 `新增实现` /
  `修改实现` / `删除实现`)。

### 规划

- 五阶段规划状态, 列表左侧色条 + 详情页徽标直观显示;
- 收藏标记快速标记重点条目;
- 统计面板显示**状态分布、语言分布、热门标签**的柱状图, 以及容器概况。

### 查找

- 左侧标签面板: 勾选任意多个标签, 选择 **同时包含 (AND) / 包含任一 (OR) / 排除所选 (NOT) / 恰好等于 (EXACT)**;
- 全文检索: 覆盖标题、标签、描述、前置要求、备注、文件名与**全部源代码**, 可以按需关闭代码/描述范围;
- 字段限定符: `tag:` / `lang:` / `status:` / `is:`;
- 短语匹配 (`"tcp server"`) 与排除 (`-deprecated`);
- 10 种排序方式 (最近修改、标题、状态、代码行数、修订次数、标签数量…);
- 点击详情页里的标签胶囊即可立刻把它加入/移出筛选条件; 标签右键可看**相关标签**推荐。

### 代码预览与复制

- VSCode 风格代码区: 行号、当前行高亮、括号匹配、自动缩进、Ctrl+滚轮缩放;
- **语法高亮**支持 22 种语言 (见下);
- 一键复制**当前页签那种语言**的代码, 或"复制全部代码"(把所有语言拼成一份);
- 复制条目为 **Markdown / JSON**(含完整修订历史)。

### 历史与回滚

- 任何修改 (改标题、改代码、加标签、改状态、删除实现…) 都产生一条修订记录;
- 修订列表按时间倒序显示时间、动作类型、变更摘要, 并按动作类型着色;
- 右侧差异视图按行着色 (新增/删除/区块头), 可复制差异文本;
- **回滚到任意版本**: 全量恢复, 且回滚本身也记为新的一条修订;
- **撤销 / 重做**: 在修订时间线上前后移动游标, 不破坏历史;
- 历史面板显示当前游标位置 (加粗行)。

### 数据与可移植性

- 自定义容器格式, **不依赖 SQLite 或任何数据库**;
- 二进制 `.cmdb`: zlib 压缩 + CRC32 + SHA-256 + 内嵌清单, 典型压缩率 **~20%**;
- 文本 `.cmj`: 格式化的 JSON, 便于 `git diff` / 人工审阅;
- 两种格式**可无损互转**;
- **原子写入** (先写临时文件再 `os.replace`) + **滚动备份** (`x.cmdb.1 … .5`);
- **完整性校验**: 校验魔数、载荷 CRC32、清单 CRC、SHA-256 摘要, 并检测截断与篡改;
- **导入 / 合并**: 可并入另一个 `.cmdb` / `.cmj` / `.zip` / `.json`, ID 冲突时自动重编号并携带完整历史;
- **导出**: Markdown (可读文档) / JSON / ZIP (每个条目一个文件夹 + `entry.json` + `README.md` + **真实源码文件**)。

---

## 界面说明

| 区域 | 内容 |
| --- | --- |
| **侧边栏** | 「标签筛选」(模式下拉 + 标签过滤 + 清空/反选 + 带颜色的标签列表 + 底部已选摘要) 与「统计信息」(总览、状态/语言/标签分布、容器信息)。用 `Ctrl+1` / `Ctrl+2` 切换。 |
| **中间列** | 检索栏 (关键词 + 排序 + 检索范围开关) 与条目列表。列表每行是一张两行卡片: 第一行标题 (收藏显示 ★) 与修订数, 第二行规划状态、前 4 个标签与语言、右侧"N 实现"徽标; 左边缘色条表示状态。 |
| **右侧** | 条目详情: 标题、状态徽标、收藏星、元信息、"编辑 / 历史 / 复制全部代码 / ⋯更多"按钮, 以及「概览」+ 每个实现一个页签。概览含标签胶囊、实现语言、描述、前置要求。 |
| **底部** | 历史记录面板: 左侧修订时间线, 右侧差异视图, 顶部"撤销 / 重做 / 复制差异 / 回滚到此版本"。 |
| **状态栏** | 操作反馈消息、当前条目摘要、可见/总条目统计、未保存标记、当前文件名。 |

---

## 多标签检索语法

检索框直接支持一套轻量查询语言:

| 写法 | 含义 |
| --- | --- |
| `socket server` | 同时包含 `socket` 与 `server` (AND) |
| `"tcp server"` | 短语匹配 |
| `-deprecated` | 排除包含该词的结果 |
| `tag:network` | 仅在标签中匹配 (`#network` 也可) |
| `lang:python` | 含该语言实现的条目 (支持 `py` / `golang` / `c++` 等别名) |
| `status:done` | 指定规划状态 (也接受中文名) |
| `prereq:cargo` | 前置要求命中 (**通用 + 各语言自己的** 一起搜, 子串匹配) |
| `is:favorite` | 仅收藏 (`is:deleted` / `is:multi` / `is:has_code` / `is:history` 亦可用) |

多个条件之间是 **AND** 关系, 例如:

```
tag:network lang:go status:done -deprecated
```

**多标签组合** 在左侧面板完成 —— 勾选标签, 再选择组合方式:

| 模式 | 语义 |
| --- | --- |
| 同时包含 (AND) | 条目必须带全部所选标签 |
| 包含任一 (OR) | 带任意一个所选标签即可 |
| 排除所选 (NOT) | 带任一所选标签的条目被排除 |
| 恰好等于 (EXACT) | 条目的标签集合与所选完全相同 |

---

## 修订历史与回滚

### 记录策略

修订采用**全量快照**, 而不是增量补丁。原因:

1. 任意历史版本都能**无损全量恢复**, 不必回放补丁链;
2. 快照按内容做 SHA-256 去重, 内容没变就不会产生新修订;
3. 差异文本在需要显示时用 `difflib` 现算, 不占用存储。

每次修改产生的修订都带: 修订 ID、条目 ID、动作类型、变更摘要、作者、时间戳、
父修订 ID、快照校验和、完整快照。

### 动作类型

`新建条目` · `修改条目` · `删除条目` · `从回收站恢复` · `彻底删除` · `新增实现` ·
`修改实现` · `删除实现` · `恢复实现` · `标签变更` · `状态变更` · `收藏变更` ·
`回滚到历史版本` · `撤销` · `重做` · `导入`

### 三种"回到过去"的方式

| 方式 | 行为 | 是否新增修订 |
| --- | --- | --- |
| **撤销 / 重做** (`Ctrl+Z` / `Ctrl+Y`) | 在时间线上前后移动游标并把对应快照应用回当前状态 | 否, 历史不变 |
| **回滚到此版本** | 把条目全量恢复为选中的历史版本 | 是, 追加一条 `回滚到历史版本` |
| **从回收站恢复** | 把软删除的条目重新启用 | 是, 追加一条 `从回收站恢复` |

因为历史只追加、不截断, 所以**回滚操作本身也可以再被回滚**。
默认每个条目最多保留 500 条修订 (可在 `Repository(max_revisions=...)` 调整), 超出后
从最旧的开始丢弃, 游标会自动修正。

---

## 容器格式规范

### 二进制容器 `.cmdb` — 格式 v1.0

全部字段小端序 (little-endian)。

```
┌──────────────────────── 头部 HEADER (固定 64 字节) ────────────────────────┐
│ off  size 类型    字段          说明                                       │
│ 0    8    char[8] magic         b"CMDBFMT\x01"                           │
│ 8    2    uint16  major         主版本号 (不兼容变更时递增)                │
│ 10   2    uint16  minor         次版本号 (向后兼容增量)                    │
│ 12   4    uint32  flags         bit0 载荷已压缩, bit1 含清单,              │
│                                 bit2 载荷校验为 CRC32, bit3 已加密(预留)   │
│ 16   4    uint32  header_size   头部字节数 (=64), 便于将来扩展             │
│ 20   8    uint64  payload_len   载荷存储长度 (压缩后)                      │
│ 28   8    uint64  payload_raw   载荷原始长度 (压缩前)                      │
│ 36   4    uint32  payload_crc   载荷字节的 CRC32                           │
│ 40   8    uint64  created       创建时间 (Unix 秒)                         │
│ 48   8    uint64  modified      最后修改时间 (Unix 秒)                     │
│ 56   8    uint64  manifest_off  清单块偏移 (0 表示无清单)                  │
├──────────────────────── 载荷 PAYLOAD ─────────────────────────────────────┤
│ payload_len 字节: zlib(JSON UTF-8), 或未压缩的 JSON UTF-8                  │
├──────────────────────── 清单 MANIFEST ────────────────────────────────────┤
│ JSON UTF-8 (zlib 压缩): 无需解压载荷即可列出条目 + 载荷 SHA-256            │
│   { format, format_version, generator, stats{entries, implementations,   │
│     revisions, tags, languages}, languages{}, tags{}, entries[],         │
│     integrity{algorithm, digest, payload_bytes}, written_at }            │
├──────────────────────── 尾部 FOOTER (固定 32 字节) ───────────────────────┤
│ 0    8    uint64  manifest_len  清单块长度                                 │
│ 8    8    uint64  file_size     文件总长度 (用于检测截断)                  │
│ 16   4    uint32  manifest_crc  清单块 CRC32                               │
│ 20   4    uint32  footer_crc    CRC32(头部 64 字节 ‖ 本尾部前 20 字节)     │
│ 24   8    char[8] end_magic     b"CMDBEND\x00"                           │
└────────────────────────────────────────────────────────────────────────────┘
```

设计要点:

- **自带描述**: 魔数 + 版本号 + 标记位 + 长度, 任何程序都能解析;
- **分层校验**: 载荷 CRC32 → 清单 CRC32 → 载荷 SHA-256 → 尾部 CRC 覆盖头部,
  因此**篡改头部任意一个字节都会被检出**;
- **快速索引**: 清单块让工具不必解压载荷就能列目录、统计、做"最近打开"预览;
- **向前兼容**: 读文件时若 `major` 高于本程序支持的版本会明确报错, 而不是给出错误结果;
- **原子落盘**: 永远先写 `*.tmp-<pid>` 再 `os.replace`。

### 文本容器 `.cmj`

```json
{
  "format": "codemethod-repository",
  "format_version": "1.0",
  "generator": "CodeMethod 1.0.0",
  "created_at": 1700000000.0,
  "modified_at": 1700000000.0,
  "stats": { "entries": 4, "implementations": 5, "revisions": 9, "tags": 12 },
  "integrity": { "algorithm": "sha256", "digest": "…", "payload_bytes": 4567 },
  "repository": { "schema_version": 1, "name": "…", "entries": [ … ], "history": { … } }
}
```

适合纳入版本控制: 结构稳定、键序固定、每个条目与每条修订独占一行层级, `git diff` 可读。
若被手工编辑, 读取时会给出 `integrity.digest` 不一致的提示 (但不会拒绝加载)。

### 导出格式

| 格式 | 内容 |
| --- | --- |
| `.md` | 目录 + 每个条目一节 (描述 / 前置要求 / 每个语言一个围栏代码块 / 修订历史表格) |
| `.json` | `{format, generator, stats, repository}` |
| `.zip` | `codemethod.json` (完整数据, 可无损导回) + `README.md` + 每个条目一个文件夹 (含 `entry.json`、`README.md` 与**真实源码文件**) |

---

## 代码预览与语法高亮

### 支持的语言 (22 种)

| 必需支持 | C · C++ · Go · Java · Python · PHP · Rust |
| --- | --- |
| **额外内置** | JavaScript · TypeScript · C# · Kotlin · Swift · Ruby · Lua · SQL · Bash/Shell · JSON · YAML · HTML · CSS/SCSS · Markdown · Plain Text |

高亮的 token 类型: 关键字、控制流关键字、类型、内建函数、常量、函数名、属性/成员、
字符串、数字、注释、注解、预处理指令、变量、键。配色采用 **VSCode Dark+** 的默认配色
(`#569CD6` 关键字、`#CE9178` 字符串、`#6A9955` 注释、`#4EC9B0` 类型、`#DCDCAA` 函数…),
另附一套 Light+ 浅色主题 (视图 → 主题)。

### 实现方式

高亮引擎分两步, 由语言注册表驱动, **新增语言只需往
`codemethod/core/languages.py` 里追加一条 `LanguageSpec`, 不必改算法**:

1. **跨行扫描器** (纯 Python 字符扫描) 负责块注释、三引号字符串、嵌套块注释 (Rust)、
   行注释与多行原始字符串。它理解字符串与转义, 所以注释里的 `"未闭合引号` 不会
   被误判为字符串, 字符串里的 `//` 也不会被误判为注释。
2. **正则规则** (`QRegularExpression`) 负责单行 token, 按"后写覆盖先写"应用:
   数字 → 常量 → 内建 → 类型 → 关键字 → 函数名 → 属性 → 注解 → 预处理 →
   字符串 → **跨行结构与行注释最后应用** (这样注释与字符串总能压住关键字)。

非法正则会自动跳过 (不会静默产生错误高亮), 每种语言的规则集都带缓存。
`highlighted_tokens()` 还提供了一个不依赖 GUI 的离线高亮接口, 便于导出 HTML 或写回归测试。

---

## 键盘快捷键

| 快捷键 | 功能 | 快捷键 | 功能 |
| --- | --- | --- | --- |
| `Ctrl+N` | 新建条目 | `Ctrl+E` | 编辑当前条目 |
| `Ctrl+L` | **添加语言实现** | `Ctrl+Shift+E` | 编辑当前语言实现 |
| `Ctrl+Shift+N` | 新建代码库 | `Ctrl+T` | 标签管理 |
| `Ctrl+O` | 打开代码库 | `Ctrl+Shift+T` | 给当前条目添加标签 |
| `Ctrl+S` | 保存 | `Ctrl+D` | 收藏 / 取消收藏 |
| `Ctrl+Shift+S` | 另存为 | `Ctrl+Delete` | 删除条目 (进回收站) |
| `Ctrl+I` | 导入 / 合并 | `Ctrl+Z` | 撤销 |
| `Ctrl+F` | 聚焦检索框 | `Ctrl+Y` | 重做 |
| `Esc` | 清空检索条件 | `Ctrl+H` | 显示 / 隐藏历史面板 |
| `Ctrl+Shift+C` | 复制当前实现代码 | `Ctrl+Shift+M` | 复制条目为 Markdown |
| `Ctrl+1` / `Ctrl+2` | 切换侧边栏 | `F5` | 刷新 |
| `F1` | 快捷键与检索语法帮助 | `Ctrl+滚轮` | 缩放代码字号 |

编辑器内: `Tab` / `Shift+Tab` 缩进与反缩进 (支持多行选区), `Enter` 自动缩进并在
`:`、`{`、`(`、`[` 后自动多缩进一级、自动补全右括号所在行, `Home` 智能跳到首个非空白字符,
点击行号选中整行。

---

## 项目结构

```
CodeMethod/
├── main.py                     启动脚本 (python main.py --demo)
├── codemethod/
│   ├── __init__.py             包元信息 (版本号)
│   ├── __main__.py             python -m codemethod 入口
│   ├── app.py                  参数解析 + 应用装配 + 示例库构造
│   ├── core/                   领域层 (不依赖 Qt, 可单独使用)
│   │   ├── languages.py        22 种语言的词法/元数据注册表
│   │   ├── models.py           Entry / Implementation / Revision / 状态与标签工具
│   │   ├── history.py          修订存储: 全量快照、校验和、游标、差异计算
│   │   ├── repository.py       仓储门面: 条目/实现/标签的全部可变操作
│   │   └── query.py            多标签查询、全文检索语法、排序
│   ├── storage/                存储层 (不依赖 Qt)
│   │   ├── container.py        .cmdb / .cmj 编解码、清单、校验
│   │   ├── database.py         打开/保存/另存/备份/合并/转换
│   │   └── exporter.py         Markdown / JSON / ZIP 导出与回读
│   └── ui/                     界面层 (PySide6)
│       ├── theme.py            VSCode Dark+/Light+ 配色与全局样式表
│       ├── highlighter.py      由语言注册表驱动的通用语法高亮器
│       ├── editor.py           代码编辑器 / 只读预览 / 差异视图
│       ├── resources.py        用 QPainter 现场绘制的程序图标
│       ├── main_window.py      主窗口装配 (菜单/工具栏/面板/状态栏)
│       ├── widgets/            flow_layout, tag_chip, search_bar, tag_panel,
│       │                       entry_list (模型+委托), detail_panel, history_panel
│       └── dialogs/            entry_editor, implementation_dialog, tag_manager,
│                               container_dialog, about
├── tests/                      226 个单元测试 (unittest, 无需显示器)
├── codemethod.spec             PyInstaller 打包配置
├── build.py / build.ps1 / build.bat   一键构建脚本
├── pyproject.toml              打包元数据与安装配置
└── requirements.txt            运行期依赖 (仅 PySide6)
```

分层原则: **`core` 与 `storage` 完全不导入 Qt**, 因此可以脱离 GUI 单独用作库
(例如批量导入脚本、CI 校验工具); `ui` 只通过 `Repository` 接口改动数据, 因此
"任何修改都有历史"是结构上的保证, 而不是靠调用方自觉。

---

## 打包与分发

### 安装为命令

```bash
pip install .
codemethod --demo          # 安装后可直接运行
```

### 构建独立可执行文件

```powershell
.\build.ps1                    # 单文件 dist\CodeMethod.exe
.\build.ps1 -OneDir            # 目录版 dist\CodeMethod\ (启动更快)
.\build.ps1 -All -Zip          # 两种都构建, 并额外产出分发包 zip
.\build.ps1 -Tests -Clean      # 先跑 226 个测试并清理旧产物
```

或者直接用 Python / 批处理:

```bash
python build.py --all --zip --tests    # 等价, 推荐用于正式发版
build.bat --onedir                     # Windows 批处理
pyinstaller codemethod.spec --noconfirm   # 直接调用 PyInstaller (需先有图标与版本资源)
```

`build.py` 会自动完成:

1. 从 `codemethod/ui/resources.py` 里用 `QPainter` 画出的图标导出**多尺寸 `.ico`**
   (仓库里不需要放任何图片);
2. 生成 Windows **版本资源** (决定"属性 → 详细信息"里的产品名/版本号/版权);
3. 按需 `pip install pyinstaller`, 然后调用 `codemethod.spec` 打包;
4. **对产物执行内置自检** `CodeMethod.exe --selftest`, 不通过就让整个构建返回非零;
5. 按需把目录版压成分发 zip, 并打印每个发行物的大小与 SHA-256。

### 发行物

| 路径 | 说明 |
| --- | --- |
| `dist/CodeMethod.exe` | **单文件绿色版**, 约 45 MB, 拷走即可运行, 无需安装 |
| `dist/CodeMethod/` | 目录版, 启动更快 (免去每次解压临时目录) |
| `dist/CodeMethod-<版本>-win64.zip` | 目录版的压缩包, 可直接分发给别人 |

spec 里主动排除了 WebEngine / 3D / Multimedia / Quick 等用不到的 Qt 模块, 把体积从
~250 MB 压到 ~45 MB。程序**不依赖任何外部数据文件** (图标是运行时画出来的),
因此不会出现"资源路径找不到"的问题。

### 验证打包产物 (`--selftest`)

打包最容易出的问题是"能编译但跑不起来"(缺 Qt 插件、少 hiddenimport)。为此程序内置了
一个无界面自检模式:

```powershell
dist\CodeMethod.exe --selftest report.txt
```

它会在离屏模式下跑完 40+ 项检查并写报告:

- 22 种语言注册表与**每种语言的语法高亮**都能产出 token;
- `.cmdb` / `.cmj` 容器的**写入、读回、完整性校验**(CRC32 / SHA-256);
- Markdown / JSON / ZIP 三种导出与 ZIP 回读;
- 主窗口构建、条目列表、**多语言页签**、代码视图挂载 (覆盖 Qt 平台插件是否被正确打包)。

全部通过时退出码为 `0`。`build.py` 在每次构建后都会自动调用它, 所以"构建成功"
本身就意味着"产物自检通过"。CI 里也可以直接对产物调用来做冒烟测试。

### 打包常见问题

**`Failed to load Python DLL '...\build\codemethod\_internal\python3xx.dll'`**

说明你运行的是 PyInstaller 在 `build\codemethod\` 里的**中间产物** —— 那是引导程序
半成品, 旁边没有 `_internal\`, 跑不起来。它与真成品同名同图标, 极易误点。

- 请改运行 `dist\` 下的产物 (见上面的发行物表);
- 现在 `codemethod.spec` 会在构建完成后**自动删除**这个中间 exe, `build.py` 也会再兜底
  检查一次并把问题当成构建失败, 因此正常构建不会再有这个文件;
- `build\README.txt` 里也会写明这一点。

**目录版移动后报同样的错**

目录版依赖同级的 `_internal\` 文件夹, 必须**整个 `CodeMethod\` 文件夹一起拷贝**,
只拷 `CodeMethod.exe` 会找不到 Python DLL 与 Qt 插件。
只想拿单个文件就用单文件版 `dist\CodeMethod.exe`。

**单文件版启动慢 / 被安全软件拦截**

单文件版每次启动都会把内容解压到 `%TEMP%`, 因此首启比目录版慢几秒;
若安全软件限制临时目录写入, 请改用目录版。

**产物启动后没有窗口**

先跑 `dist\CodeMethod.exe --selftest report.txt` 看报告定位问题;
如果自检通过但界面不出来, 通常是显卡/远程桌面下的 Qt 渲染问题, 可设
`QT_OPENGL=software` 或 `QT_QPA_PLATFORM=windows` 再试。

---

## 测试

```bash
python -m unittest discover -s tests -t .     # 226 个测试
python -m pytest                              # 也兼容 pytest
```

覆盖范围:

| 文件 | 内容 |
| --- | --- |
| `test_models.py` | 语言注册表与别名/扩展名识别、标签规范化与去重、模型序列化往返 (含**分语言前置要求**与旧数据兼容) |
| `test_history.py` | 快照校验和稳定性、去重、游标边界、撤销/重做、只追加语义、修剪、序列化、差异计算 (含实现前置要求的差异) |
| `test_repository.py` | 每类操作都产生正确动作的修订、软/硬删除、回滚的全量性、标签重命名/合并/删除级联、**各语言前置要求互不干扰** |
| `test_query.py` | 查询语法解析、AND/OR/NOT/EXACT 标签组合、字段限定符 (含 `prereq:`)、短语与排除、10 种排序 |
| `test_storage.py` | 两种容器往返、64/32 字节头部规范、**篡改与截断检测**、原子写入、滚动备份、格式转换、合并去冲突、导出与回读 (含分语言前置要求) |
| `test_ui.py` | 22 种语言的高亮规则合法性与 token 产出、跨行注释/字符串、编辑器与预览部件、主窗口端到端操作 (离屏)、**详情面板刷新回归**、**多种语言实现的增删改与历史**、**前置要求的界面呈现与存盘往返** |

界面测试使用 `QT_QPA_PLATFORM=offscreen`, 因此**不需要显示器**, 可在 CI 中运行。

> 界面测试基类 `_TrackedWidgets` 会登记并在每个用例结束时销毁所创建的 QWidget。
> 这一步是必需的: 遗留的部件若等到解释器关闭阶段才被 GC, Qt 的销毁顺序不可控,
> 进程会直接崩掉 —— 表现为"所有测试都 ok, 退出码却是非零"。

---

## 扩展指南

### 新增一种语言

只需在 `codemethod/core/languages.py` 追加一条 `LanguageSpec` 并加进 `_SPECS` 序列:

```python
_ZIG = LanguageSpec(
    id="zig",
    name="Zig",
    extensions=("zig",),
    aliases=("ziglang",),
    keywords=_words("const var fn pub export test defer errdefer"),
    control_keywords=_words("if else for while switch break continue return"),
    types=_words("i8 i16 i32 i64 u8 u16 u32 u64 f32 f64 bool void usize isize anytype"),
    line_comment="//",
    block_comment=None,
    string_delimiters=('"',),
    monaco_id="zig",
    color="#EC915C",
)
```

语言下拉框、文件名推断、语法高亮会**自动**生效。
若该语言还支持第二种行注释标记 (如 PHP 的 `#`), 填 `extra_line_comments=("#",)`。

### 换肤

`codemethod/ui/theme.py` 里的 `Theme` 是一个纯数据类, 新增一个实例并放进 `THEMES`
即可在"视图 → 主题"里选择。所有颜色 (含语法 token 颜色) 都集中在该类中。

### 扩展容器格式

`codemethod/storage/container.py` 的 `FORMAT_MINOR` 用于向后兼容的增量 (新增可选字段),
`FORMAT_MAJOR` 用于不兼容变更。读取时会校验 `major`, 因此老版本程序遇到新格式会
明确拒绝而不是给出错误结果; 新增可选字段只需提高 `minor` 并让解析代码给出默认值。

### 作为库使用 (不启动 GUI)

```python
from codemethod.storage.database import Database
from codemethod.core.query import QuerySpec, TagMatch

db = Database.open("我的代码库.cmdb")

# 多标签检索
hits = db.repository.entries_list()
spec = QuerySpec(tags=["network", "tcp"], tag_mode=TagMatch.ALL)

# 修改 (自动进入历史)
entry = hits[0]
db.repository.update_entry(entry.id, status="done")
db.repository.add_implementation(entry.id, "rust", "fn main() {}")

# 回滚到第一条修订
first = db.repository.revisions(entry.id, descending=False)[0]
db.repository.restore_revision(entry.id, first.id)

db.save()
```

---

## 已知限制

- 修订历史采用全量快照, 单条目默认保留 500 条 (超出后丢弃最旧的); 对该条目而言
  超长编辑历史不会无限增长, 但也不可能回溯到被丢弃的版本。
- `.cmdb` 的加密标记位 (bit3) 已在头部预留, 但当前版本**尚未实现**加密;
  读取到置位该标记的文件会明确报错。
- 合并两个代码库时按 ID 去冲突, 不做内容级的三方合并 (同名条目不会被自动归并,
  而是作为独立条目导入)。
- 语法高亮是启发式的 (正则 + 扫描器), 不是完整的词法分析器; 对宏重度展开、
  模板元编程等场景可能与 IDE 的结果有细微差异。

---

## 许可证

MIT
