<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/components/Card.svelte';
  import {
    get as apiGet,
    put as apiPut,
    post as apiPost,
  } from '$lib/api/client';

  // ----------------------------------------------------------------
  // Types mirroring config YAML
  // ----------------------------------------------------------------

  interface VisionConfig {
    capture_region: string;
    capture_interval_s: number;
    max_resolution: [number, number];
    compression: { format: string; quality: number };
  }

  interface HearingConfig {
    asr: {
      default_engine: string;
      vosk_model_path: string;
      vad_sensitivity: number;
    };
    wake_word: { phrases: string[] };
  }

  interface SpeechConfig {
    engine: string;
    voice_model: string;
    speaking_rate: number;
    volume: number;
    output_device: string;
  }

  interface SensesData {
    vision: VisionConfig;
    audio: {
      asr: HearingConfig['asr'];
      wake_word: HearingConfig['wake_word'];
      tts: SpeechConfig;
    };
  }

  // ----------------------------------------------------------------
  // State
  // ----------------------------------------------------------------

  let vision: VisionConfig = $state({
    capture_region: 'active-window',
    capture_interval_s: 1.0,
    max_resolution: [1920, 1080],
    compression: { format: 'jpeg', quality: 85 },
  });

  let hearing: HearingConfig = $state({
    asr: {
      default_engine: 'vosk',
      vosk_model_path: '',
      vad_sensitivity: 0.5,
    },
    wake_word: { phrases: ['hey agent'] },
  });

  let speech: SpeechConfig = $state({
    engine: 'piper',
    voice_model: '',
    speaking_rate: 1.0,
    volume: 1.0,
    output_device: '',
  });

  let dirty = $state(false);
  let saving = $state(false);
  let statusMsg = $state('');

  let visionOpen = $state(true);
  let hearingOpen = $state(true);
  let speechOpen = $state(true);

  // ----------------------------------------------------------------
  // Data loading
  // ----------------------------------------------------------------

  async function load(): Promise<void> {
    try {
      const d = await apiGet<SensesData>('/api/senses');
      if (d.vision) vision = d.vision;
      if (d.audio?.asr) {
        hearing = {
          asr: d.audio.asr,
          wake_word: d.audio.wake_word ?? { phrases: [] },
        };
      }
      if (d.audio?.tts) speech = d.audio.tts;
    } catch (e) {
      statusMsg = `Load failed: ${e}`;
    }
  }

  onMount(() => { load(); });

  // ----------------------------------------------------------------
  // Save
  // ----------------------------------------------------------------

  async function save(): Promise<void> {
    saving = true;
    statusMsg = '';
    try {
      await apiPut('/api/senses', {
        vision,
        audio: {
          asr: hearing.asr,
          wake_word: hearing.wake_word,
          tts: speech,
        },
      });
      dirty = false;
      statusMsg = 'Saved';
    } catch (e) {
      statusMsg = `Save failed: ${e}`;
    } finally {
      saving = false;
    }
  }

  function markDirty(): void { dirty = true; }

  // ----------------------------------------------------------------
  // Test buttons
  // ----------------------------------------------------------------

  async function testTts(): Promise<void> {
    try {
      await apiPost('/api/senses/test-tts', {
        text: 'Hello from OpenBaD',
      });
    } catch (e) {
      statusMsg = `TTS test failed: ${e}`;
    }
  }

  async function previewCapture(): Promise<void> {
    try {
      await apiPost('/api/senses/preview-capture');
    } catch (e) {
      statusMsg = `Preview failed: ${e}`;
    }
  }

  // ----------------------------------------------------------------
  // Wake phrase helpers
  // ----------------------------------------------------------------

  function addPhrase(): void {
    hearing.wake_word.phrases = [
      ...hearing.wake_word.phrases,
      '',
    ];
    dirty = true;
  }

  function removePhrase(idx: number): void {
    hearing.wake_word.phrases =
      hearing.wake_word.phrases.filter((_, i) => i !== idx);
    dirty = true;
  }
</script>

<h2>Senses</h2>

