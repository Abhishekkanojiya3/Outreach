/** @type {import('tailwindcss').Config} */

// ─── Dark theme via palette inversion ────────────────────────────────────────
// The app's components consistently use: bg-white (cards), bg-zinc-50 (page/
// inputs), border-zinc-200 (borders), text-zinc-950 (headings), text-zinc-500
// (secondary). Instead of editing every page, the palette itself is remapped
// to dark equivalents here — light-scale utilities render dark surfaces, and
// dark-scale text utilities render light text. Accent scales keep their mid
// shades (solid buttons/dots) but get dark 50–300 tints and light 700–900s.

const darkNeutral = {
  50: '#0c0f16',   // page background
  100: '#1a2029',  // subtle hover / chips
  200: '#252e3c',  // borders
  300: '#344052',  // strong borders / scrollbar
  400: '#5d6d84',  // placeholders, muted
  500: '#8c9cb2',  // secondary text
  600: '#a9b7c9',
  700: '#c5cfdd',
  800: '#dbe3ee',
  900: '#eaeff7',
  950: '#f5f8fd',  // headings / primary text
}

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Plus Jakarta Sans', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
        display: ['Sora', 'Space Grotesk', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: {
        // The codebase uses `rounded-none` as its card/input radius token —
        // remapping it here softens the entire UI in one place.
        none: '0.65rem',
      },
      colors: {
        white: '#151b27',   // card surfaces
        zinc: darkNeutral,
        gray: darkNeutral,
        indigo: {
          50: '#1d2142',
          100: '#262c58',
          200: '#353e7c',
          300: '#4a55a3',
          400: '#8b96f8',
          500: '#6d78f2',
          600: '#8b96f8',
          700: '#6366f1',  // primary buttons + links
          800: '#4f52e0',  // button hover
          900: '#c7d2fe',
        },
        red: {
          50: '#2a151a',
          100: '#3a1b22',
          200: '#553040',
          300: '#7a3d50',
          400: '#f87171',
          500: '#ef4444',
          600: '#f87171',
          700: '#fca5a5',
          800: '#fecaca',
          900: '#fecdd3',
        },
        green: {
          50: '#12241b',
          100: '#173425',
          200: '#204a32',
          300: '#2c6644',
          400: '#4ade80',
          500: '#22c55e',
          600: '#4ade80',
          700: '#86efac',
          800: '#bbf7d0',
          900: '#dcfce7',
        },
        emerald: {
          50: '#0f231e',
          100: '#14322a',
          200: '#1c483b',
          300: '#286551',
          400: '#34d399',
          500: '#10b981',
          600: '#34d399',
          700: '#6ee7b7',
          800: '#a7f3d0',
          900: '#d1fae5',
        },
        amber: {
          50: '#292013',
          100: '#3a2d18',
          200: '#544020',
          300: '#775a2c',
          400: '#fbbf24',
          500: '#f59e0b',
          600: '#d97706',  // solid amber buttons (white text)
          700: '#b45309',  // amber button hover
          800: '#fcd34d',
          900: '#fde68a',
        },
        yellow: {
          50: '#2a2412',
          100: '#3a3216',
          200: '#554920',
          300: '#78672c',
          400: '#facc15',
          500: '#eab308',
          600: '#facc15',
          700: '#fde047',
          800: '#fef08a',
          900: '#fef9c3',
        },
        blue: {
          50: '#14213a',
          100: '#1a2c4e',
          200: '#243e6d',
          300: '#315490',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#60a5fa',
          800: '#93c5fd',
          900: '#bfdbfe',
        },
      },
    },
  },
  plugins: [],
}
