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
