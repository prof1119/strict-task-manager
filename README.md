# ⚡ Strict Task Manager

A personal task and study planner built with **Flask, SQLite, and APScheduler**, with an Arabic RTL interface and optional Telegram reminders.

> **Status:** Personal-use project under active improvement. Security and deployment hardening are ongoing; do not expose it publicly until you have reviewed authentication, input validation, and background-job deployment.

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Flask" src="https://img.shields.io/badge/Flask-Web%20app-000000?logo=flask">
  <img alt="SQLite" src="https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite">
  <img alt="Interface" src="https://img.shields.io/badge/Interface-Arabic%20RTL-00C7D9">
</p>

## ✨ Highlights

- Task planning, priorities, durations, and completion tracking.
- Study-course planning and progress tracking.
- Arabic, right-to-left interface with a dark neon visual style.
- Optional Telegram notifications and callback handling.
- SQLite storage for a lightweight, single-user setup.
- Scheduled background checks for reminders and prayer-time synchronization.

## 🧰 Stack

- Python 3.10+
- Flask
- SQLite
- APScheduler 3.x
- Telegram Bot API (optional)
- Gunicorn for WSGI hosting

## 🚀 Run locally

1. Install Python 3.10 or newer.
2. Clone this repository and enter its folder.
3. Create and activate a virtual environment:

   **Windows**
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   **Linux / macOS**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. Install dependencies:

   ```bash
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

5. Set the required environment variables before starting the app.

   **PowerShell**
   ```powershell
   $env:SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"
   $env:APP_PASSWORD = "use-a-long-unique-password"
   ```

   **Linux / macOS**
   ```bash
   export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
   export APP_PASSWORD='use-a-long-unique-password'
   ```

6. Start the app:

   ```bash
   python app.py
   ```

7. Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

The app creates its SQLite database on startup. Keep `tasks.db` backed up; it contains your task and course data.

## 🔔 Optional Telegram setup

Set these environment variables in your terminal or hosting provider:

- `BOT_TOKEN`: a newly generated Telegram bot token.
- `CHAT_ID`: the destination chat ID.
- `SECRET_KEY`: a long, random secret used to sign sessions.
- `APP_PASSWORD`: a strong, unique password for the app.
- `DATABASE_PATH`: optional database path; defaults to `tasks.db`.
- `COOKIE_SECURE`: set to `1` when served over HTTPS. Leave unset for local HTTP development.

A `.env.example` file documents the variable names. It is a template only, not a secrets store. The app does not automatically load a `.env` file, so configure variables in your shell or hosting dashboard.

**Important:** if a bot token was ever committed to a public repository, revoke it in [BotFather](https://t.me/BotFather) and create a replacement. Removing a token from the latest file does not remove it from Git history.

## ⚠️ Deployment notes

- Use HTTPS and set `COOKIE_SECURE=1` in production.
- Do not use a weak/default password or share the app publicly.
- Run a single application worker while the scheduler and Telegram polling run inside the app. Multiple Gunicorn workers can duplicate background jobs and polling.
- This is not yet a multi-user application. Review authorization, CSRF protection, request validation, and error handling before exposing it to the internet.
- Never commit real tokens, passwords, session secrets, or the live SQLite database.

## 🗺️ Next improvements

- Add automated tests for task/course CRUD, CSRF enforcement, and reminder scheduling.
- Add stricter validation for numeric and date inputs.
- Separate the scheduler and Telegram polling into a dedicated worker process.
- Split the large application module into routes, services, and database modules.
- Add a deployment guide and a health-check endpoint.

## 📄 License

No license has been specified yet. Add a license file before inviting others to reuse or redistribute the project.
