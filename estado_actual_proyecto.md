# Estado Actual del Proyecto — AsistenteClases
> Última actualización: 2026-07-27. Refleja la arquitectura post-refactor modular (v3).

---

## 1. Visión General del Sistema

**AsistenteClases** es una aplicación web local (localhost:8000) que funciona como asistente académico personal. Su flujo central es: **captura de audio de clase → procesamiento por IA → almacenamiento de resúmenes estructurados → consulta mediante chat**.

El sistema tiene tres capas:
1. **Backend** — `server.py` (FastAPI, Python, ~340 líneas). Únicamente enrutador: define rutas y delega a servicios.
2. **Frontend** — `frontend/index.html` (SPA monolítica, un solo archivo de ~74 KB).
3. **Extensión de Chrome** — carpeta `extension/`. Captura audio de la pestaña y lo sube al backend.

**Stack de dependencias** (`requirements.txt`): `fastapi`, `uvicorn`, `python-multipart`, `google-genai`, `python-dotenv`, `tenacity`, `icalendar`, `yt-dlp`.

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
│   ├── llm_service.py     Todo el SDK de Gemini: prompts, RAG, chat, stats, image tasks.
│   └── export_service.py  Guardado Markdown, Obsidian, ICS, reglas del profesor.
│
├── frontend/
│   └── index.html         Dashboard web (SPA monolítica).
│
└── extension/             Extensión de Chrome (Manifest V3).
    ├── manifest.json
    ├── background.js      Service Worker orquestador.
    ├── offscreen.js       MediaRecorder + upload al servidor.
    ├── popup.html
    └── popup.js
```

### `database.py` — Protección de Concurrencia

Clase `JsonStore` con `asyncio.Lock()`. Cada archivo JSON del sistema tiene su propia instancia:

| Instancia | Archivo |
|---|---|
| `settings_store` | `settings.json` |
| `materias_store` | `materias.json` |
| `stats_store` | `stats.json` |
| `meta_store` | `resumenes/resumenes_meta.json` |
| `tareas_store` | `resumenes/tareas_meta.json` |

Toda lectura/escritura en el sistema pasa por `store.read()`, `store.write()` o `store.update(fn)`. Ningún endpoint hace `open('archivo.json', 'w')` directamente.

---

## 3. Flujo Principal de Procesamiento (El Pipeline)

### Punto de Entrada A — Extensión de Chrome (Ruta Principal)

```
[Usuario abre clase en Google Meet]
  → popup.js: presiona "Grabar"
  → background.js: obtiene streamId via chrome.tabCapture
  → offscreen.js: MediaRecorder graba audio/webm en memoria
  → [Al detener] offscreen.js ensambla Blob → uploadAudio()
  → POST http://localhost:8000/upload (multipart: audio + custom_name opcional)

  VALIDACIÓN DE TAMAÑO (Fase 4):
  → server.py lee el contenido en memoria
  → Si tamaño > max_audio_upload_mb (settings) → HTTP 413
  → Si OK → guarda en audios/meet_YYYYMMDD_HHMMSS[_nombre].webm

  FALLBACK (si servidor no responde):
  → saveAudioLocallyFallback() → base64 en chunks de 5 MB → chrome.downloads
  → Descarga local en Backups_Clases/ (carpeta configurable)
```

### Núcleo — Generación del Resumen (POST /api/generate)

```
{ filename, materia_id, modelo_elegido }
  → server.py delega a:
    1. materias_store.read() → obtiene prompt_personalizado de la materia
    2. audio_service.remove_silences(filepath) → FFmpeg silenceremove
    3. llm_service.generate_summary_from_audio(upload_path, prompt, modelo)
       → Gemini File API upload → poll hasta ACTIVE → generate_content (retry x5)
    4. audio_service.cleanup_temp() → borra el .webm temporal
  → Retorna { content: texto_con_JSON_embebido, stats }
