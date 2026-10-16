import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests")); sys.path.insert(0, str(ROOT))
import fake_tk
import course_registration as cr

def fresh():
    cr.DB_PATH = os.path.join(tempfile.mkdtemp(), "a.db"); cr.init_db(); return cr.connect()
c = fresh()
def oid(code, term="2026-27 Fall"): return c.execute("SELECT id FROM offerings WHERE code=? AND term=?", (code, term)).fetchone()[0]
def cap(o): return cr.offering(c, o)["capacity"]
E, M, S, D, A = "20260003", "20260002", "20260007", "20260008", "20260001"
adv = "A1001"
cse = oid("CSE101")
assert cr.register(c, E, cse)[0] == "ok" and cr.seats_taken(c, cse) == 3          # 3/3, full
# validation
assert cr.set_rule(c, adv, cse, 0, 2, 5)[0] is False
assert cr.set_rule(c, adv, cse, 2, 0, 5)[0] is False
assert cr.set_rule(c, adv, cse, 2, 2, 3)[0] is False                              # max must exceed capacity
assert cr.get_rule(c, cse) is None and cr.rule_text(None) == "-"
ok, msg = cr.set_rule(c, adv, cse, 2, 2, 5); print(msg); assert ok and "fired" not in msg   # nobody waiting yet
print(cr.rule_text(cr.get_rule(c, cse)))
# first waiter: below threshold, no growth
assert cr.register(c, M, cse)[0] == "full"
print(cr.join_waitlist(c, M, cse)); assert cap(cse) == 3
# second waiter reaches the threshold -> +2 seats, both enrolled
assert cr.register(c, S, cse)[0] == "full"
note = cr.join_waitlist(c, S, cse); print(note)
assert "added automatically" in note and cap(cse) == 5 and cr.seats_taken(c, cse) == 5 and cr.waitlist_len(c, cse) == 0
assert cr.enrollment_status(c, M, cse) == "Draft" and cr.enrollment_status(c, S, cse) == "Draft"
n_adv = [n["message"] for n in cr.unseen_notifications(c, adv) if "Auto-capacity" in n["message"]]; print(n_adv); assert len(n_adv) == 1
assert any(n["kind"] == "success" and "waitlist" in n["message"] for n in cr.unseen_notifications(c, M))
# third waiter: threshold 2 not reached again
assert cr.register(c, D, cse)[0] == "full"
assert cr.join_waitlist(c, D, cse).startswith("You are number 1") and cap(cse) == 5
# re-saving with a lower threshold fires immediately, respects the maximum
ok, msg = cr.set_rule(c, adv, cse, 1, 10, 6); print(msg); assert ok and "fired" in msg
assert cap(cse) == 6 and cr.enrollment_status(c, D, cse) == "Draft"
# toggle off / on
ok, msg = cr.toggle_rule(c, adv, cse); assert ok and cr.get_rule(c, cse)["enabled"] == 0 and "off" in msg
assert cr.apply_capacity_rule(c, cse) is None
ok, msg = cr.toggle_rule(c, adv, cse); assert cr.get_rule(c, cse)["enabled"] == 1
assert cr.toggle_rule(c, adv, oid("MAT101"))[0] is False
assert cr.delete_rule(c, cse) == 1 and cr.get_rule(c, cse) is None
# maximum reached while students are still waiting
c = fresh()
hum = oid("HUM101")                                    # 2/2 (Zeynep, Burak)
for sid in (S, D): cr.register(c, sid, hum); cr.join_waitlist(c, sid, hum)
assert cr.waitlist_len(c, hum) == 2
ok, msg = cr.set_rule(c, adv, hum, 1, 1, 3); print(msg); assert ok and cap(hum) == 3 and cr.waitlist_len(c, hum) == 1
assert any("maximum of 3" in n["message"] for n in cr.unseen_notifications(c, adv))
# ineligible waiters never trigger growth
c = fresh()
hum = oid("HUM101")
cr.join_waitlist(c, A, hum)                            # Ayse already passed HUM101 -> not eligible
assert cr.eligible_waiters(c, hum) == 0
ok, msg = cr.set_rule(c, adv, hum, 1, 2, 9); assert ok and cap(hum) == 2 and "fired" not in msg
print("AUTOCAP LOGIC OK")
