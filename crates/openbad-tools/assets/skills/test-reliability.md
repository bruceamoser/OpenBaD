---
name: test-reliability
description: Write and fix tests that fail for the right reason under concurrency, shared hosts, clocks, and process-global state.
---

# Test reliability

A test that fails one run in fifty costs more than the bug it was meant to
catch. Before adding a test, name what makes it deterministic.

## Common sources of flake

- **Time.** Read the clock once per test, or inject it. Assertions on
  wall-clock differences ("this took under 100ms") fail on a loaded CI box.
  Prefer ordering and causality over elapsed time.
- **Ports and sockets.** Bind to port 0 and ask the OS which port you got.
  A hardcoded port collides with whatever else the runner is doing.
- **Process-global state.** Environment variables, working directory, signal
  handlers, and global caches leak between tests that run in one process. Set
  them in a guard that restores on drop, and keep tests that need them
  serialized.
- **Shared fixtures and temp paths.** One temp directory per test, removed on
  drop. A shared fixture that one test mutates makes the suite order-dependent.
- **Asynchronous teardown.** Await the shutdown of every task, thread, and
  listener your test started. "Spawn and hope" leaves handles that trip the
  next test.
- **Concurrency assumptions.** Do not assume a background task has run by the
  time the next line executes. Wait on an observable condition with a bounded
  timeout, not a fixed sleep.

## Writing the test

- Make the failing assertion describe behaviour, not implementation detail, so
  it survives a refactor.
- Prefer one deep comparison of a whole result over a dozen partial assertions:
  it reads as the expected outcome and updates in one place.
- Cover the failure path you just fixed with a test that fails before the fix.
  Reproduce first, then fix.
- Keep the arrangement short. If setup dominates the test, the unit under test
  needs a seam, not more mocks.

## Do not

- Do not stub a global object to install a fake; make the dependency injectable
  and pass the fake.
- Do not add retries or sleeps to silence a flake — find the unsynchronized
  thing and wait on it properly.
- Do not weaken an assertion to make an intermittent failure disappear.
