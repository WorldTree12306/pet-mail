# -*- coding: utf-8 -*-
# pet-mail 桌宠互联适配器（大肥鱼桌宠侧）· 可独立分享的模块
#
# 协议规范见本仓库 SPEC.md（pet-mail 协议 v1）。
# 本模块对外只暴露一个入口：PetMailboxManager(pet).start()/stop()，
# 宿主桌宠需要的挂钩（约 5 行）见仓库 adapters/dafeiyu-pyside/INSTALL.md：
#   1. pet.cfg["pet_mail"] = {"enabled": True, "self_id": "dafeiyu"}
#   2. self.petmail = PetMailboxManager(self); self.petmail.start()
#   3.（可选）设置页开关：self.petmail.set_enabled(on) / set_self_id(pid)
#
# 依赖：PySide6（QtWidgets/QtGui/QtCore）+ 标准库，零第三方包。
import json
import math
import os
import random
import threading
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

#
# 桌宠之间用 ~/.zcode/pet-mail/ 下的 JSON 文件传"卡通邮件"：发件方把邮件写进
# mails/pending/，收件方扫到自己名下的就领走。协议全文见该目录的 PROTOCOL.md。
# 大肥鱼这一侧干三件事：
#   1. 后台线程扫信箱，看到 from==自己 的邮件 → 主线程放"邮件飞出去"动画；
#   2. 每 2 秒把自己的屏幕坐标发布到 positions/<id>.json（对方飞行时取目标点）；
#   3. 给设置页提供「测试发送」和 registry.json（角色名册+路由）的读写。
# 「DSH 派单」这个事件源在小狐娘那侧探测（它看得见无头 ZCode 进程），这里只管
# 把飞行动画演好——所以两边谁也不用改 MCP 桥。
# --------------------------------------------------------------------------- #
PET_MAIL_DIR = os.environ.get("PET_MAIL_DIR") or os.path.join(
    os.path.expanduser("~"), ".zcode", "pet-mail")
PET_MAIL_PROTOCOL_TTL_MS = 30 * 1000      # 超过这个岁数的邮件不再回放（死信）
PET_MAIL_POSITION_STALE_MS = 15 * 1000    # 对方坐标超过这个岁数视为过期
PET_MAIL_SEEN_FILE = os.path.join(PET_MAIL_DIR, "state", "dafeiyu-seen.json")

PET_MAIL_DEFAULT_REGISTRY = {
    "version": 1,
    "pets": [
        {"id": "dafeiyu", "name": "大肥鱼", "canSend": True, "canReceive": False},
        {"id": "fox", "name": "小狐娘", "canSend": False, "canReceive": True},
    ],
    "routes": [
        {"event": "dsh-dispatch", "from": "dafeiyu", "to": "fox", "enabled": True},
    ],
}


def _petmail_read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _petmail_write_json(path, data):
    """先写临时文件再改名，读方（另一只桌宠）永远不会读到写了一半的邮件。"""
    d = os.path.dirname(path)
    try:
        os.makedirs(d, exist_ok=True)
        tmp = path + f".tmp{random.randrange(1 << 30)}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as ex:
        try:
            os.remove(tmp)
        except Exception:
            pass
        print("pet-mail 写盘失败:", repr(ex))


