# Where Is The Card

Where Is The Card is a small, self-hosted church asset custody application for tracking the four church bank/payment cards. It lets staff quickly see who has each card, check them out and back in, request extensions, and keep a simple audit trail. The project is intentionally lightweight and designed for a church office environment rather than a general-purpose asset management system.

## Overview

The app is a server-rendered Flask application that runs in Docker behind Nginx Proxy Manager (NPM). The public site is served over HTTPS by NPM while the container itself listens only on an internal HTTP port. SQLite is used for the database, with the file stored in a persistent `/data` directory so it survives container restarts and redeployments.

## Key features

- Dashboard showing the status of all four cards
- Secure local user authentication with password hashing
- Staff checkout and return workflow for the card they personally hold
- Automatic due time tracking and overdue detection
- Finance overdue email and admin reason tracking
- Extension requests with Senior Pastor approval or rejection
- Basic audit log and admin interface
- Mobile-first interface designed for quick use on a phone

## Architecture

- Flask + Jinja2 for the server-rendered web app
- SQLAlchemy for database access
- SQLite for local persistence
- Flask-Login for sessions and authentication
- Flask-WTF for CSRF protection
- Flask-Migrate for database migrations
- Gunicorn for the production WSGI server
- SMTP email notifications for overdue and extension updates
- A separate lightweight worker process handles overdue checks reliably without depending on a browser session

## Local development

1. Create and activate a virtual environment.
2. Install dependencies.
3. Copy the environment file and set values.
4. Run the app locally with Flask.

Example:

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app.cli create-admin
python run.py
```

If you want to run the worker locally as well:

```bash
python -m app.worker
```

## Docker deployment

The repository is prepared for a straightforward Docker Compose deployment.

1. Create a `.env` file from `.env.example`.
2. Update the SMTP and secret values.
3. Build and start the stack.

```bash
docker compose build
docker compose up -d
```

This will create the web app and a worker service. The worker is intentionally separate so overdue checks continue running even though the main app is only serving requests.

## Environment variables

The application reads values from environment variables. The example file includes:

- `SECRET_KEY`
- `DATABASE_URL`
- `SESSION_TIMEOUT_MINUTES`
- `CARD_CHECKOUT_MINUTES`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_USE_TLS`
- `MAIL_FROM`
- `FINANCE_EMAIL`
- `SENIOR_PASTOR_EMAIL`
- `SENIOR_PASTOR_NOTIFICATION_THRESHOLD_MINUTES`

The built-in bootstrap administrator is `server@solidground.co.za` with the default password `CardAdmin123`, and the admin can change it from the web UI after first login.

The docker-compose example mounts the database directory at `/data`, so the SQLite file is persisted between container restarts.

## Database location

The SQLite database is kept under the mounted Docker data directory:

```text
/data/app.db
```

This keeps the database data outside the app container filesystem and is essential for persistence.

## Initial setup

Create the initial administrator account with:

```bash
docker compose exec whereisthecard python -m app.cli create-admin
```

The command prompts for:

- name
- email
- password

Additional commands available through the CLI:

```bash
docker compose exec whereisthecard python -m app.cli create-user
docker compose exec whereisthecard python -m app.cli list-users
docker compose exec whereisthecard python -m app.cli reset-password
docker compose exec whereisthecard python -m app.cli disable-user
```

## Users and roles

Available roles:

- `USER`
- `FINANCE`
- `SENIOR_PASTOR`
- `ADMIN`

The role system is intentionally small and matches the church's operational needs. The app does not attempt to manage general assets or broad access-control features beyond what is required for this custody workflow.

## Nginx Proxy Manager configuration

NPM should terminate HTTPS and pass traffic to the internal port on the Docker host. A typical reverse-proxy target is:

```text
http://192.168.0.18:8000
```

The application expects the incoming request to be forwarded correctly and uses Werkzeug `ProxyFix` so the application can trust forwarded headers.

Recommended NPM configuration:

- Domain: `whereisthecard.solidground.co.za`
- Scheme: `https`
- Forward Host: `whereisthecard`
- Forward Port: `8000`
- Websocket: off
- Force SSL: on

This keeps the application internal and lets NPM handle the public certificate.

## SMTP configuration

The app sends email for:

- polite 5-minute pre-due reminders to the current holder
- overdue notifications to Finance
- optional Senior Pastor alerts when a card exceeds the configured overdue threshold
- extension request emails to the Senior Pastor
- approval or rejection emails to the user

The Senior Pastor threshold is configurable in the admin settings page and can also be set with `SENIOR_PASTOR_NOTIFICATION_THRESHOLD_MINUTES`.

If the SMTP configuration is not available, email sending is skipped with a clear log message rather than failing the main database transaction.

## Backups

Because the important state is in the SQLite file, the recommended backup is a copy of:

```text
/mnt/docker/docker/data/whereisthecard/app.db
```

A simple backup command is:

```bash
cp /mnt/docker/docker/data/whereisthecard/app.db /mnt/docker/docker/data/whereisthecard/app.db.bak
```

If needed, a CLI backup command is also available:

```bash
docker compose exec whereisthecard python -m app.cli backup
```

## Updating the application from GitHub

Use the standard deployment flow:

```bash
git pull
docker compose build
docker compose up -d
```

This is the intended production process for the church environment.

## Restoring the database

Stop the stack, replace the SQLite file with the backup, and start the app again:

```bash
cp /path/to/backup/app.db /mnt/docker/docker/data/whereisthecard/app.db
docker compose up -d
```

## Troubleshooting

Common checks:

- Confirm the `.env` file has valid values.
- Review the container logs with `docker compose logs -f`.
- Confirm the `/data` mount exists and is writable.
- Check that the app can reach the configured SMTP server.
- Ensure the database file is not being overwritten by a stale container mount.

## Security considerations

This is an internal application, but it still needs real safeguards:

- password hashing via Werkzeug
- CSRF protection for form posts
- secure, HttpOnly, SameSite session cookies
- short session lifetime by default
- no passwords kept in source control
- no sensitive information in URLs
- server-side authorization checks for every action
- no browser GPS or location permissions

The application is intentionally internal and assumes it is reachable only from the church network behind NPM.

## Why the worker process was chosen

The project uses a separate lightweight worker service instead of a scheduler inside the main Flask app. This keeps the app simple, avoids duplicated scheduling across Gunicorn workers, and makes overdue checks continue reliably after container restarts. It is a small, robust architecture for a church office environment and avoids introducing Redis or Celery for this tiny use case.

## License

This project is intended for the Solid Ground Church internal environment.
