# Estado Actual del Proyecto — AsistenteClases
> Última actualización: 2026-08-01. Refleja la auditoría y refactorización profunda de la extensión de Chrome (v2.0), con eliminación de módulos legacy (Capturar Tarjeta), UI alineada a Asistente Identidad Visual, y limpieza total de metadatos manuales.

---

## 1. Visión General del Sistema

**AsistenteClases** es una aplicación web local (localhost:8000) que funciona como asistente académico personal. Su flujo central es: **captura multi-modal de clase (audio + capturas de pantalla) → procesamiento por IA → almacenamiento de resúmenes estructurados → consulta mediante chat RAG o tutoría socrática**.

El sistema tiene tres capas:
1. **Backend** — `server.py` (FastAPI, Python, ~900 líneas). Únicamente enrutador: define rutas y delega a servicios.
2. **Frontend** — `frontend/index.html` (SPA monolítica, un solo archivo de ~80 KB).
3. **Extensión de Chrome** — carpeta `extension/`. Captura audio + screenshots y los sube al backend.

**Stack de dependencias** (`requirements.txt`): `fastapi`, `uvicorn`, `python-multipart`, `google-genai`, `python-dotenv`, `tenacity`.

---

## 2. Arquitectura de Módulos

```
AsistenteClases/
│
├── server.py              Enrutador FastAPI puro. Sin lógica de negocio.
├── database.py            Gestor de persistencia con asyncio.Lock por archivo JSON.
├── nlp_engine.py          Motor NLP local (filtro temporal + scoring de relevancia).
│
├── services/
│   ├── audio_service.py   Preprocesamiento FFmpeg (remove_silences, cleanup_temp).
│   ├── llm_service.py     SDK de Gemini: prompts multi-modal, RAG, chat, tutor socrático.
│   └── export_service.py  Guardado Markdown, Obsidian (Adjuntos/), ICS, reglas del profesor.
│
├── frontend/
│   └── index.html         Dashboard web (SPA monolítica).
│
└── extension/             Extensión de Chrome (Manifest V3, v1.2).
    ├── manifest.json      Permisos: tabCapture, scripting, tabs, storage, downloads.
    ├── background.js      Service Worker: orquestador de grabación + silencio + fallbacks.
    ├── offscreen.js       MediaRecorder + IndexedDB (chunks + screenshots) + upload multimodal.
    ├── popup.html         UI "Oscuro Midnight Slate" (CSS extraído), iconos Lucide locales, diseño purgado de tabs obsoletos.
    ├── popup.css          Variables de diseño, utilidades visuales y badges de estado.
    └── popup.js           Lógica UI enfocada exclusivamente en grabación, Disaster Recovery modularizado.
```

### `database.py` — Protección de Concurrencia

Clase `JsonStore` con `asyncio.Lock()`. Cada archivo JSON del sistema tiene su propia instancia:

| Instancia | Archivo |
|---|---|
| `settings_store` | `settings.json` |
| `materias_store` | `materias.json` |

| `meta_store` | `resumenes/resumenes_meta.json` |
| `tarjetas_store` | `resumenes/tarjetas_informativas.json` |
| `cola_store` | `cola_procesamiento.json` |

Toda lectura/escritura en el sistema pasa por `store.read()`, `store.write()` o `store.update(fn)`.
> **Nota de Resiliencia**: `update(updater_fn)` detecta automáticamente si `updater_fn` es síncrona o asíncrona para evitar errores de serialización.

---

## 3. Flujo Principal de Procesamiento — Multi-Modal (Obligatorio)

> **CAMBIO DE PARADIGMA v3.1**: El sistema ya **no admite solo-audio**. Todo upload EXIGE al menos 1 imagen (`HTTP 400` si no). La IA siempre recibe audio + capturas para correlacionarlas.

### Punto de Entrada A — Extensión (Clase en Vivo via Google Meet)

