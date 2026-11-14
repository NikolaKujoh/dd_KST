"""Jedan smoke test: python test_app.py"""
import os, tempfile
from datetime import datetime

os.environ["DD_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["DD_SECRET"] = "test"
import app as m


def client(un, approve_as=None, name=None):
    c = m.app.test_client()
    c.post("/register", data={"username": un, "password": "secret1"})
    c.post("/login", data={"username": un, "password": "secret1"})
    return c


def at(y, mo, d, h, mi):
    m.now = lambda: datetime(y, mo, d, h, mi, tzinfo=m.TZ)


def uid(un):
    with m.app.app_context():
        return m.db().execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]


def queue():
    with m.app.app_context():
        return [(r["display_name"], r["tier"]) for r in m.red("2026-11-14")]


def first_order_id(name):
    with m.app.app_context():
        return m.db().execute("SELECT o.id FROM porudzbine o JOIN users u ON u.id=o.user_id "
                              "WHERE u.display_name=? AND o.servirano=0 ORDER BY o.id", (name,)).fetchone()["id"]


at(2026, 11, 14, 18, 0)  # subota
admin = client("boss")
users = {n: client(n) for n in "abc"}
for n in "abc":
    admin.post(f"/admin/user/{uid(n)}/odobri", data={"display_name": n.upper()})
admin.post("/admin/prilog", data={"naziv": "Salata"})
admin.post("/admin/prilog", data={"naziv": "Hleb"})

# bez jela nema porudzbine
users["a"].post("/", data={"prilozi": "Salata"})
assert queue() == []
admin.post("/admin/jelo", data={"datum": "2026-11-14", "jelo": "Pasulj"})

# prozor: petak i subota 22:30 zatvoreno
at(2026, 11, 13, 18, 0); users["a"].post("/", data={}); assert queue() == []
at(2026, 11, 14, 22, 30); users["a"].post("/", data={}); assert queue() == []
at(2026, 11, 14, 16, 59); users["a"].post("/", data={}); assert queue() == []

# a, b, a(dodatna), c -> a, b, c, pa dodatna od a
at(2026, 11, 14, 18, 0)
for n in "abac":
    users[n].post("/", data={"prilozi": "Salata"})
assert queue() == [("A", 1), ("B", 1), ("C", 1), ("A", 3)], queue()

# neodobren korisnik ne moze
d = client("d"); d.post("/", data={}); assert len(queue()) == 4

# a jede prvu -> jeo 1; njegova dodatna ostaje nivo 3
admin.post(f"/admin/porudzbina/{first_order_id('A')}/servirano")
assert queue() == [("B", 1), ("C", 1), ("A", 3)], queue()

# b jede, pa b porucuje opet (jeo 1x, ali ima pravo): nivo 3 ispod svih
admin.post(f"/admin/porudzbina/{first_order_id('B')}/servirano")
users["b"].post("/", data={})
assert queue() == [("C", 1), ("A", 3), ("B", 3)], queue()

# a jede dodatnu -> 2 obroka -> blokiran
admin.post(f"/admin/porudzbina/{first_order_id('A')}/servirano")
with m.app.app_context():
    assert m.eaten(uid("a")) == 2
users["a"].post("/", data={})
assert [q[0] for q in queue()] == ["C", "B"], queue()

# izvestaj + odblokiranje
assert b"Blokirani (jeli 2 puta) (1)" in admin.get("/admin/izvestaj").data
admin.post(f"/admin/user/{uid('a')}/odblokiraj")
users["a"].post("/", data={})
assert ("A", 3) in queue(), queue()

# obican korisnik nema admin
assert users["b"].get("/admin").status_code == 403
print("OK")
