# obsidian-line-bot

一個透過 LINE 與個人 Obsidian vault 對話的 bot，內建 **manifest / index driven retrieval + deterministic pre-check + bounded LLM search** 的查詢路由設計。

> 中文使用者面向；英文摘要見最下方 [English summary](#english-summary)。

## 為什麼不是 RAG？

這個 repo **不**用向量資料庫，也不靠 embedding similarity 做檢索。它的核心假設是：個人筆記本身就有作者親手維護的結構（資料夾、frontmatter、index.md），這些結構比 embedding 更可靠、也更能解釋。

實際運作：

1. **Index pre-check（決定性）**：先讀 `index.md` 與檔頂第二層目錄，命中即回答，連 LLM 都不用呼叫。
2. **Manifest-driven retrieval**：把 vault 結構 + 命名規則塞進 prompt，讓 LLM 用 Read/Grep 自己挑檔，而不是靠相似度盲撈。
3. **Bounded LLM search**：fallback 才走 LLM，並用 `prompt_rules.py` 把搜尋範圍、輸出格式、引用規則統一限制。
4. **Dual backend router**：同一 query 可切 Claude 或 Codex（`ask_claude.py` / `ask_codex.py`），用 `AGENT_BACKEND` env var 切換，方便比較兩個 agent 在同任務上的差異。

## 架構

```
LINE webhook ─► main.py ─┬─► retrieval.py ── deterministic pre-check（index + 二層目錄）
                         │                     │
                         │                     └─► hit?  ─► reply (no LLM)
                         │
                         └─► ask_claude.py / ask_codex.py ── prompt_rules.py
                                                              │
                                                              └─► Claude / Codex CLI
                                                                    └─► reply
```

| 檔案 | 功能 |
|---|---|
| [main.py](main.py) | LINE webhook 入口、查詢分流、回應格式化、`/open-note` 簽章閱讀頁、todo dashboard |
| [retrieval.py](retrieval.py) | manifest / index 驅動的 deterministic pre-check 與候選筆記擴展 |
| [ask_claude.py](ask_claude.py) | 呼叫 Claude Code CLI（subprocess + stdin prompt） |
| [ask_codex.py](ask_codex.py) | 呼叫 Codex CLI，與 Claude 同介面，可由 `AGENT_BACKEND` 切換 |
| [prompt_rules.py](prompt_rules.py) | 統一的 vault prompt 規則：搜尋策略、輸出格式、生活/工作分流、tag 規範 |
| [build_search_manifest.py](build_search_manifest.py) | 從 vault 產生 retrieval 用的 manifest |
| [frontend/](frontend/) | React + Vite 的 `/open-note` 行動閱讀頁，含 Markdown 渲染、TOC、CodeBlock |
| [samples/dummy-vault/](samples/dummy-vault/) | 公開可用的範例 vault（3 篇假筆記） |
| [tests/golden/sample_retrieval_cases.jsonl](tests/golden/sample_retrieval_cases.jsonl) | 對應 dummy vault 的 regression 測試樣本 |

## AI in our workflow

OpenAI Codex for OSS 申請相關 — 這個專案的開發本身就是 AI workflow 的實例：

- **codex/* 分支**：Codex 直接負責的功能分支，包括 `codex/feature-codex-backend`（為 bot 加上 Codex backend）、`codex/9006-google-rag`（Google RAG MVP 驗證）、`codex/fix-adps-precheck`、`codex/line-result-pages`。
- **dual backend router**：[ask_claude.py](ask_claude.py) 與 [ask_codex.py](ask_codex.py) 同介面，方便 A/B 比較兩個 agent 在「manifest-driven retrieval prompt」這類非典型任務上的表現。
- **prompt 集中管理**：所有給 agent 的 vault 規則寫在 [prompt_rules.py](prompt_rules.py)，更新一處即可同步切換兩個 backend。
- **regression test 驅動**：每次 router 改動跑 [tests/golden/sample_retrieval_cases.jsonl](tests/golden/sample_retrieval_cases.jsonl) 確認沒退化，避免 prompt 漂移。

## Setup

```bash
git clone https://github.com/maintainer/obsidian-line-bot.git
cd obsidian-line-bot
pip install -r requirements.txt
cp .env.example .env
# 編輯 .env，至少填 LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN / LINE_ALLOWED_USER_ID
# VAULT_DIR 預設指向 samples/dummy-vault，直接試跑可不改
```

### 跑 dummy vault demo

```bash
# 預設 VAULT_DIR 指向 ./samples/dummy-vault
uvicorn main:app --reload --port 8000
```

對 LINE bot 發以下 query，回應應命中 dummy vault 的對應筆記：

| Query | 預期命中 |
|---|---|
| `index` | `samples/dummy-vault/index.md` |
| `SampleProjectA 進度` | `samples/dummy-vault/02_Projects/SampleProjectA/issue-tracking.md` |
| `今天的 daily` | `samples/dummy-vault/03_Daily/20260101-daily-report.md` |

`tests/golden/sample_retrieval_cases.jsonl` 是 regression dataset，可程式化跑同一組 query 驗證 retrieval 行為。

### Production setup（指向自己的 vault）

```ini
# .env
VAULT_DIR=C:\path\to\your\obsidian-vault
AGENT_BACKEND=claude   # 或 codex
```

Windows 上可直接跑 [start.bat](start.bat)，會同時啟動 ngrok 與 uvicorn。

## 雙 backend 比較

| | Claude (`ask_claude.py`) | Codex (`ask_codex.py`) |
|---|---|---|
| 呼叫 | Claude Code CLI via stdin | Codex CLI `exec` |
| Vault Read/Grep | 內建 | 需 `--sandbox workspace-write` 給 vault 寫權限（其實只用到讀） |
| Timeout | 預設 — | `CODEX_TIMEOUT_SECONDS`（預設 180s） |
| 適合 | 長 context、長推理 | 短查詢、結構化任務 |

用 `AGENT_BACKEND=claude` 或 `AGENT_BACKEND=codex` 切換。

## License

[MIT](LICENSE)

---

## English summary

`obsidian-line-bot` is a personal LINE bot that queries an Obsidian vault. It deliberately avoids vector RAG; instead it uses author-maintained vault structure (`index.md`, second-level table-of-contents per file, naming conventions) plus a deterministic pre-check stage. Only when pre-check misses does it fall back to an LLM agent (Claude or Codex, switchable via `AGENT_BACKEND`) bounded by the rules in `prompt_rules.py`. The repo ships a dummy vault and regression dataset so the retrieval pipeline can be exercised without a real vault.
