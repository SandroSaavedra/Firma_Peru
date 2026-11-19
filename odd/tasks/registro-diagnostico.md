# Registro de diagnóstico (log) para fallos de firma

## Objective

Write a persistent, rotating diagnostic log. When a signer reports that "the DNIe is not recognized" or "the PIN prompt never appears", their log file shows where the flow stopped and why.

## Problem

On a colleague's PC, signing a PDF that already carried a signature reportedly did not recognize the DNIe and never showed the PIN prompt. Signing a fresh PDF worked on the same PC. The packaged app runs windowed (no console), and `logging.basicConfig` only writes to stderr. Nothing is kept, so the failure cannot be diagnosed.

## Why

Without evidence we can only guess: the save dialog may be hidden behind the window, the UI may block the sign button, the CNG key lookup may be failing, or the PIN prompt may be failing. The log has to show which one it is.

## Scope (authorized)

- A file log at `%LOCALAPPDATA%\FirmadorDNIe\registros\firmador.log`, rotating at 1 MB with 5 backups, in UTF-8. The console output stays.
- Uncaught exceptions are logged, including those from worker threads.
- A startup header with app version, frozen yes/no, Python, Windows and pyHanko versions.
- A single source for the app version (`version.py`). `build.ps1` reads its default from that file.
- Signing-flow instrumentation:
  - opening and enqueuing a PDF;
  - the decision about where to save;
  - DNIe key enumeration and selection (CNG);
  - the moment before the PIN prompt and the status it returns;
  - `firmar_pdf` duration;
  - failures, with exception class, message and HRESULT.
- UI-side events sent to the log:
  - a click on «Firmar»;
  - the sign button being disabled, with the reason;
  - error dialogs shown.
- A way for the user to open the log folder from the app. The README explains where the log lives and how to send it.

## Constraints

- Never log the PIN (the app never sees it) or document contents.
- Identifiers that are fine to log locally: file names and paths, page and signature counts, DocMDP, certificate subject CN, key usage and validity.
- Logging failures must never break signing. If the log folder is not writable, fall back to console only.
- No AI attribution in commits. Use Conventional Commits.

## Tasks

- [x] T1 — Logging infrastructure: `registro.py` (rotating file handler, excepthooks, startup header) and `version.py` as the version source, with `build.ps1` reading it. Wired into `app.py` startup. Tests included.
  - Evidence: RED `tests/test_registro.py` failed at collection (`ModuleNotFoundError: registro`); GREEN `python -m pytest -q` → `192 passed`; `node --check ui/app.js` OK; `build.ps1` parses (0 errors) and its `Select-String` pattern yields `1.1.0` from `version.py` (full build not run).
- [x] T2 — Backend instrumentation of the signing flow in `app.py` and `dnie_cng.py` (single and batch). Tests use `caplog` for success and failure paths.
  - Added from a field failure (`NCryptEnumKeys falló (0x80090011)` on a second PC): `listar_llaves` treats NTE_NOT_FOUND as "no keys" (WARNING with status hex and provider), so the user gets the friendly «No se encontró ninguna llave con certificado…» message; other enumeration failures log their status hex and re-raise. `ErrorCNG` now keeps `codigo` (message unchanged).
  - Evidence: RED 14 new tests failed before implementation (missing `describir_llave`, `codigo`, log lines); NTE_NOT_FOUND test RED with the branch disabled. GREEN `python -m pytest -q` → `208 passed`; `node --check ui/app.js` OK.
- [x] T3 — UI: event logging through an API method, an «Abrir registro» action (error dialog plus an unobtrusive link), and a README section. Static UI tests included.
  - Evidence: RED 5 new tests failed (3 static UI checks, `registrar_evento`, `abrir_carpeta_registros`); GREEN `python -m pytest -q` → `213 passed`; `node --check ui/app.js` OK.
- [x] T4 — Precise DNIe diagnosis (WinSCard facts → `diagnosticar` → friendly message) and up to 3 retries of transient card errors during key listing/selection. Never retries PIN errors, cancellation or NCryptSignHash. Added by the coordinator after the field failure `NCryptEnumKeys 0x80090011` on every PC for one DNIe.
  - Route: inline in the same delegated writer (new module `diagnostico_dnie.py` plus wiring in `dnie_cng.py`, `app.py`, `ui/app.js`).
  - Precedence: sin_servicio > sin_lector > sin_tarjeta > tarjeta_muda > sin_minidriver > en_uso_exclusivo > sin_certificados > certificado_vencido > ok; unknown facts give `desconocido` and keep the original error.
  - Retries: up to 3 retries (4 attempts, 1 s apart) of 0x8010000B, 0x80100068, 0x80100069 and 0x80090011 while listing keys; PIN errors, cancellation and NCryptSignHash are never retried.
  - `estado_dnie` shows the diagnosis (and `codigo` as a tooltip) and reports an expired signing certificate.
  - Evidence: RED `tests/test_diagnostico_dnie.py` failed at collection (`ModuleNotFoundError: diagnostico_dnie`), then 2 app-level tests failed until `estado_dnie` was wired; GREEN `python -m pytest -q` → `245 passed`; `node --check ui/app.js` OK. `recolectar_hechos()` smoke-run on the dev PC listed 2 readers (one with a card, name `CJCOP3`).

## Route

Delegated direct, one writer. Trigger: 2+ non-trivial files (`app.py`, `dnie_cng.py`, `ui/app.js`, a new module and tests).

## Acceptance criteria

- After any signing attempt, `firmador.log` shows each step reached and, on failure, the reason, including the CNG status code.
- The full test suite passes and `node --check ui/app.js` is OK.
- `build.ps1 -SinInstalador` (or a full build) still works and picks up the version from `version.py`.

## Progress

- 2026-11-19: document created; T1–T3 delegated to one writer.
- T1 committed as `cd18094` (feat: keep a rotating diagnostic log under LOCALAPPDATA).
- T2 committed as `874f712` (feat: log each signing phase and CNG status to the diagnostic log).
- T3 committed in the commit that adds this line (feat: send UI events to the diagnostic log and let users open it).
- T3 committed as `ab191ab`.
- T4 committed in the commit that adds this line (feat: diagnose why the DNIe is not available and retry transient card errors).
