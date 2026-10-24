# Upwork / Contra profile (draft)

Paste-ready. `{BRACES}` are yours to fill. Every claim about SnapWorth is
from `case-study.md`, which cites the code and commits behind it.

## Title

iOS Developer (SwiftUI, StoreKit 2) · AI features · App Store launches

## Overview (300 words)

I build iOS apps end to end and ship them to the App Store. My own app,
SnapWorth, is live with paying subscribers: point the camera at a thrift
find and get an AI resale estimate in seconds. I built all of it alone: the
SwiftUI app, the Python backend, the website and the release pipeline.

What that means for your project:

• SwiftUI apps with SwiftData, AVFoundation camera work, WidgetKit widgets,
Live Activities, Control Center controls and App Intents.

• StoreKit 2 subscriptions done right: server-side transaction checks,
App Store Server Notifications V2, free trials and paywalls that pass
review.

• AI features that hold up in production. SnapWorth sends photos to Google
Gemini through a FastAPI backend with rate limiting, input validation and
App Attest device checks, so the API key never ships in the app and the
model can't be abused through it.

• App Store releases without surprises. I've written review notes,
handled metadata in five languages, kept privacy manifests and App Privacy
labels in step with the binary, and fixed the issues App Review cares about
before submission.

• Code you can keep. Around 3,600 automated tests across the app and
backend, GitHub Actions CI on every push, secret scanning over the whole
git history, and a runbook for production.

I work in small, reviewed pull requests with clear commit messages, so
you can see exactly what changed and why. I explain trade-offs before I
build, tell you when an idea will cost more than it returns, and write down
the decisions so the next developer isn't guessing.

Good fits: an MVP you want in the App Store in weeks, adding AI or a
camera feature to an existing app, setting up or fixing subscriptions, or
getting an app through a rejection.

Based in {CITY}, {TIMEZONE}. {LANGUAGES}.

## Skills

Swift · SwiftUI · SwiftData · UIKit · StoreKit 2 · In-App Purchases · App
Store Server API · WidgetKit · Live Activities · App Intents · AVFoundation ·
App Attest · String Catalogs (localization) · XCTest · Python · FastAPI ·
Redis · Docker · REST API design · Google Gemini API · LLM integration ·
Prompt engineering · GitHub Actions · CI/CD · App Store Connect · App Store
Optimization · TestFlight · Vercel

---

## Proposal templates

Keep each under 150 words once filled. Open with their problem, not with you.

### 1 · "Build my iOS MVP"

> Hi {NAME},
>
> {ONE LINE SHOWING YOU READ THEIR BRIEF: the core action their users take}.
> The fastest way to an App Store MVP is to build that one flow properly and
> leave the rest for after real users have tried it.
>
> I've done this for my own app, SnapWorth: SwiftUI, SwiftData and a small
> Python backend, live on the App Store with paying users ({APP STORE LINK}).
>
> How I'd run it:
> 1. A short call to cut the scope to the one flow.
> 2. A TestFlight build every week, so you see progress, not reports.
> 3. Submission with the review notes, privacy labels and metadata done.
>
> Estimate: {WEEKS} weeks for {SCOPE}. One question first: {QUESTION}?
>
> {YOUR NAME}

### 2 · "Add AI to my app"

> Hi {NAME},
>
> Adding {AI FEATURE} is mostly not the model call. It's keeping the API key
> off the device, handling the slow and failed responses, and showing users
> how sure the result is.
>
> In SnapWorth, photos go to Google Gemini through my own FastAPI backend.
> It has rate limits, input checks and App Attest, so only the real app can
> call it, and every estimate comes with a confidence level, not a bare
> number.
>
> For your app I'd {PLAN: endpoint, model, where it sits in the UI}, with a
> fallback for when the model is slow or wrong.
>
> Estimate: {DAYS} days. Which model or provider are you using now, if any?
>
> {YOUR NAME}

### 3 · "StoreKit subscriptions / App Store rejection help"

> Hi {NAME},
>
> {IF REJECTION: "The guideline Apple cited, {GUIDELINE}, usually means
> {PLAIN MEANING}."} I can read the rejection, find the cause, and either fix
> it or draft the reply to App Review.
>
> I run StoreKit 2 subscriptions in production on my own app: server-side
> transaction checks, App Store Server Notifications V2, trials, and
> paywalls that passed review.
>
> What I need: the rejection message (or what's broken with purchases), and
> access to the project. Most fixes like this take {DAYS} days, and you get
> a build to test before anything goes back to Apple.
>
> {YOUR NAME}
