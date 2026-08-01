### [Bloqueo de UI por Inconsistencia de Clases CSS]
- **Síntoma/Log:** Los botones de control de grabación (pausar, cancelar, terminal) desaparecen o se muestran bloqueados al iniciar una segunda grabación tras haber finalizado la primera.
- **Causa raíz:** Al reiniciar el estado de la UI (en `popup.js`), se eliminaban o sobrescribían clases de layout críticas (como `flex-row`) al alternar estados, rompiendo la estructura flexbox del contenedor al volver al estado "recording".
- **Regla de prevención:** Al alternar la visibilidad de elementos del DOM, manipula EXCLUSIVAMENTE la clase `hidden`. NUNCA remuevas ni reasignes clases estructurales (`flex`, `flex-row`, `grid`) durante los reseteos de estado.

### [Bloqueo del API tabCapture (Múltiples Grabaciones)]
- **Síntoma/Log:** La segunda grabación consecutiva falla silenciosamente o la extensión se queda congelada sin avanzar el temporizador.
- **Causa raíz:** El documento `offscreen` permanecía vivo en segundo plano tras finalizar la primera grabación. La API de `chrome.tabCapture` no maneja bien múltiples conexiones simultáneas o flujos zombis si el contexto del documento anterior no fue destruido.
- **Regla de prevención:** Destruye SIEMPRE el documento `offscreen` (`chrome.offscreen.closeDocument()`) al finalizar o cancelar una grabación, y vuelve a crearlo limpio (`chrome.offscreen.createDocument()`) para la siguiente.

### [Acceso Restringido a Storage y Downloads en Offscreen]
- **Síntoma/Log:** `Uncaught TypeError: Cannot read properties of undefined (reading 'local')` o fallos intentando usar `chrome.downloads` en `offscreen.js`.
- **Causa raíz:** Asumí incorrectamente que el `offscreen document` heredaría todos los permisos del `background` (como `chrome.storage` y `chrome.downloads`). Sin embargo, por diseño de seguridad en Manifest V3, los documentos fuera de pantalla tienen accesos casi nulos a las APIs de extensión más allá de `chrome.runtime`.
- **Regla de prevención:** NUNCA accedas a `chrome.storage` o `chrome.downloads` directamente desde `offscreen.js`. Delega SIEMPRE la ejecución de descargas nativas o la persistencia de estado mediante paso de mensajes (`chrome.runtime.sendMessage`) hacia el `background.js`.

### [Bloqueo Institucional 403 con yt-dlp]
- **Síntoma/Log:** `ERROR: [GoogleDrive] ... Unable to download JSON metadata: HTTP Error 403: Forbidden`.
- **Causa raíz:** Las cuentas institucionales (Google Workspace) bloquean la descarga directa de archivos de video protegidos, haciendo inútil la extracción de cookies del navegador con `yt-dlp`.
- **Regla de prevención:** ABANDONA cualquier intento de descargar videos de Drive institucional vía scrapeo/cookies. Depende EXCLUSIVAMENTE del pipeline de grabación en vivo mediante `chrome.tabCapture`.

### [Límites de Tamaño en Mensajes IPC (Manifest V3)]
- **Síntoma/Log:** Error fatal al enviar un string codificado masivo por `sendMessage` (ej. Error en canal IPC o desconexión del puerto).
- **Causa raíz:** Intenté enviar un archivo Blob enorme directamente en un solo mensaje asumiendo que el canal IPC no tendría límite para el paso de Base64. Sin embargo, Chrome impone un límite de tamaño por mensaje de ~50MB, el cual es fácilmente superado por grabaciones largas.
- **Regla de prevención:** NUNCA envíes archivos pesados de una sola vez por `chrome.runtime.sendMessage`. Divide SIEMPRE las cadenas Base64 en "chunks" pequeños (ej. de 5MB) en el emisor, envíalos con un identificador e índice, y reconstrúye el archivo completo en el receptor (`background.js`) antes de consumirlo.

