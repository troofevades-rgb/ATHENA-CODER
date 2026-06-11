"""Capability advertisement for the ``initialize`` handshake.

The IDE asks what we support up front; we hand back the
:data:`CAPABILITIES` table plus our server identity and the protocol
version we speak. The values are conservative — we advertise what's
actually wired through to the rest of athena, not aspirational
features.

Don't add a key here without a working implementation. The IDE will
take us at our word and call us for any capability we claim — an
unhandled call produces a noisy error in the user's editor.
"""

from __future__ import annotations

PROTOCOL_VERSION = "1.0"


SERVER_INFO = {
    "name": "athena",
    "version": "0.2.0",
}


CAPABILITIES = {
    # We emit content_block_delta notifications (currently a single
    # delta after the buffered turn completes, wrapped in start/stop
    # blocks so the IDE renders it in its response panel).
    "streaming": True,
    # Surface tool calls via tool_call_start + a matching tool_result
    # for each (the result closes the IDE's activity block — without it
    # the IDE shows a dangling spinner).
    "tools": True,
    # Send permission_request to the client for dangerous tools;
    # await user decision.
    "approvals": True,
    # NOTE: file attachments are NOT yet wired — session/send_message
    # only extracts text (see _coerce_user_text). Advertising this had
    # the IDE send attachments we silently dropped. Flip to True only
    # once the attachment path lands.
    "file_attachments": False,
    # /steer, /queue, /goal via session/slash_command.
    "slash_commands": True,
    # models/list returns every provider:model name the resolver
    # would accept.
    "models_listing": True,
    # session/new + session/end IDE-driven lifecycle.
    "session_lifecycle": True,
}
