// 桌宠互联（pet-mail 协议）的小狐娘服务端部分。协议全文见本仓库 SPEC.md。
//
// 职责：
//   1. 派单 watcher：定时枚举本机 node 进程命令行，识别「无头 ZCode 任务」
//      （zcode.cjs -p ...，覆盖 MCP 桥、zcode-bg.mjs 等一切派单路径），维护
//      state/headless-tasks.json，并按 registry 路由生成桌宠邮件（mails/pending/）。
//   2. 信箱领取：把 pending 里 to==fox 的邮件移入 delivered 并返回给前端播收件动画。
//   3. 位置发布：接收前端上报的全局屏幕坐标，镜像到 positions/fox.json 供发件方取目标点。
//
// 刻意不做在 MCP 桥上：桥是 DSH 的进程，改它就得重启 DSH；进程级探测对所有
// 派单脚本一视同仁，天然是通用版本。
import { execFile } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const PET_MAIL_DIR = process.env.PET_MAIL_DIR || path.join(os.homedir(), '.zcode', 'pet-mail')
const REGISTRY_FILE = path.join(PET_MAIL_DIR, 'registry.json')
const PENDING_DIR = path.join(PET_MAIL_DIR, 'mails', 'pending')
const DELIVERED_DIR = path.join(PET_MAIL_DIR, 'mails', 'delivered')
const POSITIONS_DIR = path.join(PET_MAIL_DIR, 'positions')
const STATE_DIR = path.join(PET_MAIL_DIR, 'state')
const HEADLESS_TASKS_FILE = path.join(STATE_DIR, 'headless-tasks.json')

const SCAN_INTERVAL_MS = 2000
const CLEANUP_INTERVAL_MS = 5 * 60 * 1000
const DELIVERED_TTL_MS = 10 * 60 * 1000
const PENDING_TTL_MS = 30 * 60 * 1000
const POSITION_STALE_MS = 15 * 1000

const SELF_PET_ID = 'fox'

function log(...args) {
  console.error('[pet-mail]', ...args)
}

function ensureDirs() {
  for (const dir of [PENDING_DIR, DELIVERED_DIR, POSITIONS_DIR, STATE_DIR]) {
    try {
      fs.mkdirSync(dir, { recursive: true })
    } catch (err) {}
  }
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch (err) {
    return null
  }
}

function writeJsonAtomic(file, data) {
  ensureDirs()
  const tmp = file + '.' + crypto.randomBytes(4).toString('hex') + '.tmp'
  try {
    fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8')
    fs.renameSync(tmp, file)
  } catch (err) {
    try {
      fs.unlinkSync(tmp)
    } catch (e) {}
    throw err
  }
}

function readRegistry() {
  const reg = readJson(REGISTRY_FILE)
  if (reg && Array.isArray(reg.pets) && Array.isArray(reg.routes)) return reg
  return { pets: [], routes: [] }
}

// ---------- 进程命令行解析 ----------

// PowerShell 每行输出一个 base64(pid \t cmdline)，绕开引号/换行/中文的转义地狱。
const PS_SCAN_ARGS = [
  '-NoProfile',
  '-NonInteractive',
  '-Command',
  "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | ForEach-Object { " +
    '[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("$($_.ProcessId)`t$($_.CommandLine)")) }',
]

function decodeScanLine(line) {
  const decoded = Buffer.from(line.trim(), 'base64').toString('utf8')
  const tab = decoded.indexOf('\t')
  if (tab <= 0) return null
  return { pid: Number(decoded.slice(0, tab)), cmdline: decoded.slice(tab + 1) }
}

function scanNodeProcesses() {
  return new Promise((resolve) => {
    execFile('powershell.exe', PS_SCAN_ARGS, { windowsHide: true, timeout: 8000 }, (err, stdout) => {
      if (err) {
        resolve([])
        return
      }
      const out = []
      for (const line of String(stdout || '').split('\n')) {
        if (!line.trim()) continue
        try {
          const rec = decodeScanLine(line)
          if (rec && Number.isFinite(rec.pid) && rec.cmdline) out.push(rec)
        } catch (e) {}
      }
      resolve(out)
    })
  })
}

// Windows 命令行数组还原（Locale/Echo 侧的经典算法，够用即可）：
// 空格分隔；双引号包段；反斜杠在引号前成对转义；"" 在引号段内表示字面引号。
function parseArgv(cmdline) {
  const argv = []
  let cur = ''
  let inQuotes = false
  let hasCur = false
  let i = 0
  while (i < cmdline.length) {
    const ch = cmdline[i]
    if (ch === '\\') {
      let slashes = 0
      while (i < cmdline.length && cmdline[i] === '\\') {
        slashes++
        i++
      }
      if (i < cmdline.length && cmdline[i] === '"') {
        for (let s = 0; s < Math.floor(slashes / 2); s++) cur += '\\'
        if (slashes % 2 === 1) cur += '"'
        else continue // 偶数个反斜杠+引号：反斜杠减半，引号按普通处理
      } else {
        for (let s = 0; s < slashes; s++) cur += '\\'
      }
      continue
    }
    if (ch === '"') {
      inQuotes = !inQuotes
      hasCur = true
      i++
      continue
    }
    if (!inQuotes && (ch === ' ' || ch === '\t')) {
      if (hasCur || cur) {
        argv.push(cur)
        cur = ''
        hasCur = false
      }
      i++
      continue
    }
    cur += ch
    hasCur = true
    i++
  }
  if (hasCur || cur) argv.push(cur)
  return argv
}

