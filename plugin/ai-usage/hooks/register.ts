import type { CoreEngineInterface, Register } from 'claude-code'

type Api = CoreEngineInterface
type Row = { label: string; remain: number; resetMs?: number }
type Result = { rows?: Row[]; error?: string; short?: string; stale?: string; retry?: number }

const REFRESH_MS = 60_000
const MIN_INTERVAL: Record<string, number> = { Claude: 180_000, Codex: 120_000, Gemini: 180_000 }
const NAMES = ['Claude', 'Codex', 'Gemini']
const ABBR: Record<string, string> = { Claude: 'C', Codex: 'X', Gemini: 'G' }

// ---------- 공통 ----------
async function home($: Api): Promise<string> {
  return (await $.env.get('HOME')) ?? (await $.env.get('USERPROFILE')) ?? ''
}

async function readJson($: Api, path: string): Promise<any> {
  return JSON.parse(await $.fs.read(path))
}

async function getJson(
  $: Api,
  url: string,
  token: string,
  extra: Record<string, string> = {},
  body?: unknown,
): Promise<any> {
  const res = await $.http.fetch(url, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...extra,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    throw Object.assign(new Error(`HTTP ${res.status}`), {
      status: res.status,
      retryAfter: Number(res.headers['retry-after'] ?? 0),
    })
  }
  return JSON.parse(res.text)
}

function fail(e: any): Result {
  const status = e?.status as number | undefined
  if (status === 401 || status === 403) {
    return { error: '토큰 만료 - 해당 CLI 실행 시 자동 갱신', short: '만료', retry: 120_000 }
  }
  if (status === 429) {
    return {
      error: '요청 제한(429) - 잠시 후 자동 재시도',
      short: '대기',
      retry: Math.max((e.retryAfter || 0) * 1000, 300_000),
    }
  }
  if (status) return { error: `서버 오류 HTTP ${status}`, short: '오류', retry: 120_000 }
  return { error: String(e?.message ?? e).slice(0, 60), short: '오류', retry: 120_000 }
}

// ---------- 조회 ----------
async function fetchClaude($: Api): Promise<Result> {
  try {
    const h = await home($)
    let raw: string | undefined
    try {
      raw = await $.fs.read(`${h}/.claude/.credentials.json`)
    } catch {
      // macOS: Claude Code 는 로그인 정보를 키체인에 저장
      const r = await $.process.run(['security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'])
      if (r.exitCode === 0) raw = r.stdout
    }
    if (!raw) return { error: 'Claude Code 로그인 정보 없음', short: '로그인' }
    const oauth = JSON.parse(raw).claudeAiOauth
    if (oauth.expiresAt && oauth.expiresAt < Date.now()) {
      return { error: '토큰 만료 - Claude Code 실행 시 자동 갱신', short: '만료', retry: 60_000 }
    }
    const d = await getJson($, 'https://api.anthropic.com/api/oauth/usage', oauth.accessToken, {
      'anthropic-beta': 'oauth-2025-04-20',
    })
    const rows: Row[] = []
    for (const [label, key] of [
      ['5시간', 'five_hour'],
      ['주간', 'seven_day'],
    ]) {
      const v = d[key]
      if (v) rows.push({ label, remain: Math.max(0, 100 - Number(v.utilization)), resetMs: Date.parse(v.resets_at) })
    }
    return { rows }
  } catch (e) {
    return fail(e)
  }
}

async function fetchCodex($: Api): Promise<Result> {
  try {
    const h = await home($)
    const t = (await readJson($, `${h}/.codex/auth.json`)).tokens
    const d = await getJson(
      $,
      'https://chatgpt.com/backend-api/wham/usage',
      t.access_token,
      t.account_id ? { 'chatgpt-account-id': t.account_id } : {},
    )
    const rows: Row[] = []
    for (const [label, key] of [
      ['5시간', 'primary_window'],
      ['주간', 'secondary_window'],
    ]) {
      const w = d.rate_limit?.[key]
      if (w) rows.push({ label, remain: Math.max(0, 100 - Number(w.used_percent)), resetMs: w.reset_at * 1000 })
    }
    return rows.length ? { rows } : { error: 'Codex 사용량 응답 없음', short: '오류' }
  } catch (e) {
    return fail(e)
  }
}

