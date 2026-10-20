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

<div class="page-header">
  <h2>Senses</h2>
  <p>Configure vision, hearing, and speech subsystems</p>
</div>

<div class="sections">
  <!-- Vision -->
  <Card label="👁 Vision">
    <button class="section-toggle" onclick={() => visionOpen = !visionOpen}>
      <span class="toggle-arrow">{visionOpen ? '▾' : '▸'}</span>
      <span>Screen Capture Configuration</span>
    </button>
    {#if visionOpen}
      <div class="form-grid">
        <div class="form-row-2">
          <label>Capture Region
            <select bind:value={vision.capture_region} onchange={markDirty}>
              <option value="full-screen">Full Screen</option>
              <option value="active-window">Active Window</option>
              <option value="custom-rect">Custom Rect</option>
            </select>
          </label>
          <label>Interval (sec)
            <input type="number" min="0.1" step="0.1" bind:value={vision.capture_interval_s} oninput={markDirty} />
          </label>
        </div>
        <div class="form-row-2">
          <label>Max Width
            <input type="number" min="320" step="1" bind:value={vision.max_resolution[0]} oninput={markDirty} />
          </label>
          <label>Max Height
            <input type="number" min="240" step="1" bind:value={vision.max_resolution[1]} oninput={markDirty} />
          </label>
        </div>
        <div class="form-row-2">
          <label>Format
            <select bind:value={vision.compression.format} onchange={markDirty}>
              <option value="jpeg">JPEG</option>
              <option value="png">PNG</option>
              <option value="raw_rgb">Raw RGB</option>
            </select>
          </label>
          <label>Quality — {vision.compression.quality}
            <input type="range" min="10" max="100" step="1" bind:value={vision.compression.quality} oninput={markDirty} />
          </label>
        </div>
        <button class="secondary test-btn" onclick={previewCapture}>📸 Preview Capture</button>
      </div>
    {/if}
  </Card>

  <!-- Hearing -->
  <Card label="👂 Hearing">
    <button class="section-toggle" onclick={() => hearingOpen = !hearingOpen}>
      <span class="toggle-arrow">{hearingOpen ? '▾' : '▸'}</span>
      <span>ASR & Wake Word Configuration</span>
    </button>
    {#if hearingOpen}
      <div class="form-grid">
        <div class="form-row-2">
          <label>ASR Engine
            <select bind:value={hearing.asr.default_engine} onchange={markDirty}>
              <option value="vosk">Vosk</option>
              <option value="whisper">Whisper</option>
            </select>
          </label>
          <label>Model Path
            <input type="text" bind:value={hearing.asr.vosk_model_path} oninput={markDirty} placeholder="/path/to/model" />
          </label>
        </div>
        <label>VAD Sensitivity — {hearing.asr.vad_sensitivity.toFixed(2)}
          <input type="range" min="0" max="1" step="0.05" bind:value={hearing.asr.vad_sensitivity} oninput={markDirty} />
        </label>
        <div class="wake-section">
          <div class="wake-header">
            <h4>Wake Phrases</h4>
            <button class="ghost" onclick={addPhrase}>+ Add</button>
          </div>
          {#each hearing.wake_word.phrases as phrase, i}
            <div class="phrase-row">
              <input
                type="text"
                value={phrase}
                placeholder="e.g. hey agent"
                oninput={(e: Event) => {
                  hearing.wake_word.phrases[i] = (e.target as HTMLInputElement).value;
                  dirty = true;
                }}
              />
              <button class="ghost danger-text" onclick={() => removePhrase(i)}>✕</button>
            </div>
          {/each}
          {#if hearing.wake_word.phrases.length === 0}
            <p class="hint">No wake phrases configured.</p>
          {/if}
        </div>
      </div>
    {/if}
  </Card>

  <!-- Speech -->
  <Card label="🗣 Speech">
    <button class="section-toggle" onclick={() => speechOpen = !speechOpen}>
      <span class="toggle-arrow">{speechOpen ? '▾' : '▸'}</span>
      <span>TTS Output Configuration</span>
    </button>
    {#if speechOpen}
      <div class="form-grid">
        <div class="form-row-2">
          <label>TTS Engine
            <select bind:value={speech.engine} onchange={markDirty}>
              <option value="piper">Piper</option>
              <option value="espeak">eSpeak</option>
            </select>
          </label>
          <label>Voice Model
            <input type="text" bind:value={speech.voice_model} oninput={markDirty} placeholder="e.g. en_US-amy-medium" />
          </label>
        </div>
        <div class="form-row-2">
          <label>Speed — {speech.speaking_rate.toFixed(1)}x
            <input type="range" min="0.5" max="2.0" step="0.1" bind:value={speech.speaking_rate} oninput={markDirty} />
          </label>
          <label>Volume — {(speech.volume * 100).toFixed(0)}%
            <input type="range" min="0" max="1" step="0.05" bind:value={speech.volume} oninput={markDirty} />
          </label>
        </div>
        <label>Output Device
          <input type="text" bind:value={speech.output_device} oninput={markDirty} placeholder="default" />
        </label>
        <button class="secondary test-btn" onclick={testTts}>🔊 Test TTS</button>
      </div>
    {/if}
  </Card>
</div>

<!-- Actions -->
<div class="actions-bar">
  <button onclick={save} disabled={!dirty || saving}>
    {saving ? 'Saving…' : 'Save Changes'}
  </button>
  {#if statusMsg}
    <span class="status-msg">{statusMsg}</span>
  {/if}
</div>

<style>
  .sections { display: flex; flex-direction: column; gap: 1rem; }

  .section-toggle {
    display: flex; align-items: center; gap: 0.5rem;
    background: none; border: none; cursor: pointer; padding: 0; margin-bottom: 0.75rem;
    font-size: 0.9rem; font-weight: 500; color: var(--text-sub);
  }
  .section-toggle:hover { color: var(--text); }
  .toggle-arrow { font-size: 0.8rem; width: 1rem; }

  .form-grid { display: flex; flex-direction: column; gap: 0.75rem; }
  .form-grid label { display: flex; flex-direction: column; gap: 0.3rem; }
  .form-row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
  @media (max-width: 600px) { .form-row-2 { grid-template-columns: 1fr; } }

  .test-btn { align-self: flex-start; margin-top: 0.25rem; }

  .wake-section { display: flex; flex-direction: column; gap: 0.5rem; }
  .wake-header { display: flex; justify-content: space-between; align-items: center; }
  .wake-header h4 { margin: 0; font-size: 0.9rem; color: var(--text-sub); }
  .phrase-row { display: flex; gap: 0.4rem; align-items: center; }
  .phrase-row input { flex: 1; }
  .danger-text { color: var(--red); }
  .hint { font-size: 0.8rem; color: var(--text-dim); }

  .actions-bar {
    display: flex; gap: 1rem; align-items: center; margin-top: 1.25rem;
    padding-top: 1rem; border-top: 1px solid var(--border);
  }
  .status-msg { font-size: 0.85rem; color: var(--text-sub); }
</style>