### [Throttling Agresivo de setInterval en Offscreen Documents]
- **Síntoma/Log:** Las capturas automáticas programadas con intervalos largos (ej. cada 5 minutos) toman solo la primera captura y luego dejan de ejecutarse, o los intervalos cortos (1 minuto) pierden exactitud (toman 9 capturas en lugar de 12).
- **Causa raíz:** Aunque el documento `offscreen` se mantenga vivo por el procesamiento multimedia, Chrome aplica un throttling (congelamiento) agresivo a las funciones nativas como `setInterval` si el navegador está en segundo plano o minimizado.
- **Regla de prevención:** NUNCA uses `setInterval` para temporizadores críticos o largos en `offscreen.js`. Delega SIEMPRE la ejecución temporal a la API nativa `chrome.alarms` en el `background.js`, la cual está diseñada para despertar al Service Worker sin verse afectada por las políticas de ahorro de batería.

### [Fallo Silencioso de captureVisibleTab en Pestañas Minimizadas]
- **Síntoma/Log:** No se guardan las capturas de pantalla de la clase (salvo la primera) cuando el usuario minimiza el navegador, o se capturan fotogramas de la pestaña equivocada si el usuario cambia de pestaña.
- **Causa raíz:** La función `chrome.tabs.captureVisibleTab` SOLO puede capturar el área visible de la pestaña ACTIVA en la ventana especificada. Si la ventana está minimizada o tapada, la API arroja un error interno silencioso y aborta la captura.
- **Regla de prevención:** Para captura robusta de video en segundo plano, extrae la pista de video directamente del `MediaStream` (usando `chromeMediaSource: 'tab'`) hacia un elemento `<video>` interno en memoria (`offscreen.js`) y corta los fotogramas usando un `<canvas>`. Nunca dependas de la visibilidad real de la ventana.

### [Bloqueo de UI y TypeError por Asignación a Constante]
- **Síntoma/Log:** Al iniciar la grabación, los botones de pausa/captura desaparecen y la UI no cambia a "Grabación Activa", quedándose bloqueada en "Listo para Iniciar", aunque la grabación real sí inició por debajo. `TypeError: Assignment to constant variable` en el log del popup.
- **Causa raíz:** Al configurar event listeners que mutan el estado de la UI (como actualizar el estado dinámico de audio de la pestaña `isAudible = tab.audible;`), la variable había sido declarada como `const`. Al mutarla, el listener lanzaba una excepción fatal y estrellaba todo el script visual sin completar la función de actualización (`updateUI`).
- **Regla de prevención:** Revisa estrictamente el scope y tipo de declaración (`let` vs `const`) de variables mutables consumidas dentro de `chrome.tabs.onUpdated` o `chrome.storage.onChanged` en los popups. Mantenlas como `let` si su estado depende del entorno dinámico.
### [Anidamiento Erróneo de Descargas con Rutas Absolutas]
- **Síntoma/Log:** Configurar una ruta como `/home/usuario/Documentos/` provoca que la extensión cree la carpeta en `Descargas/home/usuario/Documentos/`.
- **Causa raíz:** Las extensiones (por seguridad del sandboxing) no pueden escribir libremente en el sistema de archivos. La API `chrome.downloads` trata cualquier ruta como relativa a la carpeta predeterminada "Descargas".
- **Regla de prevención:** NUNCA permitas a los usuarios configurar rutas absolutas para descargas en la extensión. Usa siempre subcarpetas relativas y fijas (ej. `Backups_Clases/`) para evitar comportamientos confusos.

