import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'

type Page<T> = { items: T[]; total: number; limit: number; offset: number }
type Project = { id: string; name: string; description: string | null; created_at: string }
type Environment = { id: string; project_id: string; name: string; description: string | null }
type Source = { id: string; environment_id: string; name: string; provider: string; path: string; enabled: boolean }
type Resource = { id: string; source_id: string; address: string; resource_type: string; provider_id: string | null; last_seen_at: string | null }
type Scan = { id: string; source_id: string; status: string; trigger_type: string; started_at: string | null; completed_at: string | null; resource_count: number; created_at: string }
type Finding = { id: string; scan_id: string; source_id: string; resource_address: string; resource_type: string; severity: string; status: string; recommended_action: string | null; last_seen_at: string }
type AuditLog = { id: string; action: string; entity_type: string; entity_id: string; created_at: string; details: Record<string, unknown> }
type DashboardData = { projects: Project[]; environments: Environment[]; sources: Source[]; resources: Resource[]; scans: Scan[]; findings: Finding[]; audit: AuditLog[] }

const emptyData: DashboardData = { projects: [], environments: [], sources: [], resources: [], scans: [], findings: [], audit: [] }
const navItems = [
  { id: 'overview', label: 'Overview', icon: '◫' },
  { id: 'projects', label: 'Projects', icon: '▦' },
  { id: 'environments', label: 'Environments', icon: '◇' },
  { id: 'resources', label: 'Resources', icon: '▤' },
  { id: 'findings', label: 'Drift findings', icon: '⌁' },
  { id: 'scans', label: 'Scan history', icon: '◷' },
  { id: 'audit', label: 'Audit log', icon: '≡' },
] as const
type Section = (typeof navItems)[number]['id']

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = await response.json() as { detail?: { message?: string } | string }
      message = typeof body.detail === 'string' ? body.detail : body.detail?.message ?? message
    } catch { /* Keep the status message when the response has no JSON body. */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

async function loadDashboard(): Promise<DashboardData> {
  const projectsPage = await api<Page<Project>>('/projects?limit=100')
  const projects = projectsPage.items
  const environmentPages = await Promise.all(projects.map((project) => api<Page<Environment>>(`/projects/${project.id}/environments?limit=100`)))
  const environments = environmentPages.flatMap((page) => page.items)
  const sourcePages = await Promise.all(environments.map((environment) => api<Page<Source>>(`/environments/${environment.id}/sources?limit=100`)))
  const sources = sourcePages.flatMap((page) => page.items)
  const [resourcePages, scanPages, findingPages, auditPage] = await Promise.all([
    Promise.all(sources.map((source) => api<Page<Resource>>(`/sources/${source.id}/resources?limit=100`))),
    Promise.all(sources.map((source) => api<Page<Scan>>(`/sources/${source.id}/scans?limit=100`))),
    Promise.all(sources.map((source) => api<Page<Finding>>(`/findings?source_id=${source.id}&limit=100`))),
    api<Page<AuditLog>>('/audit-logs?limit=100'),
  ])
  return {
    projects, environments, sources,
    resources: resourcePages.flatMap((page) => page.items),
    scans: scanPages.flatMap((page) => page.items).sort((a, b) => b.created_at.localeCompare(a.created_at)),
    findings: findingPages.flatMap((page) => page.items).sort((a, b) => b.last_seen_at.localeCompare(a.last_seen_at)),
    audit: auditPage.items,
  }
}

function App() {
  const [section, setSection] = useState<Section>('overview')
  const [data, setData] = useState<DashboardData>(emptyData)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [showProjectForm, setShowProjectForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try { setData(await loadDashboard()) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not load DriftWatch data') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const filtered = useMemo(() => {
    const value = query.trim().toLowerCase()
    if (!value) return data
    const matchingIds = new Set(data.sources.filter((source) => `${source.name} ${source.provider} ${source.path}`.toLowerCase().includes(value)).map((source) => source.id))
    const matchingProjectIds = new Set(data.projects.filter((project) => `${project.name} ${project.description ?? ''}`.toLowerCase().includes(value)).map((project) => project.id))
    const matchingEnvironmentIds = new Set(data.environments.filter((environment) => `${environment.name} ${environment.description ?? ''}`.toLowerCase().includes(value) || matchingProjectIds.has(environment.project_id)).map((environment) => environment.id))
    const sourceIds = new Set([...matchingIds, ...data.sources.filter((source) => matchingEnvironmentIds.has(source.environment_id)).map((source) => source.id)])
    return {
      ...data,
      projects: data.projects.filter((item) => `${item.name} ${item.description ?? ''}`.toLowerCase().includes(value)),
      environments: data.environments.filter((item) => `${item.name} ${item.description ?? ''}`.toLowerCase().includes(value) || matchingProjectIds.has(item.project_id)),
      sources: data.sources.filter((item) => sourceIds.has(item.id)),
      resources: data.resources.filter((item) => `${item.address} ${item.resource_type}`.toLowerCase().includes(value) || sourceIds.has(item.source_id)),
      scans: data.scans.filter((item) => item.status.toLowerCase().includes(value) || sourceIds.has(item.source_id)),
      findings: data.findings.filter((item) => `${item.resource_address} ${item.severity} ${item.status}`.toLowerCase().includes(value) || sourceIds.has(item.source_id)),
      audit: data.audit.filter((item) => `${item.action} ${item.entity_type}`.toLowerCase().includes(value)),
    }
  }, [data, query])

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setSaving(true)
    setNotice('')
    try {
      await api<Project>('/projects', { method: 'POST', body: JSON.stringify({ name: String(form.get('name') ?? ''), description: String(form.get('description') ?? '') || null }) })
      setShowProjectForm(false)
      setNotice('Project created.')
      await refresh()
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : 'Project could not be created.') }
    finally { setSaving(false) }
  }

  const title = navItems.find((item) => item.id === section)?.label ?? 'Overview'
  const activeFindings = data.findings.filter((item) => item.status === 'open' || item.status === 'active')
  const criticalCount = activeFindings.filter((item) => item.severity.toLowerCase() === 'critical').length

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#overview" onClick={() => setSection('overview')}>
          <span className="brand-mark">D</span><span>Drift<span className="brand-light">Watch</span></span>
        </a>
        <div className="workspace-label">WORKSPACE</div>
        <button className="workspace-switch"><span className="workspace-avatar">DW</span><span className="workspace-name">Infrastructure team<small>Free workspace</small></span><span className="chevron">⌄</span></button>
        <nav className="nav-list" aria-label="Main navigation">
          {navItems.map((item) => <button key={item.id} className={`nav-item ${section === item.id ? 'selected' : ''}`} onClick={() => setSection(item.id)}><span className="nav-icon">{item.icon}</span>{item.label}{item.id === 'findings' && activeFindings.length > 0 && <span className="nav-count">{activeFindings.length}</span>}</button>)}
        </nav>
        <div className="sidebar-bottom"><div className="api-indicator"><span className={`status-dot ${error ? 'offline' : ''}`} /><span>{error ? 'API unavailable' : 'API connected'}</span><button title="Refresh data" onClick={() => void refresh()} aria-label="Refresh data">↻</button></div><div className="user-profile"><div className="user-avatar">A</div><span>Administrator<small>Local session</small></span><span className="chevron">···</span></div></div>
      </aside>

      <main className="main-area">
        <header className="topbar"><div className="breadcrumbs"><span>Workspace</span><span className="crumb-separator">/</span><strong>{title}</strong></div><div className="topbar-actions"><label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search anything..." aria-label="Search dashboard" /><kbd>⌘ K</kbd></label><button className="icon-button" title="Notifications" aria-label="Notifications">♧<i /></button><div className="top-avatar">A</div></div></header>
        <div className="page-content">
          <div className="page-heading"><div><div className="eyebrow">INFRASTRUCTURE OBSERVABILITY</div><h1>{section === 'overview' ? 'Good morning' : title}<span>{section === 'overview' ? ', Abhay' : ''}</span></h1><p>{section === 'overview' ? 'Here’s what’s happening across your infrastructure today.' : `Explore and manage your ${title.toLowerCase()} across DriftWatch.`}</p></div><div className="heading-actions"><button className="button button-quiet" onClick={() => void refresh()} disabled={loading}><span className={loading ? 'spin' : ''}>↻</span> Refresh</button><button className="button button-primary" onClick={() => setShowProjectForm(true)}>＋ <span>Add project</span></button></div></div>
          {error && <div className="alert alert-error"><span>!</span><div><strong>Could not connect to the API</strong><p>{error}. Check that the backend is running, then refresh.</p></div><button onClick={() => void refresh()}>Try again</button></div>}
          {notice && <div className="alert alert-notice"><span>✓</span>{notice}<button onClick={() => setNotice('')} aria-label="Dismiss">×</button></div>}
          {loading && !error ? <div className="loading-state"><span className="loader" />Loading infrastructure data…</div> : <>
            {section === 'overview' && <Overview data={filtered} activeFindings={activeFindings} criticalCount={criticalCount} onNavigate={setSection} onAdd={() => setShowProjectForm(true)} />}
            {section === 'projects' && <Projects data={filtered} onAdd={() => setShowProjectForm(true)} />}
            {section === 'environments' && <Environments data={filtered} />}
            {section === 'resources' && <Resources data={filtered} />}
            {section === 'findings' && <Findings data={filtered} />}
            {section === 'scans' && <Scans data={filtered} />}
            {section === 'audit' && <Audit data={filtered} />}
          </>}
          <footer className="page-footer"><span>DriftWatch <b>·</b> Infrastructure drift detection</span><span>Data refreshes when you open this dashboard <b>·</b> <a href="/docs" target="_blank" rel="noreferrer">API documentation ↗</a></span></footer>
        </div>
      </main>

      {showProjectForm && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowProjectForm(false) }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="project-modal-title"><div className="modal-heading"><div><div className="eyebrow">GET STARTED</div><h2 id="project-modal-title">Add a project</h2><p>Create a project to organize your environments and infrastructure sources.</p></div><button className="icon-button" onClick={() => setShowProjectForm(false)} aria-label="Close">×</button></div><form onSubmit={(event) => void createProject(event)}><label className="field-label">Project name<input autoFocus name="name" required maxLength={120} placeholder="Production infrastructure" /></label><label className="field-label">Description <span>Optional</span><textarea name="description" maxLength={4000} rows={3} placeholder="A short description of this project" /></label><div className="modal-actions"><button type="button" className="button button-quiet" onClick={() => setShowProjectForm(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={saving}>{saving ? 'Creating…' : 'Create project'}</button></div></form></section></div>}
    </div>
  )
}

