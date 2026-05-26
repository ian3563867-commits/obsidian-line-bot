import os
import json
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

GENERIC_PROJECT_TERMS = {
    "api",
    "json",
    "message",
    "msg",
    "sql",
    "sop",
    "常用sql",
}

LIFE_MATCH_IGNORED_TERMS = GENERIC_PROJECT_TERMS | {
    "line",
    "bot",
    "linebot",
    "fastapi",
    "security",
    "ai",
    "9002",
    "9002-vaultlinebot",
}

PROJECT_CONTEXT_TERMS = ("SampleProjectB", "SampleProjectA", "SampleProjectC", "SampleProjectD", "SampleProjectE", "SampleProjectF")

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
    life_override: bool = False

    @property
    def hit(self) -> bool:
        return self.mode in {"candidate_files", "preloaded_context", "manifest_candidates"} and bool(self.source_blocks)


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
        manifest = _manifest_precheck(query, vault_dir, max_blocks)
        if manifest and manifest.life_override:
            return manifest
        if hints:
            return PrecheckResult(
                mode="index_hints",
                confidence="low",
                matched_entries=[_entry_payload(entry) for _, entry in hints],
                source_blocks=[],
                fallback_reason="index hints only; deep search still required",
            )
        if manifest:
            return manifest
        reason = "time-sensitive, open-item, or identifier query" if deep_search_required else "no index hints"
        return _fallback(reason)

    entries = _asset_entries(all_entries) if asset_query else [entry for entry in all_entries if entry.high_value]
    if not entries:
        hints = _index_hint_entries(query, all_entries, max_blocks)
        manifest = _manifest_precheck(query, vault_dir, max_blocks)
        if manifest and manifest.life_override:
            return manifest
        if hints:
            return PrecheckResult(
                mode="index_hints",
                confidence="low",
                matched_entries=[_entry_payload(entry) for _, entry in hints],
                source_blocks=[],
                fallback_reason="no strong deterministic entries; index hints only",
            )
        if manifest:
            return manifest
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
        manifest = _manifest_precheck(query, vault_dir, max_blocks)
        if manifest and manifest.life_override:
            return manifest
        if hints:
            return PrecheckResult(
                mode="index_hints",
                confidence="low",
                matched_entries=[_entry_payload(entry) for _, entry in hints],
                source_blocks=[],
                fallback_reason="no strong deterministic match; index hints only",
            )
        if manifest:
            return manifest
        return _fallback("no deterministic index match")

    project_candidates = [item for item in candidates if _entry_matches_named_project_context(query, item[1])]
    project_named = bool(project_candidates)
    if project_named:
        conflict_candidates = _cross_project_tool_conflict_candidates(query, candidates, project_candidates, max_blocks)
        if conflict_candidates:
            return PrecheckResult(
                mode="candidate_list",
                confidence="ambiguous",
                matched_entries=[_entry_payload(item[1]) for item in conflict_candidates],
                source_blocks=[],
                fallback_reason="project term conflicts with cross-project tool term",
            )
        candidates = [item for item in candidates if _entry_matches_named_project_context(query, item[1])]
    else:
        specific_operation_matches = _specific_operation_matches(query, candidates)
        if len(specific_operation_matches) == 1:
            candidates = specific_operation_matches
        elif _is_ambiguous_message_query(query, candidates):
            return PrecheckResult(
                mode="candidate_list",
                confidence="ambiguous",
                matched_entries=[_entry_payload(item[1]) for item in candidates[:max_blocks]],
                source_blocks=[],
                fallback_reason="message query has multiple project candidates and no explicit project",
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
        block = build_candidate_file_hint(path, toc_entry)
        if block:
            blocks.append(block)
            matched_entries.append(_entry_payload(entry))
        if blocks and project_named:
            break

    if not blocks:
        return _fallback("matched entry but failed to read source block")

    return PrecheckResult(
        mode="candidate_files",
        confidence="high",
        matched_entries=matched_entries,
        source_blocks=blocks,
        fallback_reason="",
        life_override=any("life" in entry.tags.lower() for _, entry, _, _ in candidates[:max_blocks]),
    )


def build_preloaded_prompt(original_prompt: str, result: PrecheckResult) -> str:
    parts = [
        "本次查詢已由 Python deterministic index pre-check 先完成文件級縮範圍。",
        "重要邊界：Python 只負責命中候選文件，不負責精準切出答案行號或段落。",
        "請只在下方候選文件內 Read / 搜尋 / 判斷段落；不要全 vault Grep / Read。",
        "若候選文件明顯不足，請明確說明需要 fallback，不要自行擴大到其他檔案。",
        "回答需使用繁體中文，保持 LINE 可讀性，並在結尾列出實際參考來源檔案路徑；行號可列但不可自行猜測。",
        "",
        f"使用者原始問題：{original_prompt}",
        "",
        "Python pre-check 命中：",
    ]
    for entry in result.matched_entries:
        parts.append(f"- {entry['page']}：{entry['summary']}")
    parts.append("")
    parts.append("候選文件（請限定在這些檔案內查詢）：")
    for block in result.source_blocks:
        parts.append("")
        parts.append(f"- {block.file}")
        parts.append(f"  命中原因：{block.match_reason}")
        if block.content:
            parts.append(f"  提示：{block.content}")
    return "\n".join(parts)


def build_index_hint_prompt(original_prompt: str, result: PrecheckResult) -> str:
    parts = [
        "Python 已先讀取 04_Knowledge/index.md，找到以下可能相關的 index hints。",
        "這些只是候選入口，不是最終答案；請先 Read hinted knowledge page。",
        "若 knowledge page 頂部或來源區塊標示了原始來源檔，且使用者是在查「內容、完整、流程、步驟、SOP、實作、自動化」這類需要細節的問題，必須一併 Read 原始來源檔。",
        "若 hinted knowledge page 或來源原始檔仍不足，請再依照原本保守三層搜尋規則判斷是否需要 Grep。",
        "若使用者是在做廣泛主題查詢（例如：所有、全部、相關內容、內容、有哪些、整理），不可只靠 index hints 宣稱「所有」；必須用主題關鍵字在 02_Projects / 04_Knowledge 做精準 Grep 補齊其他脈絡。",
        "若使用者是在查「自動化內容、架構、經驗、規劃、方案」這類方案 / 經驗整理型問題，答案必須整合原始需求、解法流程、關鍵判斷、限制風險與後續事項；不可只摘錄主題詞附近段落或只列 action 名稱。",
        "若 hints 與問題不符，請忽略 hints 並照原流程查詢。回答結尾仍需列出實際參考來源。",
        "",
        "Index hints：",
    ]
    for entry in result.matched_entries:
        parts.append(f"- {entry['page']}：{entry['summary']}（tags: {entry['tags']}）")
    parts.extend(["", "使用者原始問題：", original_prompt])
    return "\n".join(parts)


def build_candidate_search_prompt(original_prompt: str, result: PrecheckResult) -> str:
    parts = [
        "Python pre-check 找到多個可能入口，但未硬猜唯一答案。",
        "這些候選入口是搜尋邊界，不是最終答案；請先 Read / 搜尋下方候選文件或 knowledge page。",
        "請比較候選內容與使用者原始問題，嘗試縮小到最相關的段落、SQL、MSG、JSON、SOP 或操作步驟並回答。",
        "不可只把候選清單回覆給使用者；只有在讀完候選後仍無法判定時，才請使用者補專案、系統或關鍵字。",
        "若候選文件仍不足，請依原本保守三層搜尋規則，用問題中的具體詞彙在 02_Projects / 04_Knowledge 做精準 Grep 補齊。",
        "回答結尾仍需列出實際參考來源。",
        "",
        "Candidate entries：",
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


def read_manifest(vault_dir: str) -> list[dict]:
    manifest_path = os.path.join(vault_dir, "06_System", "Search", "vault_search_manifest.jsonl")
    if not os.path.isfile(manifest_path):
        return []
    records = []
    with open(manifest_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


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


def build_candidate_file_hint(path: str, toc_entry: TocEntry | None) -> SourceBlock | None:
    if not os.path.isfile(path):
        return None
    if toc_entry:
        reason = f"子目錄命中：{toc_entry.category} / {toc_entry.section}"
        hint = f"可優先查看段落「{toc_entry.section}」；同義詞 / 常見問法：{toc_entry.terms}"
    else:
        reason = "主目錄命中，未定位子目錄段落"
        hint = "請在此候選文件內自行 Read / 搜尋相關段落，不要只依賴檔名判斷。"
    return SourceBlock(
        file=_display_path(path),
        start_line=0,
        end_line=0,
        content=hint,
        match_reason=reason,
    )


def _manifest_precheck(query: str, vault_dir: str, max_blocks: int) -> PrecheckResult | None:
    life_candidates = []
    candidates = []
    for record in read_manifest(vault_dir):
        if record.get("life"):
            if not _strict_life_match(query, record):
                continue
            life_candidates.append((100, record))
            continue
        else:
            haystack = " ".join(
                [
                    str(record.get("path", "")),
                    str(record.get("title", "")),
                    str(record.get("project", "")),
                    " ".join(record.get("tags") or []),
                    " ".join(record.get("aliases") or []),
                    " ".join(record.get("headings") or []),
                ]
            )
            score = _score_text(query, haystack)
            if score <= 0:
                continue
        candidates.append((score, record))

    if life_candidates:
        candidates = life_candidates
    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], str(item[1].get("path", ""))))
    blocks: list[SourceBlock] = []
    matched_entries: list[dict[str, str]] = []
    selected_records = []
    for _, record in candidates[:max_blocks]:
        path = os.path.join(vault_dir, str(record.get("path", "")).replace("/", os.sep))
        block = build_candidate_file_hint(path, None)
        if not block:
            continue
        blocks.append(block)
        matched_entries.append(_manifest_entry_payload(record))
        selected_records.append(record)

    if not blocks:
        return None

    return PrecheckResult(
        mode="manifest_candidates",
        confidence="high",
        matched_entries=matched_entries,
        source_blocks=blocks,
        fallback_reason="",
        life_override=any(bool(record.get("life")) for record in selected_records),
    )


