// Prints the digest the demo app computes in the browser for each JSON file given,
// using the app's own canonical() and sha256(), so the test can compare it with Python.
import { readFileSync } from "node:fs";

const app = readFileSync(process.argv[2], "utf8");
const body = app.slice(app.indexOf("function canonical"), app.indexOf("async function showApproval"));
const { canonical, sha256 } = new Function(`${body}; return { canonical, sha256 };`)();
const out = {};
for (const file of process.argv.slice(3)) {
  out[file] = await sha256(canonical(JSON.parse(readFileSync(file, "utf8"))));
}
console.log(JSON.stringify(out));
