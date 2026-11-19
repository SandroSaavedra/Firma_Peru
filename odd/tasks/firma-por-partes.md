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
