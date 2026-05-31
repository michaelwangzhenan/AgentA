# 1.整体架构

AgentA 是"私有知识库 Agent"，按职责分为三层(表现层/ Agent Core / RAG) ，通过两套接口（AgentAPI 和 RetrieverAPI）连接。

## 1.1.分解视角

**纵向:三大业务模块**

| 模块 | 职责 |
|---|---|
| 表现层 | CLI / Web UI / SDK: 采集输入、订阅事件流分层渲染、命令管理 |
| Agent Core | 推理循环 + 工具调用 + 上下文管理 |
| RAG | 异构文档多模型索引；对查询做精准召回 |

**横向:四档可换/可扩展**

| 维度 | 选项 | 
|---|---|
| LLM Provider | 国内 / 国外 / 本地模型，按配置切换 | 
| Embedding 模型 | en / zh / m3，支持多模型并行 | 
| Agent 实现 | Python / LangChain / AutoGPT；共享公共层（Tools/Memory/LLM），差异只在 loop | 
| Prompt / Skill / MCP | 文件驱动，**并存叠加 + 热更新** |


## 1.2.整体架构

```mermaid
flowchart TB
    subgraph PRESENT["表现层"]
        direction LR
        CLI["CLI"]
        WEB["Web UI"]
        SDK["SDK / 脚本"]
    end

    AAPI["AgentAPI<br/>run · activate_skill<br/>· set_event_callback"]

    subgraph AGENT["Agent core"]
        IMP["三种 Agent loop ⇄<br/>Python · LangChain<br/>AutoGPT"]
        subgraph SHARED["公共层"]
            direction LR
            BASE["Tools · Memory<br/> · EventBus · Helpers"]
            LLMP["LLM Provider ⇄<br/>国内 / 国外 / 本地"]
            FILES["Skill / Prompt / MCP loader ⇄<br/>文件驱动 · 热更新"]
        end
        IMP --> SHARED
    end

    RAPI["RetrieverAPI<br/>search · expand_queries<br/>· format · warm_up"]

    subgraph RAG_BOX["RAG"]
        direction LR
        ING["Ingest<br/>Parse → Clean<br/>Split → Index"]
        EMB["Embedding 模型 ⇄<br/>en / zh / m3"]
        IDX[("索引存储<br/>ChromaDB + BM25")]
        RET["Retrieval<br/>多 query → 召回<br/>RRF → 阈值<br/>Rerank → 去重"]
        ING --> EMB
        EMB --> IDX
        RET --> EMB
    end

    CLI --> AAPI
    WEB --> AAPI
    SDK --> AAPI
    AAPI --> IMP
    IMP -.->|Agent Event| PRESENT
    SHARED --> RAPI
    RAPI --> RET

    classDef swappable stroke:#d97706,stroke-width:2px,stroke-dasharray:5 3
    class IMP,LLMP,FILES,EMB swappable
```

> 图例：节点名后带 `⇄`、外框为橙色虚线的 4 个节点对应 §1.1 表里的 4 档可换/可扩展维度；实线为正向请求/响应通道，虚线为反向事件流通道。

**设计要点**

