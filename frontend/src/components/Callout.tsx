import type { ReactNode } from "react";

export type CalloutKind = "info" | "warn" | "tip";

const STYLES: Record<CalloutKind, { wrap: string; label: string; tag: string }> = {
  info: {
    wrap: "border-blue-200 bg-blue-50 text-blue-900",
    label: "text-blue-700",
    tag: "注意",
  },
  warn: {
    wrap: "border-amber-200 bg-amber-50 text-amber-900",
    label: "text-amber-700",
    tag: "警告",
  },
  tip: {
    wrap: "border-emerald-200 bg-emerald-50 text-emerald-900",
    label: "text-emerald-700",
    tag: "重點",
  },
};

const PATTERNS: Array<{ kind: CalloutKind; re: RegExp }> = [
  { kind: "warn", re: /^(警告|warning|caution|危險)[:：]/i },
  { kind: "tip", re: /^(重點|tip|提示|note)[:：]/i },
  { kind: "info", re: /^(注意|info|資訊)[:：]/i },
];

export function detectCalloutKind(text: string): CalloutKind | null {
  for (const { kind, re } of PATTERNS) {
    if (re.test(text)) return kind;
  }
  return null;
}

interface Props {
  kind: CalloutKind;
  children: ReactNode;
}

export default function Callout({ kind, children }: Props) {
  const s = STYLES[kind];
  return (
    <div className={`not-prose my-4 rounded-lg border px-4 py-3 ${s.wrap}`}>
      <div className={`mb-1 text-xs font-semibold uppercase tracking-wide ${s.label}`}>{s.tag}</div>
      <div className="text-sm leading-relaxed [&>p]:m-0 [&>p+p]:mt-2">{children}</div>
    </div>
  );
}
