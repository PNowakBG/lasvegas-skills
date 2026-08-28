# Procedura uczenia się playbooków bukmacherów

Playbook = wiedza agenta o interfejsie JEDNEGO bukmachera, trzymana lokalnie
w `playbooks/<slug>.md`. Nikt poza agentem go nie pisze i nie aktualizuje:
agent EKSPLORUJE → DOKUMENTUJE → WERYFIKUJE, a potem SAMONAPRAWIA i raz na
tydzień robi HEALTH CHECK. Użytkownik w tym procesie nie uczestniczy
(poza jednorazowym zalogowaniem, gdy buk wymusi ekran logowania).

## EKSPLORUJ (pierwszy kontakt z bukmacherem)

Cel: ustalić łańcuch stawiania bez postawienia niczego.

1. Otwórz stronę główną buka w trybie headed (widoczne okno).
2. Mapuj UI przez `browser_snapshot` (accessibility tree) + `browser_vision`
   (screenshot) tam, gdzie tekst nie wystarcza (loga, ikony, captcha).
3. Ustal po kolei:
   - **login state detection**: po czym poznać „zalogowany vs nie"
     (np. widoczny saldo / nazwa konta vs przycisk „Zaloguj"),
   - **wyszukiwanie meczu**: pole szukania / nawigacja po ligach; jak znaleźć
     mecz `homeTeam vs awayTeam`,
   - **rynek i typ**: gdzie przełączać rynki (1x2, over/under, BTTS…), jak
     klikać selekcję i trafić do kuponu,
   - **kupon**: gdzie wpisuje się stawkę, gdzie pokazuje się kurs i potencjalna wygrana,
   - **postawienie**: który przycisk stawia (JEGO dokładna etykieta), jaki ekran
     potwierdzenia następuje, gdzie odczytać identyfikator kuponu,
   - **pułapki**: popup cookies, komunikaty o limitach, banery bonusowe,
     „quick bet", zmiany layoutu live.
4. PODCZAS eksploracji NIGDY nie klikaj przycisku stawiania. Zatrzymaj się na
   ekranie kuponu. Jeśli przycisk stawiania jest osiągalny jednym kliknięciem
   od kursora — nie trzymaj kursora na nim.

## DOKUMENTUJ

Zapisz wynik przez `skill_manage write_file` jako `playbooks/<slug>.md`
(`skill_manage` działa w katalogu skilla; slug = `superbet` | `sts` | `betclic` | `betfan`).

Wymagane sekcje playbooka:

```markdown
# Playbook: <bukmacher>
updated: <ISO date>   verified: <yes/no>   failures: <licznik>

## Login state detection
## Wyszukiwanie meczu (eventUrl kind=event vs kind=home)
## Rynek i typ (mapowanie market/outcome → ścieżka w UI)
## Kupon (stawka, kurs, weryfikacja wartości)
## Postawienie (przycisk, potwierdzenie, odczyt ticketId)
## Pułapki (popupy, limity, komunikaty)
## Log napraw (data + co się zmieniło)
```

Zasady zapisu: konkretne ref-id/etykiety tam gdzie stabilne, opisy ścieżek tam
gdzie UI płynne. Playbook ma pozwalać wczorajszemu Tobie wykonać zlecenie bez
ponownej eksploracji.

## WERYFIKUJ (przed pierwszym REALNYM zleceniem)

Przebieg próbny: wykonaj cały łańcuch do ekranu kuponu z wartościami zlecenia,
NIE klikając „Postaw". Porównaj ekran z zleceniem wg `verification-rules.md`.
Zgadza się → ustaw `verified: yes` w nagłówku playbooka. Nie zgadza się →
popraw playbook i powtórz. Zlecenia bukmachera bez `verified: yes` NIE stawiają realnie.

## SAMONAPRAWA (drift UI)

Gdy zlecenie wywala się na kroku (przycisk znika, popup blokuje, nowy layout):

1. Zrób `browser_snapshot` + `browser_vision` aktualnego ekranu.
2. Ustal, co się zmieniło względem playbooka.
3. `skill_manage patch` — popraw TYLKO zmienioną sekcję, podbij `updated`,
   zwiększ `failures`, dopisz wpis w „Log napraw".
4. Powtórz krok zlecenia.
5. Druga porażka pod rząd → przerwij zlecenie (`failed: ui_error`) i powiadom
   użytkownika. Playbook zostaje z poprawkami — kolejne zlecenie zacznie od nich.

## HEALTH CHECK (tygodniowy, bez stawiania)

Raz w tygodniu (cron) przejdź po każdym bukmacherze z playbookiem:

1. Otwórz stronę, sprawdź login state detection (bez logowania — tylko wykrycie).
2. Wyszukaj jeden aktualny mecz z oferty, zbuduj kupon na 0,00 (lub najmniejszą
   dozwoloną „pustą" stawkę), NIE stawiaj.
3. Gdy którykolwiek krok różni się od playbooka → SAMONAPRAWA od razu.
4. Zaktualizuj `updated` w nagłówku, nawet gdy nic się nie zmieniło
   (dowód, że health check przeszedł).

Dzięki temu drift UI wykrywany jest w tygodniowym oknie, zanim trafi pierwsze
realne zlecenie.
