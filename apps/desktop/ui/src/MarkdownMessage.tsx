import { isValidElement, memo, useEffect, useId, useRef, useState } from "react";
import type { ComponentProps, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import "katex/dist/katex.min.css";

type ColorScheme = "dark" | "light";

const lightQuery = "(prefers-color-scheme: light)";

function currentScheme(): ColorScheme {
  return window.matchMedia(lightQuery).matches ? "light" : "dark";
}

/** Re-renders when the OS appearance changes. */
function useColorScheme(): ColorScheme {
  const [scheme, setScheme] = useState<ColorScheme>(currentScheme);
  useEffect(() => {
    const query = window.matchMedia(lightQuery);
    const update = () => setScheme(query.matches ? "light" : "dark");
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return scheme;
}

/** Reads a design token so diagrams match the current theme. */
function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value.length > 0 ? value : fallback;
}

const mermaidLoaders = new Map<ColorScheme, Promise<typeof import("mermaid").default>>();

/** Mermaid keeps one global config, so it is initialized per scheme and the
    diagrams re-render when the scheme changes. */
function loadMermaid(scheme: ColorScheme): Promise<typeof import("mermaid").default> {
  let loader = mermaidLoaders.get(scheme);
  if (loader === undefined) {
    loader = import("mermaid").then((module) => {
      const mermaid = module.default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: scheme === "dark" ? "dark" : "default",
        fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
        themeVariables: {
          background: token("--obd-surface", "#1f2833"),
          primaryColor: token("--obd-raised", "#263340"),
          primaryTextColor: token("--obd-text", "#e6edf3"),
          primaryBorderColor: token("--obd-accent", "#34d399"),
          lineColor: token("--obd-muted", "#94a3b8"),
          secondaryColor: token("--obd-raised-strong", "#2e3d4c"),
          tertiaryColor: token("--obd-bg", "#171f29"),
          textColor: token("--obd-body", "#cbd5e1"),
          mainBkg: token("--obd-raised", "#263340"),
          nodeBorder: token("--obd-accent", "#34d399"),
        },
      });
      return mermaid;
    });
    mermaidLoaders.set(scheme, loader);
  }
  return loader;
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
  const scheme = useColorScheme();

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
        const mermaid = await loadMermaid(scheme);
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
  }, [chart, idBase, scheme]);

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
