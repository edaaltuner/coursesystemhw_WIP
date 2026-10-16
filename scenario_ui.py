import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests")); sys.path.insert(0, str(ROOT))
import fake_tk
from fake_tk import BUTTONS, TREES, ANSWERS
import course_registration as cr
cr.DB_PATH = os.path.join(tempfile.mkdtemp(), "ui.db")
cr.init_db()

app = cr.App()
print("UI scale:", cr.UI_SCALE)
# real dialog / popup code paths (fake Tk returns no user input, so results are the cancel values)
assert app.confirm("Drop course", "Sure?", "Drop", danger=True) is False
assert app.ask_text("Reject", "why", "Reason") is None
assert app.ask_int("Capacity", "n", 3, 1, 10) is None
assert app.ask_fields("New", "msg", [("Title", ""), ("Message", "")], "Post") is None
d = cr.Dialog(app, "Capacity", "msg", fields=[("Value", "3")], validator=lambda v: None if v[0].isdigit() else "Enter a number.")
d.vars[0].set("x"); d.ok(); assert d.result is None
d.vars[0].set("5"); d.ok(); assert d.result == ["5"]
d2 = cr.Dialog(app, "T", "m"); d2.ok(); assert d2.result is True
d3 = cr.Dialog(app, "T", "m", kind="error"); d3.cancel(); assert d3.result is None
win, body, foot, sub = app.popup("Popup", "subtitle", 600, 400); sub.config(text="x")
# patch dialogs so the flow tests can answer them
app.confirm = lambda *a, **k: ANSWERS["yes"]
app.ask_text = lambda *a, **k: ANSWERS["string"]
app.ask_int = lambda *a, **k: ANSWERS["int"]
app.ask_fields = lambda *a, **k: ["Exam week", "Good luck!"]
banners = []
orig = app.banner
def rec(text, kind="info", nid=None, auto=0):
    banners.append((kind, text)); return orig(text, kind, nid, auto)
app.banner = rec

def login(uid, pw):
    BUTTONS.clear(); TREES.clear(); banners.clear()
    app.id_var.set(uid); app.pw_var.set(pw); app.do_login()
def btn(text):
    return [b for b in BUTTONS if b.text == text][-1]
def tree(*cols):
    return [t for t in TREES if all(c in t.cols for c in cols)][-1]
def nonempty(t): return len(t.get_children()) > 0

# wrong password stays on login
app.id_var.set("20260002"); app.pw_var.set("nope"); app.do_login(); assert app.user is None and app.login_err.get().startswith("Wrong")

# ---------- student: Mehmet ----------
login("20260002", "1234")
assert app.user["role"] == "student" and any("Welcome" in t for _, t in banners), banners
for key in cr.nav_keys("student"):
    app.show_page(key)
app.show_page("catalog")
cat = tree("code", "seats", "status")
print("catalog rows:", len(cat.get_children()))
# select CSE101 (open 2/3) and add
oid = str(app.conn.execute("SELECT id FROM offerings WHERE code='CSE101' AND term='2026-27 Fall'").fetchone()[0])
cat.selection_set(oid); cat.binds["<<TreeviewSelect>>"]()
btn("Add course").invoke()
print("after add:", banners[-1])
assert banners[-1][0] == "success"
# HUM101 is full -> waitlist prompt (answer yes)
hum = str(app.conn.execute("SELECT id FROM offerings WHERE code='HUM101' AND term='2026-27 Fall'").fetchone()[0])
cat.selection_set(hum); cat.binds["<<TreeviewSelect>>"]()
btn("Add course").invoke()
print("waitlist:", banners[-1]); assert "waitlist" in banners[-1][1].lower()
# prerequisite error shows via add attempt
cse201 = str(app.conn.execute("SELECT id FROM offerings WHERE code='CSE201'").fetchone()[0])
cat.selection_set(cse201); cat.binds["<<TreeviewSelect>>"]()
# double-click path also works
cat.binds["<Double-1>"](); print("dbl:", banners[-1]); assert banners[-1][0] == "error"
# switch term filter to Spring (trace fires refresh) and level filter
import fake_tk as ft
app.show_page("myreg")
reg = tree("code", "room", "status")
print("myreg rows:", [reg.item(i, "values") for i in reg.get_children()])
btn("Submit for advisor approval").invoke(); assert banners[-1][0] == "success", banners[-1]
reg = tree("code", "room", "status")
rows = [reg.item(i, "values")[5] for i in reg.get_children()]; print(rows); assert any("Pending" in r for r in rows) and any("Waitlist" in r for r in rows)
# drop the waitlist row
wl = [i for i in reg.get_children() if i.startswith("W")][0]
reg.selection_set(wl); btn("Drop course / leave waitlist").invoke()
assert not [i for i in tree("code", "room", "status").get_children() if i.startswith("W")]
for key in ("sched", "exams", "trans", "dash", "notif", "acct"): app.show_page(key)
btn("Mark all as read").invoke()
# account page password change
app.show_page("acct"); btn("Change password").invoke(); print("acct:", banners[-1]); assert banners[-1][0] == "error"

