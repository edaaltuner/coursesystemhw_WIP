import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests")); sys.path.insert(0, str(ROOT))
import fake_tk
from fake_tk import BUTTONS, TREES, W
import course_registration as cr
cr.DB_PATH = os.path.join(tempfile.mkdtemp(), "s.db"); cr.init_db()
app = cr.App()
app.confirm = lambda *a, **k: True
def login(u, pw="1234"):
    app.show_login(); BUTTONS.clear(); TREES.clear(); app.id_var.set(u); app.pw_var.set(pw); app.do_login()
def tree(*cols): return [t for t in TREES if all(c in t.cols for c in cols)][-1]
def btn(text): return [b for b in BUTTONS if b.text == text][-1]
class Ev:
    def __init__(self, delta=0, num="??", x=0, y=0): self.delta, self.num, self.x_root, self.y_root = delta, num, x, y

# ---- wheel units ----
assert app.wheel_units(Ev(-120)) == 3 and app.wheel_units(Ev(120)) == -3
assert app.wheel_units(Ev(num=4)) == -3 and app.wheel_units(Ev(num=5)) == 3
assert app.wheel_units(Ev(-30)) == 1 and app.wheel_units(Ev(30)) == -1 and app.wheel_units(Ev(0)) == 0
cr.sys.platform = "darwin"; assert app.wheel_units(Ev(-2)) == 2 and app.wheel_units(Ev(1)) == -1; cr.sys.platform = "linux"

# ---- pages are scrollable containers ----
login("20260002")
for k in cr.nav_keys("student"):
    assert isinstance(app.pages[k][0], cr.ScrollFrame), k
sf = app.pages["catalog"][0]
sf._layout(); sf._set_bar("0.1", "0.5"); assert sf._bar_on; sf._set_bar("0.0", "1.0"); assert not sf._bar_on
calls = []; sf.canvas.yview_scroll = lambda n, w: calls.append((n, w))
sf._need = 5000; assert sf.can_scroll(); sf.scroll(3); assert calls == [(3, "units")]
sf._need = 10; sf.scroll(3); assert len(calls) == 1                      # content fits -> no scrolling
sf._need = 5000
app.show_page("catalog"); cat = tree("code", "seats", "status")

# pointer over a table that can scroll -> the table scrolls, not the page
cat._yv = (0.2, 0.6); app.winfo_containing = lambda x, y: cat
app.on_wheel(Ev(-120)); assert cat.scrolled[-1] == (3, "units") and len(calls) == 1
# table already at its end -> the page takes over
cat._yv = (0.5, 1.0); app.on_wheel(Ev(-120)); assert len(calls) == 2 and calls[-1] == (3, "units")
cat._yv = (0.0, 0.5); app.on_wheel(Ev(120)); assert len(calls) == 3 and calls[-1] == (-3, "units")      # at the top, wheel up
# pointer over plain page content -> page scrolls
app.winfo_containing = lambda x, y: sf.body; app.on_wheel(Ev(-120)); assert len(calls) == 4
# pointer over the sidebar / outside the content -> nothing
app.winfo_containing = lambda x, y: app.root_frame; app.on_wheel(Ev(-120)); assert len(calls) == 4
# listbox popups keep their own scrolling
Listbox = type("Listbox", (W,), {}); app.winfo_containing = lambda x, y: Listbox(); app.on_wheel(Ev(-120)); assert len(calls) == 4
# errors never escape
def boom(x, y): raise KeyError("popdown")
app.winfo_containing = boom; app.on_wheel(Ev(-120))
# signed out -> ignored
app.show_login(); app.on_wheel(Ev(-120))
print("SCROLL OK")

# ---- advisor automation page through the UI ----
login("A1001")
assert "autocap" in cr.nav_keys("advisor")
app.show_page("autocap"); t = tree("rule", "by")
cse = str(app.conn.execute("SELECT id FROM offerings WHERE code='CSE101' AND term='2026-27 Fall'").fetchone()[0])
assert cse in t.items
captured = {}
def fake_fields(*a, **k):
    captured["validator"] = a[4]; captured["fields"] = a[2]; return ["2", "2", "8"]
app.ask_fields = fake_fields
t.selection_set(cse); btn("Set rule").invoke()
v = captured["validator"]
assert v(["x", "1", "9"]).startswith("Please") and "at least 1" in v(["0", "1", "9"]) and "above" in v(["2", "2", "1"]) and v(["2", "2", "50"]) is None
assert cr.get_rule(app.conn, int(cse))["max_capacity"] == 8 and "At 2+" in t.item(cse, "values")[4]
btn("Turn on / off").invoke(); assert cr.get_rule(app.conn, int(cse))["enabled"] == 0 and "[off]" in t.item(cse, "values")[4]
btn("Remove rule").invoke(); assert cr.get_rule(app.conn, int(cse)) is None
btn("Remove rule").invoke()                                              # nothing to remove -> friendly message, no crash
login("admin", "admin"); app.show_page("offerings"); off = tree("code", "auto"); assert "-" in [off.item(i, "values")[6] for i in off.get_children()]
print("AUTOCAP UI OK")
