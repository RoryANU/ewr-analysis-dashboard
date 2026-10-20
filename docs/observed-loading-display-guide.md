# Observed-data loading display: implementation guide

Responds to UX feedback from a non-technical reader of the "Fetching gauges · N of M" panel. The panel is accurate but reads like a developer log: it doesn't say how bad a problem is, why it happened, what it means for the charts, or what to do next.

## Where the code lives

| Concern | Location |
|---|---|
| Panel markup, status rows, `failureLabel`, `stateIcon` | `observedPanel()` in `src/ewr_analysis/dashboard_template.html` (~L565-594) |
| Panel CSS (`.obs-*`) | `src/ewr_analysis/dashboard_template.html` L115 |
| Job polling, `S.obs.status`, `S.obs.progress`, retry/cancel handlers | `src/ewr_analysis/dashboard_template.html` ~L500-556 |
| Per-variable result (`GaugeResult.status` / `.message`) and progress callback | `src/ewr_analysis/observed.py` (`_fetch_gauge_cached`, `fetch_many`) |
| Generated dashboard | `ewr_analysis_dashboard.html` is build output. Edit `src/`, then rebuild with `src/ewr_analysis/build_dashboard.py`. |

Statuses already produced: `ok`, `empty`, `timeout`, `rate_limited`, `unsupported`, `error`. Variable keys are `f` (flow) and `l` (level).

## Feedback to change mapping

| # | Feedback | Change |
|---|---|---|
| 1 | Can't tell what's wrong or if it matters | Summary line + per-row outcome icon and colour |
| 2 | Jargon (variables, BoM, cache, source busy) | Plain-language message dictionary; raw text moved to a Details disclosure |
| 3 | Unclear impact on analysis | Per-row impact sentence, plus an overall impact note |
| 4 | No next step | Per-failure action text; retry only what can succeed; auto-retry for transient errors |
| 5 | "12 of 16" implies success | Count outcomes, not completions; show waiting rows |
| 6 | Layout: far-apart columns, wrapping, joined messages | Two-line stacked row; one message per variable |

## Step 1: Classify, don't just count (pure functions)

Add two pure helpers above `observedPanel()` so they can be unit-tested without the DOM.

```js
const VAR_NAME = {f: "Flow", l: "Water level"};

// One variable result -> plain-language description
function describeResult(v, x) {
  // x = {status, message}
  // returns {label, why, action, retryable, severity}
}

// One gauge's {f, l} results -> outcome
function classifyGauge(states) {
  // returns {outcome: "complete"|"partial"|"nodata"|"failed"|"waiting"|"running", okVars:[], badVars:[]}
}
```

Outcome rules:
- no status yet: `waiting`
- any variable still `running` / `pending`: `running`
- all `ok`: `complete`
- some `ok`: `partial`
- none `ok`, all `empty`/`unsupported`: `nodata` (the source has nothing; not a fault)
- none `ok`, any `timeout`/`rate_limited`/`error`: `failed`

Keep "no data for the period" (`nodata`) distinct from "couldn't ask" (`failed`). Today both render as "0 of 2 variables", but they need different actions.

## Step 2: Message dictionary

Replace `failureLabel` with `describeResult`. Proposed copy (adjust wording with whoever owns the content):

| Trigger | Label | Why | Action | Retryable |
|---|---|---|---|---|
| message matches `stations.json` + `bom_water` | Weather-bureau station list not set up | The local copy of the Bureau of Meteorology station list is missing, so this gauge can't be looked up. | One-time setup. See `docs/observed-gauge-pull-guide.md`. Retrying won't help. | **No** |
| message matches `too many requests in queue` / `error_num 218` | Data service busy | The Bureau's server is handling too many requests. | We'll retry automatically. | Yes |
| `timeout` | Timed out | The data service didn't answer in time. | Retry. | Yes |
| `rate_limited` | Slowed down by data service | Too many requests too quickly. | Retry in a minute. | Yes |
| `empty` | No records for this period | The gauge has nothing recorded for the dates you chose. | Widen the date range or ignore this gauge. | No |
| `unsupported` | Not available from this source | This source doesn't provide this measurement for this gauge. | None. | No |
| `error` (other) | Unexpected problem | Something went wrong on our side. | Retry; if it persists, copy details for support. | Yes |

Always keep the raw `status` and `message` for the Details view (today they sit in a hover `title`, which is invisible on touch and to screen readers).

**Behaviour bug to fix on the way:** the current "Retry failed" filter (~L555, and `failures` at L568) retries `timeout`/`rate_limited`/`error`. The "BoM station cache missing" case surfaces as a generic `error`, so it is currently retried and fails again every time. Drive both off `describeResult(...).retryable` instead.

