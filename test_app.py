"""Smoke test: python test_app.py"""
import io, os, sqlite3, tempfile
from datetime import datetime

tmp = tempfile.mkdtemp()
os.environ.update(DD_DB=os.path.join(tmp, "t.db"), DD_UPLOADS=os.path.join(tmp, "up"), DD_SECRET="test")

# stara baza iz prve verzije -> migracija
with sqlite3.connect(os.environ["DD_DB"]) as c:
    c.executescript("""
    CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pass_hash TEXT NOT NULL,
      display_name TEXT, approved INTEGER NOT NULL DEFAULT 0, is_admin INTEGER NOT NULL DEFAULT 0,
      offset INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE porudzbine(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, datum TEXT NOT NULL,
      prilozi TEXT NOT NULL DEFAULT '', servirano INTEGER NOT NULL DEFAULT 0, kreirano TEXT NOT NULL);""")
from werkzeug.security import generate_password_hash
with sqlite3.connect(os.environ["DD_DB"]) as c:
    c.execute("INSERT INTO users(username, pass_hash, display_name, approved, is_admin) VALUES('boss',?,'Boss',1,1)",
              (generate_password_hash("secret1"),))

import app as m

assert m.db.__name__  # import ok
with sqlite3.connect(os.environ["DD_DB"]) as c:
    assert c.execute("SELECT role FROM users WHERE username='boss'").fetchone()[0] == "superadmin"


def at(y, mo, d, h, mi):
    m.now = lambda: datetime(y, mo, d, h, mi, tzinfo=m.TZ)


def login(un):
    c = m.app.test_client()
    c.post("/login", data={"username": un, "password": "secret1"})
    return c


def client(un):
    c = m.app.test_client()
    c.post("/register", data={"username": un, "password": "secret1"})
    return login(un)


def q(sql, *a):
    with m.app.app_context():
        return [tuple(r) for r in m.db().execute(sql, a)]


uid = lambda un: q("SELECT id FROM users WHERE username=?", un)[0][0]
D = "2026-11-14"


def queue():
    with m.app.app_context():
        return [(r["display_name"], r["tier"]) for r in m.red(D)]


def oid(name, served=0):
    return q("""SELECT o.id FROM porudzbine o JOIN users u ON u.id=o.user_id
                WHERE u.display_name=? AND o.servirano=? ORDER BY o.id""", name, served)[0][0]


at(2026, 11, 14, 18, 0)
boss = login("boss")
users = {n: client(n) for n in "abcs"}
assert users["a"].get("/ankete").status_code == 302  # neodobren -> nazad na DD
for n in "abc":
    boss.post(f"/ljudi/{uid(n)}/odobri", data={"display_name": n.upper(), "role": "user"})
boss.post(f"/ljudi/{uid('s')}/odobri", data={"display_name": "S", "role": "server"})
srv = users["s"]

# uloge
assert users["a"].get("/red").status_code == 403
assert srv.get("/red").status_code == 200
assert srv.post(f"/ljudi/{uid('a')}/odblokiraj").status_code == 403  # samo super admin
assert boss.post(f"/ljudi/{uid('boss')}/izmeni", data={"role": "user", "display_name": ""}).status_code == 302
assert q("SELECT role FROM users WHERE username='boss'")[0][0] == "superadmin"

srv.post("/meni/prilog", data={"naziv": "Salata"})
users["a"].post("/poruci", data={"prilozi": "Salata"})
assert queue() == []  # nema jela
srv.post("/meni/jelo?d=" + D, data={"jelo": "Pasulj"})

for t in [(2026, 11, 13, 18, 0), (2026, 11, 14, 22, 30), (2026, 11, 14, 16, 59)]:
    at(*t); users["a"].post("/poruci", data={}); assert queue() == []
at(2026, 11, 14, 18, 0)

r = users["a"].post("/poruci", data={"prilozi": ["Salata", "Kavijar"]})
assert "/p/" in r.headers["Location"] and "novo=1" in r.headers["Location"]
for n in "bac":
    users[n].post("/poruci", data={})
assert queue() == [("A", 1), ("B", 1), ("C", 1), ("A", 3)], queue()
assert q("SELECT prilozi, broj FROM porudzbine ORDER BY id")[0] == ("Salata", 1)

