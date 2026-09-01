# Playbook: STS (sts.pl)

Zweryfikowany E2E 2026-08-29 (Chrome, profil `~/.hermes/lv-browser-profile`, CDP :9222).

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
- Niezalogowany: widać `Zaloguj się` i `Załóż konto`. Wtedy procedura logowania ze SKILL.md
  (poproś użytkownika, czekaj ~30 s × max 5 min; dalej `failed not_logged_in`).
- Odczytaj saldo z tekstu `Depozyt NNN,NN zł` (prawy górny róg) → `balanceBefore`.

## Krok 3: nawigacja do meczu

**Zlecenie niesie `eventUrl`.** Gdy `eventUrlKind` = `event`, otwórz go BEZPOŚREDNIO
(`goto_url(eventUrl)` w bieżącej karcie) — żadnego szukania. Dopiero gdy `eventUrl`
jest null/`home`, szukaj: karta meczu na stronie głównej albo lupa → nazwa drużyny →
wynik meczu. URL meczu ma postać `/kursy/<slug>/<id>` — zweryfikuj, że tytuł strony
zawiera OBIE drużyny.

**Dyscyplina kart:** pracuj w JEDNEJ karcie (`goto_url`), nie otwieraj nowych
(`new_tab` tylko na samym początku, gdy nie ma żadnej prawdziwej karty). Strona meczu
STS jest ciężka (live-kursy + trackery) — każda dodatkowa karta mnoży CPU i potrafi
zawiesić `Runtime.evaluate`.

**Renderer zawieszony (eval timeout):** gdy `js(...)`/`page_info()` wisi do timeoutu —
karta jest martwa. Zamknij ją przez CDP HTTP: `curl http://localhost:9222/json`,
znajdź `id` karty sts.pl, `curl http://localhost:9222/json/close/<id>`, potem
`new_tab(eventUrl)` i pracuj dalej. Nigdy nie zostawiaj duplikatów kart meczu.

## Krok 4: wybór rynku i kursu

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
   Fallback: `Moje kupony` → `W grze` → najnowszy kupon → detal/numer.
   Ostateczność (gdy oba zawiodą): ticketId = betId — lepsze to niż brak raportu.
5. Odczytaj ponownie `Depozyt NNN,NN zł` → `balanceAfter`. Saldo powinno spaść dokładnie
   o stawkę (STS: z konta schodzi stawka brutto; „Możesz wygrać" liczone od stawki netto
   po podatku 12% — np. 2 zł → netto 1,76 zł → wygrana 5,19 zł przy kursie 2.95).
6. Raport: `bash scripts/lv-api.sh placed <betId> <ticketId> <actualOdds> <actualStake> <balanceBefore> <balanceAfter>`
   (actualOdds = kurs z kuponu, actualStake = kwota z przycisku Postaw, salda z kroku 2
   i punktu 5 jako liczby z kropką: `Depozyt 130,50 zł` → `130.50`). Serwer porównuje
   spadek salda ze stawką i przy rozjeździe wyłącza regułę auto-place — to bezpiecznik,
   nie statystyka; bez sald go nie ma.

## Pułapki

- Banery „bonus / boost / zgarnij" — ignoruj, nigdy nie zaznaczaj boostów (zmieniają kurs).
- **Minimalna stawka STS: 2 zł.** Zlecenie ze stawką < 2 zł odbije się od kasy —
  raport `skipped bookmaker_limit` (nie próbuj podnosić stawki samowolnie).
- Przycisk `Postaw` nieaktywny / toast z błędem → najpierw dopasuj do znanych
  komunikatów z kroku 6.3 (limit czasu gry = `bookmaker_limit`, minimalna stawka =
  `skipped bookmaker_limit`); nieznany tekst → `failed ui_error` z jego treścią.
- **Dzienny limit czasu gry (Odpowiedzialna gra) liczy CZAS SESJI, także sesje agenta.**
  Bieg 35–40 min ×4 w jeden dzień zjada limit sam z siebie — im krótsza sesja, tym lepiej;
  nie „szukaj" po stronie dłużej niż potrzeba (krok 3: bezpośredni URL meczu).
- Wylogowanie w trakcie (znów widać `Zaloguj się`) → `failed not_logged_in`.
- Nie klikaj `Postaw` dwa razy — po kliknięciu czekaj na ekran potwierdzenia.
