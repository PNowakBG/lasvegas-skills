# Playbook: STS (sts.pl)
updated: 2026-10-09   verified: yes   failures: 2

Zweryfikowany E2E 2026-08-29 (Chrome, profil `~/.hermes/lv-browser-profile`, CDP :9222).
Ponownie sprawdzony na zywych zleceniach 2026-09-24 (Puchar Polski) - patrz "Log napraw".
Sprawdzony na 10 zywych zleceniach 2026-10-09 (Ekstraklasa/Bundesliga/LaLiga/Ligue1/I Liga) - patrz "Log napraw 2026-10-09".
Czytaj tez sekcje "Wyszukiwarka", "Viewport", Krok 4 i Krok 5 - to poprawki z tych biegow.

## Zasady ogólne

- Pracuj na AX tree (`cdp("Accessibility.getFullAXTree")["nodes"]`), nie na screenshotach.
  `backendDOMNodeId` zmienia się co sesję — ZAWSZE odpytuj drzewo na nowo, nigdy nie
  zapisuj node id na później.
- W `js(...)` używaj IIFE `(() => { ... })()` — `const` w top-level koliduje między
  wywołaniami (SyntaxError: already declared).
- Koordynaty kliknięcia: `q = cdp("DOM.getBoxModel", backendNodeId=n)["model"]["content"]`,
  `x, y = sum(q[0::2])/4, sum(q[1::2])/4`, potem `click_at_xy(x, y)`.
  (Na tym agencie działa tez `getBoundingClientRect()` przez `js(...)` + `click_at_xy`.)

## Krok 1: wejście i cookies