async function fetchGemini($: Api): Promise<Result> {
  try {
    const h = await home($)
    let c: any
    try {
      c = await readJson($, `${h}/.gemini/oauth_creds.json`)
    } catch {
      return { error: 'Gemini CLI 로그인 필요 (gemini 실행 후 Google 로그인)', short: '로그인' }
    }
    if (c.expiry_date && c.expiry_date < Date.now()) {
      return { error: '토큰 만료 - gemini 를 한 번 실행하세요', short: '만료', retry: 120_000 }
    }
    const base = 'https://cloudcode-pa.googleapis.com/v1internal'
    const meta = { metadata: { ideType: 'IDE_UNSPECIFIED', platform: 'PLATFORM_UNSPECIFIED', pluginType: 'GEMINI' } }
    let project: any =
      (await $.env.get('GOOGLE_CLOUD_PROJECT')) ??
      (await getJson($, `${base}:loadCodeAssist`, c.access_token, {}, meta)).cloudaicompanionProject
    if (project && typeof project === 'object') project = project.id
    const q = await getJson($, `${base}:retrieveUserQuota`, c.access_token, {}, project ? { project } : {})
    const groups: Record<string, { frac: number; reset?: string }> = {}
    for (const b of q.buckets ?? []) {
      const m = String(b.modelId ?? '').toLowerCase()
      const g = m.includes('pro') ? 'Pro' : m.includes('flash') ? 'Flash' : undefined
      if (!g || b.remainingFraction == null) continue
      const frac = Number(b.remainingFraction)
      if (!groups[g] || frac < groups[g].frac) groups[g] = { frac, reset: b.resetTime }
    }
    const rows: Row[] = []
    for (const g of ['Pro', 'Flash']) {
      const v = groups[g]
      if (v) rows.push({ label: `${g} 일일`, remain: v.frac * 100, resetMs: v.reset ? Date.parse(v.reset) : undefined })
    }
    return rows.length ? { rows } : { error: '쿼터 정보 없음', short: '오류' }
  } catch (e) {
    return fail(e)
  }
}

// ---------- 캐시/백오프: 마지막 정상값 유지, 최소 간격 준수 ----------
type State = { t: number; until: number; view?: Result; good?: Result }
const state: Record<string, State> = {}

async function cached($: Api, name: string): Promise<Result> {
  const st = (state[name] ??= { t: 0, until: 0 })
  const now = Date.now()
  if (st.view && (now < st.until || now - st.t < MIN_INTERVAL[name])) return st.view
  st.t = now
  let d = await (name === 'Claude' ? fetchClaude($) : name === 'Codex' ? fetchCodex($) : fetchGemini($))
  if (d.error) {
    st.until = now + (d.retry ?? 0)
    if (st.good?.rows) d = { rows: st.good.rows, stale: d.error, short: d.short }
  } else {
    st.good = d
    st.until = 0
  }
  st.view = d
  return d
}

// ---------- 표시 ----------
const dot = (r: number) => (r >= 40 ? '🟢' : r >= 15 ? '🟡' : '🔴')
const pad = (n: number) => String(n).padStart(2, '0')

function shortReset(ms?: number): string {
  if (!ms) return ''
  const d = new Date(ms)
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return ms - Date.now() < 86_400_000 ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`
}

function statusLine(data: Record<string, Result>): string {
  return Object.entries(data)
    .map(([n, d]) =>
      !d.rows?.length
        ? `${ABBR[n]} ⚠${d.short ?? ''}`
        : `${dot(d.rows[0].remain)}${ABBR[n]} ${Math.round(d.rows[0].remain)}%${d.stale ? '~' : ''}`,
    )
    .join(' ')
}

function details(data: Record<string, Result>): string {
  return Object.entries(data)
    .map(([n, d]) =>
      d.error
        ? `${n}: ⚠ ${d.error}`
        : `${n}  ` +
          (d.rows ?? []).map(r => `${r.label} ${Math.round(r.remain)}% (갱신 ${shortReset(r.resetMs)})`).join(' · ') +
          (d.stale ? `  [이전 값: ${d.stale}]` : ''),
    )
    .join('\n')
}

// ---------- 등록 ----------
let latest: Record<string, Result> = {}

// 설정(providers)은 "Claude,Codex" 처럼 쉼표로 구분. 기본: Claude,Codex
function pick(options: unknown): string[] {
  const raw = String((options as any)?.providers ?? 'Claude,Codex')
  const want = raw.split(',').map(s => s.trim())
  const list = NAMES.filter(n => want.includes(n))
  return list.length ? list : ['Claude']
}

async function refresh($: Api, options: unknown): Promise<void> {
  const out: Record<string, Result> = {}
  for (const n of pick(options)) out[n] = await cached($, n)
  latest = out
  $.ui.status(statusLine(out))
}

export const register: Register = (on, options) => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'ai-usage', description: 'Claude·Codex·Gemini 남은 사용량 자세히 보기' })
    void refresh($, options)
    $.clock.every(REFRESH_MS, () => void refresh($, options))
    return next(e)
  })

  on('command.run', { command: 'ai-usage' }, async $ => {
    await refresh($, options)
    return { text: details(latest) || '조회 중입니다. 잠시 후 다시 시도하세요.' }
  })
}
