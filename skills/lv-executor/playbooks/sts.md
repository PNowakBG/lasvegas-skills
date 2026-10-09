# Playbook: STS (sts.pl)
updated: 2026-09-27   verified: yes   failures: 1

Zweryfikowany E2E 2026-08-29 (Chrome, profil `~/.hermes/lv-browser-profile`, CDP :9222).
Ponownie sprawdzony na zywych zleceniach 2026-09-24 (Puchar Polski) - patrz "Log napraw".
Czytaj tez sekcje "Wyszukiwarka", "Viewport" i Krok 4 - to poprawki z tego biegu.

## Zasady ogólne

- Pracuj na AX tree (`cdp("Accessibility.getFullAXTree")["nodes"]`), nie na screenshotach.
  `backendDOMNodeId` zmienia się co sesję — ZAWSZE odpytuj drzewo na nowo, nigdy nie
  zapisuj node id na później.
- W `js(...)` używaj IIFE `(() => { ... })()` — `const` w top-level koliduje między
  wywołaniami (SyntaxError: already declared).
- Koordynaty kliknięcia: `q = cdp("DOM.getBoxModel", backendNodeId=n)["model"]["content"]`,
  `x, y = sum(q[0::2])/4, sum(q[1::2])/4`, potem `click_at_xy(x, y)`.

## Krok 1: wejście i cookies

