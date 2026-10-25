import { FileCode2, GitBranch, Globe, Image, Layers, Sparkles, X } from "lucide-react";

import ArtifactViewer from "./ArtifactViewer";
import GitRepoScreen from "./GitRepoScreen";
import OpenExternalButton from "./OpenExternalButton";
import RevealButton from "./RevealButton";
import type { Artifact, PreviewTarget } from "./types";

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
  target,
  workspacePath,
  revision,
  width,
  reserved,
  onResizeStart,
  onClose,
  onArtifact,
  onWorkspaceChanged,
}: {
  target: PreviewTarget;
  workspacePath: string;
  revision: number;
  width: number;
  reserved: number;
  onResizeStart: (event: React.PointerEvent<HTMLDivElement>) => void;
  onClose: () => void;
  onArtifact: (artifact: Artifact | null) => void;
  onWorkspaceChanged: () => void;
}) {
  const artifact = target.kind === "artifact" ? target.artifact : null;
  const title =
    target.kind === "artifact" ? target.artifact.title : target.repoName;
  const subtitle =
    target.kind === "artifact"
      ? (target.artifact.uri ?? target.artifact.kind)
      : `source control · ${target.repoPath}`;
  const revealable =
    artifact !== null &&
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
        data-preview-kind={target.kind}
        className="flex min-h-0 shrink-0 flex-col border-l border-white/[0.08] bg-[#1b232e]"
        style={{
          width: `clamp(300px, ${width}px, calc(100vw - ${reserved}px))`,
        }}
      >
        <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-white/[0.06] px-3.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0 text-slate-500">
              {artifact === null ? <GitBranch size={12} /> : kindIcon(artifact.kind)}
            </span>
            <div className="min-w-0">
              <p className="truncate text-[12px] font-medium text-slate-200">
                {title}
              </p>
              <p className="truncate font-mono text-[9px] text-slate-500">
                {subtitle}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {revealable && artifact !== null && artifact.uri !== undefined && (
              <>
                <OpenExternalButton
                  workspacePath={workspacePath}
                  path={artifact.uri}
                  className="p-1.5 hover:bg-white/10"
                />
                <RevealButton
                  workspacePath={workspacePath}
                  path={artifact.uri}
                  className="p-1.5 hover:bg-white/10"
                />
              </>
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
          {target.kind === "artifact" ? (
            <ArtifactViewer
              artifact={target.artifact}
              workspacePath={workspacePath}
              revision={revision}
            />
          ) : (
            <GitRepoScreen
              workspacePath={target.workspacePath}
              repoPath={target.repoPath}
              repoName={target.repoName}
              onArtifact={onArtifact}
              onWorkspaceChanged={onWorkspaceChanged}
            />
          )}
        </div>
      </aside>
    </>
  );
}
