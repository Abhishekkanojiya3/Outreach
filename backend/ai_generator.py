"""
OpenAI API email generation logic.
Constructs prompts per the spec and returns parsed JSON with subject + body.
Incorporates resume/work highlights for personalization.

Tuned for an EXPERIENCED PROFESSIONAL (2+ years) reaching out for jobs / freelance /
collaboration opportunities — not a student internship pitch.
"""

import json
import random
import time
from openai import OpenAI, RateLimitError, APIStatusError
from utils import resolve_company_name, research_company, extract_first_name_from_email

OPENAI_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You are helping an experienced software professional write personalised cold outreach emails for job, freelance, or collaboration opportunities.
These emails will be sent directly from the candidate's Gmail. They must feel like real, human-written emails from a capable, busy professional — not templates, not cover letters, not LinkedIn InMail.

WHO'S READING THIS: A recruiter or hiring manager with a full inbox, skimming in under 10 seconds to decide: keep reading, or archive. They are not evaluating prose style — they are pattern-matching for "can I place this person against a req, and is there proof they're good." Every rule below serves that goal.

AUDIENCE: Most recipients are HR personnel or hiring managers, NOT engineers. Avoid jargon-heavy explanations, acronym soup, or naming every tool/library. Describe technical work in plain English — what you built and what it achieved, not implementation detail. The candidate is applying broadly across software engineering roles (full-stack, backend, general SDE), not exclusively AI/ML roles — so AI/LLM experience should come across as one strong skill among several, not the candidate's entire identity.

MANDATORY EMAIL STRUCTURE (follow this every time):
1. Subject line: Short (under 45 characters), specific, and scannable — a recruiter juggling dozens of reqs should be able to tell from the subject alone roughly who this is and what to route it to. Favor a role/experience anchor over vague enthusiasm. Phrased naturally and confidently — never the literal phrase "Exploring Opportunities" (too generic/cliché), and never the same wording twice. Examples of the RIGHT kind of energy (do not reuse verbatim, write fresh ones each time):
   - "Full-stack Dev, 2+ yrs — open to roles at [Company]"
   - "SDE open to new roles — background in React/Node + AI"
   - "Backend-leaning full-stack dev — quick intro, [Company]"
   Never use ALL CAPS, exclamation marks, emojis, or clickbait ("You won't believe...", "Quick question!!!").
2. Greeting line: Start with "Hi [Name]," if name is known, or "Hi there," if unknown. Never "Dear Sir/Madam". Never skip the greeting.
3. Introduction + experience (the FIRST paragraph after the greeting — always about the candidate): Introduce the candidate directly — name, role, and years of experience first ("I'm Abhishek, a Software Developer with 2+ years of experience..."), then describe HOW they work and what they're good at in plain language (e.g. "I build end-to-end web applications with React and Node, and I've been integrating AI/LLM capabilities into production systems"). Focus on capability and craft — the KIND of work they do. NO project stories or achievements in this paragraph — those come in the next one.
4. Company + supporting proof (the SECOND paragraph, 2–3 sentences): Reference what the recipient's company actually builds or does (use the company research provided), framed around genuinely wanting to contribute to that work — NOT generic admiration or flattery ("I'm impressed by...", "I admire..."). Then connect ONE relevant project or work outcome as brief supporting evidence (e.g. "Recently I built [short plain-language description] that [outcome]") — woven in naturally, never as a boastful resume bullet like "I led a project at [Company] where I built [long description]". If the source data has a real number or measurable outcome, it MUST appear here as proof; if not, describe scale or real usage concretely. If nothing reliable is known about the company, keep the company line light and honest instead of guessing. Frame AI/LLM experience as one strong skill among several.
5. Links: Right before the closing ask, include a short, clean line sharing the actual portfolio and GitHub links directly — e.g. "Portfolio: [url] · GitHub: [url]" or a natural one-line sentence with both URLs. Use plain text URLs (no markdown link syntax). Just these two — skip LinkedIn unless it's clearly more relevant for this recipient. Don't bury them inside a bullet list or repeat them elsewhere in the email.
6. Opportunity ask (1–2 sentences, right after the links): A warm, slightly fuller closing ask about opportunities — not a bare one-liner. It should express genuine openness to roles where the candidate's experience could add value, and make replying easy. Good examples of the register (vary the wording every time, never reuse verbatim):
   - "If there's an opening on your engineering team where my experience could add value, I'd love to hear about it — happy to share more details anytime."
   - "I'd be glad to explore any current or upcoming roles that line up with my background — even a quick pointer to the right person would mean a lot."
   - "If my profile fits an open position on your team, I'd love the chance to take the conversation forward — happy to share anything else you need."
   Keep it low-friction and ROUTABLE — something a recruiter can act on in one line. Do NOT ask for a call or meeting directly in this first email.
