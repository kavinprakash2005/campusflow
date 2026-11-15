"""PDF output (ReportLab): request summary and bonafide certificate."""
import io
import json
import os 
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

def _esc(text):
    return (str(text or "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br/>"))

NAVY=colors.HexColor("#14224f")
INDIGO=colors.HexColor("#4f46e5")

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "peclogo12.png")


def _page_decor(college, logo_h=14 * mm, watermark=False):
    def draw(canvas, doc):
        w, h = doc.pagesize
        canvas.saveState()
        x = doc.leftMargin
        if os.path.exists(LOGO_PATH):
            try:
                canvas.drawImage(LOGO_PATH, x, h - 8 * mm - logo_h, height=logo_h, width=logo_h,
                                 preserveAspectRatio=True, mask="auto", anchor="sw")
                x += logo_h + 4 * mm
                if watermark:
                    canvas.saveState()
                    canvas.setFillAlpha(0.06)
                    size = 110 * mm
                    canvas.drawImage(LOGO_PATH, (w - size) / 2, (h - size) / 2, width=size, height=size,
                                     preserveAspectRatio=True, mask="auto")
                    canvas.restoreState()
            except Exception:
                pass
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(x, h - 8 * mm - logo_h / 2 - 1 * mm, str(college or ""))
        canvas.setStrokeColor(INDIGO)
        canvas.line(doc.leftMargin, h - 10 * mm - logo_h, w - doc.rightMargin, h - 10 * mm - logo_h)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(w - doc.rightMargin, 8 * mm, "Page %d" % doc.page)
        canvas.restoreState()
    return draw




def request_pdf(req, steps, history, college, show_private=True):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Request %s" % req["request_uid"])
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=NAVY, fontSize=18, alignment=0)
    h2 = ParagraphStyle("h2", parent=ss["Heading3"], textColor=INDIGO, spaceBefore=10)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=14)
    small = ParagraphStyle("s", parent=body, fontSize=8, textColor=colors.grey)
    el = [Paragraph(_esc(college), small), Paragraph("Request %s" % req["request_uid"], h1)]

    extra = json.loads(req.get("extra") or "{}")
    rows = [["Type", req["type_name"]], ["Status", req["status"].title()],
            ["Requester", "%s (%s)" % (req["requester_name"], req["requester_cid"])],
            ["Department", req.get("dept_name") or "—"], ["Submitted", req["created_at"]]]
    if req.get("start_date"):
        rows.append(["Dates", req["start_date"] if req["start_date"] == req["end_date"]
                     else "%s to %s (%d days)" % (req["start_date"], req["end_date"], req["days"])])
    for k, v in extra.items():
        rows.append([k.replace("_", " ").title(), v])
    rows.append(["Title", req["title"]])
    if show_private:
        rows += [["Reason", req["reason"]], ["Description", req.get("description") or "—"]]
    t = Table([[Paragraph("<b>%s</b>" % _esc(a), body), Paragraph(_esc(b), body)] for a, b in rows],
              colWidths=[38 * mm, 130 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d7dcea")),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f5fb")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el += [t, Paragraph("Approval route", h2)]
    srows = [["#", "Stage", "Assigned", "Status"]] + [
        [s["step_order"], s["step_role"].title(), s.get("assigned_name") or "Role based", s["status"].title()]
        for s in steps]
    st = Table(srows, colWidths=[10 * mm, 45 * mm, 70 * mm, 43 * mm])
    st.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d7dcea")), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    el += [st, Paragraph("Decision history", h2)]
    hrows = [["When", "Who", "Action", "Remark"]] + [
        [h["created_at"], "%s (%s)" % (h["actor_name"], h["actor_role"]), h["action"].title(),
         Paragraph(_esc(h["remark"] if show_private else "—"), body)] for h in history]
    ht = Table(hrows, colWidths=[33 * mm, 48 * mm, 25 * mm, 62 * mm], repeatRows=1)
    ht.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d7dcea")),
                            ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    el += [ht, Spacer(1, 10), Paragraph("Generated by CampusFlow. Computer-generated summary.", small)]
    doc.build(el)
    return buf.getvalue()


def bonafide_pdf(req, approved_by, college):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=25 * mm, rightMargin=25 * mm,
                            topMargin=30 * mm, bottomMargin=25 * mm, title="Bonafide %s" % req["request_uid"])
    ss = getSampleStyleSheet()
    centre = ParagraphStyle("c", parent=ss["Title"], textColor=NAVY, fontSize=20)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=12, leading=22, spaceBefore=8)
    extra = json.loads(req.get("extra") or "{}")
    date = (req.get("completed_at") or req["updated_at"])[:10]
    el = [Paragraph(_esc(college).upper(), centre), Paragraph("BONAFIDE CERTIFICATE", centre), Spacer(1, 18),
          Paragraph("Ref: %s &nbsp;&nbsp;&nbsp; Date: %s" % (req["request_uid"], date), body),
          Paragraph("This is to certify that <b>%s</b> (College ID: <b>%s</b>) is a bonafide student of the "
                    "Department of <b>%s</b> of this institution." % (
                        _esc(req["requester_name"]), _esc(req["requester_cid"]), _esc(req.get("dept_name"))), body),
          Paragraph("This certificate is issued on the student's request for the purpose of <b>%s</b> "
                    "(%s copy/copies)." % (_esc(extra.get("purpose", "official use")), _esc(extra.get("copies", "1"))), body),
          Spacer(1, 60), Paragraph("Authorised by: <b>%s</b>" % _esc(approved_by), body),
          Paragraph("<font size=8 color='grey'>Digitally approved through CampusFlow. Verify using the reference above.</font>", body)]
    doc.build(el)
    return buf.getvalue()
