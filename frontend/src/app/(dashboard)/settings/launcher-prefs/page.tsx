'use client'

// v0.8.6 Item D — Settings page for launcher env-var preferences.
//
// Surfaces four knobs that are otherwise env-var-only:
//   DEEPER_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH — absolute path to a draft GGUF
//   DEEPER_NOTEBOOK_LOCAL_DRAFT_N_PREDICT  — draft tokens per verification pass
//   DN_CHAT_LLM_CTX                        — local context window (n_ctx)
//   DN_CHAT_LLM_CTX_MAX                    — n_ctx upper ceiling
//
// Design choices:
//   - Only the diff (changed fields) is submitted; unchanged fields are
//     omitted from the PUT payload. Computed by comparing form state against
//     the GET response at submit time.
//   - Cleared numeric fields send null (removes the key from launcher.env)
//     so the launcher falls back to its built-in auto-detection.
//   - A "restart to apply" banner is shown after a successful save since
//     env vars are read once at launcher startup (before the API exists).
//   - All strings i18n'd via settings.launcherPrefs.* keys.

import { useState, useEffect } from 'react'
import { AlertTriangle, Cpu, Sparkles, Zap, Check } from 'lucide-react'
import { AppShell } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { useTranslation } from '@/lib/hooks/use-translation'
import {
  useLauncherPrefs,
  useUpdateLauncherPrefs,
  useHardwareProfile,
} from '@/lib/hooks/use-launcher-prefs'
import { SystemRouteFrame } from '@/components/deeper-notebook/route-frames/SystemRouteFrames'

// ---------------------------------------------------------------------------
// Whitelisted keys — must match desktop/launcher_prefs.py:ALLOWED_KEYS.
// Frontend guard: do not submit any key outside this set even if form state
// somehow contains one (belt-and-suspenders alongside the backend check).
// ---------------------------------------------------------------------------
const ALLOWED_KEYS = new Set([
  'DEEPER_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH',
  'DEEPER_NOTEBOOK_LOCAL_DRAFT_N_PREDICT',
  'DEEPER_NOTEBOOK_LOCAL_N_CTX',
  'DEEPER_NOTEBOOK_CHAT_LLM_CTX',
  'DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX',
  'DEEPER_NOTEBOOK_SOURCE_VISUALS_ENABLED',
  'DEEPER_NOTEBOOK_LLAMACPP_FLASH_ATTN',
  'DEEPER_NOTEBOOK_LLAMACPP_KV_QUANT',
])

const PREF_KEYS = {
  draftModelPath: 'DEEPER_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH',
  draftNPredict: 'DEEPER_NOTEBOOK_LOCAL_DRAFT_N_PREDICT',
  nCtx: 'DEEPER_NOTEBOOK_CHAT_LLM_CTX',
  nCtxMax: 'DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX',
  flashAttn: 'DEEPER_NOTEBOOK_LLAMACPP_FLASH_ATTN',
  kvQuant: 'DEEPER_NOTEBOOK_LLAMACPP_KV_QUANT',
} as const

const readPref = (
  prefs: Record<string, string>,
  key: (typeof PREF_KEYS)[keyof typeof PREF_KEYS],
): string => prefs[key] ?? ''

