"""
rx_upload_server.py — DMELogic Phone Rx Upload Server

Runs a lightweight FastAPI server on 0.0.0.0:8402 so any device on the local
network (or Tailscale) can open a browser, take photos of a prescription, and
have the images converted to a PDF and saved directly to the New Orders intake
folder — without needing any app installed on the phone.

Each upload session has a one-time token that expires in 10 minutes.
"""

import io
import logging
import secrets
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

log = logging.getLogger("rx_upload_server")

_UPLOAD_PORT = 8402
_SESSION_TTL = 600  # 10 minutes

_sessions: Dict[str, Dict[str, Any]] = {}
_sessions_lock = threading.Lock()

_server_thread: Optional[threading.Thread] = None
_server_started = threading.Event()
_server_last_error: Optional[str] = None
_SCAN_DPI = 200          # fax-like resolution; plenty for Rx text + OCR
_SCAN_MAX_LONG_EDGE = 2200   # ≈ letter page at 200 DPI; camera photos get downscaled
_SCAN_JPEG_QUALITY = 72  # grayscale JPEG — keeps handwriting readable, ~10x smaller than PNG


def _detect_document_crop_box(gray):
    """Find the bounds of the bright document area inside a phone photo."""
    from PIL import Image, ImageChops

    arr = np.asarray(gray, dtype=np.uint8)
    if arr.size == 0:
        return None

    # Prefer the large bright page region over the noisy dark surround.
    # Absolute floor 140 (not 170): indoor/dim photos have page pixels well
    # below 170 in shadowed areas, and a too-high floor used to slice off the
    # shadowed side of the page.
    bright_floor = max(140, int(np.percentile(arr, 70)))
    bright = arr >= bright_floor
    if not bright.any():
        bright_floor = max(150, int(np.percentile(arr, 60)))
        bright = arr >= bright_floor
        if not bright.any():
            bright = None

    if bright is not None:
        row_ratio = bright.mean(axis=1)
        col_ratio = bright.mean(axis=0)
        row_cutoff = max(0.12, float(row_ratio.max()) * 0.55)
        col_cutoff = max(0.12, float(col_ratio.max()) * 0.55)
        row_idx = np.flatnonzero(row_ratio >= row_cutoff)
        col_idx = np.flatnonzero(col_ratio >= col_cutoff)
        if row_idx.size and col_idx.size:
            x0 = int(col_idx[0])
            y0 = int(row_idx[0])
            x1 = int(col_idx[-1]) + 1
            y1 = int(row_idx[-1]) + 1
            if x1 - x0 > 10 and y1 - y0 > 10:
                return (x0, y0, x1, y1)

    # Fallback for cleaner captures with a near-uniform surround.
    # `gray` is already mode "L"; PIL.Image.new is a module function, not a
    # method on the Image class (the previous gray.__class__.new(...) raised
    # "type object 'Image' has no attribute 'new'" and killed the whole scan).
    bg = gray.getpixel((0, 0))
    diff = ImageChops.difference(gray, Image.new("L", gray.size, bg))
    bbox = diff.getbbox()
    if bbox:
        x0, y0, x1, y1 = bbox
        if x1 - x0 > 10 and y1 - y0 > 10:
            return bbox

    return None


def _crop_keeps_content(arr, box) -> bool:
    """
    Safety guard: True only if cropping `arr` (grayscale ndarray) to `box`
    keeps essentially all of the text ink.

    "Ink" here is sparse dark pixels — dark pixels that are NOT part of solid
    dark bands (the desk / black letterbox bars are solid-dark rows/columns and
    are excluded). If the proposed box would lose more than ~2% of the ink, the
    crop is unsafe and the caller must keep the full image.
    """
    try:
        dark = arr < 100
        row_solid = dark.mean(axis=1) > 0.5
        col_solid = dark.mean(axis=0) > 0.5
        sparse = dark & ~row_solid[:, None] & ~col_solid[None, :]
        total = int(sparse.sum())
        if total == 0:
            return True
        x0, y0, x1, y1 = box
        inside = int(sparse[y0:y1, x0:x1].sum())
        return (total - inside) <= max(50, int(0.02 * total))
    except Exception:
        return False  # if the guard itself fails, do the safe thing: no crop


