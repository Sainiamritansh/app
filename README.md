# WavyGo OS

Internal operating system (ERP) for **WAVYGO MOBILITY SERVICES PRIVATE LIMITED** — one dashboard for the rental marketplace, tasks, people, opportunities, internal chat and the company calendar.

| | |
| :--- | :--- |
| **Backend** | FastAPI · Motor (async MongoDB) · JWT auth |
| **Database** | MongoDB |
| **Frontend** | React 19 (Create React App + CRACO) · Tailwind CSS · shadcn/ui · Recharts |
| **Product spec** | [`memory/PRD.md`](memory/PRD.md) — scope, roadmap and the contracts that must not change |

---

## Roles

Five roles, defined once in [`backend/permissions.py`](backend/permissions.py) and mirrored in [`frontend/src/constants/permissions.js`](frontend/src/constants/permissions.js). Change both together.

| Module | Founder | Admin | Manager | Employee | Intern |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Dashboard | Company-wide, incl. revenue | Team view | Team view | Personal | Personal |
| Marketplace (bookings, fleet, vendors) | ✅ | — | — | — | — |
| Task Board | ✅ all | ✅ all | Own department | Own + create | Assigned only |
| Opportunity Hub | ✅ | ✅ | Own department | Assigned | — |
| Employees (directory, invites, leave, attendance) | ✅ | ✅ | Own department | Own workspace | Own workspace |
| WavyGo Connect (chat) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Calendar | ✅ all events | ✅ all events | Create + visible events | Create + visible events | Visible events |
| Activity Logs | ✅ all | ✅ all | Team | — | — |
| Company Vault · Finance | ✅ | — | — | — | — |
| CRM · Marketing | ✅ | ✅ | ✅ | — | — |
| Analytics | Marketplace + operations | Operations | Own department | — | — |
| WavyGo AI | ✅ | ✅ | ✅ | ✅ | ✅ (answers only from data the role can see) |

The permission files are the source of truth; this table is a summary.

---

## Modules

### Built
- **Dashboard** — KPIs, revenue and booking charts, city and vendor performance, today's tasks, upcoming calendar events, activity feed, system status. All figures come from the database; empty data shows as empty, never as sample numbers.
- **Marketplace** — cities, vendors, vehicles, customers, bookings, pricing, coupons, reviews, KYC workflow, support tickets, analytics.
- **Task Board** — Kanban (drag and drop), list and calendar views; comments, PDF attachments, links, assignee notifications.
- **Employees** — directory by department, invitations (7-day links), one-click check-in / check-out with worked hours, leave requests with approval, performance reviews, departments.
- **Opportunity Hub** — partnership / deal pipeline with assignment, status and value tracking.
- **WavyGo Connect** — channels, private groups, direct messages, announcements, unread counts.
- **Calendar** — month / week / day / agenda views, participants, visibility (everyone / department / private), reminders delivered as notifications, deep links from notifications.
- **Notifications**, **Activity Logs**, **Settings** (profile, company, theme, security, roles), **About WavyGo**.

- **Company Vault** — company documents in folders with tags, versions, inline preview, expiry dates and reminders (30 days, 7 days, expired). Files are stored in MongoDB GridFS.
- **Finance** — customer invoices from bookings (GST, financial-year numbering, print), vendor payout batches, monthly statements, CSV exports, editable commission / GST settings.
- **CRM** — customer 360 (lifetime value, lifecycle stage, timeline of bookings, tickets, KYC and reviews), notes, tags, follow-ups and saved segments.
- **Marketing** — campaigns with budget, spend, channels, cities, audience segment and coupons; results attributed from bookings that used the campaign's coupons.
- **Analytics** — marketplace KPIs, trends, cohort retention, city drill-down, fleet utilisation, booking heatmap (Founder); team operations for Admin / Manager; CSV export.
- **WavyGo AI** — chat assistant (Claude) that answers from company data through read-only tools, scoped to what the user's role can see. Needs `ANTHROPIC_API_KEY`.
- **Password reset by email** — single-use links valid for 30 minutes (needs Brevo configured).

All pages refresh their data automatically (every 30–60 s while visible, and when you return to the tab).

---

## Project structure

```text
backend/
  server.py              FastAPI app: routers, CORS, startup (seed, indexes, calendar reminders)
  db.py                  Mongo client + ObjectId helpers
  auth_utils.py          password hashing, JWT, get_current_user / require_roles
  permissions.py         RBAC matrix (modules + actions)
  hub_utils.py           log_activity, notify, serialize
  email_utils.py         transactional email (Brevo), optional
  models.py              auth / user models
  models_part2.py        module models (tasks, employees, opportunities, calendar, …)
  seed.py                idempotent Founder account + indexes, runs on startup
  seed_part2.py          optional demo data — never runs automatically
  routers/               one router per module, all mounted under /api
  tests/                 pytest suites (see Testing)
frontend/
  src/App.js             routes; every page renders inside AppShell
  src/pages/             one page per module
  src/components/        layout shell, calendar, shadcn ui primitives
  src/constants/         nav, permissions, test ids
  src/lib/api.js         axios client (token refresh built in)
memory/                  PRD and freeze notes
docs/                    long-form project documentation
```

---

## Running locally

