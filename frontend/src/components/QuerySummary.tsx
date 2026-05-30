import type { QueryMeta } from "../types";

interface Props {
  meta: QueryMeta;
}

export default function QuerySummary({ meta }: Props) {
  if (!meta || (!meta.raw_input && !meta.query_type && !meta.elapsed_ms)) return null;
  return (
    <section className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">查詢摘要</div>
      {meta.raw_input && (
        <p className="mb-2 text-sm leading-relaxed text-slate-700">
          <span className="text-slate-400">輸入：</span>
          {meta.raw_input}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
        {meta.query_type && (
          <span>
            類型：<span className="font-medium text-slate-700">{meta.query_type}</span>
          </span>
        )}
        {meta.elapsed_ms != null && (
          <span>
            耗時：<span className="font-medium text-slate-700">{(meta.elapsed_ms / 1000).toFixed(1)}s</span>
          </span>
        )}
      </div>
    </section>
  );
}
