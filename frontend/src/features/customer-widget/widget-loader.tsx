import { createRoot } from 'react-dom/client'
import { Widget } from './Widget'
import { WidgetProvider } from './WidgetProvider'
import { WIDGET_STYLES } from './widget-styles'
import type { WidgetConfig, WidgetConfigResponse } from './widget-types'
import { DEFAULT_WIDGET_CONFIG } from './widget-types'
import { normalizeWidgetApiUrl } from './widgetApiUrl'

declare global {
  interface Window {
    RuhuWidget?: {
      init: (config: Partial<WidgetConfig> & { agentId: string }) => void
      open: () => void
      close: () => void
      destroy: () => void
    }
  }
}

class RuhuWidgetLoader {
  private root: ReturnType<typeof createRoot> | null = null
  private container: HTMLDivElement | null = null
  private shadowRoot: ShadowRoot | null = null

  public initFromScriptTag(cachedScriptTag?: HTMLScriptElement | null): void {
    const scriptTag =
      cachedScriptTag ??
      (document.currentScript as HTMLScriptElement | null) ??
      document.querySelector<HTMLScriptElement>('script[data-agent-id]')

    if (!scriptTag) {
      console.error('[RuhuWidget] Could not find script tag')
      return
    }

    const agentId = scriptTag.dataset.agentId || ''
    if (!agentId) {
      console.error('[RuhuWidget] data-agent-id is required')
      return
    }

    const apiUrl = scriptTag.dataset.apiUrl || this.inferApiUrl(scriptTag)
    void this.init({
      agentId,
      apiUrl: normalizeWidgetApiUrl(apiUrl),
      publishableKey: scriptTag.dataset.publishableKey || undefined,
      position: scriptTag.dataset.position as WidgetConfig['position'],
      primaryColor: scriptTag.dataset.primaryColor,
      accentColor: scriptTag.dataset.accentColor,
      buttonText: scriptTag.dataset.buttonText,
      companyName: scriptTag.dataset.companyName,
      companyLogo: scriptTag.dataset.companyLogo,
      welcomeMessage: scriptTag.dataset.welcomeMessage,
      autoOpen: scriptTag.dataset.autoOpen === 'true',
    })
  }

  private inferApiUrl(scriptTag: HTMLScriptElement): string {
    try {
      const url = new URL(scriptTag.src)
      return normalizeWidgetApiUrl(`${url.origin}/api/v1`)
    } catch {
      return normalizeWidgetApiUrl('/api/v1')
    }
  }

  public async init(partialConfig: Partial<WidgetConfig> & { agentId: string }): Promise<void> {
    if (!partialConfig.agentId) {
      console.error('[RuhuWidget] agentId is required')
      return
    }

    if (this.container) return

    const apiUrl = normalizeWidgetApiUrl(partialConfig.apiUrl || '/api/v1')
    const serverConfig = await this.fetchServerConfig(apiUrl, partialConfig.agentId, partialConfig.publishableKey).catch(() => null)

    const config: WidgetConfig = {
      agentId: partialConfig.agentId,
      apiUrl,
      mode: serverConfig?.widget_mode as WidgetConfig['mode'] || partialConfig.mode || DEFAULT_WIDGET_CONFIG.mode,
      position: partialConfig.position || serverConfig?.position || DEFAULT_WIDGET_CONFIG.position,
      primaryColor: partialConfig.primaryColor || serverConfig?.primary_color || DEFAULT_WIDGET_CONFIG.primaryColor,
      accentColor: partialConfig.accentColor || serverConfig?.accent_color || DEFAULT_WIDGET_CONFIG.accentColor,
      buttonText: partialConfig.buttonText || serverConfig?.button_text || DEFAULT_WIDGET_CONFIG.buttonText,
      companyName: partialConfig.companyName || serverConfig?.company_name || DEFAULT_WIDGET_CONFIG.companyName,
      companyLogo: partialConfig.companyLogo,
      welcomeMessage: partialConfig.welcomeMessage || serverConfig?.welcome_message || DEFAULT_WIDGET_CONFIG.welcomeMessage,
      autoOpen: partialConfig.autoOpen ?? DEFAULT_WIDGET_CONFIG.autoOpen,
      showPoweredBy: serverConfig?.show_powered_by ?? DEFAULT_WIDGET_CONFIG.showPoweredBy,
      features: serverConfig?.features || {},
      browserTaskRenderMode: serverConfig?.browser_task_render_mode || 'hidden',
      browserTaskApprovalMode: serverConfig?.browser_task_approval_mode || 'operator_only',
      browserTaskShowLiveSnapshot: serverConfig?.browser_task_show_live_snapshot || false,
      browserTaskMaxVisibleArtifacts: serverConfig?.browser_task_max_visible_artifacts ?? 3,
    }

    this.render(config)
  }

  private async fetchServerConfig(apiUrl: string, agentId: string, publishableKey?: string): Promise<WidgetConfigResponse> {
    const headers: Record<string, string> = {}
    if (publishableKey) {
      headers['X-Widget-Key'] = publishableKey
    }
    const response = await fetch(
      `${normalizeWidgetApiUrl(apiUrl)}/public/widget/config?agent_id=${encodeURIComponent(agentId)}`,
      {
        credentials: 'omit',
        headers,
      },
    )
    if (!response.ok) {
      throw new Error('Failed to load widget config')
    }
    return response.json()
  }

  private render(config: WidgetConfig): void {
    this.container = document.createElement('div')
    this.container.id = 'ruhu-widget-container'
    this.shadowRoot = this.container.attachShadow({ mode: 'open' })

    const style = document.createElement('style')
    style.textContent = WIDGET_STYLES
    this.shadowRoot.appendChild(style)

    const rootEl = document.createElement('div')
    this.shadowRoot.appendChild(rootEl)
    document.body.appendChild(this.container)

    this.root = createRoot(rootEl)
    this.root.render(
      <WidgetProvider config={config}>
        <Widget />
      </WidgetProvider>,
    )
  }

  public open(): void {
    window.dispatchEvent(new CustomEvent('ruhu-widget-open'))
  }

  public close(): void {
    window.dispatchEvent(new CustomEvent('ruhu-widget-close'))
  }

  public destroy(): void {
    this.root?.unmount()
    this.container?.remove()
    this.root = null
    this.container = null
    this.shadowRoot = null
  }
}

const loader = new RuhuWidgetLoader()

window.RuhuWidget = {
  init: (config) => void loader.init(config),
  open: () => loader.open(),
  close: () => loader.close(),
  destroy: () => loader.destroy(),
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => loader.initFromScriptTag(), { once: true })
} else {
  loader.initFromScriptTag()
}
