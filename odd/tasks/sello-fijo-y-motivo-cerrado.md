# Feature: sello-fijo-y-motivo-cerrado

## Objective
The «Firma» stamp always measures 160 x 40 pt and cannot be resized; the logo is always the bundled initial logo and cannot be changed or removed (T2 supersedes T1's «can be changed but not removed»); the reason (motivo) is chosen from a closed drop-down list.

## Problem / Why
User request (2026-11-18): the stamp must have a fixed, standard size and a controlled set of reasons, and every «Firma» stamp must carry a logo.

## Scope (authorized)
- «Firma»: fixed 160 x 40 pt on every page size (absolute points, not relative to A4). The stamp can still be moved.
- «Visto bueno»: keeps 140 x 110 pt, also fixed (no resizing); reason stays «Doy V° B°».
- No resizing in the UI: no corner handles, no Alt+arrow resize, help text updated.
- Remove the «Quitar logo» option (button, API and JS). If no logo is saved, the initial logo is restored. «Quitar imagen» of «Visto bueno» stays.
- Reason: drop-down list. «Firma»: «Soy el autor del documento» (default) and «En señal de conformidad». «Visto bueno»: «Doy V° B°» only. The UI boundary (app.py) rejects or normalizes any other value; the `firma_pdf` library and CLI stay flexible.
- Tests updated or added.
- T2 (user decision, 2026-11-18): the «Firma» logo cannot be changed either. Remove «Cambiar logo…» (button, help text, `elegir_logo` API); the panel keeps a read-only logo thumbnail. Signing (single and batch) and the preview always use `src/Emblema peruano con firma digital.png` next to the program; a custom `logo.png` saved by older versions is ignored. Missing/invalid bundled logo: sign without logo and log a warning. CLI `--logo` stays flexible.

## Constraints
- UI preview must match the PDF geometry (app.js mirrors firma_pdf.geometria_sello).
- Saved preferences (localStorage `caja`) with old sizes must not resize the stamp: only the position is reused.
- Existing signing behavior (multi-page widgets, V°B° over signed PDFs) must keep working.

## Tasks
- [x] T1 Fixed stamp size, no resizing, no «Quitar logo», closed reason list + tests (route: delegated writer, trigger: 2+ non-trivial files: firma_pdf.py, app.py, ui/app.js, ui/index.html, tests).

- [x] T2 Logo cannot be changed: always the bundled initial logo (`src/Emblema peruano con firma digital.png`); remove «Cambiar logo…» and its API; a custom logo saved by older versions is ignored (route: delegated writer, trigger: 2+ non-trivial files: app.py, ui/app.js, ui/index.html, tests, README).
- [x] T3 Ship the logo inside the executable/installer: `firmador.spec` datas includes `src/Emblema peruano con firma digital.png`; `ruta_logo_inicial()` resolves it from `ruta_recursos()` (_MEIPASS when frozen). RED: 2 failing tests (`test_logo_inicial_se_busca_en_los_recursos_empaquetados`, `test_el_ejecutable_empaqueta_el_logo_inicial`); GREEN: `python -m pytest -q` 151 passed; `build.ps1` exit 0 and the logo is at `dist/FirmaPeru/_internal/src/` (route: inline, 1 behavior file + spec + test + README).

## Acceptance criteria
- A «Firma» signature on A4 and on a non-A4 page produces a widget rect of 160 x 40 pt.
- The UI shows no resize handles; the size label reads «160 × 40 pt».
- No «Quitar logo» control exists; `quitar_logo` API is gone.
- Reason is a `<select>`; app.py only accepts the listed reasons per type.
- No «Elegir/Cambiar logo…» control and no `elegir_logo` API; signing and `obtener_config` use the bundled logo even if an older custom `logo.png` exists; missing bundled logo signs without logo plus a warning.

## Checks
- `python -m pytest -q`
- `node --check ui/app.js`
- Every `$("id")` in app.js exists in index.html (now `tests/test_interfaz.py`).

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

### T2 (done, commit pending: parent commits)
- Design: `app.preparar_logo()` resolves `ruta_logo_inicial()` (= `carpeta_aplicacion() / LOGO_INICIAL`) and writes its reduced copy (`imagen_reducida`, max 800 px) to the config folder as `NOMBRE_LOGO` (`logo.png`), overwriting any older custom logo; returns that path, the original if the copy cannot be written (OSError), or None plus a `logger.warning` if the file is missing or not an image. Used by `obtener_config` (preview via `_imagen_para_interfaz(..., "logo")`, margins trimmed as before) and `_opciones_firma` (single and batch paths). The config key `logo` / `logo_inicial_aplicado` is no longer read (dropped on the next config save).
- Removed: `Api.elegir_logo`, `LOGO_INICIAL_ANTERIOR`, `actualizar_logo_inicial_anterior`, `es_copia_de`, `ruta_logo_guardado`, `copiar_logo`, the `logo` entry of `IMAGENES`. Visto bueno copy machinery (`IMAGENES["imagen_vb"]`, `preparar_imagenes_iniciales`, `_elegir_imagen`, `_quitar_imagen`) unchanged. `firma1.py` unchanged (`--logo` stays optional/free).
- UI: `btn-logo` button and its help text removed from index.html; read-only thumbnail (`logo-vista`/`logo-img`/`logo-vacio`) and stamp preview (`sello-logo`) kept. app.js `SELECTORES.logo` is `{ prefijo, sello, fija: true }`; `mostrarImagen` and the click wiring skip the button for `fija` selectors; the Visto bueno picker is unchanged. «Firma» help text now «Logo a la izquierda…».
- RED: `python -m pytest -q`: 4 failed, 143 passed (test_el_logo_no_se_puede_cambiar_ni_quitar, test_logo_elegido_en_version_anterior_se_ignora, test_sin_logo_inicial_se_firma_sin_logo_aunque_haya_uno_guardado, test_lote_usa_el_logo_inicial_aunque_haya_uno_guardado). `python -m pytest -q tests/test_interfaz.py`: 1 failed, 2 passed (test_el_logo_se_muestra_pero_no_se_puede_cambiar).
- GREEN: `python -m pytest -q`: 150 passed. `node --check ui/app.js`: OK. Script check: 83 `$("id")` used in app.js, none missing in index.html.
- Existing tests changed (test_app.py): `test_logo_y_tipo_persisten_en_appdata` -> `test_tipo_persiste_y_el_logo_es_el_inicial`; `test_el_logo_se_puede_cambiar_pero_no_quitar` -> `test_el_logo_no_se_puede_cambiar_ni_quitar`; `test_logo_grande_se_reduce` -> `test_logo_inicial_grande_se_reduce_al_firmar`; `test_logo_que_no_es_imagen_se_rechaza` -> `test_logo_inicial_que_no_es_imagen_se_firma_sin_logo`; `test_logo_inicial_se_copia_una_sola_vez` -> `test_config_muestra_el_logo_inicial_reducido`; `test_logo_quitado_antes_se_restaura_al_iniciar` -> `test_logo_quitado_en_version_anterior_vuelve_a_ser_el_inicial`; `test_logo_elegido_por_el_usuario_no_se_reemplaza` inverted -> `test_logo_elegido_en_version_anterior_se_ignora`; `test_firmar_usa_tipo_motivo_y_logo_de_la_config` -> `test_firmar_usa_tipo_motivo_y_logo_inicial`; `test_logo_inicial_se_busca_junto_al_ejecutable` uses the emblem path; `test_logo_inicial_predeterminado_es_el_emblema_peruano` drops the `LOGO_INICIAL_ANTERIOR` assertion. Deleted (behavior removed): `test_elegir_logo_cancelado`, `logos_iniciales` fixture, `test_configuracion_nueva_copia_el_logo_inicial_actual`, `test_logo_inicial_anterior_sin_cambios_se_reemplaza`, `test_logo_quitado_vuelve_con_el_logo_inicial_actual`, `test_sin_logo_anterior_junto_al_programa_no_se_toca`, `test_actualizar_logo_inicial_es_idempotente`. conftest.py: dropped the `LOGO_INICIAL_ANTERIOR` patch. New: test_lote.py `test_lote_usa_el_logo_inicial_aunque_haya_uno_guardado`; tests/test_interfaz.py (3 static UI tests).
- Files: app.py, ui/app.js, ui/index.html, README.md, tests/test_app.py, tests/conftest.py, tests/test_lote.py, tests/test_interfaz.py (new), this document.

## Next step
- Parent commits T2; manual UI check (logo thumbnail read-only, no logo button, Visto bueno picker) in the app (drag, arrows, size label, reason drop-down, type switch).
