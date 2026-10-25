import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import {
  Bot,
  CircleAlert,
  FileText,
  Image as ImageIcon,
  ListPlus,
  Paperclip,
  Send,
  Shield,
  Square,
  X,
} from "lucide-react";

import { pickFiles } from "./backend";
import { droppedToUploads, fileName, filesToUploads, useFileDropZone } from "./dropzone";
import type { AccessMode, UploadSource } from "./types";
import { useDraftHistory } from "./useDraftHistory";

export type ChatComposerHandle = {
  addUploads: (sources: UploadSource[]) => void;
  focus: () => void;
  setDraft: (text: string) => void;
};

type ChatComposerProps = {
  workspacePath: string;
  hasProject: boolean;
  busy: boolean;
  stopRequested: boolean;
  permissionMode: AccessMode;
  model: string;
  thinking: boolean;
  accessNotice: string | null;
  error: string | null;
  queuedMessages: string[];
  onPermissionMode: (mode: AccessMode) => void;
  onOpenSettings: () => void;
  onDismissAccessNotice: () => void;
  onDismissError: () => void;
  onRemoveQueued: (index: number) => void;
  onSend: (text: string, attachments: UploadSource[]) => void;
  onSteer: (text: string) => void;
  onQueue: (text: string) => void;
  onStop: () => void;
  onError: (message: string) => void;
};