### [Fallo Silencioso por Desajuste de Modelo de Datos (Property Mistyping)]
- **Síntoma/Log:** Un botón o acción (como navegar a una fecha) no hace nada. No hay errores en consola, pero la ejecución se detiene silenciosamente a mitad de una función de filtrado.
- **Causa raíz:** Iterar sobre arrays de objetos usando una propiedad inexistente (ej. leer `slot.fecha_esperada` en lugar de `slot.fecha` que es como lo genera el backend). Al retornar `undefined`, funciones como Date math retornan `NaN` y rompen silenciosamente la cadena lógica.
- **Regla de prevención:** Al crear filtros en el frontend, valida SIEMPRE contra el schema real del backend en `server.py` (`ProgresoSlotCreate` o estructuras similares) y no asumas nombres de propiedades.

### [Interferencia del Navegador ("Ghost Drag") bloqueando Paneo JS]
- **Síntoma/Log:** Al implementar lógica "Drag to Pan" (arrastrar para desplazar) sobre una etiqueta `<img>`, el puntero se atasca y aparece una imagen translúcida arrastrándose hacia el escritorio, rompiendo los eventos `mouseup` de JS.
- **Causa raíz:** El navegador asume nativamente que un click sostenido sobre una imagen significa que el usuario quiere descargarla o moverla, disparando su comportamiento por defecto de arrastre (Drag & Drop nativo) el cual secuestra el hilo de eventos de ratón.
- **Regla de prevención:** Siempre que implementes interacciones de ratón personalizadas sobre medios (como imágenes), añade INVARIABLEMENTE el atributo `draggable="false"` a la etiqueta HTML y aplica `e.preventDefault()` en el evento `mousedown` para anular la intervención del navegador.

### [Documentos Fantasma (Fallo Silencioso por Mismatch de Nombre de Archivo)]
- **Síntoma/Log:** Al intentar cargar programáticamente un documento vía `fetch('/api/summaries/...')`, el sistema renderiza un documento completamente vacío sin lanzar notificación de error.
- **Causa raíz:** El endpoint del backend devuelve un JSON de error `{"error": "Archivo no encontrado"}` con un estado HTTP 200 (OK). Esto hace que `fetch` resuelva exitosamente y el frontend extraiga `d.content` el cual es `undefined`, provocando que el motor Markdown parsee un string vacío. El desajuste de archivo ocurre cuando la DB referencia el ID interno (ej. `resumen__mat__...`) pero el archivo físico se guardó con su nombre embellecido (`suggested_filename`).
- **Regla de prevención:** Si la lógica de guardado incluye "renombrado bonito", asegúrate siempre de que las bases de datos relacionales o los trackers apunten AL NOMBRE FÍSICO REAL en disco (`suggested_filename`), NO a variables intermedias temporales.

### [Fallo de Persistencia 500 (AttributeError: 'dict' object has no attribute 'append')]
- **Síntoma/Log:** `500 Internal Server Error` en peticiones POST al crear registros nuevos.
- **Causa raíz:** Un archivo JSON en disco fue inicializado (posiblemente de forma manual o por un script antiguo) como un diccionario vacío `{}` en lugar de un arreglo vacío `[]`. Cuando el backend (esperando una lista) intentó inyectar un nuevo elemento usando `data.append()`, el motor de Python colapsó.
- **Regla de prevención:** Al manejar almacenes JSON sin esquemas estrictos a nivel de disco, incluye siempre una capa de saneamiento defensiva (`if isinstance(data, dict): data = list(data.values())`) antes de realizar operaciones de lista como append o extend, garantizando resiliencia frente a bases de datos malformadas localmente.

### [Notificaciones Fantasmas (ReferenceError capturado por Promise.catch)]
- **Síntoma/Log:** Al ejecutar una acción exitosa, se muestran dos *Toasts* contradictorios (ej. "Guardado Exitoso" e inmediatamente "Error al Guardar").
- **Causa raíz:** Dentro del bloque `.then()` de una promesa, se llamó a una función UI que no existía o cambió de nombre (ej. `loadEntregables()` en lugar de `updateEntregablesSidebar()`). Esto lanza un `ReferenceError` síncrono que JavaScript intercepta silenciosamente y envía directamente al bloque `.catch(err => ...)` final, disparando la notificación de error reservada para fallos de red.
- **Regla de prevención:** Nunca asumas que el bloque `.catch()` de un `fetch` solo atrapa errores HTTP. Atrapa CUALQUIER excepción ocurrida dentro de `.then()`. Usa nombres de funciones consistentes y, si es posible, envuelve los manejadores de respuesta en un `try-catch` específico o usa el modo asíncrono `async/await` para tener una traza de error clara en consola.

