import { FileCode2, Image, Layers, Sparkles, X, Globe } from "lucide-react";

import ArtifactViewer from "./ArtifactViewer";
import RevealButton from "./RevealButton";
import type { Artifact } from "./types";

function kindIcon(kind: Artifact["kind"]) {
  switch (kind) {
    case "web":
      return <Globe size={12} />;
    case "image":
      return <Image size={12} />;
    case "file":
      return <FileCode2 size={12} />;
    case "diff":
    case "memory":
    case "markdown":
      return <Sparkles size={12} />;
    default:
      return <Layers size={12} />;
  }
}

export default function PreviewPane({
  artifact,
  workspacePath,
  revision,
  width,
  onResizeStart,
  onClose,
}: {
  artifact: Artifact;
  workspacePath: string;
  revision: number;
  width: number;
  onResizeStart: (event: React.PointerEvent<HTMLDivElement>) => void;
  onClose: () => void;
}) {
  const revealable =
    artifact.kind === "file" &&
    artifact.uri !== undefined &&
    !artifact.uri.startsWith("http://") &&
    !artifact.uri.startsWith("https://") &&
    !artifact.uri.startsWith("data:");

  return (
    <>
      <div
        role="separator"
        aria-label="Resize preview pane"
        aria-orientation="vertical"
        onPointerDown={onResizeStart}
        className="w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-emerald-400/30"
      />
      <aside
        aria-label="Preview"
        data-testid="preview-pane"
        className="flex min-h-0 shrink-0 flex-col border-l border-white/[0.08] bg-[#1b232e]"
        style={{
          width: `clamp(300px, ${width}px, calc(100vw - 720px))`,
        }}
      >
        <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-white/[0.06] px-3.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0 text-slate-500">{kindIcon(artifact.kind)}</span>
            <div className="min-w-0">
              <p className="truncate text-[12px] font-medium text-slate-200">
                {artifact.title}
              </p>
              <p className="truncate font-mono text-[9px] text-slate-500">
                {artifact.uri ?? artifact.kind}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {revealable && artifact.uri !== undefined && (
              <RevealButton
                workspacePath={workspacePath}
                path={artifact.uri}
                className="p-1.5 hover:bg-white/10"
              />
            )}
            <button
              type="button"
              aria-label="Close preview"
              onClick={onClose}
              className="rounded-md p-1.5 text-slate-500 hover:bg-white/5 hover:text-slate-300"
            >
              <X size={15} />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-3">
          <ArtifactViewer
            artifact={artifact}
            workspacePath={workspacePath}
            revision={revision}
          />
        </div>
      </aside>
    </>
  );
}
