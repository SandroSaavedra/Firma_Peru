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

### Firma real en cada página

Es el **único modo de firma**: cada página recibe su **propia** firma digital,
con su campo de firma y su sello en esa página, como el visado folio por folio
en papel. En **Sello visible en**, la opción **Firma real en cada página** viene
marcada; **Todas las páginas** (una firma y el sello repetido en las demás) y
**Solo la página de firma** se muestran con candado y no se pueden elegir.

- **El PIN se pide una sola vez**, en un cuadro de la propia aplicación: al
  firmar un documento o, en un lote, una vez para todos los documentos. La
  aplicación se lo entrega al DNIe antes de cada firma, así que Windows no
  vuelve a mostrar su cuadro del PIN.
- Con el DNIe 3.0, la aplicación firma por medio del módulo de IDEMIA que
  instala el controlador del DNIe 3.0 de RENIEC (`idplug-pkcs11.dll`), así que
  el PIN también se escribe una sola vez. Si ese módulo no está disponible, el
  DNIe 3.0 no acepta el PIN que entrega la aplicación y Windows pide el PIN en
  cada página; el diálogo de avance lo indica y cancelar ese cuadro cancela la
  firma sin guardar.
- **El PIN nunca se guarda**: queda solo en memoria mientras dura la firma y
  después se borra. No se escribe en el registro ni en la configuración, y no
  vuelve a la interfaz.
- Si el PIN es incorrecto o está bloqueado, la firma se detiene en el acto, sin
  reintentar, para no bloquear el DNIe.
- Mientras firma se ve «Firmando página X de N». **Cancelar** se detiene antes
  de la página siguiente y **no guarda** ese documento. El `_firmado.pdf` solo
  se escribe cuando todas las páginas quedaron firmadas.
- **Tarda**: cada página es una firma completa. En una prueba con un documento
  real de 569 páginas y 224 MB, sin tarjeta, las 8 partes tardaron unos 70
  segundos (de 0,1 a 0,14 s por página). Con el DNIe hay que sumar la firma de
  la tarjeta en cada página. Cada parte crece unos 12 KB por página.
- Funciona también en PDF ya firmados (las firmas anteriores siguen válidas) y
  en documentos certificados que admiten cambios.

#### Documentos de más de 75 páginas: se firman en partes

Con cientos de firmas en un mismo archivo los lectores dejan de validarlas: en
pruebas con un expediente real de 597 páginas firmadas, Acrobat se quedaba
«cargando» o marcaba algunas firmas como «modificado o dañado», y ReFirma ni
siquiera las listaba. Con 150 firmas por archivo, Acrobat y ReFirma las
validaron todas. Por eso, si un documento **sin firmas** tiene más de 75
páginas, la aplicación lo divide en partes de hasta **75 páginas
consecutivas** y firma cada página de cada parte, con el mismo PIN y la misma
sesión del DNIe. Con dos firmantes, cada parte llega a 150 firmas.

- Las partes se guardan en una **carpeta** junto al destino, con el nombre del
  documento: `expediente_firmado\expediente_parte01de08_firmado.pdf`,
  `expediente_parte02de08_firmado.pdf`… (con visto bueno, `_vb` en lugar de
  `_firmado`). El número de parte lleva ceros a la izquierda según el total.
  Si la carpeta ya existe, se usa `expediente_firmado (2)`.
- Mientras firma se ve «Parte X de Y · Firmando página A de B». Al terminar,
  **Abrir carpeta** muestra las partes en el Explorador. En un lote, cada
  documento largo cuenta como un documento firmado, con su carpeta y su número
  de partes.
- Si se cancela o algo falla, no queda ninguna carpeta ni parte a medias.
- Cada parte es un PDF nuevo con las mismas páginas. Los marcadores y los
  campos de formulario del original pueden no pasar a las partes.
- Un documento de 75 páginas o menos se firma como siempre, en un solo archivo.
- **Un PDF que ya tiene firmas y más de 75 páginas no se puede dividir**:
  dividirlo invalidaría esas firmas. Al abrirlo solo, la aplicación lo avisa
  antes de pedir el PIN (en un lote, ese documento queda con error y los demás
  se firman): «Este PDF ya tiene firmas y más de 75 hojas: no se puede dividir sin
  invalidarlas. Pida al primer firmante el expediente dividido en partes.». El
  segundo firmante firma cada parte por separado (cada una tiene 75 páginas o
  menos).

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

### Sello en todas las páginas

