import subprocess
import os
from dotenv import load_dotenv

from prompt_rules import build_vault_prompt

load_dotenv()

VAULT_DIR = os.environ.get("VAULT_DIR", r"G:\MyDrive\my-vault")


def _build_cmd() -> list:
    claude_js = os.environ.get("CLAUDE_JS_PATH", "")
    if claude_js:
        node = os.environ.get("NODE_PATH", "node")
        return [node, claude_js, "-p", "--output-format", "text"]
    return ["claude", "-p", "--output-format", "text"]


def ask_claude(prompt: str, allow_write: bool = False) -> str:
    try:
        full_prompt = build_vault_prompt(prompt, allow_write=allow_write)
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
