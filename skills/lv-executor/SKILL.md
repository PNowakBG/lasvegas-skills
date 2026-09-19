---
name: lv-executor
description: Egzekwuje zlecenia zakładów z LasVegas u bukmacherów (Superbet, STS, Betclic, Betfan)
version: 1.5.10
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [betting, automation, lasvegas]
    requires_toolsets: [terminal, browser]
    config:
      - key: lvexecutor.api_url
        description: "URL instancji LasVegas"
        default: "https://lv.ap2ju.com"
        prompt: "Adres Twojej instancji LasVegas"
---

# LV Executor — egzekucja zakładów wg rekomendacji LasVegas

## When to Use

Uruchamiaj ten skill, gdy masz wykonać zaległe zlecenia zakładów z LasVegas:
przy każdym uruchomieniu cyklu (cron/autostart po instalatorze), albo gdy
użytkownik wpisze `/lv-executor`. Kryteria typowania (edge, stawki, limity)
żyją w LasVegas w parametrach funduszu — skill ich NIE zmienia i NIE podejmuje
decyzji inwestycyjnych. Skill to tylko ręce: poll → claim → weryfikacja → postawienie → raport.

Wymaga: `LV_EXECUTOR_TOKEN` (token urządzenia z LasVegas) i włączonego
real-profile browsing w Hermesie (`browser.use_real_profile: true`).
Logowanie do bukmacherów robi za Ciebie `scripts/lv-login.py` — z poświadczeń
zapisanych lokalnie przez `lv-executor-cycle.sh credentials <slug>`. Ty tych
poświadczeń NIE czytasz i NIE wpisujesz.

Token przychodzi **wyłącznie ze środowiska procesu** — skrypty skilla nie czytają
plików z sekretami. Wstrzykuje go `lv-executor-cycle.sh` (wpis usługi: launchd na
macOS, systemd --user na Linuksie) albo sesja Hermesa, która przekazuje narzędziom
własne środowisko.

## Procedure

Na początku załaduj `references/verification-rules.md` (twarde reguły weryfikacji)
i, przy pierwszym zleceniu danego bukmachera, `references/learning-procedure.md`
(procedura uczenia się playbooka).

Pętla egzekucji — wykonuj SEKWENCYJNIE, jedno zlecenie po drugim:

0. **Weryfikacje zaległe (PRZED poll normalnej kolejki).**
   `bash scripts/lv-api.sh verifications` — zlecenia, których poprzednia próba
   padła bez potwierdzenia, więc **kupon mógł wejść u buka**. Dla każdego:
   otwórz buka (wg playbooka) i sprawdź otwarte zakłady: szukaj meczu
   (`homeTeam` vs `awayTeam`), stawki `stake` i rynku `market`/`outcome` z
   zlecenia. Kupon jest na koncie → `bash scripts/lv-api.sh verify <betId> true
   <ticketId>` (podaj PRAWDZIWY numer kuponu z buka). Kuponu nie ma (i saldo
   bez zmian) → `bash scripts/lv-api.sh verify <betId> false "" "brak kuponu w
   otwartych zakładach, saldo bez zmian"` — zlecenie wróci do kolejki.
   **Nigdy nie stawiaj ponownie zlecenia z listy weryfikacji bez tego
   rozstrzygnięcia** — retraj bez sprawdzenia postawił duplikat kuponu
   (prod 2026-09-06: Napoli – Arsenal, dwa identyczne kupony u STS).

**Krok 0 — logowanie bukmacherów.** Pobierz kolejkę SAM na starcie:
`bash scripts/lv-api.sh orders` — nie czekaj z tym na krok 1 (pusta lista =
koniec cyklu, bez otwierania przeglądarki). Serwer oddaje w niej także zlecenia
buków, o których wie, że TWOJA przeglądarka jest wylogowana (albo Twój raport
`logged_in` ma więcej niż **30 minut**, `LOGIN_STATE_FRESH_MS`); takie zlecenia
mają `loginBlocked: true`, a ich `claim` kończy się 404 — dlatego najpierw ustal
stan logowania, zanim cokolwiek podbijesz. Stan jest PER KONSUMENT: rozszerzenie
w Chrome użytkownika ma własny, Ty własny. Bramka dotyczy sondowanych buków:
**superbet** i **sts** (`betclic-pl` i `betfan` nie są sondowane; brak raportu
= `unknown` też nie blokuje).

