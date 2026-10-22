import { isValidElement, memo, useEffect, useId, useRef, useState } from "react";
import type { ComponentProps, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import "highlight.js/styles/github-dark.css";
import "katex/dist/katex.min.css";

let mermaidLoader: Promise<typeof import("mermaid").default> | null = null;

function loadMermaid(): Promise<typeof import("mermaid").default> {
  mermaidLoader ??= import("mermaid").then((module) => {
    const mermaid = module.default;
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "dark",
      fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
      themeVariables: {
        background: "#151c25",
        primaryColor: "#13231f",
        primaryTextColor: "#e6edf3",
        primaryBorderColor: "#34d399",
        lineColor: "#64748b",
        secondaryColor: "#111827",
        tertiaryColor: "#0f172a",
      },
    });
    return mermaid;
  });
  return mermaidLoader;
}

function describeError(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function MermaidDiagram({ chart }: { chart: string }) {
  const reactId = useId();
  const idBase = reactId.replace(/[^a-zA-Z0-9_-]/g, "");
  const sequence = useRef(0);
  const [svg, setSvg] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);

  useEffect(() => {
    const source = chart.trim();
    if (source.length === 0) {
      setSvg("");
      setError(null);
      setRendering(false);
      return;
    }
    let cancelled = false;
    setRendering(true);
    const timer = window.setTimeout(async () => {
      try {
        const mermaid = await loadMermaid();
        const renderId = `openbad-mermaid-${idBase}-${sequence.current++}`;
        const result = await mermaid.render(renderId, source);
        if (!cancelled) {
          setSvg(result.svg);
          setError(null);
        }
      } catch (cause) {
        if (!cancelled) {
          setSvg("");
          setError(describeError(cause));
        }
      } finally {
        if (!cancelled) {
          setRendering(false);
        }
      }
    }, 320);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [chart, idBase]);

  return (
    <div className="mermaid-diagram" data-rendering={rendering}>
      <div className="mermaid-diagram-toolbar">
        <span>Mermaid</span>
        {rendering && <span className="mermaid-diagram-status">rendering…</span>}
      </div>
      {svg.length > 0 ? (
        <div
          className="mermaid-diagram-canvas"
          role="img"
          aria-label="Mermaid diagram"
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      ) : error === null ? (
        <p className="mermaid-diagram-empty">Waiting for diagram source…</p>
      ) : (
        <p className="mermaid-diagram-error">
          Diagram could not be rendered: {error}
        </p>
      )}
      <details className="mermaid-diagram-source">
        <summary>Diagram source</summary>
        <pre>
          <code>{chart}</code>
        </pre>
      </details>
    </div>
  );
}

function extractText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(extractText).join("");
  }
  if (isValidElement(node)) {
    return extractText((node.props as { children?: ReactNode }).children);
  }
  return "";
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="markdown-copy"
      onClick={() => {
        if (!navigator.clipboard) {
          return;
        }
        void navigator.clipboard
          .writeText(text)
          .then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1_500);
          })
          .catch(() => undefined);
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

type MarkdownPreProps = ComponentProps<"pre"> & { node?: unknown };

function MarkdownPre({ children, node: _node, ...props }: MarkdownPreProps) {
  const child = Array.isArray(children) ? children[0] : children;
  if (isValidElement(child) && child.type === MermaidDiagram) {
    return <>{children}</>;
  }
  const text = extractText(children);
  return (
    <div className="markdown-code-block">
      {text.length > 0 && <CopyButton text={text} />}
      <pre {...props}>{children}</pre>
    </div>
  );
}

function MarkdownMessage({ text }: { text: string }) {
  return (
    <div className="markdown-body chat-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkBreaks]}
        rehypePlugins={[
          rehypeKatex,
          [rehypeHighlight, { detect: true, ignoreMissing: true }],
        ]}
        components={{
          a: ({ children, node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          pre: MarkdownPre,
          table: ({ children, node: _node, ...props }) => (
            <div className="markdown-table-wrap">
              <table {...props}>{children}</table>
            </div>
          ),
          code: ({ className, children, node: _node, ...props }) => {
            const language = /language-([\w-]+)/.exec(className ?? "")?.[1];
            if (language === "mermaid") {
              return (
                <MermaidDiagram chart={String(children).replace(/\n$/, "")} />
              );
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownMessage);
