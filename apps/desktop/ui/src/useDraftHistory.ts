import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type {
  ChangeEvent,
  CompositionEvent,
  KeyboardEvent as ReactKeyboardEvent,
  RefObject,
  SyntheticEvent,
} from "react";

/**
 * The draft textarea is controlled, so the browser's native undo stack cannot be
 * used reliably. This hook keeps a bounded, per-draft undo/redo stack and
 * restores both the text and the caret/selection on every step.
 */
type DraftSnapshot = {
  value: string;
  start: number;
  end: number;
};

export type DraftHistory = {
  value: string;
  handleChange: (event: ChangeEvent<HTMLTextAreaElement>) => void;
  handleCompositionStart: () => void;
  handleCompositionEnd: (event: CompositionEvent<HTMLTextAreaElement>) => void;
  handleKeyDown: (event: ReactKeyboardEvent<HTMLTextAreaElement>) => boolean;
  handleSelect: (event: SyntheticEvent<HTMLTextAreaElement>) => void;
  /** Replace the draft as an undoable edit and move the caret (defaults to end). */
  setValue: (value: string, caret?: number) => void;
  /** Start a new draft lifecycle and discard undo/redo history. */
  reset: (value?: string) => void;
};

const DEFAULT_LIMIT = 100;
const COALESCE_MS = 750;

type DraftPoint = {
  start: number;
  end: number;
};

function normalize(snapshot: DraftSnapshot): DraftSnapshot {
  const start = Math.max(0, Math.min(snapshot.start, snapshot.value.length));
  const end = Math.max(start, Math.min(snapshot.end, snapshot.value.length));
  return { value: snapshot.value, start, end };
}

function snapshotOf(textarea: HTMLTextAreaElement): DraftSnapshot {
  return normalize({
    value: textarea.value,
    start: textarea.selectionStart,
    end: textarea.selectionEnd,
  });
}

function pushBounded(
  stack: DraftSnapshot[],
  snapshot: DraftSnapshot,
  limit: number,
): void {
  stack.push(snapshot);
  if (stack.length > limit) {
    stack.splice(0, stack.length - limit);
  }
}

/**
 * Describe a single-character insert/remove/replace, or null for anything
 * larger (paste, drop, autocomplete, multi-character selection replacement).
 */
function singleCharacterChange(
  previous: DraftSnapshot,
  next: DraftSnapshot,
): { removed: string; inserted: string } | null {
  const before = previous.value;
  const after = next.value;
  if (before === after || Math.abs(after.length - before.length) > 1) {
    return null;
  }
  let prefix = 0;
  while (
    prefix < before.length &&
    prefix < after.length &&
    before[prefix] === after[prefix]
  ) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix < before.length - prefix &&
    suffix < after.length - prefix &&
    before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  ) {
    suffix += 1;
  }
  const removed = before.slice(prefix, before.length - suffix);
  const inserted = after.slice(prefix, after.length - suffix);
  if (removed.length > 1 || inserted.length > 1) {
    return null;
  }
  return { removed, inserted };
}

/**
 * True when an edit only inserts or removes one non-whitespace character, i.e.
 * ordinary typing/backspace that should collapse into a single undo step while
 * the user keeps typing without a pause. Whitespace starts a new undo group so
 * Ctrl/Cmd-Z removes roughly one word at a time.
 */
function isTypingContinuation(previous: DraftSnapshot, next: DraftSnapshot): boolean {
  const change = singleCharacterChange(previous, next);
  if (change === null) {
    return false;
  }
  return !/\s/.test(change.removed) && !/\s/.test(change.inserted);
}

