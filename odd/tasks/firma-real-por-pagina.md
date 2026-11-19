# Firma real en cada página (un solo PIN)

## Objective

Add a signing mode, «Firma real en cada página». It puts one real PAdES signature on every page (one signature field and one visible stamp per page) in an automatic loop, and the user enters the DNIe PIN only once.

## Problem

The institution requires a real digital signature on every page, like the per-folio initialling done on paper. Today people do this by hand: they sign page by page in conventional apps that ask for the PIN on every page. Our current «todas» mode makes one real signature plus locked stamps on the other pages. It is cryptographically equivalent, but it does not meet that requirement.

## Why

It saves hours of repetitive work and many PIN entries, while meeting the per-page signature requirement.

## Scope (authorized by the user on 2026-11-19)

- A new page mode `cada_pagina` beside `todas` and `ultima`. It signs every page with its own signature field and a single widget on that page, using the per-page stamp positions the UI already manages.
- Supported for both «Firma» and «Visto bueno», for one document and for batches.
- The PIN is asked once:
  - the app collects it in its own dialog and supplies it to the DNIe key through CNG (`NCRYPT_PIN_PROPERTY`) before each signature, with silent signing;
  - the PIN is held only in memory for the duration of the loop and wiped afterwards;
  - it is never logged, persisted or returned to the UI.
- A wrong or blocked PIN stops the loop right away. There are no retries, because they could block the DNIe.
- Progress («Firmando página X de N») and Cancel. Output is atomic: the `_firmado.pdf` file is written only when every page has been signed. On cancel or error, no partial file is left.
- Later signers can also use `cada_pagina` on a document that already holds per-page signatures, and every earlier signature must stay valid (pyHanko + validacion.py).

## Constraints

- Signatures go into incremental updates only. Earlier signatures must stay intact.
- Performance matters: documents can have about 600 pages. Chain the revisions in memory, avoid re-parsing from disk on each page, and measure.
- Logging never includes the PIN. Follow the existing diagnostic log (`registro.py`, logger `firmador`).
- No AI attribution in commits. Use Conventional Commits.

## Tasks

- [x] T1 — Core loop in `firma_pdf.py`: `paginas="cada_pagina"`, one signature per page with unique field names, in-memory chaining, a progress/cancel callback and atomic output. Tests with the test signer cover: N valid signatures; a second signer keeps every earlier one valid; cancel leaves no file. Benchmark at 10/100/≈600 pages, recorded here.
  - Route: delegated writer (same worker for T1–T3).
  - RED: `tests/test_firma_por_pagina.py` failed at import (`cannot import name 'FirmaCancelada'`). `tests/test_validacion.py::test_firma_visible_despues_de_certificar_es_cambio_permitido` failed 2/2: pyHanko's default policy rejects a new *visible* signature field after a certification signature (`... only allowed after an approval signature, not a certification signature`). This also affected the existing single-field «solo_campo» mode. The logo-reuse test fails when reuse is disabled.
  - GREEN: `python -m pytest -q` → 260 passed (the 245 existing tests plus 15 new ones). The logo test brings the count to 261.
  - Implementation:
    - `_firmar_cada_pagina` keeps one `BytesIO` and signs each page with `sign_pdf(in_place=True)` (no copy of the document per page). It reopens an `IncrementalPdfFileWriter` on the same buffer for each page.
    - Field names follow `_nombre_libre` (FirmaDNIe, FirmaDNIe_2, …) and skip names that earlier signers already used.
    - The output is written through `_escribir_atomico` (temp file in the same folder, then `os.replace`). The «todas»/«ultima» modes use it too.
    - `progreso(hechas, total)` returning False raises `FirmaCancelada(ErrorFirma)`.
    - `ResultadoFirma.campos` lists the signed fields.
  - Decision (validacion.py): certified documents now use `POLITICA_CERTIFICADO`. This is pyHanko's default policy with `SigFieldCreationRule(allow_new_visible_after_certify=True)`, which matches Acrobat: with DocMDP 2/3 a new visible signature field counts as form filling. Non-widget annotations are still rejected.
  - Cheap win: the logo (`PdfImage`) is embedded once per document. Later signatures reference the same image XObject. Before this change each page added +853 KB with the full-size logo; after it each page adds ~11 KB on a small PDF.
  - Benchmark (SimpleSigner, tipo «firma» with the full-size `src/` logo, Python 3.12, this PC). The 10- and 100-page rows use synthetic A4 PDFs; the last row is `original_sin_firmar.pdf`:

    | Pages | Input | Total time | Time per page (first → last) | Output growth | Peak working set |
    |---|---|---|---|---|---|
    | 10 | 3 KB | 1.6 s | 0.23 → 0.09 s | +0.96 MB (logo once + ~11 KB/page) | 104 MB |
    | 100 | 30 KB | 11.4 s | 0.22 → 0.13 s | +2.0 MB (~11 KB/page) | 120 MB |
    | 569 (real) | 224 MB, 20 revisions | 337 s (5.6 min) | 0.45 → 0.84 s | +18.5 MB (~32 KB/page) | 562 MB (≈ 2.2 × document + 65 MB base) |

    - All 569 signatures were produced; the first and the last were checked as intact and valid.
    - With the real DNIe, each signature also costs the card's RSA operation (roughly 0.3–1 s), so a ~570-page document can take about 10–15 minutes.
    - Main hotspot (not changed): each page reopens the reader, which re-parses every xref section. The original has 20 sections of ~63k entries, which costs ~0.25 s/page and was about 60% of the time in a profile. On top of that, pyHanko enumerates every existing signature field twice per page, so the per-page time grows linearly with the number of signatures. Fixing either would mean patching pyHanko internals (`XRefBuilder`/`PdfFileReader`), which this task does not do.
