---
name: factory
description: Run the software factory. Use when the user types /factory <feature>, /factory next, /factory approve <job-id>, or /factory rework <job-id> <note>. The session that runs it becomes the orchestrator of the spec → build → review → approve loop.
---

# Factory

You are the orchestrator. You run the loop below, spawn every agent, and are the
only writer of `factory/board.json`. You never write code, specs, reviews or
decisions yourself. Agents do the work and each writes one file; you move the
job along and keep `board.json` current.

## The loop

```
                   ┌──────────────────┐
                   │     feature      │
                   └─────────┬────────┘
                             ▼
                   ┌──────────────────┐
                   │   spec-writer    │
                   │       opus       │
                   └─────────┬────────┘
                             ▼
                   ┌──────────────────┐
                   │     builder      │◄────────────────────┐
                   │       opus       │                     │
                   └─────────┬────────┘                     │
        ┌─────────────┬──────┴──────┬─────────────┐         │
        ▼             ▼             ▼             ▼         │
  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐   │
  │  security │ │     ux    │ │ ui design │ │    code   │   │
  │    opus   │ │   sonnet  │ │   sonnet  │ │   sonnet  │   │
  └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘   │
        └─────────────┴──────┬──────┴─────────────┘         │
                             ▼                              │
                   ┌──────────────────┐   any CHANGES:      │
                   │   WAIT FOR ALL   ├─────────────────────┘
                   │   4 reviews in   │   resume builder
                   └─────────┬────────┘   (max 2 rounds)
                             ▼ all PASS
                   ┌──────────────────┐
                   │     approver     │
                   │       opus       │
                   └─┬───────────────┬┘
             APPROVE │               │ ESCALATE
                     ▼               ▼
              ┌─────────────┐ ┌─────────────┐
              │merge to main│ │ needs-human │ ◄── /factory approve
              └─────────────┘ └─────────────┘
```

| Step    | Agent (`subagent_type`) | Writes                                     |
| ------- | ----------------------- | ------------------------------------------ |
| spec    | `spec-writer`           | `spec.md`                                  |
| build   | `builder`               | `build.md` + commits on `factory/<job-id>` |
| review  | `reviewer-security`     | `round-N/review-security.md`               |
|         | `reviewer-ux`           | `round-N/review-ux.md`                     |
|         | `reviewer-ui`           | `round-N/review-ui.md`                     |
|         | `reviewer-code`         | `round-N/review-code.md`                   |
| approve | `approver`              | `decision.md`                              |

`MAX_RESUMES = 2`: after round 1 the builder can be resumed at most twice, so a
job has at most 3 review rounds. To change the factory, edit this file and the
agent files in `.claude/agents/`. What each agent needs to know about SnapWorth
(its test commands, its hot spots) is in that agent's file.

## Files

`<repo>` is the main checkout (`git rev-parse --show-toplevel`).

```
<repo>/factory/board.json                      every job's state (you write it, the dashboard reads it)
<repo>/factory/backlog.md                      queue for /factory next
<repo>/factory/jobs/<job-id>/spec.md
<repo>/factory/jobs/<job-id>/build.md
<repo>/factory/jobs/<job-id>/rework-<k>.md     written by you, from /factory rework
<repo>/factory/jobs/<job-id>/round-<N>/review-{security,ux,ui,code}.md
<repo>/factory/jobs/<job-id>/decision.md
<repo>/../SnapWorth-factory/<job-id>/          the job's worktree, on factory/<job-id>
```

Agents never change the main checkout: it is the user's, and it may be dirty or
on another branch. Code changes happen in the job's worktree; the job folder in
the main checkout only collects the agents' markdown. `factory/jobs/` is
gitignored runtime state, apart from the committed example job.

### board.json

```json
{ "jobs": [
  { "id": "003-rate-limiting", "feature": "Rate limit the TikTok link callback",
    "branch": "factory/003-rate-limiting",
    "worktree": "/Users/you/code/SnapWorth-factory/003-rate-limiting",
    "stage": "review", "round": 2,
    "reviews": { "security": "CHANGES", "ux": "PASS", "ui": "pending", "code": "PASS" } }
] }
```

- `stage` is one of `spec`, `build`, `review`, `approve`, `merged`,
  `needs-human`. Worktree setup counts as `spec`.
- `round` is the current review round (0 until the first round starts).
- `reviews` holds the current round only. Each value is `pending`, `PASS` or
  `CHANGES`. Earlier rounds live in their `round-<N>/` folders, which the
  dashboard reads directly.
- Rewrite the whole file on every change, keeping every other job as it was.
  `worktree` is always an absolute path.
- Run one orchestrator at a time. Two sessions running `/factory` would both
  rewrite `board.json` and lose each other's updates.

### Prompts

Every agent prompt starts with the job's absolute paths:
`Job folder: <repo>/factory/jobs/<job-id>/. Worktree: <worktree>. Branch: factory/<job-id>.`
Below, `<paths>` stands for that sentence.

The builder, `reviewer-ux` and `reviewer-ui` may start the backend or the website
to look at a change. They get ports `5100`, `5101` and `5102`, appended as
` Port: <port>.`

## /factory <feature>

