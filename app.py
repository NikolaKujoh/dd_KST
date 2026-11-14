import hashlib, hmac, os, secrets, sqlite3, uuid
from datetime import date, datetime, timedelta, time
from functools import wraps
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("DD_DB", os.path.join(HERE, "dd.db"))
UPLOADS = os.environ.get("DD_UPLOADS", os.path.join(HERE, "uploads"))
TZ = ZoneInfo("Europe/Belgrade")
OPEN, CLOSE = time(17, 0), time(22, 30)  # subota
MAX_OBROKA = 2
SLIKE = {"jpg", "jpeg", "png", "webp", "heic", "heif"}
STAFF = ("server", "superadmin")
ULOGE = {"user": "Gost", "server": "DDejac", "superadmin": "Super admin"}

app = Flask(__name__)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=40 * 1024 * 1024)
os.makedirs(UPLOADS, exist_ok=True)


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


# ---------- vreme ----------
def now():  # testovi ovo menjaju
    return datetime.now(TZ)


def is_open(t):
    return t.weekday() == 5 and OPEN <= t.time() < CLOSE


def aktivna_subota(t):
    d = t.date()
    return d + timedelta((5 - d.weekday()) % 7)


def prosla_subota(t):  # poslednja subota <= danas (za racune posle smene)
    d = t.date()
    return d - timedelta((d.weekday() - 5) % 7)


def prozor(t):
    if is_open(t):
        return "Porudžbine su otvorene do 22:30"
    if t.weekday() == 5 and t.time() < OPEN:
        return "Porudžbine se otvaraju danas u 17:00"
    if t.weekday() == 5:
        return "Porudžbine su zatvorene za danas"
    return "Porudžbine se otvaraju u subotu u 17:00"


