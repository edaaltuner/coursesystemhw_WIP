import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests")); sys.path.insert(0, str(ROOT))
import fake_tk
from fake_tk import BUTTONS, TREES
import course_registration as cr
cr.DB_PATH = os.path.join(tempfile.mkdtemp(), "b.db"); cr.init_db()
app = cr.App(); got = []
app.confirm = lambda *a, **k: True
orig = app.banner
app.banner = lambda t, k="info", nid=None, auto=0: (got.append((k, t)), orig(t, k, nid, auto))[1]
def login(u):
    app.show_login(); BUTTONS.clear(); TREES.clear(); got.clear(); app.show_login(); app.id_var.set(u); app.pw_var.set("1234"); app.do_login()
hum = app.conn.execute("SELECT id FROM offerings WHERE code='HUM101' AND term='2026-27 Fall'").fetchone()[0]
login("20260002"); app.show_page("catalog")
t = [x for x in TREES if "seats" in x.cols][-1]; t.selection_set(str(hum)); t.binds["<<TreeviewSelect>>"]()
[b for b in BUTTONS if b.text == "Add course"][-1].invoke()           # full -> asks yes -> waitlist
login("20260006"); app.show_page("myreg")                              # Burak drops HUM101
r = [x for x in TREES if "room" in x.cols and "status" in x.cols][-1]; r.selection_set(f"R{hum}")
[b for b in BUTTONS if b.text == "Drop course / leave waitlist"][-1].invoke()
login("20260002")                                                      # Mehmet signs in again
print(got)
assert any(k == "success" and "moved from the waitlist into HUM101" in t for k, t in got)
print("BANNER OK")
