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
2. Elija el **Tipo de firma** (**Firma** o **Visto bueno**) y, si quiere, elija otro motivo de la lista.
3. Arrastre el sello a la posición deseada (o muévalo con las flechas). Su tamaño es fijo.
   Lo que mueva se aplica solo a la página a la vista (en las miniaturas, un punto
   junto al número marca las páginas con posición propia); **Aplicar a todas las
   páginas** usa esa posición en todo el documento.
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
2. El tipo, el motivo, el lugar y «Sello visible en» valen para **todos**. El
   campo de firma va en la última página de cada documento.
3. La posición del sello es de cada documento y de cada página: lo que mueva
   se aplica solo a esa página de ese documento (en la lista aparece «posición
   propia»). Las páginas que no haya movido usan la posición del documento, y
   los documentos que no haya movido, la posición común. **Aplicar a todas las
   páginas** usa la posición de la página a la vista en todo ese documento;
   **Aplicar a todos los documentos** la copia a todas las páginas de todos los
   de la lista y la deja como predeterminada. **Página actual** también se
   aplica solo al documento a la vista (y se limita a sus páginas).
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

| Tipo | Tamaño | Aspecto | Motivos | Fecha |
| --- | --- | --- | --- | --- |
| **Firma** | 160 × 40 pt | Logo a la izquierda, datos a la derecha | Soy el autor del documento (predeterminado), En señal de conformidad | `2026/10/27 10:38:55-0500` |
| **Visto bueno** | 140 × 110 pt | Imagen de visto bueno arriba, datos debajo (sin superponerse) | Doy V° B° | `27.10.2026 10:25:52 -05:00` |

- El sello mide lo mismo en cualquier tamaño de página y no se puede
  redimensionar; solo se mueve. Si en la posición elegida no cabe, se desplaza
  hasta quedar dentro de la página.
- El motivo se elige de la lista del tipo y también queda registrado en la firma
  digital (campo *Reason*). El lugar, si se indica, solo se registra en la firma.
- El texto usa Arial incrustada en el PDF; si Arial no está disponible, Helvetica.
- **Logo**: siempre es `src\Emblema peruano con firma digital.png`; el
  ejecutable y el instalador lo llevan incluido (`firmador.spec`), así que no
  hace falta copiarlo junto a `FirmaPeru.exe`. No se puede cambiar ni quitar. El panel
  lo muestra solo como referencia. Se usa una copia reducida que se guarda en
  `%APPDATA%\FirmadorDNIe` (junto con `config.json`, que recuerda el último
  tipo usado). Un logo elegido con versiones anteriores se ignora. Si el
  archivo falta o no es una imagen, se firma igual, el texto ocupa todo el
  sello y queda un aviso en el registro.
- **Imagen de visto bueno**: elíjala con **Elegir imagen…** (solo con
  **Visto bueno**). Se guarda una copia en la misma carpeta; el fondo blanco se
  vuelve transparente. Sin imagen se dibuja una marca de verificación propia.
- **Imagen de visto bueno inicial**: si junto al programa existe
  `src\Icono circular con marca roja.png`, se copia la primera vez. Si la
  quita con **Quitar imagen**, no se vuelve a copiar.
- Las imágenes de `src\` no se incluyen en el ejecutable; cópielas junto a él.

### PDF que ya tienen firmas

Un PDF ya firmado se sella igual que uno sin firmas: con «Sello visible en»
todas las páginas, la nueva firma pone su sello en cada página (en la posición
de cada página) y las firmas anteriores siguen siendo válidas. La nueva firma
se agrega como una revisión con su propio campo de firma, un cambio permitido
después de firmar; al validar, las firmas anteriores aparecen con
modificaciones «permitidas».

Si el documento está **certificado** (firma de certificación con permisos
DocMDP), el sello va solo en el campo de la nueva firma, y si la certificación
no permite ningún cambio, la aplicación no lo firma.

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
predeterminado del tipo (aquí admite cualquier texto, no solo los de la lista); `--imagen-vb` es la imagen de arriba del visto bueno
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

Genera `dist\FirmaPeru\FirmaPeru.exe` y el instalador
`dist\installer\FirmaPeru-Setup-1.0.0.exe`. Opciones:

- `-SinPruebas`: no ejecuta las pruebas antes de construir.
- `-Version 1.2.0`: versión del instalador (predeterminada: `1.0.0`).
- `-SinInstalador`: solo genera la carpeta `dist\FirmaPeru`; para distribuirla,
  copie la carpeta completa.

## Instalador (sin permisos de administrador)

El instalador se compila con [Inno Setup 6](https://jrsoftware.org/isinfo.php)
(`installer\FirmaPeru.iss`). Si no está instalado, instálelo para su usuario,
sin permisos de administrador:

```powershell
winget install JRSoftware.InnoSetup --scope user
```

`build.ps1` busca `ISCC.exe` en la instalación por usuario
(`%LOCALAPPDATA%\Programs\Inno Setup 6`), en `Program Files` y en el `PATH`.

Qué hace el instalador:

- Instala solo para el usuario actual en `%LOCALAPPDATA%\Programs\Firma Peru`.
  No pide permisos de administrador ni muestra el aviso de Control de cuentas de usuario (UAC).
- Crea un acceso directo en el menú Inicio y, si lo marca, otro en el escritorio.
- No toca la configuración ni las imágenes guardadas en `%APPDATA%\FirmadorDNIe`.

Instalación silenciosa (por ejemplo, para distribuirlo con un script):

```powershell
.\FirmaPeru-Setup-1.0.0.exe /VERYSILENT /SUPPRESSMSGBOXES
```

Para desinstalar, use **Configuración > Aplicaciones > Aplicaciones instaladas >
Firma Perú > Desinstalar**. La configuración en `%APPDATA%\FirmadorDNIe` se conserva.

El instalador no está firmado digitalmente, así que Windows SmartScreen puede
mostrar «Windows protegió su PC». Para continuar, pulse **Más información** y
luego **Ejecutar de todas formas**.
