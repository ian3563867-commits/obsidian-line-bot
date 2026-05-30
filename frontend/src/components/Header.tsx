interface Props {
  title: string;
  updated?: string | null;
  kind: "result" | "note";
  queryId?: string | null;
}

export default function Header({ title, updated, kind, queryId }: Props) {
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/85 backdrop-blur">
      <div className="mx-auto flex max-w-reader items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-emerald-500 text-xs font-bold text-white"
            aria-hidden
          >
            V
          </span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-slate-800">{title || "Vault Reader"}</div>
            <div className="flex items-center gap-2 text-[11px] text-slate-500">
              <span className="rounded bg-slate-100 px-1.5 py-0.5">
                {kind === "result" ? "查詢結果" : "Vault 筆記"}
              </span>
              {updated && <span>更新 {updated}</span>}
              {queryId && <span className="truncate font-mono">{queryId}</span>}
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
