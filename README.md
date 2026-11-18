# Document Check — prototype UI (proxy pattern)

A lightweight front-end for the Document Verification service. The browser only talks to
`server.py` (same origin, so the upstream service needs no CORS change). The server forwards
each upload to the upstream `POST /api/analyze` and returns its JSON; all result processing
(normalising, verdicts, filtering, exports) happens in the browser.

```
browser ──/api/analyze──▶ server.py (proxy) ──▶ fraud-detection Cloud Run service
        ◀── JSON ────────                   ◀── JSON ──
```

## Run

Requires Python 3.9+ — standard library only, nothing to install.

```powershell
cd docverify-ui
python server.py
```

- App: http://127.0.0.1:8080
- Demo with a saved sample result (no upstream call): http://127.0.0.1:8080/?demo

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `UPSTREAM_URL` | the Cloud Run URL | Verification service base URL |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | Bind address. Use `0.0.0.0` in a container |
| `MAX_UPLOAD_MB` | `15` | Request size cap |
| `MAX_CONCURRENT` | `4` | Upstream calls in flight (cost/abuse brake) |
| `UPSTREAM_TIMEOUT` | `180` | Seconds allowed per analysis |

## Files

- `server.py` — static file server + `/api/analyze` and `/api/health` proxy
- `static/app.js` — upload queue (2 at a time), response normalisation, rendering, CSV/JSON export
- `static/styles.css` — light/dark themes, responsive layout
- `static/demo/` — one saved upstream response for `?demo`

## Front-end processing

`normalize()` in `app.js` turns the upstream response into a stable shape, so a missing or
renamed field degrades to "Not returned" instead of breaking the page:

- score (0–100), risk level (falls back to score bands if absent), document type
- findings sorted by weight
- checks: web image search, receipt arithmetic, ML model, merchant lookup, ID verification, manipulation/AI
- detector flags, OCR text, raw JSON

Service data is rendered with `textContent` only, and served under a strict
Content-Security-Policy. The CSV export guards against spreadsheet formula injection.

## Before using beyond a prototype

- **Authentication** on the proxy (e.g. Cloud Run IAM / IAP, or your SSO). Without it anyone
  who can reach the proxy can spend your Vision/Places quota.
- **Rate limiting** per user, not just the global concurrency cap.
- **Hosting**: deploy as its own Cloud Run service (add a Dockerfile; set `HOST=0.0.0.0`);
  then lock the upstream service to that proxy's service account.
- **Data handling**: uploads are held in memory only and never logged; confirm that meets your
  retention policy for ID documents.
- `ThreadingHTTPServer` is fine for a prototype; use a production server (e.g. a small
  FastAPI/uvicorn app) for real load.