class MailFlightOverlay(QWidget):
    """全屏透明覆盖窗：一封带翅膀的卡通信封从起点沿弧线飞到终点。

    刻意整窗覆盖（收发两点的外接矩形 + 边距）而不是在桌宠窗口里画：邮件要
    飞过整个桌面，飞出大肥鱼自己的窗口。整窗对输入完全透明，也不抢前台。
    """

    FLIGHT_SECONDS = 1.8       # 飞行时长
    RIPPLE_SECONDS = 0.55      # 到达后的涟漪停留
    TRAIL_SECONDS = 0.45       # 残影寿命

    def __init__(self, start, end, on_done=None):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)

        self._start = QPointF(start)
        self._end = QPointF(end)
        # 控制点取两点中点上方拱起：距离越远拱得越高，弧线才看得出来
        lift = max(90.0, math.hypot(end.x() - start.x(), end.y() - start.y()) * 0.28)
        self._ctrl = QPointF((start.x() + end.x()) / 2.0,
                             min(start.y(), end.y()) - lift)
        self._on_done = on_done
        self._trail = []                      # [(pos, angle, scale, born)]
        self._t0 = time.monotonic()
        self._ripple_t0 = None

        # 窗口几何 = 收发两点外接矩形 + 边距，夹在虚拟桌面（多屏）里
        margin = 150
        screen = self.screen() or QApplication.primaryScreen()
        vg = screen.availableGeometry() if screen is not None else None
        x1 = int(min(start.x(), end.x())) - margin
        y1 = int(min(start.y(), end.y())) - margin
        x2 = int(max(start.x(), end.x())) + margin
        y2 = int(max(start.y(), end.y())) + margin
        if vg is not None:
            x1, y1 = max(x1, vg.left()), max(y1, vg.top())
            x2, y2 = min(x2, vg.right()), min(y2, vg.bottom())
        self.setGeometry(x1, y1, max(50, x2 - x1), max(50, y2 - y1))
        self._origin = QPointF(self.geometry().topLeft())

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self.show()

    # ---- 几何 ----
    def _bezier(self, t):
        """二次贝塞尔：起点 → 控制点 → 终点。"""
        mt = 1.0 - t
        return QPointF(mt * mt * self._start.x() + 2 * mt * t * self._ctrl.x() + t * t * self._end.x(),
                       mt * mt * self._start.y() + 2 * mt * t * self._ctrl.y() + t * t * self._end.y())

    def _tangent_angle(self, t):
        """路径切向：信封机头顺着飞，不是平移硬拖。"""
        mt = 1.0 - t
        dx = 2 * mt * (self._ctrl.x() - self._start.x()) + 2 * t * (self._end.x() - self._ctrl.x())
        dy = 2 * mt * (self._ctrl.y() - self._start.y()) + 2 * t * (self._end.y() - self._ctrl.y())
        return math.degrees(math.atan2(dy, dx))

    # ---- 拍子 ----
    def _tick(self):
        now = time.monotonic()
        t = (now - self._t0) / self.FLIGHT_SECONDS
        if t < 1.0:
            pos = self._bezier(t)
            self._trail.append((pos, self._tangent_angle(t),
                                0.7 + 0.45 * math.sin(math.pi * t), now))
            self._trail = [e for e in self._trail if now - e[3] <= self.TRAIL_SECONDS]
        elif self._ripple_t0 is None:
            self._trail = []
            self._ripple_t0 = now
        elif now - self._ripple_t0 >= self.RIPPLE_SECONDS:
            self._finish()
            return
        self.update()

    def _finish(self):
        self._timer.stop()
        self.close()
        if self._on_done is not None:
            try:
                self._on_done()
            except Exception as ex:
                print("pet-mail 动画收尾失败:", repr(ex))
        self.deleteLater()

    # ---- 画 ----
    def paintEvent(self, _ev):
        now = time.monotonic()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(-self._origin)
        t = min(1.0, (now - self._t0) / self.FLIGHT_SECONDS)
        for pos, angle, scale, born in self._trail:
            age = (now - born) / self.TRAIL_SECONDS
            self._draw_envelope(p, pos, angle, scale, alpha=0.30 * (1.0 - age))
        if self._ripple_t0 is None:
            self._draw_envelope(p, self._bezier(t), self._tangent_angle(t),
                                0.7 + 0.45 * math.sin(math.pi * t), alpha=1.0,
                                flap=now)
        else:
            k = min(1.0, (now - self._ripple_t0) / self.RIPPLE_SECONDS)
            pen = QPen(QColor(255, 180, 84, int(255 * (1.0 - k))), 4)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = 12.0 + 90.0 * k
            p.drawEllipse(self._end, r, r)
        p.end()

    def _draw_envelope(self, p, pos, angle, scale, alpha, flap=None):
        """一只卡通信封：圆角信封 + 掀盖折线 + 蜡封 + 一对扇动的翅膀。"""
        p.save()
        p.translate(pos)
        p.rotate(angle)
        p.scale(scale, scale)
        p.setOpacity(max(0.0, min(1.0, alpha)))
        w, h = 84.0, 56.0
        # 翅膀先画，压在信封底下；flap 传了时间就按它扇动（残影定格不扇）
        wing_phase = math.sin(flap * 2.0 * math.pi / 0.24) * 26.0 if flap is not None else 0.0
        for side in (-1, 1):
            p.save()
            p.translate(side * w * 0.40, 2.0)
            p.rotate(side * (14.0 + wing_phase))
            p.scale(side, 1.0)
            wing = QPolygonF([QPointF(0, 0), QPointF(-30, -12), QPointF(-38, -30), QPointF(-10, -16)])
            p.setPen(QPen(QColor("#7c3a12"), 3))
            p.setBrush(QColor("#ffd28a"))
            p.drawPolygon(wing)
            p.restore()
        body = QRectF(-w / 2, -h / 2, w, h)
        p.setPen(QPen(QColor("#7c3a12"), 3.5))
        p.setBrush(QColor("#ff9a4d"))
        p.drawRoundedRect(body, 8, 8)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(-w / 2 + 3, -h / 2 + 4), QPointF(0, 4))
        p.drawLine(QPointF(0, 4), QPointF(w / 2 - 3, -h / 2 + 4))
        p.setBrush(QColor("#ffd28a"))
        p.drawEllipse(QPointF(0, h / 2 - 12), 6, 6)
        p.restore()


