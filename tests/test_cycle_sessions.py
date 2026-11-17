"""Sesje u bukmachera w lv-executor-cycle.sh: brak zleceń = brak sesji i brak przeglądarki.

Produkcja 11–17.11.2026: w oknie przeglądarki agenta ktoś zalogował się do STS
przy pustej kolejce. Cykl zamykał wyłącznie sesje, które sam otworzył, a przy
pustej kolejce do przeglądarki w ogóle nie zaglądał — sesja wisiała 6,5 doby
i zjadała dzienny limit czasu gry, choć agent nie postawił ani jednego kuponu.

Testy uruchamiają PRAWDZIWY skrypt cyklu w piaskownicy: własny HOME, atrapy
lv-api.sh i przeglądarkowej części lv-login.py oraz atrapy curl/pkill/pgrep/Chrome
w PATH. Licznik czasu zalogowania (tryby --session-*) liczy prawdziwy lv-login.py.
Nie dotykają sieci, przeglądarki ani API.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pty
import select
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "lv-executor"
CYCLE = SKILL / "scripts" / "lv-executor-cycle.sh"

API_STUB = """#!/bin/bash
echo "$*" >> "$LV_TEST_DIR/api-calls"
case "$1" in
  orders) cat "$LV_TEST_DIR/orders.json" ;;
  login-state) cat "$LV_TEST_DIR/login-state.json" ;;
  session-policy) cat "$LV_TEST_DIR/session-policy.json" ;;
  *) echo '{}' ;;
esac
"""

# Atrapa lv-login.py: wynik logowania/wylogowania bierze z pliku
# login-<slug>-<tryb>.json, a gdy go nie ma — stan domyślny (logowanie udane, przy
# wylogowaniu sesji już nie było). Tryby licznika oddaje prawdziwemu skryptowi.
LOGIN_STUB = """import json, os, sys
args = [a for a in sys.argv[1:] if not a.startswith("--")]
mode = "logout" if "--logout" in sys.argv else "login"
root = os.environ["LV_TEST_DIR"]
if any(a.startswith("--session-") for a in sys.argv[1:]):
    real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lv-login-real.py")
    os.execv(sys.executable, [sys.executable, real] + sys.argv[1:])
with open(os.path.join(root, "login-calls"), "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")
path = os.path.join(root, "login-%s-%s.json" % (args[0], mode))
if os.path.exists(path):
    sys.stdout.write(open(path).read().strip() + "\\n")
elif mode == "logout":
    print(json.dumps({"bookmaker": args[0], "state": "logged_out", "reason": "session_closed",
                      "detail": "sesja była już zamknięta"}, ensure_ascii=False))
else:
    print(json.dumps({"bookmaker": args[0], "state": "logged_in", "reason": None, "detail": None}))
"""

BIN_STUBS = {
    # Tylko CDP agenta „odpowiada"; tap, Telegram i reszta sieci są nieosiągalne.
    "curl": """#!/bin/bash
case "$*" in
  *"/json/version"*) [ -f "$LV_TEST_DIR/chrome-alive" ] && exit 0; exit 7 ;;
esac
exit 7
""",
    "pkill": """#!/bin/bash
echo "$*" >> "$LV_TEST_DIR/pkill-calls"
case "$*" in *"user-data-dir="*) rm -f "$LV_TEST_DIR/chrome-alive" ;; esac
exit 0
""",
    "pgrep": """#!/bin/bash
case "$*" in *"user-data-dir="*) [ -f "$LV_TEST_DIR/chrome-alive" ] && exit 0 ;; esac
exit 1
""",
    "google-chrome-stable": """#!/bin/bash
echo "$*" >> "$LV_TEST_DIR/chrome-starts"
touch "$LV_TEST_DIR/chrome-alive"
""",
    "notify-send": """#!/bin/bash