7. Sign-off: "Best regards," then the candidate's first name on a new line, then — only if the candidate's role/title is known — one short line under the name in the form "{role} · {experience_years}+ yrs" (e.g. "Full-Stack Developer · 2+ yrs"). This lets a recruiter place the candidate at a glance without rereading the email. Omit this line if role or experience isn't available rather than guessing.

BANNED PHRASES (never use any of these):
- "I am writing to express my interest"
- "I would be honored"
- "I am a passionate individual/developer"
- "I believe my skills could be a great fit"
- "Please find attached my resume" (reference the resume naturally instead)
- "synergy", "leverage", "cutting-edge", "innovative solutions", "bringing to the table", "fast-paced environment", "wear many hats", "rockstar", "ninja"
- "I hope this email finds you well"
- "Dear Sir/Madam"
- "I'm impressed by", "I've been following your innovative approaches", "I admire your commitment to" — and any similar generic-flattery phrasing
- "I wanted to reach out", "I hope this reaches you at a good time", "I'm reaching out to introduce myself" — generic filler that delays the actual point
- Any phrase that sounds like it was copied from a cover letter or LinkedIn connection request

TONE & SCANNABILITY:
- Semi-formal: confident peer-to-peer professional tone — like one builder reaching out to another, not a candidate pleading for a chance
- Direct and concise — lead with capability, not backstory
- Warm but not sycophantic — genuine, not flattering
- Short sentences, active voice, one idea per sentence — this is read on a phone between meetings, not studied. Avoid stacking multiple clauses with commas into one long sentence.
- Every sentence should earn its place: if a sentence doesn't add a fact, a proof point, or move toward the ask, cut it.

LENGTH: 120–160 words for the body (including greeting, links line, and sign-off). Shorter and sharper consistently outperforms comprehensive — err toward the low end when the pitch is complete without padding.

OUTPUT FORMAT: Return ONLY a valid JSON object: {"subject": "...", "body": "..."}
The body must include the greeting line and sign-off. No markdown, no extra text."""


FOLLOWUP_SYSTEM_PROMPT = """You are helping an experienced software professional write a brief follow-up email for an unanswered job/freelance/collaboration outreach.

The reader already saw the full pitch once and didn't reply — this email's only job is a low-effort, low-pressure nudge that makes replying (even with "not right now") as easy as possible. It is not a second chance to re-pitch.

MANDATORY EMAIL STRUCTURE:
1. Greeting: "Hi [Name]," or "Hi there," — same as the original email
2. Opening: A natural reference to the previous email (e.g., "Just wanted to follow up on my note from last week." or "Circling back on my previous email in case it got buried.")
3. Body (1–2 sentences max): A very brief re-statement of interest — do NOT repeat the full pitch or re-list achievements already sent
4. Soft closing ask: Low-pressure, open-ended, and easy to answer in one line (e.g., "Even a quick note on whether this is the right time would be great.")
5. Sign-off: "Best regards," + first name

RULES:
- 70–100 words total including greeting and sign-off — shorter than the original email, not longer
- Warm and non-pushy — confident, not desperate
- Short, single-idea sentences — no stacked clauses
- Do NOT use: "I hope this email finds you well", "I wanted to circle back" as the first three words of every email (vary it), "Dear Sir/Madam", "just checking in" as a bare opener
- Sound like a real working professional following up, not an automated sequence

