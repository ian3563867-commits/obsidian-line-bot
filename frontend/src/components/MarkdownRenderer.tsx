import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import CodeBlock from "./CodeBlock";
import Callout, { detectCalloutKind } from "./Callout";
import { slugify } from "../lib/toc";

interface Props {
  markdown: string;
  apiUrl?: string;
}

const usedIds = new Map<string, number>();

function makeHeadingId(children: any): string {
  const text = extractText(children);
  const base = slugify(text) || "section";
  const n = usedIds.get(base) ?? 0;
  usedIds.set(base, n + 1);
  return n === 0 ? base : `${base}-${n}`;
}

function extractText(node: any): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (node && typeof node === "object" && "props" in node) {
    return extractText(node.props.children);
  }
  return "";
}

export default function MarkdownRenderer({ markdown }: Props) {
  usedIds.clear();
  return (
    <div className="reader-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ node, children, ...rest }) => (
            <h2 id={makeHeadingId(children)} {...rest}>
              {children}
            </h2>
          ),
          h2: ({ node, children, ...rest }) => (
            <h2 id={makeHeadingId(children)} {...rest}>
              {children}
            </h2>
          ),
          h3: ({ node, children, ...rest }) => (
            <h3 id={makeHeadingId(children)} {...rest}>
              {children}
            </h3>
          ),
          pre: ({ children }) => <>{children}</>,
          code: ({ node, className, children, ...rest }: any) => {
            const inline = !className;
            if (inline) {
              return (
                <code className={className} {...rest}>
                  {children}
                </code>
              );
            }
            const match = /language-(\w+)/.exec(className || "");
            const lang = match ? match[1] : "";
            const text = String(children).replace(/\n$/, "");
            return <CodeBlock language={lang} code={text} />;
          },
          blockquote: ({ children }) => {
            const text = extractText(children).trim();
            const kind = detectCalloutKind(text);
            if (kind) {
              return <Callout kind={kind}>{children}</Callout>;
            }
            return <blockquote>{children}</blockquote>;
          },
          table: ({ children }) => (
            <div className="not-prose my-4 overflow-x-auto rounded-lg border border-slate-200">
              <table className="w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border-b border-slate-200 bg-slate-50 px-3 py-2 text-left font-semibold text-slate-700">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border-b border-slate-100 px-3 py-2 align-top text-slate-700">{children}</td>
          ),
          a: ({ children, href }) => {
            const h = href || "";
            const isExternal = /^https?:\/\//i.test(h);
            return (
              <a href={h} target={isExternal ? "_blank" : undefined} rel="noopener noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