function Overview({ data, activeFindings, criticalCount, onNavigate, onAdd }: { data: DashboardData; activeFindings: Finding[]; criticalCount: number; onNavigate: (section: Section) => void; onAdd: () => void }) {
  const completedScans = data.scans.filter((scan) => scan.status === 'completed').length
  return <>
    <div className="metrics-grid">
      <Metric label="Projects" value={data.projects.length} icon="▦" tone="blue" foot={`${data.environments.length} environments`} />
      <Metric label="Tracked resources" value={data.resources.length} icon="▤" tone="violet" foot={`${data.sources.length} infrastructure sources`} />
      <Metric label="Open drift findings" value={activeFindings.length} icon="⌁" tone="amber" foot={criticalCount ? `${criticalCount} critical` : data.scans.length ? 'No critical findings' : 'Waiting for first scan'} />
      <Metric label="Successful scans" value={completedScans} icon="◷" tone="green" foot={data.scans.length ? `${data.scans.length} total scans` : 'No scans recorded yet'} />
    </div>
    <div className="overview-grid"><section className="panel main-panel"><PanelHeading title="Recent drift findings" subtitle="Infrastructure changes detected across your sources" action="View all findings" onAction={() => onNavigate('findings')} />{activeFindings.length ? <FindingTable findings={activeFindings.slice(0, 5)} sources={data.sources} /> : <EmptyState icon="⌁" title={data.sources.length ? 'Everything looks in sync' : 'No drift findings yet'} description={data.sources.length ? 'There are no open findings in the latest scans.' : 'Connect an infrastructure source and run a scan to see drift here.'} action={!data.projects.length ? 'Create your first project' : undefined} onAction={onAdd} />}</section><section className="panel activity-panel"><PanelHeading title="Recent activity" subtitle="Latest changes in your workspace" action="View audit log" onAction={() => onNavigate('audit')} />{data.audit.length ? <ActivityList items={data.audit.slice(0, 6)} /> : <EmptyState compact icon="≡" title="No activity recorded" description="Project and configuration changes will appear here." />}</section></div>
    <section className="panel sources-panel"><PanelHeading title="Infrastructure sources" subtitle="Your connected environments and providers" action="View projects" onAction={() => onNavigate('projects')} /><SourceOverview data={data} onAdd={onAdd} /></section>
  </>
}

