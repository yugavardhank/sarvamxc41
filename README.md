# Tokri

A voice-first grocery reordering assistant for supermarkets, built on Sarvam
(voice, language, intent) and Cashfree (payment links + a prepaid wallet
ledger), with price negotiation inside merchant-set limits. See
`ARCHITECTURE.md` for the original product spec. This README covers setup,
running, the demo script, and how to wire a real voice orchestrator (Cashfree
Relay) on top.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

By default `MOCK_SARVAM=1` and `MOCK_CASHFREE=1`, so the app runs fully
without any real API keys: Sarvam calls are answered by deterministic
rule-based stand-ins, and Cashfree payment links are fake URLs you can mark
paid from the dashboard. Add your real keys to `.env` and flip the relevant
flag to `0` once you have them.

```
SARVAM_API_KEY=       # from Sarvam dashboard
CASHFREE_APP_ID=      # from Cashfree sandbox dashboard
CASHFREE_SECRET=
CASHFREE_ENV=sandbox  # or 'production'
PUBLIC_BASE_URL=      # https URL Cashfree/Relay can reach you at, for webhooks
```

## Run

```bash
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/ for the dashboard. It seeds four demo customers
on first run, covering the four negotiation branches:

- **Priya Sharma** (Gold, 5% cap) - has order history and a Rs.150 wallet
  balance, so her next payment is partly wallet-covered automatically. Good
  for demoing "same as last time" and the wallet-first payment split.
- **Ravi Kumar** (New, 2% cap, prepay only) - no history, no credit. Good for
  demoing catalog-driven matching and the brand-swap negotiation offer on a
  fresh basket.
- **Manoj Rao** (Silver, at churn risk -> 7% win-back cap) - last ordered 41
  days ago. His call greeting leads with a win-back line, and he has enough
  credit to confirm an order "on account" with no payment needed yet.
- **Lakshmi Iyer** (credit blocked, 3% cap, prepay only) - outstanding at her
  credit limit with a poor on-time rate. Asking to pay "on account" still
  gets forced to full prepayment.

## Demo script

1. **Negotiation + wallet**: Call Priya. Say `same as last time` - she gets
   her usual Tata toor dal + Tata salt read back. Say `that's too expensive`
   - Tokri offers the first ladder rung (a cheaper brand swap). Accept it,
   then `pay in full` - her Rs.150 wallet balance covers part of it
   automatically and a Cashfree link covers the rest. Mark it paid.
2. **Fresh customer + catalog matching**: Call Ravi. Say
   `toor dal tata, rice, oil, and atta` - each item is matched/priced from
   the catalog. Object on price again to see the swap offer, accept it, then
   try `pay in full` - as a New customer he's forced to prepay (no credit).
3. **Win-back / credit**: Call Manoj. His greeting opens with win-back
   wording. Order `rice`, then choose `on account` - since he has credit
   headroom, the order confirms immediately with nothing paid now.
4. **Credit blocked**: Call Lakshmi. Order `milk`, then try `on account` -
   she's credit-blocked, so Tokri still asks for full payment via a link.
5. **Saved lists**: Call any customer, build a basket, say
   `save this as my Sunday list`. Start a new call and say
   `order from my Sunday list` - the exact same basket reloads instantly.
6. **Wallet top-up**: On any customer card, click "Top up wallet", enter an
   amount, send the link, and it's marked paid in the dashboard - the
   balance is available on their very next call.

## Wiring a real voice orchestrator (Cashfree Relay)

`app/routes/relay.py` exposes a contract designed for an external voice
tool to drive, so the LLM/voice step does the talking while this backend
computes every price, discount and credit decision:

- `POST /session/start` `{phone, name, language}` -> customer profile
  (tier, churn risk, credit, wallet), the catalog, and the policy caps
- `POST /basket/price` `{order_id, items:[{text_or_sku, qty}]}` -> priced
  lines, subtotal, delivery fee, total, unmatched items
- `POST /negotiate` `{order_id, objection}` -> the next valid offer (or none)
- `POST /offer/accept` `{order_id, offer_id}` -> applies it, returns new total
- `POST /payment/plan` `{order_id, pay_now}` -> validates only (no link yet)
- `POST /payment/link` `{order_id}` -> creates the Cashfree link (or settles
  instantly from the wallet/credit if that already covers it)
- `POST /order/confirm` `{order_id}` -> marks paid, updates preferences
- `GET /api/customer/{id}` -> full computed profile for a dashboard/Relay step

CORS is open on all of these so a hosted Relay canvas can call them
cross-origin. Point Relay's HTTP actions at your deployed `PUBLIC_BASE_URL`
and give its voice step the extraction schema in
`app/services/sarvam.py` (payment choice, objection, list save/reference
phrases) as a guide for what it should extract per turn.

## Tests

```bash
pytest tests
```

(Scope to the `tests/` directory - `documents/` holds the unrelated Bharosa
project's own test suite.) Tests force `MOCK_SARVAM`/`MOCK_CASHFREE` on
regardless of `.env`, so the suite never makes a real network call.

## Known limits

- No real supermarket inventory integration; the catalog (`app/db.py`
  `PRODUCTS`) is seeded demo data.
- No real outbound phone call from this app directly - `app/routes/relay.py`
  is built so Cashfree Relay (or Twilio/Exotel) can place the real call and
  drive the conversation through these endpoints, but that wiring happens in
  Relay's own dashboard, not in this repo.
- No real-time barge-in or interruption handling in conversation.
- Credit is a demo ledger, not a real credit product; wallet balance is our
  own tracked balance funded through ordinary Cashfree payment links, not a
  separate Cashfree wallet API.
- Call scheduling (finding customers due for a call) is not automated yet;
  the dashboard lets you call any customer on demand.