- **三层职责清晰**：表现层只管 IO，Agent core 只管推理与工具，RAG 只管检索；任一层换实现不影响其它层。
- **两套接口隔离关注点**：`AgentAPI` 隔离表现层与 Agent，`RetrieverAPI` 隔离 Agent 与 RAG。
- **横向可换/可扩展正交于纵向分层**：图中 `⇄` 标记的 4 个节点（LLM Provider / Embedding 模型 / Agent 实现 / Skill·Prompt·MCP loader）都可通过配置切换或叠加扩展（前三档单选切换，Skill·Prompt·MCP 并存叠加），不影响接口约定。
- **三种实现共享公共层**：三种 Agent 实现共享 Tools / Memory / EventBus / LLM Provider / Skill·Prompt·MCP loader 等公共能力。
- **事件流反向回流到表现层**：Agent core 内 `EventBus`（图中虚线箭头）把思考 / token / 工具调用 / plan / 错误等共 10 类事件推送给表现层订阅者，与正向请求/响应通道并行——表现层据此做分层流式渲染。详 [§3.1](#31-agentapi)。

## 1.3.两套接口

AgentA 模块间通过两套接口连接：

| 接口 | 边界 | API 简介 |
|---|---|---|
| `AgentAPI` | 表现层 ↔ Agent core | `run`：执行一次完整推理循环，返回最终回答<br/> `activate_skill`：手动注入 Skill 指令到当前会话<br/> `set_event_callback`：注册事件回调（思考 / token / 工具调用 / 最终回答 / 错误 / plan 进度等）|
| `RetrieverAPI` | Agent core ↔ RAG | `search`：多 query 召回 + RRF 融合 + 阈值过滤 + Rerank，返回 `Hit` 列表<br/> `expand_queries`：把原 query 扩展为 Multi-Query / HyDE / 翻译三轴<br/> `format_search_results`：把 `Hit` 列表格式化为 LLM 可读文本<br/> `warm_up`：启动时预热 embedding 与 reranker 模型 |

详细签名见 [§3.1 AgentAPI](#31-agentapi) 与 [§3.2 RetrieverAPI](#32-retrieverapi)


# 2.RAG
## 2.1.Ingestion

将异构本地文档（当前覆盖 PDF / DOCX / PPTX / XLSX / MD / HTML / TXT，可扩展）转换为可被多路检索的索引数据。

### 2.1.1.整体流程

```mermaid
flowchart LR
    A[源文档] --> B[Parse 解析]
    B --> C[Clean 清洗]
    C --> D[Split 分块]
    D --> E1[Dense 索引<br/>ChromaDB]
    D --> E2[Sparse 索引<br/>BM25]
```

### 2.1.2.Parse解析

把多种异构格式统一抽象为"纯文本 + 页号/标题标记"（如 `[[PAGE:5]]`、`## 章节标题`）的中间表示，让下游 Clean、Split 不再做格式分支；新增格式只需补一个 parser，主链路零侵入。

```mermaid
flowchart LR
    F1["PDF"] --> P1["pymupdf → pdfplumber<br/> → pypdf+ OCR 兜底"]
    F2["DOCX"] --> P2["python-docx"]
    F3["..."] --> P3["..."]
    F4["MD / TXT"] --> P4["编码探测<br/>utf-8 / gbk / latin-1"]
    P1 --> O["统一出口：<br/>纯文本<br/> +page锚点([PAGE:N]) <br/> +标题锚点(#~######)"]
    P2 --> O
    P3 --> O
    P4 --> O
```

**设计要点**

- **多格式统一抽象 · 易扩展**：每种格式各走专用 parser，出口都收敛到同一形态——"纯文本 + 两类锚点"（`[[PAGE:N]]` 标记页号，`#~######` 标记章节标题）。下游 Clean 、 Split 对格式零感知，未来扩 EPUB / CSV / 邮件等只需新增一个 parser，不动后续链路。
- **结构信号显式注入文本**：HTML 的 h1~h6、DOCX 的 Heading 1~9、PPTX 的 title placeholder、XLSX 的 sheet name 全部在解析阶段翻译成 Markdown `#` 标题写进正文，PDF / PPTX 每页前插 `[[PAGE:N]]`——格式特有的结构信息被"语言化"到正文里，splitter 只需识别这两类锚点就拿到完整结构。
- **PDF 多后端递降**：pymupdf（最快、质量最优）→ pdfplumber（表格/分栏强）→ pypdf（兜底），任一可用且产文非空即采用，缺哪个库都不报错。
- **OCR 兜底是 Parse 子环节**：检测"平均每页字符数 < 阈值"自动触发 RapidOCR，调用方对扫描版/数字版 PDF 无感知。OCR 与 PDF 三后端均为软依赖，缺失时静默禁用而非崩溃。
- **文本编码自适应**：MD / TXT 按 utf-8 → gbk → latin-1 递降探测，兼容 Windows 中文环境与历史文件来源。

### 2.1.3.Clean清洗

剥离页眉页脚、版权声明、纯页码、跨页重复模板等"模板噪声"，避免高频片段污染向量空间与 BM25 IDF。

```mermaid
flowchart LR
    R["原始文本"] --> S1["统计短行重复次数"]
    S1 --> S2["规则过滤<br/>重复短行剔除<br/>空行折叠"]
    S2 --> C["清洗后文本"]
```

**设计要点**

- **两类噪声分开识别**：单行规则匹配（纯页码、`Page X of Y`、`1/32`、`©®™`、"版权所有"…）覆盖"长得就像噪声"；文档级跨页重复（出现 ≥ 5 次的短行，长度 ≤ 80 字符）覆盖"出现频率说明它是噪声"——两条互补，避免漏掉 PDF 页眉这类无固定模式的模板行。
- **解析层一次清洗 · 全格式覆盖**：所有 parser 出口统一清洗，下游对格式透明。
- **空行折叠**：连续多个空行被压缩为单空行，避免 chunk 内出现大段空白浪费 `chunk_size` 预算。
- **保留两类结构锚点**：`[[PAGE:N]]` 与 `# 标题` 行不在噪声规则覆盖范围内，确保 Split 阶段仍能拿到完整结构信号。

### 2.1.4.Split分块

把清洗后的纯文本切成"既不超长又保留语义边界"的 chunk，并把页号 / 标题层级显式带入下游可检索的 metadata。

```mermaid
flowchart TD
    T["清洗后文本"] --> SC["按 [[PAGE:N]] 与 # 标题锚点<br/>切成 section"]
    SC --> AT["每个section递归切<br/>原子单元：段落→行<br/>→句号→空格→字符"]
    AT --> PK["贪心打包成 ≤ chunk_size<br/>相邻chunk,保留overlap字符"]
    PK --> BR["父级标题路径前缀<br/>注入到 chunk text"]
    BR --> CK["Chunk 列表<br/>text + heading_path + page_no + line"]
```

**设计要点**

- **递归回退优先级 · 不在词中间断**：段落(`\n\n`) → 行(`\n`) → 中英文断句(`。！？；./?/!`) → 空格 → 字符。前一级能真正切短文本就用它，全部失败才退化到字符级硬切——保证 chunk 边界尽量落在自然语义边界，不会把术语切成两半。
- **结构化分块元数据**：识别 `[[PAGE:N]]` 与 `#~######` 为切段点，同时维护 `(current_page, heading_stack)` 状态；每个 chunk 自动带上 `heading_path` / `page_no` / `line_start` / `line_end`，下游既可在工具结果里展示"来自 X 文件第 N 页 / 第几章"，也可做溯源跳转。
- **父级标题路径作为前缀注入正文**：把当前 chunk 所属的"父级标题链"（如 `# 第 5 章 物理层` → `## 5.2 子载波间隔`）以 Markdown 形式拼到 chunk 文本开头，与正文之间留空行。这样每个 chunk 自带"我在第几章 / 讲什么"的语义锚点——dense embedding 命中率显著提升，因为孤立短句被父级标题上下文化。
- **贪心打包 + overlap**：原子单元按 `chunk_size` 上限贪心拼接；相邻 chunk 间保留 `overlap` 字符尾巴防止跨边界语义被切断。若 overlap + 下一原子单元仍超长则放弃 tail，保证 chunk 永不超长。
- **标题前缀预算自适应**：正文 budget = chunk_size − 标题前缀长度，但有下限 `max(chunk_size/2, 64)` 防止"标题路径太深把正文挤掉"。
- **防递归不收敛**：分隔符只出现在文本末尾时（split 后只剩 1 个有效 piece），若把 sep 加回原文等于自身、递归调用栈不会收敛——检测到该情形自动跳到下一级 sep，避免 `RecursionError`。

### 2.1.5.ChromaDB（Dense 索引）

捕获语义相似度，让"5G 基站"能匹配"无线接入网络"这类语义近义表达。

```mermaid
flowchart LR
    CK["Chunk 列表<br/>含 heading_path 前缀"] --> EM["SentenceTransformer<br/>编码"]
    EM --> RT["按模型路由 collection<br/>kb_en / kb_zh / kb_m3"]
    RT --> HDB["ChromaDB + HNSW<br/>cosine 空间"]
    HDB --> MD["chroma.sqlite3<br/>doc_id / source / <br/>heading_path 等元数据"]
```

**设计要点**

- **多模型并存 · 分库存储**：每个 embedding 模型对应独立 collection——不同模型的向量维度天然不可比，必须分库。通过别名（`en / zh / m3`）切换默认模型，也支持多库并行召回再融合。
- **统一 cosine 空间**：ChromaDB + HNSW (Hierarchical Navigable Small World, 分层可导航小世界) 索引，统一使用 cosine 空间（与 BGE / MiniLM 等模型训练目标对齐，避免默认 squared L2 造成命中错位）。
- **存量库自动修正**：发现旧 collection 距离空间不是 cosine（如老数据用默认 L2 建的），ingest 时自动 drop & recreate，保证 ingest 与 retriever 的空间始终一致——避免"参数看似一样但底层错位"的隐性事故。
- **物理布局透明可溯源**：每个 collection 一个 UUID 目录存 HNSW 二进制文件，所有元数据集中在 `chroma.sqlite3`，可直接 SQL 查询溯源 doc_id / source / heading_path。
- **幂等增量**：以文件 `content_sha1` 驱动；未变化整文件跳过 re-embed，变化时先删旧 chunks 再写新，重复运行不产生重复或孤儿数据。

### 2.1.6.BM25（Sparse 索引）

捕获字面精确命中，解决 dense 模型对"无语义符号"的盲区——版本号、缩写、项目代号、命令名（如 `3GPP TS 38.211`、`CB014670`、`L2PS`），这些字符串没有可学习的语义，embedding 之后会被完全抹平。

```mermaid
flowchart LR
    CK["Chunk 列表<br/>与 dense 共享 chunk_id"] --> TK["分词<br/>EN: whitespace + lowercase + 停用词<br/>ZH: bigram (连续 2 字符)"]
    TK --> IDX["BM25 Okapi 倒排索引<br/>k1=1.5  b=0.75"]
    IDX --> PK["pickle 持久化<br/>按 collection 分文件"]
```

**设计要点**

- **与 dense 互补**：dense 强在语义近义、BM25 强在字面精确，两者通过 RRF 融合形成"语义 + 字面"双签到。
- **自实现 · 零外部依赖**：BM25 Okapi 公式 + 倒排索引 + pickle 持久化足够覆盖需求，无需引入 `rank_bm25` / Lucene 这类重量依赖。
- **混合分词**：英文走 whitespace + lowercase + 轻量停用词；中文走 **bigram**（连续 2 字符）——避免 `jieba` 30MB 词典依赖，且 RAG 场景下 bigram 召回率优于 unigram。
- **与 dense 共 chunk_id**：RRF 融合按 id 对齐的前提；缺这一条会退化为粗暴的跨尺度分数加权。
- **每 collection 一份索引**：按 collection_name 分文件持久化（`bm25_kb_en.pkl` 等），与 ChromaDB collection 一一对应，多语种 / 多模型并行不互扰。
- **按文件粒度可删**：以 `doc_id` 为索引键支持批量删除，与 ChromaDB 在 ingest 文件级 upsert 上行为对齐。

## 2.2.Retrieval

### 2.2.1.整体流程

```mermaid
flowchart LR
    A[用户 query] --> B[Query rewrite]
    B --> C[Hybrid Retrieval]
    C --> D[Cross-Encoder reranker]
    D --> E[最终结果]
```

### 2.2.2.Query rewrite

在检索前对用户原始 query 做语义/词汇扩展，提升召回鲁棒性。

```mermaid
flowchart LR
    Q[原 query] --> M[Multi-Query<br/>同义改写]
    Q --> T[翻译轴]
    Q --> H[HyDE<br/>假设性答案]
    M --> U[去重得到 effective_queries]
    T --> U
    H --> U
```

**设计要点**

- **三类策略各打一种盲区，可独立开关**：
  - *Multi-Query* 解决"同义 / 术语化"差异——把口语化或不规范的措辞替换成专业术语；
  - *HyDE*（Hypothetical Document Embeddings，假设性文档嵌入） 解决"口语 → 文档术语"的词汇分布差距，让 LLM 先编一段"假设性答案"作为额外检索 query；
  - *翻译轴* 解决"中文提问、英文文档"的跨语言失衡（dense 跨语言能力有限、BM25 跨语言完全失效）。
- **零侵入 · 静默降级**：query 改写定位为"锦上添花"层，LLM 调用失败时返回空、不打断主链路。
- **进程级 LRU (Least Recently Used, 最近最少使用) 缓存**：同一 query 二次命中零开销，避免重复花 LLM token。
- **不依赖 chat_history**：指代消解由上层 Agent 在 query 入参前自行完成；本模块对会话状态零耦合，便于独立测试与离线评估复用。

### 2.2.3.Hybrid Retrieval

在 Dense + BM25 双索引基础上，对多 query 并行召回结果做"同 collection 内分级融合 + 跨 collection 公平合并 + per-model 阈值过滤"。

```mermaid
flowchart TD
    EQ["effective_queries<br/>1~N 条"] --> PCL["遍历 active embeddings"]
    PCL --> DSE["Dense 召回"]
    PCL --> BMR["BM25 召回"]
    DSE --> RRF["同 collection 内<br/>RRF 融合"]
    BMR --> RRF
    RRF --> RR["跨 collection<br/>round-robin 合并"]
    RR --> TH["Dense 阈值过滤<br/>per-model · BM25 豁免"]
    TH --> OUT["候选交给 Reranker"]
```

**设计要点**

- **RRF 而非分数加权**：Dense (cosine) 与 BM25 (Okapi raw) 分数尺度完全不可比，强行加权权重几乎不可调；RRF 只看"排第几"，天然解决跨尺度融合，多查询命中的 chunk 自然加权。
- **多 query 召回量自适应**：每条 query 的召回窗口随改写条数收缩，总候选量与单 query 一致——避免改写越多候选越爆炸、Reranker 被洪水冲垮。
- **跨 collection round-robin 合并**：不同 embedding 模型距离空间不同，直接按分数拼接会让某个模型的高分占满名额；round-robin 保证每个库公平出列。
- **Dense 阈值 per-model**：各模型同主题相似度分布差异显著（MiniLM 偏低、bge-zh 偏高），用单一全局阈值会一边误杀好结果、一边放进噪声——按 collection 分别校准。
- **BM25 命中豁免 dense 阈值**：BM25 是强字面信号，若被 dense 低分误杀，将失去"无语义符号"召回的全部价值。
- **检索器溯源**：每条 Hit 保留"由哪些检索器召回"的字段，下游可做差异化阈值策略，也便于评估与排查。

### 2.2.4.Cross-Encoder reranker

对 Bi-Encoder 召回的候选做二阶段精排，弥补"召回阶段双塔模型缺乏 token 级 query-doc 交互"的固有缺陷。

```mermaid
flowchart TD
    C["候选 hits"] --> G{"候选 &gt; top_k?"}
    G -->|否| P["透传"]
    G -->|是| CE["Cross-Encoder<br/>逐对编码 query×doc"]
    CE --> N["Sigmoid 归一化到 [0,1]"]
    N --> F["min_score 阈值过滤"]
    P --> D["Per-source 去重"]
    F --> D
    D --> OUT["最终 top_k"]
```

**设计要点**

- **Bi-Encoder + Cross-Encoder 两段式**：召回阶段双塔结构 query 与 doc 各自独立编码，能预算向量、毫秒级检索；精排阶段把 (query, doc) 拼起来过一次 Transformer 做 token 级交互，精度更高但代价 O(N)——故只对召回 top-N 跑。
- **候选 ≤ top_k 直接透传**：精排空间为零时跑 reranker 纯属浪费，前置短路。
- **多模型可切换 · 懒加载缓存**：默认 `bge-reranker-base`（中英双语），可一行配置切换更大/更轻量模型；按模型名缓存到进程，首次调用懒加载，切换模型不重启。
- **Sigmoid 归一化统一刻度**：不同 reranker 输出尺度天然不同（bge 近似概率、ms-marco 是 logit），统一过 sigmoid 后阈值 / 比较 / 展示用同一套尺度，切换模型不必同步调阈值刻度。
- **加载失败降级不崩溃**：模型缺失 / 网络不可达时返回召回前 top_k 条，主链路继续工作并 warning 给出修复指引（offline 开关、模型名拼写、备选轻量模型）。
- **Per-source 去重置后**：放在 reranker 之后保证"被去重的是真的低质重复"，而不是先按 source 限流再被精排选中——顺序颠倒会废掉一部分精排成果。

## 2.3.Evaluation

用"黄金集（golden set）"对当前 RAG 配置做端到端检索评估，输出指标 + 实验上下文，支撑离线调优、回归对比与 ablation 实验。

```mermaid
flowchart LR
    G["golden.json<br/>黄金集"] --> R["对每条 query 调 search<br/>(rerank 透传 ablation 开关)"]
    R --> M["逐条计算<br/>first_source_rank<br/>first_keyword_rank"]
    M --> A["汇总<br/>hit@1 / @3 / @k · MRR"]
    A --> O["存储 Markdown 报告<br/>+ 同名 .log 伴生文件"]
```

**指标矩阵**

| 指标 | 衡量什么 | 适用场景 |
|---|---|---|
| `hit_source@1` / `@3` / `@k` | 第 1 / 前 3 / 前 K 位是否命中"期望源文件" | 不同档位分别敏感于精排、短列表 UX、整体召回 |
| `hit_keyword@k` | top-K 内是否包含"期望关键词" | 关键词命中与位次弱相关，只看 @k 即可 |
| `hit_either@1` / `@3` / `@k` | 上述任一命中（更宽松）| 综合通过率，三档对齐 source |
| `MRR` | 第一次命中位置的倒数平均 | 衡量"是否早命中"（而非"是否命中"）|

**设计要点**

- **位置敏感的多档指标**：`hit@1` 对精排最敏感，`hit@3` 反映短列表 UX，`hit@k` 反映整体召回；单 `hit@k` 会掩盖召回与精排的不同贡献，三档并报让 ablation 实验能定位差异落在哪一层。
- **`first_source_rank` 与 `first_keyword_rank` 分开追踪**：两者由不同检索能力贡献（source 拼的是 ranking，keyword 拼的是内容覆盖），合并为单一 `first_hit_rank` 会让"source 排第 5 但 keyword 排第 1"这类信号丢失。
- **空目标自动剔除分母**：未标注 `expected_source*` / `expected_keywords` 的 case 不进入对应指标分母，避免"想留宽容评估却被惩罚"——比如纯靠 keyword 评估时不强求每个 case 都标 source 文件。
- **Results-first Markdown 报告**：字段顺序刻意按"核心指标 → 实验开关 → metadata → Miss 用例"排，打开报告第一屏即看到结果，调优时无需滚动；选 Markdown 而非 JSON，是因为评估输出的主要消费者是人（在 IDE 里 diff、贴到对比表），原始 case 列表对人工阅读价值低，放进 Miss 用例小节足够。
- **`.log` 伴生文件收集 trace**：`-o report.md` 同时存储 `report.md.log`，把每条 query 的 retriever 阶段日志（dense / bm25 / rrf / rerank / dedupe 各阶段候选数）落地，事后回溯 ablation 异常结果时无需重跑评估。终端默认仅打进度条与汇总（避免 INFO 倒灌进度行），`-v` 才把 INFO 抬到终端。
- **Metadata 全量留档**：报告头部序列化所有"影响结果的配置因子"（git commit + dirty 标志、LLM provider、active embeddings、KB chunk 实测数、reranker / dense 阈值 / BM25 / query 改写 / 切分参数），保证每次实验可追溯、可 diff、可复现。
- **KB chunk 数实测而非读配置**：直接查 ChromaDB `collection.count()`，因为 ingest 历史会让"配置里写的"和"真实存储的"分叉——避免"配置看起来一样但 KB 已变"造成误判。
- **Ablation 通过 CLI 开关 + retriever 内层透传**：`--no-rewriter` 跳过 query 改写、`--no-rerank` 经 `search(rerank=False)` 强制关闭 retriever 内层 cross-encoder；报告同时记录"该组件最终是否真生效"（处理过静默降级路径），避免命令行参数与实际行为脱节。
- **Golden 集题型可分组**：每条 item 可选 `type` 字段（baseline / rerank / rewrite / hyde），便于做 per-type 统计——某组件只在自己擅长的题型上有增益时，全量指标会被稀释，按 type 拆分才看得见。


## 2.3.RAG代码

### 2.4.1.文件职责速查

| 路径 | 角色 | 主要入口 |
|---|---|---|
| `src/rag/parser.py` | 多格式 → 纯文本（txt/md/html/pdf/docx/pptx/xlsx + OCR 兜底）| `parse_file(path)` |
| `src/rag/splitter.py` | 结构化分块（识别 Markdown 标题与 PDF 页号作为锚点，把父级标题路径作为前缀注入 chunk 文本）| `split_structured(text, ...)` |
| `src/rag/ingest.py` | 入库主流程（遍历目录 → parse → split → 双索引写盘 + 幂等增量）| `ingest_all(...)` · CLI `python -m src.rag.ingest` |
| `src/rag/bm25_index.py` | BM25 Okapi 自实现（倒排索引 + bigram 中文分词 + pickle 持久化）| `get_index(coll)` |
| `src/rag/query_rewriter.py` | 三轴 query 改写（Multi-Query / HyDE / 翻译轴），LRU 缓存包装 | `expand_queries(query)` |
| `src/rag/retriever.py` | 检索总枢纽：多 query × 多 collection × dense+bm25 → RRF → 阈值 → rerank → dedupe(去重）) | `search(query, ..., rerank=None)` |
| `src/rag/reranker.py` | Cross-Encoder 精排，输出统一 sigmoid 归一化到 [0,1] | `rerank(query, hits, top_k)` |
| `tools/rag_eval/runner.py` | 端到端检索评估（黄金集 → 指标 → Markdown 报告 + `.log` 伴生文件）| `python -m tools.rag_eval.runner` |
| `tests/test_rag.py` | 单元 + 集成测试（chunk / 阈值 / reranker / search 端到端）| `pytest tests/test_rag.py` |

### 2.4.2.两条主调用链

**生产路径**（Agent 在线检索）：

```
Agent 工具调用
  └─ src/agent/tools.py · _tool_search_knowledge
       └─ src/rag/query_rewriter.py · expand_queries(query)         ← 三轴改写
            └─ src/rag/retriever.py · search(query, queries=...)    ← 总枢纽
                 ├─ _query_collection(...)                          ← Dense 召回
                 ├─ _query_bm25(...) → bm25_index.get_index()       ← Sparse 召回
                 ├─ _rrf_fuse(...)                                   ← RRF 融合
                 └─ src/rag/reranker.py · rerank(...)               ← Cross-Encoder 精排
```

**评估路径**（离线 ablation）：

```
python -m tools.rag_eval.runner [--no-rewriter] [--no-rerank] [-o report.md] [-v]
  └─ tools/rag_eval/runner.py · main() → evaluate()
       ├─ [默认] src/rag/query_rewriter.py · expand_queries(query)  ← 可用 --no-rewriter 关
       └─ src/rag/retriever.py · search(query, queries=..., rerank=...)  ← rerank 透传 ablation
            └─ (dense + BM25 → RRF → 阈值 → rerank → dedupe，同生产路径)
       → 指标聚合（hit@1/@3/@k · MRR）
       → 存储 Markdown 报告 + 同名 .log 伴生文件
```

### 2.4.3.推荐阅读顺序

**先读"主线"**，掌握"用户 query 进来后到底走了哪几步"：

1. `src/rag/retriever.py · search()` —— 整个 RAG 的"主函数"，看明白它就掌握了 80% 的检索逻辑。重点关注其中的阶段化日志（`[search]` 前缀），它是流程的天然导览。
2. `src/rag/reranker.py · rerank()` —— 短小，看完能理解 sigmoid 归一化与 score 字段语义。
3. `src/rag/query_rewriter.py · expand_queries()` —— 三轴改写如何各自降级、如何合并去重。

**再按需要往下钻**：

- 想优化召回质量 / 阈值 → `retriever.py` 的 dense 阈值过滤与 RRF 段
- 想加新文档格式 → `parser.py` 的 `parse_file()` 与各 `_parse_*` 私有函数
- 想调分块策略 → `splitter.py · split_structured()`
- 想加 / 改指标 → `tools/rag_eval/runner.py · evaluate()` 与 `_render_markdown()`
- 想理解入库幂等性 → `ingest.py · ingest_all()` 的 `content_sha1` 比对逻辑

### 2.4.4.常见改动落点

| 需求 | 改动文件 | 关键函数 / 配置 |
|---|---|---|
| 切换 embedding 模型 / 加新 alias | `src/config.py` | `EMBEDDING_MODELS` 字典；ingest 后自动新建 collection |
| 调 RRF / 阈值 / 去重 | `.env` | `RRF_K` / `RAG_DENSE_MIN_SCORE_*` / `RAG_K_PER_SOURCE` |
| 切换 reranker 模型 | `.env` | `RERANKER_MODEL` + `RAG_RERANK_MIN_SCORE`（统一 sigmoid 后仍需按分布微调） |
| 关闭某个改写轴 | `.env` | `RAG_QUERY_REWRITE_ENABLED` / `RAG_HYDE_ENABLED` / `RAG_TRANSLATE_QUERY_ENABLED` |
| Agent / 评估临时关 rerank | 调用方 | `search(..., rerank=False)`，无需改全局 config |
| 新增黄金集题目 | `tools/rag_eval/golden.json` | 字段 schema 见 `tools/rag_eval/runner.py` 顶部 docstring |
| 加新指标 | `tools/rag_eval/runner.py` | `EvalReport` 字段 + `evaluate()` 累加 + `_render_markdown()` 渲染 |


# 3.Agent

## 3.1. AgentAPI

`AgentAPI` 是**表现层 ↔ Agent core** 之间的接口，以 `@runtime_checkable Protocol` 定义于 `src/agent/agent_api.py`，三种 Agent（Python / LangChain / AutoGPT）通过 duck typing 满足。

| API | 说明 |
|---|---|
| `run` | 执行一轮推理，返回 LLM 最终回答；失败返回 `Error: <msg>` 不抛异常 |
| `activate_skill` | 注入 Skill 到 system_prompt；`True`=新激活、`False`=已激活 |
| `set_event_callback` | 设置统一事件回调（覆盖语义，传 `None` 清空） |

**事件协议（AgentEvent）** —— `src/agent/core/event_bus.py` 的 frozen dataclass，三字段 `type` / `payload` / `ts`。两种订阅方式：

- 简单：`agent.set_event_callback(fn)` —— 一个回调收所有 7 类事件，`fn` 收 `AgentEvent` 对象（含 `type` / `ts`）
- 高级：`agent.events.subscribe(EVENT_X, fn)` —— 按事件类型订阅，`fn` 仅收 `payload`

| event.type | payload | 触发时机 |
|---|---|---|
| `thinking_chunk` | `{text}` | Extended Thinking 流式（CLI 渲染分层详 [§4.1](#41cli)） |
| `token_chunk` | `{text}` | 正文 token 流式 |
| `tool_call_start` | `{name, args, call_id}` | LLM 决定调用工具 |
| `tool_call_end` | `{call_id, status, preview}` | 工具返回（`ok` / `empty` / `error`） |
| `final_answer` | `{text, usage}` | 最终回答 |
| `error` | `{message, recoverable, phase}` | 运行时异常 |
| `info` | `{message, ...}` | session / skill 切换等元信息 |
| `plan_created` | `{steps: [{id, text}, ...]}` | LLM 调 `make_plan` 成功后（见 [§3.8](#38-plan-execute)） |
| `plan_step_start` | `{step_id, text}` | plan 创建后 step 1 立即触发；之后每次 `update_step` 完成且仍有 pending 步时触发下一步 |
| `plan_step_end` | `{step_id, status, note}` | LLM 调 `update_step` 成功后；`status` ∈ `success` / `failed` / `skipped` |

## 3.2. RetrieverAPI

`RetrieverAPI` 是 **Agent core ↔ RAG** 之间的接口，以 **module-level 函数** 形式分布在 `src/rag/retriever.py` 与 `src/rag/query_rewriter.py`。

| 函数 | 说明 |
|---|---|
| `search` | 主入口；支持多查询并行（HyDE）与可选 reranker，返回 `list[Hit]` |
| `expand_queries` | LLM 查询改写（HyDE），返回原查询 + 扩展查询 |
| `format_search_results` | 把 hits 拼成可注入 prompt 的 markdown 字符串 |
| `warm_up` | 预热全部 collection，避免首查延迟 |

> **两套 API 风格**：`AgentAPI` 用 Protocol 类是因为 3 个实现并存，需 `isinstance` 校验任一实现没破约定；`RetrieverAPI` 仅 1 实现，按 Python 社区 idiom（`os.path` / `json` / `re` 风格）用 module 函数，未来出现第 2 个 retriever 实现时再升级为 Protocol。

## 3.3 会话管理

会话状态存储到 SQLite，可跨切换会话并查看历史。

**表结构**

| 表 | 字段 | 用途 |
|---|---|---|
| `sessions` | `session_id` (PK) / `created_at` / `first_user_msg` / `prompt_name` | 会话元数据；`first_user_msg` 用于 list/搜索预览 |
| `messages` | `id` (PK) / `session_id` (idx) / `role` / `content` / `tool_calls` (JSON) / `tool_call_id` / `timestamp` | 消息全量；`tool_calls` 序列化为 JSON |

**Internal API**

| 方法 | 说明 |
|---|---|
| `append(session_id, msg, prompt_name="")` | 写入单条 message；首次写入自动创建 session 元数据 |
| `load(session_id)` | 全量加载 messages（按 id 升序），供 Agent 恢复上下文 |
| `load_last_n_messages(session_id, n)` | 仅取末尾 n 条，规避长 session I/O 开销 |
| `list_sessions(query=None, limit=None)` | 列出 session；`query` 按 id 前缀 OR `first_user_msg` LIKE（不区分大小写）过滤 |
| `set_prompt_name(session_id, name)` | 更新当前 session 关联的自定义 Prompt 名 |
| `clear / delete_session / clean_all_sessions` | 三档清理：当前重置 / 单 session 删除 / 全删 |



## 3.4 用户记忆

跨会话存储用户偏好 / 背景 / 指令 / 任务 / 纠错，使 Agent 在新一次对话中仍"认得"用户。由两层组成：`MemoryManager`（注入与提取策略）+ `UserMemoryStore`（SQLite 存储）。

### 3.4.1 数据模型

| 字段 | 用途 |
|---|---|
| `id` | 主键；`/memory` 列表 / `del` / `edit` 用 |
| `category` | 五类之一：preference（偏好）/ background（背景）/ instruction（指令）/ task（任务）/ correction（纠错） |
| `key` | 短标识，类内唯一 |
| `value` | 实际内容；写入时做 prompt-injection sanitize |
| `source` | 写入来源：auto / explicit / manual（详 §3.4.2） |
| `created_at` / `accessed_at` | 时间戳；写入或更新时刷新 |

约束：`(category, key)` 唯一 — 同类同 key 自动覆盖去重。

### 3.4.2 写入来源与混合范式

**混合范式**（参考 ChatGPT / Cursor Memories）：三种写入路径共存于同一记忆池，`source` 字段标记来源便于审计与排错。

| source | 触发 | 是否调 LLM |
|---|---|---|
| `auto` | `USER_MEMORY_AUTO_EXTRACT=true` 时由 `MemoryManager.try_extract` 自动跑 | ✅ |
| `explicit` | 用户输入命中显式触发词（"请记住"等）立即触发，附最近若干轮历史作为 context | ✅ |
| `manual` | `/memory add` / `/memory edit` CLI 命令 | ❌ |

三者底层都走 `UserMemoryStore.upsert(category, key, value, source=...)`。

### 3.4.3 触发节流

避免每轮 `auto` 路径频繁调 LLM 提取，两个配置项节流：

| config | 默认 | 含义 |
|---|---|---|
| `USER_MEMORY_EXTRACT_EVERY_N` | 5 | 每 N 轮用户消息才触发一次自动提取 |
| `USER_MEMORY_EXTRACT_MIN_INPUT_LEN` | 20 | 用户输入字符数低于阈值不触发（短问无个人信息可提） |

**显式触发不受此限**，且不消耗也不重置 auto 计数器 — 两条流水线相互独立。

### 3.4.4 注入 system_prompt
参考 [§3.5.2 四层注入顺序](#352-四层注入顺序)。


## 3.5 Prompt 管理

用户在项目根放一份 Markdown 偏好文件，Agent 每次对话自动遵守，无需每轮重申。
对比 [§3.4 用户记忆](#34-用户记忆memory)：**静态偏好**（用户主动声明）vs. **动态偏好**（会话中学到）。
同理 Cursor Rules(.cursor/rules/agenta-conventions.mdc) / GHC Instructions(.github/instructions.md)。

### 3.5.1 文件位置与加载

| 项 | 约定 |
|---|---|
| 默认路径 | `.agenta/rules.md`（可由 `USER_RULES_FILE` 配置） |
| 格式 | 纯 Markdown / 文本，无 frontmatter，无元数据 |
| 加载时机 | 进程启动后**一次性**读入并缓存；改完文件需重启 AgentA 生效 |
| 异常处理 | 文件缺失 / 空 / 全空白 → 静默跳过（不报错）；超过 `USER_RULES_MAX_CHARS` 自动截断 |

注：不支持热加载 / 文件 watch。

### 3.5.2 四层注入顺序

system prompt 最终由四层拼成：

| 层 | 来源 | 决定 | 切换粒度 |
|---|---|---|---|
| **`base system_prompt`** | `agent.py:SYSTEM_PROMPT` 常量 + 启动时拼接的 `<available_skills>` skill catalog（详 [§3.7.2](#372-渐进披露)） | "Agent 是谁" + "有哪些 skill 可调" | 常量全局不变；catalog 在 `/reload-skills` 后刷新 |
| **`<project_rules>`** | 项目根 `.agenta/rules.md` | "Agent 在本项目下要遵守什么" — 语言 / 格式 / 引用风格等静态偏好 | 进程启动加载一次；改 `.agenta/rules.md` 后重启生效 |
| **`<user_context>`** | `UserMemoryStore` （[§3.4](#34-用户记忆memory)） | "Agent 这次会话还要注意什么" — 动态学到的临时偏好 | 每轮对话即时刷新 |
| **`<active_study_plan>`** | 学习计划（[§3.9.4](#394-跨-session-状态可见性)） | "Agent 当前在帮用户跟踪哪个学习计划 / 进度到哪了" |  `/study load` 手动注入 |

```mermaid
flowchart TD
    SYS["agent.py SYSTEM_PROMPT"]
    SKILLS[(".agenta/skills/*/SKILL.md<br/>frontmatter description")]
    RULES[(".agenta/rules.md<br/>静态偏好")]
    MEM[("user_memory 池<br/>动态偏好")]
    LP[("learning.db<br/>已 /study load 的 plan")]

    SYS --> BASE["base system_prompt<br/>= 常量 + &lt;available_skills&gt; catalog"]
    SKILLS -.->|"启动时拼 catalog"| BASE
    RULES -.->|"启动时一次性加载"| R["拼 &lt;project_rules&gt; 块"]
    MEM -.->|"每轮 load_for_context()"| C["拼 &lt;user_context&gt; 块"]
    LP -.->|"仅当本 session 已 /study load"| P["拼 &lt;active_study_plan&gt; 块"]

    BASE --> R --> C --> P --> OUT["最终 system_prompt<br/>(发给 LLM)"]
```

**注入顺序**：`base system_prompt → <project_rules> → <user_context> → <active_study_plan>`。
**覆盖约定**：用户定义高于系统默认，后注入覆盖前注入。如：AgentA 提供的默认能力（base）可被项目偏好（rules）覆盖，项目偏好可被会话偏好（memory）覆盖。


### 3.5.3 防 prompt injection
参考 [§3.13 防 prompt injection](#313-防-prompt-injection)。



## 3.6 引用展示（Citation）

让用户从 Agent 的回答能直接追溯到知识库原文。每次 RAG 召回后，回答正文带 `[n]` 行内标号，末尾追加 `— sources —` 块写明引自哪个文件、哪个章节、哪一页。
 `web_search` / `fetch_url` 等非 RAG 来源不做引用。

### 3.6.1 数据来源

引用所需元数据由 [§2.1.4 Split](#214split分块) 阶段写入并由 [§2.1.5 Retrieve+Rerank](#215retriverank) 透传。

| 字段 | 来源 | 用于 |
|---|---|---|
| `source` | ingest 时的相对路径（如 `src/rag/retriever.py`） | 引用条目主键 |
| `metadata.heading_path` | Splitter 提取的标题层级 | 章节级定位 |
| `metadata.page_no` | PDF / DOCX ingest 写入 | 页码级定位（Markdown 来源无此字段，省略不显示） |
| `id` | chunk 唯一 ID | builder 内部去重；不进 LLM 回答 |

### 3.6.2 编号规则

引用编号 `[n]` 由 `CitationBuilder` 统一分配，遵循三条约定：

| 约定 | 含义 |
|---|---|
| **每轮独立** | 每次 `Agent.run()` 实例化新 builder，编号从 `[1]` 起；不跨轮累计 |
| **同轮累计** | 同一轮内多次 `search_knowledge` tool_call 共用一个 builder，编号连续递增（第一次 [1][2]，第二次接着 [3][4]） |
| **同源合并** | 同 `(source, heading_path)` 的多个 chunk 共享同一编号，在展示行附 `chunks=N` |


```mermaid
    sequenceDiagram
    autonumber
    participant A as Agent.run()
    participant T as search_knowledge tool
    participant CB as CitationBuilder
    participant L as LLM

    A->>CB: new()（每轮一次）
    CB-->>A: builder 实例
    A->>T: tool_call
    T->>CB: register(Hits)
    CB-->>T: 分配编号 [n]
    T-->>A: 编号化 chunk 文本
    A->>L: prompt（含 [n] 上下文）
    L-->>A: answer（含 [n] 标号）
    A->>CB: extract_used(answer)
    CB-->>A: sources 块
    A-->>A: answer += sources 块
```

### 3.6.3 反幻觉

LLM "造引用"是已知风险（写 `[7]` 但实际只有 `[3]`，或编造不存在的文件路径）。三道防线：

| 防线 | 机制 |
|---|---|
| **编号源头唯一** | `[n]` 完全由 builder 分配；LLM 只能从 prompt 给的"可见编号"里选 |
| **未分配静默丢弃** | `extract_used` 只回填 builder 已分配过的编号；LLM 写了 `[99]` 直接被滤掉 |
| **sources 块程序生成** | 块内容（文件路径 / heading / page）从 builder 内部存的真实 Hit 取，LLM 写不动这部分 |


## 3.7 Agent Skills

符合 Skills 标准规范 [agentskills.io](https://agentskills.io/specification)：frontmatter（`name` + `description`）, catalog 让 LLM 主动认出，markdown 正文在被加载时才进 prompt。

### 3.7.1 数据来源与生命周期

| 项 | 约定 |
|---|---|
| 目录路径 |  `.agenta/skills/<name>/SKILL.md` |
| 加载时机 | 启动时扫描；`/reload-skills` 可热更新|
| frontmatter 必填 | `description`（用于 catalog）；`name` 缺失则回退用目录名 |
| 异常处理 | skill load失败由 CLI / WebUI 显式回显 |

### 3.7.2 渐进披露

Skills 规范定义的**渐进披露（progressive disclosure）**有三层：catalog（目录）/ prompt body（正文）/ scripts（脚本）。AgentA 目前实现前两层。

| 层 | 内容 | 何时 | 进哪 | 目的 |
|---|---|---|---|---|
| **Catalog** | 每个 skill 的 name + description 渲染为 `<available_skills>` XML 块 | 启动时拼到 base system_prompt 末尾 | base system_prompt（[§3.5.2](#352-四层注入顺序)） | LLM 浏览目录、主动认出该用谁 |
| **Prompt Body** | SKILL.md 正文（专业指令、模板、流程约束） | LLM 调 `load_skill` tool 时 | messages 历史（作为 `role: "tool"` 响应，不进 system_prompt） | 完整指令只在用到时才占 context |

```mermaid
    sequenceDiagram
    autonumber
    participant A as Agent.run()
    participant L as LLM
    participant T as load_skill tool

    Note over A,L: 启动时已注入 catalog
    A->>L: user question + catalog
    L-->>A: tool_call(load_skill, name="X")
    A->>T: execute_tool
    T-->>A: SKILL.md body
    A->>L: messages + body
    L-->>A: 按 skill 指令执行的回答
```

## 3.8 Plan-Execute

复杂任务下，Agent 先列计划再分步执行。类似 GHC/Cursor Plan 模式。

### 3.8.1 数据载体

Plan存储在 `messages.tool_calls` JSON 字段里。任意时点的 plan 状态动态算出。

**数据模型**：

| 类 | 字段 |
|---|---|
| `PlanStep` | `id` (int 从 1 起) / `text` (str) / `status` (StepStatus) / `note` (str) |
| `PlanState` | `steps: list[PlanStep]` / `aborted: bool` |
### 3.8.2 三个 tool 

Plan 用 OpenAI Function Calling 暴露给 LLM，跟普通 tool 同一列表。

| tool | 必填参数 | 语义 | 返回内容（写回 LLM 的下一轮 prompt） |
|---|---|---|---|
| `make_plan` | `steps: list[str]` | 列计划（3-6 步，每步 10-30 字） | "已记录 plan，共 N 步" + 步骤清单 + "下一步：第 1 步 — xxx" 指引 |
| `update_step` | `step_id` (int ≥1) / `status` ("success" \| "failed" \| "skipped") + 可选 `note` | 标记某步结果 | "✓/✗step N..." + 当前进度 + 下一 pending 步指引（plan 完成则提示综合答案） |
| `abort_plan` | （都可选） + 可选 `reason` | 主动放弃整个 plan | "plan 已中止" + "请综合已有信息总结答案" |


### 3.8.3 完整流程

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant A as Agent.run()
    participant L as LLM
    participant TCE as ToolCallEngine
    participant PM as plan_manager
    participant EB as EventBus

    U->>A: 复杂任务问题
    A->>L: messages
    L-->>A: tool_calls=[make_plan(steps=[a,b,c])]
    A->>TCE: process(message)
    TCE-->>TCE: execute_tool("make_plan", ...)
    TCE->>EB: plan_created + plan_step_start(1)
    TCE-->>A: tool_msg("已记录 plan...下一步：第 1 步 — a")

    A->>L: messages（含 plan ack）
    L-->>A: tool_calls=[search_knowledge(q=a)]
    A->>TCE: process(message)
    TCE-->>A: tool_msg(hits)

    A->>L: messages
    L-->>A: tool_calls=[update_step(1, success)]
    A->>TCE: process(message)
    TCE-->>TCE: execute_tool("update_step", ...)
    TCE->>PM: reconstruct_from_messages(messages)
    PM-->>TCE: PlanState(progress=1/3, next=step 2)
    TCE->>EB: plan_step_end(1, success) + plan_step_start(2)
    TCE-->>A: tool_msg("✓ step 1...下一步：第 2 步 — b")

    Note over A,L: 重复 step 2 / step 3 同样模式

    A->>L: messages（plan 已完成）
    L-->>A: 最终答（无 tool_calls）
    A-->>U: final_answer
```

### 3.8.4 循环上限自适应

`Agent.run()` 的 ReAct loop 有两层上限：tool 轮次上限（达到后强制 LLM 出文本回答）与总轮次上限（达到后终止 loop）。
Plan 步数多了需要更大预算，所以 tool 轮次上限需按 plan 步数动态调整。

| 状态 | tool 上限 | 总上限 | 硬上限（防极端） |
|---|---|---|---|
| 无 plan / plan 完成/ plan 中止 | `MAX_TOOL_ROUNDS` (默认8) | `Agent.max_iterations` (默认 12) | `MAX_HARD_CAP_ROUNDS` (默认50) |
| 有 active plan N 步 | `max(MAX_TOOL_ROUNDS, N×4 + 2)` | `max(Agent.max_iterations, tool 上限 + 4)` | `MAX_HARD_CAP_ROUNDS` (默认50) |

**Note:**
- 每轮 LLM 调用前重算（每轮都 reconstruct 一次 messages → PlanState）。
- reconstruct 是 O(messages 长度) 纯内存遍历，开销小可忽略。
- 一旦超过总上限 loop 强制退出，走"达最大迭代次数"兜底文本。
- Plan step 失败时**不由程序控制重试 / 跳过 / 中止**，完全交给 LLM 看 `update_step` 的返回后自决：


## 3.9 学习计划制定

让 Agent 帮用户**管理跨 session 长期学习目标**：用户描述目标 → Agent 生成阶段任务清单 → 在任意后续 session 中追踪进度、打勾完成、切换多目标、放弃失败计划。
类似 Todoist / Notion / Anki 的"目标-任务-进度"模型。在 Agent 形态下，状态必须对 LLM 可见（驱动决策 ）、且状态变更由 LLM 通过 tool 触发。

与 [§3.8 Plan-Execute](#38-plan-execute) 的"用完即弃 plan"互为对照 —— 两者都叫 plan，但生命周期与定位不同：

| 维度 | §3.8 Plan-Execute | §3.9 学习计划 |
|---|---|---|
| 用途 | Agent 给**当前问题**拆步骤 | 用户管**长期学习目标** |
| 生命周期 | 单次问答内 | 周 / 月级，跨 session |
| 持久化 | 寄生 messages.tool_calls | 独立 SQLite |
| 谁是状态主人 | Agent（LLM 自决） | 用户（Agent 协助维护） |
| 失败时 | LLM 自决 retry / skip / abort | 用户主动 abandon |

### 3.9.1 数据模型

跨 session 持久化的学习计划存储在独立 SQLite 文件 `learning.db`，由 `learning_plans` 与 `learning_tasks` 两张表承载，1:N 关系。

**`learning_plans`** —— 计划元信息

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER (PK) | plan 唯一 ID |
| `goal` | TEXT | 学习目标描述（如 "8 周准备 ML 面试"） |
| `weeks` | INTEGER | 总周数，0 表示未指定 |
| `status` | TEXT | `active` / `completed` / `abandoned` |
| `is_active` | INTEGER | 当前活跃标记，全表至多 1 条为 1 |
| `created_at` / `updated_at` | TEXT | ISO 8601 时间戳 |

索引：`is_active`、`status` 各一份。

**`learning_tasks`** —— 计划下的任务

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER (PK) | task 唯一 ID |
| `plan_id` | INTEGER (FK → learning_plans.id) | 所属 plan，`ON DELETE CASCADE` 级联删除 |
| `stage_idx` | INTEGER | 阶段编号（Week 1, 2...），从 1 起 |
| `order_idx` | INTEGER | 同阶段内顺序，从 1 起 |
| `title` | TEXT | 任务描述（动词起头） |
| `status` | TEXT | `pending` / `success` / `skipped` |
| `note` | TEXT | 完成备注 / 失败原因，写入时截断到 200 字 |
| `completed_at` | TEXT | `success` / `skipped` 时填，否则空串 |

索引：`(plan_id, stage_idx, order_idx)` 复合索引，用于按计划取任务并按阶段渲染。

**不变量**

- 任意时刻 `learning_plans.is_active = 1` 的记录 ≤ 1 条；切换由 app 层事务保证（先全置 0 再 set 1）
- `learning_tasks.plan_id` 外键 `ON DELETE CASCADE`，删 plan 自动级联清 task，无孤儿数据
- `(stage_idx, order_idx)` 决定同 plan 内任务渲染顺序，不要求唯一（允许并列任务）
- `note` 写入时截断到 200 字，防止 LLM 写小作文撑爆 context

### 3.9.2 三个 tool

操作学习计划用 OpenAI Function Calling 暴露给 LLM。

| tool | 必填参数 | 语义 | 返回内容（写回 LLM 下一轮 prompt） |
|---|---|---|---|
| `create_study_plan` | `goal` / `tasks: [{stage_idx, order_idx, title}]` + 可选 `weeks` | 一次性创建 plan + 全部 task | "✓ 已创建 plan_id=N..." + 任务数 + 用户呈现指引 |
| `update_study_progress` | `plan_id` / `task_id` / `status` + 可选 `note` | 标记单任务状态 | "✓ task_id=N → status" + 当前进度 + 下一个待办（全 success 时自动 complete plan） |
| `query_study_status` | （都可选）`plan_id` / `list_all` / `detail` | 查 plan：默认 active / 指定 / 全部摘要 | 摘要 markdown（detail=true 含全任务清单） |

### 3.9.3 完整流程

学习计划的生成本身就是一个**复杂多源任务**（要查领域 KB、要拆阶段、要列任务、要落库），自然适用 §3.8 Plan-Execute 来分步驱动。该嵌套是有意设计 —— 让 LLM 用同一套 plan-execute 心智模型驱动业务 plan 的生成，避免引入第 2 套"长方法链"风格。

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant A as Agent
    participant L as LLM
    participant SK as study-planner skill
    participant T as Tools
    participant LP as LearningPlanStore

    U->>A: "我想 8 周准备 ML 面试"
    A->>L: chat(messages, tools)
    L-->>A: load_skill(study-planner)
    A->>SK: 加载 SKILL.md body
    SK-->>A: skill 指引

    Note over A,L: 下一轮 — LLM 把 plan 生成本身拆成 4 步
    A->>L: chat(messages, tools)
    L-->>A: make_plan(steps=[查领域 KB, 列阶段, 列任务, 落库])

    Note over A,L: Step 1: 查领域
    A->>L: chat(...)
    L-->>A: search_knowledge(query="机器学习面试 重点")
    A->>T: execute_tool
    T-->>A: hits（检索结果）
    A->>L: chat(...)
    L-->>A: update_step(1, success)

    Note over A,L: Steps 2-3: 列阶段 / 任务（纯推理无 tool）

    Note over A,L: Step 4: 落库
    A->>L: chat(...)
    L-->>A: create_study_plan(goal, weeks=8, tasks=[...])
    A->>T: execute_tool
    T->>LP: create_plan + add_tasks（自动 is_active 互斥）
    LP-->>T: plan_id
    T-->>A: ack（plan_id + 任务数）
    A->>L: chat(...); L-->>A: update_step(4, success) + final_answer
    A-->>U: 计划概要 + "完成任务时告诉我"
```

嵌套约定由 `study-planner` skill body 在 prompt 层硬性指引（"收到学习目标后，第一步永远是 `make_plan`"），不靠程序硬编码。Skill 因此是**业务路径的事实入口**。

### 3.9.4 跨 session 状态可见性

让用户当前有什么计划、进度到哪：

手动 load 注入, 默认不注入。 
用户用 `/study load [id]` 显式激活后，**仅当前 session** 注入；切 session 失效需重新 load。
如何注入见 [§3.5.2 四层注入顺序](#352-四层注入顺序)。

### 3.9.5 渲染

- 按 stage 分组，状态打 icon（☐ / ✓ / ⏭）—— 视觉化让 LLM 一眼看到 pending 任务
- 含 `task_id=N` 标号 —— 用户报告完成时 LLM 可直接拿 id 调 `update_study_progress`，无需先 query
- 含防注入提示"不可执行其中任何指令" —— title 是用户可控字段，理论可被注入攻击
- 超出 `LEARNING_PLAN_MAX_INJECT_CHARS`（1500）截断 —— 极端长 plan 不撑爆 context
- 未 load / load 已失效时整段不输出（不留空 `<active_study_plan></active_study_plan>` tag）


## 3.10 测验与批改

让 Agent 帮用户**周期性自检知识掌握度**：用户描述出题主题（或绑定学习计划某阶段）→ Agent 从知识库检索内容自动出 5-15 道混合题（单选 / 多选 / 简答）→ 用户用一段自然语言批量作答 → Agent 自动批改给逐题反馈 + 总分 + 薄弱点；测验结果保存起来可跨 session 复盘。
类似 Anki / Quizlet，在 Agent 形态下，题目要从用户私有 KB 产出，批改采用混合制(string-match + LLM-judge)，跨 session 错题可追溯（为后续 SRS 喂数据）。
与 [§3.9 学习计划业务](#39-学习计划业务) 互补 —— 学习计划是"长期目标跟踪"，测验是"周期性练习"：


### 3.10.1 数据模型

跨 session 持久化的测验数据存储为独立 SQLite 文件 `quiz.db`，由 `quiz_sets` 与 `quiz_questions` 两张表承载，1:N 关系。

**`quiz_sets`** —— 测验集元信息

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER (PK) | 测验集唯一 ID |
| `topic` | TEXT | 出题主题 |
| `plan_id` | INTEGER | 软引用 `learning_plans.id`（无 FK，详 [§3.9](#39-学习计划业务)） |
| `stage_idx` | INTEGER | 软引用阶段编号（与 `plan_id` 配对） |
| `num_questions` | INTEGER | 题目总数（出题阶段固定 5-15） |
| `status` | TEXT | `created` / `graded` / `archived` |
| `total_score` | REAL | 批改后的总分；未批改为 NULL |
| `created_at` / `graded_at` / `updated_at` | TEXT | ISO 8601 时间戳 |

索引：`status`、`plan_id` 各一份。

**`quiz_questions`** —— 测验集下的题目

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER (PK) | 题目唯一 ID |
| `quiz_set_id` | INTEGER (FK → quiz_sets.id) | 所属测验集，`ON DELETE CASCADE` 级联删除 |
| `order_idx` | INTEGER | 同测验内题号（应用层维护，允许跳号） |
| `q_type` | TEXT | `mcq_single` / `mcq_multi` / `short_answer` |
| `stem` | TEXT | 题干 |
| `options` | TEXT | 选项 JSON 字符串（简答题为空串） |
| `correct_answer` | TEXT | 标准答案 |
| `explanation` | TEXT | 答案解析 |
| `user_answer` | TEXT | 用户作答（批改前为空串） |
| `score` | REAL | 单题得分 0.0-1.0（MCQ 整对 1.0 / 否则 0；简答按 LLM-judge） |
| `feedback` | TEXT | 批改反馈，写入时截断到 500 字 |
| `harness_flagged` | INTEGER | critic 自检标记位（详 [§3.12](#312-harness-自检)） |

索引：`(quiz_set_id, order_idx)` 复合索引，用于按测验取题并按题号渲染。

**不变量**

- `quiz_questions.quiz_set_id` 外键 `ON DELETE CASCADE`，删测验自动级联清题，无孤儿数据
- `(quiz_set_id, order_idx)` 决定题号渲染顺序，不要求唯一（容忍 LLM 偶然跳号）
- `options` 以 JSON 字符串存；读回时反序列化为 list，序列化失败软返回空 list
- `feedback` 写入时截断到 500 字，防止 LLM 写小作文撑爆 context
- `status = archived` 的测验拒绝再次批改（防止误改历史归档）

### 3.10.2 三个 tool

| tool | 必填参数 | 语义 | 返回内容 |
|---|---|---|---|
| `create_quiz` | `questions: [{order_idx, q_type, stem, options?, correct_answer, explanation?}]` + 至少一个 `topic` 或 `plan_id` | 一次性创建测验 + 全部题目落库 | "✓ 已创建 quiz_set_id=N..." + 题数 + 用户呈现指引 |
| `grade_quiz` | `quiz_set_id` / `user_answers: {question_id: 答案串}` | 批改 + 落库批改结果 + 计算总分 | 总分 + 错题清单（含考点 / 标答 / LLM 反馈） |
| `query_quiz_history` | 全部可选（`quiz_set_id` / `plan_id` / `limit` / `detail`） | 三路径互斥查询 | 单套测验详情 / plan 关联列表 / 全局列表（按优先级取一种） |

### 3.10.3 完整流程

测验生成本身是个**多源任务**（解析意图 / 查 KB / 组题 / 落库），使用 [§3.8 Plan-Execute](#38-plan-execute) 分步驱动，嵌套类似 [§3.9.3](#393-端到端流程) 。

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant A as Agent
    participant L as LLM
    participant SK as quiz-maker skill
    participant T as Tools
    participant Q as QuizStore

    U->>A: "考考我 RAG 检索基础，出 10 道题"
    A->>L: chat(messages, tools)
    L-->>A: load_skill(quiz-maker)
    A->>SK: 加载 SKILL.md body
    SK-->>A: skill 指引

    Note over A,L: 下一轮 — LLM 把测验生成拆成 4 步
    A->>L: chat(messages, tools)
    L-->>A: make_plan(steps=[解析意图, 查 KB, 组题, 落库])

    Note over A,L: Step 1: 解析意图（topic="RAG 检索基础", 10 题）
    Note over A,L: Step 2: 查 KB
    A->>L: chat(...); L-->>A: search_knowledge(query="RAG 检索 召回 rerank")
    A->>T: 执行检索 → 命中 chunks
    A->>L: chat(...); L-->>A: update_step(2, success)

    Note over A,L: Step 3: 组题（按 60% MCQ + 40% 简答；纯推理无 tool）

    Note over A,L: Step 4: 落库
    A->>L: chat(...)
    L-->>A: create_quiz(topic="RAG 检索基础", questions=[...10 题])
    A->>T: execute_tool
    T->>Q: create_quiz_set + add_questions
    Q-->>T: quiz_set_id
    T-->>A: ack(quiz_set_id + 题数)
    A->>L: chat(...); L-->>A: update_step(4, success) + final_answer
    A-->>U: 题目呈现 + "按 『1.B 2.AC 3. <文字>』格式作答"

    Note over U,A: 下一轮 —— 用户作答
    U->>A: "1.B 2.AC 3. RAG 是检索增强生成..."
    A->>L: chat(messages, tools)
    L-->>A: grade_quiz(quiz_set_id, user_answers={"15": "B", "16": "AC", ...})
    A->>T: execute_tool
    T->>T: 按 q_type 分发 — MCQ 字符串归一化比对 / 简答内置 LLM-judge
    T->>Q: update_grading + status=graded
    T-->>A: 总分 + 错题清单
    A-->>U: 友好反馈（含薄弱点 + 复习建议）
```

嵌套约定由 `quiz-maker` skill body 在 prompt 层硬性指引（"收到出题请求后，第一步永远是 `make_plan`"）。

### 3.10.4 批改策略

不同题型需要不同判分器：

| 题型 | 判分器 | 
|---|---|
| 选择题 | 字符串归一化比对：等则 1.0 / 否则 0.0 | 
| 简答题 |  LLM-judge 调 `chat()` 给 0-1 浮点 + ≤ 60 字反馈 | 

**LLM-judge 失败软返回 0.0**：网络问题 / JSON 解析失败时不抛异常，返回 (0.0, 错误说明)，避免单题失败让整次测验失败。

## 3.11 主动复习(SRS)

让 Agent 帮用户**按遗忘曲线长期巩固已学知识**：用户做完测验有错题（或手动加一张卡）→ 进入跨 session 持久化的 SRS 队列 → 之后用户每次说"今天复习"就被引导复习到期卡片 → 用户对每张卡用 4 档自评（again / hard / good / easy）→ 调度算法（SM-2）动态调整卡片。
类似 Anki， 但在 Agent 形态下，入队走对话语义，复习过程被 Skill 编排成一问一答，LLM 不感知公式细节。

### 3.11.1 数据模型

跨 session 的 SRS 卡片数据存储在独立 SQLite 文件 `srs.db`，由 `srs_cards` 表承载。

**`srs_cards`** —— SRS 卡片

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER (PK) | 卡片唯一 ID |
| `source_type` | TEXT | 来源类型：`quiz_question`（测验错题）/ `manual`（用户手动加） |
| `source_ref` | INTEGER | 软引用源数据 ID（如 `quiz_questions.id`）；`manual` 来源时为 NULL |
| `front` | TEXT | 卡正面（问题），从源数据冗余复制 |
| `back` | TEXT | 卡背面（答案），从源数据冗余复制 |
| `note` | TEXT | 用户备注 |
| `ease_factor` | REAL | SM-2 难度系数，默认 2.5，写库前夹紧到 ≥ 1.3 |
| `interval_days` | INTEGER | 复习间隔天数，默认 0（新卡），写库前夹紧到 ≥ 1 |
| `repetitions` | INTEGER | 累计答对次数 |
| `lapses` | INTEGER | 累计 again 次数 |
| `next_review_at` | TEXT | 下次到期时间 ISO 8601；新卡初始化 = `created_at`（立即 due） |
| `last_reviewed_at` | TEXT | 上次复习时间 ISO 8601；从未复习为空串 |
| `status` | TEXT | `active`（在 due 队列）/ `suspended`（用户暂停）/ `archived`（软删，list 默认不显示） |
| `created_at` / `updated_at` | TEXT | ISO 8601 时间戳 |

索引：
- `(status, next_review_at)` 复合索引，用于按 due 时间筛 active 卡
- `(source_type, source_ref)` 复合索引，用于防重复入队查询

**不变量**

- `ease_factor` 写库前夹紧到 ≥ 1.3（SM-2 原版约定），无上限（用户连答 easy 自然上扬合理）
- `interval_days` 写库前夹紧到 ≥ 1（不允许同日二次出现，避免短期记忆作弊）
- `next_review_at` 初始化 = `created_at`（新卡立即 due，首次 review 后 SM-2 给真正 interval）
- 同源去重：以 `(source_type, source_ref)` 在 `status != archived` 范围查重；存在则跳过新增（防一张错题入两次）
- `archived` / `suspended` 卡拒绝 `update_review_state`（store 层兜底，scheduler 层不感知）


### 3.11.2 四个 tool

| tool | 必填参数 | 语义 | 返回内容 |
|---|---|---|---|
| `add_to_srs` | `source_type`（`quiz_question` / `manual`）+ `question_ids` 或 `front`+`back` | 卡入队（quiz 批量 / manual 单卡）；防重复 | "✓ 新增 N 张 / 跳过 M 张" + card_id 列表 |
| `query_srs_due` | 全部可选（`limit` / `detail`） | 列 active 且 `next_review_at <= now` 的 due 卡 | 摘要列表 / detail=true 时含完整 front+back |
| `review_srs_card` | `card_id` + `rating`（4 档之一）| 调 srs_scheduler 算出新状态 → 写库 | "✓ 新 ease=X / iv=Yd / next=Z" |
| `query_srs_stats` | 无 | 队列摘要统计 | total_active / due_count / 平均 ease / mature(≥21d) 数 |


### 3.11.3 完整流程

复习路径**没有 plan-execute 嵌套**，SRS review 是单 tool 多轮交互（用户读 → 评分 → tool 调度），非多源任务。

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant A as Agent
    participant L as LLM
    participant SK as srs-review skill
    participant T as Tools
    participant S as SRSStore
    participant SCH as srs_scheduler

    U->>A: "今天有什么要复习的？"
    A->>L: chat(messages, tools)
    L-->>A: load_skill(srs-review)
    A->>SK: 加载 SKILL.md body
    SK-->>A: skill 指引（"先调 query_srs_due detail=true"）

    Note over A,L: 第 2 轮 — LLM 拉 due 列表
    A->>L: chat(messages, tools)
    L-->>A: query_srs_due(detail=true)
    A->>T: execute_tool
    T->>S: list_due()
    S-->>T: [card 5, card 12, card 18]
    T-->>A: due 列表（含 front+back）
    A->>L: chat(...)
    L-->>A: final_answer("📚 今天 3 张，第 1/3：<front>，想想答案后告诉我评分")
    A-->>U: 单卡呈现 — 不揭晓答案

    Note over U,A: 第 3 轮 — 用户回忆 + 评分
    U->>A: "我想起来了，good"
    A->>L: chat(messages, tools)
    L-->>A: review_srs_card(card_id=5, rating="good")
    A->>T: execute_tool
    T->>S: get_card(5)
    S-->>T: CardState(ef=2.5, iv=1, reps=0, lapses=0)
    T->>SCH: schedule_review(state, "good")
    SCH-->>T: ScheduleResult(ef=2.5, iv=1, reps=1, next=2026-01-02 ...)
    T->>S: update_review_state(card_id=5, new_state)
    S-->>T: ok
    T-->>A: "✓ ef=2.50 iv=1d next=2026-01-02"
    A->>L: chat(...)
    L-->>A: final_answer("✓ 评分 good。答案：<back>。下次 1 天后。第 2/3：<下一张 front>")
    A-->>U: 揭晓答案 + 进入下一张
```

## 3.12 Harness 自检

让 Agent 在产出"主观打分 / 检索召回"等**半客观结果**后多走一步 LLM-as-Judge 复审，提高LLM输出的质量。

自检覆盖的场景：

| 路径 | 触发位置 | critic 复审什么 | 
|---|---|---|
| **Q1 — 测验批改** | `grade_quiz` 跑完简答题 LLM-judge 之后 | "Agent 给的 score + feedback 跟用户答案 vs 标答的实际语义贴合度是否一致" | 
| **R1 — RAG 召回** | `search_knowledge` 拿到 hits 之后 / 格式化之前 | "每条召回片段是否与用户问题相关（5/0 二分类）" | 


### 3.12.1 自检实现

`HarnessManager`以进程级单例承载两路 critic，由主路径 tool 直接调用（不走 skill）：

| 路径 | 入口 | critic 调用 | 失败软返回 |
|---|---|---|---|
| **Q1** | `_tool_grade_quiz` → `_run_quiz_critic`（仅 `short_answer`，MCQ 跳过） | 单题逐次调 `review_grading()`，内部复用 `judge_with_llm` helper；critic 0-5 分，< 阈值即 flag `harness_flagged` | 超时 / 解析失败 → `HarnessVerdict(passed=True, failure=True)`，不 flag |
| **R1** | `_tool_search_knowledge`（hits 之后、格式化之前） | 一次 LLM 调用批量评 K 条，prompt 内附编号 1..K 要求返回 `{"verdicts": [...]}` JSON；单条 score ≥ 3.0 保留 | 超时 / 解析失败 → 返回原始 hits 不过滤 |

**配置项**

| 配置项 | 用途 | 默认 |
|---|---|---|
| `HARNESS_QUIZ_ENABLED` | Q1 全局开关 | `true` |
| `HARNESS_RAG_ENABLED` | R1 全局开关 | `true` |
| `HARNESS_LLM_TIMEOUT_SEC` | critic 单次调用超时（秒） | `15` |
| `HARNESS_GRADING_THRESHOLD` | Q1 critic 阈值（< 该值即 flag） | `3.5` |


### 3.12.2 完整流程

Q1 测验批改 + 自检：

```mermaid
sequenceDiagram
    autonumber
    participant LLM as LLM
    participant T as _tool_grade_quiz
    participant SS as _grade_one_short_answer
    participant QS as QuizStore
    participant HM as HarnessManager
    participant J as judge_with_llm

    LLM->>T: grade_quiz(quiz_set_id, user_answers)
    Note over T: MCQ 字符串比对
    T->>SS: 简答 LLM-judge（一阶）
    SS-->>T: (score, feedback)
    T->>QS: update_grading(...)
    QS-->>T: ok

    Note over T,HM: Phase 2.5：critic 复审简答题（二阶）
    loop 每道 short_answer 题
        T->>HM: review_grading(stem, user_answer, correct, agent_score, agent_feedback)
        HM->>J: judge_with_llm(prompt, output, criteria=quiz_critic.txt)
        J-->>HM: JudgeResult(score, reason)
        HM-->>T: HarnessVerdict(passed, score, reason)
        alt verdict.passed=False
            T->>QS: mark_question_harness_flagged(qid)
        end
    end
    T-->>LLM: 总分 + 错题清单 + ⚠️ harness_warning 段
```

R1 RAG 召回 + 过滤：

```mermaid
sequenceDiagram
    autonumber
    participant LLM as LLM
    participant T as _tool_search_knowledge
    participant R as retriever.search
    participant HM as HarnessManager
    participant Chat as chat()
    participant FMT as format_search_results

    LLM->>T: search_knowledge(query, top_k)
    T->>R: 多 query 召回 + RRF + rerank + dedupe
    R-->>T: hits: list[Hit] (K 条)

    Note over T,HM: Phase 2.5：critic 一次评 K 条
    T->>HM: filter_chunks(query, hits)
    HM->>Chat: 拼 K 条 chunks 进 batch prompt（rag_critic.txt）
    Chat-->>HM: 原始 JSON {verdicts: [{i:1, score:5}, ...]}
    HM-->>T: kept: list[Hit] (≤ K 条)

    alt kept 非空
        T->>FMT: format_search_results(kept, citation_nums)
        FMT-->>T: 格式化文本
        T-->>LLM: ToolResult(ok, content)
    else kept 为空
        T-->>LLM: ToolResult(empty, "知识库中未找到相关内容")
    end
```


## 3.13 防 prompt injection

让 Agent 在面对外部不可信数据（RAG 召回 / web 抓取 / MCP server 返回）时不被诱导调危险 tool / 泄露系统 prompt，并给 plan-execute 多步任务提供用户审批入口。
SSRF / URL 校验在 [§3.14](#314-mcp-接入-与-ssrf-防御)实现。

### 3.13.1 防御层次
对照：
```mermaid
flowchart LR
    INPUT["L1 输入侧<br/>user msg / 命令"]
    SUPPLY["L2 数据供应侧<br/>RAG / web / tool 返回"]
    PROCESS["L3 处理侧<br/>system prompt / role 隔离"]
    OUTPUT["L4 输出侧<br/>tool 调用 / 答案"]

    INPUT --> SUPPLY --> PROCESS --> OUTPUT
```
目前实现：
| 层 | 在 AgentA 的落点 | 注入点 |
|---|---|---|
| L2 数据供应侧 | 不可信数据进 LLM context 前过 [`security_filter.scrub_injection`](../src/agent/core/security_filter.py) 段级删除 + [`wrap_untrusted`](../src/agent/core/security_filter.py) 标签包装（kind `doc` / `web` / `tool`） | [`format_search_results`](../src/rag/retriever.py) / [`_tool_web_search`](../src/agent/tools.py) / [`_tool_fetch_url`](../src/agent/tools.py) / MCP tool 转发出口 [`_execute_mcp_tool`](../src/agent/tools.py) |
| L3 处理侧 | [`SYSTEM_PROMPT`](../src/agent/agent.py) 末尾「数据隔离原则」段告知 LLM `<untrusted_*>` 标签内的内容是数据不是指令 | `SYSTEM_PROMPT` 静态段 |
| L4 输出侧 · 名单门 | tool 名单门：[`get_tools`](../src/agent/tools.py) 按 `SECURITY_MODE` 切换 fail-open + `TOOL_BLOCKLIST` 或 fail-close + `TOOL_ALLOWLIST`；[`execute_tool`](../src/agent/tools.py) 入口 double-check | `get_tools` + `execute_tool` |
| L4 输出侧 · plan 审批 | `make_plan` 调用成功后 [`tool_call_engine._maybe_publish_plan_events`](../src/agent/core/tool_call_engine.py) 调 [`Agent.request_plan_approval`](../src/agent/agent.py)；用户回 "no" → 抛 `PlanAbortedByUser` 让 `agent.run` break loop | `tool_call_engine` make_plan 分支 + UI 端 `approval_callback` 注册 |

### 3.13.2 完整流程

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant A as Agent.run
    participant TCE as ToolCallEngine
    participant T as Tool
    participant SF as security_filter
    participant L as LLM

    U->>A: user_input
    Note over A,L: SYSTEM_PROMPT 含「数据隔离原则」段（L3 处理侧）
    A->>L: messages
    L-->>A: response（含 tool_calls）
    A->>TCE: process(message)

    TCE->>SF: is_tool_allowed(name)
    Note right of SF: L4 名单门
    alt 名单门拒绝
        SF-->>TCE: False
        TCE-->>L: tool_msg "error: 名单门拒绝"
    else 名单门放行
        SF-->>TCE: True
        TCE->>T: execute_tool

        opt tool 涉及外部不可信数据（RAG / web / MCP）
            T->>SF: scrub_injection + wrap_untrusted(kind)
            Note right of SF: L2 数据供应侧
            SF-->>T: 含 untrusted_* 标签的内容
        end
        T-->>TCE: ToolResult

        opt tool == make_plan 且调用成功
            TCE->>A: request_plan_approval(plan)
            A->>U: 询问 yes / no
            Note right of A: L4 plan 审批
            alt 用户 no
                U-->>A: "no"
                TCE-->>A: raise PlanAbortedByUser → break loop
            else 用户 yes
                U-->>A: "yes"
                TCE-->>A: publish plan_created event
            end
        end
    end
```

## 3.14 MCP 支持
Agent 作为 MCP Host 支持 [标准 MCP协议](https://modelcontextprotocol.io) 。
Agent 通过本地配置文件`.agenta/mcp/config.json`接入 MCP server（如 `@modelcontextprotocol/server-filesystem` / `mcp-server-fetch`），把 server 暴露的 tool 合并后发给 LLM，LLM 像调内置 tool 一样调用这些 server 暴露的 tool。
同时实现 SSRF（Server-Side Request Forgery）防御，统一拦截内置 `fetch_url` 与 MCP `fetch.fetch` 双入口。

### 3.14.1 角色映射

| MCP 角色 | 在 AgentA 的实体 |
|---|---|
| Host | `python main.py` 主进程（含 Agent core + LLM 客户端） |
| Client | [`MCPManager`](../src/agent/core/mcp_manager.py)：每个 server 对应一个 `ClientSession`，由模块级单例 + 后台 thread 持有 |
| Server | 配置文件里写的 `command + args` 启起来的子进程（stdio transport） |

```mermaid
flowchart TB
    subgraph HOST["AgentA Host（python main.py）"]
        AGENT["Agent core<br/>+ LLM 客户端"]
        TOOLS["tools.execute_tool<br/>name 含 '.' → MCP 转发"]
        MGR["MCPManager（singleton）<br/>后台 thread + event loop"]
        AGENT --> TOOLS --> MGR
    end

    subgraph SERVERS["MCP Servers（子进程，stdio）"]
        FS["filesystem<br/>npx ... server-filesystem"]
        FETCH["fetch<br/>python -m mcp_server_fetch"]
    end

    MGR -. "JSON-RPC<br/>initialize / list_tools / call_tool" .-> FS
    MGR -. "JSON-RPC<br/>initialize / list_tools / call_tool" .-> FETCH

    CONF[(".agenta/mcp/config.json")] -. "mcp_config.load_mcp_config" .-> MGR

    classDef host fill:#eef6ff,stroke:#5b8def
    classDef server fill:#fff5e6,stroke:#e89c3a
    class AGENT,TOOLS,MGR host
    class FS,FETCH server
```

> 说明：图中只画 **MCP 角色**之间的关系（Host / Client / Server / Config）。tool 返回值的 `security_filter` 清洗（详 [§3.13](#313-防-prompt-injection)）和 `url_guard` 拦截（详 [§3.14.2](#3142-ssrf-防御)）属于穿越本图的安全关卡，不在角色拓扑里画。

### 3.14.2 SSRF 防御

[`url_guard.is_url_safe(url) -> bool`](../src/agent/core/url_guard.py) 是 host 侧针对**内置 `fetch_url`** 的入口防线，覆盖如下拒绝类别：

| 拒绝类别 | 覆盖范围 |
|---|---|
| 非法 scheme | 非 `http(s)`（含 `file://` / `ftp://` / 自定义 scheme） |
| localhost 字面值 | `localhost` / `localhost.localdomain` / `ip6-localhost` / `ip6-loopback` |
| 私有 IPv4 | 10/8、172.16/12、192.168/16、127/8 |
| 保留 IP | loopback / link-local（含 AWS metadata `169.254.169.254`）/ multicast / reserved / unspecified |
| 域名 DNS 反查 | `socket.gethostbyname` 解析后再判私有/保留段，防 DNS rebinding |
| 解析失败 | 一律拒（保守路径） |

**双入口的拦截路径**（以代码实际为准）：

```mermaid
sequenceDiagram
    autonumber
    participant L as LLM
    participant E as execute_tool
    participant FU as _tool_fetch_url
    participant UG as url_guard.is_url_safe
    participant MC as _execute_mcp_tool
    participant MGR as MCPManager
    participant FS as fetch server（子进程）

    alt name == "fetch_url"（内置入口）
        L->>E: tool_call(fetch_url, url)
        E->>FU: 路由
        FU->>UG: is_url_safe(url)
        alt 拒绝（私网 / file:// / DNS 失败）
            UG-->>FU: False
            FU-->>L: ToolResult(error="URL 被拒")
        else 放行
            UG-->>FU: True
            FU->>FU: requests.get(url)
            FU-->>L: ToolResult(ok, 正文)
        end
    else name == "fetch.fetch"（MCP 入口）
        L->>E: tool_call("fetch.fetch", url)
        E->>MC: 路由（name 含 "."）
        Note over MC: host 侧未过 is_url_safe<br/>SSRF 防线落在 MCP server 子进程内
        MC->>MGR: call_tool("fetch.fetch", args)
        MGR->>FS: JSON-RPC tools/call
        FS-->>MGR: 返回正文
        MGR-->>MC: text
        MC-->>L: ToolResult(ok, 包装后正文)
    end
```

> **现状说明**：内置 `fetch_url` 与 MCP `fetch.fetch` 的 URL 拦截**并未共用** host 侧 `url_guard`——前者在 `_tool_fetch_url` 里显式调 `is_url_safe`（[tools.py L758-759](../src/agent/tools.py)），后者在 `_execute_mcp_tool` 里直接转发给子进程（[tools.py L2228+](../src/agent/tools.py)），依赖 MCP server 自身实现 SSRF 防御（`mcp-server-fetch` 默认不抓内网，但这是 server 端约定而非 host 强制）。`url_guard.py` docstring 写的"二者共用同一道防线"是设计意图，**当前实现尚未对齐**。


### 3.14.3 配置文件

`.agenta/mcp/config.json` 参考 Cursor / Claude Desktop 配置文件格式：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `servers.<name>` | object | ✅ | server 名作为 namespace 前缀；禁含 `.` |
| `servers.<name>.command` | string | ✅ | 可执行命令名或绝对路径（如 `npx` / `.venv/Scripts/python.exe`） |
| `servers.<name>.args` | string[] | 可选 | 命令行参数，缺省为 `[]` |
| `servers.<name>.env` | object | 可选 | 注入 server 子进程的环境变量；value 内 `${VAR}` 从进程 env 展开，缺失保留原样 |


# 4.表现层

表现层负责"采集输入 → 调用 Agent → 渲染输出"三件事，按 IO 形态分为 CLI / Web UI / SDK 三种形态，全部通过 `AgentAPI` 与 Agent core 通信。

```mermaid
flowchart TB
    subgraph CLI["CLI"]
        ENTRY["main.py<br/>启动 · 预热 · 主循环"]
        DISPATCH["dispatcher.py<br/>命令解析 + 路由"]
        H["handlers/<br/>session · memory · thinking · history · save · reload"]
        RENDER["render.py<br/>事件流 → 终端渲染"]
        ENTRY --> DISPATCH --> H
        H -->|"事件流"| RENDER
    end

    subgraph WEB["Web UI"]
        WS["WebSocket / SSE handler"]
        UI["前端组件"]
        WS --> UI
    end

    subgraph SDK["SDK / 脚本"]
        SCRIPT["from src.agent import Agent<br/>agent.run(...)"]
    end

    AAPI(("AgentAPI"))

    subgraph FILES["文件驱动配置"]
        direction LR
        R[".agenta/rules.md"]
        SK[".agenta/skills/&lt;name&gt;/SKILL.md"]
    end

    H -->|"调用"| AAPI
    WS -->|"事件流 + 调用"| AAPI
    SCRIPT -->|"调用"| AAPI
    R -.加载.-> H
    SK -.加载.-> H
```

**设计要点**

- **三段分离**：命令解析（`dispatcher`）→ 业务处理（`handlers/`）→ 渲染（`render`）三段独立，替换 IO 形态时只换 render 段。
- **事件流驱动 UI**：handler 不直接 `print`，而是 emit `AgentEvent`；CLI 的 render 把事件转成终端流式输出，Web UI 直接转 WebSocket / SSE。
- **文件驱动配置**：Prompt 与 Skill 都用文件落地，启动时扫描 + 运行时 `/reload-*` 热更新；新增不需要改代码。
- **本期范围**：CLI 形态完成重构（命令解析 / handler / render 三段分离）；Web UI / SDK 留接口位，后续任务实现。

## 4.1.CLI

### 4.1.1 Thinking 渲染分层

Extended Thinking 的"思考流"在 CLI 端按"Provider 推流 → EventBus 分发 → 渲染层组装"三段分层，渲染状态全部在渲染层闭包内自管，Agent core 不感知 UI 细节。

```mermaid
flowchart LR
    PROV["LLM Provider<br/>流式 thinking_delta"]
    AG["Agent._on_thinking_chunk<br/>publish(EVENT_THINKING_CHUNK)"]
    BUS["EventBus<br/>异常隔离 + 多订阅扇出"]
    CLI["handlers.run_query<br/>_event_router 闭包状态机"]
    CHL["chainlit_app._event_router<br/>独立 cl.Message 推送"]
    OUT["stdout 终端"]

    PROV --> AG --> BUS
    BUS --> CLI --> OUT
    BUS --> CHL
```

**渲染约定**

| 维度 | 约定 |
|---|---|
| 段起止 | 首段 thinking_chunk 到来时打 header（`💭 思考中...`），段切换时打 footer（`─── 思考结束 ───`）|
| 段切换信号 | `token_chunk` / `plan_created` / `plan_step_end` / `tool_call_start` 任一事件到达，渲染层先关闭未关闭的 thinking 段再渲染目标事件 |
| 多轮编号 | 单 query 多轮 thinking（tool 调用后再思考）独立分段；首轮不带编号，第 N≥2 轮 header / footer 带 `（第 N 轮）`，编号由渲染层自管不污染 EventBus payload |
| 行前缀 | 每行 thinking 文本前注入 `│ ` 视觉前缀，与正文 `Agent: ...` 行视觉区分；按行检测 + chunk 跨行状态机驱动 |
| 关 thinking | `THINKING_ENABLED=false` 时 Provider 不推 thinking_chunk，渲染层零 artifact（无 `💭` 头 / 无 `─── ───` 尾）|
| 异常隔离 | 渲染端抛异常由 `EventBus.publish` try/except 吞掉，不影响 `agent.run()` 主循环 |
| `run_query` finally | 兜底 `_close_thinking_segment()`，覆盖"仅 thinking 无 token / 异常 / 中断"三种边界 |

**与其他渲染分支共存**：CLI `_event_router` 同时订阅 thinking / token / plan_created / plan_step_end，按段切换协议互不交错；Chainlit 端 `_event_router` 把 thinking / token / plan 各自推到独立 `cl.Message`，与 CLI 渲染策略解耦。

## 4.2.WebUI

## 4.3.SDK


# 5.IMP

三种 Agent 实现共享公共层，差异只在 loop 编排。每个实现都必须：

1. 满足 `AgentAPI` Protocol（duck-typed，不强制继承；定义在 `src/agent/agent_api.py`）
2. 用 `src/agent/core/` 提供的 helper 组装 loop，而不是重写 helper 内部逻辑
3. 至少 emit `final_answer` 与 `error` 事件；`thinking_chunk` / `token_chunk` 视 framework 能力可选

**公共层复用粒度**

| 共享组件 | 类型 | 职责 | Python | LangChain | AutoGPT |
|---|---|---|---|---|---|
| `tools.py`（工具实现） | 依赖 | 业务 tool（`search_knowledge` / `web_search` / `fetch_url` / `load_skill`）+ plan-execute 三 tool（`make_plan` / `update_step` / `abort_plan`，详 [§3.8](#38-plan-execute)）+ 学习计划业务三 tool（`create_study_plan` / `update_study_progress` / `query_study_status`，详 [§3.9](#39-学习计划业务)）+ 测验业务三 tool（`create_quiz` / `grade_quiz` / `query_quiz_history`，详 [§3.10](#310-测验业务)）+ SRS 业务四 tool（`add_to_srs` / `query_srs_due` / `review_srs_card` / `query_srs_stats`，详 [§3.11](#311-srs-主动复习业务)）JSON Schema 定义与 `execute_tool` 路由 | ✓ | ✓（StructuredTool 包装） | ✓ |
| `LLMProvider` | 依赖 | 多 provider chat + Extended Thinking + 流式 token 抽象 | ✓ | △（可选，framework 自带） | ✓ |
| `ChatHistoryStore` | 依赖 | session 内消息持久化与按 N 条加载（CRUD） | ✓ | ✓（adapter） | ✓ |
| `UserMemoryStore` | 依赖 | 跨 session 用户偏好 / 背景持久化（CRUD） | ✓ | ✓ | ✓ |
| `LearningPlanStore` | 依赖 | 跨 session 学习计划与任务持久化（CRUD + active 互斥，详 [§3.9](#39-学习计划业务)） | ✓ | ✓ | ✓ |
| `QuizStore` | 依赖 | 跨 session 测验与题目持久化（CRUD + 三态生命周期 + 软引用 learning_plan，详 [§3.10](#310-测验业务)） | ✓ | ✓ | ✓ |
| `SRSStore` | 依赖 | 跨 session SRS 卡片持久化（CRUD + 三态 + SM-2 调度字段，详 [§3.11.1](#3111-数据模型)） | ✓ | ✓ | ✓ |
| `EventBus` | Helper | 统一流式事件分发（thinking / token / tool / plan / final） | ✓ | ✓ | ✓ |
| `ToolCallEngine` | Helper | 工具调用编排：执行 + 结果格式化 + 引导提示注入 + 写历史 + plan tool 调用后叠加发 `plan_*` 事件 | ✓ | ✓ | ✓ |
| `HistoryManager` | Helper | 历史按轮截断 + skill_pair 完整性保护 + system 拼接 | ✓ | ✓ | △（用 summary 替代） |
| `MemoryManager` | Helper | UserMemory 触发判定 + 提取 + 注入 system_prompt | ✓ | ✓ | ✓ |
| `ThinkingPolicy` | Helper | adaptive thinking budget 估算（LOW / MED / HIGH 三档） | ✓ | ✓ | △（子任务不启用） |
| `plan_manager` | Helper | `PlanStep` / `PlanState` dataclass + `reconstruct_from_messages()`；plan 状态从 messages 历史 reconstruct（详 [§3.8.1](#381-数据载体与-reconstruct)） | ✓ | ✓ | ✓ |
| `srs_scheduler` | Helper | SM-2 公式纯函数（4 档 → ease/interval/repetitions/lapses 调度计算，详 [§3.11.2](#3112-sm-2-算法核心)） | ✓ | ✓ | ✓ |
| `HarnessManager` | Helper | Q1 测验批改自检 + R1 RAG 召回过滤；复用 `judge_with_llm` + 自管 batch prompt + ThreadPoolExecutor timeout（详 [§3.12](#312-harness-自检)） | ✓ | ✓ | ✓ |

> **类型说明**
> - **依赖**：底层能力，不感知 Agent loop 语义（turn / skill_pair / thinking budget），可独立测试与替换实现（如 SQLite → Postgres）。命名约定：数据存储用 `*Store` 后缀。
> - **Helper**：公共层抽象，封装"何时调依赖、如何编排结果"的业务策略，被三种 Agent 实现共享。命名约定：编排类用 `*Manager` / `*Engine` / `*Policy` / `*Bus` 后缀。

**代码组织**

公共层 helpers 统一放在 `src/agent/core/`，命名遵循上文"类型说明"约定：

```
src/agent/core/
├── __init__.py
├── history_manager.py        # HistoryManager
├── memory_manager.py         # MemoryManager
├── event_bus.py              # EventBus（含 EVENT_* 类型常量，含 plan_* 三类）
├── tool_call_engine.py       # ToolCallEngine（含 TOOL_EMPTY_HINT 常量）
├── thinking_policy.py        # ThinkingPolicy + ThinkingConfig 数据类
├── citation_builder.py       # CitationBuilder（详 §3.6）
├── plan_manager.py           # PlanState / PlanStep + reconstruct_from_messages（详 §3.8）
├── srs_scheduler.py          # SM-2 公式纯函数 + Rating / CardState / ScheduleResult（详 §3.11.2）
└── harness_manager.py        # HarnessManager + HarnessVerdict（Q1/R1 critic，详 §3.12）
```

依赖层（`src/memory/chat_history.py` 的 `ChatHistoryStore` / `src/memory/user_memory.py` 的 `UserMemoryStore` / `src/memory/learning_plan_store.py` 的 `LearningPlanStore` / `src/memory/quiz_store.py` 的 `QuizStore` / `src/memory/srs_store.py` 的 `SRSStore` / `src/llm/provider.py`）位置不动，被 helper 调用。

**实现进度**

- Python：本期落地，完成公共层抽取与对接；`Agent.events` 暴露 `EventBus` 实例供 UI 多订阅
- AutoGPT：持有 `events: EventBus` 与 `set_thinking/token_callback` 转发方法，接口对齐；流式事件 emit 视后续 LLM 调用是否接入
- LangChain：保留骨架代码，对接公共层放在后续任务（依赖环境 `langchain.agents.create_agent` 修复）


## Python 

## LangChain

## AutoGPT

# A. Debugging

## A.1 CLI 输出落盘

CLI 终端的所有可见输出（banner / 用户输入 / Agent 回答 / 模块日志 / 异常堆栈）按 `CLI_LOG_MODE` 同步写到 `./logs/` 下的日志文件，便于离线复盘对话与排查 bug。模式默认 `NONE`（不写），关闭时零副作用。支持两种落盘策略：单文件追加（`SINGLE`，固定 `agenta.log`，跨启动 append）与多文件分卷（`MULTI`，每次启动新建带时间戳文件）。

**输出流分布**

| 来源 | 路径 | 是否落盘 |
|---|---|---|
| `print()` 调用 | `sys.stdout` | ✅ 通过 stdout tee |
| `logger.*` 调用 | `logging.StreamHandler` → `sys.stderr` | ✅ 通过 stderr tee（复用，不另起 `FileHandler`） |
| `prompt_toolkit` 渲染（`你: ` 提示符 / 补全菜单 / 控制码） | 终端原生 API（Win32 console / Vt100），绕 Python stream | ❌ 控制码进文件无意义 |
| 用户键入字符 | TTY 驱动回显，不经 Python | ❌ → 由主循环显式补写 `你: <input>` 行 |

**数据流**

```mermaid
flowchart LR
    P[print] --> O[sys.stdout = _Tee]
    L[logger] --> H[StreamHandler] --> E[sys.stderr = _Tee]
    PT[prompt_toolkit] -. 终端原生 API .-> T[终端]
    O --> T
    O --> F[(SINGLE: agenta.log / MULTI: agenta-时间戳.log)]
    E --> T
    E --> F
    UI[用户键入] -. TTY 回显 .-> T
    UI -. 主循环显式补写 .-> F
```

**设计取舍**

| 维度 | 选择 | 取舍 |
|---|---|---|
| 分流机制 | Python 内置 `_Tee` 包装 `sys.stdout` / `sys.stderr` | 不用 shell 重定向 / `tee` 管道 —— 后者破坏 TTY，`prompt_toolkit` 检测不到 TTY 退化为盲打，且 Win / bash 不通用 |
| logger 衔接 | 复用 stderr tee | 不另起 `FileHandler` 避免与 tee 重复写 |
| 用户输入 | 主循环手动写文件，不走 stdout | 不在终端重复打一遍（TTY 已回显过） |
| 文件粒度 | 模式枚举（`SINGLE` 单文件 append / `MULTI` 多文件分卷），不含 rotation | 同一开关收口"长期追加排查"与"单次会话复盘"两种用法；rotation 需求出现再扩 |

**`_Tee` 透明性约定**：包装类只代理 `write` / `flush`，其余属性（`isatty` / `fileno` / `encoding` / `buffer` 等）透传原 stream，确保 `prompt_toolkit` 的 TTY 检测与底层二进制 buffer 访问不受影响。

**配置**

| key | 类型 | 默认 | 含义 |
|---|---|---|---|
| `CLI_LOG_MODE` | str | `NONE` | tee 落盘模式（`NONE` / `SINGLE` / `MULTI`，大小写不敏感）：`NONE` 不写；`SINGLE` 固定 `./logs/agenta.log` 跨启动 append；`MULTI` 每次启动新建 `./logs/agenta-YYYYMMDD-HHMMSS.log` 覆盖；非法值降级 `NONE` 并 warn |
| `LOG_LEVEL` | str | `INFO` | root logger 输出级别（`DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`）；同时作用于终端 stderr 与落盘文件（同一 stream 两路输出）；非法值降级 `INFO` 并 warn。三方噪声库（`httpx` / `chromadb` 等）独立固定 `WARNING`，不随本值缩放 |

实施细节详 [`iter_8_debugging.md §1`](iter_8_debugging.md#1-cli-打印写文件)（落盘）与 [`§2`](iter_8_debugging.md#2-logger-级别可配置)（级别）。