```
[Google Meet en vivo]
  → popup.js: "Iniciar Grabación"
  → background.js: streamId via chrome.tabCapture
  → offscreen.js: MediaRecorder graba audio/webm en chunks de 5s → IndexedDB (store: 'chunks')
  → background.js: gestiona temporizador con `chrome.alarms` (anti-throttling) y envía orden a offscreen.
  → offscreen.js: extrae fotogramas directamente de un `<video>` interno en memoria hacia un `<canvas>` (bypass a `captureVisibleTab`, funciona incluso minimizado) → IndexedDB (store: 'screenshots')
  → [Al detener] offscreen.js: ensambla Blob audio + lee screenshots de IndexedDB
  → FormData: campo 'audio' (Blob webm) + N campos 'imagenes' (Blob jpeg)
  → POST http://localhost:8000/upload

  VALIDACIÓN Y REPARACIÓN ESTRICTA:
  → Si no llegan imágenes → HTTP 400 (regla del sistema)
  → Si tamaño > max_audio_upload_mb → HTTP 413
  → Se repara la duración del archivo `.webm` mediante `ffmpeg -c copy` para reconstruir los metadatos de búsqueda (`Cues`) omitidos por `MediaRecorder`.
  → Guarda en grabaciones/session_YYYYMMDD_HHMMSS[_nombre]/
      ├── meet_TIMESTAMP.webm
      ├── captura_000_t0s.jpg
      ├── captura_001_t300s.jpg
      └── ...

  FALLBACK (si servidor no responde o rechaza la subida):
  → Se emula una carpeta en Descargas: `Descargas/Backups_Clases/[nombre]_[fecha]/`
  → Se descarga directamente vía `chrome.downloads` el audio `.webm` y TODAS las imágenes `.jpg` en esa carpeta.
  → Sin popups molestos y sin librerías externas. Todo 100% nativo y limpio.
```

### Núcleo — Cola de Procesamiento (Worker Asíncrono)

```
POST /api/generate { filename, materia_id, modelo_elegido, session_dir, image_filenames }
  → Encola en cola_store (resumenes/cola_tareas.json)

Worker (asyncio task, polling cada 5s):
  → Lee pending task → marca como 'processing'
  → Resuelve session_dir + image_paths del filesystem
  → materias_store.read() → prompt_personalizado
  → audio_service.remove_silences() → FFmpeg
  → llm_service.generate_summary_from_audio(audio, prompt, modelo, image_paths=[...])
      → Gemini Files API: upload audio → poll ACTIVE
      → Gemini Files API: upload cada imagen → poll ACTIVE
      → generate_content(contents=[audio_file, img1, img2, ..., prompt_maestro])
  → audio_service.cleanup_temp()
  → export_service.save_markdown_and_metadata()
  → export_service.save_to_obsidian(image_paths=[...])  ← copia a vault/Adjuntos/
  → shutil.move(session_dir → papelera_sesiones/) ← mueve directorio multimodal completo a papelera
  → Aplica rotación de papelera (limite max_backups_almacenados definido en preferencias)
```

### Prompt Maestro Multi-Modal (Único, sin bifurcaciones)

La función `_build_summary_prompt()` en `llm_service.py` genera un único prompt que:
1. **CORRELACIÓN AUDIO-IMAGEN (OBLIGATORIO)**: instruye a la IA a correlacionar audio con imágenes y usar `![[captura_000_t0s.jpg]]` en el Markdown resultante.
2. **ANTI-RESUMEN (EXHAUSTIVIDAD TOTAL)**: Prohíbe estrictamente comprimir información o usar frases aglutinantes. Obliga a transcribir y estructurar todos los temas, anécdotas y ejemplos cronológicamente (Estructura PARA, arquetipos de nota, Googleability).
3. Extracción de `tarjetas_informativas` usando **Negative Prompting** estricto para bloquear falsos positivos (actividades en clase, consejos vagos) y `nuevas_reglas_profesor` en bloque JSON.

