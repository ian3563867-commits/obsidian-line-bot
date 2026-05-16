import os
import re
from dataclasses import dataclass


TECHNICAL_QUERY_TERMS = (
    "msg",
    "message",
    "message test",
    "json",
    "api",
    "sql",
    "sop",
    "模板",
    "範例",
    "關單",
    "poclose",
    "woclose",
    "stocktakingclose",
    "調撥",
    "轉撥",
    "移轉",
    "可上架",
    "不能上架",
    "取消匹配",
    "取消揀貨",
    "取消組盤",
)

TIME_SENSITIVE_TERMS = (
    "最新",
    "最近",
    "今天",
    "本週",
    "目前",
    "目前狀況",
    "還沒解決",
    "現在",
    "上次",
    "剛剛",
)

ASSET_QUERY_TERMS = ("資產", "家庭資產", "我的資產")

IDENTIFIER_PATTERNS = (
    re.compile(r"\bpcn[-_\w]*\d", re.IGNORECASE),
    re.compile(r"\bwo[-_\w]*\d", re.IGNORECASE),
    re.compile(r"\bpo[-_\w]*\d", re.IGNORECASE),
    re.compile(r"\bdn[-_\w]*\d", re.IGNORECASE),
    re.compile(r"\b\d{6,}\b"),
)


@dataclass
class IndexEntry:
    page: str
    title: str
    summary: str
    tags: str
    high_value: bool


@dataclass
class TocEntry:
    category: str
    section: str
    purpose: str
    terms: str


@dataclass
class SourceBlock:
    file: str
    start_line: int
    end_line: int
    content: str
    match_reason: str


@dataclass
class PrecheckResult:
    mode: str
    confidence: str
    matched_entries: list[dict[str, str]]
    source_blocks: list[SourceBlock]
    fallback_reason: str

    @property
    def hit(self) -> bool:
        return self.mode == "preloaded_context" and bool(self.source_blocks)


def run_precheck(query: str, vault_dir: str, max_blocks: int = 3) -> PrecheckResult:
    query = query.strip()
    if not query:
        return _fallback("empty query")
    all_entries = read_index_entries(vault_dir)
    deep_search_required = _should_deep_search(query)
    asset_query = _is_asset_query(query)
    strong_precheck_allowed = not deep_search_required and (_is_tool_query(query) or asset_query)
    if not strong_precheck_allowed:
        hints = _index_hint_entries(query, all_entries, max_blocks)
        if hints:
            return PrecheckResult(
                mode="index_hints",
                confidence="low",
                matched_entries=[_entry_payload(entry) for _, entry in hints],
                source_blocks=[],
                fallback_reason="index hints only; deep search still required",
            )
        reason = "time-sensitive, open-item, or identifier query" if deep_search_required else "no index hints"
        return _fallback(reason)

    entries = _asset_entries(all_entries) if asset_query else [entry for entry in all_entries if entry.high_value]
    if not entries:
        hints = _index_hint_entries(query, all_entries, max_blocks)
        if hints:
            return PrecheckResult(
                mode="index_hints",
                confidence="low",
                matched_entries=[_entry_payload(entry) for _, entry in hints],
                source_blocks=[],
                fallback_reason="no strong deterministic entries; index hints only",
            )
        return _fallback("no deterministic index entries")

    candidates = []
    for entry in entries:
        entry_score = _score_text(query, " ".join([entry.page, entry.title, entry.summary, entry.tags]))
        if entry_score <= 0:
            continue
        path = resolve_wikilink(vault_dir, entry.page)
        if not path:
            continue
        toc = read_top_toc(path) if entry.high_value else []
        toc_matches = []
        for toc_entry in toc:
            toc_score = _score_text(
                query,
                " ".join([toc_entry.category, toc_entry.section, toc_entry.purpose, toc_entry.terms]),
            )
            if toc_score > 0:
                toc_matches.append((toc_score, toc_entry))
        if toc_matches:
            toc_matches.sort(key=lambda item: (-item[0], item[1].section))
            best_toc_score, best_toc = toc_matches[0]
            candidates.append((entry_score + best_toc_score, entry, path, best_toc))
        else:
            candidates.append((entry_score, entry, path, None))

    if not candidates:
        hints = _index_hint_entries(query, all_entries, max_blocks)
        if hints:
            return PrecheckResult(
                mode="index_hints",
                confidence="low",
                matched_entries=[_entry_payload(entry) for _, entry in hints],
                source_blocks=[],
                fallback_reason="no strong deterministic match; index hints only",
            )
        return _fallback("no deterministic index match")

    project_candidates = [item for item in candidates if _entry_matches_query_project(query, item[1])]
    project_named = bool(project_candidates)
    if project_named:
        candidates = project_candidates
    elif len(candidates) > 1:
        return PrecheckResult(
            mode="candidate_list",
            confidence="ambiguous",
            matched_entries=[_entry_payload(item[1]) for item in candidates[:max_blocks]],
            source_blocks=[],
            fallback_reason="multiple high-value candidates and no explicit project",
        )

    candidates.sort(key=lambda item: (-item[0], item[1].title))
    top_score = candidates[0][0]
    top_candidates = [item for item in candidates if item[0] == top_score]
    if len(top_candidates) > 1 and not project_named:
        return PrecheckResult(
            mode="candidate_list",
            confidence="ambiguous",
            matched_entries=[_entry_payload(item[1]) for item in top_candidates[:max_blocks]],
            source_blocks=[],
            fallback_reason="multiple equally strong high-value candidates",
        )

    blocks: list[SourceBlock] = []
    matched_entries: list[dict[str, str]] = []
    for _, entry, path, toc_entry in candidates[:max_blocks]:
        block = read_source_block(path, toc_entry)
        if block:
            blocks.append(block)
            matched_entries.append(_entry_payload(entry))
        if blocks and project_named:
            break

    if not blocks:
        return _fallback("matched entry but failed to read source block")

    return PrecheckResult(
        mode="preloaded_context",
        confidence="high",
        matched_entries=matched_entries,
        source_blocks=blocks,
        fallback_reason="",
    )


