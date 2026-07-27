# Estado Actual del Proyecto — AsistenteClases
> Generado el 2026-07-27 mediante auditoría directa del código fuente. Cero alucinaciones.

---

## 1. Visión General del Sistema

**AsistenteClases** es una aplicación web local (localhost:8000) que funciona como asistente académico personal. Su flujo central es: **captura de audio de clase → procesamiento por IA → almacenamiento de resúmenes estructurados → consulta mediante chat**.

El sistema tiene tres capas:
1. **Backend** — `server.py` (FastAPI, Python, 996 líneas). Toda la lógica de negocio, llamadas a Gemini y manejo de archivos.
2. **Frontend** — `frontend/index.html` (SPA monolítica, un solo archivo de 74 KB).
3. **Extensión de Chrome** — carpeta `extension/`. Captura el audio de la pestaña y lo sube al backend.

**Dependencias reales** (`requirements.txt`): `fastapi`, `uvicorn`, `python-multipart`, `google-genai`, `python-dotenv`, `genanki`, `icalendar`, `yt-dlp`.
> `tenacity` se usa en código para reintentos pero **no está en requirements.txt**.

---

## 2. Flujo Principal de Procesamiento (El Pipeline)

El pipeline tiene **dos puntos de entrada** y un núcleo compartido.

### Punto de Entrada A — Extensión de Chrome (Ruta Principal)

```
[Usuario abre clase en Google Meet]
  → popup.js: usuario presiona "Grabar"
  → background.js: obtiene streamId via chrome.tabCapture
  → offscreen.js: MediaRecorder graba audio/webm en memoria (array de Blobs)
  → [Al detener] offscreen.js ensambla Blob y llama uploadAudio()
  → POST http://localhost:8000/upload (multipart: audio + custom_name opcional)
  → server.py /upload: guarda en audios/ como meet_YYYYMMDD_HHMMSS[_nombre].webm
  → El audio queda en audios/ esperando procesamiento manual por el usuario
```

**Fallback si el servidor no responde:**
```
offscreen.js: fetch() falla → saveAudioLocallyFallback()
  → convierte Blob a base64
  → divide en chunks de 5 MB (límite IPC de Chrome)
  → background.js ensambla chunks → chrome.downloads.download()
  → Se descarga .webm a la carpeta local configurada (default: Backups_Clases/)
```

### Punto de Entrada B — Descarga desde Google Drive

```
POST /api/download-drive { url }
  → yt-dlp intenta descarga con cookies del browser (Brave por defecto)
  → Reintenta con authuser=0, 1, 2 si recibe 403
  → Si exitoso: archivo en audios/ + BackgroundTask que llama generate_summary()
    inmediatamente (materia_id="default", modelo=gemini-3.5-flash)
  → Si 403: error HTTP 403 con instrucción de usar la extensión
  → Único flujo donde la generación ocurre automáticamente sin revisión humana
```

### Núcleo — Generación del Resumen (POST /api/generate)

```
{ filename, materia_id, modelo_elegido }

1. PROMPT:
   Si materia_id != "default" → busca en materias.json → usa prompt_personalizado
   Si materia_id == "default" → prompt_usar = "" (vacío)
   Mezcla: Rol PKM + Fecha actual + Contexto de materia + Reglas Obsidian PARA
   Solicita bloque JSON al final: filename, folder, anki_cards, calendario, nuevas_reglas_profesor

2. PRE-PROCESAMIENTO (FFmpeg):
   Elimina silencios > 2s a -30dB → guarda como audios/temp_{filename}
   Si FFmpeg falla → usa archivo original sin preprocesar
   El temp se borra inmediatamente después del upload a Gemini

3. UPLOAD A GEMINI FILE API:
   client.files.upload(file=..., mime_type='audio/webm')
   Poll cada 3s hasta state != "PROCESSING"
   Si state == "FAILED" → lanza excepción

4. LLAMADA A GEMINI (con reintento tenacity):
   stop_after_attempt(5), wait_exponential(multiplier=2, min=5, max=60)
   max_output_tokens: 8192
   Si respuesta < 50 chars → fuerza reintento
   Actualiza stats.json

5. RETORNO al frontend:
   { content: texto_completo_con_JSON_embebido, stats: ... }
   El usuario revisa el contenido (Human-in-the-Loop) y aprueba
```

### Etapa Final — Guardado (POST /api/save)