OUTPUT FORMAT: Return ONLY valid JSON: {"subject": "Re: [original subject]", "body": "..."}"""


def build_resume_highlights(resume_parsed: dict) -> str:
    """
    Construct a concise work-highlights block for injection into the AI prompt.
    Prioritizes real work experience (most relevant for an experienced candidate),
    then projects, then achievements.
    """
    if not resume_parsed:
        return "No resume data available."

    lines = []

    experience = resume_parsed.get("experience", [])
    if experience:
        lines.append("""WORK EXPERIENCE (most relevant for this candidate — prefer this over side projects when picking what to mention):
Selection rule: Choose the role/entry whose domain or stack most closely matches this recipient's company.""")
        for e in experience[:3]:  # max 3 entries
            role = e.get("role", "")
            org = e.get("organization", "")
            desc = e.get("description", "")
            if role:
                lines.append(f"  - {role} at {org}: {desc}")

    projects = resume_parsed.get("projects", [])
    projects = projects.copy()
    random.shuffle(projects)

    if projects:
        lines.append("""PROJECTS (listed in random order — evaluate ALL of them before choosing; use only if no work-experience entry fits better):
Selection rule: Pick whichever project best demonstrates solid, well-rounded engineering ability for THIS recipient. Don't over-index on AI alone — the candidate is applying broadly across software engineering roles, not just AI-specific ones. When this gets used in the email, it must be described in plain, simple language (most recipients are non-technical HR/hiring managers) — mention at most 1–2 recognizable technologies, never a full stack list.
Do NOT default to the first project listed. Actively reason about fit.""")
        for p in projects[:4]:  # max 4 projects passed to AI
            title = p.get("title", "")
            desc = p.get("description", "")
            if title:
                lines.append(f"  - {title}: {desc}")

    achievements = resume_parsed.get("achievements", [])
    if achievements:
        lines.append("NOTABLE ACHIEVEMENTS (prefer ones with a concrete number/metric/outcome — these are the strongest proof points a recruiter will see, prioritize them over purely descriptive project/experience entries when one is a close enough fit):")
        for a in achievements[:3]:
            lines.append(f"  - {a}")

    return "\n".join(lines) if lines else "No structured resume data extracted."


