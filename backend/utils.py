"""
Utility functions for the outreach tool.
Includes robust email list parsing.
"""

import re
import json
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from openai import OpenAI

try:
    import requests
except ImportError:
    requests = None

_company_research_cache = {}

# Free/personal mail providers — no company website to research behind these
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in",
    "outlook.com", "hotmail.com", "live.com", "msn.com", "icloud.com",
    "aol.com", "protonmail.com", "proton.me", "zoho.com", "zohomail.in",
    "rediffmail.com", "mail.com", "gmx.com", "ymail.com",
}

# Generic mailbox prefixes that are never a person's first name
GENERIC_LOCAL_PARTS = {
    "info", "hr", "hello", "contact", "contactus", "admin", "support",
    "team", "careers", "career", "jobs", "job", "hiring", "talent",
    "recruitment", "recruiter", "recruiting", "recruit", "sales", "office",
    "mail", "email", "enquiry", "enquiries", "inquiry", "help", "helpdesk",
    "noreply", "no-reply", "reply", "connect", "work", "apply", "business",
    "marketing", "media", "press", "tech", "dev", "developer", "engineering",
    "founders", "founder", "ceo", "cto", "director", "md", "feedback",
    "query", "queries", "reach", "reachus", "web", "webmaster", "service",
    "services", "account", "accounts", "billing", "finance", "ops",
    "operations", "people", "peopleops", "staffing", "placement",
    "placements", "resume", "resumes", "cv", "internship", "interns",
}


def extract_first_name_from_email(email: str):
    """
    Try to extract a person's first name from the local part of an email.
    e.g. "rahul.sharma@acme.com" -> "Rahul", "priya_v@x.io" -> "Priya",
    "hr@acme.com" -> None, "info@x.com" -> None.
    Returns a capitalized first name, or None if no confident match.
    """
    if not email or "@" not in email:
        return None

    local = email.split("@")[0].lower().strip()

    # Whole local part is a generic mailbox — no name here
    if local.replace("-", "").replace("_", "").replace(".", "") in GENERIC_LOCAL_PARTS:
        return None

    # Split on common separators and digits: rahul.sharma / rahul_sharma / rahul-sharma / rahul123
    tokens = re.split(r"[._\-+\d]+", local)

    for token in tokens:
        if not token:
            continue
        if token in GENERIC_LOCAL_PARTS:
            continue
        # Must be purely alphabetic and long enough to plausibly be a name
        if token.isalpha() and 3 <= len(token) <= 15:
            return token.capitalize()
        # Stop at the first non-name-looking token only if it's clearly junk;
        # otherwise keep scanning (e.g. "s.rahul" -> skip "s", pick "rahul")
        continue

    return None


