# Stamp by displayed orientation and C_Sign retry

## Objective

1. A transient card error during a PKCS#11 signature must not cost a whole document.
2. The stamp must follow how each page is displayed, whatever its stored rotation.

## Problem

- Field run 2026-11-20 (DNIe 3.0, 8 parts, 591 signatures with one login): part 06 failed at page 70 of 75. The error was `C_Sign` with `CKR_GENERAL_ERROR` (0x5), and the signatures had slowed from about 690 ms to 920–980 ms just before. The user only learned about it from the final summary.
- The stamp is always a horizontal box at the bottom right of the page's *unrotated* space (`rect_sello`). It ignores page size and `/Rotate`, so the viewer turns the stamp with the page.
- In the original `EXP TEC NE-JUN-15 (1).pdf` (597 pages):

  | Pages | Size and storage | Stamp today | Expected stamp |
  |---|---|---|---|
  | 465 | A4, /Rotate 0 | bottom, horizontal | bottom, horizontal |
  | 47 | Letter, /Rotate 0 | bottom, horizontal | bottom, horizontal |
  | 51 | A4, landscape box, /Rotate 270 | vertical, right | **should be** bottom, horizontal |
  | 30 | A3, /Rotate 270 | vertical, right | vertical, right |
  | 3 | A3, /Rotate 90 | vertical, left | **should be** vertical, right |
  | 1 | A3, landscape box, /Rotate 0 | bottom, horizontal | **should be** vertical, right |

## Why

- The user's rule: a page displayed in portrait (A4, Letter) gets the stamp at the bottom, horizontal. A page displayed in landscape (A3) gets it on the right margin, vertical (rotated).
- PIN safety: the DNIe 3.0 key has CKA_ALWAYS_AUTHENTICATE, so the accepted PIN is already sent before every signature. A retry repeats exactly that. A PIN error never retries, and the PIN is never asked for again by hand, because a typo is what decrements the card's counter.

## Scope

- `dnie_pkcs11.py`: bounded retry of a signature on transient card errors only.
- `app.py`: requeue a failed document once at the end of the batch; live progress event; fix the batch summary log that says "CNG" for a PKCS#11 session.
- `firma_pdf.py`: default stamp position and rotation by displayed orientation (rotation-aware box and rotated appearance).
- `ui/app.js` (and CSS if needed): preview that matches the signed result.

## Constraints

- Never retry or resend the PIN after a PIN error (`CODIGOS_PIN_PKCS11`, `es_error_de_pin`).
- A position the user placed by hand (`posiciones_paginas`, per-document `caja`) keeps priority over the automatic layout.
- Generated artifacts in English; user-facing UI copy in Spanish (existing project language).

## Tasks

- [x] T1 PKCS#11 signature retry: up to 2 retries with about 1 s backoff on `CKR_GENERAL_ERROR` (0x5), `CKR_DEVICE_ERROR` (0x30), and `CKR_FUNCTION_FAILED` (0x6). Each retry redoes C_SignInit, the context login, and C_Sign. No retry on PIN errors. Each retry is logged. Route: inline (one source file plus its tests).
- [x] T2 Batch resilience: a document that fails with a transient card error goes back to the end of the queue once. The UI shows "reintentando" live. Fix the "firmas en la sesión CNG" label. Route: inline or delegated, depending on the UI size.
- [ ] T3 Stamp by displayed orientation in `firma_pdf.py`: classify each page from its view box and `/Rotate`, choose the default position (portrait: bottom right; landscape: right margin, vertical), and draw a rotated box and appearance that compensate `/Rotate`. Route: delegated writer (2+ non-trivial files with tests).
- [ ] T4 UI preview of the automatic layout per page (`rectPdf`, `cajaDePagina`, `ajustarTextoSello`). Route: delegated writer.
- [ ] T5 Version bump, build (direct steps, see the build memory), release (ask before push).

## Acceptance criteria

- A `CKR_GENERAL_ERROR` on one page does not fail the document when a retry succeeds; a PIN error stops at once with no retry.
- On the 597-page original, every page displayed in portrait shows a horizontal stamp at the bottom, and every page displayed in landscape shows a vertical stamp on the right margin.

## Checks

- `python -m pytest -q` (400 before this feature).
- `node --check` on a `.mjs` copy of `ui/app.js` for UI tasks.
- Field: the user re-signs the expediente with a DNIe 3.0.

## Delivery

- Forecast: about 600–900 authored lines (T3 and T4 carry most of them). Strategy: ask-on-risk; the user decides any split.

## Progress

- 2026-11-20 T1 done (route: inline; one source file plus its tests, no research needed).
  - `SesionPkcs11._firmar` retries through `_firmar_una_vez` (C_SignInit, context login, C_Sign) up to `REINTENTOS_FIRMA = 2` times, `ESPERA_REINTENTO_S = 1.0` apart, only for `CODIGOS_TRANSITORIOS` (0x5, 0x6, 0x30). A PIN error stops at once. Other errors raise with no retry. New counter `reintentos` in the close log.
  - Fake: `ModuloFalso.fallos_una_vez`. Tests: transient retry (3 codes), give up after 2 retries, no retry for CKR_DEVICE_REMOVED, PIN rejected in the retry's context login stops with no further PIN use.
  - RED: 41 errors (no `ESPERA_REINTENTO_S`). GREEN: 406 passed. Commit `29ca256`.
- 2026-11-20 T2 done (route: inline; only `app.py` changed, plus tests; the UI already shows a row's `pendiente` state with its message, so no UI change).
  - `_firmar_lote` iterates a `trabajo` list. A document that fails with `_es_error_transitorio_de_tarjeta` (`ErrorPkcs11` with an rv in `CODIGOS_TRANSITORIOS`, i.e. after the in-place retries) is marked `pendiente` with `MENSAJE_REINTENTO_AL_FINAL`, emitted live, and appended once to the end. A second failure is a normal error.
  - The batch summary log names the real session (`PKCS#11` or `CNG`) instead of always "CNG".
  - RED: 2 failed. GREEN: 408 passed.
