"""Email sender — Gmail SMTP, HTML body inline, CSV/HTML attachments."""

from __future__ import annotations

import configparser
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Optional


def load_email_config(config_path: Path) -> Optional[dict]:
    if not config_path.exists():
        return None
    cp = configparser.ConfigParser()
    cp.read(config_path)
    if "email" not in cp:
        return None
    section = cp["email"]
    cfg = {
        "sender":    section.get("sender", "").strip(),
        "password":  section.get("app_password", "").strip().replace(" ", ""),
        "recipient": section.get("recipient", "").strip(),
        "smtp_host": section.get("smtp_host", "smtp.gmail.com").strip(),
        "smtp_port": section.getint("smtp_port", 465),
    }
    if not cfg["sender"] or not cfg["password"] or not cfg["recipient"]:
        return None
    return cfg


def _build(sender: str, recipient: str, subject: str, html_body: str,
           attachments: list[Path]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(
        "Cash-Secured Put Screener report.\n\n"
        "Your email client doesn't appear to support HTML — open the attached "
        "top15_report.html in a browser, or view the CSVs."
    )
    msg.add_alternative(html_body, subtype="html")

    for path in attachments:
        if not path.exists():
            continue
        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(path, "rb") as f:
            data = f.read()
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def send_email(sender: str, password: str, recipient: str, subject: str,
               html_body: str, attachments: list[Path],
               smtp_host: str = "smtp.gmail.com", smtp_port: int = 465) -> None:
    msg = _build(sender, recipient, subject, html_body, attachments)
    context = ssl.create_default_context()
    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as s:
            s.login(sender, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls(context=context)
            s.login(sender, password)
            s.send_message(msg)