def generate_email(profile, recipient, campaign_goal, additional_context, api_key, resume_parsed=None):
    """
    Generate a unique cold outreach email for a single recipient.
    """
    domain = recipient["email"].split("@")[1]
    research = research_company(domain, api_key)
    company = research["company_name"]
    company_summary = research.get("summary")
    company_industry = research.get("industry")

    # Recipient first name: explicit name > extracted from email > "there"
    if recipient.get("name"):
        recipient_first_name = recipient["name"].split()[0].strip().capitalize()
    else:
        recipient_first_name = extract_first_name_from_email(recipient["email"])

    greeting = f"Hi {recipient_first_name}," if recipient_first_name else "Hi there,"
    recipient_name = recipient.get("name") or recipient_first_name or "Unknown"

    if company_summary:
        company_intel_block = f"""{company_summary}
Industry: {company_industry or 'Not specified'}
(This was researched from their website/domain — treat it as reliable. Use it to make the opener and any company reference SPECIFIC to what they actually do, e.g. tie the candidate's experience to their domain of work. Do not quote it verbatim — paraphrase naturally.)"""
    else:
        company_intel_block = ("No reliable research available for this company. Keep company references "
                               "generic and safe — do NOT invent or guess what they build, their products, "
                               "or their industry.")

    resume_block = build_resume_highlights(resume_parsed) if resume_parsed else "Not available."

    role = profile.get('role') or profile.get('title') or 'Software Developer'
    experience_years = profile.get('experience_years') or profile.get('years_experience') or 'a few'
    current_company = profile.get('current_company') or profile.get('company') or 'Not specified'
    portfolio_url = profile.get('portfolio') or profile.get('website') or 'https://abhishek-kanojiya2.netlify.app/'
    github_url = profile.get('github') or 'https://github.com/Abhishekkanojiya3'
    ai_highlight = profile.get('ai_highlight') or (
        "Worked hands-on on the LLM Twin project — an open-source, production-grade "
        "LLM/RAG system (github.com/decodingai-magazine/llm-twin-course) — building and "
        "modifying real pipeline components (e.g. data pipelines, retrieval-based RAG "
        "pipelines, infrastructure swaps). Describe this simply for a non-technical "
        "reader, e.g. \"I've worked with LLM tools and built retrieval-based AI "
        "pipelines\" — don't get into implementation detail or mention the repo link "
        "itself unless it adds value."
    )

    first_name = profile.get('name', '').split()[0] if profile.get('name') else 'Candidate'
    has_real_experience_years = bool(profile.get('experience_years') or profile.get('years_experience'))
    sign_off_block = f"Best regards,\n{first_name}"
    if has_real_experience_years and role:
        sign_off_block += f"\n{role} · {experience_years}+ yrs"

    user_prompt = f"""Generate a cold outreach email from the following professional to the recipient at {company}.

CANDIDATE PROFILE:
- Name: {profile.get('name', '')}
- Current/most recent role: {role}
- Experience: {experience_years} years
- Current/most recent company: {current_company}
- Core stack / skills: {profile.get('skills', '')}
- Bio: {profile.get('bio', '')}
- Portfolio: {portfolio_url}
- GitHub: {github_url}
- LinkedIn: {profile.get('linkedin', 'Not provided')}

AI EXPERIENCE TO DRAW ON (one candidate highlight option — see RESUME HIGHLIGHT RULES below for how to choose between this and the entries below):
{ai_highlight}

WORK / RESUME HIGHLIGHTS (the other set of candidate highlight options — see RESUME HIGHLIGHT RULES below):
{resume_block}

RECIPIENT:
- Email: {recipient['email']}
- Name: {recipient_name}
- Company: {company}

COMPANY INTELLIGENCE (what {company} actually does):
{company_intel_block}

GREETING TO USE (line 1 of the body, exactly as given — do not change the name or invent a different one):
{greeting}

CAMPAIGN GOAL:
{campaign_goal}

ROLE FOCUS: The candidate is open to general software engineering roles (full-stack, backend, SDE, etc.), not exclusively AI/ML roles, unless CAMPAIGN GOAL explicitly says otherwise. AI/LLM experience should be mentioned as a differentiator/skill, not as the entire pitch.

ADDITIONAL CONTEXT:
{additional_context or 'None'}

SUBJECT LINE RULES:
- Should read as approachable to a non-technical HR/hiring-manager audience — no jargon
- Anchor it to role + experience level (e.g. "{role}, {experience_years}+ yrs") so a recruiter can tell at a glance who this is and what to route it to — never the literal cliché "Exploring Opportunities"
- Tie loosely to {company} where it feels natural — don't force it
- Keep it short (aim under ~45 characters) and skimmable
- No buzzwords, no excessive punctuation, no clickbait

STRUCTURE ORDER RULES (strict — candidate intro first, company + projects after):
- The FIRST paragraph after the greeting must introduce the candidate: name, role, years of experience, and how they work / core strengths in plain language. NO projects or achievements in this paragraph.
- The SECOND paragraph references {company} — use the COMPANY INTELLIGENCE above to anchor it to their actual line of work (what they build, who they serve), framed around genuinely wanting to contribute. This makes the email feel researched, not mass-sent. Then ONE project/outcome appears here as brief supporting evidence, connected to their domain where natural.
- If no reliable intelligence is available, do NOT guess what they do — keep the {company} line light and honest instead.
- Do NOT use flattery phrases like "I'm impressed by," "I've been following your innovative approaches," or "I admire your commitment to..."
- Instead, signal real interest in working WITH them, e.g. "What {company} is building around [specific thing] is exactly the kind of work I'd want to be part of."

RESUME HIGHLIGHT RULES:
- Pick exactly ONE highlight to mention — either the AI EXPERIENCE TO DRAW ON or a single entry from WORK / RESUME HIGHLIGHTS. Choose whichever gives the recruiter the strongest, most concrete proof for THIS recipient — a quantified, measurable outcome (a %, a scale, a headcount, time saved, revenue) beats a qualitative description every time, even if the qualitative option is the AI/RAG work. Do not default to the AI angle out of habit — treat it as one candidate option among several, weighed on the same "is there a real number here" basis as everything else.
- If a WORK / RESUME HIGHLIGHTS entry has a real metric and the AI EXPERIENCE does not, prefer the metric — proof beats novelty.
- If nothing has a hard number, prefer whichever is most relevant to this recipient's likely domain; AI/LLM experience is still valuable to mention as a differentiator in that case, framed as one strong skill, not the whole pitch (see ROLE FOCUS above).
- Describe whichever is chosen in SIMPLE, plain language for a non-technical reader — mention at most 1–2 key technologies (e.g. "LLM tools," "RAG pipelines"), never implementation detail
- PHRASING: lead with the candidate's capability/craft ("I build...", "I work on...") and let the chosen highlight appear as brief supporting evidence at the END of that thought — e.g. "...most recently, an internal system that cut manual effort significantly." NEVER phrase it as a boastful project announcement like "I recently led a project at [Company] where I built [long description]" — that reads like a resume bullet, not an email.
- If the source description contains a number, percentage, scale, or measurable outcome, that number MUST survive into the email — it's the single strongest piece of proof a recruiter sees. If it doesn't contain one, describe the outcome in concrete terms of scale or real usage rather than just naming the feature.
- Keep it to one natural sentence
- Do NOT mention more than one project/achievement in the same email
- Do NOT copy text verbatim — rephrase naturally and simply

EMAIL STRUCTURE RULES:
- Line 1 must be exactly the GREETING TO USE given above — never substitute a different name and never fall back to "Hi there," when a name was provided
- Paragraph order is fixed: (1) greeting, (2) candidate intro + experience (about the candidate, no projects), (3) company reference + one project/outcome as supporting proof, (4) links, (5) opportunity ask (1–2 warm sentences), (6) sign-off
- Vary the wording of each section across emails — but never change this paragraph order
- Closing ask (after the links): 1–2 warm sentences asking about opportunities — expressing openness to roles where the candidate could add value and making replying easy (see the example phrasings in the system instructions; vary the wording each time, never a bare "let me know if there's a fit"). Low-friction and routable — do NOT ask for a call or meeting directly in this first email
- The email must read like a natural message from one professional to another — not a list of credentials
- Give proper spacing of 1 blank line between paragraphs. Don't dump every piece of information into 1 single paragraph.
- Keep sentences short and single-idea — this is skimmed, not read closely. Cut any sentence that doesn't add a fact, proof point, or move toward the ask.

LINKS RULES:
- Include these exact links, near the end of the body (after the main pitch, before or alongside the availability/ask line), as plain text — not markdown:
  Portfolio: {portfolio_url}
  GitHub: {github_url}
- Present them cleanly on their own short line(s) or woven into one natural sentence — not as a bullet list buried mid-paragraph

SIGN-OFF:
End the email body with exactly this format (on its own lines, after the main content, nothing added or removed):

{sign_off_block}

Return ONLY valid JSON: {{"subject": "...", "body": "..."}}"""

    return _call_openai(SYSTEM_PROMPT, user_prompt, api_key)


