"""
Flask application with all API routes for the outreach tool.
"""

import os
import random
import threading
import time
import uuid
from datetime import datetime

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename

from config import load_config, save_config, is_profile_complete, is_settings_complete
from db import init_db, get_db, query_db, execute_db
from utils import parse_email_list, extract_email_text_from_file
from email_sender import send_email
from ai_generator import generate_email, generate_followup, generate_contextual_followup
from resume_parser import parse_resume

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

def get_openai_key(config):
    """Return the configured OpenAI API key, or raise if missing."""
    key = config.get("openai_api_key", "").strip()
    if not key:
        raise ValueError("No OpenAI API key configured. Go to Settings.")
    return key

app = Flask(__name__)


def get_cors_origins():
    """Return allowed frontend origins from CORS_ORIGINS or local defaults."""
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if raw:
        return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


# Restrict CORS to trusted frontend origins only.
CORS(app, origins=get_cors_origins())

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# In-memory progress tracking for send jobs
send_progress = {}

generation_progress = {}
# Structure per campaign_id:
# {
#   "total": 10,
#   "completed": 0,
#   "failed": 0,
#   "status": "generating" | "complete" | "error",
#   "errors": []
# }

from apscheduler.schedulers.background import BackgroundScheduler
from inbox_monitor import run_inbox_monitor
import threading


def extract_domain(email):
    """Extract the domain part from an email address."""
    return email.split("@")[-1].strip().lower() if "@" in email else ""


# 1x1 transparent GIF served by the open-tracking endpoint
TRACKING_PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00!\xf9\x04"
    b"\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D"
    b"\x01\x00;"
)


def build_tracking_pixel_url(config, tracking_id):
    """
    Return the public pixel URL for a tracking id, or None if open tracking
    isn't configured. Needs a publicly reachable base URL (e.g. an ngrok
    tunnel or deployed server) — recipients' mail clients must be able to
    fetch it from the internet.
    """
    base = (config.get("tracking_base_url") or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/track/open/{tracking_id}"


# Global variable to store last monitor run result
_last_monitor_result = {}
_monitor_lock = threading.Lock()

def scheduled_inbox_check():
    """Runs every 10 minutes in background."""
    global _last_monitor_result
    from config import load_config
    from db import get_db
    config = load_config()
    conn = get_db()
    try:
        with _monitor_lock:
            result = run_inbox_monitor(config, conn)
            result["triggered_by"] = "scheduler"
            _last_monitor_result = result
            print(f"[Inbox Monitor] Bounces: {result.get('bounces_detected', 0)}, "
                  f"OOO: {result.get('ooo_detected', 0)}, "
                  f"Updated: {result.get('updated', 0)}")
    except Exception as e:
        print(f"[Inbox Monitor] Scheduler error: {e}")
    finally:
        conn.close()

# Start scheduler when app starts
scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_inbox_check, 'interval', minutes=10, id='inbox_monitor')
scheduler.start()

import atexit
atexit.register(lambda: scheduler.shutdown(wait=False))


# ─── Profile Routes ──────────────────────────────────────────────────────────


@app.route("/api/profile", methods=["GET"])
def get_profile():
    config = load_config()
    return jsonify({
        "profile": config.get("profile", {}),
        "is_complete": is_profile_complete(config),
        "resume_parsed": config.get("resume_parsed", {}),
        "has_resume": os.path.exists(
            os.path.join(UPLOAD_FOLDER, "resume.pdf")
        ),
    })


@app.route("/api/profile", methods=["POST"])
def save_profile():
    config = load_config()
    data = request.json

    if not data:
        return jsonify({"error": "No data provided"}), 400

    profile_fields = [
        "name", "college", "branch", "year",
        "cgpa", "skills", "github", "linkedin", "bio",
    ]
    for field in profile_fields:
        if field in data:
            config["profile"][field] = data[field]

    save_config(config)
    return jsonify({"message": "Profile saved successfully"})


