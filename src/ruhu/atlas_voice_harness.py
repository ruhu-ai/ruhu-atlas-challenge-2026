from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Protocol

from .atlas_readiness_models import AtlasReadinessCase, AtlasReadinessTrace, AtlasVoiceArtifact
from .voice.protocol import VoiceProvider


@dataclass
class AtlasVoiceHarnessResult:
    metrics: dict[str, object]
    artifacts: list[AtlasVoiceArtifact] = field(default_factory=list)
    blob_payloads: dict[str, tuple[bytes, str]] = field(default_factory=dict)


class AtlasVoiceHarness(Protocol):
    def run_voice_case(
        self,
        *,
        run_id: str,
        case: AtlasReadinessCase,
        trace: AtlasReadinessTrace,
    ) -> AtlasVoiceHarnessResult: ...


class DeterministicAtlasVoiceHarness:
    def run_voice_case(
        self,
        *,
        run_id: str,
        case: AtlasReadinessCase,
        trace: AtlasReadinessTrace,
    ) -> AtlasVoiceHarnessResult:
        metrics: dict[str, object] = {
            "provider": "deterministic_stub",
            "stt_provider": "deterministic_stub",
            "tts_provider": "deterministic_stub",
            "stt_confidence": 1.0,
            "entity_preservation": 1.0,
            "intent_preservation": 1.0,
            "tts_artifact_generated": True,
            "latency_ms": 0,
        }
        return AtlasVoiceHarnessResult(
            metrics=metrics,
            artifacts=[
                AtlasVoiceArtifact(
                    run_id=run_id,
                    case_id=case.case_id,
                    provider="deterministic_stub",
                    artifact_type="voice_metrics",
                    metadata=metrics,
                )
            ],
        )


