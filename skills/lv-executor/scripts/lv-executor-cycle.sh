#!/bin/bash
# lv-executor-cycle.sh — jeden cykl agenta egzekutora LasVegas (v2, 2026-09-01).
#
# 1) sprawdza kolejkę zleceń BEZ budzenia LLM (curl do API); pusta = koniec cyklu
#    — wcześniej co 5 min startowała sesja Hermesa (~10 wywołań narzędzi), żeby
#    przeczytać „[]"; 623 sesje w 3 dni, z czego ~620 pustych,
# 2) pilnuje, że dedykowana przeglądarka bukmacherska żyje (CDP :9222) i startuje ją
#    POZA grupą procesów tego joba. macOS oddaje ją LaunchServices (open -n), bo
#    launchd po wyjściu skryptu zabija całą grupę (AbandonProcessGroup=false);
#    na Linuksie robi to setsid, a dopełnieniem jest KillMode=process w unicie
#    systemd. Chrome odpalany wprost przez „&" ginął co cykl (86 restartów
#    31.08–01.09, okno „Nowa karta" co 5 min),
# 3) blokuje nakładanie się cykli (lock),
# 4) odpala hermes chat ze skillem lv-executor i wysyła powiadomienie systemowe
#    (osascript na macOS, notify-send na Linuksie), gdy agent zgłosi brak
#    logowania / brak środków — w trybie -q nikt nie czyta czatu.
#
# Użycie: lv-executor-cycle.sh                 # pełny cykl (launchd/systemd co 300 s)
#         lv-executor-cycle.sh selfupdate      # wymuś aktualizację skilla z tapa (bez limitu godziny)
#         lv-executor-cycle.sh ensure-chrome   # tylko podnieś przeglądarkę agenta
#         lv-executor-cycle.sh credentials sts # zapisz login+hasło do buka (lokalnie, chmod 600)
#         lv-executor-cycle.sh login [superbet|sts|https://adres]  # zaloguj skryptem; gdy się nie da — okno dla Ciebie
#         lv-executor-cycle.sh logout [superbet|sts]                # zamknij sesję u buka (limit czasu gry)
#         lv-executor-cycle.sh session sts logged_in [saldo]      # zgłoś stan logowania ręcznie
#
# Pełna pętla logowania (od 1.5.0): przed obudzeniem agenta cykl sam sprawdza
# sesję u buków z kolejki (scripts/lv-login.py) i loguje z zapisanych
# poświadczeń. Hasło nigdy nie przechodzi przez model ani linię poleceń — czyta
# je wyłącznie lv-login.py z pliku poświadczeń. Gdy logowanie wymaga człowieka
# (captcha, kod SMS, złe hasło), cykl melduje POWÓD do LasVegas (powiadomienie
# in-app/push + baner) i wysyła powiadomienie systemowe.
set -u

# Argumenty wywołania skryptu. W „skill_selfupdate" „$@" to już argumenty FUNKCJI
# (np. samo „force"), a restart przez „exec" ma odtworzyć całe wywołanie skryptu.
SCRIPT_ARGS=("$@")

HERMES_HOME="$HOME/.hermes"
# System rozstrzyga w tym skrypcie dokładnie trzy rzeczy: czym startuje się
# przeglądarka, czym leci powiadomienie i jaką składnię ma stat. Cała reszta
# cyklu jest wspólna dla macOS i Linuksa.
LV_OS="$(uname -s)"
CHROME_APP="Google Chrome"
PROFILE_DIR="$HERMES_HOME/lv-browser-profile"
CDP="http://localhost:9222"
LOCK="$HERMES_HOME/lv-executor.lock"
LOG="$HERMES_HOME/lv-executor.log"
HEARTBEAT="$HERMES_HOME/lv-executor.heartbeat"
LAST_RUN="$HERMES_HOME/lv-executor.last-run.log"
ENV_FILE="$HERMES_HOME/.env"
# Instalator Hermesa kładzie binarkę w ~/.local/bin, ale użytkownik mógł mieć ją
# wcześniej z innego źródła (pakiet dystrybucji, /usr/local/bin) — bierzemy to, co
# realnie stoi w PATH, a ścieżka domyślna zostaje jako ostatnia deska ratunku.
HERMES_BIN="${HERMES_BIN:-$(command -v hermes 2>/dev/null || echo "$HOME/.local/bin/hermes")}"

# Model bierze się z LasVegas („GET /api/executor/agent-config"), nie z tego pliku.
# Powód: do 2026-09-10 model był tu zahardkodowany, więc wycofanie modelu u
# dostawcy docierało do zadań serwerowych, a egzekutor zostawał na starej nazwie,
# dopóki ktoś nie poprawił tego skryptu ręcznie. Teraz zmiana modelu to jedna
# zmienna po stronie LasVegas — bez edycji tapa i bez reinstalacji na maszynie.
# LV_MODEL/LV_PROVIDER zostały jako RĘCZNE nadpisanie do diagnostyki (gdy
# ustawione, wygrywają z odpowiedzią API i o nic nie pytamy). Puste = pytamy API,
# a gdy API nie odpowie, nie przekazujemy „-m" wcale — Hermes użyje wtedy swojego
# model.default i łańcucha fallback_providers z config.yaml (darmowe modele Nous;
# płatne wymagają kredytów, których konto nie ma).
# Dobór modelu pod SESJE, nie pod jakość: bieg z dwoma zleceniami trwał 35–39 min
# i ~200 wywołań narzędzi — to sesje zjadają dzienny limit czasu gry na koncie STS.
LV_MODEL="${LV_MODEL:-}"
LV_PROVIDER="${LV_PROVIDER:-openrouter}"

