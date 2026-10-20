/**
 * OpenBaD Corsair MCP Sidecar
 *
 * Reads plugin configuration from peripherals.yaml and starts a Corsair
 * MCP stdio server.  The OpenBaD daemon communicates with this process
 * via stdio (mcp_bridge) for egress and HTTP webhooks for ingress.
 *
 * Usage:
 *   OPENBAD_PERIPHERALS_CONFIG=/etc/openbad/peripherals.yaml node dist/corsair.js
 */

import { readFileSync } from "node:fs";
import { parse } from "yaml";

// Corsair imports — resolved after `npm install`.
// @ts-ignore — packages may not be installed in the dev environment yet.
import { createCorsair } from "corsair";
// @ts-ignore
import { runStdioMcpServer } from "@corsair-dev/mcp";

/* ------------------------------------------------------------------ */
/*  Configuration                                                      */
/* ------------------------------------------------------------------ */

interface PluginEntry {
  name: string;
  enabled: boolean;
  credentials_file?: string;
}

interface PeripheralsConfig {
  corsair?: {
    entry_point?: string;
    webhook_secret?: string;
    plugins?: PluginEntry[];
  };
}

function loadConfig(): PeripheralsConfig {
  const configPath =
    process.env.OPENBAD_PERIPHERALS_CONFIG ??
    "/etc/openbad/peripherals.yaml";
  const raw = readFileSync(configPath, "utf-8");
  return (parse(raw) as PeripheralsConfig) ?? {};
}

/* ------------------------------------------------------------------ */
/*  Dynamic plugin loader                                              */
/* ------------------------------------------------------------------ */

/**
 * Dynamically import Corsair plugin functions by name.
 *
 * Each enabled plugin (e.g. "discord") is expected to be importable as
 * `@corsair-dev/discord` and to export a default factory function.
 */
async function loadPlugins(entries: PluginEntry[]): Promise<any[]> {
  const loaded: any[] = [];
  for (const entry of entries) {
    if (!entry.enabled) continue;
    try {
      // Corsair packages follow @corsair-dev/{name} or corsair-{name}
      // Try the scoped package first, then the unscoped variant.
      let mod: any;
      try {
        mod = await import(`@corsair-dev/${entry.name}`);
      } catch {
        mod = await import(`corsair-${entry.name}`);
      }
      const factory = mod.default ?? mod[entry.name];
      if (typeof factory === "function") {
        loaded.push(factory());
        console.error(`[corsair] Loaded plugin: ${entry.name}`);
      } else {
        console.error(`[corsair] Plugin ${entry.name}: no factory export found`);
      }
    } catch (err) {
      console.error(`[corsair] Failed to load plugin ${entry.name}:`, err);
    }
  }
  return loaded;
}

/* ------------------------------------------------------------------ */
/*  Main                                                               */
/* ------------------------------------------------------------------ */

async function main(): Promise<void> {
  const config = loadConfig();
  const pluginEntries = config.corsair?.plugins ?? [];

  const enabledPlugins = await loadPlugins(pluginEntries);

  if (enabledPlugins.length === 0) {
    console.error(
      "[corsair] No plugins enabled.  " +
        "Enable at least one plugin in peripherals.yaml.",
    );
    // Still start the server so health checks pass — just with no plugins.
  }

  const corsair = createCorsair({ plugins: enabledPlugins });
  const server = runStdioMcpServer({ corsair });

  console.error(
    `[corsair] MCP sidecar running with ${enabledPlugins.length} plugin(s).`,
  );

  // Keep the process alive until stdin closes (daemon shuts down).
  process.stdin.resume();
  process.stdin.on("end", () => {
    console.error("[corsair] stdin closed — shutting down.");
    process.exit(0);
  });
}

main().catch((err) => {
  console.error("[corsair] Fatal:", err);
  process.exit(1);
});
