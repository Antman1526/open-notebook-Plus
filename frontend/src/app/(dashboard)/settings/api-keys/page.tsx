'use client'

import { useMemo, useState, useEffect, useId } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { AppShell } from '@/components/layout/AppShell'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Key,
  ShieldAlert,
  AlertTriangle,
  Plus,
  Edit,
  Trash2,
  Plug,
  Loader2,
  Check,
  X,
  AlertCircle,
  Wand2,
  Bot,
  Search,
} from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'
import { formatModelProviderLabel } from '@/lib/types/models'
import { useModels, useDeleteModel, useModelDefaults, useUpdateModelDefaults, useAutoAssignDefaults, useAutoAssignCapability, useTestModel } from '@/lib/hooks/use-models'
import {
  useCredentials,
  useCredential,
  useCredentialStatus,
  useEnvStatus,
  useCreateCredential,
  useUpdateCredential,
  useTestCredential,
} from '@/lib/hooks/use-credentials'
import { Credential, CreateCredentialRequest, UpdateCredentialRequest } from '@/lib/api/credentials'
import { Model, ModelDefaults } from '@/lib/types/models'
import { MigrationBanner, ModelTestResultDialog, DeleteCredentialDialog, OsaurusDetectionBanner, SmartRoutingPanel } from '@/components/settings'
// ONP shadow-layer components (see frontend/src/components/deeper-notebook/README.md)
import { ReasoningSlotCard, GmailIntegration } from '@/components/deeper-notebook'
import { EmbeddingModelChangeDialog } from '@/components/settings/EmbeddingModelChangeDialog'

// v0.7.46 — type + constants moved to ./constants.tsx so the page and
// the extracted subcomponents (DiscoverModelsDialog, future ones)
// share one source of truth without re-declaring.
import {
  ModelType,
  PROVIDER_DISPLAY_NAMES,
  ALL_PROVIDERS,
  PROVIDER_MODALITIES,
  PROVIDER_DOCS,
  TYPE_ICONS,
  TYPE_COLORS,
  TYPE_COLOR_INACTIVE,
  TYPE_LABELS,
} from './constants'
import { DiscoverModelsDialog } from './components/DiscoverModelsDialog'
import { SystemRouteFrame } from '@/components/deeper-notebook/route-frames/SystemRouteFrames'

// =============================================================================
// Credential Form Dialog
// =============================================================================