# Katalog tego skilla — używany do wołania lv-api.sh, jedynego klienta API.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Poświadczenia bukmacherów — OSOBNY plik (nie konfiguracja Hermesa), którego
# nie czyta żadne narzędzie modelu; sięga do niego tylko lv-login.py.
CREDENTIALS_FILE="$HERMES_HOME/lv-bookmakers.env"
LOGIN_PY="$SKILL_DIR/scripts/lv-login.py"

# Samoaktualizacja z tapa. Instalator kładzie skill w ~/.hermes/skills/lv-executor
# (stąd SKILL_DIR), a tap publikuje wersję w pliku VERSION obok tego skryptu.
# Cykl sprawdza ją najwyżej raz na godzinę (UPDATE_INTERVAL), żeby autostart co
# 5 minut nie tłukł w GitHub i nie instalował w kółko.
UPDATE_URL="https://raw.githubusercontent.com/PNowakBG/lasvegas-skills/main/skills/lv-executor/VERSION"
VERSION_FILE="$SKILL_DIR/VERSION"
UPDATE_CHECK="$HERMES_HOME/lv-skill-update-check"
UPDATE_INTERVAL=3600

# Jedyne miejsce, przez które ten cykl rozmawia z API LasVegas.
# lv_api <limit-czasu-sekundy> <komenda lv-api.sh> [argumenty...]
#
# Sekrety przekazujemy JAWNIE, zamiast eksportować je globalnie. Token trafia
# dokładnie tam, gdzie jest potrzebny: do każdego wywołania lv-api.sh i — osobnym
# przypisaniem inline, niżej — do sesji agenta, bo skill woła lv-api.sh z jej
# wnętrza. Globalny „export" wpuściłby go dodatkowo do przeglądarki startowanej
# przez chrome_spawn, gdzie nie służy niczemu.
lv_api() {
  local timeout="$1"
  shift
  LV_API_TIMEOUT="$timeout" \
    LV_EXECUTOR_TOKEN="$LV_EXECUTOR_TOKEN" \
    LV_API_URL="$LV_API_URL" \
    bash "$SKILL_DIR/scripts/lv-api.sh" "$@"
}

