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
                ? "bg-obd-accent/15 text-obd-accent"
                : "bg-obd-raised text-obd-muted hover:bg-obd-raised-strong"
            }`}
          >
            {value}
          </button>
        ))}
        <span className="ml-auto self-center text-[9px] text-obd-subtle">
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
          className="min-h-[420px] w-full flex-1 rounded-lg border border-obd-line bg-obd-canvas"
        />
      ) : (
        <CodeView code={content} path={path} />
      )}
    </div>
  );
}
