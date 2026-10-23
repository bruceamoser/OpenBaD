import { useState } from "react";

import CodeView from "./CodeView";

export default function HtmlPreview({
  content,
  title,
  path,
}: {
  content: string;
  title: string;
  path?: string;
}) {
  const [mode, setMode] = useState<"rendered" | "source">("rendered");

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex shrink-0 gap-1">
        {(["rendered", "source"] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setMode(value)}
            className={`rounded px-2 py-1 text-[10px] capitalize ${
              mode === value
                ? "bg-emerald-400/15 text-emerald-200"
                : "bg-white/[0.04] text-slate-400 hover:bg-white/[0.08]"
            }`}
          >
            {value}
          </button>
        ))}
        <span className="ml-auto self-center text-[9px] text-slate-500">
          scripts are disabled
        </span>
      </div>
      {mode === "rendered" ? (
        <iframe
          title={title}
          srcDoc={content}
          sandbox=""
          referrerPolicy="no-referrer"
          data-testid="html-preview"
          className="min-h-[420px] w-full flex-1 rounded-lg border border-white/10 bg-white"
        />
      ) : (
        <CodeView code={content} path={path} />
      )}
    </div>
  );
}
