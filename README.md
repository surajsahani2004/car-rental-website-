# Safar Suvidha Travel Facility

A car rental web application built with Flask and SQLAlchemy.

## Features

- User registration and login
- Car catalogue with availability (multiple cities)
- Booking system with date selection, GST calculation (18%)
- PDF invoice generation with QR code for verification
- Admin dashboard for managing bookings and adding cars
- Role-based access (customer, admin)

## Setup

1. Ensure Python 3.14+ is installed.
2. Install dependencies: `pip install flask flask-sqlalchemy flask-login flask-wtf qrcode reportlab`
3. Run the app: `python app.py`
4. Open browser to `http://localhost:5000`

## Usage

- Register as a new user or login.
- Browse available cars on the home page.
- Book a car by selecting dates (cost includes GST).
- Download PDF invoice with QR code after booking.
- Admin login: email=admin@example.com, password=admin123
- Admin can view bookings and add new cars.

## Technologies

- Flask (web framework)
- SQLAlchemy (database ORM)
- SQLite (database)
- HTML/CSS/JS (frontend)
- ReportLab (PDF generation)
- qrcode (QR code generation)

## Future Enhancements

- Real payment gateway integration
- Availability calendar
- User feedback system
- Email notifications
- Responsive design improvements