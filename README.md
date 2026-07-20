# Asistente de Clases: AI-Powered Multi-Tab Audio Recorder 🎙️🤖

![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![Chrome Extension](https://img.shields.io/badge/Chrome_Extension-V3-4285F4?style=for-the-badge&logo=google-chrome&logoColor=white)
![Google Gemini](https://img.shields.io/badge/Google_Gemini-3.5_Flash-8E75B2?style=for-the-badge&logo=google)
![Vanilla JS](https://img.shields.io/badge/Vanilla_JS-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black)

**Asistente de Clases** es una solución integral compuesta por una extensión de Chrome (Manifest V3) y un backend en FastAPI. Su propósito es grabar el audio de reuniones, clases virtuales o cualquier pestaña del navegador de forma autónoma, y procesarlo mediante inteligencia artificial (Gemini) para generar resúmenes estructurados en formato Markdown.

Diseñado con resiliencia, eficiencia en memoria y automatización como pilares fundamentales.

---

## ✨ Características Principales

*   **🎧 Grabación Multi-Pestaña Aislada:** Graba múltiples sesiones simultáneamente sin cruce de audio. Cada pestaña mantiene su propia instancia de `MediaRecorder` mediante *Offscreen Documents*.
*   **🧠 Auto-Apagado Inteligente (Silence Detection):** Configura un temporizador que detecta automáticamente si el profesor ha dejado de hablar o el video ha terminado. Si el silencio supera el tiempo límite, la extensión detiene la grabación y la envía al servidor, ahorrando espacio y tokens de IA.
*   **⏸️ Control de Recesos (Pausa Nativa):** Pausa la grabación manualmente durante los descansos. Al hacerlo, el micrófono deja de capturar, generando un solo archivo de audio continuo al final de la clase (omitiendo horas de silencio y optimizando el consumo del LLM).
*   **⏱️ Matemática de Timestamps (Cero RAM):** La UI cuenta con temporizadores en vivo (reloj de duración y cuenta regresiva de auto-apagado) diseñados con cálculo de fechas, consumiendo **0% de memoria en segundo plano**.
*   **🏷️ Etiquetado Dinámico:** Asigna nombres personalizados (ej. `Clase_Calculo`) antes o durante la clase para organizar fácilmente tus resúmenes generados.
*   **🛡️ Resiliencia de IA:** El backend cuenta con un sistema de reintentos con *Exponential Backoff* (vía `tenacity`). Si los servidores de Gemini se saturan (Errores 503/500) o devuelven respuestas vacías, el sistema reintenta automáticamente de forma inteligente.
*   **♻️ Papelera de Seguridad:** Los audios procesados no se borran permanentemente. Se mueven a una papelera de reciclaje (`papelera_audios/`) que conserva de forma automática los últimos 10 archivos, garantizando que nunca pierdas datos por una alucinación de la IA.

---

## 🏗️ Arquitectura del Sistema

### 1. Frontend (Extensión Chrome Manifest V3)
*   **Estricto Cumplimiento CSP:** No se usan CDNs externos. Vanilla JS y CSS nativo.
*   **Service Workers (`background.js`):** Gestiona el estado global de las grabaciones, almacena configuración en `chrome.storage.local` y ejecuta alarmas persistentes.
*   **Offscreen API (`offscreen.js`):** API avanzada de Chrome utilizada para poder ejecutar `MediaRecorder` en segundo plano sin interrumpir la navegación del usuario (formato de salida: `audio/webm` con códec Opus).

### 2. Backend (FastAPI)
*   **Endpoints Asíncronos:** Recepción de archivos pesados de manera eficiente en `/upload`.
*   **Background Tasks:** El procesamiento de Gemini se realiza en segundo plano para liberar inmediatamente la extensión de Chrome.
*   **Recuperación Manual:** Ruta `/retry/{filename}` para forzar el procesamiento de audios que hayan fallado por caídas masivas en los servidores de Google.
*   **Diagnóstico:** Ruta `/info` que devuelve un JSON estructurado con el estado de las dependencias, rutas y configuración de todo el ecosistema.

---

## 🚀 Guía de Instalación

### Requisitos Previos
*   Google Chrome (versión reciente compatible con Manifest V3 y Offscreen API).
*   Python 3.10 o superior.
*   Una API Key de Google Gemini.

### Paso 1: Configurar el Backend (Servidor Local)
1. Clona este repositorio y navega a la carpeta del proyecto.
2. Crea un entorno virtual e instala las dependencias:
   ```bash
   python -m venv venv
   source venv/bin/activate  # En Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Crea un archivo `.env` en la raíz del proyecto y añade tu clave de Gemini:
   ```env
   GEMINI_API_KEY=tu_clave_aqui
   ```
4. Inicia el servidor de FastAPI:
   ```bash
   uvicorn server:app --reload
   ```
   *El servidor correrá en `http://localhost:8000`.*

### Paso 2: Cargar la Extensión en Chrome
1. Abre Google Chrome y navega a `chrome://extensions/`.
2. Activa el **Modo de desarrollador** (esquina superior derecha).
3. Haz clic en **"Cargar descomprimida"** y selecciona la carpeta `/extension` que se encuentra dentro de este repositorio.
4. Ancla la extensión a tu barra de tareas para un acceso rápido.

---

## 📖 Uso

1. **Inicia una reunión:** Abre Google Meet, Zoom Web, YouTube o cualquier pestaña que emita audio.
2. **Abre la extensión:** Haz clic en el icono del "Asistente de Clases". (El botón de grabar estará deshabilitado si la pestaña no está emitiendo sonido por seguridad).
3. **Configura (Opcional):**
   *   Define la etiqueta del resumen (ej. `Fisica_Cuantica`).
   *   Ajusta el temporizador de auto-apagado por silencio (por defecto 5 minutos).
4. **Graba:** Haz clic en "🔴 Grabar".
5. **Gestiona Recesos:** Si el profesor da un descanso de 15 minutos, presiona **"⏸️ Pausa"**. El contador se congelará y el archivo omitirá esos 15 minutos. Cuando vuelva la clase, presiona **"▶️ Reanudar"**.
6. **Finalización:** 
   *   Si abandonas la reunión y el audio cesa, el temporizador de auto-apagado terminará la grabación por ti.
   *   Si lo prefieres, puedes darle a **"⏹ Detener"** manualmente.
7. **Magia de la IA:** La extensión enviará el archivo al servidor silenciosamente. Revisa la consola de tu servidor Python; verás a Gemini procesando. Cuando termine, tendrás un archivo `.md` estructurado en la carpeta `/resumenes/`.

---

## 🛠️ Tecnologías Utilizadas

*   **Python:** FastAPI, Uvicorn, Google GenAI SDK, Tenacity.
*   **Web/Extension:** HTML5, Vanilla JavaScript, CSS3, Chrome Extensions API (V3).
*   **LLM:** Gemini 3.5 Flash (Optimizado para contextos largos y transcripción multimodal veloz).

---

## 🔒 Privacidad y Seguridad
Este sistema procesa grabaciones de audio enviándolas a los servidores de Google Gemini. Asegúrate de tener el consentimiento de los participantes de las reuniones antes de grabar y procesar sus voces o información confidencial. El código de la extensión no se comunica con ningún servidor de terceros aparte del `localhost:8000` alojado en tu propia máquina.
