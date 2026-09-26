# Developer Guide — CRM AI Agents

This describes the **code for the three agents** — @andSons Email, @andSons Analytics, and
@OvaEmail — so you can understand what a change will actually affect before you make it. It
deliberately skips the web app plumbing (Flask routes, Slack signature verification, the frontend
dashboard) — see [`deployment-and-testing-guide.md`](./deployment-and-testing-guide.md) for that
side.

If you're new to this system, read **Section 1** first — it explains the shared vocabulary and
pattern every agent uses, so the rest of this document makes sense without re-explaining it three
times.

---

## 1. The shared mental model

All three bots are built from the same small set of ideas, reused across two codebases:

| Codebase | Folder | Powers |
|---|---|---|
| andSons backend | `backend/` | @andSons Email **and** @andSons Analytics (one shared codebase, two Slack bots) |
| OVA Email | `ova_email/` | @OvaEmail only (its own separate, standalone app) |

`ova_email/` was originally built by copying `backend/`'s structure and swapping in OVA's own
brand content — so the two codebases look almost identical file-for-file, even though the actual
prompts/data inside each file are completely different (andSons is a men's hair-loss brand; OVA is
women's health). Once you understand one, the other reads the same way.

### What "an agent" actually means here

There's no exotic AI framework involved. Each "agent" is a Python function that:
1. Builds a big block of instructions (a **prompt**) — the brand voice rules, compliance rules,
   the specific real data for this request (e.g. this customer's flow, the real approved photo
   bank, real product benefits).
2. Sends that prompt to an AI model (Claude or Groq — see **LLM providers** below).
3. Gets back a **structured** answer — not free-form text, but a filled-in form with named fields
   (subject line, hero image choice, callout text, etc.), enforced by a Python schema
   (`pydantic.BaseModel`) so the AI literally cannot skip a required field or return the wrong
   shape of answer.

### The core pipeline (email side)

Every email or flow goes through the same chain of agents, always in this order:

```
Head of CRM  →  Copywriter  →  Creative Director  →  Sweeper  →  (Visual QA)  →  posted to Slack
     ↑                                                    |
     └──────────────── auto-retry on failure ─────────────┘
```

- **Head of CRM** decides the *strategy*: which real flow this is, how many touchpoints (emails/
  WhatsApp messages) it should be, in what order and timing. It does not write any customer-facing
  words.
- **Copywriter** writes the actual content — subject, body, callouts, CTA button, and picks a hero
  photo from the approved bank.
- **Creative Director** is a second, independent pass that can override just the hero photo choice
  (a second opinion, not a rewrite).
- **Sweeper** is the quality gate — it reads the drafted content like a strict brand/compliance
  reviewer and either passes it or fails it with specific reasons (e.g. "this names a prescription
  medicine," "these callouts aren't in the approved benefits list").
- If the Sweeper fails a draft, the system automatically retries — it sends the Copywriter back to
  redraft, this time also given the Sweeper's exact reasons, up to a capped number of attempts
  (`MAX_RETRIES`). If it still hasn't passed after that, it's shown anyway but flagged as needing a
  human look, rather than silently retrying forever or silently failing.
- **Visual QA** is a final, different-in-kind check: every other step only ever reads *text*; this
  one actually looks at the rendered image (the real PNG that gets posted to Slack) and catches
  things text can't — cramped layout, a broken-looking photo, overlapping elements.

A **human replying in the Slack thread with feedback** re-enters this same loop one level up: the
system re-sends the Copywriter the original request, the full history of feedback so far, and the
new feedback, and reruns the Sweeper on the result. This is why replying in-thread works and
starting a new thread doesn't share any memory — see `session_store.py` below.

### The Analytics side is a different pipeline

@andSons Analytics doesn't use the Head of CRM/Copywriter/Sweeper chain at all — it's a
**question-answering agent** that translates a plain-English question into a real SQL query against
BigQuery, runs it, and only reports numbers it can trace back to what the query actually returned.
See **Section 3** below.

### LLM providers — Claude primary, Groq fallback

Every agent gets its AI model from one shared function, `get_llm(agent_name)`
(`agents/llm_provider.py`, identical file in both codebases). It:
- Uses **Claude (Sonnet 5)** as the primary model for every agent role.
- **Automatically falls back to Groq** if Claude is unreachable (bad key, no credit, network
  issue) — this is checked with a real, cheap test call, cached for 5 minutes, so it doesn't cost
  an extra API call on every single request. If Claude comes back online later, the app switches
  back on its own — no restart needed.
- Model choice is deliberately **not** the most expensive Claude model (Opus) — that was tried
  early on and burned a real ~$100 in credits in a short testing period, for no meaningful quality
  gain on this kind of task. Sonnet 5 is the settled default. If you ever see `claude-opus-5`
  anywhere in a config file, that's a mistake worth fixing, not an intentional choice.
- You can override the provider or exact model per-agent via environment variables
  (`<AGENT>_PROVIDER`, `<AGENT>_ANTHROPIC_MODEL`, `<AGENT>_GROQ_MODEL`) without touching code — see
  `cloudrun-env.yaml` / `.env.example` in each folder.

### Structured output and "hard rules" vs. "judgment calls"

Every agent's prompt draws a hard line between:
- **Hard rules** (compliance, legal, safety) — things the AI must never do regardless of what a
  human asks for, e.g. naming a specific prescription medicine, quoting a discount percentage,
  giving medical advice, inventing a statistic. These are enforced in *two* places on purpose: the
  prompt tells the Copywriter not to do it, and the Sweeper independently checks the *output* for
  it — so a single missed instruction doesn't let something non-compliant through.
- **Judgment calls** (tone, which real approved photo fits best, whether a second touchpoint is
  warranted) — the AI is expected to reason about these using real data it's given, never invent
  facts to fill a gap.

### Firestore — how the bots remember anything

Both codebases use **Google Firestore** (a cloud database) to remember:
- The current draft and full feedback history for each Slack thread (so replying in-thread works).
- **Learned rules** — see below.
- **Synthesized flows** — a brand-new flow design the system invented on the fly for a request that
  didn't match any of the real pre-built ones (see **Head of CRM**, Section 2/3) gets saved here so
  the *next* similar request reuses it instead of designing it from scratch again.

OVA's Firestore collections are all prefixed `ova_` (e.g. `ova_learned_rules`) even though it
shares the same Firestore database as the andSons backend — this keeps the two bots' data from
mixing while still sharing infrastructure. See `session_store.py` in each codebase.

### Learned rules — the one thing that improves over time without a code change

When a human gives real revision feedback on a draft, a separate small agent
(`learned_rules_agent.py`) looks at that feedback and asks: *is this a one-off preference for this
one email, or a genuine repeatable rule?* If it's genuinely repeatable, it's saved to Firestore as
a short standing instruction, and every future Head of CRM/Copywriter/Sweeper call reads the full
list of these rules. This is deliberately conservative — it's biased toward saving a *conditional*
rule ("only do X when someone explicitly asks") rather than silently making X the new default from
one person's one-time request.

---

## 2. andSonsEmail and andSonsAnalytics (`backend/agents/`)

These two Slack bots share one codebase and one set of supporting files (`flows.py`,
`categories.py`, `image_bank.py`, `session_store.py`, etc., all at `backend/`'s top level).

### The email pipeline files

| File | What it does |
|---|---|
| `copywriter_agent.py` | The content-writing core. Defines the real shape of an email (`EmailContent`): subject, opening lines, an optional numbered "what happens next" list, an optional reassurance line, a CTA button (with its own literal on-screen position), an optional trust line, hero photo + optional headline baked over it, and a few optional style overrides (background colour, italics) that only apply when a human explicitly asks for them. Also has the equivalent shapes for WhatsApp and push messages — each is its own distinct format, not a shrunk-down email. Also handles classifying a free-text Slack request into a real flow, and generating a whole multi-step flow (not just one email) with each step aware of what came before it so the sequence reads as one continuous story rather than repeating itself. |
| `sweeper_agent.py` | The quality gate. One function per channel (email/WhatsApp/push). Checks compliance (hard-fail), writing quality, whether the draft genuinely matches this flow's real moment (not a generic reused line), and — critically — a Python-level (not AI-level) check that the hero photo really is one of the approved bank photos and actually belongs to the right category. If the Sweeper itself fails to run (a technical error), the result is treated as a **fail**, never a silent pass — this is a safety gate, not a nice-to-have. |
| `feedback_node.py` | The largest file — plain Python orchestration (no AI calls of its own) that runs the whole pipeline in order, manages the auto-retry loop, and handles a human's free-text revision request: figuring out whether the person wants a wording change, a live-data lookup, a hero-image change, or a restructuring of the flow itself (add/remove/reorder a step, or convert an email step to WhatsApp), and routing each case correctly. Every successful human revision also triggers the learned-rules mechanism described in Section 1. |
| `head_of_crm_agent.py` | Decides strategy before any copy is written: which real flow, what the campaign objective/audience/success metric is, and the actual touchpoint plan (how many sends, which channel, when). Also designs a brand-new flow from scratch when a request doesn't match anything in the real catalog, and saves it for reuse. Hard rule: never states a number it wasn't actually given (a real early bug had it inventing percentages out of nowhere). |
| `creative_director_agent.py` | A second, independent look at just the hero photo choice — can override the Copywriter's pick, and separately checks that the same photo isn't reused across multiple steps of one flow. |
| `template_agent.py` | Lets someone upload a reference screenshot ("follow this template") — describes its visual structure (background colour, where the button sits, whether there's a numbered list, overall tone) in the same vocabulary the Copywriter's own fields already understand, so it can act on it directly rather than just "seeing" a picture. |
| `visual_qa_agent.py` | The one check that looks at the actual rendered image (not text) — catches broken layout, a cramped or cut-off element, a photo that doesn't fit — as a last check before posting. |
| `learned_rules_agent.py` | See Section 1. |
| `insight_agent.py` | Turns a business question/signal ("OTC sales are down") into a brief the Copywriter can use, by asking the Analytics agent (see below) for real numbers first — so this never invents a number either. |
| `llm_provider.py`, `anthropic_agent_loop_patch.py` | Shared plumbing described in Section 1 — you're unlikely to need to touch these unless a new AI provider is added. |

### Supporting data files (not AI agents, but what the agents are grounded in)

| File | What it holds |
|---|---|
| `flows.py` | The real, pre-designed lifecycle moments (e.g. "Plan Created, Not Purchased," "Consult No-Show") — each with its own audience, goal, real pricing rule, and cadence (how many touchpoints, on which channel, when). **This is usually the first file to check** if a request is being routed to the wrong flow, or if you need to add/adjust a real flow by hand instead of letting the AI design one. |
| `categories.py` | The brand's real product/service categories (for andSons: hair loss, sexual health, weight loss, skin), each with its own approved voice notes, real approved benefit claims, common objections, and compliance notes. This is the **only allowed source** of what a callout or feature line can say — the Copywriter and Sweeper are both told never to state a benefit that isn't listed here. |
| `image_bank.py` | The real, curated list of approved hero photos, each with a plain description of what it actually shows and what moment it fits — this is genuinely the only place hero images come from; the AI is never allowed to invent or generate one. |
| `session_store.py` | The Firestore read/write layer (feedback threads, learned rules, synthesized flows). |
| `text_sanitize.py` | A defensive cleanup pass run on every piece of AI-written text — strips em-dashes, curly quotes, and stray markdown the model might slip in despite being told not to. |
| `file_context.py` | Turns an uploaded CSV/Excel file into a short text summary the AI can read. |
| `flow_html_export.py` | Turns an **APPROVED** flow into a self-contained HTML file (used by the "type APPROVED" feature). |

### The Analytics agent (`analytics_agent.py`) — a separate capability

This is the single largest file in the system, and it does something genuinely different from the
email side: it's a **question-answering agent over a real database**. In plain terms:

1. It takes a plain-English question (optionally with earlier Q&A from the same Slack thread for
   context) and rewrites it into a complete, standalone question if it was a follow-up.
2. It decides which real data source(s) are actually relevant: the main BigQuery sales/marketing
   warehouse, a second set of pre-verified BigQuery views built specifically for CRM questions
   (built because naive matching produced real, embarrassing wrong numbers in the past — see the
   file's own comments for the specific incidents), and/or a live pull from MoEngage (the
   marketing/email-engagement platform) when the question is about campaign performance the
   warehouse doesn't track.
3. It hands the question to an AI agent that's allowed to write and run real SQL queries
   (read-only — it cannot write/modify/delete anything, enforced twice over), following a mandatory
   step-by-step process: check the real table structure first (never assume), find the right
   tables, run the query, and only report what the query actually returned.
4. Before the answer ever reaches Slack, **three independent, non-AI safety checks** re-verify it:
   does every number in the answer actually trace back to a real query result; if the AI's query
   used a partial name match, does re-running a guaranteed-complete version give the same number;
   and for certain known-tricky question types, is there an independent way to re-derive the answer
   that would catch the AI being internally consistent but still wrong.

This matters for a maintainer mostly as: **if the Analytics bot gives a wrong or oddly-hedged
answer, the fix is very rarely "the AI is bad at reasoning"** — it's almost always that the schema
notes (a large block of text in this file explaining what the tables' columns really mean, since
several columns share the same name across tables but mean different things) need a correction or
addition. Search this file for `BIGQUERY_SCHEMA_NOTES` to find that block.

### MoEngage integration files (Analytics-only capability)

`moengage_client.py`, `moengage_summary.py`, `moengage_dump_context.py`, and the `moengage_export/`
folder give the Analytics agent (and, more thinly, the email side's `insight_agent.py`) real
visibility into email/WhatsApp campaign performance — open rates, click rates, control-group
uplift — which the BigQuery sales warehouse alone doesn't track. Some of this data comes from a
periodically-refreshed static export (needs a person to manually re-run a loader script
occasionally, or it goes stale silently) and some is pulled live per-question (which is
deliberately slower — minutes, not seconds — since it re-downloads the whole account's flow data
each time). OVA Email has no equivalent of any of this.

---

## 3. OvaEmail (`ova_email/agents/`)

Structurally the same pipeline as andSons Email (Head of CRM → Copywriter → Creative Director →
Sweeper → Visual QA), same file names, same shared plumbing (`llm_provider.py`,
`anthropic_agent_loop_patch.py`, `learned_rules_agent.py`, `template_agent.py`). The real
differences:

- **Its own brand content**: `categories.py` has OVA's 4 real categories (contraception,
  emergency contraception, intimate health, weight loss) instead of andSons' hair-loss-focused
  ones; `flows.py` has OVA's own 17 real lifecycle flows; `image_bank.py` has OVA's own curated
  hero photo bank.
- **A real, stakeholder-confirmed email template with a fixed outer shape and a flexible middle**:
  the actual visual format (see `email_image_renderer.py`) is 4 zones, in the stakeholder's own
  words: **Logo → Hero banner & CTA → Body content → Footer & Closing CTA**. Zones 1, 2, and 4 are
  fixed and mandatory on every email (a dark logo bar; a headline/subcopy/CTA/circular hero photo;
  a dark closing footer with its own heading, checkmark bullets, button, and second photo). Zone 3
  ("body content") is deliberately **flexible** — it's an ordered list of typed content modules
  (`blocks` in the Copywriter's schema: `text`, `image_spotlight`, `divider_header`, `icon_grid`,
  `two_column`, `callout_card`, `checklist_card`, `price_card`) that the Copywriter picks and
  orders per email based on what that email actually needs — a short logistics nudge might use one
  `text` block, a longer onboarding email might use six. This is enforced by a Pydantic
  discriminated union (`Block` in `copywriter_agent.py`) — an email with no blocks, or a missing
  closing zone, gets rejected before the Sweeper ever sees it. A single email can now use *more
  than one* real bank photo (the top hero, the closing photo, and any block's own photo) — the
  Sweeper has a Python-level check (`sweeper_agent._hero_deterministic_issues`) that hard-fails if
  any two of them are the same image. If you need to add a new kind of content module, it's a
  three-file change: a new Pydantic block class + add it to the `Block` union
  (`copywriter_agent.py`), a new render function + register it in `_BLOCK_RENDERERS`
  (`email_image_renderer.py`), and a line in the Sweeper's `CANDIDATE FORMAT` description
  (`sweeper_agent.py`) so it knows how to read the new block type in the rendered text.
- **No Analytics agent, no MoEngage/BigQuery integration** — OVA Email is email/WhatsApp
  generation only.
- **Firestore collections are `ova_`-prefixed** (shares the same Firestore project as the andSons
  backend, but keeps its own data separate).

### OvaEmail-specific supporting files

| File | What it does |
|---|---|
| `email_image_renderer.py` | Draws the actual branded PNG image posted to Slack, using Pillow (a Python image library) — no web browser involved. Draws the fixed logo/hero/closing-footer zones, then loops over `content["blocks"]` calling each block type's own render function (`_BLOCK_RENDERERS`). If you need to change the visual design itself (colours, fonts, spacing, a block's layout), this is the file to edit — not the Copywriter's prompt. |
| `whatsapp_image_renderer.py` | Same idea, for the WhatsApp message's chat-card look. |
| `build_hero_catalog.py` | A small utility script (not part of the live app) that regenerates a human-readable folder (`hero_image_catalog/`) listing every approved hero photo with its description, generated directly from `image_bank.py` so it can never drift out of sync. Run it after editing `image_bank.py`: `python3 build_hero_catalog.py`. |

---

## 4. "I need to change X — which file?"

A quick-reference table for common requests, for both codebases (paths shown relative to
`backend/` or `ova_email/` — same file names in both):

| You want to... | Edit this file |
|---|---|
| Change what a specific real flow's emails should achieve, or its cadence (how many touchpoints) | `flows.py` |
| Add a brand-new pre-designed flow (rather than letting the AI design one on request) | `flows.py`, following the exact shape of an existing entry |
| Change what benefits/claims are allowed to be mentioned for a category | `categories.py` |
| Add, remove, or fix a hero photo | `image_bank.py` (+ the actual image file under `static/hero_images/`) |
| Change the brand voice/tone rules | `agents/copywriter_agent.py`'s `SYSTEM_PROMPT` |
| Change what the Sweeper treats as a hard-fail | `agents/sweeper_agent.py`'s `SYSTEM_PROMPT` |
| Change the actual visual look of the email/WhatsApp image | `email_image_renderer.py` / `whatsapp_image_renderer.py` |
| Change how many retries happen before giving up | `agents/feedback_node.py` (search for `MAX_RETRIES`) |
| Change which AI model/provider an agent uses | `agents/llm_provider.py`'s `DEFAULT_PROVIDERS`/`DEFAULT_MODELS`, or the env vars in `cloudrun-env.yaml` (no code change needed for a quick swap) |
| Fix a wrong answer from @andSons Analytics | `agents/analytics_agent.py`'s `BIGQUERY_SCHEMA_NOTES` block, almost always — see Section 2 |
| Add a standing rule by hand (rather than waiting for it to be learned from feedback) | Not a file — use `session_store.add_learned_rule()` from a Python shell, or just give the feedback once for real through Slack and let `learned_rules_agent.py` do it |

## 5. Real, known gaps (told to you honestly, not hidden)

- **There's no automated test suite.** Testing is manual, through Slack — see
  [`deployment-and-testing-guide.md`](./deployment-and-testing-guide.md). This is a deliberate
  tradeoff, not an oversight: the actual product is the quality of an AI-generated draft, which a
  unit test can't meaningfully assert on.
- **Some categories have thin or no dedicated hero photography** — both codebases fall back to a
  generic "general" photo for a category with no specific real photo available, rather than
  forcing a bad-fitting one. This is intentional (a smaller honest photo bank beats a padded one
  with images that don't actually work) but worth knowing before assuming a missing category-
  specific photo is a bug.
- **Synthesized (AI-designed) flows are not human-audited by default.** When a request doesn't
  match a real pre-built flow, the Head of CRM designs a new one and saves it for reuse — this is a
  reasonable judgment call grounded in real data, but it's not the same level of scrutiny as a
  flow someone deliberately designed and reviewed. If a synthesized flow is being reused a lot,
  it's worth promoting it into `flows.py` properly (with a human review of its cadence/goal) rather
  than leaving it as an ad-hoc AI creation forever.
- **Cloud Run cold starts / scale-to-zero can silently kill a long-running background request** —
  see the **Known issue** section in the deployment guide. Not a code bug, an infrastructure
  tradeoff.
