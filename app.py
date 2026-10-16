"""The web app. Start it with:  .venv/bin/python -m streamlit run app.py

Pages (left sidebar):
  New case  - get the client's reply (from Front, or pasted) plus the Original/Revised files
  Cases     - see saved cases and what's in them
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src import clickup_fields, db, review, settings
from src.cases import (case_dir, case_pdfs, doccom_changes, doccom_info, doccom_pdf, save_case,
                       save_doccom_link, send_to_doccom)
from src.integrations.doccom import DoccomChange, job_id_from_link, repeated_on_most_pages
from src.integrations.front_source import FrontEmailSource, conversation_id_from_link
from src.integrations.base import EmailThread
from src.integrations.eml import eml_attachments, parse_eml
from src.integrations.manual_source import build_manual_thread
from src.pdf.annotation_export import read_export
from src.analysis.change_classifier import classify_changes
from src.analysis.email_analyzer import analyze_email
from src.analysis.llm import AINotAllowed
from src.estimation.engine import estimate, features_from_diff
from src.actuals import actual_minutes_from_clickup
from src.estimation.rounding import time_range
from src.pdf.diff import diff_documents
from src.pdf.extract import extract

st.set_page_config(page_title="Digi Estimation Agent", layout="wide")


# ---------- Small helpers ----------

def file_inputs(key: str, need_v2: bool = True):
    """The upload boxes shared by both tabs. Returns a list of (role, filename, bytes)."""
    st.markdown("**Original (the version we already annotated)**")
    export_zip = st.file_uploader("Annotation export zip (preferred: contains the Original PDF + field map)",
                                  type=["zip"], key=f"{key}_zip")
    v1_pdf = st.file_uploader("...or just the Original PDF (if there's no export)", type=["pdf"], key=f"{key}_v1")
    v2_pdf = None
    if need_v2:
        st.markdown("**Revised (the client's updated version)**")
        v2_pdf = st.file_uploader("Revised PDF", type=["pdf"], key=f"{key}_v2")
    extras = st.file_uploader("Other attachments from the client", accept_multiple_files=True, key=f"{key}_extra")

    files = []
    if export_zip:
        files.append(("annotation_export", export_zip.name, export_zip.getvalue()))
    if v1_pdf:
        files.append(("v1", v1_pdf.name, v1_pdf.getvalue()))
    if v2_pdf:
        files.append(("v2", v2_pdf.name, v2_pdf.getvalue()))
    for f in extras or []:
        files.append(("other", f.name, f.getvalue()))
    return files


# ---------- Page: New case ----------

def page_new_case():
    st.title("New case")
    st.caption("A client replied about issues on an existing fund's subdoc.")

    tab_front, tab_manual = st.tabs(["From Front", "Paste manually"])

    with tab_front:
        front_tab()

    with tab_manual:
        manual_tab()


def front_tab():
    if not settings.FRONT_API_TOKEN:
        st.warning("No Front token yet. Add FRONT_API_TOKEN to the .env file, then restart the app. "
                   "Until then, use the 'Paste manually' tab.")
        return

    source = FrontEmailSource(settings.FRONT_API_TOKEN, settings.FRONT_INBOX_ID)

    # Step 1: choose a conversation (from the list, or paste a Front link)
    st.subheader("1. Choose the conversation")
    link = st.text_input("Paste a Front conversation link (or leave empty and pick from the list)")
    conv_id = conversation_id_from_link(link)
    if not conv_id:
        try:
            recent = source.list_recent(limit=25)
        except Exception as e:  # e.g. wrong token, no internet
            st.error(f"Couldn't reach Front: {e}")
            return
        if not recent:
            st.info("No conversations found.")
            return
        labels = {f"{c.subject}  ·  {c.last_message_at[:10]}  ·  {c.status}": c.conversation_id for c in recent}
        conv_id = labels[st.selectbox("Recent conversations", list(labels))]

    # Step 2: fetch messages + attachments (kept in memory until the case is saved)
    if st.button("Fetch from Front", type="primary"):
        with st.spinner("Downloading messages and attachments..."):
            try:
                download_dir = settings.DATA_DIR / "front" / conv_id
                st.session_state["front_thread"] = source.fetch_thread(conv_id, download_dir)
            except Exception as e:
                st.error(f"Fetch failed: {e}")
                return

    thread = st.session_state.get("front_thread")
    if not thread or thread.conversation_id != conv_id:
        return

    st.subheader("2. Check the emails")
    reply = thread.latest_client_reply()
    st.write(f"**Subject:** {thread.subject}  ·  {len(thread.messages)} messages")
    with st.expander("Our earlier email(s) with the issue list", expanded=False):
        for m in thread.our_earlier_emails():
            st.caption(f"{m.sent_at} · {m.author}")
            st.text(m.body_text)
    with st.expander("Client's latest reply", expanded=True):
        if reply:
            st.caption(f"{reply.sent_at} · {reply.author}")
            st.text(reply.body_text)
        else:
            st.warning("No client reply found in this conversation.")

    # Step 3: say which attachment is the Revised subdoc; add the Original export
    st.subheader("3. Files")
    all_atts = [a for m in thread.messages if m.direction == "inbound" for a in m.attachments]
    pdfs = [a for a in all_atts if a.filename.lower().endswith(".pdf")]
    v2_choice = st.selectbox("Which attachment is the Revised subdoc?",
                             ["(none / upload below)"] + [a.filename for a in pdfs])
    files = file_inputs("front", need_v2=(v2_choice == "(none / upload below)"))
    for a in all_atts:
        role = "v2" if a.filename == v2_choice else "other"
        files.append((role, a.filename, Path(a.path)))

    fund = st.text_input("Fund name", key="front_fund")
    if st.button("Create case", key="front_create"):
        case_id = save_case(thread, "front", fund, files)
        st.success(f"Case #{case_id} saved. See it under 'Cases'.")
        auto_doccom(case_id)
        del st.session_state["front_thread"]


def manual_tab():
    st.info("Backup option: add the emails yourself.")
    fund = st.text_input("Fund name", key="manual_fund")
    emls = st.file_uploader("Upload the emails (.eml), ours and the client's", type=["eml"],
                            accept_multiple_files=True, key="manual_eml",
                            help="Download them from Front or Gmail. Ours = sent from @anduintransact.com.")
    parsed = [(f.name, parse_eml(f.getvalue()), f.getvalue()) for f in emls or []]
    if parsed:
        st.caption(" · ".join(f"{'Anduin' if m.direction == 'outbound' else 'Client'}: {name}" for name, m, _ in parsed))
    with st.expander("...or paste the text", expanded=not parsed):
        subject = st.text_input("Email subject")
        our_email = st.text_area("Our earlier email listing the issues", height=180,
                                 help="The email we sent the client with the list of problems in their subdoc.")
        reply = st.text_area("Client's reply", height=180)
    files = file_inputs("manual")

    if st.button("Create case", key="manual_create", type="primary"):
        if parsed:
            messages = sorted((m for _, m, _ in parsed), key=lambda m: (m.sent_at or "", m.direction))
            thread = EmailThread(subject=messages[-1].subject or subject, messages=messages)
            files += [("other", name, data) for _, _, raw in parsed for name, data in eml_attachments(raw)]
        elif reply.strip() or our_email.strip():
            thread = build_manual_thread(subject, our_email, reply)
        else:
            st.error("Add the emails first (upload .eml files or paste the text).")
            return
        if not any(m.direction == "inbound" for m in thread.messages):
            st.warning("No client reply yet: the case is saved with our email only.")
        case_id = save_case(thread, "manual", fund, files)
        st.success(f"Case #{case_id} saved. See it under 'Cases'.")
        auto_doccom(case_id)


def auto_doccom(case_id: int):
    """Send the new case to Doccom straight away, so the comparison link is ready for the team."""
    original, revised = case_pdfs(case_id)
    if not (original and revised):
        return
    with st.spinner("Sending Original and Revised to Doccom..."):
        try:
            send_to_doccom(case_id)
            st.success("Doccom comparison ready (see the case page).")
        except Exception as e:
            st.warning(f"Couldn't reach Doccom ({e}). You can add the Doccom link by hand on the case page.")


# ---------- Page: Cases ----------

CATEGORY_NAMES = {1: "Text only (no fields)", 2: "Fields moved", 3: "Fields changed", 4: "Logic change"}
NO_CHANGE = "No change"
STATUS_NAMES = {"new": "Waiting", "in_review": "Waiting", "analyzed": "Waiting", "approved": "Sent", "rejected": "Rejected"}


@dataclass
class CaseResult:
    export: object
    diff: object
    feats: object
    est: object
    logic_pages: list
    reasons: list


@st.cache_data(show_spinner=False)
def _diff_cached(original: str, revised: str, export_dir: str | None, _mtimes: tuple):
    export = read_export(Path(export_dir)) if export_dir else None
    return export, diff_documents(Path(original), Path(revised), export)


def compute_case(conn, case_id: int) -> CaseResult | None:
    """Comparison + estimate for a case (plain code). Logic pages come from the AI check and reviewer corrections."""
    original, revised = case_pdfs(case_id)
    if not (original and revised):
        return None
    unzipped = case_dir(case_id) / "annotation_export" / "unzipped"
    export_dir = str(unzipped) if unzipped.exists() else None
    mtimes = (original.stat().st_mtime, revised.stat().st_mtime)
    export, diff = _diff_cached(str(original), str(revised), export_dir, mtimes)
    logic_pages = suggested_logic_pages(conn, case_id)
    email = db.latest_analysis(conn, case_id, "email_analysis")
    total = export.total_fields if export else sum(p.v1_fields for p in diff.pages)
    _, _, files = db.get_case_details(conn, case_id)
    extra_pdfs = [f["filename"] for f in files if f["role"] == "other" and f["filename"].lower().endswith(".pdf")]
    feats = features_from_diff(diff, total, set(logic_pages), len((email or {}).get("logic_change_requests", [])),
                               extra_pdfs)
    est = estimate(feats)
    reasons = review.attention_reasons(est, feats, email, db.latest_analysis(conn, case_id, "change_classification"),
                                       export is not None)
    return CaseResult(export, diff, feats, est, logic_pages, reasons)


def suggested_logic_pages(conn, case_id: int) -> list[int]:
    """Pages marked 'Logic change' by a reviewer, plus pages the AI logic check found (unless a reviewer said otherwise)."""
    corrected = {}
    for r in db.latest_review_decisions(conn, case_id, "page_compare"):
        corrected[r["item_id"]] = (json.loads(r["edited_json"] or "{}").get("right_category") or "")
    ai = db.latest_analysis(conn, case_id, "change_classification") or {}
    pages = {p for p in ai.get("logic_pages", []) if p not in corrected}
    pages |= {p for p, cat in corrected.items() if cat.startswith(("4", CATEGORY_NAMES[4]))}
    return sorted(pages)


def page_cases():
    st.title("Cases")
    conn = db.connect()
    cases = db.list_cases(conn)
    if not cases:
        st.info("No cases yet.")
        return
    cases = sorted(cases, key=lambda c: (c["status"] in ("approved", "rejected"), -c["id"]))  # waiting first

    rows, results = [], {}
    for c in cases:
        res = compute_case(conn, c["id"])
        results[c["id"]] = res
        rows.append({"Case": f"#{c['id']}", "Fund": c["fund_name"] or "", "Status": STATUS_NAMES.get(c["status"], c["status"]),
                     "Estimate": time_range(res.est.low_s / 60, res.est.high_s / 60) if res else "",
                     "Check": ", ".join(res.reasons) if res else "Files missing"})
    picked = st.dataframe(rows, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                          key="case_list")
    chosen = picked.selection.rows[0] if picked.selection.rows else 0
    case = cases[chosen]
    res = results[case["id"]]
    _, messages, files = db.get_case_details(conn, case["id"])

    st.header(f"#{case['id']} · {case['fund_name'] or 'No fund name'}")
    if res and not res.diff.changed_pages:
        st.warning("No differences found between the Original and the Revised. Is the right Original attached? "
                   "(An annotation export may already be of the Revised version: then upload the Original PDF too.)")
    if res:
        show_update(conn, case, res)
        show_comparison(conn, case["id"], res)
    else:
        st.info("This case needs both an Original and a Revised PDF.")
    st.subheader("Email and files")
    show_emails(conn, case["id"], messages, files, res)
    show_files(case["id"], files, res)
    conn.close()


# ---------- Update: Slack message + ClickUp tasks, approve or reject ----------

def show_update(conn, case, res: CaseResult):
    case_id, fund = case["id"], case["fund_name"] or "Fund"
    st.subheader("Update")
    sent = review.already_sent(conn, case_id)
    if case["status"] in ("approved", "rejected"):
        st.write("Sent:" if case["status"] == "approved" else "Rejected.")
        for row in sent:
            r = json.loads(row["edited_json"])
            st.markdown(f"- {r['what']}: {r['link']}" + (" *(test, not really sent)*" if r["pretend"] else ""))
        return

    if res.reasons:
        st.warning("Check: " + " · ".join(res.reasons))
    times = review.rounded_times(res.est, res.feats)
    info = doccom_info(case_id)
    doccom_url = info["review_url"] if info else None

    details = review.change_details(res.diff, set(res.logic_pages), res.feats.added_files)
    message = st.text_area("Slack message", review.slack_message(doccom_url, details, times), height=260,
                           key=f"slack_{case_id}")
    channel = st.text_input("Slack channel", review.default_channel(fund), key=f"channel_{case_id}",
                            help=f"Posted in the {fund} thread (started the first time). While testing, "
                                 "everything goes to the test channel.")
    if not review.get_notifier()[1]:
        with st.expander("Copy for Slack (Slack not connected yet)"):
            st.code(review.copy_text(message), language=None, wrap_lines=True)
            if review.channel_url():
                st.link_button("Open test channel", review.channel_url())
    cfg = clickup_fields.load_config()
    ctx = clickup_context_cached(fund)
    if f"item_{case_id}" not in st.session_state and ctx.get("next_item"):
        st.session_state[f"item_{case_id}"] = ctx["next_item"]
    item = st.text_input("Item", key=f"item_{case_id}", placeholder="e.g. Item 3, Items 2 to 5",
                         help="Suggested from the fund's existing ClickUp tasks when ClickUp is connected.")
    st.markdown(f"**ClickUp:** {review.parent_title(fund, item or '<item>')}")
    tasks_in = review.propose_tasks(fund, case_id, res.est, res.feats, times, doccom_url, details)
    tasks = st.data_editor(tasks_in, key=f"tasks_{case_id}", hide_index=True, width="stretch",
                           column_order=["create", "task", "minutes"],
                           column_config={"create": st.column_config.CheckboxColumn("Create"),
                                          "task": st.column_config.TextColumn("Subtask", width="large"),
                                          "minutes": st.column_config.NumberColumn("Time (min)", min_value=0, step=15)})
    for t, original in zip(tasks, tasks_in):
        t["description"] = original["description"]
    parent_fields, subtask_fields = clickup_field_inputs(conn, case, res, cfg, ctx)

    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Approve & send", type="primary", key=f"approve_{case_id}"):
        try:
            review.approve(conn, case_id, fund, item, tasks, message, channel.lstrip("#"), res.est, reviewer(),
                           doccom_url=doccom_url, parent_fields=parent_fields, subtask_fields=subtask_fields,
                           details=review.details_from_message(message) or details)
            st.rerun()
        except Exception as e:
            st.error(f"Not sent: {e}")
    with c2.popover("Reject"):
        why = st.text_input("Reason", key=f"reject_why_{case_id}")
        if st.button("Reject case", key=f"reject_{case_id}"):
            review.reject(conn, case_id, why, reviewer())
            st.rerun()


@st.cache_data(ttl=600, show_spinner=False)
def clickup_context_cached(fund: str) -> dict:
    try:
        return review.clickup_context(fund, clickup_fields.load_config())
    except Exception:
        return {}


def clickup_field_inputs(conn, case, res: CaseResult, cfg: dict, ctx: dict):
    """The ClickUp custom fields, learned from the team's tasks. Per-case ones can be changed here."""
    case_id = case["id"]
    replies = conn.execute("SELECT sent_at FROM messages WHERE case_id = ? AND direction = 'inbound' ORDER BY id DESC",
                           (case_id,)).fetchall()
    received = clickup_fields.parse_date(replies[0]["sent_at"] if replies else None) \
        or clickup_fields.parse_date(case["created_at"])
    with st.expander("ClickUp fields"):
        c1, c2, c3, c4 = st.columns(4)
        doc_type = c1.selectbox("Doc type received", ["Updated", "Final", "Draft"], key=f"doctype_{case_id}")
        duplicable = c2.selectbox("Duplicable?", ["Yes - Partially", "Yes - Full", "No"],
                                  index=0 if res.feats.field_pages_changed else 1, key=f"dup_{case_id}")
        complexity = c3.selectbox("Complexity", ["", "Light", "Medium", "Heavy", "Extreme Heavy"], key=f"cx_{case_id}",
                                  format_func=lambda v: v or "Leave empty")
        received = c4.date_input("Doc received", received.date() if received else None, key=f"recv_{case_id}")
        values = clickup_fields.parent_values(
            cfg, previous=ctx.get("previous"), doc_type=doc_type,
            received=datetime.combine(received, datetime.min.time()) if received else None,
            front_id=case["front_conversation_id"], fields_changed=duplicable != "Yes - Full", complexity=complexity)
        values["duplicable"] = duplicable
        copied = ", ".join(str(values[k]) for k in cfg["parent"]["copy_from_previous"])
        st.caption(f"Also set: Update - Customer Request · {copied}"
                   + (" (from the fund's last update)" if ctx.get("previous") else "")
                   + " · subtasks: Role DS")
    return clickup_fields.to_clickup(values, cfg), clickup_fields.to_clickup(clickup_fields.subtask_values(cfg), cfg)


