---
name: asistente-identidad-visual
description: Define la identidad visual oficial, filosofía antifatiga y paleta exacta de colores (Claro Industrial y Oscuro Midnight Slate) para la aplicación Asistente de Clases.
---

# Identidad Visual - Asistente de Clases

Esta skill define la estética específica y la identidad de marca de la aplicación "Asistente de Clases". No compite con las reglas generales de otras skills de diseño (como espaciados, tipografías o animaciones), sino que **especifica los colores, jerarquías y decisiones estéticas** únicas de este proyecto.

## 1. Filosofía Base: "Zero Eye-Fatigue" (Cero Fatiga Visual)
El Asistente de Clases es una herramienta académica de uso prolongado. Su prioridad absoluta es evitar el dolor de cabeza y el desgaste ocular.
- **Prohibición del Blanco Puro y Negro Puro:** Jamás se usa `#ffffff` para fondos o `#000000` para textos, y viceversa.
- **Bajo Contraste Lumínico:** Las interfaces deben verse como materiales opacos (pizarra, arcilla, acero, papel entintado) y no como pantallas emitiendo luz direccional.

---

## 2. Paletas de Color Específicas

### Modo Claro (Mid-Gray / Acero Industrial)
El modo claro **no es blanco**. Es un tema industrial de tonos grises densos (Slate) que neutraliza por completo la emisión brillante de la pantalla. Nunca insertes blancos puros aquí.

- **Background (`--color-bg`):** Slate 300 (`#cbd5e1`). Un gris estructural pesado.
- **Surface (`--color-surface`):** Slate 200 (`#e2e8f0`). Tarjetas ligeramente más claras que el fondo, pero estrictamente grises.
- **Textos (`--color-text-main`):** Slate 900 (`#0f172a`). Necesario para mantener legibilidad sin usar negro puro.
- **Accent (`--color-accent`):** Sky 700 (`#0369a1`). Un azul oscuro industrial.
- **Bordes (`--color-border`):** Slate 400 (`#94a3b8`).

### Modo Oscuro (Midnight Slate)
El modo oscuro evita el fondo negro de alto contraste para evitar el efecto de "halación" (texto que brilla excesivamente).

- **Background (`--color-bg`):** Navy/Slate 950 (`#090e17`). Un azul-gris medianoche profundo y relajante.
- **Surface (`--color-surface`):** Slate 900 (`#131d2e`). Tarjetas sutilmente elevadas con tinte frío.
- **Textos (`--color-text-main`):** Slate 200 (`#e2e8f0`). Un blanco roto, suave, jamás `#ffffff`.
- **Accent (`--color-accent`):** Blue 500 (`#3b82f6`). Azul vibrante y luminoso adaptado para resaltar en oscuridad.
- **Bordes (`--color-border`):** Slate 800 (`#1e293b`).

---

## 3. Implementación Estructural (Componentes)

Al crear o modificar componentes dentro del Asistente de Clases, sigue estas reglas estrictas de pintado:

### Tarjetas (Cards) y Módulos
- Deben usar obligatoriamente la clase semántica vinculada a `--color-surface` (ej. `bg-surface`).
- Los bordes siempre deben existir para separar las tarjetas del fondo opaco, usando `border border-borderGray`.
- **Prohibido:** No usar sombras pesadas (`shadow-xl` o `shadow-2xl`) de color negro intenso. Las tarjetas se separan del fondo por color y borde, las sombras son mínimas o inexistentes (diseño plano/industrial).

### Banners y Alertas
- No deben ser de colores chillones (neon red, bright yellow).
- Deben mantener fondos suaves con colores semánticos (ej. `--color-danger-bg` con `--color-danger`).
- Su padding debe ser holgado para no ahogar el texto.

### Botones
- **Botón Primario:** Usa `--color-accent` para el fondo y `--color-accent-text` para el texto. 
- **Botón Secundario:** Usa el color de la tarjeta (`bg-surface`) con un borde definido (`border-borderGray`).
- Siempre requieren interacciones sutiles en `:hover` cambiando al tono `-hover` definido en la paleta.

### Elementos Inactivos / Texto Secundario
- Utiliza `--color-text-muted` para fechas, metadatos, subtítulos y marcadores de posición (placeholders). Esta jerarquía es crucial para no ensuciar la visión periférica del usuario.
