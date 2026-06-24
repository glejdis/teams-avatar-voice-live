"""Microsoft Graph helpers — create Teams meetings & send invite emails.

**Auth strategy (dual-mode):**

1. **Client-credentials** (app-only) token — used for ``Mail.Send`` and as the
    first attempt for meeting creation.  Requires an **Application Access
    Policy** in Teams (can take up to 24 h to propagate).

2. **Delegated** token via MSAL device-code flow — automatic fallback for
    meeting creation when the policy hasn't propagated yet.  The refresh
    token is persisted to ``~/.teams_avatar_graph_token_cache.bin`` so the
    interactive device-code step is only needed once.

Env vars
--------
  GRAPH_CLIENT_ID        Entra ID app (client) ID.
  GRAPH_CLIENT_SECRET    Client secret value.
  GRAPH_TENANT_ID        Azure AD tenant ID.
  GRAPH_ONLINE_MEETING_ORGANIZER_USER_ID
                                 Entra object ID GUID of the licensed user who owns
                                 Teams online meetings.
  GRAPH_ORGANIZER_UPN    Optional UPN/email of the same user. Used to resolve
                                 the object ID when the GUID config is not set, and as
                                 the sender for Graph sendMail.
"""

from __future__ import annotations

import logging
import os
import pathlib
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import msal
import requests

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_ORGANIZER_USER_ID_ENV = "GRAPH_ONLINE_MEETING_ORGANIZER_USER_ID"
_ORGANIZER_UPN_ENV = "GRAPH_ORGANIZER_UPN"
_BRAND_NAME_ENV = "BRAND_NAME"
_DEFAULT_BRAND = "Contoso"


def _brand_name() -> str:
    """Brand label woven into the invite email subject + body.

    Override per deployment via the ``BRAND_NAME`` env var (e.g. set it on
    the prod environment in GitHub Actions or in your local ``.env``).
    Falls back to ``Contoso`` so a fresh clone produces sensible-looking
    output without any configuration.
    """
    return os.getenv(_BRAND_NAME_ENV, "").strip() or _DEFAULT_BRAND

# Module-level cache for the client-credentials access token.
_cached_token: str | None = None
_token_expires: float = 0.0

# Persistent MSAL token cache for delegated flow.
#
# Least privilege (see governance/agent-registry.yaml ->
# lisa-voice-avatar.least_privilege.graph_scopes): the avatar acts On-Behalf-Of
# the signed-in organiser with these *delegated* scopes only — never app-only
# `.default`. The governance validator rejects `.default` / wildcard scopes in
# the registry, so this list is the enforced least-privilege contract.
# (Calendars.ReadWrite / Calendars.Read are requested on demand below.)
_DELEGATED_SCOPES = ["OnlineMeetings.ReadWrite", "Mail.Send", "User.Read"]
_TOKEN_CACHE_PATH = pathlib.Path.home() / ".teams_avatar_graph_token_cache.bin"
_msal_app: msal.PublicClientApplication | None = None


# ── Config ───────────────────────────────────────────────────────────────────

def graph_configured() -> bool:
    """Return True if the minimum env vars for Graph API are set."""
    return bool(
        os.environ.get("GRAPH_CLIENT_ID")
        and os.environ.get("GRAPH_CLIENT_SECRET")
        and os.environ.get("GRAPH_TENANT_ID")
        and (
            os.environ.get(_ORGANIZER_USER_ID_ENV)
            or os.environ.get(_ORGANIZER_UPN_ENV)
        )
    )


def _is_guid(value: str) -> bool:
    """Return True when ``value`` is a valid GUID string."""
    try:
        uuid.UUID(value.strip())
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def _sanitize_user_identifier(value: str) -> str:
    """Return a log-safe version of a UPN/email/object identifier."""
    trimmed = (value or "").strip()
    if not trimmed:
        return "<empty>"
    if _is_guid(trimmed):
        return trimmed
    if "@" in trimmed:
        local, _, domain = trimmed.partition("@")
        prefix = local[:2] if len(local) > 2 else local[:1]
        return f"{prefix}***@{domain}"
    if len(trimmed) <= 8:
        return "***"
    return f"{trimmed[:4]}...{trimmed[-2:]}"


