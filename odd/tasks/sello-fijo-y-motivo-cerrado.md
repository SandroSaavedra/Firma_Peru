# Feature: sello-fijo-y-motivo-cerrado

## Objective
The «Firma» stamp always measures 160 x 40 pt and cannot be resized; the logo can be changed but not removed; the reason (motivo) is chosen from a closed drop-down list.

## Problem / Why
User request (2026-11-18): the stamp must have a fixed, standard size and a controlled set of reasons, and every «Firma» stamp must carry a logo.

## Scope (authorized)
- «Firma»: fixed 160 x 40 pt on every page size (absolute points, not relative to A4). The stamp can still be moved.
- «Visto bueno»: keeps 140 x 110 pt, also fixed (no resizing); reason stays «Doy V° B°».
- No resizing in the UI: no corner handles, no Alt+arrow resize, help text updated.
- Remove the «Quitar logo» option (button, API and JS). If no logo is saved, the initial logo is restored. «Quitar imagen» of «Visto bueno» stays.
- Reason: drop-down list. «Firma»: «Soy el autor del documento» (default) and «En señal de conformidad». «Visto bueno»: «Doy V° B°» only. The UI boundary (app.py) rejects or normalizes any other value; the `firma_pdf` library and CLI stay flexible.
- Tests updated or added.

## Constraints
- UI preview must match the PDF geometry (app.js mirrors firma_pdf.geometria_sello).
- Saved preferences (localStorage `caja`) with old sizes must not resize the stamp: only the position is reused.
- Existing signing behavior (multi-page widgets, V°B° over signed PDFs) must keep working.

## Tasks
- [x] T1 Fixed stamp size, no resizing, no «Quitar logo», closed reason list + tests (route: delegated writer, trigger: 2+ non-trivial files: firma_pdf.py, app.py, ui/app.js, ui/index.html, tests).

## Acceptance criteria
- A «Firma» signature on A4 and on a non-A4 page produces a widget rect of 160 x 40 pt.
- The UI shows no resize handles; the size label reads «160 × 40 pt».
- No «Quitar logo» control exists; `quitar_logo` API is gone.
- Reason is a `<select>`; app.py only accepts the listed reasons per type.

## Checks
- `python -m pytest -q`
- `node --check ui/app.js`

## Progress
- Prior uncommitted work on the branch (logo margin trim, 210 x 60 default, size label) is superseded in size by this feature.
- Commits (user authorized committing, 2026-11-18): logo margin trim in `2e1faf1`; T1 (with the size label and this document) in the `feat: fix stamp size...` commit right after it on `feat/instalador-sin-admin`.

### T1 (done, uncommitted)
- Design: `firma_pdf.TAMANOS_SELLO` (pt per type) + `PosicionRelativa(x, y)` (bottom-left corner as page-view fractions, replaces `CajaRelativa`) + `rect_sello(posicion, vista, tipo)` (replaces `caja_absoluta`; clamps the fixed-size stamp inside the page, rejects x/y outside 0..1 or NaN). `POSICIONES_PREDETERMINADAS` / `posicion_predeterminada(tipo)` (right edge 560/595, bottom 30/842 in A4) replace `CAJAS_POR_TIPO` / `CAJA_PREDETERMINADA` / `caja_predeterminada`. `OpcionesFirma.posicion` (None = type default) replaces `OpcionesFirma.caja`. Multi-page widgets now all reuse the field's single appearance XObject (same size on every page).
- `MOTIVOS_POR_TIPO` in firma_pdf (first = default; `MOTIVOS_PREDETERMINADOS` derived). app.py `obtener_config` returns `motivos` as `{tipo: [list]}`; `motivo_permitido` makes `_opciones_firma` accept only listed reasons (else type default). `_opciones_firma` uses only `caja.x/y` (ignores `ancho/alto`). The UI JSON key `caja` and localStorage key `caja` are kept for compatibility.
- `Api.quitar_logo` removed; `preparar_imagenes_iniciales` re-copies the initial logo whenever no logo file is saved (V°B° image keeps the copy-once rule).
- UI: handles, `redimensionar`, Alt+arrows, `MIN_ANCHO/MIN_ALTO` and `btn-quitar-logo` removed; `<select id="sel-motivo">` replaces `txt-motivo`; size label shows the type's fixed size; legacy saved boxes `{x, y, ancho, alto}` keep their bottom-right corner (A4 reference) and drop the size; type switch keeps the bottom-right corner (current page width or A4).
- RED: `python -m pytest -q`: 32 failed, 119 passed (new/updated tests: test_tamanos_fijos_de_cada_tipo, test_sello_mide_lo_mismo_en_cualquier_pagina[8], test_posicion_*, test_sello_que_no_cabe_se_desplaza_dentro_de_la_pagina, test_sello_tiene_tamano_fijo_en_paginas_de_distinto_tamano[2], test_motivos_por_tipo_empiezan_por_el_predeterminado, test_config_predeterminada, test_el_logo_se_puede_cambiar_pero_no_quitar, test_logo_quitado_antes_se_restaura_al_iniciar, test_logo_quitado_vuelve_con_el_logo_inicial_actual, test_firmar_ignora_el_tamano_que_envia_la_interfaz, test_firmar_solo_acepta_motivos_de_la_lista[3 unlisted cases], test_todas_sella_cada_pagina_y_una_sola_firma_pades, test_campo_en_otra_pagina_usa_su_vista, test_pagina_rotada_usa_coordenadas_sin_rotar, test_ajuste_por_documento_solo_se_aplica_a_ese).
- GREEN: `python -m pytest -q`: 151 passed. `node --check ui/app.js`: OK. Every `$("id")` in app.js exists in index.html (script check).
- Existing tests changed: test_firma_pdf.py (caja tests rewritten to `rect_sello`; multi-page test now expects one shared appearance and 160 x 40 on A4 and Letter; `test_campo_en_otra_pagina_usa_su_tamano` renamed `..._usa_su_vista`); test_app.py (`test_config_predeterminada` expects reason lists; `quitar_logo` steps removed from `test_logo_y_tipo_persisten_en_appdata` and `test_logo_inicial_se_copia_una_sola_vez`; `test_logo_quitado_no_vuelve_tras_actualizar` became `test_logo_quitado_vuelve_con_el_logo_inicial_actual`); test_lote.py (expected rect uses fixed 160 x 40); test_firma1.py (renamed `test_cli_firma_con_posicion_predeterminada`, asserts rect (400, 30, 560, 70)).
- Files: firma_pdf.py, app.py, firma1.py, ui/app.js, ui/index.html, ui/estilos.css, README.md, tests/test_firma_pdf.py, tests/test_app.py, tests/test_estilos_sello.py, tests/test_lote.py, tests/test_firma1.py.

## Next step
- User decision on commits; manual UI check in the app (drag, arrows, size label, reason drop-down, type switch).
