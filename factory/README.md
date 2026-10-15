# Software factory

One Claude Code skill, seven subagents and plain markdown files. Type
`/factory <feature>` in Claude Code and agents write a spec, build it on its own
branch, review it from four angles, and either merge it into your local `main`
or hand it to you.

```
feature → spec-writer → builder → [security, ux, ui, code] → WAIT FOR ALL
                           ▲                                      │
                           └──── any CHANGES: resume builder ◄────┤ (max 2)
                                                                  ▼ all PASS
                               merge to local main ◄─ APPROVE ─ approver ─ ESCALATE → needs-human
```

```
.claude/skills/factory/SKILL.md   the orchestrator: runs the loop, only writer of board.json
.claude/agents/                   spec-writer, builder, reviewer-{security,ux,ui,code}, approver
factory/board.json                every job's stage, round and verdicts
factory/dashboard.html            the visual board
factory/backlog.md                queue for /factory next
factory/jobs/<job-id>/            spec.md, build.md, round-N/review-*.md, decision.md
```

The loop is defined in `SKILL.md`; edit it, or an agent file, to change the
factory. What each agent needs to know about SnapWorth (test commands, security
hot spots, what always goes to a human) is in that agent's file, on top of
`CLAUDE.md`.

## Commands

```
/factory <feature>               run a new job through the loop
/factory next                    run the first unchecked item in backlog.md
/factory approve <job-id>        merge a needs-human job into local main
/factory rework <job-id> <note>  send a needs-human job back to the builder
```

Each job's code lives in a worktree next to the repo
(`../SnapWorth-factory/<job-id>/`) on `factory/<job-id>`, so your checkout is
never touched until the merge. The factory never pushes: `main` takes changes
only through a pull request (`docs/RUNBOOK.md`, "Merging"), so push the job's
branch and open one.

## Dashboard

```bash
cd factory && python3 -m http.server
```

Open <http://localhost:8000/dashboard.html>. Jobs move across the stages as
cards, review chips fill in as verdicts land, and jobs that need you stand out.
Click a card to read its files. It polls every two seconds.

## The example job

`jobs/003-rate-limiting/` is a real run of this factory, kept so the dashboard
has something to show. It put `GET /social/tiktok/callback` behind the shared
per-IP limit: all four reviews passed in round 1 and the approver approved it,
but the merge guard held it in needs-human because the session that ran it was
on a different branch from `main`. Its `factory/003-rate-limiting` branch was
never pushed, so `/factory approve 003-rate-limiting` has nothing to merge here.
`factory/jobs/` is otherwise gitignored runtime state.

Adapted from zazencodes'
[software-factory-claude-code](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code).