def reviewer() -> str | None:
    return st.session_state.get("reviewer") or None


# ---------- Comparison: Doccom + the agent's page-by-page check ----------

def show_comparison(conn, case_id: int, res: CaseResult):
    st.subheader("Comparison")
    info, pdf = doccom_info(case_id), doccom_pdf(case_id)
    c1, c2, c3, _ = st.columns([1, 1, 1, 3])
    if info:
        c1.link_button("Open in Doccom", info["review_url"])
        if pdf:
            c2.download_button("Download PDF", pdf.read_bytes(), file_name=f"case {case_id} comparison.pdf",
                               mime="application/pdf")
    elif c1.button("Send to Doccom", key=f"doccom_send_{case_id}"):
        with st.spinner("Sending to Doccom..."):
            try:
                send_to_doccom(case_id)
                st.rerun()
            except Exception as e:
                st.warning(f"Doccom not reachable ({e}). Add it by hand under Files.")
    if c3.button("Check pages", key=f"check_{case_id}"):
        st.session_state[f"show_pages_{case_id}"] = not st.session_state.get(f"show_pages_{case_id}", False)
    if st.session_state.get(f"show_pages_{case_id}"):
        page_table(conn, case_id, res)


def page_table(conn, case_id: int, res: CaseResult):
    """One row per page. Change 'Category' if the agent got it wrong; 'Logic change' feeds the estimate."""
    doccom = doccom_by_page(case_id)
    saved = {r["item_id"]: json.loads(r["edited_json"] or "{}")
             for r in db.latest_review_decisions(conn, case_id, "page_compare")}
    rows = []
    for p in res.diff.pages:
        page = p.page_v1 or p.page_v2
        agent = CATEGORY_NAMES[4] if page in res.logic_pages else CATEGORY_NAMES.get(p.category, NO_CHANGE)
        right = _category_name(saved.get(page, {}).get("right_category")) or agent
        rows.append({"Page": page, "Section": p.section or "", "What changed": "; ".join(p.reasons) or "",
                     **({} if doccom is None else {"Doccom": "; ".join(describe(c) for c in doccom.get(page, []))}),
                     "Category": right, "Comment": saved.get(page, {}).get("comment", ""), "_agent": agent})
    edited = st.data_editor(rows, key=f"pages_{case_id}", hide_index=True, width="stretch",
                            column_order=["Page", "Section", "What changed", "Doccom", "Category", "Comment"],
                            disabled=["Page", "Section", "What changed", "Doccom"],
                            column_config={"Category": st.column_config.SelectboxColumn(
                                options=[NO_CHANGE, *CATEGORY_NAMES.values()], required=True),
                                "What changed": st.column_config.TextColumn(width="large")})
    if st.button("Save", key=f"save_pages_{case_id}"):
        for row, before in zip(edited, rows):
            if (row["Category"], row["Comment"]) != (before["Category"], before["Comment"]):
                db.add_review_decision(conn, case_id, "page_compare", row["Page"],
                                       "confirmed" if row["Category"] == row["_agent"] else "corrected",
                                       {"agent_category": row["_agent"], "right_category": row["Category"],
                                        "comment": row["Comment"]}, reviewer())
        st.rerun()

    for p in res.diff.changed_pages:
        with st.expander(f"Page {p.page_v1}: details"):
            for t in p.text_changes:
                st.markdown(f"- {t.summary or (t.new or t.old)}" + (" *(new field)*" if t.new_field else ""))
            moved = [{"Wording": h.text, "Status": h.status, "Moved (pt)": h.offset or "", "New wording": h.new_text or ""}
                     for h in p.highlights if h.status != "unchanged"]
            if moved:
                st.table(moved)


