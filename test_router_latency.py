import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

VAULT_DIR = Path(os.environ.get("VAULT_DIR", r"G:\MyDrive\my-vault"))
APP_DIR = Path(__file__).resolve().parent
DEFAULT_TIMEOUT = 15

ROUTER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["query", "report", "discussion", "unknown"],
        },
        "project_alias": {"type": "string"},
        "project_id": {"type": "string"},
        "topic": {"type": "string"},
        "need_latest": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": [
        "intent",
        "project_alias",
        "project_id",
        "topic",
        "need_latest",
        "confidence",
        "reason",
    ],
}

DEFAULT_QUERIES = [
    "查詢SampleProjectB異常",
    "查詢SampleProjectA關單MSG",
    "查詢YouTrack用法",
    "查詢SampleProjectD目前問題",
]


def build_cmd(use_bare: bool) -> list[str]:
    claude_js = os.environ.get("CLAUDE_JS_PATH", "")
    if claude_js:
        node = os.environ.get("NODE_PATH", "node")
        base = [node, claude_js]
    else:
        base = ["claude"]

    cmd = base + [
        "-p",
        "--tools",
        "",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--output-format",
        "text",
    ]
    if use_bare:
        cmd.insert(len(base) + 1, "--bare")
    return cmd


def list_project_ids(limit: int = 80) -> list[str]:
    project_root = VAULT_DIR / "02_Projects"
    if not project_root.is_dir():
        return []
    names = sorted(p.name for p in project_root.iterdir() if p.is_dir())
    return names[:limit]


def build_router_prompt(query: str, project_ids: list[str]) -> str:
    projects = "\n".join(f"- {name}" for name in project_ids)
    return f"""你是 LINE Bot 查詢 router，只能做分類，不准查 vault、不准使用工具、不准讀檔。

請根據使用者輸入，從有效 project 清單中選最可能的 project_id。
如果找不到對應專案，project_id 與 project_alias 請回空字串。
topic 可以自由填寫短詞，例如：異常、MSG、SQL、用法、流程、SOP、人事、生活、規劃。
need_latest 只有在問題包含最新、目前、最近、今天、本週、還沒解決、現在、上次、剛剛等時間或狀態詞時才回 true。
只回 raw JSON，不要 markdown code fence，不要加解釋文字。
JSON 欄位必須完全符合：
{json.dumps(ROUTER_SCHEMA, ensure_ascii=False)}

有效 project 清單：
{projects}

使用者輸入：
{query}
"""


def run_router(query: str, timeout: int, use_bare: bool) -> dict:
    project_ids = list_project_ids()
    prompt = build_router_prompt(query, project_ids)
    start = time.perf_counter()
    try:
        result = subprocess.run(
            build_cmd(use_bare),
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=APP_DIR,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - start
    except subprocess.TimeoutExpired:
        return {
            "query": query,
            "ok": False,
            "elapsed": timeout,
            "error": "timeout",
            "router": None,
        }

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    try:
        parsed = json.loads(stdout)
        parse_error = ""
    except json.JSONDecodeError as exc:
        parsed = None
        parse_error = str(exc)

    return {
        "query": query,
        "ok": result.returncode == 0 and parsed is not None,
        "elapsed": round(elapsed, 2),
        "returncode": result.returncode,
        "router": parsed,
        "parse_error": parse_error,
        "stdout": stdout[:500],
        "stderr": stderr[:500],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure Claude router latency without vault tool use.")
    parser.add_argument("queries", nargs="*", help="Queries to route. Defaults to a small regression set.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--bare",
        action="store_true",
        help="Use Claude Code bare mode. Requires ANTHROPIC_API_KEY/API key auth and may not work with subscription login.",
    )
    args = parser.parse_args()

    queries = args.queries or DEFAULT_QUERIES
    results = [run_router(query, args.timeout, args.bare) for query in queries]

    for item in results:
        print(json.dumps(item, ensure_ascii=False))

    ok_results = [item for item in results if item["ok"]]
    if ok_results:
        avg = sum(item["elapsed"] for item in ok_results) / len(ok_results)
        worst = max(item["elapsed"] for item in ok_results)
        print(f"summary ok={len(ok_results)}/{len(results)} avg={avg:.2f}s worst={worst:.2f}s")
    else:
        print(f"summary ok=0/{len(results)}")

    return 0 if len(ok_results) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