def build_preloaded_prompt(original_prompt: str, result: PrecheckResult) -> str:
    parts = [
        "本次查詢已由 Python deterministic index pre-check 先完成主目錄 / 子目錄定位。",
        "請優先根據下方已讀出的來源內容回答；不要重新搜尋 vault，除非你判斷來源明顯不足。",
        "回答需使用繁體中文，保持 LINE 可讀性，並在結尾列出來源檔案路徑與行號。",
        "",
        f"使用者原始問題：{original_prompt}",
        "",
        "Python pre-check 命中：",
    ]
    for entry in result.matched_entries:
        parts.append(f"- {entry['page']}：{entry['summary']}")
    parts.append("")
    parts.append("已讀來源內容：")
    for block in result.source_blocks:
        parts.append("")
        parts.append(f"--- {block.file}:{block.start_line}-{block.end_line} ---")
        parts.append(f"命中原因：{block.match_reason}")
        parts.append(block.content)
    return "\n".join(parts)


def build_index_hint_prompt(original_prompt: str, result: PrecheckResult) -> str:
    parts = [
        "Python 已先讀取 04_Knowledge/index.md，找到以下可能相關的 index hints。",
        "這些只是候選入口，不是最終答案；請仍依照原本保守三層搜尋規則判斷是否需要 Read / Grep。",
        "若 hints 與問題不符，請忽略 hints 並照原流程查詢。回答結尾仍需列出實際參考來源。",
        "",
        "Index hints：",
    ]
    for entry in result.matched_entries:
        parts.append(f"- {entry['page']}：{entry['summary']}（tags: {entry['tags']}）")
    parts.extend(["", "使用者原始問題：", original_prompt])
    return "\n".join(parts)


def format_candidate_summary(result: PrecheckResult) -> str:
    lines = ["Python pre-check 找到多個可能入口，未硬猜唯一答案："]
    for entry in result.matched_entries:
        lines.append(f"- {entry['page']}：{entry['summary']}")
    return "\n".join(lines)


