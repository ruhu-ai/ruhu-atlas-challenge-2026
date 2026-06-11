
from __future__ import annotations

import hashlib
import hmac
import mimetypes
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from .schemas import Modality, RenderedMessage


@dataclass(slots=True, frozen=True)
class WhatsAppMetaChannelConfig:
    agent_id: str
    phone_number_id: str
    verify_token: str
    access_token: str
    app_secret: str
    organization_id: str | None = None
    api_base_url: str = "https://graph.facebook.com/v18.0"

    @property
    def messages_url(self) -> str:
        return f"{self.api_base_url.rstrip('/')}/{self.phone_number_id}/messages"


@dataclass(slots=True, frozen=True)
class MetaWhatsAppInboundMessage:
    sender_id: str
    text: str | None
    message_id: str
    phone_number_id: str
    modality: Modality = "text"
    message_type: str = "text"
    sender_name: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class MetaWhatsAppInboundStatus:
    status: str
    phone_number_id: str
    recipient_id: str | None = None
    provider_message_id: str | None = None
    occurred_at: str | None = None
    errors: list[dict[str, object]] = field(default_factory=list)
    pricing: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class MetaWhatsAppDownloadedMedia:
    media_id: str
    filename: str
    content_type: str
    content_bytes: bytes
    metadata: dict[str, object] = field(default_factory=dict)


def parse_whatsapp_meta_channels(raw_channels: dict[str, dict[str, object]] | None) -> dict[str, WhatsAppMetaChannelConfig]:
    if not raw_channels:
        return {}
    parsed: dict[str, WhatsAppMetaChannelConfig] = {}
    for key, value in raw_channels.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("whatsapp meta channel keys must be non-empty phone_number_id strings")
        if not isinstance(value, dict):
            raise ValueError("whatsapp meta channel config must be an object")
        phone_number_id = str(value.get("phone_number_id") or key).strip()
        agent_id = str(value.get("agent_id") or "").strip()
        verify_token = str(value.get("verify_token") or "").strip()
        access_token = str(value.get("access_token") or "").strip()
        app_secret = str(value.get("app_secret") or "").strip()
        organization_id = value.get("organization_id")
        api_base_url = str(value.get("api_base_url") or "https://graph.facebook.com/v18.0").strip()
        if not agent_id or not phone_number_id or not verify_token or not access_token or not app_secret:
            raise ValueError(
                "whatsapp meta channel config requires agent_id, phone_number_id, verify_token, access_token, and app_secret"
            )
        parsed[phone_number_id] = WhatsAppMetaChannelConfig(
            agent_id=agent_id,
            phone_number_id=phone_number_id,
            verify_token=verify_token,
            access_token=access_token,
            app_secret=app_secret,
            organization_id=str(organization_id).strip() if isinstance(organization_id, str) and organization_id.strip() else None,
            api_base_url=api_base_url,
        )
    return parsed


def provider_secret_is_valid(expected_secret: str | None, provided_secret: str | None) -> bool:
    if not expected_secret or not provided_secret:
        return False
    return hmac.compare_digest(expected_secret, provided_secret)


def match_whatsapp_meta_verify_token(
    configs: Iterable[WhatsAppMetaChannelConfig],
    verify_token: str | None,
) -> WhatsAppMetaChannelConfig | None:
    if not verify_token:
        return None
    for config in configs:
        if hmac.compare_digest(config.verify_token, verify_token):
            return config
    return None


def extract_whatsapp_meta_phone_number_id(payload: dict[str, Any]) -> str | None:
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []):
            if not isinstance(change, dict):
                continue
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            phone_number_id = metadata.get("phone_number_id")
            if isinstance(phone_number_id, str) and phone_number_id.strip():
                return phone_number_id.strip()
    return None


def verify_whatsapp_meta_signature(
    config: WhatsAppMetaChannelConfig,
    body: bytes,
    signature_header: str | None,
) -> bool:
    if not signature_header:
        return False
    expected = hmac.new(config.app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header, f"sha256={expected}")