1. `new_tab("https://www.sts.pl")` + `wait_for_load()`.
2. Jeśli popup cookies (dialog „KORZYSTAMY Z PLIKÓW COOKIES"): znajdź w AX tree
   `button` o nazwie dokładnie `Akceptuj wszystkie` i kliknij. Popup znika.

## Krok 2: weryfikacja logowania

- Zalogowany: widać `Wpłata` oraz `Depozyt NNN,NN zł`. Odczytaj saldo z `Depozyt` i
  zaraportuj sesję LasVegas: `bash scripts/lv-api.sh session sts logged_in <saldo>`
  (liczba z kropką: `130,50 zł` → `130.50`) — meldunek Kroku 0 SKILL.md; powtórz go po
  udanym postawieniu, gdy saldo się zmieniło. Najpewniejszy test w `js(...)`:
  `document.body.innerText` zawiera /Depozyt\s+[\d\s,]+\s*zł/.
- Niezalogowany: widać `Zaloguj się` i `Załóż konto`. Uruchom
  `python3 scripts/lv-login.py sts` — skrypt zamyka ekran powitalny
  („Kontynuuj jako gość"), otwiera modal logowania (`[data-testid="input-username"]`,
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

### Wyszukiwarka (eventUrlKind=home) — sprawdzone 24.09.2026, POTWIERDZONE 09.10.2026

STS obsługuje szukanie przez parametr URL — to najpewniejsza droga i NIE wymaga
klikania w lupkę ani wysyłania Enter:

1. `goto_url("https://www.sts.pl/szukaj?s=<token>")` + `wait_for_load()`.
   Token = najdłuższy nie-generyczny wyraz nazwy drużyny (`Lechia Gdańsk` → `lechia`,
   `Polonia Warszawa` → `warszawa`, `Puszcza Niepołomice` → `niepolomice`).
2. Poczekaj na `a[href*="/kursy/"]` (kafle wyników). Kafel pasuje, gdy jego `innerText`
   zawiera najdłuższy token OBIEJ drużyn.
3. Wejdź w `href` (`/kursy/<slug>/<id>`) i sprawdź, że `document.title` zawiera OBIE drużyny.

**Tokeny potwierdzone:** `lechia`, `niepolomice`, `warszawa`, `opole`, `gdynia`,
`tarnobrzeg` (27.09) + `wieczysta`, `podbeskidzie`, `rakow`, `malaga`, `lens`,
`dortmund`, `hoffenheim` (09.10). Wyszukiwarka zwraca tez mecze esportowe
(`B Dortmund (Bjela)`) — filtruj kafel po tokenie PRZECIWNIKA (nie tylko gospodarza),
inaczej latwo wejsc w esport zamiast realnego meczu (09.10: `szukaj?s=dortmund`
zwrocilo najpierw 4 esporty, Borussia realna byla dopiero 5. w liscie).

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
`Postaw <stawka> zł`. Jeśli mimo to trafisz na `bs-betslip-mobile`: rozwiń pasek
przyciskiem `.betslip-bar-default-content__action__expand-betslip button`.

**Dyscyplina kart:** pracuj w JEDNEJ karcie (`goto_url`), nie otwieraj nowych.
Strona meczu STS jest ciężka — każda dodatkowa karta mnoży CPU i potrafi zawiesić
`Runtime.evaluate`.

**Renderer zawieszony (eval timeout):** gdy `js(...)`/`page_info()` wisi do timeoutu —
karta jest martwa. Zamknij ją przez CDP HTTP: `curl http://localhost:9222/json`,
znajdź `id` karty sts.pl, `curl http://localhost:9222/json/close/<id>`, potem
`new_tab(eventUrl)` i pracuj dalej.

## Krok 4: wybór rynku i kursu

**Najpierw sprawdź, czy rynek w ogóle jest w ofercie tego meczu.** Liczba rynków
zależy od meczu: Ekstraklasa ma ich ~10, a mecz Pucharu Polski miesiąc naprzód — tylko 3.
Szybki test (lupka rynków w pasku nad listą):

```python
js("(() => { const b=document.querySelector('.bundle-menu__search-button button'); if(b) b.click(); return true; })()")
fill_input("#Szukaj", "goli")   # wyszukiwarka RYNKÓW (id=Szukaj), nie wydarzeń
```

- wyniki → rynek jest, szukaj DOKŁADNEJ linii;
- "Brak wyników" → **całego rynku nie ma w ofercie** → NIE klikaj niczego,
  `bash scripts/lv-api.sh skipped <betId> market_mismatch "brak rynku 'Liczba goli' …; dostępne rynki: Mecz, Podwójna szansa, Awans"`.
  To NIE jest `event_not_found` ani `line_not_found` — mecz istnieje i ma ofertę,
  brakuje rynku (24.09: oba zlecenia `ou2/under` na Puchar Polski tak zostały
  rozstrzygnięte). Nagłówki `.market-tile-header__name` przy aktywnym chipie
  "Wszystkie" to komplet renderowanych bloków — użyj ich jako listy w detail.

0. **Kupon musi być PUSTY przed pierwszym klikiem.** STS trzyma nogi kuponu
   w sesji przeglądarki między przebiegami — 01.09 agent zastał na kuponie nogę
   Toulouse z poprzedniego (pominiętego) zlecenia i budował kupon 2-nogowy dla
   Real Sociedad. Zanim klikniesz kurs: sprawdź prawą kolumnę kuponu;
   każdą istniejącą nogę usuń, dopiero potem dodawaj selekcję. Kupon z inną
   liczbą nóg niż 1 (lub nogi AKO) NIGDY nie idzie do „Postaw" (verification-rules §4).

   **Jak usunąć nogę (zmierzone 01.09, UPROSZCZONE i POTWIERDZONE 09.10):**
   X przy nodze to przycisk BEZ etykiety — `button.only-icon.sds-button.tertiary.small`
   w wierszu nogi. W `bs-betslip-desktop` na starcie 09.10 byly 4 nogi (kontaminacja
   z nieudanego biegu `lv-place.py`). Skuteczny sposob:
   ```js
   (() => {
     const slip=document.querySelector('bs-betslip-desktop');
     const b=[...slip.querySelectorAll('button.only-icon')].find(x=>{
       let n=x; for(let k=0;k<6&&n;k++,n=n.parentElement){
         if(/Dortmund|Raków|Podbeskidzie|Wieczysta|Kupon/i.test(n.innerText||'') && (n.innerText||'').length<200) return true;
       } return false;});
     if(b){ b.click(); return 'clicked'; } return 'none';
   })()
   ```
   Powtarzaj do zera nóg; weryfikacja: kupon pokazuje
   „Ten kupon czeka na zakłady". Gdy nogi nadal są (kupon „skażony" przeżył reload),
   skasuj szkic kuponu: `localStorage.removeItem('betslip-cache'); location.reload()`.
1. Rynek `1x2` ma nagłówek `Mecz` (StaticText). Pod nim trzy przyciski kursów:
   `1 <kurs>` (home), `X <kurs>` (remis), `2 <kurs>` (away) — np. `1 2.95`.
2. Wybierz przycisk wg `outcome` ze zlecenia (home→`1 `, draw→`X `, away→`2 `).
3. PRZED kliknięciem przeczytaj kurs z etykiety przycisku (np. `1 2.95` → 2.95).
   Bramka kursu (verification-rules §1): niższy niż `odds` ze zlecenia o więcej niż 2 %
   → NIE klikaj; raport `bash scripts/lv-api.sh skipped <betId> odds_drift "kurs 2.10 → 1.95"`.
   Równy, minimalnie niższy (≤ 2 %) albo **wyższy** → klikaj — wyższy kurs to lepszy zakład
   (01.09: 2.50 wobec 2.40 na Toulouse–Lille miało zostać postawione).
4. Kliknij — po prawej pojawia się kupon z selekcją. Po kliknięciu przeczytaj kurs
   Z KUPONU i zastosuj tę samą bramkę.

### Rynek rożnych (`corners_ouNNN`) — od 29.08

Zlecenie niesie rynek kanoniczny `corners_ou<cyfry>`: dekoduj linię jak w goli niżej
(`corners_ou105` → 10,5; `corners_ou95` → 9,5). Strona: `corners_under` → „Poniżej <linia>",
`corners_over` → „Powyżej <linia>".

1. Na stronie meczu znajdź sekcję rożnych — nagłówek zawiera „Rzuty rożne" / „Rożne".
   Sekcja ma WIELE linii (8,5 / 9,5 / 10,5 / 11,5…), każda z parą „Powyżej X"/„Poniżej X".
2. Wybierz przycisk DOKŁADNIE z linią ze zlecenia. Jeśli linii nie ma w ofercie
   → `bash scripts/lv-api.sh skipped <betId> line_not_found "dostępne: 8,5 / 9,5 / 11,5"`
   — NIGDY nie klikaj „najbliższej" linii.
3. Bramka kursu i weryfikacja kuponu jak w 1x2. Jeśli kupon pokazuje inną linię niż
   zlecenie → usuń selekcję i `failed wrong_line`.

### Rynek goli (`ou<cyfry>`) — od 01.09, SKORYGOWANE 09.10.2026

Klucz rynku to linia z usuniętą kropką: `ou2` = **2** (linia całkowita), `ou25` = 2,5,
`ou3` = 3, `ou35` = 3,5, `ou05` = 0,5, `ou1` = 1, `ou15` = 1,5. Reguła: jedna cyfra →
liczba całkowita; dwie–trzy cyfry zakończone „5" → ostatnia cyfra to połówka (`105` → 10,5).
NIGDY nie bierz „najbliższej" linii z oferty.

**KRYTYCZNE (09.10): linie CAŁKOWITE i POŁÓWKOWE są w DWÓCH RÓŻNYCH blokach rynków.**
Strona meczu (duże ligi) ma OBA nagłówki `.market-tile-header__name`:
- **`Liczba goli`** — TYLKO linie połówkowe; przyciski z kropką: `-1.5 3.70`, `+2.5 1.80`.
- **`Liczba goli (z możliwym zwrotem)`** — linie CAŁKOWITE (zakład azjatycki, przy
  dokładnie tylu golach STS zwraca stawkę); przyciski BEZ kropki: `-2 5.40`, `+3 1.58`.

Mapowanie: dla zleceń `ou1`,`ou2`,`ou3`,`ou4`… (linia całkowita) klikaj w blok
**`Liczba goli (z możliwym zwrotem)`**, przycisk `-<N> <kurs>` (under). Dla `ou15`,
`ou25`, `ou35`… — blok **`Liczba goli`**, przycisk `-<N.5> <kurs>`. STS nazywa nogę
kuponu odpowiednio: cała linia → „Liczba goli (z możliwym zwrotem)", połówka → „Liczba goli".
Selektor bloku po nagłówku:
```js
for (const w of [...document.querySelectorAll('bo-match-detail-market-wrapper')]) {
  const h=w.querySelector('.market-tile-header__name');
  if (h && h.innerText.trim()==='Liczba goli (z możliwym zwrotem)') { /* przyciski tu */ }
}
```
(UWAGA: to custom-element TAG, nie klasa — `querySelectorAll('bo-match-detail-market-wrapper')`,
NIE `.bo-match-detail-market-wrapper`.)

1. `under` → przycisk z prefiksem `-` („Poniżej"), `over` → `+` („Powyżej"). Kurs
   czytaj z etykiety (np. `-2 5.40` → linia 2, kurs 5.40).
2. Wybierz DOKŁADNIE linię ze zlecenia. Brak linii → `bash scripts/lv-api.sh skipped <betId> line_not_found "dostępne: 1,5 / 2,5 / 3,5"`.
3. Bramka kursu i weryfikacja kuponu jak w 1x2. Kupon musi pokazywać TĘ SAMĄ linię
   (noga czyta się `-N <kurs>` + market `Liczba goli (z możliwym zwrotem)`); inna linia
   → usuń selekcję i `failed wrong_line`.

## Krok 5: stawka (KRYTYCZNE — Angular)

Input stawki: `input[inputmode="decimal"]` (jedyne widoczne pole tekstowe w bloku kuponu;
domyślnie pokazuje ulubioną stawkę, np. `5,00`). Zwykłe fill DOPISUJE zamiast nadpisać.

Dokładna sekwencja (sprawdzona 27.09; WARIANT 09.10 z CDP `Input.insertText` — bez
`type_text` na tym agencie, zachowanie identyczne):

```python
js("""(() => {
  const inp = document.querySelector('input[inputmode="decimal"]');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(inp, '');
  inp.dispatchEvent(new Event('input', {bubbles: true}));
  inp.focus();
})()""")
cdp("Input.insertText", text="<stawka>")   # np. "22.48"; rownowaznik type_text
js("""(() => {
  const inp = document.querySelector('input[inputmode="decimal"]');
  inp.dispatchEvent(new Event('change', {bubbles: true}));
  inp.blur();
})()""")
```

Weryfikacja: pole ma `value` == stawka (np. `22,48`) a przycisk MUSI pokazywać
`Postaw <stawka> zł` (np. stawka 22.48 → `Postaw 22,48 zł`). Inna kwota — powtórz
sekwencję; nigdy nie klikaj Postaw przy złej kwocie.

## Krok 6: postawienie i potwierdzenie

1. Kliknij przycisk `Postaw ... zł` (prawdziwy klik przez `click_at_xy` na środku
   `getBoundingClientRect()` przycisku — 09.10 działał konsekwentnie; `x≈1212`).
2. Jeśli wyskoczy dialog zmiany kursu — zaakceptuj, gdy nowy kurs jest wyższy albo niższy
   o ≤ 2 % od `odds`; niższy o więcej → anuluj i `skipped odds_drift`.
3. Poczekaj ~5-6 s (saldo ma opóźnienie) i odczytaj TRZY rzeczy naraz — potwierdzenie,
   błędy, saldo:
   - potwierdzenie: „Przyjęliśmy Twój kupon!", „Kurs całkowity", „Możesz wygrać";
     uwaga na modal „Dzień Bonuserii" (1/14) — zamknij go (X);
   - **błędy: zebrać tekst WSZYSTKICH widocznych komunikatów**, nie tylko słowa „błąd"
     — kontenery przy kuponie (`[role="alert"]`, klasy z `error`, `alert`, `toast`,
     `notification`, `message`). Znane komunikaty:
     - „Osiągnięto dzienny limit czasu gry. Zmień limity" → NATYCHMIAST
       `failed bookmaker_limit "<dokładny tekst>"` i koniec pracy nad WSZYSTKIMI
       zleceniami z tej sesji (limit jest na koncie, nie na kuponie).
     - „minimalna stawka" → `skipped bookmaker_limit`.
   - saldo `Depozyt NNN,NN zł`: bez zmiany + brak potwierdzenia + przycisk `Postaw`
     nadal aktywny = kupon NIE poszedł.
   **Jedno kliknięcie, potem czytanie — nie drugie kliknięcie.** Ponowny klik wolno
   wykonać tylko, gdy wszystkie trzy odczyty mówią „nie postawiono" i nie ma komunikatu
   błędu; wtedy raz, tą samą metodą i znów odczyt. Bez komunikatu i bez przyjęcia po
   drugim kliku → `failed ui_error` z opisem, co pokazuje ekran.
4. **ticketId — najpewniejsze źródło to modal „Kupon w grze".** Otwórz
   `goto_url("https://www.sts.pl/moje-zaklady/w-grze")`, poczekaj aż istnieje
   `.my-bets-ticket-header-actions` (strona renderuje LENIWIE — pętla po ~2 s do ~10 prób),
   kliknij strzałkę pierwszego (najnowszego) kuponu → modal „Kupon w grze",
   pole **„Numer kuponu"** (cyfry z odstępami, np. `568 226 110 024 038 850` →
   `<numer kuponu>`); numer jest tez w URL `…(modal:szczegoly/<numer>)`.
   09.10 ta droga zadziałała dla 10/10 kuponów. Odczyty weryfikuj ZAWSZE razem z
   kursem i stawką z modala (żeby nie wpisać numeru sąsiedniego kuponu).
   Fallback z sieci: przed kliknięciem Postaw `cdp("Network.enable")`, po kliknięciu
   znajdź odpowiedź POST-a (url zawiera bet/coupon/ticket) i `Network.getResponseBody`.
   Gdy oba zawiodą: raportuj BEZ numeru — `placed <betId> - <kurs> …` („-” w miejscu ticketId).
   NIGDY nie wpisuj betId jako numeru kuponu.