echo "$*" >> "$LV_TEST_DIR/notifications"
""",
}

ALREADY_CLOSED = "sesja była już zamknięta"


def logout_result(slug: str, detail: str, state: str = "logged_out", reason: str = "session_closed") -> str:
    return json.dumps(
        {"bookmaker": slug, "state": state, "reason": reason, "detail": detail}, ensure_ascii=False
    )


class Sandbox:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="lv-cycle-"))
        self.home = self.root / "home"
        self.hermes = self.home / ".hermes"
        self.data = self.root / "data"
        self.bin = self.root / "bin"
        skill = self.hermes / "skills" / "lv-executor"
        shutil.copytree(SKILL, skill, ignore=shutil.ignore_patterns("__pycache__"))
        self.cycle = skill / "scripts" / "lv-executor-cycle.sh"
        self.data.mkdir()
        self.bin.mkdir()
        (skill / "scripts" / "lv-api.sh").write_text(API_STUB)
        (skill / "scripts" / "lv-login.py").rename(skill / "scripts" / "lv-login-real.py")
        (skill / "scripts" / "lv-login.py").write_text(LOGIN_STUB)
        for name, body in BIN_STUBS.items():
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.write("orders.json", "[]")
        self.write("login-state.json", '{"bookmakers": []}')
        self.write("session-policy.json", "[]")

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, name: str, text: str) -> None:
        (self.data / name).write_text(text)

    def lines(self, name: str) -> list[str]:
        path = self.data / name
        return path.read_text().splitlines() if path.exists() else []

    def log(self) -> str:
        path = self.hermes / "lv-executor.log"
        return path.read_text() if path.exists() else ""

    def chrome(self, alive: bool) -> None:
        marker = self.data / "chrome-alive"
        if alive:
            marker.touch()
        elif marker.exists():
            marker.unlink()

    def chrome_alive(self) -> bool:
        return (self.data / "chrome-alive").exists()

    def env(self) -> dict[str, str]:
        return {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "LV_TEST_DIR": str(self.data),
            "LV_EXECUTOR_TOKEN": "test-token",
            "LV_API_URL": "http://lasvegas.invalid",
            "LV_MODEL": "test-model",
            "DISPLAY": ":0",
            "LANG": "C.UTF-8",
        }

    def run(self, *args: str, script: str | None = None) -> subprocess.CompletedProcess:
        cmd = ["bash", "-c", script] if script else ["bash", str(self.cycle), *args]
        return subprocess.run(cmd, env=self.env(), capture_output=True, text=True, timeout=120)

    def functions(self, *names: str) -> str:
        """Źródło wskazanych funkcji skryptu cyklu — do testów bez całego biegu."""
        text = self.cycle.read_text()
        chunks = []
        for name in names:
            start = text.index(f"\n{name}() {{") + 1
            end = text.index("\n}\n", start) + len("\n}\n")
            chunks.append(text[start:end])
        return "\n".join(chunks)


class SandboxCase(unittest.TestCase):
    def setUp(self) -> None:
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def logouts(self) -> list[str]:
        return [c for c in self.box.lines("login-calls") if "--logout" in c]

    def closed_reports(self) -> list[str]:
        return [c for c in self.box.lines("api-calls") if "session_closed" in c]


class IdleQueueTest(SandboxCase):
    """Pusta kolejka: cykl sprawdza prawdziwy stan przeglądarki i ją zamyka."""

    def test_orphan_session_is_closed_reported_and_browser_stopped(self):
        self.box.chrome(True)
        self.box.write("login-sts-logout.json", logout_result("sts", "wylogowano przyciskiem"))

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.logouts(), ["sts --logout", "superbet --logout"])
        self.assertTrue(any(c.startswith("session sts logged_out") for c in self.closed_reports()))
        self.assertFalse(self.box.chrome_alive(), "przeglądarka agenta ma być zamknięta")
        log = self.box.log()
        self.assertIn("POWIADOMIENIE: LasVegas agent — sts:", log)
        self.assertIn("bez zleceń", log)
        self.assertNotIn("POWIADOMIENIE: LasVegas agent — superbet:", log)

    def test_browser_without_sessions_is_stopped_quietly(self):
        self.box.chrome(True)

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.logouts()), 2)
        self.assertFalse(self.box.chrome_alive())
        self.assertNotIn("POWIADOMIENIE", self.box.log())

    def test_no_browser_means_no_work_at_all(self):
        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.box.lines("login-calls"), [])
        self.assertEqual(self.box.lines("pkill-calls"), [])
        self.assertEqual(self.box.lines("chrome-starts"), [])

    def test_failed_logout_still_stops_browser_and_tells_owner(self):
        self.box.chrome(True)
        self.box.write(
            "login-sts-logout.json",
            logout_result("sts", "po próbie wylogowania nadal widać saldo", "logged_in", "logout_failed"),
        )

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.box.chrome_alive())
        self.assertFalse(any(c.startswith("session sts") for c in self.closed_reports()))
        self.assertIn("POWIADOMIENIE: LasVegas agent — sts: nie potwierdziłem", self.box.log())

    def test_unconfirmed_logout_does_not_leave_a_running_counter(self):
        # Licznik bez końca wyczerpałby budżet i zablokował logowanie na stałe.
        self.box.chrome(True)
        marker = self.box.hermes / "lv-session-open-sts"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{int(time.time()) - 120}\n")
        self.box.write(
            "login-sts-logout.json",
            logout_result("sts", "po próbie wylogowania nadal widać saldo", "logged_in", "logout_failed"),
        )

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        ledger = (self.box.hermes / "lv-session-time-sts").read_text().split()
        self.assertTrue(120 <= int(ledger[1]) <= 125, ledger)

    def test_browser_failure_during_logout_is_not_reported_as_closed(self):
        # Zawieszona przeglądarka: harness rzuca wyjątek i nie wiadomo, czy sesja żyje.
        self.box.chrome(True)
        self.box.write(
            "login-superbet-logout.json",
            logout_result("superbet", "wyjątek harnessu: Runtime.evaluate timed out", "logged_out", "login_error"),
        )

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c.startswith("session superbet") for c in self.closed_reports()))
        self.assertFalse(self.box.chrome_alive())

    def test_session_kept_by_policy_is_not_logged_out(self):
        self.box.chrome(True)
        self.box.write("session-policy.json", '[{"bookmaker":"superbet","logoutAfterWork":false}]')

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.logouts(), ["sts --logout"])

    def test_blocked_login_does_not_start_browser(self):
        # Serwer widzi kupony STS, ale logowanie czeka na człowieka (captcha).
        self.box.write(
            "login-state.json", '{"bookmakers":[{"bookmaker":"sts","needsLogin":true,"queued":2}]}'
        )
        (self.box.hermes / "lv-login-block-sts").write_text(f"captcha {int(time.time())}\n")

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.box.lines("chrome-starts"), [])
        self.assertEqual(self.box.lines("login-calls"), [])
        self.assertIn("pomijam logowanie sts: captcha", self.box.log())

    def test_login_for_hidden_orders_is_closed_when_queue_stays_empty(self):
        self.box.write(
            "login-state.json", '{"bookmakers":[{"bookmaker":"sts","needsLogin":true,"queued":1}]}'
        )
        self.box.write(
            "login-sts-logout.json",
            json.dumps(
                {"bookmaker": "sts", "state": "logged_out", "reason": "session_closed",
                 "detail": "wylogowano przyciskiem", "sessionMin": 3, "usedTodayMin": 41, "budgetMin": 240},
                ensure_ascii=False,
            ),
        )

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.box.lines("login-calls")
        self.assertEqual(calls[0], "sts")
        # STS zamknięty raz (przez cykl, który go otworzył), Superbet sprawdzony przy sprzątaniu.
        self.assertEqual(sorted(self.logouts()), ["sts --logout", "superbet --logout"])
        self.assertFalse(self.box.chrome_alive())
        self.assertNotIn("POWIADOMIENIE", self.box.log())
        self.assertIn("sts: sesja trwała 3 min, dziś razem 41 min z 240", self.box.log())


class SessionBudgetTest(SandboxCase):
    """Dzienny budżet czasu zalogowania: licznik na dysku i odmowa po wyczerpaniu."""

    def spend(self, slug: str, seconds: int) -> None:
        today = time.strftime("%Y-%m-%d")
        (self.box.hermes / f"lv-session-time-{slug}").write_text(f"{today} {seconds}\n")

    def test_spent_budget_blocks_login_and_browser(self):
        self.box.write(
            "login-state.json", '{"bookmakers":[{"bookmaker":"sts","needsLogin":true,"queued":1}]}'
        )
        self.spend("sts", 4 * 3600)

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.box.lines("login-calls"), [])
        self.assertEqual(self.box.lines("chrome-starts"), [])
        self.assertIn("POWIADOMIENIE: LasVegas agent — sts: dzienny budżet", self.box.log())

    def test_yesterday_does_not_count(self):
        self.box.write(
            "login-state.json", '{"bookmakers":[{"bookmaker":"sts","needsLogin":true,"queued":1}]}'
        )
        (self.box.hermes / "lv-session-time-sts").write_text("2000-01-01 99999\n")

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.box.lines("login-calls")[0], "sts")

    def open_marker(self, slug: str, seconds_ago: int) -> Path:
        marker = self.box.hermes / f"lv-session-open-{slug}"
        marker.write_text(f"{int(time.time()) - seconds_ago}\n")
        return marker

    def test_session_past_budget_is_closed_even_when_api_is_down(self):
        # Zostawiona sesja + awaria API: cykl kończy się na „API niedostępne",
        # ale sesji z wyczerpanym budżetem nie zostawia zalogowanej.
        self.box.chrome(True)
        self.spend("sts", 4 * 3600)
        self.open_marker("sts", 60)
        (self.box.data / "orders.json").unlink()
        self.box.write(
            "login-sts-logout.json",
            json.dumps({"bookmaker": "sts", "state": "logged_out", "reason": "session_closed",
                        "detail": "wylogowano przyciskiem"}, ensure_ascii=False),
        )

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 1)
        self.assertIn("API LasVegas", self.box.log())
        self.assertEqual(self.logouts(), ["sts --logout"])
        self.assertIn("sts: sesja trwa, a dzienny budżet czasu zalogowania jest wyczerpany", self.box.log())

    def test_spent_budget_without_open_session_does_not_touch_the_browser(self):
        # Budżet wyczerpany, ale sesji nie ma: cykl nie otwiera bukmachera co 5 minut,
        # żeby wylogować kogoś, kto nie jest zalogowany.
        self.box.chrome(True)
        self.spend("sts", 4 * 3600)
        (self.box.data / "orders.json").unlink()

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.box.lines("login-calls"), [])

    def test_session_within_budget_survives_api_outage_and_keeps_counting(self):
        self.box.chrome(True)
        marker = self.open_marker("sts", 600)
        os.utime(marker, (time.time() - 500, time.time() - 500))
        (self.box.data / "orders.json").unlink()

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.logouts(), [])
        self.assertLess(time.time() - marker.stat().st_mtime, 10, "otwarta sesja potwierdzona")
        self.assertTrue(600 <= time.time() - int(marker.read_text()) <= 610)

    def test_marker_without_browser_is_settled_at_cycle_start(self):
        marker = self.open_marker("sts", 9 * 3600)
        os.utime(marker, (time.time() - 8 * 3600, time.time() - 8 * 3600))

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        ledger = (self.box.hermes / "lv-session-time-sts").read_text().splitlines()
        self.assertEqual(sum(int(line.split()[1]) for line in ledger), 3600)
        self.assertEqual(self.box.lines("chrome-starts"), [])

    def test_session_kept_by_policy_is_confirmed_after_work(self):
        # Bez potwierdzenia po biegu modelu jego czas wypadłby z licznika,
        # gdyby przeglądarka padła przed następnym cyklem.
        script = self.keep_script(spent=True).replace(
            "policy_logout_after_work() { return 0; }", "policy_logout_after_work() { return 1; }"
        )
        result = self.box.run(script=script)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("LOGOUT", result.stdout)
        self.assertIn("TOOL sts --session-sync=alive", result.stderr)

    def keep_script(self, spent: bool) -> str:
        return f"""set -u