### [Bloqueo CSP de Scripts Externos en Extensiones Manifest V3]
- **Síntoma/Log:** `Loading the script 'https://unpkg.com/lucide@latest' violates the following Content Security Policy directive: "script-src 'self'".` en el popup de la extensión de Chrome.
- **Causa raíz:** Manifest V3 prohíbe por defecto cargar scripts remotos (CDNs externos) en las interfaces de la extensión por motivos de seguridad, rechazando los orígenes cruzados y `unsafe-eval`.
- **Regla de prevención:** En el entorno de la extensión (`popup.html`, `background.js`, `offscreen.html`), NUNCA uses etiquetas `<script src="https://...">` para cargar librerías externas. Descarga siempre los archivos estáticos minimizados (`.min.js`) de forma local en la carpeta de la extensión e inclúyelos con una ruta relativa.

### [FastAPI 422 Unprocessable Content por Missing Nullability]
- **Síntoma/Log:** Endpoint rechaza peticiones con `422 Unprocessable Content` aunque el JSON parezca correcto estructuralmente.
- **Causa raíz:** Un modelo de Pydantic (`BaseModel`) define un campo como `campo: str = None` o `campo: str` esperando que FastAPI admita `null` en el JSON entrante de forma transparente. En versiones recientes de Pydantic, la tipificación estricta bloquea los nulos a menos que el campo esté declarado explícitamente como `Optional[str]` usando el módulo `typing`.
- **Regla de prevención:** SIEMPRE declara `Optional[str]` (o usa la sintaxis `str | None` en Python 3.10+) cuando el valor pueda venir como `null` desde el frontend, incluso si asignas `= None` por defecto.

### [Alucinación Estructural XML en Respuestas de Gemini]
- **Síntoma/Log:** El modelo omite envolver elementos en etiquetas XML cuando se le pide (ej. deja la "Opción A" fuera de `<quiz>`) rompiendo las expresiones regulares en el frontend.
- **Causa raíz:** Los LLMs (Gemini, Claude, GPT) tienen un bias fuerte hacia el texto libre y viñetas (Markdown natural) para estructurar listas, siendo propensos a "olvidar" las etiquetas XML en el primer elemento generado si están procesando la respuesta lógicamente antes de estructurarla.
- **Regla de prevención:** NO luches contra la naturaleza del LLM pidiéndole XML rígido a mitad del chat. Mejor instrúyelo para usar su notación natural Markdown (ej. `A)`, `B)`, `C)`) y emplea *parsers* robustos (RegEx de captura en bloque) del lado del frontend para convertir este formato natural a UI dinámica interactiva.

### [Fallo de Renderizado HTML en Markdown (Codeblock Secuestro)]
- **Síntoma/Log:** En lugar de renderizarse botones interactivos o componentes UI, la pantalla muestra el código HTML puro envuelto en una caja gris de texto.
- **Causa raíz:** Las librerías como `marked.js` siguen la regla oficial de Markdown: cualquier línea que comience con 4 o más espacios en blanco a la izquierda (identación) es convertida incondicionalmente en un bloque de código `<pre><code>`. Al inyectar *template literals* indentados (para mantener el código fuente legible), el parser escapa todas las etiquetas HTML.
- **Regla de prevención:** Cuando inyectes HTML crudo en un pipeline que pasará por un parser Markdown (como la burbuja de chat), asegúrate de usar `String.prototype.trim()` o construir las cadenas HTML en una sola línea plana (0-indentación) sin ningún espacio inicial.
