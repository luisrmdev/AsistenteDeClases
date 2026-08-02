
      tailwind.config = {
        darkMode: "class",
        theme: {
          extend: {
            fontFamily: {
              sans: ["Geist", "system-ui", "sans-serif"],
              serif: ["Newsreader", "serif"],
              mono: ["Geist Mono", "monospace"],
            },
            colors: {
              background: "var(--color-bg)",
              surface: "var(--color-surface)",
              surfaceHover: "var(--color-surface-hover)",
              borderGray: "var(--color-border)",
              charcoal: "var(--color-text-main)",
              textMuted: "var(--color-text-muted)",
              accent: "var(--color-accent)",
              accentHover: "var(--color-accent-hover)",
              accentText: "var(--color-accent-text)",
              danger: "var(--color-danger)",
              dangerBg: "var(--color-danger-bg)",
            },
            boxShadow: {
              glass: "0 4px 30px rgba(0, 0, 0, 0.1)",
              glow: "0 0 20px rgba(var(--color-accent-rgb), 0.15)",
            },
            animation: {
              "fade-in": "fadeIn 0.5s cubic-bezier(0.16, 1, 0.3, 1) forwards",
              "slide-up": "slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards",
              "toast-enter":
                "toastEnter 0.3s cubic-bezier(0.2, 0.9, 0.3, 1.1) forwards",
              "toast-leave": "toastLeave 0.3s ease-in forwards",
            },
            keyframes: {
              fadeIn: {
                "0%": { opacity: "0" },
                "100%": { opacity: "1" },
              },
              slideUp: {
                "0%": { opacity: "0", transform: "translateY(16px)" },
                "100%": { opacity: "1", transform: "translateY(0)" },
              },
              toastEnter: {
                "0%": {
                  opacity: "0",
                  transform: "translateY(100%) scale(0.9)",
                },
                "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
              },
              toastLeave: {
                "0%": { opacity: "1", transform: "translateY(0) scale(1)" },
                "100%": {
                  opacity: "0",
                  transform: "translateY(100%) scale(0.9)",
                },
              },
            },
          },
        },
      };
    