function isHeadlessTask(cmdline) {
  const lower = cmdline.toLowerCase()
  if (!lower.includes('zcode.cjs')) return false
  if (lower.includes('__zcode-plugin-host')) return false
  // 无头跑任务固定是 -p <prompt>；交互会话、插件宿主都不带它
  return /(^|\s)-p(\s|$)/.test(lower)
}

function summarizeHeadless(cmdline) {
  const argv = parseArgv(cmdline)
  let prompt = ''
  let mode = ''
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '-p' && argv[i + 1]) prompt = argv[i + 1]
    if (argv[i] === '--mode' && argv[i + 1]) mode = argv[i + 1]
  }
  const oneLine = String(prompt || '').replace(/\s+/g, ' ').trim()
  return { promptSummary: oneLine.slice(0, 160), mode }
}

// ---------- 邮件 ----------

function petName(registry, id) {
  const pet = registry.pets.find((p) => p && p.id === id)
  return (pet && pet.name) || id
}

function writeMail(route, task) {
  const registry = readRegistry()
  const from = route.from
  const to = route.to
  if (from === to) return null
  const ts = Date.now()
  const mail = {
    id: 'm' + ts.toString(36) + crypto.randomBytes(3).toString('hex'),
    kind: 'task-dispatch',
    from,
    to,
    title: `${petName(registry, from)} 给 ${petName(registry, to)} 派了新任务`,
    body: (task.promptSummary || '（任务详情见派单方）') + (task.mode ? `（mode=${task.mode}）` : ''),
    ts,
  }
  const file = path.join(PENDING_DIR, `${ts}-${mail.id}.json`)
  try {
    writeJsonAtomic(file, mail)
    return mail
  } catch (err) {
    log('写邮件失败:', String((err && err.message) || err))
    return null
  }
}

// 领取：pending 里 to==自己 的邮件移入 delivered 后返回（防重复投递的唯一动作）
function claimPendingMails(selfId) {
  const claimed = []
  let names = []
  try {
    names = fs.readdirSync(PENDING_DIR)
  } catch (err) {
    return claimed
  }
  for (const name of names) {
    if (!name.endsWith('.json') || name.includes('.tmp')) continue
    const file = path.join(PENDING_DIR, name)
    const mail = readJson(file)
    if (!mail || typeof mail !== 'object') continue
    if (mail.to !== selfId) continue
    claimed.push(mail)
    try {
      fs.renameSync(file, path.join(DELIVERED_DIR, name))
    } catch (err) {
      try {
        fs.unlinkSync(file)
      } catch (e) {}
    }
  }
  return claimed
}

function cleanupMaildirs() {
  // seen 表跟着清一次（清掉后若再扫到同 id，也因 30s 过期而不会重播）
  if (outgoingSeen.size > 200) outgoingSeen.clear()
  const sweep = (dir, ttl) => {
    let names = []
    try {
      names = fs.readdirSync(dir)
    } catch (err) {
      return
    }
    for (const name of names) {
      if (!name.endsWith('.json')) continue
      const file = path.join(dir, name)
      try {
        const st = fs.statSync(file)
        if (Date.now() - st.mtimeMs > ttl) fs.unlinkSync(file)
      } catch (err) {}
    }
  }
  sweep(DELIVERED_DIR, DELIVERED_TTL_MS)
  // pending 里滞留太久的是收发双方都不在场的死信，挪去 delivered 等统一过期
  let stale = []
  try {
    stale = fs.readdirSync(PENDING_DIR).filter((n) => n.endsWith('.json'))
  } catch (err) {}
  for (const name of stale) {
    const file = path.join(PENDING_DIR, name)
    try {
      const st = fs.statSync(file)
      if (Date.now() - st.mtimeMs > PENDING_TTL_MS) fs.renameSync(file, path.join(DELIVERED_DIR, name))
    } catch (err) {}
  }
}

// ---------- 派单 watcher ----------

const watcher = {
  timer: null,
  cleanupTimer: null,
  tasks: new Map(), // pid -> { promptSummary, mode, startedAt }
  scanning: false,
  consecutiveFailures: 0,
}

function persistTasks() {
  const list = [...watcher.tasks.entries()].map(([pid, t]) => ({ pid, ...t }))
  list.sort((a, b) => a.startedAt - b.startedAt)
  const payload = {
    active: list.length > 0,
    count: list.length,
    tasks: list,
    updatedAt: Date.now(),
  }
  try {
    writeJsonAtomic(HEADLESS_TASKS_FILE, payload)
  } catch (err) {}
  return payload
}

