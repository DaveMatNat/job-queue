# FUTURE — deliberately not built

Ideas that came up during the build and were excluded, either by the hard
non-goals or by scope discipline. Recorded so they aren't re-litigated.

- **Browser automation / form filling** — excluded by design. The tool stages;
  David submits. Bot detection, per-company Workday accounts, and irreversible
  bad submissions make this net-negative even where it's technically possible.
- **International listings** — `INTERN_INTL.md` in both speedyapply repos parses
  with the exact same code path. Add the URLs to `speedyapply.py` if the search
  ever goes international.
- **Simplify `category` / `degrees` as scoring inputs** — the fields exist and
  are kept in `raw`; a future `role_fit` term could use `category` instead of
  title keywords. Not done now: title keywords are explainable and configurable.
- **Streaming JSON parse for Simplify** — the file is 10.7 MB and growing. If it
  crosses ~50 MB, swap `resp.json()` for ijson. Not worth a dependency today.
- **ntfy action buttons** — ntfy supports tap-to-open actions; the digest could
  deep-link each listing's apply URL. Kept plain-text to stay within budget.
- **Auto-detecting closed postings** — a listing that disappears from all
  sources is probably closed; we could flag rows unseen for N days. Cheap to add
  on top of `source_records.last_seen_at`.