def _get_required_organizer_identifier() -> str:
    """Return the configured organizer object ID or lookup identifier."""
    configured_user_id = os.environ.get(_ORGANIZER_USER_ID_ENV, "").strip()
    if configured_user_id:
        return configured_user_id

    organizer_upn = os.environ.get(_ORGANIZER_UPN_ENV, "").strip()
    if organizer_upn:
        return organizer_upn

    raise RuntimeError(
        f"Graph organizer is not configured. Set {_ORGANIZER_USER_ID_ENV} "
        f"to the Entra object ID GUID, or set {_ORGANIZER_UPN_ENV} so it can be resolved."
    )


def _resolve_graph_user_id(identifier: str) -> str:
    """Resolve a Graph user lookup value to an Entra object ID GUID."""
    safe_identifier = _sanitize_user_identifier(identifier)
    user_url = f"{_GRAPH_BASE}/users/{quote(identifier.strip(), safe='')}"
    resp = requests.get(
        user_url,
        params={"$select": "id,userPrincipalName,mail"},
        headers=_headers(),
        timeout=15,
    )

    if not resp.ok:
        detail = resp.text[:300]
        logger.error(
            "Graph organizer lookup failed for organizer=%s status=%s detail=%s",
            safe_identifier,
            resp.status_code,
            detail,
        )
        raise RuntimeError(
            "Graph organizer lookup failed before creating the Teams meeting. "
            f"Set {_ORGANIZER_USER_ID_ENV} to the organizer user's Entra object ID GUID, "
            f"or set {_ORGANIZER_UPN_ENV} to a resolvable mailbox UPN. "
            f"Graph returned {resp.status_code}: {detail}"
        )

    data = resp.json()
    object_id = (data.get("id") or "").strip()
    if not _is_guid(object_id):
        raise RuntimeError(
            "Graph organizer lookup did not return a valid object ID GUID. "
            f"Set {_ORGANIZER_USER_ID_ENV} explicitly."
        )

    resolved_upn = _sanitize_user_identifier(data.get("userPrincipalName") or identifier)
    logger.info(
        "Resolved Graph online meeting organizer: organizerUpn=%s objectId=%s",
        resolved_upn,
        object_id,
    )
    return object_id


def _get_online_meeting_organizer_user_id() -> str:
    """Return the GUID required by POST /users/{id}/onlineMeetings."""
    identifier = _get_required_organizer_identifier()
    if _is_guid(identifier):
        logger.info(
            "Using configured Graph online meeting organizer objectId=%s",
            identifier.strip(),
        )
        return identifier.strip()
    return _resolve_graph_user_id(identifier)


def _get_mail_sender_user_identifier() -> str:
    """Return a Graph /users identifier suitable for sendMail."""
    configured_user_id = os.environ.get(_ORGANIZER_USER_ID_ENV, "").strip()
    if configured_user_id:
        if _is_guid(configured_user_id):
            return configured_user_id
        return _resolve_graph_user_id(configured_user_id)
    return _get_required_organizer_identifier()


def _looks_like_local_url(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        lowered.startswith("http://localhost")
        or lowered.startswith("https://localhost")
        or lowered.startswith("http://127.0.0.1")
        or lowered.startswith("https://127.0.0.1")
    )


def _require_teams_join_url(join_url: str) -> str:
    """Validate that the candidate invite will use Graph's Teams join URL."""
    clean_url = (join_url or "").strip()
    if not clean_url:
        raise RuntimeError("Graph meeting creation succeeded but did not return a Teams join URL.")
    if _looks_like_local_url(clean_url):
        raise RuntimeError(
            "Refusing to send interview invite with a localhost link. "
            "The email must use the Teams joinWebUrl returned by Microsoft Graph."
        )
    return clean_url


# ── Client-credentials token ────────────────────────────────────────────────

