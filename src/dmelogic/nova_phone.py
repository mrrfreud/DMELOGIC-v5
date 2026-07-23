"""
Nova Phone — server-side support for the "📞 Answer Calls" feature.

Phase 1 scope:
  - sip_provision(): fetch WebRTC softphone credentials from RingCentral
    (POST /restapi/v1.0/client-info/sip-provision) using the OAuth token
    already managed by dmelogic.services.ringcentral_service.
  - Answer-toggle state shared with the incoming-call monitor so the UI can
    render "Nova is answering" instead of a plain ringing alert.
  - Greeting text/config for the browser softphone.

Phase 2 adds: transcribe_utterance() (ElevenLabs Scribe) and per-call agent
session management for the /call-audio WebSocket.
"""

from __future__ import annotations

import io
import logging
import os
import threading
from typing import Any, Dict, Optional

import requests

log = logging.getLogger("nova_phone")

# ── Config ──────────────────────────────────────────────────────────────────
TRANSFER_NUMBER = os.getenv("NOVA_PHONE_TRANSFER_NUMBER", "").strip()
MAX_CALL_SECONDS = int(os.getenv("NOVA_PHONE_MAX_CALL_SECONDS", "600") or "600")
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2").strip() or "scribe_v2"
DEFAULT_GREETING = (
    os.getenv("NOVA_PHONE_GREETING", "").strip()
    or "Thank you for calling 1st Aid Pharmacy & Surgical Supplies. This is Nova. "
       "Would you like to continue in English or Spanish? "
       "Gracias por llamar a 1st Aid Pharmacy & Surgical Supplies. Le habla Nova. "
       "¿Desea continuar en inglés o en español?"
)

# ── Answer-toggle state (single owner tab) ──────────────────────────────────
_state_lock = threading.Lock()
_answer_enabled = False
_owner_id = ""


def set_answer_state(enabled: bool, owner: str = "") -> Dict[str, Any]:
    global _answer_enabled, _owner_id
    with _state_lock:
        _answer_enabled = bool(enabled)
        _owner_id = str(owner or "") if enabled else ""
        return {"enabled": _answer_enabled, "owner": _owner_id}


def is_answer_enabled() -> bool:
    with _state_lock:
        return _answer_enabled


def get_config() -> Dict[str, Any]:
    return {
        "transfer_number": TRANSFER_NUMBER,
        "max_call_seconds": MAX_CALL_SECONDS,
        "greeting": DEFAULT_GREETING,
    }


# ── RingCentral SIP provisioning ────────────────────────────────────────────
def _rc_service():
    from dmelogic.settings import load_settings
    from dmelogic.services.ringcentral_service import get_ringcentral_service
    return get_ringcentral_service(load_settings())


def sip_provision() -> Dict[str, Any]:
    """Fetch SIP/WebRTC registration credentials for the browser softphone.

    Returns {"ok": True, "provision": <raw sip-provision JSON>} on success, or
    {"ok": False, "status": <int>, "error": <code>, "detail": <msg>}.
    """
    try:
        svc = _rc_service()
        if svc is None or not svc.is_connected:
            return {"ok": False, "status": 409, "error": "not_connected",
                    "detail": "RingCentral is not connected. Ask Nova to connect RingCentral first."}
        token = svc.access_token
        if not token:
            return {"ok": False, "status": 409, "error": "token_refresh_failed",
                    "detail": "RingCentral token refresh failed — reconnect RingCentral."}

        url = f"{svc.config.server_url}/restapi/v1.0/client-info/sip-provision"
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"sipInfo": [{"transport": "WSS"}]},
            timeout=20,
        )
        if r.status_code == 200:
            return {"ok": True, "provision": r.json()}
        if r.status_code == 403:
            return {"ok": False, "status": 403, "error": "voip_permission_missing",
                    "detail": "The RingCentral app is missing the 'VoIP Calling' permission. "
                              "Enable it in the RingCentral developer portal, then reconnect RingCentral."}
        return {"ok": False, "status": r.status_code, "error": "sip_provision_failed",
                "detail": r.text[:400]}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "status": 503, "error": "network",
                "detail": "Could not reach RingCentral (network error)."}
    except Exception as e:
        log.exception("sip_provision failed")
        return {"ok": False, "status": 500, "error": "internal", "detail": str(e)[:400]}


# ── Speech-to-text (Phase 2) ────────────────────────────────────────────────
_stt_client = None

# Bias the recognizer toward date-of-birth vocabulary so months, days, and
# years come through cleanly even with an accent. These are the words callers
# most often get misheard on when stating a DOB.
_MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
_MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
              "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
_NUMWORDS_EN = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh",
                "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
                "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
                "nineteenth", "twentieth", "thirtieth", "nineteen", "twenty",
                "date of birth", "birthday"]
_NUMWORDS_ES = ["primero", "dos", "tres", "cuatro", "cinco", "seis", "siete",
                "ocho", "nueve", "diez", "once", "doce", "trece", "catorce",
                "quince", "dieciseis", "diecisiete", "dieciocho", "diecinueve",
                "veinte", "treinta", "mil", "novecientos", "fecha de nacimiento",
                "cumpleanos"]


def _dob_keyterms(language: str) -> list:
    lang = str(language or "").lower()
    if lang.startswith("es"):
        return _MONTHS_ES + _NUMWORDS_ES
    if lang.startswith("en"):
        return _MONTHS_EN + _NUMWORDS_EN
    # Unknown language: help both.
    return _MONTHS_EN + _MONTHS_ES


def _get_stt_client():
    """Reuse one ElevenLabs client across utterances (lower per-turn latency)."""
    global _stt_client
    if _stt_client is None:
        from elevenlabs.client import ElevenLabs
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            return None
        _stt_client = ElevenLabs(api_key=api_key)
    return _stt_client


def transcribe_utterance(audio_bytes: bytes, mime: str = "audio/webm",
                         language: str = "") -> Optional[str]:
    """Transcribe one caller utterance with ElevenLabs Scribe. Returns None on failure.

    language: "en" or "es" gives Scribe a strong hint so accented speech and
    short utterances (like a date of birth) are recognized far more reliably.
    Empty string lets Scribe auto-detect.
    """
    try:
        if not audio_bytes:
            return None
        client = _get_stt_client()
        if client is None:
            return None

        lang = str(language or "").lower()
        language_code = "es" if lang.startswith("es") else ("en" if lang.startswith("en") else None)

        kwargs = dict(
            file=io.BytesIO(audio_bytes),
            model_id=ELEVENLABS_STT_MODEL,
            tag_audio_events=False,
        )
        if language_code:
            kwargs["language_code"] = language_code
        # keyterms biasing is only supported by scribe_v2+.
        if ELEVENLABS_STT_MODEL.lower() not in ("scribe_v1",):
            try:
                kwargs["keyterms"] = _dob_keyterms(language)
            except Exception:
                pass

        try:
            result = client.speech_to_text.convert(**kwargs)
        except TypeError:
            # Older SDK without keyterms/tag_audio_events/language_code kwargs.
            result = client.speech_to_text.convert(
                file=io.BytesIO(audio_bytes), model_id=ELEVENLABS_STT_MODEL)
        text = (getattr(result, "text", "") or "").strip()
        return text or None
    except Exception as e:
        log.error(f"transcribe_utterance failed: {type(e).__name__}: {e}")
        return None