def _strict_life_match(query: str, record: dict) -> bool:
    query_lower = query.lower()
    units = []
    units.extend(record.get("tags") or [])
    units.extend(record.get("aliases") or [])
    for unit in units:
        normalized = str(unit).strip().lower()
        if normalized in LIFE_MATCH_IGNORED_TERMS:
            continue
        if re.fullmatch(r"[a-z0-9_-]+", normalized):
            if re.search(rf"(?<![a-z0-9_-]){re.escape(normalized)}(?![a-z0-9_-])", query_lower):
                return True
            continue
        if len(normalized) >= 2 and normalized in query_lower:
            return True
    return False


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


def _is_ambiguous_message_query(query: str, candidates: list[tuple[int, IndexEntry, str, TocEntry | None]]) -> bool:
    query_lower = query.lower()
    if "msg" not in query_lower and "message" not in query_lower:
        return False
    project_like = [
        item for item in candidates
        if any(term in item[1].tags for term in ("SampleProjectB", "SampleProjectA", "SampleProjectC", "SampleProjectD", "SampleProjectE", "SampleProjectF"))
    ]
    if len(project_like) > 1 and len(_specific_operation_matches(query, project_like)) == 1:
        return False
    return len(project_like) > 1


def _specific_operation_matches(
    query: str,
    candidates: list[tuple[int, IndexEntry, str, TocEntry | None]],
) -> list[tuple[int, IndexEntry, str, TocEntry | None]]:
    query_terms = _specific_query_terms(query)
    if not query_terms:
        return []
    matches = []
    for item in candidates:
        _, entry, _, toc_entry = item
        text_parts = [entry.page, entry.title, entry.summary, entry.tags]
        if toc_entry:
            text_parts.extend([toc_entry.category, toc_entry.section, toc_entry.purpose, toc_entry.terms])
        text = "".join(text_parts).lower()
        if any(term in text for term in query_terms):
            matches.append(item)
    return matches