---

## 4. Estructura de Directorios y Archivos

```
AsistenteClases/
├── grabaciones/            COLA DE ESPERA. Subdirectorios por sesión.
│   └── session_TIMESTAMP/  Una carpeta por clase.
│       ├── meet_*.webm     Audio de la clase.
│       ├── captura_000_*.jpg
│       └── captura_NNN_*.jpg
├── papelera_sesiones/      Sesiones procesadas. Límite: rotación automática.
├── resumenes/              CORAZÓN DE DATOS.
│   ├── *.md                Resúmenes (Markdown con Frontmatter YAML + links ![[imagen]]).
│   ├── resumenes_meta.json Índice central (meta_store con Lock).
│   └── tarjetas_informativas.json (tarjetas_store con Lock).
├── memoria_ia/             Reglas del profesor por materia.
│   └── reglas_{materia}.md
└── exportaciones/          Archivos descargables.
```

### Formatos internos clave

**Resúmenes .md** (Gemini genera `![[imagen.jpg]]` inline donde corresponde):
```markdown
---
tipo: [teoria | cheatsheet | tarea]
estado: [borrador | pendiente]
tags: [tag1, tag2]
---
> [!summary] Resumen Feynman
...
![[captura_001_t300s.jpg]]
...
[[Índice - Semestre actual]]
```

**`resumenes_meta.json`** (dict, clave = nombre archivo .md interno):
```json
{
  "resumen__{id}__meet_FECHA.md": {
    "filename": "Titulo Googleable.md",
    "folder": "02 Recursos/Tema",
    "tags": ["tag1"],
    "condensado": "Primeros 150 chars del contenido...",
    "fecha": "YYYY-MM-DD",
    "resumen": "...contenido completo del markdown..."
  }
}
```

**`settings.json`**:
```json
{
  "obsidian_vault_path": "/ruta/boveda/",
  "max_audio_upload_mb": 500,
  "max_papelera_items": 10,
  "rag_max_docs": 8,
  "nlp_threshold": 1.0,
  "audio_silence_db": -30,
  "default_model": "gemini-3.1-flash-lite"
}
```

**`resumenes/cola_tareas.json`** (array de tareas):
```json
[{
  "id": "uuid",
  "filename": "meet_*.webm",
  "session_name": "session_TIMESTAMP",
  "session_dir": "ruta/absoluta/grabaciones/session_TIMESTAMP/",
  "image_filenames": ["captura_000_t0s.jpg"],
  "materia_id": "uuid",
  "modelo_elegido": "gemini-3.1-flash-lite",
  "estado": "pending|processing|completed|failed",
  "intentos": 0,
  "error_msg": "",
  "fecha_creacion": "YYYY-MM-DD HH:MM:SS"
}]
```

---

## 5. Módulos y Funcionalidades Clave

### 5.1 Gestión de Asignaturas (Materias)

- CRUD completo: `GET/POST /api/materias`, `PUT/DELETE /api/materias/{id}`.
- Modal UI Rediseñado: Panel de 1000px de ancho con diseño grid (2 columnas) para edición espaciosa.
- Parámetros guardados: Nombre, Prompt Técnico y **Temperatura (0.0 a 1.0)** específica por asignatura.
- El `prompt_personalizado` llena el slot `{prompt_usar}` del Prompt Maestro.
- El `materia_id` (UUID) se incrusta en el nombre del .md para filtrado en el chat.
- La `temperatura` inyecta dinámicamente la creatividad/rigurosidad al modelo Gemini por materia.

### 5.2 Memoria Dinámica del Profesor

- Gemini extrae reglas en `nuevas_reglas_profesor` del JSON embebido.
- `export_service.save_teacher_rules()` acumula en `memoria_ia/reglas_{materia}.md` con APPEND.
- En el chat RAG y en el Tutor Socrático, el contenido se inyecta al `system_instruction`.

