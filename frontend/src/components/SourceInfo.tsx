interface Props {
  path: string;
  tags?: string[] | null;
  project?: string | null;
  updated?: string | null;
}

export default function SourceInfo({ path, tags, project, updated }: Props) {
  const hasTags = Array.isArray(tags) && tags.length > 0;
  if (!hasTags && !project && !updated) return null;
  return (
    <section className="mt-4 rounded-xl border border-slate-200 bg-white p-4 text-sm shadow-sm sm:p-5">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">來源資訊</div>
      <div className="mb-2 break-all font-mono text-xs text-slate-600">{path}</div>
      <div className="flex flex-wrap items-center gap-2">
        {project && (
          <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
            {project}
          </span>
        )}
        {updated && (
          <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-600">
            updated {updated}
          </span>
        )}
        {hasTags &&
          tags!.map((t) => (
            <span key={t} className="rounded-full bg-slate-50 px-2.5 py-0.5 text-xs text-slate-600">
              #{t}
            </span>
          ))}
      </div>
    </section>
  );
}
