# Feature: sello-clicable-por-pagina

## Objective
With `paginas="todas"`, the stamp on every page is part of the signature: a clickable `/Sig` widget, not page content. Clicking it in Acrobat opens the signature validation status, as with ReFirma.

## Problem / Why
Today `firma_pdf.py` draws the stamp with `TextStamp` into the page content of every page except `pagina_firma`, and only `pagina_firma` gets the `/Sig` widget. Evidence: in `EXP TEC NE-JUN-15 (1)_firmado.pdf` (597 pages) only page 597 has a widget. On the other pages Acrobat shows a text cursor and clicking does nothing.

## Scope (authorized)
- One signature field (a single PAdES signature) with one widget annotation per stamped page (`/Kids`), sharing the appearance stream for pages of the same size.
- No stamp drawn into page content any more for the `todas` mode.
- Tests updated or added.

## Constraints
- Still exactly ONE embedded signature and ONE ByteRange. Integrity and later signatures (`solo_campo` mode, V°B°) must keep validating without "modified after signing" warnings in pyHanko diff analysis.
- Widgets must be read-only and printable (`/F 4`, field `/Ff` read-only where applicable).
- Performance: must work on large documents (about 600 pages).
- No DocMDP certification. It would block later signatures.

## Tasks
- [x] T1 Multi-widget signature field for `paginas="todas"` + tests (route: delegated writer, trigger: preparation reading + pyHanko internals research + 2 files).

## Acceptance criteria
- Every stamped page has a `/Widget` annotation whose field is the signed `/Sig` field.
- `validate_pdf_signature` reports intact + valid; difference analysis reports no unauthorized modifications.
- A second signature on the result keeps the first one valid.
- Acrobat: clicking the stamp on any page opens "La firma es VÁLIDA" (manual user check).

## Checks
- `python -m pytest -q`

## Progress
- Branch `feat/sello-clicable-por-pagina` created.
- T1 done (uncommitted; commit is the parent's decision).
  - Approach: pyHanko creates the field with `combine_annotation=False` (field + one widget on `pagina_firma`, so `get_single_field_annot` in `cms_embedder.py:464` still finds exactly one kid and draws the appearance). `firma_pdf._firmar_con_paso_previo` mirrors `PdfSigner.async_sign_pdf` (`pdf_signer.py:1477`) via the staged signing API and, between `prepare_tbs_document` and `digest_tbs_document`, `_agregar_widgets` appends one widget per other page to `/Kids` (same revision, covered by the ByteRange). Same-size pages reuse the signature widget's `/AP /N`; other sizes get one XObject per size rendered with `session.system_time`, so the date matches. No stamp is drawn into page content any more.
  - RED: 3 failing tests before implementation (`test_todas_sella_cada_pagina_y_una_sola_firma_pades`, `test_todas_segunda_firma_mantiene_valida_la_primera`, `test_campo_en_otra_pagina_usa_su_tamano`: no widget on non-signature pages).
  - GREEN: `python -m pytest -q`: 109 passed.
  - Manual check (600 pages, 2 sizes): signing 0.68 s; 600 kids in the field; 3 distinct appearance XObjects; after a V°B° signature, `validar_pdf`: first signature intacta/valida, FORM_FILLING, permitidas; second intacta/valida, NONE. Date text identical on widgets of different page sizes (checked with Helvetica).
  - Pending: Acrobat click check by the user.
