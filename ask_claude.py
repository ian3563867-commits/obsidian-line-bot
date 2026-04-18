import subprocess
import os
from dotenv import load_dotenv

load_dotenv()

VAULT_DIR = os.environ.get("VAULT_DIR", r"G:\MyDrive\my-vault")


def _build_cmd() -> list:
    claude_js = os.environ.get("CLAUDE_JS_PATH", "")
    if claude_js:
        node = os.environ.get("NODE_PATH", "node")
        return [node, claude_js, "-p", "--output-format", "text"]
    return ["claude", "-p", "--output-format", "text"]


_LIFE_KEYWORDS = ("生活", "life", "個人", "personal")


def _is_life(prompt: str) -> bool:
    lower = prompt.lower()
    return any(k in lower for k in _LIFE_KEYWORDS)


def ask_claude(prompt: str) -> str:
    try:
        life = _is_life(prompt)

        if life:
            query_steps = (
                "【查詢 vault 必須執行的步驟 - 不可省略】\n"
                "Step 1: Read 04_Knowledge/index.md 全文，找出與問題相關的知識頁面\n"
                "Step 2: Grep 搜尋 02_Projects/ 和 00_Inbox/ 資料夾，列出所有命中檔案\n"
                "Step 3: Read 所有命中的檔案\n"
                "Step 4: 整合所有資料後回答，結尾列出所有參考來源檔案路徑\n"
                "（即使一個檔案已經有完整答案，也必須執行 Step 2 和 Step 3 找相關檔案）\n\n"
            )
        else:
            query_steps = (
                "【查詢 vault 必須執行的步驟 - 不可省略】\n"
                "Step 1: Read 04_Knowledge/index.md 全文，找出與問題相關的知識頁面\n"
                "Step 2: Grep 搜尋 02_Projects/ 資料夾，列出所有命中檔案（關鍵字從問題中抽取）\n"
                "Step 3: Read 所有命中的檔案（包含 04_Knowledge 知識頁、02_Projects 原始紀錄）\n"
                "        跳過 frontmatter tags 含 life 的檔案\n"
                "Step 4: 整合所有資料後回答，結尾列出所有參考來源檔案路徑\n"
                "（即使一個檔案已經有完整答案，也必須執行 Step 2 和 Step 3 找相關檔案）\n\n"
            )

        write_rule = (
            "【寫入規則】若需要寫入 vault，一律存到 00_Inbox/ 資料夾。\n"
            "若使用者訊息含有「生活/Life/life/個人/personal」，"
            "frontmatter tags 必須包含 life。\n\n"
        )

        format_rule = (
            "【回覆格式】純文字，不要用 Markdown（不用 **粗體**、不用表格、不用 # 標題）。"
            "用「─」分隔區塊、用「•」列點、欄位用「：」對齊。"
            "因為訊息會顯示在 LINE，Markdown 不會渲染。\n\n"
        )

        full_prompt = query_steps + write_rule + format_rule + "【使用者問題】\n" + prompt
        result = subprocess.run(
            _build_cmd(),
            input=full_prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=VAULT_DIR,
            timeout=120,
        )
        output = (result.stdout or "").strip()
        if not output:
            err = (result.stderr or "").strip()[:500]
            return f"（無輸出，return code={result.returncode}）\n{err}"
        return output
    except subprocess.TimeoutExpired:
        return "逾時，請縮短問題後再試。"
    except FileNotFoundError:
        return "找不到 claude 指令，請確認 Claude Code 已安裝並在 PATH 中。"
    except Exception as e:
        return f"錯誤：{e}"