<!-- Vision -->
<Card label="Vision">
  <button
    class="collapse-toggle"
    onclick={() => visionOpen = !visionOpen}
  >
    {visionOpen ? '▾' : '▸'} Vision
  </button>
  {#if visionOpen}
    <div class="form-grid">
      <label>Capture Region
        <select
          bind:value={vision.capture_region}
          onchange={markDirty}
        >
          <option value="full-screen">Full Screen</option>
          <option value="active-window">Active Window</option>
          <option value="custom-rect">Custom Rect</option>
        </select>
      </label>

      <label>Interval (s)
        <input
          type="number" min="0.1" step="0.1"
          bind:value={vision.capture_interval_s}
          oninput={markDirty}
        />
      </label>

      <label>Max Width
        <input
          type="number" min="320" step="1"
          bind:value={vision.max_resolution[0]}
          oninput={markDirty}
        />
      </label>

      <label>Max Height
        <input
          type="number" min="240" step="1"
          bind:value={vision.max_resolution[1]}
          oninput={markDirty}
        />
      </label>

      <label>Compression Format
        <select
          bind:value={vision.compression.format}
          onchange={markDirty}
        >
          <option value="jpeg">JPEG</option>
          <option value="png">PNG</option>
          <option value="raw_rgb">Raw RGB</option>
        </select>
      </label>

      <label>Quality
        <input
          type="range" min="10" max="100" step="1"
          bind:value={vision.compression.quality}
          oninput={markDirty}
        />
        <span>{vision.compression.quality}</span>
      </label>

      <button class="test-btn" onclick={previewCapture}>
        Preview Capture
      </button>
    </div>
  {/if}
</Card>

<!-- Hearing -->
<Card label="Hearing">
  <button
    class="collapse-toggle"
    onclick={() => hearingOpen = !hearingOpen}
  >
    {hearingOpen ? '▾' : '▸'} Hearing
  </button>
  {#if hearingOpen}
    <div class="form-grid">
      <label>ASR Engine
        <select
          bind:value={hearing.asr.default_engine}
          onchange={markDirty}
        >
          <option value="vosk">Vosk</option>
          <option value="whisper">Whisper</option>
        </select>
      </label>

      <label>Model Path
        <input
          type="text"
          bind:value={hearing.asr.vosk_model_path}
          oninput={markDirty}
        />
      </label>

      <label>VAD Sensitivity
        <input
          type="range" min="0" max="1" step="0.05"
          bind:value={hearing.asr.vad_sensitivity}
          oninput={markDirty}
        />
        <span>{hearing.asr.vad_sensitivity}</span>
      </label>

      <fieldset>
        <legend>Wake Phrases</legend>
        {#each hearing.wake_word.phrases as phrase, i}
          <div class="phrase-row">
            <input
              type="text"
              value={phrase}
              oninput={(e: Event) => {
                hearing.wake_word.phrases[i] =
                  (e.target as HTMLInputElement).value;
                dirty = true;
              }}
            />
            <button
              class="small-btn"
              onclick={() => removePhrase(i)}
            >✕</button>
          </div>
        {/each}
        <button class="small-btn" onclick={addPhrase}>
          + Add phrase
        </button>
      </fieldset>
    </div>
  {/if}
</Card>

<!-- Speech -->
<Card label="Speech">
  <button
    class="collapse-toggle"
    onclick={() => speechOpen = !speechOpen}
  >
    {speechOpen ? '▾' : '▸'} Speech
  </button>
  {#if speechOpen}
    <div class="form-grid">
      <label>TTS Engine
        <select bind:value={speech.engine} onchange={markDirty}>
          <option value="piper">Piper</option>
          <option value="espeak">eSpeak</option>
        </select>
      </label>

      <label>Voice Model
        <input
          type="text"
          bind:value={speech.voice_model}
          oninput={markDirty}
        />
      </label>

      <label>Speed
        <input
          type="range" min="0.5" max="2.0" step="0.1"
          bind:value={speech.speaking_rate}
          oninput={markDirty}
        />
        <span>{speech.speaking_rate}</span>
      </label>

      <label>Volume
        <input
          type="range" min="0" max="1" step="0.05"
          bind:value={speech.volume}
          oninput={markDirty}
        />
        <span>{speech.volume}</span>
      </label>

      <label>Output Device
        <input
          type="text"
          bind:value={speech.output_device}
          oninput={markDirty}
        />
      </label>

      <button class="test-btn" onclick={testTts}>Test TTS</button>
    </div>
  {/if}
</Card>

<!-- Save -->
<div class="actions">
  <button onclick={save} disabled={!dirty || saving}>
    {saving ? 'Saving…' : 'Save'}
  </button>
  {#if statusMsg}
    <span class="status">{statusMsg}</span>
  {/if}
</div>

<style>
  .form-grid {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .form-grid label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }
  .form-grid input, .form-grid select {
    padding: 0.3rem 0.5rem;
  }
  .collapse-toggle {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 1rem;
    font-weight: 600;
    padding: 0;
    margin-bottom: 0.5rem;
  }
  .test-btn {
    align-self: flex-start;
    padding: 0.4rem 1rem;
    margin-top: 0.5rem;
  }
  .phrase-row {
    display: flex;
    gap: 0.3rem;
    align-items: center;
    margin-bottom: 0.3rem;
  }
  .phrase-row input { flex: 1; }
  .small-btn {
    padding: 0.2rem 0.5rem;
    font-size: 0.8rem;
  }
  fieldset {
    border: 1px solid #444;
    border-radius: 4px;
    padding: 0.5rem;
  }
  legend { font-weight: 600; }
  .actions {
    display: flex;
    gap: 1rem;
    align-items: center;
    margin-top: 1rem;
  }
  .actions button { padding: 0.5rem 1.5rem; }
  .status { font-size: 0.85rem; opacity: 0.8; }
</style>
