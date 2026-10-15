VERDICT: PASS

## Findings
None.

## Notes
- No iOS, website or localization change on this branch, so nothing reaches the app's users (the thrift resellers) and no catalog entry is needed. The only person who can see a changed response is the operator, who opens this route in a browser about once a year.
- Over the limit, the operator's browser shows the raw `apierrors` JSON (`{"detail":"Rate limit: 60 requests/hour.","code":"rate_limited"}`) with a `Retry-After` header, not the callback's styled HTML page. The spec rules this out ("Rendering the 429 as an HTML page ... Out of scope"), so it is not a finding. The text does say what happened but not what to do next. If this ever bites, the HTML 429 is the fix, and the page could say "ask the bot for a fresh link".
- Under the limit, the four existing pages (linked, failed, denied, unconfigured) are unchanged, as the spec requires. I read the route diff and did not start the server, because the builder's live check of the 429 body and header is recorded in build.md and the code path is the one `_enforce_ip_limit` already uses everywhere else.
- The corrected comment ("the operator's browser lands on it", not "TikTok's servers call it") matches the spec's assumption. The risk the spec names stays: an operator who has spent the shared 60/h bucket from the same network gets a 429 on the link, and the 10-minute state nonce may expire before they can retry. This is accepted in the spec and is not worth more than the RUNBOOK mention it already has.
