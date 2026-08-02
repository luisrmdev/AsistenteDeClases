
      // Configurar Marked para soportar KaTeX (matemáticas)
      if (window.marked && window.markedKatex) {
        marked.use(window.markedKatex({
          throwOnError: false,
          output: "html",
          nonStandard: true
        }));
      }

      // Interceptor para soportar sintaxis Obsidian de imágenes en visor web
      function renderMarkdown(text) {
          if (!text) return "";
          // Reemplazar ![[imagen.jpg]] con ![Adjunto](/adjuntos/imagen.jpg)
          const processedText = text.replace(/!\[\[(.*?\.(jpg|jpeg|png|webp))\]\]/gi, '![Adjunto](/adjuntos/$1)');
          return marked.parse(processedText);
      }

      // Delegación de eventos para las imágenes en el visualizador Markdown
      function attachImagePreviewToMarkdown() {
          const contentArea = document.getElementById("content-area");
          if (!contentArea) return;
          const images = Array.from(contentArea.querySelectorAll("img"));
          if (images.length === 0) return;
          
          const imageSrcs = images.map(img => img.src);
          images.forEach((img, index) => {
              // Estilizar para indicar que es interactiva
              img.classList.add("cursor-pointer", "hover:opacity-80", "transition-opacity", "rounded-md", "shadow-sm");
              img.onclick = () => {
                  openImagePreview(img.src, img.alt || "Imagen adjunta", index, imageSrcs);
              };
          });
      }

      // Theme logic
      const themeBtn = document.getElementById("theme-toggle");

      function setTheme(isDark) {
        if (isDark) {
          document.documentElement.classList.add("dark");
          localStorage.setItem("theme", "dark");
        } else {
          document.documentElement.classList.remove("dark");
          localStorage.setItem("theme", "light");
        }
      }

      themeBtn.addEventListener("click", () => {
        const isDark = document.documentElement.classList.contains("dark");
        setTheme(!isDark);
      });

      // Sidebar logic
      function toggleMainSidebar() {
        const sidebar = document.getElementById("main-sidebar");
        const chevron = document.getElementById("sidebar-chevron");
        sidebar.classList.toggle("collapsed");
        if (sidebar.classList.contains("collapsed")) {
            chevron.setAttribute("data-lucide", "chevron-right");
        } else {
            chevron.setAttribute("data-lucide", "chevron-left");
        }
        lucide.createIcons();
      }

      function toggleBiblioteca(forceState) {
        const sidebar = document.getElementById("biblioteca-sidebar");
        const showBtn = document.getElementById("btn-show-biblioteca");
        
        if (forceState === 'open') {
            sidebar.classList.remove("collapsed");
        } else if (forceState === 'closed') {
            sidebar.classList.add("collapsed");
        } else {
            sidebar.classList.toggle("collapsed");
        }
        
        if (sidebar.classList.contains("collapsed")) {
            showBtn.classList.remove("hidden");
            localStorage.setItem("biblioteca_state", "closed");
        } else {
            showBtn.classList.add("hidden");
            localStorage.setItem("biblioteca_state", "open");
        }
      }

      function toggleEntregables(forceState) {
        const sidebar = document.getElementById("entregables-sidebar");
        const showBtn = document.getElementById("btn-show-entregables");
        
        if (forceState === 'open') {
            sidebar.classList.remove("collapsed-right");
        } else if (forceState === 'closed') {
            sidebar.classList.add("collapsed-right");
        } else {
            sidebar.classList.toggle("collapsed-right");
        }
        
        if (sidebar.classList.contains("collapsed-right")) {
            showBtn.classList.remove("hidden");
            localStorage.setItem("entregables_state", "closed");
        } else {
            showBtn.classList.add("hidden");
            localStorage.setItem("entregables_state", "open");
        }
      }

      // Toast System
      function showToast(message, type = "info") {
        const container = document.getElementById("toast-container");
        const toast = document.createElement("div");
        toast.className = "toast fade-in";

        let icon = '<i data-lucide="info" class="w-5 h-5 text-blue-500"></i>';
        if (type === "success")
          icon =
            '<i data-lucide="check-circle-2" class="w-5 h-5 text-green-500"></i>';
        if (type === "error")
          icon =
            '<i data-lucide="alert-circle" class="w-5 h-5 text-red-500"></i>';

        toast.innerHTML = `<span class="flex-shrink-0">${icon}</span> <span class="text-sm font-medium flex-1">${message}</span>`;

        container.appendChild(toast);
        lucide.createIcons({ root: toast });

        setTimeout(() => {
          toast.classList.remove("fade-in");
          toast.style.animation = "toastLeave 0.3s ease-in forwards";
          setTimeout(() => toast.remove(), 300);
        }, 4000);
      }

      function escapeHTML(text) {
        const element = document.createElement("div");
        element.textContent = text;
        return element.innerHTML;
      }

      // Auth Interceptor
      const originalFetch = window.fetch;
      window.fetch = async function () {
        let [resource, config] = arguments;
        
        if (typeof resource === 'string' && resource.startsWith('/api/') && !resource.startsWith('/api/login')) {
          config = config || {};
          config.headers = config.headers || {};
          const token = localStorage.getItem('synq_token');
          if (token) {
            config.headers['Authorization'] = `Bearer ${token}`;
          }
        }
        
        const response = await originalFetch(resource, config);
        
        if (response.status === 401 && resource !== '/api/login') {
          showLoginScreen();
        }
        
        return response;
      };

      function showLoginScreen() {
        localStorage.removeItem('synq_token');
        checkAuthAndBoot();
      }
      
      async function handleLogin(e) {
        e.preventDefault();
        const fd = new FormData(e.target);
        const btn = e.target.querySelector('button[type="submit"]');
        const originalText = btn.innerHTML;
        btn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i>';
        btn.disabled = true;
        lucide.createIcons({ root: btn });
        
        try {
            const res = await fetch('/api/login', {
              method: 'POST',
              body: fd
            });
            if(res.ok) {
               const data = await res.json();
               localStorage.setItem('synq_token', data.access_token);
               // Refresh UI
               checkAuthAndBoot();
            } else {
               showToast("Credenciales inválidas", "error");
            }
        } finally {
            btn.innerHTML = originalText;
            btn.disabled = false;
            lucide.createIcons({ root: btn });
        }
      }

      function logout() {
        localStorage.removeItem('synq_token');
        checkAuthAndBoot();
      }

      function checkAuthAndBoot() {
        const token = localStorage.getItem('synq_token');
        const loginView = document.getElementById('login-view');
        const appContainer = document.getElementById('app-container');
        
        if (!token) {
           // Show Login
           appContainer.classList.add('hidden');
           loginView.classList.remove('hidden');
        } else {
           // Show App
           loginView.classList.add('hidden');
           appContainer.classList.remove('hidden');
           // Initialize app data if needed
           fetchMaterias();
           fetchResumenes();
           fetchAudiosCola();
           
           // Boot other modules
           loadSummaries();
           loadMaterias().then(() => {
              return loadProgreso();
           }).then(() => {
              loadAudios();
           });
           loadAppSettings();

           fetch("/api/models")
             .then((r) => r.json())
             .then((data) => {
               const modelSelector = document.getElementById("model-selector");
               modelSelector.innerHTML = "";
               data.models.forEach((m) => {
                 const option = document.createElement("option");
                 option.value = m.id;
                 option.textContent = `${m.name} - ${m.description.substring(0, 50)}${m.description.length > 50 ? "..." : ""}`;
                 modelSelector.appendChild(option);
               });
               
               // Re-apply settings after models are loaded to ensure the selected model exists
               loadAppSettings(); 
             })
             .catch(console.error);
        }
      }

      // State
      let materias = [];
      let currentAudioFile = "";
      let currentMateriaId = "";
      let editingMateriaId = null;
      let tutorHistoryState = [];

      // Init
      document.addEventListener("DOMContentLoaded", () => {
        const progresoDateInput = document.getElementById("progreso-base-date");
        progresoDateInput.value = localStorage.getItem("last_progreso_date") || new Date().toISOString().split('T')[0];
        progresoDateInput.addEventListener("change", (e) => {
           localStorage.setItem("last_progreso_date", e.target.value);
           renderProgresoGrid();
           if(typeof syncDropdownValue === 'function') syncDropdownValue();
        });

        const progresoDropdown = document.getElementById("progreso-weeks-dropdown");
        progresoDropdown.addEventListener("change", (e) => {
           if(e.target.value) {
               progresoDateInput.value = e.target.value;
               localStorage.setItem("last_progreso_date", e.target.value);
               renderProgresoGrid();
           }
        });

        // Restore Sidebar states
        const bibState = localStorage.getItem("biblioteca_state") || "closed";
        toggleBiblioteca(bibState);
        const entState = localStorage.getItem("entregables_state") || "closed";
        toggleEntregables(entState);


        const modelSelector = document.getElementById("model-selector");
        modelSelector.addEventListener("change", (e) => {
          localStorage.setItem("modelo_elegido", e.target.value);
          document.getElementById("ui-current-model").innerText = getUiName(
            e.target.value,
          );

        });



        // Chat form submission
        document.getElementById("chat-form").addEventListener("submit", (e) => {
          e.preventDefault();
          handleChatSubmit();
        });

        // Tutor form submission
        document
          .getElementById("tutor-form")
          .addEventListener("submit", (e) => {
            e.preventDefault();
            handleTutorSubmit();
          });

        // Render icons
        lucide.createIcons();
      });



      // Settings config logic
      let currentAppSettings = {};

      function getUiName(val) {
        if (val.includes("3.5-flash")) return "Flash 3.5";
        if (val.includes("lite")) return "Flash Lite";
        if (val.includes("pro")) return "Pro";
        return val.split("-")[1] || val; // simple fallback
      }

      function loadAppSettings() {
        fetch("/api/settings")
          .then((r) => r.json())
          .then((data) => {
            currentAppSettings = data;
            
            document.getElementById("config-obsidian-path").value =
              data.obsidian_vault_path || "";
            document.getElementById("config-max-audio-mb").value =
              data.max_audio_upload_mb || 500;
            document.getElementById("config-rag-max-docs").value =
              data.rag_max_docs || 8;
            document.getElementById("config-papelera-items").value =
              data.max_papelera_items || 10;
            document.getElementById("config-nlp-threshold").value = 
              data.nlp_threshold || 1.0;
            document.getElementById("config-audio-silence").value = 
              data.audio_silence_db || -30;

            if (data.default_model) {
              document.getElementById("model-selector").value =
                data.default_model;
              localStorage.setItem("modelo_elegido", data.default_model);
              document.getElementById("ui-current-model").innerText = getUiName(
                data.default_model,
              );
            }
          })
          .catch((e) => console.log("Error cargando configuración", e));
      }

      function saveAppSettings() {
        const path = document.getElementById("config-obsidian-path").value;
        const maxAudioMB =
          parseInt(document.getElementById("config-max-audio-mb").value) || 500;
        const maxPapeleraItems =
          parseInt(document.getElementById("config-papelera-items").value) || 10;
        const ragMaxDocs =
          parseInt(document.getElementById("config-rag-max-docs").value) || 8;
        const nlpThreshold = 
          parseFloat(document.getElementById("config-nlp-threshold").value) || 1.0;
        const audioSilenceDb = 
          parseInt(document.getElementById("config-audio-silence").value) || -30;
        const selectedModel = document.getElementById("model-selector").value;

        const btn = document.getElementById("btn-save-settings");
        const originalText = btn.innerText;
        btn.innerText = "Guardando...";
        btn.disabled = true;

        const payload = {
          obsidian_vault_path: path,
          max_audio_upload_mb: maxAudioMB,
          max_papelera_items: maxPapeleraItems,
          rag_max_docs: ragMaxDocs,
          nlp_threshold: nlpThreshold,
          audio_silence_db: audioSilenceDb,
          default_model: selectedModel,
          
          prompt_maestro_resumenes: currentAppSettings.prompt_maestro_resumenes,
          prompt_chat_rag: currentAppSettings.prompt_chat_rag,
          prompt_tutor_socratico: currentAppSettings.prompt_tutor_socratico,
          prompt_generator_sys: currentAppSettings.prompt_generator_sys,
          prompt_tarea_extractor: currentAppSettings.prompt_tarea_extractor
        };

        return fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
          .then((r) => r.json())
          .then(() => {
            btn.innerText = "¡Guardado!";
            setTimeout(() => {
              btn.innerText = originalText;
              btn.disabled = false;
            }, 2000);
          })
          .catch((e) => {
            showToast("Error al guardar: " + e.message, "error");
            btn.innerText = originalText;
            btn.disabled = false;
          });
      }

      // ----------------------------------------------------
      // Prompts Modal Logic
      // ----------------------------------------------------
      let activePromptKey = null;

      function openPromptEditor(promptKey) {
        activePromptKey = promptKey;
        const modal = document.getElementById("prompt-modal");
        const textarea = document.getElementById("prompt-modal-textarea");
        
        // Cargar el valor actual de la caché local
        // Si no existe, al estar vacío el usuario puede clickear Restaurar Original
        textarea.value = currentAppSettings[promptKey] || "";
        if (!textarea.value) {
            textarea.placeholder = "Cargando default / Vacío...";
        }

        modal.classList.remove("hidden");
        // Trigger reflow
        void modal.offsetWidth;
        modal.classList.remove("opacity-0");
        modal.querySelector("div").classList.remove("scale-95");
      }

      function closePromptModal() {
        const modal = document.getElementById("prompt-modal");
        modal.classList.add("opacity-0");
        modal.querySelector("div").classList.add("scale-95");
        setTimeout(() => {
          modal.classList.add("hidden");
          activePromptKey = null;
        }, 300);
      }

      function savePromptModal() {
        if (!activePromptKey) return;
        const textarea = document.getElementById("prompt-modal-textarea");
        currentAppSettings[activePromptKey] = textarea.value;
        
        saveAppSettings().then(() => {
          showToast("Prompt guardado exitosamente", "success");
          closePromptModal();
        });
      }

      function resetPromptToDefault() {
        if (!activePromptKey) return;
        // Limpiamos la clave para que el backend use el default
        currentAppSettings[activePromptKey] = null;
        saveAppSettings().then(() => {
          showToast("Prompt restaurado al valor por defecto", "info");
          closePromptModal();
        });
      }

      // Tab Switching
      function switchTab(tabId) {
        // Detener cualquier audio en reproducción para evitar audios fantasmas al destruir el DOM
        document.querySelectorAll("audio").forEach((audio) => {
          if (!audio.paused) {
            audio.pause();
            audio.currentTime = 0;
          }
        });

        document
          .querySelectorAll(".tab-content")
          .forEach((el) => el.classList.add("hidden"));
        document
          .querySelectorAll(".nav-btn")
          .forEach((el) => el.classList.remove("active-nav"));

        const target = document.getElementById(`tab-${tabId}`);
        target.classList.remove("hidden");
        if (tabId === "resumenes") target.classList.add("flex");
        else target.classList.add("flex");

        document.getElementById(`nav-${tabId}`).classList.add("active-nav");

        if (tabId === "audios") {
          loadAudios();
          loadCola();
        }
        if (tabId === "config") loadMaterias();
        if (tabId === "resumenes") loadSummaries();
        if (tabId === "tarjetas") loadTarjetas();
        if (tabId === "progreso") loadProgreso();
      }

      // ====== TAB: RESUMENES ======
      function filterSummariesList() {
          const query = document.getElementById("doc-search-filter").value.toLowerCase();
          const items = document.querySelectorAll("#summaries-list > div");
          
          items.forEach(item => {
              const textContent = item.textContent.toLowerCase();
              if (textContent.includes(query)) {
                  item.style.display = "flex";
              } else {
                  item.style.display = "none";
              }
          });
      }

      function cargarYMostrarDocumento(filename) {
          // Reset highlights in sidebar if they exist
          document.querySelectorAll("#summaries-list div").forEach((d) => {
              d.style.backgroundColor = "transparent";
              d.style.borderColor = "transparent";
          });
          
          // Highlight active file if it's currently rendered in the list
          const fileItem = document.querySelector(`#summaries-list div[data-filename="${filename}"]`);
          if (fileItem) {
              fileItem.style.backgroundColor = "var(--color-bg)";
              fileItem.style.borderColor = "var(--color-border)";
              // Only scroll into view if we are on desktop or sidebar is open
              const sidebar = document.getElementById("biblioteca-sidebar");
              if (!sidebar.classList.contains("collapsed")) {
                  fileItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
              }
          }

          // Fetch and Render
          fetch(`/api/summaries/${filename}`)
              .then((r) => r.json())
              .then((d) => {
                  document.getElementById("content-area").innerHTML = renderMarkdown(d.content);
                  document.getElementById("content-area").dataset.filename = filename;
                  attachImagePreviewToMarkdown();
                  document.getElementById("content-area").classList.remove("hidden");
                  document.getElementById("edit-area").classList.add("hidden");
                  document.getElementById("edit-area").value = d.content;
                  
                  const header = document.getElementById("content-header");
                  header.classList.remove("hidden");
                  
                  const btnEdit = document.getElementById("btn-edit-summary");
                  const btnSave = document.getElementById("btn-save-summary");
                  btnEdit.classList.remove("hidden");
                  btnSave.classList.add("hidden");
                  
                  // Setup Edit Listeners
                  btnEdit.onclick = () => {
                      document.getElementById("content-area").classList.add("hidden");
                      document.getElementById("edit-area").classList.remove("hidden");
                      btnEdit.classList.add("hidden");
                      btnSave.classList.remove("hidden");
                  };
                  
                  btnSave.onclick = () => {
                      const newContent = document.getElementById("edit-area").value;
                      fetch(`/api/summaries/${filename}`, {
                          method: "PUT",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ content: newContent }),
                      }).then(() => {
                          document.getElementById("content-area").innerHTML = renderMarkdown(newContent);
                          attachImagePreviewToMarkdown();
                          document.getElementById("content-area").classList.remove("hidden");
                          document.getElementById("edit-area").classList.add("hidden");
                          btnEdit.classList.remove("hidden");
                          btnSave.classList.add("hidden");
                          showToast("Documento guardado correctamente", "success");
                      });
                  };
              })
              .catch(err => {
                  console.error(err);
                  showToast("Error al cargar el documento", "error");
              });
      }

      function loadSummaries() {
        const filterVal = document.getElementById("doc-materia-filter")?.value || "todas";
        
        fetch("/api/summaries")
          .then((r) => r.json())
          .then((data) => {
            const list = document.getElementById("summaries-list");
            list.innerHTML = "";
            
            let summaries = data.summaries;
            if (filterVal !== "todas") {
               summaries = summaries.filter(s => s.materia_id === filterVal);
            }
            
            if (!summaries.length) {
              list.innerHTML =
                '<p class="text-xs text-textMuted p-2 font-mono">/sin_documentos</p>';
              return;
            }
            
            summaries.forEach((s) => {
              const div = document.createElement("div");
              div.setAttribute("data-filename", s.filename);
              div.className =
                "p-2 rounded-md hover:bg-background cursor-pointer text-charcoal flex justify-between items-center group transition-colors border border-transparent hover:border-borderGray";

              const titleSpan = document.createElement("span");
              titleSpan.className = "flex-1 overflow-hidden pr-2";
              
              const materiaPill = s.materia_name !== "General" ? `<span class="inline-block px-1.5 py-0.5 rounded text-[8px] uppercase tracking-wider font-bold bg-borderGray/50 text-charcoal ml-2 shrink-0 align-middle">${s.materia_name}</span>` : '';
              titleSpan.innerHTML = `<div class="truncate font-medium text-charcoal flex items-center"><span class="truncate">[ DOC ] ${s.display_name}</span>${materiaPill}</div><div class="text-[9px] text-textMuted font-mono mt-0.5 opacity-80">${s.created_at}</div>`;

              const delBtn = document.createElement("button");
              delBtn.innerHTML =
                '<i data-lucide="trash-2" class="w-3.5 h-3.5"></i>';
              delBtn.className =
                "opacity-0 group-hover:opacity-100 bg-paleRed text-paleRedText p-1 rounded transition";
              delBtn.onclick = (e) => {
                e.stopPropagation();
                if (
                  confirm(
                    "¿Eliminar este documento de la base de conocimiento?",
                  )
                ) {
                  fetch(`/api/summaries/${s.filename}`, {
                    method: "DELETE",
                  }).then(() => {
                    loadSummaries();
                    if (typeof loadProgreso === 'function') loadProgreso();
                    
                    if (document.getElementById("content-area").dataset.filename === s.filename) {
                      document.getElementById("content-area").innerHTML = '<div class="flex flex-col items-center justify-center h-full text-textMuted/50 mt-20"><i data-lucide="file-x-2" class="w-16 h-16 mb-4 opacity-50"></i><p class="font-mono text-sm">Documento eliminado</p></div>';
                      document.getElementById("content-header").classList.add("hidden");
                      document.getElementById("edit-area").classList.add("hidden");
                      delete document.getElementById("content-area").dataset.filename;
                      lucide.createIcons();
                    }
                  });
                }
              };

              div.appendChild(titleSpan);
              div.appendChild(delBtn);

              div.onclick = () => {
                cargarYMostrarDocumento(s.filename);
              };
              list.appendChild(div);
            });
            lucide.createIcons({ root: list });
          });
      }

      // ====== TAB: CONFIGURACION ======
      function loadMaterias() {
        return fetch("/api/materias")
          .then((r) => r.json())
          .then((data) => {
            materias = data;

            // Render List
            const list = document.getElementById("materias-list");
            list.innerHTML = "";
            data.forEach((m) => {
              const safePrompt = m.prompt_personalizado.replace(/"/g, "&quot;");
              list.innerHTML += `
                            <div class="bento-card p-6 flex flex-col justify-between group hover:border-charcoal/30 transition-colors duration-300 min-h-[160px]">
                                <div class="mb-5">
                                    <div class="flex justify-between items-start mb-3">
                                        <h4 class="font-serif text-lg text-charcoal truncate pr-2" title="${m.nombre}">${m.nombre}</h4>
                                        <span class="text-[9px] font-bold bg-borderGray/50 text-charcoal px-2 py-0.5 rounded-full uppercase tracking-wider shrink-0" title="Documentos procesados">${m.doc_count || 0} Docs</span>
                                    </div>
                                    <div class="bg-background border border-borderGray rounded p-3">
                                        <p class="text-[10px] text-textMuted font-mono leading-relaxed line-clamp-3 break-words" title="${safePrompt}">${m.prompt_personalizado}</p>
                                    </div>
                                </div>
                                <div class="flex justify-between items-center pt-4 border-t border-borderGray/50 mt-auto">
                                    <button onclick="editMateria('${m.id}')" class="text-[10px] font-bold text-textMuted hover:text-charcoal uppercase tracking-widest transition-colors flex items-center gap-1.5">
                                        <i data-lucide="pen-line" class="w-3 h-3"></i> Editar
                                    </button>
                                    <button onclick="deleteMateria('${m.id}')" class="text-[10px] font-bold text-red-400 hover:text-red-600 uppercase tracking-widest transition-colors flex items-center gap-1.5">
                                        <i data-lucide="trash-2" class="w-3 h-3"></i> Eliminar
                                    </button>
                                </div>
                            </div>
                        `;
            });
            lucide.createIcons({ root: list });

            // Update Chat Select Options
            const chatSelect = document.getElementById("chat-context-select");
            const tarjetasSelect = document.getElementById(
              "tarjetas-materia-filter",
            );
            const tutorSelect = document.getElementById("tutor-materia-select");
            const docFilter = document.getElementById("doc-materia-filter");

            chatSelect.innerHTML =
              '<option value="todas">Todo el Semestre</option>';
            if (tarjetasSelect)
              tarjetasSelect.innerHTML =
                '<option value="todas">Todas las materias</option>';
            if (docFilter)
              docFilter.innerHTML =
                '<option value="todas">Todas las materias</option>';
            if (tutorSelect)
              tutorSelect.innerHTML =
                '<option value="" disabled selected>Selecciona una asignatura...</option>';

            data.forEach((m) => {
              chatSelect.innerHTML += `<option value="${m.id}">${m.nombre}</option>`;
              if (tarjetasSelect)
                tarjetasSelect.innerHTML += `<option value="${m.id}">${m.nombre}</option>`;
              if (docFilter)
                docFilter.innerHTML += `<option value="${m.id}">${m.nombre}</option>`;
              if (tutorSelect)
                tutorSelect.innerHTML += `<option value="${m.id}">${m.nombre}</option>`;
            });
          });
      }

      function generatePrompt() {
        const btn = document.getElementById("btn-gen-prompt");
        const input = document.getElementById("mat-natural").value;
        if (!input.trim())
          return showToast(
            "Escribe qué quieres que haga la IA primero.",
            "error",
          );

        btn.innerHTML =
          '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i> <span>Procesando...</span>';
        lucide.createIcons({ root: btn });
        btn.disabled = true;

        const modelStr =
          localStorage.getItem("modelo_elegido") || "gemini-1.5-flash";
        fetch("/api/generate-prompt", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            descripcion: input,
            modelo_elegido: modelStr,
          }),
        })
          .then((r) => r.json())
          .then((d) => {
            document.getElementById("mat-prompt").value = d.prompt_generado;

          })
          .catch((e) =>
            showToast("Error generando prompt: " + e.message, "error"),
          )
          .finally(() => {
            btn.innerHTML = "<span>Traducir a Prompt Técnico</span>";
            btn.disabled = false;
          });
      }

      function openMateriaModal(isEdit = false) {
        const modal = document.getElementById("materia-modal");
        const content = document.getElementById("materia-modal-content");
        modal.classList.remove("hidden");
        // Timeout to allow display:block to apply before animating opacity
        setTimeout(() => {
          modal.classList.remove("opacity-0");
          content.classList.remove("scale-95");
        }, 10);

        document.getElementById("mat-form-title").innerText = isEdit
          ? "Editar Asignatura"
          : "Nueva Asignatura";
        document.getElementById("btn-save-materia").innerText = isEdit
          ? "Actualizar"
          : "Guardar";
        if (!isEdit) {
          editingMateriaId = null;
          document.getElementById("mat-nombre").value = "";
          document.getElementById("mat-natural").value = "";
          document.getElementById("mat-prompt").value = "";
          document.getElementById("mat-temperatura").value = "0.3";
          document.getElementById("mat-temperatura-val").innerText = "0.3";
          document.querySelectorAll('.mat-dia-checkbox').forEach(cb => cb.checked = false);
        }
      }

      function closeMateriaModal() {
        const modal = document.getElementById("materia-modal");
        const content = document.getElementById("materia-modal-content");
        modal.classList.add("opacity-0");
        content.classList.add("scale-95");
        setTimeout(() => {
          modal.classList.add("hidden");
          editingMateriaId = null;
        }, 300);
      }

      function editMateria(id) {
        const m = materias.find((x) => x.id === id);
        if (!m) return;
        editingMateriaId = id;
        document.getElementById("mat-nombre").value = m.nombre;
        document.getElementById("mat-prompt").value = m.prompt_personalizado;
        document.getElementById("mat-temperatura").value = m.temperatura || "0.3";
        document.getElementById("mat-temperatura-val").innerText = m.temperatura || "0.3";
        
        document.querySelectorAll('.mat-dia-checkbox').forEach(cb => {
            cb.checked = (m.dias_imparticion || []).includes(cb.value);
        });
        
        openMateriaModal(true);
      }

      function saveMateria() {
        const nombre = document.getElementById("mat-nombre").value;
        const prompt = document.getElementById("mat-prompt").value;
        const temperatura = parseFloat(document.getElementById("mat-temperatura").value) || 0.3;
        
        const dias_imparticion = Array.from(document.querySelectorAll('.mat-dia-checkbox:checked')).map(cb => cb.value);

        if (!nombre || !prompt)
          return showToast(
            "El Nombre y el Prompt Final son obligatorios.",
            "error",
          );

        if (editingMateriaId) {
          fetch(`/api/materias/${editingMateriaId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nombre, prompt_personalizado: prompt, temperatura, dias_imparticion }),
          }).then(() => {
            editingMateriaId = null;
            closeMateriaModal();
            loadMaterias();
          });
        } else {
          fetch("/api/materias", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nombre, prompt_personalizado: prompt, temperatura, dias_imparticion }),
          }).then(() => {
            closeMateriaModal();
            loadMaterias();
          });
        }
      }

      function deleteMateria(id) {
        if (confirm("¿Eliminar asignatura de forma permanente?")) {
          fetch(`/api/materias/${id}`, { method: "DELETE" }).then(() =>
            loadMaterias(),
          );
        }
      }

      // ====== TAB: TARJETAS ======
      function loadTarjetas() {
        const materiaId = document.getElementById(
          "tarjetas-materia-filter",
        ).value;
        fetch(`/api/tarjetas?materia_id=${materiaId}`)
          .then((r) => r.json())
          .then((data) => {
            const grid = document.getElementById("tarjetas-grid");
            grid.innerHTML = "";
            if (!data.tarjetas || data.tarjetas.length === 0) {
              grid.innerHTML =
                '<div class="col-span-full text-center text-textMuted font-serif italic py-8">No hay avisos pendientes.</div>';
              return;
            }
            data.tarjetas.forEach((t) => {
              const card = document.createElement("div");
              card.className =
                "bento-card p-6 flex flex-col gap-4 relative group hover:border-textMuted transition-colors";

              let badgeColor = "bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700";
              if (t.tipo === "tarea")
                badgeColor = "bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800";
              if (t.tipo === "examen")
                badgeColor = "bg-red-50 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800";
              if (t.tipo === "aviso")
                badgeColor = "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-400 dark:border-amber-800";

              card.innerHTML = `
                            <button onclick="deleteTarjeta('${t.id}')" class="absolute top-4 right-4 text-textMuted hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity" title="Eliminar aviso">
                                <i data-lucide="trash-2" class="w-4 h-4"></i>
                            </button>
                            <div class="flex items-center gap-2">
                                <span class="px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider border ${badgeColor}">
                                    ${t.tipo}
                                </span>
                                <span class="text-[10px] text-textMuted font-mono">
                                    ${t.fecha_creacion}
                                </span>
                            </div>
                            <div class="text-sm text-charcoal font-medium leading-relaxed">
                                ${t.contenido}
                            </div>
                            <div class="text-xs text-textMuted flex items-center gap-1.5 border-b border-borderGray pb-4">
                                <i data-lucide="clock" class="w-3.5 h-3.5"></i>
                                Para: <span class="font-bold">${t.referencia_temporal || "Sin especificar"}</span>
                            </div>
                            
                            <div class="mt-auto pt-2">
                                <label class="block text-[10px] font-bold text-textMuted uppercase tracking-wider mb-1">Mis Notas</label>
                                <textarea id="nota-${t.id}" class="w-full text-xs p-2 rounded bg-surface border border-borderGray focus:border-charcoal outline-none resize-none" rows="2" placeholder="Añade una nota...">${t.nota_personal || ""}</textarea>
                                <button onclick="updateNotaPersonal('${t.id}')" class="text-[10px] uppercase font-bold tracking-wider text-charcoal hover:text-accent mt-1 flex items-center gap-1">
                                    <i data-lucide="save" class="w-3 h-3"></i> Guardar
                                </button>
                            </div>
                        `;
              grid.appendChild(card);
            });
            lucide.createIcons();
          });
      }

      function deleteTarjeta(id, cardElementId = null) {
        if (confirm("¿Eliminar aviso?")) {
          fetch(`/api/tarjetas/${id}`, { method: "DELETE" }).then(() => {
            if (cardElementId) {
                const el = document.getElementById(cardElementId);
                if (el) el.remove();
            } else {
                loadTarjetas();
            }
            updateEntregablesSidebar();
            showToast("Aviso eliminado", "success");
          });
        }
      }

      function updateNotaPersonal(id) {
        const nota = document.getElementById(`nota-${id}`).value;
        fetch(`/api/tarjetas/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nota_personal: nota }),
        }).then(() => {
          const btn = document.querySelector(`#nota-${id}`).nextElementSibling;
          const originalText = btn.innerHTML;
          btn.innerHTML =
            '<i data-lucide="check" class="w-3 h-3 text-emerald-500"></i> Guardado';
          lucide.createIcons();
          setTimeout(() => {
            btn.innerHTML = originalText;
          }, 2000);
        });
      }

      // ====== TAB: AUDIOS PENDIENTES ======
      function loadAudios() {
        fetch("/api/audios")
          .then((r) => r.json())
          .then((data) => {
            const list = document.getElementById("audios-list");

            // Limpiar audios existentes (pausarlos primero por seguridad)
            list.querySelectorAll("audio").forEach((a) => {
              if (!a.paused) {
                a.pause();
                a.currentTime = 0;
              }
            });

            list.innerHTML = "";
            if (!data.audios.length) {
              list.innerHTML =
                '<div class="bento-card p-12 text-center text-textMuted font-serif italic">El directorio de trabajo está vacío. No hay grabaciones locales.</div>';
              return;
            }

            let options = '<option value="" disabled selected>Selecciona destino...</option>';
            
            const pending = (typeof progresoSlots !== "undefined" ? progresoSlots : []).filter(s => s.estado === "AUSENTE" || s.estado === "EN_COLA");
            if (pending.length > 0) {
               pending.sort((a,b) => b.fecha.localeCompare(a.fecha));
               options += `<optgroup label="Clases Pendientes (Recomendado)">`;
               pending.forEach(slot => {
                  const mat = materias.find(m => m.id === slot.materia_id);
                  const name = mat ? mat.nombre : slot.materia_id;
                  options += `<option value="${slot.id}">${slot.fecha} - ${name}</option>`;
               });
               options += `</optgroup>`;
            }

            if (materias.length > 0) {
               options += `<optgroup label="Solo Asignatura (Sin Slot)">`;
               materias.forEach(m => {
                  options += `<option value="${m.id}">${m.nombre}</option>`;
               });
               options += `</optgroup>`;
            }

            window.audiosMap = {};
            data.audios.forEach((a) => {
              window.audiosMap[a.filename] = a;

              let mediaSrc = a.audio_url || `/media/${a.filename}`;
              let imagesHtml = "";
              if (a.image_urls && a.image_urls.length > 0) {
                if (a.session_name) {
                  const carouselId = `carousel-${a.filename.replace(/[^a-zA-Z0-9]/g, "_")}`;
                  const imgListJson = JSON.stringify(a.image_urls).replace(
                    /"/g,
                    "&quot;",
                  );
                  imagesHtml = `<div class="img-carousel-wrapper">`;
                  imagesHtml += `<button class="img-carousel-arrow left" onclick="scrollCarousel('${carouselId}', -1)" title="Anterior"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg></button>`;
                  imagesHtml += `<button class="img-carousel-arrow right" onclick="scrollCarousel('${carouselId}', 1)" title="Siguiente"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg></button>`;
                  imagesHtml += `<div class="img-carousel-track" id="${carouselId}">`;
                  a.image_urls.forEach((imgSrc, idx) => {
                    const img =
                      a.image_filenames && a.image_filenames[idx]
                        ? a.image_filenames[idx]
                        : imgSrc.split("/").pop();
                    imagesHtml += `
                                        <div class="relative group img-carousel-thumb" id="img-container-${img}">
                                            <img src="${imgSrc}" class="h-20 w-32 object-cover rounded border border-borderGray" title="${img}" onclick="openImagePreview('${imgSrc}', '${img}', ${idx}, ${imgListJson})">
                                            <button onclick="event.stopPropagation(); deleteImage('${a.filename}', '${img}')" class="absolute top-1 right-1 bg-red-500 text-white rounded-full p-0.5 opacity-0 group-hover:opacity-100 transition-opacity z-10" title="Eliminar imagen">
                                                <i data-lucide="x" class="w-3 h-3"></i>
                                            </button>
                                        </div>
                                    `;
                  });
                  imagesHtml += "</div>";
                  imagesHtml += `<div class="text-[10px] text-textMuted font-mono mt-1 text-right">${a.image_filenames.length} captura${a.image_filenames.length !== 1 ? "s" : ""}</div>`;
                  imagesHtml += "</div>";
                }
              }

              let checkboxHtml = "";
              if (a.session_name) {
                  checkboxHtml = `<input type="checkbox" class="session-merge-checkbox mr-3 accent-accent cursor-pointer w-4 h-4 mt-0.5" data-session="${a.session_name}" onchange="handleMergeCheckboxChange()">`;
              }

              list.innerHTML += `
                            <div class="bento-card p-6 flex flex-col md:flex-row gap-6 relative">
                                <div class="flex-1 w-full md:border-r border-borderGray pr-0 md:pr-6">
                                    <div class="flex items-start">
                                        ${checkboxHtml}
                                        <h4 class="font-bold text-sm text-charcoal mb-3 truncate font-mono pt-0.5">${a.display_name}</h4>
                                    </div>
                                    <audio controls src="${mediaSrc}" class="h-8 w-full outline-none opacity-80 filter contrast-125 rounded"></audio>
                                    ${imagesHtml}
                                </div>
                                <div class="w-full md:w-72 flex flex-col gap-3 shrink-0 self-center">
                                    <div>
                                        <label class="block text-[10px] font-bold text-textMuted uppercase tracking-wider mb-1">Contexto</label>
                                        <select id="sel-${a.filename}" class="w-full rounded-md p-2 text-xs bg-surface font-medium">
                                            ${options}
                                        </select>
                                    </div>
                                    <div class="flex gap-2">
                                        <button onclick="processAudio('${a.filename}')" class="flex-1 btn-primary py-2 text-xs font-medium">
                                            Extraer Apuntes
                                        </button>
                                        <button onclick="deleteAudio('${a.filename}')" class="btn-secondary px-3 py-2 text-xs font-medium text-paleRedText bg-paleRed/20 border-transparent">
                                            Descartar Sesión
                                        </button>
                                    </div>
                                </div>
                            </div>
                        `;
            });
            lucide.createIcons({ root: list });
          });
      }

      function deleteAudio(filename) {
        if (confirm("¿Descartar esta sesión permanentemente?")) {
          const a = window.audiosMap[filename];
          let pathToDelete = filename;
          if (a && a.session_name) {
            pathToDelete = `${a.session_name}/${filename}`;
          }
          fetch(`/api/audios/${pathToDelete}`, { method: "DELETE" }).then(() =>
            loadAudios(),
          );
        }
      }

      function deleteImage(filename, imgFilename) {
        const a = window.audiosMap[filename];
        if (a && a.image_filenames && a.image_filenames.length <= 1) {
          showToast(
            "No puedes eliminar la única imagen. El flujo requiere al menos una.",
            "error",
          );
          return;
        }
        if (confirm("¿Eliminar esta imagen de la sesión?")) {
          let pathToDelete = imgFilename;
          if (a && a.session_name) {
            pathToDelete = `${a.session_name}/${imgFilename}`;
          }
          fetch(`/api/audios/${pathToDelete}`, { method: "DELETE" }).then(
            (res) => {
              if (res.ok) {
                loadAudios();
              } else {
                res.json().then((data) => showToast(data.detail, "error"));
              }
            },
          );
        }
      }

      let colaInterval = null;

      function startColaPolling() {
        if (!colaInterval) {
          colaInterval = setInterval(loadCola, 3000);
        }
      }

      function stopColaPolling() {
        if (colaInterval) {
          clearInterval(colaInterval);
          colaInterval = null;
        }
      }

      function loadCola() {
        fetch("/api/cola")
          .then((r) => r.json())
          .then((data) => {
            const list = document.getElementById("cola-list");
            list.innerHTML = "";

            if (data.cola && data.cola.length > 0) {
              const hasProcessingOrPending = data.cola.some(
                (t) => t.estado === "pending" || t.estado === "processing",
              );
              if (hasProcessingOrPending) {
                startColaPolling();
              } else {
                stopColaPolling();
                loadAudios(); // Actualizar listado cuando todo acabe
                loadProgreso(); // Refresh the progress UI
              }

              data.cola.forEach((t) => {
                let icon = "";
                let stateClass = "";
                let actions = "";

                if (t.estado === "pending") {
                  icon =
                    '<i data-lucide="clock" class="w-4 h-4 text-textMuted"></i>';
                  stateClass = "bg-surface";
                } else if (t.estado === "processing") {
                  icon =
                    '<i data-lucide="loader-2" class="w-4 h-4 text-emerald-500 animate-spin"></i>';
                  stateClass =
                    "bg-emerald-50 border-emerald-100 dark:bg-emerald-900/20 dark:border-emerald-800/50";
                } else if (t.estado === "failed") {
                  icon =
                    '<i data-lucide="alert-circle" class="w-4 h-4 text-red-500"></i>';
                  stateClass =
                    "bg-red-50 border-red-100 dark:bg-red-900/20 dark:border-red-800/50";
                  actions = `
                                    <button onclick="retryTask('${t.id}')" class="text-[10px] uppercase font-bold tracking-wider text-charcoal hover:text-accent flex items-center gap-1">
                                        <i data-lucide="refresh-cw" class="w-3 h-3"></i> Reintentar
                                    </button>
                                `;
                } else if (t.estado === "completed") {
                  icon =
                    '<i data-lucide="check-circle-2" class="w-4 h-4 text-emerald-500"></i>';
                  stateClass =
                    "bg-emerald-50 border-emerald-100 dark:bg-emerald-900/20 dark:border-emerald-800/50 opacity-60";
                }

                const card = document.createElement("div");
                card.className = `p-4 border border-borderGray rounded flex justify-between items-center transition-colors ${stateClass}`;
                card.innerHTML = `
                                <div class="flex items-center gap-4">
                                    ${icon}
                                    <div>
                                        <div class="text-sm font-bold text-charcoal">${t.filename}</div>
                                        <div class="text-xs text-textMuted font-mono mt-1">Estado: ${t.estado.toUpperCase()} ${t.error_msg ? "| Error: " + t.error_msg : ""}</div>
                                    </div>
                                </div>
                                <div class="flex items-center gap-3">
                                    ${actions}
                                    <button onclick="deleteTask('${t.id}')" class="text-textMuted hover:text-red-500 p-1"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
                                </div>
                            `;
                list.appendChild(card);
              });
              lucide.createIcons();
            } else {
              stopColaPolling();
            }
          });
      }

      function retryTask(id) {
        fetch(`/api/cola/${id}/retry`, { method: "POST" }).then(() =>
          loadCola(),
        );
      }

      function deleteTask(id) {
        fetch(`/api/cola/${id}`, { method: "DELETE" }).then(() => loadCola());
      }

      function handleMergeCheckboxChange() {
          const checkboxes = document.querySelectorAll('.session-merge-checkbox:checked');
          const fab = document.getElementById('merge-fab');
          if (checkboxes.length === 2) {
              fab.classList.remove('translate-y-24', 'opacity-0', 'pointer-events-none');
          } else {
              fab.classList.add('translate-y-24', 'opacity-0', 'pointer-events-none');
          }
      }

      function mergeSelectedSessions() {
          const checkboxes = document.querySelectorAll('.session-merge-checkbox:checked');
          if (checkboxes.length !== 2) return;

          // Ordenar alfabéticamente asegura que la sesión más vieja sea la 1 y la más nueva la 2
          const sessions = Array.from(checkboxes).map(cb => cb.dataset.session).sort();
          
          if(!confirm(`¿Fusionar permanentemente estas dos grabaciones?\n\n1. ${sessions[0]}\n2. ${sessions[1]}`)) return;
          
          showToast("Procesando fusión. Por favor espera...", "info");
          
          fetch("/api/audios/merge", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session1: sessions[0], session2: sessions[1] })
          }).then(res => {
              if(!res.ok) {
                  res.json().then(d => showToast(d.detail || "Error al fusionar", "error"));
                  return;
              }
              res.json().then(data => {
                  showToast(data.message);
                  const fab = document.getElementById('merge-fab');
                  fab.classList.add('translate-y-24', 'opacity-0', 'pointer-events-none');
                  loadAudios();
              });
          });
      }

      // ====== HUMAN IN THE LOOP ======
      function processAudio(filename) {
        const select = document.getElementById(`sel-${filename}`);
        const a = window.audiosMap[filename];

        if (!select.value) {
          showToast("Por favor selecciona un destino para extraer apuntes.", "error");
          return;
        }
        
        let materiaId = select.value;
        let slotId = null;
        
        if (select.value.startsWith("slot_")) {
           slotId = select.value;
           const slot = (typeof progresoSlots !== "undefined" ? progresoSlots : []).find(s => s.id === slotId);
           if (slot) materiaId = slot.materia_id;
        }

        const payload = {
          filename: filename,
          materia_id: materiaId,
          slot_id: slotId,
          modelo_elegido:
            localStorage.getItem("modelo_elegido") || "gemini-1.5-flash",
          session_name: a ? a.session_name : null,
          session_dir: a ? a.session_dir : null,
          image_filenames: a ? a.image_filenames || [] : [],
        };

        fetch("/api/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
          .then((r) => r.json())
          .then((data) => {
            if (data.detail) throw new Error(data.detail);
            showToast("Añadido a la cola de procesamiento", "success");
            loadCola();
            loadAudios();
          })
          .catch((e) => {
            showToast("Fallo al encolar: " + e.message, "error");
          });
      }

      // ====== CARGA MANUAL DE RESPALDO ======
      async function uploadBackupAudio() {
        const input = document.getElementById("backup-audio-input");
        const btn = document.getElementById("btn-backup-upload");

        if (!input.files || input.files.length === 0) {
          showToast("Por favor, selecciona archivos de respaldo.", "error");
          return;
        }

        const files = Array.from(input.files);
        let audioFile = null;
        let imageFiles = [];
        let jsonFile = null;
        let zipFile = null;
        let customName = "";

        for (const file of files) {
          const ext = file.name.split('.').pop().toLowerCase();
          if (['webm', 'mp3', 'wav', 'ogg', 'm4a', 'mp4', 'aac'].includes(ext)) {
            audioFile = file;
          } else if (['jpg', 'jpeg', 'png', 'webp'].includes(ext)) {
            imageFiles.push(file);
          } else if (ext === 'json') {
            jsonFile = file;
          } else if (ext === 'zip') {
            zipFile = file;
          }
        }

        if (!zipFile && !audioFile) {
          showToast("Debes incluir al menos un archivo de audio o un archivo .zip.", "error");
          return;
        }

        const maxMBStr = document.getElementById("config-max-audio-mb")?.value || 500;
        const maxMB = parseInt(maxMBStr, 10);
        const mainFile = zipFile || audioFile;

        if (mainFile.size > maxMB * 1024 * 1024) {
          showToast(
            `El archivo pesa ${(mainFile.size / 1024 / 1024).toFixed(1)}MB. El límite es ${maxMB}MB.`,
            "error"
          );
          return;
        }

        const originalText = btn.innerHTML;
        btn.innerHTML =
          '<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline"></i> <span>Subiendo...</span>';
        lucide.createIcons({ root: btn });
        btn.disabled = true;
        input.disabled = true;

        try {
          const formData = new FormData();

          if (mainFile.name.includes("backup-")) {
            const parts = mainFile.name.split("-");
            if (parts.length >= 3 && parts[1].length > 0) {
              customName = parts[1];
              formData.append("custom_name", customName);
            }
          }



          if (zipFile) {
            formData.append("audio", zipFile, zipFile.name);
          } else {
            formData.append("audio", audioFile, audioFile.name);

            if (jsonFile) {
              const text = await jsonFile.text();
              const data = JSON.parse(text);
              if (data.capturas) {
                data.capturas.forEach(cap => {
                   const base64Data = cap.base64.split(',')[1] || cap.base64;
                   const byteString = atob(base64Data);
                   const arrayBuffer = new ArrayBuffer(byteString.length);
                   const int8Array = new Uint8Array(arrayBuffer);
                   for (let i = 0; i < byteString.length; i++) {
                     int8Array[i] = byteString.charCodeAt(i);
                   }
                   const blob = new Blob([int8Array], { type: 'image/jpeg' });
                   formData.append("imagenes", blob, cap.filename);
                });
              }
            }

            for (const img of imageFiles) {
              formData.append("imagenes", img, img.name);
            }
          }

          const response = await fetch("/upload", {
            method: "POST",
            body: formData,
          });

          if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.detail || `Error HTTP: ${response.status}`);
          }

          const data = await response.json();
          showToast(data.message || "Material subido correctamente.", "success");

          input.value = "";
          loadAudios(); // Refrescar la lista de pendientes
        } catch (err) {
          console.error("Error subiendo backup:", err);
          showToast(err.message || "Error al subir el archivo.", "error");
        } finally {
          btn.innerHTML = originalText;
          btn.disabled = false;
          input.disabled = false;
        }
      }

      // ====== CHAT DE ESTUDIO (RAG) ======
      function handleChatSubmit() {
        const input = document.getElementById("chat-input");
        const msg = input.value.trim();
        if (!msg) return;

        const context = document.getElementById("chat-context-select").value;
        input.value = "";

        addChatBubble(msg, "user");

        // Temporary loading bubble
        const chatHistory = document.getElementById("chat-history");
        const loadingBubble = document.createElement("div");
        loadingBubble.className =
          "chat-bubble chat-ai p-5 text-sm italic text-textMuted border border-borderGray rounded-lg font-serif";
        loadingBubble.innerText = "Indexando documentos...";
        chatHistory.appendChild(loadingBubble);
        chatHistory.scrollTop = chatHistory.scrollHeight;

        const modelStr =
          localStorage.getItem("modelo_elegido") || "gemini-1.5-flash";
        fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            mensaje: msg,
            materia_id: context,
            modelo_elegido: modelStr,
          }),
        })
          .then((r) => r.json())
          .then((data) => {
            chatHistory.removeChild(loadingBubble);
            addChatBubble(data.respuesta, "ai");

          })
          .catch((e) => {
            chatHistory.removeChild(loadingBubble);
            addChatBubble("Error del motor de inferencia: " + e.message, "ai");
          });
      }

      function addChatBubble(text, type) {
        const chatHistory = document.getElementById("chat-history");
        const bubble = document.createElement("div");
        bubble.className = `chat-bubble p-5 text-sm prose ${type === "user" ? "chat-user" : "chat-ai"}`;

        if (type === "ai") {
          bubble.innerHTML = renderMarkdown(text);
        } else {
          bubble.innerText = text;
        }

        chatHistory.appendChild(bubble);
        chatHistory.scrollTop = chatHistory.scrollHeight;
      }
      // ====== TAB: TUTOR SOCRÁTICO ======
      let currentTutorV2SlotId = null;
      let currentTutorV2ImageData = null;

      function clearTutorImage() {
         currentTutorV2ImageData = null;
         document.getElementById('tutor-image-preview').classList.add('hidden');
         document.getElementById('tutor-image-upload').value = "";
      }

      function previewTutorImage(event) {
         const file = event.target.files[0];
         if (!file) return;
         const reader = new FileReader();
         reader.onload = (e) => {
            currentTutorV2ImageData = e.target.result;
            document.getElementById('tutor-image-preview-img').src = currentTutorV2ImageData;
            document.getElementById('tutor-image-preview').classList.remove('hidden');
         };
         reader.readAsDataURL(file);
      }

      function iniciarSimulacionTutor() {
        currentTutorV2SlotId = null;
        clearTutorImage();
        
        const select = document.getElementById("tutor-materia-select");
        if (!select.value) {
           showToast("Por favor selecciona una asignatura primero.", "error");
           return;
        }

        tutorHistoryState = [];
        const historyDiv = document.getElementById("tutor-history");
        historyDiv.innerHTML = "";

        const input = document.getElementById("tutor-input");
        const btn = document.getElementById("tutor-send-btn");

        input.disabled = false;
        input.classList.remove("opacity-60", "cursor-not-allowed");
        input.placeholder = "Responde al profesor...";

        btn.disabled = false;
        btn.classList.remove("opacity-60", "cursor-not-allowed");

        const matName = select.options[select.selectedIndex].text;

        const inicioText = `¡Hola! Soy tu tutor virtual. He analizado tus apuntes de la materia **${matName}**. ¿Estás listo para comenzar con la primera pregunta de razonamiento para evaluar tus conocimientos?`;
        appendTutorMessage(inicioText, false);

        tutorHistoryState.push({
          role: "model",
          text: inicioText,
        });

        input.focus();
      }

      function iniciarSimulacionTutorV2(slotId) {
          const slot = progresoSlots.find(s => s.id === slotId);
          if (!slot) return;
          
          currentTutorV2SlotId = slotId;
          clearTutorImage();
          tutorHistoryState = [];
          
          const historyDiv = document.getElementById("tutor-history");
          historyDiv.innerHTML = "";

          const input = document.getElementById("tutor-input");
          const btn = document.getElementById("tutor-send-btn");
          
          input.disabled = false;
          input.classList.remove("opacity-60", "cursor-not-allowed");
          input.placeholder = "Responde al Tutor V2...";
          btn.disabled = false;
          btn.classList.remove("opacity-60", "cursor-not-allowed");
          
          const mat = materias.find(m => m.id === slot.materia_id);
          const matName = mat ? mat.nombre : "Clase";
          
          const temasPendientes = slot.temas ? slot.temas.filter(t => (t.dominio || 0) < 100).length : 0;
          
          const inicioText = `¡Hola! Soy tu **Tutor Cognitivo V2**. He analizado la sesión de **${matName}** del ${slot.fecha}. He detectado ${temasPendientes} conceptos que debemos certificar hoy. ¿Comenzamos con la evaluación?`;
          appendTutorMessage(inicioText, false);

          tutorHistoryState.push({
            role: "model",
            text: inicioText,
          });

          input.focus();
      }

      function appendTutorMessage(text, isUser = false) {
        const historyDiv = document.getElementById("tutor-history");
        const bubble = document.createElement("div");

        if (isUser) {
          bubble.className = `chat-bubble chat-user p-5 text-sm prose dark:prose-invert max-w-none shadow-sm`;
          bubble.innerHTML = escapeHTML(text);
        } else {
          bubble.className = `chat-bubble chat-ai border border-borderGray p-5 text-sm prose dark:prose-invert max-w-none`;
          
          // Parse natural multiple choice A) B) C) to interactive UI
          let parsedText = text;
          
          // Limpiar residuos de XML en caso de que Gemini se confunda
          parsedText = parsedText.replace(/<\/?quiz\s*>/gi, '');
          parsedText = parsedText.replace(/<\/?opcion[^>]*>/gi, '');

          parsedText = parsedText.replace(/^([A-E])\)\s+(.*)$/gm, (match, optId, optText) => {
              const cleanText = optText.replace(/"/g, '&quot;').replace(/'/g, "\\'");
              return `<div class="mt-2 w-full not-prose"><button onclick="document.getElementById('tutor-input').value = '${optId}) ${cleanText}'; document.getElementById('tutor-send-btn').click();" class="text-left w-full px-4 py-3 rounded-lg border border-borderGray bg-surface hover:border-charcoal hover:shadow-sm transition-all flex gap-3 text-sm font-medium text-charcoal"><span class="font-bold text-textMuted">${optId})</span><span>${optText}</span></button></div>`;
          });
          
          bubble.innerHTML = renderMarkdown(parsedText);
        }

        historyDiv.appendChild(bubble);
        historyDiv.scrollTop = historyDiv.scrollHeight;
      }

      async function handleTutorSubmit(e) {
        if (e) e.preventDefault();
        const input = document.getElementById("tutor-input");
        const btn = document.getElementById("tutor-send-btn");
        const text = input.value.trim();
        
        // We can send image only without text
        if (!text && !currentTutorV2ImageData) return;

        const materiaId = document.getElementById("tutor-materia-select").value;
        const model = localStorage.getItem("modelo_elegido") || "gemini-1.5-flash";

        if (currentTutorV2ImageData) {
            appendTutorMessage(text ? text + "\\n*[Imagen adjunta]*" : "*[Imagen adjunta]*", true);
        } else {
            appendTutorMessage(text, true);
        }
        
        input.value = "";
        input.disabled = true;
        btn.disabled = true;
        const imgBtn = document.getElementById("tutor-img-btn");
        if (imgBtn) imgBtn.disabled = true;
        
        const originalText = btn.innerHTML;
        btn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i>';
        lucide.createIcons({ root: btn });

        // Save image before clearing UI
        const imageToSend = currentTutorV2ImageData;
        clearTutorImage();

        try {
          let response, data;
          
          if (currentTutorV2SlotId) {
              response = await fetch("/api/tutor/v2/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  slot_id: currentTutorV2SlotId,
                  historial: tutorHistoryState,
                  pregunta: text,
                  modelo_elegido: model,
                  image_data: imageToSend
                }),
              });
          } else {
              response = await fetch("/api/tutor/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  materia_id: materiaId,
                  historial: tutorHistoryState,
                  pregunta: text,
                  modelo_elegido: model,
                }),
              });
          }

          data = await response.json();

          if (!response.ok)
            throw new Error(data.detail || "Error en la conexión del tutor");

          tutorHistoryState.push({ role: "user", text: text });
          tutorHistoryState.push({ role: "model", text: data.respuesta });

          appendTutorMessage(data.respuesta, false);
          
          if (data.updated && currentTutorV2SlotId) {
             showToast("¡Dominio actualizado en la base de datos!", "success");
             loadProgreso(); // Refresh the progress background state
          }

        } catch (error) {
          console.error(error);
          appendTutorMessage(
            "⚠️ Error de conexión con el modelo. Por favor, intenta de nuevo.",
            false,
          );
        } finally {
          input.disabled = false;
          btn.disabled = false;
          if (imgBtn) imgBtn.disabled = false;
          btn.innerHTML = "Enviar";
          input.focus();
        }
      }

      // ====== TAB: PROGRESO ======
      let progresoSlots = [];
      function loadProgreso() {
         return fetch("/api/progreso")
            .then(res => res.json())
            .then(data => {
               progresoSlots = data.slots || [];
               renderProgresoGrid();
               populateWeeksDropdown();
               updateBackupSlotSelect();
               updateEntregablesSidebar();
            })
            .catch(err => console.error("Error cargando progreso:", err));
      }

      function renderProgresoGrid() {
         const grid = document.getElementById("progreso-grid");
         grid.innerHTML = "";
         
         const baseDateStr = document.getElementById("progreso-base-date").value;
         if(!baseDateStr) return;
         
         const [y, m, d] = baseDateStr.split('-');
         const baseDate = new Date(y, m - 1, d);
         const dayOfWeek = baseDate.getDay(); 
         const diff = baseDate.getDate() - dayOfWeek + (dayOfWeek === 0 ? -6 : 1);
         const monday = new Date(baseDate.setDate(diff));
         
         const weekDates = [];
         for(let i=0; i<6; i++) {
             const dt = new Date(monday);
             dt.setDate(monday.getDate() + i);
             const isoDate = dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, '0') + "-" + String(dt.getDate()).padStart(2, '0');
             weekDates.push(isoDate);
         }
         
         const days = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"];
         const slotsByDay = {};
         days.forEach((day, index) => {
             slotsByDay[day] = {
                 date: weekDates[index],
                 slots: []
             };
         });
         
         let totalSlotsInWeek = 0;
         progresoSlots.forEach(s => {
             const dayName = s.dia_semana || s.dia;
             if(slotsByDay[dayName] && weekDates.includes(s.fecha)) {
                 slotsByDay[dayName].slots.push(s);
                 totalSlotsInWeek++;
             }
         });
         
         const btnEliminar = document.getElementById("btn-eliminar-semana");
         if (btnEliminar) {
             if (totalSlotsInWeek > 0) {
                 btnEliminar.classList.remove("hidden");
             } else {
                 btnEliminar.classList.add("hidden");
             }
         }
         
         let finalHtml = "";
         
         days.forEach(day => {
            const dayData = slotsByDay[day];
            const daySlots = dayData.slots;
            
            daySlots.sort((a,b) => a.fecha.localeCompare(b.fecha));
            
            let html = `
              <div class="flex flex-col gap-3">
                 <div class="text-center pb-2 border-b border-borderGray">
                    <span class="text-[11px] font-bold text-textMuted uppercase tracking-wider">${day}</span>
                    <div class="text-[9px] text-textMuted font-mono mt-1">${dayData.date}</div>
                 </div>
                 <div class="flex flex-col gap-3 flex-1">
            `;
            
            if(daySlots.length === 0) {
               html += `<div class="flex-1 flex items-center justify-center text-textMuted text-xs font-serif italic border border-dashed border-borderGray rounded-lg bg-surface/30 p-4 text-center">Libre</div>`;
            } else {
               daySlots.forEach(slot => {
                  const mat = materias.find(m => m.id === slot.materia_id);
                  const matName = mat ? mat.nombre : slot.materia_id;
                  
                  let bg = "bg-surface";
                  let border = "border-borderGray";
                  let icon = `<i data-lucide="circle-dashed" class="w-4 h-4 text-textMuted"></i>`;
                  
                  if (slot.estado === "AUSENTE") {
                     bg = "bg-red-50 dark:bg-red-950/20";
                     border = "border-red-200 dark:border-red-900/50";
                     icon = `<i data-lucide="alert-circle" class="w-4 h-4 text-red-500"></i>`;
                  } else if (slot.estado === "EN_COLA") {
                     bg = "bg-amber-50 dark:bg-amber-950/20";
                     border = "border-amber-200 dark:border-amber-900/50";
                     icon = `<i data-lucide="loader-2" class="w-4 h-4 text-amber-500 animate-spin"></i>`;
                  } else if (slot.estado === "AL_DIA") { // Legacy (Solo grabación)
                     bg = "bg-blue-50 dark:bg-blue-950/20";
                     border = "border-blue-200 dark:border-blue-900/50";
                     icon = `<i data-lucide="file-audio" class="w-4 h-4 text-blue-500"></i>`;
                  } else if (slot.estado === "EN_PROGRESO") {
                     bg = "bg-indigo-50 dark:bg-indigo-950/20";
                     border = "border-indigo-200 dark:border-indigo-900/50";
                     const prog = slot.progreso_global || 0;
                     const dashoffset = 50.2 * (1 - prog / 100);
                     icon = `<div class="relative w-5 h-5 flex items-center justify-center">
                               <svg class="w-5 h-5 transform -rotate-90 absolute">
                                 <circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2" fill="transparent" class="text-indigo-200 dark:text-indigo-900/50"/>
                                 <circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2" fill="transparent" stroke-dasharray="50.2" stroke-dashoffset="${dashoffset}" class="text-indigo-500 transition-all duration-1000 ease-out"/>
                               </svg>
                               <span class="text-[7px] font-bold text-indigo-700 dark:text-indigo-300 absolute">${prog}%</span>
                             </div>`;
                  } else if (slot.estado === "DOMINADO") {
                     bg = "bg-emerald-50 dark:bg-emerald-950/20";
                     border = "border-emerald-200 dark:border-emerald-900/50";
                     icon = `<i data-lucide="award" class="w-4 h-4 text-emerald-500"></i>`;
                  }
                  
                  html += `
                    <div onclick="openSlotModal('${slot.id}')" class="${bg} border ${border} p-4 rounded-xl flex flex-col gap-2 relative group hover:shadow-md transition-shadow cursor-pointer">
                        <div class="flex justify-between items-start">
                           <span class="text-[10px] font-bold text-textMuted font-mono">${slot.fecha}</span>
                           ${icon}
                        </div>
                        <h4 class="text-sm font-bold text-charcoal leading-tight">${matName}</h4>
                        <div class="mt-auto pt-2 text-[10px] uppercase font-bold tracking-wider text-textMuted">
                           ${slot.estado}
                        </div>
                        <button onclick="event.stopPropagation(); deleteSlot('${slot.id}')" class="absolute top-2 right-8 opacity-0 group-hover:opacity-100 text-textMuted hover:text-red-500 transition-opacity" title="Eliminar Slot">
                           <i data-lucide="x" class="w-3.5 h-3.5"></i>
                        </button>
                    </div>
                  `;
               });
            }
            
            html += `</div></div>`;
            finalHtml += html;
         });
         grid.innerHTML = finalHtml;
         lucide.createIcons();
      }
      
      function deleteSlot(id) {
         if(!confirm("¿Eliminar este hueco del control semanal?")) return;
         fetch(`/api/progreso/${id}`, { method: "DELETE" })
            .then(() => loadProgreso())
            .catch(err => showToast("Error al eliminar slot", "error"));
      }

      function generarSemanaUI() {
         const date = document.getElementById("progreso-base-date").value;
         if(!date) return;
         fetch(`/api/progreso/generar_semana`, { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fecha_base: date })
         })
            .then(res => res.json())
            .then(data => {
               if(data.status === "success") {
                  showToast(`Semana de ${date} generada.`);
                  loadProgreso();
               } else {
                  showToast("Error generando semana", "error");
               }
            })
            .catch(err => {
               console.error(err);
               showToast("Error generando semana", "error");
            });
      }

      function eliminarSemanaUI() {
         const date = document.getElementById("progreso-base-date").value;
         if(!date) return;
         if(!confirm(`¿Estás seguro de que quieres eliminar todos los registros generados para la semana del ${date}? Esta acción no se puede deshacer.`)) return;
         
         fetch(`/api/progreso/eliminar_semana?fecha_base=${date}`, { method: 'DELETE' })
            .then(res => res.json())
            .then(data => {
               if(data.status === "success") {
                  showToast(`Semana eliminada (${data.slots_eliminados} registros borrados)`);
                  loadProgreso();
               } else {
                  showToast("Error eliminando semana", "error");
               }
            })
            .catch(err => {
               console.error(err);
               showToast("Error eliminando semana", "error");
            });
      }

      let currentSlotModalId = null;

      function openSlotModal(slotId) {
          const slot = progresoSlots.find(s => s.id === slotId);
          if (!slot) return;
          
          currentSlotModalId = slotId;
          
          const modal = document.getElementById("slot-modal");
          const content = document.getElementById("slot-modal-content");
          
          // Set Header
          const mat = materias.find(m => m.id === slot.materia_id);
          document.getElementById("slot-modal-title").textContent = mat ? mat.nombre : "Clase";
          
          // Use UTC for date parsing to avoid timezone shifts
          const parts = slot.fecha.split('-');
          const dateObj = new Date(Date.UTC(parts[0], parts[1]-1, parts[2]));
          const options = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
          document.getElementById("slot-modal-subtitle").textContent = dateObj.toLocaleDateString('es-ES', options);
          
          // Set Status
          const statusText = document.getElementById("slot-modal-status-text");
          const statusDot = document.getElementById("slot-modal-status-dot");
          const attendanceControls = document.getElementById("slot-modal-attendance");
          const actionsContainer = document.getElementById("slot-modal-actions");
          
          statusText.textContent = slot.estado;
          actionsContainer.innerHTML = "";
          
          if (slot.estado === "AL_DIA" || slot.estado === "DOMINADO" || slot.estado === "EN_PROGRESO") {
              if (slot.estado === "AL_DIA") statusDot.className = "w-3 h-3 rounded-full bg-blue-500 shrink-0";
              else if (slot.estado === "DOMINADO") statusDot.className = "w-3 h-3 rounded-full bg-emerald-500 shrink-0";
              else if (slot.estado === "EN_PROGRESO") statusDot.className = "w-3 h-3 rounded-full bg-indigo-500 shrink-0";
              
              attendanceControls.classList.add("hidden");
              
              if (slot.md_vinculado) {
                  const btn = document.createElement("button");
                  btn.className = "btn-primary text-xs px-4 py-2 font-medium flex items-center gap-2 shadow-sm hover:shadow transition-all shrink-0";
                  btn.innerHTML = `<i data-lucide="book-open" class="w-4 h-4"></i> Abrir Apuntes`;
                  btn.onclick = () => {
                      closeSlotModal();
                      abrirDocumentoDesdeSlot(slot.md_vinculado);
                  };
                  actionsContainer.appendChild(btn);
                  
                  if (slot.estado === "EN_PROGRESO") {
                     const btnStudy = document.createElement("button");
                     btnStudy.className = "btn-secondary text-xs px-4 py-2 font-medium flex items-center gap-2 shadow-sm hover:shadow transition-all shrink-0 bg-surface border border-borderGray text-accent";
                     btnStudy.innerHTML = `<i data-lucide="brain-circuit" class="w-4 h-4"></i> Entrar a Sala`;
                     btnStudy.onclick = () => {
                         closeSlotModal();
                         switchTab('tutor');
                         setTimeout(() => iniciarSimulacionTutorV2(slot.id), 100);
                     };
                     actionsContainer.appendChild(btnStudy);
                  }
              }
          } else {
              if (slot.estado === "AUSENTE") statusDot.className = "w-3 h-3 rounded-full bg-red-500 shrink-0";
              else if (slot.estado === "EN_COLA") statusDot.className = "w-3 h-3 rounded-full bg-amber-500 animate-pulse shrink-0";
              else statusDot.className = "w-3 h-3 rounded-full bg-yellow-500 shrink-0";
              
              attendanceControls.classList.remove("hidden");
              
              const btnUpload = document.createElement("button");
              btnUpload.className = "btn-secondary text-xs px-4 py-2 font-medium flex items-center gap-2 shadow-sm hover:shadow transition-all bg-surface border border-borderGray shrink-0";
              btnUpload.innerHTML = `<i data-lucide="upload-cloud" class="w-4 h-4"></i> Subir Grabación`;
              btnUpload.onclick = () => {
                  closeSlotModal();
                  switchTab('audios');
                  setTimeout(() => {
                      const uploadSection = document.getElementById("backup-audio-input");
                      if(uploadSection) {
                          uploadSection.scrollIntoView({ behavior: 'smooth', block: 'center' });
                      }
                  }, 150);
              };
              actionsContainer.appendChild(btnUpload);
          }
          
          // Temario Atómico
          const temasContainer = document.getElementById("slot-modal-temas-container");
          const temasList = document.getElementById("slot-modal-temas-list");
          if (slot.temas && slot.temas.length > 0) {
              temasContainer.classList.remove("hidden");
              temasList.innerHTML = "";
              slot.temas.forEach(tema => {
                  const dom = tema.dominio || 0;
                  const bgBar = dom === 100 ? "bg-emerald-500" : "bg-indigo-500";
                  temasList.innerHTML += `
                     <div class="flex flex-col gap-2 p-3 bg-surface border border-borderGray rounded-lg">
                        <div class="flex justify-between items-start">
                           <span class="text-xs font-bold text-charcoal">${tema.nombre}</span>
                           <span class="text-[10px] font-mono text-textMuted">${dom}%</span>
                        </div>
                        <p class="text-[10px] text-textMuted leading-tight">${tema.profundidad_sesion}</p>
                        <div class="w-full h-1.5 bg-surface rounded-full overflow-hidden mt-1">
                           <div class="h-full ${bgBar} transition-all duration-500" style="width: ${dom}%"></div>
                        </div>
                     </div>
                  `;
              });
          } else {
              temasContainer.classList.add("hidden");
          }
          
          // Tareas / Avisos
          const tasksContainer = document.getElementById("slot-modal-tasks");
          const tasksList = document.getElementById("slot-modal-tasks-list");
          const btnAddTask = document.getElementById("btn-add-manual-task");
          const addTaskForm = document.getElementById("slot-modal-add-task-form");
          const btnSaveTask = document.getElementById("btn-save-manual-task");
          const btnCancelTask = document.getElementById("btn-cancel-manual-task");
          
          tasksContainer.classList.add("hidden");
          btnAddTask.classList.add("hidden");
          addTaskForm.classList.add("hidden");
          tasksList.innerHTML = `<p class="text-xs text-textMuted italic">Cargando avisos...</p>`;
          
          if (slot.estado === "AL_DIA" || slot.estado === "EN_PROGRESO" || slot.estado === "DOMINADO") {
              tasksContainer.classList.remove("hidden");
              btnAddTask.classList.remove("hidden");
              
              // Reset Form
              document.getElementById("manual-task-content").value = "";
              document.getElementById("manual-task-date").value = "";
              
              btnAddTask.onclick = () => {
                  addTaskForm.classList.remove("hidden");
                  btnAddTask.classList.add("hidden");
              };
              
              btnCancelTask.onclick = () => {
                  addTaskForm.classList.add("hidden");
                  btnAddTask.classList.remove("hidden");
              };
              
              btnSaveTask.onclick = () => {
                  const content = document.getElementById("manual-task-content").value.trim();
                  const type = document.getElementById("manual-task-type").value;
                  const dateStr = document.getElementById("manual-task-date").value.trim();
                  if (!content) return;
                  
                  btnSaveTask.disabled = true;
                  btnSaveTask.textContent = "Guardando...";
                  
                  fetch("/api/tarjetas", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({
                          materia_id: slot.materia_id,
                          origen_md: slot.md_vinculado || "",
                          origen_slot_id: slot.id,
                          contenido: content,
                          tipo: type,
                          referencia_temporal: dateStr
                      })
                  }).then(r => r.json()).then(res => {
                      btnSaveTask.disabled = false;
                      btnSaveTask.textContent = "Guardar";
                      addTaskForm.classList.add("hidden");
                      btnAddTask.classList.remove("hidden");
                      showToast("Anuncio creado exitosamente", "success");
                      // Recargar la lista de tareas
                      loadSlotTasks();
                      // Recargar el sidebar de entregables si está abierto
                      updateEntregablesSidebar();
                  }).catch(err => {
                      btnSaveTask.disabled = false;
                      btnSaveTask.textContent = "Guardar";
                      showToast("Error al crear el anuncio", "error");
                  });
              };
              
              const loadSlotTasks = () => {
                  fetch('/api/tarjetas?materia_id=todas')
                      .then(res => res.json())
                      .then(data => {
                          if (data.tarjetas) {
                              const slotCards = data.tarjetas.filter(t => t.origen_slot_id === slotId);
                              if (slotCards.length > 0) {
                                  tasksList.innerHTML = "";
                                  slotCards.forEach(t => {
                                      const card = document.createElement("div");
                                      const cardId = 'slot-card-' + t.id;
                                      card.id = cardId;
                                      card.className = "bg-background border border-borderGray rounded-lg p-4 flex gap-4 items-start shadow-sm group";
                                      card.innerHTML = `
                                          <div class="mt-0.5 shrink-0">
                                             <i data-lucide="${t.tipo === 'aviso' ? 'bell' : 'check-square'}" class="w-4 h-4 text-textMuted"></i>
                                          </div>
                                          <div class="flex-1 min-w-0">
                                              <h5 class="text-sm font-bold text-charcoal leading-tight mb-1 truncate">${t.titulo || 'Anuncio'}</h5>
                                              <p class="text-xs text-textMuted whitespace-normal break-words">${t.descripcion || t.contenido || 'Sin descripción'}</p>
                                          </div>
                                          <button onclick="deleteTarjeta('${t.id}', '${cardId}')" class="text-textMuted hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity p-1">
                                              <i data-lucide="trash-2" class="w-4 h-4"></i>
                                          </button>
                                      `;
                                      tasksList.appendChild(card);
                                  });
                                  lucide.createIcons({ root: tasksList });
                              } else {
                                  tasksList.innerHTML = `<p class="text-xs text-textMuted italic">No hay avisos extraídos para esta sesión.</p>`;
                              }
                          }
                      })
                      .catch(err => console.error("Error cargando tarjetas para modal:", err));
              }
              loadSlotTasks();
          }
          
          modal.classList.remove("hidden");
          // Trigger reflow for transition
          void modal.offsetWidth;
          modal.classList.remove("opacity-0");
          content.classList.remove("scale-95");
          lucide.createIcons();
      }

      function closeSlotModal() {
          const modal = document.getElementById("slot-modal");
          const content = document.getElementById("slot-modal-content");
          
          modal.classList.add("opacity-0");
          content.classList.add("scale-95");
          
          setTimeout(() => {
              modal.classList.add("hidden");
          }, 300);
          currentSlotModalId = null;
      }

      function updateSlotStateModal(newState) {
          if (!currentSlotModalId) return;
          
          // Si cambian a FERIADO, vamos a mapearlo a AUSENTE internamente, o usar FERIADO si el backend lo soporta. 
          // En nuestro sistema, el backend acepta string libre en `estado`.
          
          fetch(`/api/progreso/${currentSlotModalId}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ estado: newState })
          })
          .then(res => res.json())
          .then(data => {
              showToast("Estado actualizado correctamente.");
              loadProgreso();
              closeSlotModal();
          })
          .catch(err => showToast("Error al actualizar estado", "error"));
      }

      function abrirDocumentoDesdeSlot(md_filename) {
          const filter = document.getElementById("doc-materia-filter");
          if (filter) filter.value = "todas";
          
          // Clear any search filter if present
          const searchFilter = document.getElementById("doc-search-filter");
          if (searchFilter) {
              searchFilter.value = "";
              setTimeout(() => { if (typeof filterSummariesList === 'function') filterSummariesList(); }, 100);
          }
          
          switchTab('resumenes');
          
          // Cargar el documento programáticamente SIN depender del DOM de la barra lateral
          cargarYMostrarDocumento(md_filename);
          
          // Poll to highlight in sidebar once it renders asynchronously
          let attempts = 0;
          const interval = setInterval(() => {
              const fileItem = document.querySelector(`#summaries-list div[data-filename="${md_filename}"]`);
              if (fileItem) {
                  clearInterval(interval);
                  document.querySelectorAll("#summaries-list div").forEach((d) => {
                      d.style.backgroundColor = "transparent";
                      d.style.borderColor = "transparent";
                  });
                  fileItem.style.backgroundColor = "var(--color-bg)";
                  fileItem.style.borderColor = "var(--color-border)";
                  const sidebar = document.getElementById("biblioteca-sidebar");
                  if (sidebar && !sidebar.classList.contains("collapsed")) {
                      fileItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
                  }
              } else if (attempts > 30) {
                  clearInterval(interval);
              }
              attempts++;
          }, 100);
      }

      function getMonday(dStr) {
          if (!dStr) return null;
          const [y, m, d] = dStr.split('-');
          const dt = new Date(y, m - 1, d);
          const day = dt.getDay();
          const diff = dt.getDate() - day + (day === 0 ? -6 : 1);
          return new Date(dt.setDate(diff)).toISOString().split('T')[0];
      }

      function populateWeeksDropdown() {
          const dropdown = document.getElementById("progreso-weeks-dropdown");
          if (!progresoSlots || progresoSlots.length === 0) {
              dropdown.classList.add("hidden");
              return;
          }
          dropdown.classList.remove("hidden");
          
          const mondays = [...new Set(progresoSlots.map(s => getMonday(s.fecha)))];
          mondays.sort((a, b) => new Date(b) - new Date(a)); // Más recientes primero
          
          dropdown.innerHTML = `<option value="">Semanas Generadas</option>`;
          const months = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
          
          mondays.forEach(mondayStr => {
              if(!mondayStr) return;
              const [y1, m1, d1] = mondayStr.split('-');
              const dt1 = new Date(y1, m1 - 1, d1);
              
              const dt2 = new Date(dt1);
              dt2.setDate(dt2.getDate() + 6);
              
              const label = `${dt1.getDate()} ${months[dt1.getMonth()]} - ${dt2.getDate()} ${months[dt2.getMonth()]} ${dt1.getFullYear() !== new Date().getFullYear() ? dt1.getFullYear() : ''}`.trim();
              
              const option = document.createElement("option");
              option.value = mondayStr;
              option.textContent = label;
              dropdown.appendChild(option);
          });
          
          syncDropdownValue();
      }

      function syncDropdownValue() {
          const dropdown = document.getElementById("progreso-weeks-dropdown");
          const baseDateStr = document.getElementById("progreso-base-date").value;
          if(baseDateStr && typeof getMonday === 'function') {
             const monday = getMonday(baseDateStr);
             dropdown.value = monday || "";
          }
      }

      function irASemanaActual() {
          const dateInput = document.getElementById("progreso-base-date");
          const today = new Date();
          const todayISO = today.toISOString().split('T')[0];
          
          if (!progresoSlots || progresoSlots.length === 0) {
              dateInput.value = todayISO;
              localStorage.setItem('last_progreso_date', todayISO);
              renderProgresoGrid();
              return;
          }
          
          const todayMonday = getMonday(todayISO);
          const hasTodayWeek = progresoSlots.some(s => getMonday(s.fecha) === todayMonday);
          
          if (hasTodayWeek) {
              dateInput.value = todayISO;
          } else {
              let closestDateStr = null;
              let minDiff = Infinity;
              
              const mondays = [...new Set(progresoSlots.map(s => getMonday(s.fecha)))];
              const todayTime = new Date(todayMonday).getTime();
              
              mondays.forEach(mondayStr => {
                  const mTime = new Date(mondayStr).getTime();
                  const diff = Math.abs(mTime - todayTime);
                  
                  if (diff < minDiff) {
                      minDiff = diff;
                      closestDateStr = mondayStr;
                  } else if (diff === minDiff) {
                      if (mTime > new Date(closestDateStr).getTime()) {
                          closestDateStr = mondayStr;
                      }
                  }
              });
              
              dateInput.value = closestDateStr || todayISO;
          }
          
          localStorage.setItem('last_progreso_date', dateInput.value);
          renderProgresoGrid();
          syncDropdownValue();
          showToast("Ubicado en el Presente");
      }

      function updateBackupSlotSelect() {
         // Dropdown removed as per user request (linking happens in queue UI instead)
      }

      function updateEntregablesSidebar() {
         const list = document.getElementById("entregables-sidebar-list");
         if(!list) return;
         
         fetch(`/api/tarjetas?materia_id=todas`)
            .then(res => res.json())
            .then(data => {
               list.innerHTML = "";
               const entregables = data.tarjetas.filter(t => t.estado === "PENDIENTE" && t.tipo !== "aviso");
               
               if(entregables.length === 0) {
                  list.innerHTML = `<div class="text-[11px] text-textMuted italic font-serif text-center mt-8">No hay entregables pendientes.</div>`;
                  return;
               }
               
               // Sort by fecha_entrega if available
               entregables.sort((a,b) => (a.fecha_entrega || "ZZZ").localeCompare(b.fecha_entrega || "ZZZ"));
               
               entregables.forEach(t => {
                  const mat = materias.find(m => m.id === t.materia_id);
                  const matName = mat ? mat.nombre : t.materia_id;
                  
                  let badgeColor = "bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800";
                  if (t.tipo === "examen") badgeColor = "bg-red-50 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800";
                  
                  list.innerHTML += `
                     <div class="bg-background border border-borderGray p-3 rounded-lg flex flex-col gap-2">
                        <div class="flex justify-between items-start">
                           <span class="px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border ${badgeColor}">
                              ${t.tipo}
                           </span>
                           <span class="text-[9px] text-textMuted font-mono font-bold">${t.fecha_entrega || t.referencia_temporal}</span>
                        </div>
                        <p class="text-xs text-charcoal font-medium leading-snug">${t.contenido}</p>
                        <div class="text-[9px] font-bold text-textMuted uppercase tracking-wider truncate mt-1">
                           ${matName}
                        </div>
                     </div>
                  `;
               });
            });
      }

      // ====== IMAGE CAROUSEL & PREVIEW ======
      function scrollCarousel(carouselId, direction) {
        const track = document.getElementById(carouselId);
        if (!track) return;
        const scrollAmount = 160;
        track.scrollBy({ left: direction * scrollAmount, behavior: "smooth" });
      }

      let currentPreviewImages = [];
      let currentPreviewIndex = 0;

      function openImagePreview(src, caption, index, allImages) {
        currentPreviewImages = allImages || [src];
        currentPreviewIndex = index || 0;

        const modal = document.getElementById("image-preview-modal");
        const img = document.getElementById("image-preview-img");
        const cap = document.getElementById("image-preview-caption");
        const prevBtn = document.getElementById("image-preview-prev");
        const nextBtn = document.getElementById("image-preview-next");

        img.src = src;
        cap.textContent = caption || "";
        
        // Reset zoom state
        resetZoomState(modal, img);

        // Show/hide nav arrows
        if (currentPreviewImages.length > 1) {
          prevBtn.style.display = currentPreviewIndex > 0 ? "flex" : "none";
          nextBtn.style.display =
            currentPreviewIndex < currentPreviewImages.length - 1
              ? "flex"
              : "none";
        } else {
          prevBtn.style.display = "none";
          nextBtn.style.display = "none";
        }

        modal.classList.remove("hidden");
        requestAnimationFrame(() => modal.classList.add("active"));
      }

      const zoomLevels = [0, 1, 1.5, 2, 3];
      let currentScale = 0; // 0 significa estado "ajustado" por defecto

      function resetZoomState(modal, img) {
         currentScale = 0;
         img.style.width = "";
         img.style.height = "";
         img.classList.remove("zoomed");
         modal.style.overflow = "hidden";
         modal.style.alignItems = "center";
         modal.style.justifyContent = "center";
         img.style.cursor = "zoom-in";
      }

      function applyZoom(newScale, eventX, eventY) {
         const modal = document.getElementById("image-preview-modal");
         const img = document.getElementById("image-preview-img");
         
         if (newScale <= 0 || newScale < 0.5) {
             resetZoomState(modal, img);
             return;
         }
         if (newScale > 5) newScale = 5; // Límite máximo
         
         // Guardar la proporción relativa de donde está el ratón ANTES de escalar
         const imgRect = img.getBoundingClientRect();
         const clickXRatio = (eventX - imgRect.left) / imgRect.width;
         const clickYRatio = (eventY - imgRect.top) / imgRect.height;
         
         currentScale = newScale;
         
         img.classList.add("zoomed");
         modal.style.overflow = "auto";
         modal.style.alignItems = "flex-start";
         modal.style.justifyContent = "flex-start";
         
         const nativeWidth = img.naturalWidth || img.width;
         const newWidth = nativeWidth * currentScale;
         img.style.width = newWidth + "px";
         img.style.height = "auto";
         img.style.cursor = currentScale >= 3 ? "zoom-out" : "zoom-in";
         
         // Forzar reflow
         const newHeight = img.offsetHeight;
         
         // Ajustar scroll para mantener el ratón en la misma posición de la imagen
         const targetX = (newWidth * clickXRatio) - (eventX - modal.getBoundingClientRect().left);
         const targetY = (newHeight * clickYRatio) - (eventY - modal.getBoundingClientRect().top);
         
         modal.scrollLeft = targetX;
         modal.scrollTop = targetY;
      }

      let isPanning = false;
      let startX, startY, scrollLeft, scrollTop;
      let draggedDistance = 0;

      document.addEventListener("DOMContentLoaded", () => {
         const modal = document.getElementById("image-preview-modal");
         
         modal.addEventListener("mousedown", (e) => {
             const img = document.getElementById("image-preview-img");
             if (!img.classList.contains("zoomed")) return;
             if (e.target !== modal && e.target !== img) return;
             
             e.preventDefault(); // Prevents native image ghost drag
             isPanning = true;
             draggedDistance = 0;
             startX = e.pageX - modal.offsetLeft;
             startY = e.pageY - modal.offsetTop;
             scrollLeft = modal.scrollLeft;
             scrollTop = modal.scrollTop;
             modal.style.cursor = "grabbing";
         });
         
         modal.addEventListener("mouseleave", () => {
             isPanning = false;
             modal.style.cursor = "";
         });
         
         modal.addEventListener("mouseup", () => {
             isPanning = false;
             modal.style.cursor = "";
         });
         
         modal.addEventListener("mousemove", (e) => {
             if (!isPanning) return;
             e.preventDefault();
             const x = e.pageX - modal.offsetLeft;
             const y = e.pageY - modal.offsetTop;
             const walkX = (x - startX);
             const walkY = (y - startY);
             // Solo sumar distancia relativa pequeña para detectar drag vs click
             draggedDistance = Math.abs(x - startX) + Math.abs(y - startY);
             modal.scrollLeft = scrollLeft - walkX;
             modal.scrollTop = scrollTop - walkY;
         });
         
         // Zoom con Rueda del Ratón
         modal.addEventListener("wheel", (e) => {
             if (modal.classList.contains("hidden")) return;
             e.preventDefault(); // Evitar scroll nativo
             
             let scale = currentScale === 0 ? 1 : currentScale;
             const zoomSpeed = 0.15; // Velocidad del zoom
             
             // deltaY negativo significa que el usuario gira la rueda hacia adelante (Acercar)
             const delta = e.deltaY < 0 ? 1 : -1;
             
             // Zoom multiplicativo para que se sienta natural en cualquier escala
             scale = scale + (delta * zoomSpeed * scale);
             
             applyZoom(scale, e.clientX, e.clientY);
         }, { passive: false });
      });

      function toggleZoom(event) {
         event.stopPropagation();
         if (draggedDistance > 10) {
             draggedDistance = 0;
             return; // Fue un arrastre, no un clic
         }
         
         // Buscar el siguiente nivel predefinido al hacer clic
         let nextScale = 0;
         for (let z of zoomLevels) {
             if (z > currentScale + 0.05) { // Margen de error por decimales
                 nextScale = z;
                 break;
             }
         }
         
         applyZoom(nextScale, event.clientX, event.clientY);
      }

      function navigatePreview(direction) {
        const newIndex = currentPreviewIndex + direction;
        if (newIndex < 0 || newIndex >= currentPreviewImages.length) return;
        currentPreviewIndex = newIndex;
        const src = currentPreviewImages[currentPreviewIndex];
        const caption = src.split("/").pop();
        const modal = document.getElementById("image-preview-modal");
        const img = document.getElementById("image-preview-img");
        
        img.src = src;
        document.getElementById("image-preview-caption").textContent = caption;
        
        resetZoomState(modal, img);
        document.getElementById("image-preview-prev").style.display =
          currentPreviewIndex > 0 ? "flex" : "none";
        document.getElementById("image-preview-next").style.display =
          currentPreviewIndex < currentPreviewImages.length - 1
            ? "flex"
            : "none";
      }

      function closeImagePreview() {
        const modal = document.getElementById("image-preview-modal");
        modal.classList.remove("active");
        setTimeout(() => modal.classList.add("hidden"), 250);
      }

      // Global Keyboard listeners
      document.addEventListener("keydown", (e) => {
        const previewModal = document.getElementById("image-preview-modal");
        const slotModal = document.getElementById("slot-modal");
        const materiaModal = document.getElementById("materia-modal");
        const promptModal = document.getElementById("prompt-modal");

        if (e.key === "Escape") {
            if (previewModal && !previewModal.classList.contains("hidden")) closeImagePreview();
            else if (slotModal && !slotModal.classList.contains("hidden")) closeSlotModal();
            else if (materiaModal && !materiaModal.classList.contains("hidden")) closeMateriaModal();
            else if (promptModal && !promptModal.classList.contains("hidden")) closePromptModal();
        }
        
        if (previewModal && !previewModal.classList.contains("hidden")) {
            if (e.key === "ArrowLeft") navigatePreview(-1);
            if (e.key === "ArrowRight") navigatePreview(1);
        }
      });
    