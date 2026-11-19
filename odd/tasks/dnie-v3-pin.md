# DNIe V3.0: per-page signing when the card refuses a silent PIN

## Objective

Let DNIe V3.0 holders sign with «Firma real en cada página».
- First, unblock them right away: when the card refuses the app-supplied PIN, fall back to the Windows PIN prompt on every page.
- Then, make it a single PIN entry through PKCS#11 (IDEMIA `idplug-pkcs11.dll`).

## Problem

Field log from a DNIe V3.0 (IDEMIA IDplug Peru minidriver, key name is a GUID, legacy key spec 2):
- `NCryptSignHash` with `NCRYPT_SILENT_FLAG`, after setting `SmartCardPin`, returns `0x80090022` (NTE_SILENT_CONTEXT) in 0 ms. The card is never touched.
- The app then shows «El DNIe no aceptó el PIN sin su propio cuadro de diálogo. Firme con otra opción de páginas». The other page options are locked since 1.2.2, so the user has no way out.

Hypothesis, not verified without a V3 card: the V3 signing key has an «always prompt» PIN policy, so the Smart Card KSP refuses silent use. This matches ReFirma and the other apps asking for the PIN on every page.

DNIe V1/V2 (CJCOP3, ciamd.dll) is not affected: one PIN works there.

## Scope (authorized by the user on 2026-11-19)

- T1 — Fallback.
  - When `NCryptSignHash` returns NTE_SILENT_CONTEXT in a session with an app-supplied PIN, switch that session to interactive mode:
    - wipe the PIN buffer;
    - stop setting `SmartCardPin`;
    - sign without `NCRYPT_SILENT_FLAG`, with the window handle, so Windows shows its own PIN prompt;
    - retry the same page once. That retry is safe because the card was not touched.
  - All later pages in that session, and later documents in a batch, use interactive mode.
  - Cancelling the Windows prompt cancels the whole run and leaves no partial file. A wrong or blocked PIN still stops the run right away, with no retries.
  - The UI tells the user that this DNIe asks for the PIN on every page.
  - The error text no longer points to the locked page options.
  - Log the switch.
- T2 — PKCS#11 path for DNIe V3.0 with IDEMIA `idplug-pkcs11.dll`, if it is installed:
  - log in once with the app-supplied PIN;
  - for keys with `CKA_ALWAYS_AUTHENTICATE`, do a context-specific login with the in-memory PIN before each signature;
  - one PIN entry for the whole document or batch;
  - CNG stays the path for V1/V2 cards and the fallback when PKCS#11 is unavailable or fails before any signature.

## Constraints

- The PIN is only ever in memory and wiped after use. It is never logged, persisted or returned to the UI.
- Never retry after a PIN error, which could block the DNIe.
- Incremental signatures only. Earlier signatures stay valid. Output is atomic.
- Unit tests with fakes. The field check is done by the user with a V3 card.
- No AI attribution in commits. Use Conventional Commits.

## Tasks

- [x] T1 — Interactive fallback on NTE_SILENT_CONTEXT, plus UI notice and error text. Version 1.2.4, installer, release.
  - Route: delegated writer. Trigger: preparation reading of `dnie_cng.py`, `app.py` and `ui/app.js`, plus 2+ non-trivial files.
  - RED: 10 new `tests/test_dnie_cng.py` tests failed (`SesionFirma` had no `al_pedir_pin_por_firma` / `pin_por_firma`); 12 new or updated tests in `tests/test_lote.py`, `tests/test_app.py` and `tests/test_interfaz.py` failed (no retry, no `pin_por_pagina` event, old 0x80090022 text, NTE_USER_CANCELLED not a cancel, Windows-prompt cancel returned an error, no UI note).
  - GREEN: `python -m pytest -q` → 325 passed (301 before). `node --check ui/app.js` → OK. `build.ps1` → its test run 325 passed; `dist\installer\FirmaPeru-Setup-1.2.4.exe` built.
  - Implementation:
    - `dnie_cng.SesionFirma`: on `NTE_SILENT_CONTEXT` (0x80090022) from the PIN assignment or the silent sign, it wipes the PIN buffer, sets the read-only `pin_por_firma`, logs the switch (WARNING, no PIN), calls the optional `al_pedir_pin_por_firma` callback once (its errors are swallowed), and retries the same hash once without `NCRYPT_SILENT_FLAG`. The HWND set at session open is kept. All later signatures in the session are interactive, with no `SmartCardPin`. A second refusal, a cancel or a PIN error is never retried. PIN errors still stop the session (`pin_provisto` stays True). Sessions without a PIN never retry.
    - `app.py`: both `_firmar` and `_firmar_lote` pass `al_pedir_pin_por_firma=self._avisar_pin_por_pagina`, which emits `progresoLote({"evento": "pin_por_pagina"})`. New 0x80090022 text: «El DNIe no permitió firmar. Vuelva a intentarlo; si continúa, envíe el registro de diagnóstico.». 0x80090036 (NTE_USER_CANCELLED) counts as a cancel. In `_firmar`, a cancel code from the PIN prompt now returns `{"ok": False, "cancelado": True, "mensaje": …}`, so the UI shows a notice instead of an error. The batch still ends as `pin_cancelado`, and a PIN error still stops it.
    - `ui/app.js`: `NOTA_PIN_POR_PAGINA`; the `pin_por_pagina` event sets `estado.lote.pinPorPagina` and replaces the progress dialog note; `notaDialogoLote` keeps the note if the dialog is redrawn.
    - README «Firma real en cada página» note. Version 1.2.4 (`version.py`, `installer/FirmaPeru.iss` ×2, README ×3).
  - Pre-existing, unrelated: `tests/test_diagnostico_dnie.py::test_recolectar_hechos_nunca_lanza` reads WinSCard for real and faulthandler prints «Windows fatal exception: code 0x8010002e» (no readers). It is not an access violation, and the test passes.
  - Not verifiable without a V3 card: whether the KSP shows its prompt (and accepts the PIN) on a handle that already received `SmartCardPin`; which code a cancelled V3 prompt returns.
  - Commit: `bc5a0b6` feat: fall back to the Windows PIN prompt per page when the DNIe refuses a silent PIN.
- [ ] T2 — PKCS#11 single-PIN path for DNIe V3.0.
  - Route: delegated writer, after T1.

## Acceptance criteria

- T1, tested with fakes:
  - silent refusal → the same page is retried interactively, the PIN buffer is wiped, and later pages are interactive;
  - Windows prompt cancelled → the run is cancelled with no file;
  - a PIN error in interactive mode → the run stops;
  - V1/V2 behavior is unchanged.
- The full suite passes and `node --check ui/app.js` is OK.
- Field check (user, V3 card): T1 signs every page with a PIN prompt per page; T2 signs every page with one PIN entry.

## Progress

- 2026-11-19: document created; T1 delegated.
- 2026-11-19: T1 done (route: delegated writer). Field check with a V3 card pending.