@app.route("/api/upload-resume", methods=["POST"])
def upload_resume():
    if "resume" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["resume"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are accepted"}), 400

    # Check file size (5MB max)
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > 5 * 1024 * 1024:
        return jsonify({"error": "File too large. Maximum size is 5MB."}), 400

    filepath = os.path.join(UPLOAD_FOLDER, "resume.pdf")
    file.save(filepath)

    config = load_config()
    config["resume_path"] = filepath
    
    # Trigger parsing automatically if an OpenAI key exists
    openai_api_key = config.get("openai_api_key", "")
    if openai_api_key:
        try:
            parsed = parse_resume(filepath, openai_api_key)
            config["resume_parsed"] = parsed
        except Exception as e:
            print(f"Resume parsing failed: {e}")
            config["resume_parsed"] = {}
    else:
        config["resume_parsed"] = {}

    save_config(config)

    return jsonify({"message": "Resume uploaded successfully", "parsed": config["resume_parsed"]})


@app.route("/api/resume/reparse", methods=["POST"])
def reparse_resume():
    config = load_config()
    openai_api_key = config.get("openai_api_key", "")
    resume_path = config.get("resume_path", os.path.join(UPLOAD_FOLDER, "resume.pdf"))

    if not openai_api_key:
        return jsonify({"error": "OpenAI API key not configured. Go to Settings."}), 400

    if not os.path.exists(resume_path):
        return jsonify({"error": "No resume uploaded yet"}), 400

    try:
        parsed = parse_resume(resume_path, openai_api_key)
        config["resume_parsed"] = parsed
        save_config(config)
        return jsonify({"success": True, "parsed": parsed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Settings Routes ─────────────────────────────────────────────────────────


@app.route("/api/settings", methods=["GET"])
def get_settings():
    config = load_config()
    return jsonify({
        "gmail_address": config.get("gmail_address", ""),
        "has_gmail_password": bool(config.get("gmail_app_password", "")),
        "has_openai_key": bool(config.get("openai_api_key", "")),
        "send_delay_seconds": config.get("send_delay_seconds", 60),
        "tracking_base_url": config.get("tracking_base_url", ""),
        "is_complete": is_settings_complete(config),
    })


@app.route("/api/settings", methods=["POST"])
def save_settings():
    config = load_config()
    data = request.json

    if not data:
        return jsonify({"error": "No data provided"}), 400

    if "gmail_address" in data:
        config["gmail_address"] = data["gmail_address"]
    if "gmail_app_password" in data and data["gmail_app_password"]:
        config["gmail_app_password"] = data["gmail_app_password"]
    if "openai_api_key" in data and data["openai_api_key"]:
        config["openai_api_key"] = data["openai_api_key"]
    if "send_delay_seconds" in data:
        delay = int(data["send_delay_seconds"])
        config["send_delay_seconds"] = max(20, min(90, delay))
    if "tracking_base_url" in data:
        config["tracking_base_url"] = data["tracking_base_url"].strip().rstrip("/")

    save_config(config)
    return jsonify({"message": "Settings saved successfully"})


# ─── Campaign Routes ─────────────────────────────────────────────────────────


@app.route("/api/campaigns", methods=["GET"])
def list_campaigns():
    campaigns = query_db("""
        SELECT c.*, 
               COUNT(r.id) as total_recipients,
               SUM(CASE WHEN r.status = 'sent' THEN 1 ELSE 0 END) as sent_count,
               SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END) as failed_count,
               SUM(CASE WHEN r.status = 'draft' AND r.subject IS NOT NULL THEN 1 ELSE 0 END) as draft_count,
               SUM(CASE WHEN COALESCE(r.open_count, 0) > 0 THEN 1 ELSE 0 END) as opened_count,
               (
                   SELECT COUNT(*) FROM followups f
                   JOIN recipients r2 ON f.recipient_id = r2.id
                   WHERE r2.campaign_id = c.id AND f.status = 'sent'
               ) as followups_sent_count
        FROM campaigns c
        LEFT JOIN recipients r ON r.campaign_id = c.id
        GROUP BY c.id
        ORDER BY c.created_at DESC
    """)
    return jsonify(campaigns)


@app.route("/api/campaign/extract-emails", methods=["POST"])
def extract_campaign_emails():
    """Extract emails from an uploaded PDF or XLSX file."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    filename = secure_filename(file.filename)
    if not filename.lower().endswith((".pdf", ".xlsx")):
        return jsonify({"error": "Only PDF and XLSX files are accepted"}), 400

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > 10 * 1024 * 1024:
        return jsonify({"error": "File too large. Maximum size is 10MB."}), 400

    filepath = os.path.join(UPLOAD_FOLDER, f"email_list_{int(time.time())}_{filename}")
    try:
        file.save(filepath)
        extracted_text = extract_email_text_from_file(filepath, filename)
        parsed = parse_email_list(extracted_text)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except OSError:
            pass

    if not parsed:
        return jsonify({"error": "No valid emails found in the uploaded file"}), 400

    email_list = "\n".join(
        f"{entry['name']} <{entry['email']}>" if entry.get("name") else entry["email"]
        for entry in parsed
    )
    return jsonify({
        "emails": parsed,
        "email_list": email_list,
        "count": len(parsed),
        "message": f"Extracted {len(parsed)} emails",
    })


@app.route("/api/campaign/<int:campaign_id>", methods=["GET"])
def get_campaign(campaign_id):
    campaign = query_db(
        "SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True
    )
    if not campaign:
        return jsonify({"error": "Campaign not found"}), 404

    recipients = query_db(
        "SELECT * FROM recipients WHERE campaign_id = ? ORDER BY id",
        (campaign_id,),
    )

    # Fetch follow-ups for each recipient
    for r in recipients:
        followups = query_db(
            "SELECT * FROM followups WHERE recipient_id = ? ORDER BY id",
            (r["id"],),
        )
        r["followups"] = followups

    campaign["recipients"] = recipients
    return jsonify(campaign)


@app.route("/api/campaign/new", methods=["POST"])
def create_campaign():
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    name = data.get("name", "").strip()
    goal = data.get("goal", "").strip()
    email_list_raw = data.get("email_list", "").strip()
    additional_context = data.get("additional_context", "").strip()
    send_limit_raw = data.get("send_limit")

    if not name:
        return jsonify({"error": "Campaign name is required"}), 400
    if not goal:
        return jsonify({"error": "Campaign goal is required"}), 400
    if not email_list_raw:
        return jsonify({"error": "Email list is required"}), 400

    # Check resume exists
    resume_path = os.path.join(UPLOAD_FOLDER, "resume.pdf")
    if not os.path.exists(resume_path):
        return jsonify({"error": "Please upload your resume in Profile before creating a campaign"}), 400

    # Parse emails
    parsed = parse_email_list(email_list_raw)
    if not parsed:
        return jsonify({"error": "No valid emails found in the list"}), 400

    source_count = len(parsed)
    send_limit = None
    if send_limit_raw not in (None, ""):
        try:
            send_limit = int(send_limit_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "Send limit must be a number"}), 400
        if send_limit < 1:
            return jsonify({"error": "Send limit must be at least 1"}), 400
        if send_limit < source_count:
            parsed = random.sample(parsed, send_limit)

    # Create campaign
    campaign_id = execute_db(
        "INSERT INTO campaigns (name, goal, additional_context) VALUES (?, ?, ?)",
        (name, goal, additional_context or None),
    )

    # Insert recipients as drafts
    conn = get_db()
    for entry in parsed:
        conn.execute(
            "INSERT INTO recipients (campaign_id, email, name, status) VALUES (?, ?, ?, 'draft')",
            (campaign_id, entry["email"], entry.get("name")),
        )
    conn.commit()
    conn.close()

    return jsonify({
        "campaign_id": campaign_id,
        "recipients_count": len(parsed),
        "source_count": source_count,
        "send_limit": send_limit,
        "message": f"Campaign created with {len(parsed)} recipients",
    })


@app.route("/api/campaign/<int:campaign_id>/generate", methods=["POST"])
def generate_campaign_emails(campaign_id):
    config = load_config()

    try:
        api_key = get_openai_key(config)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    campaign = query_db("SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True)
    if not campaign:
        return jsonify({"error": "Campaign not found"}), 404

    recipients = query_db(
        "SELECT * FROM recipients WHERE campaign_id = ? AND status = 'draft'",
        (campaign_id,)
    )
    if not recipients:
        return jsonify({"error": "No draft recipients to generate emails for"}), 400

    # Initialize progress
    generation_progress[campaign_id] = {
        "total": len(recipients),
        "completed": 0,
        "failed": 0,
        "status": "generating",
        "errors": []
    }

    profile = config.get("profile", {})
    resume_parsed = config.get("resume_parsed", {})

    # Start parallel generation in background thread
    thread = threading.Thread(
        target=_generate_emails_parallel,
        args=(campaign_id, recipients, campaign, profile, resume_parsed, api_key),
        daemon=True
    )
    thread.start()

    return jsonify({
        "message": "Generation started",
        "total": len(recipients)
    })

def _generate_emails_parallel(campaign_id, recipients, campaign, profile, resume_parsed, api_key):
    """
    Background function: generates emails for all recipients in parallel
    using ThreadPoolExecutor.
    """
    progress = generation_progress[campaign_id]

    num_workers = min(len(recipients), 10)

    def generate_one(r):
        """Generate email for a single recipient. Returns (recipient_id, result_or_error)."""
        try:
            result = generate_email(
                profile=profile,
                recipient={"email": r["email"], "name": r["name"]},
                campaign_goal=campaign["goal"],
                additional_context=campaign.get("additional_context", ""),
                api_key=api_key,
                resume_parsed=resume_parsed
            )
            return r["id"], r["email"], result, None
        except Exception as e:
            return r["id"], r["email"], None, str(e)
    
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(generate_one, r): r for r in recipients}
        
        conn = get_db()
        try:
            for future in as_completed(futures):
                recipient_id, email, result, error = future.result()
                
                if error:
                    progress["failed"] += 1
                    progress["errors"].append({"email": email, "error": error})
                    
                    if "quota" in error.lower():
                        progress["status"] = "error"
                        for f in futures:
                            f.cancel()
                        break
                else:
                    conn.execute(
                        "UPDATE recipients SET subject = ?, email_body = ? WHERE id = ?",
                        (result["subject"], result["body"], recipient_id)
                    )
                    conn.commit()
                    progress["completed"] += 1
        finally:
            conn.close()
    
    if progress["status"] != "error":
        progress["status"] = "complete"

@app.route("/api/campaign/<int:campaign_id>/generate-progress", methods=["GET"])
def get_generation_progress(campaign_id):
    """Poll endpoint for email generation progress."""
    progress = generation_progress.get(campaign_id)
    if not progress:
        return jsonify({
            "status": "idle",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "errors": []
        })
    return jsonify(progress)


@app.route("/api/campaign/<int:campaign_id>/preview", methods=["GET"])
def preview_campaign(campaign_id):
    """Get all recipients with their generated emails for preview."""
    campaign = query_db(
        "SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True
    )
    if not campaign:
        return jsonify({"error": "Campaign not found"}), 404

    recipients = query_db(
        "SELECT * FROM recipients WHERE campaign_id = ? ORDER BY id",
        (campaign_id,),
    )

    return jsonify({
        "campaign": campaign,
        "recipients": recipients,
    })


@app.route("/api/campaign/<int:campaign_id>/recipient/<int:recipient_id>", methods=["PUT"])
def update_recipient(campaign_id, recipient_id):
    """Update a recipient's email subject/body (manual edit)."""
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    conn = get_db()
    if "subject" in data:
        conn.execute(
            "UPDATE recipients SET subject = ? WHERE id = ? AND campaign_id = ?",
            (data["subject"], recipient_id, campaign_id),
        )
    if "email_body" in data:
        conn.execute(
            "UPDATE recipients SET email_body = ? WHERE id = ? AND campaign_id = ?",
            (data["email_body"], recipient_id, campaign_id),
        )
    conn.commit()
    conn.close()

    return jsonify({"message": "Recipient updated"})


@app.route("/api/recipient/<int:recipient_id>/status", methods=["PATCH"])
def update_recipient_status(recipient_id):
    data = request.json or {}
    reply_status = data.get("reply_status", "no_reply")
    reply_content = data.get("reply_content")
    check_back_date = data.get("check_back_date")
    exclude_followup = 1 if reply_status in ["invalid_email", "interview_scheduled", "final_rejection" ] else data.get("exclude_followup", 0)
    
    conn = get_db()
    conn.execute(
        "UPDATE recipients SET reply_status = ?, reply_content = ?, check_back_date = ?, exclude_followup = ?, status_updated_at = ? WHERE id = ?",
        (reply_status, reply_content, check_back_date, int(exclude_followup), datetime.utcnow().isoformat(), recipient_id)
    )
    conn.commit()
    conn.close()
    
    recipient = query_db("SELECT * FROM recipients WHERE id = ?", (recipient_id,), one=True)
    return jsonify({"message": "Status updated successfully", "recipient": recipient})

@app.route("/api/recipient/<int:recipient_id>", methods=["DELETE"])
def delete_recipient(recipient_id):
    conn = get_db()
    # Delete any follow-ups for this recipient first (FK safety)
    conn.execute("DELETE FROM followups WHERE recipient_id = ?", (recipient_id,))
    conn.execute("DELETE FROM recipients WHERE id = ?", (recipient_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Recipient deleted"})

@app.route("/api/campaign/<int:campaign_id>/recipients/status", methods=["PATCH"])
def bulk_update_recipient_statuses(campaign_id):
    data = request.json or {}
    updates = data.get("updates", [])
    conn = get_db()
    for update in updates:
        recipient_id = update["recipient_id"]
        reply_status = update["reply_status"]
        reply_content = update.get("reply_content")
        check_back_date = update.get("check_back_date")
        exclude_followup = 1 if reply_status in ["invalid_email", "interview_scheduled", "final_rejection"] else update.get("exclude_followup", 0)
        
        conn.execute(
            "UPDATE recipients SET reply_status = ?, reply_content = ?, check_back_date = ?, exclude_followup = ?, status_updated_at = ? WHERE id = ? AND campaign_id = ?",
            (reply_status, reply_content, check_back_date, int(exclude_followup), datetime.utcnow().isoformat(), recipient_id, campaign_id)
        )
    conn.commit()
    conn.close()
    return jsonify({"message": "Statuses updated successfully"})

@app.route("/api/campaign/<int:campaign_id>/followup-eligible", methods=["GET"])
def get_followup_eligible(campaign_id):
    recipients = query_db("""
        SELECT * FROM recipients 
        WHERE campaign_id = ? 
        AND status = 'sent' 
        AND follow_up_sent = 0 
        AND exclude_followup = 0 
        AND reply_status NOT IN ('invalid_email', 'interview_scheduled', 'final_rejection', 'interested')
    """, (campaign_id,))
    return jsonify(recipients)

@app.route("/api/campaign/<int:campaign_id>/recipient/<int:recipient_id>/regenerate", methods=["POST"])
def regenerate_recipient_email(campaign_id, recipient_id):
    """Regenerate AI email for a single recipient."""
    config = load_config()
    if not config.get("openai_api_key"):
        return jsonify({"error": "OpenAI API key not configured"}), 400

    campaign = query_db(
        "SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True
    )
    recipient = query_db(
        "SELECT * FROM recipients WHERE id = ? AND campaign_id = ?",
        (recipient_id, campaign_id),
        one=True,
    )
    if not campaign or not recipient:
        return jsonify({"error": "Campaign or recipient not found"}), 404

    try:
        result = generate_email(
            profile=config.get("profile", {}),
            recipient={"email": recipient["email"], "name": recipient["name"]},
            campaign_goal=campaign["goal"],
            additional_context=campaign.get("additional_context", ""),
            api_key=config["openai_api_key"],
            resume_parsed=config.get("resume_parsed", {})
        )

        conn = get_db()
        conn.execute(
            "UPDATE recipients SET subject = ?, email_body = ? WHERE id = ?",
            (result["subject"], result["body"], recipient_id),
        )
        conn.commit()
        conn.close()

        return jsonify({
            "subject": result["subject"],
            "body": result["body"],
            "message": "Email regenerated",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/campaign/<int:campaign_id>/send", methods=["POST"])
def send_campaign(campaign_id):
    """Start sending emails in a background thread."""
    config = load_config()

    if not is_settings_complete(config):
        return jsonify({"error": "Gmail and Gemini credentials not configured. Go to Settings."}), 400

    resume_path = os.path.join(UPLOAD_FOLDER, "resume.pdf")
    if not os.path.exists(resume_path):
        return jsonify({"error": "Resume PDF not found. Upload it in Profile."}), 400

    recipients = query_db("""
        SELECT * FROM recipients
        WHERE campaign_id = ?
          AND status = 'draft'
          AND subject IS NOT NULL
          AND LOWER(SUBSTR(email, INSTR(email, '@') + 1)) NOT IN (
              SELECT domain FROM blocked_domains
          )
    """, (campaign_id,))
    if not recipients:
        return jsonify({"error": "No emails ready to send"}), 400

    # Initialize progress tracking
    send_progress[campaign_id] = {
        "current": 0,
        "total": len(recipients),
        "status": "sending",
        "log": [],
        "current_email": "",
    }

    # Start background thread
    thread = threading.Thread(
        target=_send_campaign_thread,
        args=(campaign_id, recipients, config, resume_path),
        daemon=True,
    )
    thread.start()

    return jsonify({"message": "Sending started", "total": len(recipients)})


def _send_campaign_thread(campaign_id, recipients, config, resume_path):
    """Background thread that sends emails one by one with delay."""
    delay = config.get("send_delay_seconds", 60)
    progress = send_progress[campaign_id]

    for i, r in enumerate(recipients):
        progress["current"] = i + 1
        progress["current_email"] = r["email"]

        tracking_id = uuid.uuid4().hex
        pixel_url = build_tracking_pixel_url(config, tracking_id)

        conn = get_db()
        try:
            message_id = send_email(
                sender_email=config["gmail_address"],
                sender_name=config["profile"]["name"],
                app_password=config["gmail_app_password"],
                recipient_email=r["email"],
                subject=r["subject"],
                body=r["email_body"],
                resume_path=resume_path,
                tracking_pixel_url=pixel_url,
            )
            conn.execute(
                "UPDATE recipients SET status = 'sent', sent_at = ?, message_id = ?, tracking_id = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), message_id, tracking_id if pixel_url else None, r["id"]),
            )
            progress["log"].append({
                "email": r["email"],
                "status": "sent",
                "time": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            conn.execute(
                "UPDATE recipients SET status = 'failed', error_message = ? WHERE id = ?",
                (str(e), r["id"]),
            )
            progress["log"].append({
                "email": r["email"],
                "status": "failed",
                "error": str(e),
                "time": datetime.utcnow().isoformat(),
            })
        finally:
            conn.commit()
            conn.close()

        # Wait between emails (skip delay after last one)
        if i < len(recipients) - 1:
            time.sleep(delay)

    progress["status"] = "complete"
    progress["current_email"] = ""


@app.route("/api/campaign/<int:campaign_id>/progress", methods=["GET"])
def get_send_progress(campaign_id):
    """Poll endpoint for send progress."""
    progress = send_progress.get(campaign_id)
    if not progress:
        return jsonify({"status": "idle", "current": 0, "total": 0, "log": []})
    return jsonify(progress)


@app.route("/api/campaign/<int:campaign_id>", methods=["DELETE"])
def delete_campaign(campaign_id):
    """Delete/discard a campaign and its recipients."""
    conn = get_db()
    # Delete followups first (FK constraint)
    conn.execute("""
        DELETE FROM followups WHERE recipient_id IN 
        (SELECT id FROM recipients WHERE campaign_id = ?)
    """, (campaign_id,))
    conn.execute("DELETE FROM recipients WHERE campaign_id = ?", (campaign_id,))
    conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Campaign deleted"})


# ─── Follow-up Routes ─────────────────────────────────────────────────────────


@app.route("/api/campaign/<int:campaign_id>/generate-followups", methods=["POST"])
def generate_followups(campaign_id):
    config = load_config()

    try:
        api_key = get_openai_key(config)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    data = request.json or {}
    global_context = data.get("global_context", "")
    recipient_ids = data.get("recipient_ids")

    base_query = """
        SELECT * FROM recipients 
        WHERE campaign_id = ? AND status = 'sent' AND follow_up_sent = 0
        AND exclude_followup = 0 
        AND reply_status NOT IN ('invalid_email', 'interview_scheduled', 'final_rejection', 'interested')
    """
    
    recipients = query_db(base_query, (campaign_id,))
    if recipient_ids:
        recipients = [r for r in recipients if r["id"] in recipient_ids]

    if not recipients:
        return jsonify({"error": "No recipients eligible for follow-up"}), 400

    progress_key = f"{campaign_id}_followup"
    generation_progress[progress_key] = {
        "total": len(recipients),
        "completed": 0,
        "failed": 0,
        "status": "generating",
        "errors": []
    }

    profile = config.get("profile", {})
    resume_parsed = config.get("resume_parsed", {})

    thread = threading.Thread(
        target=_generate_followups_parallel,
        args=(campaign_id, recipients, profile, resume_parsed, api_key, global_context, progress_key),
        daemon=True
    )
    thread.start()

    return jsonify({
        "message": "Follow-up generation started",
        "total": len(recipients)
    })

def _generate_followups_parallel(campaign_id, recipients, profile, resume_parsed, api_key, global_context, progress_key):
    progress = generation_progress[progress_key]
    num_workers = min(len(recipients), 10)

    def generate_one(r):
        try:
            result = generate_contextual_followup(
                profile=profile,
                original_subject=r["subject"],
                original_body=r["email_body"],
                recipient_email=r["email"],
                recipient_name=r["name"],
                reply_status=r["reply_status"] or "no_reply",
                reply_content=r["reply_content"],
                check_back_date=r["check_back_date"],
                global_context=global_context,
                api_key=api_key,
                resume_parsed=resume_parsed
            )
            return r["id"], r["email"], result, None
        except Exception as e:
            return r["id"], r["email"], None, str(e)

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(generate_one, r): r for r in recipients}
        
        conn = get_db()
        try:
            for future in as_completed(futures):
                recipient_id, email, result, error = future.result()
                
                if error:
                    progress["failed"] += 1
                    progress["errors"].append({"email": email, "error": error})
                    
                    if "quota" in error.lower():
                        progress["status"] = "error"
                        for f in futures:
                            f.cancel()
                        break
                else:
                    conn.execute(
                        "INSERT INTO followups (recipient_id, subject, email_body, status) VALUES (?, ?, ?, 'draft')",
                        (recipient_id, result["subject"], result["body"])
                    )
                    conn.commit()
                    progress["completed"] += 1
        finally:
            conn.close()
    
    if progress["status"] != "error":
        progress["status"] = "complete"

@app.route("/api/campaign/<int:campaign_id>/generate-followup-progress", methods=["GET"])
def get_followup_generate_progress(campaign_id):
    progress = generation_progress.get(f"{campaign_id}_followup")
    if not progress:
        return jsonify({
            "status": "idle",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "errors": []
        })
    return jsonify(progress)


@app.route("/api/campaign/<int:campaign_id>/followup/preview", methods=["GET"])
def preview_followups(campaign_id):
    """Get follow-up drafts for preview."""
    followups = query_db("""
        SELECT f.*, r.email as recipient_email, r.name as recipient_name, r.message_id as original_message_id, r.reply_status
        FROM followups f
        JOIN recipients r ON r.id = f.recipient_id
        WHERE r.campaign_id = ? AND f.status = 'draft'
        ORDER BY f.id
    """, (campaign_id,))

    return jsonify({"followups": followups, "campaign_id": campaign_id})


@app.route("/api/followup/<int:followup_id>", methods=["PUT"])
def update_followup(followup_id):
    """Edit a follow-up email."""
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400

    conn = get_db()
    if "subject" in data:
        conn.execute("UPDATE followups SET subject = ? WHERE id = ?", (data["subject"], followup_id))
    if "email_body" in data:
        conn.execute("UPDATE followups SET email_body = ? WHERE id = ?", (data["email_body"], followup_id))
    conn.commit()
    conn.close()

    return jsonify({"message": "Follow-up updated"})

@app.route("/api/followup/<int:followup_id>", methods=["DELETE"])
def delete_followup(followup_id):
    conn = get_db()
    conn.execute("DELETE FROM followups WHERE id = ?", (followup_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Follow-up deleted"})


# ─── Blocked Domains Routes ───────────────────────────────────────────────────


@app.route("/api/blocked-domains", methods=["GET"])
def get_blocked_domains():
    domains = query_db("SELECT * FROM blocked_domains ORDER BY blocked_at DESC")
    return jsonify(domains)


@app.route("/api/blocked-domains", methods=["POST"])
def block_domain():
    data = request.json or {}
    domain = data.get("domain", "").strip().lower()
    reason = data.get("reason", "")
    if not domain:
        return jsonify({"error": "Domain required"}), 400
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO blocked_domains (domain, reason, blocked_at) VALUES (?, ?, ?)",
            (domain, reason, datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
        return jsonify({"message": f"{domain} blocked"})
    except Exception:
        return jsonify({"error": "Domain already blocked"}), 409


@app.route("/api/blocked-domains/<string:domain>", methods=["DELETE"])
def unblock_domain(domain):
    conn = get_db()
    conn.execute("DELETE FROM blocked_domains WHERE domain = ?", (domain.lower(),))
    conn.commit()
    conn.close()
    return jsonify({"message": f"{domain} unblocked"})



@app.route("/api/campaign/<int:campaign_id>/followup/send", methods=["POST"])
def send_followups(campaign_id):
    """Start sending follow-up emails in a background thread."""
    config = load_config()

    if not is_settings_complete(config):
        return jsonify({"error": "Credentials not configured"}), 400

    resume_path = os.path.join(UPLOAD_FOLDER, "resume.pdf")
    if not os.path.exists(resume_path):
        return jsonify({"error": "Resume PDF not found"}), 400

    followups = query_db("""
        SELECT f.*, r.email as recipient_email, r.name as recipient_name,
               r.message_id as original_message_id, r.id as rid
        FROM followups f
        JOIN recipients r ON r.id = f.recipient_id
        WHERE r.campaign_id = ? AND f.status = 'draft'
          AND LOWER(SUBSTR(r.email, INSTR(r.email, '@') + 1)) NOT IN (
              SELECT domain FROM blocked_domains
          )
        ORDER BY f.id
    """, (campaign_id,))

    if not followups:
        return jsonify({"error": "No follow-ups ready to send"}), 400

    # Use a separate progress key for follow-ups
    progress_key = f"{campaign_id}_followup"
    send_progress[progress_key] = {
        "current": 0,
        "total": len(followups),
        "status": "sending",
        "log": [],
        "current_email": "",
    }

    thread = threading.Thread(
        target=_send_followup_thread,
        args=(campaign_id, followups, config, resume_path, progress_key),
        daemon=True,
    )
    thread.start()

    return jsonify({"message": "Follow-up sending started", "total": len(followups), "progress_key": progress_key})


def _send_followup_thread(campaign_id, followups, config, resume_path, progress_key):
    """Background thread that sends follow-up emails."""
    delay = config.get("send_delay_seconds", 60)
    progress = send_progress[progress_key]

    for i, f in enumerate(followups):
        progress["current"] = i + 1
        progress["current_email"] = f["recipient_email"]

        tracking_id = uuid.uuid4().hex
        pixel_url = build_tracking_pixel_url(config, tracking_id)

        conn = get_db()
        try:
            message_id = send_email(
                sender_email=config["gmail_address"],
                sender_name=config["profile"]["name"],
                app_password=config["gmail_app_password"],
                recipient_email=f["recipient_email"],
                subject=f["subject"],
                body=f["email_body"],
                resume_path=resume_path,
                reply_to_message_id=f["original_message_id"],
                tracking_pixel_url=pixel_url,
            )
            conn.execute(
                "UPDATE followups SET status = 'sent', sent_at = ?, tracking_id = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), tracking_id if pixel_url else None, f["id"]),
            )
            conn.execute(
                "UPDATE recipients SET follow_up_sent = 1 WHERE id = ?",
                (f["rid"],),
            )
            progress["log"].append({
                "email": f["recipient_email"],
                "status": "sent",
                "time": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            conn.execute(
                "UPDATE followups SET status = 'failed', error_message = ? WHERE id = ?",
                (str(e), f["id"]),
            )
            progress["log"].append({
                "email": f["recipient_email"],
                "status": "failed",
                "error": str(e),
                "time": datetime.utcnow().isoformat(),
            })
        finally:
            conn.commit()
            conn.close()

        if i < len(followups) - 1:
            time.sleep(delay)

    progress["status"] = "complete"
    progress["current_email"] = ""


@app.route("/api/campaign/<int:campaign_id>/followup/progress", methods=["GET"])
def get_followup_send_progress(campaign_id):
    """Poll endpoint for follow-up send progress."""
    progress_key = f"{campaign_id}_followup"
    progress = send_progress.get(progress_key)
    if not progress:
        return jsonify({"status": "idle", "current": 0, "total": 0, "log": []})
    return jsonify(progress)


# ─── Dashboard Route ──────────────────────────────────────────────────────────


@app.route("/api/dashboard/reply-stats", methods=["GET"])
def reply_stats():
    res = query_db("""
        SELECT reply_status, COUNT(*) as count 
        FROM recipients 
        WHERE status = 'sent' 
        GROUP BY reply_status
    """)
    
    stats_dict = {
        "interested": 0,
        "check_back": 0,
        "no_reply": 0,
        "invalid_email": 0
    }
    
    for row in res:
        key = row["reply_status"] or "no_reply"
        if key in stats_dict:
            stats_dict[key] += row["count"]
            
    return jsonify(stats_dict)

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    """Get quick stats for the dashboard."""
    stats = query_db("""
        SELECT 
            (SELECT COUNT(*) FROM campaigns) as total_campaigns,
            (SELECT COUNT(*) FROM recipients WHERE status = 'sent') as total_sent,
            (SELECT COUNT(*) FROM recipients WHERE status = 'failed') as total_failed,
            (SELECT COUNT(*) FROM followups WHERE status = 'sent') as total_followups_sent
    """, one=True)

    recent = query_db("""
        SELECT c.id, c.name, c.created_at, COUNT(r.id) as recipient_count
        FROM campaigns c
        LEFT JOIN recipients r ON r.campaign_id = c.id
        GROUP BY c.id
        ORDER BY c.created_at DESC
        LIMIT 5
    """)

    return jsonify({"stats": stats, "recent_campaigns": recent})


# ─── CRM Search Routes ──────────────────────────────────────────────────────────

import urllib.parse

@app.route("/api/search", methods=["GET"])
def search_global():
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"results": [], "total": 0, "page": 1, "limit": 20, "pages": 0})
        
    status = request.args.get("status")
    days = request.args.get("days")
    campaign_id = request.args.get("campaign_id")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))
    
    q = f"%{query.lower()}%"
    offset = (page - 1) * limit

    base_sql = """
        SELECT
            r.id,
            r.email,
            r.name,
            c.name as campaign_name,
            r.reply_status,
            r.reply_content,
            r.check_back_date,
            r.follow_up_sent,
            r.sent_at,
            r.subject,
            r.email_body,
            r.status as send_status,
            r.error_message,
            c.id as campaign_id,
            c.goal as campaign_goal,
            c.created_at as campaign_created_at
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE (
            LOWER(r.email) LIKE ?
            OR LOWER(r.name) LIKE ?
            OR LOWER(SUBSTR(r.email, INSTR(r.email, '@') + 1)) LIKE ?
        )
    """

    params = [q, q, q]

    if status and status != "all":
        base_sql += " AND r.reply_status = ?"
        params.append(status)

    if days:
        base_sql += " AND r.sent_at >= datetime('now', ?)"
        params.append(f"-{days} days")

    if campaign_id:
        base_sql += " AND r.campaign_id = ?"
        params.append(campaign_id)

    count_sql = f"SELECT COUNT(*) as cnt FROM ({base_sql})"
    count_res = query_db(count_sql, params, one=True)
    total_count = count_res["cnt"] if count_res else 0

    base_sql += " ORDER BY r.sent_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    results = query_db(base_sql, params)

    return jsonify({
        "results": results,
        "total": total_count,
        "page": page,
        "limit": limit,
        "pages": (total_count + limit - 1) // limit
    })


@app.route("/api/contact/<path:email>", methods=["GET"])
def contact_history(email):
    email = urllib.parse.unquote(email)
    domain = email.split("@")[-1] if "@" in email else ""
    
    history = query_db("""
        SELECT r.*, c.name as campaign_name 
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE r.email = ?
        ORDER BY r.sent_at DESC
    """, (email,))
    
    for h in history:
        f = query_db("SELECT * FROM followups WHERE recipient_id = ? ORDER BY sent_at DESC LIMIT 1", (h["id"],), one=True)
        if f:
            h["follow_up_body"] = f["email_body"]
            h["follow_up_sent_at"] = f["sent_at"]
        else:
            h["follow_up_body"] = None
            h["follow_up_sent_at"] = None

    same_domain = query_db("""
        SELECT r.email, c.name as campaign_name, r.sent_at, r.reply_status
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE SUBSTR(r.email, INSTR(r.email, '@') + 1) = ? AND r.email != ?
        ORDER BY r.sent_at DESC
    """, (domain, email))
    
    return jsonify({
        "email": email,
        "resolved_name": history[0]["name"] if history and history[0].get("name") else None,
        "domain": domain,
        "company": domain.split(".")[0].capitalize() if domain else None,
        "total_contacts": len(history),
        "history": history,
        "same_domain_contacts": same_domain
    })

@app.route("/api/campaign/check-duplicates", methods=["POST"])
def check_duplicates():
    data = request.json or {}
    emails = data.get("emails", [])
    
    if not emails:
        return jsonify({"duplicates": [], "new_emails": [], "duplicate_count": 0, "new_count": 0})
        
    placeholders = ",".join("?" for _ in emails)
    
    duplicates_res = query_db(f"""
        SELECT r.email, MAX(r.sent_at) as last_contacted, c.name as campaign_name, c.id as campaign_id, r.reply_status, COUNT(*) as times_contacted
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE r.email IN ({placeholders})
        GROUP BY r.email
    """, emails)
    
    dup_emails = {d["email"] for d in duplicates_res}
    new_emails = [e for e in emails if e not in dup_emails]
    
    return jsonify({
        "duplicates": duplicates_res,
        "new_emails": new_emails,
        "duplicate_count": len(duplicates_res),
        "new_count": len(new_emails)
    })

@app.route("/api/reengagement", methods=["GET"])
def reengagement_candidates():
    min_days = int(request.args.get("min_days", 14))
    max_days = int(request.args.get("max_days", 60))
    status_arg = request.args.get("status", "no_reply,check_back")
    statuses = [s.strip() for s in status_arg.split(",")]
    
    placeholders = ",".join("?" for _ in statuses)
    
    query = f"""
        SELECT r.email, r.name, r.sent_at, r.reply_status, c.name as campaign_name, c.id as campaign_id
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE r.sent_at <= datetime('now', ?)
        AND r.sent_at >= datetime('now', ?)
        AND r.reply_status IN ({placeholders})
        AND r.follow_up_sent = 0
        AND r.exclude_followup = 0
        ORDER BY r.sent_at DESC
    """
    
    params = [f"-{min_days} days", f"-{max_days} days"] + statuses
    candidates = query_db(query, params)
    
    return jsonify({"candidates": candidates})


# ─── Email Open Tracking ──────────────────────────────────────────────────────


@app.route("/track/open/<string:tracking_id>", methods=["GET"])
def track_open(tracking_id):
    """
    Invisible 1x1 pixel endpoint. Mail clients fetch this image when the
    recipient opens the email — we count each fetch as an open.
    """
    # Only accept well-formed ids (uuid4().hex = 32 hex chars)
    if len(tracking_id) == 32 and all(c in "0123456789abcdef" for c in tracking_id):
        now = datetime.utcnow().isoformat()
        try:
            conn = get_db()
            cursor = conn.execute(
                """UPDATE recipients
                   SET open_count = COALESCE(open_count, 0) + 1,
                       first_opened_at = COALESCE(first_opened_at, ?),
                       last_opened_at = ?
                   WHERE tracking_id = ?""",
                (now, now, tracking_id),
            )
            if cursor.rowcount == 0:
                conn.execute(
                    """UPDATE followups
                       SET open_count = COALESCE(open_count, 0) + 1,
                           first_opened_at = COALESCE(first_opened_at, ?),
                           last_opened_at = ?
                       WHERE tracking_id = ?""",
                    (now, now, tracking_id),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Tracking] Failed to record open for {tracking_id}: {e}")

    response = Response(TRACKING_PIXEL_GIF, mimetype="image/gif")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/api/dashboard/open-stats", methods=["GET"])
def open_stats():
    """Aggregate open-tracking stats for the dashboard."""
    stats = query_db("""
        SELECT
            (SELECT COUNT(*) FROM recipients WHERE status = 'sent' AND tracking_id IS NOT NULL) as tracked_sent,
            (SELECT COUNT(*) FROM recipients WHERE COALESCE(open_count, 0) > 0) as unique_opens,
            (SELECT COALESCE(SUM(open_count), 0) FROM recipients) as total_opens,
            (SELECT COUNT(*) FROM followups WHERE COALESCE(open_count, 0) > 0) as followup_unique_opens
    """, one=True)

    tracked = stats["tracked_sent"] or 0
    unique = stats["unique_opens"] or 0
    stats["open_rate"] = round((unique / tracked) * 100, 1) if tracked > 0 else 0

    recent_opens = query_db("""
        SELECT r.email, r.name, r.open_count, r.first_opened_at, r.last_opened_at,
               c.name as campaign_name, c.id as campaign_id
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE COALESCE(r.open_count, 0) > 0
        ORDER BY r.last_opened_at DESC
        LIMIT 10
    """)

    stats["recent_opens"] = recent_opens
    return jsonify(stats)


# ─── Inbox Monitor Routes ─────────────────────────────────────────────────────

@app.route("/api/inbox/check", methods=["POST"])
def manual_inbox_check():
    """Manually trigger inbox monitoring — same as scheduled job."""
    global _last_monitor_result
    if not _monitor_lock.acquire(blocking=False):
        return jsonify({
            "error": "Monitor is already running. Please wait and try again.",
            "busy": True,
            "last_result": _last_monitor_result
        }), 200

    from config import load_config
    from db import get_db
    config = load_config()
    conn = None

    try:
        conn = get_db()
        result = run_inbox_monitor(config, conn)
        result["triggered_by"] = "manual"
        _last_monitor_result = result
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()
        _monitor_lock.release()

@app.route("/api/inbox/status", methods=["GET"])
def inbox_status():
    return jsonify(_last_monitor_result if _last_monitor_result else {
        "last_run": None,
        "bounces_detected": 0,
        "ooo_detected": 0,
        "updated": 0,
        "message": "Monitor has not run yet since app start."
    })

@app.route("/api/campaign/<int:campaign_id>/bounces", methods=["GET"])
def campaign_bounces(campaign_id):
    from db import get_db
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, email, name, reply_status,
               error_message, status_updated_at
        FROM recipients
        WHERE campaign_id = ?
        AND reply_status = 'invalid_email'
        AND (error_message LIKE '%bounce%' OR error_message LIKE '%does not exist%')
    """, (campaign_id,))
    rows = cursor.fetchall()
    columns = [d[0] for d in cursor.description]
    conn.close()
    return jsonify([dict(zip(columns, row)) for row in rows])


# ─── App Startup ──────────────────────────────────────────────────────────────


init_db()
load_config()  # Ensure config.json exists


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", debug=debug, use_reloader=False, port=port)