# Model egzekutora z LasVegas. Niepowodzenie = nie wiemy, jaki model → caller
# pomija „-m" i oddaje wybór Hermesowi (lepsze niż wpisanie czegokolwiek na ślepo).
agent_config() {
  local json model provider
  json=$(lv_api 10 agent-config 2>/dev/null) || return 1
  model=$(printf '%s' "$json" | sed -n 's/.*"model"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  provider=$(printf '%s' "$json" | sed -n 's/.*"provider"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  [ -n "$model" ] || return 1
  LV_MODEL="$model"
  [ -n "$provider" ] && LV_PROVIDER="$provider"
  return 0
}

# Blackhole trackerów przez PAC zamiast --host-resolver-rules: tamta flaga jest na
# liście „złych flag" Chromium (bad_flags_prompt.cc) i wyświetla baner
# „Użyto nieobsługiwanej flagi". Hosty z listy → PROXY 127.0.0.1:9 (port discard,
# connection refused = szybka porażka), reszta DIRECT. W data: URL nie może być
# '?' ani '#'. Zweryfikowane przez CDP 2026-09-01: blokada 0,6 s, inne strony 200.
PAC='data:,function FindProxyForURL(url, host) { if (/(^|\.)(doubleclick\.net|snapchat\.com|google-analytics\.com|googletagmanager\.com|analytics\.google\.com|redditstatic\.com|tiktokw\.us|contentsquare\.net)$/.test(host)) { return "PROXY 127.0.0.1:9"; } return "DIRECT"; }'

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# stat ma dwie niekompatybilne składnie: BSD (-f %m) na macOS, GNU (-c %Y) na
# Linuksie. Bez tego rozgałęzienia wiek locka liczył się na Linuksie z pustej
# wartości, a „set -u" kończył wtedy cykl błędem arytmetycznym zamiast czystym
# pominięciem biegu.
dir_mtime() {
  if [ "$LV_OS" = "Darwin" ]; then
    stat -f %m "$1" 2>/dev/null || echo 0
  else
    stat -c %Y "$1" 2>/dev/null || echo 0
  fi
}
cdp_alive() { curl -sf --max-time 3 "$CDP/json/version" > /dev/null 2>&1; }

# Python do lv-login.py: systemowy python3 wystarcza (sam stdlib), zapas = venv Hermesa.
python_bin() {
  if command -v python3 > /dev/null 2>&1; then
    echo python3
  elif [ -x "$HERMES_HOME/hermes-agent/venv/bin/python" ]; then
    echo "$HERMES_HOME/hermes-agent/venv/bin/python"
  else
    return 1
  fi
}

# Wartość pola z jednolinijkowego JSON-a lv-login.py (bez jq — jq nie jest pewne).
json_field() {
  # $1 = JSON, $2 = klucz; wartości null/liczby/łańcuchy; brak = pusty łańcuch
  printf '%s' "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -1 | grep . \
    || printf '%s' "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\([0-9.]*\).*/\1/p" | head -1
}

# Logowanie do buka skryptem + meldunek do LasVegas + powiadomienie, gdy
# potrzebny człowiek. $1 = slug (sts|superbet), $2 = "--check-only" (opcjonalnie).
# Zwraca 0 = zalogowany, 1 = nie. Wynik JSON zostaje w LOGIN_RESULT.
login_bookmaker() {
  local slug="$1" mode="${2:-}" py state reason detail balance
  py=$(python_bin) || { log "BŁĄD: brak python3 — lv-login.py nie ma czym ruszyć"; return 1; }
  LOGIN_RESULT=$(LV_LOGIN_FILE="$CREDENTIALS_FILE" BU_CDP_URL="$CDP" \
    "$py" "$LOGIN_PY" "$slug" $mode 2>> "$LOG") || true
  state=$(json_field "$LOGIN_RESULT" state)
  reason=$(json_field "$LOGIN_RESULT" reason)
  detail=$(json_field "$LOGIN_RESULT" detail)
  balance=$(json_field "$LOGIN_RESULT" balance)
  log "logowanie $slug: ${state:-brak wyniku}${reason:+ ($reason)}${detail:+ — $detail}"
  if [ "$state" = "logged_in" ]; then
    lv_api 15 session "$slug" logged_in ${balance:+"$balance"} > /dev/null 2>&1 \
      || log "OSTRZEŻENIE: nie zgłoszono logged_in $slug do LasVegas"
    return 0
  fi
  # Brak wyniku (skrypt padł) też jest powodem — nie zostawiamy LasVegas bez wiedzy.
  [ -n "$reason" ] || reason="login_error"
  [ -n "$detail" ] || detail="lv-login.py nie zwrócił wyniku"
  lv_api 15 session "$slug" logged_out "" "$reason" "$detail" > /dev/null 2>&1 \
    || log "OSTRZEŻENIE: nie zgłoszono logged_out $slug do LasVegas"
  case "$reason" in
    captcha) notify "LasVegas agent" "$slug: bukmacher pokazał captchę — zaloguj się w oknie agenta (lv-executor-cycle.sh login $slug)." ;;
    two_factor) notify "LasVegas agent" "$slug: potrzebny kod SMS/2FA — zaloguj się w oknie agenta (lv-executor-cycle.sh login $slug)." ;;
    bad_credentials|no_credentials) notify "LasVegas agent" "$slug: brak lub złe poświadczenia — uruchom: lv-executor-cycle.sh credentials $slug" ;;
    not_logged_in) ;;  # tylko --check-only bez próby logowania — nic do zgłaszania
    *) notify "LasVegas agent" "$slug: logowanie nie powiodło się ($reason) — zaloguj się w oknie agenta." ;;
  esac
  return 1
}

# Buki sondowane (sts, superbet) z zleceniami w kolejce — dla nich cykl loguje
# ZANIM obudzi model, żeby agent nie tracił sesji LLM na ekran logowania.
queue_bookmakers() {
  printf '%s' "$1" | grep -o '"bookmaker"[[:space:]]*:[[:space:]]*"[a-z0-9-]*"' \
    | sed 's/.*"\([a-z0-9-]*\)"$/\1/' | sort -u | grep -E '^(sts|superbet)$' || true
}

ensure_logins() {
  local slug
  for slug in $(queue_bookmakers "$1"); do
    login_bookmaker "$slug" || true
  done
}

# Wylogowanie PO pracy. Bukmacherzy liczą czas zalogowania do dziennego limitu
# gry (STS: „Osiągnięto dzienny limit czasu gry" po kilku sesjach agenta, 01.09),
# a sesja trzymana między cyklami zjada limit sama. Meldunek `session_closed`
# jest celowy: LasVegas nie robi z niego alarmu, a przy zleceniu cykl loguje
# ponownie. LV_KEEP_SESSION=1 wyłącza (np. gdy buk za każdym razem żąda captchy).
logout_bookmaker() {
  local slug="$1" py state reason detail
  py=$(python_bin) || { log "BŁĄD: brak python3 — nie wyloguję $slug"; return 1; }
  LOGOUT_RESULT=$(BU_CDP_URL="$CDP" "$py" "$LOGIN_PY" "$slug" --logout 2>> "$LOG") || true
  state=$(json_field "$LOGOUT_RESULT" state)
  reason=$(json_field "$LOGOUT_RESULT" reason)
  detail=$(json_field "$LOGOUT_RESULT" detail)
  if [ "$state" = "logged_out" ]; then
    log "wylogowanie $slug: ${detail:-ok}"
    lv_api 15 session "$slug" logged_out "" session_closed "${detail:-wylogowano po cyklu}" > /dev/null 2>&1 \
      || log "OSTRZEŻENIE: nie zgłoszono session_closed $slug do LasVegas"
    return 0
  fi
  log "OSTRZEŻENIE: $slug nadal zalogowany po próbie wylogowania (${reason:-brak wyniku}${detail:+ — $detail})"
  return 1
}