### 5.3 Generación Asistida de Prompts

- `POST /api/generate-prompt` → `llm_service.generate_prompt_for_materia()`.
- Transforma descripción natural en prompt estructurado [Rol][Contexto][Tarea][Formato].

### 5.4 Integración Obsidian (Multi-Modal)

- `export_service.save_to_obsidian()` escribe el .md en la bóveda configurada.
- Siempre copia las imágenes de la sesión a `vault/Adjuntos/`.
- Los `![[captura_*.jpg]]` en el Markdown generado por Gemini resuelven correctamente en Obsidian.
- Falla silenciosamente si la ruta no está montada.

### 5.5 Tablón de Tarjetas Informativas (Avisos y Entregables)

- **Extracción Automática**: Gemini extrae tareas automáticamente desde el audio/imágenes.
- **Creación Manual**: Endpoint `POST /api/tarjetas` permite inyectar anuncios manualmente desde el modal de un Slot al Día, utilizando un `<input type="date">` nativo para robustez UI.
- **Persistencia y UI**: `export_service.save_tarjetas_informativas()` persiste en `tarjetas_informativas.json`.
- **Integración Transversal**: La eliminación (`DELETE /api/tarjetas/{id}`) actualiza en tiempo real tanto la vista principal, como el panel lateral "Entregables de la semana" y el propio modal del Slot, manteniendo la consistencia de datos sin necesidad de recargar la página.

### 5.6 Extractor Visual de Tareas (Captura Directa) - [Migrado / Frontend-Only]

- Anteriormente existía una pestaña de "Captura" en la extensión. **Ha sido eliminada por completo** para reducir deuda técnica.
- Ahora, la ingesta visual de tareas se maneja o bien subiendo imágenes al backend, o capturando de forma nativa desde la interfaz web (o delegándolo a futuras integraciones).
- `POST /api/extract-task` sigue activo en el backend.

### 5.7 Visor Documental y Búsqueda Instantánea

- Arquitectura de visualización programática (Desacoplada de la UI lateral). `cargarYMostrarDocumento(filename)` recupera asíncronamente el documento por API.
- Búsqueda nativa (Live Filtering) incorporada en el frontend para filtrar instántaneamente documentos por título, fecha y materia sin latencia.
- Sistema de "Deep Linking" entre los slots del "Control Semanal" y la vista de "Resúmenes", apoyado por un interval-polling puramente visual para iluminar el archivo en la barra lateral una vez renderizado.

### 5.8 Resolución de Rutas Multimedia (Seguridad)

- El backend usa helpers (`_media_url`, `_public_session_dir`) para exponer rutas consistentes `/media/session_.../archivo.jpg` sin filtrar rutas locales del sistema operativo.
- El frontend utiliza `session_name` en lugar de `session_dir` para orquestar los endpoints DELETE de imágenes y sesiones, garantizando portabilidad multiplataforma.

### 5.7 Validación Multi-Capa del Upload (OOM-safe)

**Capa 0 — Regla Estricta (sin imágenes → HTTP 400)**:
El sistema rechaza cualquier upload que no incluya al menos 1 imagen. No existe el flujo "solo audio".

**Capa 1 — `Content-Length` header (O(1), cero RAM)**:
Inspecciona el header antes de leer ningún byte. Si supera `max_audio_upload_mb` → **HTTP 413**.

**Capa 2 — Streaming por chunks de 1 MB**:
Lee audio e imágenes en fragmentos. Si la suma supera el límite, borra el `session_dir` completo y devuelve HTTP 413. RAM máxima: 1 MB.

### 5.8 Cola de Procesamiento Asíncrona (Worker)

- `resumenes/cola_tareas.json` persiste entre reinicios del servidor.
- Worker (`asyncio.create_task`) hace polling cada 5 segundos.
- Almacena `session_dir` e `image_filenames` para que el worker resuelva rutas.
- Compatible hacia atrás: detecta tareas legacy (solo filename, sin `session_dir`) y busca el audio en subdirectorios.