5. Odczytaj ponownie `Depozyt NNN,NN zł` → `balanceAfter`. Saldo powinno spaść dokładnie
   o stawkę (STS: z konta schodzi stawka brutto; „Możesz wygrać" liczone od stawki netto
   po podatku 12%). Potwierdzenie 09.10: 10/10 kuponów, spadek == stake co do grosza.
6. Raport: `bash scripts/lv-api.sh placed <betId> <ticketId> <actualOdds> <actualStake> <balanceBefore> <balanceAfter>`
   (salda jako liczby z kropką: `Depozyt 130,50 zł` → `130.50`). Serwer porównuje spadek
   salda ze stawką i przy rozjeździe wyłącza regułę auto-place — to bezpiecznik; bez sald go nie ma.

## Pułapki

- **Zamknięcie WSZYSTKICH kart zabija agentowego Chrome.** Sprzątanie na starcie biegu
  (SKILL.md) zostawia JEDNĄ kartę roboczą — `curl .../json/close/<id>` na ostatniej
  karcie kończy proces i CDP :9222 pada. Wznowienie:
  `bash scripts/lv-executor-cycle.sh ensure-chrome`.
- **Layout mobilny przy wąskim oknie** — patrz "Viewport". Objawy: brak prawej kolumny
  kuponu, `Postaw` tylko w modalu, `#Stawka` nieobecny.