1. `new_tab("https://www.sts.pl")` + `wait_for_load()`.
2. Jeśli popup cookies (dialog „KORZYSTAMY Z PLIKÓW COOKIES"): znajdź w AX tree
   `button` o nazwie dokładnie `Akceptuj wszystkie` i kliknij. Popup znika.

## Krok 2: weryfikacja logowania

- Zalogowany: w AX tree (role button/link/StaticText) widać `Wpłata` oraz `Depozyt NNN,NN zł`.
  Odczytaj saldo z `Depozyt` i zaraportuj sesję LasVegas:
  `bash scripts/lv-api.sh session sts logged_in <saldo>` (liczba z kropką:
  `130,50 zł` → `130.50`) — meldunek Kroku 0 SKILL.md; powtórz go po udanym
  postawieniu, gdy saldo się zmieniło.
- Niezalogowany: widać `Zaloguj się` i `Załóż konto`. Uruchom
  `python3 scripts/lv-login.py sts` — skrypt zamyka ekran powitalny
  („Kontynuuj jako gość”), otwiera modal logowania (`[data-testid="input-username"]`,
  `[data-testid="input-password"]`), loguje z zapisanych poświadczeń i czeka na
  `Depozyt`. STS ma NIEWIDZIALNĄ hCaptchę: gdy pokaże wyzwanie, skrypt oddaje
  `reason: captcha` — wtedy loguje się użytkownik w oknie agenta, nie Ty.
  Wynik `logged_out` → `bash scripts/lv-api.sh session sts logged_out "" <reason> "<detail>"`
  i pomiń w tym cyklu zlecenia STS z `loginBlocked: true` (`claim` odrzuciłby
  je 404); przy już claimniętym zleceniu `failed not_logged_in "<reason>"`.
- Odczytaj saldo z tekstu `Depozyt NNN,NN zł` (prawy górny róg) → `balanceBefore`.

## Krok 3: nawigacja do meczu

**Zlecenie niesie `eventUrl`.** Gdy `eventUrlKind` = `event`, otwórz go BEZPOŚREDNIO
(`goto_url(eventUrl)` w bieżącej karcie) — żadnego szukania. Dopiero gdy `eventUrl`
jest null/`home`, szukaj: karta meczu na stronie głównej albo lupa → nazwa drużyny →
wynik meczu. URL meczu ma postać `/kursy/<slug>/<id>` — zweryfikuj, że tytuł strony
zawiera OBIE drużyny.

### Wyszukiwarka (eventUrlKind=home) — sprawdzone 24.09.2026

STS obsługuje szukanie przez parametr URL — to najpewniejsza droga i NIE wymaga
klikania w lupkę ani wysyłania Enter:

1. `goto_url("https://www.sts.pl/szukaj?s=<token>")` + `wait_for_load()`.
   Token = najdłuższy nie-generyczny wyraz nazwy drużyny (`Lechia Gdańsk` → `lechia`,
   `Polonia Warszawa` → `warszawa`, `Puszcza Niepołomice` → `niepolomice`).
   Wpisanie tekstu w `#Search` tez podmienia URL na `/szukaj?s=…` po ~1 s (SPA),
   a `Enter` tego nie psuje — ale wersja z URL-em jest deterministyczna.
2. Poczekaj na `a[href*="/kursy/"]` (kafle wyników; klasy
   `one-ticket-match-tile-link` NIE ma). Kafel pasuje, gdy jego `innerText` zawiera
   najdłuższy token OBIEJ drużyn.
3. Nagłówek "Nadchodzące" + licznik "Wyświetlono N z M wydarzeń" potwierdza wyniki.
4. Wejdź w `href` (`/kursy/<slug>/<id>`) i sprawdź, że `document.title` zawiera OBIE drużyny.

**Gotowość strony meczu — NIE licz rynków progiem > 3.** Mecze dalekie (Puchar Polski
miesiąc naprzód) mają w ofercie TYLKO 2-3 rynki (`Mecz`, `Podwójna szansa`, `Awans`),
więc warunek `bo-match-detail-market-wrapper` > 3 dawał fałszywe
`navigation_failed/event_page_not_rendered` (24.09, zlecenie 9bf67d87 — strona była
poprawna, miała dokładnie 3 bloki). Gotowość sprawdzaj po obecności obu drużyn:
`bo-prematch-detail-info__team-name` (2 sztuki) albo `document.title` z obiema nazwami.

### Viewport: layout mobilny vs desktop — sprawdzone 24.09.2026

Okno agentowego Chrome potrafi wstać mikroskopijne (24.09: `innerWidth` 520 px mimo
`--window-size=1400,900`). Poniżej ~768 px STS przełącza się na layout MOBILNY
(`bs-betslip-mobile`) i wtedy NIE ma prawej kolumny kuponu ani `#Stawka` — kupon
siedzi w pasku "1 Kupon | KURS CAŁKOWITY …" i trzeba go rozwinąć (modal). To wygląda
jak "strona się zmieniła", a to tylko za wąskie okno.

Naprawa (raz na bieg, PRZED budową kuponu):

```python
cdp("Emulation.setDeviceMetricsOverride", width=1400, height=900, deviceScaleFactor=1, mobile=False)
```

Po tym w DOM pojawia się `bs-betslip-desktop` z `#Stawka` i przyciskiem
`Postaw <stawka> zł` — dalej wszystko działa jak w tym playbooku.
Jeśli mimo to trafisz na `bs-betslip-mobile`: rozwiń pasek przyciskiem
`.betslip-bar-default-content__action__expand-betslip button`, wtedy `#Stawka`
(`inputmode=decimal`) i `Postaw …` są w modalu.

**Dyscyplina kart:** pracuj w JEDNEJ karcie (`goto_url`), nie otwieraj nowych
(`new_tab` tylko na samym początku, gdy nie ma żadnej prawdziwej karty). Strona meczu
STS jest ciężka (live-kursy + trackery) — każda dodatkowa karta mnoży CPU i potrafi
zawiesić `Runtime.evaluate`.

**Renderer zawieszony (eval timeout):** gdy `js(...)`/`page_info()` wisi do timeoutu —
karta jest martwa. Zamknij ją przez CDP HTTP: `curl http://localhost:9222/json`,
znajdź `id` karty sts.pl, `curl http://localhost:9222/json/close/<id>`, potem
`new_tab(eventUrl)` i pracuj dalej. Nigdy nie zostawiaj duplikatów kart meczu.

## Krok 4: wybór rynku i kursu

**Najpierw sprawdź, czy rynek w ogóle jest w ofercie tego meczu.** Liczba rynków
zależy od meczu: Ekstraklasa ma ich ~10 (Mecz, Podwójna szansa, Liczba goli, BTTS,
Handicap…), a mecz Pucharu Polski miesiąc naprzód — tylko 3. Szybki test (lupka
rynków w pasku nad listą):

```python
js("(() => { const b=document.querySelector('.bundle-menu__search-button button'); if(b) b.click(); return true; })()")
fill_input("#Szukaj", "goli")   # wyszukiwarka RYNKÓW (id=Szukaj), nie wydarzeń
```

- wyniki (np. `Liczba goli -2.5 1.90 +2.5 1.90 …`) → rynek jest, szukaj DOKŁADNEJ linii;
- "Brak wyników" → **całego rynku nie ma w ofercie** → NIE klikaj niczego,
  `bash scripts/lv-api.sh skipped <betId> market_mismatch "brak rynku 'Liczba goli' …; dostępne rynki: Mecz, Podwójna szansa, Awans"`.
  To NIE jest `event_not_found` ani `line_not_found` — mecz istnieje i ma ofertę,
  brakuje rynku (24.09: oba zlecenia `ou2/under` na Puchar Polski tak zostały
  rozstrzygnięte). Nagłówki `.market-tile-header__name` przy aktywnym chipie
  "Wszystkie" to komplet renderowanych bloków — użyj ich jako listy w detail.

0. **Kupon musi być PUSTY przed pierwszym klikiem.** STS trzyma nogi kuponu
   w sesji przeglądarki między przebiegami — 01.09 agent zastał na kuponie nogę
   Toulouse z poprzedniego (pominiętego) zlecenia i budował kupon 2-nogowy dla
   Real Sociedad. Zanim klikniesz kurs: sprawdź w AX tree prawą kolumnę kuponu;
   każdą istniejącą nogę usuń, dopiero potem dodawaj selekcję. Kupon z inną
   liczbą nóg niż 1 (lub nogi AKO) NIGDY nie idzie do „Postaw" (verification-rules §4).

   **Jak usunąć nogę (zmierzone w DOM 01.09):** X przy nodze to przycisk BEZ
   etykiety — `button.only-icon.sds-button.tertiary.small` w wierszu nogi
   (wiersz = najbliższy przodek elementu z nazwą meczu). W AX tree jest
   bezimienny, więc szukaj po klasie w `js(...)`:
   ```js
   (() => { const rows = [...document.querySelectorAll('*')].filter(e => e.children.length === 0
     && e.getBoundingClientRect().left > innerWidth*0.55 && /NAZWA_MECZU/i.test(e.textContent));
     for (const leaf of rows) { let n = leaf; for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
       const x = [...n.querySelectorAll('button')].find(b => /only-icon/.test(b.className) && !/Postaw/i.test(b.textContent));
       if (x) { x.click(); break; } } } })()
   ```
   Gdy po tym nogi nadal są (kupon „skażony" przeżył reload — 01.09), skasuj
   lokalny szkic kuponu i przeładuj: `localStorage.removeItem('betslip-cache'); location.reload()`.
   Weryfikacja: w prawej kolumnie brak nazw meczów i brak przycisku `Postaw`.
1. Rynek `1x2` ma nagłówek `Mecz` (StaticText). Pod nim trzy przyciski kursów:
   `1 <kurs>` (home), `X <kurs>` (remis), `2 <kurs>` (away) — np. `1 2.95`.
2. Wybierz przycisk wg `outcome` ze zlecenia (home→`1 `, draw→`X `, away→`2 `;
   dokładniej: prefiks przed spacją musi pasować do wyniku rynku 1x2).
3. PRZED kliknięciem przeczytaj kurs z etykiety przycisku (spacja, potem liczba z
   kropką, np. `1 2.95` → 2.95). Bramka kursu (verification-rules §1): niższy niż `odds`
   ze zlecenia o więcej niż 2 % → NIE klikaj; raport
   `bash scripts/lv-api.sh skipped <betId> odds_drift "kurs 2.10 → 1.95"` (podaj aktualny kurs w detail).
   Równy, minimalnie niższy (≤ 2 %) albo **wyższy** → klikaj — wyższy kurs to lepszy zakład,
   nie sygnał ostrzegawczy (01.09: 2.50 wobec 2.40 na Toulouse–Lille miało zostać postawione).
4. Kliknij — po prawej pojawia się kupon z selekcją. Po kliknięciu przeczytaj kurs
   Z KUPONU i zastosuj tę samą bramkę; dopiero gdy kupon pokazuje kurs niższy o >2 % →
   usuń selekcję (X na kuponie) i `bash scripts/lv-api.sh skipped <betId> odds_drift "kurs 2.10 → 1.95"`.

### Rynek rożnych (`corners_ouNNN`) — od 29.08

Zlecenie niesie rynek kanoniczny `corners_ou<cyfry>`: dekoduj linię jak w sekcji
goli niżej (`corners_ou105` → 10,5; `corners_ou95` → 9,5). Strona:
`corners_under` → przycisk „Poniżej <linia>", `corners_over` → „Powyżej <linia>".

1. Na stronie meczu znajdź w AX tree sekcję rożnych — nagłówek zawiera
   „Rzuty rożne" / „Rożne". Sekcja ma WIELE linii (8,5 / 9,5 / 10,5 / 11,5…),
   każda z parą przycisków „Powyżej X" / „Poniżej X" (kurs w etykiecie).
2. Wybierz przycisk DOKŁADNIE z linią ze zlecenia. Jeśli linii nie ma w ofercie
   → `bash scripts/lv-api.sh skipped <betId> line_not_found "dostępne: 8,5 / 9,5 / 11,5"` (podaj dostępne linie w detail) — NIGDY nie klikaj
   „najbliższej" linii, to inny zakład.
3. Bramka kursu i weryfikacja kuponu: identycznie jak w 1x2 (punkty 3-4 wyżej).
   Na kuponie selekcja musi czytać się jako rożne z właściwą linią — jeśli kupon
   pokazuje inną linię niż zlecenie → usuń selekcję i `failed wrong_line`.

Status: sekcja dodana przed pierwszym żywym zleceniem rożnych — przy nim
zweryfikuj dokładne nazwy etykiet i DOPRECYZUJ ten przepis (learning-procedure).

### Rynek goli (`ou<cyfry>`) — od 01.09

Klucz rynku to linia z usuniętą kropką: `ou2` = **2** (linia całkowita), `ou25` = 2,5,
`ou3` = 3, `ou35` = 3,5, `ou05` = 0,5, `ou1` = 1, `ou15` = 1,5. Reguła: jedna cyfra →
liczba całkowita; dwie–trzy cyfry zakończone „5" → ostatnia cyfra to połówka
(`105` → 10,5). NIGDY nie bierz „najbliższej" linii z oferty — 01.09 zlecenie
`ou2`/under (2.70) zostało odczytane jako „Poniżej 2,5" (1.90) i pominięte
z fałszywym `odds_drift`; właściwa linia 2 była w ofercie.

1. Na stronie meczu sekcja **„Liczba goli"** ma WIELE linii (0,5 / 1,5 / 2 / 2,5 / 3 / 3,5…).
   STS renderuje przyciski jako `-2 <kurs>` (poniżej) i `+2 <kurs>` (powyżej) albo
   „Poniżej 2" / „Powyżej 2". `under` → minus/„Poniżej", `over` → plus/„Powyżej".
2. Wybierz DOKŁADNIE linię ze zlecenia. Linia całkowita (2, 3) to zakład azjatycki —
   przy dokładnie tylu golach STS zwraca stawkę; to inny rynek niż 2,5 i kursy się nie
   pokrywają. Brak linii w ofercie → `bash scripts/lv-api.sh skipped <betId> line_not_found "dostępne: 1,5 / 2,5 / 3,5"`
   (podaj dostępne linie w detail).
3. Bramka kursu i weryfikacja kuponu jak w 1x2 (punkty 3–4 wyżej). Kupon musi pokazywać
   TĘ SAMĄ linię co zlecenie — inna linia → usuń selekcję i `failed wrong_line`.

## Krok 5: stawka (KRYTYCZNE — Angular)

Input stawki: `input[inputmode="decimal"]` (jedyny widoczny input tekstowy na stronie;
domyślnie pokazuje ulubioną stawkę, np. `5,00`). Zwykłe fill DOPISUJE zamiast nadpisać.

Dokładna sekwencja (sprawdzona):

```python
js("""(() => {
  const inp = document.querySelector('input[inputmode="decimal"]');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(inp, '');
  inp.dispatchEvent(new Event('input', {bubbles: true}));
  inp.focus();
})()""")
type_text("<stawka>")          # np. "1" — tylko cyfry, bez przecinka
js("""(() => {
  const inp = document.querySelector('input[inputmode="decimal"]');
  inp.dispatchEvent(new Event('change', {bubbles: true}));
  inp.blur();
})()""")
```

Weryfikacja: przycisk w AX tree MUSI pokazywać `Postaw <stawka>,00 zł` (np. stawka 1 →
`Postaw 1,00 zł`). Jeśli pokazuje inną kwotę — powtórz sekwencję; nigdy nie klikaj Postaw
przy złej kwocie.

## Krok 6: postawienie i potwierdzenie

1. Kliknij przycisk `Postaw ... zł`.
2. Jeśli wyskoczy dialog zmiany kursu — zaakceptuj, gdy nowy kurs jest wyższy albo niższy
   o ≤ 2 % od `odds` ze zlecenia; niższy o więcej → anuluj i `skipped odds_drift`.
3. Poczekaj ~3 s i odczytaj TRZY rzeczy naraz — potwierdzenie, błędy, saldo:
   - potwierdzenie: „Przyjęliśmy Twój kupon!", „Kurs całkowity", „Możesz wygrać";
     uwaga na modal „Dzień Bonuserii" (1/14) — zamknij go (X);
   - **błędy: zebrać tekst WSZYSTKICH widocznych komunikatów**, nie tylko szukać słowa
     „błąd" — STS pisze je w kontenerach przy kuponie (`[role="alert"]`, elementy
     z klasą zawierającą `error`, `alert`, `toast`, `notification`, `message`) i bez
     słowa „błąd". Znane komunikaty i decyzje:
     - „Osiągnięto dzienny limit czasu gry. Zmień limity" → NATYCHMIAST
       `failed bookmaker_limit "<dokładny tekst>"` i koniec pracy nad WSZYSTKIMI
       zleceniami z tej sesji (limit jest na koncie, nie na kuponie). 01.09 agent
       klikał „Postaw" na cztery sposoby przez 8 minut, zanim przeczytał ten tekst.
     - „minimalna stawka" → `skipped bookmaker_limit`.
   - saldo `Depozyt NNN,NN zł`: bez zmiany + brak potwierdzenia + przycisk `Postaw`
     nadal aktywny = kupon NIE poszedł.
   **Jedno kliknięcie, potem czytanie — nie drugie kliknięcie.** Ponowny klik wolno
   wykonać tylko, gdy wszystkie trzy odczyty mówią „nie postawiono" i nie ma
   komunikatu błędu; wtedy raz, tą samą metodą co w kroku 5 (prawdziwy klik CDP), i
   znów odczyt. Bez komunikatu i bez przyjęcia po drugim kliku → `failed ui_error`
   z opisem, co pokazuje ekran.
4. **ticketId — najpewniejsze źródło to sieć, nie UI.** Przed kliknięciem Postaw włącz
   `cdp("Network.enable")`; po kliknięciu `drain_events()` i znajdź odpowiedź POST-a
   stawiającego kupon (url zawiera bet/coupon/ticket) — body odpowiedzi
   (`cdp("Network.getResponseBody", requestId=...)`) zawiera numer kuponu.
   Fallback: `Moje kupony` → `W grze` → klik w kartę kuponu (strzałka
   `.my-bets-ticket-header-actions`) → modal "Kupon w grze" z polem
   **"Numer kuponu"** (cyfry z odstępami, np. `566 726 110 083 672 374` →
   `<numer kuponu>`); URL modala to `…(modal:szczegoly/<numer>)`. 24.09 to
   była JEDYNA działająca droga — modal potwierdzenia po postawieniu i lista
   "W grze" numeru NIE pokazują.
   Gdy oba zawiodą: raportuj BEZ numeru — `placed <betId> - <kurs> …` („-” w
   miejscu ticketId). NIGDY nie wpisuj betId jako numeru kuponu: fałszywy
   numer psuje weryfikację, podsumowanie na Telegram i porównanie z kontem.
5. Odczytaj ponownie `Depozyt NNN,NN zł` → `balanceAfter`. Saldo powinno spaść dokładnie
   o stawkę (STS: z konta schodzi stawka brutto; „Możesz wygrać" liczone od stawki netto
   po podatku 12% — np. 2 zł → netto 1,76 zł → wygrana 5,19 zł przy kursie 2.95).
6. Raport: `bash scripts/lv-api.sh placed <betId> <ticketId> <actualOdds> <actualStake> <balanceBefore> <balanceAfter>`
   (actualOdds = kurs z kuponu, actualStake = kwota z przycisku Postaw, salda z kroku 2
   i punktu 5 jako liczby z kropką: `Depozyt 130,50 zł` → `130.50`). Serwer porównuje
   spadek salda ze stawką i przy rozjeździe wyłącza regułę auto-place — to bezpiecznik,
   nie statystyka; bez sald go nie ma.

## Pułapki

- **Zamknięcie WSZYSTKICH kart zabija agentowego Chrome.** Sprzątanie na starcie
  biegu (SKILL.md) zostawia JEDNĄ kartę roboczą — `curl .../json/close/<id>` na
  ostatniej karcie kończy proces i CDP :9222 pada (24.09: "Connection refused",
  `lv-login.py` zwrócił `login_error`). Wznowienie:
  `bash scripts/lv-executor-cycle.sh ensure-chrome`.
- **Layout mobilny przy wąskim oknie** — patrz "Viewport" wyżej. Objawy: brak prawej
  kolumny kuponu, `Postaw` tylko w modalu, `#Stawka` nieobecny.
- Banery „bonus / boost / zgarnij" — ignoruj, nigdy nie zaznaczaj boostów (zmieniają kurs).
- **Minimalna stawka STS: 2 zł.** Zlecenie ze stawką < 2 zł odbije się od kasy —
  raport `skipped bookmaker_limit` (nie próbuj podnosić stawki samowolnie).
- Przycisk `Postaw` nieaktywny / toast z błędem → najpierw dopasuj do znanych
  komunikatów z kroku 6.3 (limit czasu gry = `bookmaker_limit`, minimalna stawka =
  `skipped bookmaker_limit`); nieznany tekst → `failed ui_error` z jego treścią.
- **Dzienny limit czasu gry (Odpowiedzialna gra) liczy CZAS SESJI, także sesje agenta.**
  Bieg 35–40 min ×4 w jeden dzień zjada limit sam z siebie — im krótsza sesja, tym lepiej;
  nie „szukaj" po stronie dłużej niż potrzeba (krok 3: bezpośredni URL meczu).
  Po pracy sesję ZAMYKA `lv-login.py sts --logout` (cykl albo Ty — SKILL.md pkt 10);
  sesja trzymana między cyklami zjada limit sama.
- Wylogowanie w trakcie (znów widać `Zaloguj się`) → `failed not_logged_in`.
- Nie klikaj `Postaw` dwa razy — po kliknięciu czekaj na ekran potwierdzenia.

## Log napraw

- **2026-09-27 (bieg manualny, 9 zleceń STS Puchar Polski; 2 postawione, 7 pominiętych).**
  `lv-place.py` oddał 1x `navigation_failed/event_page_not_rendered` (36037d84)
  i 8x `event_not_found`; WSZYSTKIE zlecenia miały `eventUrlKind: home`. Diagnoza
  i poprawki do przeniesienia do skryptu:
  1. **Wyszukiwarka: tylko `goto_url("/szukaj?s=<token>")`.** Ścieżka skryptu
     (`fill_input` na `#Search` + Enter) jest zawodna i dawała `event_not_found`;
     wejście URL-em zwróciło właściwy kafel dla WSZYSTKICH 6 meczów (deterministyczne).
     Tokeny potwierdzone: `lechia`, `niepolomice`, `warszawa`, `opole`, `gdynia`,
     `tarnobrzeg`. Kafel = `a[href*="/kursy/"]`, którego innerText zawiera tokeny
     OBIE drużyn; poprawny `href` ma obie drużyny w slugu (`/kursy/<home>-<away>/<id>`).
  2. **Gotowość strony meczu: mecze Pucharu Polski mają DOKŁADNIE 3 bloki rynków**
     (`Mecz`, `Podwójna szansa`, `Awans`) — próg `bo-match-detail-market-wrapper > 3`
     z `lv-place.py` daje fałszywe `event_page_not_rendered` (36037d84). Kryterium
     gotowości: OBIE drużyny w `document.title` (potwierdzone dla wszystkich 6 meczów).
  3. **Rynek `Liczba goli` (ou...) NIE istnieje w ofercie żadnego z 6 meczów Pucharu
     Polski** — lupka rynków (`.bundle-menu__search-button button` -> `#Szukaj` = "goli")
     zwraca "Brak wyników". Wszystkie zlecenia `ou2/under` i `ou3/under` (7 szt.)
     rozstrzygnięte jako `skipped market_mismatch` z listą dostępnych rynków
     (`Mecz, Podwójna szansa, Awans`). To NIE `event_not_found` ani `line_not_found`.
  4. **Filtr rynków ukrywa CAŁY DOM rynków** — po wpisaniu frazy w `#Szukaj`
     `bo-match-detail-market-wrapper` zwraca 0, a `.market-tile-header__name` jest puste
     (wygląda jak "strona bez oferty"). Przed budową kuponu przeładuj stronę
     (`goto_url` na ten sam URL, najlepiej z `eventUrl`) albo wyczyść filtr.
  5. **Numer kuponu tylko z modala "Kupon w grze"** (`Moje kupony` -> `W grze` -> strzałka
     `.my-bets-ticket-header-actions`); numer jest też w URL `...(modal:szczegoly/<numer>)`.
     Lista "W grze" i ekran potwierdzenia numeru NIE pokazują. Bieg:
     88424434 -> <numer kuponu> (Mecz 2, 1.90, 17.16 zł, saldo przed->po);
     7a3a8ed9 -> <numer kuponu> (Mecz 1, 20.00, 10 zł, saldo przed->po).
  6. **Odczyt salda po "Postaw" ma opóźnienie** — ~3 s po przyjęciu kuponu `Depozyt`
     pokazuje jeszcze STARĄ kwotę (555,19 mimo przyjętego kuponu). Odczytuj po ~5-6 s.
     Kryterium przyjęcia: "Przyjęliśmy Twój kupon!" ORAZ spadek salda o `stake`;
     samo "Kurs całkowity" w treści NIE jest potwierdzeniem (jest na kuponie zawsze).
  7. Kurs `Mecz` dla gospodarza był zgodny z `odds` (Siarka 1 -> 20.00 = zlecenie 20),
     więc bramka kursu przeszła bez dryfu.

- **2026-09-24 (bieg na żywo, 3 zlecenia STS Puchar Polski; failures: 1).**
  `lv-place.py` oddał `navigation_failed/event_page_not_rendered` (9bf67d87)
  i dwa `event_not_found` (6d591731, 1f46ebd1). Diagnoza i poprawki:
  1. próg gotowości `bo-match-detail-market-wrapper` > 3 → mecze Pucharu Polski mają
     tylko 3 rynki; nowy warunek: obie drużyny w `bo-prematch-detail-info__team-name`
     / `document.title` (sekcja "Wyszukiwarka");
  2. wyszukiwanie przez `goto_url("/szukaj?s=<token>")` zamiast fill+Enter (tamże);
  3. wąskie okno agenta → layout mobilny bez `#Stawka`; naprawa przez
     `Emulation.setDeviceMetricsOverride` (sekcja "Viewport");
  4. brak rynku `Liczba goli` na obu meczach `ou2/under` → nowy przepis
     `skipped market_mismatch` z listą dostępnych rynków (Krok 4);
  5. numer kuponu tylko z modala "Kupon w grze" (Krok 6).
  Wynik: 9bf67d87 POSTAWIONE (1x2 home, kurs 4.00, stawka 10 zł, kupon
  <numer kuponu>, saldo przed → po); 6d591731 i 1f46ebd1 pominięte
  (`market_mismatch`). Poprawki 1-3 i 5 do przeniesienia do `lv-place.py`.