function Metric({ label, value, icon, tone, foot }: { label: string; value: number; icon: string; tone: string; foot: string }) { return <div className="metric-card"><div className="metric-top"><span>{label}</span><span className={`metric-icon ${tone}`}>{icon}</span></div><div className="metric-value">{value.toLocaleString()}</div><div className="metric-foot"><span className="metric-foot-dot" />{foot}</div></div> }
function PanelHeading({ title, subtitle, action, onAction }: { title: string; subtitle: string; action?: string; onAction?: () => void }) { return <div className="panel-heading"><div><h2>{title}</h2><p>{subtitle}</p></div>{action && <button className="text-action" onClick={onAction}>{action} <span>→</span></button>}</div> }
function EmptyState({ icon, title, description, action, onAction, compact = false }: { icon: string; title: string; description: string; action?: string; onAction?: () => void; compact?: boolean }) { return <div className={`empty-state ${compact ? 'compact' : ''}`}><span className="empty-icon">{icon}</span><strong>{title}</strong><p>{description}</p>{action && <button className="button button-primary button-small" onClick={onAction}>＋ {action}</button>}</div> }

function Projects({ data, onAdd }: { data: DashboardData; onAdd: () => void }) {
  return <section className="panel table-panel"><PanelHeading title="All projects" subtitle={`${data.projects.length} project${data.projects.length === 1 ? '' : 's'} in this workspace`} /><div className="table-wrap">{data.projects.length ? <table><thead><tr><th>Project</th><th>Environments</th><th>Sources</th><th>Resources</th><th>Created</th><th>Status</th></tr></thead><tbody>{data.projects.map((project) => { const envs = data.environments.filter((env) => env.project_id === project.id); const sources = data.sources.filter((source) => envs.some((env) => env.id === source.environment_id)); return <tr key={project.id}><td><div className="primary-cell"><span className="project-avatar">{project.name.slice(0, 1).toUpperCase()}</span><span><strong>{project.name}</strong><small>{project.description || 'No description'}</small></span></div></td><td>{envs.length}</td><td>{sources.length}</td><td>{data.resources.filter((resource) => sources.some((source) => source.id === resource.source_id)).length}</td><td>{formatDate(project.created_at)}</td><td><span className="pill pill-green">Active</span></td></tr> })}</tbody></table> : <EmptyState icon="▦" title="No projects yet" description="Create your first project to start organizing infrastructure environments and sources." action="Create a project" onAction={onAdd} />}</div></section>
}

