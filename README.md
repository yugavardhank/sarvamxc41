# Tokri

A voice-first grocery reordering assistant for supermarkets. Tokri calls
(or is called by) a customer, listens to their order, negotiates on price
within limits the merchant sets, and takes payment - all by voice, in their
own language, using their real order history to decide how generous to be.

Built on:
- **Sarvam** - the voice agent itself (speech, language, the conversation),
  plus its Instant Outbound API to actually dial a phone
- **Cashfree** - real sandbox payment links, and a prepaid wallet balance
  built on top of them
- **MCP (Model Context Protocol)** - how Sarvam's agent calls into this
  backend for every price, discount and credit decision

This README explains how the whole system fits together, how to run it,
and how it's wired into Sarvam. See `TESTING.md` for a quick team-facing
"how to test this right now" guide, and `ARCHITECTURE.md` for the original
product spec this was built from.

## The big picture

```
 Customer's phone
       │  (real call, or Sarvam's own Test-agent simulator)
       ▼
 Sarvam Voice Agent ("Bada tokri")
   - does all the talking, in Hindi/Kannada/English
   - decides WHEN to call a tool (greeting, pricing, objection, payment)
   - never invents a price or a discount - only repeats what a tool said
       │  MCP tool calls (start_session, price_basket, get_offer,
       │  accept_offer, set_payment_and_link, log_outcome)
       ▼
 app/mcp_server.py  (this repo, its own venv: .venv-mcp)
   - the ONLY thing Sarvam actually talks to
   - thin wrapper: each tool calls straight into app/services + app/pipeline
       │
       ▼
 app/services/*.py + app/pipeline.py  (the actual business logic)
   - catalog matching, tiers/credit/churn policy, the negotiation ladder,
     the wallet, saved lists, Cashfree payment links
       │
       ▼
 tokri.db  (one SQLite file, created automatically)
   - synced from tokri_Database.xlsx at startup / on demand
       │
       ▼
 Cashfree sandbox  -->  real payment link, real checkout page, real webhook
```

There's a second, independent way to drive the exact same backend: the
**dashboard** (`app/main.py`, its own venv: `.venv`) is a browser UI where
you type what a customer would say instead of actually calling - useful for
fast iteration without burning Sarvam call minutes. Both paths - the real
voice agent via MCP, and the dashboard's "Simulate call" - go through the
same `app/services`/`app/pipeline` code, so a fix in one fixes both.

## Why two separate Python environments (`.venv` and `.venv-mcp`)

The `mcp` SDK (used by `app/mcp_server.py`) depends on a newer `starlette`
than the main FastAPI app (`app/main.py`) is pinned to. Rather than fight
that version conflict, they run as two separate processes in two separate
venvs. Both import the same `app/` package and share the same `tokri.db` -
there is exactly one copy of the business logic, just two separate
processes serving it over two different protocols (plain HTTP for the
dashboard, MCP for Sarvam).

## Project layout

```
app/
  main.py              FastAPI app: dashboard + dev/demo routes + Cashfree webhook
  mcp_server.py         MCP server: the 6 tools Sarvam's agent actually calls
  config.py             All environment variables, one place
  db.py                 SQLite schema + one-time seed data (fallback if no spreadsheet)
  models.py             Pydantic models (order status enum, etc.)
  pipeline.py            Call orchestration: start call, handle a turn, pay, confirm
  services/
    catalog.py           Product matching (brand/alias/budget aware), substitutions
    policy.py            Tiers, churn risk, credit-blocked, discount caps (the merchant policy)
    negotiate.py          The concession ladder: swap brand -> free delivery -> bundle -> loyalty
    wallet.py             Prepaid wallet: top-up via Cashfree link, applied first on any order
    lists.py              Named saved grocery lists ("save this as my Sunday list")
    orders.py             Order/basket persistence and pricing math
    cashfree.py           Real Cashfree Payment Links API (create/cancel/check status)
    sarvam.py             Mock-able STT/TTS/extraction + the real Instant Outbound call trigger
    xlsx_sync.py          Reads tokri_Database.xlsx and upserts it into the live database
  routes/
    dev.py                Dashboard's own endpoints (simulate call, wallet top-up, etc.)
    relay.py              The HTTP-contract version of the 6 tools (same logic, REST shape)
    cashfree.py           Cashfree's payment webhook
  static/dashboard.html   The whole dashboard UI (customers, live call, inventory, orders)
tests/                    pytest suite - 42 tests, all against mocked Sarvam/Cashfree
```

