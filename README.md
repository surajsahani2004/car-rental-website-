# Safar Suvidha Car Rental

Flask + SQLite based multi-company car rental system with role-based dashboards.

## Main Features

- Roles: `super_admin (king)`, `boss`, `manager`, `customer`
- Company-level data isolation
- Boss/Manager registration via admin access codes
- Manager approval flow (only company boss can approve/reject)
- Customer car booking with overlap (double-booking) prevention
- Payment simulation + PDF invoice + QR code
- Car photo upload
- Help/Complaint request to king
- King-to-user Order/Notification system
- User profile fields: full name, gender, age, driving license yes/no

## Default Super Admin

- Username: `king`
- Password: `developer`

## Admin Access Codes

- Boss: `BOSS123`
- Manager: `MANAGER123`

## Quick Start

1. Create virtual environment and activate it.
2. Install packages:
   `pip install flask flask-sqlalchemy flask-login flask-wtf qrcode reportlab`
3. Run:
   `python app.py`
4. Open:
   `http://127.0.0.1:5000`

## Tech Stack

- Flask
- SQLAlchemy
- SQLite
- Jinja2 + HTML/CSS
- ReportLab
- qrcode

## Notes

- Database file is created automatically in `instance/`.
- For a fresh reset, delete `instance/safar_suvidha.db` and run again.
