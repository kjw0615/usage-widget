import type { CoreEngineInterface, Register } from 'claude-code'

type Api = CoreEngineInterface
type Row = { label: string; remain: number; resetMs?: number }
type Result = { rows?: Row[]; error?: string; short?: string; stale?: string; retry?: number }

const REFRESH_MS = 60_000
const MIN_INTERVAL_MS = 180_000 // 사용량 조회 주소가 요청이 잦으면 429 를 내므로 3분에 한 번만

// ---------- 조회 (Claude) ----------
async function home($: Api): Promise<string> {
  return (await $.env.get('HOME')) ?? (await $.env.get('USERPROFILE')) ?? ''
}

async function getUsage($: Api, token: string): Promise<any> {
  const res = await $.http.fetch('https://api.anthropic.com/api/oauth/usage', {
    headers: { Authorization: `Bearer ${token}`, 'anthropic-beta': 'oauth-2025-04-20' },
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
    return { error: '토큰 만료 - Claude Code 사용 중 자동 갱신', short: '만료', retry: 120_000 }
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
      return { error: '토큰 만료 - Claude Code 사용 중 자동 갱신', short: '만료', retry: 60_000 }
    }
    const d = await getUsage($, oauth.accessToken)
    const rows: Row[] = []
    for (const [label, key] of [
      ['5시간', 'five_hour'],
      ['주간', 'seven_day'],
    ]) {
      const v = d[key]
      if (v) rows.push({ label, remain: Math.max(0, 100 - Number(v.utilization)), resetMs: Date.parse(v.resets_at) })
    }
    return rows.length ? { rows } : { error: '사용량 응답 없음', short: '오류' }
  } catch (e) {
    return fail(e)
  }
}

// ---------- 캐시/백오프: 마지막 정상값 유지 ----------
let lastTry = 0
let blockedUntil = 0
let view: Result | undefined
let good: Result | undefined

async function cached($: Api): Promise<Result> {
  const now = Date.now()
  if (view && (now < blockedUntil || now - lastTry < MIN_INTERVAL_MS)) return view
  lastTry = now
  let d = await fetchClaude($)
  if (d.error) {
    blockedUntil = now + (d.retry ?? 0)
    if (good?.rows) d = { rows: good.rows, stale: d.error, short: d.short }
  } else {
    good = d
    blockedUntil = 0
  }
  view = d
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

function statusLine(d: Result): string {
  if (!d.rows?.length) return `Claude ⚠${d.short ?? ''}`
  const parts = d.rows.map(r => `${dot(r.remain)}${r.label === '5시간' ? '5h' : '주'} ${Math.round(r.remain)}%`)
  return `Claude ${parts.join(' ')}${d.stale ? ' ~' : ''}`
}

function details(d: Result): string {
  if (d.error) return `Claude: ⚠ ${d.error}`
  const lines = (d.rows ?? []).map(r => `  ${r.label}: 남은 ${Math.round(r.remain)}% · 갱신 ${shortReset(r.resetMs)}`)
  return ['Claude 남은 사용량', ...lines, ...(d.stale ? [`  [이전 값: ${d.stale}]`] : [])].join('\n')
}

let latest: Result | undefined

async function refresh($: Api): Promise<void> {
  latest = await cached($)
  $.ui.status(statusLine(latest))
}

// ---------- 등록 ----------
export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'ai-usage', description: 'Claude 남은 사용량(5시간·주간) 자세히 보기' })
    void refresh($)
    $.clock.every(REFRESH_MS, () => void refresh($))
    return next(e)
  })

  on('command.run', { command: 'ai-usage' }, async $ => {
    await refresh($)
    return { text: latest ? details(latest) : '조회 중입니다. 잠시 후 다시 시도하세요.' }
  })
}
