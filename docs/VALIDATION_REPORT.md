# Validation report

*Generated 2026-09-11 06:13:12 on Linux-6.1.158+-x86_64-with-glibc2.36 in 67.6s.*

**Result: 5/5 checks passed.**

## ✅ environment

```
* **Python** — 3.11.2 — /home/user/Abhi/backend/.venv/bin/python
* **Node.js** — v22.22.3
* **FFmpeg** — bundled with imageio-ffmpeg: {"path": "/home/user/Abhi/backend/.venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2", "event": "ffmpeg_resolved", "level": "info", "timestamp": "2026-09-11T06:12:05.742637Z"}
/home/user/Abhi/backend/.venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2
* **Free disk** — 20.0 GB
* **Platform** — Linux-6.1.158+-x86_64-with-glibc2.36
```

## ✅ backend tests

```
..........................................................               [100%]
58 passed in 14.12s
```

## ✅ frontend tests

```
> ai-creative-studio-frontend@0.1.0 test
> vitest run --run


 RUN  v2.1.9 /home/user/Abhi/frontend

 ✓ tests/api.test.ts (5 tests) 13ms
 ✓ tests/login.test.tsx (2 tests) 166ms
 ✓ tests/format.test.ts (6 tests) 4ms

 Test Files  3 passed (3)
      Tests  13 passed (13)
   Start at  06:12:21
   Duration  2.82s (transform 231ms, setup 168ms, collect 523ms, tests 183ms, environment 1.12s, prepare 184ms)
```

## ✅ frontend build

```
> ai-creative-studio-frontend@0.1.0 build
> tsc -b && vite build

vite v6.4.3 building for production...
transforming...
✓ 2181 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.60 kB │ gzip:   0.36 kB
dist/assets/index-D6u2tl0h.css   38.08 kB │ gzip:   7.42 kB
dist/assets/index-CSmD-R2X.js   628.83 kB │ gzip: 191.45 kB
✓ built in 4.53s
```

## ✅ end-to-end smoke test

```
[PASS] health endpoint — db=ok ffmpeg=True
[PASS] login — user=admin@studio.ai role=owner
[PASS] unauthenticated request is rejected
[PASS] create project — prj_01M27HHBHF76AQ54TYMGNDFEZ4
[PASS] prompt enhancement
[PASS] image job enqueued — engine=local-cpu-image
[PASS] image generation completed — status=completed 
[PASS] image asset stored — 1008x576 904639B
[PASS] image upscale completed — status=completed
[PASS] video generation completed — status=completed
[PASS] video asset stored — 3.0s 672x384
[PASS] voice generation completed — status=completed
[PASS] music generation completed — status=completed
[PASS] talking avatar completed — status=completed
[PASS] story scenes generated — status=completed
[PASS] story persisted scenes — 3 scenes
[PASS] timeline export completed — status=completed
[PASS] assistant planning — intent=create_image
[PASS] model registry — 7/17 ready
[PASS] uninstalled model is reported — POST /generate/image → 409: {"error": {"code": "model_not_installed", "message": "Model not installed: Diffusion Image M

20/20 checks passed
```
