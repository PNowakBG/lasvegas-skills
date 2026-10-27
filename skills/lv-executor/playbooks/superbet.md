# Playbook: Superbet (superbet.pl)

**Status: SKALIBROWANY 2026-10-24** (`updated: 2026-10-24`, `verified: yes`,
`failures: 4` — patrz „Log napraw”: `document.title` meczu przestał zawierać
drużyny, skrypt `lv-place.py` padał na tym z `needs_model`; 2026-10-24 siatka rynków
nie renderowała się bez wymuszenia klatek → `navigation_failed: event_page_not_rendered`). Selektory z żywej strony (Playwright + kupony agenta 17.09:
Korona – Raków i GKS – Cracovia, rożne). Ścieżkę standardową (1X2, liczba goli,
rożne, obie drużyny strzelą) przechodzi skrypt `scripts/lv-place.py` — Ty
wchodzisz, gdy skrypt odda `needs_model`, i zaczynasz od kroku, który wskazał.
Superbet jest jednym z dwóch buków sondowanych przez bramkę kolejki (obok STS):
bez świeżego (≤ 30 min) raportu `logged_in` jego zlecenia wracają z kolejki
z `loginBlocked: true` (claim = 404) — patrz Krok 0 SKILL.md.

## Zasady ogólne

- Pracuj na AX tree (`cdp("Accessibility.getFullAXTree")["nodes"]`), nie na screenshotach.
  `backendDOMNodeId` zmienia się co sesję — ZAWSZE odpytuj drzewo na nowo, nigdy nie
  zapisuj node id na później.
- W `js(...)` używaj IIFE `(() => { ... })()` — `const` w top-level koliduje między
  wywołaniami (SyntaxError: already declared).
- Koordynaty kliknięcia: `q = cdp("DOM.getBoxModel", backendNodeId=n)["model"]["content"]`,
  `x, y = sum(q[0::2])/4, sum(q[1::2])/4`, potem `click_at_xy(x, y)` (jak w `playbooks/sts.md`).

## Krok 1: wejście i cookies

1. `new_tab("https://superbet.pl")` + `wait_for_load()`.
2. Popup cookies/RODO przy pierwszym wejściu — zaakceptuj (nazwę przycisku potwierdź
   w AX tree). Banery bonusowe/boost ignoruj.

## Krok 2: weryfikacja logowania (krytyczne — Krok 0 SKILL.md)

- **Zalogowany (metoda pierwotna — stan aplikacji, skalibrowana 2026-09-13)**:
  w `js(...)` sprawdź `localStorage` strony —
  `(() => { try { return JSON.parse(localStorage.getItem("user")||"null") } catch { return null } })()`
  → obiekt z `value != null` znaczy zalogowany; dodatkowo
  `JSON.parse(localStorage.getItem("users:sessionExpire")||"null").value > Date.now()`
  potwierdza ważność sesji. `value: null` = gość.
- **Zalogowany (metoda zapasowa — DOM)**: widoczne menu konta
  (`[data-testid="account-menu"]` / `.account-menu`) — te selektory NIE są
  potwierdzone na żywej stronie; storage z metody pierwotnej wygrywa.
