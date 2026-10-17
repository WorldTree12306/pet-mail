# CHANGELOG

## v1.0.0 · 2026-10-17

首个公开版本：桌宠通用互动协议 **pet-mail v1**。

- `SPEC.md` —— 协议规范 v1：共享目录信箱布局、`registry.json` 名册与事件路由、
  邮件结构与生命周期（pending → delivered 消费语义）、坐标发布与新鲜度、
  去重与 TTL、新桌宠接入清单。
- `adapters/fox-zcode-plugin/` —— ZCode 小狐娘挂件适配器：派单事件源（进程命令行探测）、
  完工回执事件源、working 指示灯、收件气泡、发件飞行动画、手动寄信。
- `adapters/dafeiyu-pyside/` —— PySide6 桌宠适配器：全屏透明飞行动画窗 + 信箱管理器，
  零第三方依赖。
- `tools/selfcheck.mjs` —— 一键自检：目录可写、名册合法、坐标新鲜度，`--send` 走全链路。
- `dsh-plugin/` —— DSH 原生插件设计稿（二期，原生派单事件源）。
- `registry.example.json` —— 名册模板。

已知局限见 SPEC §8（进程枚举仅 Windows、轮询延迟 1~2s、单机单用户设计）。
