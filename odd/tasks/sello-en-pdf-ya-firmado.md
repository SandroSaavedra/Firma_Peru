# Feature: sello-en-pdf-ya-firmado

## Objective
A second (or later) signer of an already-signed PDF gets their stamp exactly like the first signer: on every page with "todas" (each page at its own position) and drawn normally (not translucent) in the panel, while every earlier signature stays valid.

## Problem / Why
`firma_pdf.py` (module docstring lines 25-29, `modo = "solo_campo" if firmas_previas`) stamps only the signature-field page (last page by default) when the PDF already has signatures. The UI then shows the stamp translucent and dashed with «Sin sello en esta página» on every other page (`ui/app.js` ~1146-1148, `paginasConSello` ~983). On 2026-11-18 a second professional signed a document the user had signed; the stamp did not appear where they looked and the panel looked "invalid". The user wants later signers to stamp normally.

## Gate (high-consequence premise)
The premise to prove BEFORE changing behavior: adding a new signature field with one widget per page (as the first signature does) in an incremental revision keeps every earlier signature intact and valid, and pyHanko's difference analysis classifies the revision as an allowed modification (FORM_FILLING or lower, no unauthorized modifications). If the premise fails, do not change behavior; report the evidence instead.

## Scope (authorized, only if the gate passes)
- Signed PDFs (no DocMDP no-changes certification) get the same stamping modes as unsigned ones: "todas" puts a widget on every page at the per-page/document position; the field page keeps its own position.
- UI: stamp opaque on stamped pages for signed PDFs; translucency stays only where truly no stamp will be drawn.
- DocMDP level 1 (no changes) keeps being rejected. Level 2/3 certification: keep current behavior unless the gate proves widgets on other pages are allowed there too.
- Messages and README updated.

## Tasks
- [x] T1 Prove the premise, then enable full stamping on already-signed PDFs + UI opacity + tests (route: delegated writer, trigger: high-consequence assumption + 2+ non-trivial files: firma_pdf.py, ui/app.js, tests).

## Acceptance criteria
- After a second "todas" signature: two embedded signatures; the first one intact/valid with only allowed modifications; the second intact/valid; one widget per page for each signature.
- The panel shows the second signer's stamp opaque on every stamped page.
- Manual check by the user in Acrobat: both signatures «válidas».

## Checks
- `python -m pytest -q`
- `node --check ui/app.js`

## Progress
- Created 2026-11-18.
- T1 done (uncommitted; the parent commits).
  - Gate (scratch pytest `test_gate.py` with the repo fixtures, `firma_pdf` patched to take the multi-widget path when signed; `validate_pdf_signature` with the test ValidationContext + `validacion.validar_pdf`):
    - todas + todas: sig 1 intact/valid, ENTIRE_REVISION, FORM_FILLING, docmdp_ok=True (validar_pdf: «permitidas», «valida»); sig 2 intact/valid, ENTIRE_FILE, NONE; both fields have 3 kids.
    - per-page positions (signer 1 {1: own}, signer 2 own document position + {0, 2: own}) and a third V°B° signer: every earlier signature FORM_FILLING, docmdp_ok=True, valid; last one NONE.
    - V°B° as second signer: same as todas + todas.
    - DocMDP level 2 (FILL_FORMS) and 3 (ANNOTATE) certified first signature: with pyHanko's DEFAULT_DIFF_POLICY the certification becomes OTHER, docmdp_ok=False («no_permitidas», «invalida») — but the CURRENT field-only path gives exactly the same result (pre-existing): `SigFieldCreationRule(allow_new_visible_after_certify=False)` rejects any new visible signature field after a certification ("Update of ... is only allowed after an approval signature, not a certification signature."). With `allow_new_visible_after_certify=True` both field-only and multi-widget give FORM_FILLING/docmdp_ok=True. Multi-widget on certified documents is therefore unproven (not worse, not better) → kept field-only for certified documents.
    - Level 1 (NO_CHANGES): still rejected.
  - Design: `firma_pdf.firmar_pdf` decides `modo` by certification, not by previous signatures: `"solo_campo"` only when the document has DocMDP 2/3, otherwise `"contenido"` (same multi-widget path, per-page positions, shared appearance XObject). `ResultadoFirma.modo` keeps its two values; `firmas_previas` unchanged. Module docstring documents the proven rule.
  - UI (`ui/app.js`): new `soloCampoDeFirma()` (= `estado.info.docmdp != null`) replaces «has signatures» in `paginasConSello` and `configurarSegunFirmas`: «Todas las páginas» stays enabled for signed PDFs and the stamp is opaque/solid on every stamped page; translucent/dashed only on pages without a stamp («Solo la página de firma» or certified). Notice text for signed PDFs and certified PDFs updated; result dialog: certified → field-only message, otherwise «Sello en N páginas … El PDF ya tenía N firmas.». Batch comment updated (the batch path is decided per document in Python).
  - `firma1.py`: docstring (also the stale «sello como contenido» bullet) and console message. `app.py`: no change needed (passes `modo` through). README section «PDF que ya tienen firmas» rewritten.
  - RED: `python -m pytest -q`: 7 failed, 169 passed (all on `'solo_campo' == 'contenido'` or 1 widget instead of 3 per page).
  - GREEN: `python -m pytest -q`: 176 passed. `node --check ui/app.js`: OK.
  - New tests: `test_tercera_firma_mantiene_validas_las_anteriores`, `test_ultima_en_pdf_ya_firmado_solo_pone_el_campo`, `test_documento_certificado_con_cambios_permitidos_solo_pone_el_campo[FILL_FORMS|ANNOTATE]`; helper `_certificar` (level-1 test now uses it).
  - Existing tests changed to the new decision: `test_todas_segunda_firma_mantiene_valida_la_primera` (V°B° second: now one widget of each signature per page), `test_segunda_firma_no_invalida_la_primera` (now `contenido`, pages [0,1,2], both signatures valid, shared appearance), `test_posicion_propia_en_pdf_ya_firmado_mueve_solo_el_campo` → `test_posiciones_propias_en_pdf_ya_firmado_se_respetan` (both signers with per-page positions), `test_estilos_sello::test_visto_bueno_sobre_pdf_firmado_mantiene_la_primera`, `test_app::test_abrir_firmar_y_validar`, `test_lote::test_lote_con_documento_ya_firmado_conserva_la_primera_firma` (`modo` now `contenido`; first signature still «permitidas»).
  - JS exception: no JS runner in the repo; checked with `node --check` and a throwaway Node `vm` script (scratchpad `comprobar_firmados.js`: signed → todas enabled, «Sello»/opacity 1/solid on every page; «ultima» → other pages 0.45/dashed; DocMDP 2 → field only; unsigned unchanged). It fails on the pre-change app.js (missing `soloCampoDeFirma`) and passes after.
  - Performance (600 pages, 2 sizes, SimpleSigner): first signature 0.85 s, second 0.51 s; `validar_pdf`: FirmaDNIe intacta/permitidas/FORM_FILLING/valida, FirmaDNIe_2 intacta/ninguna/NONE/valida.
  - Pending: Acrobat check by the user (both signatures «válidas», click on any stamp of either signature). Open decision: certified (DocMDP 2/3) documents — pyHanko's default policy already flags any new visible signature there (pre-existing); Acrobat behavior unverified.
