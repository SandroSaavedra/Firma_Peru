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
- [x] T2 — PKCS#11 single-PIN path for DNIe V3.0. Version 1.2.5, installer.
  - Route: delegated writer. Trigger: preparation reading of `dnie_cng.py`, `app.py` and the test fakes, plus 2+ non-trivial files (new binding module, app integration, tests).
  - RED: `tests/test_dnie_pkcs11.py`, `tests/test_lote.py` and `tests/test_app.py` failed to collect (`ModuleNotFoundError: dnie_pkcs11`); `tests/test_dnie_cng.py::test_el_firmante_de_sesion_delega_la_firma_en_su_respaldo` failed (`dnie_cng` had no `FirmanteDeSesion`).
  - GREEN: `python -m pytest -q` → 369 passed (326 before; 43 new). No access violation; only the pre-existing WinSCard 0x8010002E faulthandler note. `node --check ui/app.js` → OK. `build.ps1` → its test run 369 passed; `dist\installer\FirmaPeru-Setup-1.2.5.exe` built. The IDEMIA DLL is not bundled (loaded at runtime from Program Files).
  - Smoke check on the dev PC (real `idplug-pkcs11.dll` 64-bit, outside pytest, no `C_Login`): Cryptoki 2.40; the function list's own `C_GetFunctionList` entry equals the exported address, which confirms the `_pack_ = 1` layout; `C_Initialize` OK; 0 slots with and without token (no reader was connected at that moment); `C_Finalize` OK. `buscar_llave` → None («ningún token tiene el certificado elegido»), 671 ms including the DLL load.
  - Implementation:
    - `dnie_pkcs11.py` (new): ctypes Cryptoki binding (`CK_ULONG` 32-bit, packed structs, cdecl via `CFUNCTYPE` over `C_GetFunctionList`). `_Cryptoki` is the test seam. `ruta_modulo()`: `FIRMADOR_PKCS11` if set (a path that does not exist disables the path) or `%ProgramFiles%\IDEMIA\IDPlugClassic\DLLs\idplug-pkcs11.dll`. `buscar_llave(cert_der)`: no PIN; matches `CKO_CERTIFICATE`/`CKA_VALUE` against the certificate CNG chose and returns slot + `CKA_ID`; never raises. `SesionPkcs11`: one `C_Login(CKU_USER)`; finds `CKO_PRIVATE_KEY` by `CKA_ID`; reads `CKA_ALWAYS_AUTHENTICATE`; per signature `C_SignInit(CKM_RSA_PKCS)` → `C_Login(CKU_CONTEXT_SPECIFIC)` if needed → `C_Sign(DigestInfo || hash)` (sha256/384/512). PIN in a UTF-8 ctypes buffer, wiped on close and on a PIN error; `CKR_USER_ALREADY_LOGGED_IN` is OK; close always runs `C_Logout`, `C_CloseSession`, `C_Finalize`. PIN codes 0xA0–0xA4 map to the Windows codes the app already handles (0x8010006B wrong PIN; 0x8010006C for locked and expired), so the same message and batch stop apply.
    - `abrir_sesion(llave, pin)` decides the path: CNG (returns None) when the key is not RSA, the module is missing, no token has the certificate, the token reports a PIN pad (`CKF_PROTECTED_AUTHENTICATION_PATH`), `CKM_RSA_PKCS` is not listed, anything fails before `C_Login`, or something fails after an accepted login (private key not found). It raises (no CNG) on a PIN error, on a token flagged `CKF_USER_PIN_LOCKED`, or when `C_Login` fails for a non-PIN reason, because the PIN may have reached the card.
    - Mechanism decision: only `CKM_RSA_PKCS` (hash computed locally, as with CNG). `CKM_SHA256_RSA_PKCS` would need the full data instead of the digest, so it would change the signer seam; if the token does not list `CKM_RSA_PKCS`, the session falls back to CNG before the PIN is used and logs the mechanism list.
    - `dnie_cng.FirmanteDeSesion` (was `_FirmanteDeSesion`): delegates the raw signature to any backend with `_firmar(firmante, resumen, algoritmo)`, `SesionFirma` or `SesionPkcs11`. No change to pyHanko integration or chain building.
    - `app.py`: `_sesion_de_firma(llave, hwnd, pin)` used by single `cada_pagina` and the batch; tries PKCS#11 only with an app PIN and logs «Firma por PKCS#11 (IDEMIA): un solo PIN para todas las páginas». Without an app PIN nothing changes.
    - Tests: `tests/test_dnie_pkcs11.py` (fake module: ABI sizes, DigestInfo, module path, lookup, one login + context login per signature, PIN errors, fallbacks, closing); app-level tests in `tests/test_lote.py` (batch and single document sign N pages with one PIN, wrong PIN stops with no CNG, unrecognised card and pre-login failure go to CNG); autouse `sin_pkcs11` in `tests/test_app.py` and `tests/test_lote.py` so no test loads the real DLL.
    - README note in «Firma real en cada página». Version 1.2.5 (`version.py`, `installer/FirmaPeru.iss` ×2, README ×3).
  - Not verifiable without a V3 card: whether the IDEMIA module recognises the V3 token and exposes the certificate with a `CKA_ID` shared by the private key; whether the signing key has `CKA_ALWAYS_AUTHENTICATE` (both cases are handled); whether `CKM_RSA_PKCS` is offered; whether context-specific login with the in-memory PIN is accepted or the module shows its own PIN dialog; real latency per page.
  - Commits: `6ac4a13` feat: add a minimal PKCS#11 binding for the IDEMIA DNIe 3.0 module; `3a4b806` feat: sign DNIe 3.0 pages with a single PIN through the IDEMIA PKCS#11 module.