1. **Create the job.** Pick the next 3-digit number after the highest numbered
   folder in `factory/jobs/` (`001` if there are none) and a 2–4 word
   kebab-case slug of the feature: `004-legit-retry-copy`. Create
   `factory/jobs/<job-id>/`. The worktree is
   `$(cd <repo>/.. && pwd)/SnapWorth-factory/<job-id>`. Add the job to
   `board.json` with that `worktree`, `stage: "spec"`, `round: 0` and all
   reviews `pending`.
2. **Worktree.** Create the branch and its worktree from `main`:
   ```bash
   git worktree add -b factory/<job-id> <worktree> main
   ```
   If it fails, stop and tell the user. Dependencies are installed by the
   builder for the area it touches, not here.
3. **Spec.** Spawn `spec-writer` with the prompt
   `<paths> Feature: <feature, verbatim>`.
   Wait for it to finish, then check that `spec.md` exists.
4. **Build.** Set `stage: "build"`. Spawn `builder` **with the name
   `builder-<job-id>`** and the prompt `<paths> Build round 1. Port: 5100.`
   Wait for it to finish, then go to **Build check**.

### Build check

Read the first line of `build.md`.

- `STATUS: READY`: start the next review round.
- `STATUS: BLOCKED`: go to **Needs human**.

### Review round N

1. Set `stage: "review"`, `round: N` and all four reviews to `pending`. Create
   `factory/jobs/<job-id>/round-<N>/`.
2. Spawn all four reviewers **in one message**, so they run in parallel. Each
   gets the prompt `<paths> Review round N.`, plus ` Port: 5101.` for
   `reviewer-ux` and ` Port: 5102.` for `reviewer-ui`.
3. **WAIT FOR ALL.** Each time a reviewer finishes, read the first line of its
   file (`VERDICT: PASS` or `VERDICT: CHANGES`) and write that verdict to
   `board.json` straight away, so the dashboard chips fill in as they land. The
   round is done only when all four `review-*.md` files exist in `round-<N>/`.
   Never resume the builder mid-round. If a reviewer finishes without writing
   its file, or writes a first line that isn't a verdict, stop and tell the user.
4. Decide:
   - **All PASS**: go to **Approve**.
   - **Any CHANGES, resumes left**: set `stage: "build"`, then resume the same
     builder with `SendMessage` to `builder-<job-id>`:
     `Round N reviews are in factory/jobs/<job-id>/round-<N>/. Address every review with VERDICT: CHANGES, commit, and update build.md for round N+1.`
     Wait for it to finish, then go to **Build check**.
   - **Any CHANGES, no resumes left**: write `decision.md` yourself, reading
     `ESCALATE`, then a blank line, then
     `Review rounds exhausted. Outstanding CHANGES in round-<N>/ from: <reviewers>.`
     Go to **Needs human**.

### Approve

Set `stage: "approve"`. Spawn `approver` with the prompt `<paths> Final round: N.`
When it finishes, read the first line of `decision.md`:

- `APPROVE`: go to **Merge**.
- `ESCALATE`: go to **Needs human**.

### Merge

The merge happens in the main checkout, so it must be on `main` with no changes
outside `factory/`: `git branch --show-current` prints `main` and
`git status --porcelain -- . ':!factory'` prints nothing. `factory/` is exempt
because the board and job folders change on every run and job branches never
touch them. If either check fails, set `stage: "needs-human"` and tell the user
the job is approved but the main checkout is busy; they run
`/factory approve <job-id>` once it is clean. Otherwise:

```bash
git merge --no-ff factory/<job-id> -m "Merge factory/<job-id>"
git worktree remove <worktree>
```

Set `stage: "merged"`. If the merge fails, run `git merge --abort` (when a merge
is in progress), set `stage: "needs-human"` and tell the user why. If removing
the worktree fails, the job is still merged: tell the user what
`git worktree remove` printed.

**Never push.** A push to `main` that touches `backend/` deploys production, and
`main` takes changes only through a pull request whose checks passed
(`docs/RUNBOOK.md`, "Merging"). The merge is local; the branch is kept so the
user can push `factory/<job-id>` and open that pull request.

### Needs human

Set `stage: "needs-human"`. The branch and its worktree stay for the human to
inspect. Tell the user which job is waiting, give the worktree path, and quote
`decision.md` (or the BLOCKED reason from `build.md`).

## /factory next

Read `factory/backlog.md` and take the first `- [ ]` item. Replace its line with
`- [x] <item> → <job-id>` once step 1 has given it a job id, then run
`/factory <item>`. If no unchecked item is left, say so and stop.

## /factory approve <job-id>

The job must be in `needs-human`. Run **Merge** with that job id.

## /factory rework <job-id> <note>

The job must be in `needs-human`.

1. Write the note, verbatim, to `factory/jobs/<job-id>/rework-<k>.md`, where `k`
   is the next unused number.
2. Set `stage: "build"`.
3. Spawn a fresh `builder` named `builder-<job-id>` with the prompt
   `<paths> Rework: read rework-<k>.md and address it, then update build.md for round N+1. Port: 5100.`
   (`N` is the job's last round.) A rework run gets a fresh `MAX_RESUMES`.
4. Continue from **Build check**, exactly like a new job.

## Reporting

Keep the user posted with one short line per transition, for example
`003-rate-limiting: round 1, security CHANGES, ux/ui/code PASS → resuming builder`.
When the job ends, say whether it merged or needs a human, and why.
