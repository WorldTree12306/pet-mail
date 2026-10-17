# 适配器：小狐娘挂件（ZCode 插件）

宿主：[zcode-fox-widget](https://github.com/pigeon189/zcode-fox-widget) 形态的 ZCode 桌宠插件（本地 HTTP 服务 + Electron 透明浮层）。
本目录给出把 pet-mail 能力装进任意同类插件的全部材料。

## 文件

- `lib/pet-mail.mjs` —— 服务端模块（原样可用）：派单事件源 watcher、信箱领取、坐标镜像。
- 本页 —— 集成点清单（4 处挂钩 + 2 段前端代码）。

## 服务端挂钩（lib/server.mjs）

```js
// 1) 导入
import { pollMailbox, savePetPosition, startPetMailWatcher, stopPetMailWatcher } from './pet-mail.mjs'

// 2) 路由表（404 之前）：
//    GET  /whale/mailbox/poll.json     → pollMailbox()（领取邮件 + 任务状态 + 寄出的信）
//    POST /whale/mailbox/position.json → savePetPosition(body)（前端坐标上报）
//    POST /whale/mailbox/debug.json    → 追加诊断日志（可选）

// 3) 启动：listen 回调里 startPetMailWatcher()
// 4) 停止：stop() 里 stopPetMailWatcher()
```

安全模型沿用插件既有约定：本机写接口免令牌（威胁模型见插件 server.mjs 头注释）。

## 前端挂钩（lib/widget.js）

1. **working 指示灯**：宠物根节点右上角徽章（`.zcwv-working`，脉动圆点 + working 字样），
   由 1s 轮询 `poll.json` 的 `tasks.active` 驱动显隐。
2. **收件气泡**：`poll.json` 返回的 `mails[]` → 标题压单行（14 字）、正文 44 字封顶进
   小字槽——**不要把长文塞大字槽**：自动缩字只管横向，折行后的纵向堆叠会溢出气泡。
3. **发件飞行动画**：`poll.json` 返回的 `outgoing[]`（含收件方全局坐标）→ 信封沿二次
   贝塞尔弧线飞向目标（CSS 内联 SVG 翅膀扇动 + 切向转向 + 拖尾残影 + 到达涟漪）。
4. **坐标上报**：`express()` 落定 + 15s 心跳时 POST 页面坐标；浮层窗口原点经
   `whale:screen-origin` IPC 从主进程获取（`desktop/main.cjs` + `desktop/preload.cjs` 各加
   一个 handler，见 `ipcMain.handle('whale:screen-origin')`）。

完整源码见本插件仓库（widget.js 内「桌宠互联」段落 + pet-mail.mjs），行为以 SPEC.md 为准。

## 派单事件源说明

`pet-mail.mjs` 的 watcher 每 2s 枚举 node 进程命令行，匹配 `zcode.cjs -p`（排除插件宿主），
新 PID 按 registry 路由生成 `dsh-dispatch` 邮件并维护 `state/headless-tasks.json`。
这样**不依赖任何宿主配合**：MCP 桥、后台脚本、未来任何派单方式都会被看到。
若你的环境有原生事件回调（如 DSH 插件），可整体替换 watcher，协议输出不变。