function Environments({ data }: { data: DashboardData }) {
  return <section className="panel table-panel"><PanelHeading title="Environments" subtitle="Deployment environments grouped by project" />{data.environments.length ? <div className="table-wrap"><table><thead><tr><th>Environment</th><th>Project</th><th>Sources</th><th>Tracked resources</th></tr></thead><tbody>{data.environments.map((env) => { const sources = data.sources.filter((source) => source.environment_id === env.id); return <tr key={env.id}><td><div className="primary-cell"><span className="env-icon">◇</span><span><strong>{env.name}</strong><small>{env.description || 'Environment'}</small></span></div></td><td>{data.projects.find((project) => project.id === env.project_id)?.name ?? '—'}</td><td>{sources.length}</td><td>{data.resources.filter((resource) => sources.some((source) => source.id === resource.source_id)).length}</td></tr> })}</tbody></table></div> : <EmptyState icon="◇" title="No environments found" description="Environments are created inside a project and can contain multiple infrastructure sources." />}</section>
}

function Resources({ data }: { data: DashboardData }) {
  return <section className="panel table-panel"><PanelHeading title="Tracked resources" subtitle={`${data.resources.length} resources observed in the latest saved state`} />{data.resources.length ? <div className="table-wrap"><table><thead><tr><th>Resource</th><th>Type</th><th>Source</th><th>Provider ID</th><th>Last observed</th></tr></thead><tbody>{data.resources.map((resource) => <tr key={resource.id}><td><code className="resource-address">{resource.address}</code></td><td><span className="type-pill">{resource.resource_type}</span></td><td>{data.sources.find((source) => source.id === resource.source_id)?.name ?? '—'}</td><td><span className="muted">{resource.provider_id || '—'}</span></td><td>{resource.last_seen_at ? formatDate(resource.last_seen_at) : '—'}</td></tr>)}</tbody></table></div> : <EmptyState icon="▤" title="No resources tracked yet" description="Resources appear here after a source has been scanned and its state is saved." />}</section>
}

