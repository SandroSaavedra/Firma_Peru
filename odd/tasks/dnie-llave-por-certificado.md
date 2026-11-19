# Find the DNIe key through each reader and the Windows certificate store

## Objective

Find the DNIe signing key even when `NCryptEnumKeys` with no scope returns `NTE_NOT_FOUND` (0x80090011). Do this the way other signing apps reach the key: through each reader and through the Windows certificate store.

## Problem

- One signer's DNIe fails in our app on every PC: `NCryptEnumKeys` with a NULL scope on «Microsoft Smart Card Key Storage Provider» returns 0x80090011.
- ReFirma (RENIEC) and Firma ONPE sign with that same card on those same PCs. So the card, its certificates and the RENIEC minidriver all work. The failure is in how `dnie_cng.listar_llaves` looks for keys.

## Hypotheses (not verified, no access to the card)

1. A NULL scope only enumerates the card the provider treats as the default. With several readers, or a virtual smart card (Windows Hello, TPM), it can look at the wrong one.
2. A newer DNIe chip or minidriver propagates its certificate into the user «MY» store (with `CERT_KEY_PROV_INFO_PROP_ID`), but a scope-less KSP enumeration does not list it.

## Scope (authorized by the user on 2026-11-19)

- Keep the current scope-less enumeration as the first attempt, so the cards that already work behave exactly as before.
- Fallback 1: if it finds no key with a certificate, enumerate per reader with scope `\\.\<reader name>\` for each reader that has a card. Readers come from WinSCard and the existing `diagnostico_dnie` facts.
- Fallback 2: if there is still no key, look in the current user's «MY» store for certificates with `CERT_KEY_PROV_INFO_PROP_ID`, a smart card provider and a non-repudiation/digital-signature key usage. Open their key as an NCrypt handle with `CryptAcquireCertificatePrivateKey(CRYPT_ACQUIRE_ONLY_NCRYPT_KEY_FLAG | CRYPT_ACQUIRE_SILENT_FLAG)`, or with NCryptOpenKey on the provider and container read from the property.
- Signing (`SesionFirma`, PIN supplied via `NCryptSetProperty`, silent flag, CODIGOS_PIN handling) must work with a key found by any path.
- Log which path found the key (sin alcance / lector «X» / almacén MY) and, for each fallback, what it saw: readers tried and their status codes; store certs with their provider and container names. No PIN, no document contents.
- Retries and diagnosis (T4 of registro-diagnostico) stay. The diagnosis runs only when every path failed.

## Constraints

- Windows CNG through ctypes, like today. No new dependencies.
- Unit tests with fakes. The real fallbacks can only be confirmed with the affected card, by the user.
- No AI attribution in commits. Use Conventional Commits.

## Tasks

- [x] T1 — Per-reader and certificate-store fallbacks in `dnie_cng.py`, with logging, tests and a README note. Bump the version to 1.2.3 and build the installer.
  - Route: delegated writer. Trigger: preparation reading of `dnie_cng.py`, `diagnostico_dnie.py` and the tests, plus 2+ non-trivial files.

## Acceptance criteria

- With fakes:
  - when the scope-less enumeration returns NTE_NOT_FOUND and a reader scope has the key, the key is found;
  - when both fail and the store has the certificate, the key is found;
  - when everything fails, the same friendly diagnosis is shown as today.
- Existing behavior is unchanged when the first attempt finds the key.
- The full suite passes and `node --check ui/app.js` is OK.
- Field check (by the user): the affected DNIe can sign, and `firmador.log` shows which path found the key.

## Progress

- 2026-11-19: document created; T1 delegated.
- 2026-11-19: T1 implemented by the delegated writer (route: delegated, as declared).
  - RED: `python -m pytest -q tests/test_dnie_busqueda.py` → 10 failed (`dnie_cng` had no `_certificados_de_tarjeta_en_almacen` / fallbacks).
  - GREEN: same command → 10 passed. Full suite `python -m pytest -q` → 301 passed. `node --check ui/app.js` → OK.
  - Commit: `50a4279` feat: find the DNIe key per reader and through the Windows certificate store.
  - Build: `build.ps1` → `dist\installer\FirmaPeru-Setup-1.2.3.exe` (its test run: 301 passed).
  - Implementation notes:
    - `listar_llaves` = attempt 1 `_enumerar(proveedor, None)` (unchanged behavior and errors), then `_buscar_por_lector` (scope `\\.\<reader>\` for each WinSCard reader with SCARD_STATE_PRESENT; key names are stored fully qualified `\\.\<reader>\<container>`), then `_buscar_en_almacen` (MY certs with CERT_KEY_PROV_INFO_PROP_ID, provider name containing "Smart Card", key usage digitalSignature/nonRepudiation; container opened silently via NCryptOpenKey on the Smart Card KSP, first qualified with each reader with a card, then as is; its SmartCardKeyCertificate, if readable, must match the store cert).
    - Store keys are opened through the KSP by name, not with CryptAcquireCertificatePrivateKey, so `FirmanteCNG` and `SesionFirma` open every kind of key the same way (no caller-free handle bookkeeping; PIN property, silent flag and CODIGOS_PIN unchanged). `LlaveTarjeta` needed no new field.
    - Fallback failures are logged and skipped; only when every path fails is the same `ErrorCNG` raised, with `codigo` = NTE_NOT_FOUND from attempt 1, so retries and the diagnosis are unchanged.
    - Logging (`firmador.cng`): «halladas por: sin alcance / lector «X» / almacén MY»; every reader with its state hex and enumeration result; every MY cert with CN, provider, container and key spec.
  - Known issue (outside T1's edit surface): `tests/test_registro.py::test_enumeracion_sin_llaves_nte_not_found_da_el_mensaje_amigable` now runs the real fallbacks; on a PC with a smart-card cert in MY it calls the real NCryptOpenKey with the fake provider handle 7 and faulthandler prints "Windows fatal exception: access violation" (caught by ctypes; the test passes). Fix: stub `_recolectar_hechos` and `_certificados_de_tarjeta_en_almacen` in its `_enumeracion_falsa`.
  - Pending: field check with the affected DNIe (`firmador.log` shows the path).
