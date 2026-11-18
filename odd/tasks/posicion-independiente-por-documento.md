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

## Acceptance criteria
- Moving the stamp in document A does not move it in document B.
- «Aplicar esta posición a todos» puts every document at A's position.
- Signing a batch places each document's stamp at its own position (160 x 40 pt unchanged).

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

## Next step
- Parent commits T1; then native review per RDD.
