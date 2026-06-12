"""Command-line front door for ``teams_avatar_voice_live``.

Run::

    python -m launcher schedule \\
        --to alice@example.com \\
        --start +10 \\
        --duration-mins 30 \\
        --subject "Demo interview" \\
        --mode graph_bot

The ``schedule`` subcommand performs the full end-to-end flow:

1. Create a Teams online meeting (Graph ``onlineMeetings``, with
   delegated-auth fallback).
2. Send a branded invite email to ``--to`` via Graph ``sendMail``.
3. Dispatch the meeting URL to the configured avatar transport
   (``graph_bot`` or ``browser_webrtc``) via
   :mod:`launcher.bot_dispatcher`.

The ``dispatch`` subcommand handles step 3 in isolation — useful when
the meeting URL already exists (e.g. created from Outlook by hand).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Sequence

from dotenv import load_dotenv

from . import bot_dispatcher, graph_client

logger = logging.getLogger("launcher")

# Load the project-root .env so the documented `cp .env.example .env` workflow
# works out of the box. `override=False` keeps real environment variables
# (e.g. those injected by CI or a service host) authoritative.
load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env", override=False)
load_dotenv(override=False)  # also honour a .env in the current working directory


def _configure_logging(verbose: bool) -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    if verbose:
        level = "DEBUG"
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _parse_start(value: str) -> int:
    """Convert a ``--start`` arg into "minutes from now".

    Accepts ``+N`` (minutes), ``N`` (minutes), or an ISO-8601 timestamp.
    """
    raw = value.strip()
    if raw.startswith("+"):
        raw = raw[1:]
    if raw.lstrip("-").isdigit():
        return int(raw)

    # ISO-8601 — accept both ``Z`` and ``+00:00`` suffixes.
    iso = raw.replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise SystemExit(
            f"--start must be '+N' minutes or an ISO-8601 timestamp (got {value!r})"
        ) from exc

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = (when - datetime.now(timezone.utc)).total_seconds() / 60.0
    return max(0, int(math.ceil(delta)))


def cmd_schedule(args: argparse.Namespace) -> int:
    start_minutes = _parse_start(args.start)
    logger.info(
        "Scheduling meeting: subject=%r start=+%dm duration=%dm to=%s mode=%s",
        args.subject, start_minutes, args.duration_mins, args.to, args.mode or "(env)",
    )

    meeting = graph_client.create_teams_meeting(
        subject=args.subject,
        start_minutes_from_now=start_minutes,
        duration_minutes=args.duration_mins,
    )
    join_url = meeting["joinUrl"]

    email_result: dict | None = None
    if not args.skip_email:
        email_result = graph_client.send_interview_invite(
            candidate_email=args.to,
            candidate_name=args.name,
            position=args.position,
            join_url=join_url,
            lang=args.lang,
        )

    dispatch_result = bot_dispatcher.dispatch(
        join_url,
        mode=args.mode,
        display_name=args.bot_display_name,
        session_id=meeting.get("meetingId", ""),
        email_sent_to=args.to,
        position=args.position,
        lang=args.lang,
    )

    payload = {
        "ok": True,
        "join_url": join_url,
        "meeting_id": meeting.get("meetingId"),
        "email": email_result,
        "dispatch": dispatch_result,
    }
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    result = bot_dispatcher.dispatch(
        args.join_url,
        mode=args.mode,
        display_name=args.bot_display_name,
        session_id=args.session_id,
        email_sent_to=args.email_sent_to,
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result.get("details", {}).get("ok", True) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="launcher",
        description=(
            "Create a Teams meeting, email the invite, and dispatch the "
            "avatar bot to join."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sch = sub.add_parser("schedule", help="End-to-end: create meeting, email, dispatch bot")
    sch.add_argument("--to", required=True, help="Invitee email address")
    sch.add_argument("--start", default="+5",
                     help="Start time: '+N' minutes from now, or ISO-8601 (default: +5)")
    sch.add_argument("--duration-mins", type=int, default=30, help="Meeting length (default: 30)")
    sch.add_argument("--subject", default="Interview with the AI avatar",
                     help="Meeting subject + email subject")
    sch.add_argument("--name", default="Guest", help="Invitee display name (for email)")
    sch.add_argument("--position", default="Open Position",
                     help="Role title shown in the email body")
    sch.add_argument("--lang", default="en", choices=["en", "de"],
                     help="Email template language")
    sch.add_argument("--mode", choices=sorted(bot_dispatcher.SUPPORTED_MODES),
                     help="Avatar transport (default: env TEAMS_JOIN_MODE)")
    sch.add_argument("--bot-display-name", default=None,
                     help="Roster name shown in Teams (default: env BOT_DISPLAY_NAME)")
    sch.add_argument("--skip-email", action="store_true",
                     help="Create meeting + dispatch bot, but don't send the invite email")
    sch.set_defaults(func=cmd_schedule)

    dsp = sub.add_parser("dispatch", help="Dispatch avatar bot to an existing meeting URL")
    dsp.add_argument("--join-url", required=True, help="Teams joinWebUrl")
    dsp.add_argument("--mode", choices=sorted(bot_dispatcher.SUPPORTED_MODES),
                     help="Avatar transport (default: env TEAMS_JOIN_MODE)")
    dsp.add_argument("--bot-display-name", default=None,
                     help="Roster name shown in Teams (default: env BOT_DISPLAY_NAME)")
    dsp.add_argument("--session-id", default="", help="Opaque session/correlation ID")
    dsp.add_argument("--email-sent-to", default="",
                     help="Email address recorded for the browser fallback page")
    dsp.set_defaults(func=cmd_dispatch)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return args.func(args)
    except Exception:
        logger.exception("Command failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
