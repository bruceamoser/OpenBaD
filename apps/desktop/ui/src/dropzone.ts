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

const TEXT_EXTENSIONS = new Set([
  "txt",
  "md",
  "markdown",
  "json",
  "jsonc",
  "yaml",
  "yml",
  "toml",
  "csv",
  "ts",
  "tsx",
  "js",
  "jsx",
  "py",
  "rs",
  "go",
  "java",
  "c",
  "h",
  "cpp",
  "hpp",
  "css",
  "scss",
  "html",
  "xml",
  "sql",
  "sh",
  "bash",
  "zsh",
  "log",
  "ini",
  "cfg",
  "conf",
  "env",
]);

function extensionOf(name: string): string {
  const match = /\.([a-z0-9]+)$/i.exec(name);
  return match?.[1]?.toLowerCase() ?? "";
}

function mimeExtension(mime: string): string {
  switch (mime) {
    case "image/png":
      return "png";
    case "image/jpeg":
      return "jpg";
    case "image/gif":
      return "gif";
    case "image/webp":
      return "webp";
    case "image/bmp":
      return "bmp";
    case "image/svg+xml":
      return "svg";
    case "application/pdf":
      return "pdf";
    case "text/plain":
      return "txt";
    case "text/markdown":
      return "md";
    case "application/json":
      return "json";
    default:
      return "bin";
  }
}

function isTextFile(file: File): boolean {
  if (file.type.startsWith("text/")) {
    return true;
  }
  if (
    file.type === "application/json" ||
    file.type === "application/xml" ||
    file.type === "application/javascript"
  ) {
    return true;
  }
  return TEXT_EXTENSIONS.has(extensionOf(file.name));
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Could not read file"));
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

function uploadedName(file: File, index: number, pasted: boolean): string {
  if (!pasted && file.name.length > 0) {
    return file.name;
  }
  const extension = extensionOf(file.name) || mimeExtension(file.type);
  const stamp = new Date()
    .toISOString()
    .replace(/[:.]/g, "-")
    .slice(0, 19);
  return `pasted-${stamp}-${index + 1}.${extension}`;
}

export async function filesToUploads(
  files: File[],
  options: { pasted?: boolean } = {},
): Promise<UploadSource[]> {
  const uploads: UploadSource[] = [];
  for (const [index, file] of files.entries()) {
    const path = (file as File & { path?: string }).path;
    const name = uploadedName(file, index, options.pasted === true);
    if (typeof path === "string" && path.length > 0) {
      uploads.push({ name, path, mime: file.type || undefined });
      continue;
    }
    if (isTextFile(file)) {
      uploads.push({ name, content: await file.text(), mime: file.type || undefined });
      continue;
    }
    uploads.push({
      name,
      contentBase64: await readBase64(file),
      mime: file.type || undefined,
      previewUrl: file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : undefined,
    });
  }
  return uploads;
}

export async function droppedToUploads(payload: DropPayload): Promise<UploadSource[]> {
  if (payload.files.length === 0) {
    return payload.paths.map((path) => ({ name: fileName(path), path }));
  }
  return filesToUploads(payload.files);
}