def _scan_enhance(data: bytes):
    """Turn a phone photo into a clean, scanned-document-style page.

    Pipeline: EXIF auto-rotate → grayscale → auto-crop document bounds →
    median denoise → illumination flattening → autocontrast → background
    whitening. The output is a clean *grayscale* page (like a real document
    scanner) rather than the original colour photo, which both looks correct
    and deflates to a much smaller file. The aggressive denoising keeps flat
    paper areas from filling with salt-and-pepper sensor noise.

    Returns a tuple ``(png_bytes, width_px, height_px, ocr_image)`` at 300 DPI,
    where ``ocr_image`` is the same cleaned *grayscale* PIL image. Returns
    ``None`` on failure so the caller can fall back to embedding the raw photo
    unchanged.
    """
    try:
        from PIL import Image, ImageFilter, ImageOps

        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)
        gray = im.convert("L")

        # Decide what kind of capture this is. A phone photo of a script sits on
        # a DARKER surface, so we crop away the surround and whiten it. An
        # already-clean document image — a scan, screenshot, or PDF/fax the user
        # chose from their phone's files — has a LIGHT border and must be left
        # whole: cropping those clips real content (pharmacy name, dispense box,
        # etc.). The signal is the brightness of a thin border frame.
        _a = np.asarray(gray, dtype=np.uint8)
        _b = max(2, min(_a.shape[0], _a.shape[1]) // 100)
        _frame = np.concatenate([
            _a[:_b, :].ravel(), _a[-_b:, :].ravel(),
            _a[:, :_b].ravel(), _a[:, -_b:].ravel(),
        ])
        photo_on_surface = float(np.median(_frame)) < 130

        # Auto-trim the bright page region — ONLY for photos on a dark surround,
        # and ONLY when the content-safety guard confirms no text ink is lost.
        # If the guard rejects the box, we keep the FULL image and also disable
        # the surround-whitening below: a complete page with an ugly background
        # always beats a clean-looking page missing content.
        if photo_on_surface:
            bbox = _detect_document_crop_box(gray)
            if bbox:
                x0, y0, x1, y1 = bbox
                # Keep a small margin so we don't clip the page edge text.
                pad = max(2, min(gray.width, gray.height) // 150)
                x0 = max(0, x0 - pad)
                y0 = max(0, y0 - pad)
                x1 = min(gray.width, x1 + pad)
                y1 = min(gray.height, y1 + pad)
                if x1 - x0 > 10 and y1 - y0 > 10:
                    if _crop_keeps_content(_a, (x0, y0, x1, y1)):
                        gray = gray.crop((x0, y0, x1, y1))
                    else:
                        log.info("Scan crop skipped: would cut text content; keeping full image")
                        photo_on_surface = False  # also skip surround-whitening

        # Denoise FIRST: a small median filter removes the speckle/grain that
        # phone sensors produce on flat paper. This is the single most important
        # step for keeping the background clean.
        gray = gray.filter(ImageFilter.MedianFilter(size=3))

        # Flatten uneven lighting by dividing the image by a heavily-blurred
        # estimate of the page background, then renormalize. This makes the
        # paper a uniform white regardless of shadows/gradients.
        radius = max(20, min(gray.width, gray.height) // 20)
        bg_est = gray.filter(ImageFilter.GaussianBlur(radius))
        g = np.asarray(gray, dtype=np.float32)
        b = np.asarray(bg_est, dtype=np.float32) + 1.0
        flat = np.clip(g / b * 255.0, 0, 255).astype(np.uint8)
        flat_img = ImageOps.autocontrast(Image.fromarray(flat, mode="L"), cutoff=1)

        # Produce a clean GRAYSCALE scan (not a harsh 1-bit threshold). Keeping
        # the denoised, illumination-flattened strokes at their true gray levels
        # preserves faint handwriting on medical Rx (a bitonal threshold can
        # drop light pencil/pen). Near-white paper is pushed to pure white so
        # the background is uniform and the PNG deflates to a small file.
        arr = np.asarray(flat_img, dtype=np.uint8)
        WHITE_FLOOR = 200   # pixels at/above this are treated as blank paper
        arr = np.where(arr >= WHITE_FLOOR, 255, arr).astype(np.uint8)

        # Whiten everything OUTSIDE the paper — ONLY for photos on a surround.
        # A tilted phone photo leaves large dark corners inside the rectangular
        # crop; per-row and per-column bright spans approximate the (possibly
        # rotated) paper region, and anything outside both spans is background,
        # set to pure white. Skipped for clean document images so their real
        # margins/content are never blanked.
        try:
            if photo_on_surface:
                paper = arr >= 190

                def _span_mask(m):
                    idx = np.arange(m.shape[1])
                    any_row = m.any(axis=1)
                    first = np.where(any_row, m.argmax(axis=1), 0)
                    last = np.where(any_row, m.shape[1] - 1 - m[:, ::-1].argmax(axis=1), -1)
                    return (idx[None, :] >= first[:, None]) & (idx[None, :] <= last[:, None]) & any_row[:, None]

                inside = _span_mask(paper) & _span_mask(paper.T).T
                arr = np.where(inside, arr, 255).astype(np.uint8)
        except Exception:
            pass

        scan_img = Image.fromarray(arr, mode="L")

        # Downscale to fax-like resolution: cameras shoot 3000-4000px, but a
        # letter page at 200 DPI is ~1700x2200 — beyond that is wasted bytes.
        long_edge = max(scan_img.width, scan_img.height)
        if long_edge > _SCAN_MAX_LONG_EDGE:
            ratio = _SCAN_MAX_LONG_EDGE / long_edge
            scan_img = scan_img.resize(
                (max(1, round(scan_img.width * ratio)),
                 max(1, round(scan_img.height * ratio))),
                Image.LANCZOS,
            )

        # This same cleaned grayscale image is also the best input for OCR.
        ocr_image = scan_img

        # JPEG, not PNG: photographic gray texture deflates poorly in PNG
        # (~1 MB/page) but compresses ~10x better as JPEG at this quality —
        # bringing phone captures in line with incoming fax file sizes.
        buf = io.BytesIO()
        scan_img.save(buf, format="JPEG", quality=_SCAN_JPEG_QUALITY,
                      optimize=True, dpi=(_SCAN_DPI, _SCAN_DPI))
        return buf.getvalue(), scan_img.width, scan_img.height, ocr_image
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Scan enhancement failed, using raw photo: %s", exc)
        return None

# ── FastAPI app (created lazily) ──────────────────────────────────────────────
_app = None


def _build_app():
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse

    fast = FastAPI(title="DMELogic Rx Upload", docs_url=None, redoc_url=None)
    fast.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @fast.get("/rx-upload/{token}", response_class=HTMLResponse)
    async def upload_page(token: str):
        session = _get_session(token)
        if session is None:
            return HTMLResponse(_expired_page("Link not found or expired."), status_code=404)
        if session["status"] == "done":
            return HTMLResponse(_done_page())
        return HTMLResponse(_mobile_page_html(token))

    @fast.post("/rx-upload/{token}/upload")
    async def receive_upload(token: str, request: Request):
        session = _get_session(token)
        if session is None:
            raise HTTPException(404, "Session not found or expired")
        if session["status"] == "done":
            raise HTTPException(409, "Already uploaded for this session")

        form = await request.form()
        photo_files = form.getlist("photos")
        if not photo_files:
            raise HTTPException(400, "No photos received — select at least one image")

        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise HTTPException(500, "PDF library not available on server")

        doc = fitz.open()
        skipped = 0
        for f in photo_files:
            data = await f.read()
            try:
                # A file chosen from the phone (not a live photo) may be a PDF —
                # embed its pages as-is rather than trying to treat it as an image.
                content_type = (getattr(f, "content_type", "") or "").lower()
                filename = (getattr(f, "filename", "") or "").lower()
                if content_type == "application/pdf" or filename.endswith(".pdf") or data[:5] == b"%PDF-":
                    src_pdf = fitz.open(stream=data, filetype="pdf")
                    doc.insert_pdf(src_pdf)
                    src_pdf.close()
                    continue

                enhanced = _scan_enhance(data)
                if enhanced is not None:
                    # Clean grayscale scan placed at true 300 DPI: the page
                    # is sized to the pixel dimensions / 300 dpi in PDF points.
                    img_bytes, w_px, h_px, _ocr_img = enhanced
                    w_pt = w_px / _SCAN_DPI * 72.0
                    h_pt = h_px / _SCAN_DPI * 72.0
                    page = doc.new_page(width=w_pt, height=h_pt)
                    page.insert_image(page.rect, stream=img_bytes)
                else:
                    # Fallback: embed the original photo unchanged. An image
                    # document can't be drawn with show_pdf_page (needs a PDF
                    # source), so convert it to a single-page PDF and append it.
                    img_doc = fitz.open(stream=data, filetype="image")
                    pdf_bytes = img_doc.convert_to_pdf()
                    img_doc.close()
                    img_pdf = fitz.open("pdf", pdf_bytes)
                    doc.insert_pdf(img_pdf)
                    img_pdf.close()
            except Exception as exc:
                skipped += 1
                log.warning("Could not process uploaded image: %s", exc)

        if doc.page_count == 0:
            doc.close()
            raise HTTPException(422, "No valid images could be processed")

        save_folder = Path(session["save_folder"])
        save_folder.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = save_folder / f"PhoneRx_{stamp}.pdf"
        counter = 1
        while dest.exists():
            dest = save_folder / f"PhoneRx_{stamp}_{counter}.pdf"
            counter += 1

        try:
            # Optimize on save: deflate all streams (including the grayscale
            # scan images), garbage-collect and clean redundant objects. On
            # these flattened grayscale scans this shrinks pages dramatically
            # versus embedding the raw colour photo, keeping phone Rx files small.
            doc.save(
                str(dest),
                deflate=True,
                deflate_images=True,
                deflate_fonts=True,
                garbage=4,
                clean=True,
            )
            page_count = doc.page_count
        finally:
            doc.close()

        with _sessions_lock:
            session["status"] = "done"
            session["saved_path"] = str(dest)

        log.info("Phone Rx upload saved: %s", dest)

        # Fire callback on a thread so we don't block the upload response
        cb = session.get("callback")
        if callable(cb):
            threading.Thread(target=cb, args=(str(dest),), daemon=True).start()

        return JSONResponse({"status": "ok", "filename": dest.name, "pages": page_count})

    @fast.get("/rx-upload/{token}/status")
    async def session_status(token: str):
        with _sessions_lock:
            session = _sessions.get(token)
        if session is None:
            return JSONResponse({"status": "not_found"})
        age = time.time() - session["created_at"]
        if age > _SESSION_TTL:
            return JSONResponse({"status": "expired"})
        return JSONResponse({
            "status": session["status"],
            "expires_in": max(0, int(_SESSION_TTL - age)),
            "saved_path": session.get("saved_path"),
        })

    return fast


# ── Public API ────────────────────────────────────────────────────────────────

def ensure_server_running() -> int:
    """Start the upload server if not already running. Returns the port."""
    global _server_thread, _app, _server_last_error

    if _server_thread is not None and _server_thread.is_alive():
        return _UPLOAD_PORT

    _server_last_error = None
    _server_started.clear()
    _app = _build_app()

    def _run():
        global _server_last_error
        try:
            import uvicorn
            _server_started.set()
            # log_config=None: skip uvicorn's default dictConfig, which fails
            # with "Unable to configure formatter" inside frozen PyInstaller
            # builds (no console/stdout handler available).
            # ws="none": this server is plain HTTP (QR page + photo POST). The
            # default ws="auto" makes uvicorn import its websockets protocol
            # class, which crashes in frozen builds when the bundled
            # `websockets` package is incomplete (ModuleNotFoundError:
            # websockets.exceptions). We never use websockets here, so skip it.
            config = uvicorn.Config(
                _app,
                host="0.0.0.0",
                port=_UPLOAD_PORT,
                log_level="warning",
                access_log=False,
                log_config=None,
                ws="none",
            )
            uvicorn.Server(config).run()
        except Exception as exc:
            _server_last_error = f"{type(exc).__name__}: {exc}"
            log.exception("Rx upload server failed to start")

    _server_thread = threading.Thread(target=_run, daemon=True, name="rx-upload-server")
    _server_thread.start()
    _server_started.wait(timeout=3.0)

    # Verify the socket is truly listening before returning success.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if _is_local_port_open(_UPLOAD_PORT):
            return _UPLOAD_PORT
        if _server_thread is not None and not _server_thread.is_alive() and _server_last_error:
            break
        time.sleep(0.15)

    if _server_last_error:
        raise RuntimeError(f"Rx upload server failed to start: {_server_last_error}")
    raise RuntimeError("Rx upload server failed to start on port 8402")


def _is_local_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def create_session(save_folder: str, callback: Optional[Callable[[str], None]] = None) -> str:
    """Create a new one-time upload session. Returns the session token."""
    token = secrets.token_urlsafe(20)
    with _sessions_lock:
        _sessions[token] = {
            "created_at": time.time(),
            "status": "pending",
            "save_folder": save_folder,
            "saved_path": None,
            "callback": callback,
        }
    _cleanup_expired()
    return token


def get_upload_url(token: str, host_ip: str) -> str:
    return f"http://{host_ip}:{_UPLOAD_PORT}/rx-upload/{token}"


def get_server_port() -> int:
    return _UPLOAD_PORT


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_session(token: str) -> Optional[Dict[str, Any]]:
    with _sessions_lock:
        s = _sessions.get(token)
    if s is None:
        return None
    if time.time() - s["created_at"] > _SESSION_TTL:
        return None
    return s


def _cleanup_expired():
    cutoff = time.time() - _SESSION_TTL * 2
    with _sessions_lock:
        expired = [t for t, s in _sessions.items() if s["created_at"] < cutoff]
        for t in expired:
            del _sessions[t]


# ── Mobile HTML pages ─────────────────────────────────────────────────────────

def _mobile_page_html(token: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Capture Rx — DMELogic</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f4ff;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:20px 16px}}
.card{{background:#fff;border-radius:18px;padding:24px 20px;width:100%;max-width:440px;box-shadow:0 4px 32px rgba(0,0,0,.12)}}
h1{{font-size:21px;font-weight:800;color:#0f172a;margin-bottom:4px}}
.sub{{color:#64748b;font-size:13px;margin-bottom:20px;line-height:1.4}}
.previews{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:18px;min-height:60px}}
.previews img{{width:100%;aspect-ratio:3/4;object-fit:cover;border-radius:10px;border:2px solid #e2e8f0}}
.btn{{display:block;width:100%;padding:15px;border-radius:13px;border:none;font-size:16px;font-weight:700;cursor:pointer;margin-bottom:10px;transition:opacity .15s}}
.btn-cam{{background:#2563eb;color:#fff}}
.btn-cam:active{{opacity:.8}}
.btn-pick{{background:#e0e7ff;color:#3730a3}}
.btn-pick:active{{opacity:.8}}
.btn-up{{background:#16a34a;color:#fff}}
.btn-up:disabled{{background:#94a3b8;cursor:not-allowed}}
.btn-up:not(:disabled):active{{opacity:.8}}
.pdf-chip{{display:inline-flex;align-items:center;gap:4px;background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;border-radius:10px;padding:8px 12px;font-size:13px;font-weight:700;margin:4px}}
#status{{text-align:center;font-size:13px;color:#64748b;margin-top:6px;min-height:18px}}
.done{{text-align:center;padding:28px 0}}
.done .icon{{font-size:52px;margin-bottom:12px}}
.done h2{{font-size:20px;font-weight:800;color:#16a34a}}
.done p{{color:#64748b;font-size:14px;margin-top:6px}}
#file-in,#file-pick{{display:none}}
.page-count{{display:inline-block;background:#eff6ff;color:#2563eb;border-radius:20px;padding:2px 10px;font-size:12px;font-weight:700;margin-left:8px}}
</style>
</head>
<body>
<div class="card" id="main-card">
  <h1>📋 Capture Rx <span class="page-count" id="pg-count" style="display:none">0 pages</span></h1>
  <p class="sub">Take a photo of each page, <b>or</b> choose an existing photo/PDF from your phone, then tap Send.</p>
  <div class="previews" id="previews"></div>
  <label for="file-in">
    <div class="btn btn-cam" role="button">📷 Take Photo / Add Page</div>
  </label>
  <input type="file" id="file-in" accept="image/*" capture="environment" multiple>
  <label for="file-pick">
    <div class="btn btn-pick" role="button">🖼️ Choose from Files / Gallery</div>
  </label>
  <input type="file" id="file-pick" accept="image/*,application/pdf" multiple>
  <button class="btn btn-up" id="up-btn" disabled>⬆️ Send to New Orders</button>
  <div id="status"></div>
</div>
<script>
const TOKEN='{token}';
const files=[];
const fi=document.getElementById('file-in');
const fp=document.getElementById('file-pick');
const previews=document.getElementById('previews');
const upBtn=document.getElementById('up-btn');
const status=document.getElementById('status');
const pgCount=document.getElementById('pg-count');

function addFiles(list){{
  Array.from(list).forEach(f=>{{files.push(f);addPreview(f)}});
  upBtn.disabled=files.length===0;
  pgCount.style.display='';
  pgCount.textContent=files.length+' page'+(files.length!==1?'s':'');
  status.textContent='';
}}
fi.addEventListener('change',()=>{{addFiles(fi.files);fi.value='';}});
fp.addEventListener('change',()=>{{addFiles(fp.files);fp.value='';}});

function addPreview(f){{
  if(f.type==='application/pdf'||(f.name||'').toLowerCase().endsWith('.pdf')){{
    const chip=document.createElement('div');
    chip.className='pdf-chip';
    chip.textContent='📄 '+(f.name||'PDF');
    previews.appendChild(chip);
    return;
  }}
  const img=document.createElement('img');
  img.src=URL.createObjectURL(f);
  previews.appendChild(img);
}}

upBtn.addEventListener('click',async()=>{{
  if(!files.length)return;
  upBtn.disabled=true;
  status.textContent='Uploading… please wait';
  const fd=new FormData();
  files.forEach(f=>fd.append('photos',f));
    const explain=(value)=>{{
        if(value===null||value===undefined)return '';
        if(typeof value==='string')return value;
        if(Array.isArray(value))return value.map(explain).filter(Boolean).join('; ');
        if(typeof value==='object'){{
            if('msg' in value && typeof value.msg==='string')return value.msg;
            if('detail' in value)return explain(value.detail);
            try{{return JSON.stringify(value);}}catch(_e){{return String(value);}}
        }}
        return String(value);
    }};
  try{{
    const r=await fetch('/rx-upload/'+TOKEN+'/upload',{{method:'POST',body:fd}});
    if(r.ok){{
      document.getElementById('main-card').innerHTML=`
        <div class="done">
          <div class="icon">✅</div>
          <h2>Uploaded!</h2>
          <p>The Rx has been sent to New Orders.<br>You can close this tab.</p>
        </div>`;
    }}else{{
      const d=await r.json().catch(()=>({{}}));
            const msg = explain(d.detail ?? d) || ('HTTP '+r.status);
            status.textContent='Error: '+msg;
      upBtn.disabled=false;
    }}
  }}catch(e){{
        const emsg = (e && e.message) ? e.message : String(e);
        status.textContent='Network error — '+emsg;
    upBtn.disabled=false;
  }}
}});
</script>
</body>
</html>"""


def _done_page() -> str:
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Already uploaded</title>
<style>body{{font-family:sans-serif;text-align:center;padding:48px 16px;color:#0f172a}}</style>
</head><body>
<div style="font-size:48px">✅</div>
<h2 style="margin-top:12px">Already uploaded</h2>
<p style="color:#64748b;margin-top:8px">This link was already used. Ask staff for a new QR code if needed.</p>
</body></html>"""


def _expired_page(msg: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link expired</title>
<style>body{{font-family:sans-serif;text-align:center;padding:48px 16px;color:#0f172a}}</style>
</head><body>
<div style="font-size:48px">⏱️</div>
<h2 style="margin-top:12px">Link expired</h2>
<p style="color:#64748b;margin-top:8px">{msg}</p>
</body></html>"""
