import subprocess
import os

VAULT_DIR = r"G:\MyDrive\my-vault"


def ask_claude(prompt: str) -> str:
    try:
        import tempfile, pathlib
        tmp = pathlib.Path(tempfile.mktemp(suffix=".txt"))
        prompt_file = pathlib.Path(tempfile.mktemp(suffix="_prompt.txt"))
        full_prompt = (
            "【查詢 vault 必須執行的步驟 - 不可省略】\n"
            "Step 1: Read 04_Knowledge/index.md 全文，找出與問題相關的知識頁面\n"
            "Step 2: Grep 搜尋 02_Projects/ 資料夾，列出所有命中檔案（關鍵字從問題中抽取）\n"
            "Step 3: Read 所有命中的檔案（包含 04_Knowledge 知識頁、02_Projects 原始紀錄）\n"
            "Step 4: 整合所有資料後回答，結尾列出所有參考來源檔案路徑\n"
            "（即使一個檔案已經有完整答案，也必須執行 Step 2 和 Step 3 找相關檔案）\n\n"
            "【寫入規則】若需要寫入 vault，一律存到 00_Inbox/ 資料夾。\n\n"
            "【回覆格式】純文字，不要用 Markdown（不用 **粗體**、不用表格、不用 # 標題）。"
            "用「─」分隔區塊、用「•」列點、欄位用「：」對齊。"
            "因為訊息會顯示在 LINE，Markdown 不會渲染。\n\n"
            "【使用者問題】\n" + prompt
        )
        prompt_file.write_text(full_prompt, encoding="utf-8")
        cmd = f'claude -p --output-format text < "{prompt_file}" > "{tmp}"'
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=VAULT_DIR,
            timeout=120,
        )
        prompt_file.unlink(missing_ok=True)
        output = tmp.read_text(encoding="utf-8", errors="ignore").strip() if tmp.exists() else ""
        tmp.unlink(missing_ok=True)
        return output or f"（無輸出，return code={result.returncode}）"
    except subprocess.TimeoutExpired:
        return "逾時，請縮短問題後再試。"
    except FileNotFoundError:
        return "找不到 claude 指令，請確認 Claude Code 已安裝並在 PATH 中。"
    except Exception as e:
        return f"錯誤：{e}"
