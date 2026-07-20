# Outreach

A full-stack outreach CRM for creating campaigns, generating personalized emails with AI, sending them through Gmail SMTP, and tracking replies, opens, follow-ups, and inbox activity.

This repo is organized as a monorepo:
- `frontend/` contains the React + Vite UI
- `backend/` contains the Flask + SQLite API

## What It Does

- Create campaigns from pasted email lists or extracted PDF/XLSX files
- Generate personalized outreach emails in parallel with AI
- Attach resumes automatically when sending
- Send follow-ups in threaded conversations
- Track opens with a lightweight pixel endpoint
- Monitor inbox replies, bounces, and out-of-office messages
- Search across campaigns and contacts
- Detect duplicate contacts before outreach
- Block domains you do not want to contact again
- Surface dashboard stats for campaigns, replies, and opens

## Features

### Campaign workflow
- Campaign creation with name, goal, context, and send limits
- Import emails from plain text, PDF, or XLSX files
- Duplicate checking before sending
- Re-engagement candidate finder

### AI generation
- Parallel generation of customized outreach drafts
- Context-aware follow-up generation
- Resume-aware personalization

### Sending and tracking
- Gmail SMTP sending with app passwords
- Resume attachment support
- Follow-up sending with reply threading
- Open tracking via public tracking pixel
- Reply status tracking for interested, check-back, no reply, and invalid email

### CRM and analytics
- Campaign dashboard
- Search across contacts and campaigns
- Contact history view
- Open and reply stats
- Blocked domain management

## Tech Stack

### Frontend
- React 18
- Vite
- React Router
- Axios
- Tailwind CSS

### Backend
- Python 3.11+
- Flask
- SQLite
- APScheduler
- OpenAI SDK
- PDF parsing and email extraction helpers

## Project Structure

```text
Outreach/
  backend/
  frontend/
  start-tracking.ps1
  README.md
```

## Local Setup

### 1. Clone the repo

```bash
git clone git@github.com:Abhishekkanojiya3/Outreach.git
cd Outreach
```

### 2. Backend setup

```bash
cd backend
python -m venv venv

# Windows
.\venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
python app.py
```

The backend runs on `http://localhost:5000`.

### 3. Frontend setup

Open a new terminal:

```bash
cd frontend
npm install
npm run dev
```

The frontend runs on `http://localhost:5173`.

## Configuration

Use the app UI to configure:
- Gmail address
- Gmail app password
- OpenAI API key
- Send delay
- Tracking base URL
- Profile details
- Resume upload

For deployed backend hosting, set these environment variables on the backend service:
- `DATABASE_URL`
- `OPENAI_API_KEY`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`
- `BREVO_API_KEY`
- `SMTP_HOST`
- `SMTP_PORT`
- `TRACKING_BASE_URL`
- `CORS_ORIGINS`
- `SEND_DELAY_SECONDS`

## Notes

- The backend stores data locally in SQLite by default.
- In production, set `DATABASE_URL` to use Postgres instead of SQLite.
- Set `BREVO_API_KEY` in production to send emails through Brevo's HTTPS API on Render free services.
- Gmail SMTP uses port `587` by default. Free Render web services block outbound SMTP, so use Brevo API or a paid Render instance for Gmail SMTP.
- Uploaded resumes and local config live inside `backend/`.
- For production hosting, deploy the frontend and backend separately.

## Recommended Deployment

- Frontend: Vercel or Netlify
- Backend: Render, Railway, or a small Python host

If you want persistent data in production, move the SQLite database to Postgres and store uploads in persistent storage.

## License

MIT