class GoogleAtlasVoiceHarness:
    """Evaluation-only voice harness that reuses the platform Google TTS provider."""

    def __init__(
        self,
        *,
        voice_provider: VoiceProvider | None = None,
        default_voice_id: str = "en-US-Chirp3-HD-Kore",
    ) -> None:
        self._voice_provider = voice_provider
        self._default_voice_id = default_voice_id

    @classmethod
    def from_platform_voice_provider(cls) -> "GoogleAtlasVoiceHarness":
        from .voice.factory import build_voice_provider_from_env

        return cls(voice_provider=build_voice_provider_from_env())

    def run_voice_case(
        self,
        *,
        run_id: str,
        case: AtlasReadinessCase,
        trace: AtlasReadinessTrace,
    ) -> AtlasVoiceHarnessResult:
        started = time.monotonic()
        artifacts: list[AtlasVoiceArtifact] = []
        blob_payloads: dict[str, tuple[bytes, str]] = {}
        metrics: dict[str, object] = {
            "provider": "google_voice_harness",
            "stt_provider": "google_speech_to_text",
            "tts_provider": getattr(self._voice_provider, "name", "vertex_gemini"),
            "stt_confidence": 0.0,
            "entity_preservation": 0.0,
            "intent_preservation": 0.0,
            "tts_artifact_generated": False,
        }

        transcript_text, stt_confidence, stt_reason = self._transcribe_case(case)
        metrics.update(
            {
                "transcript_text": transcript_text,
                "stt_confidence": stt_confidence,
                "stt_fallback_reason": stt_reason,
                "intent_preservation": 1.0 if transcript_text else 0.0,
                "entity_preservation": 1.0 if transcript_text else 0.0,
            }
        )
        artifacts.append(
            AtlasVoiceArtifact(
                run_id=run_id,
                case_id=case.case_id,
                provider="google_speech_to_text",
                artifact_type="stt_transcript",
                metadata={
                    "confidence": stt_confidence,
                    "fallback_reason": stt_reason,
                    "text": transcript_text,
                },
            )
        )

        reply = next((item for item in trace.replies if item.strip()), "")
        if not reply:
            reply = (
                f"Readiness case completed with status {trace.completion_status}."
                if trace.completion_status
                else "Readiness case completed."
            )
            metrics["tts_reply_source"] = "representative_fallback"
        elif reply:
            metrics["tts_reply_source"] = "trace_reply"
        if reply and self._voice_provider is not None:
            try:
                raw_voice_id = case.voice_input.get("voice_id") if case.voice_input else None
                raw_language = case.voice_input.get("language") if case.voice_input else None
                synthesis = self._voice_provider.synthesize(
                    reply,
                    voice_id=str(raw_voice_id).strip() if raw_voice_id else self._default_voice_id,
                    language=str(raw_language).strip() if raw_language else case.test_profile.locale,
                )
                digest = hashlib.sha256(synthesis.audio_bytes).hexdigest()
                metrics.update(
                    {
                        "tts_artifact_generated": True,
                        "tts_audio_mime_type": synthesis.audio_mime_type,
                        "tts_character_count": synthesis.character_count,
                        "tts_estimated_cost_usd": synthesis.estimated_cost_usd,
                    }
                )
                tts_artifact = AtlasVoiceArtifact(
                    run_id=run_id,
                    case_id=case.case_id,
                    provider=getattr(self._voice_provider, "name", "vertex_gemini"),
                    artifact_type="tts_audio",
                    metadata={
                        "sha256": digest,
                        "audio_mime_type": synthesis.audio_mime_type,
                        "character_count": synthesis.character_count,
                        "estimated_cost_usd": synthesis.estimated_cost_usd,
                        "provider_metadata": synthesis.provider_metadata,
                    },
                )
                artifacts.append(tts_artifact)
                blob_payloads[tts_artifact.artifact_id] = (synthesis.audio_bytes, synthesis.audio_mime_type)
            except Exception as exc:
                metrics["tts_fallback_reason"] = str(exc) or "tts_failed"
        else:
            metrics["tts_fallback_reason"] = "voice_provider_not_configured" if reply else "no_agent_reply"

        metrics["latency_ms"] = int((time.monotonic() - started) * 1000)
        artifacts.append(
            AtlasVoiceArtifact(
                run_id=run_id,
                case_id=case.case_id,
                provider="google_voice_harness",
                artifact_type="voice_metrics",
                metadata=metrics,
            )
        )
        return AtlasVoiceHarnessResult(metrics=metrics, artifacts=artifacts, blob_payloads=blob_payloads)

    def _transcribe_case(self, case: AtlasReadinessCase) -> tuple[str, float, str | None]:
        voice_input = dict(case.voice_input or {})
        audio_uri = str(voice_input.get("audio_uri") or "").strip()
        if audio_uri:
            return self._transcribe_google_audio_uri(audio_uri, language=str(voice_input.get("language") or case.test_profile.locale))
        text_fixture = " ".join(case.utterances).strip()
        return text_fixture, 1.0 if text_fixture else 0.0, "text_fixture_no_audio"

    def _transcribe_google_audio_uri(self, audio_uri: str, *, language: str) -> tuple[str, float, str | None]:
        # AR-2.6: only Google Cloud Storage object URIs are accepted. A
        # caller-supplied audio_uri must not steer the platform service account
        # at arbitrary schemes/hosts; bucket-level scoping is enforced by the
        # SA's own IAM. Reject anything that isn't a gs:// object reference.
        if not audio_uri.startswith("gs://") or "/" not in audio_uri[len("gs://"):]:
            return "", 0.0, "rejected_non_gcs_audio_uri"
        try:
            from google.cloud import speech_v1 as speech  # type: ignore[import-not-found]

            client = speech.SpeechClient()
            config = speech.RecognitionConfig(language_code=language or "en-US", enable_automatic_punctuation=True)
            audio = speech.RecognitionAudio(uri=audio_uri)
            response = client.recognize(config=config, audio=audio)
            alternatives = [result.alternatives[0] for result in response.results if result.alternatives]
            transcript = " ".join(item.transcript for item in alternatives).strip()
            confidence = min((item.confidence for item in alternatives), default=0.0)
            return transcript, float(confidence), None
        except Exception as exc:
            return "", 0.0, str(exc) or "google_stt_failed"