- [x] T2 — PIN once in `dnie_cng.py`: an optional PIN supplied via `NCryptSetProperty(NCRYPT_PIN_PROPERTY)` plus silent signing, kept in memory only and wiped after use. A wrong PIN stops at once. Unit tests use fakes.
  - RED: 10 new tests in `tests/test_dnie_cng.py` failed (`SesionFirma.__init__() got an unexpected keyword argument 'pin'`).
  - GREEN: `tests/test_dnie_cng.py` → 15 passed; full suite → 271 passed; `node --check ui/app.js` OK.
  - Implementation: `SesionFirma(llave, hwnd, pin=None)`.
    - The PIN is copied right away into a `ctypes.create_unicode_buffer`, which is UTF-16LE with its terminator.
    - Before every `NCryptSignHash` the session calls `NCryptSetProperty(hKey, "SmartCardPin", buffer, sizeof, 0)`, and the signature uses `NCRYPT_SILENT_FLAG`.
    - The buffer is zeroed (`ctypes.memset`) when the session closes, when opening the key fails, and as soon as the PIN is rejected.
    - The codes `CODIGOS_PIN` (0x8010006B, 0x8010006C, NTE_INCORRECT_PASSWORD 0x80090033) stop the session. Every later signature fails without touching the card, and `NCryptSignHash` is never retried.
    - Without a PIN, the flags and calls are exactly as before.
    - Logs say only «PIN provisto: sí/no».
- [x] T3 — App + UI: the mode selector, a PIN dialog (password field), progress with Cancel, batch support, and a README section. API and static UI tests.
  - RED:
    - 6 errors and 4 failures in `tests/test_app.py` / `tests/test_lote.py`: no `app.tomar_pin`, no `INTERVALO_AVANCE_PAGINAS`, no `pin_provisto` in the «inicio» event, and the PIN was not consumed.
    - 5 failures in `tests/test_interfaz.py`: no third option, no PIN field, no `pedirPin`, no «paginas» event.
  - GREEN: full suite → 288 passed; `node --check ui/app.js` OK.
  - API (`app.py`):
    - `tomar_pin` removes `pin` from the options as soon as they arrive and keeps it only with `cada_pagina`.
    - The single-document `cada_pagina` path signs through `SesionFirma(llave, hwnd, pin=...)`, so the key is opened once. The batch uses the same session, so the PIN is asked once for every document.
    - Page progress goes to the UI through the existing `progresoLote` channel, as the event `{"evento": "paginas", hechas, total[, indice, id]}`. It is throttled to one event every `INTERVALO_AVANCE_PAGINAS = 0.3 s`; the first and last events are always sent.
    - `cancelar_lote` also cancels mid-document. A single document returns `{"ok": False, "cancelado": True, "mensaje": MENSAJE_CANCELADO}`. In a batch, the document in progress goes back to «Pendiente» and the run ends with `detenido="cancelado"`.
    - Logs record only «PIN provisto: sí/no».
    - New messages: 0x80090033 shows the same text as a wrong PIN, and 0x80090022 (NTE_SILENT_CONTEXT) has its own message. Both stop a batch.
  - UI (`ui/index.html`, `ui/app.js`, `ui/estilos.css`):
    - The selector has a third option, «Firma real en cada página», with the hint «Una firma digital por página; pide el PIN una sola vez.».
    - The PIN dialog has a password field (`#inp-pin`). Enter confirms; Cancel or Escape closes. The field is cleared on confirm, cancel and close, and the PIN is never kept in `estado` or in preferences.
    - Progress shows «Firmando página X de N» with Cancel.
    - In a certified document «todas» stays disabled, but «cada_pagina» is allowed.
  - README: new section «Firma real en cada página».

## Route

Delegated direct, one writer. Trigger: 2+ non-trivial files (`firma_pdf.py`, `dnie_cng.py`, `app.py`, `ui/app.js`, tests).

## Acceptance criteria

- A 1-page and a multi-page PDF signed with `cada_pagina` have one valid signature per page (pyHanko `validate_pdf_signature` intact, and `validar_pdf` OK).
- A second signer using `cada_pagina` keeps every earlier signature valid.
- The full test suite passes and `node --check ui/app.js` is OK.
- The benchmark numbers are recorded.
- A real DNIe test (by the user) confirms that the PIN is asked once. Pending until done.

## Progress

- 2026-11-19: document created; T1–T3 delegated to one writer.
- 2026-11-19: T1 done (core loop + certified-document policy + benchmark) — commit `bd966ea`.
- 2026-11-19: T2 done (PIN supplied once through CNG, silent signing, wiped buffer) — commit `590f3de`.
- 2026-11-19: T3 done (API, UI, README). Its commit hash is in the git log («feat: add the Firma real en cada página option …»).
- Next step: a real DNIe test by the user. It should confirm four things:
  - the PIN is asked only once and no Windows PIN dialog appears;
  - a wrong PIN stops at once;
  - Acrobat shows every per-page signature as valid, including after a second signer;
  - how long a long document takes with the card.