def read_index_entries(vault_dir: str) -> list[IndexEntry]:
    index_path = os.path.join(vault_dir, "04_Knowledge", "index.md")
    if not os.path.isfile(index_path):
        return []

    entries: list[IndexEntry] = []
    high_value = False
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("## 高價值操作原始檔"):
                high_value = True
                continue
            if stripped.startswith("## ") and "高價值操作原始檔" not in stripped:
                high_value = False

            match = re.match(r"\|\s*\[\[([^\]]+)\]\]\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|", stripped)
            if not match:
                continue
            page, summary, tags = [strip_markdown_inline(part.strip()) for part in match.groups()]
            if page.startswith("-") or page == "頁面" or page == "原始檔案":
                continue
            title = os.path.basename(page.replace("\\", "/"))
            entries.append(IndexEntry(page=page, title=title, summary=summary, tags=tags, high_value=high_value))
    return entries


def resolve_wikilink(vault_dir: str, page: str) -> str | None:
    normalized = page.replace("/", os.sep).replace("\\", os.sep)
    candidates = []
    if normalized.lower().endswith(".md"):
        candidates.append(os.path.join(vault_dir, normalized))
    else:
        candidates.append(os.path.join(vault_dir, normalized + ".md"))
        candidates.append(os.path.join(vault_dir, "04_Knowledge", normalized + ".md"))

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def read_top_toc(path: str) -> list[TocEntry]:
    try:
        lines = _read_lines(path)
    except OSError:
        return []

    in_catalog = False
    entries: list[TocEntry] = []
    for line in lines[:140]:
        stripped = line.strip()
        if stripped.startswith("## 完整目錄"):
            in_catalog = True
            continue
        if in_catalog and stripped.startswith("## ") and "完整目錄" not in stripped:
            break
        if not in_catalog or not stripped.startswith("|"):
            continue
        cells = [strip_markdown_inline(cell.strip()) for cell in _split_markdown_table_row(stripped)]
        if len(cells) < 4 or cells[0] in {"類別", "---"} or set(cells[0]) <= {"-"}:
            continue
        section = _extract_section_label(cells[1])
        if section:
            entries.append(TocEntry(category=cells[0], section=section, purpose=cells[2], terms=cells[3]))
    return entries


def read_source_block(path: str, toc_entry: TocEntry | None) -> SourceBlock | None:
    try:
        lines = _read_lines(path)
    except OSError:
        return None

    if toc_entry:
        heading_line = _find_heading_line(lines, toc_entry.section)
        if heading_line:
            start = heading_line
            end = _find_next_heading_line(lines, heading_line + 1) or len(lines)
            content_lines = lines[start - 1 : end]
            reason = f"子目錄命中：{toc_entry.category} / {toc_entry.section}"
            return SourceBlock(
                file=_display_path(path),
                start_line=start,
                end_line=end,
                content="\n".join(content_lines).strip(),
                match_reason=reason,
            )

    end = min(len(lines), 120)
    if end == 0:
        return None
    return SourceBlock(
        file=_display_path(path),
        start_line=1,
        end_line=end,
        content="\n".join(lines[:end]).strip(),
        match_reason="主目錄命中，未定位子目錄段落",
    )


def strip_markdown_inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    return text.replace("\\|", "|")


def _split_markdown_table_row(row: str) -> list[str]:
    text = row.strip().strip("|")
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in text:
        if escaped:
            current.append("\\" + char if char != "|" else char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current))
            current = []
            continue
        current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current))
    return cells


def _fallback(reason: str) -> PrecheckResult:
    return PrecheckResult(mode="fallback", confidence="none", matched_entries=[], source_blocks=[], fallback_reason=reason)


def _read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().splitlines()


def _display_path(path: str) -> str:
    return path.replace("\\", "/")


def _is_tool_query(query: str) -> bool:
    lower = query.lower()
    return any(term in lower for term in TECHNICAL_QUERY_TERMS)


def _is_asset_query(query: str) -> bool:
    return any(term in query for term in ASSET_QUERY_TERMS)


def _asset_entries(entries: list[IndexEntry]) -> list[IndexEntry]:
    result = []
    for entry in entries:
        text = " ".join([entry.page, entry.title, entry.summary, entry.tags])
        if "資產" in text:
            result.append(entry)
    return result


