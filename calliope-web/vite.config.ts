import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		host: '127.0.0.1',
		port: 5173,
		strictPort: true,
		proxy: {
			'/api': {
				target: 'http://127.0.0.1:8247',
				// Preserve the browser-facing origin for authenticated requests.
				changeOrigin: false,
			},
		},
	},
});