Cada firma es una sola firma criptográfica con un solo campo de firma, en la
página de firma (la última, salvo que se elija otra): al hacer clic en ese
sello, el lector muestra el estado de la firma. En las demás páginas el sello
es una anotación bloqueada (no se puede mover ni borrar sin que se note) con el
mismo aspecto, cubierta por la firma, y encima lleva un enlace: al hacer clic,
Acrobat abre una pequeña ventana con el firmante, la fecha y el estado de ESA
firma, e indica en qué página está su campo. Ese clic usa JavaScript: Acrobat
puede pedir permiso para ejecutarlo y otros lectores de PDF pueden ignorarlo
(el sello se ve igual).

### PDF que ya tienen firmas

Un PDF ya firmado se sella igual que uno sin firmas: con «Sello visible en»
todas las páginas, la nueva firma pone su sello en cada página (en la posición
de cada página) y las firmas anteriores siguen siendo válidas. La nueva firma
se agrega como una revisión con su propio campo de firma y sus sellos, cambios
permitidos después de firmar; al validar, las firmas anteriores aparecen con
modificaciones «permitidas» (Acrobat: «la revisión cubierta por esta firma no
se ha modificado; se han efectuado cambios posteriores»).

> **Documentos firmados «en todas las páginas» con versiones anteriores de la
> aplicación** (un campo de firma con un sello-widget en cada página): Acrobat
> marca esa firma como «NO ES VÁLIDA – el documento se ha modificado» en cuanto
> se agrega otra firma. No agregue firmas a esos documentos: vuelva a firmarlos
> desde el PDF original con esta versión.

Si el documento está **certificado** (firma de certificación con permisos
DocMDP), el sello va solo en el campo de la nueva firma, y si la certificación
no permite ningún cambio, la aplicación no lo firma.

### Páginas rotadas

El sello sigue la orientación con la que se **ve** cada página, sea cual sea su
rotación guardada (/Rotate):

- Página que se ve vertical (A4, carta): sello horizontal, abajo a la derecha.
- Página que se ve horizontal (A3 apaisada): sello vertical en el margen
  derecho.

La posición (automática, del documento o puesta a mano en una página) se
interpreta en ese mismo marco: en las páginas que se ven verticales y no están
rotadas es la misma de siempre.

La vista previa muestra el sello de cada página tal como se firmará: derecho en
las páginas que se ven verticales y girado en el margen derecho de las que se
ven horizontales. Al arrastrarlo, su posición se guarda en el marco de esa
página, así que «Aplicar a todas las páginas» lo pone en el mismo lugar
relativo de cada página que se vea con la misma orientación.

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

## Registro de diagnóstico

La aplicación guarda un registro en
`%LOCALAPPDATA%\FirmadorDNIe\registros\firmador.log` (rota al llegar a 1 MB y
conserva las 5 copias anteriores). Anota cada paso de la firma: el documento
abierto (nombre, páginas y firmas previas), dónde se guarda, la búsqueda de la
llave del DNIe (titular, uso y vigencia del certificado), el momento en que
Windows pide el PIN, el código que devuelve la tarjeta y los errores. No
contiene el PIN (la aplicación nunca lo ve) ni el contenido de los documentos.

Para abrir la carpeta, use **Abrir registro de diagnóstico** (debajo del botón
«Firmar») o **Abrir registro** en un mensaje de error. Al reportar un problema,
envíe el archivo `firmador.log`.

Para encontrar la llave del DNIe, la aplicación primero consulta al proveedor
de tarjetas inteligentes de Windows sin indicar el lector. Si así no la
encuentra, la busca en cada lector que tenga una tarjeta insertada y, por
último, en el almacén de certificados personales de Windows («MY»). El
registro indica por qué vía la encontró (sin alcance, lector «X» o almacén
MY) y qué vio en cada una.

El botón **ⓘ** de la barra superior muestra la versión instalada y la ruta
del registro, con un acceso directo a la carpeta.

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
`dist\installer\FirmaPeru-Setup-1.2.9.exe`. Opciones:

- `-SinPruebas`: no ejecuta las pruebas antes de construir.
- `-Version 1.2.9`: versión del instalador (predeterminada: `VERSION` de
  `version.py`, la única fuente de la versión; cámbiela ahí al publicar).
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
.\FirmaPeru-Setup-1.2.9.exe /VERYSILENT /SUPPRESSMSGBOXES
```

Para desinstalar, use **Configuración > Aplicaciones > Aplicaciones instaladas >
Firma Perú > Desinstalar**. La configuración en `%APPDATA%\FirmadorDNIe` se conserva.

El instalador no está firmado digitalmente, así que Windows SmartScreen puede
mostrar «Windows protegió su PC». Para continuar, pulse **Más información** y
luego **Ejecutar de todas formas**.
