# Threat model — LLM-generated code execution

**Scope of this document:** `python_agent.py`'s `execute_code()`, the one place in this
codebase that runs LLM-generated code via `exec()`. (`chart_agent.py` no longer does this at
all — the Phase 4 redesign replaced "LLM writes matplotlib code" with "LLM picks a small JSON
chart spec; hand-written, deterministic code renders it." See `CLAUDE.md`'s chart agent
section. `sql_agent.py` executes via SQLite, a different threat surface not covered here.)

This document exists because an unsandboxed `exec()` on a public URL is a critical
vulnerability (PHASES.md risk #2), and honestly documenting what is and isn't mitigated is more
useful — to an interviewer, to a future maintainer, to us — than silently hoping the mitigations
below are airtight. They are defense-in-depth, not a hard sandbox guarantee.

## The core problem

`exec(code, globals_dict)` auto-injects the real, unrestricted `__builtins__` into
`globals_dict` if that dict doesn't already define the `__builtins__` key. Before this phase,
`execute_code()` passed `safe_environment = {"df": df}` — no `__builtins__` key — so despite the
variable's name, LLM-generated code had full access to `open()`, `__import__()`, `eval()`,
`exec()`, and everything reachable through them (arbitrary file read/write, `os.system`,
`subprocess`, network calls). Confirmed as a real risk pattern by the analogous `/ask`
path-traversal hole, which *was* exploited live during Phase 3 testing (read a real `.env`,
including the live Groq key — rotated afterward).

## What's implemented (Phase 5)

### 1. Restricted `__builtins__`

`_ALLOWED_BUILTINS` (see `python_agent.py`) is passed explicitly as
`safe_environment["__builtins__"]`. It's deliberately generous with safe names (arithmetic,
comparisons, type constructors, common exceptions, `print`) so ordinary pandas-shaped code
doesn't burn retry attempts on a missing builtin, and strict about the dangerous ones: no
`open`, `__import__`, `eval`, `exec`, `compile`, `input`, `getattr`/`setattr`/`delattr`,
`globals`/`locals`/`vars`.

**Mitigates:** direct calls to the excluded names.
**Does not mitigate on its own:** `import` statements (a distinct syntax node, not a name
lookup a builtins dict can intercept) or object-graph walks that only use ordinary attribute
access — see layer 2.

### 2. AST static pre-check (`_validate_code_is_safe`)

Runs `ast.parse()` on the generated code before any `exec()` call and walks the resulting tree,
rejecting:
- Any `Import`/`ImportFrom` node — closes the gap layer 1 leaves open for `import os` etc.
- Any `Attribute` node whose name is a dunder (`__class__`, `__globals__`, `__subclasses__`,
  ...) — closes the classic sandbox-escape pattern
  `().__class__.__bases__[0].__subclasses__()`, which walks Python's live class graph using
  only attribute access, never calling `open`/`__import__` directly.

A rejection raises a plain `Exception`, which flows into the existing retry loop exactly like
any other code failure — the LLM gets the error and tries again without the forbidden
construct, at the cost of one retry attempt from the complexity tier's budget.

**Known residual gap:** a sufficiently creative snippet could still probe for other
non-dunder-named ways to reach dangerous objects (e.g. via exception `__traceback__` chains, if
`__traceback__` weren't itself dunder-named and therefore already blocked) — the dunder check
is a broad net, not an exhaustive enumeration of every possible Python introspection trick.
Combined with layer 1 excluding `getattr`, most known indirection tricks (e.g. building a dunder
name from string concatenation to dodge a literal scan) still fail, but "most known" is not
"all possible."

### 3. Wall-clock timeout — best-effort, not a hard resource guarantee

`execute_code()` runs the `exec()` call inside a daemon `threading.Thread` and calls
`worker.join(EXEC_TIMEOUT_SECONDS)` (10s). If the thread is still alive after that, the request
fails fast with a timeout error.

**This bounds how long the caller waits. It does not bound how long the underlying work
actually runs, or reclaim its CPU/memory.** CPython has no safe way to forcibly kill a running
thread — unlike a process, a thread shares the interpreter's memory and can be holding locks or
mid-mutation on shared C-level buffers (pandas/numpy internals), so forcibly terminating it
risks corrupting the whole process's memory, not just that thread's. Because of this, Python
deliberately provides no `Thread.kill()`. A snippet genuinely stuck in a tight C-level loop
(e.g. a runaway merge/cartesian product) keeps consuming CPU in the background after the
10-second timeout fires and the API has already returned an error to the caller.

**What actually reclaims that CPU/memory today:**
- If the stuck code eventually terminates on its own (slow-but-finite), normal garbage
  collection reclaims it.
- If the whole process restarts (local dev) or the container instance is replaced (Cloud Run),
  OS-level process teardown reclaims everything at once — because a process, unlike a thread,
  doesn't share memory with anything else, so the OS can tear it down unconditionally.
- **On Cloud Run specifically:** the platform enforces its own request timeout (default 5 min,
  configurable up to 60 min) independent of anything in this codebase, and can replace an
  unresponsive container instance. That is a real backstop — but it operates on Cloud Run's
  clock, not our 10-second one. Under the deploy's `max-instances=1` cap, a single
  genuinely-hung request can tie up half of total capacity for however long Cloud Run's own
  timeout takes to catch it, not just our 10 seconds. A second concurrent hung request could
  exhaust the other instance.

**Follow-up, not implemented in this pass:** replacing the thread with a `multiprocessing`
child process would close this gap for real — `process.terminate()`/`process.kill()` is an
OS-level operation that actually reclaims CPU/memory immediately, because processes (unlike
threads) don't share an address space. This needs the child to re-read the CSV independently
(or receive `df` via a queue) and to send captured stdout back across the process boundary, and
was deliberately deferred out of this pass's scope rather than rushed. Tracked here so it isn't
lost; revisit if this becomes an active problem in production, or opportunistically in a later
phase.

### Known interaction with an existing, separately-tracked issue

Running `exec()` inside a background thread means `redirect_stdout`'s `sys.stdout` reassignment
now happens on a spawned thread rather than the main request thread. `sys.stdout` is
process-global. This does not introduce a new problem — it's the same one already documented in
`docs/BUGS_FOUND.md` #3 and `PHASES.md` risk #11 (two concurrent requests can race on
`sys.stdout`, only partially resolved in Phase 3 by switching to `contextlib.redirect_stdout` for
guaranteed restoration, not for the underlying concurrency). Worth flagging explicitly here so
it isn't mistaken for something this phase caused.