## Setup

```bash
# main app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# MCP server (separate venv - see "why two venvs" above)
python3 -m venv .venv-mcp
source .venv-mcp/bin/activate
pip install "mcp[cli]<2" httpx python-dotenv

cp .env.example .env
```

Fill in `.env` (see the full variable reference below). With
`MOCK_SARVAM=1` and `MOCK_CASHFREE=1` (the defaults), everything runs with
zero real credentials: Sarvam calls get deterministic rule-based stand-ins
and Cashfree links are fake URLs you can "mark paid" from the dashboard.
Flip both to `0` once you have real keys.

## Running it

```bash
# terminal 1 - the dashboard + webhook + REST contract
source .venv/bin/activate
uvicorn app.main:app --reload

# terminal 2 - the MCP server Sarvam's agent actually calls
source .venv-mcp/bin/activate
MCP_ALLOWED_HOST=<your-public-hostname> python -m app.mcp_server
```

Open `http://127.0.0.1:8000/` for the dashboard.

### Making it reachable from Sarvam (public tunnels)

Sarvam's cloud needs to reach both processes over HTTPS. The quickest way
with zero signup is [cloudflared](https://github.com/cloudflare/cloudflared)'s
"quick tunnel":

```bash
cloudflared tunnel --url http://localhost:8000   # for the dashboard/webhook
cloudflared tunnel --url http://localhost:8001   # for the MCP server
```

Each prints a random `https://<random-words>.trycloudflare.com` URL.
**These are temporary** - they only work while that `cloudflared` process
keeps running, and change every time you restart it. Put the dashboard one
in `.env` as `PUBLIC_BASE_URL` (used for Cashfree's webhook notify URL) and
`MCP_ALLOWED_HOST` as the MCP tunnel's bare hostname (required - the MCP
server's DNS-rebinding protection rejects any other Host header).

## Environment variables

```
# Sarvam - voice/STT/TTS/chat extraction
SARVAM_API_KEY=                  # from the Sarvam dashboard

# Sarvam - Voice Agents (Samvaad) platform: real outbound calling
SARVAM_VOICE_AGENTS_API_KEY=     # Deploy with code -> Generate API key (DIFFERENT key from above)
SARVAM_ORG_ID=                   # Settings -> API Key page shows this
SARVAM_WORKSPACE_ID=             # same page
SARVAM_APP_ID=                   # your agent's id (Build -> Agents)
SARVAM_APP_VERSION=              # which committed version of the agent to call
SARVAM_CONNECTION_ID=            # Deploy -> Phone numbers -> your connection's id
SARVAM_AGENT_PHONE_NUMBER=       # the number that connection owns

# Cashfree - sandbox payment links
CASHFREE_APP_ID=                 # Payment Gateway -> Developers -> API Keys (Test mode)
CASHFREE_SECRET=
CASHFREE_ENV=sandbox             # or 'production'

# Infra
PUBLIC_BASE_URL=                 # the dashboard's public tunnel URL (for Cashfree's webhook)
TOOL_SHARED_SECRET=              # required header value on every MCP-contract REST call (app/routes/relay.py)
XLSX_PATH=                       # defaults to ~/Downloads/tokri_Database.xlsx
DB_PATH=tokri.db
MOCK_SARVAM=1                    # 0 = real Sarvam API calls
MOCK_CASHFREE=1                  # 0 = real Cashfree sandbox calls
```

Getting the `SARVAM_*` outbound-calling values is the fiddliest part -
they live scattered across Sarvam's dashboard (Build, Deploy, Settings).
See the commit history / team chat log for the exact click-paths we found;
there's no single page that lists them all.

## How the spreadsheet becomes live data

`tokri_Database.xlsx` (customers + inventory + merchant policy) is the
source of truth, **not** a one-time copy. `app/services/xlsx_sync.py`
reads it directly and upserts into `tokri.db`:

- automatically, once, at server startup (if the file exists at `XLSX_PATH`)
- on demand, any time, via `POST /dev/reload-inventory` (also wired to a
  button-free curl/dashboard call - edit the sheet, hit this endpoint, the
  change is live with no restart)

Products are upserted by `sku`; customers by `phone`. Only the fields the
sheet actually owns are overwritten (name, email, language, credit limit/
outstanding, on-time rate, order-history baseline, missing-data flags) -
`wallet_balance` and anything else accrued through live testing is left
alone. If `tokri_Database.xlsx` isn't found, `app/db.py`'s own hardcoded
seed data (a frozen copy taken from the sheet on 2026-11-21) is used as a
fallback so the app still boots.

## The policy engine (`app/services/policy.py`)

Every customer gets a computed profile, never a hand-set one:

- **Tier**: Gold (6+ orders in 90 days, 90%+ on-time), Silver (2+ orders),
  else New
- **Churn risk**: last order over 30 days ago, despite having order history
- **Credit blocked**: outstanding at/above their credit limit, or on-time
  rate under 70%
- **Discount cap**: Gold 5%, Silver 4%, win-back (churn-risk Gold/Silver)
  7%, New 2% (prepay only). Credit-blocked is a *ceiling*, not a flat
  replacement: `min(3%, whatever the cap otherwise would have been)` - a
  credit-blocked New customer still only gets 2%, not 3%.

The voice agent is never told the raw tier/cap number in conversation - it
only ever gets to offer what `app/services/negotiate.py` actually returns.

## The concession ladder (`app/services/negotiate.py`)

One rung at a time, never skipped, never repeated, never below a SKU's
`floor_price`:

1. **Swap brand** - to a cheaper equivalent in the same category (uses the
   real `cheaper_equivalent_sku` chain from the spreadsheet: Premium -> Mid
   -> Economy)
2. **Add for free delivery** - if one cheap item would cross the free-
   delivery threshold, suggesting it nets out cheaper than the delivery fee
3. **Bundle discount** - a real "buy 2" bundle price from the spreadsheet,
   if the customer already ordered 2+ of something
4. **Loyalty discount** - whatever discount room is left under the cap

Each accepted offer is logged to the `concessions` table and shown in the
dashboard's Orders table.

## The wallet (`app/services/wallet.py`)

Tokri's own prepaid balance - topped up via a normal Cashfree payment link,
tracked as a plain column on the customer row (not a separate Cashfree
product). It's applied automatically, first, before any new payment link
is created - the retention mechanic: a customer with money already sitting
in Tokri has a reason to keep ordering through Tokri specifically.

## Saved lists (`app/services/lists.py`)

"Save this as my Sunday list" after building a basket; "order from my
Sunday list" later reloads the same items in one line. Matched by name,
fuzzily (substring either direction), per customer.

## Testing

```bash
source .venv/bin/activate
pytest tests
```

42 tests, all against `MOCK_SARVAM=1`/`MOCK_CASHFREE=1` forced on
regardless of `.env` - the suite never makes a real network call. Covers
policy/tier edge cases (including the credit-blocked ceiling math), the
negotiation ladder, wallet application, saved lists, and full order flows.
(`documents/` holds an earlier, unrelated project's own test suite - don't
scope pytest to the repo root, scope it to `tests/`.)

For manual/live testing, see `TESTING.md` - it has the real seeded test
phone numbers and what each one demonstrates.

## Known limits

- The catalog/customer data is real (from the spreadsheet) but still demo
  data - no live supermarket inventory feed.
- Credit is a demo ledger, not a real regulated credit product.
- No real-time barge-in/interruption handling - that's entirely Sarvam's
  own voice-agent behavior, outside this repo.
- Call scheduling (automatically finding who's due for a call) isn't
  built - calls are triggered on demand (dashboard button, or Sarvam's own
  Campaigns feature for bulk outbound).
- The public tunnels are temporary dev conveniences, not a production
  deployment - for anything long-lived, host both processes somewhere with
  a stable domain instead.
