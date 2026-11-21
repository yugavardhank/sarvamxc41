# Tokri

Voice-first grocery reordering assistant with price negotiation, built on
Sarvam (voice/language/intent), Cashfree (payment links + a self-tracked
wallet/credit ledger), and designed to be driven by Cashfree Relay (or any
other HTTP-capable voice orchestrator). See `ARCHITECTURE.md` for the
original product spec and `README.md` for setup, run, and the demo script.

## Conventions

- Every external call (Sarvam, Cashfree) lives in `app/services/` behind a
  `MOCK_*` env flag. Never call an external API directly from a route
  handler.
- `app/pipeline.py` holds the shared start-call -> transcribe -> match ->
  negotiate -> pay -> learn logic used by the `/dev/*` dashboard routes.
  `app/routes/relay.py` is a *separate, thinner* contract (session/start,
  basket/price, negotiate, offer/accept, payment/plan, payment/link,
  order/confirm) for an external voice orchestrator to drive instead -
  it calls the same underlying services (`catalog`, `negotiate`, `orders`,
  `policy`, `wallet`), never pipeline.py's transcript-handling, since Relay's
  own LLM does the raw-speech extraction upstream.
- `app/services/policy.py` is the merchant policy: tiers (Gold/Silver/New),
  churn risk, credit-blocked, and the discount cap each implies. The
  negotiation ladder (`app/services/negotiate.py`) may only ever offer what
  this module allows - it never invents a discount or prices below a SKU's
  `floor_price`.
- `app/services/wallet.py` is Tokri's prepaid balance: topped up via a normal
  Cashfree payment link, tracked as our own ledger (not a separate Cashfree
  product), and applied automatically before any new payment link on a
  customer's next order - the retention mechanic.
- `app/services/lists.py` is named saved grocery lists ("save this as my
  Sunday list" / "order from my Sunday list"), fuzzy-matched by name.
- Order statuses: `REQUESTED`, `BASKET_DRAFT`, `NEGOTIATING`,
  `AWAITING_PAYMENT`, `CONFIRMED`, `ON_ACCOUNT`, `EXPIRED`, `CANCELLED`.
- Log every transcript, basket build, concession and order status change via
  `app.db.log_event`.
- Previous project in `documents/` is Bharosa (unrelated product, same
  sponsors' tech) - kept for reference only, not imported into Tokri's code.
