# goals_tracker_2026

Local-first goals tracking with automatic public dashboards.

`goals_tracker_2026` stores goals and progress updates in a local SQLite database, provides a local Python GUI for data entry, and automatically publishes a read-only static website to GitHub Pages (and optionally a custom domain) whenever updates are saved.

---

## Features

- Local SQLite database as the source of truth
- Local Python GUI for entering and editing goals and check-ins
- Automatic generation of static HTML pages and progress charts on update
- Automatic deployment to GitHub Pages
- Public site is view-only (no online editing, no APIs)

---

## Architecture

```text
Local GUI (Python)
  -> SQLite DB (local file)
  -> Static site generator (HTML + charts)
  -> Git commit + push
  -> GitHub Pages
  -> Public read-only website
```

---

## Repository Layout (Planned)

```text
goals_tracker_2026/
├── admin_app.py
├── build_site.py
├── deploy.py
├── data/
│   └── goals_tracker_2026.db
├── site/
├── site_templates/
├── charts/
├── docs/
│   └── WHITEPAPER.md
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Prerequisites

- Python 3.10+
- Git
- A GitHub repository for this project
- GitHub Pages enabled for that repository

---

## One-Time Setup

### Clone the repository

```bash
git clone https://github.com/HalpsDesk/goals_tracker_2026.git
cd goals_tracker_2026
```

### Create and activate a virtual environment

```bash
python -m venv .venv
```

Windows (PowerShell):

```bash
.venv\Scripts\Activate.ps1
```

Windows (cmd.exe):

```bat
.venv\Scripts\activate.bat
```

macOS/Linux:

```bash
source .venv/bin/activate
```

### Install dependencies

```bash
pip install -r requirements.txt
```

---

## Configuration

### Environment file

```bash
cp .env.example .env
```

Populate `.env` with local-only values such as:

- `GITHUB_USERNAME`
- `GITHUB_REPO`
- `GITHUB_PAGES_BRANCH`
- `GITHUB_TOKEN`

---

## Running the System

### Launch the local GUI

```bash
python admin_app.py
```

All writes occur locally through the GUI.

---

## Update and Publish Workflow

When an update is saved:

1. Write to SQLite
2. Regenerate static HTML and charts
3. Commit and push the site to GitHub Pages

---

## Manual Rebuild (Optional)

```bash
python build_site.py
```

```bash
python deploy.py
```

---

## Viewing the Public Site

```text
https://<your-username>.github.io/goals_tracker_2026/
```

---

## Data and Backups

```text
data/goals_tracker_2026.db
```

Back up this file regularly.

---

## Security Model

- No online write access
- No public APIs
- SQLite remains local
- Credentials stored locally
- Public site is static HTML

---

## Non-Goals (v1)

- Online editing
- Authentication
- Mobile app
- Cloud database
- Real-time dashboards

---

## Documentation

```text
docs/WHITEPAPER.md
```

---

## Status

Architecture finalized. Implementation in progress.
