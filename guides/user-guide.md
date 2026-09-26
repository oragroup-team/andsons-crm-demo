# User Guide — CRM AI Agents on Slack

This is for anyone who wants to **use** the three Slack bots day to day — writing CRM emails,
asking business questions, or reviewing drafts. It doesn't require any coding knowledge.

If you're looking to install, deploy, or modify these bots, see
[`deployment-and-testing-guide.md`](./deployment-and-testing-guide.md) and
[`developer-guide.md`](./developer-guide.md) instead.

## The three bots

| Bot | Brand | What it does |
|---|---|---|
| **@andSons Email** | andSons (men's hair loss) | Drafts a CRM email (or a full multi-step email + WhatsApp flow) for a real lifecycle moment |
| **@andSons Analytics** | andSons | Answers business questions against the real BigQuery sales/marketing data |
| **@OvaEmail** | OVA (women's health) | Drafts a CRM email or flow for OVA's real lifecycle moments |

All three live in Slack as **@-mention bots** — you talk to them by typing `@` + their name in
any channel they've been invited to, the same way you'd @-mention a coworker. If a bot isn't in
your channel yet, invite it: `/invite @OvaEmail` (or `@andSons Email` / `@andSons Analytics`).

## Using @andSons Email / @OvaEmail

### Asking for an email

@-mention the bot with a plain-English description of what you want:

```
@OvaEmail write the contraception missed refill email
@OvaEmail EC fast response flow
@andSons Email create the plan-not-purchased email for Marcus
```

You can either:
- **Name a real flow** (a known, designed lifecycle moment — e.g. "missed refill", "consult
  no-show", "quiz recovery"). If your wording doesn't clearly match one of the real flows, the bot
  will ask you to clarify rather than guess.
- **Describe a business problem instead of a flow name**, e.g. *"OTC serum sales are down, write
  something to fix it for Wei"* or *"what should we send to someone who started the weight loss
  quiz but never finished it?"*. The bot investigates the situation and designs a new flow for it
  on the spot — including deciding whether it should be a single email or a short multi-step
  sequence (email + WhatsApp).

If a request needs a customer's first name and you didn't give one, the bot will ask for it.

### Reading the result

For an email, the bot posts back an **actual branded image** of the email (not just text) — logo,
hero photo, headline, buttons, everything — so you can see exactly what a customer would receive.
For a multi-step flow, it posts each step in order (each email as its own image, each WhatsApp
message as its own text block), with a short label saying which step of the flow it is and when it
would send.

### Giving feedback / asking for changes

Reply **in the same Slack thread** (still @-mentioning the bot) with what you want changed:

```
@OvaEmail can you make the subject line shorter?
@OvaEmail change the hero image, this one doesn't fit
@OvaEmail step 2 sounds too pushy, tone it down
```

The bot remembers the current draft and every round of feedback in that thread — you don't need to
repeat earlier instructions. **Starting a new thread starts a completely fresh draft** with no
memory of a different thread's conversation, so keep revising in the same thread until you're done.

You can also attach a reference image (e.g. a template you want it to match more closely) or a
CSV/Excel file (e.g. real numbers to ground the email in) directly to your message or a feedback
reply — the bot reads it and folds it in.

### Approving a flow

Once you're happy with a multi-step flow, type **`APPROVED`** anywhere in the thread. The bot
exports the whole approved flow as a set of HTML files you can hand off or archive — a clean,
final record of exactly what's shipping.

### Common phrases that work well

- *"looks good, just fix [specific thing]"* — targeted feedback works better than "make it better"
- *"use this as the template"* + an attached image — steers structure/tone from a real reference
- *"add a step for X"* / *"remove step 3"* — restructures the flow's cadence, not just wording
- *"APPROVED"* — locks in the flow and exports it

## Using @andSons Analytics

@-mention it with a plain business question:

```
@andSons Analytics how many orders has andSons had in Singapore
@andSons Analytics what's our total final revenue from delivered orders this quarter
@andSons Analytics which channel had the highest marketing spend last month
```

It queries the real, live sales/marketing data warehouse and replies with:
- A plain-English answer
- The actual number(s), traced back to a real query result — it does not estimate or guess
- The SQL query it ran, so you (or a data-literate colleague) can double-check it

You can attach a CSV/Excel file to your question too — e.g. a list of specific products — and the
bot will combine it with the warehouse data where relevant (e.g. "here's revenue for the products
in your file").

If the agent can't verify a number against a real source, it will say so rather than presenting a
guess as fact — treat any answer that reads as uncertain or caveated as a signal to double-check
with the data team, not a final number.

## Things worth knowing

- **Response time**: most single-email requests come back within 10–30 seconds. A full multi-step
  flow (several email + WhatsApp steps, each independently checked for quality) can take a couple
  of minutes — this is normal, not a sign something's stuck. If a flow request goes unanswered for
  several minutes with no partial response at all, see the troubleshooting note in
  [`deployment-and-testing-guide.md`](./deployment-and-testing-guide.md) — it's a known infrastructure
  edge case, not a content problem.
- **Threads matter**: a reply outside the original thread is treated as a brand-new, unrelated
  request. Always reply in-thread to revise something.
- **The bots won't invent facts**: they're built to refuse rather than make up a price, a
  statistic, a customer name, or a medical claim that wasn't actually given to them. If a draft
  looks oddly generic or declines to include something you expected (like a specific number), it's
  very likely because that number wasn't available from a real, approved source — not a bug.
- **Compliance guardrails are intentional, not a bug**: both email bots automatically avoid naming
  prescription medicines, quoting discount percentages, giving medical advice, or writing anything
  that could read as a suitability/care decision. If a draft feels oddly cautious around a topic
  like side effects or dosing, that's the guardrail working correctly, not something to work around
  with cleverer phrasing.