logout_bookmakers() {
  local slug
  [ "${LV_KEEP_SESSION:-0}" = "1" ] && { log "LV_KEEP_SESSION=1 — sesje zostają otwarte"; return 0; }
  for slug in $(queue_bookmakers "$1"); do
    logout_bookmaker "$slug" || true
  done
}
notify() {
  # $1 = tytuł, $2 = treść. Log jest kanałem pewnym, powiadomienie tylko wygodnym:
  # z timera bez sesji graficznej notify-send nie ma gdzie dostarczyć komunikatu,
  # a sprawa wymagająca człowieka musi zostać zapisana tak czy inaczej.
  log "POWIADOMIENIE: $1 — $2"
  if [ "$LV_OS" = "Darwin" ]; then
    osascript -e "display notification \"$2\" with title \"$1\" sound name \"Basso\"" > /dev/null 2>&1 || true
  elif command -v notify-send > /dev/null 2>&1; then
    notify-send "$1" "$2" > /dev/null 2>&1 || true
  fi
}

# Ostrzeżenie nie może wyglądać jak zwykły log: żółte („orange") na tty,
# a w pliku prefiks OSTRZEŻENIE — inaczej samoaktualizacja ginie w szumie cyklu.
warn() {
  printf '\033[33m%s\033[0m\n' "$*" >&2
  log "OSTRZEŻENIE: $*"
}

# Żywy lock = trwa bieg agenta. Podmiana plików skilla w jego trakcie zostawiłaby
# agenta w połowie starej procedury (część kroków z nowego pliku, część z pamięci
# sesji), dlatego aktualizacja czeka na wolny cykl.
lock_alive() {
  local pid
  pid=$(cat "$LOCK/pid" 2>/dev/null || echo "")
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# Sprawdzenie zdalnego VERSION i podmiana skilla. Brak sieci, nieosiągalny tap
# albo błąd instalacji = cichy powrót (0): cykl działa dalej na tym, co ma.
# Po UDANEJ instalacji „exec" restartuje bieg na nowym kodzie — nowy lokalny
# VERSION == zdalny, więc kolejne sprawdzenie nic już nie zainstaluje (brak pętli).
skill_selfupdate() {
  local force="${1:-}" remote local_ver
  remote=$(curl -fsS --max-time 10 "$UPDATE_URL" 2>/dev/null | tr -d '[:space:]') || true
  if [ -z "$remote" ]; then
    [ -n "$force" ] && echo "nie mogę sprawdzić zdalnej wersji (brak sieci?) — bez zmian"
    return 0
  fi
  local_ver=$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null) || local_ver=""
  if [ "$local_ver" = "$remote" ]; then
    [ -n "$force" ] && echo "aktualne ($local_ver)"
    return 0
  fi
  log "nowa wersja skilla: ${local_ver:-brak VERSION} → $remote — instaluję z tapa"
  # Ta sama para komend co w instalatorze (install.sh, krok 3): tap add z || true,
  # bo tap zwykle już jest; --force, bo bez niego stary skill zostaje na dysku.
  "$HERMES_BIN" skills tap add PNowakBG/lasvegas-skills >/dev/null 2>&1 || true
  if ! "$HERMES_BIN" skills install --force PNowakBG/lasvegas-skills/lv-executor; then
    warn "instalacja skilla $remote nie udała się — zostaję na ${local_ver:-nieznanej wersji}"
    return 0
  fi
  # Kod wyjścia 0 nie znaczy, że pliki się zmieniły: skaner skilli Hermesa potrafi
  # zablokować instalację i zwrócić zero. Bez tej kontroli cykl logował udaną
  # aktualizację i restartował się przez „exec" na dokładnie tym samym kodzie.
  if [ "$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null)" != "$remote" ]; then
    warn "instalacja zwróciła sukces, ale na dysku dalej jest ${local_ver:-brak VERSION} zamiast $remote — zostaję na starym kodzie"
    return 0
  fi
  log "zaktualizowano skill do $remote — restart cyklu na nowym kodzie"
  [ -n "$force" ] && echo "zaktualizowano do $remote"
  # „${SCRIPT_ARGS[@]+…}" — macOS ma bash 3.2, a tam pusta tablica pod „set -u"
  # kończy skrypt błędem (ten sam idiom co przy MODEL_ARGS niżej).
  exec "$0" ${SCRIPT_ARGS[@]+"${SCRIPT_ARGS[@]}"}
}