def _should_deep_search(query: str) -> bool:
    lower = query.lower()
    if any(term in lower for term in TIME_SENSITIVE_TERMS):
        return True
    if any(term in lower for term in ("open item", "open-item", "未解", "追蹤")):
        return True
    return any(pattern.search(query) for pattern in IDENTIFIER_PATTERNS)


def _score_text(query: str, text: str) -> int:
    query_lower = query.lower()
    text_lower = text.lower()
    score = 0
    for term in _candidate_terms(text_lower):
        if term and term in query_lower:
            score += 6 if len(term) >= 4 else 3
    for term in TECHNICAL_QUERY_TERMS:
        if term in query_lower and term in text_lower:
            score += 5
    if "msg" in query_lower and "message" in text_lower:
        score += 8
    if "message" in query_lower and "msg" in text_lower:
        score += 8
    if ("json" in query_lower or "msg" in query_lower or "message" in query_lower) and (
        "json" in text_lower or "message" in text_lower or "msg" in text_lower
    ):
        score += 5
    return score


def _entry_matches_query_project(query: str, entry: IndexEntry) -> bool:
    query_lower = query.lower()
    return any(term in query_lower for term in _project_terms(entry))


def _index_hint_entries(query: str, entries: list[IndexEntry], limit: int) -> list[tuple[int, IndexEntry]]:
    scored = []
    life_query = any(term in query.lower() for term in ("生活", "life", "個人", "personal")) or _is_asset_query(query)
    for entry in entries:
        if not life_query and "life" in entry.tags.lower():
            continue
        score = _score_text(query, " ".join([entry.page, entry.title, entry.summary, entry.tags]))
        if score > 0:
            scored.append((score, entry))
    project_scored = [(score, entry) for score, entry in scored if _entry_matches_query_project(query, entry)]
    if project_scored:
        scored = project_scored
    scored.sort(key=lambda item: (-item[0], item[1].title))
    deduped = []
    seen_pages = set()
    for score, entry in scored:
        if entry.page in seen_pages:
            continue
        seen_pages.add(entry.page)
        deduped.append((score, entry))
        if len(deduped) >= limit:
            break
    return deduped


def _candidate_terms(text: str) -> set[str]:
    terms = set(re.findall(r"[a-z0-9][a-z0-9_-]*", text.lower()))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if 2 <= len(chunk) <= 12:
            terms.add(chunk)
        for phrase in re.split(r"[、，,/\s]+", chunk):
            if 2 <= len(phrase) <= 12:
                terms.add(phrase)
    return {term for term in terms if len(term) >= 2 and term not in {"高價值操作原始檔", "直接參考"}}


def _query_names_project(query: str, candidates) -> bool:
    query_lower = query.lower()
    for _, entry, _, _ in candidates:
        project_terms = _project_terms(entry)
        if any(term in query_lower for term in project_terms):
            return True
    return False


def _project_terms(entry: IndexEntry) -> set[str]:
    terms = set()
    text = " ".join([entry.page, entry.title, entry.tags])
    for token in re.findall(r"[a-z0-9][a-z0-9_-]*", text.lower()):
        terms.add(token)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for phrase in re.split(r"[、，,/\s]+", chunk):
            if 2 <= len(phrase) <= 8:
                terms.add(phrase)
    return terms


def _entry_payload(entry: IndexEntry) -> dict[str, str]:
    return {
        "page": entry.page,
        "title": entry.title,
        "summary": entry.summary,
        "tags": entry.tags,
    }


def _extract_section_label(text: str) -> str:
    match = re.search(r"\[\[#.+?\|(.+?)\]\]", text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _normalize_heading(text: str) -> str:
    return re.sub(r"\s+", "", strip_markdown_inline(text).lower())


def _find_heading_line(lines: list[str], section: str) -> int | None:
    target = _normalize_heading(section)
    for index, line in enumerate(lines, start=1):
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match and _normalize_heading(match.group(1)) == target:
            return index
    return None


def _find_next_heading_line(lines: list[str], start_line: int) -> int | None:
    for index in range(start_line, len(lines) + 1):
        if re.match(r"^##\s+", lines[index - 1]):
            return index - 1
    return None