- **Niezalogowany**: widoczny przycisk `.e2e-login` („zaloguj") — znacznik e2e
  Superbet potwierdzony żywą sondą 2026-09-13 (nagłówek i /logowanie).
- **Saldo (SKALIBROWANE 2026-10-24 — metoda pierwotna)**: kwota siedzi w nagłówku jako
  osobne elementy (`<login>` / `123,45` / `PLN`). Czytaj z `document.body.innerText`
  regexem `/(\d[\d\s\u00a0]*,\d{2})\s*PLN/` — PIERWSZY trafiony to saldo konta
  (`123,45 PLN` → `123.45`); podaj je jako `balanceBefore`/`balanceAfter` (bez sald serwer
  nie uzgodni budżetu i wyłączy auto-place).
- **Saldo**: DO KALIBRACJI (`[data-testid="account-balance"]` / `.account-balance`
  to placeholdery) — jeśli nie znajdziesz kwoty, raportuj `logged_in` BEZ salda,
  nie zgaduj liczby. Znalezioną kwotę (`NNN,NN zł` → `130.50`) podaj jako
  `balanceBefore`/`balanceAfter`.
- Sesję zdobywa `python3 scripts/lv-login.py superbet`: klika `Zaloguj` w
  nagłówku (`.e2e-login`) — formularz to modal z polami
  `input[name="usernameOrEmail"]` i `input[name="password"]` oraz przyciskiem
  `#login-modal-submit` (**nie istnieje** strona `/logowanie` — 404, sprawdzone
  2026-09-13) — i czeka na `localStorage.user.value`. Przy captchy/kodzie SMS
  skrypt oddaje powód, a loguje się użytkownik w oknie agenta (nie Ty). Po
  `logged_in` ponów odczyt `localStorage` z metody pierwotnej.
- Wykryta ważna sesja → `bash scripts/lv-api.sh session superbet logged_in <saldo>`
  — świeży raport odblokowuje zlecenia buka i odświeża saldo konta w LasVegas.
- Nadal niezalogowany → `bash scripts/lv-api.sh session superbet logged_out`
  i pomiń w tym cyklu zlecenia tego buka z `loginBlocked: true` (`claim`
  odrzuciłby je 404) — zostają w kolejce na następny cykl.

## Krok 3: nawigacja do meczu

`eventUrl` ze zlecenia to strona główna z fragmentem `#event=…` — NIE prowadzi
do meczu. Szukaj: `goto_url("https://superbet.pl/wyszukaj")`, pole
`input[name="search-events"]` (placeholder „Znajdź swoją drużynę”), wpisz JEDEN
najdłuższy token nazwy gospodarza (np. `espanyol`, nie „RCD Espanyol”) i
`Enter`. Wyniki to wiersze `a.e2e-event-row` z tekstem „Jutro, 21:00 … Espanyol
Elche Mecz 1 1.85 X 3.55 2 4.15” i href
`https://superbet.pl/kursy/pilka-nozna/espanyol-vs-elche-13847086`. Wybierz
wiersz z OBIEMA drużynami i otwórz href (`goto_url`).

**Weryfikacja drużyn na stronie meczu — przez `h1`, NIE przez `document.title`.**
Od 2026-09-19 `document.title` strony meczu to generyczne „🐴 Superbet Polska”
(poprzedzone emoji powiadomienia) i NIE zawiera nazw drużyn. Prawdziwy tytuł jest
w nagłówku `h1`: „Espanyol vs Elche: Kursy i Zakłady” — sprawdzaj OBIE drużyny
tam (fuzzy, jak w `text_has_team`). To jest błąd, na którym `scripts/lv-place.py`
oddał `needs_model` (`navigation_failed: event_title_mismatch`, a wcześniej
`event_not_found`) — od 1.5.15 skrypt weryfikuje przez `h1`. Jedna karta na bieg; po czarnym ekranie: `location.reload()`, potem `new_tab`.

**Viewport musi być desktopowy (≥ ~1200 px).** Gdy okno przeglądarki jest wąskie
(zaobserwowane 941 px), Superbet serwuje layout responsywny/mobile: NIE ma
`.sds-betslip-desktop` ani `.e2e-betslip-submit`, a kupon to dolny pasek. Playbook
i `lv-place.py` używają selektorów desktop. Napraw na starcie, zanim zabraknie
kuponu: `cdp('Emulation.setDeviceMetricsOverride', width=1440, height=900,
deviceScaleFactor=1, mobile=False)` (i ponownie po `goto_url`, gdy override
ginie). Po dodaniu pierwszej nogi panel kuponu desktop pojawia się w DOM
(`.sds-betslip-desktop`); przy PUSTYM kuponie na desktopie bywa nieobecny —
brak `.sds-betslip-desktop` przy pustym kuponie to nie błąd.

**WYMUSZANIE KLATEK (obowiązkowe — to była przyczyna `event_page_not_rendered`).** Siatka
rynków renderuje się LENIWIE: 233 `.e2e-expanded-market` z `.event-grid__market-placeholder`,
a treść kart wstawia IntersectionObserver + `requestAnimationFrame`. W oknie agenta klatki
NIE powstają samoczynnie (rAF nie odpala się, IO zostaje „pending” — `visibilityState` =
„visible”, ale `hasFocus()` = false), więc `.e2e-market` zostaje 0 i skrypt pada na
`event_page_not_rendered` — mimo że strona jest w pełni interaktywna (przełączanie grup
rynków działa) i wszystkie żądania sieciowe mają 200. Po KAŻDYM `goto_url`/przeładowaniu,
po kliku w grupę rynków i po scrollu wywołaj 6–10 razy
`cdp("Page.captureScreenshot", format="png", captureBeyondViewport=False)` z ~0,6 s przerwy:
zrzut ekranu zmusza kompozytor do wyprodukowania klatki, IO dostarcza wpisy, rAF rusza i karty
się renderują (w biegu 2026-10-24: `.e2e-market` 0 → 21, `button.odd-button` → 31). Czekanie
samo (pętla gotowości skryptu, nawet 25 s) NIE pomaga — to nie wolne ładowanie, tylko brak
klatek. Sam `Page.startScreencast` nie wystarczył.

## Krok 4: wybór rynku i kursu

Rynki to karty `.e2e-market` z nazwą w `.e2e-market-name`. Na starcie w DOM są
tylko karty grupy „Wszystko”; grupy przełączają zakładki
`.sds-tabs-secondary-item` („Wszystko”, „Zawodnicy”, „Gole”, „Handicap”, „Połowy”,
„Rzuty rożne”, „Kartki”, „Statystyki”, „Łączone”, „Szybkie”) — kliknij zakładkę w JS
(`b.click()`), WYMUŚ KLATKI (Krok 3), dopiero potem szukaj karty (stary selektor
`.sds-filter-bar__filter-container button` NIE istnieje).
Karta zwinięta ma przycisk `.e2e-market-collapse` (klik rozwija); „Zobacz N
więcej zakładów” to `.e2e-expand-markets`.

Kurs = `.e2e-market-odd` → wewnątrz `button.odd-button` z `aria-label`
„<rynek>, <opis>, współczynnik <kurs>, active”; nazwa kursu w `.e2e-odd-name`
(1/X/2, tak/nie), wartość w `.e2e-odd-value`. Karty tabelaryczne (Liczba goli,
rożne) NIE mają `.e2e-odd-name` — czytaj aria-label:
- `1x2` → karta „Mecz”, aria „Mecz, Espanyol wygra mecz…” (1), „Mecz, Remis w meczu…” (X),
  „Mecz, Elche wygra mecz…” (2); nazwa kursu `1`/`X`/`2` rozstrzyga;
- `ou<linia>` → chip „Gole”, karta „Liczba goli”, aria „Liczba goli, Poniżej 2.5
  goli w meczu, współczynnik 1.97” / „Powyżej 2.5 …”. Linie tylko połówkowe
  (0.5…8.5); linii całkowitej (`ou2` = 2) Superbet nie ma → `skipped line_not_found`;
- `corners_ou<linia>` → chip „Rzuty rożne”, karta „Liczba rzutów rożnych”, aria
  „…, Poniżej 11.5 …”; brak linii → `skipped line_not_found` z dostępnymi liniami;
- `btts` → karta „Obie drużyny strzelą”, kursy `tak`/`nie`.
`.e2e-market-odd` już NIE istnieje (0 elementów, 2026-10-24) — kursy czytaj z `button.odd-button`
w karcie rynku (aria-label „…, współczynnik 1.65, active”).
Bramka kursu (verification-rules §1) PRZED kliknięciem: kurs z aria/`.e2e-odd-value`
niższy o >2 % niż `odds` → `skipped odds_drift`. Kupon musi być PUSTY: licznik nóg to
tekst przycisku `.clear-button` (np. „1”); pusty kupon = `.e2e-betslip-empty-placeholder`
(„Kupon jest pusty. Kliknij w kurs aby dodać mecz.”). Nogi z poprzednich biegów
usuń `.clear-button` → potwierdź „Usuń wszystko”/„Wyczyść”.

## Krok 5: stawka (KRYTYCZNE — pole wstępnie wypełnione)

Kupon: `.sds-betslip-desktop` (tryb „Prosty”), nogi w `.sds-betslip-selections`
(„Espanyol - Elche Jutro, 21:00 poniżej 2.5 Liczba goli 1.97”), pole stawki
`input[name="stake"]` (`#stake-0`, `inputmode="decimal"`). **Pole ma już kwotę**
(domyślnie 2, u użytkownika ostatnia/proponowana — 17.09: 45,22 zł!). Wpisz stawkę
przez `fill_input('input[name="stake"]', "10.68")` (Vue — zwykłe wpisanie DOPISUJE),
potem odczytaj `input.value` i sekcję „STAWKA … PLN” na kuponie. Inna kwota niż
`stake` → NIE klikaj, powtórz. „POT. WYPŁATA” = stawka × kurs × 0,88 (podatek 12 %)
— zgodność potwierdza stawkę i kurs. „KURS 1.97” na kuponie musi zgadzać się z
klikniętym kursem (bramka 2 %).
**Kupon NIE czyści się sam po postawieniu** — noga właśnie postawionego kuponu zostaje w panelu,
a dodanie kolejnej nogi dałoby AKO. Przed budową następnego kuponu wyczyść `.clear-button`
(klasa `e2e-clear-destop-betslip`); w biegu 2026-10-24 czyszczenie jednej nogi NIE pokazało
okna potwierdzenia. Sygnał pustego kuponu: brak `.sds-betslip-desktop` (albo brak nóg w nim)
i widoczny `.e2e-betslip-empty-placeholder`. Pole stawki pamięta wartość z POPRZEDNIEGO
zlecenia (nie tylko kwotę domyślną) — zawsze wpisz `stake` i potwierdź „POT. WYPŁATA” =
stawka × kurs × 0,88 (2026-10-24: 10×1.65 → 14,52; 10×1.66 → 14,61; 33,46×1.62 → 47,69).

## Krok 6: postawienie i potwierdzenie

Przycisk `button.e2e-betslip-submit` („Postaw zakład”; przy wylogowaniu obok stoi
`.e2e-login` „Zaloguj” — wtedy `failed not_logged_in`). Kliknij RAZ. Potwierdzenie:
modal z tekstem **„ZAKŁAD POSTAWIONY”** (tylko „Powtórz kupon” + OK — **numeru
kuponu w modalu NIE ma**, sprawdzone 2026-09-19; modal „OK” ma też checkbox
Supersocial — nie zaznaczaj go). Saldo w nagłówku spada o stawkę (np.
144,10 → 134,10 przy 10 zł).

**Numer kuponu — z „Moje zakłady”.** Otwórz `goto_url("https://superbet.pl/moje-zaklady/otwarte")`
(przekierowuje z `/kupony`), zakładka „Aktywne”. Każdy kupon to element
`[data-testid="ticket-list-item-<numer kuponu>"]` (ten sam ciąg w `id` kontenera)
— format `NNNN-XXXXXX` (4 znaki, myślnik, 6 znaków). Dopasuj kupon po drużynach,
rynku i stawce z chwili biegu (na liście mogą być inne kupony na ten sam mecz),
potem odczytaj sam identyfikator:
`[...document.querySelectorAll('[data-testid^="ticket-list-item-"]')]`. Bez modalu i bez spadku salda → `failed
unknown_after_click` (weryfikacja), nigdy drugi klik. Znane komunikaty o limitach →
`bookmaker_limit`; „niewystarczające środki” → `skipped insufficient_balance`.
Raport: `bash scripts/lv-api.sh placed <betId> <numer> <kurs> <stawka> <saldoPrzed> <saldoPo>`.

## Pułapki

- Banery „bonus / boost / zgarnij" — ignoruj; nigdy nie zaznaczaj boostów (zmieniają kurs).
- Popupy cookies/RODO przy pierwszym wejściu na stronę.
- Komunikaty o limitach (minimalna/maksymalna stawka, limit czasu gry) → odpowiedni
  powód raportu; NIE zmieniaj stawki samowolnie.
- Wylogowanie w trakcie (znika menu konta, wraca `Zaloguj`) →
  `bash scripts/lv-api.sh session superbet logged_out` oraz `failed not_logged_in`
  na bieżącym zleceniu.

## Log napraw

- **2026-10-24 (bieg manualny #2, 3 zaległe zlecenia Superbet — WSZYSTKIE POSTAWIONE; przyczyna
  `navigation_failed: event_page_not_rendered` USTALONA I NAPRAWIONA).** Kolejka (wszystkie
  `superbet`, `eventUrlKind: home`, bez `loginBlocked` — sesja aktywna):
  Wisła Kraków – Raków Częstochowa `corners_ou95` over @1.65 st.10 → kupon **<nr kuponu>**
  (saldo <saldo> → <saldo>); Jagiellonia Białystok – Wisła Płock `corners_ou105` under @1.66 st.10
  → kupon **<nr kuponu>** (<saldo> → <saldo>); Legia Warszawa – Lech Poznań `corners_ou105` under
  @1.62 st.33,46 → kupon **<nr kuponu>** (<saldo> → <saldo>). Kursy na kuponie == kursy zleceń
  (1.65 / 1.66 / 1.62), stawki co do grosza, wszędzie 1 noga. Numery z `/moje-zaklady/otwarte`
  (`[data-testid^="ticket-list-item-"]`, format `NNNN-XXXXXX`). Sesji NIE zdobywał `lv-login.py`
  — użytkownik zalogował się ręcznie po poprzednim biegu (poprzednia próba skryptu:
  `login_form_not_found`, wpis niżej).
  1. **PRZYCZYNA `event_page_not_rendered`:** leniwy render siatki rynków (patrz „Krok 3:
     WYMUSZANIE KLATEK”) — bez klatek `.e2e-market` zostaje 0 i pętla gotowości skryptu pada.
     DO PRZENIESIENIA DO `lv-place.py`: po `goto_url`/przeładowaniu, po kliku grupy rynków
     i po scrollu zrobić 6–10 × `Page.captureScreenshot` (≈0,6 s przerwy) PRZED testem
     `.e2e-market`. Samo wydłużanie timeoutu nic nie daje.
  2. **DO PRZENIESIENIA DO `lv-place.py` (selektory/kalibracja):** `.e2e-market-odd` nie istnieje
     → kursy czytać z `button.odd-button` w karcie `.e2e-market`; saldo = pierwszy
     `/(\d[\d\s\u00a0]*,\d{2})\s*PLN/` z `document.body.innerText`; po postawieniu trzeba
     wyczyścić `.clear-button`, bo noga zostaje na kuponie; pole stawki pamięta poprzednią
     wartość; `.e2e-betslip-submit` („Postaw zakład”) bez zmian.
  3. **Pułapki potwierdzone:** modal „BONUS PRZYZNANY” (Super Booster 25%) wraca przy każdym
     wejściu na stronę meczu — „OK” zamyka go (rejestr `overlays.superbet`), ale wpis
     `.sds-modal` bywa jeszcze w DOM i nie blokuje kuponu; modal „ZAKŁAD POSTAWIONY” numeru
     kuponu nie pokazuje.

- **2026-10-24 (bieg manualny, 3 zalegle zlecenia Superbet - NIEPODJETE: brak zalogowania).**
  Kolejka zwrocila 3 zlecenia `superbet` z `loginBlocked: true` (Wisla Krakow - Rakow
  Czestochowa `corners_ou95` over 1.65 st.10; Jagiellonia - Wisla Plock `corners_ou105`
  under 1.66 st.10; Legia Warszawa - Lech Poznan `corners_ou105` under 1.62 st.33.46).
  `python3 scripts/lv-login.py superbet` zwrocil DWUKROTNIE wynik `logged_out` z powodem
  `login_form_not_found` i detail "modal logowania nie pokazal pola usernameOrEmail (karta: visible)".
  **To DRIFT DETEKCJI FORMULARZA w `lv-login.py`, nie brak poswiadczen i nie captcha.**
  Reczna sonda TEGO SAMEGO ekranu po nieudanym skrypcie pokazala, ze formularz JEST w DOM
  i widoczny: `input[name=usernameOrEmail]` (id `usernameOrEmail-0` oraz DRUGA kopia
  `usernameOrEmail-2`), `input[name=password]` (id `password-1`/`password-3`) i przycisk
  `#login-modal-submit`; modal to `.sds-modal.login-modal`, ale w DOM sa **DWIE instancje
  modala jednoczesnie** (querySelectorAll po `.sds-modal, [role=dialog], .modal` zwraca 2
  elementy z klasa `login-modal`). Skrypt melduje brak pola, choc pole istnieje -> prawdopodobna
  przyczyna: pojedynczy `querySelector` + test widocznosci przewraca sie na duplikacie modala
  (albo pole renderuje sie z opoznieniem, a skrypt nie czeka na `#login-modal-submit`).
  **Do przeniesienia do `lv-login.py`:** brac PIERWSZY WIDOCZNY element (find po
  offsetParent !== null) zamiast pojedynczego selektora i czekac na `#login-modal-submit`.
  Sesja NIE zostala otwarta -> zlecenia `superbet` zostaja w kolejce na nastepny cykl
  (nic nie claimowano, nic nie postawiono). Zgloszono `session superbet logged_out` z powodem
  `login_form_not_found`; uzytkownik loguje sie RECZNIE w oknie agenta (`.e2e-login` ->
  formularz) - hasla agent nie wpisuje.


- **2026-10-10 (bieg manualny, 3 zaległe zlecenia: 1× Superbet, 2× STS — wszystkie POSTAWIONE).**
  Zlecenie Superbet oddane przez `lv-place.py` jako `market_not_found` (lista nazw „Mecz, Podwójna
  szansa, Obie drużyny strzelą, Liczba goli, Liczba goli drużyny”):
  1. **DRIFT paska grup rynków — to była przyczyna fałszywego `market_not_found`.** Stary selektor
     `.sds-filter-bar__filter-container button` już NIE istnieje. Grupy rynków to teraz
     `.sds-tabs-secondary .sds-tabs-secondary-item` (tag BUTTON, klasy
     `sds-tabs-secondary-item--body sds-focus-on-elevation-subtle sds-tabs-secondary-item
     e2e-sds-tabs-secondary-item`), etykiety: Wszystko, Zawodnicy, Gole, Handicap, Połowy,
     Rzuty rożne, Kartki, Statystyki, Łączone, Szybkie, Mecz, SUPER. Z aktywną zakładką
     „Wszystko” DOM ma TYLKO karty grup podstawowych (stąd lista z błędu skryptu). Aby dostać
     rożne: kliknąć zakładkę „Rzuty rożne” — najlepiej `b.click()` w JS na `.sds-tabs-secondary-item`;
     `click_at_xy` na współrzędnej okazał się zawodny (jeden raz nie przełączył). Po przełączeniu
     pojawia się karta `.e2e-market-name` = „Liczba rzutów rożnych” z liniami 7.5…12.5 i kursami
     w `aria-label` `.e2e-market-odd button` (np. „Liczba rzutów rożnych, Poniżej 11.5 rzutów
     rożnych w meczu, współczynnik 1.35”). (`corners_ou115` = 11.5 → `Poniżej 11.5`.)
  2. **Wynik zlecenia `c685c63b` (corners_ou115 under, Śląsk Wrocław – Lech Poznań):** kupon
     **<numer kuponu>**, kurs 1.35, stawka 16,67, saldo przed → po. Selekcja z kupnia
     potwierdzona jako „Śląsk Wrocław - Lech Poznań … poniżej 11.5 … Liczba rzutów rożnych 1.35”,
     1 noga. Numer z `/moje-zaklady/otwarte` → `[data-testid=\"ticket-list-item-<numer kuponu>\"]`.
  3. **Wyszukiwanie `/wyszukaj?query=<token>` DZIAŁA** (token `slask` → wiersz `a.e2e-event-row`
     „Śląsk Wrocław Lech Poznań”, href `/kursy/pilka-nozna/slask-wroclaw-vs-lech-poznan-14025561`);
     drużyny potwierdzone przez `h1` („Śląsk Wrocław vs Lech Poznań: Kursy i Zakłady”). UWAGA:
     `fill_input` na `input[name=search-events]` wpisuje DOPISUJĄC (uzyskałem `wroclawwroclaw`
     i „BRAK WYNIKÓW WYSZUKIWANIA”) — używać URL-a `/wyszukaj?query=<token>` (jeden czysty token)
     albo najpierw wyczyścić pole. Modal „BONUS PRZYZNANY” (Super Booster 25%) wraca po wejściu
     na stronę meczu — klikać „OK” (jest w rejestrze `overlays.superbet`).

- **2026-10-09 (skill 1.5.15) — poprawki z 19.09 PRZENIESIONE do `lv-place.py`.**
  Skrypt teraz: wymusza 1440×900 (`Emulation.setDeviceMetricsOverride`), szuka przez
  `/wyszukaj?query=<token>` i bierze tylko `/kursy/pilka-nozna/` (koszykówka
  „Zastal – Legia” ma ten sam format adresu), wybiera wiersz z największym pokryciem
  nazw obu drużyn i odrzuca II/U19/kobiety („Legia Ladies (K)”), gdy zlecenie ich nie
  dotyczy, weryfikuje mecz przez `h1`, liczy nogi w
  `.sds-betslip-desktop .betslip-selection-item` (pusty kupon bywa bez panelu = 0),
  czyści kupon do zera przed każdym zleceniem i po każdym, które nie doszło do
  `ready` (`.grouped-selection-item__delete` „Usuń”, odwód `.clear-button`), sprawdza
  nogę po drużynach i kursie na kuponie, czyta saldo z tekstu nagłówka („<saldo>” i
  „PLN” to osobne elementy) i bierze numer kuponu z `/moje-zaklady/otwarte`
  (`ticket-list-item-<<numer kuponu>>`, dopasowanie po drużynach i „STAWKA x,xx PLN”).
  Zakończone kupony są pod `/moje-zaklady/zakonczony` (zakładka „Rozliczone”).
  Sprawdzone na żywo bez stawiania: 1x2, ou25, btts, odds_drift, kupon z cudzą nogą,
  odczyt numeru (trafny / zła stawka / obce drużyny), prepare na zalogowanym koncie.

- 2026-09-19 — **drift: `document.title` meczu bez drużyn.** Strona meczu zwraca
  `document.title` = „🐴 Superbet Polska”; `h1` = „Korona Kielce vs Raków
  Częstochowa: Kursy i Zakłady”. `scripts/lv-place.py` (sprawdza
  `document.title`) oddawał `needs_model` / `event_not_found`
  (`navigation_failed: event_title_mismatch`) mimo poprawnie otwartej strony
  meczu. Weryfikację drużyn robić przez `h1`. Do przeniesienia w skrypcie.
  **Wymuszony desktopowy viewport** (941 px → 1440 px przez
  `Emulation.setDeviceMetricsOverride`), bo bez tego nie ma selektorów kuponu
  desktop. **Numer kuponu** odczytany z `/moje-zaklady/otwarte`
  (`[data-testid^="ticket-list-item-"]`), bo modal „ZAKŁAD POSTAWIONY” go nie
  pokazuje. Zrealizowane zlecenia: Korona Kielce – Raków Częstochowa rożne
  poniżej 11.5 @1.35 za 10 zł (kupon <numer kuponu>, saldo przed → po) oraz
  Paris FC – Strasbourg 1X2 (2) @3.60 za 10 zł (kupon <numer kuponu>, kurs wyższy
  niż w zleceniu 3.45 → wzięty, saldo przed → po).

- 2026-09-17 — kalibracja pełnej ścieżki (Playwright na żywej stronie + dwa realne
  kupony agenta: GKS – Cracovia rożne poniżej 11.5 @1.69 za 10,68; Korona – Raków
  rożne poniżej 11.5 @1.35 — ten drugi ze ZŁĄ stawką 45,22 zamiast 10, bo pole było
  wstępnie wypełnione, stąd krok 5). Ścieżka standardowa przeniesiona do
  `scripts/lv-place.py`.
- 2026-09-13 — playbook utworzony: szkielet z selektorami detekcji logowania i salda;
  placement oznaczony jako wymagający kalibracji.
- 2026-09-13 — kalibracja detekcji sesji żywą sondą Playwright: metoda pierwotna =
  `localStorage` (`user.value`, `users:sessionExpire`), wylogowany = `.e2e-login`;
  saldo nadal do kalibracji (raport `logged_in` bez salda jest poprawny).
