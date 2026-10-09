# FoodBridge
Surplus-food redistribution platform. Flask + SQLite backend, vanilla HTML/CSS/JS frontend.

## Run
```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
export SECRET_KEY="change-me" EMERGENCY_PHONE="+91-..." EMERGENCY_ADDRESS="Your emergency desk address"
python app.py        # http://localhost:5000
```
Production: `gunicorn app:app`.

## Flow
Login (choose role) → Dashboard → Register food / Find food → Smart matching → Confirm → Delivery tracking → Impact.

## Your requested features
| Request | Where |
|---|---|
| Role ("vertical") picker on login | `static/index.html` role tabs, checked server-side in `/api/login` |
| Auto-rotate | Responsive CSS, `orientation:any` in manifest, no orientation lock |
| Graphics | Inline SVG bridge animation (respects reduced motion) |
| Emergency contact on login | `EMERGENCY_*` env vars, `/api/config` |
| No clashes between recipients | `allocate()` in `app.py`: atomic guarded UPDATE inside `BEGIN IMMEDIATE` |
| Donor offers to non-requesting recipients | `/api/recipients`, `/api/offers`, recipient accept/decline |
| Dynamic reallocation | Failed/Cancelled status returns meals to the pool |

## Notes
- Live GPS needs a maps/tracking service; statuses are updated manually.
- Recipient verification: `users.verified` defaults to 1. Set to 0 and approve manually for production.
- Add rate limiting, HTTPS and CSRF protection before going public.