```
{ filename, content, materia_id }

1. NAMING:
   meet_YYYYMMDD_HHMMSS.webm → resumen__{materia_id}__meet_YYYYMMDD_HHMMSS.md

2. EXTRACCIÓN DEL BLOQUE JSON:
   Regex: ```json ... ``` (fallback: busca {"filename":... sin backticks)
   El bloque JSON se elimina del texto antes de guardarlo

3. GUARDADO EN OBSIDIAN (si ruta configurada):
   Crea carpeta vault/suggested_folder/ y escribe suggested_filename
   Si falla → silencioso (print + continúa)

4. GUARDADO LOCAL:
   resumenes/{suggested_filename} — Markdown limpio
   resumenes/resumenes_meta.json — actualizado con metadata

5. ANKI (si enable_anki=True y hay anki_cards):
   genanki → exportaciones/{base}.apkg
   IDs de deck aleatorios en cada guardado (no persistentes)

6. CALENDARIO ICS (si hay "calendario" en JSON):
   exportaciones/{base}.ics (eventos de día completo, sin hora)
   + agrega entradas a resumenes/tareas_meta.json

7. REGLAS DEL PROFESOR (si hay "nuevas_reglas_profesor"):
   APPEND en memoria_ia/reglas_{materia_name}.md
   Formato: ### Regla extraída el YYYY-MM-DD: {tema}\n{metodo}\n---

8. MOVER AUDIO A PAPELERA:
   audios/{filename} → papelera_audios/{filename}
   Retención máxima: 10 archivos (los más viejos se eliminan)
```

---

## 3. Estructura de Directorios y Archivos

```
AsistenteClases/
├── server.py              Backend FastAPI (996 líneas, único archivo de lógica)
├── nlp_engine.py          Motor NLP local para el RAG
├── materias.json          BD de asignaturas (array JSON)
├── settings.json          Configuración global
├── stats.json             Uso de modelos por día (se resetea diario)
│
├── audios/                COLA DE ESPERA. .webm pendientes. Vacía en runtime normal.
│                          Contiene temp_*.webm durante el procesado (vida muy corta)
│
├── papelera_audios/       Audios ya procesados. Límite: 10 archivos.
│
├── resumenes/             CORAZÓN DE DATOS
│   ├── *.md               Resúmenes (Markdown con Frontmatter YAML)
│   ├── resumenes_meta.json Índice central de todos los resúmenes
│   └── tareas_meta.json   Lista de tareas/eventos extraídos
│
├── memoria_ia/            Reglas del profesor por materia. ACTUALMENTE VACÍO.
│   └── reglas_{materia}.md (se crea al detectar nuevas_reglas_profesor)
│
├── exportaciones/         Archivos descargables. ACTUALMENTE VACÍO.
│   ├── *.apkg             Mazos de Anki
│   └── *.ics              Calendarios iCalendar
│
├── extension/             Extensión de Chrome (Manifest V3)
└── frontend/
    └── index.html         Dashboard web (SPA monolítica)
```

### Formatos internos clave

**Resúmenes .md:**
```markdown
---
tipo: [teoria | cheatsheet | tarea]
estado: [borrador | pendiente]
tags: [tag1, tag2]
---
[Cuerpo con callouts de Obsidian: > [!summary], > [!example], etc.]
[[Enlace MOC relacionado]]
```
> El bloque JSON que genera Gemini se **elimina** del .md antes de guardarlo.

**resumenes_meta.json** (dict, clave = nombre archivo .md interno):
```json
{
  "resumen__{id}__meet_FECHA.md": {
    "filename": "Titulo Googleable.md",
    "folder": "02 Recursos/Tema",
    "tags": ["tag1"],
    "condensado": "Resumen autogenerado de la clase.",
    "fecha": "YYYY-MM-DD",
    "resumen": "...contenido completo del markdown..."
  }
}
```
> **BUG CONOCIDO:** El campo `condensado` siempre se guarda con el texto fijo
> `"Resumen autogenerado de la clase."`. El valor real generado por Gemini no se persiste.

**tareas_meta.json** (array):
```json
[{ "titulo":"...", "fecha_YYYY_MM_DD":"...", "descripcion":"...",
   "id":"uuid", "origen":"archivo.md", "completada": false }]
```

