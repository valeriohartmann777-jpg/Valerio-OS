# 11 — Integration in ein unbekanntes bestehendes JARVIS-Repository

**Das reale Repo liegt uns nicht vor.** Jede Pfadangabe nachfolgend ist ein mögliches Beispiel, keine Tatsache. Claude muss die Integrationspunkte identifizieren.

## Prüfen
1. `git status --short`, `git branch --show-current`, `ls`/Directory listing; existierendes `CLAUDE.md` lesen.
2. Package Manager (npm/pnpm/yarn), UI Stack, Router, Query Cache, State management, Design tokens, test suite.
3. Python service vorhanden? Wie wird Backend gestartet? Gibt es einen WebSocket Eventbus/Mission service?
4. Datenbanken/Migrationen, lokaler Storage, vorhandene Security-Grenzen, Scope von File APIs.
5. Repo-Stand und Arbeit anderer Module (insbes. JARVIS Core) schützen; niemals Reset/Force-Push.

## Plan A — React frontend + Python backend vorhanden
- Frontend routes `/quantlab` und `/quantlab/strategies/:id`, `/quantlab/experiments/:id` nach bestehendem Router.
- Python Domain package `backend/quantlab` oder `services/quantlab` per bestehender App-Konvention.
- API endpoints, event namespaced `quantlab.*`, migrations, tests integrieren.

## Plan B — Nur React/Next.js App vorhanden
- UI-Integration im vorhandenen Projekt. Backend QuantEngine separat als lokaler Python-Prozess mit klarer HTTP/typed contract API; vorhandene Auth-/CORS Regeln einhalten.
- Keine neuen Electron-Schichten installieren, nur weil die ursprüngliche Produktvision Electron erwähnte.

## Plan C — Noch kein JARVIS-Code
- Klar als „standalone MVP scaffold awaiting JARVIS integration“ kennzeichnen und nicht behaupten, JARVIS-Integration sei fertig.

## Nicht stören
- Bestehende `CLAUDE.md`, `.env`, package lockfiles und Daten ohne Prüfung in Ruhe lassen.
- Keine Daten aus anderen Projekten oder geheimen Ordnern importieren.
- Tests anderer Komponenten als Regression mitlaufen lassen.

## Informative Statusausgabe nach Repo-Inspektion
```
Observed repo: <real path>
Frontend/backend: <actual stack>
Existing tests: <commands + baseline>
Integration points: <files>
Changed/added files: <planned subset>
Release scope: R0+R1
Risks/blockers: <actual findings>
```
