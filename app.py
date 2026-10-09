"""FoodBridge backend: Flask + SQLite. Run: python app.py"""
import os, sqlite3, time, math
from flask import Flask, request, jsonify, session, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")
DB = os.environ.get("DB_PATH", "foodbridge.db")
EMERGENCY = {
    "name": os.environ.get("EMERGENCY_NAME", "FoodBridge Emergency Desk"),
    "phone": os.environ.get("EMERGENCY_PHONE", "+91-00000-00000"),
    "email": os.environ.get("EMERGENCY_EMAIL", "emergency@foodbridge.example"),
    "address": os.environ.get("EMERGENCY_ADDRESS", "Set EMERGENCY_ADDRESS in your environment"),
}
ROLES = ("donor", "recipient", "partner")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, org TEXT, email TEXT UNIQUE,
  phone TEXT, address TEXT, lat REAL, lng REAL, pw TEXT, role TEXT, verified INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS foods(id INTEGER PRIMARY KEY, donor_id INT, item TEXT, diet TEXT,
  qty INT, remaining INT, expires_at REAL, created REAL);
CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, recipient_id INT, diet TEXT, qty INT,
  note TEXT, status TEXT DEFAULT 'open', created REAL);
CREATE TABLE IF NOT EXISTS allocations(id INTEGER PRIMARY KEY, food_id INT, recipient_id INT,
  partner_id INT, qty INT, status TEXT DEFAULT 'Confirmed', created REAL);
CREATE TABLE IF NOT EXISTS offers(id INTEGER PRIMARY KEY, food_id INT, donor_id INT, recipient_id INT,
  qty INT, status TEXT DEFAULT 'pending', created REAL);
