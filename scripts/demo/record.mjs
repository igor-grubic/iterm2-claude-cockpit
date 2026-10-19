import { chromium } from "playwright";
import { mkdir, readFile, writeFile, copyFile, stat, access } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { createDemoServer } from "./server.mjs";

const directory = dirname(fileURLToPath(import.meta.url));
const root = resolve(directory, "../..");
const scenes = JSON.parse(await readFile(join(directory, "scenes.json"), "utf8"));
const { values } = parseArgs({
  options: {
    out: { type: "string", default: ".playwright-mcp/demo/video" },
    "encode-only": { type: "boolean", default: false },
    "update-readme": { type: "boolean", default: false },
  },
});
const output = resolve(root, values.out);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: "inherit", ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited with status ${result.status}`);
}

async function capture() {
  const demo = await createDemoServer();
  let browser;
  try {
    browser = await chromium.launch();
    const context = await browser.newContext({
      viewport: { width: 1000, height: 760 }, deviceScaleFactor: 1, reducedMotion: "reduce", locale: "en-US",
    });
    await context.addInitScript(() => {
      window.__demoCopiedPaths = [];
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: { writeText: async text => { window.__demoCopiedPaths.push(text); } },
      });
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${demo.url}/demo`);
    const panel = page.frameLocator("#cockpit");
    await panel.locator(".group-classic").first().waitFor();
    await page.evaluate(() => document.fonts.ready);
    await panel.locator("body").evaluate(() => document.fonts.ready);
    for (const [index, scene] of scenes.entries()) {
      for (const action of scene.actions) {
        const target = panel.locator(action.selector);
        if (action.type === "click") await target.click();
        else if (action.type === "press") await target.press(action.key);
        else throw new Error(`Unknown scene action: ${action.type}`);
      }
      if (scene.waitFor) await panel.locator(scene.waitFor).waitFor({ state: "visible" });
      if (scene.waitHidden) await panel.locator(scene.waitHidden).waitFor({ state: "hidden" });
      await page.evaluate(({ caption, step }) => window.setDemoCaption(caption, step), { caption: scene.caption, step: index + 1 });
      await page.waitForTimeout(scene.settle ?? 650);
      await page.screenshot({ path: join(output, `${scene.name}.png`), animations: "disabled", scale: "css" });
      console.log(`Captured ${scene.name}`);
    }
    if (errors.length) throw new Error(`Demo browser errors:\n${errors.join("\n")}`);
  } finally {
    if (browser) await browser.close();
    await demo.close();
  }
}

async function encode() {
  const lines = [];
  const seconds = scenes.reduce((total, scene) => total + (scene.seconds ?? 1.7), 0);
  for (const scene of scenes) {
    await access(join(output, `${scene.name}.png`));
    lines.push(`file '${scene.name}.png'`, `duration ${scene.seconds ?? 1.7}`);
  }
  lines.push(`file '${scenes.at(-1).name}.png'`);
  await writeFile(join(output, "frames.txt"), lines.join("\n") + "\n");
  await writeFile(join(output, "captions.txt"), scenes.map(scene => `${scene.name} | ${scene.caption}`).join("\n") + "\n");
  run("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", "frames.txt",
    "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
    "-t", seconds.toFixed(3), "-movflags", "+faststart", "demo.mp4",
  ], { cwd: output });
  run("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y", "-i", "demo.mp4",
    "-filter_complex", "fps=8,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
    "-loop", "0", "demo.gif",
  ], { cwd: output });
  if (values["update-readme"]) await copyFile(join(output, "demo.gif"), join(root, "docs/demo.gif"));
  for (const name of ["demo.gif", "demo.mp4"]) {
    const path = join(output, name);
    console.log(`${path} (${(await stat(path)).size.toLocaleString()} bytes, approximately ${seconds.toFixed(1)}s)`);
  }
}

try {
  run("ffmpeg", ["-version"], { stdio: "ignore" });
  await mkdir(output, { recursive: true });
  if (!values["encode-only"]) await capture();
  await encode();
} catch (error) {
  console.error(`Demo recording failed: ${error.message}`);
  process.exitCode = 1;
}
