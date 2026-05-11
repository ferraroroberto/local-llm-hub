# 2026-05-11 — Make Gemini work end-to-end on Windows + image upload in Playground

## What changed

Yesterday's [20260510 changelog](20260510-add-gemini-and-image-blocks.md)
shipped the `gemini-*` routes and the image-content-block plumbing.
Today we finished the install path on Windows and added the matching UI
affordance: the **Playground** tab now grows an image uploader whenever
the selected model resolves to a `claude` or `gemini` backend, so you
can actually test multimodal input from the admin app instead of from
curl.

Along the way we hit (and fixed) four real bugs that kept the
`gemini-*` route returning 502 even though the CLI itself was working
in a terminal:

1. **`.cmd` shim wasn't found by `subprocess`.** npm installs the
   Windows binary as `gemini.cmd` (a shell shim). Python's
   `subprocess.run([..., "gemini", ...], shell=False)` calls
   `CreateProcess`, which does **not** consult `PATHEXT`, so a bare
   `"gemini"` raises `FileNotFoundError` even when `where gemini`
   resolves it. `claude` happened to ship as a real `.exe`, so the
   same wrapper pattern worked for it and masked the bug.
2. **`gemini -p` now needs a value, not a flag.** As of CLI v0.41 the
   `-p` / `--prompt` option requires a non-empty string argument
   ("Run in non-interactive mode with the given prompt. Appended to
   input on stdin (if any)."). Calling `gemini -p` with no value
   exits 1 with `Not enough arguments following: p`.
3. **Headless mode aborts in untrusted folders.** A new trusted-folder
   check rejects headless runs unless `--skip-trust` is passed,
   `GEMINI_CLI_TRUST_WORKSPACE=true` is set, or the directory was
   marked trusted from interactive mode beforehand.
4. **Sandbox blocks reads outside the workspace.** Even with trust
   bypassed, the CLI scopes file access to its `cwd`. Passing
   `@C:/Users/.../AppData/Local/Temp/hub-img-xxxx/img_0.png` —
   absolute paths into `%TEMP%` — comes back as `"file path is
   inaccessible due to security constraints"`. The fix is to run the
   subprocess with `cwd=<image temp dir>` and reference each image by
   basename (`@img_0.png`). Safe because `_extract_image_blocks`
   writes all images for a request into a single per-request temp
   dir, so one `cwd` covers the whole batch.

The model IDs in `config/models.yaml` also had to change: the actual
addressable names in the CLI are `gemini-3.1-pro-preview`,
`gemini-3-flash-preview`, `gemini-3.1-flash-lite-preview`. Without the
`-preview` suffix the API returns `ModelNotFoundError`.

## Files modified

**New**
- `docs/changelog/20260511-gemini-windows-install-and-playground-images.md` —
  this file.

**Changed**
- `src/gemini_cli.py` — four-part fix:
  - resolve the binary via `shutil.which("gemini")` and pass the
    absolute path to `subprocess.run`, so `.cmd` shims work on Windows;
  - pass `-p " "` (non-empty placeholder) and rely on stdin for the
    real prompt, matching the CLI v0.41 contract;
  - add `--skip-trust` so headless runs bypass the workspace-trust
    check;
  - when `images` is non-empty, run with `cwd=images[0].parent` and
    reference each file by basename so they fall inside the CLI's
    workspace sandbox.
- `config/models.yaml` — Gemini `display_name`s gain the `-preview`
  suffix (`gemini-3.1-pro-preview`, `gemini-3-flash-preview`,
  `gemini-3.1-flash-lite-preview`).
- `app/views/playground.py` — adds an `st.file_uploader` that only
  appears when the selected alias resolves to `backend in {claude,
  gemini}`. When images are attached, the payload switches from a
  plain string `content` to Anthropic-style content blocks
  (`{"type":"text",...} + {"type":"image", "source":{"type":"base64",...}}`
  per upload); the text-only flow is unchanged. Small fixed-width
  thumbnails (96 px) confirm the staged images at a glance. After a
  200 response a session-state nonce rotates the uploader's `key` so
  attached files clear and the next prompt starts fresh; HTTP errors
  leave the images staged so the user can retry without re-attaching.
- `tests/test_image_blocks.py` — bumps the stale `gemini-3.1-pro`
  model name in `test_gemini_path_writes_image_and_passes_to_cli` to
  `gemini-3.1-pro-preview` so the registry resolves it.
- `README.md` — Gemini model names in the Active rotation section and
  the Python code example pick up the `-preview` suffix.

## Why

The 20260510 patch was developed against an environment that already
had Node + the `gemini` CLI installed and trusted, on a non-Windows
shell, against pre-`-preview` model IDs. Once a fresh Windows install
landed on the box it surfaced all four layered failures above. The
Streamlit Playground also lacked any way to test image content blocks
without writing curl, which made it easy to regress the multimodal
path silently — adding the file uploader closes that gap.

## Validation run

```bat
:: 1. End-to-end multimodal call via the patched wrapper
& .\.venv\Scripts\python.exe -c "import base64, tempfile, pathlib; from src.gemini_cli import call_gemini; td = pathlib.Path(tempfile.mkdtemp()); img = td/'pixel.png'; img.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==')); print(call_gemini('What color? One word.', model='gemini-3.1-flash-lite-preview', images=[img])['result'])"
:: -> "Red."

:: 2. Full test suite still green
& .\.venv\Scripts\python.exe -m pytest tests/ -q
:: -> all green (image-blocks suite: 8 passed in 0.50s)
```

UI smoke: pick `gemini_lite` (or any `claude_*` / `gemini_*`) in the
Playground, attach a screenshot, send. The hub writes the image to a
per-request temp dir, the wrapper hands it to `gemini -p` with the
right cwd, and the response is the CLI's actual reading of the
picture — confirmed against the OCR-of-a-Gemini-CLI-screenshot test
case.

## Limits / known issues

- The Gemini CLI's "trusted folder" check uses `--skip-trust` per
  call. If Google later removes that flag we'll have to either set
  `GEMINI_CLI_TRUST_WORKSPACE=true` in the subprocess `env=` or
  pre-trust the per-request temp dir non-interactively (no such API
  exists today as far as we can tell).
- The `claude` wrapper still calls the bare binary name. That works
  today because Claude Code ships a real `.exe`, but if a future
  installer switches to a `.cmd` shim the same `shutil.which` fix
  will be needed there too — left out of this patch to keep scope
  tight and avoid regressing a working path.
- The Playground uploader is single-shot (clears after a successful
  send). If you want to send the same image to several models in a
  row, re-attach each time.
