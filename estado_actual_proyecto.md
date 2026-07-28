# Estado Actual del Proyecto — AsistenteClases
> Última actualización: 2026-07-28. Refleja la arquitectura post-refactor modular (v3) con pipeline multi-modal completo y limpieza de Drive v1.2.

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
    ├── popup.html         UI con tabs Grabar / Clase Drive (guía) / Capturar Tarjeta / Ajustes.
    └── popup.js           UI controller para grabación, capturas y extractor de tareas.
```

### `database.py` — Protección de Concurrencia

Clase `JsonStore` con `asyncio.Lock()`. Cada archivo JSON del sistema tiene su propia instancia:

| Instancia | Archivo |
|---|---|
| `settings_store` | `settings.json` |
| `materias_store` | `materias.json` |
| `stats_store` | `stats.json` |
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
  → Cada 5 min ó Alt+S (manual): captureVisibleTab → IndexedDB (store: 'screenshots')
  → [Al detener] offscreen.js: ensambla Blob audio + lee screenshots de IndexedDB
  → FormData: campo 'audio' (Blob webm) + N campos 'imagenes' (Blob jpeg)
  → POST http://localhost:8000/upload

  VALIDACIÓN ESTRICTA:
  → Si no llegan imágenes → HTTP 400 (regla del sistema)
  → Si tamaño > max_audio_upload_mb → HTTP 413
  → Guarda en audios/session_YYYYMMDD_HHMMSS[_nombre]/
      ├── meet_TIMESTAMP.webm
      ├── captura_000_t0s.jpg
      ├── captura_001_t300s.jpg
      └── ...

  FALLBACK (si servidor no responde):
  → Intenta generar .zip (audio + imágenes) via JSZip
  → Si falla: descarga audio .webm + capturas como JSON base64
```

### Punto de Entrada B — Clase Grabada en Google Drive (mismo flujo de tabCapture)

> **DECISIÓN DE DISEÑO v1.2**: Google Workspace con `preventDownload` bloquea toda descarga programática (yt-dlp, fetch, chrome.downloads con URLs de videoplayback). La solución es usar **el mismo mecanismo de tabCapture** que funciona para clases en vivo: el usuario reproduce el video de Drive en el navegador y graba el audio de la pestaña.

```
[Usuario abre video de clase grabada en Google Drive]
  → Reproduce el video en el navegador (la cuenta institucional tiene permiso de reproducción)
  → Abre la extensión → Tab "Grabar Audio" → "Iniciar Grabación"
  → tabCapture captura todo el audio que suena en la pestaña
  → Alt+S o botón cámara para capturar momentos clave
  → Al terminar → "Finalizar" → pipeline idéntico al Punto de Entrada A

  TIP DE VELOCIDAD:
  → Consola del navegador: document.querySelector('video').playbackRate = 4
  → Una clase de 2h se graba en ~30 min. El audio queda intacto para la IA.
```

La pestaña "Clase Drive" en el popup es una **guía paso a paso** (sin lógica JS) que explica este flujo.

### Núcleo — Cola de Procesamiento (Worker Asíncrono)

```
POST /api/generate { filename, materia_id, modelo_elegido, session_dir, image_filenames }
  → Encola en cola_store (cola_procesamiento.json)

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
  → Limpia session_dir completo del filesystem
  → shutil.move(audio → papelera_audios/) → retención 10 archivos
```

### Prompt Maestro Multi-Modal (Único, sin bifurcaciones)

La función `_build_summary_prompt()` en `llm_service.py` genera un único prompt que:
1. **CORRELACIÓN AUDIO-IMAGEN (OBLIGATORIO)**: instruye a la IA a correlacionar audio con imágenes y usar `![[captura_000_t0s.jpg]]` en el Markdown resultante.
2. Estructura PARA de Obsidian, arquetipos de nota, Googleability, exhaustividad proporcional.
3. Extracción de `tarjetas_informativas` y `nuevas_reglas_profesor` en bloque JSON.

---

## 4. Estructura de Directorios y Archivos

```
AsistenteClases/
├── audios/                 COLA DE ESPERA. Subdirectorios por sesión.
│   └── session_TIMESTAMP/  Una carpeta por clase.
│       ├── meet_*.webm     Audio de la clase.
│       ├── captura_000_*.jpg
│       └── captura_NNN_*.jpg
├── papelera_audios/        Audios procesados. Límite: 10 archivos.
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
  "browser_cookie_source": "brave",
  "max_audio_upload_mb": 500,
  "rag_max_docs": 8,
  "default_model": "gemini-3.1-flash-lite"
}
```