```

### Etapa Final — Guardado (POST /api/save)

```
{ filename, content, materia_id }
  → server.py:
    1. llm_service.extract_json_block(content) → separa JSON del Markdown
    2. Extrae tags del Frontmatter YAML con regex
    3. export_service.save_markdown_and_metadata(...) → .md local + meta_store.update()
    4. export_service.save_to_obsidian(...) → copia a bóveda si ruta existe
    5. Si json_data["calendario"]: export_service.generate_ics_and_save_tasks(...)
       → .ics en exportaciones/ + tareas_store.update()
    6. Si json_data["nuevas_reglas_profesor"]: export_service.save_teacher_rules(...)
       → append en memoria_ia/reglas_{materia}.md
    7. shutil.move(audio → papelera_audios/) + retención de 10 archivos
  → Retorna { message, md_filename, anki_file: null, ics_file }
```

> **Nota:** `anki_file` siempre es `null`. La generación de Anki fue eliminada completamente.

---

## 4. Estructura de Directorios y Archivos

```
AsistenteClases/
├── audios/                COLA DE ESPERA. .webm pendientes. Archivos temp_*.webm
│                          de vida muy corta durante el procesado.
├── papelera_audios/       Audios procesados. Límite: 10 archivos.
├── resumenes/             CORAZÓN DE DATOS.
│   ├── *.md               Resúmenes (Markdown con Frontmatter YAML).
│   ├── resumenes_meta.json Índice central (gestionado por meta_store con Lock).
│   └── tareas_meta.json   Tareas/eventos (gestionado por tareas_store con Lock).
├── memoria_ia/            Reglas del profesor por materia.
│   └── reglas_{materia}.md (append al detectar nuevas_reglas_profesor)
└── exportaciones/         Archivos descargables.
    └── *.ics              Calendarios iCalendar.
```

### Formatos internos clave

**Resúmenes .md** (el bloque JSON de Gemini se elimina antes de guardar):
```markdown
---
tipo: [teoria | cheatsheet | tarea]
estado: [borrador | pendiente]
tags: [tag1, tag2]
---
[Cuerpo con callouts de Obsidian]
[[Enlace MOC relacionado]]
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
  "default_model": "gemini-3.1-flash-lite"
}
```

**`tareas_meta.json`** (array):
```json
[{ "titulo":"...", "fecha_YYYY_MM_DD":"...", "descripcion":"...",
   "id":"uuid", "origen":"archivo.md", "completada": false }]