HERMES_HOME="{self.box.hermes}"
KEPT_SESSIONS="$HERMES_HOME/lv-kept-sessions"
log() {{ echo "LOG $*"; }}
notify() {{ echo "NOTIFY $2"; }}
queue_json() {{ echo '[{{"betId":"b1","bookmaker":"sts"}}]'; }}
queue_bookmakers() {{ echo sts; }}
lv_api() {{ echo '[]'; }}
policy_logout_after_work() {{ return 0; }}
logout_bookmaker() {{ echo "LOGOUT $1"; }}
session_tool() {{ echo "TOOL $*" >&2; }}
session_budget_spent() {{ return {0 if spent else 1}; }}
{self.box.functions("logout_bookmakers")}
logout_bookmakers "sts"
echo finished
"""

    def test_session_stays_while_orders_wait_and_budget_allows(self):
        result = self.box.run(script=self.keep_script(spent=False))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("LOGOUT", result.stdout)
        self.assertIn("sesja zostaje", result.stdout)
        # Zostawiona sesja jest potwierdzana, inaczej następny cykl uznałby ją za porzuconą.
        self.assertIn("TOOL sts --session-sync=alive", result.stderr)

    def test_waiting_orders_do_not_hold_session_past_budget(self):
        result = self.box.run(script=self.keep_script(spent=True))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LOGOUT sts", result.stdout)


class ManualLoginLockTest(SandboxCase):
    """Okno ręcznego logowania trzyma lock cyklu — cykl nie zamyka wtedy przeglądarki."""

    def lock_script(self, body: str) -> str:
        return f"""set -u
