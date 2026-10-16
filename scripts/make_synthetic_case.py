"""Builds a fully FAKE case so we can develop and demo without real client data.

Run:  .venv/bin/python scripts/make_synthetic_case.py
Output: synthetic/case_01/  (regenerate any time; nothing in it is real)

The case contains one change of each type in our taxonomy (see CLAUDE.md), and
expected.json says what the agent should find, so we can check its answers.

Assumption to verify on a real export: field `area` is in PDF points, measured
from the top-left corner of the page (PyMuPDF's convention).
"""

import json
import shutil
import zipfile
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "synthetic" / "case_01"

FUND = "Maple Ridge Growth Fund II, L.P."
CLIENT = "Harbor Lane Capital"
PAGE_W, PAGE_H = 612, 792  # US Letter
LEFT, RIGHT = 72, 540
FONT, SIZE, LINE = "helv", 10, 14


# ---------- Document content ----------
# Each page is a list of items. v2 is built from v1 with a few edits, so any
# inserted line naturally pushes the content below it down (a reflow).

def v1_pages():
    return [
        [  # 1: instructions, no fields
            ("heading", "Instructions for Subscribers"),
            ("para", f"Thank you for your interest in {FUND} (the \"Fund\"). Please complete every "
                     "section of this Subscription Booklet that applies to you."),
            ("para", "Subscription amounts must be wired to the Fund's account within 5 business days "
                     "after the Fund accepts your subscription."),
            ("para", "Return the completed booklet, together with any required tax forms, to the "
                     "Fund's administrator."),
        ],
        [  # 2: investor information, text fields
            ("heading", "Part A - Investor Information"),
            ("text_field", "investor_name", "Full legal name"),
            ("text_field", "address", "Residential or registered address"),
            ("text_field", "email", "Email address"),
            ("text_field", "phone", "Telephone number"),
            ("text_field", "tax_id", "Taxpayer identification number"),
        ],
        [  # 3: investor type, checkboxes
            ("heading", "Part B - Investor Type"),
            ("para", "Check the ONE box that best describes the Subscriber."),
            ("checkbox", "investor_type", "type_individual", "An individual (natural person)"),
            ("checkbox", "investor_type", "type_corporation", "A corporation"),
            ("checkbox", "investor_type", "type_partnership", "A partnership or limited liability company"),
            ("checkbox", "investor_type", "type_trust", "A trust"),
            ("checkbox", "investor_type", "type_ira", "An individual retirement account (IRA)"),
        ],
        [  # 4: tax status, radios + checkboxes
            ("heading", "Part C - Tax Status"),
            ("para", "Is the Subscriber a United States person for U.S. federal income tax purposes?"),
            ("radio", "us_person", "us_person_yes", "Yes"),
            ("radio", "us_person", "us_person_no", "No"),
            ("para", "If you are a United States person, attach a completed IRS Form W-9. "
                     "Otherwise, attach the applicable IRS Form W-8."),
            ("checkbox", "tax_forms", "w9_attached", "Form W-9 attached"),
            ("checkbox", "tax_forms", "w8_attached", "Form W-8BEN attached"),
        ],
        [  # 5: signature
            ("heading", "Part D - Signature"),
            ("para", "By signing below, the Subscriber confirms that the information in this "
                     "Subscription Booklet is true and complete."),
            ("signature", "subscriber_signature", "Signature of Subscriber"),
            ("text_field", "signatory_name", "Printed name"),
            ("text_field", "signature_date", "Date"),
        ],
        [  # 6: privacy notice, no fields
            ("heading", "Privacy Notice"),
            ("para", "The Fund collects personal information from Subscribers to process "
                     "subscriptions and to meet its legal and regulatory obligations."),
            ("para", "The Fund does not sell personal information. Information may be shared with "
                     "service providers, such as the administrator, under confidentiality terms."),
        ],
    ]


def v2_pages():
    pages = v1_pages()
    # p1 (type 1): text change on a page with no fields
    pages[0][2] = ("para", "Subscription amounts must be wired to the Fund's account within 10 business days "
                           "after the Fund accepts your subscription.")
    # p2 (type 2): one inserted sentence pushes every field down by the same amount
    pages[1].insert(1, ("para", "All fields in this Part are required."))
    # p3 (type 4): new investor type = new Checklist logic
    pages[2].insert(6, ("checkbox", "investor_type", "type_family_office",
                        "A family office with at least $5,000,000 in assets under management"))
    # p4 (type 4): changed gating condition; (type 3): reworded field
    pages[3][4] = ("para", "If you are a United States person or a U.S. tax resident, attach a completed "
                           "IRS Form W-9. Otherwise, attach the applicable IRS Form W-8.")
    pages[3][6] = ("checkbox", "tax_forms", "w8_attached", "Form W-8BEN or Form W-8BEN-E attached")
    return pages


# ---------- Drawing ----------