# status: vlasnik i staff vide, drugi ne
a1 = oid("A")
assert users["a"].get(f"/p/{a1}").status_code == 200
assert users["b"].get(f"/p/{a1}").status_code == 404
assert srv.get(f"/p/{a1}").status_code == 200

# obavestenje za staff o novoj porudzbini
ev = srv.get("/api/poll?since=0").get_json()["events"]
assert any("Nova porudžbina #1" in (e or "") for e in ev), ev

# servirano tek posle placeno
srv.post(f"/p/{a1}/servirano"); assert q("SELECT servirano FROM porudzbine WHERE id=?", a1)[0][0] == 0
srv.post(f"/p/{a1}/placeno"); srv.post(f"/p/{a1}/servirano")
assert queue() == [("B", 1), ("C", 1), ("A", 3)], queue()
ev = users["a"].get("/api/poll?since=0").get_json()["events"]
assert any("spremna" in (e or "") for e in ev) and not any("Nova porudžbina" in (e or "") for e in ev), ev

# b jede, poruci opet -> nivo 3 ispod svih
b1 = oid("B"); srv.post(f"/p/{b1}/placeno"); srv.post(f"/p/{b1}/servirano")
users["b"].post("/poruci", data={})
assert queue() == [("C", 1), ("A", 3), ("B", 3)], queue()

# a jede dodatnu -> 2 obroka -> blokiran
a2 = oid("A"); srv.post(f"/p/{a2}/placeno"); srv.post(f"/p/{a2}/servirano")
users["a"].post("/poruci", data={})
assert [x[0] for x in queue()] == ["C", "B"], queue()
assert "Blokirani" in boss.get("/ljudi").get_data(as_text=True)
boss.post(f"/ljudi/{uid('a')}/odblokiraj")
users["a"].post("/poruci", data={})
assert ("A", 3) in queue()

# RSVP toggle
users["c"].post("/dolazim"); assert len(q("SELECT * FROM dolasci")) == 1
users["c"].post("/dolazim"); assert len(q("SELECT * FROM dolasci")) == 0

# ankete
users["a"].post("/ankete", data={"pitanje": "X?", "opcije": "1\n2"}); assert not q("SELECT * FROM ankete")
srv.post("/ankete", data={"pitanje": "Šta sledeće?", "opcije": "Sarma\nMusaka\n"})
op = q("SELECT id FROM opcije ORDER BY id")
users["a"].post("/ankete/1/glas", data={"opcija": op[0][0]})
users["a"].post("/ankete/1/glas", data={"opcija": op[1][0]})  # promena glasa
assert q("SELECT opcija_id FROM glasovi") == [(op[1][0],)]
srv.post("/ankete/1/zatvori"); users["b"].post("/ankete/1/glas", data={"opcija": op[0][0]})
assert len(q("SELECT * FROM glasovi")) == 1

# ocene samo ako si jeo
users["c"].post(f"/ocene/{D}", data={"hrana": 5, "usluga": 5}); assert not q("SELECT * FROM recenzije")
users["a"].post(f"/ocene/{D}", data={"hrana": 4, "usluga": 5, "komentar": "top"})
users["a"].post(f"/ocene/{D}", data={"hrana": 9, "usluga": 5})
assert q("SELECT hrana, usluga FROM recenzije") == [(4, 5)]
assert "top" in srv.get("/ocene").get_data(as_text=True)

# racuni: server otprema, super admin vidi, gost ne
srv.post("/racuni", data={"slike": (io.BytesIO(b"\xff\xd8fake"), "r.jpg"), "napomena": "Maxi"},
         content_type="multipart/form-data")
srv.post("/racuni", data={"slike": (io.BytesIO(b"<script>"), "x.html")}, content_type="multipart/form-data")
assert len(q("SELECT * FROM racuni")) == 1
assert boss.get("/racuni/1/slika").status_code == 200
assert users["a"].get("/racuni/1/slika").status_code == 403

# TV
assert m.app.test_client().get("/tv/pogresno").status_code == 404
with m.app.test_request_context():
    tok = m.tv_token()
tv = m.app.test_client().get(f"/tv/{tok}").get_data(as_text=True)
assert "Pasulj" in tv and "#3" in tv

# sve stranice se renderuju
for c, urls in [(boss, ["/", "/ljudi", "/racuni", "/red", "/meni", "/ankete", "/ocene"]), (users["a"], ["/", "/ankete", "/ocene"])]:
    for u in urls:
        assert c.get(u).status_code == 200, u
print("OK")
