# Rainbow — Bookly Customer Experience Agent

Rainbow is an AI customer experience agent for **Bookly**, a fictional online bookstore. It's built
with Google's [ADK](https://adk.dev) (Agent Development Kit) via `agents-cli`, and handles three
use cases: order status inquiries, return/refund requests, and general questions (shipping,
policies, password reset).

No mocked LLM calls and no all-in-one agent platform — Rainbow calls real APIs (Google Sheets,
Stytch) and a real vector database (ChromaDB) directly, orchestrated through a single ADK agent
with six tools.

## Contents

- [Architecture](#architecture)
- [Tools — what they are, how they're set up](#tools--what-they-are-how-theyre-set-up)
- [Guardrails](#guardrails)
- [SOP and knowledge base](#sop-and-knowledge-base)
- [Repo structure](#repo-structure)
- [Setup — running it yourself](#setup--running-it-yourself)
- [Examples — the 3 minimum requirements](#examples--the-3-minimum-requirements)
- [Evaluation](#evaluation)

---

## Architecture

**One agent, six tools — not a multi-agent system.** All three use cases share the same identity
verification step and a comparable level of complexity (a handful of decisions, not independent
reasoning domains), so a single agent with tool-calling handles intent routing itself, via its own
reasoning over the system instruction. See [`docs/SOP.md`](docs/SOP.md) for the full human process
this agent is built to replicate.

- **Model:** `gemini-3.5-flash-lite`
- **Framework:** ADK, scaffolded with `agents-cli` (prototype mode, no cloud deployment)
- **Agent code:** [`rainbow/app/agent.py`](rainbow/app/agent.py) — instruction + tool wiring
- **Tool implementations:** [`rainbow/app/tools.py`](rainbow/app/tools.py)

A customer message flows: **intent classification** (the model reading the instruction) →
**identity verification** if the request touches order/account data → **tool call(s)** (Sheets,
RAG, or Stytch) → grounded response, or **escalation** if the SOP doesn't cover the situation.

---

## Tools — what they are, how they're set up

| Tool | Purpose | Backing system | Real or mocked |
|---|---|---|---|
| `search_policy_kb` | Answers general policy/FAQ questions | ChromaDB (local vector DB) | **Real** |
| `lookup_order` | Looks up an order by number | Google Sheets API | **Real** |
| `lookup_customer` | Looks up a customer by email | Google Sheets API | **Real** |
| `initiate_return` | Checks return eligibility, writes the return | Google Sheets API | **Real** |
| `send_auth_code` | Sends a 6-digit email OTP | Stytch API | **Real** (test env) |
| `verify_auth_code` | Verifies the OTP | Stytch API | **Real** (test env) |
| `escalate_to_human` | Logs a handoff for a human agent | Local log file | Mocked (no paging system attached) |

### RAG (`search_policy_kb`)
- Source document: [`data/bookly_knowledge_base.md`](data/bookly_knowledge_base.md) — Bookly's
  shipping, returns, payment, account, and loyalty-program policies.
- Indexed by section (`##` headers) into a ChromaDB collection using its built-in local embedding
  model (no external API key needed): [`rag/build_index.py`](rag/build_index.py).
- Queried via [`rag/query.py`](rag/query.py) standalone, or through the wired-in ADK tool in
  `tools.py`.
- Rebuild the index any time the knowledge base changes: `python3 rag/build_index.py`.

### Google Sheets (`lookup_order`, `lookup_customer`, `initiate_return`)
- Data lives in a Google Sheet with two tabs, `Customers` and `Orders` (schema in
  [`docs/SOP.md`](docs/SOP.md#data-schema)).
- Access is via the real Sheets API using an OAuth **installed-app** credential (not a service
  account) — the agent acts as a specific authorized Google account.
- One-time setup script: [`rainbow/scripts/authorize_sheets.py`](rainbow/scripts/authorize_sheets.py)
  runs a local browser consent flow and saves a reusable token (see [Setup](#setup--running-it-yourself)).

### Stytch (`send_auth_code`, `verify_auth_code`)
- Real Stytch **Email OTP** API (`otps.email.login_or_create` / `otps.authenticate`) via the
  official `stytch` Python SDK, run against a free Stytch **test** project — no cost, no
  production data.
- This is the mechanism behind Rainbow's identity-verification flow: a customer must prove they
  own the email on the account before Rainbow will touch order or account data.

### Escalation (`escalate_to_human`)
- Mocked: writes a timestamped reason + summary to `rainbow/escalations.log` rather than paging a
  real human queue. Swappable for a real ticketing/paging integration later.

---

## Guardrails

Three deterministic (code-level, not just prompt-level) safety checks, found and hardened while
building this:

1. **Order ownership.** `lookup_order` and `initiate_return` both require a `verified_email`
   argument and reject any order that doesn't belong to that email — a verified customer cannot
   access or return someone else's order, no matter what they claim.
2. **30-day return window.** `initiate_return` computes real elapsed days from the order date and
   rejects returns past the window, overriding a stale/incorrect static eligibility flag if one
   exists.
3. **No mockable monetary compensation.** Rainbow has no tool that can issue a gift card, discount,
   or refund outside of `initiate_return`'s own eligibility determination — so it structurally
   cannot be talked into promising one.

A fourth rule — escalate immediately, no troubleshooting, if a customer explicitly says an order
marked "Delivered" was never received — is instruction-level (in `agent.py`). Eval testing caught
this rule initially over-triggering on the word "Delivered" alone with no actual customer
complaint; see [Evaluation](#evaluation).

---

## SOP and knowledge base

- **[`docs/SOP.md`](docs/SOP.md)** — the step-by-step human process (identity verification, each
  use case's decision tree, escalation criteria, data schema) that Rainbow's instruction and tools
  are built to replicate.
- **[`data/bookly_knowledge_base.md`](data/bookly_knowledge_base.md)** — Bookly's policies, the
  source document indexed for RAG.

---

## Repo structure

```
docs/
  SOP.md                        Human support-agent process this agent replicates
  Rainbow_Eval_Report.pdf       Full eval methodology + results write-up
data/
  bookly_knowledge_base.md      RAG source document
rag/
  build_index.py                Chunk + embed the knowledge base into ChromaDB
  query.py                      Standalone RAG query function
rainbow/                        The ADK project
  app/
    agent.py                    Rainbow's instruction + tool wiring
    tools.py                    All 6 tool implementations
  scripts/
    authorize_sheets.py         One-time Google OAuth consent flow
  tests/eval/
    datasets/
      single-turn.json          RAG / clarifying-question / guardrail eval cases
      multi-turn.json           Identity-verified order/return eval cases
    eval_config_single_turn.yaml
    eval_config_multi_turn.yaml
  .env.example                  Template for required environment variables
```

`credentials/` (OAuth client + token) and any `.env` files are **not** in this repo — see Setup.

---

## Setup — running it yourself

**Prerequisites:** `uv`, `agents-cli` (`uv tool install google-agents-cli`), a Google Cloud
project with the Sheets API and Vertex AI API enabled, a free [Stytch](https://stytch.com) test
project.

1. **Install dependencies**
   ```
   cd rainbow && agents-cli install
   ```

2. **Build the RAG index**
   ```
   python3 rag/build_index.py
   ```

3. **Set up your own Google Sheet** with `Customers` and `Orders` tabs matching the schema in
   [`docs/SOP.md`](docs/SOP.md#data-schema). Update `_SPREADSHEET_ID` in `rainbow/app/tools.py` to
   your sheet's ID.

4. **Google OAuth (installed-app credential)**
   - In Google Cloud Console → Credentials, create an OAuth Client ID of type **Desktop app**,
     download the JSON, save it as `credentials/google_oauth_client.json`.
   - Run the one-time consent flow: `python3 rainbow/scripts/authorize_sheets.py` — opens a
     browser, saves a reusable token to `credentials/google_token.json`.
   - Update `_TOKEN_FILE` / `GOOGLE_OAUTH_CLIENT_SECRETS_FILE` paths in `tools.py` / `.env` if you
     place these files elsewhere.

5. **Stytch** — sign up, grab your **test** project's Project ID + Secret.

6. **Environment variables** — copy `rainbow/.env.example` to `rainbow/.env` and fill in:
   ```
   GOOGLE_CLOUD_PROJECT=your-gcp-project-id
   STYTCH_PROJECT_ID=project-test-...
   STYTCH_SECRET=secret-test-...
   STYTCH_ENV=test
   ```

7. **Run it**
   ```
   cd rainbow && agents-cli playground
   ```
   Opens a local web UI at `http://127.0.0.1:8080/dev-ui/?app=app`.

> Note: file paths in `tools.py` (`_RAG_DB_PATH`, `_TOKEN_FILE`, `_ESCALATION_LOG`) are currently
> absolute paths from development — update them to your own machine's paths, or parameterize via
> `.env`, before running elsewhere.

---

## Examples — the 3 minimum requirements

Real transcripts, captured by actually running the agent (not written by hand). Emails shown are
Bookly's dummy customer data, not real people.

### 1. Multi-turn interaction (collecting information before responding)

Rainbow won't answer an order question until it has verified the customer's identity across two
separate exchanges:

```
user:     I'd like some help, my email is sarah.chen@example.com
rainbow:  [calls send_auth_code] → I've sent a 6-digit verification code to your email.
          Could you share that code with me?
user:     123456
rainbow:  [calls verify_auth_code] → You're verified! What would you like to do?
user:     What's the status of order BK-10001?
rainbow:  [calls lookup_order] → Order BK-10001 (The Midnight Library) was shipped to
          482 Elm Street, Portland, OR 97205 and has a status of Delivered.
```

This exact flow was also verified live end-to-end with a real Stytch email during manual testing
(real inbox, real 6-digit code) — the eval-case version above uses dummy data so it can run
without a live email round-trip.

### 2. The agent taking a real action / using a tool

Continuing the same verified session, requesting a return — `initiate_return` checks eligibility
in code and **writes the result to the live Google Sheet**:

```
user:     I'd like to return order BK-10001, I changed my mind.
rainbow:  [calls initiate_return(order_number="BK-10001", reason="I changed my mind.",
           verified_email="sarah.chen@example.com")]
          → Return initiated for order BK-10001 (The Midnight Library). A return QR code
            has been emailed to the customer — they should bring the book and that QR code
            to their nearest USPS, UPS, or FedEx store, no printer needed. Refund will be
            processed within 5-7 business days after the item is received.
```

Verified independently by reading the sheet back after this call — row for BK-10001 shows
`return_status = Requested`.

### 3. Choosing not to answer immediately — a clarifying question

A deliberately vague general question, where Rainbow asks before proceeding instead of guessing:

```
user:     I want to return something
rainbow:  I'd be happy to help you with your return! Before we can access your order
          details and process the return, I need to verify your identity. Could you
          please share your account email address with me?
```

Rather than guessing which order or what the customer means, it asks for the one piece of
information it actually needs next.

---

## Evaluation

Full methodology, per-case results, and a real bug found and fixed during eval (an escalation
rule that was over-triggering on the word "Delivered" alone) are in
**[`docs/Rainbow_Eval_Report.pdf`](docs/Rainbow_Eval_Report.pdf)**.

**Summary:**

| Dataset | Cases | Result |
|---|---|---|
| Single-turn (`tests/eval/datasets/single-turn.json`) | 3 | `final_response_quality`: 3/3 · `safety`: 2/3 (one known false positive — flags routine identity-verification email requests as a PII violation) |
| Multi-turn (`tests/eval/datasets/multi-turn.json`) | 4 | `multi_turn_task_success`: 4/4 · `multi_turn_tool_use_quality`: 4/4 |

Rerun it yourself:
```
cd rainbow
agents-cli eval run --dataset tests/eval/datasets/single-turn.json --config tests/eval/eval_config_single_turn.yaml
agents-cli eval run --dataset tests/eval/datasets/multi-turn.json --config tests/eval/eval_config_multi_turn.yaml
```