### 5.10 Renderizado Matemático Universal (LaTeX)

- Integración de **KaTeX** en el frontend (más rápido que MathJax), enganchado a `marked.js` a través de `marked-katex-extension`.
- La UI renderiza fórmulas nativas tanto en la vista de resúmenes (Markdown), como en el Chat Normal y el Tutor Socrático.
- **Enforcement en el Backend**: Los prompts maestros de *Generación*, *Chat* y *Tutor* imponen el uso de sintaxis estricta `$fórmula$` (inline) y `$$fórmula$$` (bloque). Además, fuerzan el comando `\frac{a}{b}` en lugar de divisiones planas (`a/b`), garantizando notación matemática universitaria estándar.

---

## 6. El Motor de Chat y RAG

### 6.1 Chat Normal (`POST /api/chat`)

Implementado en `llm_service.chat_with_rag()`. RAG ligero sin embeddings.

**Flujo `{ mensaje, materia_id, modelo_elegido }`**:

| Paso | Descripción |
|---|---|
| 1. Filtrado | Por `materia_id` en claves de `resumenes_meta.json` |
| 2. Filtro temporal | `nlp_engine.parse_temporal_filter` (última clase, esta semana, mes) |
| 3. Índice condensado | Lista cronológica de `condensado` por documento |
| 4. Scoring | `nlp_engine.score_relevance`: tags ×3.0, texto ×1.0. Top N (rag_max_docs) |
| 5. Contexto | `contents = [índice, apuntes_top_N, mensaje]` |
| 6. Reglas | Inyecta `memoria_ia/reglas_{materia}.md` al `system_instruction` si existe |

### 6.2 Tutor Socrático (`POST /api/tutor/chat`)

Implementado en `llm_service.tutor_chat_with_rag()`.

- **Reutiliza el mismo motor RAG** (filtrado, scoring, recuperación de apuntes).
- Cambia únicamente el `system_instruction`:
  > *"Eres un profesor riguroso. Haz UNA pregunta a la vez. Si acierta, felicítalo y sube la dificultad. Si se equivoca, guíalo socráticamente con pistas."*
- El frontend mantiene y envía el **historial completo** en cada petición (`historial: [{role, text}]`).
- El RAG se inyecta solo en el primer mensaje del usuario genuino del historial para no contaminar turnos posteriores.
- Se filtran mensajes generativos iniciales de la UI para no romper el formato esperado por la API de Gemini.
- Endpoint en dashboard: pestaña **"Sala de Estudio"** en el sidebar.

---

## 7. La Extensión de Chrome (v2.0)

**Arquitectura Manifest V3 — Multi-Modal:**

| Archivo | Rol |
|---|---|
| `background.js` | Service Worker: orquestador de grabación, alarmas anti-throttling |
| `offscreen.js` | MediaRecorder + IndexedDB (chunks + screenshots) + upload multimodal |
| `popup.html/js/css`| UI minimalista (Oscuro Midnight Slate), control de grabación, y Disaster Recovery |

**Permisos** (`manifest.json` v1.2):
- `activeTab`, `tabCapture`, `offscreen`, `storage`, `alarms`, `downloads`, `scripting`, `tabs`
- `host_permissions`: solo `localhost:8000` y `127.0.0.1:8000`

**Ciclo de estados** (`chrome.storage.local.recordingStates[tabId]`):
`idle → recording ↔ paused → uploading → completed | fallback_saved | error → idle`

### Flujo de Clases de Drive (v1.2 — tabCapture)

La pestaña "Clase Drive" del popup es una **guía estática** (sin lógica JS) que instruye al usuario a:
1. Abrir el video de Drive en el navegador (cuenta institucional)
2. Ir al tab "Grabar Audio" e iniciar grabación (tabCapture)
3. Reproducir el video + capturar pantalla con Alt+S
4. Finalizar → pipeline normal de upload