# Wywoływane na starcie każdego cyklu. Znacznik czasu w $UPDATE_CHECK dławi
# odpytywanie GitHuba do raz na godzinę. Tryb „selfupdate" (ręczny) omija throttle.
selfupdate_check() {
  local now last
  lock_alive && return 0
  now=$(date +%s)
  last=$(cat "$UPDATE_CHECK" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  [ $(( now - last )) -lt "$UPDATE_INTERVAL" ] && return 0
  # Znacznik PRZED próbą: porażka instalacji nie zamieni cyklu w pętlę odpytywań
  # co 5 minut, a udany „exec" nie sprawdzi wersji drugi raz od razu.
  date +%s > "$UPDATE_CHECK" 2>/dev/null || true
  skill_selfupdate || warn "samoaktualizacja nie udała się — cykl działa dalej"
  return 0
}

# Binarka przeglądarki na Linuksie. LV_CHROME_BIN nadpisuje wykrywanie: dystrybucje
# nazywają ją różnie, a Flatpak i Snap chowają ją za własnym wrapperem.
linux_chrome_bin() {
  local candidate
  if [ -n "${LV_CHROME_BIN:-}" ]; then
    command -v "$LV_CHROME_BIN" 2>/dev/null && return 0
    return 1
  fi
  for candidate in google-chrome-stable google-chrome chromium chromium-browser brave-browser; do
    command -v "$candidate" 2>/dev/null && return 0
  done
  return 1
}

# Start okna agenta — te same flagi na obu systemach, różni się tylko sposób
# oddania procesu systemowi. Chrome MUSI przeżyć koniec cyklu: na macOS oddaje go
# LaunchServices (open -n), na Linuksie setsid wyprowadza go z grupy procesów
# usługi. Samo setsid nie wystarcza pod systemd — unit ma KillMode=process,
# inaczej menedżer sprząta cały cgroup razem z przeglądarką.
chrome_spawn() {
  if [ "$LV_OS" = "Darwin" ]; then
    open -na "$CHROME_APP" --args "$@"
    return $?
  fi
  local bin
  bin=$(linux_chrome_bin) || {
    log "BŁĄD: nie znalazłem Chrome ani Chromium. Zainstaluj przeglądarkę albo wskaż ją: LV_CHROME_BIN=/ścieżka/do/chrome"
    return 1
  }
  # Bez sesji graficznej okno nie ma się gdzie otworzyć. Timer systemd startuje
  # w środowisku menedżera użytkownika, które DISPLAY dostaje dopiero po imporcie
  # ze środowiska sesji — lepiej powiedzieć to wprost, niż pozwolić Chrome paść
  # w ciszy i zostawić cykl z pustym CDP.
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    log "BŁĄD: brak sesji graficznej (DISPLAY/WAYLAND_DISPLAY). Uruchom w sesji graficznej: systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY"
    return 1
  fi
  if command -v setsid > /dev/null 2>&1; then
    setsid nohup "$bin" "$@" > /dev/null 2>&1 &
  else
    nohup "$bin" "$@" > /dev/null 2>&1 &
  fi
  return 0
}

ensure_chrome() {
  cdp_alive && return 0
  log "startuję Chrome (profil $PROFILE_DIR) poza grupą procesów cyklu"
  # Trzy flagi anty-throttling: okno agenta bywa zasłonięte/zminimalizowane, a Chrome
  # dławi wtedy timery i renderer — 01.09 co drugi browser_exec kończył się po 30 s
  # timeoutem na stronie STS. Flagi są standardem w automatyzacji, nie dają banera.
  chrome_spawn \
    --remote-debugging-port=9222 \
    --user-data-dir="$PROFILE_DIR" \
    --no-first-run --no-default-browser-check --hide-crash-restore-bubble \
    --disable-background-timer-throttling --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    --window-size=1400,900 \
    --proxy-pac-url="$PAC" \
    about:blank || return 1
  # Zimny start na 8 GB RAM bywa dłuższy niż 20 s (04:10–04:44 siedem cykli pod rząd
  # „CDP nie odpowiada") — czekamy do 60 s.
  for _ in $(seq 1 60); do
    cdp_alive && return 0
    sleep 1
  done
  log "BŁĄD: CDP :9222 nie odpowiada po 60 s"
  return 1
}

# Ten skrypt jest wpisem usługi i JEDYNYM miejscem w skillu, które sięga po
# konfigurację z dysku — lv-api.sh dostaje wszystko podane w środowisku wywołania.
# Pierwszeństwo ma środowisko, żeby dało się je wstrzyknąć z zewnątrz (eksport przy
# diagnostyce, EnvironmentFile, gdy ktoś sobie taki unit dopisze). Gdy go nie ma,
# czytamy konfigurację Hermesa — tak działa i launchd, i domyślny unit systemd,
# bo żaden z nich nie parsuje tego pliku sam.
load_env() {
  # Brak pliku to normalny stan przed instalacją — sed nie ma czym straszyć
  # użytkownika, komunikat należy do wołającego.
  [ -n "${LV_EXECUTOR_TOKEN:-}" ] || LV_EXECUTOR_TOKEN=$(sed -n 's/^LV_EXECUTOR_TOKEN=//p' "$ENV_FILE" 2>/dev/null | tail -1 | tr -d '"'"'"' ')
  [ -n "${LV_API_URL:-}" ] || LV_API_URL=$(sed -n 's/^LV_API_URL=//p' "$ENV_FILE" 2>/dev/null | tail -1 | tr -d '"'"'"' ')
  LV_API_URL="${LV_API_URL:-https://lv.ap2ju.com}"
}

# GET /api/executor/queue — poza listą zleceń zwalnia po stronie serwera zawieszone
# claimy tego urządzenia, więc warto go wołać co cykl nawet bez agenta.
# Cały dostęp do API idzie przez lv-api.sh — to jedyne miejsce, które czyta
# token urządzenia. Ten skrypt nie składa już samodzielnie żadnego żądania.
queue_json() {
  lv_api 15 orders
}

case "${1:-cycle}" in
  selfupdate)
    # Ręczne wymuszenie: bez throttle i zawsze z komunikatem — „aktualne (X)"
    # albo „zaktualizowano do X" (po udanej instalacji exec restartuje ten tryb,
    # co widać jako „aktualne").
    skill_selfupdate force
    exit $?
    ;;
  ensure-chrome)
    ensure_chrome && echo "Chrome agenta działa: $CDP (profil $PROFILE_DIR)"
    exit $?
    ;;
  login)
    # login [superbet|sts|https://adres] — otwiera okno logowania bukmachera
    # i czeka na świadome potwierdzenie użytkownika. Samo otwarcie okna nie
    # znaczy, że sesja jest ważna — dopiero ENTER użytkownika jest dowodem,
    # a agent ma o tym zameldować LasVegas (Krok 0 SKILL.md).
    # Walidacja PRZED dotknięciem przeglądarki: zła flaga/URL ma skończyć się
    # instrukcją, nie uruchomieniem Chrome z „--headless".
    case "${2:-sts}" in
      # UWAGA (2026-09-13, produkcja): „/logowanie" NIE istnieje — Superbet
      # rzuca tam własną stronę 404 („Spalony!"). Logowanie to modal pod
      # przyciskiem „zaloguj" (.e2e-login) w nagłówku STRONY GŁÓWNEJ, więc
      # otwieramy dom i user klika „zaloguj".
      superbet) LOGIN_SLUG=superbet; LOGIN_URL="https://superbet.pl/" ;;
      sts) LOGIN_SLUG=sts; LOGIN_URL="https://www.sts.pl" ;;
      -*)
        # Flaga zamiast adresu („login --headless") przeszłaby do „open … --args"
        # i włączyła tryb Chrome, którego agent nie kontroluje.
        echo "użycie: $0 login [superbet|sts|https://adres] — URL musi zaczynać się od https://" >&2
        exit 2
        ;;
      https://*) LOGIN_SLUG=""; LOGIN_URL="$2" ;;
      *)
        # Zgodność: „login https://…" działało wcześniej; wszystko inne to literówka.
        echo "użycie: $0 login [superbet|sts|https://adres] — URL musi zaczynać się od https://" >&2
        exit 2
        ;;
    esac
    ensure_chrome || exit 1
    # Najpierw skrypt: gdy poświadczenia są zapisane, logowanie nie potrzebuje
    # człowieka. Okno dla użytkownika otwieramy dopiero, gdy skrypt oddał sprawę
    # (captcha, kod SMS, brak poświadczeń).
    if [[ -n "$LOGIN_SLUG" ]]; then
      load_env
      if [ -n "${LV_EXECUTOR_TOKEN:-}" ] && login_bookmaker "$LOGIN_SLUG"; then
        echo "Zalogowano do $LOGIN_SLUG skryptem i zgłoszono do LasVegas: $LOGIN_RESULT"
        exit 0
      fi
      echo "Logowanie skryptem nie wystarczyło: ${LOGIN_RESULT:-brak wyniku}"
      echo "Otwieram okno przeglądarki agenta — dokończ logowanie sam."
    fi
    # Ta sama aplikacja + ten sam user-data-dir → Chrome przekazuje URL działającej
    # instancji agenta (process singleton) i kończy nowy proces.
    chrome_spawn --user-data-dir="$PROFILE_DIR" "$LOGIN_URL" || exit 1
    echo "Zaloguj się w oknie przeglądarki agenta (profil $PROFILE_DIR) — to osobny Chrome, nie Twój zwykły."
    if [[ "$LOGIN_SLUG" == "superbet" ]]; then
      echo "Na superbet.pl kliknij „zaloguj” w nagłówku — formularz logowania to modal, nie osobna strona."
    fi
    # Bez tty (launchd, pipe) nie ma kogo zapytać o potwierdzenie — kończymy
    # sukcesem, żeby nie blokować cyklu; logowanie zweryfikuje Krok 0 agenta.
    if [[ -t 0 ]]; then
      read -r -p "Zaloguj się, a potem wciśnij ENTER (wpisany tekst jest ignorowany): " _ < /dev/tty || true
      # ENTER jest dowodem zalogowania (tak stanowi procedura wyżej), więc meldunek
      # do LasVegas idzie od razu — użytkownik nie ma po co przepisywać drugiej
      # komendy. Do 14.09 drukowaliśmy wskazówkę z lv-api.sh, ale ten skrypt
      # świadomie nie czyta już konfiguracji z dysku, więc wklejona wprost kończyła
      # się „brak LV_EXECUTOR_TOKEN w środowisku".
      if [[ -n "$LOGIN_SLUG" ]]; then
        load_env
        if [ -z "${LV_EXECUTOR_TOKEN:-}" ]; then
          echo "Nie znalazłem tokenu urządzenia — meldunek pominięty. Zainstaluj agenta komendą z LasVegas (Podłącz agenta)." >&2
        elif lv_api 15 session "$LOGIN_SLUG" logged_in > /dev/null; then
          echo "Zgłoszone do LasVegas: $LOGIN_SLUG zalogowany."
          echo "Saldo (opcjonalne, odświeża lustro konta w LasVegas): $0 session $LOGIN_SLUG logged_in 130.50"
        else
          echo "Nie udało się zgłosić stanu do LasVegas. Powtórz: $0 session $LOGIN_SLUG logged_in" >&2
        fi
      else
        echo "Zgłoś zalogowanie: $0 session <slug> logged_in <saldo_opcjonalnie>"
      fi
    else
      echo "Brak terminala (tty) — nie czekam na potwierdzenie. Zaloguj się, a agent sprawdzi to w Kroku 0."
    fi
    exit 0
    ;;
  credentials)
    # credentials <sts|superbet> — zapis loginu i hasła do pliku poświadczeń
    # (chmod 600). Hasło czytane bez echa z terminala, nigdy z argumentów.
    # Po zapisie próbne logowanie skryptem — użytkownik od razu wie, czy działa.
    case "${2:-}" in
      sts) CRED_SLUG=sts; CRED_KEY=LV_STS ;;
      superbet) CRED_SLUG=superbet; CRED_KEY=LV_SUPERBET ;;
      *) echo "użycie: $0 credentials <sts|superbet>" >&2; exit 2 ;;
    esac
    [[ -t 0 ]] || { echo "credentials wymaga terminala (hasło czytane bez echa)." >&2; exit 2; }
    read -r -p "Login/e-mail do $CRED_SLUG: " CRED_LOGIN < /dev/tty
    read -r -s -p "Hasło do $CRED_SLUG (bez echa): " CRED_PASS < /dev/tty; echo
    [[ -n "$CRED_LOGIN" && -n "$CRED_PASS" ]] || { echo "Login i hasło nie mogą być puste." >&2; exit 2; }
    umask 077
    touch "$CREDENTIALS_FILE"
    chmod 600 "$CREDENTIALS_FILE"
    # Podmiana per klucz (jak instalator z tokenem) — reszta pliku nietknięta.
    CRED_TMP=$(mktemp "$HERMES_HOME/lv-bookmakers.XXXXXX")
    grep -v -E "^${CRED_KEY}_(LOGIN|PASSWORD)=" "$CREDENTIALS_FILE" > "$CRED_TMP" 2>/dev/null || true
    printf '%s_LOGIN=%s\n%s_PASSWORD=%s\n' "$CRED_KEY" "$CRED_LOGIN" "$CRED_KEY" "$CRED_PASS" >> "$CRED_TMP"
    mv "$CRED_TMP" "$CREDENTIALS_FILE"
    chmod 600 "$CREDENTIALS_FILE"
    unset CRED_PASS
    echo "Zapisano poświadczenia $CRED_SLUG w $CREDENTIALS_FILE (tylko Ty masz do niego dostęp)."
    load_env
    if [ -z "${LV_EXECUTOR_TOKEN:-}" ]; then
      echo "Brak tokenu urządzenia — pomijam próbne logowanie. Zainstaluj agenta komendą z LasVegas."
      exit 0
    fi
    echo "Próbne logowanie skryptem…"
    ensure_chrome || exit 1
    if login_bookmaker "$CRED_SLUG"; then
      echo "Działa: $LOGIN_RESULT"
      # Sesja próbna nie ma prawa wisieć i zjadać limitu czasu gry.
      logout_bookmaker "$CRED_SLUG" && echo "Sesja próbna zamknięta (limit czasu gry u buka)."
      exit 0
    fi
    echo "Nie udało się: ${LOGIN_RESULT:-brak wyniku}"
    echo "Gdy powód to captcha/kod SMS: $0 login $CRED_SLUG (okno dla Ciebie)."
    exit 1
    ;;
  logout)
    # logout <sts|superbet> — zamknij sesję u buka i zamelduj `session_closed`.
    case "${2:-}" in
      sts|superbet) ;;
      *) echo "użycie: $0 logout <sts|superbet>" >&2; exit 2 ;;
    esac
    load_env
    [ -n "${LV_EXECUTOR_TOKEN:-}" ] || { echo "BŁĄD: brak tokenu urządzenia w $ENV_FILE." >&2; exit 2; }
    ensure_chrome || exit 1
    if logout_bookmaker "$2"; then
      echo "Sesja $2 zamknięta: $LOGOUT_RESULT"
      exit 0
    fi
    echo "Nie udało się wylogować: ${LOGOUT_RESULT:-brak wyniku}" >&2
    exit 1
    ;;
  session)
    # Meldunek stanu logowania z terminala. Istnieje, bo lv-api.sh bierze token
    # wyłącznie ze środowiska, a użytkownik nie ma powodu eksportować zmiennych
    # ręcznie: wpis usługi zna konfigurację i podaje ją dalej.
    # Użycie: lv-executor-cycle.sh session <slug> <logged_in|logged_out> [saldo]
    load_env
    if [ -z "${LV_EXECUTOR_TOKEN:-}" ]; then
      echo "BŁĄD: brak tokenu urządzenia w $ENV_FILE — zainstaluj agenta komendą z LasVegas (Podłącz agenta)." >&2
      exit 2
    fi
    shift
    lv_api 15 session "$@"
    exit $?
    ;;
  cycle) ;;
  *)
    echo "użycie: $0 [cycle|selfupdate|ensure-chrome|credentials <sts|superbet>|login [superbet|sts|https://adres]|logout <sts|superbet>|session <slug> <logged_in|logged_out> [saldo] [reason] [detail]]" >&2
    exit 2
    ;;