def _category_name(value: str | None) -> str | None:
    """Accepts old saved values like '4 · Logic change'."""
    if not value:
        return None
    for name in [NO_CHANGE, *CATEGORY_NAMES.values()]:
        if value.lower().endswith(name.lower()) or value.lower() == name.lower():
            return name
    return {"1": CATEGORY_NAMES[1], "2": CATEGORY_NAMES[2], "3": CATEGORY_NAMES[3], "4": CATEGORY_NAMES[4]}.get(value[:1])


def doccom_by_page(case_id: int) -> dict[int, list[DoccomChange]] | None:
    changes = doccom_changes(case_id)
    if changes is None:
        return None
    repeated = repeated_on_most_pages(changes)
    by_page: dict[int, list[DoccomChange]] = {}
    for c in changes:
        if (c.old, c.new) not in repeated and (c.old or c.new):
            by_page.setdefault(c.page, []).append(c)
    return by_page


def describe(c: DoccomChange) -> str:
    if c.old and c.new:
        return f'"{c.old}" to "{c.new}"'
    return f'added "{c.new}"' if c.new else f'removed "{c.old}"'


# ---------- Emails (and the AI email check) ----------

def show_emails(conn, case_id: int, messages, files, res: CaseResult | None):
    with st.expander("Emails"):
        for m in messages:
            st.markdown(f"**{'Client' if m['direction'] == 'inbound' else 'Anduin'}** {m['sent_at'] or ''}")
            st.text(m["body_text"])
        if not settings.ai_allowed():
            return
        c1, c2, _ = st.columns([1, 1, 3])
        if c1.button("Analyse email (AI)", key=f"ai_email_{case_id}"):
            ours = "\n\n".join(m["body_text"] for m in messages if m["direction"] == "outbound")
            replies = [m for m in messages if m["direction"] == "inbound"]
            names = [f["filename"] for f in files if f["role"] in ("v2", "other")]
            run_ai(conn, case_id, "email_analysis",
                   lambda: analyze_email(ours, replies[-1]["body_text"] if replies else "", names))
        if res and c2.button("Check logic (AI)", key=f"ai_logic_{case_id}"):
            _, revised = case_pdfs(case_id)
            run_ai(conn, case_id, "change_classification", lambda: classify_changes(res.diff, extract(revised)))
        email = db.latest_analysis(conn, case_id, "email_analysis")
        if email:
            st.table([{"#": i.get("n"), "Status": i["status"], "Client's words": i.get("quote", ""),
                       "Sure": f"{i['confidence']:.0%}" + ("" if i["quote_verified"] else " ⚠")} for i in email["issues"]])


