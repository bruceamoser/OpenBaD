import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Loader2, RefreshCw, Server } from "lucide-react";

import { openServiceUrl, servicesStatus } from "./backend";
import type { ServiceStatus } from "./types";

function describeError(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function statusTone(status: string): string {
  if (status === "running") return "bg-emerald-400";
  if (status === "missing") return "bg-rose-400";
  return "bg-amber-400";
}

export default function ServicesSettings() {
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setServices(await servicesStatus());
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-[13px] font-medium text-slate-200">Tools &amp; services</h3>
          <p className="mt-1 text-[11px] leading-4 text-slate-500">
            Local services the agent can use. Missing entries show the install hint;
            URL-backed services can be overridden with the listed environment variable.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/10 px-2 py-1 text-[10px] text-slate-300 hover:bg-white/5 disabled:opacity-40"
        >
          {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
          Refresh
        </button>
      </div>

      {error !== null && (
        <p className="rounded-md border border-rose-400/20 bg-rose-400/[0.06] px-2 py-1.5 text-[10px] text-rose-200">
          {error}
        </p>
      )}

      <div className="space-y-2">
        {services.map((service) => (
          <div
            key={service.id}
            className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-2.5"
          >
            <div className="flex items-center gap-2">
              <span className={`h-1.5 w-1.5 rounded-full ${statusTone(service.status)}`} />
              <Server size={12} className="text-slate-500" />
              <p className="text-[11px] font-medium text-slate-200">{service.name}</p>
              <span className="ml-auto font-mono text-[9px] uppercase text-slate-500">
                {service.status}
              </span>
            </div>
            <p className="mt-1 text-[10px] leading-4 text-slate-500">{service.description}</p>
            <p className="mt-1 font-mono text-[9px] text-slate-500">{service.detail}</p>
            {service.url !== null && (
              <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[9px] text-slate-500">
                <span className="truncate">{service.url}</span>
                <button
                  type="button"
                  onClick={() => void openServiceUrl(service.url ?? "")}
                  className="inline-flex items-center gap-0.5 text-emerald-300 hover:text-emerald-200"
                >
                  <ExternalLink size={9} /> Open
                </button>
                {service.envVar !== null && (
                  <span className="rounded bg-black/20 px-1 py-0.5">{service.envVar}</span>
                )}
              </div>
            )}
            {service.status !== "running" && (
              <p className="mt-1 text-[10px] leading-4 text-amber-200/80">{service.installHint}</p>
            )}
          </div>
        ))}
        {services.length === 0 && !loading && (
          <p className="rounded-lg border border-white/[0.05] px-2 py-3 text-center text-[10px] text-slate-500">
            No service information available.
          </p>
        )}
      </div>
    </div>
  );
}
