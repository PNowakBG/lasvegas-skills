# lasvegas-skills — skille Hermesa do LasVegas

Paczka skilli dla [Hermes Agent](https://hermes-agent.nousresearch.com) podłączających
stacjonarnego agenta na Twoim komputerze do [LasVegas](https://lv.ap2ju.com).

## Co to daje

Agent Hermes na Twoim desktopie (macOS, Linux/Omarchy, Windows) sam:

1. pobiera z LasVegas zlecenia zakładów wynikające z **Twoich kryteriów**
   (fundusz: minimalny edge, Kelly, limity stawek — wszystko ustawiasz w LasVegas, nie w agencie),
2. korzysta z **Twojego profilu przeglądarki** (Twoje loginsy do bukmacherów — bez pytania o hasła),
3. sam poznaje interfejs bukmachera (Superbet, STS, Betclic, Betfan), pisze i naprawia
   własne playbooki, gdy UI się zmieni,
4. przed każdym postawieniem **weryfikuje kupon** (kurs, stawka, rynek) i tylko przy zgodności klika „Postaw",
5. raportuje wynik z powrotem do LasVegas (betId, realny kurs, saldo), a każda sesja ma
   pełny transkrypt w Hermesie (audyt każdego wywołania narzędzia).

## Instalacja — jedno polecenie

W LasVegas: **Podłącz agenta** → skopiuj komendę dla swojego systemu. Wygląda tak:

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/PNowakBG/lasvegas-skills/main/install.sh | bash -s -- KOD_PAROWANIA
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/PNowakBG/lasvegas-skills/main/install.ps1 | iex
```

**W trakcie instalacji** Hermes zapyta *„How would you like to set up Hermes?"* —
wybierz **Quick Setup (Nous Portal)** (pierwsza opcja): darmowy login OAuth,
zero kluczy API, model i narzędzia skonfigurowane automatycznie.

Instalator sam: stawia Hermesa, konfiguruje przeglądarkę, instaluje skilla, podłącza token
i rejestruje autostart. Nie musisz znać się na terminalu — poza wklejeniem jednej komendy.

Ręcznie (jeśli wolisz):

```bash
hermes skills tap add PNowakBG/lasvegas-skills
hermes skills install PNowakBG/lasvegas-skills/lv-executor
```

## Jedyny krok, którego nie da się zautomatyzować

Gdy agent pierwszy raz trafi na ekran logowania bukmachera, otworzy widoczne okno
przeglądarki i poprosi: *„Zaloguj się do X w otwartym oknie — poczekam"*. Logujesz się
raz (hasłem/2FA), agent czeka i dalej działa sam. Cookies z Twojego profilu załatwiają resztę.

## Wyłączanie awaryjne (kill switch)

W LasVegas: reguły auto-place per bukmacher → wyłącz. Kolejka natychmiast przestaje
wydawać zlecenia — agent przy każdym postawieniu sprawdza stan i się zatrzymuje.
Urządzenia agenta odwołasz w zakładce **Podłącz agenta → Twoje urządzenia**.

## Ryzyko — przeczytaj

Regulaminy bukmacherów zakazują automatyzacji obstawiania. Realna konsekwencja to
**blokada konta i możliwa konfiskata środków** — przenosisz to ryzyko na siebie.
Limity budżetu w LasVegas chronią pieniądze, nie konto.

## Struktura repo

```text
skills/lv-executor/     # skill: kolejka → claim → weryfikacja kuponu → postawienie → raport
  references/           # twarde reguły weryfikacji + procedura uczenia się playbooków
  playbooks/            # powstają LOKALNIE u użytkownika — agent sam je pisze i naprawia
  scripts/lv-api.sh     # helpery API LasVegas (poll, claim, confirm, kill switch)
install.sh / install.ps1 # instalator jednoplikowy (krok 0 wdrożenia)
```