def _specific_query_terms(query: str) -> set[str]:
    terms = set()
    ignored_terms = set(GENERIC_PROJECT_TERMS)
    ignored_terms.update(TECHNICAL_QUERY_TERMS)
    for token in re.findall(r"[a-z0-9][a-z0-9_-]*", query.lower()):
        if len(token) >= 3 and token not in ignored_terms:
            terms.add(token)
    for chunk in re.findall(r"[\u4e00-\u9fff]{4,}", query):
        normalized = re.sub(r"^(查詢|查|找|搜尋|看|幫我|請問)", "", chunk)
        if len(normalized) >= 4:
            terms.add(normalized.lower())
        for size in range(4, min(8, len(normalized)) + 1):
            for index in range(len(normalized) - size + 1):
                term = normalized[index:index + size].lower()
                if term not in ignored_terms and term not in {"message", "常用sql"}:
                    terms.add(term)
    return terms


def _cross_project_tool_conflict_candidates(
    query: str,
    candidates: list[tuple[int, IndexEntry, str, TocEntry | None]],
    project_candidates: list[tuple[int, IndexEntry, str, TocEntry | None]],
    max_blocks: int,
) -> list[tuple[int, IndexEntry, str, TocEntry | None]]:
    specific_terms = _cross_project_specific_terms(query, project_candidates)
    if not specific_terms:
        return []
    cross_project_matches = []
    for item in candidates:
        _, entry, _, toc_entry = item
        if _entry_matches_named_project_context(query, entry) or not _is_cross_project_tool_entry(entry):
            continue
        text_parts = [entry.page, entry.title, entry.summary, entry.tags]
        if toc_entry:
            text_parts.extend([toc_entry.category, toc_entry.section, toc_entry.purpose, toc_entry.terms])
        text = " ".join(text_parts).lower()
        if any(term in text for term in specific_terms):
            cross_project_matches.append(item)
    if not cross_project_matches:
        return []

    combined = []
    seen_pages = set()
    for group in (project_candidates, cross_project_matches):
        for item in sorted(group, key=lambda candidate: (-candidate[0], candidate[1].title)):
            page = item[1].page
            if page in seen_pages:
                continue
            combined.append(item)
            seen_pages.add(page)
            if len(combined) >= max_blocks:
                return combined
    return combined


