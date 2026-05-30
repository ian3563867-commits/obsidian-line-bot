import { useState } from "react";

interface Props {
  markdown: string;
  backToLineUrl?: string | null;
}

export default function ActionButtons({ markdown, backToLineUrl }: Props) {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(markdown);
      } else {
        const ta = document.createElement("textarea");
        ta.value = markdown;
        ta.style.position = "fixed";
        ta.style.top = "-1000px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="mt-4 flex flex-wrap gap-3">
      <button
        type="button"
        onClick={onCopy}
        className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
      >
        {copied ? "已複製全文" : "複製 Markdown"}
      </button>
      {backToLineUrl && (
        <a
          href={backToLineUrl}
          className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-emerald-600"
        >
          回到 LINE
        </a>
      )}
    </div>
  );
}
