# 适配器：大肥鱼桌宠（PySide6）

宿主：任意 PySide6 桌面宠物（参考实现接在大肥鱼桌宠的改造分支上；上游项目见 [1190fasheqi/dafeiyu-pet](https://github.com/1190fasheqi/dafeiyu-pet)，MIT）。

## 文件

- `pet_mail.py` —— 独立模块（原样可用，零第三方依赖，只需 PySide6）：
  `MailFlightOverlay`（全屏透明飞行动画窗）+ `PetMailboxManager`（信箱管理器）。

## 接入（约 5 行挂钩）

```python
# 1) 导入（pet_mail.py 放在桌宠主程序同目录）
from pet_mail import PetMailboxManager

# 2) 配置默认值（cfg_defaults 里）
"pet_mail": {"enabled": True, "self_id": "dafeiyu"},

# 3) 主窗口 __init__ 末尾启动
self.petmail = PetMailboxManager(self)
self.petmail.start()

# 4)（可选）设置页开关与身份切换——即开即停，不用重启
self.petmail.set_enabled(on)
self.petmail.set_self_id(pet_id)
```

宿主窗口需提供：`cfg`（dict，含 pet_mail 键）、`save_config()`、`say(text)`
（收件冒泡/派单播报）、`frameGeometry()`、`screen()`——QWidget 子类天然具备后两个。

## 行为

- **发件**：后台线程每 1s 扫 `pending/`+`delivered/`，发现 `from == 自己id` 的新邮件
  （30s 新鲜度 + seen 断点文件防重放）→ 主线程放飞行动画：全屏透明、点击穿透、
  不抢焦点的覆盖窗里，QPainter 绘制卡通信封（翅膀扇动 + 路径切向转向 + 缩放 +
  拖尾残影 + 到达涟漪）沿二次贝塞尔弧线飞向收件方坐标。
- **收件**：扫到 `to == 自己id` 的邮件 → 移入 `delivered/`（消费）→ 主线程冒泡读信。
- **坐标发布**：每 2s 检查，移动节流 2s、静止心跳 10s，写 `positions/<self_id>.json`。

对上游更新的友好性：全部 pet-mail 代码只在本模块 + 上述挂钩行里，
rebase 上游新版本时仅需重放挂钩。
