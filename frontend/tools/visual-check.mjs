/** Compatibility command: reuse the unified platform browser acceptance. */
process.env.BUGG_VISUAL_ONLY ??= 'artifacts,artifact,collections,new-collection,archive-status,dependency-settings';
await import('./migration-visual.mjs');
