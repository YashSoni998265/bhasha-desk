// Prints the digest the demo app computes in the browser for each JSON file given,
// using the app's own canonical() and sha256(), so the test can compare it with Python.
import { readFileSync } from "node:fs";

const app = readFileSync(process.argv[2], "utf8");
const start = app.indexOf("// --- digest:");
const end = app.indexOf("// --- end digest ---");
if (start < 0 || end < start) throw new Error("app.js lost its digest markers");
const body = app.slice(start, end);
const { canonical, sha256 } = new Function(`${body}; return { canonical, sha256 };`)();
const out = {};
for (const file of process.argv.slice(3)) {
  out[file] = await sha256(canonical(JSON.parse(readFileSync(file, "utf8"))));
}
console.log(JSON.stringify(out));
