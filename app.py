import os, secrets, sqlite3
from datetime import datetime, timedelta, time
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, g, redirect, render_template, request, session, url_for, flash, abort
from werkzeug.security import check_password_hash, generate_password_hash

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("DD_DB", os.path.join(HERE, "dd.db"))
TZ = ZoneInfo("Europe/Belgrade")
OPEN, CLOSE = time(17, 0), time(22, 30)  # subota
MAX_OBROKA = 2

app = Flask(__name__)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def _secret():
    if os.environ.get("DD_SECRET"):
        return os.environ["DD_SECRET"]
    p = os.path.join(HERE, "secret.key")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
    with open(p) as f:
        return f.read().strip()


app.secret_key = _secret()


def now():  # testovi ovo menjaju
    return datetime.now(TZ)


def is_open(t):
    return t.weekday() == 5 and OPEN <= t.time() < CLOSE


def aktivna_subota(t):
    d = t.date()
    return d + timedelta((5 - d.weekday()) % 7)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close(_):
    if "db" in g:
        g.db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pass_hash TEXT NOT NULL,
  display_name TEXT, approved INTEGER NOT NULL DEFAULT 0, is_admin INTEGER NOT NULL DEFAULT 0,
  offset INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS prilozi(
  id INTEGER PRIMARY KEY, naziv TEXT UNIQUE NOT NULL, aktivan INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS subote(datum TEXT PRIMARY KEY, jelo TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS porudzbine(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), datum TEXT NOT NULL,
  prilozi TEXT NOT NULL DEFAULT '', servirano INTEGER NOT NULL DEFAULT 0, kreirano TEXT NOT NULL);
"""


def init_db():
    with sqlite3.connect(DB) as c:
        c.executescript(SCHEMA)


init_db()


# ---------- pomocno ----------
def me():
    if "uid" not in session:
        return None
    return db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()


@app.context_processor
def _user():
    return {"user": me()}


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not me():
            return redirect(url_for("login"))
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        u = me()
        if not u:
            return redirect(url_for("login"))
        if not u["is_admin"]:
            abort(403)
        return f(*a, **k)
    return w


EATEN = "(SELECT COUNT(*) FROM porudzbine p WHERE p.user_id=u.id AND p.servirano=1) - u.offset"


def eaten(uid):
    return db().execute(f"SELECT {EATEN} AS n FROM users u WHERE u.id=?", (uid,)).fetchone()["n"]


def red(datum):
    """Neservirane porudzbine u redosledu: nivo 1 (nikad jeo), 2 (jeo jednom+), 3 (dodatne)."""
    rows = db().execute(
        f"""SELECT o.*, u.display_name, {EATEN} AS eaten FROM porudzbine o
            JOIN users u ON u.id=o.user_id WHERE o.datum=? AND o.servirano=0 ORDER BY o.id""",
        (datum,)).fetchall()
    # prva porudzbina korisnika za taj datum se racuna i kad je vec servirana
    first = {r["user_id"]: r["id"] for r in db().execute(
        "SELECT user_id, MIN(id) AS id FROM porudzbine WHERE datum=? GROUP BY user_id", (datum,))}
    out = []
    for r in rows:
        r = dict(r)
        r["tier"] = 3 if first[r["user_id"]] != r["id"] else (1 if r["eaten"] == 0 else 2)
        out.append(r)
    return sorted(out, key=lambda r: (r["tier"], r["id"]))


def aktivni_prilozi():
    return db().execute("SELECT * FROM prilozi WHERE aktivan=1 ORDER BY naziv").fetchall()


# ---------- auth ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        un = request.form["username"].strip().lower()
        pw = request.form["password"]
        if not un or len(pw) < 6:
            flash("Nadimak je obavezan, lozinka mora imati bar 6 karaktera.")
        elif db().execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone():
            flash("Taj nadimak je zauzet.")
        else:
            # prvi nalog je admin i odmah odobren
            first = db().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
            db().execute(
                "INSERT INTO users(username, pass_hash, display_name, approved, is_admin) VALUES(?,?,?,?,?)",
                (un, generate_password_hash(pw), un if first else None, int(first), int(first)))
            db().commit()
            flash("Admin nalog napravljen." if first else "Nalog napravljen. Sačekaj da ga admin odobri.")
            return redirect(url_for("login"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE username=?",
                         (request.form["username"].strip().lower(),)).fetchone()
        if u and check_password_hash(u["pass_hash"], request.form["password"]):
            session["uid"] = u["id"]
            return redirect(url_for("index"))
        flash("Pogrešan nadimak ili lozinka.")
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- korisnik ----------
@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    u, t = me(), now()
    datum = aktivna_subota(t).isoformat()
    jelo = db().execute("SELECT jelo FROM subote WHERE datum=?", (datum,)).fetchone()
    n = eaten(u["id"])
    mozes = bool(u["approved"]) and n < MAX_OBROKA and is_open(t) and jelo is not None
    if request.method == "POST":
        if not mozes:
            flash("Trenutno ne možeš da poručiš.")
        else:
            izabrano = set(request.form.getlist("prilozi"))
            dozvoljeno = {p["naziv"] for p in aktivni_prilozi()}
            db().execute(
                "INSERT INTO porudzbine(user_id, datum, prilozi, kreirano) VALUES(?,?,?,?)",
                (u["id"], datum, ", ".join(sorted(izabrano & dozvoljeno)), t.isoformat()))
            db().commit()
            flash("Porudžbina poslata.")
        return redirect(url_for("index"))
    cekanje = red(datum)
    moje = db().execute("SELECT * FROM porudzbine WHERE user_id=? AND datum=? ORDER BY id",
                        (u["id"], datum)).fetchall()
    moje = [dict(m, pozicija=next((i + 1 for i, r in enumerate(cekanje) if r["id"] == m["id"]), None)) for m in moje]
    return render_template("index.html", u=u, datum=datum, jelo=jelo, prilozi=aktivni_prilozi(),
                           moje=moje, n=n, mozes=mozes, otvoreno=is_open(t), max=MAX_OBROKA)


# ---------- admin ----------
@app.route("/admin")
@admin_required
def admin():
    datum = request.args.get("d") or aktivna_subota(now()).isoformat()
    d = datetime.fromisoformat(datum).date()
    jelo = db().execute("SELECT jelo FROM subote WHERE datum=?", (datum,)).fetchone()
    cekanje = red(datum)
    servirane = db().execute(
        """SELECT o.*, u.display_name FROM porudzbine o JOIN users u ON u.id=o.user_id
           WHERE o.datum=? AND o.servirano=1 ORDER BY o.id""", (datum,)).fetchall()
    zbir = {}
    for r in cekanje + [dict(s) for s in servirane]:
        for p in filter(None, r["prilozi"].split(", ")):
            zbir[p] = zbir.get(p, 0) + 1
    users = db().execute(f"SELECT u.*, {EATEN} AS eaten FROM users u ORDER BY u.approved, u.username").fetchall()
    return render_template("admin.html", datum=datum, jelo=jelo["jelo"] if jelo else "",
                           prev=(d - timedelta(7)).isoformat(), next=(d + timedelta(7)).isoformat(),
                           cekanje=cekanje, servirane=servirane, zbir=sorted(zbir.items()),
                           users=users, prilozi=aktivni_prilozi(), max=MAX_OBROKA)


def back():
    return redirect(url_for("admin", d=request.form.get("datum")))


@app.post("/admin/jelo")
@admin_required
def set_jelo():
    d, jelo = request.form["datum"], request.form["jelo"].strip()
    if jelo:
        db().execute("INSERT INTO subote(datum, jelo) VALUES(?,?) ON CONFLICT(datum) DO UPDATE SET jelo=excluded.jelo", (d, jelo))
        db().commit()
    return back()


@app.post("/admin/prilog")
@admin_required
def add_prilog():
    naziv = request.form["naziv"].strip().replace(",", " ")
    if naziv:
        db().execute("INSERT INTO prilozi(naziv) VALUES(?) ON CONFLICT(naziv) DO UPDATE SET aktivan=1", (naziv,))
        db().commit()
    return back()


@app.post("/admin/prilog/<int:pid>/brisi")
@admin_required
def del_prilog(pid):
    db().execute("UPDATE prilozi SET aktivan=0 WHERE id=?", (pid,))
    db().commit()
    return back()


@app.post("/admin/user/<int:uid>/odobri")
@admin_required
def odobri(uid):
    ime = request.form["display_name"].strip()
    if ime:
        db().execute("UPDATE users SET display_name=?, approved=1 WHERE id=?", (ime, uid))
        db().commit()
    return back()


@app.post("/admin/user/<int:uid>/odblokiraj")
@admin_required
def odblokiraj(uid):
    # brojac obroka krece ispocetka: offset = svi dosadasnji serviranii obroci
    db().execute("UPDATE users SET offset=(SELECT COUNT(*) FROM porudzbine WHERE user_id=users.id AND servirano=1) WHERE id=?", (uid,))
    db().commit()
    return back()


@app.post("/admin/porudzbina/<int:oid>/servirano")
@admin_required
def servirano(oid):
    db().execute("UPDATE porudzbine SET servirano=1-servirano WHERE id=?", (oid,))
    db().commit()
    return back()


@app.route("/admin/izvestaj")
@admin_required
def izvestaj():
    users = db().execute(f"SELECT u.display_name, {EATEN} AS eaten FROM users u WHERE u.approved=1 ORDER BY u.display_name").fetchall()
    return render_template("izvestaj.html",
                           nikad=[u for u in users if u["eaten"] == 0],
                           jednom=[u for u in users if u["eaten"] == 1],
                           blokirani=[u for u in users if u["eaten"] >= MAX_OBROKA])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
