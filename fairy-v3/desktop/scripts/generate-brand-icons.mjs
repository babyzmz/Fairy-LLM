// Derive static branding from the existing Apache-2.0 Fairy-DSH anatomy.
// Run with --check to verify checked-in assets without replacing them.
import { spawnSync } from "node:child_process";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve, basename } from "node:path";
import { fileURLToPath } from "node:url";
import { rolldown } from "rolldown";

const root = fileURLToPath(new URL("../", import.meta.url));
const sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256];
const temporary = await mkdtemp(join(tmpdir(), "fairy-brand-icons-"));
try {
  const bundle = await rolldown({ input: join(root, "src/fairyEye/eyeSvg.ts") });
  let generated;
  try { generated = await bundle.generate({ format: "esm" }); }
  finally { await bundle.close(); }
  const { createEyeMarkup } = await import(
    `data:text/javascript;base64,${Buffer.from(generated.output[0].code).toString("base64")}`
  );
  const markup = createEyeMarkup("brand");
  const start = markup.indexOf('<svg class="dsh-fairy-main"');
  if (start < 0) throw new Error("Fairy main SVG missing");
  const svg = '<!-- Generated from Fairy-DSH eyeSvg.ts. Copyright 2026 Chengzhibense; Apache-2.0. -->\n'
    + markup.slice(start)
      .replaceAll("var(--dsh-fairy-outer-halo-color)", "#c9f8ff")
      .replace('class="dsh-fairy-eye-flicker"', 'class="dsh-fairy-eye-flicker" display="none"')
      .replace('class="dsh-fairy-glitch-blocks"', 'class="dsh-fairy-glitch-blocks" display="none"')
    + "\n";
  // At titlebar sizes the halo consumes pixels and scanlines muddy the eye.
  // Keep the same anatomy, but use an optical crop and clean solid contours.
  let smallSvg = svg.replace('viewBox="0 0 160 160"', 'viewBox="10 10 140 140"');
  for (const layer of ["outer-halo", "sclera-halo", "highlight-halo", "scanlines"]) {
    smallSvg = smallSvg.replace(`class="dsh-fairy-${layer}"`, `class="dsh-fairy-${layer}" display="none"`);
  }
  for (const [artwork, dimensions] of [
    [svg, sizes.filter(size => size > 48)],
    [smallSvg, sizes.filter(size => size <= 48)],
  ]) {
    const source = join(temporary, "brand.svg");
    await writeFile(source, artwork);
    const result = spawnSync(process.execPath, [
      join(root, "node_modules/@tauri-apps/cli/tauri.js"), "icon", source,
      "--output", temporary, ...dimensions.flatMap(size => ["--png", String(size)]),
    ], { cwd: root, encoding: "utf8", windowsHide: true });
    if (result.error || result.status !== 0) throw new Error("Tauri icon rasterization failed", { cause: result.error });
  }
  const images = await Promise.all(sizes.map(size => readFile(join(temporary, `${size}x${size}.png`))));
  const header = Buffer.alloc(6 + images.length * 16);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  let offset = header.length;
  images.forEach((image, index) => {
    const entry = 6 + index * 16;
    header[entry] = header[entry + 1] = sizes[index] % 256;
    header.writeUInt16LE(1, entry + 4);
    header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(image.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += image.length;
  });
  for (const [relative, bytes] of [
    ["src/fairyEye/brand.svg", Buffer.from(svg)],
    ["src-tauri/icons/icon.ico", Buffer.concat([header, ...images])],
  ]) {
    const target = join(root, relative);
    if (process.argv.includes("--check")) {
      if (!(await readFile(target)).equals(bytes)) throw new Error(`Stale brand asset: ${relative}`);
    } else await writeFile(target, bytes);
  }
  console.log(process.argv.includes("--check") ? "Brand assets match SVG source" : "Generated Fairy SVG and Windows ICO");
} finally {
  // Only remove the exact directory allocated above, never a workspace/user root.
  if (dirname(resolve(temporary)) !== resolve(tmpdir()) || !basename(temporary).startsWith("fairy-brand-icons-")) {
    throw new Error("Unsafe icon scratch directory");
  }
  await rm(temporary, { recursive: true, force: true });
}
