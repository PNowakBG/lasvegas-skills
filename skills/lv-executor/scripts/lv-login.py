#!/usr/bin/env python3
"""lv-login.py — deterministyczne logowanie agenta do bukmachera (sts | superbet).

Dlaczego skrypt, a nie model: hasło NIE MA PRAWA przejść przez kontekst LLM ani
przez linię poleceń. Ten program czyta poświadczenia sam (plik podany w
LV_LOGIN_FILE albo $HERMES_HOME/lv-bookmakers.env), wkłada je do
formularza przez harness browser-use (ten sam, którym agent steruje przeglądarką)
i wypisuje na stdout WYŁĄCZNIE wynik jako JSON — bez loginu, bez hasła.

Użycie:
  lv-login.py <sts|superbet> [--check-only] [--logout] [--cdp http://localhost:9222]
  lv-login.py <sts|superbet> --session-status | --session-seen | --session-end
                             | --session-sync=alive | --session-sync=dead

--logout: zamyka sesję u buka (przycisk „Wyloguj”, w odwodzie wyczyszczenie danych
witryny). Bukmacherzy liczą CZAS zalogowania do dziennego limitu gry, więc sesja
trzymana między cyklami zjada limit sama — cykl loguje przed pracą i wylogowuje po.

Wyjście (stdout, jedna linia JSON):
  {"bookmaker":"sts","state":"logged_in","balance":123.45,"reason":null,"detail":null}
  {"bookmaker":"sts","state":"logged_out","reason":"captcha","detail":"…","screenshot":"…"}

Kody wyjścia: 0 = zalogowany (przy --logout: wylogowany), 1 = nie (reason w JSON),
2 = błąd użycia. Powody `logged_out` (te same kody rozumie LasVegas i baner uwagi):
  no_credentials, bad_credentials, captcha, two_factor, login_form_not_found,
  login_error, not_logged_in (tylko --check-only), session_closed (po --logout),
  session_budget (dzienny budżet czasu zalogowania wyczerpany — bez próby logowania).

Czas zalogowania: bukmacher liczy do dziennego limitu gry cały czas sesji, więc ten
skrypt — jedyna droga logowania i cyklu, i modelu — prowadzi licznik na dysku
($HERMES_HOME/lv-session-open-<buk>, lv-session-time-<buk>) i po 4 h jednego dnia
odmawia logowania do północy. Otwarta sesja liczy się od logowania do TERAZ, aż
zamknie ją wylogowanie — także przez cały bieg modelu. --session-status: stan
licznika (kod 3 = budżet wyczerpany); --session-seen: zalogowano z ręki;
--session-end: sesja skończona bez wylogowania skryptem; --session-sync=alive|dead:
uzgodnienie na początku cyklu — przeglądarka żyje (otwarta sesja potwierdzona)
albo nie żyje (nic sesji nie podtrzymuje: rozliczenie do ostatniego potwierdzenia).
Te tryby nie dotykają przeglądarki.

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
from datetime import datetime, timedelta
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


# --- czas zalogowania u buka ---------------------------------------------------
# 11–17.11.2026: sesja STS wisiała w oknie agenta 6,5 doby bez jednego zlecenia
# i dzienny limit czasu gry właściciela schodził co dzień. Budżet to mur, nie cel:
# najcięższy dzień agenta w logach to 123 min (17.09), zwykły poniżej 40 — 4 h to
# dwukrotny zapas i wciąż ułamek limitu, który zostaje właścicielowi.
SESSION_DAY_BUDGET_S = 4 * 3600
# Otwartą sesję zamyka wylogowanie albo uzgodnienie cyklu (--session-sync=dead).
# Próg wieku jest tylko odwodem dla pracy BEZ cyklu (Windows): po awarii nikt by
# znacznika nie zamknął, rósłby bez końca i na stałe zablokował logowanie. Sześć
# godzin bez potwierdzenia to więcej niż jakikolwiek bieg — krótszy próg rozliczał
# sesję w trakcie długiego biegu modelu i gubił jego czas z budżetu.
SESSION_STALE_S = 6 * 3600
# To samo zdanie zwraca program harnessu, gdy przy wylogowaniu sesji już nie było.
ALREADY_CLOSED = "sesja była już zamknięta"


def session_open_file(slug: str) -> Path:
    return HERMES_HOME / f"lv-session-open-{slug}"


def session_time_file(slug: str) -> Path:
    return HERMES_HOME / f"lv-session-time-{slug}"


def session_start(slug: str) -> int | None:
    try:
        return int(session_open_file(slug).read_text().strip())
    except (OSError, ValueError):
        return None


def session_seen(slug: str, now: float) -> None:
    """Pierwsze wywołanie otwiera licznik; kolejne tylko potwierdzają, że sesja trwa."""
    marker = session_open_file(slug)
    try:
        if session_start(slug) is None:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(f"{int(now)}\n")
        os.utime(marker, (now, now))
    except OSError:
        pass


def day_slices(start: float, end: float) -> list[tuple[str, int]]:
    """Czas sesji rozbity na doby kalendarzowe — sesja przez północ obciąża oba dni."""
    slices = []
    cursor = start
    while cursor < end:
        day = datetime.fromtimestamp(cursor)
        midnight = (day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
        stop = min(end, midnight)
        slices.append((day.strftime("%Y-%m-%d"), int(stop - cursor)))
        cursor = stop
    return slices


def session_end(slug: str, now: float, at_last_seen: bool = False) -> int | None:
    """Zamyka licznik i zwraca sekundy sesji. at_last_seen: sesja skończyła się nie
    wiadomo kiedy (bukmacher zamknął ją sam, komputer padł) — liczymy ją do
    ostatniego potwierdzenia, nie do teraz."""
    marker = session_open_file(slug)
    start = session_start(slug)
    end = now
    if at_last_seen:
        try:
            end = marker.stat().st_mtime
        except OSError:
            pass
    try:
        marker.unlink()
    except OSError:
        pass
    if start is None or end < start:
        return None
    try:
        with session_time_file(slug).open("a") as ledger:
            for day, seconds in day_slices(start, end):
                ledger.write(f"{day} {seconds}\n")
    except OSError:
        pass
    return int(end - start)


def session_reap(slug: str, now: float) -> None:
    """Odwód dla pracy bez cyklu — patrz SESSION_STALE_S."""
    try:
        seen = session_open_file(slug).stat().st_mtime
    except OSError:
        return
    if now - seen > SESSION_STALE_S:
        session_end(slug, now, at_last_seen=True)


def session_sync(slug: str, now: float, browser_alive: bool) -> None:
    """Uzgodnienie licznika z rzeczywistością na początku cyklu. Bez przeglądarki
    agenta nic sesji nie podtrzymuje (restart komputera, zabity Chrome), więc
    kończy się na ostatnim potwierdzeniu; z żywą przeglądarką otwarta sesja jest
    potwierdzana. Nigdy nie otwiera licznika — to robi tylko logowanie."""
    if session_start(slug) is None:
        return
    if browser_alive:
        session_seen(slug, now)
    else:
        session_end(slug, now, at_last_seen=True)


def session_used_today(slug: str, now: float) -> int:
    today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    total = 0
    try:
        for line in session_time_file(slug).read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == today and parts[1].isdigit():
                total += int(parts[1])
    except OSError:
        pass
    start = session_start(slug)
    if start is not None and start <= now:
        total += sum(seconds for day, seconds in day_slices(start, now) if day == today)
    return total


def session_status(slug: str, now: float) -> dict:
    session_reap(slug, now)
    used = session_used_today(slug, now)
    return {
        "bookmaker": slug,
        "open": session_start(slug) is not None,
        "usedTodayMin": used // 60,
        "budgetMin": SESSION_DAY_BUDGET_S // 60,
        "spent": used >= SESSION_DAY_BUDGET_S,
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
MODE = os.environ.get("LV_LOGIN_MODE", "login")
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

def click_by_label(pattern, selector="button, a, [role=button], [role=menuitem]"):
    # Tekst ALBO aria-label (menu konta bywa samą ikoną z etykietą dostępności).
    return js_bool(
        "(() => { const re = new RegExp(%s, 'i'); const b = [...document.querySelectorAll('%s')]"
        ".find(e => (re.test((e.innerText || e.textContent || '').trim()) || re.test(e.getAttribute('aria-label') || ''))"
        " && e.getBoundingClientRect().width > 0); if (!b) return false; b.click(); return true; })()" % (json.dumps(pattern), selector)
    )

def click_logout_control():
    # 1) widoczny „Wyloguj"; 2) otwórz menu konta i spróbuj ponownie.
    if click_by_label("wyloguj"):
        return True
    for pattern in ("menu użytkownika", "moje konto", "^konto$", "profil", "depozyt"):
        if click_by_label(pattern):
            time.sleep(0.8)
            if click_by_label("wyloguj"):
                return True
    return False

def clear_site_data(origins):
    # Odwód, gdy przycisku nie ma: wyczyść ciasteczka i storage witryny — sesja
    # po stronie przeglądarki znika, a przy następnym wejściu buk widzi gościa.
    ok = False
    for origin in origins:
        try:
            cdp("Storage.clearDataForOrigin", origin=origin,
                storageTypes="cookies,local_storage,session_storage,indexeddb,cache_storage")
            ok = True
        except Exception:
            pass
    if not ok:
        try:
            cdp("Network.clearBrowserCookies")
            ok = True
        except Exception:
            pass
    try:
        js("localStorage.clear(); sessionStorage.clear(); true")
    except Exception:
        pass
    return ok

def dismiss_cookies():
    for label in ("^Akceptuj wszystkie", "^Akceptuję", "^Zgadzam się", "^Akceptuj$"):
        if click_by_text(label):
            time.sleep(0.8)
            return


def known_overlays(slug):
    # Rejestr znanych okien z LasVegas (agent-config → overlays.<slug>), zapisany
    # przez cykl do pliku. Brak pliku/pola = pusta lista — cookies i ekran
    # powitalny i tak obsługują funkcje wyżej.
    path = os.environ.get("LV_AGENT_CONFIG_FILE", "")
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    entries = (data.get("overlays") or {}).get(slug) if isinstance(data, dict) else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def dismiss_known_overlays(slug):
    # Deterministycznie, przed modelem: każdy wpis to selektor CSS albo tekst
    # przycisku plus akcja (click / remove / escape). Nic wykonywalnego nie
    # przychodzi z serwera — tylko opis, co i jak kliknąć.
    closed = 0
    for entry in known_overlays(slug):
        action = entry.get("action") or "click"
        selector = entry.get("selector")
        text = entry.get("buttonText")
        done = False
        if action == "escape":
            try:
                press_key("Escape")
                done = True
            except Exception:
                done = False
        elif selector and action == "remove":
            done = js_bool(
                "(() => { const n = document.querySelectorAll(%s); n.forEach(e => e.remove()); return n.length > 0; })()"
                % json.dumps(selector)
            )
        elif selector:
            done = js_bool(
                "(() => { const e = document.querySelector(%s); if (!e || e.getBoundingClientRect().width === 0) return false; e.click(); return true; })()"
                % json.dumps(selector)
            )
        elif text:
            done = click_by_label(text)
        if done:
            closed += 1
            time.sleep(0.6)
    return closed

def captcha_visible():
    # hCaptcha/reCAPTCHA renderuje wyzwanie w iframe; niewidzialna wersja NIE ma
    # widocznej ramki — liczy się tylko ramka wyzwania (frame=challenge) na ekranie.
    return js_bool(
        "[...document.querySelectorAll('iframe')].some(f => /hcaptcha|recaptcha|turnstile|challenges\\.cloudflare/i.test(f.src || '')"
        " && /challenge|bframe/i.test(f.src || '') && f.getBoundingClientRect().width > 50"
        " && getComputedStyle(f).visibility !== 'hidden')"
    )

# Cloudflare Turnstile w formularzu logowania STS (widziany 09.10): niewidzialny widget
# z ukrytym polem cf-turnstile-response. Puste pole = Cloudflare jeszcze (albo
# wcale) nie przepuścił przeglądarki — logowanie bez tokenu kończy się ciszą.
TURNSTILE_PENDING = "(() => { const i = document.querySelector('input[name=\"cf-turnstile-response\"]'); return !!i && !i.value; })()"

def turnstile_pending():
    return js_bool(TURNSTILE_PENDING)

def two_factor_visible():
    # Mocny sygnał: widoczne pole na kod (one-time-code / name z code|otp|sms).
    if js_bool(
        "[...document.querySelectorAll('input[autocomplete=\"one-time-code\"], input[name*=\"code\" i], input[name*=\"otp\" i], input[name*=\"sms\" i], input[id*=\"code\" i]')]"
        ".some(i => i.getBoundingClientRect().width > 0)"
    ):
        return True
    # Słaby sygnał: tekst o kodzie — ale tylko w dialogu/modalu, nie w tle strony
    # (marketing Superbetu bywa pełen „potwierdź tożsamość”; 18.09 fałszywe 2FA).
    txt = js_str(
        "(() => { const d = [...document.querySelectorAll('[role=dialog], [class*=modal], [class*=dialog], [class*=login]')]"
        ".filter(e => e.getBoundingClientRect().width > 0); return d.length ? d.map(e => e.innerText || '').join(' ').slice(0, 4000) : ''; })()"
    )
    if not txt:
        return False
    return bool(re.search(r"kod (sms|weryfikacyjny|jednorazowy)|wpisz kod|wys[łl]ali[śs]my (ci )?kod|potwierd[zź] (logowanie|to[żz]samo)|dwuetapow|2fa", txt, re.I))

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
    dismiss_known_overlays("sts")
    if wait_until(STS_LOGGED, 8):
        return out("logged_in", balance=sts_balance())
    if CHECK_ONLY:
        return out("logged_out", "not_logged_in", "brak salda Depozyt na stronie")
    if not USER or not PASS:
        return out("logged_out", "no_credentials", "brak LV_STS_LOGIN / LV_STS_PASSWORD")
    # Modal logowania: przycisk „Zaloguj się" w nagłówku (klik JS omija nakładki).
    # Modal logowania: od 2026-10 STS ignoruje sztuczny JS-click w nagłówku, a
    # helper `wait_for_element` harnessu bywa ślepy na ten modal (widzi inną kartę
    # niż js — prod 2026-10-24: 4× login_form_not_found przy złotym DOM-ie).
    # Dlatego czekanie i fill idą przez js; modal jest osobną trasą Angulara:
    # sts.pl/(modal:my-account) — nawigacja wprost otwiera formularz z testidami.
    STS_FIELD_READY = ("(() => { const e = document.querySelector('[data-testid=\"input-username\"]');"
                       " return !!e && e.getBoundingClientRect().width > 0; })()")
    if not wait_until(STS_FIELD_READY, 6):
        goto_url("https://www.sts.pl/(modal:my-account)")
        if not wait_until(STS_FIELD_READY, 25):
            if not click_by_text("^Zaloguj się$"):
                time.sleep(3)
                click_by_text("^Zaloguj się$")
            if not wait_until(STS_FIELD_READY, 15):
                return out("logged_out", "login_form_not_found", "modal logowania nie pokazał pola e-mail (karta: %s)" % js_str("document.visibilityState"))
    if not js_bool(
        "(() => { const e = document.querySelector('[data-testid=\"input-username\"]'); if (!e) return false;"
        " const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
        " set.call(e, " + json.dumps(USER) + "); e.dispatchEvent(new Event('input', { bubbles: true }));"
        " e.dispatchEvent(new Event('change', { bubbles: true })); return true; })()"
    ):
        fill_input('[data-testid="input-username"]', USER)
    if not js_bool(
        "(() => { const e = document.querySelector('[data-testid=\"input-password\"]'); if (!e) return false;"
        " const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
        " set.call(e, " + json.dumps(PASS) + "); e.dispatchEvent(new Event('input', { bubbles: true }));"
        " e.dispatchEvent(new Event('change', { bubbles: true })); return true; })()"
    ):
        fill_input('[data-testid="input-password"]', PASS)
    time.sleep(0.5)
    # Klik przed wydaniem tokenu Turnstile = formularz bez dowodu „nie-bota”.
    wait_until("!" + TURNSTILE_PENDING, 15)
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
    if turnstile_pending():
        return out("logged_out", "captcha", "Cloudflare Turnstile nie wydał tokenu — STS nie przepuścił logowania (%s)" % (err or "brak komunikatu"))
    return out("logged_out", "login_error", err or "po 40 s brak salda i brak komunikatu")

def sts_logout():
    goto_url("https://www.sts.pl/")
    wait_for_load(20)
    wait_until("!!document.body && document.body.innerText.length > 200", 20)
    time.sleep(1.5)
    dismiss_cookies()
    dismiss_known_overlays("sts")
    sts_dismiss_welcome()
    if not js_bool(STS_LOGGED):
        return out("logged_out", "session_closed", "sesja była już zamknięta")
    if click_logout_control() and wait_until("!(" + STS_LOGGED + ")", 15):
        return out("logged_out", "session_closed", "wylogowano przyciskiem")
    clear_site_data(["https://www.sts.pl", "https://sts.pl"])
    goto_url("https://www.sts.pl/")
    wait_for_load(20)
    time.sleep(2.0)
    if not js_bool(STS_LOGGED):
        return out("logged_out", "session_closed", "wylogowano przez wyczyszczenie danych witryny")
    return out("logged_in", "logout_failed", "po próbie wylogowania nadal widać saldo")

# ---------------- SUPERBET ----------------
SB_LOGGED = "(() => { try { const u = JSON.parse(localStorage.getItem('user') || 'null'); return !!(u && u.value != null); } catch (e) { return false; } })()"
SB_LOGGED_OUT = "!![...document.querySelectorAll('.e2e-login')].find(b => b.getBoundingClientRect().width > 0)"

def sb_login():
    goto_url("https://superbet.pl/")
    wait_for_load(20)
    wait_until("!!document.body && document.body.innerText.length > 200", 20)
    time.sleep(1.5)
    dismiss_cookies()
    dismiss_known_overlays("superbet")
    if wait_until(SB_LOGGED, 5):
        return out("logged_in")
    if CHECK_ONLY:
        return out("logged_out", "not_logged_in", "localStorage.user puste, widoczny .e2e-login")
    if not USER or not PASS:
        return out("logged_out", "no_credentials", "brak LV_SUPERBET_LOGIN / LV_SUPERBET_PASSWORD")
    if not js_bool("(() => { const b = [...document.querySelectorAll('.e2e-login')].find(b => b.getBoundingClientRect().width > 0); if (!b) return false; b.click(); return true; })()"):
        return out("logged_out", "login_form_not_found", "brak przycisku .e2e-login w nagłówku")
    # Ten sam problem co STS: wbudowany wait_for_element bywa ślepy na modal
    # (widzi inną kartę niż js), a selektory Superbeta są poprawne — czekanie
    # i fill idą przez js (prod 2026-10-24: login_form_not_found przy złotym DOM-ie).
    SB_USER_READY = ("(() => { const e = document.querySelector('input[name=\"usernameOrEmail\"]');"
                     " return !!e && e.getBoundingClientRect().width > 0; })()")
    if not wait_until(SB_USER_READY, 20):
        return out("logged_out", "login_form_not_found", "modal logowania nie pokazał pola usernameOrEmail (karta: %s)" % js_str("document.visibilityState"))
    if not js_bool(
        "(() => { const e = document.querySelector('input[name=\"usernameOrEmail\"]'); if (!e) return false;"
        " const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
        " set.call(e, " + json.dumps(USER) + "); e.dispatchEvent(new Event('input', { bubbles: true }));"
        " e.dispatchEvent(new Event('change', { bubbles: true })); return true; })()"
    ):
        fill_input('input[name="usernameOrEmail"]', USER)
    if not js_bool(
        "(() => { const e = document.querySelector('input[name=\"password\"]'); if (!e) return false;"
        " const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
        " set.call(e, " + json.dumps(PASS) + "); e.dispatchEvent(new Event('input', { bubbles: true }));"
        " e.dispatchEvent(new Event('change', { bubbles: true })); return true; })()"
    ):
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

def sb_logout():
    goto_url("https://superbet.pl/")
    wait_for_load(20)
    wait_until("!!document.body && document.body.innerText.length > 200", 20)
    time.sleep(1.5)
    dismiss_cookies()
    dismiss_known_overlays("superbet")
    if not js_bool(SB_LOGGED):
        return out("logged_out", "session_closed", "sesja była już zamknięta")
    if click_logout_control() and wait_until("!(" + SB_LOGGED + ")", 15):
        return out("logged_out", "session_closed", "wylogowano przyciskiem")
    # NIGDY nie czyść danych witryny Superbetu: razem z sesją ginie zaufanie do
    # urządzenia i każde kolejne logowanie kończy się kodem SMS (18.09: alert
    # „potrzebny kod SMS/2FA” co 5 minut przez całą noc). Zamykamy samą sesję
    # aplikacji — klucze użytkownika w localStorage — ciasteczka zostają.
    js_bool("(() => { try { localStorage.removeItem('user'); localStorage.removeItem('users:sessionExpire'); sessionStorage.clear(); } catch (e) {} return true; })()")
    goto_url("https://superbet.pl/")
    wait_for_load(20)
    time.sleep(2.0)
    if not js_bool(SB_LOGGED):
        return out("logged_out", "session_closed", "wylogowano przez zamknięcie sesji aplikacji (bez czyszczenia ciasteczek)")
    return out("logged_in", "logout_failed", "po próbie wylogowania sesja w localStorage nadal żyje — zostaje otwarta (wyłącz „Zamykaj sesję po pracy” dla Superbet, jeśli tak ma być)")

try:
    ensure_real_tab()
except Exception:
    pass
# Karta harnessu bywa kartą W TLE (agent ma kilka kart, demon co cykl startuje od
# nowa). Ukryta karta nie dostaje klatek animacji, więc animowany modal logowania
# STS/Superbet nie pojawia się wcale — 10.10 nocą login_form_not_found u obu buków.
try:
    cdp("Page.bringToFront")
    time.sleep(0.5)
except Exception:
    pass
try:
    if SLUG not in ("sts", "superbet"):
        out("logged_out", "login_error", "nieznany bukmacher %s" % SLUG)
    elif MODE == "logout":
        (sts_logout if SLUG == "sts" else sb_logout)()
    elif SLUG == "sts":
        sts_login()
    else:
        sb_login()
except Exception as exc:
    # Przy wylogowaniu wyjątek nie mówi nic o sesji — nie wolno zgłosić jej jako zamkniętej.
    if MODE == "logout":
        out("logged_in", "logout_failed", "wyjątek harnessu: %s" % str(exc)[:200])
    else:
        out("logged_out", "login_error", "wyjątek harnessu: %s" % str(exc)[:200])
'''


def run_harness(
    slug: str,
    mode: str,
    check_only: bool,
    cdp: str,
    user: str | None,
    password: str | None,
) -> tuple[dict | None, str]:
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
    env["LV_LOGIN_MODE"] = mode
    env["LV_LOGIN_CHECK_ONLY"] = "1" if check_only else "0"
    env["LV_LOGIN_USER"] = user or ""
    env["LV_LOGIN_PASS"] = password or ""
    shot = HERMES_HOME / f"lv-login-{slug}.png"
    env["LV_LOGIN_SHOT"] = str(shot)
    # Rejestr znanych okien (zapisany przez cykl z agent-config) — także przy
    # ręcznym uruchomieniu bez cyklu, jeśli plik istnieje.
    env.setdefault("LV_AGENT_CONFIG_FILE", str(HERMES_HOME / "lv-agent-config.json"))
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
    # Tryby licznika: bez przeglądarki i bez poświadczeń.
    if "--session-status" in flags:
        status = session_status(slug, time.time())
        return emit(status, 3 if status["spent"] else 0)
    if "--session-seen" in flags:
        session_seen(slug, time.time())
        return emit(session_status(slug, time.time()), 0)
    if "--session-end" in flags:
        session_end(slug, time.time())
        return emit(session_status(slug, time.time()), 0)
    if "--session-sync=alive" in flags or "--session-sync=dead" in flags:
        session_sync(slug, time.time(), "--session-sync=alive" in flags)
        status = session_status(slug, time.time())
        return emit(status, 3 if status["spent"] else 0)
    check_only = "--check-only" in flags
    mode = "logout" if "--logout" in flags else "login"
    # Budżet stoi PRZED przeglądarką i przed plikiem poświadczeń: tędy loguje się
    # i cykl, i model, więc żadna z dróg nie obejdzie go drugą.
    if mode == "login" and not check_only:
        status = session_status(slug, time.time())
        if status["spent"]:
            return emit(
                {
                    "bookmaker": slug,
                    "state": "logged_out",
                    "reason": "session_budget",
                    "detail": "agent był dziś zalogowany %d min — dzienny budżet %d min wyczerpany, "
                    "do północy bez logowania" % (status["usedTodayMin"], status["budgetMin"]),
                    "balance": None,
                },
                1,
            )
    cdp = os.environ.get("BU_CDP_URL") or DEFAULT_CDP
    for flag in flags:
        if flag.startswith("--cdp="):
            cdp = flag.split("=", 1)[1]
    credentials_path = Path(os.environ.get("LV_LOGIN_FILE") or DEFAULT_CREDENTIALS)
    user, password = (
        (None, None) if (check_only or mode == "logout") else read_credentials(credentials_path, slug)
    )

    started = time.time()
    result, error = run_harness(slug, mode, check_only, cdp, user, password)
    if result is None:
        # Przy --logout nie wiemy, czy sesja żyje — meldujemy zalogowanie, żeby
        # cykl nie zgłosił fałszywego „sesja zamknięta".
        return emit(
            {
                "bookmaker": slug,
                "state": "logged_in" if mode == "logout" else "logged_out",
                "reason": "logout_failed" if mode == "logout" else "login_error",
                "detail": error,
                "balance": None,
            },
            1,
        )
    result.setdefault("balance", None)
    result["elapsedS"] = round(time.time() - started, 1)
    now = time.time()
    if result.get("state") == "logged_in":
        # Także po nieudanym wylogowaniu: sesja trwa, licznik biegnie dalej.
        session_seen(slug, now)
    elif mode == "logout" and result.get("reason") == "session_closed":
        spent = session_end(slug, now, at_last_seen=result.get("detail") == ALREADY_CLOSED)
        if spent is not None:
            result["sessionMin"] = spent // 60
            result["usedTodayMin"] = session_used_today(slug, now) // 60
            result["budgetMin"] = SESSION_DAY_BUDGET_S // 60
    # Bez poświadczeń wynik jest z góry znany — ale dopiero po sprawdzeniu, czy
    # sesja nie żyje z poprzedniego logowania (ciasteczka profilu agenta).
    if result.get("state") == "logged_out" and result.get("reason") == "no_credentials":
        result["detail"] = (
            f"brak {BOOKMAKERS[slug]['login_key']}/{BOOKMAKERS[slug]['password_key']} w {credentials_path}"
        )
    wanted = "logged_out" if mode == "logout" else "logged_in"
    return emit(result, 0 if result.get("state") == wanted else 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