class PetMailboxManager(QObject):
    """大肥鱼侧的 pet-mail 信箱：收件扫描在后台线程，动画/落盘回主线程。"""

    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self._queue = []              # 后台线程扫到的发件邮件 → 主线程播动画
        self._inbox = []              # 后台线程扫到的收件邮件 → 主线程冒泡
        self._inboxed = set()         # 已冒泡过的收件 id（内存去重即可）
        self._flying = False
        self._overlay = None          # 持住正在飞的窗，防被 GC 掐断
        self._seen = {}               # mail id → 首见时刻（断点文件，防重启重放）
        self._seen_dirty = False
        self._last_pos = None
        self._last_pos_ts = 0.0
        self._poll_thread = None
        self._drain_timer = None
        self._pos_timer = None
        self._running = False
        pm = pet.cfg.get("pet_mail") or {}
        self.self_id = str(pm.get("self_id") or "dafeiyu")
        self._seen = _petmail_read_json(PET_MAIL_SEEN_FILE, {}) or {}
        if not isinstance(self._seen, dict):
            self._seen = {}

    # ---- 生命周期（设置页开关要能即开即停） ----
    def start(self):
        if self._running or not self.enabled():
            return
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        self._drain_timer = QTimer(self)
        self._drain_timer.timeout.connect(self._drain)
        self._drain_timer.start(700)
        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self.publish_position)
        self._pos_timer.start(2000)
        QTimer.singleShot(1500, self.publish_position)

    def stop(self):
        self._running = False
        for t in (self._drain_timer, self._pos_timer):
            if t is not None:
                t.stop()
        self._drain_timer = self._pos_timer = None
        self._poll_thread = None      # daemon 线程，随它自然退出
        self._queue = []

    def enabled(self):
        return bool((self.pet.cfg.get("pet_mail") or {}).get("enabled", True))

    def set_enabled(self, on):
        pm = self.pet.cfg.setdefault("pet_mail", {})
        pm["enabled"] = bool(on)
        self.pet.save_config()
        if on:
            self.start()
        else:
            self.stop()

    # ---- 后台线程：扫信箱 ----
    def _poll_loop(self):
        while self._running:
            try:
                self._scan_once()
            except Exception as ex:
                print("pet-mail 扫描失败:", repr(ex))
            time.sleep(1.0)

    def _scan_once(self):
        now_ms = int(time.time() * 1000)
        # 同时看 pending 和 delivered：收件方随时可能把信领走，只盯 pending 会跟它
        # 抢时间（两边都是 1s 轮询，纯看谁先到）。信被领走后 delivered 里还留
        # 10 分钟，照样能认出「这是我寄出的」——动画就不会漏。
        for sub in ("pending", "delivered"):
            self._scan_dir(os.path.join(PET_MAIL_DIR, "mails", sub), now_ms)
        if self._seen_dirty:
            self._seen_dirty = False
            cutoff = now_ms - 3600 * 1000
            self._seen = {k: v for k, v in self._seen.items() if int(v) >= cutoff}
            _petmail_write_json(PET_MAIL_SEEN_FILE, self._seen)

    def _scan_dir(self, d, now_ms):
        try:
            names = os.listdir(d)
        except OSError:
            return
        for name in names:
            if not name.endswith(".json") or ".tmp" in name:
                continue
            full = os.path.join(d, name)
            mail = _petmail_read_json(full)
            if not isinstance(mail, dict):
                continue
            mid = str(mail.get("id") or name)
            # ---- 收件：寄给我的信领走（移入 delivered），主线程冒泡说一声 ----
            if mail.get("to") == self.self_id:
                if mid not in self._inboxed:
                    self._inboxed.add(mid)
                    self._inbox.append(mail)
                if d.endswith("pending"):
                    try:
                        os.replace(full, os.path.join(PET_MAIL_DIR, "mails", "delivered", name))
                    except OSError:
                        pass
                continue
            # ---- 发件：我寄出的信 → 放飞行动画（防重启重放，只回放新鲜信） ----
            if mail.get("from") != self.self_id:
                continue
            if mid in self._seen:
                continue
            self._seen[mid] = now_ms
            self._seen_dirty = True
            if now_ms - int(mail.get("ts") or 0) <= PET_MAIL_PROTOCOL_TTL_MS:
                self._queue.append(mail)

    # ---- 主线程：播动画 / 收件冒泡 ----
    def _drain(self):
        if self._inbox:
            mail = self._inbox.pop(0)
            try:
                self.pet.say(f"收到「{mail.get('from') or '？'}」的信：{str(mail.get('body') or '')[:40]}")
            except Exception:
                pass
        if self._flying or not self._queue:
            return
        mail = self._queue.pop(0)
        if self._fly(mail):
            self._flying = True

    def _fly(self, mail):
        start = self.pet.frameGeometry().center()
        target = self.target_center(str(mail.get("to") or "fox"))
        note = None
        if target is None:
            # 对方没在发布坐标（没开/挂了）：朝屏幕对上角飞，链路照走
            screen = self.pet.screen() or QApplication.primaryScreen()
            if screen is None:
                return False
            g = screen.availableGeometry()
            target = QPointF(g.right() - 180, g.top() + 220)
            note = "没找到对方的坐标，邮件朝右上飞啦"
        to_name = mail.get("to") or "对方"

        def done():
            self._flying = False
            self._overlay = None
            try:
                self.pet.say(f"任务派给「{to_name}」啦，等它干完活儿～")
            except Exception:
                pass

        self._overlay = MailFlightOverlay(start, target, on_done=done)
        if note:
            try:
                self.pet.say(note)
            except Exception:
                pass
        return True

    def target_center(self, pet_id):
        """读对方发布的坐标（15s 内算新鲜），返回它中心点的全局坐标。"""
        data = _petmail_read_json(os.path.join(PET_MAIL_DIR, "positions", f"{pet_id}.json"))
        if not isinstance(data, dict):
            return None
        try:
            if int(time.time() * 1000) - int(data.get("ts") or 0) > PET_MAIL_POSITION_STALE_MS:
                return None
            x, y = float(data["x"]), float(data["y"])
            w, h = float(data.get("w") or 0), float(data.get("h") or 0)
            return QPointF(x + w / 2.0, y + h / 2.0)
        except (KeyError, TypeError, ValueError):
            return None

    # ---- 位置发布（对方飞邮件时要用大肥鱼的位置） ----
    def publish_position(self):
        if not self._running:
            return
        try:
            g = self.pet.frameGeometry()
            if g.width() <= 0 or g.height() <= 0:
                return
            now = time.time()
            if getattr(self, "_force_pos", False):
                self._force_pos = False
            else:
                moved = (self._last_pos is None
                         or abs(g.x() - self._last_pos[0]) > 2
                         or abs(g.y() - self._last_pos[1]) > 2)
                if moved and now - self._last_pos_ts < 2.0:
                    return          # 拖动中节流，落定那一拍会跟上来
                if not moved and now - self._last_pos_ts < 10.0:
                    return          # 静止时 10s 一拍心跳，维持 ts 新鲜
            self._last_pos = (g.x(), g.y())
            self._last_pos_ts = now
            _petmail_write_json(
                os.path.join(PET_MAIL_DIR, "positions", f"{self.self_id}.json"),
                {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height(),
                 "ts": int(time.time() * 1000)})
        except Exception as ex:
            print("pet-mail 位置发布失败:", repr(ex))

    # ---- 名册/路由（registry.json 是唯一真源，设置页和事件源都读它） ----
    def read_registry(self):
        reg = _petmail_read_json(os.path.join(PET_MAIL_DIR, "registry.json"))
        return reg if isinstance(reg, dict) else json.loads(json.dumps(PET_MAIL_DEFAULT_REGISTRY))

    def write_registry(self, reg):
        _petmail_write_json(os.path.join(PET_MAIL_DIR, "registry.json"), reg)

    def dispatch_route(self):
        for r in self.read_registry().get("routes") or []:
            if isinstance(r, dict) and r.get("event") == "dsh-dispatch":
                return r
        return None

    def send_test_mail(self):
        """设置页的「测试发送」：写一封测试邮件走全链路（本端放飞行，对方收信）。"""
        route = self.dispatch_route()
        to_id = str((route or {}).get("to") or "fox")
        ts = int(time.time() * 1000)
        mail = {
            "id": f"m{ts:x}test",
            "kind": "test",
            "from": self.self_id,
            "to": to_id,
            "title": "测试邮件",
            "body": "桌宠互联链路测试：看到这封信，说明配置是通的！",
            "ts": ts,
        }
        _petmail_write_json(os.path.join(PET_MAIL_DIR, "mails", "pending", f"{ts}-test.json"), mail)
        return to_id

    def set_self_id(self, pet_id):
        self.self_id = str(pet_id or "dafeiyu")
        pm = self.pet.cfg.setdefault("pet_mail", {})
        pm["self_id"] = self.self_id
        self.pet.save_config()
        self._force_pos = True         # 绕过节流，下一拍立刻用新身份落盘
        QTimer.singleShot(50, self.publish_position)
