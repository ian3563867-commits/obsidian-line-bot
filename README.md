# obsidian-line-bot

A personal **chat-messaging bot** that turns your [Obsidian](https://obsidian.md) note vault into a queryable assistant — built around **manifest / index-driven retrieval, deterministic pre-check, and bounded LLM search**, with a **dual Claude / Codex backend**.

The chat front-end uses [LINE](https://line.me) (a messaging app, comparable to WhatsApp / Telegram / Messenger, dominant in Taiwan / Japan / Thailand). The same architecture works behind any webhook-based chat platform; only `main.py`'s webhook layer is LINE-specific.

> 中文摘要見 [Chinese summary](#chinese-summary)。

## Why not vector RAG?

This project deliberately avoids vector databases and embedding similarity search. The core assumption: a personal note vault already carries structure that the *author* has hand-curated — folders, frontmatter, index files, naming conventions — and that structure is more reliable, more explainable, and cheaper to query than embeddings.

How it actually works:

1. **Deterministic index pre-check.** Read `index.md` and the second-level table of contents at the top of each large file. If the query maps to a known entry, answer directly — no LLM call.
2. **Manifest-driven retrieval.** Feed the vault's structure + naming rules into the prompt; let the LLM use `Read` / `Grep` to pick files, instead of guessing via similarity.
3. **Bounded LLM search.** Only when the pre-check misses, fall back to an LLM agent (Claude or Codex). All search scope, output format, and citation rules are centralized in [prompt_rules.py](prompt_rules.py).
4. **Dual backend router.** The same query can run through Claude (`ask_claude.py`) or Codex (`ask_codex.py`), switchable via the `AGENT_BACKEND` env var — useful for A/B comparing two agents on the same non-trivial retrieval task.

## Architecture

```
chat webhook ──► main.py ──┬──► retrieval.py ── deterministic pre-check (index + ToC)
                           │                       │
                           │                       └──► hit?  ──► reply (no LLM)
                           │
                           └──► ask_claude.py / ask_codex.py ── prompt_rules.py
                                                                 │
                                                                 └──► Claude / Codex CLI
                                                                       └──► reply
```

| File | Role |
|---|---|
| [main.py](main.py) | Chat webhook entry, query routing, response formatting, signed `/open-note` mobile reader page, todo dashboard |
| [retrieval.py](retrieval.py) | Manifest / index-driven deterministic pre-check and candidate-note expansion |
| [ask_claude.py](ask_claude.py) | Calls the Claude Code CLI (subprocess + stdin prompt) |
| [ask_codex.py](ask_codex.py) | Calls the Codex CLI with the same interface; swap via `AGENT_BACKEND` |
| [prompt_rules.py](prompt_rules.py) | Centralized vault-prompt rules: search strategy, output format, life-vs-work routing, tag conventions |
| [build_search_manifest.py](build_search_manifest.py) | Generates the retrieval manifest from a vault snapshot |
| [frontend/](frontend/) | React + Vite mobile reader page for `/open-note`: Markdown rendering, TOC, code blocks |
| [samples/dummy-vault/](samples/dummy-vault/) | A public, redacted sample vault (4 notes) so the pipeline can be exercised without a real vault |
| [tests/golden/sample_retrieval_cases.jsonl](tests/golden/sample_retrieval_cases.jsonl) | Regression dataset against the sample vault |

## AI in our workflow

This project is itself an AI-assisted development case study — relevant context for the [OpenAI Codex for OSS](https://openai.com/form/codex-for-oss/) program:

- **`codex/*` branches** contain features authored by Codex: `codex/feature-codex-backend` (added the Codex backend), `codex/9006-google-rag` (Google RAG MVP investigation), `codex/fix-adps-precheck`, `codex/line-result-pages`.
- **Dual backend router**: [ask_claude.py](ask_claude.py) and [ask_codex.py](ask_codex.py) share the same interface, so the same prompt-engineering work in [prompt_rules.py](prompt_rules.py) can run through either agent. This gives a built-in A/B harness for evaluating Codex vs. Claude on the project's actual retrieval task.
- **Centralized prompts**: every rule we hand to the agent lives in [prompt_rules.py](prompt_rules.py) — one edit, both backends pick it up.
- **Golden regression**: [tests/golden/sample_retrieval_cases.jsonl](tests/golden/sample_retrieval_cases.jsonl) guards against prompt drift; we run it after each router-touching change.

## Setup

```bash
git clone https://github.com/ian3563867-commits/obsidian-line-bot.git
cd obsidian-line-bot
pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum set LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN / LINE_ALLOWED_USER_ID
# VAULT_DIR defaults to samples/dummy-vault — leave it for the demo.
```

### Run the dummy-vault demo

```bash
uvicorn main:app --reload --port 8000
```

Then send queries via your chat platform of choice (LINE in our setup; the webhook is small enough to port). Expected hits:

| Query | Hits |
|---|---|
| `index` | `samples/dummy-vault/index.md` |
| `SampleProjectA progress` | `samples/dummy-vault/02_Projects/SampleProjectA/issue-tracking.md` |
| `today's daily` | `samples/dummy-vault/03_Daily/20260101-daily-report.md` |
| `WMS query concepts` | `samples/dummy-vault/04_Knowledge/wms-query-concepts.md` |

`tests/golden/sample_retrieval_cases.jsonl` runs the same set programmatically to verify retrieval behavior.

### Production setup (point at your own vault)

```ini
# .env
VAULT_DIR=C:\path\to\your\obsidian-vault
AGENT_BACKEND=claude   # or `codex`
```

On Windows, [start.bat](start.bat) launches both ngrok and uvicorn.

## Dual backend comparison

| | Claude (`ask_claude.py`) | Codex (`ask_codex.py`) |
|---|---|---|
| Invocation | Claude Code CLI via stdin | Codex CLI `exec` |
| Vault `Read`/`Grep` | Built-in | Needs `--sandbox workspace-write` |
| Timeout | (none by default) | `CODEX_TIMEOUT_SECONDS` (default 180s) |
| Sweet spot | Long context, deep reasoning | Short queries, structured tasks |

Switch with `AGENT_BACKEND=claude` or `AGENT_BACKEND=codex`.

## A note on LINE

LINE is the dominant messaging platform in several East Asian markets; if you're not in one of them, think Telegram / WhatsApp / Messenger with a richer "rich-menu" UI primitive. The webhook contract is standard: HTTP POST in, JSON reply out, signed with a channel secret. Porting `main.py`'s handler to Telegram Bot API or Slack Events API is a few hours of work — the retrieval pipeline below it is platform-agnostic.

## License

[MIT](LICENSE)

---

## Chinese summary

`obsidian-line-bot` 是一個個人用通訊軟體（LINE）機器人，把 Obsidian vault 變成可用聊天介面查詢的助手。核心設計刻意避開 vector RAG，改用 **manifest / index 驅動的決定性 pre-check + 有界的 LLM fallback search**，並支援 Claude 與 Codex 雙 backend 互換。Repo 內附 dummy vault 與 regression dataset，可在沒有真實 vault 的情況下跑通整個 pipeline。
