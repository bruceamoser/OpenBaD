# Phase 2: Sensory Integration (Eyes and Ears)

> **Parent Document:** [overview-spec.md](overview-spec.md)

---

## Conceptual Foundation

### Sensory Processing and Operating System Integration

An autonomous agent's ability to perceive its environment relies heavily on deep integration with the underlying Operating System. The choice of host environment fundamentally dictates the efficiency, security, and modularity of the agent's sensory apparatus.

#### The "Eyes" — Digital Vision and Spatial Awareness

The agent's "Eyes" represent its capacity for digital vision and spatial awareness across the desktop environment. Linux operates on a philosophy natively synergistic with agentic autonomy. The Command Line Interface (CLI) is the most pure, low-latency environment for AI interaction, allowing the agent to bypass GUI interpretation entirely for system-level operations. When visual processing is required, modern Linux subsystems provide superior architectures:

- The **Wayland** display server protocol, paired with **PipeWire**, allows for secure, modular screen capturing.
- PipeWire facilitates seamless routing of video streams between isolated sandbox containers without exposing the entire desktop environment, strictly adhering to the principle of least privilege.
- The agent only "sees" the specific application windows relevant to its task, drastically reducing the token cost of visual processing compared to sending full-screen accessibility trees.

#### The "Ears" and "Voice" — Audio Processing and Communication

Digital hearing requires continuous background audio monitoring, implemented using localized Automatic Speech Recognition (ASR) models, parsing environmental audio and system sounds into structured text. The voice is synthesized through Text-to-Speech (TTS) pipelines for organic user interaction. Under Linux, advanced audio routing through ALSA or PipeWire allows the agent to intercept and analyze audio streams from specific applications independently.

#### Why Linux

The Linux kernel's control groups (cgroups) and eBPF mechanisms allow for intent-driven resource controllers. An agent running on Linux can dynamically adjust its own memory and CPU constraints at the tool-call level, preventing system hangs and ensuring that background scanning does not interfere with the user's primary workloads.