def wrap(text, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if pymupdf.get_text_length(trial, fontname=FONT, fontsize=SIZE) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + [line] if line else lines


def text_area(x, baseline, text, size=SIZE):
    """Box around a single line of text, top-left origin, in points."""
    w = pymupdf.get_text_length(text, fontname=FONT, fontsize=size)
    return {"left": round(x, 2), "top": round(baseline - size, 2),
            "right": round(x + w, 2), "bottom": round(baseline + size * 0.25, 2)}


def render(pages, pdf_path, title):
    """Draw the pages and return the annotation objects (field map) for them."""
    doc = pymupdf.open()
    objects = []
    groups = {}  # (page, group) -> container object

    for p_index, items in enumerate(pages):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text((LEFT, 50), title, fontname=FONT, fontsize=8, color=(0.4, 0.4, 0.4))
        y = 100

        def label(fid, text, x, baseline):
            objects.append({"__typename__": "PdfTextLabelDTO", "id": f"lbl-{fid}", "name": f"{fid}_label",
                            "pageIndex": p_index, "area": text_area(x, baseline, text),
                            "content": text, "labelOf": fid})

        def add_to_group(kind, group, child_id, area):
            key = (p_index, group)
            if key not in groups:
                groups[key] = {"__typename__": kind, "id": f"grp-{group}", "name": group,
                               "pageIndex": p_index, "area": dict(area), "children": []}
                objects.append(groups[key])
            g = groups[key]
            g["children"].append(child_id)
            g["area"] = {"left": min(g["area"]["left"], area["left"]), "top": min(g["area"]["top"], area["top"]),
                         "right": max(g["area"]["right"], area["right"]),
                         "bottom": max(g["area"]["bottom"], area["bottom"])}

        for item in items:
            kind = item[0]
            if kind == "heading":
                page.insert_text((LEFT, y), item[1], fontname="hebo", fontsize=14)
                y += 30
            elif kind == "para":
                for line in wrap(item[1], RIGHT - LEFT):
                    page.insert_text((LEFT, y), line, fontname=FONT, fontsize=SIZE)
                    y += LINE
                y += 10
            elif kind == "text_field":
                _, fid, text = item
                page.insert_text((LEFT, y), text, fontname=FONT, fontsize=SIZE)
                page.draw_line((300, y + 2), (RIGHT, y + 2), width=0.5)
                label(fid, text, LEFT, y)
                objects.append({"__typename__": "PdfTextFieldDTO", "id": fid, "name": fid, "pageIndex": p_index,
                                "area": {"left": 300, "top": y - 10, "right": RIGHT, "bottom": y + 3}})
                y += 28
            elif kind in ("checkbox", "radio"):
                _, group, fid, text = item
                box = pymupdf.Rect(LEFT, y - 9, LEFT + 10, y + 1)
                if kind == "checkbox":
                    page.draw_rect(box, width=0.7)
                else:
                    page.draw_circle(((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2), 5, width=0.7)
                page.insert_text((LEFT + 18, y), text, fontname=FONT, fontsize=SIZE)
                label(fid, text, LEFT + 18, y)
                area = {"left": box.x0, "top": box.y0, "right": box.x1, "bottom": box.y1}
                field_type = "PdfCheckboxFieldDTO" if kind == "checkbox" else "PdfRadioFieldDTO"
                objects.append({"__typename__": field_type, "id": fid, "name": fid,
                                "pageIndex": p_index, "area": area})
                group_type = "PdfMultipleCheckboxesFieldDTO" if kind == "checkbox" else "PdfRadioGroupFieldDTO"
                add_to_group(group_type, group, fid, area)
                y += 20
            elif kind == "signature":
                _, fid, text = item
                page.draw_rect(pymupdf.Rect(300, y - 20, 500, y + 4), width=0.5)
                page.insert_text((LEFT, y), text, fontname=FONT, fontsize=SIZE)
                label(fid, text, LEFT, y)
                objects.append({"__typename__": "PdfSignatureFieldDTO", "id": fid, "name": fid,
                                "pageIndex": p_index, "area": {"left": 300, "top": y - 20, "right": 500, "bottom": y + 4}})
                y += 40
        page.insert_text((PAGE_W / 2 - 10, PAGE_H - 40), f"{p_index + 1}", fontname=FONT, fontsize=9)

    doc.save(pdf_path)
    doc.close()
    return objects


# ---------- Emails ----------

OUR_EMAIL = f"""Hi {CLIENT} team,

Thank you for sending the Subscription Booklet for {FUND}. While setting it up we found the following issues:

1. Investor type (Part B, page 3): the PPM allows family offices to invest, but the booklet has no family office option.
2. Tax forms (Part C, page 4): the W-8 option only mentions Form W-8BEN. Entity investors need Form W-8BEN-E.
3. Wire deadline (Instructions, page 1): the booklet says 5 business days, but the PPM says 10 business days.
4. Signature page (Part D, page 5): there is no "Title" field for people signing on behalf of an entity.
5. Please send us the side letter template you mentioned on our call.

Could you send an updated booklet and the side letter template?

Best regards,
Digitization Team
"""

CLIENT_REPLY = f"""Hi Digitization Team,

Thanks for the careful review. Please find attached the updated Subscription Booklet and the side letter template.

- We added a family office option to Part B. Family offices need at least $5,000,000 in assets under management.
- Part C now mentions both Form W-8BEN and Form W-8BEN-E. We also clarified that U.S. tax residents must provide a W-9.
- The wire deadline is now 10 business days, matching the PPM.
- On the Title field: is it really needed if the signatory is an individual? Could you explain how it would work before we change the page?
- The side letter template is attached. Note it is still a draft; the final version will follow next week.

We also added a short note at the top of Part A saying all fields are required.

Kind regards,
Jordan Lee
{CLIENT}
"""

EXPECTED = {
    "issues": [
        {"n": 1, "status": "resolved", "evidence": "We added a family office option to Part B."},
        {"n": 2, "status": "resolved", "evidence": "Part C now mentions both Form W-8BEN and Form W-8BEN-E."},
        {"n": 3, "status": "resolved", "evidence": "The wire deadline is now 10 business days"},
        {"n": 4, "status": "open", "client_asked_question": True,
         "evidence": "is it really needed if the signatory is an individual?"},
        {"n": 5, "status": "partial", "evidence": "it is still a draft; the final version will follow next week"},
    ],
    "client_asked_question": True,
    "unrequested_change_mentioned": "note at the top of Part A saying all fields are required",
    "pages": [
        {"page": 1, "category": 1, "note": "instruction page text change (5 -> 10 business days)"},
        {"page": 2, "category": 2, "note": "one inserted sentence; all 5 fields shift down by the same offset (one reflow)"},
        {"page": 3, "category": 4, "note": "new investor type 'family office' with $5,000,000 AUM condition; 1 new checkbox "
                                           "inserted above the IRA option, which shifts down"},
        {"page": 4, "category": 4, "note": "gating condition now includes U.S. tax residents (logic); "
                                           "W-8 checkbox reworded (category 3)"},
        {"page": 5, "category": None, "note": "no change"},
        {"page": 6, "category": None, "note": "no change"},
    ],
    "fields_added": 1,
    "fields_reworded": 1,
    "logic_change": True,
    "needs_attention": True,
}


# ---------- Build everything ----------

def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    v1_name = "Maple Ridge Growth Fund II - Subscription Booklet (old).pdf"
    v2_name = "Maple Ridge Growth Fund II - Subscription Booklet (updated).pdf"
    objects = render(v1_pages(), OUT / v1_name, f"{FUND} - Subscription Booklet - SYNTHETIC v1")
    render(v2_pages(), OUT / v2_name, f"{FUND} - Subscription Booklet - SYNTHETIC v2")

    # Annotation export zip, same layout as the real tool's export
    annotation = {"fileName": v1_name, "fileContentHash": "synthetic", "totalPages": 6, "pdfObjectDTO": objects}
    zip_path = OUT / "Maple Ridge Growth Fund II - Subscription Booklet - Version 3.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(OUT / v1_name, v1_name)
        z.writestr("annotation_document_data.json", json.dumps(annotation, indent=2))
        z.writestr("record_data.json", json.dumps({"synthetic": True}))
        z.writestr("textract_annotation_target.json", json.dumps({}))

    # Extra attachment from the client
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_text((LEFT, 100), "Side Letter Template - DRAFT (synthetic)", fontname="hebo", fontsize=14)
    page.insert_text((LEFT, 130), f"This side letter is entered into in connection with {FUND}.", fontname=FONT, fontsize=SIZE)
    doc.save(OUT / "Side Letter Template - DRAFT.pdf")
    doc.close()

    (OUT / "our_email.txt").write_text(OUR_EMAIL, encoding="utf-8")
    (OUT / "client_reply.txt").write_text(CLIENT_REPLY, encoding="utf-8")
    (OUT / "case.json").write_text(json.dumps({"subject": f"RE: {FUND} - Subscription Booklet issues",
                                               "fund": FUND, "client": CLIENT}, indent=2), encoding="utf-8")
    (OUT / "expected.json").write_text(json.dumps(EXPECTED, indent=2), encoding="utf-8")

    fields = [o for o in objects if o["__typename__"] not in ("PdfTextLabelDTO", "PdfTextBlockDTO")]
    print(f"Synthetic case written to {OUT.relative_to(ROOT)}/")
    print(f"  v1 annotation objects: {len(objects)} ({len(fields)} without text highlights)")
    for f in sorted(OUT.iterdir()):
        print("  -", f.name)


if __name__ == "__main__":
    main()
