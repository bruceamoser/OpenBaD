import { useMemo } from "react";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import csharp from "highlight.js/lib/languages/csharp";
import css from "highlight.js/lib/languages/css";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import kotlin from "highlight.js/lib/languages/kotlin";
import makefile from "highlight.js/lib/languages/makefile";
import markdown from "highlight.js/lib/languages/markdown";
import php from "highlight.js/lib/languages/php";
import python from "highlight.js/lib/languages/python";
import ruby from "highlight.js/lib/languages/ruby";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import swift from "highlight.js/lib/languages/swift";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

const languages = {
  bash,
  c,
  cpp,
  csharp,
  css,
  dockerfile,
  go,
  ini,
  java,
  javascript,
  json,
  kotlin,
  makefile,
  markdown,
  php,
  python,
  ruby,
  rust,
  sql,
  swift,
  typescript,
  xml,
  yaml,
};
for (const [name, language] of Object.entries(languages)) {
  hljs.registerLanguage(name, language);
}

const languageByExtension: Record<string, string> = {
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  ts: "typescript",
  tsx: "typescript",
  mts: "typescript",
  cts: "typescript",
  cs: "csharp",
  rs: "rust",
  py: "python",
  go: "go",
  java: "java",
  kt: "kotlin",
  rb: "ruby",
  php: "php",
  c: "c",
  h: "c",
  cc: "cpp",
  cpp: "cpp",
  hpp: "cpp",
  json: "json",
  jsonl: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  ini: "ini",
  cfg: "ini",
  conf: "ini",
  env: "bash",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  ps1: "bash",
  sql: "sql",
  css: "css",
  scss: "css",
  html: "xml",
  htm: "xml",
  xml: "xml",
  svg: "xml",
  md: "markdown",
  markdown: "markdown",
  mdx: "markdown",
  swift: "swift",
  dockerfile: "dockerfile",
  makefile: "makefile",
};

export function languageForPath(path: string | undefined): string | null {
  if (path === undefined) {
    return null;
  }
  const name = path.split("/").at(-1)?.toLowerCase() ?? "";
  if (name === "dockerfile") {
    return "dockerfile";
  }
  if (name === "makefile") {
    return "makefile";
  }
  const extension = name.includes(".") ? (name.split(".").at(-1) ?? null) : null;
  return extension === null ? null : (languageByExtension[extension] ?? null);
}

export default function CodeView({
  code,
  path,
}: {
  code: string;
  path?: string;
}) {
  const language = languageForPath(path);
  const highlighted = useMemo(() => {
    if (code.length > 400_000) {
      return null;
    }
    try {
      return language === null
        ? hljs.highlightAuto(code).value
        : hljs.highlight(code, { language }).value;
    } catch {
      return null;
    }
  }, [code, language]);
  const lines = useMemo(() => code.split("\n"), [code]);
  const gutter = useMemo(
    () => Array.from({ length: lines.length }, (_, index) => index + 1).join("\n"),
    [lines.length],
  );

  return (
    <div className="flex h-full min-h-0 overflow-auto rounded-lg border border-white/[0.06] bg-black/30">
      <pre
        aria-hidden="true"
        data-testid="line-numbers"
        className="sticky left-0 z-[1] shrink-0 select-none border-r border-white/[0.06] bg-[#161d26] px-2 py-2 text-right font-mono text-[10px] leading-5 text-slate-600"
      >
        {gutter}
      </pre>
      <pre className="min-w-0 flex-1 px-3 py-2 font-mono text-[10px] leading-5">
        {highlighted === null ? (
          <code className="hljs">{code}</code>
        ) : (
          <code
            className="hljs"
            // highlight.js returns escaped, highlighted HTML from the source text.
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        )}
      </pre>
    </div>
  );
}
