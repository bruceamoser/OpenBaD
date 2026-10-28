import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Loader2, RefreshCw, Server } from "lucide-react";

import { openServiceUrl, servicesStatus } from "./backend";
import type { ServiceStatus } from "./types";

function describeError(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function statusTone(status: string): string {
  if (status === "running") return "bg-obd-accent";
  if (status === "missing") return "bg-obd-danger";
  return "bg-obd-warning";
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
          <h3 className="text-[13px] font-medium text-obd-text">Tools &amp; services</h3>
          <p className="mt-1 text-[11px] leading-4 text-obd-subtle">
            Local services the agent can use. Missing entries show the install hint;
            URL-backed services can be overridden with the listed environment variable.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-obd-line px-2 py-1 text-[10px] text-obd-body hover:bg-obd-raised disabled:opacity-40"
        >
          {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
          Refresh
        </button>
      </div>

      {error !== null && (
        <p className="rounded-md border border-obd-danger/20 bg-obd-danger/[0.06] px-2 py-1.5 text-[10px] text-obd-danger">
          {error}
        </p>
      )}

      <div className="space-y-2">
        {services.map((service) => (
          <div
            key={service.id}
            className="rounded-lg border border-obd-line bg-obd-raised p-2.5"
          >
            <div className="flex items-center gap-2">
              <span className={`h-1.5 w-1.5 rounded-full ${statusTone(service.status)}`} />
              <Server size={12} className="text-obd-subtle" />
              <p className="text-[11px] font-medium text-obd-text">{service.name}</p>
              <span className="ml-auto font-mono text-[9px] uppercase text-obd-subtle">
                {service.status}
              </span>
            </div>
            <p className="mt-1 text-[10px] leading-4 text-obd-subtle">{service.description}</p>
            <p className="mt-1 font-mono text-[9px] text-obd-subtle">{service.detail}</p>
            {service.url !== null && (
              <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[9px] text-obd-subtle">
                <span className="truncate">{service.url}</span>
                <button
                  type="button"
                  onClick={() => void openServiceUrl(service.url ?? "")}
                  className="inline-flex items-center gap-0.5 text-obd-accent hover:text-obd-accent"
                >
                  <ExternalLink size={9} /> Open
                </button>
                {service.envVar !== null && (
                  <span className="rounded bg-obd-inset px-1 py-0.5">{service.envVar}</span>
                )}
              </div>
            )}
            {service.status !== "running" && (
              <p className="mt-1 text-[10px] leading-4 text-obd-warning/80">{service.installHint}</p>
            )}
          </div>
        ))}
        {services.length === 0 && !loading && (
          <p className="rounded-lg border border-obd-line px-2 py-3 text-center text-[10px] text-obd-subtle">
            No service information available.
          </p>
        )}
      </div>
    </div>
  );
}
