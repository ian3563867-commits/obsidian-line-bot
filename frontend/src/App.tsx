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
import type { NotePayload, ResultProgress } from "./types";

interface Props {
  apiUrl: string;
}

const extractResultStatus = (payload: NotePayload) => {
  if (payload.status) return payload.status;
  const match = payload.markdown.match(/^狀態：\s*(\S+)/m);
  return match?.[1] ?? null;
};

const formatElapsed = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
};

const getRouteSteps = (backendLabel: string) => [
  {
    route: "precheck",
    label: "強命中",
    summary: "明確命中候選來源",
    activeClass: "border-emerald-500 bg-emerald-100 text-emerald-950 shadow-sm",
    inactiveClass: "border-emerald-200 bg-white/60 text-emerald-900",
    badgeClass: "bg-emerald-600 text-white",
  },
  {
    route: "candidate_search",
    label: "候選搜尋",
    summary: `多個可能入口，交給 ${backendLabel} 比較`,
    activeClass: "border-amber-500 bg-amber-100 text-amber-950 shadow-sm",
    inactiveClass: "border-amber-200 bg-white/60 text-amber-900",
    badgeClass: "bg-amber-600 text-white",
  },
  {
    route: "index_hints",
    label: "低信心提示",
    summary: "index 有線索，但需補判斷",
    activeClass: "border-sky-500 bg-sky-100 text-sky-950 shadow-sm",
    inactiveClass: "border-sky-200 bg-white/60 text-sky-900",
    badgeClass: "bg-sky-600 text-white",
  },
  {
    route: "fallback",
    label: "深度搜尋",
    summary: "沒有可信入口，走保守搜尋",
    activeClass: "border-rose-500 bg-rose-100 text-rose-950 shadow-sm",
    inactiveClass: "border-rose-200 bg-white/60 text-rose-900",
    badgeClass: "bg-rose-600 text-white",
  },
];