```

---

## 5. Módulos y Funcionalidades Clave

### 5.1 Gestión de Asignaturas (Materias)

- CRUD completo: `GET/POST /api/materias`, `PUT/DELETE /api/materias/{id}`.
- Persistencia via `materias_store` con Lock.
- El `prompt_personalizado` llena el slot `{prompt_usar}` del prompt de Gemini.
- El `materia_id` (UUID) se incrusta en el nombre del .md para filtrado en el chat.
- **4 materias activas:** Sistemas Operativos 2, Modelado y Simulación 1, Ingeniería Económica 1, Ética Profesional.

### 5.2 Memoria Dinámica del Profesor

- Gemini extrae reglas en `nuevas_reglas_profesor` del JSON embebido.
- `export_service.save_teacher_rules()` acumula en `memoria_ia/reglas_{materia}.md` con APPEND.
- Directorio `memoria_ia/` **actualmente vacío** (ninguna clase ha generado reglas aún).
- En el chat, `llm_service.chat_with_rag()` lee ese archivo e inyecta su contenido al `system_instruction`.

### 5.3 Generación Asistida de Prompts

- `POST /api/generate-prompt` → `llm_service.generate_prompt_for_materia()`.
- Transforma descripción natural en prompt estructurado [Rol][Contexto][Tarea][Formato].

### 5.4 Integración Obsidian

- `export_service.save_to_obsidian()` escribe directamente el .md en la bóveda configurada.
- La carpeta destino y el nombre los propone Gemini en el JSON (estructura PARA: `02 Recursos/Tema`).
- Falla silenciosamente si la ruta no está montada.

### 5.5 Integración Calendario (ICS)

- `export_service.generate_ics_and_save_tasks()` genera `.ics` y persiste en `tareas_meta.json`.
- El dashboard permite marcar tareas como completadas (`PUT /api/tareas/{id}`).

### 5.6 Extractor Visual de Tareas (Captura de Pantalla)

- Pestaña "Captura" de la extensión → `chrome.tabs.captureVisibleTab()` → JPEG.
- `POST /api/extract-task { image_base64, modelo_elegido }` → `llm_service.extract_task_from_image()`.
- Usa el modelo enviado en el payload, con fallback a `settings.default_model`.
- Resultado: .md en `resumenes/`, copia en Obsidian, entradas en `tareas_meta.json`.

### 5.7 Validación de Tamaño de Upload (OOM-safe)

El endpoint `POST /upload` implementa **dos capas de defensa** para nunca cargar un archivo gigante en RAM:

**Capa 1 — `Content-Length` header (O(1), cero RAM):**
Antes de leer ningún byte, se inspecciona el header `Content-Length` de la request. Si el cliente declara un tamaño superior a `max_audio_upload_mb`, se devuelve **HTTP 413** inmediatamente sin tocar el cuerpo de la petición.

**Capa 2 — Streaming por chunks de 1 MB (fallback para transferencias chunked):**
Cubre el caso en que el cliente no envía `Content-Length` (ej. transferencias chunked). El archivo se escribe en disco en fragmentos de 1 MB. Si la suma acumulada supera el límite, se interrumpe la escritura, **se borra el archivo parcial** del disco y se devuelve HTTP 413. La RAM máxima consumida en cualquier punto es exactamente 1 MB.

Si ocurre cualquier otro error de IO durante la escritura, el archivo parcial también se borra antes de responder HTTP 500.

### 5.8 Estadísticas de Uso

- `llm_service.update_stats()` usa `stats_store.update()` (con Lock) en cada llamada a Gemini.
- Se resetea automáticamente si la fecha del archivo no coincide con hoy.
- `GET /api/stats` expone los contadores del día.

---

## 6. El Motor de Chat y RAG

Implementado en `llm_service.chat_with_rag()`. RAG ligero sin embeddings.

### Flujo `POST /api/chat { mensaje, materia_id, modelo_elegido }`

**Paso 1 — Filtrado por materia** (desde `meta_store`):
| materia_id | Documentos seleccionados |
|---|---|
| `"todas"` | Todos los registros en resumenes_meta.json |
| `"default"` | Registros con `__default__` en clave, o sin `__` |
| `{uuid}` | Registros con `__{uuid}__` en clave |

**Paso 2 — Filtro temporal** (`nlp_engine.parse_temporal_filter`):
| Expresión | Rango |
|---|---|
| "clase pasada" / "última clase" | Últimos 7 días |
| "esta semana" | Desde el lunes de la semana actual |
| "semana pasada" | Semana anterior completa |
| Nombre de mes en español | Todo ese mes del año actual |
| Sin expresión temporal | Sin filtro |

**Paso 3 — Índice condensado** (lista cronológica de `condensado` por documento).

**Paso 4 — Scoring** (`nlp_engine.score_relevance`):
```
score += coincidencia_tags * 3.0
score += coincidencia_texto * 1.0
threshold = 1.0, máximo N documentos en contexto (definido por settings.rag_max_docs, default 8)
```

**Paso 5 — Contexto a Gemini**:
```python
contents = [
  "ÍNDICE CONDENSADO:\n" + indice,
  "APUNTES COMPLETOS:\n" + resumenes_completos,  # top N (rag_max_docs)
  mensaje_usuario
]
```

**Paso 6 — Reglas del profesor**: si `memoria_ia/reglas_{materia}.md` existe, se inyecta al `system_instruction`.

---

## 7. La Extensión de Chrome

**Arquitectura Manifest V3:**

| Archivo | Rol |
|---|---|
| `background.js` | Service Worker. Orquestador de estados, timers y alarmas. |
| `offscreen.js` | MediaRecorder, AudioContext, upload + fallback local. |
| `popup.html/js` | UI: pestañas "Grabar Audio", "Captura de Tarea", panel Ajustes. |

**Ciclo de estados** (`chrome.storage.local.recordingStates[tabId]`):
`idle → recording ↔ paused → uploading → completed | fallback_saved | error → idle`

**Resiliencia Absoluta (Motor de Fallback)**:
- `offscreen.js` graba en fragmentos de 5 segundos (`MediaRecorder.start(5000)`) y guarda todo en `IndexedDB` (`AudioRescueDB`).
- Si el backend falla, `offscreen.js` lee la base de datos, genera un Blob URL, y el background ejecuta la descarga local (sin Base64).
- Si la PC o navegador crashea, `popup.js` ejecuta un **Disaster Recovery** en su arranque: busca fragmentos huérfanos en `IndexedDB`, los ensambla y gatilla la descarga de emergencia, garantizando cero pérdida de clases.

**Protección de Pestaña (Anti-Cierres)**:
Al iniciar grabación, `background.js` inyecta un listener `beforeunload` en la pestaña activa para mostrar un modal de confirmación si el usuario intenta cerrarla por accidente. Se remueve al detener la grabación.

**Auto-Stop por silencio**: detecta `tab.audible` vía `chrome.tabs.onUpdated`. Crea alarma con delay configurable. Si el audio vuelve, cancela la alarma.

**Ghost Mode**: `gainNode.gain.value = 0` → graba sin reproducir por altavoces.

**Configuración de backup local**: carpeta destino (`Backups_Clases/` por defecto) y opción "Guardar como" en cada backup.

---

## 8. Tabla de Endpoints

| Método | Ruta | Servicio |
|---|---|---|
| GET | `/api/stats` | `llm_service.get_or_reset_stats` |
| GET | `/api/models` | `llm_service.list_available_models` |
| GET/POST | `/api/materias` | `materias_store` |
| PUT/DELETE | `/api/materias/{id}` | `materias_store` |
| GET/PUT | `/api/settings` | `settings_store` |
| GET/DELETE | `/api/audios` `/api/audios/{f}` | filesystem directo |
| POST | `/upload` | filesystem + validación 413 |
| POST | `/api/generate` | `audio_service` + `llm_service` |
| POST | `/api/save` | `llm_service` + `export_service` + stores |
| GET | `/api/summaries` | filesystem |
| GET/PUT/DELETE | `/api/summaries/{f}` | filesystem + `meta_store` |
| POST | `/api/chat` | `llm_service.chat_with_rag` |
| POST | `/api/generate-prompt` | `llm_service.generate_prompt_for_materia` |
| GET/PUT | `/api/tareas` `/api/tareas/{id}` | `tareas_store` |
| POST | `/api/extract-task` | `llm_service` + `export_service` |
| GET | `/api/exportaciones/{f}` | filesystem |
| GET | `/info` | health check |
| GET | `/media/*` | static (audios/) |
| GET | `/*` | static (frontend/) |

---

## 9. Cambios Respecto a la Versión Anterior (v2 → v3)

| Área | Antes (v2) | Ahora (v3) |
|---|---|---|
| Arquitectura | Monolítico (~950 líneas en server.py) | Modular: server + database + 3 services |
| Concurrencia | `open('file.json', 'w')` sin protección | `asyncio.Lock()` por archivo JSON |
| Anki | Generaba `.apkg` con IDs aleatorios (bug) | **Eliminado completamente** |
| `requirements.txt` | Incluía `genanki`, omitía `tenacity` | Corregido |
| Upload tamaño | `max_audio_upload_mb` guardado pero ignorado | Validación activa → HTTP 413 |
| `condensado` en meta | Siempre cadena fija (bug) | Primeros 150 chars del contenido real |
| `enable_anki` | Campo en settings y UI | **Eliminado** |
