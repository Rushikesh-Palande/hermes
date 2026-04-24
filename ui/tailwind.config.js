// tailwind.config.js
//
// Tailwind CSS config.
//
// Dark mode is "media"-driven by default so operators on a night shift
// get a readable dashboard without touching a toggle. A manual toggle
// lands in Phase 5 (class-based dark mode on top of the OS preference).

/** @type {import('tailwindcss').Config} */
export default {
    content: ['./src/**/*.{html,js,svelte,ts}'],
    darkMode: 'media',
    theme: {
        extend: {
            // The 12-sensor palette is reused from the legacy uPlot
            // wrapper so operators' colour mapping muscle memory carries
            // over unchanged. Avoid reordering; sensor N uses index N-1.
            colors: {
                sensor: {
                    1:  '#FF6384',
                    2:  '#36A2EB',
                    3:  '#FFCE56',
                    4:  '#4BC0C0',
                    5:  '#9966FF',
                    6:  '#FF9F40',
                    7:  '#FF6384',
                    8:  '#C9CBCF',
                    9:  '#4BC0C0',
                    10: '#00FF80',
                    11: '#0080FF',
                    12: '#FF8080',
                },
            },
            fontFamily: {
                // System UI first to keep page weight down. Fallback stack
                // ensures consistency on Linux/Pi, macOS, and Windows.
                sans: [
                    'system-ui',
                    '-apple-system',
                    'Segoe UI',
                    'Roboto',
                    'Inter',
                    'sans-serif',
                ],
                mono: ['ui-monospace', 'Menlo', 'Consolas', 'monospace'],
            },
        },
    },
    plugins: [],
};
