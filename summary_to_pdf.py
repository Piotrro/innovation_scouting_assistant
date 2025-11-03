from ibm_watsonx_orchestrate.agent_builder.tools import tool
from typing import List, Dict, Optional
from io import BytesIO
import re
import os
import json
import ast
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Imports for sending emails with attachments
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

@tool
def build_conversation_pdf(
    subject: str,
    recipient_email: str,
    conversation_text: str,
    sections: Optional[str] = None,
    page_size: str = "A4",
    parse_markdown_headings: bool = True
) -> str:
    """
    Build a conversation PDF in memory and email it, returning the conversation history.
    """
    if isinstance(sections, str) and sections.strip().lower() in {"none", "null", "", "undefined"}:
        sections = None

    ttf_path = None
    title = subject

    conversation_text = conversation_text.replace("\\n", "\n")  # Unescape serialized newlines

    sects = _parse_sections_json(sections)
    pmh = _coerce_bool(parse_markdown_headings)

    if not sects and (conversation_text is None or not str(conversation_text).strip()):
        pmh = False

    if (not sects) and (conversation_text is not None) and pmh:
        parsed = _parse_markdown_to_sections(conversation_text)
        sects = parsed if parsed else [{"heading": "Summary", "content": conversation_text.strip()}]
    if (not sects) and (conversation_text is not None):
        sects = [{"heading": "Summary", "content": (conversation_text or "").strip()}]
    if not sects:
        sects = [{"heading": "Summary", "content": "No content."}]

    pdf_bytes = _build_pdf_bytes(
        summary_text=conversation_text,
        sections=sects,
        title=title,
        page_size=page_size,
        ttf_path=ttf_path,
        parse_markdown_headings=False
    )

    sender_email = ""
    sender_password = ""
    body_text = "Please find attached the conversation summary in PDF format."

    message = MIMEMultipart()
    message["Subject"] = subject
    message["From"] = sender_email
    message["To"] = recipient_email
    message.attach(MIMEText(body_text, 'plain'))

    pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf")
    pdf_part.add_header('Content-Disposition', 'attachment', filename="ConversationSummary.pdf")
    message.attach(pdf_part)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, message.as_string())
    except smtplib.SMTPAuthenticationError as e:
        raise RuntimeError("Authentication SMTP error.") from e
    except Exception as e:
        raise RuntimeError(f"Unable to send mail: {e}") from e

    output_lines = []
    for sec in sects:
        heading = (sec.get("heading") or "").strip()
        content = (sec.get("content") or "").strip()
        if heading:
            output_lines.append(f"{heading}:")
        if content:
            output_lines.append(content)
        output_lines.append("")
    return "\n".join(output_lines).strip()


def _coerce_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(val)


def _parse_sections_json(sections_json: Optional[str]) -> List[Dict[str, str]]:
    if sections_json is None:
        return []
    s = str(sections_json).strip()
    if s == "" or s.lower() in {"none", "null", "undefined"}:
        return []
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        try:
            data = ast.literal_eval(s)
        except Exception:
            return [{"heading": "Summary", "content": s}]
    if data is None:
        return []
    return _normalize_sections_list(data if isinstance(data, list) else [data])


def _normalize_sections_list(items) -> List[Dict[str, str]]:
    norm: List[Dict[str, str]] = []
    if items is None:
        return []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return [{"heading": "Summary", "content": str(items)}]
    for it in items:
        if it is None:
            continue
        if isinstance(it, dict):
            heading = str(it.get("heading", "") or "").strip()
            content = str(it.get("content", "") or "").strip()
            if heading or content:
                norm.append({"heading": heading, "content": content})
        else:
            value = str(it).strip()
            if value:
                norm.append({"heading": "Summary", "content": value})
    return norm


def _build_pdf_bytes(
    summary_text: Optional[str],
    sections: Optional[List[Dict[str, str]]],
    title: Optional[str],
    page_size: str,
    ttf_path: Optional[str],
    parse_markdown_headings: bool
) -> bytes:
    page_size_obj = A4 if (page_size or "").upper() == "A4" else LETTER
    body_font = "Helvetica"
    heading_font = "Helvetica-Bold"
    if ttf_path:
        if not os.path.isfile(ttf_path):
            raise Exception(f"TTF font not found: {ttf_path}")
        font_name = os.path.splitext(os.path.basename(ttf_path))[0]
        pdfmetrics.registerFont(TTFont(font_name, ttf_path))
        body_font = font_name
        heading_font = font_name
    if (not sections) and (summary_text is not None) and parse_markdown_headings:
        parsed = _parse_markdown_to_sections(summary_text)
        sections = parsed if parsed else [{"heading": "Summary", "content": summary_text.strip()}]
    if (not sections) and (summary_text is not None):
        sections = [{"heading": "Summary", "content": summary_text.strip()}]
    if not sections:
        sections = [{"heading": "Summary", "content": "No content."}]

    styles = getSampleStyleSheet()
    if "DocTitle" not in styles:
        styles.add(ParagraphStyle(name="DocTitle", parent=styles["Title"], fontName=heading_font,
                                  alignment=TA_CENTER, fontSize=22, spaceAfter=18))
    if "H1" not in styles:
        styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], fontName=heading_font,
                                  fontSize=16, spaceBefore=12, spaceAfter=6))
    if "Body" not in styles:
        styles.add(ParagraphStyle(name="Body", parent=styles["BodyText"], fontName=body_font,
                                  fontSize=10, leading=14))

    buffer = BytesIO()

    def _header_footer(canvas, doc):
        canvas.saveState()
        if title:
            canvas.setFont(body_font, 8)
            canvas.drawString(40, 20, str(title))
        canvas.setFont(body_font, 8)
        canvas.drawRightString(doc.pagesize[0] - 40, 20, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size_obj,
        topMargin=36, bottomMargin=36, leftMargin=48, rightMargin=48
    )

    story = []
    if title:
        story.append(Paragraph(_escape_html(str(title)), styles["DocTitle"]))
        story.append(Spacer(1, 12))

    for idx, sec in enumerate(sections):
        heading = (sec.get("heading") or "").strip()
        content = (sec.get("content") or "").strip()
        if heading:
            story.append(Paragraph(_escape_html(heading), styles["H1"]))
        if content:
            for p in re.split(r"\n{2,}", content):
                if p.strip():
                    story.append(Paragraph(_escape_html(p.strip()), styles["Body"]))
                    story.append(Spacer(1, 8))
        if idx < len(sections) - 1:
            story.append(Spacer(1, 12))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    pdf_data = buffer.getvalue()
    buffer.close()
    return pdf_data


def _parse_markdown_to_sections(md: str) -> List[Dict[str, str]]:
    lines = (md or "").splitlines()
    sections: List[Dict[str, str]] = []
    current_heading: Optional[str] = None
    current_content: List[str] = []
    heading_regex = re.compile(r"^(#{1,2})\s+(.*)$")

    def flush():
        nonlocal current_heading, current_content
        if (current_heading and current_heading.strip()) or any(line.strip() for line in current_content):
            sections.append({
                "heading": current_heading or "Section",
                "content": "\n".join(current_content).strip()
            })
        current_heading = None
        current_content = []

    for line in lines:
        m = heading_regex.match(line)
        if m:
            flush()
            current_heading = m.group(2).strip()
        else:
            current_content.append(line)
    flush()
    return sections


def _escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")