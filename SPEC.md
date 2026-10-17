# pet-mail 协议规范 · v1

状态：稳定（参考实现已长期运行）· 本规范的实现以「行为兼容」为准，字段新增必须向后兼容。

## 1. 信箱目录

默认 `~/.zcode/pet-mail/`，可用环境变量 `PET_MAIL_DIR` 覆盖。

```
~/.zcode/pet-mail/
  registry.json          # 必需：宠物名册 + 事件路由（配置唯一真源）
  mails/pending/*.json   # 待投递邮件，每封一个文件
  mails/delivered/       # 已投递邮件（收件方领取后移入，留档后按 TTL 清理）
  positions/<petId>.json # 各宠实时屏幕坐标（全局屏幕坐标，各自发布）
  state/                 # 实现私有的状态（任务表、去重断点等），协议不约束内部格式
```

## 2. registry.json（名册与路由）

```json
{
  "version": 1,
  "pets": [
    { "id": "dafeiyu", "name": "大肥鱼", "canSend": true, "canReceive": true },
    { "id": "fox",     "name": "小狐娘", "canSend": true, "canReceive": true }
  ],
  "routes": [
    { "event": "dsh-dispatch", "from": "dafeiyu", "to": "fox",      "enabled": true },
    { "event": "task-done",    "from": "fox",     "to": "dafeiyu",  "enabled": true }
  ]
}
```

- `pets[].id`：全局唯一、 ASCII 标识符；`name` 为显示名；`canSend/canReceive` 为声明能力。
- `routes[]`：`event`（事件类型）+ `from`/`to`（宠物 id）+ `enabled`。
- **事件注册表**（v1 内置）：
  - `dsh-dispatch`：AI 宿主（如 DSH）向编码代理派发任务。邮件 body 应含人类可读的任务摘要。
  - `task-done`：编码代理把派来的任务做完后向收件方回执。参考事件源与 `dsh-dispatch`
    同源（进程命令行探测：任务进程从进程表消失即算完工），因此每笔无头任务会
    「开工一封、完工一封」成对飞行。body 应含任务摘要与耗时。
  - 预留（尚未有参考事件源，命名已保留）：`task-failed`、`custom:*`。
- 写入约定：读 → 改 → 原子写（临时文件 + rename）。任何实现不得缓存 registry 超过一个事件周期（路由改完即生效）。

## 3. 邮件

文件名：`<毫秒时间戳>-<本地唯一后缀>.json`。

```json
{ "id": "m<base36时间戳><随机>", "kind": "task-dispatch", "from": "dafeiyu", "to": "fox",
  "title": "大肥鱼 给 小狐娘 派了新任务", "body": "人类可读的摘要（展示用，可截断）", "ts": 1730000000000 }
```

- `id`：全局唯一，接收/发送双方各自用它去重。
- `title`/`body`：**展示字段**，实现方应假定它们会被截断（参考实现：标题 14 字、正文 44 字封顶）。
- `kind`：`task-dispatch`（开工派单）/ `task-done`（完工回执）/ `note`（手动寄信）/ `test`（链路测试）。收发双方遇到未知 kind 不得报错，按普通邮件展示即可。
- 写入必须原子（临时文件 + rename），读方必须容忍损坏文件（跳过）。

### 生命周期

1. **事件源**（或任意发件方）写 `pending/`；
2. **发件方桌宠**（`from == 自己id`）：播「飞出去」动画。只读不消费；
   为避免与收件方领取竞态，应同时扫 `pending/` 与 `delivered/`；
   仅回放 `ts` 距今 ≤30s 的新邮件（重启不补飞旧信），用内存 set + 断点文件去重；
3. **收件方桌宠**（`to == 自己id`）：播收件反馈（气泡/音效随意），然后把文件
   **移入 `delivered/`**——这是唯一消费动作，防重复投递；
4. TTL：`delivered/` 建议 10 分钟后清理；`pending/` 里滞留 >30 分钟视为死信移入 `delivered/` 等清。

## 4. positions/<petId>.json

```json
{ "x": 120, "y": 880, "w": 160, "h": 160, "ts": 1730000000000 }
```

- 全局屏幕坐标（虚拟桌面），`(x+w/2, y+h/2)` 为宠物中心；飞行目标即中心点。
- 各宠自己发布：移动时节流（≥2s），静止时 10~15s 心跳。
- 读方以 `ts` 判新鲜（参考实现 15s），过期走兜底（如飞向屏幕对侧）。

## 5. state/（实现私有区）

协议不约束内部格式。参考实现放了两类东西：
- 任务表（如 `headless-tasks.json`：`{active, count, tasks:[{pid, mode, promptSummary, startedAt}], updatedAt}`）——「工作中」指示灯的数据源；
- 去重断点（如 `dafeiyu-seen.json`）——防止重启后重放旧邮件。

## 6. 新桌宠接入清单

1. 在 `registry.json` 的 `pets` 登记自己（id/name/canSend/canReceive）；
2. **要收邮件**：轮询 `pending/` 过滤 `to == 自己id` → 播反馈 → 移入 `delivered/`；
   发布自己的 `positions/<id>.json`（心跳维持 ts 新鲜）；
3. **要发邮件动画**：扫 `pending/`+`delivered/` 里 `from == 自己id` 的新邮件，
   读 `positions/<to>.json` 取目标点，放飞行动画（30s 新鲜度 + 去重断点）；
4. **要发光源事件**：实现某个 `event` 类型的产生方，按 registry 路由写邮件；
5. **手动寄信（note）**：写邮件时 `to` 可指向名册里的任意角色（不是只能走路由）；
   发送方 UI 应在多伙伴时提供收件人选择（参考实现：狐娘菜单收件人列表、
   大肥鱼设置页「测试收件人」下拉）。收发两侧对任意数量的角色开放——
   名册里加一个新 id，它立刻可以成为路由目标或手动收件人。

## 7. 参考实现

- `adapters/fox-zcode-plugin/`：Electron 挂件。含派单事件源（进程命令行探测，
  Windows PowerShell/wmic，见 §8 局限）、完工回执事件源（task-done，与派单同源）、
  working 指示灯、收件气泡、发件飞行动画（二次贝塞尔 + 翅膀扇动 + 拖尾残影）、
  手动寄信（挂件菜单「寄信」→ `POST /whale/mailbox/send.json`）。
- `adapters/dafeiyu-pyside/`：PySide6 桌宠。含发件飞行动画（QPainter 全屏透明覆盖窗）、
  收件冒泡、坐标发布、设置页挂钩。

## 8. 已知局限（v1）

- 事件源探测的进程枚举仅 Windows（PowerShell CIM/wmic）；macOS/Linux 需 `ps` 实现。
- 文件轮询间隔 1~2s：事件延迟在同量级，不适合毫秒级场景。
- 单机单用户设计：多用户请用 `PET_MAIL_DIR` 隔离。
