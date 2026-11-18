# Firma Perú

Aplicación de escritorio para Windows que firma documentos PDF con el DNI
electrónico peruano y valida las firmas de un PDF.

- La firma es PAdES (una sola firma digital por documento) con un sello visible
  que se puede ubicar arrastrándolo sobre la página.
- El DNIe se usa a través de Windows (minidriver de RENIEC), no PKCS#11.
- **El PIN se ingresa en la ventana de Windows.** La aplicación nunca lo ve ni lo guarda.
- Funciona sin conexión a internet (el visor de PDF va incluido).

## Requisitos

- Windows 10 u 11 con el lector de tarjetas y el software del DNIe de RENIEC instalados.
- Python 3.12 (64 bits) y el runtime WebView2 de Microsoft (incluido en Windows 11).

## Instalación

```powershell
python -m pip install -r requirements.txt
```

## Uso

```powershell
python app.py
```

1. Abra un PDF (botón **Agregar PDFs**, `Ctrl+O` o arrastrándolo a la ventana).
2. Elija el **Tipo de firma** (**Firma** o **Visto bueno**) y, si quiere, cambie el motivo.
3. Arrastre el sello a la posición deseada y ajuste su tamaño con las esquinas.
4. Elija si el sello va en todas las páginas o solo en la página de la firma.
5. Pulse **Firmar documento** (`Ctrl+Enter`) y escriba el PIN en la ventana de Windows.

El resultado se guarda junto al original como `<nombre>_firmado.pdf`
(o donde usted elija con **Elegir dónde guardar**). Nunca se sobrescribe un archivo.

El botón **×** junto al nombre del documento (o `Ctrl+W`) cierra el documento:
lo quita de la lista y deja la ventana lista para otro. Las opciones de firma se mantienen.

### Firmar varios documentos (cola)

1. Agregue varios PDF con **Agregar PDFs** (`Ctrl+O`, selección múltiple) o
   arrastrándolos juntos a la ventana. Aparecen en **Documentos (N)**, a la
   izquierda, con su número de páginas, «ya firmado» si ya tienen firmas y su
   estado (**Pendiente**, **Firmando**, **Firmado**, **Error**, **Omitido**).
   Los archivos repetidos se ignoran y los que no son PDF válidos quedan con
   **Error** (no se firman). Haga clic en uno para verlo; **×** lo quita de la
   lista y la papelera vacía la cola.
2. Ubique el sello en cualquiera de ellos: el tipo, el motivo, el lugar, «Sello
   visible en» y la posición del sello valen para **todos**. El campo de firma va
   en la última página de cada documento.
3. Para un documento distinto, active **Posición propia para este documento**:
   lo que mueva o redimensione en él se aplica solo a ese. **Página actual** también
   se aplica solo al documento a la vista (y se limita a sus páginas).
4. Pulse **Firmar N documentos**. Se firman uno tras otro con una sola apertura
   de la llave del DNIe, para que Windows pueda pedir el PIN una sola vez (si la
   tarjeta exige el PIN en cada firma, lo pedirá en cada documento). **Cancelar**
   se detiene antes del siguiente documento, sin cortar el que se está escribiendo.
   - Si cancela el cuadro del PIN, la firma se detiene y lo que falta queda **Pendiente**.
   - Si el PIN es incorrecto o está bloqueado, la firma se detiene de inmediato
     para no bloquear el DNIe con más intentos.
   - Cualquier otro error marca ese documento con **Error** y se sigue con el siguiente.
5. Cada firmado queda junto a su original como `<nombre>_firmado.pdf`, o en una
   sola carpeta con **Guardar todos en una carpeta** (se elige una vez al empezar).
   Los PDF arrastrados sin carpeta de origen piden una carpeta, también una sola vez.
6. Al final se muestra un resumen (firmados, con error y omitidos, con el motivo)
   con **Abrir carpeta** y **Firmar más documentos** (quita los ya firmados de la lista).