def _cross_project_specific_terms(
    query: str,
    project_candidates: list[tuple[int, IndexEntry, str, TocEntry | None]],
) -> set[str]:
    query_lower = query.lower()
    project_terms = set()
    for _, entry, _, _ in project_candidates:
        project_terms.update(_project_terms(entry))

    ignored_terms = set(GENERIC_PROJECT_TERMS)
    ignored_terms.update(TECHNICAL_QUERY_TERMS)
    ignored_terms.update(project_terms)
    ignored_terms.update({"查詢", "搜尋", "請問", "幫我"})

    terms = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", query_lower)
        if len(token) >= 3 and token not in ignored_terms
    }
    for chunk in re.findall(r"[\u4e00-\u9fff]{3,}", query):
        normalized = re.sub(r"^(查詢|查|找|搜尋|看|幫我|請問)", "", chunk)
        for size in range(3, min(8, len(normalized)) + 1):
            for index in range(len(normalized) - size + 1):
                term = normalized[index:index + size].lower()
                if term not in ignored_terms and not any(project_term in term for project_term in project_terms):
                    terms.add(term)
    return terms


def _is_cross_project_tool_entry(entry: IndexEntry) -> bool:
    text = " ".join([entry.page, entry.title, entry.summary, entry.tags]).lower()
    return "通用" in text or "cross-project" in text


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


def _entry_matches_named_project_context(query: str, entry: IndexEntry) -> bool:
    return any(term in query and term in entry.tags for term in PROJECT_CONTEXT_TERMS)


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
        for size in range(2, min(6, len(chunk)) + 1):
            for index in range(len(chunk) - size + 1):
                terms.add(chunk[index:index + size])
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
        if token not in GENERIC_PROJECT_TERMS:
            terms.add(token)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for phrase in re.split(r"[、，,/\s]+", chunk):
            if 2 <= len(phrase) <= 8 and phrase.lower() not in GENERIC_PROJECT_TERMS:
                terms.add(phrase)
    return terms


def _entry_payload(entry: IndexEntry) -> dict[str, str]:
    return {
        "page": entry.page,
        "title": entry.title,
        "summary": entry.summary,
        "tags": entry.tags,
    }


def _manifest_entry_payload(record: dict) -> dict[str, str]:
    tags = record.get("tags") or []
    return {
        "page": str(record.get("path", "")),
        "title": str(record.get("title") or record.get("filename") or record.get("path", "")),
        "summary": str(record.get("project") or record.get("folder") or record.get("kind") or ""),
        "tags": ", ".join(str(tag) for tag in tags),
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
