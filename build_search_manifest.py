import argparse
import json
import os
import re
from pathlib import Path


INCLUDE_DIRS = ("00_Inbox", "01_Assets", "02_Projects", "03_Daily", "04_Knowledge")
EXCLUDED_PARTS = {".obsidian", ".claude", "05_Templates", "06_System"}
EXCLUDED_PREFIXES = (("01_Assets", "原始檔"),)


def build_manifest(vault_dir: str) -> list[dict]:
    vault = Path(vault_dir)
    records = []
    for top_dir in INCLUDE_DIRS:
        root = vault / top_dir
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            rel_parts = path.relative_to(vault).parts
            if should_skip(rel_parts):
                continue
            records.append(build_record(vault, path))
    records.sort(key=lambda item: item["path"])
    return records


def should_skip(rel_parts: tuple[str, ...]) -> bool:
    if any(part in EXCLUDED_PARTS for part in rel_parts):
        return True
    return any(rel_parts[: len(prefix)] == prefix for prefix in EXCLUDED_PREFIXES)


def build_record(vault: Path, path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter = parse_frontmatter(text)
    rel_path = path.relative_to(vault).as_posix()
    tags = parse_tags(frontmatter.get("tags", ""))
    title = frontmatter.get("title", "").strip()
    filename = path.name
    subject = re.sub(r"^\d{8}-", "", path.stem).strip()
    aliases = unique_aliases([title, subject, *tags])
    return {
        "path": rel_path,
        "title": title,
        "date": frontmatter.get("date", "").strip(),
        "tags": tags,
        "project": frontmatter.get("project", "").strip(),
        "folder": path.parent.relative_to(vault).as_posix(),
        "filename": filename,
        "headings": extract_headings(text),
        "aliases": aliases,
        "kind": "knowledge_page" if rel_path.startswith("04_Knowledge/") else "source_note",
        "life": any(tag.lower() == "life" for tag in tags),
    }


def parse_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, flags=re.S)
    if not match:
        return {}
    data = {}
    for line in match.group(1).splitlines():
        item = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if item:
            data[item.group(1)] = item.group(2).strip().strip("'\"")
    return data


def parse_tags(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    tags = [part.strip().strip("'\"") for part in re.split(r"[,，]", raw)]
    return [tag for tag in tags if tag]


def extract_headings(text: str) -> list[str]:
    headings = []
    for line in text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            headings.append(match.group(1).strip())
    return headings


def unique_aliases(values: list[str]) -> list[str]:
    aliases = []
    seen = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", value or "").strip()
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        aliases.append(normalized)
    return aliases


def write_manifest(vault_dir: str) -> str:
    vault = Path(vault_dir)
    output = vault / "06_System" / "Search" / "vault_search_manifest.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        for record in build_manifest(vault_dir)
    ]
    content = "\n".join(lines) + ("\n" if lines else "")
    old_content = output.read_text(encoding="utf-8") if output.exists() else None
    if content != old_content:
        output.write_text(content, encoding="utf-8")
    return str(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build my-vault vault search manifest.")
    parser.add_argument("--vault", required=True, help="Vault root path")
    args = parser.parse_args()
    output = write_manifest(args.vault)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