**settings.json:**
```json
{
  "obsidian_vault_path": "/ruta/boveda/",
  "enable_anki": false,
  "browser_cookie_source": "brave",
  "max_audio_upload_mb": 500
}
```
> `max_audio_upload_mb` se guarda pero **no se usa en ninguna validación del servidor**.

---

## 4. Módulos y Funcionalidades Clave

### 4.1 Gestión de Asignaturas

- CRUD completo: `GET/POST /api/materias`, `PUT/DELETE /api/materias/{id}`.
- El `prompt_personalizado` **reemplaza** el contexto vacío en el prompt de Gemini (no se concatena con el prompt base, sino que llena el slot `{prompt_usar}`).
- El `materia_id` (UUID) se incrusta en el nombre del archivo .md del resumen, permitiendo filtrado en el chat.
- **4 materias activas:** Sistemas Operativos 2, Modelado y Simulación 1, Ingeniería Económica 1, Ética Profesional.

### 4.2 Memoria Dinámica del Profesor

- Gemini extrae reglas en el campo `nuevas_reglas_profesor` del JSON.
- Se acumulan con APPEND en `memoria_ia/reglas_{materia_name}.md`.
- El directorio `memoria_ia/` está **actualmente vacío**.
- En el chat, si existe el archivo de reglas de la materia, su contenido se inyecta al `system_instruction` antes de llamar a Gemini.

### 4.3 Generación Asistida de Prompts

- `POST /api/generate-prompt { descripcion }` → llama a Gemini con un prompt de "ingeniero de prompts" → devuelve texto estructurado [Rol][Contexto][Tarea][Formato].
- Sirve para que el usuario construya el `prompt_personalizado` de una materia.

### 4.4 Integración Obsidian

- El servidor escribe directamente el .md en la ruta configurada en `settings.obsidian_vault_path`.
- La carpeta destino y el nombre del archivo los propone Gemini en el JSON (estructura PARA: `02 Recursos/Tema`).
- Si la ruta no está montada: falla silenciosamente y continúa con el guardado local.

### 4.5 Integración Anki

- Actualmente **desactivada** (`enable_anki: false` en settings.json).
- Cuando activa: `genanki` genera `.apkg` en `exportaciones/`. IDs aleatorios en cada guardado.

### 4.6 Integración Calendario

- Eventos de `calendario` del JSON → archivo `.ics` en `exportaciones/` + entradas en `tareas_meta.json`.
- Solo fecha (sin hora). El dashboard permite marcar tareas como completadas.

### 4.7 Extractor Visual de Tareas

- Pestaña "Captura" de la extensión → `chrome.tabs.captureVisibleTab()` → JPEG de la pantalla.
- `POST /api/extract-task { image_base64 }` → Gemini analiza la imagen con modelo `gemini-3.5-flash` (hardcodeado).
- Resultado: .md en `resumenes/`, copia en Obsidian, entradas en `tareas_meta.json`.
- **No pasa por la cola de audios pendientes ni por `/api/generate`.**

### 4.8 Edición de Resúmenes

- `PUT /api/summaries/{filename}` sobreescribe el .md **y** actualiza el campo `resumen` en `resumenes_meta.json`.

---

## 5. El Motor de Chat y RAG

RAG ligero sin embeddings ni base de datos vectorial. Usa coincidencia de palabras clave y metadatos.

### Flujo `POST /api/chat { mensaje, materia_id, modelo_elegido }`

**Paso 1 — Selección por materia:**
| materia_id | Archivos seleccionados |
|---|---|
| `"todas"` | Todos los .md de resumenes/ |
| `"default"` | Archivos con `__default__` en el nombre O sin `__` (formato antiguo) |
| `{uuid}` | Archivos con `__{uuid}__` en el nombre |

**Paso 2 — Filtro temporal** (`nlp_engine.parse_temporal_filter`):
| Expresión en el mensaje | Rango |
|---|---|
| "clase pasada" / "última clase" | Últimos 7 días |
| "esta semana" | Desde el lunes de la semana actual |
| "semana pasada" | Semana anterior completa |
| Nombre de mes en español | Todo ese mes del año actual |
| Sin expresión temporal | Sin filtro (None) |

**Paso 3 — Índice condensado cronológico:**
Lista de `"- YYYY-MM-DD: {condensado}"` de los documentos filtrados.
> Inutilizado en la práctica porque el campo `condensado` siempre es la cadena fija.

