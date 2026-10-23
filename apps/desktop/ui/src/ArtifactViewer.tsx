import { useEffect, useRef, useState } from "react";
import { Layers, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { loadKnowledgeDocument, previewProjectFile } from "./backend";
import CodeView from "./CodeView";
import HtmlPreview from "./HtmlPreview";
import PdfViewer from "./PdfViewer";
import type { Artifact, ProjectFileView, SheetPreview } from "./types";

export default function ArtifactViewer({
  artifact,
  workspacePath,
  revision,
}: {
  artifact: Artifact | null;
  workspacePath: string;
  revision: number;
}) {
  const [file, setFile] = useState<ProjectFileView | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const request = useRef(0);
  const loadedKey = useRef<string | null>(null);

  useEffect(() => {
    const sequence = request.current + 1;
    request.current = sequence;
    const key =
      artifact === null
        ? ""
        : `${artifact.kind}:${artifact.uri ?? ""}:${artifact.content?.length ?? 0}`;
    const changed = loadedKey.current !== key;
    loadedKey.current = key;
    if (changed) {
      setFile(null);
      setMarkdown(null);
      setError(null);
      setLoading(true);
    }
    if (artifact === null) {
      setLoading(false);
      return;
    }
    const uri = artifact.uri ?? "";
    const load = async () => {
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
  }, [artifact, revision, workspacePath]);

  if (artifact === null) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-4 py-12 text-center">
        <Layers size={22} className="text-slate-500" />
        <p className="text-[11px] text-slate-500">Nothing open yet.</p>
        <p className="max-w-[220px] text-[10px] leading-4 text-slate-500">
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
          <span className="truncate font-mono text-[9px] text-slate-500">
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

  const previewKind: ProjectFileView["kind"] | "artifact-image" | null =
    artifact.kind === "image" ? "artifact-image" : (file?.kind ?? null);

  if (previewKind === "artifact-image" || previewKind === "image") {
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
          <p className="text-[10px] text-slate-500">No image data available.</p>
        )}
      </div>
    );
  }

  if (artifact.kind === "diff") {
    return <DiffView content={artifact.content ?? markdown ?? ""} />;
  }

  if (previewKind === "pdf") {
    return <PdfPreview file={file} title={artifact.title} />;
  }

  if (previewKind === "doc") {
    const extracted = file?.content ?? "";
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="mb-2 flex shrink-0 items-center gap-2 font-mono text-[9px] text-slate-500">
          <span>Extracted text</span>
          <span>· {formatFileSize(file?.size ?? 0)}</span>
          {file?.truncated === true && <span>· truncated</span>}
        </div>
        {extracted.trim().length === 0 ? (
          <EmptyPreview message="No text could be extracted from this document." />
        ) : (
          <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-lg border border-white/[0.06] bg-black/30 p-2 font-mono text-[10px] leading-5 text-slate-300">
            {extracted}
          </pre>
        )}
      </div>
    );
  }

  if (previewKind === "sheet") {
    return <SheetTable sheets={file?.sheets ?? []} truncated={file?.truncated ?? false} />;
  }

  if (previewKind === "audio" || previewKind === "video") {
    const source = file?.data_url ?? null;
    if (source === null) {
      return <EmptyPreview message="No inline media data is available." />;
    }
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 rounded-lg border border-white/[0.06] bg-black/30 p-3">
        {previewKind === "audio" ? (
          <audio controls src={source} className="w-full">
            <track kind="captions" />
          </audio>
        ) : (
          <video controls src={source} className="max-h-[70vh] w-full">
            <track kind="captions" />
          </video>
        )}
        <p className="font-mono text-[9px] text-slate-500">
          {file?.mime} · {formatFileSize(file?.size ?? 0)}
        </p>
      </div>
    );
  }

  if (previewKind === "binary") {
    return (
      <EmptyPreview
        message={`No inline preview for this file type (${formatFileSize(
          file?.size ?? 0,
        )}). Use "Open in system viewer" for the full file.`}
      />
    );
  }

  if (artifact.kind === "file" && markdown !== null && isHtmlArtifact(artifact)) {
    return (
      <HtmlPreview
        content={markdown}
        title={artifact.title}
        path={file?.path ?? artifact.uri}
      />
    );
  }

  if (artifact.kind === "file" && markdown !== null && !isMarkdownArtifact(artifact)) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="mb-2 flex shrink-0 items-center gap-2 font-mono text-[9px] text-slate-500">
          <span className="truncate">{file?.path ?? artifact.uri}</span>
          <span>· {formatFileSize(file?.size ?? 0)}</span>
          {file?.truncated === true && <span>· truncated</span>}
        </div>
        <CodeView code={markdown} path={file?.path ?? artifact.uri} />
      </div>
    );
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

  return <p className="text-[10px] text-slate-500">No preview content available.</p>;
}