# sorting, empty state and shortcuts (student session still active)
login("20260002", "1234"); app.show_page("catalog"); cat = tree("code", "seats", "status")
before = [cat.item(i, "values")[1] for i in cat.get_children()]
app.sort_tree(cat, "name"); asc = [cat.item(i, "values")[1] for i in cat.get_children()]
assert asc == sorted(before, key=str.lower), (asc, before)
app.sort_tree(cat, "name"); desc = [cat.item(i, "values")[1] for i in cat.get_children()]
assert desc == asc[::-1]
app.sort_tree(cat, "seats"); app.sort_tree(cat, "cr")
app.empty_state(cat, "none"); cat.delete(*cat.get_children()); app.empty_state(cat, "none")
app.focus_search(); assert app.current == "catalog"

# ---------- advisor: A1001 ----------
login("A1001", "1234")
assert app.user["role"] == "advisor"
for key in cr.nav_keys("advisor"): app.show_page(key)
app.show_page("approvals")
ap = tree("sid", "sname", "code")
print("approvals:", [ap.item(i, "values") for i in ap.get_children()])
assert nonempty(ap)
first = ap.get_children()[0]; ap.selection_set(first); ap.binds["<<TreeviewSelect>>"]()
btn("Approve selected").invoke(); assert banners[-1][1] == "Approved."
app.show_page("advisees")
ad = tree("id", "cgpa", "state"); assert nonempty(ad)
ad.selection_set(ad.get_children()[0]); btn("View transcript").invoke()
# reject path (needs another pending item): Ayse submits two courses first
login("20260001", "1234")
app.show_page("catalog"); cat = tree("code", "seats", "status")
for code in ("CSE201", "AIT301"):
    o = str(app.conn.execute("SELECT id FROM offerings WHERE code=? AND term='2026-27 Fall'", (code,)).fetchone()[0])
    cat.selection_set(o); cat.binds["<<TreeviewSelect>>"](); btn("Add course").invoke()
    assert banners[-1][0] == "success", banners[-1]
app.show_page("myreg"); btn("Submit for advisor approval").invoke()
login("A1001", "1234"); app.show_page("approvals")
assert any("submitted" in t for _, t in banners), banners
ap = tree("sid", "sname", "code"); ap.selection_set(ap.get_children()[0]); ap.binds["<<TreeviewSelect>>"]()
btn("Approve all for this student").invoke(); print(banners[-1])
login("20260001", "1234"); print("student sees:", [t[:50] for k, t in banners])
assert any("approved" in t.lower() for _, t in banners)

# ---------- admin ----------
login("admin", "admin")
for key in cr.nav_keys("admin"): app.show_page(key)
app.show_page("offerings"); off = tree("code", "seats", "wait"); assert nonempty(off)
hum_row = [i for i in off.get_children() if off.item(i, "values")[0] == "HUM101"][0]
off.selection_set(hum_row)
ANSWERS["int"] = 5; btn("Change capacity").invoke(); print(banners[-1])
btn("View roster").invoke()
app.show_page("control"); btn("Close registration").invoke(); assert "closed" in banners[-1][1].lower()
btn("Open registration").invoke()
btn("Close term and post grades (demo)").invoke(); print(banners[-1]); assert banners[-1][0] == "success"
app.show_page("ann"); btn("New announcement").invoke(); assert "Announcement posted" in banners[-1][1]
# after close_term: poll and logout
app.poll(); app.show_login(); app.on_close()
print("UI ALL OK")
