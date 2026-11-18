# Feature: posicion-independiente-por-documento

## Objective
Moving the stamp changes the position of the open document only. A button «Aplicar esta posición a todos» copies it to every document in the list when the user wants the same position in a batch.

## Problem / Why
Today moving the stamp changes the common position of every document unless the user ticks «Posición propia para este documento» (`ui/index.html:240`, unchecked by default). The user (2026-11-18) wants each document positioned independently, and nothing but the location may change.

## Scope (authorized)
- Drag and arrow-key moves write the open document's own position (`estado.ajustes[id].caja`). Other documents keep theirs.
- Documents never moved use the common position (`estado.caja`: saved preference or the type default).
- New button «Aplicar esta posición a todos»: the current document's position becomes the common position, is saved as the preference (localStorage `caja`), and replaces the own position of every document in the list. Per-document signature page («Página actual», `ajustes[id].pagina`) is not touched.
- Remove the «Posición propia para este documento» checkbox and its wiring; keep the «posición propia» marker in the list rows.
- Help texts updated. README updated if it describes the old behavior.

## Constraints
- Only the location changes: stamp size, logo, reason, text, signature type and signing logic stay as they are.
- The backend already accepts per-document positions (`ajustes` {id: {caja, pagina_firma}}); the single-document and batch signing paths must both use the open document's own position.
- Switching stamp type keeps adjusting both the common and the own positions (`posicionAjustadaAlTipo`).

## Tasks
- [x] T1 Independent per-document position + «Aplicar esta posición a todos» + tests (route: delegated writer, trigger: preparation reading of ui/app.js + 2+ files: ui/app.js, ui/index.html, tests, README). Commit: pending (parent commits).

- [x] T2 Per-page position inside a document: moving the stamp on page N changes only page N (other pages keep the document position); buttons «Aplicar a todas las páginas» (this document) and «Aplicar a todos los documentos» (renamed from «Aplicar esta posición a todos»); moved pages marked in the thumbnails. Backend signs each page's widget at its own rect within the single signature (route: delegated writer, trigger: preparation reading + 2+ non-trivial files: firma_pdf.py, app.py, ui/app.js, ui/index.html, tests). User decision 2026-11-18. Commit: pending (parent commits).

## Acceptance criteria
- Moving the stamp in document A does not move it in document B.
- «Aplicar esta posición a todos» puts every document at A's position.
- Signing a batch places each document's stamp at its own position (160 x 40 pt unchanged).
- (T2) Moving the stamp on page N of a document changes only page N; «Aplicar a todas las páginas» makes it the document position; «Aplicar a todos los documentos» (renamed from «Aplicar esta posición a todos») makes it the common position and clears every own and per-page position. Still one embedded signature; each page's widget sits at its own rect.

## Checks
- `python -m pytest -q`
- `node --check ui/app.js`

## Progress
- Created 2026-11-18. User chose the «Aplicar a todos» button over the checkbox.
- T1 done 2026-11-18 (delegated writer):
  - `ui/app.js`: `confirmarCaja` writes only `ajusteEditable().caja` (never `estado.caja` nor the `caja` preference); new `aplicarPosicionATodos` (open document's `cajaVisible()` becomes `estado.caja`, saved with `guardarPreferencia("caja", …)`, every document's own `caja` removed, `pagina` kept, empty ajustes deleted, list/stamp refreshed, `aviso` feedback); button disabled in `actualizarBotonFirmar` while signing or with no open document; checkbox wiring removed; help text updated.
  - `ui/index.html`: checkbox `chk-posicion-propia` and its label removed; button `btn-posicion-todos` («Aplicar esta posición a todos», `btn btn-secundario btn-chico`) added in the same place.
  - `ui/estilos.css`: `.interruptor-propio` replaced by `.btn-posicion-todos { margin-top: 10px; }`.
  - `README.md`: batch section describes per-document position and the button.
  - `tests/test_interfaz.py`: 3 new static tests.
  - Backend unchanged: single-document `firmar` already sends `caja: cajaVisible()` (own position) and batch uses `ajuste.get("caja") or opciones.get("caja")` (`app.py:1074`).
- RED: `python -m pytest -q tests/test_interfaz.py`: 3 failed, 3 passed (new tests failed before implementation).
- GREEN: `python -m pytest -q`: 154 passed. `node --check ui/app.js`: OK.
- JS behavior exception: no JS test runner in the project. Verified by readback plus a throwaway Node `vm` script (scratchpad, not committed) that loads `ajusteActual`, `ajusteEditable`, `cajaVisible`, `ajustesParaPython`, `confirmarCaja`, `aplicarPosicionATodos` with DOM stubs: moving A leaves `estado.caja`, the preference and B untouched; B shows the common position; applying to all sets common = A, saves it, keeps B's `pagina`; no-op while signing. Result: OK.
- Open point for the user: with a single document, moving the stamp no longer updates the remembered position for the next session; only «Aplicar esta posición a todos» does (as the scope requires).

