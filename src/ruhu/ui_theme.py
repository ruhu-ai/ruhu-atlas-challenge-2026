from __future__ import annotations


PRIMARY_HEX = "#D14118"
PRIMARY_HEX_HOVER = "#B93815"
PRIMARY_RGB = "209, 65, 24"
TEXT_HEX = "#111827"
MUTED_TEXT_HEX = "#6B7280"
BORDER_HEX = "#E5E7EB"
SURFACE_HEX = "#FFFFFF"
BACKGROUND_HEX = "#F9FAFB"

# ---------------------------------------------------------------------------
# Google Fonts — Inter (sans) + JetBrains Mono (mono)
# ---------------------------------------------------------------------------

FONT_LINK_HTML = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Inter:wght@400;500;600;700&amp;'
    'family=JetBrains+Mono:wght@400;500&amp;'
    'display=swap" rel="stylesheet">'
)

# ---------------------------------------------------------------------------
# Theme toggle — persisted in localStorage, defaults to system preference
# ---------------------------------------------------------------------------

THEME_TOGGLE_CSS = """
  .theme-toggle {
    position: fixed;
    bottom: 1.25rem;
    right: 1.25rem;
    z-index: 9999;
    width: 2.25rem;
    height: 2.25rem;
    border-radius: 999px;
    border: 1px solid hsl(var(--border));
    background: hsl(var(--card));
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 2px 8px rgba(0,0,0,0.10);
    transition: background 120ms ease, color 120ms ease, box-shadow 120ms ease;
  }
  .theme-toggle:hover {
    background: hsl(var(--accent));
    color: hsl(var(--foreground));
    box-shadow: 0 4px 16px rgba(0,0,0,0.14);
  }
  .theme-toggle svg { width: 1rem; height: 1rem; pointer-events: none; }
  .theme-toggle .icon-sun  { display: none; }
  .theme-toggle .icon-moon { display: block; }
  html.dark .theme-toggle .icon-sun  { display: block; }
  html.dark .theme-toggle .icon-moon { display: none; }
"""

THEME_TOGGLE_HTML = """
<button class="theme-toggle" id="theme-toggle" aria-label="Toggle dark/light theme" title="Toggle theme">
  <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>
  </svg>
  <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>
  </svg>
</button>
<script>
(function() {
  var stored = localStorage.getItem('ruhu-theme');
  var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  var isDark = stored ? stored === 'dark' : prefersDark;
  if (isDark) document.documentElement.classList.add('dark');

  var btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.addEventListener('click', function() {
      var nowDark = document.documentElement.classList.toggle('dark');
      localStorage.setItem('ruhu-theme', nowDark ? 'dark' : 'light');
    });
  }
})();
</script>
"""

# Early-init script (goes in <head> before any rendering to avoid flash)
THEME_INIT_SCRIPT = """<script>
(function(){
  var s=localStorage.getItem('ruhu-theme');
  var p=window.matchMedia('(prefers-color-scheme: dark)').matches;
  if(s==='dark'||(s===null&&p)) document.documentElement.classList.add('dark');
})();
</script>"""


def app_theme_styles() -> str:
    return """
    :root {
      --background: 40 20% 98%;
      --foreground: 30 10% 10%;
      --card: 0 0% 100%;
      --card-foreground: 30 10% 10%;
      --secondary: 40 10% 94%;
      --secondary-foreground: 30 10% 10%;
      --muted: 40 10% 94%;
      --muted-foreground: 30 5% 45%;
      --accent: 40 10% 94%;
      --accent-foreground: 30 10% 10%;
      --primary: 14 82% 45%;
      --primary-foreground: 0 0% 100%;
      --border: 30 8% 88%;
      --input: 30 8% 88%;
      --ring: 14 82% 45%;
      --sidebar: 40 15% 96%;
      --primary-rgb: 209, 65, 24;
      --shadow: 0 12px 32px rgba(17, 24, 39, 0.08);
      --radius: 0.75rem;
      --mono: "JetBrains Mono", "IBM Plex Mono", "SFMono-Regular", ui-monospace, monospace;
      --sans: "Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --text-xs: 0.75rem;
      --text-sm: 0.875rem;
      --text-base: 1rem;
      --text-lg: 1.125rem;
      --text-2xl: 1.5rem;
      --text-4xl: 2.25rem;
    }

    /* Dark theme — via .dark class (JS toggle) */
    html.dark :root,
    html.dark {
      --background: 30 5% 5.5%;
      --foreground: 40 6% 96%;
      --card: 30 3% 9%;
      --card-foreground: 40 6% 96%;
      --secondary: 30 3% 12%;
      --secondary-foreground: 40 6% 96%;
      --muted: 30 2% 14%;
      --muted-foreground: 30 3% 55%;
      --accent: 30 3% 13%;
      --accent-foreground: 40 6% 96%;
      --primary: 14 80% 51%;
      --primary-foreground: 0 0% 100%;
      --border: 30 2% 16%;
      --input: 30 3% 12%;
      --ring: 14 80% 51%;
      --sidebar: 30 4% 7%;
      --primary-rgb: 230, 78, 32;
      --shadow: 0 20px 60px rgba(0, 0, 0, 0.28);
    }

    /* Dark theme — system preference fallback (when no .dark class) */
    @media (prefers-color-scheme: dark) {
      :root:not(.light) {
        --background: 30 5% 5.5%;
        --foreground: 40 6% 96%;
        --card: 30 3% 9%;
        --card-foreground: 40 6% 96%;
        --secondary: 30 3% 12%;
        --secondary-foreground: 40 6% 96%;
        --muted: 30 2% 14%;
        --muted-foreground: 30 3% 55%;
        --accent: 30 3% 13%;
        --accent-foreground: 40 6% 96%;
        --primary: 14 80% 51%;
        --primary-foreground: 0 0% 100%;
        --border: 30 2% 16%;
        --input: 30 3% 12%;
        --ring: 14 80% 51%;
        --sidebar: 30 4% 7%;
        --primary-rgb: 230, 78, 32;
        --shadow: 0 20px 60px rgba(0, 0, 0, 0.28);
      }
    }

    * { box-sizing: border-box; }
    html { font-size: 16px; }
    body {
      margin: 0;
      font-family: var(--sans);
      font-feature-settings: "ss01", "ss02", "cv01", "cv02";
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      color: hsl(var(--foreground));
      background:
        radial-gradient(circle at top left, rgba(var(--primary-rgb), 0.14), transparent 28%),
        radial-gradient(circle at top right, rgba(var(--primary-rgb), 0.08), transparent 30%),
        linear-gradient(180deg, hsl(var(--secondary)) 0%, hsl(var(--background)) 100%);
      min-height: 100vh;
    }

    ::selection {
      background: rgba(var(--primary-rgb), 0.20);
      color: hsl(var(--primary));
    }

    a {
      color: hsl(var(--primary));
    }
    """ + THEME_TOGGLE_CSS
