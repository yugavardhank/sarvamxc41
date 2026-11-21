# Tokri

A voice-first grocery reordering assistant for supermarkets, built on Sarvam AI
(voice, language, intent) and Cashfree (payments). Tokri calls a customer on
their usual reorder schedule, listens to what they want, picks the
cheapest/best-rated match for each item within their budget, reads the
basket back for confirmation, and charges them, all by voice. It remembers
brand and quantity preferences across orders so repeat customers get a
faster, more personal call each time.

Previous project in this folder: `documents/` holds Bharosa, a WhatsApp
voice-payment bot for elderly parents with scam detection and child approval
(Sarvam x Cashfree hackathon entry). Tokri is a fresh direction for the same
sponsors' tech, not a continuation of Bharosa, kept here for reference and
reusable code.

## Problem

Supermarkets want recurring customers to keep reordering regularly, but
reordering is friction: remembering what's low at home, re-typing or
re-picking the same list in an app, comparing brands and prices every time.
A phone call that already knows your usual order and just asks "same as
last time, or anything different?" removes nearly all of that friction, and
upselling/cross-selling happens naturally in conversation instead of banner
ads.

## Core flow

1. **Trigger.** A scheduler finds customers due for their next reorder
   (weekly, biweekly, custom cadence per customer) and places (or, for the
   demo, simulates) a call.
2. **Greeting.** Sarvam TTS greets the customer in their language, optionally
   referencing their last order ("Last time you got Tata Salt and 2kg toor
   dal, same again?").
3. **Listening.** The customer speaks a free-form list: items, quantities,
   brand hints, budget hints ("nothing too expensive this time").
4. **Understanding.** Sarvam STT transcribes, then an LLM call extracts a
   structured item list from the rambling transcript.
5. **Matching and optimizing.** For each requested item, Tokri searches its
   product catalog, applies the customer's known brand preference if any,
   and otherwise ranks candidates by price and rating within the stated
   budget, building a basket.
6. **Confirming.** Sarvam TTS reads back the basket and total; the customer
   confirms, asks for a swap, or cancels an item, by voice.
7. **Paying.** On confirmation, Tokri creates a Cashfree payment link (or, for
   a trusted repeat customer, an autopay-style charge) and the order is
   placed once payment succeeds.
8. **Learning.** Accepted brand and quantity choices update the customer's
   preference record for next time.

## Feasibility notes

- **Voice understanding, TTS, and intent extraction**: fully feasible with
  Sarvam, the same approach proven in Bharosa's pipeline (STT to transcript,
  LLM to structured intent, TTS to spoken replies).
- **Price/rating optimization**: feasible, but the product catalog (SKUs,
  prices, brands, ratings) has to be our own seeded data. Neither Sarvam nor
  Cashfree provides a grocery catalog; in a real deployment this would be the
  supermarket's own inventory/pricing feed.
- **Preference memory**: plain data modelling, no external dependency, and
  the main differentiator worth demoing well.
- **Payment**: feasible, same pattern as Bharosa's Cashfree payment link and
  webhook flow.
- **Real outbound phone calls with live conversation**: the one real risk.
  Sarvam provides STT/TTS/LLM but not telephony, so a true phone call needs a
  voice carrier (e.g. Twilio Voice Media Streams, Exotel) streaming live audio
  to Sarvam and back, which is a harder integration than Bharosa's WhatsApp
  text/voice-note flow and was not attempted here. Decision: simulate the call
  in the demo UI (an "incoming call" screen, same spirit as Bharosa's phone
  mockups) and document real PSTN calling as a known future step, not a
  blocker for the demo.
- **Call scheduling and follow-ups**: feasible as a simple cron in our own
  backend. Cashfree's Relay product (a no-code AI agent builder with
  schedule triggers, voice-call actions via Sarvam/Bolna/Osvi, and
  human-approval gates) maps unusually well onto this problem and is worth
  revisiting once its MCP access is authorized, as a way to let the
  supermarket configure call cadence and payment-recovery follow-ups without
  us hand-building that layer.

## Data model

- `customers` - phone, name, language, reorder_cadence_days, last_called_at,
  default_budget
- `products` - sku, name, category, brand, price, rating, in_stock
- `preferences` - customer_id, category, preferred_brand, usual_qty (learned
  over time from accepted orders)
- `orders` - customer_id, status (DRAFT / CONFIRMING / AWAITING_PAYMENT /
  PAID / FAILED), total, created_at
- `order_items` - order_id, requested_text (what the customer said),
  matched_sku, qty, unit_price
- `events` - append-only log of every call, LLM decision, and payment status
  change, same pattern as Bharosa's `log_event`

## What carries over from Bharosa

- `app/services/sarvam.py`, `app/services/cashfree.py`, and the `MOCK_*`
  env-flag pattern behind every external call.
- The shared pipeline principle (`app/pipeline.py`): one place holds the
  transcribe -> understand -> match -> confirm -> pay logic, used by both the
  dev simulator and whatever real entry point replaces it later.
- `app.db.log_event` logging discipline.
- The dashboard's phone-mockup simulation UI, reskinned as an incoming-call
  screen instead of a WhatsApp chat.

## What's new

- Product catalog and price/rating optimization logic.
- Preference-learning logic (the differentiator to highlight to judges).
- Reorder-cadence scheduling.

## Known limits (to state up front, same spirit as Bharosa's)

- No real supermarket inventory integration; the catalog is seeded demo data.
- No real outbound phone call; the call is simulated in the dashboard.
- No real-time barge-in or interruption handling in conversation.
