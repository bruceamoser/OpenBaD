import { useState } from "react";
import type { MouseEvent } from "react";
import { CircleAlert, FolderOpen } from "lucide-react";

import { revealPath } from "./backend";

export default function RevealButton({
  workspacePath,
  path,
  label = "Show in file manager",
  className,
}: {
  workspacePath: string;
  path: string;
  label?: string;
  className?: string;
}) {
  const [error, setError] = useState<string | null>(null);

  const reveal = async (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    setError(null);
    try {
      await revealPath(workspacePath, path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  if (error !== null) {
    return (
      <span
        role="alert"
        title={error}
        aria-label={`Could not open the file manager: ${error}`}
        className={`inline-flex shrink-0 items-center text-obd-warning ${className ?? ""}`}
      >
        <CircleAlert size={11} />
      </span>
    );
  }

  return (
    <button
      type="button"
      title={`${label}: ${path}`}
      aria-label={label}
      onClick={(event) => void reveal(event)}
      className={`shrink-0 rounded p-0.5 text-obd-subtle hover:bg-obd-raised-strong hover:text-obd-text ${className ?? ""}`}
    >
      <FolderOpen size={11} />
    </button>
  );
}