def extract_whatsapp_meta_messages(payload: dict[str, Any]) -> list[MetaWhatsAppInboundMessage]:
    messages: list[MetaWhatsAppInboundMessage] = []
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []):
            if not isinstance(change, dict) or change.get("field") not in {None, "messages"}:
                continue
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata", {}) if isinstance(value.get("metadata"), dict) else {}
            phone_number_id = str(metadata.get("phone_number_id") or "").strip()
            contacts = value.get("contacts", []) if isinstance(value.get("contacts"), list) else []
            contacts_by_waid: dict[str, dict[str, Any]] = {}
            for contact in contacts:
                if not isinstance(contact, dict):
                    continue
                wa_id = str(contact.get("wa_id") or "").strip()
                if wa_id:
                    contacts_by_waid[wa_id] = contact
            for item in value.get("messages", []):
                if not isinstance(item, dict):
                    continue
                message_type = str(item.get("type") or "text")
                modality = _whatsapp_meta_modality(message_type)
                text = _extract_whatsapp_meta_text(item)
                sender_id = str(item.get("from") or "").strip()
                if not sender_id:
                    continue
                contact = contacts_by_waid.get(sender_id, {})
                profile = contact.get("profile", {}) if isinstance(contact.get("profile"), dict) else {}
                media = _extract_whatsapp_meta_media(item, message_type=message_type)
                messages.append(
                    MetaWhatsAppInboundMessage(
                        sender_id=sender_id,
                        text=text or None,
                        message_id=str(item.get("id") or ""),
                        phone_number_id=phone_number_id,
                        modality=modality,
                        message_type=message_type,
                        sender_name=str(profile.get("name") or "").strip() or None,
                        metadata={
                            "provider": "meta_whatsapp",
                            "message_type": message_type,
                            "wa_id": sender_id,
                            "phone_number_id": phone_number_id,
                            **({"media": media} if media else {}),
                        },
                    )
                )
    return messages


def extract_whatsapp_meta_statuses(payload: dict[str, Any]) -> list[MetaWhatsAppInboundStatus]:
    statuses: list[MetaWhatsAppInboundStatus] = []
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []):
            if not isinstance(change, dict) or change.get("field") not in {None, "messages"}:
                continue
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata", {}) if isinstance(value.get("metadata"), dict) else {}
            phone_number_id = str(metadata.get("phone_number_id") or "").strip()
            for item in value.get("statuses", []):
                if not isinstance(item, dict):
                    continue
                errors = item.get("errors")
                parsed_errors = [error for error in errors if isinstance(error, dict)] if isinstance(errors, list) else []
                pricing = item.get("pricing")
                parsed_pricing = pricing if isinstance(pricing, dict) else {}
                statuses.append(
                    MetaWhatsAppInboundStatus(
                        status=str(item.get("status") or "").strip() or "unknown",
                        phone_number_id=phone_number_id,
                        recipient_id=str(item.get("recipient_id") or "").strip() or None,
                        provider_message_id=str(item.get("id") or "").strip() or None,
                        occurred_at=str(item.get("timestamp") or "").strip() or None,
                        errors=parsed_errors,
                        pricing=parsed_pricing,
                        metadata={
                            "provider": "meta_whatsapp",
                            "conversation": item.get("conversation"),
                            **({"biz_opaque_callback_data": item.get("biz_opaque_callback_data")} if item.get("biz_opaque_callback_data") is not None else {}),
                        },
                    )
                )
    return statuses


def assistant_texts(messages: Iterable[RenderedMessage]) -> list[str]:
    outbound: list[str] = []
    for message in messages:
        text = (message.text or "").strip()
        if text:
            outbound.append(text)
    return outbound


async def send_whatsapp_meta_texts(
    config: WhatsAppMetaChannelConfig,
    *,
    recipient_id: str,
    texts: Iterable[str],
    client: Any | None = None,
) -> list[dict[str, object]]:
    outbound_texts = [text.strip() for text in texts if text and text.strip()]
    if not outbound_texts:
        return []

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=10.0)
    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "Content-Type": "application/json",
    }
    try:
        deliveries: list[dict[str, object]] = []
        for text in outbound_texts:
            response = await http_client.post(
                config.messages_url,
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": recipient_id,
                    "type": "text",
                    "text": {"body": text},
                },
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
            message_id = None
            if isinstance(payload, dict):
                message_id = (payload.get("messages") or [{}])[0].get("id")
            delivery: dict[str, object] = {"status": "sent", "text": text, "message_id": message_id}
            if isinstance(payload, dict):
                for key in ("amount_usd", "cost_usd", "provider_cost_usd", "cost_type", "reference_key"):
                    if key in payload:
                        delivery[key] = payload[key]
                pricing = payload.get("pricing")
                if isinstance(pricing, dict):
                    delivery["metadata"] = {"pricing": pricing}
                    for key in ("amount_usd", "cost_usd", "provider_cost_usd", "cost_type", "reference_key"):
                        if key in pricing and key not in delivery:
                            delivery[key] = pricing[key]
            deliveries.append(delivery)
        return deliveries
    finally:
        if owns_client:
            await http_client.aclose()


