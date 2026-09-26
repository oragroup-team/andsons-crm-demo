# Guides

Documentation for the three CRM AI Slack bots: **@andSons Email**, **@andSons Analytics**, and
**@OvaEmail**.

- **[user-guide.md](./user-guide.md)** — start here if you just want to *use* the bots (writing
  emails, asking analytics questions, giving feedback). No coding knowledge needed.
- **[developer-guide.md](./developer-guide.md)** — start here if you're maintaining or changing the
  agent code. Explains the shared architecture, what each agent file does, and a quick-reference
  table for "I need to change X — which file?"
- **[deployment-and-testing-guide.md](./deployment-and-testing-guide.md)** — how to deploy a code
  change, test it, read the logs, and roll back if something breaks.

These guides cover the three agents' own code only — the underlying Flask web app and frontend
dashboard plumbing are out of scope by design (ask whoever handed this over if you need that).