def run_ai(conn, case_id, step, call):
    with st.spinner("Asking the AI..."):
        try:
            result = call()
        except AINotAllowed as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"AI step failed: {e}")
            return
    db.add_analysis(conn, case_id, step, result, result.get("confidence"))
    st.rerun()


# ---------- Files (and adding Doccom by hand) ----------

def show_files(case_id: int, files, res: CaseResult | None):
    with st.expander("Files"):
        for f in files:
            st.markdown(f"- {f['filename']} ({ROLE_NAMES.get(f['role'], f['role'])})")
        if res and res.export:
            st.caption(f"{res.export.total_fields} fields · pages without fields: "
                       + (", ".join(map(str, res.export.pages_without_fields)) or "none"))
        st.markdown("**Add Doccom by hand**")
        link = st.text_input("Doccom link", key=f"doccom_link_{case_id}")
        upload = st.file_uploader("Doccom PDF (side by side)", type=["pdf"], key=f"doccom_pdf_{case_id}")
        if st.button("Save", key=f"doccom_save_{case_id}"):
            info = doccom_info(case_id)
            job_id = job_id_from_link(link) or (info or {}).get("job_id")
            if not job_id:
                st.error("Paste a Doccom review link (it contains '?job=').")
            else:
                save_doccom_link(case_id, job_id, "manual", upload.getvalue() if upload else None)
                st.rerun()