HERMES_HOME="{self.box.hermes}"
LOCK="$HERMES_HOME/lv-executor.lock"
LV_OS=Linux
log() {{ echo "LOG $*"; }}
{self.box.functions("dir_mtime", "acquire_lock")}
{body}
"""

    def test_lock_is_taken_and_released_on_exit(self):
        script = self.lock_script('acquire_lock && cat "$LOCK/pid" > /dev/null && echo taken')
        result = self.box.run(script=script)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("taken", result.stdout)
        self.assertFalse((self.box.hermes / "lv-executor.lock").exists())

    def test_live_owner_keeps_the_lock(self):
        lock = self.box.hermes / "lv-executor.lock"
        lock.mkdir(parents=True)
        (lock / "pid").write_text(str(os.getpid()))

        result = self.box.run(script=self.lock_script('acquire_lock || echo "busy: $LOCK_BUSY"'))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"busy: pid {os.getpid()}", result.stdout)
        self.assertTrue(lock.exists(), "cudzy żywy lock nie może zniknąć")

    def test_dead_owner_is_replaced(self):
        lock = self.box.hermes / "lv-executor.lock"
        lock.mkdir(parents=True)
        (lock / "pid").write_text("999999999")

        result = self.box.run(script=self.lock_script("acquire_lock && echo taken"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("taken", result.stdout)

    def test_cycle_skips_while_manual_login_holds_the_lock(self):
        self.box.chrome(True)
        lock = self.box.hermes / "lv-executor.lock"
        lock.mkdir(parents=True)
        (lock / "pid").write_text(str(os.getpid()))

        result = self.box.run("cycle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.box.chrome_alive(), "przeglądarka z trwającym logowaniem zostaje")
        self.assertEqual(self.box.lines("login-calls"), [])

    def run_login_on_terminal(self, timeout_s: int, press_enter: bool) -> tuple[int, str]:
        """`login sts` z prawdziwym terminalem (pty) — tylko wtedy komenda czeka na ENTER."""
        text = self.box.cycle.read_text()
        self.assertEqual(text.count("LOGIN_CONFIRM_TIMEOUT=600\n"), 1)
        self.box.cycle.write_text(text.replace("LOGIN_CONFIRM_TIMEOUT=600\n", f"LOGIN_CONFIRM_TIMEOUT={timeout_s}\n"))
        self.box.write(
            "login-sts-login.json",
            json.dumps({"bookmaker": "sts", "state": "logged_out", "reason": "captcha", "detail": "captcha"}),
        )
        pid, fd = pty.fork()
        if pid == 0:
            os.execve("/bin/bash", ["bash", str(self.box.cycle), "login", "sts"], self.box.env())
        output = b""
        sent = False
        deadline = time.time() + 60
        status = None
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    chunk = b""
                output += chunk
            if press_enter and not sent and "wciśnij ENTER".encode() in output:
                self.assertTrue((self.box.hermes / "lv-executor.lock").exists(), "lock trzymany podczas czekania")
                os.write(fd, b"\n")
                sent = True
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
        else:
            os.kill(pid, 9)
            self.fail("login nie skończył się w 60 s: " + output.decode(errors="replace"))
        os.close(fd)
        return os.waitstatus_to_exitcode(status), output.decode(errors="replace")

    def test_forgotten_terminal_releases_the_lock_without_reporting_login(self):
        # Zapomniany ENTER nie może zatrzymać cykli: lock żywego procesu nie wygasa.
        code, output = self.run_login_on_terminal(timeout_s=2, press_enter=False)

        self.assertEqual(code, 0, output)
        self.assertIn("Nie doczekałem się ENTER", output)
        self.assertFalse((self.box.hermes / "lv-executor.lock").exists())
        self.assertFalse(any(c.startswith("session sts logged_in") for c in self.box.lines("api-calls")))
        self.assertFalse((self.box.hermes / "lv-session-open-sts").exists())

    def test_confirmed_manual_login_is_reported_and_counted(self):
        code, output = self.run_login_on_terminal(timeout_s=30, press_enter=True)

        self.assertEqual(code, 0, output)
        self.assertTrue(any(c.startswith("session sts logged_in") for c in self.box.lines("api-calls")))
        self.assertTrue((self.box.hermes / "lv-session-open-sts").exists(), "licznik otwarty po ENTER")
        self.assertFalse((self.box.hermes / "lv-executor.lock").exists())

    def test_manual_login_takes_the_lock_before_touching_the_browser(self):
        text = CYCLE.read_text()
        start = text.index("\n  login)\n")
        end = text.index("\n  credentials)\n", start)
        block = text[start:end]
        self.assertIn("acquire_lock", block)
        self.assertLess(block.index("acquire_lock"), block.index("ensure_chrome"))


def load_login_module(hermes: Path):
    """Prawdziwy lv-login.py jako moduł, z HERMES_HOME w katalogu testu."""
    spec = importlib.util.spec_from_file_location("lv_login_under_test", SKILL / "scripts" / "lv-login.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HERMES_HOME = hermes
    return module


class LoginScriptSessionTest(unittest.TestCase):
    """Licznik i budżet w lv-login.py — tędy loguje się i cykl, i model."""

    def setUp(self) -> None:
        self.hermes = Path(tempfile.mkdtemp(prefix="lv-login-"))
        self.addCleanup(shutil.rmtree, self.hermes, True)
        self.mod = load_login_module(self.hermes)
        self.harness_calls: list[tuple] = []
        self.harness_result: dict | None = {"bookmaker": "sts", "state": "logged_in", "reason": None, "detail": None}
        self.mod.run_harness = self.fake_harness
        self.marker = self.hermes / "lv-session-open-sts"
        self.ledger = self.hermes / "lv-session-time-sts"

    def fake_harness(self, slug, mode, check_only, cdp, user, password):
        self.harness_calls.append((slug, mode, check_only))
        return (dict(self.harness_result) if self.harness_result is not None else None), "brak wyniku"

    def run_main(self, *argv: str) -> tuple[int, dict]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.mod.main(list(argv))
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def open_session(self, seconds_ago: int, seen_seconds_ago: int = 0) -> None:
        now = time.time()
        self.marker.write_text(f"{int(now - seconds_ago)}\n")
        os.utime(self.marker, (now - seen_seconds_ago, now - seen_seconds_ago))

    def spend_today(self, seconds: int) -> None:
        self.ledger.write_text(f"{time.strftime('%Y-%m-%d')} {seconds}\n")

    def test_login_opens_counter_and_repeat_does_not_move_its_start(self):
        code, result = self.run_main("sts")
        self.assertEqual((code, result["state"]), (0, "logged_in"))
        self.assertTrue(self.marker.exists())
        self.open_session(600)

        self.run_main("sts")

        self.assertTrue(600 <= time.time() - int(self.marker.read_text()) <= 605)

    def test_spent_budget_refuses_login_without_touching_browser(self):
        self.spend_today(4 * 3600)

        code, result = self.run_main("sts")

        self.assertEqual(code, 1)
        self.assertEqual((result["state"], result["reason"]), ("logged_out", "session_budget"))
        self.assertEqual(self.harness_calls, [], "przy wyczerpanym budżecie nie ma próby logowania")
        self.assertFalse(self.marker.exists())

    def test_running_session_counts_towards_budget(self):
        self.spend_today(3 * 3600)
        self.open_session(3700)

        code, result = self.run_main("sts")

        self.assertEqual((code, result["reason"]), (1, "session_budget"))

    def test_budget_is_per_bookmaker_and_per_day(self):
        self.spend_today(4 * 3600)
        (self.hermes / "lv-session-time-superbet").write_text("2000-01-01 99999\n")

        code, result = self.run_main("superbet")

        self.assertEqual((code, result["state"]), (0, "logged_in"))

    def test_logout_and_check_are_never_blocked_by_budget(self):
        self.spend_today(5 * 3600)
        self.harness_result = {"bookmaker": "sts", "state": "logged_out", "reason": "session_closed",
                               "detail": "wylogowano przyciskiem"}
        code, _ = self.run_main("sts", "--logout")
        self.assertEqual(code, 0)
        self.run_main("sts", "--check-only")
        self.assertEqual([c[1:] for c in self.harness_calls], [("logout", False), ("login", True)])

    def test_logout_settles_session_and_reports_minutes(self):
        self.open_session(600)
        self.harness_result = {"bookmaker": "sts", "state": "logged_out", "reason": "session_closed",
                               "detail": "wylogowano przyciskiem"}

        code, result = self.run_main("sts", "--logout")

        self.assertEqual(code, 0)
        self.assertFalse(self.marker.exists())
        self.assertEqual((result["sessionMin"], result["usedTodayMin"], result["budgetMin"]), (10, 10, 240))
        day, seconds = self.ledger.read_text().split()
        self.assertEqual(day, time.strftime("%Y-%m-%d"))
        self.assertTrue(600 <= int(seconds) <= 605)

    def test_session_found_already_closed_counts_to_last_confirmation(self):
        # Bukmacher sam zamknął sesję: wiadomo tylko, że 20 min temu jeszcze trwała.
        self.open_session(3600, seen_seconds_ago=1200)
        self.harness_result = {"bookmaker": "sts", "state": "logged_out", "reason": "session_closed",
                               "detail": self.mod.ALREADY_CLOSED}

        _, result = self.run_main("sts", "--logout")

        self.assertEqual(result["sessionMin"], 40)

    def test_failed_logout_keeps_counter_running(self):
        self.open_session(600, seen_seconds_ago=500)
        self.harness_result = {"bookmaker": "sts", "state": "logged_in", "reason": "logout_failed",
                               "detail": "po próbie wylogowania nadal widać saldo"}

        code, _ = self.run_main("sts", "--logout")

        self.assertEqual(code, 1)
        self.assertTrue(600 <= time.time() - int(self.marker.read_text()) <= 605)
        self.assertLess(time.time() - self.marker.stat().st_mtime, 5, "sesja potwierdzona")

    def test_no_harness_result_on_logout_changes_nothing(self):
        self.open_session(600)
        self.harness_result = None

        code, result = self.run_main("sts", "--logout")

        self.assertEqual((code, result["state"], result["reason"]), (1, "logged_in", "logout_failed"))
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.ledger.exists())

    def test_long_model_run_stays_on_the_counter(self):
        # Cykl loguje się, model pracuje 40 min bez żadnego potwierdzenia, zlecenie
        # dalej czeka. Pytanie o budżet nie może rozliczyć sesji „do logowania".
        self.open_session(40 * 60, seen_seconds_ago=40 * 60)

        code, status = self.run_main("sts", "--session-status")

        self.assertEqual(code, 0)
        self.assertTrue(status["open"])
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.ledger.exists())
        self.assertEqual(self.mod.session_used_today("sts", time.time()) // 60, 40)

    def test_stuck_orders_cannot_outrun_the_budget(self):
        # Sześć cykli po 25 min (logowanie potwierdzone tylko na początku cyklu,
        # potem bieg modelu, zlecenie zostaje): 150 min realnej sesji to 150 min w liczniku.
        from datetime import datetime

        start = datetime(2026, 11, 17, 9, 0).timestamp()
        self.mod.session_seen("sts", start)
        now = start
        for _ in range(6):
            self.mod.session_sync("sts", now, browser_alive=True)   # początek cyklu
            now += 25 * 60                                            # bieg modelu
            self.assertFalse(self.mod.session_status("sts", now)["spent"])  # decyzja „zostaw sesję"
        self.assertEqual(self.mod.session_status("sts", now)["usedTodayMin"], 150)
        now += 95 * 60
        self.assertTrue(self.mod.session_status("sts", now)["spent"])

    def test_sync_without_browser_settles_at_last_confirmation(self):
        # Komputer padł z otwartą sesją: znacznik sprzed 9 h, ostatnio potwierdzony 8 h temu.
        # Liczy się potwierdzona godzina, nie dziewięć — i logowanie nie jest zablokowane.
        self.open_session(9 * 3600, seen_seconds_ago=8 * 3600)

        code, status = self.run_main("sts", "--session-sync=dead")

        self.assertEqual((code, status["spent"], status["open"]), (0, False, False))
        self.assertFalse(self.marker.exists())
        self.assertEqual(sum(int(line.split()[1]) for line in self.ledger.read_text().splitlines()), 3600)

    def test_sync_with_browser_confirms_but_never_opens(self):
        code, status = self.run_main("sts", "--session-sync=alive")
        self.assertEqual((code, status["open"]), (0, False))
        self.assertFalse(self.marker.exists(), "uzgodnienie nie otwiera licznika")

        self.open_session(600, seen_seconds_ago=500)
        self.run_main("sts", "--session-sync=alive")

        self.assertLess(time.time() - self.marker.stat().st_mtime, 5)
        self.assertTrue(600 <= time.time() - int(self.marker.read_text()) <= 605)

    def test_sync_reports_open_session_past_budget(self):
        self.spend_today(4 * 3600)
        self.open_session(60)

        code, status = self.run_main("sts", "--session-sync=alive")

        self.assertEqual((code, status["open"], status["spent"]), (3, True, True))

    def test_without_cycle_abandoned_marker_expires_after_six_hours(self):
        # Praca bez cyklu (Windows): nikt nie uzgadnia licznika, więc po awarii
        # znacznik sam wygasa — inaczej blokowałby logowanie na stałe.
        self.open_session(9 * 3600, seen_seconds_ago=7 * 3600)

        code, result = self.run_main("sts")

        self.assertEqual((code, result["state"]), (0, "logged_in"))
        self.assertEqual(sum(int(line.split()[1]) for line in self.ledger.read_text().splitlines()), 2 * 3600)

    def test_status_exit_code_three_means_spent(self):
        self.spend_today(4 * 3600)

        code, status = self.run_main("sts", "--session-status")

        self.assertEqual((code, status["spent"], status["usedTodayMin"]), (3, True, 240))
        self.assertEqual(self.harness_calls, [])

    def test_seen_confirms_and_end_settles_without_browser(self):
        self.open_session(600, seen_seconds_ago=800)

        self.run_main("sts", "--session-seen")
        self.assertLess(time.time() - self.marker.stat().st_mtime, 5)
        self.run_main("sts", "--session-end")

        self.assertFalse(self.marker.exists())
        self.assertTrue(600 <= int(self.ledger.read_text().split()[1]) <= 605)
        self.assertEqual(self.harness_calls, [])

    def test_session_across_midnight_is_split_between_days(self):
        from datetime import datetime

        start = datetime(2026, 11, 16, 23, 30).timestamp()
        end = datetime(2026, 11, 17, 0, 45).timestamp()

        self.assertEqual(self.mod.day_slices(start, end), [("2026-11-16", 1800), ("2026-11-17", 2700)])
        # Trwająca sesja obciąża dzisiejszy budżet tylko częścią po północy.
        self.marker.write_text(f"{int(start)}\n")
        self.assertEqual(self.mod.session_used_today("sts", end), 2700)

    def test_corrupt_files_do_not_block_login(self):
        self.marker.write_text("śmieci\n")
        self.ledger.write_text("to nie jest dziennik\n\n2026-11-17\n")

        code, result = self.run_main("sts")

        self.assertEqual((code, result["state"]), (0, "logged_in"))


class LogoutTruthTest(unittest.TestCase):
    def test_already_closed_phrase_is_shared(self):
        # Cykl i licznik rozpoznają „sesji nie było" po tym zdaniu — program harnessu
        # (osobny program, bez dostępu do stałych modułu) musi zwracać dokładnie je.
        source = (SKILL / "scripts" / "lv-login.py").read_text()
        harness = source[source.index("HARNESS_PROGRAM = r\'\'\'"):]
        harness = harness[: harness.index("\n\'\'\'\n")]
        self.assertEqual(harness.count(f'"session_closed", "{ALREADY_CLOSED}")'), 2)
        self.assertNotIn("ALREADY_CLOSED", harness)
        self.assertIn(f'ALREADY_CLOSED = "{ALREADY_CLOSED}"', source)
        self.assertIn(f'"{ALREADY_CLOSED}"', CYCLE.read_text())

    def test_harness_failure_during_logout_is_not_a_logout(self):
        source = (SKILL / "scripts" / "lv-login.py").read_text()
        tail = source[source.index("except Exception as exc:", source.index("elif MODE == \"logout\":")):]
        self.assertIn('out("logged_in", "logout_failed"', tail[: tail.index("\n\'\'\'\n")])


if __name__ == "__main__":
    unittest.main()
