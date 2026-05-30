interface Props {
  message: string;
}

export default function ErrorView({ message }: Props) {
  const onRetry = () => window.location.reload();

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-sm">
        <div className="mx-auto mb-3 inline-flex h-10 w-10 items-center justify-center rounded-full bg-rose-50 text-rose-500">
          !
        </div>
        <h2 className="mb-2 text-lg font-semibold text-slate-800">無法載入</h2>
        <p className="mb-5 break-words text-sm text-slate-500">{message}</p>
        <div className="flex justify-center gap-2">
          <button
            type="button"
            onClick={onRetry}
            className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-600"
          >
            重試
          </button>
        </div>
        <p className="mt-4 text-xs text-slate-400">使用 LINE 右上角的 X 返回聊天室</p>
      </div>
    </div>
  );
}