## What deploy-time controls are still doing the rest of the work

None of the above is a substitute for isolating the container itself. The Cloud Run deploy
config (Phase 5, Docker half) is expected to carry:
- **A non-root container user** — `app`, created in the `Dockerfile`, owns only its own files;
  no elevated OS permissions. (Cloud Run's filesystem is actually writable and held in memory —
  not read-only, as an earlier draft of this doc assumed; see `PHASES.md` Phase 5's correction.
  The non-root user is the real mitigation here, not filesystem immutability.)
- **No elevated service-account permissions** — `agent-runtime`'s only IAM role is
  `secretmanager.secretAccessor` on the one `groq-api-key` secret. A compromised container
  can't pivot into Cloud Run itself, Artifact Registry, or anything else in the project.
- **`max-instances=1`** (not 2 — revised once sessions were confirmed in-memory and
  single-process by design) — bounds the blast radius of any resource-exhaustion attempt to the
  one instance that will ever exist, though see the timeout discussion above for why this cap
  also means one hung request costs proportionally more: there's no second instance to fall
  back on.
- **`concurrency=1`** — only one request processed at a time per instance, closing the
  `sys.stdout` race documented in `docs/BUGS_FOUND.md` #3 for real at the deploy level.

## Chart URL exposure (outside this doc's original exec() scope, added Phase 5 follow-up)

`main.py` serves generated chart PNGs at `GET /charts/{filename}` so a remote caller (not just
someone with filesystem access to the server) can actually see a chart an `/upload` response
points to via `chart_url`.

**The risk this created:** the file naming this replaced used the requesting session's
`session_id` as the chart's filename. Once that file is reachable at a URL, the URL itself
becomes a leak: `session_id` is the caller's only credential for its session's uploaded CSV and
follow-up questions (see `PHASES.md`'s SaaS-readiness discussion — sessions have no other
auth). Anyone who saw a `session_id`-derived chart URL — in a shared link, a browser history, or
Cloud Run's request logs — could replay that `session_id` against `/upload` and read the
session's actual data, not just view the one chart that was meant to be shared.

**Fix:** chart filenames are a fresh, unrelated random UUID (`uuid4().hex`) per chart, generated
in `chart_agent.py`, never derived from `session_id` or from the question/title. A name built
from the question text was considered and rejected: two different sessions asking a
similarly-worded question would collide (last write wins — one session could end up seeing
another session's chart), and a name predictable from common question phrasing is partially
guessable, defeating the point of an unguessable link. `session_service.register_chart()`
tracks each session's chart paths separately so they can still be deleted when the session
expires, since the filename itself no longer encodes anything to search by.

**What this does not fix:** the chart URL, once generated, has no expiry and no access check of
its own — anyone sent a specific chart link can view that one image indefinitely (until the
session's TTL cleanup deletes the file). This is an accepted tradeoff for a demo with no auth
layer, consistent with the "no SaaS, no login" locked decision in `PHASES.md`.

## Bottom line

This is defense-in-depth against the known, common attack shapes (direct dangerous-builtin
calls, `import`-based escapes, the standard dunder object-graph walk), plus a best-effort bound
on how long a caller waits for a hang. It is explicitly **not** a hard sandbox guarantee — real
isolation from a determined adversary would need a process or container boundary around each
`exec()` call, which is a larger change than this phase's scope (and arguably belongs with the
"multi-user SaaS" candidate phase's sandboxing requirement in `PHASES.md`, where the stakes of
getting this wrong are highest).
