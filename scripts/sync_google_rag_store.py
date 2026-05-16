import argparse
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

DEFAULT_ROOTS = ("02_Projects", "04_Knowledge")
DEFAULT_STORE_DISPLAY_NAME = "my-vault-9006-test"
DEFAULT_EMBEDDING_MODEL = "models/gemini-embedding-2"


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def iter_markdown_files(vault_dir: Path, roots: list[str]):
    for root in roots:
        base = vault_dir / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.md"):
            if any(part.startswith(".") for part in path.relative_to(vault_dir).parts):
                continue
            yield path


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    result = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            result[key.strip()] = value
    return result


def build_upload_text(path: Path, vault_dir: Path) -> tuple[str, dict[str, str]]:
    relative_path = path.relative_to(vault_dir).as_posix()
    raw = path.read_text(encoding="utf-8", errors="replace")
    frontmatter = parse_frontmatter(raw)
    root = relative_path.split("/", 1)[0]
    title = frontmatter.get("title") or path.stem
    project = frontmatter.get("project") or infer_project(relative_path)
    date = frontmatter.get("date") or infer_date(path.name)

    header = [
        f"source_path: {relative_path}",
        f"root: {root}",
        f"project: {project}",
        f"title: {title}",
        f"date: {date}",
        "",
    ]
    metadata = {
        "source_path": relative_path,
        "root": root,
        "project": project,
        "title": title,
        "date": date,
    }
    return "\n".join(header) + raw, metadata


def infer_project(relative_path: str) -> str:
    parts = relative_path.split("/")
    if len(parts) >= 2 and parts[0] in {"02_Projects", "04_Knowledge"}:
        return parts[1]
    return ""


def infer_date(filename: str) -> str:
    match = re.match(r"(\d{4})(\d{2})(\d{2})-", filename)
    if not match:
        return ""
    return "-".join(match.groups())


def metadata_items(metadata: dict[str, str]) -> list[dict[str, str]]:
    items = []
    for key, value in metadata.items():
        if value:
            items.append({"key": key, "string_value": str(value)[:500]})
    return items


def wait_operations(client, pending: list[tuple[object, str]], poll_seconds: int, completed_count: int) -> int:
    remaining = pending
    while remaining:
        time.sleep(poll_seconds)
        next_remaining = []
        for operation, relative_path in remaining:
            operation = client.operations.get(operation)
            if getattr(operation, "done", False):
                completed_count += 1
                print(f"[{completed_count}] uploaded {relative_path}", flush=True)
            else:
                next_remaining.append((operation, relative_path))
        remaining = next_remaining
    return completed_count


def find_store_by_display_name(client, display_name: str):
    for store in client.file_search_stores.list():
        if getattr(store, "display_name", "") == display_name:
            return store
    return None


def get_or_create_store(client, display_name: str, embedding_model: str, recreate: bool):
    store = find_store_by_display_name(client, display_name)
    if store and recreate:
        client.file_search_stores.delete(name=store.name, config={"force": True})
        store = None
    if store:
        return store, False
    store = client.file_search_stores.create(
        config={
            "display_name": display_name,
            "embedding_model": embedding_model,
        }
    )
    return store, True


def sync_store(args) -> int:
    vault_dir = Path(args.vault_dir).expanduser().resolve()
    if not vault_dir.is_dir():
        raise SystemExit(f"Vault folder not found: {vault_dir}")

    all_files = list(iter_markdown_files(vault_dir, args.roots))
    if args.limit:
        all_files = all_files[: args.limit]
    if args.start_index < 1:
        raise SystemExit("--start-index must be >= 1")
    files = all_files[args.start_index - 1 :]

    print(f"files={len(all_files)}")
    if args.start_index > 1:
        print(f"start_index={args.start_index}")
        print(f"selected_files={len(files)}")
    if args.dry_run:
        for path in files[:20]:
            print(path.relative_to(vault_dir).as_posix())
        return 0

    try:
        from google import genai
    except ImportError as exc:
        raise SystemExit("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    client = genai.Client()
    store, created = get_or_create_store(
        client,
        args.store_display_name,
        args.embedding_model,
        args.recreate,
    )

    print(f"store_name={store.name}")
    print(f"store_display_name={getattr(store, 'display_name', args.store_display_name)}")
    print(f"created={created}")

    with tempfile.TemporaryDirectory(prefix="google-rag-upload-") as tmp:
        tmp_dir = Path(tmp)
        pending = []
        completed_count = args.start_index - 1
        total_files = len(all_files)
        for index, path in enumerate(files, start=args.start_index):
            text, metadata = build_upload_text(path, vault_dir)
            relative_path = path.relative_to(vault_dir).as_posix()
            upload_path = tmp_dir / safe_upload_name(relative_path)
            upload_path.write_text(text, encoding="utf-8")

            sample_file = client.files.upload(
                file=str(upload_path),
                config={"display_name": relative_path},
            )
            operation = client.file_search_stores.import_file(
                file_search_store_name=store.name,
                file_name=sample_file.name,
                config={"custom_metadata": metadata_items(metadata)},
            )
            pending.append((operation, relative_path))
            print(f"[{index}/{total_files}] queued {relative_path}", flush=True)
            if len(pending) >= args.batch_size:
                completed_count = wait_operations(client, pending, args.poll_seconds, completed_count)
                pending = []
        if pending:
            completed_count = wait_operations(client, pending, args.poll_seconds, completed_count)

    print("done")
    print(f"Set GOOGLE_RAG_STORE_NAME={store.name}")
    return 0


def safe_upload_name(relative_path: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", relative_path)[-180:] or "note.md"


def safe_display_name(relative_path: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", relative_path)[-120:] or "note"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Obsidian vault markdown files to Gemini File Search.")
    parser.add_argument("--vault-dir", default=os.environ.get("VAULT_DIR", r"G:\MyDrive\my-vault"))
    parser.add_argument("--store-display-name", default=os.environ.get("GOOGLE_RAG_STORE_DISPLAY_NAME", DEFAULT_STORE_DISPLAY_NAME))
    parser.add_argument("--embedding-model", default=os.environ.get("GOOGLE_RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
    parser.add_argument("--roots", nargs="+", default=list(DEFAULT_ROOTS))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--start-index", type=int, default=1, help="Resume from this 1-based file index after a partial run.")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--recreate", action="store_true", help="Delete and recreate an existing store with the same display name.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return sync_store(args)


if __name__ == "__main__":
    configure_utf8_stdio()
    raise SystemExit(main())
