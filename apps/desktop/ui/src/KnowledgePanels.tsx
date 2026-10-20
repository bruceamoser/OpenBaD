import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  BookOpen,
  Download,
  FileCode2,
  Loader2,
  Plus,
  Printer,
  Search,
  Trash2,
} from "lucide-react";

import {
  addKnowledgeNote,
  deleteKnowledgeDocument,
  downloadKnowledgeDocument,
  importKnowledgeFile,
  loadKnowledgeDocument,
  loadKnowledgeDocuments,
  loadKnowledgeStaleDocuments,
  searchProject,
} from "./backend";
import MarkdownMessage from "./MarkdownMessage";
import type {
  Artifact,
  KnowledgeDocument,
  KnowledgeDocumentSummary,
  ProjectHit,
} from "./types";

export function KnowledgePanel({
  workspacePath,
  onOpenArtifact,
  revision,
  onChanged,
}: {
  workspacePath: string;
  onOpenArtifact: (artifact: Artifact) => void;
  onTab: (tab: "artifact" | "memory") => void;
  revision: number;
  onChanged: () => void;
}) {
  const [knowledge, setKnowledge] = useState<KnowledgeDocumentSummary[]>([]);
  const [stale, setStale] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [composing, setComposing] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [confidence, setConfidence] = useState("inferred");
  const [kind, setKind] = useState("note");
  const [importing, setImporting] = useState(false);
  const [importPath, setImportPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const [printDocument, setPrintDocument] = useState<KnowledgeDocument | null>(null);

  const refresh = useCallback(async () => {
    if (workspacePath.length === 0) {
      return;
    }
    setLoading(true);
    try {
      const [documents, staleDocuments] = await Promise.all([
        loadKnowledgeDocuments(workspacePath),
        loadKnowledgeStaleDocuments(workspacePath),
      ]);
      setKnowledge(documents);
      setStale(new Set(staleDocuments));
      setError(null);
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setLoading(false);
    }
  }, [workspacePath]);

  useEffect(() => {
    setKnowledge([]);
    setStale(new Set());
    setQuery("");
    setNotice(null);
  }, [workspacePath]);

  useEffect(() => {
    void refresh();
  }, [refresh, revision]);

  useEffect(() => {
    if (printDocument === null) {
      return;
    }
    const timer = window.setTimeout(() => {
      window.print();
      window.setTimeout(() => setPrintDocument(null), 1_000);
    }, 600);
    return () => window.clearTimeout(timer);
  }, [printDocument]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle.length === 0
      ? knowledge
      : knowledge.filter(
          (document) =>
            document.title.toLowerCase().includes(needle) ||
            (document.source ?? "").toLowerCase().includes(needle) ||
            document.tags.some((tag) => tag.toLowerCase().includes(needle)),
        );
  }, [knowledge, query]);

  const open = (document: KnowledgeDocumentSummary) => {
    onOpenArtifact({
      kind: "markdown",
      title: document.title,
      uri: `knowledge:${document.id}`,
    });
  };

  const save = async () => {
    if (content.trim().length === 0) {
      return;
    }
    try {
      const document = await addKnowledgeNote(
        workspacePath,
        title,
        content,
        tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        confidence,
        kind,
      );
      setKnowledge((items) => [document, ...items]);
      setTitle("");
      setContent("");
      setTags("");
      setConfidence("inferred");
      setKind("note");
      setComposing(false);
      setNotice(`Added ${document.title}`);
      setError(null);
      onChanged();
      void refresh();
    } catch (cause) {
      setError(describeError(cause));
    }
  };

  const remove = async (document: KnowledgeDocumentSummary) => {
    try {
      await deleteKnowledgeDocument(workspacePath, document.id);
      setKnowledge((items) => items.filter((item) => item.id !== document.id));
      setNotice(`Deleted ${document.title}`);
      onChanged();
    } catch (cause) {
      setError(describeError(cause));
    }
  };

  const importFile = async (path: string) => {
    try {
      const document = await importKnowledgeFile(workspacePath, path, []);
      setKnowledge((items) => [document, ...items]);
      setImportPath("");
      setImporting(false);
      setNotice(`Imported ${document.title}`);
      setError(null);
      onChanged();
      void refresh();
    } catch (cause) {
      setError(describeError(cause));
    }
  };

  const download = async (document: KnowledgeDocumentSummary) => {
    setExportingId(document.id);
    try {
      const path = await downloadKnowledgeDocument(workspacePath, document.id);
      setNotice(path === null ? "Download cancelled" : `Saved ${path}`);
      setError(null);
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setExportingId(null);
    }
  };

  const print = async (document: KnowledgeDocumentSummary) => {
    try {
      setPrintDocument(await loadKnowledgeDocument(workspacePath, document.id));
      setNotice("Preparing print / PDF view…");
      setError(null);
    } catch (cause) {
      setError(describeError(cause));
    }
  };

  return (
    <div className="space-y-2">
      <p className="px-1 pb-1 text-[10px] leading-4 text-slate-600">
        Knowledge documents: long-form notes, decisions, references, procedures, and
        findings. They are chunked, full-text indexed, and cited by document. Use Memory
        for short atomic snippets.
      </p>
      <SearchField value={query} onChange={setQuery} label="Filter knowledge" />
      {loading && knowledge.length === 0 && (
        <p className="flex items-center gap-1.5 px-1 py-2 text-[10px] text-slate-600">
          <Loader2 size={11} className="animate-spin" /> Loading knowledge…
        </p>
      )}
      {visible.map((document) => (
        <div
          key={document.id}
          className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-2.5"
        >
          <button
            type="button"
            onClick={() => open(document)}
            className="block w-full text-left"
          >
            <p className="truncate text-[11px] text-slate-200">{document.title}</p>
            <p className="mt-0.5 truncate font-mono text-[9px] text-slate-600">
              {document.kind} · {document.chunks} chunk
              {document.chunks === 1 ? "" : "s"} · {formatBytes(document.characters)}
            </p>
            <p className="mt-1 flex items-center gap-1.5 text-[9px]">
              <span
                className={
                  document.confidence === "verified"
                    ? "rounded bg-emerald-400/15 px-1 py-0.5 text-emerald-300"
                    : document.confidence === "uncertain"
                      ? "rounded bg-amber-400/15 px-1 py-0.5 text-amber-300"
                      : "rounded bg-white/[0.06] px-1 py-0.5 text-slate-400"
                }
              >
                {document.confidence}
              </span>
              {stale.has(document.id) && (
                <span className="rounded bg-amber-400/15 px-1 py-0.5 text-amber-300">
                  source changed
                </span>
              )}
              {document.review_status !== "accepted" && (
                <span className="rounded bg-sky-400/15 px-1 py-0.5 text-sky-300">
                  {document.review_status}
                </span>
              )}
            </p>
            {document.tags.length > 0 && (
              <p className="mt-1 truncate text-[9px] text-emerald-300/70">
                {document.tags.join(" · ")}
              </p>
            )}
          </button>
          <div className="mt-1.5 flex items-center justify-between gap-2">
            <span className="min-w-0 flex-1 truncate font-mono text-[9px] text-slate-700">
              {document.source ?? "project note"}
            </span>
            <div className="flex shrink-0 items-center gap-0.5">
              <button
                type="button"
                aria-label={`Download ${document.title}`}
                title="Download Markdown"
                onClick={() => void download(document)}
                disabled={exportingId === document.id}
                className="rounded p-1 text-slate-600 hover:bg-white/5 hover:text-emerald-300 disabled:opacity-40"
              >
                {exportingId === document.id ? (
                  <Loader2 size={11} className="animate-spin" />
                ) : (
                  <Download size={11} />
                )}
              </button>
              <button
                type="button"
                aria-label={`Export ${document.title} as PDF`}
                title="Print / save as PDF"
                onClick={() => void print(document)}
                className="rounded p-1 text-slate-600 hover:bg-white/5 hover:text-emerald-300"
              >
                <Printer size={11} />
              </button>
              <button
                type="button"
                aria-label={`Delete ${document.title}`}
                onClick={() => void remove(document)}
                className="rounded p-1 text-slate-600 hover:bg-white/5 hover:text-amber-300"
              >
                <Trash2 size={11} />
              </button>
            </div>
          </div>
        </div>
      ))}
      {!loading && visible.length === 0 && (
        <p className="px-1 py-2 text-[11px] text-slate-600">
          {knowledge.length === 0
            ? "No knowledge yet. Add a note or import a file."
            : "No matching documents."}
        </p>
      )}
      <div className="flex gap-1.5">
        <button
          type="button"
          onClick={() => setComposing((value) => !value)}
          className="flex flex-1 items-center justify-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1.5 text-[10px] text-slate-300 hover:bg-white/[0.06]"
        >
          <Plus size={11} /> Note
        </button>
        <button
          type="button"
          onClick={() => setImporting((value) => !value)}
          className="flex flex-1 items-center justify-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1.5 text-[10px] text-slate-300 hover:bg-white/[0.06]"
        >
          <BookOpen size={11} /> Import
        </button>
      </div>
      {importing && (
        <div className="space-y-1.5 rounded-lg border border-white/[0.08] bg-black/20 p-2.5">
          <input
            value={importPath}
            onChange={(event) => setImportPath(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && importPath.trim().length > 0) {
                void importFile(importPath.trim());
              }
            }}
            aria-label="Knowledge import path"
            placeholder="/home/you/docs/architecture.md"
            className="w-full rounded-md border border-white/10 bg-black/30 px-2 py-1.5 font-mono text-[10px] text-slate-200 outline-none focus:border-emerald-400/40"
          />
          <button
            type="button"
            onClick={() => void importFile(importPath.trim())}
            disabled={importPath.trim().length === 0}
            className="w-full rounded-md border border-white/10 bg-white/[0.04] px-2 py-1.5 text-[10px] text-slate-200 hover:bg-white/[0.08] disabled:opacity-30"
          >
            Import UTF-8 file
          </button>
        </div>
      )}
      {composing && (
        <div className="space-y-1.5 rounded-lg border border-white/[0.08] bg-black/20 p-2.5">
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            aria-label="Knowledge title"
            placeholder="Title"
            className="w-full rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none focus:border-emerald-400/40"
          />
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            aria-label="Knowledge content"
            rows={5}
            placeholder="Durable context the assistant should retrieve later…"
            className="w-full resize-y rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-[10px] leading-4 text-slate-200 outline-none focus:border-emerald-400/40"
          />
          <input
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            aria-label="Knowledge tags"
            placeholder="tags, comma separated"
            className="w-full rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none focus:border-emerald-400/40"
          />
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value)}
            aria-label="Knowledge kind"
            className="w-full rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none focus:border-emerald-400/40"
          >
            <option value="note">Note</option>
            <option value="decision">Decision</option>
            <option value="reference">Reference</option>
            <option value="gotcha">Gotcha</option>
            <option value="procedure">Procedure</option>
            <option value="glossary">Glossary</option>
            <option value="finding">Finding</option>
            <option value="question">Question</option>
          </select>
          <select
            value={confidence}
            onChange={(event) => setConfidence(event.target.value)}
            aria-label="Knowledge confidence"
            className="w-full rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none focus:border-emerald-400/40"
          >
            <option value="inferred">Inferred — reasonable, not verified</option>
            <option value="verified">Verified — checked against source</option>
            <option value="uncertain">Uncertain — needs confirmation</option>
          </select>
          <button
            type="button"
            onClick={() => void save()}
            disabled={content.trim().length === 0}
            className="w-full rounded-md bg-emerald-400 px-2 py-1.5 text-[10px] font-medium text-emerald-950 hover:bg-emerald-300 disabled:opacity-30"
          >
            Save to project knowledge
          </button>
        </div>
      )}
      {notice !== null && <p className="text-[10px] text-emerald-300">{notice}</p>}
      {error !== null && <p className="text-[10px] text-amber-300">{error}</p>}
      {printDocument !== null &&
        createPortal(
          <div className="knowledge-print">
            <p className="knowledge-print-kicker">OpenBaD project knowledge</p>
            <h1>{printDocument.summary.title}</h1>
            <p className="knowledge-print-meta">
              {printDocument.summary.kind} · {printDocument.summary.confidence}
              {printDocument.summary.tags.length > 0 &&
                ` · ${printDocument.summary.tags.join(", ")}`}
            </p>
            <MarkdownMessage text={printDocument.content} />
          </div>,
          document.body,
        )}
    </div>
  );
}