- **Kupon zostaje NIE-pusty po nieudanym biegu `lv-place.py`** (09.10: 4 nogi z 7 zleceń,
  które skrypt zgłosił jako `selection_not_added`). ZAWSZE zaczynaj od wyczyszczenia
  kuponu (Krok 4 pkt 0) i potwierdź „Ten kupon czeka na zakłady" — inaczej postawisz AKO.
- **Sesja STS potrafi wypaść w trakcie biegu** (09.10: po wejściu na `/moje-zaklady/w-grze`
  pojawił się `…(modal:my-account)?navigateTo=…` i tytuł „Zaloguj się"; zdarzyło się 2x).
  Objaw: `Zaloguj się`/`Załóż konto` w treści. Reakcja: `python3 scripts/lv-login.py sts`
  i powtórz krok. Nie zgub z tego powodu raportu `placed` ani numeru kuponu.
- Banery „bonus / boost / zgarnij" — ignoruj, nigdy nie zaznaczaj boostów (zmieniają kurs).
- **Minimalna stawka STS: 2 zł.** Zlecenie ze stawką < 2 zł odbije się od kasy —
  raport `skipped bookmaker_limit` (nie podnoś stawki samowolnie).
- Przycisk `Postaw` nieaktywny / toast z błędem → dopasuj do znanych komunikatów z 6.3;
  nieznany tekst → `failed ui_error` z jego treścią.
- **Dzienny limit czasu gry (Odpowiedzialna gra) liczy CZAS SESJI, także sesje agenta.**
  Bieg 35–40 min ×4 w jeden dzień zjada limit sam z siebie. Po pracy sesję ZAMYKA
  `lv-login.py sts --logout` (cykl albo Ty — SKILL.md pkt 10).
- **API LasVegas zwracał przejściowe `403/503`** (09.10: `curl (22) … error: 503` na
  wszystkich endpointach przez ~1 min). Reakcja: ponawiaj `lv-api.sh <cmd>` po ~10 s aż
  do sukcesu (pętla); zlecenie/claim/raport nie giną — `claim` wraca, a `placed` po
  powrocie serwera zapisuje sie normalnie.
- Wylogowanie w trakcie (znów widać `Zaloguj się`) → `failed not_logged_in`.
- Nie klikaj `Postaw` dwa razy — po kliknięciu czekaj na ekran potwierdzenia.

## Log napraw

- **2026-10-09 (bieg manualny, 9 zleceń zaległych + 1 nowe, wszystkie STS, 10/10 POSTAWIONE).**
  `lv-place.py` oddał 7x `selection_not_added` i 2x `event_not_found`. Diagnoza i poprawki
  (do przeniesienia do skryptu):
  1. **`selection_not_added` to FAŁSZYWY alarm skryptu — klik DZIAŁA.** Na starcie kupon
     `bs-betslip-desktop` miał 4 nogi z poprzedniego biegu (Dortmund -2 5.40, Raków -3 2.00,
     Podbeskidzie -2 2.85, Wieczysta -1 10.50) — dokładnie te, które skrypt zgłosił jako
     `selection_not_added`. Selekcja wchodzi na kupon, a weryfikacja PO kliku w
     `lv-place.py` jej nie widzi (zły selektor/struktura nogi). Poprawka: sprawdzać nogę
     kuponu po `bs-betslip-desktop` i tekście wiersza (mecz + `-N` + kurs), nie po starym
     ref-id; skrypt MUSI czyścić kupon przed każdym zleceniem.
  2. **`event_not_found` to skutek złej wyszukiwarki skryptu.** `goto_url("/szukaj?s=<token>")`
     zwrócił właściwe kafle dla WSZYSTKICH meczów, w tym LaLiga/Ligue1 (`malaga`, `lens`).
     Tokeny z tego biegu: `wieczysta`, `podbeskidzie`, `rakow`, `malaga`, `lens`, `dortmund`,
     `hoffenheim`. Filtr kafla po tokenie PRZECIWNIKA obowiązkowy — `s=dortmund` zwraca
     najpierw esporty (`B Dortmund (Bjela)`).
  3. **Linie CAŁKOWITE (`ou1`,`ou2`,`ou3`) są w bloku „Liczba goli (z możliwym zwrotem)",
     NIE w „Liczba goli".** Blok „Liczba goli" ma tylko połówki (kropka w etykiecie).
     Etykietę rynku na kuponie czytaj jako `Liczba goli (z możliwym zwrotem)`.
  4. **Stawka:** `input[inputmode="decimal"]`, czyszczenie natywnym setterem + `input`,
     wpis realny przez `cdp("Input.insertText")` (odpowiednik `type_text`), potem
     `change`+`blur`; kontrola po `Postaw <stawka> zł`. 10/10 zgodne co do grosza.
  5. **Numer kuponu z modala „Kupon w grze"** (`/moje-zaklady/w-grze` →
     `.my-bets-ticket-header-actions` → URL `(modal:szczegoly/<numer>)`); strona renderuje
     leniwie — pętla kliku aż pojawi się „Numer kuponu". 10/10 odczytane.
  6. Sesja STS wypadła 2x (przekierowanie na `(modal:my-account)` po wejściu na
     `/moje-zaklady/w-grze`); po `lv-login.py sts` wróciła i odczyt się udał.
  7. API LasVegas rzuciło przejściowe `503` (~1 min) na wszystkie endpointy — ponawianie
     pomogło; raport `placed` dla 22f2df26 czekał do powrotu serwera.
  Wyniki (betId → ticket, kurs, stawka, saldo przed→po):
  03baa9b7 → <numer kuponu> (3.60, 22.48, saldo przed->po);
  f60f04ec → <numer kuponu> (8.10, 13.49, saldo przed->po);
  86b7d85d → <numer kuponu> (4.25, 13.49, saldo przed->po);
  816d5246 → <numer kuponu> (7.25, 17.99, saldo przed->po);
  09e25416 → <numer kuponu> (1.98, 32.18, saldo przed->po);
  947b09a6 → <numer kuponu> (5.40, 32.18, saldo przed->po);
  22f2df26 → <numer kuponu> (2.00, 35.04, saldo przed->po);
  1fcd834a → <numer kuponu> (10.50, 21.22, saldo przed->po);
  4040b1bf → <numer kuponu> (2.85, 15.77, saldo przed->po);
  e14699cf → <numer kuponu> (5.25, 10.00, saldo przed->po).
  Wszystkie kursy ≥ `odds` ze zlecenia (brak dryfu), wszystkie stawki == `stake`.

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
     gotowości: OBIE drużyny w `document.title`.
  3. **Rynek `Liczba goli` (ou...) NIE istnieje w ofercie żadnego z 6 meczów Pucharu
     Polski** — lupka rynków zwraca "Brak wyników". Wszystkie zlecenia `ou2/under` i
     `ou3/under` (7 szt.) rozstrzygnięte jako `skipped market_mismatch` z listą
     dostępnych rynków (`Mecz, Podwójna szansa, Awans`). To NIE `event_not_found` ani `line_not_found`.
  4. **Filtr rynków ukrywa CAŁY DOM rynków** — po wpisaniu frazy w `#Szukaj`
     `bo-match-detail-market-wrapper` zwraca 0, a `.market-tile-header__name` jest puste.
     Przed budową kuponu przeładuj stronę albo wyczyść filtr.
  5. **Numer kuponu tylko z modala "Kupon w grze"**; numer jest tez w URL
     `...(modal:szczegoly/<numer>)`. Lista "W grze" i ekran potwierdzenia numeru NIE pokazują.
  6. **Odczyt salda po "Postaw" ma opóźnienie** — ~3 s po przyjęciu kuponu `Depozyt`
     pokazuje jeszcze STARĄ kwotę. Odczytuj po ~5-6 s. Kryterium przyjęcia:
     "Przyjęliśmy Twój kupon!" ORAZ spadek salda o `stake`.

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
