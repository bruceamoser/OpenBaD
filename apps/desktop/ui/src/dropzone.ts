import { useEffect, useRef, useState } from "react";
import { getCurrentWebview } from "@tauri-apps/api/webview";

import type { UploadSource } from "./types";

export type DropPayload = {
  paths: string[];
  files: File[];
};

type RegisteredZone = {
  onDrop: (payload: DropPayload) => void;
  setActive: (active: boolean) => void;
};

const ZONE_ATTRIBUTE = "data-openbad-dropzone";
const zones = new Map<string, RegisteredZone>();
let nextZoneId = 1;
let activeZoneId: string | null = null;
let nativeBound = false;

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function zoneAt(x: number, y: number): string | null {
  const element = document.elementFromPoint(x, y);
  const target = element?.closest(`[${ZONE_ATTRIBUTE}]`);
  return target?.getAttribute(ZONE_ATTRIBUTE) ?? null;
}

function setActiveZone(next: string | null) {
  if (activeZoneId === next) {
    return;
  }
  if (activeZoneId !== null) {
    zones.get(activeZoneId)?.setActive(false);
  }
  activeZoneId = next;
  if (next !== null) {
    zones.get(next)?.setActive(true);
  }
}

function bindNativeDrops() {
  if (!isTauri() || nativeBound) {
    return;
  }
  nativeBound = true;
  void getCurrentWebview()
    .onDragDropEvent((event) => {
      const payload = event.payload;
      if (payload.type === "leave") {
        setActiveZone(null);
        return;
      }
      const ratio = window.devicePixelRatio > 0 ? window.devicePixelRatio : 1;
      const zone = zoneAt(payload.position.x / ratio, payload.position.y / ratio);
      if (payload.type === "drop") {
        setActiveZone(null);
        if (zone !== null) {
          zones.get(zone)?.onDrop({ paths: payload.paths, files: [] });
        }
        return;
      }
      setActiveZone(zone);
    })
    .catch(() => {
      nativeBound = false;
    });
}

function bindBrowserFallback() {
  if (typeof window === "undefined") {
    return;
  }
  const blockNavigation = (event: DragEvent) => {
    if (event.dataTransfer?.types.includes("Files") === true) {
      event.preventDefault();
    }
  };
  window.addEventListener("dragover", blockNavigation);
  window.addEventListener("drop", blockNavigation);
}

let browserBound = false;

export type DropZoneProps = {
  "data-openbad-dropzone": string;
  onDragEnter: (event: React.DragEvent<HTMLElement>) => void;
  onDragOver: (event: React.DragEvent<HTMLElement>) => void;
  onDragLeave: (event: React.DragEvent<HTMLElement>) => void;
  onDrop: (event: React.DragEvent<HTMLElement>) => void;
};

export function useFileDropZone(
  onDrop: (payload: DropPayload) => void | Promise<void>,
  options: { disabled?: boolean } = {},
): { active: boolean; zoneProps: DropZoneProps } {
  const [id] = useState(() => `openbad-drop-${nextZoneId++}`);
  const [active, setActive] = useState(false);
  const handler = useRef(onDrop);
  handler.current = onDrop;
  const disabled = useRef(options.disabled === true);
  disabled.current = options.disabled === true;

  useEffect(() => {
    zones.set(id, {
      onDrop: (payload) => {
        if (!disabled.current) {
          void handler.current(payload);
        }
      },
      setActive,
    });
    bindNativeDrops();
    if (!browserBound) {
      browserBound = true;
      bindBrowserFallback();
    }
    return () => {
      zones.delete(id);
      if (activeZoneId === id) {
        activeZoneId = null;
      }
    };
  }, [id]);

  const acceptsFiles = (event: React.DragEvent<HTMLElement>) =>
    event.dataTransfer.types.includes("Files");

  const zoneProps: DropZoneProps = {
    [ZONE_ATTRIBUTE]: id,
    onDragEnter: (event) => {
      if (!disabled.current && acceptsFiles(event)) {
        event.preventDefault();
        event.stopPropagation();
        setActive(true);
      }
    },
    onDragOver: (event) => {
      if (!disabled.current && acceptsFiles(event)) {
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = "copy";
        setActive(true);
      }
    },
    onDragLeave: (event) => {
      const next = event.relatedTarget;
      if (next instanceof Node && event.currentTarget.contains(next)) {
        return;
      }
      event.stopPropagation();
      setActive(false);
    },
    onDrop: (event) => {
      if (disabled.current || !acceptsFiles(event)) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      setActive(false);
      const files = Array.from(event.dataTransfer.files);
      const paths = files
        .map((file) => (file as File & { path?: string }).path)
        .filter((path): path is string => typeof path === "string" && path.length > 0);
      void handler.current({ paths, files });
    },
  };

  return { active, zoneProps };
}

export function fileName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts.filter(Boolean).at(-1) ?? path;
}

export async function droppedToUploads(payload: DropPayload): Promise<UploadSource[]> {
  if (payload.files.length === 0) {
    return payload.paths.map((path) => ({ name: fileName(path), path }));
  }
  const uploads: UploadSource[] = [];
  for (const file of payload.files) {
    const path = (file as File & { path?: string }).path;
    uploads.push(
      typeof path === "string" && path.length > 0
        ? { name: file.name, path }
        : { name: file.name, content: await file.text() },
    );
  }
  return uploads;
}