function ProcessingStatus({ elapsedSeconds, progress }: { elapsedSeconds: number; progress?: ResultProgress | null }) {
  const backendLabel = progress?.backend_label || "Agent";
  const routeSteps = getRouteSteps(backendLabel);
  const phase = progress?.route_label || (
    elapsedSeconds < 10
      ? "正在確認問題與候選入口"
      : elapsedSeconds < 30
        ? "正在讀取 vault 相關筆記"
        : "正在整理可回覆的答案"
  );
  const sources = progress?.sources?.filter((source) => source.path) ?? [];
  const events = progress?.events?.filter((event) => event.text) ?? [];
  const activeRoute = progress?.route || "";

  return (
    <div className="mb-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-semibold">{phase}</div>
        <div className="font-mono text-xs text-emerald-700">已等待 {formatElapsed(elapsedSeconds)}</div>
      </div>
      {progress && (
        <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
          {routeSteps.map((step) => {
            const isActive = activeRoute === step.route;
            return (
              <div
                key={step.route}
                className={`rounded border px-3 py-2 transition ${isActive ? step.activeClass : step.inactiveClass}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-semibold">{step.label}</div>
                    <div className="mt-0.5 font-mono text-[11px]">{step.route}</div>
                  </div>
                  {isActive && (
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${step.badgeClass}`}>
                      目前走這條
                    </span>
                  )}
                </div>
                <div className="mt-1 leading-relaxed">{step.summary}</div>
                {isActive && (
                  <div className="mt-2 grid gap-1 font-mono text-[11px] sm:grid-cols-2">
                    <div>mode: {progress.mode || "-"}</div>
                    <div>confidence: {progress.confidence || "-"}</div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-emerald-100">
        <div
          className="h-full rounded-full bg-emerald-500 transition-all duration-700"
          style={{ width: `${Math.min(92, 18 + elapsedSeconds * 2)}%` }}
        />
      </div>
      {progress?.description && <p className="mt-3 text-xs leading-relaxed text-emerald-900">{progress.description}</p>}
      {progress?.reason && (
        <div className="mt-2 rounded border border-emerald-100 bg-white/50 px-3 py-2 text-xs text-emerald-900">
          <span className="font-semibold">判斷原因：</span>
          {progress.reason}
        </div>
      )}
      {sources.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs font-semibold text-emerald-800">
            {sources.some((source) => source.kind === "source") ? "候選來源" : "候選入口"}
          </div>
          <ul className="space-y-1.5">
            {sources.map((source, index) => (
              <li key={`${source.path}-${index}`} className="rounded border border-emerald-100 bg-white/50 px-3 py-2">
                <div className="break-all font-mono text-xs text-emerald-950">{source.path}</div>
                {source.summary && <div className="mt-1 text-xs text-emerald-800">{source.summary}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {events.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs font-semibold text-emerald-800">進度</div>
          <ul className="space-y-1 text-xs text-emerald-900">
            {events.map((event, index) => (
              <li key={`${event.text}-${index}`} className="flex gap-2">
                <span className="shrink-0 font-mono text-emerald-700">{event.time || "--:--:--"}</span>
                <span>{event.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="mt-2 text-xs leading-relaxed text-emerald-800">
        {progress?.next_step || `這段時間仍是 ${backendLabel} 查詢與整理答案，不是瀏覽器卡住；完成後頁面會自動更新。`}
      </p>
    </div>
  );
}

export default function App({ apiUrl }: Props) {
  const [data, setData] = useState<NotePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [processingStartedAt, setProcessingStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!apiUrl) {
      setError("缺少 API URL");
      setLoading(false);
      return;
    }
    let cancelled = false;
    let intervalId: number | undefined;
    let pollCtrl: AbortController | null = null;
    let consecutiveErrors = 0;

    setData(null);
    setError(null);
    setLoading(true);

    const fetchOnce = async (controller: AbortController): Promise<NotePayload> => {
      const timer = window.setTimeout(() => controller.abort(), 10000);
      try {
        const res = await fetch(apiUrl, { signal: controller.signal });
        if (!res.ok) {
          const text = await res.text().catch(() => "");
          throw new Error(`HTTP ${res.status} ${text.slice(0, 200)}`);
        }
        return res.json() as Promise<NotePayload>;
      } finally {
        window.clearTimeout(timer);
      }
    };

    const stopPolling = () => {
      if (intervalId !== undefined) {
        window.clearInterval(intervalId);
        intervalId = undefined;
      }
      pollCtrl?.abort();
      pollCtrl = null;
    };

    const shouldPoll = (payload: NotePayload) =>
      payload.kind === "result" && extractResultStatus(payload) === "處理中";

    const isTerminalStatus = (payload: NotePayload) =>
      payload.kind === "result" && (extractResultStatus(payload) === "完成" || extractResultStatus(payload) === "失敗");

    const startPolling = () => {
      if (intervalId !== undefined) return;
      intervalId = window.setInterval(async () => {
        if (pollCtrl) return;
        const controller = new AbortController();
        pollCtrl = controller;
        try {
          const payload = await fetchOnce(controller);
          if (cancelled) return;
          consecutiveErrors = 0;
          setData(payload);
          document.title = payload.title || "Vault Reader";
          if (isTerminalStatus(payload) || !shouldPoll(payload)) {
            stopPolling();
          }
        } catch {
          if (cancelled) return;
          consecutiveErrors += 1;
          if (consecutiveErrors > 3) {
            stopPolling();
          }
        } finally {
          if (pollCtrl === controller) {
            pollCtrl = null;
          }
        }
      }, 2000);
    };

    const initialCtrl = new AbortController();
    fetchOnce(initialCtrl)
      .then((payload) => {
        if (cancelled) return;
        setData(payload);
        document.title = payload.title || "Vault Reader";
        if (shouldPoll(payload)) {
          startPolling();
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err.name === "AbortError") {
          setError("讀取逾時，請重試");
        } else {
          setError(err.message || "讀取失敗");
        }
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });

    return () => {
      cancelled = true;
      initialCtrl.abort();
      stopPolling();
    };
  }, [apiUrl]);

  const isProcessingResult = data ? data.kind === "result" && extractResultStatus(data) === "處理中" : false;

  useEffect(() => {
    if (!isProcessingResult) {
      setProcessingStartedAt(null);
      return;
    }
    setProcessingStartedAt((value) => value ?? Date.now());
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [isProcessingResult, data?.path]);

  const toc = useMemo(() => (data ? extractToc(data.markdown) : []), [data]);
  const elapsedSeconds = processingStartedAt ? Math.max(0, Math.floor((now - processingStartedAt) / 1000)) : 0;

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
            {isProcessingResult && <ProcessingStatus elapsedSeconds={elapsedSeconds} progress={data.progress} />}
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
