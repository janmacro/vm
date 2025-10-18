# Vereinsmeisterschaft

Small Flask app to manage swimmers and generate optimal lineups using OR-Tools.

## Features
- Per-user accounts (Flask-Login) with isolated data
- CRUD for swimmers and personal bests
- Import PBs from swimrankings.net
- Lineup optimizer with rest constraints
- CSRF protection (Flask-WTF)

## Quick Start (Docker)
1. Build the image:
   
   docker build -t vereinsmeisterschaft .

2. Run with a persistent volume and a strong secret key:
   
   docker run -d \
     --name vereinsmeisterschaft \
     -e SECRET_KEY='change-me-to-a-random-string' \
     -v vm_instance:/app/instance \
     -p 8000:8000 \
     vereinsmeisterschaft

The container automatically runs an idempotent database init on startup.
Visit http://localhost:8000 and register a new account.

## Environment Variables
- SECRET_KEY: required in production; long random string used for sessions/CSRF.
- SQLALCHEMY_DATABASE_URI (optional): defaults to SQLite in `/app/instance/app.db`.

## Local Development
- Install Python 3.12+
- Install dependencies:
  
  pip install .

- Initialize DB and run:
  
  flask --app app:create_app init-db
  flask --app app:create_app run --debug