function Findings({ data }: { data: DashboardData }) {
  return <section className="panel table-panel"><PanelHeading title="Drift findings" subtitle="Changes detected between declared and observed infrastructure" />{data.findings.length ? <FindingTable findings={data.findings} sources={data.sources} /> : <EmptyState icon="⌁" title="No findings to show" description="When a scan detects infrastructure drift, findings and recommended actions will appear here." />}</section>
}

function FindingTable({ findings, sources }: { findings: Finding[]; sources: Source[] }) {
  return <div className="table-wrap"><table><thead><tr><th>Resource</th><th>Severity</th><th>Finding status</th><th>Source</th><th>Last detected</th></tr></thead><tbody>{findings.map((finding) => <tr key={finding.id}><td><div className="primary-cell"><span className="finding-symbol">⌁</span><span><strong>{finding.resource_address}</strong><small>{finding.resource_type}</small></span></div></td><td><span className={`severity severity-${finding.severity.toLowerCase()}`}><i />{capitalize(finding.severity)}</span></td><td><span className={`pill ${finding.status === 'open' || finding.status === 'active' ? 'pill-amber' : 'pill-gray'}`}>{capitalize(finding.status)}</span></td><td>{sources.find((source) => source.id === finding.source_id)?.name ?? '—'}</td><td>{formatDate(finding.last_seen_at)}</td></tr>)}</tbody></table></div>
}