**Tip integrado**: `document.querySelector('video').playbackRate = 4` para grabar a 4x velocidad.

> **Por qué no se usa descarga directa**: Google Workspace bloquea yt-dlp. La reproducción en el navegador + tabCapture es la única vía funcional.

### Políticas de Seguridad (CSP) e Iconografía
- **Lucide Icons**: Debido a la estricta CSP de Manifest V3, la extensión ya no carga scripts externos (CDNs). La librería `lucide.min.js` se aloja y distribuye localmente en el directorio de la extensión para evitar el bloqueo `script-src` y `unsafe-eval`.
- **Desacople de Metadatos**: Se ha eliminado por completo la opción de definir "Etiquetas Personalizadas" o "Nombres de Sesión" desde el popup. Todo el upload y fallback usa el comodín estándar `sesion_YYYY-MM-DD`, delegando la clasificación semántica de las grabaciones al backend o al frontend web.

### IndexedDB — Schema v2 (`AudioRescueDB`)

| Object Store | Contenido |
|---|---|
| `chunks` | `{ id, tabId, timestamp, chunk: Blob }` — fragmentos de audio de 5s |
| `screenshots` | `{ id, tabId, timestamp, image_base64 \| blob }` — capturas JPEG |

**Capturas automáticas**: cada 5 minutos via `setInterval` en offscreen.js.
**Capturas manuales**: botón cámara en popup (visible solo durante grabación activa) ó `Alt+S`.

### Resiliencia Multi-Modal

| Escenario | Comportamiento |
|---|---|
| Backend falla durante upload | Fallback Suave (offscreen.js): Crea carpeta en Descargas y descarga ahí `.webm` y múltiples `.jpg`. |
| Browser/PC crashea | Disaster Recovery (popup.js): Al arrancar ensambla chunks huérfanos + recupera capturas. Crea carpeta en Descargas y descarga todo de forma idéntica al Fallback Suave. |

**Botones del popup según estado**:

| Estado | Visible |
|---|---|
| `idle` | "Iniciar Grabación" |
| `recording` | Cancelar / Pausar / Finalizar + Botón cámara (captura manual) |

---

## 8. Tabla de Endpoints

| Método | Ruta | Servicio |
|---|---|---|

| GET | `/api/models` | `llm_service.list_available_models` |
| GET/POST | `/api/materias` | `materias_store` |
| PUT/DELETE | `/api/materias/{id}` | `materias_store` |
| GET/PUT | `/api/settings` | `settings_store` |
| GET | `/api/audios` | filesystem (sesiones + legacy) |
| DELETE | `/api/audios/{f}` | filesystem |
| POST | `/upload` | filesystem + validación 400/413 (exige imágenes) |
| POST | `/api/generate` | `cola_store` (encola con session_dir + image_filenames) |
| GET | `/api/cola` | `cola_store` |
| POST | `/api/cola/{id}/retry` | `cola_store` |
| DELETE | `/api/cola/{id}` | `cola_store` |
| GET | `/api/summaries` | filesystem |
| GET/PUT/DELETE | `/api/summaries/{f}` | filesystem + `meta_store` |
| POST | `/api/chat` | `llm_service.chat_with_rag` |
| POST | `/api/tutor/chat` | `llm_service.tutor_chat_with_rag` |
| POST | `/api/generate-prompt` | `llm_service.generate_prompt_for_materia` |
| GET/PUT/DEL | `/api/tarjetas` `/api/tarjetas/{id}` | `tarjetas_store` |
| POST | `/api/extract-task` | `llm_service` + `export_service` |
| GET | `/api/exportaciones/{f}` | filesystem |
| GET | `/info` | health check |
| GET | `/media/*` | static (audios/) |
| GET | `/*` | static (frontend/) |

---

## 9. Módulos del Dashboard (Frontend)

