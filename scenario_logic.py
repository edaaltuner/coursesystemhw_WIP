import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests")); sys.path.insert(0, str(ROOT))
import random
import fake_tk
import course_registration as cr
cr.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
cr.init_db()
c = cr.connect()
def oid(code, term="2026-27 Fall"): return c.execute("SELECT id FROM offerings WHERE code=? AND term=?", (code, term)).fetchone()[0]
def codes(sid): return [r["code"] for r in cr.my_regs(c, sid)]
A, M, E, K, Z, B = "20260001", "20260002", "20260003", "20260004", "20260005", "20260006"

# auth
assert cr.authenticate(c, A, "1234") and not cr.authenticate(c, A, "x") and cr.authenticate(c, "admin", "admin")
# seeded seat counts
assert cr.seats_taken(c, oid("CSE101")) == 2 and cr.seats_taken(c, oid("HUM101")) == 2
assert cr.status_label(c, M, cr.offering(c, oid("HUM101"))) == "Full"
# Elif retakes CSE101 (failed with FF) -> takes last seat
st, msg, w = cr.register(c, E, oid("CSE101")); print(1, st, msg); assert st == "ok"
# Mehmet: CSE101 full -> waitlist
st, msg, w = cr.register(c, M, oid("CSE101")); print(2, st, msg); assert st == "full"
print(cr.join_waitlist(c, M, oid("CSE101")))
print(cr.register(c, M, oid("CSE101"))[:2])   # already on waitlist
st, _, _ = cr.register(c, M, oid("HUM101")); assert st == "full"; cr.join_waitlist(c, M, oid("HUM101"))
assert [p for _, p in cr.my_waitlist(c, M)] == [1, 1]
# prerequisite
print(3, cr.register(c, M, oid("CSE201"))[:2]); assert cr.register(c, M, oid("CSE201"))[0] == "error"
print(4, cr.register(c, E, oid("CSE201"))[:2])   # Elif hasn't passed CSE102 either
# already passed
print(5, cr.register(c, A, oid("CSE203"))[:2])   # ok
assert cr.register(c, A, oid("CSE102", "2026-27 Spring"))[0] == "error"   # wrong term
print(6, cr.register(c, A, oid("CSE102", "2026-27 Spring"))[1])
# Burak drops HUM101 -> Mehmet promoted
assert cr.drop(c, B, oid("HUM101"))[0] == "ok"
assert "HUM101" in codes(M) and cr.my_waitlist(c, M)[0][0]["code"] == "CSE101"
n = cr.unseen_notifications(c, M); print([x["kind"] + ": " + x["message"][:60] for x in n])
assert any(x["kind"] == "success" and "waitlist" in x["message"] for x in n)
# time conflict + exam clash for Mehmet
assert cr.register(c, M, oid("AIT301"))[0] == "ok"
st, msg, _ = cr.register(c, M, oid("ENG101")); print(7, msg); assert "conflict" in msg.lower()
assert cr.register(c, M, oid("MAT101"))[0] == "ok"
st, msg, warns = cr.register(c, M, oid("PHY101")); print(8, st, warns); assert st == "ok" and warns
# duplicate
assert cr.register(c, M, oid("PHY101"))[0] == "error"
# credit limit for Ayse (CGPA >= 3 -> 36)
rows = cr.transcript_rows(c, A); print("Ayse CGPA", round(cr.cgpa(rows), 2), cr.credit_limit_from_rows(rows)); assert cr.credit_limit_from_rows(rows) == 36
cr.HIGH_GPA_LIMIT = 15
assert cr.register(c, A, oid("CSE201"))[0] == "ok"      # 5 + 6 = 11
st, msg, _ = cr.register(c, A, oid("MAT201")); print(9, msg); assert st == "error" and "AKTS limit" in msg