function Scans({ data }: { data: DashboardData }) {
  return <section className="panel table-panel"><PanelHeading title="Scan history" subtitle="Recent infrastructure scans and their outcomes" />{data.scans.length ? <div className="table-wrap"><table><thead><tr><th>Scan</th><th>Source</th><th>Trigger</th><th>Resources</th><th>Started</th><th>Status</th></tr></thead><tbody>{data.scans.map((scan) => <tr key={scan.id}><td><code className="id-code">{scan.id.slice(0, 8)}</code></td><td>{data.sources.find((source) => source.id === scan.source_id)?.name ?? '—'}</td><td>{capitalize(scan.trigger_type)}</td><td>{scan.resource_count}</td><td>{formatDate(scan.started_at ?? scan.created_at)}</td><td><span className={`pill ${scan.status === 'completed' ? 'pill-green' : scan.status === 'failed' ? 'pill-red' : 'pill-blue'}`}>{capitalize(scan.status)}</span></td></tr>)}</tbody></table></div> : <EmptyState icon="◷" title="No scans have run yet" description="Scan history will appear here once a source is scanned. Scan execution is being added in a later phase." />}</section>
}

function Audit({ data }: { data: DashboardData }) { return <section className="panel table-panel"><PanelHeading title="Audit log" subtitle="A record of changes made in this workspace" />{data.audit.length ? <div className="table-wrap"><table><thead><tr><th>Action</th><th>Entity</th><th>Details</th><th>Time</th></tr></thead><tbody>{data.audit.map((item) => <tr key={item.id}><td><span className="audit-action"><i />{item.action}</span></td><td><span className="type-pill">{item.entity_type}</span></td><td>{String(item.details.name ?? '—')}</td><td>{formatDate(item.created_at)}</td></tr>)}</tbody></table></div> : <EmptyState icon="≡" title="Your audit log is empty" description="Changes made through the API, such as creating a project, will be recorded here." />}</section> }

function SourceOverview({ data, onAdd }: { data: DashboardData; onAdd: () => void }) {
  if (!data.sources.length) return <div className="source-empty"><div className="source-empty-copy"><span className="provider-icon">＋</span><span><strong>Connect your first source</strong><small>Create a project, add an environment, then connect an OpenTofu, Docker, or Kubernetes source.</small></span></div><button className="button button-secondary" onClick={onAdd}>Add project <span>→</span></button></div>
  return <div className="source-cards">{data.sources.slice(0, 4).map((source) => { const env = data.environments.find((item) => item.id === source.environment_id); const project = data.projects.find((item) => item.id === env?.project_id); const count = data.resources.filter((resource) => resource.source_id === source.id).length; const drift = data.findings.filter((item) => item.source_id === source.id && (item.status === 'open' || item.status === 'active')).length; return <div className="source-card" key={source.id}><div className="source-card-top"><Provider provider={source.provider} /><span className={`pill ${source.enabled ? 'pill-green' : 'pill-gray'}`}>{source.enabled ? 'Connected' : 'Disabled'}</span></div><strong>{source.name}</strong><small>{project?.name ?? 'Project'} / {env?.name ?? 'Environment'}</small><div className="source-card-bottom"><span>{count} resources</span><span>{drift} open findings</span></div></div> })}</div>
}

function Provider({ provider }: { provider: string }) { const labels: Record<string, string> = { opentofu: 'TF', docker: 'DK', kubernetes: 'K8' }; return <span className={`provider-icon provider-${provider}`}>{labels[provider] ?? provider.slice(0, 2).toUpperCase()}</span> }
function ActivityList({ items }: { items: AuditLog[] }) { return <div className="activity-list">{items.map((item) => <div className="activity-item" key={item.id}><span className="activity-dot" /><div><strong>{item.action.replace('.', ' ')}</strong><p>{item.entity_type} · {String(item.details.name ?? item.entity_id.slice(0, 8))}</p><small>{formatRelative(item.created_at)}</small></div></div>)}</div> }
function formatDate(value: string | null) { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—' }
function formatRelative(value: string) { const elapsed = Date.now() - new Date(value).getTime(); if (elapsed < 60_000) return 'Just now'; if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)} min ago`; if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)} hr ago`; return formatDate(value) }
function capitalize(value: string) { return value ? value[0].toUpperCase() + value.slice(1) : 'Unknown' }

export default App