## Step 3: Summary line and honest progress

Replace `statusText` (L569) with two parts.

While running, use `Checking gauges: 12 of 16 done`. Don't say "Fetching".

Always show a tally from `classifyGauge`:

```
12 of 16 checked · 4 complete · 5 partial · 3 no data   (4 still waiting)
```

Add an impact note when anything is not `complete`:

> Gauges with missing data will show gaps in the observed results. EWRs that depend on the missing measurement can't be assessed for those gauges.

**Verify before shipping:** which EWRs need flow vs level vs both. Do not publish impact copy until that mapping is checked in `src/ewr_analysis/observed.py` / the EWR evaluation code. If level-only gaps don't affect results, say so explicitly, since that makes "partial" much less alarming. Write the per-row impact line from the same mapping.

Progress bar: keep `<progress>`, but pair it with the tally so 100% can't read as "all succeeded".

## Step 4: Row layout

Change `.obs-gauge-status` from a two-column flex row to a stacked row:

```
✓  410001 · Murrumbidgee R @ Wagga
   Flow ✓  Water level ✓
⚠  410006 · Tumut River @ Tumut
   Flow ✓  Water level ✗: Weather-bureau station list not set up
   [Details]
```

- Line 1: outcome icon, id, name (wrap freely; the name is the only long field).
- Line 2: one chip per variable, using `VAR_NAME` (never "N of M variables"). The reason is shown only beside the variable that failed. No comma-joined messages.
- Don't signal state by colour alone. Pair the icon with a text label (`Complete`, `Partial`, `No data`, `Failed`, `Waiting`) and give the icon an `aria-label`. Add tokens for green/amber/red/grey next to the existing `.obs-error` colour.
- Optional tidy-up: title-case gauge names for display (keep the raw name in Details). Fall back to "Unnamed gauge" when `g.name` is empty (the current `1AS` row); check `data/map/bom_gauge_stations.csv` to see whether a name exists to map in.
- Render `waiting` gauges as rows too. Today only gauges with a status entry are listed (L592 filter), which is why "12 of 16" shows 12 rows.

Ordering: keep the original order while a job is running, to avoid rows jumping. When the job finishes, sort by outcome (failed, partial, nodata, then complete). Group complete gauges under a collapsed "Show 4 complete gauges" `<details>`.

## Step 5: Actions

- Rename the button to `Retry N failed` using the count of retryable variables or gauges.
- Add automatic retry with backoff (e.g. 2 attempts, 10 s and 30 s) for results where `describeResult(...).retryable` is true. Note `fetch_many` already passes `retries` into the fetch layer, so first check whether busy responses are exhausting that budget. If so, raising or spacing it server-side is better than a client loop. Show "Retrying in 30 s" in the row while this happens.
- For non-retryable failures, show the action text as a plain sentence, not a button.
- Keep `Cancel` as is.
- Add a "Copy details" button inside Details (raw status and message for all non-ok variables) for support requests.

## Step 6: Tests

Follow the existing patterns in `tests/test_dashboard_core.js`, `tests/test_dashboard_html.py` and the `*_browser.cjs` suites.

1. Unit: `classifyGauge` for every combination (ok/ok, ok/err, err/err, empty/empty, running/ok, undefined).
2. Unit: `describeResult` against real message strings captured from the screenshot (`stations.json`/`bom_water`, `too many requests in queue`, `error_num 218`), asserting `retryable` and the label.
3. Unit: Retry selection excludes the station-cache case and includes busy/timeout.
4. Browser: render a mixed set of 16 gauges and assert the tally text, row order after completion, Details content, and that no raw jargon strings ("variables", "cache", "BoM") appear in visible text.
5. Existing `tests/test_observed*.py` should be unaffected, since this is front-end only. Backend changes are limited to the optional retry-budget tweak.

## Acceptance criteria

- A first-time reader can answer "did it work, and what's missing?" from the summary alone, without reading rows.
- No visible text uses "variables", "cache", "BoM", "source", or raw error strings. These live only under Details.
- Each non-ok row says what happened, what it affects, and what to do (or that nothing can be done).
- Retry never re-requests a failure that can't succeed.
- No row wraps awkwardly at the panel's narrowest width; the long station name wraps on its own line.
- Colour is never the only state indicator.

## Suggested delivery order

1. `classifyGauge` / `describeResult` plus tests, then fix retry filtering (small, fixes a real bug).
2. Summary tally, waiting rows and honest progress copy.
3. Stacked row layout, Details disclosure, completion sort/grouping.
4. Auto-retry, "Copy details" and impact copy (after the EWR-dependency check).
