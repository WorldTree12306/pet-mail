#!/usr/bin/env node
// pet-mail 自检：验证本机信箱目录、名册、坐标新鲜度；--send 额外投一封测试邮件。
// 零依赖，node tools/selfcheck.mjs [--send] [--from <id>] [--to <id>]
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const DIR = process.env.PET_MAIL_DIR || path.join(os.homedir(), '.zcode', 'pet-mail')
const args = process.argv.slice(2)
const doSend = args.includes('--send')
const opt = (n, d) => (args.includes(n) ? args[args.indexOf(n) + 1] : d)
const FROM = opt('--from', 'dafeiyu')
const TO = opt('--to', 'fox')

let fail = 0
const ok = (msg) => console.log('  ✓ ' + msg)
const bad = (msg) => { console.log('  ✗ ' + msg); fail++ }

console.log('pet-mail 自检 · ' + DIR)

// 1. 目录
const need = ['mails/pending', 'mails/delivered', 'positions', 'state']
for (const d of need) {
  const p = path.join(DIR, d)
  try {
    fs.accessSync(p, fs.constants.W_OK)
    ok('目录可写 ' + d)
  } catch {
    bad('目录缺失或不可写 ' + d + '（可 mkdir -p 修复）')
  }
}

// 2. 名册
let reg = null
try {
  reg = JSON.parse(fs.readFileSync(path.join(DIR, 'registry.json'), 'utf8'))
  const pets = Array.isArray(reg.pets) ? reg.pets.length : 0
  const routes = Array.isArray(reg.routes) ? reg.routes.length : 0
  ok(`registry.json 合法：${pets} 个角色、${routes} 条路由`)
  for (const r of reg.routes || []) {
    const ids = new Set((reg.pets || []).map((p) => p.id))
    if (!ids.has(r.from) || !ids.has(r.to)) bad(`路由 ${r.event} 引用了未登记角色（${r.from} → ${r.to}）`)
  }
} catch (e) {
  bad('registry.json 缺失或非法：' + e.message.slice(0, 80))
}

// 3. 坐标新鲜度
try {
  for (const f of fs.readdirSync(path.join(DIR, 'positions'))) {
    if (!f.endsWith('.json')) continue
    const p = JSON.parse(fs.readFileSync(path.join(DIR, 'positions', f), 'utf8'))
    const age = Math.round((Date.now() - (p.ts || 0)) / 1000)
    const id = f.replace('.json', '')
    if (age <= 15) ok(`坐标 ${id}：(${p.x},${p.y}) ${age}s 前更新`)
    else bad(`坐标 ${id}：${age}s 未更新（超过 15s 新鲜度，对方离线或未开互联）`)
  }
  if (fs.readdirSync(path.join(DIR, 'positions')).length === 0) {
    console.log('  - positions 为空：还没有桌宠发布过位置（功能未开或首次运行）')
  }
} catch (e) {
  bad('positions 读取失败：' + e.message.slice(0, 80))
}

// 4. 任务表
try {
  const t = JSON.parse(fs.readFileSync(path.join(DIR, 'state', 'headless-tasks.json'), 'utf8'))
  console.log(`  - 任务表：${t.active ? '有 ' + t.count + ' 个任务在跑' : '空闲'}（更新于 ${Math.round((Date.now() - t.updatedAt) / 1000)}s 前）`)
} catch {
  console.log('  - 任务表不存在：事件源（小狐娘服务端 watcher）未运行或无派单历史')
}

// 5. 可选：投测试邮件
if (doSend) {
  const ts = Date.now()
  const mail = {
    id: 'm' + ts.toString(36) + 'chk',
    kind: 'task-dispatch',
    from: FROM,
    to: TO,
    title: `${FROM} → ${TO} 自检邮件`,
    body: 'selfcheck：看到这封信说明收发链路通畅。',
    ts,
  }
  fs.mkdirSync(path.join(DIR, 'mails', 'pending'), { recursive: true })
  fs.writeFileSync(path.join(DIR, 'mails', 'pending', ts + '-chk.json'), JSON.stringify(mail, null, 2))
  console.log(`  ➤ 已投测试邮件（${FROM} → ${TO}），双方在 1~2 秒内应有动画/气泡反应；` +
    `随后 pending 应清空、delivered 应出现 ${ts}-chk.json`)
}

console.log(fail ? `\n结果：${fail} 项异常` : '\n结果：全部通过')
process.exit(fail ? 1 : 0)
