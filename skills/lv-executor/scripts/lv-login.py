#!/usr/bin/env python3
"""lv-login.py — deterministyczne logowanie agenta do bukmachera (sts | superbet).

Dlaczego skrypt, a nie model: hasło NIE MA PRAWA przejść przez kontekst LLM ani
przez linię poleceń. Ten program czyta poświadczenia sam (plik podany w
LV_LOGIN_FILE albo $HERMES_HOME/lv-bookmakers.env), wkłada je do
formularza przez harness browser-use (ten sam, którym agent steruje przeglądarką)
i wypisuje na stdout WYŁĄCZNIE wynik jako JSON — bez loginu, bez hasła.

Użycie:
  lv-login.py <sts|superbet> [--check-only] [--cdp http://localhost:9222]

Wyjście (stdout, jedna linia JSON):
  {"bookmaker":"sts","state":"logged_in","balance":555.19,"reason":null,"detail":null}
  {"bookmaker":"sts","state":"logged_out","reason":"captcha","detail":"…","screenshot":"…"}

Kody wyjścia: 0 = zalogowany, 1 = niezalogowany (reason w JSON), 2 = błąd użycia.
Powody `logged_out` (te same kody rozumie LasVegas i baner uwagi):
  no_credentials, bad_credentials, captcha, two_factor, login_form_not_found,
  login_error, not_logged_in (tylko --check-only).

Poświadczenia w pliku (chmod 600), klucze:
  LV_STS_LOGIN=…        LV_STS_PASSWORD=…
  LV_SUPERBET_LOGIN=…   LV_SUPERBET_PASSWORD=…
Zakłada je `lv-executor-cycle.sh credentials <slug>` (macOS/Linux) albo instalator.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from os import environ as OS_ENV
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
DEFAULT_CREDENTIALS = HERMES_HOME / "lv-bookmakers.env"
DEFAULT_CDP = "http://localhost:9222"
# Zimny start Chrome + hydracja SPA buka + hCaptcha: 90 s to górna granica,
# po której nie ma sensu czekać — cykl i tak wróci za 5 minut.
EXEC_TIMEOUT_S = 120

# Klucze środowiska, których harness nie dostaje (patrz run_harness).
SECRET_SHAPED = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)

BOOKMAKERS = {
    "sts": {"login_key": "LV_STS_LOGIN", "password_key": "LV_STS_PASSWORD", "url": "https://www.sts.pl/"},
    "superbet": {
        "login_key": "LV_SUPERBET_LOGIN",
        "password_key": "LV_SUPERBET_PASSWORD",
        "url": "https://superbet.pl/",
    },
}


def emit(result: dict, code: int) -> "int":
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return code


def read_credentials(path: Path, slug: str) -> tuple[str | None, str | None]:
    """Plik KEY=VALUE (bez interpolacji). Brak pliku / klucza = (None, None)."""
    keys = BOOKMAKERS[slug]
    values: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    except FileNotFoundError:
        return None, None
    login = values.get(keys["login_key"]) or None
    password = values.get(keys["password_key"]) or None
    return login, password


def find_browser_use() -> list[str] | None:
    """Ta sama kolejność co Hermes (_find_cli): kopia zarządzana, PATH, ~/.local/bin, uvx."""
    candidates = [HERMES_HOME / "bin", None, Path.home() / ".local" / "bin"]
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        candidates = [HERMES_HOME / "bin", None, Path(appdata) / "uv" / "bin" if appdata else None]
    for name, argv in (("browser-use", lambda b: [b]), ("uvx", lambda b: [b, "browser-use"])):
        for probe in candidates:
            if probe is None:
                found = shutil.which(name)
            elif probe.is_dir():
                found = shutil.which(name, path=str(probe))
            else:
                found = None
            if found:
                return argv(found)
    return None


# --- Program wykonywany w harnessie browser-use (helpers: goto_url, js, fill_input, …) ---
#
# Poświadczenia wchodzą przez ŚRODOWISKO podprocesu (LV_LOGIN_USER / LV_LOGIN_PASS),
# nie przez treść programu — kod programu bywa logowany, środowisko nie.
HARNESS_PROGRAM = r'''
import json, os, re, time

SLUG = os.environ.get("LV_LOGIN_SLUG", "")
CHECK_ONLY = os.environ.get("LV_LOGIN_CHECK_ONLY") == "1"
USER = os.environ.get("LV_LOGIN_USER", "")
PASS = os.environ.get("LV_LOGIN_PASS", "")
SHOT = os.environ.get("LV_LOGIN_SHOT", "")

def out(state, reason=None, detail=None, balance=None):
    res = {"bookmaker": SLUG, "state": state, "reason": reason, "detail": detail, "balance": balance}
    if state != "logged_in" and SHOT:
        try:
            capture_screenshot(SHOT)
            res["screenshot"] = SHOT
        except Exception:
            pass
    print("LV_LOGIN_RESULT " + json.dumps(res, ensure_ascii=False))

def js_bool(expr):
    try:
        return bool(js(expr))
    except Exception:
        return False

def js_str(expr):
    try:
        v = js(expr)
        return v if isinstance(v, str) else ""
    except Exception:
        return ""

def wait_until(expr, timeout=20.0, step=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if js_bool(expr):
            return True
        time.sleep(step)
    return False

def visible_text():
    return js_str("(document.body && document.body.innerText) ? document.body.innerText.slice(0, 20000) : ''")

def click_by_text(pattern, tag="button"):
    return js_bool(
        "(() => { const re = new RegExp(%s, 'i'); const b = [...document.querySelectorAll('%s')]"
        ".find(e => re.test((e.innerText || e.textContent || '').trim()) && e.getBoundingClientRect().width > 0);"
        " if (!b) return false; b.click(); return true; })()" % (json.dumps(pattern), tag)
    )

def dismiss_cookies():
    for label in ("^Akceptuj wszystkie", "^Akceptuję", "^Zgadzam się", "^Akceptuj$"):
        if click_by_text(label):
            time.sleep(0.8)
            return

def captcha_visible():
    # hCaptcha/reCAPTCHA renderuje wyzwanie w iframe; niewidzialna wersja NIE ma
    # widocznej ramki — liczy się tylko ramka wyzwania (frame=challenge) na ekranie.
    return js_bool(
        "[...document.querySelectorAll('iframe')].some(f => /hcaptcha|recaptcha|turnstile/i.test(f.src || '')"
        " && /challenge|bframe/i.test(f.src || '') && f.getBoundingClientRect().width > 50"
        " && getComputedStyle(f).visibility !== 'hidden')"
    )

def two_factor_visible():
    txt = visible_text()
    return bool(re.search(r"kod (sms|weryfikacyjny|jednorazowy)|wpisz kod|potwierd[zź] (logowanie|to[żz]samo)|dwuetapow|2fa", txt, re.I))

def error_text():
    txt = js_str(
        "[...document.querySelectorAll('[role=alert], [class*=error], [class*=alert], [class*=toast], [class*=notification], [class*=message]')]"
        ".filter(e => e.getBoundingClientRect().width > 0).map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 5).join(' | ')"
    )
    return txt[:300]

# ---------------- STS ----------------
STS_LOGGED = "(() => { const t = document.body ? document.body.innerText : ''; return /Depozyt\\s*\\d+[.,]\\d{2}/.test(t); })()"
STS_LOGGED_OUT = "(() => { return [...document.querySelectorAll('button')].some(b => /^\\s*Zaloguj się\\s*$/.test(b.innerText) && b.getBoundingClientRect().width > 0); })()"
STS_BALANCE = "(() => { const m = (document.body ? document.body.innerText : '').match(/Depozyt\\s*([\\d\\s]*\\d,\\d{2})/); return m ? m[1] : ''; })()"

def sts_balance():
    raw = js_str(STS_BALANCE)
    raw = raw.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None

def sts_dismiss_welcome():
    # Ekran powitalny „Dla nowych klientów" przykrywa nagłówek; ma „Kontynuuj jako gość".
    if js_bool("!!document.querySelector('sts-welcome-screen')"):
        click_by_text("Kontynuuj jako go")
        time.sleep(0.8)
        js_bool("(() => { const w = document.querySelector('sts-welcome-screen'); if (w) w.remove(); return true; })()")

def sts_login():
    goto_url("https://www.sts.pl/")
    wait_for_load(20)
    wait_until("!!document.body && document.body.innerText.length > 200", 20)
    time.sleep(1.5)
    dismiss_cookies()
    sts_dismiss_welcome()
    if wait_until(STS_LOGGED, 8):
        return out("logged_in", balance=sts_balance())
    if CHECK_ONLY:
        return out("logged_out", "not_logged_in", "brak salda Depozyt na stronie")
    if not USER or not PASS:
        return out("logged_out", "no_credentials", "brak LV_STS_LOGIN / LV_STS_PASSWORD")
    # Modal logowania: przycisk „Zaloguj się" w nagłówku (klik JS omija nakładki).
    if not click_by_text("^Zaloguj się$"):
        return out("logged_out", "login_form_not_found", "brak przycisku „Zaloguj się” w nagłówku")
    if not wait_for_element('[data-testid="input-username"]', timeout=15, visible=True):
        return out("logged_out", "login_form_not_found", "modal logowania nie pokazał pola e-mail")
    fill_input('[data-testid="input-username"]', USER)
    fill_input('[data-testid="input-password"]', PASS)
    time.sleep(0.5)
    # Przycisk „Zaloguj się" WEWNĄTRZ formularza (nie ten w nagłówku).
    submitted = js_bool(
        "(() => { const f = document.querySelector('[data-testid=\"input-password\"]')?.closest('form');"
        " const b = f && [...f.querySelectorAll('button')].find(b => /Zaloguj się/i.test(b.innerText));"
        " if (!b) return false; b.click(); return true; })()"
    )
    if not submitted:
        press_key("Enter")
    deadline = time.time() + 40
    while time.time() < deadline:
        if js_bool(STS_LOGGED):
            return out("logged_in", balance=sts_balance())
        if captcha_visible():
            return out("logged_out", "captcha", "hCaptcha pokazała wyzwanie przy logowaniu")
        if two_factor_visible():
            return out("logged_out", "two_factor", "STS prosi o kod potwierdzający")
        err = error_text()
        if re.search(r"nieprawid|błędn|niepoprawn|invalid|wrong", err, re.I):
            return out("logged_out", "bad_credentials", err)
        time.sleep(1.0)
    err = error_text()
    return out("logged_out", "login_error", err or "po 40 s brak salda i brak komunikatu")

# ---------------- SUPERBET ----------------
SB_LOGGED = "(() => { try { const u = JSON.parse(localStorage.getItem('user') || 'null'); return !!(u && u.value != null); } catch (e) { return false; } })()"
SB_LOGGED_OUT = "!![...document.querySelectorAll('.e2e-login')].find(b => b.getBoundingClientRect().width > 0)"

def sb_login():
    goto_url("https://superbet.pl/")
    wait_for_load(20)
    wait_until("!!document.body && document.body.innerText.length > 200", 20)
    time.sleep(1.5)
    dismiss_cookies()
    if wait_until(SB_LOGGED, 5):
        return out("logged_in")
    if CHECK_ONLY:
        return out("logged_out", "not_logged_in", "localStorage.user puste, widoczny .e2e-login")
    if not USER or not PASS:
        return out("logged_out", "no_credentials", "brak LV_SUPERBET_LOGIN / LV_SUPERBET_PASSWORD")
    if not js_bool("(() => { const b = [...document.querySelectorAll('.e2e-login')].find(b => b.getBoundingClientRect().width > 0); if (!b) return false; b.click(); return true; })()"):
        return out("logged_out", "login_form_not_found", "brak przycisku .e2e-login w nagłówku")
    if not wait_for_element('input[name="usernameOrEmail"]', timeout=15, visible=True):
        return out("logged_out", "login_form_not_found", "modal logowania nie pokazał pola usernameOrEmail")
    fill_input('input[name="usernameOrEmail"]', USER)
    fill_input('input[name="password"]', PASS)
    time.sleep(0.5)
    if not js_bool("(() => { const b = document.querySelector('#login-modal-submit, .e2e-login-submit-btn'); if (!b) return false; b.click(); return true; })()"):
        press_key("Enter")
    deadline = time.time() + 40
    while time.time() < deadline:
        if js_bool(SB_LOGGED):
            return out("logged_in")
        if captcha_visible():
            return out("logged_out", "captcha", "captcha przy logowaniu")
        if two_factor_visible():
            return out("logged_out", "two_factor", "Superbet prosi o kod potwierdzający")
        err = error_text()
        if re.search(r"nieprawid|błędn|niepoprawn|invalid|wrong", err, re.I):
            return out("logged_out", "bad_credentials", err)
        time.sleep(1.0)
    err = error_text()
    return out("logged_out", "login_error", err or "po 40 s brak sesji w localStorage i brak komunikatu")

try:
    ensure_real_tab()
except Exception:
    pass
try:
    if SLUG == "sts":
        sts_login()
    elif SLUG == "superbet":
        sb_login()
    else:
        out("logged_out", "login_error", "nieznany bukmacher %s" % SLUG)
except Exception as exc:
    out("logged_out", "login_error", "wyjątek harnessu: %s" % str(exc)[:200])
'''


def run_harness(slug: str, check_only: bool, cdp: str, user: str | None, password: str | None) -> tuple[dict | None, str]:
    cli = find_browser_use()
    if not cli:
        return None, "brak CLI browser-use (zainstaluj: uv tool install browser-use)"
    # Środowisko harnessu = nasze minus sekrety (token urządzenia, klucze API —
    # harness ich nie potrzebuje, a kod programu bywa logowany) i minus ścieżki
    # Pythona Hermesa (browser-use ma własny interpreter; odziedziczony PYTHONPATH
    # psuł mu C-rozszerzenia).
    env = {
        k: v
        for k, v in OS_ENV.items()
        if k not in ("PYTHONPATH", "PYTHONHOME") and not SECRET_SHAPED.search(k)
    }
    env["BU_CDP_URL"] = cdp
    env.setdefault("ANONYMIZED_TELEMETRY", "false")
    env["LV_LOGIN_SLUG"] = slug
    env["LV_LOGIN_CHECK_ONLY"] = "1" if check_only else "0"
    env["LV_LOGIN_USER"] = user or ""
    env["LV_LOGIN_PASS"] = password or ""
    shot = HERMES_HOME / f"lv-login-{slug}.png"
    env["LV_LOGIN_SHOT"] = str(shot)
    try:
        proc = subprocess.run(
            cli,
            input=HARNESS_PROGRAM,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=EXEC_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None, f"browser-use nie odpowiedział w {EXEC_TIMEOUT_S} s"
    except OSError as exc:
        return None, f"nie udało się uruchomić browser-use: {exc}"
    for line in (proc.stdout or "").splitlines():
        if line.startswith("LV_LOGIN_RESULT "):
            try:
                return json.loads(line[len("LV_LOGIN_RESULT "):]), ""
            except json.JSONDecodeError:
                break
    tail = "\n".join(((proc.stderr or "") + "\n" + (proc.stdout or "")).strip().splitlines()[-4:])
    return None, f"harness nie zwrócił wyniku (kod {proc.returncode}): {tail[:300]}"


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = [a for a in argv if a.startswith("--")]
    if len(args) != 1 or args[0] not in BOOKMAKERS:
        sys.stderr.write("użycie: lv-login.py <sts|superbet> [--check-only] [--cdp URL]\n")
        return 2
    slug = args[0]
    check_only = "--check-only" in flags
    cdp = os.environ.get("BU_CDP_URL") or DEFAULT_CDP
    for flag in flags:
        if flag.startswith("--cdp="):
            cdp = flag.split("=", 1)[1]
    credentials_path = Path(os.environ.get("LV_LOGIN_FILE") or DEFAULT_CREDENTIALS)
    user, password = (None, None) if check_only else read_credentials(credentials_path, slug)

    started = time.time()
    result, error = run_harness(slug, check_only, cdp, user, password)
    if result is None:
        return emit(
            {"bookmaker": slug, "state": "logged_out", "reason": "login_error", "detail": error, "balance": None},
            1,
        )
    result.setdefault("balance", None)
    result["elapsedS"] = round(time.time() - started, 1)
    # Bez poświadczeń wynik jest z góry znany — ale dopiero po sprawdzeniu, czy
    # sesja nie żyje z poprzedniego logowania (ciasteczka profilu agenta).
    if result.get("state") == "logged_out" and result.get("reason") == "no_credentials":
        result["detail"] = (
            f"brak {BOOKMAKERS[slug]['login_key']}/{BOOKMAKERS[slug]['password_key']} w {credentials_path}"
        )
    return emit(result, 0 if result.get("state") == "logged_in" else 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