| Pestaña | ID | Descripción |
|---|---|---|
| Documentos | `tab-resumenes` | Biblioteca de apuntes con visor Markdown y editor inline |
| Por Procesar | `tab-audios` | Lista sesiones pendientes, cola de procesamiento, carga de respaldo manual |
| Tutor Virtual | `tab-chat` | Chat RAG con selector de asignatura |
| Tablón de Avisos | `tab-tarjetas` | Grid de tarjetas informativas filtradas por materia |
| Sala de Estudio | `tab-tutor` | Tutor Socrático: selector de materia + botón "Iniciar Simulación" + chat |
| Asignaturas | `tab-materias` | CRUD de asignaturas con generación de prompt asistida |
| Preferencias | `tab-config` | Ruta Obsidian, modelo, límites RAG, límites de papelera |

### 9.1 Control Semanal (Progreso)
- **Generación en Lote (Batching)**: Genera slots de clase de Lunes a Domingo escaneando las asignaturas y sus días de impartición configurados.
- **Memoria de Estado**: Persiste la semana seleccionada en `localStorage` (como `last_progreso_date`) para no perder contexto al recargar la app.
- **Navegación Dinámica**: 
  - **Dropdown de Semanas**: Muestra únicamente las semanas que tienen clases generadas (ej. `14 Ago - 20 Ago`), parseadas dinámicamente desde la base de datos sin necesidad de entidades "Semana" reales en el backend.
  - **Botón "Presente"**: Matemática de distancias para localizar y auto-seleccionar la semana más cercana a la fecha actual, priorizando semanas futuras en caso de empate.
- **Eliminación Modular**: Endpoint `DELETE /api/progreso/eliminar_semana` que permite extirpar una semana entera mediante bulk-delete atómico, protegiendo contra clics accidentales en la interfaz (el botón de basura inteligente se auto-oculta en semanas vacías).

### 9.2 Visor de Imágenes Multi-Modal (Lightbox Nativo)
- Implementación 100% nativa en JavaScript sin dependencias externas (cero jQuery, cero librerías de lightbox).
- **Zoom Continuo e Infinito**: Permite escalar imágenes utilizando una arquitectura híbrida de interacciones (clicks predefinidos `1x, 1.5x, 2x, 3x` y scroll del ratón continuo `+15%`).
- **Puntería Sincronizada (Google Maps style)**: A diferencia del zoom clásico en HTML que expande hacia la esquina (top-left offset), este motor obliga un reflow instantáneo y calcula dinámicamente el scroll basado en el `getBoundingClientRect` para asegurar que el píxel original en el que se hizo clic/scroll permanezca perfectamente bloqueado bajo el cursor del usuario.
- **Drag-to-Pan (Paneo inteligente)**: Bloquea agresivamente los eventos "ghost drag" nativos del navegador (`draggable="false"` + `e.preventDefault()`) permitiendo un arrastre libre y fluido, e interceptando pequeños micromovimientos (`draggedDistance > 10`) para no confundir arrastres con clics accidentales.

### 9.3 Tutor V2 y Pruebas Dinámicas
- **Preguntas Interactivas**: Eliminada la rigidez de las etiquetas XML `<quiz>`. El Frontend detecta automáticamente el patrón Markdown de opción múltiple (`A)`, `B)`, `C)`) y lo transforma de forma transparente en una botonera interactiva que inyecta la selección directamente al prompt.
- **Prevención de Parsing (Codeblock)**: El motor de conversión HTML garantiza que los botones renderizados se envíen al parser `marked.js` en una sola línea y sin sangría izquierda (0-indent), evitando el encapsulamiento accidental en bloques `<pre><code>`.
- **Estabilidad de Pydantic**: Solucionado el error `422 Unprocessable Content` configurando explícitamente `Optional[str]` para datos multimedia nulos.
- **Sincronización Transversal**: Los endpoints asíncronos obligan recargas (`loadProgreso()`) cruzadas para propagar estados visuales en todo el dashboard.
