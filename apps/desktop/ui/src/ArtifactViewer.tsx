import { useEffect, useRef, useState } from "react";
import { Layers, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { loadKnowledgeDocument, previewProjectFile } from "./backend";
import type { Artifact, ProjectFileView } from "./types";

export default function ArtifactViewer({
  artifact,
  workspacePath,
}: {
  artifact: Artifact | null;
  workspacePath: string;
}) {
  const [file, setFile] = useState<ProjectFileView | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const request = useRef(0);

  useEffect(() => {
    const sequence = request.current + 1;
    request.current = sequence;
    setFile(null);
    setMarkdown(null);
    setError(null);
    if (artifact === null) {
      return;
    }
    const uri = artifact.uri ?? "";
    const load = async () => {
      setLoading(true);
      try {
        if (uri.startsWith("knowledge:")) {
          const document = await loadKnowledgeDocument(
            workspacePath,
            uri.slice("knowledge:".length),
          );
          if (request.current === sequence) {
            setMarkdown(document.content);
          }
        } else if (
          artifact.content !== undefined &&
          artifact.content.length > 0
        ) {
          if (request.current === sequence) {
            setMarkdown(artifact.content);
          }
        } else if (
          (artifact.kind === "file" ||
            artifact.kind === "image" ||
            artifact.kind === "markdown") &&
          uri.length > 0 &&
          !uri.startsWith("http://") &&
          !uri.startsWith("https://") &&
          !uri.startsWith("data:")
        ) {
          const view = await previewProjectFile(workspacePath, uri);
          if (request.current === sequence) {
            setFile(view);
            if (view.kind === "text" && artifact.kind !== "image") {
              setMarkdown(view.content ?? "");
            }
          }
        }
      } catch (cause) {
        if (request.current === sequence) {
          setError(describeError(cause));
        }
      } finally {
        if (request.current === sequence) {
          setLoading(false);
        }
      }
    };
    void load();
  }, [artifact, workspacePath]);

  if (artifact === null) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-4 py-12 text-center">
        <Layers size={22} className="text-slate-700" />
        <p className="text-[11px] text-slate-500">Nothing open yet.</p>
        <p className="max-w-[220px] text-[10px] leading-4 text-slate-600">
          Open a file, knowledge document, or retrieval hit. Assistant-presented
          artifacts appear here too.
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 py-12 text-[11px] text-slate-500">
        <Loader2 size={13} className="animate-spin" /> Loading preview…
      </div>
    );
  }

  if (error !== null) {
    return <p className="p-2 text-[10px] leading-4 text-amber-300">{error}</p>;
  }

  if (artifact.kind === "web") {
    return (
      <div className="flex h-full min-h-[460px] flex-col">
        <div className="mb-2 flex shrink-0 items-center gap-2">
          <span className="truncate font-mono text-[9px] text-slate-600">
            {artifact.uri}
          </span>
          <a
            href={artifact.uri}
            target="_blank"
            rel="noreferrer"
            className="ml-auto shrink-0 text-[9px] text-emerald-300 hover:underline"
          >
            Open externally
          </a>
        </div>
        {artifact.uri !== undefined && (
          <iframe
            title={artifact.title}
            src={artifact.uri}
            sandbox="allow-scripts allow-forms allow-popups"
            referrerPolicy="no-referrer"
            className="min-h-0 w-full flex-1 rounded-lg border border-white/10 bg-white"
          />
        )}
      </div>
    );
  }

  if (artifact.kind === "image") {
    const source =
      file?.data_url ??
      (artifact.uri?.startsWith("http") || artifact.uri?.startsWith("data:")
        ? artifact.uri
        : null);
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-white/[0.06] bg-black/30 p-2">
        {source !== null && source !== undefined ? (
          <img
            src={source}
            alt={artifact.title}
            className="max-h-[70vh] max-w-full object-contain"
          />
        ) : (
          <p className="text-[10px] text-slate-600">No image data available.</p>
        )}
      </div>
    );
  }

  if (artifact.kind === "diff") {
    return <DiffView content={artifact.content ?? markdown ?? ""} />;
  }

  if (markdown !== null) {
    return (
      <div className="markdown-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ children, ...props }) => (
              <a {...props} target="_blank" rel="noreferrer">
                {children}
              </a>
            ),
          }}
        >
          {markdown}
        </ReactMarkdown>
      </div>
    );
  }

  return <p className="text-[10px] text-slate-600">No preview content available.</p>;
}

function DiffView({ content }: { content: string }) {
  if (content.length === 0) {
    return <p className="text-[10px] text-slate-600">No diff content available.</p>;
  }
  return (
    <pre className="overflow-x-auto rounded-lg border border-white/[0.06] bg-black/30 p-2 font-mono text-[9px] leading-4">
      {content.split("\n").map((line, index) => (
        <span
          key={`${index}-${line.slice(0, 12)}`}
          className={
            line.startsWith("+")
              ? "block text-emerald-300"
              : line.startsWith("-")
                ? "block text-rose-300"
                : line.startsWith("@@")
                  ? "block text-sky-300"
                  : "block text-slate-500"
          }
        >
          {line.length === 0 ? " " : line}
        </span>
      ))}
    </pre>
  );
}

function describeError(cause: unknown): string {
  if (cause instanceof Error) {
    return cause.message;
  }
  if (typeof cause === "string") {
    return cause;
  }
  return "The preview could not be loaded.";
}
