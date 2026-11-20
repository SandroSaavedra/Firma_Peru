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
- A hand-placed position keeps priority and follows the same displayed-orientation rule (frame coordinates).
- Generated artifacts in English; user-facing UI copy in Spanish (existing project language).

## Tasks

- [x] T1 PKCS#11 signature retry: up to 2 retries with about 1 s backoff on `CKR_GENERAL_ERROR` (0x5), `CKR_DEVICE_ERROR` (0x30), and `CKR_FUNCTION_FAILED` (0x6). Each retry redoes C_SignInit, the context login, and C_Sign. No retry on PIN errors. Each retry is logged. Route: inline (one source file plus its tests).
- [x] T2 Batch resilience: a document that fails with a transient card error goes back to the end of the queue once. The UI shows "reintentando" live. Fix the "firmas en la sesión CNG" label. Route: inline or delegated, depending on the UI size.
- [x] T3 Stamp by displayed orientation in `firma_pdf.py`: classify each page from its view box and `/Rotate`, choose the default position (portrait: bottom right; landscape: right margin, vertical), and draw a rotated box and appearance that compensate `/Rotate`. Route: delegated writer (2+ non-trivial files with tests).
- [x] T4 UI preview of the automatic layout per page (`rectPdf`, `cajaDePagina`, `ajustarTextoSello`). Route: delegated writer.
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
- 2026-11-20 T3 done (Route: delegated writer (writer trigger: firma_pdf.py + tests, research on pyHanko appearance rotation)).
  - `giro_de_sello(vista, rotacion)` gives the layout frame rotation θ: displayed portrait → θ = r; displayed landscape → θ = (r + 90) % 360. θ = 0 keeps the historical geometry and appearance byte-for-byte (no /Matrix).
  - `rect_sello(pos, vista, tipo, rotacion=None)`: with `rotacion` the historical rule runs in the frame (fw, fh) and the rect is mapped back to unrotated space (90/270 swap width and height); without it, θ = 0. Every position (automatic, per-document `caja`, hand-placed `posiciones_paginas`) is a frame fraction (user decision, changes spec item 4). Pure helpers `fraccion_en_marco` / `fraccion_sin_rotar` (fractions only depend on θ) for T4 to mirror in JS.
  - Appearance: `EstiloSello(TextStampStyle)` with a `giro` field; for θ ≠ 0 `create_stamp` lays out the upright box and returns `_AparienciaGirada`, the same form XObject plus a /Matrix (`_matriz_giro`, counterclockwise by θ, moved back to the origin). One style per θ per signing call. Content stamps (`_agregar_sellos`, "todas") reuse the field appearance; a page whose θ differs from the field page's gets a wrapper form XObject (`_apariencia_girada`, `/Sello Do` + /Matrix) cached per θ difference: at most 4 appearances per document.
  - Call sites: `firmar_pdf` (field box and `firmador_pdf(campo, giro)`), `_agregar_sellos`, `_firmar_paginas` (also used by `_firmar_por_partes`). `app.py` needed no change (it already sends `vistas` and `rotaciones`).
  - New tests `tests/test_sello_orientacion.py` (65): θ per class, fraction helpers both ways, rects in unrotated space, θ = 0 unchanged, pypdfium2 renders (ink box in the expected displayed rect, horizontal vs vertical, red logo left of / below the text so a wrong turn fails) for cada_pagina, partes, todas (field on θ 90, 0 and 270 pages), ultima, hand-placed positions on A4 /Rotate 270 and A3 landscape /Rotate 0, every signature `intacta`, second "todas" signature keeps the first valid (ANNOTATIONS). A mutant that flips the 90° matrix fails 6 tests.
  - Changed by design (the landscape-displayed Letter page and rotated pages now take the frame layout; only expected rects/sizes changed): `tests/test_firma_pdf.py` (`_sellos_de` also accepts the rotation wrapper; 8 tests pass `rotacion=` or expect the turned 40 x 160 footprint; `test_pagina_rotada_usa_coordenadas_sin_rotar` became `test_pagina_rotada_sigue_su_orientacion_mostrada`), `tests/test_firma_por_partes.py` (`test_tamanos_mezclados_cada_pagina_solo_su_sello_y_dentro_de_su_vista`, `test_cajas_heredadas_y_origen_distinto_de_cero_se_conservan_en_las_partes`: pass each page's rotation), `tests/test_firma_por_pagina.py` (`test_cada_pagina_pone_una_firma_real_en_cada_pagina`, `test_segundo_firmante_cada_pagina_mantiene_validas_todas_las_anteriores`: `rotacion=0`), `tests/test_app.py` (`test_firmar_usa_la_posicion_propia_de_cada_pagina`, `test_posiciones_por_pagina_mal_formadas_se_ignoran`: new literal rects for the θ = 90 page).
  - README "Páginas rotadas" rewritten for the new rule.
  - RED: 64 failed, 1 passed (no `giro_de_sello` / `rect_sello(rotacion=)`). GREEN: `python -m pytest -q` 473 passed.
  - Next: T4 must mirror `giro_de_sello`, the frame mapping and the turned preview in `ui/app.js`; until then the UI preview of landscape-displayed or rotated pages does not match the signed result, and hand-placed positions on θ ≠ 0 pages are sent in unrotated fractions.
  - Commit `4016fa8`. Parent spot check on the field PDF: page 375 (A4, /Rotate 270) displays the stamp at (400–560, 30–70), the same as page 1; pages 134 (A3, /Rotate 90) and 279 (A3, landscape box) display it at (1109–1149, 566–726), the same as the approved page 262. Parent re-run: 473 passed.
- 2026-11-20 T4 done (Route: delegated writer (writer trigger: ui/app.js + parity test, mirrors firma_pdf geometry)).
  - New `ui/geometria.js` (ES module, no DOM): `TAMANOS_SELLO`, `POSICIONES_PREDETERMINADAS`, `giroDeSello`, `fraccionEnMarco`, `fraccionSinRotar`, `tamanoMarco`, `rectSello` (mirror of `rect_sello(..., rotacion)`), `posicionDeRect` (inverse: unrotated rect -> frame fraction), `giroEnPantalla` (clockwise content turn on screen = /Rotate − θ: 0 on portrait-displayed pages, 270 on landscape-displayed ones), `rectEnPixeles` / `rectDesdePixeles` (pdf.js viewport conversions). `app.js` imports it (`rectSello as rectSelloPdf`, since app.js has its own DOM `rectSello`); `index.html` needed no change (module import).
  - `app.js`: `rectPdf` removed; `TIPOS` sizes and defaults come from geometria; `estado.rotacionPdf` set in `renderizar` from `rotacionDePagina(n, vp)` (Python's `rotaciones` of the document, pdf.js `vp.rotation` as fallback); `cajaAPixeles` and the thumbnail marks lay the stamp out in each page's frame; `pixelesACaja` stores frame fractions (so `confirmarCaja`, «Aplicar a todas las páginas» and «Aplicar a todos los documentos» store what the signer reads); `ajustarTextoSello` turns the content by `giroEnPantalla`; `posicionAjustadaAlTipo` uses the frame width. Payload shape (`caja`, `posiciones_paginas`, `ajustesParaPython`) unchanged; only the meaning is frame fractions.
  - New `tests/test_ui_paridad.py` (12): Node runs geometria on 11 page classes (A4 r0, A4 landscape r270, A3 r270, A3 r90, A3 landscape r0, Letter landscape r0, CropBox with nonzero origin in r0/90/180/270 and a landscape one in r90; all four θ) × both types × 6 positions (default, custom, corners): rects equal `rect_sello` within 0.01 pt, sizes and defaults equal, fraction helpers equal and round-trip, stored positions reproduce the rect in Python. A second half loads a real PDF with the vendored pdf.js in Node (with a `Uint8Array.prototype.toHex` polyfill): pdf.js view/rotate equal Python's, preview pixel boxes are horizontal or vertical as displayed and inside the page, the default sits at the bottom or the right margin, and a simulated drag stores a fraction that Python signs exactly at the dropped box. Two static checks keep app.js wired to geometria. Mutants (90° inverse mapping swapped; θ = r always) fail 2 and 7 tests.
  - README "Páginas rotadas": the preview shows the stamp as it will be signed and dragging stores frame positions.
  - RED: `python -m pytest -q tests/test_ui_paridad.py` 10 errors (FileNotFoundError: `ui/geometria.js`). GREEN: same command 12 passed; `python -m pytest -q` 485 passed; `node --check` on `.mjs` copies of `ui/app.js` and `ui/geometria.js` OK.
  - Known residual differences: Python rounds an odd multiple of 45° /Rotate with banker's rounding and JS with `Math.round` (invalid /Rotate values only); the preview's font fitting remains an approximation of pyHanko's text layout (pre-existing).
  - Commit `dcaa2f0`. Parent re-run: 485 passed; `firmador.spec` bundles the whole `ui` folder, so `ui/geometria.js` ships in the installer. Not run: a visual check in the real app window (pending, by the user).
