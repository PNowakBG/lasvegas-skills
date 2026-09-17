# lv-credentials.ps1 — zapis (albo poprawka) loginu i hasła do bukmachera na TYM komputerze.
#
# Użycie (PowerShell):  & "$env:USERPROFILE\.hermes\skills\lv-executor\scripts\lv-credentials.ps1" sts
#                       (drugi bukmacher: superbet)
#
# Plik: %USERPROFILE%\.hermes\lv-bookmakers.env, klucze LV_STS_LOGIN / LV_STS_PASSWORD
# (dla Superbet: LV_SUPERBET_*). Ponowne uruchomienie NADPISUJE wpis tego bukmachera —
# tak poprawiasz źle wpisane hasło. Hasło czytane bez echa, nigdy z argumentów; plik
# dostaje ACL tylko dla bieżącego użytkownika (odpowiednik chmod 600). Czyta go
# wyłącznie scripts/lv-login.py przy logowaniu agenta — LasVegas ani model go nie widzą.
# To jest windowsowy odpowiednik `lv-executor-cycle.sh credentials <slug>`.
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("sts", "superbet")]
  [string]$Bookmaker
)

$ErrorActionPreference = "Stop"
$hermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:USERPROFILE ".hermes" }
$credFile = Join-Path $hermesHome "lv-bookmakers.env"
$keyPrefix = "LV_" + $Bookmaker.ToUpper()

$credLogin = Read-Host "Login/e-mail do $Bookmaker"
$securePass = Read-Host "Hasło do $Bookmaker (bez echa)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePass)
try { $credPass = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if (-not $credLogin -or -not $credPass) { throw "Login i hasło nie mogą być puste." }

New-Item -ItemType Directory -Force -Path $hermesHome | Out-Null
$credText = ""
if (Test-Path $credFile) { $credText = Get-Content -Path $credFile -Raw -Encoding UTF8 }
if (-not $credText) { $credText = "" }
# Podmiana PER KLUCZ — wpisy innych bukmacherów zostają nietknięte.
$credText = [regex]::Replace($credText, "(?m)^$keyPrefix" + "_(LOGIN|PASSWORD)=[^\r\n]*\r?\n?", "")
$credText = $credText.TrimEnd([char[]]"`r`n")
if ($credText) { $credText += "`r`n" }
$credText += "$keyPrefix" + "_LOGIN=$credLogin`r`n$keyPrefix" + "_PASSWORD=$credPass`r`n"
$credPass = $null

# UTF-8 bez BOM (lv-login.py czyta jako UTF-8).
[IO.File]::WriteAllText($credFile, $credText, (New-Object System.Text.UTF8Encoding($false)))
$acl = Get-Acl $credFile
$acl.SetAccessRuleProtection($true, $false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
  [System.Security.Principal.WindowsIdentity]::GetCurrent().Name, "FullControl", "Allow")
$acl.SetAccessRule($rule)
Set-Acl $credFile $acl

Write-Host "Zapisano poświadczenia $Bookmaker w $credFile (tylko Ty masz do niego dostęp)."
Write-Host "Agent użyje ich przy najbliższym zleceniu. Sprawdzian od ręki (gdy przeglądarka agenta działa):"
Write-Host "  python `"$hermesHome\skills\lv-executor\scripts\lv-login.py`" $Bookmaker"