async function scanOnce() {
  if (watcher.scanning) return
  watcher.scanning = true
  try {
    const procs = await scanNodeProcesses()
    watcher.consecutiveFailures = 0
    const now = Date.now()
    const seen = new Map()
    const newTasks = []
    for (const proc of procs) {
      if (!isHeadlessTask(proc.cmdline)) continue
      seen.set(proc.pid, summarizeHeadless(proc.cmdline))
      if (!watcher.tasks.has(proc.pid)) {
        const info = seen.get(proc.pid)
        const task = { ...info, startedAt: now }
        watcher.tasks.set(proc.pid, task)
        newTasks.push(task)
      }
    }
    for (const pid of [...watcher.tasks.keys()]) {
      if (!seen.has(pid)) watcher.tasks.delete(pid)
    }
    if (newTasks.length) {
      const registry = readRegistry()
      for (const route of registry.routes) {
        if (!route || route.event !== 'dsh-dispatch' || route.enabled === false) continue
        for (const task of newTasks) writeMail(route, task)
      }
    }
    persistTasks()
  } catch (err) {
    watcher.consecutiveFailures++
    if (watcher.consecutiveFailures === 3) log('进程扫描连续失败 3 次:', String((err && err.message) || err))
  } finally {
    watcher.scanning = false
  }
}

function startPetMailWatcher() {
  ensureDirs()
  // 启动先扫一次再挂定时器，尽快把「已经跑着的任务」纳入状态
  scanOnce()
  watcher.timer = setInterval(scanOnce, SCAN_INTERVAL_MS)
  watcher.timer.unref?.()
  watcher.cleanupTimer = setInterval(cleanupMaildirs, CLEANUP_INTERVAL_MS)
  watcher.cleanupTimer.unref?.()
}

function stopPetMailWatcher() {
  try {
    if (watcher.timer) clearInterval(watcher.timer)
    if (watcher.cleanupTimer) clearInterval(watcher.cleanupTimer)
  } catch (err) {}
}

function mailboxStatus() {
  const list = [...watcher.tasks.values()].sort((a, b) => a.startedAt - b.startedAt)
  return { active: list.length > 0, count: list.length, tasks: list, updatedAt: Date.now() }
}

// ---------- 前端轮询入口 ----------

// 自己寄出的信（发件方要放"飞出去"动画）：pending/delivered 都看，防跟收件方
// 领取动作抢时间；30s 内的新信才播，seen 只在内存（服务重启后旧信因过期自动跳过）。
const outgoingSeen = new Set()

function lastKnownPositionOf(petId) {
  const pos = readJson(path.join(POSITIONS_DIR, petId + '.json'))
  return pos && typeof pos === 'object' ? pos : null
}

function findOutgoingMails() {
  const now = Date.now()
  const found = []
  for (const sub of ['pending', 'delivered']) {
    let names = []
    try {
      names = fs.readdirSync(path.join(PET_MAIL_DIR, 'mails', sub))
    } catch (err) {
      continue
    }
    for (const name of names) {
      if (!name.endsWith('.json') || name.includes('.tmp')) continue
      const mail = readJson(path.join(PET_MAIL_DIR, 'mails', sub, name))
      if (!mail || typeof mail !== 'object' || mail.from !== SELF_PET_ID) continue
      const id = String(mail.id || name)
      if (outgoingSeen.has(id)) continue
      outgoingSeen.add(id)
      if (now - Number(mail.ts || 0) > 30000) continue
      found.push({ mail, target: lastKnownPositionOf(String(mail.to || '') ) })
    }
  }
  return found
}

function pollMailbox() {
  return {
    ok: true,
    mails: claimPendingMails(SELF_PET_ID),
    outgoing: findOutgoingMails(),
    tasks: mailboxStatus(),
  }
}

// ---------- 位置发布 ----------

let lastPosition = null

function savePetPosition(pos) {
  const clean = {
    x: Math.round(Number(pos.x) || 0),
    y: Math.round(Number(pos.y) || 0),
    w: Math.round(Number(pos.w) || 0),
    h: Math.round(Number(pos.h) || 0),
    ts: Date.now(),
  }
  lastPosition = clean
  const file = path.join(POSITIONS_DIR, SELF_PET_ID + '.json')
  try {
    writeJsonAtomic(file, clean)
  } catch (err) {}
  return clean
}

function lastKnownPosition() {
  if (lastPosition) return lastPosition
  const fromDisk = readJson(path.join(POSITIONS_DIR, SELF_PET_ID + '.json'))
  lastPosition = fromDisk || null
  return lastPosition
}

export {
  PET_MAIL_DIR,
  POSITION_STALE_MS,
  SELF_PET_ID,
  claimPendingMails,
  isHeadlessTask,
  lastKnownPosition,
  parseArgv,
  pollMailbox,
  readRegistry,
  savePetPosition,
  startPetMailWatcher,
  stopPetMailWatcher,
  summarizeHeadless,
  writeMail,
}
