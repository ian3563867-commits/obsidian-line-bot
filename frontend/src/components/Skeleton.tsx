export default function Skeleton() {
  return (
    <div className="min-h-screen animate-pulse">
      <div className="h-14 border-b border-slate-200 bg-white" />
      <div className="mx-auto max-w-reader space-y-3 px-4 py-6 sm:px-6">
        <div className="h-20 rounded-xl bg-slate-200/70" />
        <div className="space-y-3 rounded-xl bg-slate-200/70 p-6">
          <div className="h-7 w-2/3 rounded bg-slate-300/70" />
          <div className="h-3 w-1/3 rounded bg-slate-300/70" />
          <div className="h-3 rounded bg-slate-300/70" />
          <div className="h-3 w-5/6 rounded bg-slate-300/70" />
          <div className="h-3 w-4/6 rounded bg-slate-300/70" />
        </div>
      </div>
    </div>
  );
}
