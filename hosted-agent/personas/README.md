# Personas

A **persona** is the system prompt the hosted agent uses for every conversation.
This folder is where personas live as plain Markdown files. The shipped
example is [`lisa.md`](./lisa.md) — a recruiting-screener persona kept around
as a working reference.

## Switching personas

1. Drop a new Markdown file into this folder, e.g. `personas/support_agent.md`.
2. Point the agent at it via env var (in `hosted-agent/.env`, or in the
   container environment):

   ```env
   PERSONA_FILE=personas/support_agent.md
   ```

3. Restart the agent.

The loader (`hosted-agent/instructions.py`) reads the file verbatim — no
templating, no variable substitution. What you write is what the model gets.

Paths may be absolute or relative; relative paths resolve against
`hosted-agent/`.

## Writing a good persona

Keep it focused. A useful persona usually has four blocks:

1. **Who you are** — one-sentence identity ("You are *Alex*, a friendly
   customer-support assistant for Contoso.")
2. **What you do** — the bounded job ("Help customers track an order,
   open a return, or get to a human agent.")
3. **Hard rules** — what you must *never* do (e.g. "Never quote prices",
   "Never reveal internal SKUs", "Always stay in English").
4. **Tone** — short, friendly, professional. One emoji max. 1–2 sentences
   per turn for voice — the agent is speaking, not writing.

### Voice-specific tips

The agent speaks through Azure Voice Live, so the prompt should encourage
**spoken-style** output:

- Short sentences. No markdown. No bullet lists in replies.
- Tell the model to **wait for the user to finish** before replying
  (Voice Live's VAD does most of this, but the prompt reinforces it).
- If your persona uses an unusual proper noun, give a pronunciation hint
  (e.g. *Pronounce "Contoso" as "kon-TOH-soh"*).

### Tools (optional)

If your persona calls Python tools, register them in `hosted-agent/main.py`
the same way `job_requirements` is registered. The persona should describe
*when* to use a tool ("If the user asks about an order, call
`lookup_order`") — the framework handles the rest.

## Examples to copy from

- `lisa.md` — a structured **first-screening recruiter** persona with
  hard rules around bias, language, and scope. Good template if you want
  a tightly bounded, single-purpose agent.

## Anti-patterns

- ❌ Putting business data (job descriptions, FAQ snippets, KB articles)
  into the persona. Use a **tool** instead — keeps the prompt small,
  keeps the data fresh, and lets you swap data sources without
  redeploying.
- ❌ Asking the model to format Markdown in voice replies. It will say
  "asterisk asterisk hello asterisk asterisk".
- ❌ A persona over ~2,000 words. Voice agents do better with concise,
  punchy prompts. Move detail into tools or into a structured
  knowledge base.
