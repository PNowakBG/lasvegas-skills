#!/bin/bash
# lv-executor-cycle.sh — jeden cykl agenta egzekutora LasVegas (v2, 2026-09-01).
#
# 1) sprawdza kolejkę zleceń BEZ budzenia LLM (curl do API); pusta = koniec cyklu
#    — wcześniej co 5 min startowała sesja Hermesa (~10 wywołań narzędzi), żeby
#    przeczytać `[]`; 623 sesje w 3 dni, z czego ~620 pustych,
# 2) pilnuje, że dedykowana przeglądarka bukmacherska żyje (CDP :9222) i startuje ją
#    przez `open`, czyli POZA grupą procesów tego joba — launchd po wyjściu skryptu
#    zabija całą grupę (AbandonProcessGroup=false), więc Chrome odpalany tu `&`
#    ginął co cykl (86 restartów 31.08–01.09, okno „Nowa karta" co 5 min),
# 3) blokuje nakładanie się cykli (lock),
# 4) odpala hermes chat ze skillem lv-executor i wysyła powiadomienie macOS, gdy
#    agent zgłosi brak logowania / brak środków — w trybie -q nikt nie czyta czatu.
#
# Użycie: lv-executor-cycle.sh                 # pełny cykl (launchd co 300 s)
#         lv-executor-cycle.sh ensure-chrome   # tylko podnieś przeglądarkę agenta
#         lv-executor-cycle.sh login [url]     # podnieś przeglądarkę i otwórz buka do zalogowania
set -u

HERMES_HOME="$HOME/.hermes"
CHROME_APP="Google Chrome"
PROFILE_DIR="$HERMES_HOME/lv-browser-profile"
CDP="http://localhost:9222"
LOCK="$HERMES_HOME/lv-executor.lock"
LOG="$HERMES_HOME/lv-executor.log"
HEARTBEAT="$HERMES_HOME/lv-executor.heartbeat"
LAST_RUN="$HERMES_HOME/lv-executor.last-run.log"
ENV_FILE="$HERMES_HOME/.env"
HERMES_BIN="$HOME/.local/bin/hermes"

# Model przypięty do TEGO joba, niezależnie od model.default w config.yaml.
# Od 2026-09-01 08:24: DeepSeek V4 Flash przez OpenRouter (klucz OPENROUTER_API_KEY
# w ~/.hermes/.env; 0,065/0,18 $ za 1M tokenów, kontekst 1,3M, narzędzia ✓ —
# sonda jednorazowa zwróciła „OK"). Płatne modele Nous wymagają kredytów, których
# konto nie ma. Gdy OpenRouter zawiedzie (429/5xx/brak środków), Hermes przechodzi
# na łańcuch fallback_providers z config.yaml (darmowe modele Nous).
# 2026-09-01 16:xx: właściciel przełączył model Hermesa na v4-pro (hermes model);
# egzekutor czyta model STĄD, więc zmiana musi być też tu. OpenRouter: 1,60/3,20 $ za 1M —
# bieg z dwoma zleceniami na flashu trwał 35–39 min i ~200 wywołań narzędzi, pro ma
# skrócić sesję (to sesje zjadły dzienny limit czasu gry na koncie STS).
LV_MODEL="${LV_MODEL:-deepseek/deepseek-v4-pro}"
LV_PROVIDER="${LV_PROVIDER:-openrouter}"

# Blackhole trackerów przez PAC zamiast --host-resolver-rules: tamta flaga jest na
# liście „złych flag" Chromium (bad_flags_prompt.cc) i wyświetla baner
# „Użyto nieobsługiwanej flagi". Hosty z listy → PROXY 127.0.0.1:9 (port discard,
# connection refused = szybka porażka), reszta DIRECT. W data: URL nie może być
# '?' ani '#'. Zweryfikowane przez CDP 2026-09-01: blokada 0,6 s, inne strony 200.
PAC='data:,function FindProxyForURL(url, host) { if (/(^|\.)(doubleclick\.net|snapchat\.com|google-analytics\.com|googletagmanager\.com|analytics\.google\.com|redditstatic\.com|tiktokw\.us|contentsquare\.net)$/.test(host)) { return "PROXY 127.0.0.1:9"; } return "DIRECT"; }'

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
cdp_alive() { curl -sf --max-time 3 "$CDP/json/version" > /dev/null 2>&1; }
notify() {
  # $1 = tytuł, $2 = treść. Powiadomienie systemowe macOS w sesji użytkownika.
  osascript -e "display notification \"$2\" with title \"$1\" sound name \"Basso\"" > /dev/null 2>&1 || true
}

