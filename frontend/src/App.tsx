import { useEffect, useMemo, useState } from "react";
import Header from "./components/Header";
import QuerySummary from "./components/QuerySummary";
import MarkdownRenderer from "./components/MarkdownRenderer";
import SourceInfo from "./components/SourceInfo";
import ActionButtons from "./components/ActionButtons";
import TableOfContents from "./components/TableOfContents";
import Skeleton from "./components/Skeleton";
import ErrorView from "./components/ErrorView";
import { extractToc } from "./lib/toc";
import type { NotePayload } from "./types";

interface Props {
  apiUrl: string;
}

export default function App({ apiUrl }: Props) {
  const [data, setData] = useState<NotePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!apiUrl) {
      setError("缺少 API URL");
      setLoading(false);
      return;
    }
    let aborted = false;
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => ctrl.abort(), 10000);

    fetch(apiUrl, { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok) {
          const text = await res.text().catch(() => "");
          throw new Error(`HTTP ${res.status} ${text.slice(0, 200)}`);
        }
        return res.json() as Promise<NotePayload>;
      })
      .then((payload) => {
        if (aborted) return;
        setData(payload);
        document.title = payload.title || "Vault Reader";
      })
      .catch((err) => {
        if (aborted) return;
        if (err.name === "AbortError") {
          setError("讀取逾時，請重試");
        } else {
          setError(err.message || "讀取失敗");
        }
      })
      .finally(() => {
        if (aborted) return;
        window.clearTimeout(timer);
        setLoading(false);
      });

    return () => {
      aborted = true;
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, [apiUrl]);

  const toc = useMemo(() => (data ? extractToc(data.markdown) : []), [data]);

  if (loading) return <Skeleton />;
  if (error || !data) return <ErrorView message={error || "無資料"} />;

  return (
    <div className="min-h-screen">
      <Header title={data.title} updated={data.updated} kind={data.kind} queryId={data.query_meta?.query_id} />
      <main className="mx-auto max-w-reader px-4 pb-16 pt-4 lg:grid lg:grid-cols-[1fr_220px] lg:gap-8 lg:px-6">
        <article className="min-w-0">
          {data.query_meta && <QuerySummary meta={data.query_meta} />}
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm sm:p-8">
            <h1 className="mb-1 text-2xl font-bold leading-tight text-slate-900 sm:text-3xl">
              {data.title}
            </h1>
            <p className="mb-6 break-all text-xs text-slate-500">{data.path}</p>
            <MarkdownRenderer markdown={data.markdown} apiUrl={apiUrl} />
          </div>
          <SourceInfo path={data.path} tags={data.tags} project={data.project} updated={data.updated} />
          <ActionButtons markdown={data.markdown} backToLineUrl={data.back_to_line_url} />
        </article>
        {toc.length > 1 && (
          <aside className="hidden lg:block">
            <TableOfContents items={toc} />
          </aside>
        )}
        {toc.length > 1 && (
          <div className="lg:hidden">
            <TableOfContents items={toc} collapsible />
          </div>
        )}
      </main>
    </div>
  );
}