> **See also:** The eBPF-based interoception daemon that monitors resource consumption is defined in [Phase 1 — Task 1.2](01-environment-nervous-system.md#task-12-implement-interoception-via-ebpf-homeostasis).

---

## Technical Implementation

### Task 2.1: Implement the "Eyes" (Vision & Screen Parsing)

Configure sandboxed, permission-gated visual access to the desktop environment, routing structured visual data through the nervous system.

**Objective:** Give the agent the ability to observe specific application windows on the Linux desktop without whole-desktop exposure, producing structured representations (DOM, bounding boxes, OCR text) rather than raw pixel data wherever possible.

#### Sub-tasks

- [ ] **2.1.1 — Configure Wayland and PipeWire for secure screen capture**
  - Ensure the Linux host runs a **Wayland compositor** (e.g., Sway, Hyprland, or GNOME Wayland session).
  - Verify PipeWire is installed and running as the multimedia daemon.
  - Configure `xdg-desktop-portal` with the appropriate backend (e.g., `xdg-desktop-portal-wlr` for wlroots-based compositors, `xdg-desktop-portal-gnome` for GNOME) to expose the ScreenCast portal.
  - Document the required Flatpak/D-Bus permissions for the agent's portal access.

- [ ] **2.1.2 — Build the screen capture service**
  - Implement a Python service that connects to the PipeWire ScreenCast portal via D-Bus:
    - Request access to a **specific window** (not the entire desktop) using the portal's `SelectSources` method with `types=WINDOW`.
    - Accept the PipeWire stream node and consume frames using `GStreamer` (LGPL) or `PyPipeWire` bindings.
  - Frame capture settings:
    - Default resolution: match source window resolution (no upscaling).
    - Default frame rate: 1 FPS for static monitoring; dynamically increase to 5-10 FPS when the agent is actively interacting with a GUI element.
    - Output format: raw RGB or compressed JPEG depending on downstream consumer requirements.
  - Publish captured frames to `agent/sensory/vision/{source-id}` on the event bus as binary payloads with metadata (timestamp, window title, dimensions).

- [ ] **2.1.3 — Implement semantic screen parsing (DOM/accessibility extraction)**
  - Prefer structured representations over raw vision wherever possible to minimize token cost:
    - Use **AT-SPI2** (the Linux accessibility bus, LGPL) to extract the widget tree of the target application — button labels, text fields, tree views — as a structured JSON representation.
    - For web browsers, use a lightweight **CDP (Chrome DevTools Protocol)** client to extract the live DOM from the page, avoiding full-screen pixel analysis.
  - Implement a fallback OCR pipeline for applications without accessibility support:
    - Use **Tesseract OCR** (Apache 2.0) or **EasyOCR** (Apache 2.0) on captured frames.
    - Post-process OCR output to identify UI elements (buttons, labels, text regions) with bounding box coordinates.
  - Expose parsed screen data as a structured event on `agent/sensory/vision/{source-id}/parsed`.

- [ ] **2.1.4 — Implement the visual attention filter**
  - Not every frame needs to reach the cognitive module. Build a lightweight change-detection layer:
    - Compute frame-to-frame difference using pixel-level MSE or structural similarity (SSIM).
    - Only forward frames to downstream consumers when change exceeds a configurable threshold (default: 5% pixel delta).
    - On the System 1 reflex arc, define visual triggers: e.g., a modal dialog appearing triggers an immediate escalation event.
  - This filter prevents unnecessary token consumption from static or slowly-changing screens.

#### Suggested File Structure

```
src/
  sensory/
    vision/
      __init__.py
      capture.py            # PipeWire ScreenCast portal integration
      accessibility.py      # AT-SPI2 widget tree extraction
      cdp_dom.py            # Chrome DevTools Protocol DOM extraction
      ocr_fallback.py       # Tesseract/EasyOCR fallback pipeline
      attention_filter.py   # Frame change detection and gating
      config.py             # FPS, resolution, threshold settings
```

#### Acceptance Criteria
- Agent can capture frames from a single target window without accessing any other window's content.
- AT-SPI2 extraction produces a JSON widget tree in < 100ms for standard GTK/Qt applications.
- OCR fallback produces text output in < 500ms per frame at native resolution.
- Visual attention filter reduces downstream message volume by ≥ 80% on a static desktop.
- All visual data flows through the event bus (`agent/sensory/vision/*`); no direct coupling to cognitive modules.

---

### Task 2.2: Implement the "Ears" (Auditory Processing)

Deploy offline, low-latency speech recognition and audio monitoring, routing transcriptions through the nervous system.

**Objective:** Give the agent continuous auditory awareness — both of the user's spoken commands and of ambient system audio — using fully local models with permissive licenses.

#### Sub-tasks

- [ ] **2.2.1 — Select and deploy the ASR engine**
  - Evaluate two primary candidates:
    - **Vosk** (Apache 2.0 License): Lightweight, supports streaming recognition, many language models available, low memory footprint (~50MB for English small model).
    - **OpenAI Whisper Large V3 Turbo** (MIT License): Higher accuracy, larger model (~1.5GB), supports batch and real-time transcription via `faster-whisper` (MIT) for CTranslate2 acceleration.
  - Selection criteria:
    - For continuous background monitoring with low resource impact → **Vosk**
    - For high-accuracy on-demand transcription → **Whisper (via faster-whisper)**
  - Recommended: Deploy **both** — Vosk for always-on ambient monitoring, Whisper for high-fidelity transcription when the reflex arc detects a direct user address (wake word or attention signal).

- [ ] **2.2.2 — Configure audio routing via PipeWire/ALSA**
  - Use PipeWire to create virtual audio sinks that mirror specific application audio streams:
    - A dedicated capture node for the user's microphone input.
    - Optional capture nodes for specific application audio (e.g., meeting software, media players).
  - Use `pw-cli` or the PipeWire Python bindings to programmatically link audio nodes to the ASR input.
  - Ensure the agent's audio capture does not interrupt or degrade the user's normal audio experience (passive monitoring only).

- [ ] **2.2.3 — Implement the transcription pipeline**
  - Build a streaming transcription service:
    - Continuously feed audio chunks from PipeWire to the Vosk recognizer.
    - On detecting speech segments (Voice Activity Detection — Vosk provides this natively), produce partial and final transcription results.
    - Apply a speaker diarization step (optional, using `pyannote.audio` MIT License) to distinguish user speech from other audio sources.
  - Publish transcription events to `agent/sensory/audio/{source-id}` with:
    ```
    {
      "timestamp": "2026-04-09T12:05:30Z",
      "source": "microphone",
      "text": "Hey, check my calendar for tomorrow",
      "confidence": 0.94,
      "is_final": true,
      "speaker_id": "user_primary"
    }
    ```

- [ ] **2.2.4 — Implement the wake-word / attention detector**
  - Build a lightweight keyword-spotting layer that runs ahead of full ASR:
    - Use **openWakeWord** (Apache 2.0) or a small custom CNN model to detect configurable activation phrases.
    - Default wake phrase: configurable by user (e.g., "Hey Agent", custom name).
  - When wake word is detected:
    - Switch from Vosk ambient mode to Whisper high-accuracy mode for the next utterance.
    - Publish an attention event to `agent/reflex/attention/trigger` so the reflex arc can prepare cognitive resources.

- [ ] **2.2.5 — Implement Text-to-Speech (TTS) output (Voice)**
  - Deploy a local TTS engine for agent-initiated voice responses:
    - **Piper TTS** (MIT License): Fast, high-quality neural TTS with many voice models.
    - **Coqui TTS** (MPL 2.0): Alternative with voice cloning capabilities.
  - Route synthesized audio output through PipeWire to the user's default audio output.
  - TTS is triggered by cognitive module responses that are flagged for voice delivery.
  - Publish TTS completion events to `agent/sensory/audio/tts/complete`.

#### Suggested File Structure

```
src/
  sensory/
    audio/
      __init__.py
      capture.py            # PipeWire audio node management
      asr_vosk.py           # Vosk streaming recognition
      asr_whisper.py        # Whisper high-accuracy transcription
      wake_word.py          # openWakeWord keyword spotting
      diarization.py        # Speaker identification (optional)
      tts.py                # Piper/Coqui TTS output
      config.py             # Model paths, wake word, thresholds
```

#### Acceptance Criteria
- Vosk ambient monitoring operates continuously at < 5% CPU on a modern 4-core machine.
- Transcription events are published within 300ms of utterance completion (Vosk) or 1s (Whisper).
- Wake-word detection has a false-positive rate < 1% and a true-positive rate > 95% in quiet environments.
- TTS output is routed to the correct PipeWire audio sink without disrupting other audio streams.
- All audio data flows through the event bus; no direct coupling between ASR output and cognitive modules.

> **See also:** Audio events may trigger System 1 reflexes defined in [Phase 1 — Task 1.3](01-environment-nervous-system.md#task-13-configure-the-system-1-reflex-arc-instincts), or be escalated to System 2 reasoning in [Phase 3](03-cognitive-engine-immune-system.md#task-33-route-system-2-reasoning-the-prefrontal-cortex).

---

## Phase 2 Deliverables Summary

| Deliverable | Component | Key Technology |
| :--- | :--- | :--- |
| Sandboxed screen capture | Vision | PipeWire ScreenCast portal, Wayland |
| Accessibility tree extraction | Vision | AT-SPI2, CDP |
| OCR fallback pipeline | Vision | Tesseract / EasyOCR |
| Visual attention filter | Vision | SSIM / pixel-delta change detection |
| Ambient speech recognition | Audio | Vosk (streaming) |
| High-accuracy transcription | Audio | Whisper via faster-whisper |
| Wake-word detection | Audio | openWakeWord |
| Text-to-Speech output | Voice | Piper TTS |
| Audio routing | Infrastructure | PipeWire virtual sinks |

---

## Dependencies

- **Upstream:** Phase 1 (Nervous System event bus must be operational for all sensory data routing)
- **Downstream:** Phase 3 (Cognitive Engine consumes parsed sensory events for reasoning), Phase 1 Reflex Arc (consumes raw sensory events for instinctive responses)
- **System Requirements:** Linux with Wayland compositor, PipeWire, xdg-desktop-portal, AT-SPI2 enabled, microphone access
