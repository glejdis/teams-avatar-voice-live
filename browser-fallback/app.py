from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timezone
import urllib.error
import urllib.request

from azure.ai.voicelive.aio import AgentSessionConfig, connect
from azure.ai.voicelive.aio._patch import ConnectionClosed
from azure.ai.voicelive.models import (
    AudioInputTranscriptionOptions,
    AvatarConfig,
    AzureStandardVoice,
    Background,
    ClientEventSessionAvatarConnect,
    InputAudioFormat,
    InputTextContentPart,
    MessageItem,
    Modality,
    OutputAudioFormat,
    RequestSession,
    ServerEventType,
    ServerVad,
    VideoParams,
    VideoResolution,
)
from azure.core.credentials import AzureKeyCredential
from azure.identity.aio import AzureCliCredential, DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"

load_dotenv(REPO_ROOT / ".env", override=False)
load_dotenv(ROOT / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("lisa-demo")

DEFAULT_API_VERSION = "2026-01-01-preview"
DEFAULT_MODEL = "gpt-realtime-1.5"
DEFAULT_VOICE = "en-US-AvaMultilingualNeural"
DEFAULT_INSTRUCTIONS = (
    "You are a friendly assistant. Keep answers short, natural, and spoken. "
    "Ask one question at a time. Do not read markdown, bullets, or formatting "
    "aloud. (Override via the AGENT_DEMO_INSTRUCTIONS env var.)"
)
DEFAULT_AVATAR_CHROMA_COLOR = "#00ff00"
DEFAULT_TEAMS_AVATAR_BACKGROUND_IMAGE = "/background.png"

app = FastAPI(title="Lisa Teams WebRTC Demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _reset_local_teams_demo_state() -> None:
    if os.getenv("BROWSER_DEMO_RESET_ON_START", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return

    transcript_path = _demo_transcripts_path()
    if transcript_path.exists():
        try:
            payload = json.loads(transcript_path.read_text(encoding="utf-8"))
            transcripts = payload.get("transcripts") if isinstance(payload, dict) else None
            if isinstance(transcripts, dict):
                removed = []
                for candidate_id, record in list(transcripts.items()):
                    if isinstance(record, dict) and "teams" in str(record.get("channel") or "").lower():
                        removed.append(candidate_id)
                        transcripts.pop(candidate_id, None)
                if removed:
                    tmp = transcript_path.with_suffix(transcript_path.suffix + ".tmp")
                    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                    tmp.replace(transcript_path)
                    logger.info("Reset local Teams demo transcripts: removed %s", ", ".join(removed))
        except Exception:
            logger.exception("Failed to reset local Teams demo transcripts")

    latest_invite_path = _latest_invite_path()
    for path in (latest_invite_path, latest_invite_path.with_suffix(latest_invite_path.suffix + ".tmp")):
        try:
            if path.exists():
                path.unlink()
                logger.info("Reset local Teams demo invite handoff: deleted %s", path)
        except Exception:
            logger.exception("Failed to delete local Teams demo invite handoff %s", path)


@app.on_event("startup")
async def reset_local_teams_demo_state_on_startup() -> None:
    _reset_local_teams_demo_state()


def _agent_configured() -> bool:
    return bool(os.getenv("AGENT_FOUNDRY_AGENT_NAME") and os.getenv("AGENT_FOUNDRY_PROJECT_NAME"))


def _latest_invite_path() -> Path:
    configured = os.getenv("DEMO_LATEST_INVITE_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path.resolve()
    return DATA_DIR / "latest-invite.json"


def _read_latest_invite() -> dict[str, Any]:
    path = _latest_invite_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        logger.warning("could not read latest invite file %s: %s", path, exc)
        return {}


def _auto_join_log_path() -> Path:
    configured = os.getenv("BROWSER_DEMO_AUTO_JOIN_LOG_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path.resolve()
    return DATA_DIR / "auto-join-decisions.jsonl"


def _client_log_path() -> Path:
    configured = os.getenv("BROWSER_DEMO_CLIENT_LOG_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path.resolve()
    return DATA_DIR / "operator-events.jsonl"


def _demo_transcripts_path() -> Path:
    configured = os.getenv("BROWSER_DEMO_TRANSCRIPTS_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path.resolve()
    return DATA_DIR / "transcripts.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _pair_interview_exchanges(turns: list[dict[str, str]]) -> list[dict[str, str]]:
    exchanges: list[dict[str, str]] = []
    pending_question = ""
    for turn in turns:
        role = turn.get("role", "")
        text = turn.get("text", "").strip()
        if not text:
            continue
        if role == "assistant":
            pending_question = text
        elif role == "user":
            exchanges.append({
                "question": pending_question or "Candidate initiated the exchange.",
                "answer": text,
                "signal": "neutral",
            })
            pending_question = ""
    return exchanges


_transcripts_lock = asyncio.Lock()


async def _persist_interview_transcript(record: dict[str, Any]) -> None:
    path = _demo_transcripts_path()
    async with _transcripts_lock:
        try:
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    logger.warning("interview transcript file was invalid JSON; replacing %s", path)
                    payload = {}
            else:
                payload = {}
            transcripts = payload.get("transcripts")
            if not isinstance(transcripts, dict):
                transcripts = {}
            transcripts[record["candidate_id"]] = record
            payload["transcripts"] = transcripts
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(path)
            logger.info(
                "persisted Teams interview transcript candidate=%s exchanges=%d path=%s",
                record["candidate_id"],
                len(record.get("exchanges") or []),
                path,
            )
        except Exception:
            logger.exception("failed to persist Teams interview transcript")


def _build_agent_config() -> Optional[AgentSessionConfig]:
    agent_name = os.getenv("AGENT_FOUNDRY_AGENT_NAME")
    project_name = os.getenv("AGENT_FOUNDRY_PROJECT_NAME")
    if not (agent_name and project_name):
        return None
    cfg: AgentSessionConfig = {
        "agent_name": agent_name,
        "project_name": project_name,
    }
    agent_version = os.getenv("AGENT_FOUNDRY_AGENT_VERSION")
    if agent_version:
        cfg["agent_version"] = agent_version
    return cfg


def _resolve_endpoint(agent_mode: bool) -> str:
    endpoint = os.getenv("AZURE_VOICELIVE_ENDPOINT", "").strip()
    if agent_mode and ".cognitiveservices.azure.com" in endpoint:
        endpoint = endpoint.replace(".cognitiveservices.azure.com", ".services.ai.azure.com")
    return endpoint


def _build_credential():
    api_key = os.getenv("AZURE_VOICELIVE_API_KEY", "").strip()
    if api_key:
        return AzureKeyCredential(api_key)
    tenant_id = os.getenv("AZURE_TENANT_ID") or os.getenv("FOUNDRY_TENANT_ID")
    if tenant_id:
        return AzureCliCredential(tenant_id=tenant_id)
    return DefaultAzureCredential()


def _build_avatar_config(config: dict[str, Any]) -> AvatarConfig:
    width = int(config.get("avatarWidth") or os.getenv("AVATAR_VIDEO_WIDTH", "1280"))
    height = int(config.get("avatarHeight") or os.getenv("AVATAR_VIDEO_HEIGHT", "720"))
    bitrate = int(config.get("avatarBitrate") or os.getenv("AVATAR_VIDEO_BITRATE", "1500000"))
    gop_size = int(config.get("avatarGopSize") or os.getenv("AVATAR_VIDEO_GOP_SIZE", "30"))
    background_image_url = str(config.get("avatarBackgroundImageUrl") or os.getenv("AVATAR_BACKGROUND_IMAGE_URL", "")).strip()
    background_color = str(config.get("avatarBackgroundColor") or "").strip()
    if background_image_url:
        avatar_background = Background(image_url=background_image_url)
    elif background_color:
        avatar_background = Background(color=background_color)
    else:
        avatar_background = None
    avatar = AvatarConfig(
        character=str(config.get("avatarCharacter") or os.getenv("AVATAR_CHARACTER", "lisa")),
        style=str(config.get("avatarStyle") or os.getenv("AVATAR_STYLE", "casual-sitting")),
        output_audit_audio=False,
        video=VideoParams(
            codec="h264",
            resolution=VideoResolution(width=width, height=height),
            bitrate=bitrate,
            background=avatar_background,
            gop_size=gop_size,
        ),
    )
    try:
        avatar["output_protocol"] = "webrtc"
    except Exception:
        setattr(avatar, "output_protocol", "webrtc")
    return avatar


def _build_session(config: dict[str, Any], *, agent_mode: bool) -> RequestSession:
    voice_name = str(config.get("voice") or os.getenv("AZURE_VOICELIVE_VOICE", DEFAULT_VOICE))
    turn_detection = ServerVad(
        threshold=float(config.get("vadThreshold") or os.getenv("VOICELIVE_VAD_THRESHOLD", "0.5")),
        prefix_padding_ms=int(config.get("vadPrefixPaddingMs") or os.getenv("VOICELIVE_VAD_PREFIX_PADDING_MS", "150")),
        silence_duration_ms=int(config.get("vadSilenceDurationMs") or os.getenv("VOICELIVE_VAD_SILENCE_DURATION_MS", "350")),
    )
    common: dict[str, Any] = {
        "modalities": [Modality.TEXT, Modality.AUDIO],
        "voice": AzureStandardVoice(name=voice_name),
        "avatar": _build_avatar_config(config),
        "input_audio_format": InputAudioFormat.PCM16,
        "output_audio_format": OutputAudioFormat.PCM16,
        "input_audio_transcription": AudioInputTranscriptionOptions(
            model=str(config.get("speechRecognitionModel") or "azure-speech"),
            language=str(config.get("language") or "en"),
        ),
        "turn_detection": turn_detection,
    }
    if not agent_mode:
        common["instructions"] = str(config.get("instructions") or os.getenv("AGENT_DEMO_INSTRUCTIONS") or DEFAULT_INSTRUCTIONS)
        common["temperature"] = float(config.get("temperature") or os.getenv("VOICELIVE_TEMPERATURE", "0.7"))
    return RequestSession(**common)


def _ice_server_to_dict(server: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"urls": getattr(server, "urls", [])}
    username = getattr(server, "username", None)
    credential = getattr(server, "credential", None)
    if username:
        payload["username"] = username
    if credential:
        payload["credential"] = credential
    return payload


class VoiceSession:
    def __init__(self, client_id: str, websocket: WebSocket, config: dict[str, Any]):
        self.client_id = client_id
        self.websocket = websocket
        self.config = config
        self.connection = None
        self.running = False
        self.pending_proactive = bool(config.get("enableProactive", True))
        self.task: Optional[asyncio.Task] = None
        self.credential = None
        self.is_teams_session = client_id.endswith("--teams")
        self.candidate_id = str(config.get("candidateId") or config.get("candidate_id") or "").strip()
        self.candidate_name = str(config.get("candidateName") or config.get("candidate_name") or "").strip() or "Applicant"
        self.transcript_started_at = _iso_now()
        self.transcript_turns: list[dict[str, str]] = []
        self.persist_transcript = self.is_teams_session and bool(self.candidate_id)

    async def send(self, payload: dict[str, Any]) -> None:
        try:
            await self.websocket.send_text(json.dumps(payload))
        except Exception:
            logger.debug("client websocket already closed; dropping message type=%s", payload.get("type"))

    async def start(self) -> None:
        agent_config = _build_agent_config()
        agent_mode = agent_config is not None
        endpoint = _resolve_endpoint(agent_mode)
        if not endpoint:
            await self.send({"type": "session_error", "error": "AZURE_VOICELIVE_ENDPOINT is not set"})
            return

        api_version = os.getenv("AZURE_VOICELIVE_API_VERSION", DEFAULT_API_VERSION)
        model = str(self.config.get("model") or os.getenv("AZURE_VOICELIVE_MODEL", DEFAULT_MODEL))
        self.credential = _build_credential()
        connect_kwargs: dict[str, Any] = {
            "endpoint": endpoint,
            "credential": self.credential,
            "api_version": api_version,
        }
        if agent_config is not None:
            connect_kwargs["agent_config"] = agent_config
        else:
            connect_kwargs["model"] = model

        try:
            self.running = True
            async with connect(**connect_kwargs) as connection:
                self.connection = connection
                await connection.session.update(session=_build_session(self.config, agent_mode=agent_mode))
                await self._event_loop(connection, model=model, agent_mode=agent_mode)
        except asyncio.CancelledError:
            raise
        except ConnectionClosed as exc:
            logger.info("Voice Live connection closed for %s: %s", self.client_id, exc)
        except Exception as exc:
            logger.exception("Voice session failed")
            await self.send({"type": "session_error", "error": str(exc)})
        finally:
            self.running = False
            self.connection = None
            if active_sessions.get(self.client_id) is self:
                active_sessions.pop(self.client_id, None)
            if self.credential is not None and hasattr(self.credential, "close"):
                try:
                    await self.credential.close()
                except Exception:
                    pass
            await self.persist_current_transcript(completed=True)
            await self.send({"type": "session_closed"})

    async def stop(self) -> None:
        self.running = False
        if self.connection is not None:
            try:
                await self.connection.close()
            except Exception:
                pass
        if self.task and not self.task.done():
            self.task.cancel()

    async def send_audio(self, audio_base64: str) -> None:
        if not self.connection or not audio_base64:
            return
        await self.connection.input_audio_buffer.append(audio=audio_base64)

    async def send_text(self, text: str) -> None:
        if not self.connection or not text.strip():
            return
        await self.record_transcript_turn("user", text.strip())
        await self.connection.conversation.item.create(
            item=MessageItem(role="user", content=[InputTextContentPart(text=text.strip())])
        )
        await self.connection.response.create()

    async def record_transcript_turn(self, role: str, text: str) -> None:
        if not self.persist_transcript:
            return
        cleaned = text.strip()
        if not cleaned:
            return
        self.transcript_turns.append({"role": role, "text": cleaned, "ts": _iso_now()})

    async def persist_current_transcript(self, *, completed: bool = False) -> None:
        if not self.persist_transcript or not self.transcript_turns:
            return
        ended_at = _iso_now()
        try:
            started = datetime.fromisoformat(self.transcript_started_at.replace("Z", "+00:00"))
            ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            duration_minutes = max(1, round((ended - started).total_seconds() / 60))
        except Exception:
            duration_minutes = None
        exchanges = _pair_interview_exchanges(self.transcript_turns)
        record = {
            "candidate_id": self.candidate_id,
            "candidate_name": self.candidate_name,
            "interview_id": f"INT-{self.candidate_id}-TEAMS",
            "started_at": self.transcript_started_at,
            "ended_at": ended_at,
            "duration_minutes": duration_minutes,
            "status": "completed" if completed else "in_progress",
            "completed_at": ended_at if completed else "",
            "channel": "Lisa - Voice Live (Teams)",
            "language": str(self.config.get("language") or "en"),
            "summary": f"Live Teams interview captured by Lisa with {len(exchanges)} exchange(s).",
            "exchanges": exchanges,
            "raw_turns": self.transcript_turns,
            "red_flags": [],
            "positive_signals": [],
        }
        await _persist_interview_transcript(record)

    async def interrupt(self) -> None:
        if not self.connection:
            return
        try:
            await self.connection.response.cancel()
            await self.send({"type": "stop_playback", "reason": "interrupt"})
        except Exception as exc:
            logger.debug("interrupt failed: %s", exc)

    async def send_avatar_sdp_offer(self, client_sdp: str) -> None:
        if not self.connection or not client_sdp:
            return
        await self.connection.send(ClientEventSessionAvatarConnect(client_sdp=client_sdp))

    async def _event_loop(self, connection: Any, *, model: str, agent_mode: bool) -> None:
        while self.running:
            event = await connection.recv()
            etype = getattr(event, "type", None)
            if etype == ServerEventType.SESSION_UPDATED:
                session = getattr(event, "session", None)
                session_id = getattr(session, "id", None) if session is not None else None
                avatar = getattr(session, "avatar", None) if session is not None else None
                ice_servers = []
                for server in getattr(avatar, "ice_servers", []) or []:
                    ice_servers.append(_ice_server_to_dict(server))
                if ice_servers:
                    await self.send({"type": "ice_servers", "iceServers": ice_servers})
                await self.send({
                    "type": "session_started",
                    "sessionId": session_id,
                    "config": {
                        "model": model,
                        "agentMode": agent_mode,
                        "avatarEnabled": True,
                        "avatarOutputMode": "webrtc",
                    },
                })

            elif etype == ServerEventType.SESSION_AVATAR_CONNECTING:
                server_sdp = getattr(event, "server_sdp", "") or ""
                if server_sdp:
                    await self.send({"type": "avatar_sdp_answer", "serverSdp": server_sdp})
                if self.pending_proactive:
                    self.pending_proactive = False
                    try:
                        await connection.response.create()
                    except Exception as exc:
                        logger.warning("proactive response failed: %s", exc)

            elif etype == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
                await self.send({"type": "speech_started"})
                await self.interrupt()

            elif etype == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
                await self.send({"type": "speech_stopped"})

            elif etype == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
                transcript = getattr(event, "transcript", "") or ""
                await self.record_transcript_turn("user", transcript)
                await self.send({
                    "type": "transcript_done",
                    "role": "user",
                    "transcript": transcript,
                    "itemId": getattr(event, "item_id", "") or getattr(event, "itemId", ""),
                })

            elif etype == ServerEventType.RESPONSE_CREATED:
                response = getattr(event, "response", None)
                await self.send({"type": "response_created", "responseId": getattr(response, "id", "")})

            elif etype in (ServerEventType.RESPONSE_AUDIO_DELTA, getattr(ServerEventType, "RESPONSE_OUTPUT_AUDIO_DELTA", None)):
                delta = getattr(event, "delta", None)
                if delta:
                    if isinstance(delta, (bytes, bytearray)):
                        data = base64.b64encode(bytes(delta)).decode("ascii")
                    else:
                        data = str(delta)
                    await self.send({"type": "audio_data", "data": data, "sampleRate": 24000})

            elif etype in (ServerEventType.RESPONSE_AUDIO_DONE, getattr(ServerEventType, "RESPONSE_OUTPUT_AUDIO_DONE", None)):
                await self.send({"type": "audio_done"})

            elif etype in (ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA, getattr(ServerEventType, "RESPONSE_OUTPUT_AUDIO_TRANSCRIPT_DELTA", None)):
                await self.send({"type": "transcript_delta", "role": "assistant", "delta": getattr(event, "delta", "") or ""})

            elif etype in (ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE, getattr(ServerEventType, "RESPONSE_OUTPUT_AUDIO_TRANSCRIPT_DONE", None)):
                transcript = getattr(event, "transcript", "") or ""
                await self.record_transcript_turn("assistant", transcript)
                await self.send({"type": "transcript_done", "role": "assistant", "transcript": transcript})

            elif etype == ServerEventType.RESPONSE_DONE:
                await self.send({"type": "response_done"})

            elif etype == ServerEventType.ERROR:
                err = getattr(event, "error", None)
                await self.send({"type": "error", "error": str(err or event)})


active_sessions: dict[str, VoiceSession] = {}


async def _stop_active_session(client_id: str) -> None:
    session = active_sessions.pop(client_id, None)
    if not session:
        return
    await session.stop()
    task = session.task
    if task and task is not asyncio.current_task() and not task.done():
        with suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=3)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "activeVoiceSessions": len(active_sessions)}


@app.get("/api/session-status/{client_id}")
async def session_status(client_id: str) -> dict[str, Any]:
    session = active_sessions.get(client_id)
    task = session.task if session else None
    return {
        "clientId": client_id,
        "active": bool(session),
        "running": bool(session and session.running),
        "connected": bool(session and session.connection is not None),
        "taskDone": bool(task.done()) if task else True,
    }


@app.get("/api/preflight/voice-live")
async def preflight_voice_live() -> dict[str, Any]:
    agent_mode = _agent_configured()
    endpoint = _resolve_endpoint(agent_mode)
    if not endpoint:
        return {"ok": False, "detail": "AZURE_VOICELIVE_ENDPOINT is not set"}

    def probe() -> dict[str, Any]:
        request = urllib.request.Request(endpoint, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return {"ok": True, "status": response.status, "endpoint": endpoint}
        except urllib.error.HTTPError as exc:
            # 401/403/404 still prove DNS/TLS/service reachability for pre-flight.
            return {"ok": True, "status": exc.code, "endpoint": endpoint}
        except Exception as exc:
            return {"ok": False, "detail": str(exc), "endpoint": endpoint}

    return await asyncio.to_thread(probe)


@app.get("/api/config")
async def config() -> dict[str, Any]:
    latest_invite = _read_latest_invite()
    meeting_link = os.getenv("TEAMS_MEETING_LINK", "").strip() or str(latest_invite.get("meeting_url") or "")
    return {
        "endpoint": os.getenv("AZURE_VOICELIVE_ENDPOINT", ""),
        "model": os.getenv("AZURE_VOICELIVE_MODEL", DEFAULT_MODEL),
        "voice": os.getenv("AZURE_VOICELIVE_VOICE", DEFAULT_VOICE),
        "agentConfigured": _agent_configured(),
        "agentName": os.getenv("AGENT_FOUNDRY_AGENT_NAME", ""),
        "agentProjectName": os.getenv("AGENT_FOUNDRY_PROJECT_NAME", ""),
        "teamsMeetingLink": meeting_link,
        "teamsDisplayName": os.getenv("TEAMS_DISPLAY_NAME", "Lisa HR"),
        "acsConfigured": bool(os.getenv("AZURE_COMMUNICATION_CONNECTION_STRING") or os.getenv("AZURE_COMMUNICATION_ENDPOINT")),
        "avatarCharacter": os.getenv("AVATAR_CHARACTER", "lisa"),
        "avatarStyle": os.getenv("AVATAR_STYLE", "casual-sitting"),
        "teamsAvatarBackgroundImage": os.getenv("TEAMS_AVATAR_BACKGROUND_IMAGE", DEFAULT_TEAMS_AVATAR_BACKGROUND_IMAGE),
        "voiceLiveAvatarBackgroundImageUrl": os.getenv("AVATAR_BACKGROUND_IMAGE_URL", ""),
        "teamsAvatarChromaKeyColor": os.getenv("AVATAR_CHROMA_COLOR", DEFAULT_AVATAR_CHROMA_COLOR),
        "teamsAvatarChromaEnabled": os.getenv("AVATAR_CHROMA_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"},
        "latestInvite": latest_invite,
    }


@app.get("/api/latest-invite")
async def latest_invite() -> dict[str, Any]:
    payload = _read_latest_invite()
    return {"ok": bool(payload.get("meeting_url")), **payload}


@app.post("/api/auto-join-log")
async def auto_join_log(request: Request) -> dict[str, bool]:
    payload = await request.json()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "client_timestamp": str(payload.get("timestamp") or ""),
        "meeting_url": str(payload.get("meetingUrl") or ""),
        "decision": str(payload.get("decision") or ""),
        "outcome": str(payload.get("outcome") or ""),
        "reason": str(payload.get("reason") or ""),
        "candidate_name": str(payload.get("candidateName") or ""),
        "auto_join_enabled": bool(payload.get("autoJoinEnabled")),
    }
    try:
        path = _auto_join_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        logger.info(
            "auto-join decision decision=%s outcome=%s url=%s reason=%s",
            record["decision"],
            record["outcome"],
            record["meeting_url"][:96],
            record["reason"],
        )
        return {"ok": True}
    except Exception as exc:
        logger.warning("failed to persist auto-join decision: %s", exc)
        return {"ok": False}


@app.post("/api/client-log")
async def client_log(request: Request) -> dict[str, bool]:
    payload = await request.json()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "client_timestamp": str(payload.get("timestamp") or ""),
        "message": str(payload.get("message") or ""),
        "event": str(payload.get("event") or ""),
        "meeting_url": str(payload.get("meetingUrl") or ""),
        "voice_connected": bool(payload.get("voiceConnected")),
        "voice_connecting": bool(payload.get("voiceConnecting")),
        "teams_state": str(payload.get("teamsState") or ""),
        "auto_join_enabled": bool(payload.get("autoJoinEnabled")),
        "page_visibility": str(payload.get("pageVisibility") or ""),
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    }
    try:
        path = _client_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        return {"ok": True}
    except Exception as exc:
        logger.warning("failed to persist client event: %s", exc)
        return {"ok": False}


@app.get("/api/acs-token")
async def acs_token() -> dict[str, str]:
    connection_string = os.getenv("AZURE_COMMUNICATION_CONNECTION_STRING", "").strip()
    endpoint = os.getenv("AZURE_COMMUNICATION_ENDPOINT", "").strip()
    if not connection_string and not endpoint:
        raise HTTPException(status_code=500, detail="Set AZURE_COMMUNICATION_CONNECTION_STRING or AZURE_COMMUNICATION_ENDPOINT")
    try:
        if connection_string:
            from azure.communication.identity import CommunicationIdentityClient

            client = CommunicationIdentityClient.from_connection_string(connection_string)
            user, token = client.create_user_and_token(scopes=["voip"])
            expires_on = token.expires_on
        else:
            from azure.communication.identity.aio import CommunicationIdentityClient as AsyncCommunicationIdentityClient

            credential = _build_credential()
            async with AsyncCommunicationIdentityClient(endpoint, credential) as client:
                user, token = await client.create_user_and_token(scopes=["voip"])
            if hasattr(credential, "close"):
                await credential.close()
            expires_on = token.expires_on
        return {
            "userId": user.properties["id"],
            "token": token.token,
            "expiresOn": expires_on.isoformat() if hasattr(expires_on, "isoformat") else str(expires_on),
        }
    except Exception as exc:
        logger.exception("ACS token creation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str) -> None:
    await websocket.accept()
    logger.info("client connected: %s", client_id)
    try:
        while True:
            raw = await websocket.receive_text()
            message = json.loads(raw)
            msg_type = message.get("type")
            session = active_sessions.get(client_id)
            if msg_type == "start_session":
                if session:
                    await _stop_active_session(client_id)
                session = VoiceSession(client_id, websocket, message.get("config") or {})
                active_sessions[client_id] = session
                session.task = asyncio.create_task(session.start())
            elif msg_type == "audio_chunk" and session:
                await session.send_audio(str(message.get("data") or ""))
            elif msg_type == "send_text" and session:
                await session.send_text(str(message.get("text") or ""))
            elif msg_type == "avatar_sdp_offer" and session:
                await session.send_avatar_sdp_offer(str(message.get("clientSdp") or ""))
            elif msg_type == "interrupt" and session:
                await session.interrupt()
            elif msg_type == "stop_session" and session:
                await _stop_active_session(client_id)
            elif msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        logger.info("client disconnected: %s", client_id)
    except RuntimeError as exc:
        if "WebSocket is not connected" in str(exc):
            logger.info("client websocket closed: %s", client_id)
        else:
            raise
    except Exception:
        logger.exception("websocket error for client %s", client_id)
    finally:
        await _stop_active_session(client_id)


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "3000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
