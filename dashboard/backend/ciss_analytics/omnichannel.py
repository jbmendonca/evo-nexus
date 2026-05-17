"""Omnichannel webhook normalization for Evolution API / WhatsApp."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Any, Protocol


class AudioTranscriber(Protocol):
    """Transcribes an inbound audio media payload into text."""

    def transcribe(self, media: dict[str, Any]) -> str:
        """Return transcribed text for a media payload."""


@dataclass(slots=True)
class IncomingMessage:
    channel: str = "whatsapp"
    provider: str = "evolution_api"
    chat_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    message_id: str = ""
    message_type: str = "text"
    text: str = ""
    instance: str = ""
    media: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "provider": self.provider,
            "chat_id": self.chat_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "message_id": self.message_id,
            "message_type": self.message_type,
            "text": self.text,
            "instance": self.instance,
            "media": self.media,
        }


@dataclass(slots=True)
class NormalizationResult:
    status: str
    message: str
    incoming: IncomingMessage | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": self.status,
            "message": self.message,
            "incoming": self.incoming.to_dict() if self.incoming else None,
        }
        if self.error:
            data["error"] = self.error
        return data


def normalize_text(value: Any) -> str:
    """Collapse user-visible whitespace and strip empty text."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_chat_id(value: Any) -> str:
    """Normalize the chat id while preserving Evolution/WhatsApp suffixes."""
    return normalize_text(value).replace(" ", "")


def normalize_evolution_webhook(
    payload: dict[str, Any] | None,
    *,
    transcriber: AudioTranscriber | None = None,
    transcription_configured: bool | None = None,
) -> NormalizationResult:
    """Convert common Evolution API webhook shapes into one internal message."""
    if not isinstance(payload, dict):
        return NormalizationResult(
            status="invalid_payload",
            message="Webhook payload must be a JSON object.",
            error="payload_not_object",
        )

    data = _payload_data(payload)
    key = _message_key(data)
    if bool(key.get("fromMe")):
        return NormalizationResult(
            status="ignored",
            message="Ignoring outbound message from this WhatsApp instance.",
        )

    message = _message_body(data)
    chat_id = normalize_chat_id(
        key.get("remoteJid")
        or data.get("remoteJid")
        or data.get("chatId")
        or data.get("from")
        or payload.get("remoteJid")
    )
    sender_id = normalize_chat_id(
        key.get("participant") or data.get("sender") or data.get("from") or chat_id
    )
    sender_name = normalize_text(
        data.get("pushName") or data.get("senderName") or payload.get("senderName")
    )
    message_id = normalize_text(key.get("id") or data.get("id") or payload.get("id"))
    instance = normalize_text(payload.get("instance") or data.get("instance"))

    text = _extract_text(data, message)
    media = _extract_media(data, message)
    message_type = _detect_message_type(message, media, text)

    incoming = IncomingMessage(
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
        message_id=message_id,
        message_type=message_type,
        text=text,
        instance=instance,
        media=media,
        raw=payload,
    )

    if not chat_id:
        return NormalizationResult(
            status="invalid_payload",
            message="Webhook payload does not include a chat id.",
            incoming=incoming,
            error="missing_chat_id",
        )

    if message_type == "audio":
        if transcriber is not None:
            transcribed = normalize_text(transcriber.transcribe(media))
            if transcribed:
                incoming.text = transcribed
                return NormalizationResult(
                    status="ok",
                    message="Audio message transcribed.",
                    incoming=incoming,
                )
        if transcription_configured is None:
            transcription_configured = bool(os.environ.get("CISS_AUDIO_TRANSCRIBE_PROVIDER"))
        return NormalizationResult(
            status="needs_transcription",
            message="Audio message received, but no transcription provider is available.",
            incoming=incoming,
        )

    if not text:
        return NormalizationResult(
            status="ignored",
            message="Webhook message has no text content for the sales agent.",
            incoming=incoming,
        )

    return NormalizationResult(status="ok", message="Message normalized.", incoming=incoming)


def _payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    body = payload.get("body")
    if isinstance(body, dict):
        nested = body.get("data")
        return nested if isinstance(nested, dict) else body
    return payload


def _message_key(data: dict[str, Any]) -> dict[str, Any]:
    key = data.get("key")
    if isinstance(key, dict):
        return key
    message = data.get("message")
    if isinstance(message, dict) and isinstance(message.get("key"), dict):
        return message["key"]
    return {}


def _message_body(data: dict[str, Any]) -> dict[str, Any]:
    message = data.get("message")
    if isinstance(message, dict):
        nested = message.get("message")
        return nested if isinstance(nested, dict) else message
    messages = data.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        return _message_body(messages[0])
    return {}


def _extract_text(data: dict[str, Any], message: dict[str, Any]) -> str:
    candidates: list[Any] = [
        message.get("conversation"),
        _nested(message, "extendedTextMessage", "text"),
        _nested(message, "imageMessage", "caption"),
        _nested(message, "videoMessage", "caption"),
        _nested(message, "documentMessage", "caption"),
        _nested(message, "buttonsResponseMessage", "selectedDisplayText"),
        _nested(message, "buttonsResponseMessage", "selectedButtonId"),
        _nested(message, "templateButtonReplyMessage", "selectedDisplayText"),
        _nested(message, "listResponseMessage", "title"),
        _nested(message, "listResponseMessage", "singleSelectReply", "selectedRowId"),
        data.get("text"),
        data.get("body"),
        data.get("messageText"),
    ]
    for candidate in candidates:
        text = normalize_text(candidate)
        if text:
            return text
    return ""


def _extract_media(data: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    for key in ("audioMessage", "imageMessage", "videoMessage", "documentMessage"):
        value = message.get(key)
        if isinstance(value, dict):
            media = dict(value)
            media["kind"] = key.replace("Message", "")
            for field_name in ("mediaUrl", "url", "base64", "mimetype"):
                if field_name in data and field_name not in media:
                    media[field_name] = data[field_name]
            return media
    return {}


def _detect_message_type(message: dict[str, Any], media: dict[str, Any], text: str) -> str:
    if "audioMessage" in message or media.get("kind") == "audio":
        return "audio"
    if media.get("kind"):
        return str(media["kind"])
    return "text" if text else "unknown"


def _nested(source: dict[str, Any], *path: str) -> Any:
    current: Any = source
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
