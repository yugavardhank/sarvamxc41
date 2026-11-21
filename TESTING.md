# Testing Tokri (for teammates)

Tokri's backend, database and MCP server are all running live on one
machine right now. You don't need to install or run anything yourself -
just test through the agent.

## 1. Where things live

- **Dashboard** (customer list, orders, wallet, catalog):
  https://ceremony-gates-protein-merger.trycloudflare.com
- **Catalog API** (all 30 products): `.../api/catalog`
- **MCP server** (what the Sarvam agent calls): `https://happy-pulling-exclude-immediate.trycloudflare.com/mcp`

Both links are temporary tunnels - they only work while the host machine
keeps them running. If either one stops responding, ping the team to
restart it.

## 2. Test phone numbers (from the real seeded data)

| Number | Name | Profile |
|---|---|---|
| 9000000001 | Sunita Rao | Gold tier, healthy |
| 9000000002 | Meera Nair | Gold, at churn risk (win-back offer) |
| 9000000003 | Lakshmi Devi | Gold but credit blocked |
| 9000000004 | Prakash Hegde | Silver, healthy |
| 9000000005 | Ramesh Kumar | Silver, at churn risk |
| 9000000006 | Divya Shetty | Silver, churn risk + credit blocked |
| 9000000007 | Anita Sharma | New customer, no history |
| 9000000008 | Vikram Joshi | New, credit blocked |
| 9000000009 | Farhan Ali | Missing language - agent must ask first |
| 9000000010 | Rohit Verma | Missing most data - agent must ask first |

## 3. How to run a test call

1. Open the agent ("Bada tokri") in the Sarvam Voice Agents dashboard
2. Click **Test agent**
3. When it asks for your number, give one from the table above
4. Try ordering a few items (e.g. "toor dal and salt"), object to the
   price ("that's too expensive"), and try paying ("pay in full" / "on
   account")

## 4. What to look for

- The agent should greet the customer by name and (for churn-risk
  customers) use win-back wording
- Items/prices it reads back should match `.../api/catalog`
- A price objection should get exactly one offer at a time, never an
  invented discount
- Paying should produce a real Cashfree sandbox payment link
- Check the **dashboard** after a call - the order should show up there
  with the right status

## 5. Original data source

The seed data (customers + inventory) came from `tokri_Database.xlsx`.
That spreadsheet isn't needed for testing - it's already loaded into the
running database - but ask in the team channel if you need the original
file.