class _WebsiteTextParser(HTMLParser):
    """Extracts title, meta description, and visible text from an HTML page."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.text_parts = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            attrs_dict = dict(attrs)
            name = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            if name in ("description", "og:description") and not self.meta_description:
                self.meta_description = (attrs_dict.get("content") or "").strip()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title += text
        else:
            self.text_parts.append(text)


def _fetch_website_text(domain: str, max_chars: int = 3000):
    """
    Fetch the company homepage and return a text blob (title + meta description
    + visible text), or None if the site can't be reached.
    """
    if requests is None:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }

    for url in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
        try:
            resp = requests.get(url, headers=headers, timeout=6, allow_redirects=True)
            if resp.status_code >= 400:
                continue
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                continue

            parser = _WebsiteTextParser()
            try:
                parser.feed(resp.text[:200_000])
            except Exception:
                pass

            parts = []
            if parser.title:
                parts.append(f"PAGE TITLE: {parser.title}")
            if parser.meta_description:
                parts.append(f"META DESCRIPTION: {parser.meta_description}")
            visible = " ".join(parser.text_parts)
            if visible:
                parts.append(f"PAGE TEXT: {visible}")

            blob = "\n".join(parts).strip()
            if blob:
                return blob[:max_chars]
        except Exception:
            continue

    return None


def research_company(domain: str, openai_api_key: str) -> dict:
    """
    Research a company from its email domain:
    1. Fetch its website homepage (title + meta description + visible text)
    2. Ask OpenAI to produce the official company name + a short summary of
       what the company actually does (falls back to model knowledge if the
       site is unreachable).

    Returns: {"company_name": str, "summary": str|None, "industry": str|None}
    Results are cached in-memory per domain.
    """
    domain_clean = domain.lower().strip()

    if domain_clean in _company_research_cache:
        return _company_research_cache[domain_clean]

    fallback_name = domain_clean.split(".")[0].replace("-", " ").replace("_", " ").title()

    if domain_clean in FREEMAIL_DOMAINS:
        result = {"company_name": fallback_name, "summary": None, "industry": None}
        _company_research_cache[domain_clean] = result
        return result

    website_text = _fetch_website_text(domain_clean)

    if website_text:
        source_block = (
            f"Here is text scraped from their website homepage ({domain_clean}):\n"
            f"\"\"\"\n{website_text}\n\"\"\""
        )
    else:
        source_block = (
            f"Their website could not be fetched. Use your training knowledge of the "
            f"domain {domain_clean}. If you don't recognize the company, intelligently "
            f"parse the domain name (e.g. 'nisargaits.com' → 'Nisarga IT Solutions') "
            f"and set summary to null rather than inventing facts."
        )

    result = {"company_name": fallback_name, "summary": None, "industry": None}

    try:
        client = OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a company research assistant. Given a website domain and "
                        "(when available) scraped homepage text, return:\n"
                        "1. company_name — the official company/organization name\n"
                        "2. summary — 2-3 factual sentences on what the company actually does "
                        "(products, services, industry, who they serve). Base this ONLY on the "
                        "provided website text or solid training knowledge. If you genuinely "
                        "don't know what they do, set summary to null. NEVER invent facts.\n"
                        "3. industry — a 2-4 word industry label (e.g. 'IT services', "
                        "'fintech', 'healthcare SaaS'), or null if unknown.\n"
                        "Return ONLY valid JSON: {\"company_name\": \"...\", \"summary\": "
                        "\"...\" or null, \"industry\": \"...\" or null}"
                    )
                },
                {
                    "role": "user",
                    "content": f"Domain: {domain_clean}\n\n{source_block}"
                }
            ],
            temperature=0.2,
            response_format={"type": "json_object"}
        )

        parsed = json.loads(response.choices[0].message.content)
        name = (parsed.get("company_name") or "").strip()
        if name and len(name) <= 60:
            result["company_name"] = name
        summary = parsed.get("summary")
        if isinstance(summary, str) and summary.strip().lower() not in ("", "null", "none", "unknown"):
            result["summary"] = summary.strip()
        industry = parsed.get("industry")
        if isinstance(industry, str) and industry.strip():
            result["industry"] = industry.strip()

    except Exception as e:
        print(f"Company research failed for {domain_clean}: {e}")

    _company_research_cache[domain_clean] = result
    return result


def resolve_company_name(domain: str, openai_api_key: str) -> str:
    """Resolve the company name for a domain (backed by research_company cache)."""
    return research_company(domain, openai_api_key)["company_name"]

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Pattern: Name <email>
ANGLE_BRACKET_PATTERN = re.compile(
    r"(.+?)\s*<\s*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\s*>"
)

# Pattern: Name - email
DASH_PATTERN = re.compile(
    r"(.+?)\s*-\s*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})"
)


def parse_email_list(raw_text):
    """
    Parse a raw text block containing emails in various formats.
    
    Supported formats:
    - One per line
    - Comma-separated
    - Space-separated
    - Name <email>
    - Name - email
    - Mixed formats
    
    Returns a deduplicated list of {"email": str, "name": str|None} dicts.
    """
    if not raw_text or not raw_text.strip():
        return []

    results = []
    seen_emails = set()

    # Split on newlines first to process line by line
    lines = raw_text.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Try angle bracket pattern: Name <email>
        angle_matches = ANGLE_BRACKET_PATTERN.findall(line)
        if angle_matches:
            for name, email in angle_matches:
                _add_entry(results, seen_emails, email.strip(), name.strip())
            continue

        # Try dash pattern: Name - email
        dash_matches = DASH_PATTERN.findall(line)
        if dash_matches:
            for name, email in dash_matches:
                _add_entry(results, seen_emails, email.strip(), name.strip())
            continue

        # Split on commas and spaces, then extract bare emails
        # Split on commas first
        segments = line.split(",")
        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue

            # Find all emails in this segment
            emails_found = EMAIL_REGEX.findall(segment)
            for email in emails_found:
                _add_entry(results, seen_emails, email, None)

    return results


def _add_entry(results, seen_emails, email, name):
    """Add an email entry if not already seen (case-insensitive dedup)."""
    email_lower = email.lower()
    if email_lower in seen_emails:
        return
    seen_emails.add(email_lower)

    # Clean up the name
    if name:
        name = name.strip().strip('"').strip("'").strip()
        if not name:
            name = None

    results.append({"email": email, "name": name})


def extract_email_text_from_file(filepath, filename):
    """
    Extract plain text from supported email-list upload files.
    Supports PDF and XLSX files.
    """
    lower_name = filename.lower()
    if lower_name.endswith(".pdf"):
        return _extract_text_from_pdf(filepath)
    if lower_name.endswith(".xlsx"):
        return _extract_text_from_xlsx(filepath)
    raise ValueError("Only PDF and XLSX files are supported")


def _extract_text_from_pdf(filepath):
    try:
        import pdfplumber
    except ImportError as exc:
        raise ValueError("PDF parsing is not available on this server") from exc

    text_parts = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text:
                text_parts.append(text)
    return "\n".join(text_parts)


def _extract_text_from_xlsx(filepath):
    ns = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }

    with zipfile.ZipFile(filepath) as archive:
        shared_strings = _read_xlsx_shared_strings(archive, ns)
        sheet_paths = _read_xlsx_sheet_paths(archive, ns)
        cell_values = []

        for sheet_path in sheet_paths:
            if sheet_path not in archive.namelist():
                continue
            root = ET.fromstring(archive.read(sheet_path))
            for cell in root.findall(".//main:c", ns):
                value = _read_xlsx_cell_value(cell, shared_strings, ns)
                if value:
                    cell_values.append(value)

    return "\n".join(cell_values)


def _read_xlsx_shared_strings(archive, ns):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []

    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall("main:si", ns):
        parts = [node.text or "" for node in item.findall(".//main:t", ns)]
        strings.append("".join(parts))
    return strings


def _read_xlsx_sheet_paths(archive, ns):
    workbook_path = "xl/workbook.xml"
    rels_path = "xl/_rels/workbook.xml.rels"
    if workbook_path not in archive.namelist() or rels_path not in archive.namelist():
        return [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet")]

    rel_root = ET.fromstring(archive.read(rels_path))
    rel_targets = {}
    for rel in rel_root.findall("rel:Relationship", ns):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target", "")
        if rel_id and target:
            target = target.lstrip("/")
            rel_targets[rel_id] = target if target.startswith("xl/") else f"xl/{target}"

    workbook_root = ET.fromstring(archive.read(workbook_path))
    paths = []
    for sheet in workbook_root.findall(".//main:sheet", ns):
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        path = rel_targets.get(rel_id)
        if path:
            paths.append(path)
    return paths


def _read_xlsx_cell_value(cell, shared_strings, ns):
    cell_type = cell.attrib.get("t")

    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//main:t", ns)).strip()

    value_node = cell.find("main:v", ns)
    if value_node is None or value_node.text is None:
        return ""

    value = value_node.text.strip()
    if cell_type == "s":
        try:
            return shared_strings[int(value)].strip()
        except (ValueError, IndexError):
            return ""
    return value
