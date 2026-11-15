# labs/zoe-os — the Zoe OS kernel

## Purpose

One small process that owns the household's hard rules so that no feature carries its own copy: who may do what (consent), what
was done (receipts), why, undo, and forget. Features propose; the kernel decides and records. Design record:
`docs/architecture/zoe-os.md`; the brain's side: `docs/architecture/zoe-os-brain-bridge.md`; the programme, phases and owner
decisions: `docs/architecture/zoe-os-cutover-plan.md`.

It lives under `labs/` while production moves onto it one action at a time. It is not throwaway: treat every change here as a
change to something production will depend on.

## Ownership

The owner decides what the kernel may decide (the cut-over plan records each decision). Builders change the kernel through
reviewed pull requests. The landing session and the owner are the only ones who install its unit, set its flags or restart it.

## Local Contracts

These are the rules the kernel exists to guarantee. A change that weakens one is not a refactor; it needs an owner decision
recorded in the cut-over plan.

1. **The brain proposes, the kernel disposes.** Nothing outside the kernel process writes its record. Callers hold a socket and
   a credential, never the database.
2. **The wire is the only seam.** `services/` never imports this package. Wire changes are additive: an existing client keeps
   working unchanged.
3. **No claim without a receipt.** An action is recorded with a verified receipt or it did not happen. One receipt backs one
   claim, and a spent receipt stays spent across forget, retention and restart.
4. **Durable or not acknowledged.** An acknowledged write survives a kill at any point; an unacknowledged one is absent or
   marked uncertain, never half there. Speed is never bought with durability.
5. **Forgotten means forever.** A forget reaches every copy: events, derived text, undo snapshots, intents, grant and refusal
   records, subscriber and skill stores, and anything a later action or undo would write back. The shield is scoped to the
   person who forgot. The kernel orders a skill's store purge before the permission that would allow it is erased. When the
   kernel cannot tell what to erase, it refuses; it does not confirm.
6. **One member's data never reaches another** except through a grant the kernel holds.
7. **Authorised display is not a diagnostic.** What the kernel returns to a reader it has authorised may contain that reader's
   content. Anything a tool prints, logs or writes, and any endpoint without the token, carries codes, ids and counts only.
8. **An erased permission authorises nothing.** Consent is read from the live record every time it matters.
9. **Cost does not grow with history.** Everyday actions, undo and forget do bounded work; the step-budget tests pin it.
10. **It works alone.** No network, no cloud, no code-hosting site is needed to run or test it.

## How a feature moves onto the kernel

Owner direction 2026-11-15; phases, proofs and decisions are in `docs/architecture/zoe-os-cutover-plan.md`.

- **New features** that write a household action are built to go through the kernel, or at least to publish their receipts to
  it, from the start.
- **Existing actions** move one at a time, in this order: shadow (the kernel is shown each real action and its verdict is
  compared; it controls nothing), the pass mark for that action, authority behind its own default-off flag with the old path as
  the fallback, then the duplicate rule code is removed.
- A kernel fault (down, slow, unsure) never fails or stalls a turn: the old path runs and the fallback is counted. A real
  refusal by the kernel (no permission, forgotten) is honoured.
- Forget and member delete reach the kernel before the kernel holds anything of a person.
- Flags, units and restarts are the owner's and the landing session's.

## Forbidden

- Importing this package from `services/`, or reaching its database from anywhere but the kernel process.
- Shipping a behaviour without a test that fails when the behaviour is removed (a negative control). A control that cannot fail
  is not a control.
- Marking a test's expectation "updated" to make it pass without saying which owner decision or which stronger guarantee
  justifies it.
- Loosening a refusal to make a test pass before establishing whether the refusal was right.
- Printing, logging or writing event text, arguments or reasons from a tool, harness or report.
- Real names, places, utterances, device ids or addresses in code, tests, fixtures or word lists. Invented content only.
- Process-environment names that start with the product's flag prefix; lab settings use their own prefix.
- Installing or enabling a unit, setting a flag, restarting a service, or sending traffic to the running assistant from here.
- Raising a step budget, a size bound or a timeout to make a change fit, without the measurement and the reason in the PR.

## Work Guidance

- Failing test first, then the fix, then the old-code control. Fix the class, not the instance: when a fault is found in one
  place that stores or echoes content, sweep every other place that does.
- Keep changes surgical; other branches are usually stacked on this one.
- Measure with `bench_kernel.py` before and after any change to a hot path, and put the numbers in the PR.
- The whole suite starts processes: it is a heavy run. On the box it runs in a memory window, one run at a time.
- A finding on the lab is fixed in place when it lets text or a credential leave the kernel process, breaks rule 5, or breaks
  rule 6; otherwise it may be deferred to a named follow-up that lists it.

## Verification

- `python3 -m pytest -p no:cacheprovider -q labs/zoe-os` (the second-OS-user tests need root and are skipped without it).
- `python3 labs/zoe-os/bench_kernel.py --mode all --seed 7 --out <file>` for latency, concurrency, soak, kill-and-restart and
  cold start (arrives with the load-harness change).

## Child DOX Index

None.
