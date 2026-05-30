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
  status?: string | null;
  progress?: ResultProgress | null;
  back_to_line_url?: string | null;
}

export interface ResultProgressSource {
  kind?: "source" | "entry" | string;
  path: string;
  summary?: string | null;
}

export interface ResultProgressEvent {
  time?: string | null;
  text: string;
}

export interface ResultProgress {
  route?: string | null;
  backend_label?: string | null;
  route_label?: string | null;
  mode?: string | null;
  confidence?: string | null;
  reason?: string | null;
  description?: string | null;
  sources?: ResultProgressSource[] | null;
  events?: ResultProgressEvent[] | null;
  elapsed_seconds?: number | null;
  next_step?: string | null;
}

export interface TocItem {
  id: string;
  text: string;
  level: 1 | 2 | 3;
}
