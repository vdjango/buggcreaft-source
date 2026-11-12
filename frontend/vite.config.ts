import { defineConfig } from 'vite'
import { platformConfig } from './tools/platform-build'

export default defineConfig(({ command }) => ({
  ...platformConfig,
  base: command === 'serve' ? '/' : platformConfig.base,
  optimizeDeps: { noDiscovery: true },
  // Serve the real platform shells and compiled Vue assets together in development.
  // Run npm run build again after editing: the hub owns HTML/session/SEO rendering.
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy: {
    '^/api/v1/search(?:\\?|/|$)': {target:'http://127.0.0.1:18448',rewrite:path=>path.replace('/api/v1/search','/v1/search')},
    '^/api/v1/projects/[^/]+/similar': {target:'http://127.0.0.1:18448',rewrite:path=>path.replace('/api/v1','/v1/search')},
    '/api/v1': 'http://127.0.0.1:18449',
    '^/(?:room-cache/|assets/[a-f0-9]{2}/[a-f0-9]{40}$|java/|mods/|mc/game/|v1/(?:packages/|objects/|products/java-runtime/)|maven/|fabric-meta/|fabric-maven/|neoforge-maven/|quilt-meta/|quilt-maven/|mirrors/authlib-injector/)': 'http://127.0.0.1:18450',
    '^/': 'http://127.0.0.1:18448',
  } },
}))