**Paso 4 — Scoring de relevancia** (`nlp_engine.score_relevance`):
```
query_tokens = tokenizar(mensaje) - stopwords_español
score += coincidencia_con_tags * 3.0
score += coincidencia_con_texto_resumen * 1.0
threshold = 1.0, máximo 8 documentos devueltos
```

**Paso 5 — Contexto enviado a Gemini:**
```python
contents = [
  "ÍNDICE CONDENSADO:\n" + indice_condensado,
  "APUNTES COMPLETOS:\n" + resumenes_completos,  # top 8 relevantes
  req.mensaje
]
```
El texto de los resúmenes viene del campo `resumen` de `resumenes_meta.json` (no se leen los .md directamente).

**Paso 6 — Inyección de reglas del profesor:**
Si `memoria_ia/reglas_{materia_id}.md` existe → se agrega al `system_instruction`.

**Limitaciones actuales:**
- El índice condensado no aporta información real (bug del campo fijo).
- Solo overlap de palabras (no semántica real).
- Máximo 8 documentos en contexto.

---

## 6. La Extensión de Chrome en Detalle

**Arquitectura Manifest V3:** tres componentes que se comunican por mensajes.

| Archivo | Rol |
|---|---|
| `background.js` | Service Worker. Orquestador. Estados, timers, alarmas de silencio. |
| `offscreen.js` | Documento offscreen. MediaRecorder, AudioContext, upload al servidor. |
| `popup.html/js` | UI: pestañas "Grabar Audio", "Captura de Tarea", panel Ajustes. |

**Ciclo de estados por tabId** (en `chrome.storage.local.recordingStates`):
`idle → recording ↔ paused → uploading → completed | fallback_saved | error → idle`

**Auto-Stop por silencio:**
- `chrome.tabs.onUpdated` detecta cambio en `tab.audible`.
- Si la pestaña graba y se vuelve inaudible → `chrome.alarms.create("silence_{tabId}", delayInMinutes)`.
- Si vuelve a sonar → cancela la alarma.
- Si se dispara → `stopRecording(tabId)`.

**Ghost Mode:**
- Cuando activo: `gainNode.gain.value = 0` en AudioContext. El audio se graba pero no se escucha.

**Backup local (panel Ajustes):**
- `backupSubfolder`: carpeta destino (default `Backups_Clases/`).
- `backupAskAlways`: mostrar diálogo "Guardar como" en cada backup.

---

## 7. Scripts y Archivos Auxiliares

| Archivo | Estado | Descripción |
|---|---|---|
| `nlp_engine.py` | Activo (módulo) | Importado por server.py. Dos funciones: `parse_temporal_filter` y `score_relevance`. Sin dependencias externas. |
| `test_file.py` | Inactivo | Archivo de prueba manual, no integrado al servidor. |
| `test_models.py` | Inactivo | Archivo de prueba de conexión a la API de Gemini. |
| `model_output.txt` | Inactivo | Salida de prueba guardada manualmente. No es leído por ningún módulo. |

---

## 8. Tabla de Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/stats` | Estadísticas de tokens del día |
| GET | `/api/models` | Lista modelos Gemini disponibles |
| GET/POST | `/api/materias` | Listar / Crear materia |
| PUT/DELETE | `/api/materias/{id}` | Editar / Eliminar materia |
| GET/PUT | `/api/settings` | Leer / Actualizar configuración global |
| GET/DELETE | `/api/audios` `/api/audios/{f}` | Listar / Eliminar audio pendiente |
| POST | `/upload` | Subir audio desde extensión (multipart) |
| POST | `/api/generate` | Generar resumen de un audio con Gemini |
| POST | `/api/save` | Guardar resumen aprobado + artefactos |
| GET | `/api/summaries` | Listar resúmenes guardados |
| GET/PUT/DELETE | `/api/summaries/{f}` | Leer / Editar / Eliminar resumen |
| POST | `/api/chat` | Chat RAG con los resúmenes |
| POST | `/api/generate-prompt` | Generar prompt con IA |
| GET/PUT | `/api/tareas` `/api/tareas/{id}` | Listar tareas / Marcar completada |
| POST | `/api/extract-task` | Extraer tarea desde captura de pantalla |
| POST | `/api/download-drive` | Descargar audio desde Google Drive |
| GET | `/api/exportaciones/{f}` | Descargar .apkg o .ics |
| GET | `/info` | Health check |
| GET | `/media/*` | Servir archivos de audio (static) |
| GET | `/*` | Servir el frontend (index.html) |
