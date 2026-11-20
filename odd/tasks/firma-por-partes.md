# Per-page signing in parts of 75 pages

## Objective

When «Firma real en cada página» is used on a long document, split it into parts of up to 75 pages and sign every page of every part. Then each output PDF holds at most 150 signatures, even with two signers, and Acrobat and ReFirma can still validate it.

## Problem

Field and lab evidence from 2026-11-19:
- A real 597-page expediente with 597 per-page signatures, all intact in pyHanko:
  - Acrobat keeps «cargando», or marks some signatures as «modificado o dañado»;
  - ReFirma does not even list the signatures.
- Synthetic files:

  | Signatures | Acrobat | ReFirma |
  |---|---|---|
  | 50 | all valid | — |
  | 150 | all valid (about 5 min) | lists and verifies all 150 |
  | 300 | revisions 252–299 shown invalid | — |

- 100 real pages signed by 2 signers (200 signatures): ReFirma lists them, but «Verificar» never shows the report.
- The institution requires a digital signature on every page. Most expedientes have about 500 pages, and the user wants as few files as possible. Decision: parts of 75 pages, which is 150 signatures with two signers, the level we saw validate.

## Scope (authorized by the user on 2026-11-19)

- Constant `PAGINAS_POR_PARTE = 75`.
- Mode `cada_pagina` on a document **without existing signatures** and with more than 75 pages:
  - split it into ceil(N/75) parts of consecutive pages before signing;
  - sign every page of every part;
  - use one PIN and one signing session (CNG or PKCS#11) for all parts.
- Output:
  - a folder `<name>_firmado/` next to the chosen destination;
  - inside it, files `<name>_parte01de07_firmado.pdf` and so on, with the part number zero-padded to the width of the total.
  - Documents of 75 pages or fewer: a single file, unchanged.
- Per-page stamp positions keep working: the original page index is mapped to the page index inside its part.
- Progress shows «Parte X de Y · Firmando página A de B».
- Cancel or error leaves nothing: build in a temporary folder and rename it at the end.
- Batches split each long document the same way.
- A document that **already has signatures** and more than 75 pages cannot be split without invalidating them. Refuse `cada_pagina` with a clear message, for example «Este PDF ya tiene firmas y más de 75 hojas: no se puede dividir sin invalidarlas. Pida al primer firmante el expediente dividido en partes.». A signed document of 75 pages or fewer works as today, which is the second signer's case.
- The UI result shows the folder, the number of parts and an «Abrir carpeta» action.
- Splitting uses pypdf (already installed, 6.21.2), with `compress_identical_objects()`. Declare it in `requirements.txt`.

## Constraints

- Incremental PAdES signatures only.
- The split copies the page content faithfully. Outlines and form fields of the source may be lost.
- The PIN is only ever in memory. Never retry after a PIN error.
- No AI attribution. Use Conventional Commits.

## Tasks

- [x] T1 — Splitting plus per-part signing in `firma_pdf.py` and `app.py`, with UI, README, tests, version 1.2.6 and the installer.
  - Route: delegated writer. Trigger: preparation reading and 2+ non-trivial files.
  - RED:
    - `tests/test_firma_por_partes.py` → 15 failed, 2 passed (no `PAGINAS_POR_PARTE`, `rangos_de_partes`, `nombres_de_partes`, `mensaje_sin_division`, `ResultadoFirma.partes`).
    - app/batch tests → 3 failed, 1 passed (`progreso()` got an unexpected keyword argument `parte`; no `paginas_por_parte`/`no_dividir`).
    - UI static tests → 3 failed.
  - GREEN: `python -m pytest -q` → 396 passed (372 before; 24 new). No access violation; only the pre-existing WinSCard `0x8010002e` faulthandler note. `node --check ui/app.js` → OK.
  - Build: `build.ps1` → its test run 396 passed; `dist\installer\FirmaPeru-Setup-1.2.6.exe` built. pypdf is in the PyInstaller PYZ (static import from `firma_pdf`; no spec change needed).
  - Smoke (outside pytest, test signer «PRUEBA PRIMERA», real 75-page limit), source `original_sin_firmar.pdf` (569 pages, 224 MB):
    - 8 parts (1-75, 76-150, 151-225, 226-300, 301-375, 376-450, 451-525, 526-569); total 71 s; split 0.1–1.6 s per part, signing 4.6–10.1 s per part.
    - Sizes (MB): 10.3, 8.3, 8.4, 22.4, 11.2, 8.5, 27.8, 16.8 (113.7 total).
    - pyHanko: parts 1 and 8, first and last signature each → intact, valid, trusted (first: FORM_FILLING changes; last: NONE).
    - No loose PDF next to the destination; only the folder `original_sin_firmar_firmado`.
  - Commits: `5020460` (core split + tests + pypdf), `2849349` (app, UI, README, version 1.2.6), plus the docs commit with this record.

- [x] T2 — Fix per-part stamp regressions found in the field (v1.2.7).
  - Route: delegated writer. Trigger: field evidence sweep (8 parts × 597 pages) plus 2 non-trivial files.
  - Field evidence: real 597-page expediente signed with v1.2.6 and a real DNIe (8 parts; the user later renamed the folder to `…_firmado-bo`). Log: «posiciones por página=3». A sweep compared all 597 pages of the 8 parts with the source.
  - Root cause 1, the logo is missing from parts 2–8:
    - the logo `PdfImage` reuse (one embed per document) cached only the object number, across parts;
    - part 1 stamps → `Image 700x481` (75/75);
    - parts 2, 4, 6, 7 → object 2079 missing;
    - parts 3, 5, 8 → an unrelated object (`/FontDescriptor`, a dict without a type, a `/Widget`).
    - Fix: the cache is tied to the writer's input stream (weak reference); another stream embeds the image again.
  - Root cause 2, a stamp drawn outside the page:
    - the source has indirect EMPTY `/Annots []` arrays on 57 pages;
    - pypdf `compress_identical_objects()` merged them into one shared array per part: part 2 → 2 pages, part 3 → 11, part 4 → 14, part 7 → 3;
    - every signature widget of those pages then appeared on all of them.
    - In part 4, the A3 pages 262, 264, 266, 288 and 292 (MediaBox 842×1191, /Rotate 270) have the widget Rect [566.05, 42.43, 726.05, 82.43]. That Rect is correct for an A3 page, but it also showed on the A4 pages 228, 231, 232, 235, 238, 241, 243, 246 and 251 (595.22×842), where it falls outside the page, to the right.
    - Reproduced with pypdf alone: without compression no array is shared; with it, array 133 is shared by 14 pages.
    - Fix: after `compress_identical_objects()`, each page of a part gets its own direct `/Annots` array.
  - Ruled out (a), positions lost in remapping:
    - the user set 3 per-page positions, all on pages 1–75;
    - part 1 shows exactly 3 non-default Rects;
    - parts 2–8 use the default position, as expected;
    - the remapping (`indice - inicio`) is correct and covered by tests.
  - Ruled out (b), boxes changed by the split:
    - for all 597 pages, the inherited MediaBox, CropBox, Rotate and UserUnit are identical in the source and in the parts;
    - pypdf flattens inherited attributes into each page.
  - Page sizes per part (from the sweep):
    - part 1: A4 39, Letter 36;
    - part 2: A4 74, A3 landscape (rot 90) 1;
    - part 3: A4 73, A3 (rot 90) 2;
    - part 4: A4 44, A3 (rot 270) 30, A3 landscape 1;
    - part 5: A4 74, A4 (rot 270) 1;
    - part 6: A4 75;
    - part 7: A4 39, A4 (rot 270) 25, Letter 11;
    - part 8: A4 47, A4 (rot 270) 25.
  - Field sweep result: 45 out-of-view widget Rects, all from the shared `/Annots` arrays on 9 A4 pages of part 4, and 822 stamp image references that do not resolve to an image.
  - Open product question, not changed: on a rotated page, the relative position applies to the unrotated space. The UI preview and the unsplit path do the same. A default stamp on an A3 page with /Rotate 270 therefore shows inside the page, along its right edge, with sideways text. This matches the module convention.
  - RED (`tests/test_firma_por_partes.py`):
    - `test_cada_parte_incrusta_su_propio_logo` → part 2 had no image (`[] == [(300, 200)]`);
    - `test_tamanos_mezclados_cada_pagina_solo_su_sello_y_dentro_de_su_vista`: A4, A3 portrait and A3 landscape pages, rotated 90/270, across 4 parts, with positions in later parts. Page 1 had 3 widgets, one being the A3 Rect [566.05, 42.43, …].
    - `test_cajas_heredadas_y_origen_distinto_de_cero_se_conservan_en_las_partes` passed before the fix. It guards inherited MediaBox, CropBox and Rotate, a negative origin and a CropBox intersection.
  - GREEN:
    - `python -m pytest -q` → 399 passed (396 + 3), with no access violation;
    - `node --check ui/app.js` → OK.
  - Smoke (test signer «PRUEBA PRIMERA», real 75-page limit, the user's real 597-page source, with a logo and own positions on pages 2, 80, 262 and 561):
    - 8 parts in 81 s;
    - pyHanko: parts 1, 4 and 8, first and last signature → intact, valid, trusted;
    - sweep: 597 stamps, each resolving to `Image 1269x873` in its own file;
    - 0 Rects outside the visible box and 0 box differences;
    - every page has its own direct `/Annots` array with exactly one widget;
    - the own positions appear in parts 1, 2, 4 and 8.
  - Build: `build.ps1` → its test run 399 passed; `dist\installer\FirmaPeru-Setup-1.2.7.exe` built.
  - Commits: `f2fc6f3` (fix + tests), `900d592` (version 1.2.7), plus the docs commit with this record.

## Acceptance criteria

- With the test signer:
  - a 160-page document gives 3 parts (75 + 75 + 10);
  - every page of every part has its own valid signature;
  - a second signer signing a part keeps all signatures valid;
  - stamp positions map correctly to each part;
  - cancel leaves no folder;
  - a signed document over 75 pages is refused with the message.
- The full suite passes and `node --check ui/app.js` is OK.
- Field check (user): parts validate in ReFirma and Acrobat.

## Progress

- 2026-11-19: document created; T1 delegated.
- 2026-11-19: T1 done (route: delegated writer). 396 passed; 1.2.6 installer built; smoke on the real 569-page source OK. Field check in ReFirma and Acrobat pending (user).
- 2026-11-19: field run of v1.2.6 on the real 597-page expediente showed the logo missing in parts 2–8 and stamps of A3 pages drawn outside A4 pages. T2 added and done (route: delegated writer): 399 passed; 1.2.7 installer built; real-source smoke and full sweep clean. Field check of 1.2.7 in ReFirma and Acrobat pending (user).