export default function LauncherPrefsPage() {
  const { t } = useTranslation()
  const { data, isLoading } = useLauncherPrefs()
  const { data: hardware } = useHardwareProfile()
  const update = useUpdateLauncherPrefs()

  // Form state — we keep separate string fields for each control.
  const [draftModelPath, setDraftModelPath] = useState('')
  const [draftNPredict, setDraftNPredict] = useState('')
  const [nCtx, setNCtx] = useState('')
  const [nCtxMax, setNCtxMax] = useState('')
  const [flashAttn, setFlashAttn] = useState('')
  const [kvQuant, setKvQuant] = useState('')

  // Show "restart required" banner only after a successful save.
  const [showRestartBanner, setShowRestartBanner] = useState(false)

  // Seed form from the GET response once loaded.
  useEffect(() => {
    if (!data?.prefs) return
    const p = data.prefs
    setDraftModelPath(readPref(p, PREF_KEYS.draftModelPath))
    setDraftNPredict(readPref(p, PREF_KEYS.draftNPredict))
    setNCtx(readPref(p, PREF_KEYS.nCtx))
    setNCtxMax(readPref(p, PREF_KEYS.nCtxMax))
    setFlashAttn(readPref(p, PREF_KEYS.flashAttn))
    setKvQuant(readPref(p, PREF_KEYS.kvQuant))
  }, [data])

  // ---------------------------------------------------------------------------
  // Diff computation — only changed / cleared fields go in the PUT payload.
  // An empty string means "clear this key" → send null.
  // An unchanged field is omitted from the payload entirely.
  // ---------------------------------------------------------------------------
  const buildDiff = (): { [key: string]: string | null } => {
    const current = data?.prefs ?? {}
    const diff: { [key: string]: string | null } = {}

    const check = (
      key: (typeof PREF_KEYS)[keyof typeof PREF_KEYS],
      newVal: string,
    ) => {
      if (!ALLOWED_KEYS.has(key)) return  // frontend whitelist guard
      const old = readPref(current, key)
      if (newVal === old) return          // no change — skip
      diff[key] = newVal.trim() === '' ? null : newVal.trim()
    }

    check(PREF_KEYS.draftModelPath, draftModelPath)
    check(PREF_KEYS.draftNPredict, draftNPredict)
    check(PREF_KEYS.nCtx, nCtx)
    check(PREF_KEYS.nCtxMax, nCtxMax)
    check(PREF_KEYS.flashAttn, flashAttn)
    check(PREF_KEYS.kvQuant, kvQuant)

    return diff
  }

  const handleApplyHardwareRecommendations = () => {
    if (!hardware) return
    if (hardware.recommended_context) {
      setNCtx(String(hardware.recommended_context))
      setNCtxMax(String(hardware.recommended_context))
    }
    if (hardware.recommended_flash_attn) {
      setFlashAttn('true')
    }
    if (hardware.recommended_kv_quant) {
      setKvQuant(hardware.recommended_kv_quant)
    }
  }

  const handleSave = () => {
    const diff = buildDiff()
    if (Object.keys(diff).length === 0) return  // nothing changed
    update.mutate(
      { prefs: diff },
      {
        onSuccess: () => setShowRestartBanner(true),
      },
    )
  }

  const isDirty = Object.keys(buildDiff()).length > 0

  return (
    <AppShell>
      <SystemRouteFrame route="/settings/launcher-prefs" title={t('settings.launcherPrefs.title')} description={t('settings.launcherPrefs.description')}>
          <div className="mx-auto max-w-3xl space-y-10 rounded-lg bg-[var(--dn-folio-paper)] p-4 sm:p-6">

            {/* Restart-required banner — shown after a successful save */}
            {showRestartBanner && (
              <div
                className="flex items-start gap-3 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
                role="alert"
                data-testid="restart-banner"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{t('settings.launcherPrefs.restartRequired')}</span>
              </div>
            )}

            {/* Apple Silicon & Hardware Advisor Card */}
            {hardware && (
              <div
                className="rounded-xl border border-primary/20 bg-card/60 p-5 shadow-sm space-y-4"
                data-testid="hardware-advisor-card"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/40 pb-3">
                  <div className="flex items-center gap-2.5">
                    <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <Cpu className="h-5 w-5" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="text-base font-semibold text-foreground">
                          {hardware.chip_name || 'System Hardware'}
                        </h3>
                        {hardware.is_apple_silicon && (
                          <Badge variant="secondary" className="text-xs bg-primary/15 text-primary border-0 font-medium">
                            Apple Silicon Unified Memory
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {hardware.tier_name} • {hardware.total_ram_gb} GB RAM
                      </p>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="text-xs gap-1.5 border-primary/30 hover:bg-primary/10"
                    onClick={handleApplyHardwareRecommendations}
                    data-testid="apply-hardware-recommendations"
                  >
                    <Sparkles className="h-3.5 w-3.5 text-primary" />
                    Apply Recommended Settings
                  </Button>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                  <div className="rounded-lg bg-muted/40 p-2.5 space-y-1">
                    <span className="text-muted-foreground font-medium">Context Window</span>
                    <p className="text-sm font-semibold text-foreground">
                      {hardware.recommended_context.toLocaleString()} tokens
                    </p>
                  </div>
                  <div className="rounded-lg bg-muted/40 p-2.5 space-y-1">
                    <span className="text-muted-foreground font-medium">Recommended Model</span>
                    <p className="text-sm font-semibold text-foreground">
                      {hardware.recommended_quant}
                    </p>
                  </div>
                  <div className="rounded-lg bg-muted/40 p-2.5 space-y-1">
                    <span className="text-muted-foreground font-medium">Engine Acceleration</span>
                    <p className="text-sm font-semibold text-foreground">
                      {hardware.recommended_flash_attn ? 'Metal FlashAttn + ' + hardware.recommended_kv_quant : 'Standard CPU/GPU'}
                    </p>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground italic">
                  {hardware.guidance}
                </p>
              </div>
            )}

            {isLoading ? (
              <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
            ) : (
              <form
                className="space-y-8"
                onSubmit={(e) => { e.preventDefault(); handleSave() }}
              >

                {/* ── Speculative decoding section ───────────────────── */}
                <section className="space-y-6">
                  <h2 className="text-lg font-medium">
                    {t('settings.launcherPrefs.speculativeDecodingTitle')}
                  </h2>

                  {/* Draft model path */}
                  <div className="space-y-1.5">
                    <label
                      htmlFor="draft-model-path"
                      className="text-sm font-medium"
                    >
                      {t('settings.launcherPrefs.draftModelPathLabel')}
                    </label>
                    <Input
                      id="draft-model-path"
                      data-testid="draft-model-path"
                      placeholder={t('settings.launcherPrefs.draftModelPathPlaceholder')}
                      value={draftModelPath}
                      onChange={(e) => setDraftModelPath(e.target.value)}
                      className="w-full font-mono text-sm"
                      aria-describedby="draft-model-path-desc"
                    />
                    <p
                      id="draft-model-path-desc"
                      className="text-xs text-muted-foreground"
                    >
                      {t('settings.launcherPrefs.draftModelPathDesc')}
                    </p>
                  </div>

                  {/* Draft n_predict */}
                  <div className="space-y-1.5">
                    <label
                      htmlFor="draft-n-predict"
                      className="text-sm font-medium"
                    >
                      {t('settings.launcherPrefs.draftNPredictLabel')}
                      {' '}
                      <span className="font-normal text-muted-foreground">
                        ({t('common.optional')})
                      </span>
                    </label>
                    <Input
                      id="draft-n-predict"
                      data-testid="draft-n-predict"
                      type="number"
                      min={1}
                      max={128}
                      placeholder={t('settings.launcherPrefs.draftNPredictPlaceholder')}
                      value={draftNPredict}
                      onChange={(e) => setDraftNPredict(e.target.value)}
                      className="w-40"
                      aria-describedby="draft-n-predict-desc"
                    />
                    <p
                      id="draft-n-predict-desc"
                      className="text-xs text-muted-foreground"
                    >
                      {t('settings.launcherPrefs.draftNPredictDesc')}
                    </p>
                  </div>
                </section>

                {/* ── Context window section ─────────────────────────── */}
                <section className="space-y-6">
                  <h2 className="text-lg font-medium">
                    {t('settings.launcherPrefs.contextWindowTitle')}
                  </h2>

                  {/* DN_CHAT_LLM_CTX */}
                  <div className="space-y-1.5">
                    <label
                      htmlFor="n-ctx"
                      className="text-sm font-medium"
                    >
                      {t('settings.launcherPrefs.nCtxLabel')}
                      {' '}
                      <span className="font-normal text-muted-foreground">
                        ({t('common.optional')})
                      </span>
                    </label>
                    <Input
                      id="n-ctx"
                      data-testid="n-ctx"
                      type="number"
                      min={512}
                      max={131072}
                      placeholder={t('settings.launcherPrefs.nCtxPlaceholder')}
                      value={nCtx}
                      onChange={(e) => setNCtx(e.target.value)}
                      className="w-40"
                      aria-describedby="n-ctx-desc"
                    />
                    <p
                      id="n-ctx-desc"
                      className="text-xs text-muted-foreground"
                    >
                      {t('settings.launcherPrefs.nCtxDesc')}
                    </p>
                  </div>

                  {/* DN_CHAT_LLM_CTX_MAX */}
                  <div className="space-y-1.5">
                    <label
                      htmlFor="n-ctx-max"
                      className="text-sm font-medium"
                    >
                      {t('settings.launcherPrefs.nCtxMaxLabel')}
                      {' '}
                      <span className="font-normal text-muted-foreground">
                        ({t('common.optional')})
                      </span>
                    </label>
                    <Input
                      id="n-ctx-max"
                      data-testid="n-ctx-max"
                      type="number"
                      min={512}
                      max={131072}
                      placeholder={t('settings.launcherPrefs.nCtxMaxPlaceholder')}
                      value={nCtxMax}
                      onChange={(e) => setNCtxMax(e.target.value)}
                      className="w-40"
                      aria-describedby="n-ctx-max-desc"
                    />
                    <p
                      id="n-ctx-max-desc"
                      className="text-xs text-muted-foreground"
                    >
                      {t('settings.launcherPrefs.nCtxMaxDesc')}
                    </p>
                  </div>
                </section>

                {/* ── Engine Hardware Optimizations (Metal / FlashAttention / KV Cache) ── */}
                <section className="space-y-6">
                  <div className="flex items-center gap-2">
                    <Zap className="h-5 w-5 text-primary" />
                    <h2 className="text-lg font-medium">
                      Engine Hardware Optimizations
                    </h2>
                  </div>

                  {/* FlashAttention */}
                  <div className="space-y-1.5">
                    <label
                      htmlFor="flash-attn"
                      className="text-sm font-medium"
                    >
                      Metal FlashAttention
                    </label>
                    <select
                      id="flash-attn"
                      data-testid="flash-attn"
                      value={flashAttn}
                      onChange={(e) => setFlashAttn(e.target.value)}
                      className="flex h-9 w-52 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    >
                      <option value="">Auto-Detect (Default on Metal)</option>
                      <option value="true">Force Enabled</option>
                      <option value="false">Force Disabled</option>
                    </select>
                    <p className="text-xs text-muted-foreground">
                      Significantly accelerates attention computation on Apple Silicon and modern GPUs with lower memory bandwidth overhead.
                    </p>
                  </div>

                  {/* KV Cache Quantization */}
                  <div className="space-y-1.5">
                    <label
                      htmlFor="kv-quant"
                      className="text-sm font-medium"
                    >
                      Key-Value Cache Quantization
                    </label>
                    <select
                      id="kv-quant"
                      data-testid="kv-quant"
                      value={kvQuant}
                      onChange={(e) => setKvQuant(e.target.value)}
                      className="flex h-9 w-52 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    >
                      <option value="">Auto-Detect (q8_0 on Apple Silicon)</option>
                      <option value="q8_0">q8_0 (8-bit — 50% RAM savings, high precision)</option>
                      <option value="q4_0">q4_0 (4-bit — 75% RAM savings, massive context)</option>
                      <option value="f16">f16 (Full Precision — maximum memory usage)</option>
                    </select>
                    <p className="text-xs text-muted-foreground">
                      Quantizing the KV cache allows long contexts (32k/64k) to fit easily into unified RAM without degrading generation quality.
                    </p>
                  </div>
                </section>

                {/* Save button */}
                <div>
                  <Button
                    type="submit"
                    disabled={!isDirty || update.isPending}
                    data-testid="save-button"
                  >
                    {update.isPending
                      ? t('common.saving')
                      : t('settings.launcherPrefs.saveButton')}
                  </Button>
                </div>
              </form>
            )}
          </div>
      </SystemRouteFrame>
    </AppShell>
  )
}