ROLE_NAMES = {"v1": "Original", "v2": "Revised", "annotation_export": "annotation export", "other": "attachment"}


# ---------- Page: Estimates vs actual ----------

WORK = {"pdf_annotation": "PDF annotation", "cl_ui": "CL & UI fix"}


def page_results():
    st.title("Estimates vs actual")
    conn = db.connect()
    cases = db.list_cases(conn)
    if not cases:
        st.info("No cases yet.")
        return
    rows = []
    for c in cases:
        res = compute_case(conn, c["id"])
        if not res:
            continue
        t = review.rounded_times(res.est, res.feats)
        ref = db.reference_times(conn, c["id"])
        rows.append({"case_id": c["id"], "Case": f"#{c['id']} {c['fund_name'] or ''}",
                     "ClickUp task": db.get_clickup_task(conn, c["id"]) or "",
                     "Agent PDF": t["annotation"], "Agent CL & UI": t["cl"],
                     "Team PDF": ref.get(("team", "pdf_annotation")), "Team CL & UI": ref.get(("team", "cl_ui")),
                     "Actual PDF": ref.get(("actual", "pdf_annotation")), "Actual CL & UI": ref.get(("actual", "cl_ui"))})

    summary(rows)
    st.caption("Minutes. Agent = this app. Team = the team's own estimate. Actual = \"[PIC] Actual Working Hours\" "
               "from ClickUp. Paste a case's ClickUp task link, then get the actual hours (or type them).")
    num = lambda label: st.column_config.NumberColumn(label, min_value=0, step=5, format="%d")
    edited = st.data_editor(rows, key="results", hide_index=True, width="stretch",
                            column_order=["Case", "ClickUp task", "Agent PDF", "Agent CL & UI", "Team PDF",
                                          "Team CL & UI", "Actual PDF", "Actual CL & UI"],
                            disabled=["Case", "Agent PDF", "Agent CL & UI"],
                            column_config={"ClickUp task": st.column_config.LinkColumn(width="medium"),
                                           **{k: num(k) for k in ("Team PDF", "Team CL & UI", "Actual PDF", "Actual CL & UI")}})
    c1, c2, _ = st.columns([1, 2, 3])
    if c1.button("Save", key="save_results"):
        save_results(conn, edited)
        st.rerun()
    if c2.button("Get actual hours from ClickUp", key="fetch_actuals"):
        save_results(conn, edited)
        fetch_actuals(conn, edited)
        st.rerun()
    conn.close()


