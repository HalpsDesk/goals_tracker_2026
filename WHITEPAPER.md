goals_tracker_2026

Local-First Goals Tracking & Public Reporting System
Architecture Whitepaper (v1)

1. Purpose

goals_tracker_2026 is a local-first goals and progress tracking system designed to record, visualize, and publicly display progress toward personal goals for the year 2026.

All goal definitions and updates are entered locally via a Python GUI and stored in a SQLite database. The system automatically generates and publishes read-only HTML dashboards to a public website using GitHub Pages.

The primary objectives are:

clear, auditable tracking of goals and progress

zero online write access

minimal operational overhead

strong documentation for future reuse and maintenance

2. Design Principles

Local source of truth
All authoritative data (goals and check-ins) lives in a local SQLite database.

Write-local, read-public separation
Data entry is only possible through a local GUI application. The public website is static and cannot modify state.

Automatic publishing on update
Any successful update in the GUI triggers regeneration and deployment of the website.

Low infrastructure complexity
No servers, background jobs, schedulers, or cloud databases.

Maintainability over convenience
The system is optimized for clarity and long-term usability rather than clever shortcuts.

3. High-Level Architecture

Core Components

SQLite Database (goals_tracker_2026.db)
Stores goals, metadata, and time-series check-ins.

Local Admin GUI (Python + PySide6)
Used to create goals and record progress updates.
This is the only component with write access.

Static Site Generator (Python)
Reads from SQLite and generates HTML pages and charts.

GitHub Pages
Hosts the generated static site and serves it via a custom domain.

Data Flow

GUI Update
   ↓
SQLite Write (transaction)
   ↓
HTML & Chart Generation
   ↓
Git Commit + Push
   ↓
GitHub Pages → Public Website

4. Technology Choices

SQLite — file-based, durable, zero-configuration local database

Python — consistent language for GUI, data processing, and publishing

PySide6 (Qt) — robust, maintainable GUI framework

Static HTML + Charts — secure, simple, and reliable

GitHub Pages — free, versioned, and easy to operate

5. Operational Model
Running the System

The project is opened and run from VS Code.
Primary entry point:

admin_app.py — launches the local GUI

No command-line usage is required.

Making an Update

Open the GUI

Add or update a goal or check-in

Click Save

The system automatically:

writes data to SQLite

regenerates all HTML pages and charts

commits and pushes changes to GitHub Pages

Viewing Progress

Visit the configured domain

All content is static and read-only

6. Security Model

SQLite database never leaves the local machine

No APIs, forms, or writable web endpoints

Deployment credentials stored locally and excluded from version control

Public site cannot modify data by design

7. Explicit Non-Goals (v1)

Mobile application

Online editing or authentication

Real-time dashboards

Multi-year historical merging

These are intentionally excluded to preserve simplicity and correctness.

8. Success Criteria

Goals and check-ins persist reliably in SQLite

GUI usage does not require memorizing commands

Website updates automatically on every valid change

The system can be understood and resumed after long periods of inactivity

Status: Architecture finalized.
Project: goals_tracker_2026
Next Step: Implementation.