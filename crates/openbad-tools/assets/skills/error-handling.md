---
name: error-handling
description: Design failures, recovery, retries, and user-visible errors so a failure is actionable instead of silent or fatal.
---

# Error handling

Ask four questions about every operation that can fail: can it be detected, can
it be retried, does the caller need to know, and what does the human see?

## Detect

- Handle the error at the level that can actually do something about it. A
  wrapper that logs and rethrows in the same breath only multiplies noise.
- Distinguish "the user did something invalid" from "the system failed".
  Invalid input is a normal answer, not an exception; a failed dependency is
  not the user's fault and should not surface as one.
- Never discard the cause. When you translate an error into a different type,
  carry the original along or fold its message in.

## Retry

- Retry only idempotent work, and only on transient causes (timeouts, refused
  connections, 429, 503). A validation failure retried forever is a loop.
- Bound it: a small attempt count with backoff, plus a deadline. Unbounded
  retries turn one slow dependency into a hung application.
- Re-check the cancellation or shutdown signal between attempts, so a stopping
  turn stops promptly instead of finishing its retry budget first.

## Surface

- A background task that fails must leave a mark the human will actually see —
  a failed row, an unread marker, a notification — not only a log line.
- Say what happened, what it means, and what to do next. "Failed to load" is
  not a message; "Could not reach the model endpoint (timeout after 30s).
  Retry, or check your network." is.
- Include the actionable identifier (path, host, status code). Exclude secrets,
  tokens, and file contents from anything user-visible or persisted.

## Do not

- Do not swallow an error with an empty catch and a comment.
- Do not retry a request that may have already had a side effect.
- Do not turn a recoverable failure into a panic, an abort, or a lost turn when
  the surrounding work could continue.
- Do not log the same failure at every layer.
