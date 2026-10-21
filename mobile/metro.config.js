/**
 * E0.0 spike: Metro must resolve `@pymoney/core` out of the `frontend/`
 * npm workspace from this Expo app at the repository root.
 *
 *   mobile/                  <- projectRoot
 *   frontend/                <- workspace (watched)
 *     packages/core/         <- @pymoney/core source
 *     node_modules/
 *   node_modules/            <- mobile's own deps
 */
const path = require("path");
const { getDefaultConfig } = require("expo/metro-config");

const projectRoot = __dirname;
const frontendRoot = path.resolve(projectRoot, "../frontend");

const config = getDefaultConfig(projectRoot);

// See every file under frontend/ so edits to @pymoney/core hot-reload.
config.watchFolders = [frontendRoot];

// Resolve packages from mobile's node_modules first, then the workspace's.
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, "node_modules"),
  path.resolve(frontendRoot, "node_modules"),
];

// Prefer source over any stale build output of the workspace package.
config.resolver.disableHierarchicalLookup = true;

module.exports = config;