ensure_chrome() {
  cdp_alive && return 0
  log "startuję Chrome (profil $PROFILE_DIR) przez open — poza grupą procesów cyklu"
  # -n = nowa instancja (zwykły Chrome usera może działać obok, to inny user-data-dir),
  # -a = aplikacja, --args = flagi dla Chrome. Proces należy do LaunchServices, nie do
  # tego skryptu, więc przeżywa koniec cyklu, a jego stderr nie zaśmieca logu.
  # Trzy flagi anty-throttling: okno agenta bywa zasłonięte/zminimalizowane, a Chrome
  # dławi wtedy timery i renderer — 01.09 co drugi browser_exec kończył się po 30 s
  # timeoutem na stronie STS. Flagi są standardem w automatyzacji, nie dają banera.
  open -na "$CHROME_APP" --args \
    --remote-debugging-port=9222 \
    --user-data-dir="$PROFILE_DIR" \
    --no-first-run --no-default-browser-check --hide-crash-restore-bubble \
    --disable-background-timer-throttling --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    --window-size=1400,900 \
    --proxy-pac-url="$PAC" \
    about:blank
  # Zimny start na 8 GB RAM bywa dłuższy niż 20 s (04:10–04:44 siedem cykli pod rząd
  # „CDP nie odpowiada") — czekamy do 60 s.
  for _ in $(seq 1 60); do
    cdp_alive && return 0
    sleep 1
  done
  log "BŁĄD: CDP :9222 nie odpowiada po 60 s"
  return 1
}

load_env() {
  LV_EXECUTOR_TOKEN=$(sed -n 's/^LV_EXECUTOR_TOKEN=//p' "$ENV_FILE" | tail -1 | tr -d '"'"'"' ')
  LV_API_URL=$(sed -n 's/^LV_API_URL=//p' "$ENV_FILE" | tail -1 | tr -d '"'"'"' ')
  LV_API_URL="${LV_API_URL:-https://lv.ap2ju.com}"
}

# GET /api/executor/queue — poza listą zleceń zwalnia po stronie serwera zawieszone
# claimy tego urządzenia, więc warto go wołać co cykl nawet bez agenta.
queue_json() {
  curl -fsS --max-time 15 -H "Authorization: Bearer $LV_EXECUTOR_TOKEN" "$LV_API_URL/api/executor/queue"
}

case "${1:-cycle}" in
  ensure-chrome)
    ensure_chrome && echo "Chrome agenta działa: $CDP (profil $PROFILE_DIR)"
    exit $?
    ;;
  login)
    ensure_chrome || exit 1
    # Ta sama aplikacja + ten sam user-data-dir → Chrome przekazuje URL działającej
    # instancji agenta (process singleton) i kończy nowy proces.
    open -na "$CHROME_APP" --args --user-data-dir="$PROFILE_DIR" "${2:-https://www.sts.pl}"
    echo "Zaloguj się w oknie przeglądarki agenta (profil $PROFILE_DIR) — to osobny Chrome, nie Twój zwykły."
    exit 0
    ;;
  cycle) ;;
  *)
    echo "użycie: $0 [cycle|ensure-chrome|login [url]]" >&2
    exit 2
    ;;
esac

# --- singleton lock (mkdir jest atomowe) -----------------------------------
# Lock niesie PID cyklu. Do 2026-09-01 „stary" lock (>20 min) był kasowany
# w ciemno — a realny przebieg z dwoma zleceniami trwa 20–30 min. Drugi agent
# na tej samej przeglądarce i tym samym kuponie to podwójny zakład albo
# rozjechany kupon. Żywy proces = lock ważny bez względu na wiek; wiek liczy
# się tylko, gdy po procesie nie ma śladu (crash bez trap EXIT).
if ! mkdir "$LOCK" 2>/dev/null; then
  lock_pid=$(cat "$LOCK/pid" 2>/dev/null || echo "")
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK") ))
  if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
    log "inny cykl jeszcze trwa (pid $lock_pid, ${lock_age}s) — pomijam"
    exit 0
  fi
  if [ "$lock_age" -gt 3600 ] || [ -n "$lock_pid" ]; then
    # proces nie żyje (albo lock bez PID starszy niż godzina) — sprzątamy
    rm -rf "$LOCK"
    mkdir "$LOCK" 2>/dev/null || exit 0
  else
    log "inny cykl jeszcze trwa (lock ${lock_age}s, bez PID) — pomijam"
    exit 0
  fi
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