esac

# --- samoaktualizacja skilla (raz na godzinę, poza żywym biegiem) ------------
# Przed lockiem, bo „skill_selfupdate" kończy się „exec" na nowym kodzie —
# trap EXIT, który zwalnia lock, nie zdążyłby się wykonać.
selfupdate_check

# --- singleton lock (mkdir jest atomowe) -----------------------------------
# Lock niesie PID cyklu. Do 2026-09-01 „stary" lock (>20 min) był kasowany
# w ciemno — a realny przebieg z dwoma zleceniami trwa 20–30 min. Drugi agent
# na tej samej przeglądarce i tym samym kuponie to podwójny zakład albo
# rozjechany kupon. Żywy proces = lock ważny bez względu na wiek; wiek liczy
# się tylko, gdy po procesie nie ma śladu (crash bez trap EXIT).
if ! mkdir "$LOCK" 2>/dev/null; then
  lock_pid=$(cat "$LOCK/pid" 2>/dev/null || echo "")
  lock_age=$(( $(date +%s) - $(dir_mtime "$LOCK") ))
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

# Model pytamy ZAWSZE — także przy pustej kolejce. To jedno małe żądanie, a
# pieczątka z niego jest jedynym sygnałem, po którym LasVegas odróżnia agenta
# aktualnego od starszej generacji. Bez tego urządzenie, które nie ma zleceń,
# nie zawołałoby tego endpointu i wyglądało w UI na nieaktualne (fałszywy alarm).
# Ręczne LV_MODEL (jeśli ustawione) wygrywa i wtedy o nic nie pytamy.
[ -n "$LV_MODEL" ] || agent_config || true

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
log "kolejka: $ORDERS zleceń — budzę agenta (${LV_MODEL:-model domyślny Hermesa} @ ${LV_PROVIDER:-?})"