export function RetrievalPanel({
  workspacePath,
  onOpenArtifact,
  onTab,
}: {
  workspacePath: string;
  onOpenArtifact: (artifact: Artifact) => void;
  onTab: (tab: "artifact" | "memory") => void;
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<ProjectHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    if (query.trim().length === 0) {
      return;
    }
    setLoading(true);
    try {
      setHits(await searchProject(workspacePath, query.trim()));
      setError(null);
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setLoading(false);
    }
  };

  const openHit = (hit: ProjectHit) => {
    if (hit.source === "knowledge") {
      onOpenArtifact({
        kind: "markdown",
        title: hit.title,
        uri: `knowledge:${hit.locator.split("#")[0]}`,
      });
    } else if (hit.source === "code") {
      onOpenArtifact({
        kind: "file",
        title: hit.title.split("/").at(-1) ?? hit.title,
        uri: hit.locator,
      });
    } else {
      onTab("memory");
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex gap-1.5">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              void search();
            }
          }}
          aria-label="Retrieval query"
          placeholder="Ask the project corpus…"
          className="min-w-0 flex-1 rounded-md border border-white/10 bg-black/30 px-2.5 py-1.5 text-[11px] text-slate-200 outline-none focus:border-emerald-400/40"
        />
        <button
          type="button"
          onClick={() => void search()}
          disabled={loading || query.trim().length === 0}
          className="rounded-md bg-emerald-400 px-2.5 py-1.5 text-[10px] font-medium text-emerald-950 hover:bg-emerald-300 disabled:opacity-30"
        >
          {loading ? "…" : "Search"}
        </button>
      </div>
      <p className="px-1 text-[10px] leading-4 text-slate-600">
        Retrieval fuses knowledge chunks, indexed code, and durable memory. Hits
        carry locators the assistant can cite.
      </p>
      {hits === null && (
        <p className="px-1 py-2 text-[11px] text-slate-600">
          Run a query to inspect what the project can retrieve.
        </p>
      )}
      {hits !== null && hits.length === 0 && (
        <p className="px-1 py-2 text-[11px] text-slate-600">No matches.</p>
      )}
      {hits?.map((hit) => (
        <button
          key={`${hit.source}-${hit.locator}`}
          type="button"
          onClick={() => openHit(hit)}
          className="block w-full rounded-lg border border-white/[0.07] bg-white/[0.02] p-2.5 text-left hover:border-white/[0.14] hover:bg-white/[0.04]"
        >
          <div className="flex items-center gap-1.5">
            <span
              className={`rounded px-1 py-0.5 text-[8px] uppercase tracking-wide ${
                hit.source === "knowledge"
                  ? "bg-sky-400/10 text-sky-300"
                  : hit.source === "memory"
                    ? "bg-violet-400/10 text-violet-300"
                    : "bg-emerald-400/10 text-emerald-300"
              }`}
            >
              {hit.source}
            </span>
            {hit.source === "code" ? (
              <FileCode2 size={10} className="text-slate-600" />
            ) : null}
            <span className="truncate text-[10px] text-slate-300">{hit.title}</span>
          </div>
          <p className="mt-1 line-clamp-3 text-[10px] leading-4 text-slate-500">
            {hit.snippet}
          </p>
          <p className="mt-1 truncate font-mono text-[8px] text-slate-700">
            {hit.locator}
          </p>
        </button>
      ))}
      {error !== null && <p className="text-[10px] text-amber-300">{error}</p>}
    </div>
  );
}

function SearchField({
  value,
  onChange,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
}) {
  return (
    <label className="flex items-center gap-2 rounded-md border border-white/[0.07] bg-black/20 px-2.5 py-1.5">
      <Search size={11} className="shrink-0 text-slate-600" />
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Filter knowledge"
        aria-label={label}
        className="min-w-0 flex-1 bg-transparent text-[10px] text-slate-300 outline-none placeholder:text-slate-600"
      />
    </label>
  );
}

function describeError(cause: unknown): string {
  if (cause instanceof Error) {
    return cause.message;
  }
  if (typeof cause === "string") {
    return cause;
  }
  return "The project area request failed.";
}

function formatBytes(bytes: number): string {
  if (bytes < 1_024) {
    return `${bytes} B`;
  }
  if (bytes < 1_048_576) {
    return `${(bytes / 1_024).toFixed(1)} KB`;
  }
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}