"""
STAGES = ["Confirmed", "Ready for Pickup", "In Transit", "Delivered"]


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB, isolation_level=None, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init():
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    c.close()


def me():
    uid = session.get("uid")
    if not uid:
        return None
    return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def need(*roles):
    u = me()
    if not u or (roles and u["role"] not in roles):
        return None, (jsonify(error="Not allowed for your role or not signed in."), 403)
    return u, None


def km(a, b):
    if not (a["lat"] and a["lng"] and b["lat"] and b["lng"]):
        return 10.0  # unknown location: neutral default
    p = math.pi / 180
    x = math.sin((b["lat"] - a["lat"]) * p / 2) ** 2 + math.cos(a["lat"] * p) * math.cos(b["lat"] * p) * math.sin((b["lng"] - a["lng"]) * p / 2) ** 2
    return round(12742 * math.asin(math.sqrt(x)), 1)


def allocate(food_id, rid, qty):
    """Race-safe allocation: one atomic UPDATE guards against two recipients taking the same meals."""
    d = db()
    d.execute("BEGIN IMMEDIATE")
    try:
        n = d.execute("UPDATE foods SET remaining=remaining-? WHERE id=? AND remaining>=? AND expires_at>?",
                      (qty, food_id, qty, time.time() + 3600)).rowcount
        if n == 0:
            d.execute("ROLLBACK")
            return None
        cur = d.execute("INSERT INTO allocations(food_id,recipient_id,qty,created) VALUES(?,?,?,?)",
                        (food_id, rid, qty, time.time()))
        d.execute("COMMIT")
        return cur.lastrowid
    except Exception:
        d.execute("ROLLBACK")
        raise


def diet_ok(food, want):
    return want == "any" or food == want or food == "vegetarian"


@app.get("/api/config")
def config():
    return jsonify(emergency=EMERGENCY)


@app.post("/api/signup")
def signup():
    j = request.json or {}
    if j.get("role") not in ROLES or not j.get("email") or len(j.get("password", "")) < 6:
        return jsonify(error="Choose a role, enter an email, and use a password of 6+ characters."), 400
    try:
        db().execute("INSERT INTO users(name,org,email,phone,address,lat,lng,pw,role) VALUES(?,?,?,?,?,?,?,?,?)",
                     (j.get("name"), j.get("org"), j["email"].lower(), j.get("phone"), j.get("address"),
                      j.get("lat") or None, j.get("lng") or None, generate_password_hash(j["password"]), j["role"]))
    except sqlite3.IntegrityError:
        return jsonify(error="That email is already registered. Log in instead."), 409
    return jsonify(ok=True)


@app.post("/api/login")
def login():
    j = request.json or {}
    u = db().execute("SELECT * FROM users WHERE email=?", ((j.get("email") or "").lower(),)).fetchone()
    if not u or not check_password_hash(u["pw"], j.get("password", "")):
        return jsonify(error="Email or password is incorrect."), 401
    if u["role"] != j.get("role"):
        return jsonify(error=f"This account is registered as {u['role']}. Pick that role to log in."), 403
    session.permanent = bool(j.get("remember"))
    session["uid"] = u["id"]
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def whoami():
    u = me()
    return jsonify(user=None if not u else {k: u[k] for k in ("id", "name", "org", "role", "email")})


# ---------- Donor ----------
@app.post("/api/foods")
def add_food():
    u, err = need("donor")
    if err: return err
    j = request.json or {}
    q, h = int(j.get("qty", 0)), float(j.get("hours", 0))
    if q < 1 or h <= 1:
        return jsonify(error="Enter a quantity of 1+ and more than 1 hour of safe time."), 400
    db().execute("INSERT INTO foods(donor_id,item,diet,qty,remaining,expires_at,created) VALUES(?,?,?,?,?,?,?)",
                 (u["id"], j.get("item"), j.get("diet", "vegetarian"), q, q, time.time() + h * 3600, time.time()))
    return jsonify(ok=True)


@app.get("/api/foods")
def my_foods():
    u, err = need("donor")
    if err: return err
    rows = db().execute("SELECT * FROM foods WHERE donor_id=? ORDER BY id DESC", (u["id"],)).fetchall()
    return jsonify([{**dict(r), "hours_left": round((r["expires_at"] - time.time()) / 3600, 1)} for r in rows])


@app.get("/api/recipients")
def recipients():
    """Donor view: every verified recipient, open demand first. Lets donors offer food even with no open request."""
    u, err = need("donor")
    if err: return err
    rows = db().execute("""SELECT u.id,u.org,u.address,u.lat,u.lng,
        COALESCE((SELECT SUM(qty) FROM requests r WHERE r.recipient_id=u.id AND r.status='open'),0) AS needs,
        COALESCE((SELECT SUM(qty) FROM allocations a WHERE a.recipient_id=u.id AND a.status!='Cancelled'),0) AS received
        FROM users u WHERE role='recipient' AND verified=1""").fetchall()
    out = [{**dict(r), "distance": km(u, r)} for r in rows]
    out.sort(key=lambda r: (-(r["needs"] > 0), r["received"], r["distance"]))
    return jsonify(out)


@app.post("/api/offers")
def make_offer():
    u, err = need("donor")
    if err: return err
    j = request.json or {}
    f = db().execute("SELECT * FROM foods WHERE id=? AND donor_id=?", (j.get("food_id"), u["id"])).fetchone()
    q = int(j.get("qty", 0))
    if not f or q < 1 or q > f["remaining"]:
        return jsonify(error="Pick one of your listings and a quantity that is still available."), 400
    db().execute("INSERT INTO offers(food_id,donor_id,recipient_id,qty,created) VALUES(?,?,?,?,?)",
                 (f["id"], u["id"], j.get("recipient_id"), q, time.time()))
    return jsonify(ok=True)


# ---------- Recipient ----------
@app.post("/api/requests")
def add_request():
    u, err = need("recipient")
    if err: return err
    j = request.json or {}
    db().execute("INSERT INTO requests(recipient_id,diet,qty,note,created) VALUES(?,?,?,?,?)",
                 (u["id"], j.get("diet", "any"), int(j.get("qty", 1)), j.get("note"), time.time()))
    return jsonify(ok=True)


@app.post("/api/match")
def match():
    u, err = need("recipient")
    if err: return err
    j = request.json or {}
    need_qty, diet = int(j.get("qty", 1)), j.get("diet", "any")
    got = db().execute("SELECT COALESCE(SUM(qty),0) s FROM allocations WHERE recipient_id=? AND created>?",
                       (u["id"], time.time() - 7 * 86400)).fetchone()["s"]
    fairness = 20 / (1 + got / 20)  # recipients served less recently get a boost
    rows = db().execute("""SELECT f.*,u.org,u.address,u.lat,u.lng FROM foods f JOIN users u ON u.id=f.donor_id
        WHERE f.remaining>0 AND f.expires_at>?""", (time.time() + 3600,)).fetchall()
    out = []
    for r in rows:
        if not diet_ok(r["diet"], diet): continue
        give = min(r["remaining"], need_qty)
        dist = km(u, r)
        hrs = (r["expires_at"] - time.time()) / 3600
        score = 40 * give / need_qty + 30 / (1 + dist) + 10 * min(hrs, 6) / 6 + fairness
        out.append({"food_id": r["id"], "donor": r["org"], "item": r["item"], "diet": r["diet"], "available": r["remaining"],
                    "allocate": give, "distance": dist, "hours_left": round(hrs, 1), "score": round(score, 1)})
    out.sort(key=lambda m: -m["score"])
    return jsonify(out[:10])


@app.post("/api/allocate")
def confirm():
    u, err = need("recipient")
    if err: return err
    j = request.json or {}
    aid = allocate(int(j["food_id"]), u["id"], int(j["qty"]))
    if not aid:
        return jsonify(error="Someone else just claimed that food or it is no longer safe to deliver. Run Find matches again."), 409
    return jsonify(ok=True, allocation_id=aid)


@app.get("/api/offers")
def offers():
    u, err = need("recipient", "donor")
    if err: return err
    col = "recipient_id" if u["role"] == "recipient" else "donor_id"
    rows = db().execute(f"""SELECT o.*,f.item,du.org donor,ru.org recipient FROM offers o JOIN foods f ON f.id=o.food_id
        JOIN users du ON du.id=o.donor_id JOIN users ru ON ru.id=o.recipient_id WHERE o.{col}=? ORDER BY o.id DESC""", (u["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/offers/<int:oid>/respond")
def respond(oid):
    u, err = need("recipient")
    if err: return err
    o = db().execute("SELECT * FROM offers WHERE id=? AND recipient_id=? AND status='pending'", (oid, u["id"])).fetchone()
    if not o:
        return jsonify(error="Offer not found."), 404
    if (request.json or {}).get("accept"):
        if not allocate(o["food_id"], u["id"], o["qty"]):
            db().execute("UPDATE offers SET status='expired' WHERE id=?", (oid,))
            return jsonify(error="That food is no longer available."), 409
        db().execute("UPDATE offers SET status='accepted' WHERE id=?", (oid,))
    else:
        db().execute("UPDATE offers SET status='declined' WHERE id=?", (oid,))
    return jsonify(ok=True)


# ---------- Deliveries ----------
@app.get("/api/allocations")
def allocations():
    u, err = need()
    if err: return err
    where, args = {"recipient": ("a.recipient_id=?", (u["id"],)),
                   "donor": ("f.donor_id=?", (u["id"],)),
                   "partner": ("(a.partner_id=? OR (a.partner_id IS NULL AND a.status IN ('Confirmed','Ready for Pickup')))", (u["id"],))}[u["role"]]
    rows = db().execute(f"""SELECT a.*,f.item,du.org donor,du.address pickup,ru.org recipient,ru.address dropoff FROM allocations a
        JOIN foods f ON f.id=a.food_id JOIN users du ON du.id=f.donor_id JOIN users ru ON ru.id=a.recipient_id
        WHERE {where} ORDER BY a.id DESC""", args).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/allocations/<int:aid>/claim")
def claim(aid):
    u, err = need("partner")
    if err: return err
    n = db().execute("UPDATE allocations SET partner_id=? WHERE id=? AND partner_id IS NULL", (u["id"], aid)).rowcount
    return jsonify(ok=True) if n else (jsonify(error="Another partner already took this job."), 409)


@app.post("/api/allocations/<int:aid>/status")
def set_status(aid):
    u, err = need("donor", "partner")
    if err: return err
    s = (request.json or {}).get("status")
    a = db().execute("SELECT a.*,f.donor_id FROM allocations a JOIN foods f ON f.id=a.food_id WHERE a.id=?", (aid,)).fetchone()
    if not a or (u["role"] == "donor" and a["donor_id"] != u["id"]) or (u["role"] == "partner" and a["partner_id"] != u["id"]):
        return jsonify(error="Not your delivery."), 403
    if s in ("Failed", "Cancelled"):
        # Dynamic reallocation: return the meals to the pool so another recipient can claim them.
        db().execute("UPDATE foods SET remaining=remaining+? WHERE id=?", (a["qty"], a["food_id"]))
        db().execute("UPDATE allocations SET status=?, partner_id=NULL WHERE id=?", (s, aid))
    elif s in STAGES:
        db().execute("UPDATE allocations SET status=? WHERE id=?", (s, aid))
    else:
        return jsonify(error="Unknown status."), 400
    return jsonify(ok=True)


@app.get("/api/stats")
def stats():
    d = db()
    one = lambda q, a=(): d.execute(q, a).fetchone()[0] or 0
    u = me()
    s = {"donated": one("SELECT SUM(qty) FROM foods"),
         "delivered": one("SELECT SUM(qty) FROM allocations WHERE status='Delivered'"),
         "failed": one("SELECT COUNT(*) FROM allocations WHERE status IN ('Failed','Cancelled')"),
         "recipients": one("SELECT COUNT(DISTINCT recipient_id) FROM allocations WHERE status='Delivered'"),
         "open_demand": one("SELECT SUM(qty) FROM requests WHERE status='open'")}
    s["success_rate"] = round(100 * s["delivered"] / s["donated"]) if s["donated"] else 0
    if u and u["role"] == "recipient":
        s["mine"] = one("SELECT SUM(qty) FROM allocations WHERE recipient_id=? AND status='Delivered'", (u["id"],))
    elif u and u["role"] == "donor":
        s["mine"] = one("SELECT SUM(a.qty) FROM allocations a JOIN foods f ON f.id=a.food_id WHERE f.donor_id=? AND a.status='Delivered'", (u["id"],))
    elif u:
        s["mine"] = one("SELECT COUNT(*) FROM allocations WHERE partner_id=? AND status='Delivered'", (u["id"],))
    return jsonify(s)


@app.get("/")
def index():
    return app.send_static_file("index.html")


init()
if __name__ == "__main__":
    app.run(debug=os.environ.get("DEBUG") == "1", host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