function PdfPreview({
  file,
  title,
}: {
  file: ProjectFileView | null;
  title: string;
}) {
  const [mode, setMode] = useState<"rendered" | "text">(
    file?.data_url ? "rendered" : "text",
  );
  const extracted = file?.content ?? "";
  const canRender = typeof file?.data_url === "string";
  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          disabled={!canRender}
          onClick={() => setMode("rendered")}
          className={`rounded px-2 py-1 text-[10px] ${
            mode === "rendered"
              ? "bg-emerald-400/15 text-emerald-200"
              : "bg-white/[0.04] text-slate-400 hover:bg-white/[0.08] disabled:opacity-40"
          }`}
        >
          Rendered
        </button>
        <button
          type="button"
          onClick={() => setMode("text")}
          className={`rounded px-2 py-1 text-[10px] ${
            mode === "text"
              ? "bg-emerald-400/15 text-emerald-200"
              : "bg-white/[0.04] text-slate-400 hover:bg-white/[0.08]"
          }`}
        >
          Text
        </button>
        <span className="ml-auto font-mono text-[9px] text-slate-500">
          {formatFileSize(file?.size ?? 0)}
        </span>
      </div>
      {mode === "rendered" && canRender ? (
        <PdfViewer dataUrl={file.data_url ?? ""} title={title} />
      ) : extracted.trim().length === 0 ? (
        <EmptyPreview message="This PDF has no extractable text; it may be a scanned document. Use Open in system viewer." />
      ) : (
        <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-lg border border-white/[0.06] bg-black/30 p-2 font-mono text-[10px] leading-5 text-slate-300">
          {extracted}
        </pre>
      )}
    </div>
  );
}

function isHtmlArtifact(artifact: Artifact): boolean {
  const uri = artifact.uri ?? "";
  const extension = uri.slice(uri.lastIndexOf(".") + 1).toLowerCase();
  return extension === "html" || extension === "htm";
}

function EmptyPreview({ message }: { message: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-white/[0.08] px-4 py-12 text-center">
      <Layers size={20} className="text-slate-500" />
      <p className="max-w-[260px] text-[10px] leading-4 text-slate-500">{message}</p>
    </div>
  );
}

function formatFileSize(bytes: number): string {
  if (bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function SheetTable({
  sheets,
  truncated,
}: {
  sheets: SheetPreview[];
  truncated: boolean;
}) {
  const [active, setActive] = useState(0);
  if (sheets.length === 0) {
    return <EmptyPreview message="This spreadsheet has no readable sheets." />;
  }
  const sheet = sheets[Math.min(active, sheets.length - 1)];
  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {sheets.length > 1 && (
        <div className="flex shrink-0 flex-wrap gap-1">
          {sheets.map((item, index) => (
            <button
              key={`${item.name}-${index}`}
              type="button"
              onClick={() => setActive(index)}
              className={`rounded px-2 py-1 text-[10px] ${
                index === active
                  ? "bg-emerald-400/15 text-emerald-200"
                  : "bg-white/[0.04] text-slate-400 hover:bg-white/[0.08]"
              }`}
            >
              {item.name}
            </button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-white/[0.06]">
        <table className="min-w-full border-collapse text-[10px]">
          <tbody>
            {sheet.rows.map((row, rowIndex) => (
              <tr
                key={`${sheet.name}-row-${rowIndex}`}
                className={
                  rowIndex === 0
                    ? "bg-white/[0.06] text-slate-200"
                    : "text-slate-400 odd:bg-white/[0.02]"
                }
              >
                <th className="sticky left-0 z-[1] border-b border-white/[0.06] bg-[#1b232e] px-2 py-1 text-right font-mono text-[9px] font-normal text-slate-500">
                  {rowIndex + 1}
                </th>
                {row.map((cell, cellIndex) => (
                  <td
                    key={`${sheet.name}-${rowIndex}-${cellIndex}`}
                    className="max-w-[280px] truncate border-b border-white/[0.04] px-2 py-1 font-mono"
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(truncated || sheet.truncated) && (
        <p className="shrink-0 text-[9px] text-amber-300">
          Preview truncated to the first rows and columns. Open in system viewer for
          the full sheet.
        </p>
      )}
    </div>
  );
}

function isMarkdownArtifact(artifact: Artifact): boolean {
  const uri = artifact.uri ?? "";
  const extension = uri.slice(uri.lastIndexOf(".") + 1).toLowerCase();
  return (
    artifact.kind === "markdown" ||
    extension === "md" ||
    extension === "markdown" ||
    extension === "mdx"
  );
}

function DiffView({ content }: { content: string }) {
  if (content.length === 0) {
    return <p className="text-[10px] text-slate-500">No diff content available.</p>;
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
