import { useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Download,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { checkForUpdates, installUpdate } from "./backend";
import type { InstallOutcome, UpdateStatus } from "./types";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes <= 0) {
    return "";
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

function outcomeMessage(outcome: InstallOutcome): string {
  switch (outcome.kind) {
    case "app_image_replaced":
      return "Update installed. OpenBaD is restarting with the new version.";
    case "installer_launched":
      return `The ${outcome.installer} installer was saved to ${outcome.path} and opened.`;
    case "installer_saved":
      return `The ${outcome.installer} installer was saved to ${outcome.path}. Open it to finish the update.`;
    default:
      return "The update finished.";
  }
}

export default function UpdatePanel({
  currentVersion,
}: {
  currentVersion: string | null;
}) {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<InstallOutcome | null>(null);

  const check = async () => {
    setChecking(true);
    setError(null);
    setOutcome(null);
    try {
      setStatus(await checkForUpdates());
    } catch (checkError) {
      setStatus(null);
      setError(messageOf(checkError));
    } finally {
      setChecking(false);
    }
  };

  const install = async (version: string) => {
    setInstalling(true);
    setError(null);
    try {
      setOutcome(await installUpdate(version));
    } catch (installError) {
      setError(messageOf(installError));
    } finally {
      setInstalling(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] text-slate-500">
          Current version:{" "}
          <span className="font-mono text-slate-300">
            {currentVersion ?? "unknown"}
          </span>
        </p>
        <button
          type="button"
          aria-label="Check for updates"
          onClick={() => void check()}
          disabled={checking || installing}
          className="flex items-center gap-1.5 rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-[11px] text-slate-200 hover:bg-white/10 disabled:opacity-40"
        >
          {checking ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <RefreshCw size={12} />
          )}
          Check for updates
        </button>
      </div>

      {error !== null && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-rose-400/20 bg-rose-400/[0.06] px-3 py-2 text-[11px] text-rose-200"
        >
          <CircleAlert size={13} className="mt-0.5 shrink-0" />
          <span className="min-w-0 break-words">{error}</span>
        </div>
      )}

      {outcome !== null && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-md border border-emerald-400/20 bg-emerald-400/[0.06] px-3 py-2 text-[11px] text-emerald-200"
        >
          <CheckCircle2 size={13} className="mt-0.5 shrink-0" />
          <span className="min-w-0 break-words">{outcomeMessage(outcome)}</span>
        </div>
      )}

      {status !== null && outcome === null && (
        <>
          {status.kind === "up_to_date" && (
            <p
              role="status"
              className="flex items-center gap-2 text-[11px] text-emerald-200"
            >
              <CheckCircle2 size={13} /> OpenBaD {status.latest} is up to date.
            </p>
          )}
          {status.kind === "no_release" && (
            <p role="status" className="text-[11px] text-slate-400">
              No releases have been published yet.
            </p>
          )}
          {status.kind === "available" && (
            <div className="space-y-2 rounded-lg border border-emerald-400/20 bg-emerald-400/[0.04] p-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[12px] text-emerald-200">
                  OpenBaD {status.latest} is available
                </p>
                <span className="font-mono text-[10px] text-slate-500">
                  {status.asset.name}
                  {status.asset.size === null
                    ? ""
                    : ` · ${formatBytes(status.asset.size)}`}
                </span>
              </div>
              {status.release.notes.trim().length > 0 && (
                <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md border border-white/10 bg-black/20 px-2.5 py-2 font-sans text-[11px] text-slate-400">
                  {status.release.notes}
                </pre>
              )}
              <button
                type="button"
                aria-label={`Download and install ${status.latest}`}
                onClick={() => void install(status.latest)}
                disabled={installing}
                className="flex items-center gap-1.5 rounded-md bg-emerald-400 px-3 py-1.5 text-[11px] font-medium text-emerald-950 hover:bg-emerald-300 disabled:opacity-40"
              >
                {installing ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <Download size={12} />
                )}
                {installing ? "Downloading and installing…" : "Download and install"}
              </button>
              <p className="text-[10px] text-slate-500">
                AppImages are replaced in place and the app restarts. Platform
                installers are saved and opened for you. Release checksums are
                verified when published.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
