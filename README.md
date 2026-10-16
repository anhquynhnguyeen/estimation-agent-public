# Estimation Agent

An internal hackathon project from the Digitization team at Anduin.

A client sends back an updated subscription document (subdoc). The app works out what changed, estimates the work, and drafts the ClickUp tasks and the Slack update. A person reviews everything and approves it before anything is sent.

## The problem

When the Digitization team finds issues in a client's subdoc, we email the client a list. The client replies, usually with an updated subdoc and a few extra documents. Today, a person has to:

1. read the reply,
2. compare the old and new subdoc page by page,
3. decide what work it causes,
4. estimate the time,
5. create the ClickUp tasks,
6. post an update in the client's Slack channel.

This is slow and repetitive, and estimates vary from person to person.

## What the app does

1. **Takes in the case.** You give it our email, the client's reply (`.eml` files or pasted text), the Original subdoc with its annotation export (our field map), the Revised subdoc, and any extra PDFs.
2. **Compares Original and Revised page by page.** This is plain code (PyMuPDF), not AI. It finds:
   - text that was added, removed or changed, with quotes as evidence,
   - **reflows**, where one inserted line pushes everything below it down; these count as one shift, not many changes,
   - which annotated fields moved, were reworded, added or removed.
3. **Sends the pair to Doccom**, the internal comparison tool, so reviewers get a visual side-by-side link and PDF.
4. **Puts each page in a category:**
   - text-only page (embedded file swap),
   - fields moved,
   - fields changed,
   - logic change: judged by AI through the company's LiteLLM gateway, or marked by the reviewer.
5. **Estimates the work with a transparent formula** from `config/estimation_playbook.yaml`. It is never an AI guess. Times are rounded the way the team writes them:
   - **PDF annotation:** a placement baseline (e.g. 30 min) plus 70 s per new or reworded field,
   - **CL & UI fix:** 70 s per field that moved or changed, per embedded file, and per Checklist logic change.
6. **Review and approve.** The case page shows:
   - the Slack update in the team's format,
   - the ClickUp parent task `Update - <fund> - <item>` with its *PDF Annotation* and *CL & UI Fix* subtasks, including custom fields and the suggested next item number.

   The reviewer edits anything, then clicks **Approve & send**. Until a manager signs off, everything goes to a test ClickUp list and a test Slack channel.
7. **Estimates vs actual.** A page compares the agent's estimate with the team's estimate and with the real time spent ("Actual Working Hours" in ClickUp). This shows whether the rules are accurate.

## Design principle

**AI makes the judgement calls** (reading messy emails, deciding whether a wording change affects form logic). **Plain code does everything that must be consistent and explainable** (comparing positions, counting fields, computing hours). Every AI answer has to quote its source, and code checks that the quote is really there.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # fill in your own values; never commit .env
.venv/bin/python -m streamlit run app.py
```

Without any tokens, the app still works. You paste emails by hand, and ClickUp and Slack run in "pretend" mode.

To create a fake test case with a known answer key:

```bash
.venv/bin/python scripts/make_synthetic_case.py      # writes synthetic/case_01/
```

Run the tests:

```bash
for t in tests/test_*.py; do .venv/bin/python "$t"; done
```

## Project layout

```
app.py                     Streamlit web app (Cases, New case, Estimates vs actual)
src/pdf/                   PDF text extraction, annotation export reader, page-by-page comparison
src/estimation/            estimation engine and the team's rounding
src/analysis/              AI steps (email analysis, logic check) via LiteLLM, with quote checks
src/integrations/          Doccom, ClickUp, Slack, Front, .eml, and pretend versions for testing
src/review.py              review rules, Slack message, ClickUp tasks, approve / reject
config/                    estimation playbook, review rules, ClickUp field mapping
tests/                     tests against a synthetic case with an answer key
```

## Data and safety

- Client documents, emails and the database live in `data/`, which is never committed (see `.gitignore`).
- Secrets live only in `.env`.
- AI calls go only through the company's LiteLLM gateway.
- Nothing is written to ClickUp or Slack before a person clicks Approve.

## Status

This is a hackathon prototype. On the first real case, the estimate matched the team's own estimate after calibration (PDF annotation 30 mins, CL & UI fix 45 mins). More past cases are needed to confirm the rules hold.
