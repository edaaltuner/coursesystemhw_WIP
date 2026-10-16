# Course Registration System

A desktop course registration app inspired by university student information systems (OBS).
It focuses on the problems students actually hit during registration: schedule conflicts, full classes,
missing prerequisites and heavy course loads - and gives students, advisors and the registrar
their own screens.

Single Python file, no dependencies, runs locally (Tkinter + SQLite).

<!--
Add screenshots to docs/screenshots/ and show them here, for example:
![Dashboard](docs/screenshots/dashboard.png)
-->

## Features

**Registration rules**
- Time conflicts are blocked and name the clashing course
- Exam clashes produce a warning
- Prerequisites must be passed (a failed grade does not count)
- AKTS load limit: 30 per term, 36 for students with a CGPA of 3.00 or higher
- No duplicate registration, no retaking a passed course (a failed one can be retaken)
- Registration is only open for the current term, and only while the registrar keeps the window open

**Waitlists**
- Full courses offer a waitlist; the next eligible student is enrolled automatically when a seat opens
- A banner (and a notification) tells the student as soon as they are moved in from a waitlist
- **Automatic capacity (advisors):** set a rule per course - when the waitlist reaches *N* students, add *M* seats
  up to a maximum. Waitlisted students move in at once and the advisor is notified

**Roles**
- **Student:** dashboard, course catalog with live "can I add this?" feedback, registration progress
  (select, submit, advisor approval), weekly timetable, exam schedule, transcript with GPA/CGPA, notifications
- **Advisor:** approve or reject submitted courses (with a reason), view advisee transcripts, capacity automation
- **Admin (registrar):** open/close the registration window, change capacities, view and edit rosters,
  post announcements, close the term and post grades (demo)

**Interface**
- Sidebar navigation, flat tables you can sort by clicking a heading, themed dialogs and pop-up windows
- Crisp on high-DPI screens (the layout scales with the screen), mouse-wheel scrolling that follows the pointer
- Notifications appear live, so you can run two windows side by side as different users

## Quick start

```bash
python course_registration.py
```

Requires Python 3.9+ with Tkinter (included with the python.org installers; on Debian/Ubuntu run
`sudo apt install python3-tk`). The first run creates a local `registration.db` with demo data.
Rebuild the demo data any time with `python course_registration.py --reset`.

Optional: put a `logo.png` next to the script to show it in the sidebar (it is git-ignored on purpose).

### Demo accounts

| Role | Number | Password |
| --- | --- | --- |
| Student | `20260001` ... `20260008` | `1234` |
| Advisor | `A1001`, `A1002` | `1234` |
| Admin | `admin` | `admin` |

All names and courses are fictional.

### Try the waitlist banner and automatic capacity

1. Sign in as `20260003` and add **CSE101** - it takes the last seat (3/3).
2. Sign in as advisor `A1001`, open **Capacity Automation**, select CSE101 and set a rule: at **2** waiting, add **2** seats, maximum **8**.
3. Sign in as `20260002`, try CSE101 and join the waitlist. Do the same as `20260007`.
4. When the second student joins, the course grows to 5 seats and both are enrolled. They see a green banner, and the advisor gets a notification.

Another route: sign in as `20260006` and drop **HUM101**; the student waiting for it gets the banner on their next sign-in.

## How it works

Everything lives in `course_registration.py`, split into two layers:

- **Data layer** (top of the file, no UI code): SQLite schema, seed data and every rule -
  `check_eligibility`, `register`, `promote_waitlist`, `apply_capacity_rule`, `close_term` and friends.
- **Interface layer** (below): a Tkinter `App` with one page method per screen, a `Dialog` for pop-ups
  and a `ScrollFrame` that makes every page scrollable.

Tables: `users`, `courses`, `offerings` (a course in a term), `slots`, `enrollments`
(`Draft` -> `Pending` -> `Approved`), `waitlist`, `completed` (transcript), `capacity_rules`,
`notifications`, `announcements`, `settings`.

### Make it yours

Edit the `SEED_*` lists near the top (courses, offerings, users, grades), or the constants
`BASE_CREDIT_LIMIT`, `HIGH_GPA_LIMIT`, `HIGH_GPA_THRESHOLD`, `TERMS`, `START_TERM`,
`GRADE_POINTS` and `FAIL_GRADES`, then run with `--reset`.

## Tests

```bash
python -m unittest discover -s tests -v
```

The scenarios cover the registration rules, waitlist promotion, automatic capacity, advisor approval,
term closing, and every screen and button handler at two simulated screen scales. They run against
`tests/fake_tk.py`, a small stand-in for Tkinter, so no display is needed (this is what GitHub Actions uses).
That means they check logic and wiring, not how the window looks - give the real UI a quick look before a release.

## Limitations

- A learning/demo project: it is not connected to any real university system, and the rules (for example the
  36 AKTS overload for a CGPA of 3.00+) are examples you can change.
- Demo passwords are deliberately weak; accounts are stored with salted PBKDF2 hashes, but this is a
  single-machine app and not built for real student data.
- Not affiliated with Yasar University or any other institution.

## AI assistance

This project was built with help from Claude (Anthropic) as part of a course on AI tools.
<!-- Edit or remove this section to match your course's policy. -->

## License

MIT - see [LICENSE](LICENSE). Replace `<YOUR NAME>` in the license file before publishing.