async def fetch_whatsapp_meta_media(
    config: WhatsAppMetaChannelConfig,
    *,
    media_id: str,
    message_id: str,
    message_type: str,
    client: Any | None = None,
) -> MetaWhatsAppDownloadedMedia:
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=20.0)
    headers = {
        "Authorization": f"Bearer {config.access_token}",
    }
    try:
        metadata_response = await http_client.get(
            f"{config.api_base_url.rstrip('/')}/{media_id}",
            headers=headers,
        )
        metadata_response.raise_for_status()
        metadata_payload = metadata_response.json()
        if not isinstance(metadata_payload, dict):
            raise ValueError("unexpected whatsapp media metadata payload")
        download_url = str(metadata_payload.get("url") or "").strip()
        if not download_url:
            raise ValueError("whatsapp media payload did not include download url")
        content_type = str(metadata_payload.get("mime_type") or "").strip().lower() or "application/octet-stream"
        filename = _whatsapp_media_filename(
            media_id=media_id,
            message_id=message_id,
            message_type=message_type,
            content_type=content_type,
            raw_filename=str(metadata_payload.get("filename") or "").strip() or None,
        )
        content_response = await http_client.get(download_url, headers=headers)
        content_response.raise_for_status()
        return MetaWhatsAppDownloadedMedia(
            media_id=media_id,
            filename=filename,
            content_type=content_type,
            content_bytes=bytes(content_response.content),
            metadata={
                "provider": "meta_whatsapp",
                "media_id": media_id,
                "download_url": download_url,
                "mime_type": content_type,
                **(
                    {
                        key: metadata_payload[key]
                        for key in ("sha256", "file_size", "id")
                        if key in metadata_payload
                    }
                ),
            },
        )
    finally:
        if owns_client:
            await http_client.aclose()


def _extract_whatsapp_meta_text(message: dict[str, Any]) -> str:
    message_type = str(message.get("type") or "text")
    if message_type == "text":
        return str((message.get("text") or {}).get("body") or "").strip()
    if message_type == "button":
        return str((message.get("button") or {}).get("text") or "").strip()
    if message_type == "interactive":
        interactive = message.get("interactive") or {}
        interactive_type = interactive.get("type")
        if interactive_type == "button_reply":
            return str((interactive.get("button_reply") or {}).get("title") or "").strip()
        if interactive_type == "list_reply":
            return str((interactive.get("list_reply") or {}).get("title") or "").strip()
    if message_type in {"image", "video", "document"}:
        return str((message.get(message_type) or {}).get("caption") or "").strip()
    return ""


def _extract_whatsapp_meta_media(message: dict[str, Any], *, message_type: str) -> dict[str, object] | None:
    if message_type not in {"image", "video", "document", "audio", "sticker"}:
        return None
    payload = message.get(message_type)
    if not isinstance(payload, dict):
        return None
    media: dict[str, object] = {"type": message_type}
    for key in ("id", "mime_type", "sha256", "filename", "caption"):
        value = payload.get(key)
        if value is not None:
            media[key] = value
    if message_type == "audio" and payload.get("voice") is not None:
        media["voice"] = bool(payload.get("voice"))
    return media


def _whatsapp_meta_modality(message_type: str) -> Modality:
    if message_type == "audio":
        return "audio"
    if message_type == "image":
        return "image"
    if message_type in {"video", "document", "sticker"}:
        return "file"
    return "text"


def _whatsapp_media_filename(
    *,
    media_id: str,
    message_id: str,
    message_type: str,
    content_type: str,
    raw_filename: str | None,
) -> str:
    if raw_filename:
        return raw_filename
    extension = mimetypes.guess_extension(content_type, strict=False) or ""
    if message_type == "audio" and extension == ".oga":
        extension = ".ogg"
    stem = message_id.strip() or media_id.strip() or f"whatsapp-{message_type}"
    return f"{stem}{extension}"