def _get_token() -> str:
    """Obtain (or return cached) client-credentials access token."""
    import time

    global _cached_token, _token_expires  # noqa: PLW0603

    now = time.time()
    if _cached_token and now < _token_expires - 60:
        return _cached_token

    tenant = os.environ["GRAPH_TENANT_ID"]
    resp = requests.post(
        _TOKEN_URL.format(tenant=tenant),
        data={
            "client_id": os.environ["GRAPH_CLIENT_ID"],
            "client_secret": os.environ["GRAPH_CLIENT_SECRET"],
            # `.default` is the only scope value the client-credentials grant
            # accepts; it resolves to whatever *application* permissions are
            # consented for the app registration. Least privilege is therefore
            # enforced at the app-registration level — grant ONLY the application
            # permissions the avatar needs (e.g. Mail.Send) so `.default` cannot
            # widen the blast radius. Prefer the delegated (OBO) path above.
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    _cached_token = payload["access_token"]
    _token_expires = now + int(payload.get("expires_in", 3600))
    return _cached_token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
    }


# ── Delegated token (MSAL device-code flow, cached) ─────────────────────────

def _get_msal_app() -> msal.PublicClientApplication:
    """Return a singleton PublicClientApplication with a persistent cache."""
    global _msal_app  # noqa: PLW0603

    if _msal_app is not None:
        return _msal_app

    cache = msal.SerializableTokenCache()
    if _TOKEN_CACHE_PATH.exists():
        cache.deserialize(_TOKEN_CACHE_PATH.read_text())

    _msal_app = msal.PublicClientApplication(
        client_id=os.environ["GRAPH_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{os.environ['GRAPH_TENANT_ID']}",
        token_cache=cache,
    )
    return _msal_app


def _save_msal_cache() -> None:
    app = _get_msal_app()
    if app.token_cache.has_state_changed:
        _TOKEN_CACHE_PATH.write_text(app.token_cache.serialize())


def _get_delegated_token() -> str:
    """Get a delegated token — silently from cache or via device-code flow."""
    app = _get_msal_app()

    # Try silent acquisition first (uses cached refresh token).
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(_DELEGATED_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_msal_cache()
            return result["access_token"]

    # Fall back to device-code flow (one-time interactive step).
    flow = app.initiate_device_flow(scopes=_DELEGATED_SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device-code flow failed: {flow}")

    logger.warning(
        "Graph delegated auth required. "
        "Open %s and enter code: %s",
        flow["verification_uri"],
        flow["user_code"],
    )
    # Also print to stdout so it's visible in the console.
    print(
        f"\n{'='*60}\n"
        f"  GRAPH LOGIN REQUIRED (one-time)\n"
        f"  Open: {flow['verification_uri']}\n"
        f"  Code: {flow['user_code']}\n"
        f"{'='*60}\n",
        flush=True,
    )

    result = app.acquire_token_by_device_flow(flow)
    _save_msal_cache()

    if "access_token" not in result:
        raise RuntimeError(f"Device-code auth failed: {result.get('error_description', result)}")

    logger.info("Delegated token acquired and cached.")
    return result["access_token"]


def _delegated_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_delegated_token()}",
        "Content-Type": "application/json",
    }


# ── Create Teams meeting ────────────────────────────────────────────────────


def create_teams_meeting(
    *,
    subject: str = "HR Interview with Lisa",
    start_minutes_from_now: int = 5,
    duration_minutes: int = 30,
) -> dict[str, Any]:
    """Create an online meeting on behalf of the organiser.

    Tries **client-credentials** first (requires Application Access Policy).
    If that returns 404 (policy not yet propagated), falls back to
    **delegated** auth via MSAL device-code flow.

    Returns a dict with ``joinUrl``, ``meetingId``, and the full Graph
    response payload.
    """
    organizer_user_id = _get_online_meeting_organizer_user_id()
    now = datetime.now(timezone.utc)
    start = now + timedelta(minutes=start_minutes_from_now)
    end = start + timedelta(minutes=duration_minutes)

    body: dict[str, Any] = {
        "subject": subject,
        "startDateTime": start.isoformat(),
        "endDateTime": end.isoformat(),
        "lobbyBypassSettings": {
            "scope": "everyone",
            "isDialInBypassEnabled": True,
        },
        "allowedPresenters": "everyone",
        "isEntryExitAnnounced": False,
    }

    # Attempt 1 — client credentials (app-only). The onlineMeetings endpoint
    # requires the organizer's Entra object ID GUID in this URL path.
    url = f"{_GRAPH_BASE}/users/{organizer_user_id}/onlineMeetings"
    resp = requests.post(url, json=body, headers=_headers(), timeout=15)

    if resp.status_code in (404, 403):
        # Application Access Policy likely not propagated yet → delegated.
        logger.warning(
            "Client-credentials meeting creation returned %s "
            "(policy not propagated?). Falling back to delegated auth.",
            resp.status_code,
        )
        # Delegated flow: POST /me/onlineMeetings (user context).
        url_me = f"{_GRAPH_BASE}/me/onlineMeetings"
        resp = requests.post(
            url_me, json=body, headers=_delegated_headers(), timeout=15,
        )

    if not resp.ok:
        error_detail = resp.text[:500]
        logger.error("Meeting creation failed %s: %s", resp.status_code, error_detail)
        raise RuntimeError(f"Graph meeting creation failed {resp.status_code}: {error_detail}")
    data = resp.json()

    join_url = _require_teams_join_url(data.get("joinWebUrl") or data.get("joinUrl") or "")
    logger.info(
        "Created Teams meeting: subject=%r joinUrl=%s",
        subject,
        join_url[:80],
    )
    return {
        "joinUrl": join_url,
        "meetingId": data.get("id"),
        "raw": data,
    }


# ── Invite Lisa calling-bot to a Teams meeting ──────────────────────────────


def bot_join_configured() -> bool:
    """True if the Lisa Graph calling-bot endpoint is configured."""
    return bool(os.environ.get("BOT_JOIN_ENDPOINT"))


# In-process dedupe window for invite_bot_to_meeting. Two near-simultaneous
# calls for the same join_url (e.g. duplicate /schedule-interview retries
# from a flaky frontend) must NOT each POST to the bot, or Graph creates two
# call legs and the meeting gets two Lisa participants.
_BOT_JOIN_DEDUPE_WINDOW_SECONDS = 60.0
_bot_join_recent: dict[str, float] = {}
_bot_join_lock = threading.Lock()


def _is_recent_bot_join(join_url: str) -> bool:
    """Return True (and record) when ``join_url`` was already invited within the
    dedupe window. Side-effect: records ``join_url`` -> now() so subsequent
    calls inside the window also short-circuit.
    """
    now = time.monotonic()
    with _bot_join_lock:
        # Cheap GC of stale entries.
        if len(_bot_join_recent) > 256:
            cutoff = now - _BOT_JOIN_DEDUPE_WINDOW_SECONDS
            for k, ts in list(_bot_join_recent.items()):
                if ts < cutoff:
                    _bot_join_recent.pop(k, None)
        prev = _bot_join_recent.get(join_url)
        if prev is not None and (now - prev) < _BOT_JOIN_DEDUPE_WINDOW_SECONDS:
            return True
        _bot_join_recent[join_url] = now
        return False


def invite_bot_to_meeting(
    join_url: str,
    *,
    display_name: str | None = None,
    candidate_id: str = "",
    candidate_name: str = "",
    position: str = "",
    session_id: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Ask the Lisa Graph calling-bot to dial into ``join_url``.

    The bot exposes ``POST /calls/joinCall`` (see
    ``bot/src/EchoBot/Controllers/CallsController.cs`` in the submodule)
    which
    accepts ``{"JoinUrl": str, "DisplayName": str}`` plus optional
    candidate metadata for transcript correlation.

    Behaviour is governed by env vars:

    ``BOT_JOIN_ENDPOINT``
        Required. Full URL, e.g.
        ``https://bot.<your-domain>/calls/joinCall``.
    ``BOT_DISPLAY_NAME``
        Optional default display name shown in the Teams meeting roster
        (e.g. ``"Lisa"`` for the shipped persona, or ``"Avatar Bot"``).
    ``BOT_AUTH_SHARED_SECRET``
        Optional. Sent as ``X-Bot-Auth`` header for the bot to validate.
    ``BOT_JOIN_REQUIRED``
        ``true``/``false`` (default ``false``). When ``false``, errors are
        logged but **not raised** so the applicant still gets the meeting
        link even if the avatar VM is unreachable.

    Returns a dict with ``status``, ``ok``, optional ``call_id`` and
    ``error``. Never raises when ``BOT_JOIN_REQUIRED`` is unset/false.
    """
    endpoint = os.environ.get("BOT_JOIN_ENDPOINT", "").strip()
    required = os.environ.get("BOT_JOIN_REQUIRED", "false").lower() == "true"

    if not endpoint:
        msg = "BOT_JOIN_ENDPOINT not set — skipping avatar bot dial-in."
        if required:
            raise RuntimeError(msg)
        logger.info(msg)
        return {"ok": False, "skipped": True, "error": msg}

    if not join_url:
        raise ValueError("join_url is required to invite the avatar bot.")

    # Belt-and-braces dedupe: even though the bot itself now atomically
    # rejects duplicate joinCall POSTs for the same threadId, a duplicate
    # POST still costs Graph round-trips and produces an exception path.
    # Short-circuit here.
    if _is_recent_bot_join(join_url):
        msg = "Duplicate avatar bot invite within dedupe window — skipping."
        logger.info(msg)
        return {"ok": True, "deduped": True, "skipped": True, "info": msg}

    name = display_name or os.environ.get("BOT_DISPLAY_NAME") or "Avatar Bot"
    body = {
        "JoinUrl": join_url,
        "joinURL": join_url,
        "DisplayName": name,
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "position": position,
        "session_id": session_id,
    }
    headers = {"Content-Type": "application/json"}
    secret = os.environ.get("BOT_AUTH_SHARED_SECRET", "").strip()
    if secret:
        headers["X-Bot-Auth"] = secret

    try:
        resp = requests.post(endpoint, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        msg = f"Avatar bot endpoint unreachable: {exc}"
        logger.warning(msg)
        if required:
            raise RuntimeError(msg) from exc
        return {"ok": False, "error": msg}

    ok = resp.ok
    payload: dict[str, Any]
    try:
        payload = resp.json() if resp.content else {}
    except ValueError:
        payload = {"raw": resp.text[:500]}

    if not ok:
        text = resp.text[:300]
        if "Call has already been added" in text:
            msg = "Avatar bot was already invited to this meeting — treating as idempotent success."
            logger.info(msg)
            return {"ok": True, "deduped": True, "status": resp.status_code, "info": msg}

        msg = f"Avatar bot join failed {resp.status_code}: {text}"
        logger.error(msg)
        if required:
            raise RuntimeError(msg)
        return {"ok": False, "status": resp.status_code, "error": msg}

    logger.info("Avatar bot accepted join request (status=%s)", resp.status_code)
    return {
        "ok": True,
        "status": resp.status_code,
        "call_id": payload.get("callLegId") or payload.get("id"),
        "raw": payload,
    }


# ── Send invite email ───────────────────────────────────────────────────────

_EMAIL_TEMPLATE_EN = """\
<h2>You've been invited to a screening interview</h2>
<p>Hello {candidate_name},</p>
<p>Thank you for applying for the <strong>{position}</strong> position at {brand}.</p>
<p>We'd like to conduct a brief screening interview (approx. 5 minutes).
Please join the Teams meeting at the scheduled time by clicking the link below:</p>
<p style="margin:20px 0;">
  <a href="{join_url}"
     style="background:#cc071e;color:#fff;padding:12px 24px;
            border-radius:8px;text-decoration:none;font-weight:600;">
    Join Interview Meeting
  </a>
</p>
<p>You'll be speaking with <strong>Lisa</strong>, our AI HR assistant, who will
ask a few standard questions about your availability, experience, and
preferences. This is <em>not</em> a decision — a human recruiter will
review your answers afterwards.</p>
<p>Best regards,<br/>{brand} Team</p>
"""

_EMAIL_TEMPLATE_DE = """\
<h2>Einladung zum Screening-Interview</h2>
<p>Hallo {candidate_name},</p>
<p>Vielen Dank für Ihre Bewerbung als <strong>{position}</strong> bei {brand}.</p>
<p>Wir möchten ein kurzes Screening-Interview (ca. 5 Minuten) mit Ihnen führen.
Bitte treten Sie dem Teams-Meeting zum geplanten Zeitpunkt bei:</p>
<p style="margin:20px 0;">
  <a href="{join_url}"
     style="background:#cc071e;color:#fff;padding:12px 24px;
            border-radius:8px;text-decoration:none;font-weight:600;">
    Am Interview teilnehmen
  </a>
</p>
<p>Sie sprechen mit <strong>Lisa</strong>, unserer KI-HR-Assistentin, die Ihnen
einige Standardfragen zu Verfügbarkeit, Erfahrung und Präferenzen stellt.
Dies ist <em>keine</em> Entscheidung — ein menschlicher Recruiter prüft
Ihre Antworten anschließend.</p>
<p>Mit freundlichen Grüßen,<br/>{brand} Team</p>
"""


def send_interview_invite(
    *,
    candidate_email: str,
    candidate_name: str,
    position: str,
    join_url: str,
    lang: str = "en",
) -> dict[str, Any]:
    """Send a branded interview-invite email via Graph Mail.Send."""
    organizer = _get_mail_sender_user_identifier()
    join_url = _require_teams_join_url(join_url)
    brand = _brand_name()
    template = _EMAIL_TEMPLATE_DE if lang.startswith("de") else _EMAIL_TEMPLATE_EN
    html_body = template.format(
        candidate_name=candidate_name,
        position=position,
        join_url=join_url,
        brand=brand,
    )

    subject = (
        f"{brand} Interview-Einladung – {position}"
        if lang.startswith("de")
        else f"{brand} Interview Invitation – {position}"
    )

    body = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_body,
            },
            "toRecipients": [
                {"emailAddress": {"address": candidate_email, "name": candidate_name}}
            ],
        },
        "saveToSentItems": True,
    }

    url = f"{_GRAPH_BASE}/users/{organizer}/sendMail"
    resp = requests.post(url, json=body, headers=_headers(), timeout=15)
    resp.raise_for_status()
    logger.info(
        "Sent interview invite: to=%s subject=%r",
        candidate_email,
        subject,
    )
    return {"sent": True, "to": candidate_email, "subject": subject}


# ── Calendar event with attendees (sends invites + blocks calendars) ────────

def create_calendar_event(
    *,
    subject: str,
    start_iso: str,
    end_iso: str,
    timezone_name: str = "W. Europe Standard Time",
    attendees: list[dict[str, str]] | None = None,
    body_html: str = "",
    is_online_meeting: bool = True,
) -> dict[str, Any]:
    """Create a calendar event on the organiser's calendar and invite attendees.

    Graph sends a standard meeting invite to every attendee and — if
    ``is_online_meeting`` is true — embeds a Teams join link in the event.
    Internal attendees will see the event appear on their calendar; external
    attendees receive a regular .ics invite they can accept.

    ``attendees`` is a list of ``{"address": "x@y.com", "name": "Full Name"}``.

    Tries **client-credentials** first, falls back to **delegated** auth if
    the app is not authorised on the organiser's mailbox.
    """
    organizer = _get_mail_sender_user_identifier()
    attendees = attendees or []

    body: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html or subject},
        "start": {"dateTime": start_iso, "timeZone": timezone_name},
        "end": {"dateTime": end_iso, "timeZone": timezone_name},
        "attendees": [
            {
                "emailAddress": {
                    "address": a["address"],
                    "name": a.get("name", a["address"]),
                },
                "type": "required",
            }
            for a in attendees
        ],
        "isOnlineMeeting": is_online_meeting,
        "onlineMeetingProvider": "teamsForBusiness",
        "allowNewTimeProposals": True,
        "responseRequested": True,
    }

    # Attempt 1 — client credentials (app-only, /users/{upn}/events).
    url = f"{_GRAPH_BASE}/users/{organizer}/events"
    resp = requests.post(url, json=body, headers=_headers(), timeout=20)

    if resp.status_code in (401, 403, 404):
        logger.warning(
            "Client-credentials calendar event creation returned %s; "
            "falling back to delegated auth.",
            resp.status_code,
        )
        # Delegated: need Calendars.ReadWrite scope — request it on the fly.
        global _DELEGATED_SCOPES  # noqa: PLW0603
        if "Calendars.ReadWrite" not in _DELEGATED_SCOPES:
            _DELEGATED_SCOPES = list({*_DELEGATED_SCOPES, "Calendars.ReadWrite"})
        url_me = f"{_GRAPH_BASE}/me/events"
        resp = requests.post(
            url_me, json=body, headers=_delegated_headers(), timeout=20,
        )

    if not resp.ok:
        error_detail = resp.text[:500]
        logger.error("Calendar event creation failed %s: %s", resp.status_code, error_detail)
        raise RuntimeError(
            f"Graph calendar event creation failed {resp.status_code}: {error_detail}"
        )

    data = resp.json()
    online = data.get("onlineMeeting") or {}
    join_url = online.get("joinUrl", "")
    logger.info(
        "Created calendar event: subject=%r start=%s attendees=%d joinUrl=%s",
        subject,
        start_iso,
        len(attendees),
        join_url[:80],
    )
    return {
        "eventId": data.get("id"),
        "joinUrl": join_url,
        "webLink": data.get("webLink", ""),
        "raw": data,
    }


# ── Fetch a user's calendar view (busy blocks) ──────────────────────────────

def fetch_calendar_view(
    *,
    user_upn: str,
    start_iso_utc: str,
    end_iso_utc: str,
) -> list[dict[str, Any]]:
    """Return the user's calendar events between two UTC ISO timestamps.

    Tries app-only auth first (``Calendars.Read`` application permission).
    On 401/403 falls back to **delegated** auth — which requires the
    signed-in user to be viewing their own calendar. For the demo, the
    organiser UPN and the signed-in delegated user are the same account
    (``admin@...onmicrosoft.com``), so ``/me/calendarView`` works.
    """
    def _build_url(host_path: str) -> str:
        return (
            f"{_GRAPH_BASE}{host_path}"
            f"?startDateTime={start_iso_utc}&endDateTime={end_iso_utc}"
            "&$select=subject,start,end,isAllDay,showAs"
            "&$orderby=start/dateTime"
            "&$top=100"
        )

    # Attempt 1 — app-only, /users/{upn}/calendarView.
    url = _build_url(f"/users/{user_upn}/calendarView")
    headers = {
        **_headers(),
        "Prefer": 'outlook.timezone="UTC"',
    }
    resp = requests.get(url, headers=headers, timeout=15)

    if resp.status_code in (401, 403):
        logger.warning(
            "App-only calendarView denied for %s (%s); falling back to delegated.",
            user_upn, resp.status_code,
        )
        # Ensure we ask for Calendars.Read on first delegated consent.
        global _DELEGATED_SCOPES  # noqa: PLW0603
        if "Calendars.Read" not in _DELEGATED_SCOPES:
            _DELEGATED_SCOPES = list({*_DELEGATED_SCOPES, "Calendars.Read"})
        # Delegated endpoint works against the signed-in user's own mailbox.
        url_me = _build_url("/me/calendarView")
        headers_me = {
            **_delegated_headers(),
            "Prefer": 'outlook.timezone="UTC"',
        }
        resp = requests.get(url_me, headers=headers_me, timeout=15)

    if not resp.ok:
        detail = resp.text[:500]
        logger.error(
            "calendarView failed for %s: %s %s", user_upn, resp.status_code, detail
        )
        raise RuntimeError(
            f"Graph calendarView failed {resp.status_code}: {detail}"
        )
    events = resp.json().get("value", [])
    # Only keep items that actually block time.
    busy: list[dict[str, Any]] = []
    for ev in events:
        show_as = (ev.get("showAs") or "").lower()
        if show_as in ("free", "workingelsewhere"):
            continue
        busy.append({
            "subject": ev.get("subject", "(busy)"),
            "start": (ev.get("start") or {}).get("dateTime", ""),
            "end": (ev.get("end") or {}).get("dateTime", ""),
            "isAllDay": bool(ev.get("isAllDay")),
            "showAs": show_as or "busy",
        })
    logger.info(
        "Fetched calendarView for %s — %d blocking events between %s and %s",
        user_upn, len(busy), start_iso_utc, end_iso_utc,
    )
    return busy