**`cola_procesamiento.json`** (array de tareas):
```json
[{
  "id": "uuid",
  "filename": "meet_*.webm",
  "session_dir": "audios/session_TIMESTAMP/",
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
- El `prompt_personalizado` llena el slot `{prompt_usar}` del Prompt Maestro.
- El `materia_id` (UUID) se incrusta en el nombre del .md para filtrado en el chat.

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

### 5.5 Tablón de Tarjetas Informativas

- `export_service.save_tarjetas_informativas()` persiste en `tarjetas_informativas.json`.
- Dashboard: `PUT /api/tarjetas/{id}` (solo `nota_personal`) y `DELETE`.

### 5.6 Extractor Visual de Tareas (Captura Directa)

- Pestaña "Captura" → `chrome.tabs.captureVisibleTab()` → JPEG.
- `POST /api/extract-task { image_base64 }` → `llm_service.extract_task_from_image()`.
- Crea .md + tarjeta informativa + copia a Obsidian.

### 5.7 Validación Multi-Capa del Upload (OOM-safe)

**Capa 0 — Regla Estricta (sin imágenes → HTTP 400)**:
El sistema rechaza cualquier upload que no incluya al menos 1 imagen. No existe el flujo "solo audio".

**Capa 1 — `Content-Length` header (O(1), cero RAM)**:
Inspecciona el header antes de leer ningún byte. Si supera `max_audio_upload_mb` → **HTTP 413**.

**Capa 2 — Streaming por chunks de 1 MB**:
Lee audio e imágenes en fragmentos. Si la suma supera el límite, borra el `session_dir` completo y devuelve HTTP 413. RAM máxima: 1 MB.

### 5.8 Cola de Procesamiento Asíncrona (Worker)

- `cola_procesamiento.json` persiste entre reinicios del servidor.
- Worker (`asyncio.create_task`) hace polling cada 5 segundos.
- Almacena `session_dir` e `image_filenames` para que el worker resuelva rutas.
- Compatible hacia atrás: detecta tareas legacy (solo filename, sin `session_dir`) y busca el audio en subdirectorios.

### 5.9 Estadísticas de Uso

- `llm_service.update_stats()` usa `stats_store.update()` (con Lock) en cada llamada.
- Se resetea automáticamente si la fecha no coincide con hoy.

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
- El RAG se inyecta solo en el primer mensaje del historial para no contaminar turnos posteriores.
- Endpoint en dashboard: pestaña **"Sala de Estudio"** en el sidebar.

---

## 7. La Extensión de Chrome (v1.2)

**Arquitectura Manifest V3 — Multi-Modal:**

| Archivo | Rol |
|---|---|
| `background.js` | Service Worker: orquestador de grabación, silencio, fallbacks |
| `offscreen.js` | MediaRecorder + IndexedDB (chunks + screenshots) + upload multimodal |
| `popup.html/js` | Grabar Audio / Clase Drive (guía) / Capturar Tarjeta / Ajustes |

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

> **Por qué no se usa descarga directa**: Google Workspace con `preventDownload` bloquea yt-dlp (con cookies), fetch a URLs de videoplayback (DASH segmentado), y chrome.downloads. La reproducción en el navegador + tabCapture es la única vía funcional.

### IndexedDB — Schema v2 (`AudioRescueDB`)

| Object Store | Contenido |
|---|---|
| `chunks` | `{ id, tabId, timestamp, customName, chunk: Blob }` — fragmentos de audio de 5s |
| `screenshots` | `{ id, tabId, timestamp, image_base64 \| blob }` — capturas JPEG |

**Capturas automáticas**: cada 5 minutos via `setInterval` en offscreen.js.
**Capturas manuales**: botón cámara en popup (visible solo durante grabación activa) ó `Alt+S`.

### Resiliencia Multi-Modal

| Escenario | Comportamiento |
|---|---|
| Backend falla durante upload | Intenta ZIP (audio + imágenes) via JSZip; si no disponible → audio .webm + JSON base64 |
| Browser/PC crashea | Disaster Recovery en arranque de popup: ensambla chunks huérfanos + limpia screenshots huérfanos |

**Botones del popup según estado**:

| Estado | Visible |
|---|---|
| `idle` | "Iniciar Grabación" |
| `recording` | Cancelar / Pausar / Finalizar + Botón cámara (captura manual) |

---

## 8. Tabla de Endpoints

| Método | Ruta | Servicio |
|---|---|---|
| GET | `/api/stats` | `llm_service.get_or_reset_stats` |
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
| Preferencias | `tab-config` | Ruta Obsidian, modelo, límites RAG, browser cookie |

---

## 10. Historial de Cambios

### Sesión 2026-07-28 — Limpieza Drive v1.2

| Área | Cambio |
|---|---|
| **Decisión arquitectónica** | Eliminada la extracción directa de audio desde Drive. Google Workspace con `preventDownload` bloquea toda descarga programática. El flujo para clases grabadas usa tabCapture (reproducir video + grabar audio de la pestaña). |
| `extension/manifest.json` | v1.2: eliminados permisos `webRequest`, `webNavigation`, `cookies`. `host_permissions` acotado a `localhost:8000` y `127.0.0.1:8000` (antes era `<all_urls>`). |
| `extension/background.js` | Eliminado: espía de red `chrome.webRequest.onBeforeRequest`, `doDriveExtraction()`, handlers `GET_DRIVE_AUDIO_URL`, `DRIVE_EXTRACT_DONE`, `EXTRACT_DRIVE_AUDIO`. De 542 → 236 líneas. |
| `extension/popup.html` | Tab "Clase Drive" convertido de formulario interactivo (inputs + botones + screenshots) a guía estática de 4 pasos con tip de velocidad 4x. Eliminados: `driveExtractBtn`, `driveScreenshotBtn`, `driveDurationInput`, `driveResetBtn`. |
| `extension/popup.js` | Eliminado: `extractDriveClass()`, `uploadDriveSession()`, `readAllScreenshotsFromDB()`, `updateDriveUI()`, `addScreenshotToDB()`, listeners de `isExtractingDrive`, `DRIVE_EXTRACT_SUCCESS`. De 828 → 478 líneas. |
| `server.py` | Eliminados 4 endpoints: `POST /api/upload-drive/init`, `/chunk/{s}`, `/import-local/{s}`, `/finish/{s}`. Eliminado `import requests`. De 1043 → 899 líneas. |
| `requirements.txt` | Eliminada dependencia `yt-dlp`. |
| Archivos eliminados | `test_drive_url.js`, `test_download.py`, `patch_server.py` — scripts de test/parcheo ya obsoletos. |

### Sesión 2026-07-27 — Pipeline Multi-Modal v3.1

| Área | Cambio |
|---|---|
| **Paradigma** | Sistema pasa a ser estrictamente multi-modal. No existe flujo solo-audio. |
| `extension/offscreen.js` | IndexedDB v2 (stores: chunks + screenshots). Time-lapse 5min. Captura manual. Upload FormData multi-archivo. Fallback ZIP/JSON. |
| `server.py /upload` | HTTP 400 si no llegan imágenes. Guarda sesión en `audios/session_TIMESTAMP/`. |
| `server.py /api/generate` | `GenerateRequest` acepta `session_dir` + `image_filenames`. Worker resuelve rutas y pasa `image_paths` al LLM. Limpia `session_dir` completo tras completar. |
| `server.py /api/audios` | Lista sesiones (subdirectorios) y archivos legacy. Expone `image_count`. |
| `server.py` | Modificado endpoint `DELETE /api/audios/{filename:path}` para soportar borrado de archivos dentro de `session_dir`. Nuevo modelo `TutorChatRequest`. Nuevo endpoint `POST /api/tutor/chat`. |
| `services/llm_service.py` | Prompt Maestro unificado (sin `has_images`). Regla 1 exige correlación audio-imagen y `![[img]]`. `generate_summary_from_audio` sube imágenes a Gemini Files API. Nueva función `tutor_chat_with_rag` (RAG reutilizado, system_instruction socrático, historial `types.Content`). |
| `services/export_service.py` | `save_to_obsidian` siempre copia imágenes a `vault/Adjuntos/`. Sin condicional. |
| `frontend/index.html` | Solucionados errores 404 de "Audios Pendientes" mapeando correctamente el `session_dir` en el backend, actualizando el `src` de los reproductores y pasando metadata de sesión completa en `processAudio()`. Nueva pestaña "Sala de Estudio" (Tutor Socrático). Nav con icono `graduation-cap`. JS: `tutorHistoryState`, `iniciarSimulacionTutor()`, `handleTutorSubmit()`. `loadMaterias()` pobla selector del tutor. |