def save_results(conn, rows):
    for r in rows:
        if r["ClickUp task"]:
            db.set_clickup_task(conn, r["case_id"], r["ClickUp task"].strip())
        for kind, prefix in (("team", "Team"), ("actual", "Actual")):
            for work_type, col in (("pdf_annotation", "PDF"), ("cl_ui", "CL & UI")):
                value = r.get(f"{prefix} {col}")
                if value is not None and value == value:  # skip empty cells
                    db.set_reference_time(conn, r["case_id"], kind, work_type, float(value))


def fetch_actuals(conn, rows):
    tracker, real = review.get_tracker()
    if not real:
        st.warning("ClickUp isn't connected.")
        return
    for r in rows:
        if not r["ClickUp task"]:
            continue
        try:
            found = actual_minutes_from_clickup(tracker, r["ClickUp task"])
        except Exception as e:
            st.warning(f"{r['Case']}: {e}")
            continue
        for work_type, minutes in found.items():
            db.set_reference_time(conn, r["case_id"], "actual", work_type, minutes, source="clickup")


def summary(rows):
    def total(r, prefix):
        a, b = r.get(f"{prefix} PDF"), r.get(f"{prefix} CL & UI")
        vals = [v for v in (a, b) if v is not None and v == v]
        return sum(vals) if vals else None

    def gap(r, other):
        """Agent minus other, only over the parts (PDF, CL & UI) the other side has a number for."""
        parts = [col for col in ("PDF", "CL & UI") if r.get(f"{other} {col}") is not None and r.get(f"{other} {col}") == r.get(f"{other} {col}")]
        return sum(r[f"Agent {col}"] - r[f"{other} {col}"] for col in parts) if parts else None

    actual = [g for r in rows if (g := gap(r, "Actual")) is not None]
    team = [g for r in rows if (g := gap(r, "Team")) is not None]
    c1, c2, c3 = st.columns(3)
    c1.metric("Cases with actual time", len(actual))
    if actual:
        c2.metric("Agent vs actual (avg difference)", f"{sum(map(abs, actual)) / len(actual):.0f} min",
                  help="Positive = the agent estimated more than the real time.")
    if team:
        avg = sum(team) / len(team)
        c3.metric("Agent vs team estimate (avg)", f"{avg:+.0f} min", help="Positive = the agent estimated more.")
    chart = {r["Case"]: {"Agent": total(r, "Agent") or 0, "Team": total(r, "Team") or 0, "Actual": total(r, "Actual") or 0}
             for r in rows if total(r, "Team") is not None or total(r, "Actual") is not None}
    if chart:
        st.bar_chart(pd.DataFrame(chart).T, stack=False, y_label="minutes")


# ---------- Sidebar ----------

if settings.DATA_MODE == "real":
    st.sidebar.error("Real client data")
else:
    st.sidebar.caption("Test data")
page = st.sidebar.radio("Go to", ["Cases", "New case", "Estimates vs actual"], label_visibility="collapsed")
st.sidebar.text_input("Your name", key="reviewer")
if page == "New case":
    page_new_case()
elif page == "Estimates vs actual":
    page_results()
else:
    page_cases()