# ---------- baza ----------
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
  display_name TEXT, approved INTEGER NOT NULL DEFAULT 0, role TEXT NOT NULL DEFAULT 'user',
  offset INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS prilozi(
  id INTEGER PRIMARY KEY, naziv TEXT UNIQUE NOT NULL, aktivan INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS subote(datum TEXT PRIMARY KEY, jelo TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS porudzbine(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), datum TEXT NOT NULL,
  prilozi TEXT NOT NULL DEFAULT '', placeno INTEGER NOT NULL DEFAULT 0,
  servirano INTEGER NOT NULL DEFAULT 0, broj INTEGER, kreirano TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dolasci(user_id INTEGER NOT NULL, datum TEXT NOT NULL, PRIMARY KEY(user_id, datum));
CREATE TABLE IF NOT EXISTS ankete(
  id INTEGER PRIMARY KEY, pitanje TEXT NOT NULL, aktivna INTEGER NOT NULL DEFAULT 1,
  kreirao INTEGER NOT NULL, kreirano TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS opcije(id INTEGER PRIMARY KEY, anketa_id INTEGER NOT NULL, tekst TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS glasovi(
  anketa_id INTEGER NOT NULL, user_id INTEGER NOT NULL, opcija_id INTEGER NOT NULL,
  PRIMARY KEY(anketa_id, user_id));
CREATE TABLE IF NOT EXISTS recenzije(
  user_id INTEGER NOT NULL, datum TEXT NOT NULL, hrana INTEGER NOT NULL, usluga INTEGER NOT NULL,
  komentar TEXT NOT NULL DEFAULT '', kreirano TEXT NOT NULL, PRIMARY KEY(user_id, datum));
CREATE TABLE IF NOT EXISTS racuni(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, datum TEXT NOT NULL, fajl TEXT NOT NULL,
  napomena TEXT NOT NULL DEFAULT '', kreirano TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dogadjaji(id INTEGER PRIMARY KEY, aud TEXT NOT NULL, tekst TEXT, kreirano TEXT NOT NULL);
"""


def init_db():
    with sqlite3.connect(DB) as c:
        c.executescript(SCHEMA)
        cols = lambda t: {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
        # migracije sa prve verzije
        if "role" not in cols("users"):
            c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            c.execute("UPDATE users SET role='superadmin' WHERE is_admin=1")
        if "placeno" not in cols("porudzbine"):
            c.execute("ALTER TABLE porudzbine ADD COLUMN placeno INTEGER NOT NULL DEFAULT 0")
            c.execute("UPDATE porudzbine SET placeno=1 WHERE servirano=1")
        if "broj" not in cols("porudzbine"):
            c.execute("ALTER TABLE porudzbine ADD COLUMN broj INTEGER")
            c.execute("""UPDATE porudzbine SET broj=(SELECT COUNT(*) FROM porudzbine p
                         WHERE p.datum=porudzbine.datum AND p.id<=porudzbine.id)""")


init_db()


def emit(aud, tekst=None):
    """aud: 'all', 'staff', 'superadmin' ili id korisnika. tekst=None je tiho osvezavanje."""
    # ponytail: tabela raste zauvek; par stotina redova nedeljno je nebitno, cisti kad zatreba
    db().execute("INSERT INTO dogadjaji(aud, tekst, kreirano) VALUES(?,?,?)", (str(aud), tekst, now().isoformat()))


# ---------- ikonice (Lucide, ISC) ----------
ICONS = {
    "utensils": '<path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2"/><path d="M7 2v20"/><path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3Zm0 0v7"/>',
    "list": '<path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/>',
    "clipboard": '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "receipt": '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 17.5v-11"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "tv": '<rect width="20" height="15" x="2" y="7" rx="2"/><polyline points="17 2 12 7 7 2"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>',
    "cash": '<rect width="20" height="12" x="2" y="6" rx="2"/><circle cx="12" cy="12" r="2"/><path d="M6 12h.01M18 12h.01"/>',
    "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
    "left": '<path d="m15 18-6-6 6-6"/>',
    "right": '<path d="m9 18 6-6-6-6"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "calendar": '<rect width="18" height="18" x="3" y="4" rx="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/><path d="m9 16 2 2 4-4"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
}


def icon(name, size=20):
    return Markup(f'<svg class="i" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                  f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


# ?v= menja se pri svakom deploy-u, pa browser ne drzi stari CSS/JS
VER = int(max(os.path.getmtime(os.path.join(HERE, "static", f)) for f in ("app.css", "app.js")))
app.jinja_env.globals.update(icon=icon, ULOGE=ULOGE, MAX_OBROKA=MAX_OBROKA, VER=VER)


@app.template_filter()
def dan(s):
    d = date.fromisoformat(s) if isinstance(s, str) else s
    return f"{d.day}. {d.month}. {d.year}."


# ---------- korisnik / uloge ----------
def me():
    if "uid" not in g:
        g.uid = None
        if "uid" in session:
            g.uid = db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    return g.uid


def is_staff(u):
    return bool(u) and u["role"] in STAFF


TABS = {
    "user": [("index", "DD", "utensils"), ("ankete", "Ankete", "chart"), ("ocene", "Ocene", "star")],
    "server": [("red_view", "Red", "list"), ("meni", "Meni", "clipboard"), ("index", "DD", "utensils"),
               ("ankete", "Ankete", "chart"), ("ocene", "Ocene", "star"), ("racuni", "Računi", "receipt"),
               ("ljudi", "Ljudi", "users")],
    "superadmin": [("ljudi", "Ljudi", "users"), ("racuni", "Računi", "receipt"), ("red_view", "Red", "list"),
                   ("meni", "Meni", "clipboard"), ("index", "DD", "utensils"), ("ankete", "Ankete", "chart"),
                   ("ocene", "Ocene", "star")],
}


@app.context_processor
def _ctx():
    u = me()
    return {"user": u, "staff": is_staff(u), "tabs": TABS[u["role"]] if u and u["approved"] else [],
            "since": db().execute("SELECT COALESCE(MAX(id),0) FROM dogadjaji").fetchone()[0]}


def need(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = me()
            if not u:
                return redirect(url_for("login"))
            if not u["approved"] and f.__name__ != "index":
                return redirect(url_for("index"))
            if roles and u["role"] not in roles:
                abort(403)
            return f(*a, **k)
        return w
    return deco


def nazad(default="index"):
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return redirect(ref)
    return redirect(url_for(default))


def d_arg(default=None):
    s = request.values.get("d")
    if not s:
        return default or aktivna_subota(now()).isoformat()
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        abort(400)


def datenav(datum):
    d = date.fromisoformat(datum)
    return {"datum": datum, "prev": (d - timedelta(7)).isoformat(), "next": (d + timedelta(7)).isoformat()}


EATEN = "(SELECT COUNT(*) FROM porudzbine p WHERE p.user_id=u.id AND p.servirano=1) - u.offset"


def eaten(uid):
    return db().execute(f"SELECT {EATEN} FROM users u WHERE u.id=?", (uid,)).fetchone()[0]


def jelo_za(datum):
    r = db().execute("SELECT jelo FROM subote WHERE datum=?", (datum,)).fetchone()
    return r["jelo"] if r else None


def red(datum):
    """Neservirane porudzbine po redu: nivo 1 (nikad jeo), 2 (jeo jednom+), 3 (dodatne)."""
    rows = db().execute(
        f"""SELECT o.*, u.display_name, {EATEN} AS eaten FROM porudzbine o
            JOIN users u ON u.id=o.user_id WHERE o.datum=? AND o.servirano=0 ORDER BY o.id""",
        (datum,)).fetchall()
    # prva porudzbina korisnika za taj datum se racuna i kad je vec servirana
    first = {r[0]: r[1] for r in db().execute(
        "SELECT user_id, MIN(id) FROM porudzbine WHERE datum=? GROUP BY user_id", (datum,))}
    out = []
    for r in rows:
        r = dict(r)
        r["tier"] = 3 if first[r["user_id"]] != r["id"] else (1 if r["eaten"] == 0 else 2)
        out.append(r)
    return sorted(out, key=lambda r: (r["tier"], r["id"]))


def aktivni_prilozi():
    return db().execute("SELECT * FROM prilozi WHERE aktivan=1 ORDER BY naziv").fetchall()


def razlog(u, t, datum):
    """Zasto korisnik ne moze da poruci (None = moze)."""
    if not u["approved"]:
        return "Tvoj nalog čeka odobrenje."
    if eaten(u["id"]) >= MAX_OBROKA:
        return f"Jeo/la si {MAX_OBROKA} puta. Super admin može da te odblokira."
    if not is_open(t):
        return prozor(t) + "."
    if not jelo_za(datum):
        return "Jelo za ovu subotu još nije objavljeno."
    return None


# ---------- auth ----------
LANDING = {"superadmin": "ljudi", "server": "red_view"}


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        un = request.form["username"].strip().lower()
        pw = request.form["password"]
        if not un or len(un) > 40 or len(pw) < 6:
            flash("Nadimak je obavezan, a lozinka mora imati bar 6 karaktera.", "err")
        elif db().execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone():
            flash("Taj nadimak je zauzet.", "err")
        else:
            first = db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0  # prvi nalog = super admin
            db().execute("INSERT INTO users(username, pass_hash, display_name, approved, role) VALUES(?,?,?,?,?)",
                         (un, generate_password_hash(pw), un if first else None, int(first),
                          "superadmin" if first else "user"))
            if not first:
                emit("superadmin", f"Novi nalog čeka odobrenje: {un}")
            db().commit()
            flash("Super admin nalog je napravljen." if first else "Nalog je napravljen. Sačekaj odobrenje.", "ok")
            return redirect(url_for("login"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE username=?",
                         (request.form["username"].strip().lower(),)).fetchone()
        if u and check_password_hash(u["pass_hash"], request.form["password"]):
            session.clear()
            session["uid"] = u["id"]
            session.permanent = True
            return redirect(url_for(LANDING.get(u["role"], "index")))
        flash("Pogrešan nadimak ili lozinka.", "err")
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/poll")
def poll():
    u = me()
    if not u:
        return jsonify(error="login"), 401
    since = request.args.get("since", 0, type=int)
    auds = ["all", str(u["id"]), u["role"]] + (["staff"] if is_staff(u) else [])
    rows = db().execute(f"SELECT tekst FROM dogadjaji WHERE id>? AND aud IN ({','.join('?' * len(auds))}) ORDER BY id",
                        (since, *auds)).fetchall()
    last = db().execute("SELECT COALESCE(MAX(id),0) FROM dogadjaji").fetchone()[0]
    return jsonify(last=last, events=[r[0] for r in rows])


# ---------- gost ----------
@app.route("/")
@need()
def index():
    u, t = me(), now()
    datum = aktivna_subota(t).isoformat()
    moje = db().execute("SELECT * FROM porudzbine WHERE user_id=? AND datum=? ORDER BY id",
                        (u["id"], datum)).fetchall()
    return render_template(
        "dd.html", datum=datum, jelo=jelo_za(datum), prilozi=aktivni_prilozi(), moje=moje,
        razlog=razlog(u, t, datum), prozor=prozor(t), otvoreno=is_open(t), n=eaten(u["id"]),
        dolazim=db().execute("SELECT 1 FROM dolasci WHERE user_id=? AND datum=?", (u["id"], datum)).fetchone(),
        dolazi_n=db().execute("SELECT COUNT(*) FROM dolasci WHERE datum=?", (datum,)).fetchone()[0])


@app.post("/poruci")
@need()
def poruci():
    u, t = me(), now()
    datum = aktivna_subota(t).isoformat()
    r = razlog(u, t, datum)
    if r:
        flash(r, "err")
        return redirect(url_for("index"))
    izabrano = set(request.form.getlist("prilozi")) & {p["naziv"] for p in aktivni_prilozi()}
    broj = db().execute("SELECT COALESCE(MAX(broj),0)+1 FROM porudzbine WHERE datum=?", (datum,)).fetchone()[0]
    cur = db().execute("INSERT INTO porudzbine(user_id, datum, prilozi, broj, kreirano) VALUES(?,?,?,?,?)",
                       (u["id"], datum, ", ".join(sorted(izabrano)), broj, t.isoformat()))
    emit("staff", f"Nova porudžbina #{broj} · {u['display_name']}")
    db().commit()
    return redirect(url_for("status", oid=cur.lastrowid, novo=1))


@app.route("/p/<int:oid>")
@need()
def status(oid):
    u = me()
    o = db().execute("SELECT o.*, u.display_name FROM porudzbine o JOIN users u ON u.id=o.user_id WHERE o.id=?",
                     (oid,)).fetchone()
    if not o or (o["user_id"] != u["id"] and not is_staff(u)):
        abort(404)
    ispred = next((i for i, r in enumerate(red(o["datum"])) if r["id"] == oid), None)
    return render_template("status.html", o=o, ispred=ispred, jelo=jelo_za(o["datum"]), novo=request.args.get("novo"))


@app.post("/dolazim")
@need()
def dolazim():
    u, datum = me(), aktivna_subota(now()).isoformat()
    if db().execute("DELETE FROM dolasci WHERE user_id=? AND datum=?", (u["id"], datum)).rowcount == 0:
        db().execute("INSERT INTO dolasci(user_id, datum) VALUES(?,?)", (u["id"], datum))
    emit("all")
    db().commit()
    return nazad()


# ---------- ankete ----------
@app.route("/ankete")
@need()
def ankete():
    u = me()
    lista = []
    for a in db().execute("""SELECT a.*, (SELECT opcija_id FROM glasovi WHERE anketa_id=a.id AND user_id=?) AS moj
                             FROM ankete a ORDER BY a.aktivna DESC, a.id DESC LIMIT 30""", (u["id"],)).fetchall():
        opcije = db().execute("""SELECT o.*, (SELECT COUNT(*) FROM glasovi gl WHERE gl.opcija_id=o.id) AS n
                                 FROM opcije o WHERE o.anketa_id=? ORDER BY o.id""", (a["id"],)).fetchall()
        lista.append({"a": a, "opcije": opcije, "ukupno": sum(o["n"] for o in opcije)})
    return render_template("ankete.html", lista=lista)


@app.post("/ankete")
@need(*STAFF)
def nova_anketa():
    pitanje = request.form["pitanje"].strip()[:200]
    opcije = [l.strip()[:100] for l in request.form["opcije"].splitlines() if l.strip()][:8]
    if not pitanje or len(opcije) < 2:
        flash("Treba pitanje i bar 2 opcije (svaka u svom redu).", "err")
        return nazad("ankete")
    cur = db().execute("INSERT INTO ankete(pitanje, kreirao, kreirano) VALUES(?,?,?)", (pitanje, me()["id"], now().isoformat()))
    db().executemany("INSERT INTO opcije(anketa_id, tekst) VALUES(?,?)", [(cur.lastrowid, o) for o in opcije])
    emit("all", f"Nova anketa: {pitanje}")
    db().commit()
    return redirect(url_for("ankete"))


@app.post("/ankete/<int:aid>/glas")
@need()
def glasaj(aid):
    ok = db().execute("""SELECT 1 FROM opcije o JOIN ankete a ON a.id=o.anketa_id
                         WHERE o.id=? AND a.id=? AND a.aktivna=1""", (request.form.get("opcija", type=int), aid)).fetchone()
    if not ok:
        flash("Anketa je zatvorena.", "err")
    else:
        db().execute("INSERT OR REPLACE INTO glasovi(anketa_id, user_id, opcija_id) VALUES(?,?,?)",
                     (aid, me()["id"], request.form["opcija"]))
        emit("staff")
        db().commit()
    return nazad("ankete")


@app.post("/ankete/<int:aid>/zatvori")
@need(*STAFF)
def zatvori_anketu(aid):
    db().execute("UPDATE ankete SET aktivna=1-aktivna WHERE id=?", (aid,))
    emit("all")
    db().commit()
    return nazad("ankete")


# ---------- ocene ----------
@app.route("/ocene")
@need()
def ocene():
    u = me()
    datumi = db().execute("""SELECT DISTINCT o.datum, s.jelo FROM porudzbine o LEFT JOIN subote s ON s.datum=o.datum
                             WHERE o.user_id=? AND o.servirano=1 ORDER BY o.datum DESC LIMIT 12""", (u["id"],)).fetchall()
    moje = {r["datum"]: r for r in db().execute("SELECT * FROM recenzije WHERE user_id=?", (u["id"],))}
    pregled = []
    if is_staff(u):
        for r in db().execute("""SELECT r.datum, s.jelo, AVG(hrana) AS hrana, AVG(usluga) AS usluga, COUNT(*) AS n
                                 FROM recenzije r LEFT JOIN subote s ON s.datum=r.datum
                                 GROUP BY r.datum ORDER BY r.datum DESC LIMIT 12""").fetchall():
            kom = db().execute("""SELECT r.*, u.display_name FROM recenzije r JOIN users u ON u.id=r.user_id
                                  WHERE r.datum=? AND r.komentar!='' ORDER BY r.kreirano DESC""", (r["datum"],)).fetchall()
            pregled.append({"r": r, "kom": kom})
    return render_template("ocene.html", datumi=datumi, moje=moje, pregled=pregled)


@app.post("/ocene/<datum>")
@need()
def oceni(datum):
    u = me()
    hrana, usluga = request.form.get("hrana", type=int), request.form.get("usluga", type=int)
    jeo = db().execute("SELECT 1 FROM porudzbine WHERE user_id=? AND datum=? AND servirano=1", (u["id"], datum)).fetchone()
    if not jeo or hrana not in range(1, 6) or usluga not in range(1, 6):
        flash("Oceni hranu i uslugu od 1 do 5.", "err")
        return nazad("ocene")
    db().execute("INSERT OR REPLACE INTO recenzije(user_id, datum, hrana, usluga, komentar, kreirano) VALUES(?,?,?,?,?,?)",
                 (u["id"], datum, hrana, usluga, request.form.get("komentar", "").strip()[:500], now().isoformat()))
    emit("staff")
    db().commit()
    flash("Hvala na oceni!", "ok")
    return nazad("ocene")


# ---------- DDejci: red ----------
@app.route("/red")
@need(*STAFF)
def red_view():
    datum = d_arg()
    cekanje = red(datum)
    servirane = db().execute("""SELECT o.*, u.display_name FROM porudzbine o JOIN users u ON u.id=o.user_id
                                WHERE o.datum=? AND o.servirano=1 ORDER BY o.id DESC""", (datum,)).fetchall()
    zbir = {}
    for r in cekanje + [dict(s) for s in servirane]:
        for p in filter(None, r["prilozi"].split(", ")):
            zbir[p] = zbir.get(p, 0) + 1
    dolaze = db().execute("""SELECT u.display_name FROM dolasci d JOIN users u ON u.id=d.user_id
                             WHERE d.datum=? ORDER BY u.display_name""", (datum,)).fetchall()
    return render_template("red.html", nav=datenav(datum), jelo=jelo_za(datum), cekanje=cekanje,
                           servirane=servirane, zbir=sorted(zbir.items()), dolaze=dolaze,
                           tv=url_for("tv", token=tv_token(), _external=True))


def porudzbina(oid):
    o = db().execute("SELECT * FROM porudzbine WHERE id=?", (oid,)).fetchone()
    return o or abort(404)


@app.post("/p/<int:oid>/placeno")
@need(*STAFF)
def placeno(oid):
    o = porudzbina(oid)
    if o["servirano"]:
        flash("Prvo poništi serviranje.", "err")
        return nazad("red_view")
    db().execute("UPDATE porudzbine SET placeno=1-placeno WHERE id=?", (oid,))
    if not o["placeno"]:
        emit(o["user_id"], f"Plaćanje primljeno. Porudžbina #{o['broj']} se sprema.")
    emit("staff")
    db().commit()
    return nazad("red_view")


@app.post("/p/<int:oid>/servirano")
@need(*STAFF)
def servirano(oid):
    o = porudzbina(oid)
    if not o["placeno"]:
        flash("Prvo označi da je plaćeno.", "err")
        return nazad("red_view")
    db().execute("UPDATE porudzbine SET servirano=1-servirano WHERE id=?", (oid,))
    if not o["servirano"]:
        emit(o["user_id"], f"Porudžbina #{o['broj']} je spremna. Prijatno!")
    emit("staff")
    emit("all")  # TV i pozicije u redu
    db().commit()
    return nazad("red_view")


# ---------- DDejci: meni ----------
@app.route("/meni")
@need(*STAFF)
def meni():
    datum = d_arg()
    return render_template("meni.html", nav=datenav(datum), jelo=jelo_za(datum) or "", prilozi=aktivni_prilozi())


@app.post("/meni/jelo")
@need(*STAFF)
def set_jelo():
    datum, jelo = d_arg(), request.form["jelo"].strip()[:120]
    if jelo:
        db().execute("INSERT INTO subote(datum, jelo) VALUES(?,?) ON CONFLICT(datum) DO UPDATE SET jelo=excluded.jelo",
                     (datum, jelo))
        emit("all")
        db().commit()
        flash("Jelo sačuvano.", "ok")
    return nazad("meni")


@app.post("/meni/prilog")
@need(*STAFF)
def add_prilog():
    naziv = request.form["naziv"].strip().replace(",", " ")[:60]
    if naziv:
        db().execute("INSERT INTO prilozi(naziv) VALUES(?) ON CONFLICT(naziv) DO UPDATE SET aktivan=1", (naziv,))
        emit("all")
        db().commit()
    return nazad("meni")


@app.post("/meni/prilog/<int:pid>/brisi")
@need(*STAFF)
def del_prilog(pid):
    db().execute("UPDATE prilozi SET aktivan=0 WHERE id=?", (pid,))
    emit("all")
    db().commit()
    return nazad("meni")


# ---------- ljudi ----------
@app.route("/ljudi")
@need(*STAFF)
def ljudi():
    users = db().execute(f"SELECT u.*, {EATEN} AS eaten FROM users u ORDER BY u.display_name, u.username").fetchall()
    ok = [x for x in users if x["approved"]]
    return render_template("ljudi.html", pending=[x for x in users if not x["approved"]], users=ok,
                           nikad=[x for x in ok if x["eaten"] == 0], jednom=[x for x in ok if x["eaten"] == 1],
                           blokirani=[x for x in ok if x["eaten"] >= MAX_OBROKA])


def uloga_iz_forme():
    r = request.form.get("role", "user")
    return r if r in ULOGE else abort(400)


@app.post("/ljudi/<int:uid>/odobri")
@need("superadmin")
def odobri(uid):
    ime = request.form["display_name"].strip()[:40]
    if ime:
        db().execute("UPDATE users SET display_name=?, role=?, approved=1 WHERE id=?", (ime, uloga_iz_forme(), uid))
        emit(uid, "Nalog ti je odobren. Dobro došao/la na DD!")
        db().commit()
    return nazad("ljudi")


@app.post("/ljudi/<int:uid>/odbij")
@need("superadmin")
def odbij(uid):
    db().execute("DELETE FROM users WHERE id=? AND approved=0", (uid,))
    db().commit()
    return nazad("ljudi")


@app.post("/ljudi/<int:uid>/izmeni")
@need("superadmin")
def izmeni(uid):
    if uid == me()["id"]:
        flash("Svoju ulogu ne možeš da menjaš.", "err")
        return nazad("ljudi")
    ime = request.form["display_name"].strip()[:40]
    db().execute("UPDATE users SET display_name=COALESCE(NULLIF(?,''), display_name), role=? WHERE id=?",
                 (ime, uloga_iz_forme(), uid))
    db().commit()
    flash("Sačuvano.", "ok")
    return nazad("ljudi")


@app.post("/ljudi/<int:uid>/odblokiraj")
@need("superadmin")
def odblokiraj(uid):
    # brojac obroka krece ispocetka
    db().execute("UPDATE users SET offset=(SELECT COUNT(*) FROM porudzbine WHERE user_id=users.id AND servirano=1) WHERE id=?", (uid,))
    emit(uid, "Odblokiran/a si. Možeš ponovo da poručuješ.")
    db().commit()
    return nazad("ljudi")


@app.post("/ljudi/<int:uid>/lozinka")
@need("superadmin")
def nova_lozinka(uid):
    pw = request.form["password"]
    if len(pw) < 6:
        flash("Lozinka mora imati bar 6 karaktera.", "err")
    else:
        db().execute("UPDATE users SET pass_hash=? WHERE id=?", (generate_password_hash(pw), uid))
        db().commit()
        flash("Lozinka promenjena.", "ok")
    return nazad("ljudi")


# ---------- racuni ----------
@app.route("/racuni")
@need(*STAFF)
def racuni():
    u = me()
    q = "SELECT r.*, u.display_name FROM racuni r JOIN users u ON u.id=r.user_id"
    rows = (db().execute(q + " ORDER BY r.datum DESC, r.id DESC LIMIT 300").fetchall() if u["role"] == "superadmin"
            else db().execute(q + " WHERE r.user_id=? ORDER BY r.datum DESC, r.id DESC LIMIT 60", (u["id"],)).fetchall())
    grupe = {}
    for r in rows:
        grupe.setdefault(r["datum"], []).append(r)
    return render_template("racuni.html", grupe=grupe, danas=prosla_subota(now()).isoformat())


@app.post("/racuni")
@need(*STAFF)
def upload_racun():
    u, datum = me(), d_arg(prosla_subota(now()).isoformat())
    napomena = request.form.get("napomena", "").strip()[:200]
    n = 0
    for f in request.files.getlist("slike"):
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in (f.filename or "") else ""
        if ext not in SLIKE:
            continue
        ime = f"{uuid.uuid4().hex}.{ext}"
        f.save(os.path.join(UPLOADS, ime))
        db().execute("INSERT INTO racuni(user_id, datum, fajl, napomena, kreirano) VALUES(?,?,?,?,?)",
                     (u["id"], datum, ime, napomena, now().isoformat()))
        n += 1
    if n:
        emit("superadmin", f"Novi račun ({n}) · {u['display_name']} · {dan(datum)}")
        db().commit()
        flash(f"Otpremljeno: {n}.", "ok")
    else:
        flash("Izaberi sliku (jpg, png, webp, heic).", "err")
    return redirect(url_for("racuni"))


@app.route("/racuni/<int:rid>/slika")
@need(*STAFF)
def slika_racuna(rid):
    r = db().execute("SELECT * FROM racuni WHERE id=?", (rid,)).fetchone()
    if not r or (me()["role"] != "superadmin" and r["user_id"] != me()["id"]):
        abort(404)
    resp = send_from_directory(UPLOADS, r["fajl"])
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ---------- TV ----------
def tv_token():
    return hmac.new(app.secret_key.encode(), b"tv", hashlib.sha256).hexdigest()[:24]


@app.route("/tv/<token>")
def tv(token):
    if not hmac.compare_digest(token, tv_token()):
        abort(404)
    datum = aktivna_subota(now()).isoformat()
    cekanje = red(datum)
    spremno = db().execute("""SELECT o.broj, u.display_name FROM porudzbine o JOIN users u ON u.id=o.user_id
                              WHERE o.datum=? AND o.servirano=1 ORDER BY o.id DESC LIMIT 8""", (datum,)).fetchall()
    return render_template("tv.html", datum=datum, jelo=jelo_za(datum), spremno=spremno,
                           na_redu=[r for r in cekanje if not r["placeno"]],
                           priprema=[r for r in cekanje if r["placeno"]])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