# --- przeglądarka + logowanie + cykl agenta ------------------------------------
ensure_chrome || { log "BŁĄD: przeglądarka agenta nie wystartowała — cykl pominięty"; exit 1; }

# Logowanie PRZED sesją LLM: skrypt z poświadczeniami, model nic nie widzi.
# Porażka nie zatrzymuje cyklu — agent dostanie zlecenia z loginBlocked i
# zajmie się resztą kolejki, a LasVegas i użytkownik znają już powód.
ensure_logins "$QUEUE"

# UWAGA: nie używać „exec" — zastąpiłby shell i trap EXIT nigdy by nie zwolnił locka.
# „${ARR[@]+…}" zamiast zwykłego rozwinięcia w cudzysłowach: macOS ma bash 3.2,
# a tam pusta tablica pod
# „set -u" kończy skrypt błędem „unbound variable" (model z API może nie przyjść).
MODEL_ARGS=()
[ -n "$LV_MODEL" ] && MODEL_ARGS=(-m "$LV_MODEL" --provider "$LV_PROVIDER")
# Token i adres API w środowisku TEJ JEDNEJ komendy: skill woła lv-api.sh z sesji
# Hermesa, a lv-api.sh świadomie nie czyta konfiguracji z dysku (skaner skilli
# traktuje sięganie skilla do magazynu poświadczeń jak exfiltrację). Bez tego
# agent zależałby od tego, czy Hermes sam eksportuje swój .env do narzędzi.
LV_EXECUTOR_TOKEN="$LV_EXECUTOR_TOKEN" LV_API_URL="$LV_API_URL" \
  "$HERMES_BIN" chat --toolsets skills,terminal,browser ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
  -q "Załaduj skill lv-executor (skill_view) i wykonaj zaległe zlecenia dokładnie wg jego procedury" \
  2>&1 | tee -a "$LOG" > "$LAST_RUN"
rc=${PIPESTATUS[0]}

# Sesje u buków zamykamy zaraz po pracy — niezależnie od tego, jak skończył agent.
logout_bookmakers "$QUEUE"

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
