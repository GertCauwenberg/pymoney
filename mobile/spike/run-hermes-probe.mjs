#!/usr/bin/env node
/**
 * E0.0: run mobile/spike/hermes-probe.js on Node (baseline) and on Hermes.
 *
 * Hermes CLI from https://github.com/facebook/hermes/releases — the stock
 * host CLI is built *without* Intl; React Native's Hermes enables ECMA-402.
 * This script therefore separates BigInt results (authoritative here) from
 * Intl results (authoritative on-device; see the spike screen).
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, writeFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const probe = path.join(here, "hermes-probe.js");

function run(cmd, args) {
  const r = spawnSync(cmd, args, { encoding: "utf8" });
  process.stdout.write(r.stdout ?? "");
  process.stderr.write(r.stderr ?? "");
  return r.status ?? 1;
}

console.log("=== Node baseline ===");
const nodeStatus = run(process.execPath, [probe]);

function findHermes() {
  if (process.env.HERMES && existsSync(process.env.HERMES)) return process.env.HERMES;
  const candidates = [
    "/tmp/hermes-cli/hermes",
    path.join(here, "hermes"),
    path.join(here, "..", "hermes"),
  ];
  for (const c of candidates) if (existsSync(c)) return c;
  // PATH
  const which = spawnSync("which", ["hermes"], { encoding: "utf8" });
  if (which.status === 0 && which.stdout.trim()) return which.stdout.trim();
  return null;
}

const hermes = findHermes();
if (!hermes) {
  console.error("\nNo `hermes` binary found. Set HERMES=... or install hermes-cli.");
  console.error("Download: https://github.com/facebook/hermes/releases");
  process.exit(nodeStatus === 0 ? 2 : 1);
}

console.log(`\n=== Hermes (${hermes}) ===`);
const hermesStatus = run(hermes, [probe]);

// Optional: also compile to HBC and run bytecode (closer to RN's load path).
const hermesc = path.join(path.dirname(hermes), "hermesc");
if (existsSync(hermesc)) {
  const dir = mkdtempSync(path.join(tmpdir(), "hermes-hbc-"));
  const hbc = path.join(dir, "probe.hbc");
  console.log(`\n=== Hermes bytecode (hermesc) ===`);
  const compile = run(hermesc, ["-emit-binary", "-out", hbc, probe]);
  if (compile === 0) {
    run(hermes, [hbc]);
  }
}

process.exit(nodeStatus === 0 && hermesStatus === 0 ? 0 : 1);
