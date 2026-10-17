# pet-mail · 桌宠互联协议与参考实现

让桌面宠物之间互发「卡通邮件」的极简协议：一个共享目录 + 纯 JSON 文件 + 轮询。
不依赖任何框架——PySide6 桌宠、Electron 挂件、webview 小宠物，谁能读写文件谁就能接入。

```
┌──────────┐   飞行动画   ┌──────────┐
│ 大肥鱼桌宠 │ ──────────▶ │ 小狐娘挂件 │
│ (PySide6) │    信箱文件   │ (Electron) │
└────┬─────┘              └────┬─────┘
     │      ~/.zcode/pet-mail/ │
     └────── mails/ positions/ ┘
```

## 它解决什么场景

以「AI 派单工作流」为起点：**DSH（DeepSeek Harness）给 ZCode 派编码任务时，
发件方桌宠放飞行动画，收件方桌宠冒泡告知并点亮 working 指示灯**——
但协议本身与「派单」无关：任何「一方向另一方通知某事」的场景都能用
（提醒、留言、状态广播），事件类型在 registry 里可自由扩展。

## 快速开始

参考实现（两边都已在本仓库作者的机器上长期运行）：

| 适配器 | 宿主 | 目录 |
|--------|------|------|
| 小狐娘挂件 | ZCode 插件（Electron 浮层 + 本地 HTTP 服务） | `adapters/fox-zcode-plugin/` |
| 大肥鱼桌宠 | PySide6 独立桌宠 | `adapters/dafeiyu-pyside/` |

初始化信箱（一次性）：

```bash
# macOS / Linux / WSL
mkdir -p ~/.zcode/pet-mail/{mails/pending,mails/delivered,positions,state}
cp registry.example.json ~/.zcode/pet-mail/registry.json   # 再把里面的 id/name 改成你自己的
```

```powershell
# Windows PowerShell
$d = "$env:USERPROFILE\.zcode\pet-mail"
'mails\pending','mails\delivered','positions','state' | ForEach-Object { New-Item -ItemType Directory -Force -Path "$d\$_" | Out-Null }
Copy-Item registry.example.json "$d\registry.json"
```

接入自己的桌宠：读 [SPEC.md](SPEC.md) 第二章「新桌宠接入清单」——
收件方约 30 行代码（轮询 + 移动文件 + 播动画），发件方约 60 行（扫描 + 贝塞尔飞行动画）。

## 目录

- [SPEC.md](SPEC.md) —— 协议规范 v1（信箱布局、邮件生命周期、事件注册表、去重与 TTL）
- [registry.example.json](registry.example.json) —— 名册模板，复制到 `~/.zcode/pet-mail/registry.json` 后改 id
- [adapters/fox-zcode-plugin/](adapters/fox-zcode-plugin/INSTALL.md) —— ZCode 狐娘挂件适配器
- [adapters/dafeiyu-pyside/](adapters/dafeiyu-pyside/INSTALL.md) —— PySide6 桌宠适配器
- [tools/selfcheck.mjs](tools/selfcheck.mjs) —— 一键自检（目录/名册/坐标/邮件回路）
- [dsh-plugin/](dsh-plugin/README.md) —— DSH 原生插件（二期草案：原生派单事件源）

## 自检

```bash
node tools/selfcheck.mjs          # 检查目录、名册、坐标新鲜度
node tools/selfcheck.mjs --send   # 额外投一封测试邮件走全链路
```

## 环境变量

- `PET_MAIL_DIR`：覆盖默认信箱目录（默认 `~/.zcode/pet-mail`）。两端的参考实现都支持。

> 协议以本仓库 `SPEC.md` 为准；部署时可以把它同步一份到 `~/.zcode/pet-mail/PROTOCOL.md`，
> 方便两端实现和现场排查时直接读（本仓库不重复存放副本）。

## 兼容性约定

- 当前协议版本 **v1**：字段只增不改，新增必须向后兼容。
- 遇到未知 `kind`、未知字段、损坏的邮件文件：实现方应忽略/跳过，不得报错退出（见 SPEC §3）。
- 协议本身的问题：提 Issue 请说明「哪一条约定，在哪种实现下不成立」。

## License

MIT © 2026 WorldTree12306（见 [LICENSE](LICENSE)）