def generate_followup(profile, original_subject, original_body, followup_context, api_key, resume_parsed=None):
    """
    Generate a follow-up email.
    """
    resume_block = build_resume_highlights(resume_parsed) if resume_parsed else "Not available."
    first_name = profile.get('name', '').split()[0] if profile.get('name') else 'Candidate'

    user_prompt = f"""Generate a follow-up email that will be sent as a reply to the email thread below.

The candidate is following up because they haven't received a response. The tone should be:
- Brief (80–120 words)
- Warm and non-pushy — not desperate
- Reference the original email naturally ("just wanted to follow up on my previous message")
- End with a soft, open-ended ask

ORIGINAL EMAIL SENT:
Subject: {original_subject}
Body: {original_body}

CANDIDATE PROFILE:
- Name: {profile.get('name', '')}
- Current/most recent role: {profile.get('role') or profile.get('title') or 'Software Developer'}
- Experience: {profile.get('experience_years') or profile.get('years_experience') or 'a few'} years
- Skills: {profile.get('skills', '')}

WORK / RESUME HIGHLIGHTS (optional — only reference if naturally relevant to the follow-up):
{resume_block}

FOLLOW-UP CONTEXT FROM USER:
{followup_context or 'None provided — write a standard polite follow-up.'}

SIGN-OFF:
End the follow-up body with exactly this format:

Best regards,
{first_name}

Return ONLY valid JSON: {{"subject": "Re: {original_subject}", "body": "..."}}"""

    return _call_openai(FOLLOWUP_SYSTEM_PROMPT, user_prompt, api_key)


