# Firma Perú

Aplicación de escritorio para Windows que firma documentos PDF con el DNI
electrónico peruano (DNIe) y valida las firmas de un PDF.

- Firma PAdES con un sello visible que se ubica arrastrándolo sobre la página.
- Dos tipos de sello: **Firma** y **Visto bueno**.
- Firma varios documentos seguidos (cola) pidiendo el PIN una sola vez cuando la tarjeta lo permite.
- **El PIN se ingresa en la ventana de Windows.** La aplicación nunca lo ve ni lo guarda.
- Funciona sin conexión a internet.

## Requisitos

- Windows 10 u 11 (64 bits).
- Lector de tarjetas y el software del DNIe de RENIEC instalados.
- Runtime WebView2 de Microsoft (ya viene en Windows 11; en Windows 10 se
  descarga gratis desde Microsoft).

## Descarga e instalación

1. Vaya a la sección **[Releases](../../releases)** y descargue el archivo
   `.zip` de la última versión.
2. Descomprima el `.zip` en una carpeta, por ejemplo `Documentos\FirmaPeru`.
3. Abra `FirmaPeru.exe`.

> **Importante:** no saque `FirmaPeru.exe` de su carpeta. Necesita la carpeta
> `_internal` que está a su lado para funcionar.

No requiere permisos de administrador.

### Aviso de Windows SmartScreen

El ejecutable no está firmado con un certificado de editor, por lo que Windows
puede mostrar «Windows protegió su PC» la primera vez. Para continuar, pulse
**Más información** y luego **Ejecutar de todas formas**.

## Uso

1. Conecte el lector con el DNIe.
2. Abra un PDF (botón **Agregar PDFs**, `Ctrl+O` o arrastrándolo a la ventana).
3. Elija el **Tipo de firma** (**Firma** o **Visto bueno**) y, si quiere, cambie el motivo.
4. Arrastre el sello a la posición deseada y ajuste su tamaño con las esquinas.
5. Pulse **Firmar documento** (`Ctrl+Enter`) y escriba el PIN en la ventana de Windows.

El resultado se guarda junto al original como `<nombre>_firmado.pdf`.
Nunca se sobrescribe un archivo.

Para firmar varios PDF a la vez, agréguelos juntos con **Agregar PDFs** y pulse
**Firmar N documentos**.

> Si el PIN es incorrecto, la firma se detiene de inmediato para no bloquear el DNIe.

## Validar firmas

La pestaña **Validar firmas** muestra, para cada firma de un PDF, el firmante,
la fecha, si el documento fue alterado y si el certificado es de confianza.
