import { cp, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const source = join(dirname(require.resolve("monaco-editor/package.json")), "min", "vs");
const target = fileURLToPath(new URL("../public/vendor/monaco/vs", import.meta.url));
await mkdir(target, { recursive: true });
await cp(source, target, { recursive: true });
