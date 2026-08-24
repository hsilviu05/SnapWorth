# What's New — SnapWorth 1.3.1

**Version:** 1.3.1 (build 8) · **Previous release:** 1.3.0 (Ready for Distribution) · **Status:** unreleased

A fix release. 1.3.0 shipped the portfolio; this one fixes what users were
actually seeing when a scan went wrong.

## Scope

Most of this release is **server-side and already live** — it reached everyone
on 1.3.0 without an App Store update. Only the last item needs this build.

| Change | Where | Needs this build? |
|---|---|---|
| Valuations no longer come back as "$1–5" | backend `87e62c5` | no — already live |
| A depleted AI quota is no longer retried | backend `1f75d72` | no — already live |
| `/health` reports when the model is down | backend `1f75d72` | no — already live |
| Safety-blocked photos are detected again | backend `87e62c5` | no — already live |
| The app now *shows* why a photo was rejected | iOS `5112c58` | **yes** |

The valuation fix is deliberately **not** claimed in the App Store copy below:
it went live days before this build reaches anyone, so describing it as new in
1.3.1 would be false for every user who reads it.

---

## Primary — paste into App Store Connect

```
Clearer answers when a scan doesn't work.

If a photo can't be read — too dark, too far away, more than one item in
frame — SnapWorth now tells you what to change instead of just saying
something went wrong.

Behind the scenes we also fixed the cause of some inaccurate valuations.
That fix is already live, so your estimates are current whether or not
you update.
```

**Character count:** well within the 4,000 limit.

### Alternative, if you prefer the shortest possible

```
When a photo can't be read, SnapWorth now tells you what to change instead
of just saying something went wrong. Plus a fix for some inaccurate
valuations, already live on your device.
```

---

## Copy constraints this text respects

**It does not claim the valuation fix as new to this build.** It shipped
server-side first. Saying "more accurate valuations" as a 1.3.1 feature would
be untrue for anyone who reads the notes, and the honest phrasing costs one
extra clause.

**No "exact value" anywhere** — the app returns a range with a confidence
score.

**"too dark, too far away, more than one item in frame"** are the real
conditions the backend rejects on, not invented examples.

---

## What went wrong, for the record

Three defects produced one symptom: users saw `$1–5` valuations, and before
that a total scan outage.

1. **The AI account's prepaid credits ran out.** Every call returned
   RESOURCE_EXHAUSTED. Not a code bug — but the hard stop was retried like a
   transient blip, and `/health` reported `status: ok` throughout, because it
   only ever checked that an API key was *configured*, never that the model
   answered.

2. **`max_output_tokens` was sized for the payload alone.** gemini-2.5-flash
   draws its reasoning tokens from the same ceiling — measured at 1138–1777 per
   scan against a ~700-token answer. The shipped 2048 left as little as 256
   tokens for JSON, so replies truncated before the price fields. `/scan` then
   substituted the constants `1.0` and `5.0` and presented them as a valuation.

3. **The finish-reason helper could not read the real SDK container.** A guard
   written as `isinstance(candidates, (list, tuple))` rejects proto's
   `RepeatedComposite`, so it returned `""` for every genuine response while
   unit tests passing plain lists kept passing. That is why the truncation was
   silent — and why safety blocks were never detected, reaching users as "the
   AI service is unavailable" instead of a photo they could retake.

Operational detail is in `docs/RUNBOOK.md` §5.3 and §5.9.

## Before submitting

Nothing outstanding that is new to this release. The App Privacy labels were
settled for 1.3.0 and are unchanged — this build adds no new data collection.