En la consola, `python app.py` registra cuántas firmas se hicieron con la misma
llave abierta (`Sesión de firma cerrada: N firma(s) …`), nunca el PIN.

La pestaña **Validar firmas** muestra, para cada firma, el firmante, la fecha,
si el documento fue alterado, si hubo modificaciones posteriores y si el
certificado es de confianza según los certificados raíz de Windows.

### Tipos de sello

| Tipo | Aspecto | Motivo predeterminado | Fecha |
| --- | --- | --- | --- |
| **Firma** | Logo a la izquierda, datos a la derecha | Soy el autor del documento | `2026/10/27 10:38:55-0500` |
| **Visto bueno** | Imagen de visto bueno arriba, datos debajo (sin superponerse) | Doy V° B° | `27.10.2026 10:25:52 -05:00` |

- El motivo también queda registrado en la firma digital (campo *Reason*). El
  lugar, si se indica, solo se registra en la firma.
- El texto usa Arial incrustada en el PDF; si Arial no está disponible, Helvetica.
- **Logo**: elíjalo con **Elegir logo…** (PNG o JPG). Se guarda una copia en
  `%APPDATA%\FirmadorDNIe` (junto con `config.json`, que recuerda el último tipo
  usado), así que puede mover o borrar el original. Sin logo, el texto ocupa
  todo el sello.
- **Imagen de visto bueno**: elíjala con **Elegir imagen…** (solo con
  **Visto bueno**). Se guarda una copia en la misma carpeta; el fondo blanco se
  vuelve transparente. Sin imagen se dibuja una marca de verificación propia.
- **Imágenes iniciales**: si junto al programa (o junto a `FirmaPeru.exe`)
  existen `src\Emblema peruano con firma digital.png` (logo) o
  `src\Icono circular con marca roja.png` (visto bueno), se copian la primera
  vez. Si luego las quita con **Quitar logo** o **Quitar imagen**, no se vuelven
  a copiar. Estos archivos son suyos: no se incluyen en el ejecutable; cópielos
  junto a él si los necesita.
- **Actualización del logo inicial**: las versiones anteriores usaban
  `src\firma_digital_vivienda.png`. Si el logo guardado sigue siendo la copia
  sin cambios de ese archivo (y este sigue junto al programa), al iniciar se
  reemplaza por el emblema nuevo. Un logo elegido por usted o quitado con
  **Quitar logo** no se toca.

### PDF que ya tienen firmas

Si el PDF ya está firmado, el sello se coloca solo en el campo de la nueva
firma: dibujarlo en las demás páginas invalidaría las firmas anteriores. Si el
documento está certificado sin permitir cambios, la aplicación no lo firma.

### Páginas rotadas

La posición del sello se guarda en el espacio de la página sin rotar. En
páginas con rotación el sello queda donde se ubicó, pero su texto aparece girado
junto con la página.

## Línea de comandos

```powershell
python firma1.py --listar                         # certificados del DNIe (no pide PIN)
python firma1.py entrada.pdf salida.pdf           # firma con el sello en todas las páginas
python firma1.py entrada.pdf salida.pdf --cert FIR
python firma1.py entrada.pdf salida.pdf --tipo firma --logo logo.png
python firma1.py entrada.pdf salida.pdf --tipo vb --motivo "Doy V° B°"
python firma1.py entrada.pdf salida.pdf --tipo vb --imagen-vb check.png
```

`--tipo` es `firma` (predeterminado) o `vb`; `--motivo` reemplaza el motivo
predeterminado del tipo; `--imagen-vb` es la imagen de arriba del visto bueno
(sin ella, la marca de verificación propia).

## Pruebas

```powershell
python -m pytest -q
```

Las pruebas no usan la tarjeta: la firma de Windows se simula.

## Generar el ejecutable

```powershell
.\build.ps1
```

Genera `dist\FirmaPeru\FirmaPeru.exe`. Para distribuirlo, copie la carpeta
`dist\FirmaPeru` completa.