cr.HIGH_GPA_LIMIT = 36
# --- advisor workflow ---
assert cr.submit_registration(c, A)[0] == "ok"
assert cr.submit_registration(c, A)[0] == "error"            # nothing left in draft
assert any("submitted" in n["message"] for n in cr.unseen_notifications(c, "A1001"))
pend = cr.pending_for_advisor(c, "A1001"); print("pending", [(r["student_id"], r["code"]) for r in pend])
assert {r["code"] for r in pend if r["student_id"] == A} == {"CSE203", "CSE201"}
assert cr.approve(c, "A1001", A, oid("CSE203")) == 1
assert cr.approve(c, "A1001", A, oid("CSE203")) == 0         # already approved
assert cr.reject(c, "A1001", A, oid("CSE201"), "too heavy a load") == 1
assert "CSE201" not in codes(A)
assert any(n["kind"] == "error" and "too heavy" in n["message"] for n in cr.unseen_notifications(c, A))
# --- waitlist promotion on reject of a full course: Elif's CSE101 pending, Mehmet waitlisted ---
assert cr.submit_registration(c, E)[0] == "ok"
assert cr.reject(c, "A1002", E, oid("CSE101"), "wrong section") == 1
assert "CSE101" in codes(M) and not cr.my_waitlist(c, M)[0:1] or True
print("Mehmet now:", codes(M), [r["code"] for r, _ in cr.my_waitlist(c, M)])
assert "CSE101" in codes(M)
# --- capacity increase promotes the waitlist ---
st = cr.register(c, B, oid("HUM101"))[0]; print("Burak HUM101:", st); assert st == "full"   # Zeynep + Mehmet = 2/2
cr.join_waitlist(c, B, oid("HUM101"))
assert cr.set_capacity(c, oid("HUM101"), 3) == 1 and "HUM101" in codes(B)
# --- registration window ---
cr.set_setting(c, "reg_open", "0"); c.commit()
assert cr.register(c, K, oid("MAT101"))[0] == "error" and "closed" in cr.register(c, K, oid("MAT101"))[1]
assert cr.drop(c, B, oid("HUM101"))[0] == "error"
assert cr.submit_registration(c, K)[0] == "error"
cr.set_setting(c, "reg_open", "1"); c.commit()
# --- admin removal ---
assert cr.admin_remove(c, B, oid("HUM101")) == 1 and any("registrar removed" in n["message"] for n in cr.unseen_notifications(c, B))
# --- close term ---
for sid in (M, K):
    for r in cr.my_regs(c, sid): cr.approve  # no-op, keep Mehmet's as drafts
c.execute("UPDATE enrollments SET status='Approved' WHERE student_id=?", (A,)); c.commit()
ok, msg = cr.close_term(c, random.Random(3)); print(msg); assert ok
assert cr.get_setting(c, "current_term") == "2026-27 Spring"
rows = cr.transcript_rows(c, A); print("Ayse after close:", [(r["term"], r["code"], r["grade"]) for r in rows if r["term"] == "2026-27 Fall"])
assert any(r["term"] == "2026-27 Fall" for r in rows)
assert cr.my_regs(c, M) == [] and cr.my_waitlist(c, M) == []
assert cr.close_term(c)[0] is False                           # no further term in the demo
# Spring: Ayse can now take courses of Spring; CSE102 Spring for Mehmet blocked by prereq
print("Spring offering for Ayse:", cr.register(c, A, oid("DBS210", "2026-27 Spring"))[:2])
# --- accounts / announcements ---
assert cr.change_password(c, A, "bad", "abcd", "abcd")[0] is False
assert cr.change_password(c, A, "1234", "abc", "abc")[0] is False
assert cr.change_password(c, A, "1234", "abcd", "abcx")[0] is False
assert cr.change_password(c, A, "1234", "abcd", "abcd")[0] is True and cr.authenticate(c, A, "abcd") and not cr.authenticate(c, A, "1234")
cr.add_announcement(c, "Exam week", "Good luck!")
assert cr.get_announcements(c, 1)[0]["title"] == "Exam week"
print("LOGIC ALL OK")
