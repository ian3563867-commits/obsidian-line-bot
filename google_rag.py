import os
from dataclasses import dataclass


TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass
class GoogleRagResult:
    text: str
    sources: list[str]


def google_rag_enabled() -> bool:
    return os.environ.get("GOOGLE_RAG_ENABLED", "").strip().lower() in TRUE_VALUES


def google_rag_fallback_enabled() -> bool:
    value = os.environ.get("GOOGLE_RAG_FALLBACK_AGENT", "true").strip().lower()
    return value in TRUE_VALUES


def query_google_rag(user_query: str) -> GoogleRagResult:
    store_name = os.environ.get("GOOGLE_RAG_STORE_NAME", "").strip()
    if not store_name:
        raise RuntimeError("GOOGLE_RAG_STORE_NAME is not configured")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    model = os.environ.get("GOOGLE_RAG_MODEL", "gemini-3-flash-preview").strip()
    metadata_filter = os.environ.get("GOOGLE_RAG_METADATA_FILTER", "").strip()
    client = genai.Client()

    file_search = types.FileSearch(file_search_store_names=[store_name])
    if metadata_filter:
        file_search.metadata_filter = metadata_filter

    response = client.models.generate_content(
        model=model,
        contents=build_google_rag_prompt(user_query),
        config=types.GenerateContentConfig(
            tools=[
                types.Tool(
                    file_search=file_search,
                )
            ]
        ),
    )

    text = (getattr(response, "text", "") or "").strip()
    sources = extract_source_paths(response)
    if sources:
        text = append_sources(text, sources)
    return GoogleRagResult(text=text, sources=sources)


def build_google_rag_prompt(user_query: str) -> str:
    return f"""你是使用者第二大腦 vault 的查詢助手。

請用繁體中文回答，簡潔直接。
請優先根據 File Search 找到的內容回答。
若內容不足，請明確說「RAG 來源不足」，不要硬猜。
回答最後若可取得來源，請列出 vault 來源路徑。

使用者問題：
{user_query}
"""


def append_sources(text: str, sources: list[str]) -> str:
    if not text:
        text = "RAG 查詢完成，但沒有產生文字回答。"
    visible_sources = []
    for source in sources:
        if source and source not in visible_sources:
            visible_sources.append(source)
    if not visible_sources:
        return text
    if "來源" in text and any(source in text for source in visible_sources):
        return text
    source_lines = "\n".join(f"- {source}" for source in visible_sources[:8])
    return f"{text}\n\n來源：\n{source_lines}"


def extract_source_paths(response) -> list[str]:
    chunks = []
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        metadata = getattr(candidates[0], "grounding_metadata", None)
        chunks = getattr(metadata, "grounding_chunks", None) or []
    if not chunks:
        metadata = getattr(response, "grounding_metadata", None)
        chunks = getattr(metadata, "grounding_chunks", None) or []

    sources = []
    for chunk in chunks:
        retrieved_context = getattr(chunk, "retrieved_context", None)
        if not retrieved_context:
            continue
        path = source_path_from_metadata(getattr(retrieved_context, "custom_metadata", None))
        if not path:
            path = getattr(retrieved_context, "title", "") or ""
        page_number = getattr(retrieved_context, "page_number", None)
        if path and page_number:
            path = f"{path} p.{page_number}"
        if path and path not in sources:
            sources.append(path)
    return sources


def source_path_from_metadata(custom_metadata) -> str:
    for item in custom_metadata or []:
        key = getattr(item, "key", "")
        if key != "source_path":
            continue
        return (
            getattr(item, "string_value", "")
            or str(getattr(item, "numeric_value", "") or "")
        )
    return ""
