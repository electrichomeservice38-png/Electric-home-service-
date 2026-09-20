# Electric Home Services — Full Website Build

This package is a website-first Flask implementation that preserves the original service/rate/distance/billing/product/role concepts and adds the requested app features as web equivalents.

## Included
- OWNER / TECHNICIAN / CUSTOMER roles with backend authorization
- Owner-only settings, prices, commission, offers, coupons, technician verification, user controls, audit log and database backup
- Customer registration/login, booking, saved address, landmark, district/state/PIN, GPS latitude/longitude fields, date/time, booking status, invoice and payment record
- Technician registration, verification status, job workflow, location update API, service-completion OTP verification and KYC document upload
- Services, price list, service charges and technician payout percentages
- Distance-charge table: 0–3 ₹0; 3–5 ₹30; 5–10 ₹60; 10–15 ₹100; 15–20 ₹150; 20–30 ₹220; 30–40 ₹300; 40–50 ₹400; above 50 ₹400 + ₹10/km extra
- Billing/invoice with browser Print → Save as PDF
- Product and stock tables, sales schema, commission/earnings schema, reviews, notifications, coupons, offers
- SQLite WAL/full-sync and owner-downloadable backups
- CSRF protection, password hashing, secure session cookies, input limits, role checks, audit logging

## Run
1. `pip install -r requirements.txt`
2. Set a strong `SECRET_KEY` and `OWNER_PASSWORD` environment variable before production.
3. `python app.py`
4. Open `http://127.0.0.1:5000`

## Real OTP / payment / maps
The package deliberately does not fake successful OTP or payment. Configure a real SMS provider, payment gateway and maps/distance API through server-side environment variables and webhook verification before taking real customer payments. Never put private gateway keys in browser JavaScript.

## Data safety
The database is created/migrated automatically and uses SQLite WAL + FULL synchronous mode. Owner can download a consistent SQLite backup from the Owner panel. For production, use persistent storage or a managed database and an external backup schedule.

## Security note
KYC documents are stored outside templates and can only be downloaded through an Owner-authorized route (technicians can upload their own documents). Use HTTPS and private/persistent storage in production.