- T2 done 2026-11-18 (delegated writer):
  - Design: per-page positions are keyed by 0-based page index. Python: `OpcionesFirma.posiciones_paginas: dict[int, PosicionRelativa] | None`; `firmar_pdf` validates them once the page count is known (`_validar_posiciones_paginas`: int index in range, `PosicionRelativa` with finite x/y in 0..1, else `ErrorFirma`) and resolves each page with `posicion_de(indice)` (own position or the document position). The field widget (`caja_campo`, any mode: "todas", "ultima", "solo_campo") and every extra widget in `_agregar_widgets` use it. The appearance XObject is still shared by every widget (the /BBox is local and the size is fixed per type); only /Rect differs, so no per-page re-rendering.
  - `app.py`: new `posiciones_por_pagina(valor, total_paginas)` parses the UI payload `{"índice": {x, y}}` defensively (non-dict payload, non-integer keys, negative or out-of-document indices, missing/non-numeric/non-finite or out-of-0..1 x/y are dropped; those pages use the document position). `_opciones_firma` takes `posiciones_paginas` and `total_paginas`; single-document `firmar` passes `opciones["posiciones_paginas"]` and the queue item's page count; batch passes `ajustes[id]["posiciones_paginas"]` and `item["paginas"]`.
  - `ui/app.js`: per-page positions in `estado.ajustes[id].cajasPaginas = {índice0: {x, y}}` (not persisted). `cajaDocumento()` (own or common), `cajaDePagina(n)` (per-page ?? document), `cajaVisible()` = current page. `confirmarCaja` writes only `cajasPaginas[estado.pagina - 1]` and refreshes only that thumbnail. New `aplicarATodasLasPaginas` (current page position -> `a.caja`, clears `cajasPaginas`). `aplicarPosicionATodos` (button renamed «Aplicar a todos los documentos») also clears `cajasPaginas` of every document. Both buttons disabled in `actualizarBotonFirmar`. `posicionesPaginasParaPython` feeds both `ajustesParaPython` (batch) and the single `firmar` options (`caja: cajaDocumento()`, `posiciones_paginas`). `cajaDelTipo` also adjusts per-page positions. Thumbnail stamp marks use each page's position; pages with an own position get class `posicion-propia` (a small dot after the page number, plus title/aria-label). List marker «posición propia» shows for own or per-page positions. Help texts updated.
  - `ui/index.html`: new button `btn-posicion-paginas` «Aplicar a todas las páginas»; `btn-posicion-todos` renamed «Aplicar a todos los documentos»; both in `.botones-posicion`. `ui/estilos.css`: `.botones-posicion` flex row (replaces `.btn-posicion-todos`), `.miniatura.posicion-propia .miniatura-num::after` dot.
  - `README.md`: single-document and batch sections describe per-page positions and both buttons.
- T2 RED: `python -m pytest -q`: 18 failed, 154 passed (new firma_pdf, app, batch and static UI tests failed before implementation; `test_posiciones_por_pagina_mal_formadas_se_ignoran` already passed because the old backend ignored the key, kept as a regression guard).
- T2 GREEN: `python -m pytest -q`: 172 passed. `node --check ui/app.js`: OK.
- T2 existing test changed: `tests/test_interfaz.py::test_boton_aplicar_posicion_a_todos_existe_y_esta_conectado` renamed to `test_boton_aplicar_a_todos_los_documentos_existe_y_esta_conectado` and now expects the text «Aplicar a todos los documentos» (the button was renamed by the user decision).
- T2 JS behavior exception: no JS test runner. Verified by readback plus a throwaway Node `vm` script (scratchpad `comprobar_paginas.js`, not committed) with DOM stubs: moving on page 2 changes only page 2 (common position and preference untouched, only that thumbnail refreshed and marked); page 1 shows the document position; list marker shows; Python payloads for batch and single sign; stamp-type change adjusts per-page positions; «Aplicar a todas las páginas» sets the document position and clears per-page ones; «Aplicar a todos los documentos» sets/saves the common position from the current page and clears own and per-page positions of every document while keeping `pagina`; both no-ops while signing. Result: OK.

## Next step
- Parent commits T2; then native review per RDD.
