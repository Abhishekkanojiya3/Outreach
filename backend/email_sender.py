"""
Gmail SMTP email sending logic.
Supports plain text emails with PDF attachment and thread continuity headers.
"""

import smtplib
import os
import html as html_lib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.utils import make_msgid, formatdate

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # TLS


def _body_to_html(body, tracking_pixel_url=None):
    """
    Convert the plain-text email body into a simple HTML version that renders
    identically, with an optional invisible 1x1 tracking pixel appended.
    """
    escaped = html_lib.escape(body).replace("\r\n", "\n").replace("\n", "<br>\n")

    pixel = ""
    if tracking_pixel_url:
        pixel = (
            f'<img src="{tracking_pixel_url}" width="1" height="1" '
            f'style="display:block;width:1px;height:1px;border:0;" alt="">'
        )

    return (
        '<html><body>'
        '<div style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; '
        f'color: #202124; line-height: 1.5;">{escaped}</div>'
        f'{pixel}'
        '</body></html>'
    )


def send_email(
    sender_email,
    sender_name,
    app_password,
    recipient_email,
    subject,
    body,
    resume_path,
    reply_to_message_id=None,
    tracking_pixel_url=None,
):
    """
    Send an email via Gmail SMTP with optional resume attachment and threading.

    Args:
        sender_email: Gmail address
        sender_name: Display name for the sender
        app_password: Gmail App Password
        recipient_email: Recipient's email address
        subject: Email subject line
        body: Plain text email body
        resume_path: Path to resume PDF file
        reply_to_message_id: Original Message-ID for follow-up threading
        tracking_pixel_url: Public URL of the 1x1 open-tracking pixel. When set,
            the email is sent as multipart/alternative (plain + HTML) with the
            pixel embedded in the HTML part.

    Returns:
        The Message-ID of the sent email

    Raises:
        Exception on SMTP errors
    """
    msg = MIMEMultipart()
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)

    message_id = make_msgid()
    msg["Message-ID"] = message_id

    # Threading headers for follow-ups
    if reply_to_message_id:
        msg["In-Reply-To"] = reply_to_message_id
        msg["References"] = reply_to_message_id

    if tracking_pixel_url:
        # multipart/alternative: plain-text fallback + HTML with tracking pixel
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, "plain"))
        alt.attach(MIMEText(_body_to_html(body, tracking_pixel_url), "html"))
        msg.attach(alt)
    else:
        msg.attach(MIMEText(body, "plain"))

    # Attach resume PDF
    if resume_path and os.path.exists(resume_path):
        with open(resume_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition", 'attachment; filename="Resume.pdf"'
            )
            msg.attach(part)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(sender_email, app_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())

    return message_id
