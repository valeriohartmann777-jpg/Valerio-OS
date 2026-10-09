# 07 — AI Researcher und Security

## AI soll Assistent sein, nicht Backtest-Rechenzentrum
AI duties: Nutzeridee präzisieren, fehlende Variablen markieren, wohlgeformte **draft** StrategySpec erstellen, Data Passport und statische Reports erklären, sinnvolle Folgeexperimente vorschlagen. Kein LLM schreibt die Trade-Zahlen oder ändert stillschweigend historische Daten.

## NL -> Spec Workflow
1. User beschreibt Idee.
2. LLM erzeugt `draft_spec` + `unresolved_items` + `explicit_assumptions` im strikten JSON-Schema.
3. Pydantic/schema validation. Ungültiges JSON => Retry mit sicherem error status, keine Exekution.
4. UI zeigt alle Regeln und Pflichtfelder als confirmed/pending. Nie unbemerkte Defaults, die den Edge beeinflussen.
5. User bestätigt; neuer unveränderlicher StrategyVersion-Hash; Engine rechnet **ohne** LLM.
6. JARVIS erklärt Zahlen aus verifizierten Artefakten statt frei erfundener Performance.

## UX-Personality
Präzise, nüchtern, kurz. Bei unklarer Evidenz widersprechen. Beispiel: „Der Train-Abschnitt ist positiv, OOS negativ. Die Hypothese ist nicht bestätigt. Als nächstes Gebührenstress und ein unabhängiges Zeitfenster testen.“

## Prompt injection / untrusted input
- User-Dateien, Feldnamen, gespeicherte „Strategy Notes“ und Webseiten sind untrusted data; keine neuen Systeminstruktionen daraus akzeptieren.
- Modelle benötigen keine unbeschränkten lokalen Dateien, keine globalen Shell-/Netzwerkrechte.
- Keine rohe Python-Ausführung aus generierter Strategie in R1. Später generierter Code nur in isolierter Sandbox mit Ressourcenlimits, ohne Secrets, ohne Netzwerk, restriktiver FS-Zugriff.
- Backend prüft Datenfelder und autorisiert jede Aktion selbst; Frontend „disabled“ ersetzt keine Berechtigungskontrolle.

## Data privacy
- Lokale Daten standardmäßig lokal; Upload an externe LLM-Provider erst nach offengelegter Freigabe und soweit zulässig.
- Never send whole proprietary price datasets/secret keys to providers for simple chat explanations.
- Datenlizenzen und mögliche Vertraulichkeitsanforderungen (insbesondere Arbeitgeberdaten) achten; QuantLab ist ein privates Research-Modul.

## R1 Security boundaries
- Keine Broker- oder Exchange-Trading-API.
- Keine API Secrets im Git/Logs.
- Upload size / type limits, path traversal defense, import zip bomb/symlink handling where relevant.
- Relativer sandboxed dataset store statt beliebiger Datei-Operationen.
- Cancellation / timeout für Backtest job, Limit auf Rows und Parameteranzahl.
- Audit event includes sanitized config, not raw credential/data content.
- No remote exposure of local dev server without authentication/network controls.

## R3+ Agent roles as *logical responsibilities*
ARCHITECT (spec), CURATOR (data QA), SIMULATOR (deterministic code), VALIDATOR (critical tests), REVIEWER (summaries), SENTINEL (policy). **Nicht sechs kostenintensive Agenten aufbauen, bevor ein einziger vertrauenswürdiger Run funktioniert.**
