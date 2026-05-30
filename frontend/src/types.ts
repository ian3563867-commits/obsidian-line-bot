export interface QueryMeta {
  raw_input?: string | null;
  query_type?: string | null;
  elapsed_ms?: number | null;
  query_id?: string | null;
}

export interface NotePayload {
  title: string;
  path: string;
  updated?: string | null;
  tags?: string[] | null;
  project?: string | null;
  query_meta?: QueryMeta | null;
  markdown: string;
  kind: "result" | "note";
  back_to_line_url?: string | null;
}

export interface TocItem {
  id: string;
  text: string;
  level: 1 | 2 | 3;
}
