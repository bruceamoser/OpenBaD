import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import workerSource from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = workerSource;

const MAX_RENDERED_PAGES = 60;

function decodeDataUrl(dataUrl: string): Uint8Array {
  const separator = dataUrl.indexOf(",");
  const base64 = separator >= 0 ? dataUrl.slice(separator + 1) : dataUrl;
  const binary = window.atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

export default function PdfViewer({
  dataUrl,
  title,
}: {
  dataUrl: string;
  title: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [pages, setPages] = useState(0);
  const [rendering, setRendering] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let loadingTask: ReturnType<typeof getDocument> | null = null;
    let pdf: PDFDocumentProxy | null = null;

    const render = async () => {
      setRendering(true);
      setError(null);
      try {
        loadingTask = getDocument({ data: decodeDataUrl(dataUrl) });
        pdf = await loadingTask.promise;
        if (cancelled) {
          return;
        }
        setPages(pdf.numPages);
        const container = containerRef.current;
        if (container === null) {
          return;
        }
        container.replaceChildren();
        const width = Math.max(320, container.clientWidth - 16);
        const ratio = window.devicePixelRatio || 1;
        const limit = Math.min(pdf.numPages, MAX_RENDERED_PAGES);
        for (let number = 1; number <= limit; number += 1) {
          const page = await pdf.getPage(number);
          if (cancelled) {
            return;
          }
          const base = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: width / base.width });
          const canvas = window.document.createElement("canvas");
          canvas.width = Math.floor(viewport.width * ratio);
          canvas.height = Math.floor(viewport.height * ratio);
          canvas.style.width = `${Math.floor(viewport.width)}px`;
          canvas.style.height = `${Math.floor(viewport.height)}px`;
          canvas.className =
            "mx-auto mb-2 rounded border border-obd-line bg-obd-canvas shadow-lg shadow-black/30";
          container.appendChild(canvas);
          await page.render({
            canvas,
            viewport,
            transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
          }).promise;
          if (cancelled) {
            return;
          }
        }
        setRendering(false);
      } catch (cause) {
        if (!cancelled) {
          setError(
            cause instanceof Error ? cause.message : "The PDF could not be rendered.",
          );
          setRendering(false);
        }
      }
    };

    void render();
    return () => {
      cancelled = true;
      void loadingTask?.destroy();
    };
  }, [dataUrl]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex shrink-0 items-center gap-2 font-mono text-[9px] text-obd-subtle">
        <span>{title}</span>
        {pages > 0 && <span>· {pages} page{pages === 1 ? "" : "s"}</span>}
        {pages > MAX_RENDERED_PAGES && (
          <span>· first {MAX_RENDERED_PAGES} rendered</span>
        )}
        {rendering && (
          <span className="flex items-center gap-1 text-obd-subtle">
            <Loader2 size={10} className="animate-spin" /> rendering
          </span>
        )}
      </div>
      {error !== null && (
        <p role="alert" className="text-[10px] leading-4 text-obd-warning">
          {error}
        </p>
      )}
      <div className="relative min-h-0 flex-1 overflow-auto rounded-lg bg-obd-inset p-2">
        <div ref={containerRef} data-testid="pdf-pages" />
        {rendering && pages === 0 && error === null && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center gap-2 text-[11px] text-obd-subtle">
            <Loader2 size={13} className="animate-spin" /> Loading PDF…
          </div>
        )}
      </div>
    </div>
  );
}
