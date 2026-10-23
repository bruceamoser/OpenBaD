import { useState } from "react";
import type { MouseEvent } from "react";
import { CircleAlert, ExternalLink } from "lucide-react";

import { openPath } from "./backend";

export default function OpenExternalButton({
  workspacePath,
  path,
  label = "Open in system viewer",
  className,
}: {
  workspacePath: string;
  path: string;
  label?: string;
  className?: string;
}) {
  const [error, setError] = useState<string | null>(null);

  const open = async (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    setError(null);
    try {
      await openPath(workspacePath, path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  if (error !== null) {
    return (
      <span
        role="alert"
        title={error}
        aria-label={`Could not open the file: ${error}`}
        className={`inline-flex shrink-0 items-center text-amber-300 ${className ?? ""}`}
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
      onClick={(event) => void open(event)}
      className={`shrink-0 rounded p-0.5 text-slate-500 hover:bg-white/10 hover:text-slate-200 ${className ?? ""}`}
    >
      <ExternalLink size={11} />
    </button>
  );
}
