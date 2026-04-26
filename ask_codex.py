import os
import shlex
import subprocess
from dotenv import load_dotenv

from prompt_rules import build_vault_prompt

load_dotenv()

VAULT_DIR = os.environ.get("VAULT_DIR", r"G:\MyDrive\my-vault")


def _split_args(value: str) -> list[str]:
    if not value.strip():
        return []
    return shlex.split(value, posix=(os.name != "nt"))


def _build_cmd() -> list[str]:
    codex_exe = os.environ.get("CODEX_EXE", "codex")
    codex_args = os.environ.get("CODEX_ARGS", "exec --sandbox workspace-write -")
    return [codex_exe, *_split_args(codex_args)]


def ask_codex(prompt: str) -> str:
    try:
        full_prompt = build_vault_prompt(prompt)
        timeout = int(os.environ.get("CODEX_TIMEOUT_SECONDS", "180"))
        result = subprocess.run(
            _build_cmd(),
            input=full_prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=VAULT_DIR,
            timeout=timeout,
        )
        output = (result.stdout or "").strip()
        if output:
            return output

        err = (result.stderr or "").strip()[:500]
        return f"（Codex 無輸出，return code={result.returncode}）\n{err}"
    except subprocess.TimeoutExpired:
        return "Codex 逾時，請縮短問題後再試。"
    except FileNotFoundError:
        return "找不到 codex 指令，請確認 Codex CLI 已安裝並在 PATH 中。"
    except PermissionError as e:
        return f"Codex 無法執行：{e}。請確認 CODEX_EXE 指向可執行的 Codex CLI，而不是被 WindowsApps 擋住的捷徑。"
    except Exception as e:
        return f"Codex 錯誤：{e}"