def generate_contextual_followup(profile, original_subject, original_body,
                                  recipient_email, recipient_name,
                                  reply_status, reply_content,
                                  check_back_date, global_context,
                                  api_key, resume_parsed=None):
    """
    Generate a context-aware follow-up email based on the recipient's reply status.
    Each status produces a fundamentally different tone and intent.
    """

    domain = recipient_email.split("@")[1]
    company = resolve_company_name(domain, api_key)
    first_name = profile.get('name', '').split()[0] if profile.get('name') else 'Candidate'

    if recipient_name:
        followup_first_name = recipient_name.split()[0].capitalize()
    else:
        followup_first_name = extract_first_name_from_email(recipient_email)
    greeting = f"Hi {followup_first_name}," if followup_first_name else "Hi there,"

    # Build status-specific instruction block
    status_instructions = _build_status_instructions(
        reply_status, reply_content, check_back_date, company
    )

    resume_block = build_resume_highlights(resume_parsed) if resume_parsed else "Not available."

    system_prompt = f"""You are helping an experienced software professional write a follow-up email for a job/freelance/collaboration outreach.
The follow-up must be tailored precisely to the situation described below — tone, length, and intent
must match the recipient's reply status exactly.

GENERAL RULES:
- Sound completely human — not automated, not templated
- Never start with "I hope this email finds you well"
- Never use "Dear Sir/Madam"
- Use semi-formal, peer-to-peer professional tone throughout
- Start with the exact greeting provided
- End with: Best regards,\\n{first_name}
- Return ONLY valid JSON: {{"subject": "Re: {original_subject}", "body": "..."}}"""

    user_prompt = f"""Write a follow-up email for this specific situation.

SITUATION:
{status_instructions}

ORIGINAL EMAIL:
Subject: {original_subject}
Body: {original_body}

CANDIDATE PROFILE:
- Name: {profile.get('name', '')}
- Current/most recent role: {profile.get('role') or profile.get('title') or 'Software Developer'}
- Experience: {profile.get('experience_years') or profile.get('years_experience') or 'a few'} years
- Skills: {profile.get('skills', '')}
- GitHub: {profile.get('github', 'Not provided')}

WORK / RESUME HIGHLIGHTS (use only if directly relevant):
{resume_block}

RECIPIENT:
- Email: {recipient_email}
- Name: {recipient_name or 'Unknown'}
- Company: {company}

GREETING TO USE (first line, mandatory):
{greeting}

ADDITIONAL CONTEXT FROM USER:
{global_context or 'None provided.'}

SIGN-OFF:
End with exactly:
Best regards,
{first_name}

Return ONLY valid JSON: {{"subject": "Re: {original_subject}", "body": "..."}}"""

    return _call_openai(system_prompt, user_prompt, api_key)


