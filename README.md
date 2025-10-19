# Vereinsmeisterschaft

Small Flask app to generate optimal lineups for the
[Schweizer Vereinsmeisterschaft](https://www.swiss-aquatics.ch/leistungssport/swimming/nationale-meisterschaften/schweizer-vereinsmeisterschaft-nla-nlb/).

## Quick Start (Docker)
1. Build the image:
   
   docker build -t vm .

2. Run with a persistent volume and a strong secret key:
   
   docker run -d \
     --name vm \
     -e SECRET_KEY='change-me-to-a-random-string' \
     -v vm_data:/app/instance \
     -p 8000:8000 \
     vm

The container automatically runs a database init on startup.
Visit http://localhost:8000 and register a new account.

## Environment Variables
- SECRET_KEY: required in production; long random string used for sessions/CSRF.
- SQLALCHEMY_DATABASE_URI (optional): defaults to SQLite in `/app/instance/app.db`.

## Local Development
- Install Python 3.12+
- Install dependencies:
  
  pip install .

- Initialize DB and run:
  
  flask --app app init-db
  flask --app app run --debug