### Prerequisites
- Python 3.11+ (3.12 used in development)
- Node.js 18+ (22 used in development)
- MongoDB 6+ running locally, or a MongoDB Atlas connection string

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then fill in the values below
uvicorn server:app --reload --port 8000
```

- API: http://localhost:8000/api
- Interactive docs: http://localhost:8000/docs

On first start the server creates the Founder account from `FOUNDER_EMAIL` / `FOUNDER_PASSWORD`, creates indexes and starts the calendar reminder loop.

### 2. Frontend

```bash
cd frontend
npm install
npm start                       # http://localhost:3000
```

`npm run build` produces a production build in `frontend/build`.

---

## Environment variables

### `backend/.env`

| Variable | Required | Purpose |
| :--- | :---: | :--- |
| `MONGO_URL` | ✅ | MongoDB connection string, e.g. `mongodb://127.0.0.1:27017` |
| `DB_NAME` | ✅ | Database name |
| `JWT_SECRET` | ✅ | Long random string used to sign tokens |
| `CORS_ORIGINS` | ✅ | Comma-separated frontend origins, or `*` in development |
| `FOUNDER_EMAIL` | ✅ | Founder login created on first start |
| `FOUNDER_PASSWORD` | ✅ | Founder password — always set it; never rely on a default |
| `FOUNDER_NAME` | | Founder display name |
| `FRONTEND_URL` | ✅ | Public URL of the frontend, used in invitation links |
| `BREVO_API_KEY` | | Enables invitation / notification emails. Without it, emails are skipped and invite links are shown to copy instead |
| `BREVO_SENDER_EMAIL` | with Brevo | Verified sender address |
| `BREVO_SENDER_NAME` | | Sender display name |
| `CALENDAR_REMINDER_INTERVAL_SECONDS` | | How often reminders are checked (default `60`) |
| `ANTHROPIC_API_KEY` | for AI | Enables WavyGo AI. Without it the AI page shows a setup message |
| `ANTHROPIC_MODEL` | | Claude model for WavyGo AI (default `claude-opus-5`) |
| `WAVYGO_AI_RATE_LIMIT` | | AI messages per user per hour (default `30`) |

Never commit `.env` files or API keys.

### `frontend/.env`

| Variable | Purpose |
| :--- | :--- |
| `REACT_APP_BACKEND_URL` | Backend origin, e.g. `http://localhost:8000` (the client adds `/api`) |

---

## API overview

All routes are under `/api` and, except login and the public login-page KPIs, need `Authorization: Bearer <access token>`. Full, always-current reference: `/docs`.

| Area | Base path | Highlights |
| :--- | :--- | :--- |
| Auth | `/api/auth` | login, refresh (rotating), logout, me, register |
| Users | `/api/users` | profile, password, directory |
| Dashboard | `/api/dashboard` | stats, public live KPIs |
| Marketplace | `/api/marketplace` | cities, vendors, vehicles, customers, bookings, KYC, support, analytics |
| Tasks | `/api/tasks` | CRUD, status (drag-drop), comments, files, stats |
| Employees | `/api/employees` | directory, invitations, attendance + check-in/out, leave, performance, departments |
| Opportunities | `/api/opportunities` | CRUD, assign, status, stats |
| Connect | `/api/connect` | channels, members, messages, read state |
| Calendar | `/api/calendar` | events CRUD, month / week / day / agenda, invitees |
| Notifications | `/api/notifications` | list, unread count, mark read |
| Activity | `/api/activity` | audit log |
| Settings | `/api/settings` | company profile, roles |
| Company Vault | `/api/vault` | folders, documents, versions, downloads, stats |
| Finance | `/api/finance` | settings, invoices, payouts, statements, exports |
| CRM | `/api/crm` | customers, customer 360, notes, tags, follow-ups, segments |
| Marketing | `/api/marketing` | campaigns, attribution, overview |
| Analytics | `/api/analytics` | marketplace, operations, CSV exports |
| WavyGo AI | `/api/ai` | status, conversations, streamed messages |

---

## Testing

```bash
cd backend
.venv/Scripts/python -m pytest tests/ -k local -q      # Windows path; use .venv/bin/python on macOS/Linux
```

- `tests/*_local_test.py` are self-contained: each module starts its own server on a free port against a throwaway MongoDB database (see [`tests/local_harness.py`](backend/tests/local_harness.py)) and deletes it afterwards. Only a local MongoDB is needed.
- `tests/backend_test.py`, `backend_part2_test.py`, `rbac_test.py` are older end-to-end suites that call a deployed instance (`REACT_APP_BACKEND_URL`). Don't point them at production data.

---

## Deployment

- Backend: `Procfile` runs `uvicorn server:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"`. Trusting forwarded headers from any address is right only when the app is reachable solely through the hosting platform's proxy (Render, Railway, Heroku and similar); otherwise set `--forwarded-allow-ips` to the proxy's address.
- WavyGo AI streams responses (Server-Sent Events); a reverse proxy in front of the backend must not buffer `text/event-stream`.
- Frontend: static build; `frontend/vercel.json` rewrites all routes to `index.html`.

## License

No license has been chosen yet. Until one is added, all rights are reserved by WAVYGO MOBILITY SERVICES PRIVATE LIMITED.