# --- kolejka bez LLM ---------------------------------------------------------
load_env
if [ -z "${LV_EXECUTOR_TOKEN:-}" ]; then
  log "BŁĄD: brak LV_EXECUTOR_TOKEN w $ENV_FILE — cykl pominięty"
  exit 1
fi
if ! QUEUE=$(queue_json); then
  log "BŁĄD: API LasVegas ($LV_API_URL) niedostępne — cykl pominięty"
  exit 1
fi
date '+%F %T' > "$HEARTBEAT"
if [ "$(printf '%s' "$QUEUE" | tr -d '[:space:]')" = "[]" ]; then
  exit 0   # pusta kolejka: nie budzimy ani LLM, ani przeglądarki
fi
ORDERS=$(printf '%s' "$QUEUE" | grep -o '"betId"' | wc -l | tr -d ' ')
# Osierocone demony browser-use (ppid 1) z poprzednich sesji: browser-use leczy tylko
# SWÓJ demon (po pliku pid), reszta wisi tygodniami (01.09 żyły jeszcze te z 29 i 30.08).
# Sprzątamy tylko bez żywej sesji Hermesa — nowa sesja i tak startuje własny demon.
if ! pgrep -f "hermes chat" >/dev/null 2>&1; then
  orphans=$(pgrep -f "browser_harness.daemon" || true)
  if [ -n "$orphans" ]; then
    log "sprzątam osierocone demony browser-use: $(echo "$orphans" | tr '\n' ' ')"
    kill $orphans 2>/dev/null || true
  fi
fi
log "kolejka: $ORDERS zleceń — budzę agenta ($LV_MODEL @ $LV_PROVIDER)"

# --- przeglądarka + cykl agenta -------------------------------------------------
ensure_chrome || { log "BŁĄD: przeglądarka agenta nie wystartowała — cykl pominięty"; exit 1; }

# UWAGA: nie używać `exec` — zastąpiłby shell i trap EXIT nigdy by nie zwolnił locka.
"$HERMES_BIN" chat --toolsets skills,terminal,browser -m "$LV_MODEL" --provider "$LV_PROVIDER" \
  -q "Załaduj skill lv-executor (skill_view) i wykonaj zaległe zlecenia dokładnie wg jego procedury" \
  2>&1 | tee -a "$LOG" > "$LAST_RUN"
rc=${PIPESTATUS[0]}

# Powiadomienia o porażkach wymagających człowieka — deterministycznie z wyjścia biegu.
if grep -q "not_logged_in" "$LAST_RUN"; then
  notify "LasVegas agent" "Bukmacher wylogowany — zaloguj się w oknie przeglądarki agenta, inaczej zlecenia przepadają."
fi
if grep -q "insufficient_balance" "$LAST_RUN"; then
  notify "LasVegas agent" "Za mało środków na koncie bukmachera — zlecenie nie zostało postawione."
fi
# Sesja padła bez klasycznych przyczyn (model niedostępny, limity providera, crash
# narzędzia): w trybie -q nikt nie czyta czatu, więc cisza oznaczałaby wiszące
# zlecenia odkryte dopiero po godzinach. Zlecenia NIE przepadają — kolejny cykl
# je podniesie — ale ktoś powinien wiedzieć, że agent jest chory.
if [ "$rc" -ne 0 ] && ! grep -q "not_logged_in\|insufficient_balance" "$LAST_RUN"; then
  notify "LasVegas agent" "Sesja agenta zakończyła się błędem (kod $rc) — zlecenia poczekają na kolejny cykl. Szczegóły: lv-executor.last-run.log"
fi
exit $rc