function describeError(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

// Match the textarea's `leading-5` and `pt-3` classes. The composer grows from
// one line up to ten lines and then scrolls internally.
const TEXTAREA_LINE_HEIGHT_PX = 20;
const TEXTAREA_PADDING_TOP_PX = 12;
const TEXTAREA_MAX_ROWS = 10;
const TEXTAREA_MIN_HEIGHT_PX = TEXTAREA_LINE_HEIGHT_PX + TEXTAREA_PADDING_TOP_PX;
const TEXTAREA_MAX_HEIGHT_PX =
  TEXTAREA_LINE_HEIGHT_PX * TEXTAREA_MAX_ROWS + TEXTAREA_PADDING_TOP_PX;

// ^\s*(\d+)\.\s, with the indentation captured so it can be reused.
const ORDERED_LIST_PREFIX = /^([ \t]*)(\d+)\.\s/;

type OrderedListInsertion = {
  value: string;
  caret: number;
};

function orderedListInsertion(
  textarea: HTMLTextAreaElement,
): OrderedListInsertion | null {
  const { selectionStart, selectionEnd, value } = textarea;
  const lineStart =
    selectionStart === 0 ? 0 : value.lastIndexOf("\n", selectionStart - 1) + 1;
  const match = ORDERED_LIST_PREFIX.exec(value.slice(lineStart, selectionStart));
  if (match === null) {
    return null;
  }
  const current = Number.parseInt(match[2] ?? "", 10);
  const next = current + 1;
  if (!Number.isSafeInteger(current) || !Number.isSafeInteger(next)) {
    return null;
  }
  const insertion = `\n${match[1] ?? ""}${next}. `;
  return {
    value: `${value.slice(0, selectionStart)}${insertion}${value.slice(selectionEnd)}`,
    caret: selectionStart + insertion.length,
  };
}

function ChatComposer({
  workspacePath,
  hasProject,
  busy,
  stopRequested,
  permissionMode,
  model,
  thinking,
  accessNotice,
  error,
  queuedMessages,
  onPermissionMode,
  onOpenSettings,
  onDismissAccessNotice,
  onDismissError,
  onRemoveQueued,
  onSend,
  onSteer,
  onQueue,
  onStop,
  onError,
}: ChatComposerProps, ref: React.Ref<ChatComposerHandle>) {
  const [attachments, setAttachments] = useState<UploadSource[]>([]);
  const attachmentsRef = useRef<UploadSource[]>([]);
  attachmentsRef.current = attachments;
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const {
    value: draft,
    handleChange: handleDraftChange,
    handleCompositionStart: handleDraftCompositionStart,
    handleCompositionEnd: handleDraftCompositionEnd,
    handleKeyDown: handleDraftKeyDown,
    handleSelect: handleDraftSelect,
    setValue: setDraftValue,
    reset: resetDraft,
  } = useDraftHistory(textareaRef);

  const resizeTextarea = useCallback(() => {
    const textarea = textareaRef.current;
    if (textarea === null) {
      return;
    }
    const wasAtBottom =
      textarea.scrollHeight - textarea.scrollTop - textarea.clientHeight <= 4;
    textarea.style.height = "0px";
    const contentHeight = textarea.scrollHeight;
    const height = Math.min(
      Math.max(contentHeight, TEXTAREA_MIN_HEIGHT_PX),
      TEXTAREA_MAX_HEIGHT_PX,
    );
    textarea.style.height = `${height}px`;
    textarea.style.overflowY =
      contentHeight > TEXTAREA_MAX_HEIGHT_PX ? "auto" : "hidden";
    if (wasAtBottom) {
      textarea.scrollTop = textarea.scrollHeight;
    }
  }, []);

  useLayoutEffect(() => {
    resizeTextarea();
  }, [draft, resizeTextarea]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea === null) {
      return;
    }
    // Re-measure when the available width changes (window resize, sidebar, or
    // preview pane) because that changes how the draft wraps. Height changes
    // are ignored to avoid a resize loop.
    let width = textarea.clientWidth;
    const observer = new ResizeObserver(() => {
      if (textarea.clientWidth !== width) {
        width = textarea.clientWidth;
        resizeTextarea();
      }
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, [resizeTextarea]);

  const mergeAttachments = useCallback((incoming: UploadSource[]) => {
    setAttachments((current) => {
      const next = [...current];
      for (const attachment of incoming) {
        const key =
          attachment.path ??
          `${attachment.name}:${attachment.mime ?? ""}:${
            attachment.content?.length ?? attachment.contentBase64?.length ?? 0
          }`;
        if (
          next.some(
            (item) =>
              (item.path ??
                `${item.name}:${item.mime ?? ""}:${
                  item.content?.length ?? item.contentBase64?.length ?? 0
                }`) === key,
          )
        ) {
          continue;
        }
        if (next.length >= 8) {
          break;
        }
        next.push(attachment);
      }
      return next;
    });
  }, []);

  const revokePreviews = useCallback((sources: UploadSource[]) => {
    for (const source of sources) {
      if (source.previewUrl !== undefined) {
        URL.revokeObjectURL(source.previewUrl);
      }
    }
  }, []);

  useEffect(() => {
    return () => {
      for (const attachment of attachmentsRef.current) {
        if (attachment.previewUrl !== undefined) {
          URL.revokeObjectURL(attachment.previewUrl);
        }
      }
    };
  }, []);

  const handlePaste = useCallback(
    async (event: React.ClipboardEvent<HTMLDivElement>) => {
      const files = Array.from(event.clipboardData.files);
      if (files.length === 0) {
        for (const item of Array.from(event.clipboardData.items)) {
          if (item.kind !== "file") {
            continue;
          }
          const file = item.getAsFile();
          if (file !== null) {
            files.push(file);
          }
        }
      }
      if (files.length === 0) {
        return;
      }
      event.preventDefault();
      try {
        mergeAttachments(await filesToUploads(files, { pasted: true }));
        textareaRef.current?.focus();
      } catch (cause) {
        onError(describeError(cause));
      }
    },
    [mergeAttachments, onError],
  );

  useImperativeHandle(
    ref,
    () => ({
      addUploads: mergeAttachments,
      focus: () => textareaRef.current?.focus(),
      setDraft: (text: string) => {
        setDraftValue(text, text.length);
        textareaRef.current?.focus();
      },
    }),
    [mergeAttachments, setDraftValue],
  );

  const attachDocuments = useCallback(async () => {
    try {
      const chosen = await pickFiles("Attach files");
      mergeAttachments(chosen.map((path) => ({ name: fileName(path), path })));
    } catch (cause) {
      onError(describeError(cause));
    }
  }, [mergeAttachments, onError]);

  const drop = useFileDropZone(
    async (payload) => {
      mergeAttachments(await droppedToUploads(payload));
    },
    { disabled: workspacePath.length === 0 },
  );

  const send = () => {
    const text = draft.trim();
    if (
      busy ||
      workspacePath.length === 0 ||
      (text.length === 0 && attachments.length === 0)
    ) {
      return;
    }
    onSend(text, attachments);
    resetDraft("");
    revokePreviews(attachments);
    setAttachments([]);
  };

  const steer = () => {
    const text = draft.trim();
    if (text.length === 0) {
      return;
    }
    onSteer(text);
    resetDraft("");
  };

  const queue = () => {
    const text = draft.trim();
    if (text.length === 0) {
      return;
    }
    onQueue(text);
    resetDraft("");
  };

  return (
    <div className="shrink-0 border-t border-white/[0.06] bg-[#161e27]/95 px-4 py-3">
      <div className="mx-auto max-w-3xl">
        {accessNotice !== null && (
          <div className="mb-2 flex items-start gap-2 rounded-md border border-emerald-400/20 bg-emerald-400/10 px-3 py-2 text-[11px] text-emerald-200">
            <Shield size={13} className="mt-0.5 shrink-0" />
            <span className="flex-1">{accessNotice}</span>
            <button type="button" onClick={onDismissAccessNotice}>
              <X size={13} />
            </button>
          </div>
        )}
        {error !== null && (
          <div className="mb-2 flex items-start gap-2 rounded-md border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-[11px] text-amber-200">
            <CircleAlert size={13} className="mt-0.5 shrink-0" />
            <span className="flex-1">{error}</span>
            <button type="button" onClick={onDismissError}>
              <X size={13} />
            </button>
          </div>
        )}
        {queuedMessages.length > 0 && (
          <div className="mb-2 rounded-md border border-white/[0.08] bg-white/[0.02] px-2.5 py-2">
            <p className="flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              <ListPlus size={11} /> Queued · {queuedMessages.length}
            </p>
            <div className="mt-1 space-y-1">
              {queuedMessages.map((queued, index) => (
                <div
                  key={`queued-${index}-${queued.slice(0, 24)}`}
                  className="flex items-start gap-2 rounded bg-black/20 px-2 py-1"
                >
                  <span className="min-w-0 flex-1 whitespace-pre-wrap text-[10px] leading-4 text-slate-400">
                    {queued}
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove queued message ${index + 1}`}
                    onClick={() => onRemoveQueued(index)}
                    className="rounded p-0.5 text-slate-500 hover:text-amber-300"
                  >
                    <X size={10} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
        <div
          {...drop.zoneProps}
          onPaste={handlePaste}
          className={`relative rounded-xl border bg-[#212b37] shadow-lg shadow-black/20 transition-colors ${
            drop.active
              ? "border-emerald-400/60 ring-2 ring-emerald-400/20"
              : "border-white/[0.09]"
          }`}
        >
          {drop.active && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-emerald-400/10 text-[12px] font-medium text-emerald-100">
              Drop files to attach
            </div>
          )}
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5 px-3 pt-2.5">
              {attachments.map((attachment, index) => (
                <span
                  key={`${attachment.path ?? attachment.name}-${index}`}
                  className="flex max-w-full items-center gap-1 rounded border border-emerald-400/20 bg-emerald-400/10 px-1.5 py-0.5 text-[10px] text-emerald-100"
                >
                  {attachment.previewUrl !== undefined ? (
                    <img
                      src={attachment.previewUrl}
                      alt=""
                      className="h-5 w-5 shrink-0 rounded object-cover"
                    />
                  ) : attachment.mime?.startsWith("image/") === true ? (
                    <ImageIcon size={10} className="shrink-0" />
                  ) : (
                    <FileText size={10} className="shrink-0" />
                  )}
                  <span className="max-w-[220px] truncate">{attachment.name}</span>
                  <button
                    type="button"
                    aria-label={`Remove attachment ${attachment.name}`}
                    onClick={() => {
                      revokePreviews([attachment]);
                      setAttachments((current) =>
                        current.filter((_, position) => position !== index),
                      );
                    }}
                    className="rounded p-0.5 text-emerald-300/70 hover:text-emerald-100"
                  >
                    <X size={9} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={handleDraftChange}
            onCompositionStart={handleDraftCompositionStart}
            onCompositionEnd={handleDraftCompositionEnd}
            onSelect={handleDraftSelect}
            onKeyDown={(event) => {
              if (handleDraftKeyDown(event)) {
                return;
              }
              if (event.key !== "Enter") {
                return;
              }
              const native = event.nativeEvent;
              if (native.isComposing || native.keyCode === 229) {
                return;
              }
              if (event.shiftKey) {
                const insertion = orderedListInsertion(event.currentTarget);
                if (insertion !== null) {
                  event.preventDefault();
                  setDraftValue(insertion.value, insertion.caret);
                }
                return;
              }
              event.preventDefault();
              if (busy) {
                steer();
              } else {
                send();
              }
            }}
            rows={1}
            placeholder={
              hasProject
                ? "Ask OpenBaD, or paste files and images…"
                : "Add a project to start…"
            }
            className="block w-full resize-none bg-transparent px-3.5 pt-3 text-[13px] leading-5 text-slate-100 outline-none placeholder:text-slate-500"
          />
          <div className="flex items-center gap-1.5 px-2 pb-2 pt-1">
            <button
              type="button"
              onClick={() => void attachDocuments()}
              disabled={workspacePath.length === 0}
              aria-label="Attach files"
              title="Attach files"
              className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-slate-500 hover:bg-white/[0.04] hover:text-slate-300 disabled:opacity-30"
            >
              <Paperclip size={12} />
            </button>
            <label className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-slate-500 hover:bg-white/[0.04]">
              <Shield size={12} />
              <select
                value={permissionMode}
                onChange={(event) => onPermissionMode(event.target.value as AccessMode)}
                aria-label="Access mode"
                className="bg-transparent text-[11px] text-slate-400 outline-none"
              >
                <option value="read">Read only</option>
                <option value="edit">Can edit</option>
                <option value="full">Full access</option>
              </select>
            </label>
            <button
              type="button"
              onClick={onOpenSettings}
              className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
            >
              <Bot size={12} />
              <span className="max-w-[150px] truncate">{model}</span>
              {thinking && <span className="text-emerald-400/70">thinking</span>}
            </button>
            <span className="ml-auto text-[10px] text-slate-500">
              {busy
                ? "Enter to steer · Shift+Enter for a newline"
                : "Enter to send · Shift+Enter for a newline"}
            </span>
            {busy ? (
              <>
                <button
                  type="button"
                  aria-label="Steer current turn"
                  onClick={steer}
                  disabled={draft.trim().length === 0}
                  className="ml-1 flex items-center gap-1 rounded-md border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-1.5 text-[11px] text-emerald-200 disabled:opacity-30"
                >
                  <Send size={12} /> Steer
                </button>
                <button
                  type="button"
                  aria-label="Queue message"
                  onClick={queue}
                  disabled={draft.trim().length === 0}
                  className="flex items-center gap-1 rounded-md border border-white/10 px-2.5 py-1.5 text-[11px] text-slate-300 disabled:opacity-30"
                >
                  <ListPlus size={12} /> Queue
                </button>
                <button
                  type="button"
                  onClick={onStop}
                  disabled={stopRequested}
                  className="flex items-center gap-1 rounded-md border border-amber-400/30 bg-amber-400/10 px-2.5 py-1.5 text-[11px] text-amber-200 disabled:opacity-40"
                >
                  <Square size={12} /> {stopRequested ? "Stopping…" : "Stop"}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={send}
                disabled={
                  (draft.trim().length === 0 && attachments.length === 0) ||
                  workspacePath.length === 0
                }
                className="ml-1 flex items-center gap-1 rounded-md bg-emerald-400 px-2.5 py-1.5 text-[11px] font-medium text-emerald-950 hover:bg-emerald-300 disabled:cursor-not-allowed disabled:opacity-30"
              >
                <Send size={12} /> Send
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default memo(forwardRef(ChatComposer));