def _build_status_instructions(reply_status, reply_content, check_back_date, company):
    """
    Returns a detailed, status-specific instruction block for the AI prompt.
    This is the core logic that makes each follow-up fundamentally different.
    """

    reply_section = f"\nTHEIR ACTUAL REPLY:\n\"\"\"\n{reply_content}\n\"\"\"\n" if reply_content else ""

    if reply_status == "no_reply":
        return f"""STATUS: No Reply — the recipient has not responded to the original email.

TONE & INTENT:
- Brief and non-pushy — 80 to 110 words total
- Acknowledge they are likely busy
- Do not repeat the full pitch from the original email
- A single soft nudge — "wanted to make sure this didn't get buried"
- End with a low-pressure, open-ended question
- Do NOT sound desperate or apologetic{reply_section}"""

    elif reply_status == "check_back":
        date_line = f"- They asked you to follow up around: {check_back_date}" if check_back_date else ""
        return f"""STATUS: Check Back — the recipient replied and asked you to reach out again later.

TONE & INTENT:
- Warm and conversational — you already have a micro-relationship since they replied
- Open by referencing THEIR suggestion to follow up (e.g., "You had mentioned reaching out again — following up as suggested")
- Do NOT re-introduce yourself fully — they remember you
- Keep it brief — 90 to 130 words
- Mention your current availability to start/engage in one line, without inventing specific dates unless given
- End with a soft, specific ask — a quick call, a 15-min chat, etc.
{date_line}{reply_section}"""

    elif reply_status == "interested":
        return f"""STATUS: Interested — the recipient replied positively and wants more information or a call.

TONE & INTENT:
- Warm, confident, and direct — do not undersell yourself
- Directly address whatever they asked or expressed interest in
- If they asked for GitHub/portfolio, acknowledge that naturally
- Propose a concrete next step — suggest a specific timeframe for a call or meeting
- 100 to 150 words
- This is NOT a nudge — it is an advancement email. Move the conversation forward.{reply_section}"""

    elif reply_status == "no_openings":
        return f"""STATUS: No Openings — the recipient explicitly said there are no positions available right now.

TONE & INTENT:
- Short, gracious, zero pressure — 60 to 90 words
- Thank them genuinely for taking the time to reply (most people don't)
- Express that you understand completely
- Ask warmly if they could keep your profile in mind for future openings
- OR ask if they know anyone else in their network who might be looking to hire
- Leave a strongly positive final impression — these people move companies and open roles later
- Do NOT sound disappointed or pushy{reply_section}"""

    else:
        # Fallback for any unexpected status
        return f"""STATUS: General follow-up.
TONE: Semi-formal, brief, warm. 80 to 120 words.{reply_section}"""


def _call_openai(system_prompt, user_prompt, api_key, retries=4, model=OPENAI_MODEL):
    """
    Call the OpenAI API and parse the JSON response.
    Retries with exponential backoff on rate limits or transient (5xx) errors.
    """
    client = OpenAI(api_key=api_key)

    last_error = None
    for attempt in range(retries):
        try:
            # Add random seed variation for uniqueness
            seed_note = f"\n[Variation seed: {random.randint(1000, 9999)}]"

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt + seed_note}
                ],
                temperature=0.9,
                response_format={"type": "json_object"}
            )

            text = response.choices[0].message.content.strip()
            result = json.loads(text)

            if "subject" not in result or "body" not in result:
                raise ValueError("Response missing 'subject' or 'body' keys")

            return result

        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            if attempt < retries - 1:
                continue
            raise Exception(f"Failed to parse OpenAI response after {retries} attempts: {e}")

        except RateLimitError as e:
            last_error = e
            if "insufficient_quota" in str(e).lower():
                raise Exception(f"OpenAI account has run out of quota: {e}")
            if attempt < retries - 1:
                wait = 5 * (2 ** attempt)  # 5s, 10s, 20s, 40s
                print(f"OpenAI rate limit hit — retrying in {wait}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            raise Exception(f"OpenAI rate limit exceeded after {retries} attempts: {e}")

        except APIStatusError as e:
            last_error = e
            if e.status_code >= 500 and attempt < retries - 1:
                wait = 5 * (2 ** attempt)
                print(f"OpenAI API error ({e.status_code}) — retrying in {wait}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            raise Exception(f"OpenAI API error: {e}")

    raise Exception(f"OpenAI API unavailable/failed after {retries} retries. Last error: {last_error}")