## Acceptance criteria

- T1, tested with fakes:
  - silent refusal → the same page is retried interactively, the PIN buffer is wiped, and later pages are interactive;
  - Windows prompt cancelled → the run is cancelled with no file;
  - a PIN error in interactive mode → the run stops;
  - V1/V2 behavior is unchanged.
- T2, tested with fakes:
  - certificate match → PKCS#11 path, one `CKU_USER` login, a context-specific login before each signature only when `CKA_ALWAYS_AUTHENTICATE` is set;
  - PIN error → the run stops, no CNG fallback, the PIN buffer is zeroed;
  - module missing or no match → the CNG path is unchanged;
  - sessions and the library are always closed.
- The full suite passes and `node --check ui/app.js` is OK.
- Field check (user, V3 card): T1 signs every page with a PIN prompt per page; T2 signs every page with one PIN entry.

## Progress

- 2026-11-19: document created; T1 delegated.
- 2026-11-19: T1 done (route: delegated writer). Field check with a V3 card pending.
- 2026-11-19 (coordinator): the retry now runs on a clean key handle.
  - Change: before the Windows-prompt retry, the session frees the key handle that received `SmartCardPin`, opens a new one and assigns the same HWND. This removes the writer's risk #1, so the prompt does not inherit the silent PIN state.
  - Commit `cc76855`.
  - RED: 2 failed. GREEN: 326 passed. 1.2.4 rebuilt (its test run: 326 passed).
- 2026-11-19: T2 done (route: delegated writer). 369 passed; 1.2.5 installer built. Field check with a V3 card pending: one PIN for all pages through `idplug-pkcs11.dll`; the log line «Firma por PKCS#11 (IDEMIA)» confirms the path.
- 2026-11-19 (coordinator): PKCS#11 lookup only when the card can be a DNIe 3.0.
  - Before this change, the smoke test with the real V2 DNIe showed `CKR_TOKEN_NOT_RECOGNIZED`, a CNG fallback, and a 6.6 s IDEMIA lookup on every signing.
  - `app._puede_ser_dnie_v3()` checks the WinSCard card names and skips PKCS#11 only when every inserted card is known and none is «Peru eID». If the names are unknown or the query fails, PKCS#11 is tried.
  - With the real V2 card: «no es un DNIe 3.0 ('CJCOP3')» in 0.01 s.
  - RED: errors (no `_recolectar_hechos`). GREEN: 372 passed.
- 2026-11-20 (coordinator, inline): field regression of the check above.
  - A DNIe 3.0 on another machine is registered as «Peru DNIe Device». The allowlist («Peru eID») classified it as non-3.0, skipped PKCS#11 and fell back to CNG, so Windows asked for the PIN on every page (75 prompts per part, logs of 2026-11-19 19:36 and 2026-11-20 10:00).
  - Fix: invert the check into a denylist. `TARJETAS_NO_DNIE_V3 = ("cjcop3",)`; PKCS#11 is skipped only when every inserted card is a known V1/V2. Any other name tries PKCS#11, which costs a few seconds at most instead of a PIN per page.
  - RED: `test_dnie_v3_con_otro_nombre_de_tarjeta_firma_con_un_solo_pin` failed. GREEN: 400 passed.
