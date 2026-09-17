#!/usr/bin/env python3
"""
lv-place.py — deterministyczne stawianie kuponu u bukmachera (STS, Superbet) BEZ modelu.

    lv-place.py prepare < zlecenie.json     # nawigacja → rynek → stawka → weryfikacja (nic nie klika „Postaw”)
    lv-place.py commit  < zlecenie.json     # ta sama karta: ponowna weryfikacja → „Postaw” → kupon, saldo
    lv-place.py prepare --dry-run < …       # jak prepare (alias; sprawdzian na sucho)

Zlecenie = wiersz z `lv-api.sh orders` (betId, bookmaker, homeTeam, awayTeam, market,
outcome, odds, stake, …). Wynik: jedna linia `LV_PLACE_RESULT {…}` na stdout:

    state: ready | placed | skipped | failed | not_logged_in | needs_model
    reason/detail        kod i szczegół (jak w lv-api.sh skipped/failed)
    actualOdds, actualStake, ticketId, balanceBefore, balanceAfter, slip, screenshot

DLACZEGO DWIE FAZY: `prepare` trwa najdłużej (strona meczu STS jest ciężka) i NIE
wymaga claimu — zlecenie zostaje w kolejce, więc gdy skrypt odda `needs_model`
(nieznany układ strony, brak rynku), model Hermesa widzi je normalnie i robi
samonaprawę wg playbooka. Dopiero po `ready` cykl robi claim i woła `commit` na
tej samej karcie: krótka ponowna weryfikacja kuponu i jedno kliknięcie.

DLACZEGO BEZ MODELU: 17.09 model potrzebował 6–7 minut na kupon (kilkadziesiąt
wywołań), ten skrypt robi to samo w kilkadziesiąt sekund. Model zostaje do
samonaprawy, gdy strona się zmieni — skrypt wtedy oddaje `needs_model` z krokiem,
na którym padł, i NIGDY nie klika „Postaw” po drodze.

Zasady bezpieczeństwa (te same co w SKILL.md / verification-rules):
- linia rynku DOKŁADNIE ze zlecenia (2 ≠ 2,5), nigdy „najbliższa”;
- kurs na ekranie niższy o >2 % niż w zleceniu → `skipped odds_drift`; wyższy → OK;
- stawka DOKŁADNIE ze zlecenia, potwierdzona na przycisku/kuponie;
- kupon musi być pusty przed dodaniem selekcji i mieć 1 selekcję przed „Postaw”;
- po kliknięciu: jedno kliknięcie, potem czytanie (potwierdzenie, błędy, saldo).
Program harnessu (browser-use) dostaje zlecenie przez ŚRODOWISKO (LV_PLACE_ORDER).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from os import environ as OS_ENV
from pathlib import Path

HERMES_HOME = Path(OS_ENV.get("HERMES_HOME") or (Path.home() / ".hermes"))
DEFAULT_CDP = "http://localhost:9222"
EXEC_TIMEOUT_S = 150
BOOKMAKERS = ("sts", "superbet")
# Zmienne o sekretnym kształcie nie wchodzą do harnessu — kod programu bywa logowany.
SECRET_SHAPED = re.compile(r"(TOKEN|SECRET|PASSWORD|PASS|API_KEY|APIKEY|PRIVATE)", re.I)


def emit(result: dict, code: int) -> int:
    print("LV_PLACE_RESULT " + json.dumps(result, ensure_ascii=False))
    return code


def find_browser_use() -> list[str] | None:
    """Ta sama kolejność co Hermes (_find_cli): kopia zarządzana, PATH, ~/.local/bin, uvx."""
    candidates = [HERMES_HOME / "bin", None, Path.home() / ".local" / "bin"]
    if os.name == "nt":
        appdata = OS_ENV.get("APPDATA")
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
HARNESS_PROGRAM = r'''
import json, os, re, time, unicodedata

ORDER = json.loads(os.environ.get("LV_PLACE_ORDER", "{}"))
PHASE = os.environ.get("LV_PLACE_PHASE", "prepare")
SHOT = os.environ.get("LV_PLACE_SHOT", "")
SLUG = str(ORDER.get("bookmaker", ""))
STEP = "start"

def out(state, reason=None, detail=None, **extra):
    res = {"betId": ORDER.get("betId"), "bookmaker": SLUG, "state": state, "reason": reason,
           "detail": detail, "step": STEP}
    res.update(extra)
    if state in ("needs_model", "failed", "skipped") and SHOT:
        try:
            capture_screenshot(SHOT)
            res["screenshot"] = SHOT
        except Exception:
            pass
    print("LV_PLACE_RESULT " + json.dumps(res, ensure_ascii=False))
    return res

def step(name):
    global STEP
    STEP = name

def js_val(expr):
    try:
        return js(expr)
    except Exception:
        return None

def js_bool(expr):
    return bool(js_val(expr))

def js_str(expr):
    v = js_val(expr)
    return v if isinstance(v, str) else ""

def wait_until(expr, timeout=15.0, poll=0.4):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if js_bool(expr):
            return True
        time.sleep(poll)
    return False

def body_text():
    return js_str("(document.body && document.body.innerText) ? document.body.innerText.slice(0, 40000) : ''")

def norm(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.lower().replace("ł", "l").strip()

GENERIC = {"fc", "cf", "sc", "ac", "afc", "bk", "ks", "mks", "gks", "rks", "club", "clube", "sport", "sporting",
           "athletic", "atletico", "united", "city", "town", "real", "de", "la", "el", "the", "kobiety", "women",
           "balompie", "tc", "sk", "fk", "ii", "b", "u19", "u21", "u23", "ii."}

def key_tokens(team):
    toks = [t for t in re.split(r"[^a-z0-9]+", norm(team)) if len(t) >= 3 and t not in GENERIC]
    if not toks:
        toks = [t for t in re.split(r"[^a-z0-9]+", norm(team)) if len(t) >= 2]
    return toks

def text_has_team(text, team):
    """Najdłuższy nie-generyczny token drużyny musi być w tekście (jak w rozszerzeniu, ostrożniej)."""
    toks = sorted(key_tokens(team), key=len, reverse=True)
    return bool(toks) and toks[0] in text

def search_queries(home, away):
    """Najdłuższy nie-generyczny token gospodarza, potem gościa — wyszukiwarki buków
    nie lubią pełnych nazw („RCD Espanyol” zwraca pustkę, „espanyol” działa)."""
    out = []
    for team in (home, away):
        toks = sorted(key_tokens(team), key=len, reverse=True)
        if toks and toks[0] not in out:
            out.append(toks[0])
    return out or [home]

def decode_line(digits):
    """ou25 → ("2.5", True) ; ou2 → ("2", False) ; corners_ou105 → ("10.5", True). None = nieznany klucz."""
    if not re.fullmatch(r"\d+", digits or ""):
        return None
    if len(digits) == 3 and digits.endswith(("25", "75")):
        return (digits[0] + "." + digits[1:], True)
    if len(digits) >= 2:
        return (digits[:-1] + "." + digits[-1], True)
    return (digits, False)

def fmt_line(line):
    """„2.5” → wzorzec dopasowujący 2.5 / 2,5 ; „2” → 2 albo 2.0/2,0, ale nie 2.5."""
    if "." in line:
        a, b = line.split(".")
        return r"%s[.,]%s(?!\d)" % (re.escape(a), re.escape(b))
    return r"%s(?:[.,]0)?(?![.,]?\d)" % re.escape(line)

def odds_ok(displayed, wanted):
    """Bramka kursu: niższy o więcej niż 2 % → nie. Wyższy → tak."""
    try:
        return float(displayed) >= float(wanted) * 0.98
    except (TypeError, ValueError):
        return False

def parse_amount(text):
    m = re.search(r"(\d[\d\s ]*[.,]\d{2})", str(text or ""))
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None

def stake_text(stake):
    """10 → „10,00”; 10.68 → „10,68” (format kwot na przyciskach/kuponach)."""
    return ("%.2f" % float(stake)).replace(".", ",")

def click_by_text(pattern, tag="button"):
    return js_bool(
        "(() => { const re = new RegExp(%s, 'i'); const b = [...document.querySelectorAll('%s')]"
        ".find(e => re.test((e.innerText || e.textContent || '').trim()) && e.getBoundingClientRect().width > 0);"
        " if (!b) return false; b.click(); return true; })()" % (json.dumps(pattern), tag)
    )

def dismiss_overlays():
    for label in ("^Akceptuj wszystkie", "^Akceptuję", "^Zgadzam się", "^Akceptuj$"):
        if click_by_text(label):
            time.sleep(0.6)
            break
    js_bool("(() => { const b = document.querySelector('#onetrust-accept-btn-handler'); if (b && b.getBoundingClientRect().width > 0) { b.click(); return true; } return false; })()")
    if js_bool("!!document.querySelector('sts-welcome-screen')"):
        click_by_text("Kontynuuj jako go")
        time.sleep(0.6)
        js_bool("(() => { const w = document.querySelector('sts-welcome-screen'); if (w) w.remove(); return true; })()")
    # Rejestr znanych okien z LasVegas (agent-config → overlays.<slug>), jak w lv-login.py.
    path = os.environ.get("LV_AGENT_CONFIG_FILE", "")
    entries = []
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            entries = ((data.get("overlays") or {}).get(SLUG) or []) if isinstance(data, dict) else []
        except (OSError, ValueError):
            entries = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        action, selector, text = e.get("action") or "click", e.get("selector"), e.get("buttonText")
        if action == "escape":
            try:
                press_key("Escape")
            except Exception:
                pass
        elif selector and action == "remove":
            js_bool("(() => { const n = document.querySelectorAll(%s); n.forEach(x => x.remove()); return n.length > 0; })()" % json.dumps(selector))
        elif selector:
            js_bool("(() => { const x = document.querySelector(%s); if (!x || x.getBoundingClientRect().width === 0) return false; x.click(); return true; })()" % json.dumps(selector))
        elif text:
            click_by_text(text, "button, a, [role=button]")

def open_page(url, ready_expr, timeout=20):
    """goto → (czarna/zawieszona karta) reload → nowa karta. STS po nawigacji ze
    strony meczu na /szukaj potrafi zostać z czarnym ekranem bez DOM (17.09)."""
    for attempt in range(3):
        try:
            if attempt == 0:
                goto_url(url)
            elif attempt == 1:
                js_bool("(() => { location.reload(); return true; })()")
            else:
                new_tab(url)
            wait_for_load(20)
        except Exception:
            pass
        dismiss_overlays()
        if wait_until(ready_expr, timeout):
            return True
    return False

def error_texts():
    txt = js_str(
        "[...document.querySelectorAll('[role=alert], [class*=error], [class*=alert], [class*=toast], [class*=notification], [class*=snackbar]')]"
        ".filter(e => e.getBoundingClientRect().width > 0).map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 6).join(' | ')"
    )
    return txt[:400]

def classify_error(txt):
    t = (txt or "").lower()
    if "limit czasu gry" in t or "limit czasu" in t:
        return ("failed", "bookmaker_limit")
    if "minimaln" in t and "stawk" in t:
        return ("skipped", "bookmaker_limit")
    if "maksymaln" in t and "stawk" in t:
        return ("skipped", "bookmaker_limit")
    if "niewystarczaj" in t or "brak środków" in t or "brak srodkow" in t or "doładuj" in t:
        return ("skipped", "insufficient_balance")
    if "zaloguj" in t and "sesj" in t:
        return ("failed", "not_logged_in")
    return None

def wanted_selection():
    """Rynek/wynik zlecenia → (rodzina, linia, strona). Rodzina: 1x2 | ou | corners | btts | inne."""
    market = str(ORDER.get("market") or "")
    outcome = str(ORDER.get("outcome") or "")
    if market == "1x2":
        return ("1x2", None, {"home": "1", "draw": "X", "away": "2"}.get(outcome))
    m = re.fullmatch(r"ou(\d+)", market)
    if m:
        dec = decode_line(m.group(1))
        return ("ou", dec, "under" if outcome == "under" else "over" if outcome == "over" else None)
    m = re.fullmatch(r"corners_ou(\d+)", market)
    if m:
        dec = decode_line(m.group(1))
        side = "under" if outcome in ("corners_under", "under") else "over" if outcome in ("corners_over", "over") else None
        return ("corners", dec, side)
    if market == "btts":
        return ("btts", None, "yes" if outcome in ("yes", "btts_yes") else "no" if outcome in ("no", "btts_no") else None)
    return ("other", None, None)

# ---------------- STS ----------------
STS_HOME = "https://www.sts.pl"

def sts_logged_in():
    return js_bool("(() => { const t = document.body ? document.body.innerText : ''; return /Depozyt\\s*\\d+[.,]\\d{2}/.test(t); })()")

def sts_balance():
    raw = js_str("(() => { const m = (document.body ? document.body.innerText : '').match(/Depozyt\\s*([\\d\\s\\u00a0]*\\d,\\d{2})/); return m ? m[1] : ''; })()")
    return parse_amount(raw)

def sts_open_event():
    step("navigate")
    if not open_page(STS_HOME + "/szukaj", "!!document.querySelector('#Search, input[type=search]')", 12):
        return "search_input_missing"
    home, away = ORDER.get("homeTeam", ""), ORDER.get("awayTeam", "")
    href = ""
    for query in search_queries(home, away):
        try:
            fill_input("#Search, input[type=search]", query)
        except Exception:
            return "search_fill_failed"
        press_key("Enter")
        deadline = time.time() + 12
        while time.time() < deadline and not href:
            tiles = js_val("[...document.querySelectorAll('a.one-ticket-match-tile-link, a[href*=\"/kursy/\"]')].map(a => ({href: a.getAttribute('href') || '', text: (a.innerText || '').replace(/\\s+/g, ' ')})).slice(0, 30)") or []
            for t in tiles:
                text = norm(t.get("text", "") + " " + t.get("href", ""))
                if "/kursy/" in t.get("href", "") and text_has_team(text, home) and text_has_team(text, away) and "[k]" not in text:
                    href = t["href"]
                    break
            if not href:
                time.sleep(0.6)
        if href:
            break
    if not href:
        return "event_not_found"
    if not open_page(href if href.startswith("http") else STS_HOME + href, "document.querySelectorAll('bo-match-detail-market-wrapper').length > 3", 25):
        return "event_page_not_rendered"
    title = norm(js_str("document.title"))
    if not (text_has_team(title, home) and text_has_team(title, away)):
        return "event_title_mismatch"
    dismiss_overlays()
    return None

def sts_slip_has_legs():
    return js_bool("/Postaw\\s+[\\d\\s]*\\d,\\d{2}\\s*z/.test(document.body.innerText) || document.querySelectorAll('.odds-button__container--selected').length > 0")

def sts_clear_slip():
    # Nogi z poprzednich biegów: X przy nodze to bezimienny przycisk w prawej kolumnie;
    # gdy to nie pomaga — szkic kuponu w localStorage + reload (playbook STS, krok 4.0).
    js_bool("(() => { const xs = [...document.querySelectorAll('button.only-icon.sds-button.tertiary.small')].filter(b => b.getBoundingClientRect().left > innerWidth * 0.55 && !/expand/.test(String(b.className) + (b.querySelector('i') ? b.querySelector('i').className : ''))); xs.forEach(b => b.click()); return xs.length; })()")
    time.sleep(0.8)
    if sts_slip_has_legs():
        js_bool("(() => { try { localStorage.removeItem('betslip-cache'); } catch (e) {} location.reload(); return true; })()")
        wait_for_load(25)
        wait_until("document.querySelectorAll('bo-match-detail-market-wrapper').length > 3", 25)
        dismiss_overlays()
    return not sts_slip_has_legs()

def sts_market_header(family, dec):
    if family == "1x2":
        return r"^Mecz$"
    if family == "ou":
        return r"^Liczba goli \(z możliwym zwrotem\)$" if not dec[1] else r"^Liczba goli$"
    if family == "corners":
        return r"^Liczba rzutów rożnych( \(z możliwym zwrotem\))?$" if not dec[1] else r"^Liczba rzutów rożnych$"
    if family == "btts":
        return r"^Obie drużyny - strzelą gola$"
    return None

def sts_button_pattern(family, dec, side):
    if family == "1x2":
        return r"^%s\s" % re.escape(side)
    if family in ("ou", "corners"):
        line = fmt_line(dec[0])
        return (r"^(-\s?|Poniżej\s+)" if side == "under" else r"^(\+\s?|Powyżej\s+)") + line + r"\s"
    if family == "btts":
        return r"^tak\s" if side == "yes" else r"^nie\s"
    return None

def sts_find_button(header_re, label_re):
    """Zwraca {label, odds, index} przycisku w bloku rynku; rozwija zwinięty blok raz."""
    finder = """(() => {
      const hre = new RegExp(%s); const lre = new RegExp(%s, 'i');
      const vis = e => e.getBoundingClientRect().width > 0;
      const w = [...document.querySelectorAll('bo-match-detail-market-wrapper')].find(w => hre.test(((w.querySelector('.market-tile-header__name') || {}).innerText || '').trim()));
      if (!w) return {missing: 'wrapper', headers: [...document.querySelectorAll('.market-tile-header__name')].map(h => h.innerText.trim()).slice(0, 80)};
      let btns = [...w.querySelectorAll('button.odds-button__container')].filter(vis);
      if (btns.length === 0) { const h = w.querySelector('.market-tile-header'); if (h) { h.scrollIntoView({block: 'center'}); h.click(); } return {expanded: true}; }
      const labels = btns.map(b => (b.getAttribute('aria-label') || b.innerText || '').trim());
      const idx = labels.findIndex(l => lre.test(l));
      if (idx < 0) return {missing: 'button', labels};
      const m = labels[idx].match(/(\\d+[.,]\\d+)\\s*$/);
      w.setAttribute('data-lv-target', '1'); btns[idx].setAttribute('data-lv-pick', '1');
      return {label: labels[idx], odds: m ? parseFloat(m[1].replace(',', '.')) : null};
    })()""" % (json.dumps(header_re), json.dumps(label_re))
    for _ in range(3):
        res = js_val(finder)
        if isinstance(res, dict) and res.get("expanded"):
            time.sleep(1.2)
            continue
        return res
    return {"missing": "button", "labels": []}

def sts_click_pick():
    js_bool("(() => { const b = document.querySelector('[data-lv-pick=\"1\"]'); if (!b) return false; b.scrollIntoView({block: 'center'}); b.click(); return true; })()")
    return wait_until("!!document.querySelector('[data-lv-pick=\"1\"].odds-button__container--selected') && /Postaw|Zaloguj się/.test(document.body.innerText)", 8)

def sts_set_stake(stake):
    js_bool("""(() => { const inp = document.querySelector('#Stawka, input[inputmode="decimal"]'); if (!inp) return false;
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(inp, ''); inp.dispatchEvent(new Event('input', {bubbles: true})); inp.focus(); return true; })()""")
    type_text(stake_text(stake) if float(stake) != int(float(stake)) else str(int(float(stake))))
    js_bool("""(() => { const inp = document.querySelector('#Stawka, input[inputmode="decimal"]'); if (!inp) return false;
      inp.dispatchEvent(new Event('change', {bubbles: true})); inp.blur(); return true; })()""")
    want = re.escape(stake_text(stake))
    # Etykieta „Postaw …” odświeża się z opóźnieniem (~1 s) — czytaj ponownie, nie ufaj pierwszemu odczytowi.
    if wait_until("/Postaw\\s+%s\\s*z/.test(document.body.innerText)" % want, 6):
        return True
    if os.environ.get("LV_PLACE_SKIP_LOGIN") == "1":
        return js_bool("(() => { const i = document.querySelector('#Stawka, input[inputmode=\"decimal\"]'); return !!i && i.value.replace('.', ',') === %s; })()" % json.dumps(stake_text(stake)))
    return False

def sts_slip_text():
    return js_str("(() => { const els = [...document.querySelectorAll('div,section,aside')].filter(e => e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().left > innerWidth * 0.55 && /Postaw/.test(e.innerText) && e.innerText.length < 1500); els.sort((a, b) => a.innerText.length - b.innerText.length); return els.length ? els[0].innerText.replace(/\\s+/g, ' ').slice(0, 600) : ''; })()")

def sts_ticket_from_network():
    try:
        events = drain_events()
    except Exception:
        return None
    reqs = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("method") == "Network.responseReceived":
            p = ev.get("params", {})
            url = str(p.get("response", {}).get("url", ""))
            if re.search(r"bet|coupon|ticket|slip|kupon", url, re.I) and p.get("response", {}).get("status") == 200:
                reqs.append(p.get("requestId"))
    for rid in reversed(reqs):
        try:
            body = cdp("Network.getResponseBody", requestId=rid).get("body", "")
        except Exception:
            continue
        m = re.search(r"\b(\d{12,20})\b", str(body))
        if m:
            return m.group(1)
    return None

def sts_place():
    step("place")
    try:
        cdp("Network.enable")
        drain_events()
    except Exception:
        pass
    before = sts_balance()
    if not js_bool("(() => { const b = [...document.querySelectorAll('button')].find(b => /^\\s*Postaw\\s+[\\d\\s]*\\d,\\d{2}/.test(b.innerText) && b.getBoundingClientRect().width > 0 && !b.disabled); if (!b) return false; b.click(); return true; })()"):
        return out("failed", "ui_error", "brak aktywnego przycisku Postaw", balanceBefore=before)
    deadline = time.time() + 15
    confirmed = False
    err = ""
    while time.time() < deadline:
        txt = body_text()
        if re.search(r"Przyjęliśmy Twój kupon|Kupon przyjęty|zakład przyjęty|Kurs całkowity", txt, re.I):
            confirmed = True
            break
        err = error_texts()
        if err and classify_error(err):
            break
        # Dialog zmiany kursu: akceptuj tylko w bramce 2 %.
        if re.search(r"kurs.*zmieni", txt, re.I) and re.search(r"Akceptuj", txt):
            new_odds = parse_amount(js_str("(() => { const m = document.body.innerText.match(/nowy kurs[^\\d]*(\\d+[.,]\\d+)/i); return m ? m[1] : ''; })()"))
            if new_odds and odds_ok(new_odds, ORDER.get("odds")):
                click_by_text("^Akceptuj")
            else:
                click_by_text("^(Anuluj|Odrzuć)")
                return out("skipped", "odds_drift", "kurs %s → %s przy potwierdzeniu" % (ORDER.get("odds"), new_odds), balanceBefore=before)
        time.sleep(0.7)
    after = sts_balance()
    stake = float(ORDER.get("stake") or 0)
    dropped = before is not None and after is not None and (before - after) >= stake * 0.5
    ticket = sts_ticket_from_network() if (confirmed or dropped) else None
    if not ticket and (confirmed or dropped):
        m = re.search(r"\b(\d{12,20})\b", body_text())
        ticket = m.group(1) if m else None
    if confirmed or dropped:
        js_bool("(() => { const x = [...document.querySelectorAll('button.only-icon')].find(b => b.closest('[class*=modal], [class*=dialog]')); if (x) x.click(); return true; })()")
        return out("placed", None, None, ticketId=ticket, balanceBefore=before, balanceAfter=after)
    if err:
        kind = classify_error(err) or ("failed", "ui_error")
        return out(kind[0], kind[1], err[:300], balanceBefore=before, balanceAfter=after)
    return out("failed", "unknown_after_click", "brak potwierdzenia, saldo bez zmian", balanceBefore=before, balanceAfter=after)

def sts_prepare():
    family, dec, side = wanted_selection()
    if family == "other" or (family in ("ou", "corners") and dec is None) or side is None:
        return out("needs_model", "unsupported_market", "rynek %s/%s poza skryptem" % (ORDER.get("market"), ORDER.get("outcome")))
    nav = sts_open_event()
    if nav == "event_not_found":
        return out("needs_model", "event_not_found", "wyszukiwarka STS nie zwróciła kafla z obiema drużynami")
    if nav:
        return out("needs_model", "navigation_failed", nav)
    step("login")
    if not sts_logged_in() and os.environ.get("LV_PLACE_SKIP_LOGIN") != "1":
        return out("not_logged_in", "not_logged_in", "brak Depozyt na stronie meczu")
    before = sts_balance()
    step("clear_slip")
    if sts_slip_has_legs() and not sts_clear_slip():
        return out("needs_model", "betslip_not_empty", "kupon ma nogi z poprzednich biegów i nie dał się wyczyścić")
    step("market")
    found = sts_find_button(sts_market_header(family, dec), sts_button_pattern(family, dec, side))
    if not isinstance(found, dict) or found.get("missing"):
        if isinstance(found, dict) and found.get("missing") == "button":
            lines = ", ".join(str(l) for l in (found.get("labels") or [])[:12])
            return out("skipped", "line_not_found", "dostępne: %s" % lines, balanceBefore=before)
        headers = ", ".join((found or {}).get("headers", [])[:12]) if isinstance(found, dict) else ""
        return out("needs_model", "market_not_found", "brak bloku rynku; nagłówki: %s" % headers, balanceBefore=before)
    displayed = found.get("odds")
    if displayed is None:
        return out("needs_model", "odds_unreadable", "etykieta: %s" % found.get("label"), balanceBefore=before)
    if not odds_ok(displayed, ORDER.get("odds")):
        return out("skipped", "odds_drift", "kurs %s → %s" % (ORDER.get("odds"), displayed), balanceBefore=before)
    step("select")
    if not sts_click_pick():
        return out("needs_model", "selection_not_added", "klik w %s nie dodał nogi do kuponu" % found.get("label"), balanceBefore=before)
    step("stake")
    if not sts_set_stake(ORDER.get("stake")):
        if not sts_set_stake(ORDER.get("stake")):
            return out("needs_model", "stake_not_applied", "przycisk Postaw nie pokazuje %s zł" % stake_text(ORDER.get("stake")), balanceBefore=before)
    step("verify")
    slip = sts_slip_text()
    legs = js_val("document.querySelectorAll('.odds-button__container--selected').length")
    if isinstance(legs, (int, float)) and legs > 1:
        return out("needs_model", "betslip_multiple_legs", "zaznaczonych kursów: %s" % int(legs), balanceBefore=before, slip=slip)
    return out("ready", None, None, actualOdds=displayed, actualStake=float(ORDER.get("stake")), balanceBefore=before,
               slip=slip, label=found.get("label"))

def sts_commit():
    step("recheck")
    if not sts_logged_in():
        return out("not_logged_in", "not_logged_in", "sesja wygasła przed kliknięciem")
    want = re.escape(stake_text(ORDER.get("stake")))
    if not js_bool("/Postaw\\s+%s\\s*z/.test(document.body.innerText)" % want):
        return out("needs_model", "stake_not_applied", "przed kliknięciem przycisk nie pokazuje %s zł" % stake_text(ORDER.get("stake")))
    legs = js_val("document.querySelectorAll('.odds-button__container--selected').length")
    if legs != 1:
        return out("needs_model", "betslip_multiple_legs", "zaznaczonych kursów: %s" % legs)
    label = js_str("(() => { const b = document.querySelector('.odds-button__container--selected'); return b ? (b.getAttribute('aria-label') || b.innerText || '').trim() : ''; })()")
    m = re.search(r"(\d+[.,]\d+)\s*$", label)
    displayed = float(m.group(1).replace(",", ".")) if m else None
    if displayed is None or not odds_ok(displayed, ORDER.get("odds")):
        return out("skipped", "odds_drift", "kurs %s → %s tuż przed kliknięciem" % (ORDER.get("odds"), displayed))
    res = sts_place()
    if res.get("state") == "placed":
        res["actualOdds"] = displayed
        res["actualStake"] = float(ORDER.get("stake"))
        print("LV_PLACE_RESULT " + json.dumps(res, ensure_ascii=False))
    return res

# ---------------- Superbet ----------------
SB_HOME = "https://superbet.pl"

def sb_logged_in():
    return js_bool("(() => { try { const u = JSON.parse(localStorage.getItem('user') || 'null'); if (u && u.value != null) return true; const e = JSON.parse(localStorage.getItem('users:sessionExpire') || 'null'); return !!(e && e.value && Number(e.value) > Date.now()); } catch (x) { return false; } })()")

def sb_balance():
    raw = js_str("(() => { const vis = e => e.getBoundingClientRect().width > 0; const els = [...document.querySelectorAll('header *, [class*=header] *, [class*=account] *, [class*=balance] *, [class*=wallet] *')].filter(e => vis(e) && e.children.length === 0 && /\\d+[.,]\\d{2}\\s*(zł|PLN)/i.test(e.innerText || '')); return els.length ? els[0].innerText : ''; })()")
    return parse_amount(raw)

def sb_open_event():
    step("navigate")
    if not open_page(SB_HOME + "/wyszukaj", "!!document.querySelector('input[name=\"search-events\"]')", 12):
        return "search_input_missing"
    home, away = ORDER.get("homeTeam", ""), ORDER.get("awayTeam", "")
    href = ""
    for query in search_queries(home, away):
        try:
            fill_input('input[name="search-events"]', query)
        except Exception:
            return "search_fill_failed"
        press_key("Enter")
        deadline = time.time() + 12
        while time.time() < deadline and not href:
            rows = js_val("[...document.querySelectorAll('a.e2e-event-row, a[href*=\"/kursy/\"]')].map(a => ({href: a.getAttribute('href') || '', text: (a.innerText || '').replace(/\\s+/g, ' ')})).slice(0, 30)") or []
            for r in rows:
                text = norm(r.get("text", "") + " " + r.get("href", ""))
                if "/kursy/" in r.get("href", "") and text_has_team(text, home) and text_has_team(text, away):
                    href = r["href"]
                    break
            if not href:
                time.sleep(0.6)
        if href:
            break
    if not href:
        return "event_not_found"
    if not open_page(href if href.startswith("http") else SB_HOME + href, "document.querySelectorAll('.e2e-market').length > 0", 25):
        return "event_page_not_rendered"
    title = norm(js_str("document.title"))
    if not (text_has_team(title, home) and text_has_team(title, away)):
        return "event_title_mismatch"
    dismiss_overlays()
    return None

def sb_slip_has_legs():
    return js_bool("(() => { const s = document.querySelector('.sds-betslip-selections'); const ph = document.querySelector('.e2e-betslip-empty-placeholder'); return !!(s && s.innerText.trim().length > 0) && !(ph && ph.getBoundingClientRect().width > 0); })()")

def sb_clear_slip():
    js_bool("(() => { const b = document.querySelector('.clear-button'); if (b) { b.click(); return true; } return false; })()")
    time.sleep(0.8)
    click_by_text("^(Usuń wszystk|Wyczyść|Tak, usuń)", "button")
    time.sleep(0.6)
    return not sb_slip_has_legs()

def sb_group_tab(family):
    return {"1x2": None, "ou": "^Gole$", "corners": "^Rzuty rożne$", "btts": "^Gole$"}.get(family)

def sb_market_name(family, dec):
    if family == "1x2":
        return r"^Mecz$"
    if family == "ou":
        return r"^Liczba goli$"
    if family == "corners":
        return r"^(Liczba rzutów rożnych|Rzuty rożne)$"
    if family == "btts":
        return r"^Obie drużyny strzelą( gola)?$"
    return None

def sb_odd_pattern(family, dec, side):
    """Wzorzec na „nazwa kursu | aria-label”: karty tabelaryczne (Liczba goli) nie
    mają nazwy kursu, tylko aria „Liczba goli, Poniżej 2.5 goli w meczu, współczynnik 1.97”."""
    if family == "1x2":
        return {"1": r"^1\s*\||wygra mecz|^1$", "X": r"^X\s*\||Remis|^X$", "2": r"^2\s*\||wygra mecz|^2$"}[side]
    if family in ("ou", "corners"):
        line = fmt_line(dec[0])
        return (r"(Poniżej|Mniej niż)\s+" if side == "under" else r"(Powyżej|Więcej niż)\s+") + line
    if family == "btts":
        return r"^tak\s*\||^tak$" if side == "yes" else r"^nie\s*\||^nie$"
    return None

def sb_find_odd(group_re, market_re, odd_re, side=None):
    if group_re:
        # Chipy filtra rynków („Gole”, „Rzuty rożne”…) — bez kliknięcia rynki tej
        # grupy w ogóle nie są w DOM (lista wirtualna).
        js_bool("(() => { const re = new RegExp(%s, 'i'); const b = [...document.querySelectorAll('.sds-filter-bar__filter-container button')].find(b => re.test((b.innerText || '').trim())); if (!b) return false; b.scrollIntoView({block: 'center', inline: 'center'}); b.click(); return true; })()" % json.dumps(group_re))
        time.sleep(1.5)
    finder = """(() => {
      const mre = new RegExp(%s, 'i'); const ore = new RegExp(%s, 'i'); const side = %s;
      const vis = e => e.getBoundingClientRect().width > 0;
      const markets = [...document.querySelectorAll('.e2e-market')].filter(m => mre.test(((m.querySelector('.e2e-market-name') || {}).innerText || '').trim()));
      if (markets.length === 0) return {missing: 'market', names: [...document.querySelectorAll('.e2e-market-name')].map(n => n.innerText.trim()).slice(0, 80)};
      const m = markets[0];
      const collapse = m.querySelector('.e2e-market-collapse');
      let odds = [...m.querySelectorAll('.e2e-market-odd')];
      if (odds.length === 0 || !odds.some(vis)) { if (collapse) { collapse.scrollIntoView({block: 'center'}); collapse.click(); } const ex = m.querySelector('.e2e-expand-markets'); if (ex) ex.click(); return {expanded: true}; }
      const labelOf = o => { const b = o.querySelector('button') || o; const n = ((o.querySelector('.e2e-odd-name') || {}).innerText || '').trim(); return (n ? n + ' | ' : '') + (b.getAttribute('aria-label') || ''); };
      const labels = odds.map(labelOf);
      let idx = labels.findIndex(l => ore.test(l));
      // 1X2: „wygra mecz” pasuje do obu drużyn — rozstrzyga nazwa kursu (1/2) albo kolejność.
      if (side === '1' || side === '2') { const byName = odds.findIndex(o => ((o.querySelector('.e2e-odd-name') || {}).innerText || '').trim() === side); if (byName >= 0) idx = byName; else { const cands = odds.map((o, i) => [o, i]).filter(([o]) => /wygra mecz/i.test(labelOf(o))); if (cands.length === 2) idx = cands[side === '1' ? 0 : 1][1]; } }
      if (idx < 0) { const more = m.querySelector('.e2e-expand-markets'); if (more && vis(more)) { more.click(); return {expanded: true}; } return {missing: 'odd', names: labels.map(l => l.replace(/, współczynnik.*$/, '')).slice(0, 40)}; }
      const val = ((odds[idx].querySelector('.e2e-odd-value') || {}).innerText || '').trim();
      const btn = odds[idx].querySelector('button') || odds[idx];
      btn.setAttribute('data-lv-pick', '1');
      const ariaOdds = (btn.getAttribute('aria-label') || '').match(/współczynnik\s+(\d+[.,]\d+)/);
      return {label: labels[idx].replace(/, współczynnik.*$/, ''), odds: parseFloat((val || (ariaOdds ? ariaOdds[1] : '')).replace(',', '.'))};
    })()""" % (json.dumps(market_re), json.dumps(odd_re), json.dumps(side))
    for _ in range(3):
        res = js_val(finder)
        if isinstance(res, dict) and res.get("expanded"):
            time.sleep(1.2)
            continue
        return res
    return {"missing": "odd", "names": []}

def sb_click_pick():
    js_bool("(() => { const b = document.querySelector('[data-lv-pick=\"1\"]'); if (!b) return false; b.scrollIntoView({block: 'center'}); b.click(); return true; })()")
    return wait_until("(() => { const s = document.querySelector('.sds-betslip-selections'); return !!(s && s.innerText.trim().length > 0 && document.querySelector('.e2e-betslip-submit')); })()", 8)

def sb_set_stake(stake):
    try:
        fill_input('input[name="stake"]', ("%.2f" % float(stake)).rstrip("0").rstrip("."))
    except Exception:
        return False
    time.sleep(0.6)
    val = js_str("(() => { const i = document.querySelector('input[name=\"stake\"]'); return i ? String(i.value) : ''; })()")
    try:
        return abs(float(val.replace(",", ".")) - float(stake)) < 0.005
    except ValueError:
        return False

def sb_leg_count():
    """Licznik nóg przy przycisku czyszczenia kuponu („1”); None = nie odczytano."""
    v = js_str("(() => { const b = document.querySelector('.clear-button'); const t = b ? (b.innerText || '').trim() : ''; return /^\\d{1,2}$/.test(t) ? t : ''; })()")
    return int(v) if v else None

def sb_slip_text():
    return js_str("(() => { const s = document.querySelector('.sds-betslip-desktop, [class*=betslip-body]'); return s ? s.innerText.replace(/\\s+/g, ' ').slice(0, 600) : ''; })()")

def sb_slip_odds():
    return parse_amount(js_str("(() => { const s = document.querySelector('.sds-betslip-desktop, [class*=betslip-body]'); if (!s) return ''; const m = s.innerText.match(/KURS\\s*(\\d+[.,]\\d+)/i); return m ? m[1] : ''; })()")) or None

def sb_place():
    step("place")
    before = sb_balance()
    if not js_bool("(() => { const b = document.querySelector('.e2e-betslip-submit'); if (!b || b.disabled) return false; b.click(); return true; })()"):
        return out("failed", "ui_error", "brak aktywnego przycisku Postaw zakład", balanceBefore=before)
    deadline = time.time() + 15
    confirmed = False
    err = ""
    ticket = None
    while time.time() < deadline:
        txt = body_text()
        if re.search(r"ZAKŁAD POSTAWIONY|zakład został postawiony|kupon przyjęty|zakład przyjęty", txt, re.I):
            confirmed = True
            m = re.search(r"\b([0-9A-Z]{4}-[0-9A-Z]{5,8})\b", txt)
            ticket = m.group(1) if m else None
            break
        err = error_texts()
        if err and classify_error(err):
            break
        if re.search(r"kurs.*zmieni", txt, re.I) and re.search(r"Akceptuj", txt):
            new_odds = sb_slip_odds()
            if new_odds and odds_ok(new_odds, ORDER.get("odds")):
                click_by_text("^Akceptuj")
            else:
                click_by_text("^(Anuluj|Odrzuć)")
                return out("skipped", "odds_drift", "kurs %s → %s przy potwierdzeniu" % (ORDER.get("odds"), new_odds), balanceBefore=before)
        time.sleep(0.7)
    after = sb_balance()
    stake = float(ORDER.get("stake") or 0)
    dropped = before is not None and after is not None and (before - after) >= stake * 0.5
    if confirmed or dropped:
        click_by_text("^(OK|Zamknij|Gotowe)")
        return out("placed", None, None, ticketId=ticket, balanceBefore=before, balanceAfter=after)
    if err:
        kind = classify_error(err) or ("failed", "ui_error")
        return out(kind[0], kind[1], err[:300], balanceBefore=before, balanceAfter=after)
    return out("failed", "unknown_after_click", "brak potwierdzenia ZAKŁAD POSTAWIONY", balanceBefore=before, balanceAfter=after)

def sb_prepare():
    family, dec, side = wanted_selection()
    if family == "other" or (family in ("ou", "corners") and dec is None) or side is None:
        return out("needs_model", "unsupported_market", "rynek %s/%s poza skryptem" % (ORDER.get("market"), ORDER.get("outcome")))
    nav = sb_open_event()
    if nav == "event_not_found":
        return out("needs_model", "event_not_found", "wyszukiwarka Superbet nie zwróciła meczu z obiema drużynami")
    if nav:
        return out("needs_model", "navigation_failed", nav)
    step("login")
    if not sb_logged_in() and os.environ.get("LV_PLACE_SKIP_LOGIN") != "1":
        return out("not_logged_in", "not_logged_in", "localStorage.user bez sesji")
    before = sb_balance()
    step("clear_slip")
    if sb_slip_has_legs() and not sb_clear_slip():
        return out("needs_model", "betslip_not_empty", "kupon ma nogi z poprzednich biegów i nie dał się wyczyścić")
    step("market")
    found = sb_find_odd(sb_group_tab(family), sb_market_name(family, dec), sb_odd_pattern(family, dec, side), side)
    if not isinstance(found, dict) or found.get("missing"):
        if isinstance(found, dict) and found.get("missing") == "odd":
            return out("skipped", "line_not_found", "dostępne: %s" % ", ".join((found.get("names") or [])[:12]), balanceBefore=before)
        names = ", ".join((found or {}).get("names", [])[:12]) if isinstance(found, dict) else ""
        return out("needs_model", "market_not_found", "brak rynku; nazwy: %s" % names, balanceBefore=before)
    displayed = found.get("odds")
    if displayed is None or displayed != displayed:
        return out("needs_model", "odds_unreadable", "etykieta: %s" % found.get("label"), balanceBefore=before)
    if not odds_ok(displayed, ORDER.get("odds")):
        return out("skipped", "odds_drift", "kurs %s → %s" % (ORDER.get("odds"), displayed), balanceBefore=before)
    step("select")
    if not sb_click_pick():
        return out("needs_model", "selection_not_added", "klik w %s nie dodał nogi do kuponu" % found.get("label"), balanceBefore=before)
    step("stake")
    if not sb_set_stake(ORDER.get("stake")) and not sb_set_stake(ORDER.get("stake")):
        return out("needs_model", "stake_not_applied", "pole stawki nie przyjęło %s" % ORDER.get("stake"), balanceBefore=before)
    step("verify")
    slip = sb_slip_text()
    slip_odds = sb_slip_odds()
    if slip_odds is not None and not odds_ok(slip_odds, ORDER.get("odds")):
        return out("skipped", "odds_drift", "kurs na kuponie %s → %s" % (ORDER.get("odds"), slip_odds), balanceBefore=before, slip=slip)
    legs = sb_leg_count()
    if legs is not None and legs != 1:
        return out("needs_model", "betslip_multiple_legs", "nóg na kuponie: %s" % legs, balanceBefore=before, slip=slip)
    return out("ready", None, None, actualOdds=slip_odds or displayed, actualStake=float(ORDER.get("stake")), balanceBefore=before,
               slip=slip, label=found.get("label"))

def sb_commit():
    step("recheck")
    if not sb_logged_in():
        return out("not_logged_in", "not_logged_in", "sesja wygasła przed kliknięciem")
    if not sb_slip_has_legs():
        return out("needs_model", "betslip_empty", "kupon pusty przed kliknięciem")
    legs = sb_leg_count()
    if legs is not None and legs != 1:
        return out("needs_model", "betslip_multiple_legs", "nóg na kuponie: %s" % legs)
    val = js_str("(() => { const i = document.querySelector('input[name=\"stake\"]'); return i ? String(i.value) : ''; })()")
    try:
        if abs(float(val.replace(",", ".")) - float(ORDER.get("stake"))) >= 0.005:
            return out("needs_model", "stake_not_applied", "pole stawki pokazuje %s" % val)
    except ValueError:
        return out("needs_model", "stake_not_applied", "pole stawki pokazuje %s" % val)
    displayed = sb_slip_odds()
    if displayed is None or not odds_ok(displayed, ORDER.get("odds")):
        return out("skipped", "odds_drift", "kurs %s → %s tuż przed kliknięciem" % (ORDER.get("odds"), displayed))
    res = sb_place()
    if res.get("state") == "placed":
        res["actualOdds"] = displayed
        res["actualStake"] = float(ORDER.get("stake"))
        print("LV_PLACE_RESULT " + json.dumps(res, ensure_ascii=False))
    return res

# ---------------- main ----------------
try:
    ensure_real_tab()
except Exception:
    pass
if ORDER.get("legs"):
    out("needs_model", "ako_unsupported", "kupon wielonogowy poza skryptem")
elif SLUG == "sts":
    sts_commit() if PHASE == "commit" else sts_prepare()
elif SLUG == "superbet":
    sb_commit() if PHASE == "commit" else sb_prepare()
else:
    out("needs_model", "no_adapter", "bukmacher %s poza skryptem" % SLUG)
'''


def run_harness(order: dict, phase: str, cdp: str) -> tuple[list[dict], str]:
    cli = find_browser_use()
    if not cli:
        return [], "brak CLI browser-use (zainstaluj: uv tool install browser-use)"
    env = {
        k: v
        for k, v in OS_ENV.items()
        if k not in ("PYTHONPATH", "PYTHONHOME") and not SECRET_SHAPED.search(k)
    }
    env["BU_CDP_URL"] = cdp
    env.setdefault("ANONYMIZED_TELEMETRY", "false")
    env["LV_PLACE_ORDER"] = json.dumps(order, ensure_ascii=False)
    env["LV_PLACE_PHASE"] = phase
    env["LV_PLACE_SHOT"] = str(HERMES_HOME / f"lv-place-{order.get('betId', 'x')}.png")
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
        return [], f"browser-use nie odpowiedział w {EXEC_TIMEOUT_S} s"
    except OSError as exc:
        return [], f"nie udało się uruchomić browser-use: {exc}"
    results: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith("LV_PLACE_RESULT "):
            try:
                results.append(json.loads(line[len("LV_PLACE_RESULT "):]))
            except json.JSONDecodeError:
                continue
    if results:
        return results, ""
    tail = "\n".join(((proc.stderr or "") + "\n" + (proc.stdout or "")).strip().splitlines()[-4:])
    return [], f"harness nie zwrócił wyniku (kod {proc.returncode}): {tail[:300]}"


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = [a for a in argv if a.startswith("--")]
    if len(args) != 1 or args[0] not in ("prepare", "commit"):
        sys.stderr.write("użycie: lv-place.py <prepare|commit> [--dry-run] [--cdp=URL] < zlecenie.json\n")
        return 2
    phase = args[0]
    cdp = OS_ENV.get("BU_CDP_URL") or DEFAULT_CDP
    for flag in flags:
        if flag.startswith("--cdp="):
            cdp = flag.split("=", 1)[1]
    try:
        order = json.load(sys.stdin)
    except ValueError as exc:
        return emit({"state": "failed", "reason": "bad_order", "detail": f"zlecenie nie jest JSON-em: {exc}"}, 2)
    if not isinstance(order, dict) or not order.get("betId"):
        return emit({"state": "failed", "reason": "bad_order", "detail": "brak betId"}, 2)
    if str(order.get("bookmaker")) not in BOOKMAKERS:
        return emit({"betId": order.get("betId"), "state": "needs_model", "reason": "no_adapter",
                     "detail": f"bukmacher {order.get('bookmaker')} poza skryptem"}, 1)
    results, error = run_harness(order, phase, cdp)
    if not results:
        return emit({"betId": order.get("betId"), "bookmaker": order.get("bookmaker"), "state": "needs_model",
                     "reason": "browser_error", "detail": error}, 1)
    # Ostatnia linia jest ostateczna (commit dopisuje actualOdds po „placed”).
    final = results[-1]
    final.setdefault("betId", order.get("betId"))
    ok = final.get("state") in ("ready", "placed", "skipped")
    return emit(final, 0 if ok else 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
