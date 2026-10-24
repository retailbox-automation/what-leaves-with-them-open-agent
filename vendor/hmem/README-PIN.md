# vendor/hmem: the Meterless H-MEM reference engine, pinned

| | |
|---|---|
| Upstream | https://github.com/Meterless/Meterless, folder `engines/hmem` |
| Commit | `1742dec56a66e58eef9dda2d2b865823758ca8cd` (main on 2026-10-24, `git ls-remote` at 11:28Z) |
| Fetched as | `https://codeload.github.com/Meterless/Meterless/tar.gz/1742dec56a66e58eef9dda2d2b865823758ca8cd`, sha256 `9d4fa94858375064efc1bdd1a57393cfb98cea4f37c6587efd67b45bde468b48` |
| License | Apache-2.0, `LICENSE` here is the upstream file unchanged. Upstream ships no NOTICE file. |
| Changes | None to upstream files. `dist/hmem.mjs` is generated from `reference/src` (below). |

## What is here

- `AGENTS.md`, `HOW_TO_USE_H-MEM.md`, `README.md`, `LICENSE`: upstream, unchanged.
- `reference/`: the TypeScript reference implementation (`src/`, `test/smoke.test.ts`, `package.json`, `package-lock.json`, `tsconfig.json`, `README.md`), unchanged.
- `conformance/`: the upstream conformance runner and its vectors, unchanged.
- `dist/hmem.mjs`: `reference/src` bundled to one plain ES module, so the sidecar (`open_agent/hmem_sidecar.mjs`) runs on any Node >= 20 without `npm install` and without a TypeScript loader. Node 26 cannot run `reference/src` directly: the constructors use parameter properties, which type stripping does not support.

Rebuild (from `reference/`, after `npm ci --ignore-scripts`):

```
./node_modules/.bin/esbuild src/index.ts --bundle --platform=node --format=esm --target=node20 --legal-comments=inline \
  "--banner:js=// H-MEM reference implementation (Meterless, Apache-2.0, see ../LICENSE), engines/hmem/reference/src at Meterless/Meterless 1742dec56a66e58eef9dda2d2b865823758ca8cd, bundled unmodified with esbuild 0.28.1 (see ../README-PIN.md)." \
  --outfile=../dist/hmem.mjs
```

esbuild 0.28.1 (from the lockfile) produced `dist/hmem.mjs` with sha256 `7ffdedb9d061bd6e74687beb9925b1e3470bf746f3a9344cc72f92939fcaa305`.

## Review before first run (2026-10-24)

- Read every file in `reference/src` and `conformance/runner.ts`. Imports: `node:fs`, `node:path`, `node:url` only. No network calls, no `child_process`, no `eval` or `Function`. The only environment variable read is `HMEM_IMPL` (runner). Files are written only under the `persistDir` the caller passes (`state.json`, `ledger.jsonl`).
- `reference/package.json`: no runtime dependencies; dev dependencies `tsx` ^4.19.0 and `vitest` ^2.1.0. `package-lock.json`: 118 entries, all from `registry.npmjs.org`, all with an integrity hash. Packages with install scripts: `esbuild` (twice) and `fsevents`. Installed with `npm ci --ignore-scripts` (47 packages); the tests and the bundle work without those scripts.
- `node_modules/` is not committed (`vendor/hmem/.gitignore`).

## Tests on the pinned copy (outputs in `logs/`)

| Command (from `vendor/hmem/reference`) | Result | Log |
|---|---|---|
| `npm test` (vitest 2.1.9) | 10 passed | `logs/hmem-npm-test.txt` |
| `npx tsx ../conformance/runner.ts` | 13/13 checks passed | `logs/hmem-conformance-reference.txt` |
| `HMEM_IMPL=$PWD/../dist/hmem.mjs npx tsx ../conformance/runner.ts` | 13/13 checks passed | `logs/hmem-conformance-dist.txt` |

## File hashes (sha256)

```
d0aa03cead15362d5e9baeccdc4e9a7fcfad84b6248e40d230a3801637898d25  AGENTS.md
cf44fd4d3c9a8af0c46157bb015dead8d4ccb2d000c2358e7da1e4cecfbc4c39  conformance/README.md
d6724860761c4846a0bce3080d25115bcb0ba1d9d0d2e933438f5683a7a9b7c5  conformance/runner.ts
10afb5a07233466576ba6feb0d88408488909ba2721ee9157fdb932132917029  conformance/vectors/vectors.json
7ffdedb9d061bd6e74687beb9925b1e3470bf746f3a9344cc72f92939fcaa305  dist/hmem.mjs
ac6a5a0abe7a94deb10aec66043645367f23ee24f9052a36389de483c94301cc  HOW_TO_USE_H-MEM.md
63e29fc8231b53757a77fbc371071c91d768621efa1e68b1d9ff8d1bb7c4a0ef  LICENSE
a7847435143cacff4ab8c08cc0823abf0224ab2ca7a605c061a0a6d62e05c79d  README.md
62bb56c945cd18e5aa4cfe74eb63659cb46b66e6b4a2c79c689d625775ca8b65  reference/package-lock.json
982b032b671667f6c3c2f857ab0791a8f78f9574957410dcd1d0a65119cde18d  reference/package.json
dafb231927b7785103a70caa4f320429b206d0baabe7f85dc577b65c96442591  reference/README.md
432e8b86ed0b5883d435e5ab4a12160d1f40d194cbdb4c119d3b710cbb0301a3  reference/src/conflictDetection.ts
d12594d667ec068690c6b81e020cd62ade91671316a1ab9b529259304aceb009  reference/src/dreaming.ts
89433327ee1075a8049bc8518cdf5cabf51ad67802e192266841cdee727b6d10  reference/src/embeddings.ts
9c481889afcdd70086d3bb3470842fa2330215e4d7760d826d103accdf8c1cdf  reference/src/index.ts
6072693d654ea1c34a9adbf6ce80101eeb1eeb56273d264f6ceb4859ffde6562  reference/src/memoryMining.ts
4fe120b1b64fe7225b9b28fda59514c114de9e885d22c5bf0aa035788b6d629d  reference/src/memoryRetrieval.ts
a1eac8de8c0fde0e503e1212f3cb98295a661c2a8bad93ad82e51985885e6fd0  reference/src/memoryStore.ts
3ce0d4f3233d0ad7c133a2cbaf39b6122467022e0aa378a160a0d463e2264cef  reference/src/sleepCycle.ts
263253dd3342c488ce773d7e728d77aec983c1833873602b59ce1a6c7999e20e  reference/src/trustLedger.ts
becb504870a67646a20a421ffe04d8732db63418393423d2186c074b182f54c7  reference/src/types.ts
84e09fb08eb769b19488d68f0303c2a9ab275f37e80e986438f0099cd7c98824  reference/test/smoke.test.ts
a0e236840f54f788dec6c2a56a84b7cd737bac932848fd8808dc20f94e49d4d4  reference/tsconfig.json
```