Cykl (`lv-executor-cycle.sh`, macOS/Linux) loguje buki z kolejki ZANIM Cię
obudzi i sam melduje `session`. Gdy mimo to widzisz `loginBlocked: true`
(Windows bez cyklu, sesja wygasła w trakcie), dla każdego takiego buka uruchom:
`python3 scripts/lv-login.py <slug>` (Windows: `python scripts\lv-login.py <slug>`).
Skrypt sam sprawdza sesję i w razie potrzeby loguje z zapisanych poświadczeń;
na stdout dostajesz JEDNĄ linię JSON — nigdy login ani hasło:
- `"state":"logged_in"` → `bash scripts/lv-api.sh session <slug> logged_in <balance>`
  (`balance` z JSON, gdy nie jest null) — raport odblokowuje zlecenia tego buka;
  claimuj je normalnie,
- `"state":"logged_out"` → `bash scripts/lv-api.sh session <slug> logged_out "" <reason> "<detail>"`
  (reason i detail PRZEPISZ z JSON: `captcha`, `two_factor`, `bad_credentials`,
  `no_credentials`, `login_form_not_found`, `login_error`), powiedz użytkownikowi
  w czacie jednym zdaniem, co ma zrobić (captcha/kod SMS → zalogować się w oknie
  agenta; złe lub brak poświadczeń → `lv-executor-cycle.sh credentials <slug>`)
  i POMIŃ w tym cyklu zlecenia tego buka — `claim` odrzuciłby je 404. Zlecenia
  zostają w kolejce; podejmie je następny cykl po zalogowaniu. LasVegas sam
  wysyła użytkownikowi powiadomienie z tym powodem.

Krok 5 powtarza weryfikację na ekranie tuż przed kuponem — bramka patrzy na
pieczątkę z raportu, nie na to, co widzisz teraz; ten sam meldunek
(`session … logged_in`) wyślij po udanym postawieniu, gdy saldo się zmieniło.

1. **Poll.** `bash scripts/lv-api.sh orders` → lista zleceń. Pusta lista: koniec,
   nic nie rób.
2. **Kill switch PRZED każdym zleceniem.** `bash scripts/lv-api.sh kill-switch` —
   gdy `halted: true` albo reguła danego bukmachera ma `enabled: false`: koniec
   biegu, nic nie stawiaj.
   **Skrypt był pierwszy.** Zanim dostaniesz kolejkę, cykl próbował postawić
   każde zlecenie STS/Superbet skryptem `scripts/lv-place.py` (bez modelu:
   wyszukiwarka → strona meczu → blok rynku → kurs → stawka → weryfikacja →
   „Postaw”). Zlecenia, które zostały, skrypt oddał z powodem `needs_model`
   (treść w poleceniu cyklu: „Skrypt lv-place.py NIE poradził sobie…”) — zacznij
   od tego kroku wg playbooka. Gdy naprawisz ścieżkę (nowa etykieta rynku, inny
   selektor), DOKUMENTUJ ją w playbooku po biegu: developer przenosi ją do skryptu.
   Sam też możesz użyć skryptu na sucho: `python3 scripts/lv-place.py prepare < zlecenie.json`
   (JSON zlecenia z `orders`) — kończy na zweryfikowanym kuponie bez klikania.
3. **Claim.** `bash scripts/lv-api.sh claim <betId>` — każdy błąd = pomiń to
   zlecenie i idź do następnego, BEZ śledztwa (17.09: agent spędził cały bieg
   na dociekaniu „kto mi wziął zlecenie”). Treść odpowiedzi mówi dlaczego:
   409 „czeka na Twoje zalogowanie u X” → zaloguj się (Krok 0) i wróć do claimu;
   404 ze stanem („przerwane: mecz się zaczął”, „stawia je inny wykonawca”,
   „konto wyłączone”) → zlecenie nie jest Twoje, zostaw je.
4. **Playbook.** Załaduj `playbooks/<slug-bukmachera>.md` przez skill_view.
   Gdy nie istnieje → tryb EKSPLORUJ z `references/learning-procedure.md`
   (pierwszy kontakt z bukmacherem), potem DOKUMENTUJ playbook i kontynuuj.
