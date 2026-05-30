import { useState } from "react";
import type { TocItem } from "../types";

interface Props {
  items: TocItem[];
  collapsible?: boolean;
}

export default function TableOfContents({ items, collapsible }: Props) {
  const [open, setOpen] = useState(!collapsible);

  if (items.length === 0) return null;

  const list = (
    <ul className="space-y-1 text-sm">
      {items.map((it) => (
        <li
          key={it.id}
          className={
            it.level === 1
              ? "font-semibold text-slate-800"
              : it.level === 2
              ? "ml-2 text-slate-700"
              : "ml-4 text-slate-500"
          }
        >
          <a href={`#${it.id}`} className="block truncate py-0.5 hover:text-blue-600">
            {it.text}
          </a>
        </li>
      ))}
    </ul>
  );

  if (collapsible) {
    return (
      <details
        open={open}
        onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
        className="my-3 rounded-lg border border-slate-200 bg-white p-3 text-sm shadow-sm"
      >
        <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500">
          目錄（{items.length}）
        </summary>
        <div className="mt-2">{list}</div>
      </details>
    );
  }

  return (
    <nav className="sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">目錄</div>
      {list}
    </nav>
  );
}