export function useDraftHistory(
  textareaRef: RefObject<HTMLTextAreaElement | null>,
  options: { initialValue?: string; limit?: number } = {},
): DraftHistory {
  const initialValue = options.initialValue ?? "";
  const limit = Math.max(1, options.limit ?? DEFAULT_LIMIT);
  const [value, setValueState] = useState(initialValue);
  const [selectionRevision, setSelectionRevision] = useState(0);
  const pastRef = useRef<DraftSnapshot[]>([]);
  const futureRef = useRef<DraftSnapshot[]>([]);
  const lastRef = useRef<DraftSnapshot>({
    value: initialValue,
    start: initialValue.length,
    end: initialValue.length,
  });
  const lastEditAtRef = useRef(0);
  // Whether the previous edit was itself a one-character typing step. Pasting,
  // dropping, or programmatically replacing text breaks the undo group so a
  // following keystroke cannot merge into that larger edit.
  const lastWasTypingRef = useRef(false);
  const composingRef = useRef(false);
  const pendingSelectionRef = useRef<DraftPoint | null>(null);

  const requestSelection = useCallback((start: number, end: number) => {
    pendingSelectionRef.current = { start, end };
    setSelectionRevision((revision) => revision + 1);
  }, []);

  // Restore the caret/selection after React commits an undo/redo or an
  // imperative replacement. The revision state makes this run even when the
  // text itself did not change.
  useLayoutEffect(() => {
    const pending = pendingSelectionRef.current;
    const textarea = textareaRef.current;
    if (pending === null || textarea === null) {
      return;
    }
    pendingSelectionRef.current = null;
    const start = Math.max(0, Math.min(pending.start, textarea.value.length));
    const end = Math.max(start, Math.min(pending.end, textarea.value.length));
    textarea.setSelectionRange(start, end);
  }, [selectionRevision, textareaRef, value]);

  const commit = useCallback(
    (next: DraftSnapshot, coalesce: boolean) => {
      const previous = lastRef.current;
      if (next.value !== previous.value) {
        const now = Date.now();
        const canCoalesce =
          coalesce &&
          lastWasTypingRef.current &&
          pastRef.current.length > 0 &&
          now - lastEditAtRef.current <= COALESCE_MS &&
          isTypingContinuation(previous, next);
        if (!canCoalesce) {
          pushBounded(pastRef.current, previous, limit);
        }
        futureRef.current.length = 0;
        lastEditAtRef.current = now;
        lastWasTypingRef.current =
          coalesce && singleCharacterChange(previous, next) !== null;
      }
      lastRef.current = next;
      setValueState(next.value);
    },
    [limit],
  );

  const handleChange = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>) => {
      const next = snapshotOf(event.currentTarget);
      const composing =
        composingRef.current ||
        (event.nativeEvent as InputEvent).isComposing === true;
      if (composing) {
        // Do not record intermediate IME text. handleCompositionEnd records the
        // whole composition as one edit.
        setValueState(next.value);
        return;
      }
      commit(next, true);
    },
    [commit],
  );

  const handleCompositionStart = useCallback(() => {
    composingRef.current = true;
  }, []);

  const handleCompositionEnd = useCallback(
    (event: CompositionEvent<HTMLTextAreaElement>) => {
      composingRef.current = false;
      commit(snapshotOf(event.currentTarget), false);
    },
    [commit],
  );

  const handleSelect = useCallback((event: SyntheticEvent<HTMLTextAreaElement>) => {
    const target = event.currentTarget;
    if (composingRef.current || target.value !== lastRef.current.value) {
      return;
    }
    lastRef.current = normalize({
      value: target.value,
      start: target.selectionStart,
      end: target.selectionEnd,
    });
  }, []);

  const undo = useCallback(() => {
    const textarea = textareaRef.current;
    const current = textarea !== null ? snapshotOf(textarea) : lastRef.current;
    const previous = pastRef.current.pop();
    if (previous === undefined) {
      return;
    }
    pushBounded(futureRef.current, current, limit);
    lastRef.current = previous;
    lastEditAtRef.current = 0;
    lastWasTypingRef.current = false;
    setValueState(previous.value);
    requestSelection(previous.start, previous.end);
  }, [limit, requestSelection, textareaRef]);

  const redo = useCallback(() => {
    const textarea = textareaRef.current;
    const current = textarea !== null ? snapshotOf(textarea) : lastRef.current;
    const next = futureRef.current.pop();
    if (next === undefined) {
      return;
    }
    pushBounded(pastRef.current, current, limit);
    lastRef.current = next;
    lastEditAtRef.current = 0;
    lastWasTypingRef.current = false;
    setValueState(next.value);
    requestSelection(next.start, next.end);
  }, [limit, requestSelection, textareaRef]);

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLTextAreaElement>): boolean => {
      const native = event.nativeEvent;
      if (native.isComposing || native.keyCode === 229) {
        return false;
      }
      if ((!event.metaKey && !event.ctrlKey) || event.altKey) {
        return false;
      }
      const key = event.key.toLowerCase();
      if (key === "z") {
        event.preventDefault();
        if (event.shiftKey) {
          redo();
        } else {
          undo();
        }
        return true;
      }
      if (key === "y" && !event.shiftKey) {
        event.preventDefault();
        redo();
        return true;
      }
      return false;
    },
    [redo, undo],
  );

  const setValue = useCallback(
    (nextValue: string, caret?: number) => {
      const selection = caret ?? nextValue.length;
      const point = normalize({
        value: nextValue,
        start: selection,
        end: selection,
      });
      const textarea = textareaRef.current;
      const current = textarea !== null ? snapshotOf(textarea) : lastRef.current;
      if (point.value !== current.value) {
        pushBounded(pastRef.current, current, limit);
        futureRef.current.length = 0;
        lastEditAtRef.current = 0;
        lastWasTypingRef.current = false;
      }
      lastRef.current = point;
      setValueState(point.value);
      requestSelection(point.start, point.end);
    },
    [limit, requestSelection, textareaRef],
  );

  const reset = useCallback((nextValue = "") => {
    pastRef.current.length = 0;
    futureRef.current.length = 0;
    lastEditAtRef.current = 0;
    lastWasTypingRef.current = false;
    composingRef.current = false;
    pendingSelectionRef.current = null;
    lastRef.current = {
      value: nextValue,
      start: nextValue.length,
      end: nextValue.length,
    };
    setValueState(nextValue);
  }, []);

  return {
    value,
    handleChange,
    handleCompositionStart,
    handleCompositionEnd,
    handleKeyDown,
    handleSelect,
    setValue,
    reset,
  };
}