function CredentialFormDialog({
  open,
  onOpenChange,
  provider,
  credential,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  provider: string
  credential?: Credential | null
}) {
  const { t } = useTranslation()
  const createCredential = useCreateCredential()
  const updateCredential = useUpdateCredential()
  const isEditing = !!credential
  const isSubmitting = createCredential.isPending || updateCredential.isPending

  const isVertex = provider === 'vertex'
  const isOllama = provider === 'ollama'
  const isOpenAICompatible = provider === 'openai_compatible'
  const requiresApiKey = !isVertex && !isOllama && !isOpenAICompatible

  const [name, setName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)
  const [project, setProject] = useState('')
  const [location, setLocation] = useState('')
  const [credentialsPath, setCredentialsPath] = useState('')
  // Modalities
  const [modalities, setModalities] = useState<string[]>([])

  useEffect(() => {
    if (credential) {
      setName(credential.name || '')
      setBaseUrl(credential.base_url || '')
      setApiKey('')
      setProject(credential.project || '')
      setLocation(credential.location || '')
      setCredentialsPath(credential.credentials_path || '')
      setModalities(credential.modalities || [])
    } else {
      setName('')
      setBaseUrl('')
      setApiKey('')
      setProject('')
      setLocation('')
      setCredentialsPath('')
      setModalities(PROVIDER_MODALITIES[provider] || ['language'])
    }
  }, [credential, provider])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()

    const onSuccess = () => {
      onOpenChange(false)
    }

    if (isEditing && credential) {
      const data: UpdateCredentialRequest = {}
      if (name !== credential.name) data.name = name
      if (apiKey.trim()) data.api_key = apiKey.trim()
      if (baseUrl !== (credential.base_url || '')) data.base_url = baseUrl || undefined
      if (JSON.stringify(modalities) !== JSON.stringify(credential.modalities)) data.modalities = modalities
      if (isVertex) {
        if (project !== (credential.project || '')) data.project = project.trim() || undefined
        if (location !== (credential.location || '')) data.location = location.trim() || undefined
        if (credentialsPath !== (credential.credentials_path || '')) data.credentials_path = credentialsPath.trim() || undefined
      }
      updateCredential.mutate({ credentialId: credential.id, data }, { onSuccess })
    } else {
      const data: CreateCredentialRequest = {
        name: name || `${PROVIDER_DISPLAY_NAMES[provider] || provider} Config`,
        provider,
        modalities,
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl || undefined,
      }
      if (isVertex) {
        data.project = project.trim() || undefined
        data.location = location.trim() || undefined
        data.credentials_path = credentialsPath.trim() || undefined
      }
      createCredential.mutate(data, { onSuccess })
    }
  }

  const isValid = isEditing
    ? true
    : isVertex
      ? name.trim() !== '' && project.trim() !== '' && location.trim() !== ''
      : name.trim() !== '' && (!requiresApiKey || apiKey.trim() !== '')

  const docsUrl = PROVIDER_DOCS[provider]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isEditing
              ? t('apiKeys.editConfig').replace('{provider}', PROVIDER_DISPLAY_NAMES[provider] || provider)
              : t('apiKeys.addConfig').replace('{provider}', PROVIDER_DISPLAY_NAMES[provider] || provider)}
          </DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div className="space-y-2">
            <Label htmlFor="cred-name">{t('apiKeys.configName')}</Label>
            <input
              id="cred-name"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={`${PROVIDER_DISPLAY_NAMES[provider] || provider} Production`}
              disabled={isSubmitting}
            />
            <p className="text-xs text-muted-foreground">{t('apiKeys.configNameHint')}</p>
          </div>

          {/* Vertex fields */}
          {isVertex ? (
            <>
              <div className="space-y-2">
                <Label htmlFor="vertex-project">{t('apiKeys.vertexProject')}</Label>
                <input
                  id="vertex-project"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                  placeholder="my-gcp-project"
                  disabled={isSubmitting}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="vertex-location">{t('apiKeys.vertexLocation')}</Label>
                <input
                  id="vertex-location"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="us-central1"
                  disabled={isSubmitting}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="vertex-creds">
                  {t('apiKeys.vertexCredentials')}
                  <span className="text-muted-foreground font-normal ml-1">({t('common.optional')})</span>
                </Label>
                <input
                  id="vertex-creds"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={credentialsPath}
                  onChange={(e) => setCredentialsPath(e.target.value)}
                  placeholder="/path/to/service-account.json"
                  disabled={isSubmitting}
                />
              </div>
            </>
          ) : (
            /* API Key */
            <div className="space-y-2">
              <Label htmlFor="api-key">
                {t('models.apiKey')}
                {!requiresApiKey && <span className="text-muted-foreground font-normal ml-1">({t('common.optional')})</span>}
              </Label>
              <div className="relative">
                <input
                  id="api-key"
                  type={showApiKey ? 'text' : 'password'}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm pr-10"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={isEditing ? '••••••••••••' : 'sk-...'}
                  disabled={isSubmitting}
                  autoComplete="off"
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey(!showApiKey)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground text-xs"
                  tabIndex={-1}
                >
                  {showApiKey ? 'Hide' : 'Show'}
                </button>
              </div>
              {isEditing && <p className="text-xs text-muted-foreground">{t('apiKeys.apiKeyEditHint')}</p>}
              {docsUrl && (
                <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="text-xs text-primary hover:underline">
                  {t('apiKeys.getApiKey')} &rarr;
                </a>
              )}
            </div>
          )}

          {/* Base URL (non-Vertex) */}
          {!isVertex && (
            <div className="space-y-2">
              <Label htmlFor="base-url" className="text-muted-foreground">{t('apiKeys.baseUrl')}</Label>
              <input
                id="base-url"
                type="url"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={isOllama ? 'http://localhost:11434' : 'https://api.example.com/v1'}
                disabled={isSubmitting}
              />
              <p className="text-xs text-muted-foreground">{t('apiKeys.baseUrlOverrideHint')}</p>
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-4 border-t">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!isValid || isSubmitting}>
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              {isEditing ? t('common.save') : t('apiKeys.addConfig')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}


// =============================================================================
// Delete Credential Dialog
// =============================================================================

// v0.7.40 — DeleteCredentialDialog extracted to
// @/components/settings/DeleteCredentialDialog. See that file for impl.

// =============================================================================
// Credential Card (shows credential + its models)
// =============================================================================

function CredentialItem({
  credential,
  models,
  defaults,
  allCredentials,
}: {
  credential: Credential
  models: Model[]
  defaults: ModelDefaults | null
  allCredentials: Credential[]
}) {
  const { t } = useTranslation()
  const { testCredential, isPending: isTestPending, testResults } = useTestCredential()
  const { testModel, isPending: isModelTestPending, testingModelId, testResult: modelTestResult, testedModelName, clearResult: clearModelTestResult } = useTestModel()
  const deleteModel = useDeleteModel()
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [discoverOpen, setDiscoverOpen] = useState(false)
  // Full credential data needed for edit form
  const { data: fullCredential } = useCredential(editOpen ? credential.id : '')

  const linkedModels = models.filter(m => m.credential === credential.id)
  const activeTypes = new Set(linkedModels.map(m => m.type))
  const testResult = testResults[credential.id]

  // Extract translations used in model badge loops to avoid excessive Proxy accesses
  const testModelLabel = t('models.testModel')
  const deleteModelLabel = t('models.deleteModel')

  // Check which models are defaults
  const defaultSlots: Record<string, string> = {}
  if (defaults) {
    const slotMap: Record<string, string | null | undefined> = {
      'Chat': defaults.default_chat_model,
      'Transform': defaults.default_transformation_model,
      'Tools': defaults.default_tools_model,
      'Large Ctx': defaults.large_context_model,
      'Embedding': defaults.default_embedding_model,
      'TTS': defaults.default_text_to_speech_model,
      'STT': defaults.default_speech_to_text_model,
    }
    for (const [slot, modelId] of Object.entries(slotMap)) {
      if (modelId) defaultSlots[modelId] = slot
    }
  }

  return (
    <>
      <div className="border rounded-lg p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-medium truncate">{credential.name}</span>
            <div className="flex gap-1">
              {credential.modalities.map(mod => (
                <Badge
                  key={mod}
                  variant="secondary"
                  className={`text-[10px] gap-0.5 px-1 py-0 ${activeTypes.has(mod as ModelType) ? (TYPE_COLORS[mod as ModelType] || '') : TYPE_COLOR_INACTIVE}`}
                >
                  {TYPE_ICONS[mod as ModelType]}
                  <span className="hidden sm:inline">{TYPE_LABELS[mod as ModelType] || mod}</span>
                </Badge>
              ))}
            </div>
            {credential.has_api_key && (
              <Badge variant="outline" className="text-[10px]">
                <Key className="h-2.5 w-2.5 mr-0.5" />
                Key
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {testResult && (
              testResult.success
                ? <Check className="h-4 w-4 text-emerald-500" />
                : <X className="h-4 w-4 text-destructive" />
            )}
            <Button
              variant="ghost" size="sm"
              onClick={() => testCredential(credential.id)}
              disabled={isTestPending || !!credential.decryption_error}
              title={t('apiKeys.testConnection')}
            >
              {isTestPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plug className="h-4 w-4" />}
              <span className="hidden sm:inline text-xs">Test</span>
            </Button>
            <Button
              variant="ghost" size="sm"
              onClick={() => setDiscoverOpen(true)}
              disabled={!!credential.decryption_error}
              title={t('apiKeys.syncModels')}
            >
              <Bot className="h-4 w-4" />
              <span className="hidden sm:inline text-xs">Models</span>
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setEditOpen(true)} disabled={!!credential.decryption_error} title={t('common.edit')}>
              <Edit className="h-4 w-4" />
            </Button>
            {/* v0.7.198 — `title` is shown as a desktop tooltip but
                ignored by most screen readers; add `aria-label` so SR
                users hear "Delete" rather than just "button". */}
            <Button
              variant="ghost" size="sm"
              onClick={() => setDeleteOpen(true)}
              className="text-destructive hover:text-destructive hover:bg-destructive/10"
              title={t('common.delete')}
              aria-label={t('common.delete')}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {/* Decryption error warning */}
        {credential.decryption_error && (
          <Alert className="border-amber-500/50 bg-amber-50 dark:bg-amber-950/20">
            <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
            <AlertTitle className="text-amber-800 dark:text-amber-200">{t('apiKeys.decryptionError')}</AlertTitle>
            <AlertDescription className="text-amber-700 dark:text-amber-300 text-sm">
              {t('apiKeys.decryptionErrorDescription')}
            </AlertDescription>
          </Alert>
        )}

        {/* Linked models grouped by type */}
        {linkedModels.length > 0 && (
          <div className="space-y-1.5 pt-1">
            {(['language', 'embedding', 'text_to_speech', 'speech_to_text'] as ModelType[])
              .filter(type => linkedModels.some(m => m.type === type))
              .map(type => (
                <div key={type} className="flex items-start gap-1.5">
                  <Badge
                    variant="outline"
                    className={`text-[10px] gap-0.5 px-1 py-0 shrink-0 mt-0.5 ${TYPE_COLORS[type]}`}
                  >
                    {TYPE_ICONS[type]}
                    {TYPE_LABELS[type]}
                  </Badge>
                  <div className="flex flex-wrap gap-1">
                    {linkedModels.filter(m => m.type === type).map(model => {
                      const defaultSlot = defaultSlots[model.id]
                      return (
                        <Badge
                          key={model.id}
                          variant={defaultSlot ? 'default' : 'secondary'}
                          className="text-xs gap-1 pr-0.5 group/model"
                        >
                          {model.name}
                          {defaultSlot && <span className="ml-0.5 opacity-75">({defaultSlot})</span>}
                          <button
                            className="ml-0.5 opacity-0 group-hover/model:opacity-60 hover:!opacity-100 transition-opacity"
                            onClick={() => testModel(model.id, model.name)}
                            disabled={isModelTestPending && testingModelId === model.id}
                            title={testModelLabel}
                          >
                            {isModelTestPending && testingModelId === model.id
                              ? <Loader2 className="h-3 w-3 animate-spin" />
                              : <Plug className="h-3 w-3" />
                            }
                          </button>
                          <button
                            className="opacity-0 group-hover/model:opacity-60 hover:!opacity-100 hover:text-destructive transition-opacity"
                            onClick={() => deleteModel.mutate(model.id)}
                            title={deleteModelLabel}
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </Badge>
                      )
                    })}
                  </div>
                </div>
              ))}
          </div>
        )}


      </div>

      {/* Edit dialog */}
      {editOpen && (
        <CredentialFormDialog
          open={editOpen}
          onOpenChange={setEditOpen}
          provider={credential.provider}
          credential={fullCredential || credential}
        />
      )}

      {/* Delete dialog */}
      {deleteOpen && (
        <DeleteCredentialDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          credential={credential}
          allCredentials={allCredentials}
        />
      )}

      {/* Discover models dialog */}
      {discoverOpen && (
        <DiscoverModelsDialog
          open={discoverOpen}
          onOpenChange={setDiscoverOpen}
          credential={credential}
        />
      )}

      {/* Model test result dialog */}
      <ModelTestResultDialog
        open={modelTestResult !== null}
        onOpenChange={(open) => { if (!open) clearModelTestResult() }}
        result={modelTestResult}
        modelName={testedModelName}
      />
    </>
  )
}

// =============================================================================
// Provider Section (shows all credentials for a provider)
// =============================================================================

function ProviderSection({
  provider,
  credentials,
  models,
  defaults,
  allCredentials,
  encryptionReady,
}: {
  provider: string
  credentials: Credential[]
  models: Model[]
  defaults: ModelDefaults | null
  allCredentials: Credential[]
  encryptionReady: boolean
}) {
  const { t } = useTranslation()
  const [addOpen, setAddOpen] = useState(false)

  const displayName = PROVIDER_DISPLAY_NAMES[provider] || provider
  const modalities = PROVIDER_MODALITIES[provider] || ['language']
  const hasCredentials = credentials.length > 0

  // Models linked to any credential of this provider
  const providerModels = models.filter(m =>
    credentials.some(c => c.id === m.credential)
  )
  const activeTypes = new Set(providerModels.map(m => m.type))

  return (
    <Card className={!hasCredentials ? 'opacity-80' : undefined}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 flex-wrap">
            <CardTitle className="text-lg capitalize">{displayName}</CardTitle>
            <div className="flex items-center gap-1">
              {modalities.map((type) => (
                <Badge
                  key={type}
                  variant="secondary"
                  className={`text-xs gap-1 ${activeTypes.has(type) ? TYPE_COLORS[type] : TYPE_COLOR_INACTIVE}`}
                >
                  {TYPE_ICONS[type]}
                  <span className="hidden sm:inline">{TYPE_LABELS[type]}</span>
                </Badge>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {hasCredentials ? (
              <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-300">
                <Check className="mr-1 h-3 w-3" />
                {t('apiKeys.configured')}
              </Badge>
            ) : (
              <Badge variant="outline" className="text-muted-foreground border-dashed">
                <X className="mr-1 h-3 w-3" />
                {t('apiKeys.notConfigured')}
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {credentials.map(cred => (
          <CredentialItem
            key={cred.id}
            credential={cred}
            models={models}
            defaults={defaults}
            allCredentials={allCredentials}
          />
        ))}

        <Button
          variant="outline"
          size="sm"
          onClick={() => setAddOpen(true)}
          className="w-full gap-2"
          disabled={!encryptionReady}
        >
          <Plus className="h-4 w-4" />
          {t('apiKeys.addConfig')}
        </Button>
      </CardContent>

      {addOpen && (
        <CredentialFormDialog
          open={addOpen}
          onOpenChange={setAddOpen}
          provider={provider}
        />
      )}
    </Card>
  )
}

// =============================================================================
// Default Models Section
// =============================================================================

function DefaultModelSelectors({
  models,
  defaults,
}: {
  models: Model[]
  defaults: ModelDefaults
}) {
  const { t } = useTranslation()
  const updateDefaults = useUpdateModelDefaults()
  const autoAssign = useAutoAssignDefaults()
  const autoAssignCapability = useAutoAssignCapability()
  const { setValue, control } = useForm<ModelDefaults>({ defaultValues: defaults })
  const generatedId = useId()

  const [showEmbeddingDialog, setShowEmbeddingDialog] = useState(false)
  const [pendingEmbeddingChange, setPendingEmbeddingChange] = useState<{
    key: keyof ModelDefaults; value: string; oldModelId?: string; newModelId?: string
  } | null>(null)

  useEffect(() => {
    if (defaults) {
      Object.entries(defaults).forEach(([key, value]) => {
        setValue(key as keyof ModelDefaults, value)
      })
    }
  }, [defaults, setValue])

  // v0.8.37 — narrowed from `keyof ModelDefaults` to the string-typed
  // model-slot keys only. Pre-v0.8.37 every ModelDefaults field was
  // `string | null`, so `keyof` worked. The new v0.8.37 fields
  // (`auto_route_enabled: boolean`, `auto_route_provider_pref: string`)
  // belong to <SmartRoutingPanel>, not the slot dropdowns, but they
  // widened `keyof ModelDefaults` enough that `defaults[config.key]`
  // started yielding `string | true | undefined` and tripped tsc.
  // Narrowing here keeps the selector logic safely typed without
  // touching the SmartRoutingPanel.
  type ModelSlotKey =
    | 'default_chat_model'
    | 'default_transformation_model'
    | 'large_context_model'
    | 'default_text_to_speech_model'
    | 'default_speech_to_text_model'
    | 'default_embedding_model'
    | 'default_tools_model'
    | 'default_reasoning_model'
    | 'auto_route_cloud'
  interface DefaultConfig {
    key: ModelSlotKey
    label: string
    description: string
    modelType: ModelType
    required?: boolean
    id: string
  }

  const primaryConfigs: DefaultConfig[] = [
    { key: 'default_chat_model', label: t('models.chatModelLabel'), description: t('models.chatModelDesc'), modelType: 'language', required: true, id: `${generatedId}-chat` },
    { key: 'default_embedding_model', label: t('models.embeddingModelLabel'), description: t('models.embeddingModelDesc'), modelType: 'embedding', required: true, id: `${generatedId}-embed` },
    { key: 'default_text_to_speech_model', label: t('models.ttsModelLabel'), description: t('models.ttsModelDesc'), modelType: 'text_to_speech', id: `${generatedId}-tts` },
    { key: 'default_speech_to_text_model', label: t('models.sttModelLabel'), description: t('models.sttModelDesc'), modelType: 'speech_to_text', id: `${generatedId}-stt` },
  ]

  const advancedConfigs: DefaultConfig[] = [
    { key: 'default_transformation_model', label: t('models.transformationModelLabel'), description: t('models.transformationModelDesc'), modelType: 'language', required: true, id: `${generatedId}-transform` },
    { key: 'default_tools_model', label: t('models.toolsModelLabel'), description: t('models.toolsModelDesc'), modelType: 'language', id: `${generatedId}-tools` },
    { key: 'large_context_model', label: t('models.largeContextModelLabel'), description: t('models.largeContextModelDesc'), modelType: 'language', id: `${generatedId}-large` },
    // ONP v0.5 — 8th slot for slow-but-deep reasoning models (R1, gpt-oss, etc.)
    { key: 'default_reasoning_model', label: t('models.reasoningModelLabel'), description: t('models.reasoningModelDesc'), modelType: 'language', id: `${generatedId}-reasoning` },
    // v0.8.1 — dedicated cloud slot for DEEPER_NOTEBOOK_AUTO_ROUTE_CHAT smart routing.
    // Distinct from default_chat_model so the router doesn't silently route
    // oversized prompts to a locally-configured chat model (migration 18).
    { key: 'auto_route_cloud', label: t('models.autoRouteCloudLabel'), description: t('models.autoRouteCloudDesc'), modelType: 'language', id: `${generatedId}-auto-route-cloud` },
  ]

  const defaultConfigs = [...primaryConfigs, ...advancedConfigs]
  const watchedDefaultValues = useWatch({
    control,
    name: defaultConfigs.map(config => config.key),
  })
  const currentDefaultByKey = Object.fromEntries(
    defaultConfigs.map((config, index) => [config.key, watchedDefaultValues[index] || undefined])
  ) as Partial<Record<ModelSlotKey, string>>

  const handleChange = (key: keyof ModelDefaults, value: string) => {
    if (key === 'default_embedding_model') {
      const current = defaults[key]
      if (current && current !== value) {
        setPendingEmbeddingChange({ key, value, oldModelId: current, newModelId: value })
        setShowEmbeddingDialog(true)
        return
      }
    }
    updateDefaults.mutate({ [key]: value || null })
  }

  const handleConfirmEmbeddingChange = () => {
    if (pendingEmbeddingChange) {
      updateDefaults.mutate({ [pendingEmbeddingChange.key]: pendingEmbeddingChange.value || null })
      setPendingEmbeddingChange(null)
    }
  }

  const getModelsForType = (type: ModelType) => models.filter(m => m.type === type)

  const missingRequired = defaultConfigs
    .filter(c => {
      if (!c.required) return false
      const value = defaults[c.key]
      if (!value) return true
      return !models.filter(m => m.type === c.modelType).some(m => m.id === value)
    })
    .map(c => c.label)

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('models.defaultAssignments')}</CardTitle>
        <CardDescription>{t('models.defaultAssignmentsDesc')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {missingRequired.length > 0 && (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
              <span>{t('models.missingRequiredModels').replace('{models}', missingRequired.join(', '))}</span>
              <Button
                variant="outline" size="sm"
                onClick={() => autoAssign.mutate()}
                disabled={autoAssign.isPending}
                className="w-full gap-1.5 sm:w-auto sm:shrink-0"
              >
                {autoAssign.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
                {autoAssign.isPending ? t('models.autoAssigning') : t('models.autoAssign')}
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {/* ONP v0.5.2 — Re-evaluate model assignments using our local-model
            capability engine. Two buttons: a non-destructive variant that
            only fills empty slots, and a destructive "Reset & Re-evaluate"
            that wipes existing picks first. Useful after downloading a new
            model or bumping DEEPER_NOTEBOOK_CHAT_RAM_GB_CEILING. */}
        <div className="flex flex-wrap items-center gap-2 px-1 py-2 text-xs text-muted-foreground">
          <span>Local-model auto-assignment:</span>
          <Button
            variant="outline" size="sm"
            onClick={() => autoAssignCapability.mutate({ force: false })}
            disabled={autoAssignCapability.isPending}
            className="h-7 gap-1.5"
            title="Fill empty slots with the best matching local model. Keeps your existing picks."
          >
            {autoAssignCapability.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wand2 className="h-3 w-3" />}
            Fill empty slots
          </Button>
          <Button
            variant="ghost" size="sm"
            onClick={() => autoAssignCapability.mutate({ force: true })}
            disabled={autoAssignCapability.isPending}
            className="h-7 gap-1.5"
            title="Overwrite all slots with the best matching local model. Use after downloading a new model."
          >
            Reset & re-evaluate
          </Button>
        </div>

        {/* Primary models: Chat, Embedding, TTS, STT */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {primaryConfigs.map(config => {
            const available = getModelsForType(config.modelType)
            const currentValue = currentDefaultByKey[config.key]
            const isValid = currentValue && available.some(m => m.id === currentValue)

            return (
              <div key={config.key} className="min-w-0 max-w-full space-y-1">
                <Label htmlFor={config.id} className="text-xs">
                  {config.label}
                  {config.required && <span className="text-destructive ml-0.5">*</span>}
                </Label>
                <div className="flex min-w-0 max-w-full gap-1">
                  <Select
                    value={currentValue || ""}
                    onValueChange={(v) => handleChange(config.key, v)}
                  >
                    <SelectTrigger
                      id={config.id}
                      className={`h-8 w-full min-w-0 max-w-full text-xs ${config.required && !isValid && available.length > 0 ? 'border-destructive' : ''}`}
                    >
                      <SelectValue placeholder={
                        config.required && !isValid && available.length > 0
                          ? t('models.requiredModelPlaceholder')
                          : t('models.selectModelPlaceholder')
                      } />
                    </SelectTrigger>
                    <SelectContent>
                      {available.sort((a, b) => a.name.localeCompare(b.name)).map(model => (
                        <SelectItem key={model.id} value={model.id}>
                          <div className="flex items-center justify-between w-full">
                            <span>{model.name}</span>
                            <span className="text-xs text-muted-foreground ml-2">{formatModelProviderLabel(model)}</span>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {!config.required && currentValue && (
                    <Button variant="ghost" size="icon" aria-label={t('common.clear', { defaultValue: 'Clear' })} onClick={() => handleChange(config.key, "")} className="h-8 w-8 shrink-0">
                      <X className="h-3 w-3" />
                    </Button>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {/* Advanced models: Transformation, Tools, Large Context */}
        <div className="border-t pt-3">
          <p className="text-xs text-muted-foreground mb-3">{t('navigation.advanced')}</p>
            <div className="grid gap-3 sm:grid-cols-3">
              {advancedConfigs.map(config => {
                const available = getModelsForType(config.modelType)
                const currentValue = currentDefaultByKey[config.key]
                const isValid = currentValue && available.some(m => m.id === currentValue)

                return (
                  <div key={config.key} className="min-w-0 max-w-full space-y-1">
                    <Label htmlFor={config.id} className="text-xs">
                      {config.label}
                      {config.required && <span className="text-destructive ml-0.5">*</span>}
                    </Label>
                    <div className="flex min-w-0 max-w-full gap-1">
                      <Select
                        value={currentValue || ""}
                        onValueChange={(v) => handleChange(config.key, v)}
                      >
                        <SelectTrigger
                          id={config.id}
                          className={`h-8 w-full min-w-0 max-w-full text-xs ${config.required && !isValid && available.length > 0 ? 'border-destructive' : ''}`}
                        >
                          <SelectValue placeholder={
                            config.required && !isValid && available.length > 0
                              ? t('models.requiredModelPlaceholder')
                              : t('models.selectModelPlaceholder')
                          } />
                        </SelectTrigger>
                        <SelectContent>
                          {available.sort((a, b) => a.name.localeCompare(b.name)).map(model => (
                            <SelectItem key={model.id} value={model.id}>
                              <div className="flex items-center justify-between w-full">
                                <span>{model.name}</span>
                                <span className="text-xs text-muted-foreground ml-2">{formatModelProviderLabel(model)}</span>
                              </div>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {!config.required && currentValue && (
                        <Button variant="ghost" size="icon" aria-label={t('common.clear', { defaultValue: 'Clear' })} onClick={() => handleChange(config.key, "")} className="h-8 w-8 shrink-0">
                          <X className="h-3 w-3" />
                        </Button>
                      )}
                    </div>
                    <p className="text-[10px] text-muted-foreground leading-tight">{config.description}</p>
                  </div>
                )
              })}
            </div>
        </div>
      </CardContent>

      <EmbeddingModelChangeDialog
        open={showEmbeddingDialog}
        onOpenChange={(open) => { if (!open) { setPendingEmbeddingChange(null); setShowEmbeddingDialog(false) } }}
        onConfirm={handleConfirmEmbeddingChange}
        oldModelName={pendingEmbeddingChange?.oldModelId ? models.find(m => m.id === pendingEmbeddingChange.oldModelId)?.name : undefined}
        newModelName={pendingEmbeddingChange?.newModelId ? models.find(m => m.id === pendingEmbeddingChange.newModelId)?.name : undefined}
      />
    </Card>
  )
}

// =============================================================================
// Main Page
// =============================================================================

export default function ApiKeysPage() {
  const { t } = useTranslation()

  // Data
  const { data: credentials, isLoading: credentialsLoading } = useCredentials()
  const { data: models, isLoading: modelsLoading } = useModels()
  const { data: defaults, isLoading: defaultsLoading } = useModelDefaults()
  const { data: credentialStatus } = useCredentialStatus()
  const { data: envStatus } = useEnvStatus()

  const encryptionReady = credentialStatus?.encryption_configured ?? true

  // Group credentials by provider
  const credentialsByProvider = useMemo(() => {
    const grouped: Record<string, Credential[]> = {}
    for (const provider of ALL_PROVIDERS) {
      grouped[provider] = []
    }
    if (credentials) {
      for (const cred of credentials) {
        if (!grouped[cred.provider]) grouped[cred.provider] = []
        grouped[cred.provider].push(cred)
      }
    }
    return grouped
  }, [credentials])

  // Providers needing migration
  const providersToMigrate = useMemo(() => {
    if (!envStatus || !credentialStatus) return []
    const providers: string[] = []
    for (const provider in envStatus) {
      if (envStatus[provider] && credentialStatus.source[provider] === 'environment') {
        providers.push(provider)
      }
    }
    return providers
  }, [envStatus, credentialStatus])

  // Sort: configured providers first
  const sortedProviders = useMemo(() => {
    return [...ALL_PROVIDERS].sort((a, b) => {
      const aHas = (credentialsByProvider[a]?.length || 0) > 0 ? 1 : 0
      const bHas = (credentialsByProvider[b]?.length || 0) > 0 ? 1 : 0
      return bHas - aHas
    })
  }, [credentialsByProvider])

  // v0.7.35 — provider filter (search + status). With 16 providers
  // stacked, scrolling to find one is tedious. A 12-LOC filter
  // input + status-chip narrows the list.
  const [providerQuery, setProviderQuery] = useState('')
  const [providerStatusFilter, setProviderStatusFilter] = useState<
    'all' | 'configured' | 'env' | 'none'
  >('all')

  const filteredProviders = useMemo(() => {
    const q = providerQuery.trim().toLowerCase()
    return sortedProviders.filter((provider) => {
      // Name match (case-insensitive substring)
      if (q && !provider.toLowerCase().includes(q)) return false
      if (providerStatusFilter === 'all') return true
      const hasCred = (credentialsByProvider[provider]?.length || 0) > 0
      const hasEnv = envStatus?.[provider] === true
      if (providerStatusFilter === 'configured') return hasCred
      if (providerStatusFilter === 'env') return hasEnv && !hasCred
      if (providerStatusFilter === 'none') return !hasCred && !hasEnv
      return true
    })
  }, [sortedProviders, providerQuery, providerStatusFilter, credentialsByProvider, envStatus])

  const isLoading = credentialsLoading || modelsLoading || defaultsLoading

  if (isLoading) {
    return (
      <AppShell>
        <SystemRouteFrame route="/settings/api-keys">
          <div className="flex min-h-[60vh] items-center justify-center"><LoadingSpinner size="lg" /></div>
        </SystemRouteFrame>
      </AppShell>
    )
  }

  // v0.7.153 — Visual rhythm refresh (Models = edge-to-edge wide).
  // Pain points addressed (per user 2026-05-21):
  //   - Inputs/labels stacked too tightly  → page padding p-6 → px-6 py-10
  //     sm:px-8; outer space-y-6 → space-y-12 between major sections
  //   - Section headings don't separate cleanly → each major block is
  //     now in a <section> with a hairline border-t separator + an h2
  //     header (Defaults, Email Digests, Providers). The page reads
  //     as a series of clearly-delineated cards.
  //   - Buttons buried → the "Providers" section gets its own h2 plus
  //     a sticky-style row with filter input on the left and chip
  //     filters on the right with more breathing room
  //   - Title gets text-2xl font-bold → text-3xl font-semibold (lighter
  //     visual weight, more elegant per NotebookLM comparison)
  //   - Provider grid gap-4 → gap-5 so adjacent cards have visible
  //     separation instead of running into each other
  return (
    <AppShell>
      <SystemRouteFrame route="/settings/api-keys" description={t('apiKeys.description')}>
        <div className="space-y-12 rounded-lg bg-[var(--dn-folio-paper)] p-4 sm:p-6">
          {/* Header */}
          <header className="space-y-2">
            <h2 className="flex items-center gap-3 text-2xl font-semibold">
              <Key className="h-7 w-7" />
              {t('apiKeys.title')}
            </h2>
          </header>

          {/* Encryption warning */}
          {!encryptionReady && (
            <Alert className="border-red-500/50 bg-red-50 dark:bg-red-950/20">
              <ShieldAlert className="h-4 w-4 text-destructive" />
              <AlertTitle className="text-red-800 dark:text-red-200">{t('apiKeys.encryptionRequired')}</AlertTitle>
              <AlertDescription className="text-red-700 dark:text-red-300">
                <code className="text-xs bg-red-100 dark:bg-red-900/30 px-1 py-0.5 rounded">
                  {t('apiKeys.encryptionRequiredDescription')}
                </code>
              </AlertDescription>
            </Alert>
          )}

          {/* Migration banner */}
          {encryptionReady && <MigrationBanner providersToMigrate={providersToMigrate} />}

          {/* v0.8.36 — Osaurus auto-detect banner (Phase 1).
              Renders only when (a) no Osaurus credential exists yet AND
              (b) the backend probe finds Osaurus running on :1337. Mac
              users with Osaurus installed get a one-click connect. */}
          {encryptionReady && <OsaurusDetectionBanner credentials={credentials} />}

          {/* v0.7.153 — Defaults section: groups Default-Model selectors +
              the Reasoning-slot primer under one visual section. */}
          <section className="space-y-6">
            {/* v0.8.37 — Smart routing toggle (Phase 2). Lives above
                the default-model selectors so users see the routing
                story BEFORE the slot picker, not as an afterthought. */}
            {defaults && <SmartRoutingPanel defaults={defaults} />}

            {models && defaults && (
              <DefaultModelSelectors models={models} defaults={defaults} />
            )}

            {/* ONP v0.5 — Reasoning slot primer (shadow-layer component).
                Renders regardless of whether a model is assigned; it's an
                explainer + a status indicator combined. */}
            {defaults && (
              <ReasoningSlotCard
                assignedModel={
                  defaults.default_reasoning_model
                    ? models?.find(m => m.id === defaults.default_reasoning_model)?.name
                      ?? defaults.default_reasoning_model
                    : null
                }
              />
            )}
          </section>

          {/* v0.7.153 — Email Digests section gets a visible top divider so
              the user sees it as a discrete integration block rather than
              another cramped card stacked on the defaults above.
              Anchored ID matches the sidebar button's deep-link target. */}
          <section id="email-digests" className="border-t pt-12">
            <GmailIntegration />
          </section>

          {/* v0.7.153 — Providers section. The previous flat layout buried
              "16 providers in a list" under three different banners and a
              tight filter row. Now it's a labeled <section> with an h2
              and a roomier filter bar above the credential cards. */}
          <section className="border-t pt-12 space-y-6">
            <div className="space-y-2">
              <h2 className="text-xl font-semibold tracking-tight">Providers</h2>
              <p className="text-sm text-muted-foreground">
                Configure API credentials and registered models for each AI provider.
              </p>
            </div>

            {/* v0.7.35 — Provider filter row. 16 providers stacked
                vertically was hard to navigate; this gives both a
                substring search and a status-chip filter. */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative max-w-md flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={providerQuery}
                  onChange={(e) => setProviderQuery(e.target.value)}
                  placeholder="Filter providers…"
                  className="pl-9"
                  aria-label="Filter providers"
                />
              </div>
              <div className="flex flex-wrap gap-2 text-xs">
                {(['all', 'configured', 'env', 'none'] as const).map((status) => (
                  <Button
                    key={status}
                    size="sm"
                    variant={providerStatusFilter === status ? 'default' : 'outline'}
                    onClick={() => setProviderStatusFilter(status)}
                    className="h-8"
                  >
                    {status === 'all'
                      ? 'All'
                      : status === 'configured'
                        ? 'Has credential'
                        : status === 'env'
                          ? 'From env'
                          : 'Unconfigured'}
                  </Button>
                ))}
              </div>
            </div>
            {filteredProviders.length === 0 && (
              <p className="text-sm text-muted-foreground py-4">
                No providers match the filter.
              </p>
            )}

            {/* Provider Cards — bumped gap-4 → gap-5 (v0.7.153) so adjacent
                provider cards have visible breathing room instead of
                visually merging. */}
            <div className="grid gap-5">
              {filteredProviders.map(provider => (
                <ProviderSection
                  key={provider}
                  provider={provider}
                  credentials={credentialsByProvider[provider] || []}
                  models={models || []}
                  defaults={defaults || null}
                  allCredentials={credentials || []}
                  encryptionReady={encryptionReady}
                />
              ))}
            </div>
          </section>

          {/* Help link */}
          <div className="border-t pt-6">
            <a
              href="https://github.com/Antman1526/Deeper-Notebook/blob/main/docs/5-CONFIGURATION/ai-providers.md"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-primary hover:underline"
            >
              {t('apiKeys.learnMore')}
            </a>
          </div>
        </div>
      </SystemRouteFrame>
    </AppShell>
  )
}