5. **Weryfikacja logowania.** Otwórz `eventUrl` (deeplink z zlecenia; gdy
   `eventUrlKind: "home"` — otwórz stronę główną i WYSZUKAJ mecz wg
   `homeTeam`/`awayTeam` z playbooka). Sprawdź wg playbooka, czy jesteś
   zalogowany. Niezalogowany → `python3 scripts/lv-login.py <slug>` (jak w Kroku 0)
   i po `logged_in` wróć do zlecenia (przeładuj `eventUrl`). Gdy skrypt oddał
   `logged_out` → `bash scripts/lv-api.sh failed <betId> not_logged_in "<reason>: <detail>"`
   i następne zlecenie: serwer NIE liczy tego jako próby — zlecenie wraca do
   kolejki, a kolejka tego buka zamyka się dla Ciebie do następnego raportu
   `logged_in` (nie wysyłaj po tym osobnego `session logged_out`; użytkownik
   dostaje powiadomienie z powodem). Zalogowanie udane → zaraportuj
   `bash scripts/lv-api.sh session <slug> logged_in [saldo]` (świeży raport
   obowiązuje 30 minut, więc sesja z tego biegu pokrywa kolejne cykle).
6. **Budowa kuponu.** Znajdź rynek i typ zlecenia (market/outcome/selectionDetail;
   dopasowanie rozmyte: ignoruj wielkość liter, polskie znaki, „–" vs „-").
   Klucze linii (`ou…`, `ht_ou…`, `corners_ou…`, `cards_ou…`) to liczba z usuniętą
   kropką: `ou2` = 2,0, `ou25` = 2,5, `ou05` = 0,5, `corners_ou105` = 10,5 — jedna
   cyfra to liczba całkowita, końcowe „5" po innej cyfrze to połówka. Linia musi
   być DOKŁADNA; brak linii = `skipped line_not_found`, nigdy „najbliższa".
   Ustaw stawkę DOKŁADNIE `stake` (nigdy więcej, nigdy mniej, nigdy „wartość
   sugerowaną" przez bukmachera). Zignoruj banery bonusowe i boosty kursowe —
   nie klikaj niczego, co zmienia kupon.
7. **Weryfikacja kuponu na ekranie** wg `references/verification-rules.md`:
   kurs na ekranie vs `odds` (niższy o >2 % → `odds_drift`; wyższy → bierz), stawka vs `stake`, rynek/typ vs
   zlecenie, liczba selekcji w kuponie == 1 (lub liczba nóg AKO). JAKA KOLWIEK
   rozbieżność → NIE klikaj „Postaw" → `bash scripts/lv-api.sh skipped <betId> odds_drift "kurs 2.15 → 1.60"`
   (lub adekwatny powód z detail) → następne zlecenie.
8. **Postawienie.** Kliknij „Postaw"/„Zakład" zgodnie z playbookiem. Po
   potwierdzeniu odczytaj numer kuponu wg playbooka. Gdy bukmacher go nie
   pokazuje, raportuj bez numeru (`-` w miejscu ticketId) — NIGDY nie wpisuj
   betId jako numeru kuponu.
9. **Raport.** `bash scripts/lv-api.sh placed <betId> <ticketId> <actualOdds> <actualStake> <balanceBefore> <balanceAfter>`
   — salda odczytane wg playbooka przed i po postawieniu, liczby z kropką
   (`130,50 zł` → `130.50`). Bez sald serwer nie uzgodni budżetu i nie odświeży
   salda konta w LasVegas.
   **Techniczna porażka PRZED kliknięciem „Postaw”** (okno zasłania kupon, strona
   wygląda inaczej, timeout, błąd strony, nie da się dojść do meczu): najpierw
   ponów RAZ w tym samym biegu — przeładuj `eventUrl`, zamknij okna (lista
   znanych okien jest w `~/.hermes/lv-agent-config.json` → `overlays.<slug>`;
   nieznane zamknij przez „X”/„Zamknij”/Escape i ZGŁOŚ:
   `bash scripts/lv-api.sh overlay <slug> "<opis>" "<selektor CSS>" ["<tekst przycisku>"]`
   — od następnego cyklu zamyka je skrypt logowania u wszystkich użytkowników)
   i wykonaj kroki 6–7 ponownie. Druga porażka → `bash scripts/lv-api.sh failed <betId> <kod> "<szczegół>"`
   z kodem technicznym: `overlay_blocked`, `selftest_failed_dom_changed`,
   `page_error`, `timeout`, `navigation_failed`, `browser_error`, `event_not_found`.
   Serwer NIE wysyła takiego zlecenia na weryfikację (nic nie kliknięto) — wraca
   do kolejki od razu i dostaje kolejną próbę w następnym cyklu, po trzeciej
   kończy jako FAILED z powiadomieniem. Tych kodów NIGDY nie używaj po
   kliknięciu „Postaw” — wtedy obowiązuje akapit niżej.
   Błąd w kroku 7-8 PO kliknięciu (albo niepewność, czy kupon wszedł): NIE raportuj porażki od razu — najpierw tryb SAMONAPRAWY
   playbooka (`references/learning-procedure.md`); dopiero druga porażka pod rząd
   → `bash scripts/lv-api.sh failed <betId> <powód> "<co dokładnie pokazał bukmacher>"` i powiadom
   użytkownika w czacie. Detail jest obowiązkowy jak przy `skipped` — komunikat buka
   (np. „Dzienny limit czasu gry osiągnięty") to jedyna diagnoza, jaką zobaczy właściciel.
   Zlecenie trafi wtedy na listę weryfikacji (`verifications`) — rozstrzygnij je
   przy najbliższym biegu zanim cokolwiek postawisz (punkt 0 — weryfikacje zaległe).
   **`placed` zwróciło 404 („Nie znaleziono zlecenia do potwierdzenia”) po
   realnym kliknięciu:** serwer zamknął zlecenie w trakcie biegu (rozszerzenie,
   kill switch, gwizdek), ale kupon jest na koncie. NIE stawiaj drugi raz i nie
   pisz własnego klienta HTTP — treść błędu widać w wyjściu `lv-api.sh`. Sprawdź
   saldo i „Moje kupony”, potem:
   `bash scripts/lv-api.sh attach <betId> <ticketId> <kurs> <stawka> [saldoPrzed] [saldoPo]`
   — zlecenie dostaje kupon i trafia do ksiąg. 404 na `claim` („Zlecenie nie
   czeka już w kolejce”) = zlecenie zamknięte przed Twoją próbą: pomiń je.

10. **Wylogowanie po pracy.** Bukmacherzy liczą CZAS zalogowania do dziennego
    limitu gry (STS: „Osiągnięto dzienny limit czasu gry" po kilku sesjach
    agenta — 01.09), więc sesja nie ma prawa wisieć między cyklami. Na macOS
    i Linuksie robi to cykl po Twoim biegu. Gdy pracujesz bez cyklu (Windows,
    wywołanie ręczne), po ostatnim zleceniu dla KAŻDEGO buka, u którego byłeś
    zalogowany: `python3 scripts/lv-login.py <slug> --logout` (Windows:
    `python scripts\lv-login.py <slug> --logout`), a po `"state":"logged_out"`
    zamelduj `bash scripts/lv-api.sh session <slug> logged_out "" session_closed "wylogowano po cyklu"`.
    To wylogowanie jest celowe — LasVegas nie robi z niego alarmu, a przy
    następnym zleceniu logujesz się ponownie skryptem (Krok 0). Nie zostawiaj
    sesji „na zapas".

Twarde zakazy (obowiązują zawsze, nawet gdy zlecenie „wisi"):
- NIE otwieraj nowych kart na całość biegu — zlecenie otwieraj przez `goto_url`
  w istniejącej karcie przeglądarki; `new_tab` tylko gdy nie ma żadnej karty.
  Na STARCIE biegu posprzątaj karty bukmacherów z poprzednich biegów:
  `curl http://localhost:9222/json` → zamknij każdą kartę poza jedną roboczą
  (`curl http://localhost:9222/json/close/<id>`). Każda dodatkowa karta ciężkiej
  strony bukmachera mnoży CPU i potrafi zawiesić kontrolę przeglądarki.
- NIE stawiaj bez pozytywnego kill-switcha z kroku 2.
- NIE stawiaj stawki innej niż `stake` z zlecenia.
- NIE stawiaj, gdy kupon nie przeszedł pełnej weryfikacji z kroku 7.
- NIE wpisuj haseł sam, NIE czytaj i NIE wypisuj pliku poświadczeń
  (`lv-bookmakers.env`) ani jego zawartości — logowanie wykonuje WYŁĄCZNIE
  `scripts/lv-login.py`, a captcha/kod SMS rozwiązuje użytkownik w otwartym oknie.
- NIE otwieraj stron poza domeną zlecenia (bukmacher) podczas egzekucji zleceń.

## Pitfalls

- Chrome 136+ blokuje zdalne debugowanie domyślnego profilu — Hermes steruje
  migawką profilu (`~/.hermes/lv-browser-profile/`), NIE używaj `/browser connect`
  na domyślnym profilu.
- Windows: resync real-profile wymaga CAŁKOWITEJ zamkniętej przeglądarki
  (też instancja tray/background). Jeśli sesja wychodzi niezalogowana — najpierw
  to sprawdź.
- **Pole stawki bywa WSTĘPNIE WYPEŁNIONE** (Superbet: ostatnia/proponowana
  kwota, 17.09: 45,22 zł; STS: „ulubiona stawka” 12,43 zł). Zawsze wyczyść pole,
  wpisz `stake`, odczytaj kwotę z KUPONU („STAWKA … PLN”) i z przycisku
  („Postaw 10,00 zł”). Inna kwota niż `stake` → NIE klikaj i powtórz wpisanie;
  nigdy nie raportuj `placed` z inną stawką niż w zleceniu — serwer zatrzymuje
  wtedy automat u tego buka i alarmuje użytkownika (17.09: kupon za 45,22 zł
  zamiast 10 zł).
- Playbook uzupełniaj PO obsłużeniu wszystkich zleceń z kolejki (przed
  wylogowaniem, krok 10), nigdy między zleceniami: 17.09 zapis playbooka
  Superbet zabrał 29 s w środku biegu, a każde zlecenie czeka na to samo okno
  przed meczem. Wyjątek: samonaprawa, bez której następne zlecenie nie przejdzie.
- Popupy cookies/RODO i inne okna: skrypt logowania (Krok 0) zamyka znane
  z rejestru LasVegas (`overlays` w agent-config); nowe zamknij sam i zgłoś
  `bash scripts/lv-api.sh overlay …` (krok 9) — rejestr jest wspólny dla
  wszystkich użytkowników, więc następnym razem zrobi to skrypt.
- Limity stawek bukmachera (min/max) — gdy buk odrzuca stawkę z powodu limitu,
  to `failed: bookmaker_limit`, nie próbuj zmieniać stawki.
- 2FA/SCA przy płatnościach — nie dotyczy samych zakładów, ale wylogowanie
  po nieaktywności zdarza się często; wróć do kroku 5 (`lv-login.py` zaloguje
  ponownie; przy captchy/2FA oddaje sprawę użytkownikowi z powodem).
- Snapshots dużych stron bywają obcięte (`truncated: true`) — czytaj pełny
  plik z `~/.hermes/cache/web/` zamiast zgadywać ref-id.

## Verification

Po biegu:
- `bash scripts/lv-api.sh status` / LasVegas UI: zlecenia przeszły QUEUED → PLACED
  (lub FAILED z powodem).
- `bash scripts/lv-api.sh session <slug> <logged_in|logged_out> [saldo] [reason] [detail]`
  wysłane w Kroku 0 (i po udanym postawieniu) — LasVegas widzi świeży stan
  logowania TWOJEJ przeglądarki, saldo konta tego bukmachera i — przy
  `logged_out` — powód, z którego robi powiadomienie i baner „zaloguj agenta".
- Audyt: pełny transkrypt sesji Hermesa (każde wywołanie narzędzia, w tym kod
  `browser_exec`) — `hermes sessions` / `hermes --resume <id>`. Nagrań wideo NIE ma:
  backend browser-use nie nagrywa mimo `browser.record_sessions`.
- Salda kont bukmacherskich widoczne w LasVegas (pętla zwrotna z potwierdzeń).
