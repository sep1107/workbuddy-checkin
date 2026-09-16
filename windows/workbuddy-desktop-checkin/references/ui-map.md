# WorkBuddy 桌面端签到 · 界面地图与踩坑记录

实测环境：Windows 11 (10.0.26200)、WorkBuddy 5.5.6（Electron / Chromium）、
屏幕物理 1920×1080、系统 DPI 缩放 150%、comtypes 直连 UIAutomationCore。

---

## 1. 签到链路的完整路径

```
WorkBuddy 主窗口
└─ 左下角账号入口（MenuItem，位于窗口底部，注意：不是 Button）
   └─ 账号菜单（Menu，window 内浮层，不是新窗口）
      ├─ 体验版 / 积分余额 <数值>
      ├─ Buddy加油站          ← 进入签到页的入口
      ├─ 去邀约
      └─ 成长计划 / 设置 / ...
         └─ Buddy加油站弹窗（同一窗口内）
            ├─ 开学季 · 100 每日可领通用积分
            ├─ 已领 N 天 | 累计领取 N 分   ← 进度（文本被拆成多个节点）
            ├─ Button aid=fuel-expanded-claim  ← 签到按钮（唯一稳定的锚点）
            └─ Button aid=fuel-action（"认证领积分"，需实名认证，禁止自动点击）
```

## 2. 元素定位锚点

| 元素 | 首选依据 | 备选 | 备注 |
|---|---|---|---|
| 主窗口 | 进程 `workbuddy.exe` 面积最大的可见窗口 | 标题含 `WorkBuddy` | 14 个进程，只有 1 个是主窗口 |
| 账号入口 | 窗口底部 160px 内唯一的 `MenuItem` | 名称含用户名 | 不要按用户名匹配（改名/i18n 会失效） |
| 加油站入口 | 菜单内 `Text` 精确等于 `Buddy加油站` | 名称含"加油站" | 必须精确匹配 + 区域过滤，见踩坑 4 |
| 签到按钮 | `automationId == "fuel-expanded-claim"` | 文本 `立即领取` | 文案会变，aid 稳定 |
| 已签到 | aid=`fuel-expanded-claim` 且文本变成 `今日已领` | `今日已签到` | i18n key `account.menu.fuelStation.claimed` |
| 进度 | `已领` 文本锚点 + 同行数字 | 正则 `已领 (\d+) 天.*累计领取 (\d+) 分` | 树里是拆散节点，整句正则常匹配不到弹窗本体 |
| 积分余额 | 菜单内 `积分余额` 文本节点上的数字 | — | 限定左列，见踩坑 4 |

## 3. 签到按钮的状态机

| 按钮文案 | aid | 含义 | 动作 |
|---|---|---|---|
| `立即领取` | `fuel-expanded-claim` | 今日可领 | 点击签到 |
| `今日已领` | `fuel-expanded-claim` | 今日已领 | 结束（幂等，直接判定成功） |
| `认证领积分` | `fuel-action` | 需实名认证 | **不要点**，应提示用户手动处理 |

## 4. 已知踩坑（按发生频率排序）

### 4.1 文本匹配被聊天正文污染（最容易犯）
WorkBuddy 是聊天应用，**对话内容本身也会进入无障碍树**。
一旦对话里出现过"Buddy加油站""积分余额""已领 0 天"等字样，
`name` 包含匹配就会命中聊天文本，导致点到聊天区或读到假数据。

对策：**名称精确匹配（exact）+ 矩形有效（w,h>4）+ 区域过滤（x < 窗口左边界+500，
即左列菜单/弹窗范围）**。三条同时用。

### 4.2 DPI 缩放导致点击落空
未设 DPI 感知时 `GetWindowRect` 返回 1280×720 逻辑坐标，
而截图与鼠标注入是 1920×1080 物理坐标，两者错位约 1.5 倍，点击全部打偏。
必须在进程启动早期调用 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`。
`uia_win.set_dpi_aware()` 已在模块导入时执行。

### 4.3 Electron 默认不暴露无障碍树
直接 `ElementFromHandle` 可能只返回窗口壳（子节点 0）。
Chromium 检测到 UIA 客户端后**异步**开启 a11y（约 0.3~1s），需轮询重试。
`uia_win.wait_children()` 已封装。若持续为 0，说明确实拿不到控件——
此时应明确报告"无控件读取能力"，**不要改用猜测坐标**。

### 4.4 键盘注入可能被拦截
部分环境下键盘 `SendInput` 返回 0（被安全软件/Hook 拦截），鼠标 `SendInput` 正常。
`uia_win.press_key()` 自动回退 `keybd_event`。

### 4.5 组合容器矩形跨越多行
`Buddy加油站` 那行在树里存在一个组合按钮，矩形同时覆盖"Buddy加油站"和"去邀约"两行
（高度约 132px）。点它的中心会落在两行交界处，行为不确定。
应点击**内部文本子元素**的中心（"Buddy加油站"文本，高约 28px）。

### 4.6 comtypes 遍历方式
`element.CurrentFirstChild` 在部分生成版本不存在，且 property 访问会抛 AttributeError。
统一用 `IUIAutomation::RawViewWalker`（`GetFirstChildElement` / `GetNextSiblingElement`）。
另外树很深（>500 节点），用迭代 + 显式栈，避免递归爆栈。

### 4.7 弹窗不是新窗口
账号菜单和加油站弹窗都渲染在**主窗口内部**（无新顶层窗口）。
不要指望通过枚举新窗口来发现它们。

## 5. 官方后端接口（来自 app.asar 静态分析）

只作为理解状态来源的参考；**桌面端自动化应走 UI，不要直接伪造接口请求**
（涉及登录态与风控，且接口可能变更）。

| 用途 | 端点 |
|---|---|
| 查询签到状态 | `POST /billing/meter/checkin-status` |
| 执行每日签到 | `POST /billing/meter/daily-checkin` |

相关 i18n key（`app.asar` 内可检索）：

- `account.menu.fuelStation` = `Buddy加油站`
- `account.menu.fuelStation.claimCredits` = `签到领积分`
- `account.menu.fuelStation.claimed` = `今日已签到`
- DOM 埋点属性：`data-track-id="avatar_menu_fuel_station"`

## 6. 界面改版后的重新测绘流程

1. 人工把界面点到目标状态（菜单展开 / 弹窗打开）。
2. `python scripts/dump_uia.py --screenshot shot.png` 全量导出，看 name/aid/矩形。
3. `python scripts/dump_uia.py --grep 签到,积分,领取` 聚焦候选元素。
4. 优先挑 `automationId` 非空的元素作为锚点，更新 `checkin.py` 顶部常量。
5. 用 `python scripts/checkin.py --dry` 验证导航与状态识别，再正式执行。
