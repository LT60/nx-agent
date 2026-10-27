# AGENTS.md — 接手本仓库的 agent 请先读这份

> 这份文件是给**下一个接手的 AI agent**（Codex / Claude Code / WorkBuddy / 任何能读写
> 文件、跑命令的 agent）的入职手册。读完它 + `README.md` 第七节，你就能干活。
> 不要先读源码 —— 源码会告诉你"怎么实现的"，但不会告诉你"哪些坑已经踩过"。

## 0. 30 秒定位

**这是什么**：给 Siemens NX（模具行业叫 UG）做的一套 Agent 自治闭环。
Agent 通过 MCP 工具下发指令 → 跑在 NX 进程内的桥接 journal 执行 → 文件队列回传结果。
目标是让不懂 CAD/CAM 的用户也能驱动用户的模具建模/CAM 编程工作。

**当前状态（2026-10-01）**：驱动层已在真实 NX 2606 学生版上**端到端跑通**
（启动 12~16 秒、CAM 树遍历出 17 个真实加工操作、截图/保存/探测全过）。
主线卡点不在代码，在**缺两样输入**（见第 7 节）。

**GitHub 上确实已有 NX 的 MCP 项目**（`DreamEnding/NX_MCP` 最成熟）。
不换的理由和该吸收的东西，见 `README.md` 第 7.5 节 —— **别在这一步重新论证**。
一句话：社区做的是"CAD 建模"，我们做的是"**CAM + 判据 + 回滚**"，交集只有建模那块；
而且学生版的驱动入口是 `ugraf.exe -auto=`，不是它们用的 `run_journal.exe`。

**三条启动/验证/停止命令**（在仓库根目录跑）：

```bat
python tools\start_bridge.py          :: 拉起 NX + 桥接，等到就绪（12~16 秒）
python tools\bridge_probe.py          :: 冒烟测试：心跳/ping/info，三项 PASS 才算通
python tools\start_bridge.py --stop   :: 停掉桥接
python tools\selftest.py              :: 离线自检，19 项，全绿才能动别的（不需要 NX）
```

## 0.5 开发循环（**相当于网页开发的「刷新一下」**）

网页循环：改代码 → 刷新浏览器 → 人看 → 提要求 → 再改
NX 循环  ：改 journal/参数 → **NX 窗口实时刷新**（`UF.Disp.Refresh`）→ 人看 → 提要求 → 再改

**★ 给人看的地方是 NX 窗口本身，不是 HTML 画廊**（用户 2026-10-01 明确纠正）。
桥接每执行完一条命令就调 `UF.Disp.Refresh()` 把最新模型画到屏幕上；
Agent 干完活再调 `nx_fit_view`（`UF.View.FitView`，实测生效）把视图缩放到刚好装下模型。
常驻期间界面**点不动但看得见** —— 这已经覆盖"人复核"的主要目的。

```bat
:: Agent 这边干完活就调 nx_preview（适配视图 + 重播刀路 + 3D 材料切除 + 每步截图）
:: preview_gallery.py 只做历史留档/复盘用，不是给人实时看的：
python tools\preview_gallery.py       :: 一页 HTML 列出所有预览图（复盘用）
```

图片落在 `state/preview/<时间戳>_<步骤>.png`。

### ★★ 别忘了 UF 类不在 NXOpen.xml 文档里

`NXOpen.xml`（60MB）只含 `NXOpen.*` 命名空间，**`NXOpen.UF.*` 根本不在里面** ——
别去文档里找 UF 函数。UF 类只能在 **NX 里现场反射**（`dir(ufs.View)` 之类），
写个探测 journal 一分钟出真相。靠这条路已经找到了三个文档查不到的东西：

| 函数 | 用法（2606 实测） |
|---|---|
| `UF.Disp.Refresh()` | 无参调用成功；`RegenerateDisplay()` 会报 1105042 别用 |
| `UF.View.FitView(view_tag, 1.0)` | **必须先有工作部件**，否则 `AskWorkView()` 报 1105012 |
| `UF.Ui.LockUgAccess/UnlockUgAccess` | NX 官方的"批处理锁界面"机制 —— 佐证批处理锁界面是官方模型 |

**⚠️ 一个会把桥接搞死的坑（2026-10-01 实测）**：
对**没生成刀路**的操作调 `ReplayToolPath`，NX 会弹模态对话框，
journal 跑在主线程 → **整个桥接卡死**（心跳停 91 秒，连 `stop` 都投不进去，只能杀进程）。
所以 `cmd_preview` 里先用 `CAMObject.AskPathExists()` 过滤，
没刀路就只截当前视图并说明原因。**别删那段过滤。**

### 「操作软件」这一步跳不跳得过？

**启动那一步已经跳过了**（`ugraf.exe -auto=`，零点击）。
但**运行期间的弹窗跳不过** —— 这是 NX 的架构约束：GUI 单线程，
journal 跑在主线程，任何模态框都会把它堵住（已查证 NXOpen 里没有定时器/回调可绕）。

正确应对不是"绕过弹窗"，而是**在软件外面盯着它**：

```bat
python tools\dialog_sweeper.py --once     :: 诊断：现在屏幕上有什么模态框
python tools\dialog_sweeper.py --watch    :: 常驻清扫（桥接开着时另开一个窗口跑）
python tools\dialog_sweeper.py --log      :: 看历史：都弹过什么、怎么处理的
```

原理：**NX 内部的代码救不了自己**（它正在等那个框），但**外部进程可以**
—— 卡住的只是 NX 主线程，Windows 上别的进程照样能枚举它的窗口、按下按钮。

安全策略（和整条红线一致，不替用户做决定）：
- 已知弹窗 → 选**保守**项（遥测→Decline，是否保存→No）
- 未知弹窗 → **绝不乱点**，只记录 + 终端告警等人来看
- 只剩"同意/确定/覆盖"这类按钮时 → 不动
- 全程留痕到 `state/dialogs.log`

### ★ 弹窗 = NX 的「编译错误」，Agent 必须能读到它

**NX 没有可靠的编译错误输出流**（查过：Temp 下的 `*.log` 是 Office 的，不是 NX 的），
所以**弹窗里的文字就是唯一的报错信息源**。对标编译：

| | C 开发 | NX 开发 |
|---|---|---|
| 报错在哪 | 编译器的 stdout | **模态弹窗**（标题 + 正文控件） |
| 谁能读到 | Agent 直接读 | **外部进程抓窗口文本** → 落盘 → Agent 读 |
| 怎么修 | 看错误行改代码 | 看正文 → `nx_api_lookup` 核对签名 → 改脚本 → `nx_preview` 重跑 |

Agent 侧两个入口：

```
nx_dialog_log          :: 拿最近弹窗的全文（标题+正文+按钮+成因提示）
nx_status              :: 桥接卡住/心跳变陈旧时，自动附上最近弹窗的 diagnosis
```

`format_for_llm()` 输出的报告长这样（真格式）：

```
── 弹窗 1 ──────────────────────────
时间：2026-10-01 14:58:03 ｜ 处理：skipped
标题：Tool Path Replay
正文：
    There is no tool path to replay for the selected operation.
    Generate the tool path first, then replay.
按钮：OK
可能成因：刀路还没生成 —— 先调用 GenerateToolPath 再预览/后处理
```

所以**别只做"把弹窗点掉"** —— 那等于把编译错误窗口关掉不看。
先抓全文、落盘、给 Agent 分析，再决定要不要点。

## 1. 红线（不可协商，违反=项目事故）

1. **AI 只出方案，不直接上机。** 任何 G 代码（NC 程序）上机床前必须过：
   `nc_lint` 通过 → `nc_diff` 无高危 → NX 内置仿真 → 机床空运行 → 人签字。
2. **别替用户同意任何对话框。** 启动器默认把遥测同意框点成 Decline，不是 Agree。
3. **所有校验工具必须离线可跑。** 不能依赖 NX 在线 / 机床 / 许可证。
4. **脚本一律纯标准库。** NX 自带 Python 3.12，没有 pip。桥接 journal 必须单文件。
5. **改完任何代码跑 `tools/selftest.py`**，16 项全绿才算完。别靠眼看。

## 2. 架构地图（30 秒版）

```
Agent (WorkBuddy/Codex/...)
   │  stdio, JSON-RPC 2.0 (MCP, protocolVersion 2024-11-05, 14 个工具)
   ▼
mcp/server.py ──写──> queue/in/<id>.json
                          │  bridge/nx_bridge.py 在 NX 进程内轮询（0.5 秒）
                          ▼
                    queue/out/<id>.json ──读──> 回传 Agent（含 traceback）
```

- **桥接是 NX 内常驻 journal**，用 `ugraf.exe -student -auto=<path>` 启动。
  常驻期间 NX 界面冻结（NX 单线程限制，无解，开两个 NX 实例应对）。
- **验证层** `verify/nc_lint.py`（静态体检）+ `verify/nc_diff.py`（回归比对），
  纯文本、离线。
- **快照回滚** `tools/snapshot.py`：改 .prt 前先拍快照。

## 3. 学生版 NX 的硬事实（实测，别再重新试错）

| 路径 | 结论 | 证据 |
|---|---|---|
| `run_journal.exe` 任何变体 | ❌ 死路 | 固定报 `3615094`（命令行保留不了 journal 许可） |
| 外部 `libufun.dll` 的 `UF_initialize()` | ❌ 死路 | 返回 `3600045`（要开发者许可证） |
| `ugraf.exe -student -auto=<journal>` | ✅ **唯一可行入口** | 12~16 秒就绪，真 GUI 会话，许可正常 |
| 学生版同时开两个 NX 实例 | ✅ | 实测共存无冲突 → 「机器人会话」成立 |
| NX 内非阻塞定时器（AddTimer/AddIdle） | ❌ 不存在 | 查遍 60 MB `NXOpen.xml` → 常驻必冻结 UI |
| `.men` 菜单启动脚本 | ❌ 不存在 | 459 处 ACTIONS 全是 STANDARD |

启动时弹的 `Product Excellence Program` 遥测框**会阻塞启动**，
`start_bridge.py` 默认自动点 Decline。永久关闭见 `docs/启动NX桥接.md` 第三节。

### ★ 授权有两层：别把「登录令牌过期」误报成「授权过期」

学生版是 **SaaS/SALT 云端授权**，实际上有两层东西，排查时必须分清：

| 层 | 类比（程序员版） | 有效期 | 存哪 | 怎么查 |
|---|---|---|---|---|
| **授权 license** | 软件许可证 Key | **1 年** | `C:\ProgramData\Siemens\SoftwareCenter\data\machineData.json` | 看有没有 `expiration` 字段；`installDate` 是一年的起点 |
| **登录令牌 token** | OAuth 的 access token | 短（几小时~几天） | `%LOCALAPPDATA%\Siemens\ssc_session\token`（**DPAPI 加密**，绑机器+账户，搬不走） | 看 SSC 日志有没有 `身份验证错误 [CSA0002] 刷新令牌失败` |

**授权服务由 SSC（Siemens Software Center）托管**：SSC 一退出，
本地授权服务就没了 → 28000/29000 端口无监听 → NX 起不来，弹

```
Designcenter Student Edition Initialization Error
Running instance of the server not found.
```

2026-10-01 完整因果链（真机实录，别再重新推导）：

```
SSC 刷新令牌失败 (15:54:45, [CSA0002])
   → SSC 会话结束退出 (16:01:25)
   → 授权服务没了（netstat 查 28000/29000 无 LISTENING）
   → NX 启动报 "Running instance of the server not found"
   → 用户看到「要重新登录」，以为授权过期了（其实没过期）
```

**修法**：关掉报错的 NX → 启动 SSC
（`C:\Program Files\Siemens\SoftwareCenter\SiemensSoftwareCenter.exe`）
→ 在 SSC 里登录 → **从 SSC 里启动 NX**（别直接双击图标）。

**这类弹窗已被 `dialog_sweeper.py` 的 `NEEDS_HUMAN` 认得**，
会直接打印成因 + 5 步解法，不会让人干等超时。
`start_bridge.py` 等待期间每 10 秒查一次，一发现就以**退出码 3** 报错退出。

### ★★★ `-auto=` 的代价：那个 NX 是「机器人」，不是给人用的（2026-10-01 实测）

**用户看到的三个"故障"其实是同一个原因**：

| 现象 | 真相 |
|---|---|
| 一直挂着 `Work in Progress / Click Stop to interrupt` 框 | journal 正在执行 —— 桥接常驻，所以它一直在 |
| 菜单栏只剩 `File`、Ribbon 空白、Part Navigator 空白 | `-auto=` 让 NX 认为自己在跑批处理，**根本没构建完整 UI** |
| 主窗口点不动 | 同上（主线程被 journal 占着） |

**对照实验（决定性）**：

| 启动方式 | 窗口标题 | 菜单栏 | Ribbon | 侧边栏 | 截图字节 |
|---|---|---|---|---|---|
| `ugraf.exe -student -auto=<j>` | `Designcenter Student Edition **Modeling**` | 只剩 File | 空白 | 只有标题 | 128,724 |
| `ugraf.exe -student`（裸） | `Designcenter Student Edition` | ✅ 完整 | ✅ 完整 | ✅ History 树 | 204,636 |

→ **判据：窗口标题带 `Modeling` 的是「机器人」实例。**

排除掉的错误猜测（都实测过，别再走）：
- ❌ 不是 `LockAccess` 造成的：调 `UI.UnlockAccess()` 后截图**字节数完全没变**
- ❌ 不是"最小图形窗口"：`UF.Ui.AskMinimalGraphicsWindow() -> False`
- ❌ 不是 `AskRibbonVis/SetRibbonVis` 能修的：doc 写明参数是 **`UF_UI_create_ribbon` 返回的 id**，
  只管"我们自己用 API 创建的工具栏"，**管不了 NX 主菜单栏**
- ❌ 不是 `MenuBarManager`：它只有 `RegisterAddOn*` 系列，是给插件注册菜单用的
- ❌ 不是 .NET 缺失：裸启动的 NX 完整且不弹任何 .NET 提示

**`run_journal.exe` 在有 NX 运行时依然失败**（又复测过一次）：
```
Caught unexpected error: 3615094: Unable to reserve license to run journal file
```
→ 所以**没法往"已经正常启动的 NX"里注入 journal**。这条堵死意味着：

**★ 唯一可行的架构 = 双实例**

| 实例 | 启动方式 | 用途 | UI |
|---|---|---|---|
| **机器人** | `-auto=<bridge>` | Agent 干活 | 半残（无所谓，本来也不给人看） |
| **给人看的** | 裸 `-student`（双击图标） | 人看效果 / 自己操作 | ✅ 完整 |

**两个实例可以共存**（实测）。共享受害点也验证过：
**机器人保存的 `.prt` 没有被独占锁定**（`open(path,"r+b")` 成功）→ 另一个 NX 能打开。

→ 所以"刷新一下"的正确形态是：**Agent 在机器人里改完 → 保存 .prt →
人在自己的 NX 里重新打开**。不是让人盯着那个半残窗口。

### `Work in Progress` 框上的 Stop 会杀掉桥接

已把它列进 `dialog_sweeper.EXPECTED`（正常现象）+ `NEVER_CLICK`。
自检 `t_dialog_needs_human` 守着这条，防止哪天规则一改就误点。

### ★★★ CAM 实测：学生版到底能不能做刀路（2026-10-01）

| 能力 | 结果 | 证据 |
|---|---|---|
| 打开 CAM 部件 / 枚举操作 | ✅ | `hsm_setup.prt` 26 个操作全是 `has_path=True` |
| 刀路重播 `ReplayToolPath` | ⚠️ 报 `Error number: 1` | 待查（可能要特定视图/激活状态），**不致命** |
| **3D 切除显示 `Show3dWorkpiece`** | ✅ | 生成 PNG，秒级返回 |
| **输出 CLSF 刀位源文件** | ✅ **可用！** | 见下 |
| 后处理出 G 代码 | ❌ **`MOM: UGPOST License is not available`** | 这才是「学生版不能后处理」的真实机制 |

**★★★ `OutputClsf` 是本项目最有价值的发现**：它是**唯一能自动拿到真实刀路数据的入口**，
不必再干等别人发脱敏 `.nc`。实测输出（真数据）：

```
TOOL PATH/FACE_TOP,TOOL,UGT0202_001
TLDATA/MILL,40.0000,0.8000,34.6900        ← 40mm 面铣刀
FEDRAT/MMPM,1203.1                         ← 进给 mm/min
RAPID
GOTO/-79.6,42.0,3.0                        ← 快速定位
GOTO/-79.6,42.0,0.0                        ← 下刀
GOTO/-56.0,42.0,0.0 ... GOTO/79.6,42.0,0.0 ← 切削各刀位点精确坐标
```

**但必须说清 CLSF ≠ G 代码**：CLSF 是**后处理器的输入**。所以它覆盖
「刀路几何对不对」（五层里的第③层），**覆盖不了「后处理器改得对不对」（第④层）**。
第④层仍然需要真 G 代码 —— 别因为拿到 CLSF 就以为验证层全了。

**★ NX 自带 CAM 教学示例（现成的素材，别再到处找）**：
`D:\NX\MACH\samples\try_nx_cam\` —— 三个部件都带完整刀路：
`1-Create-Tool-Path\hsm_setup.prt`(26) / `3-Feature-Based-Machining\plate_setup_complete.prt`(19) /
`2-Machine-Tool-Simulation\Machine_tool_simulation.prt`(25)。
注意 `hsm.prt` 是**CAD 部分**（没 CAM），配套的 `hsm_setup.prt` 才有。

**★ API 参数名的血泪教训（我在这上面连错 5 次）**：
| API | 第 2 个参数**真名** | 含义 |
|---|---|---|
| `OutputClsf(objects, **clsfFormat**, outfileName, outputUnits)` | 格式名 | **合法值 `"CLSF_STANDARD"`**（`CLSF`/`STANDARD`/`std` 全报 `input CLSF format ... not found`）|
| `Postprocess(objects, **machineType**, outfileName, outputUnits)` | **机床类型** | 不是后处理器名 |
| `PostprocessWithPostprocessor(objects, eventHandlerFile, definitionFile, outfileName, units, outputWarning, reviewTool)` | `.tcl` + `.def` | **显式指定后处理器文件的正确入口** |

- `outputWarning` ∈ `No / PostDefined / ValueOf / Yes`
- `reviewTool` ∈ **`Off` / `On` / PostDefined / ValueOf`（是 `Off`，不是 `No`）**
- 后处理器目录 `MACH\resource\postprocessor\` 里三件套齐全：36 `.tcl` / 26 `.def` / 24 `.pui`
  —— **文件都在，是许可不给**。
- **★ 查参数名的正确姿势：反射 `fn.__doc__`**，里面有
  `` Signature ``Name(a, b, c)`` `` 和每个参数的含义，**比检索 NXOpen.xml 更细**。
  以后写不熟的 API，先 `print(fn.__doc__)`。

### ★★ `-automation` 模式的 UI 是完整的（可能是架构出路）

| 启动方式 | 窗口标题 | UI |
|---|---|---|
| `-auto=<journal>` | `… Student Edition **Modeling**` | 半残（只有 File） |
| 裸 `-student` | `Designcenter Student Edition` | ✅ 完整 |
| **`-student -automation`** | `Designcenter Student Edition` | ✅ **完整** |

`-automation` 是 NX 官方的「外部程序驱动」模式，**且 UI 完整**。若外部能连上
（NXOpen .NET 客户端），就能同时满足「给人看」+「Agent 操作」——**这才是终极架构**。
2026-10-01 未验证完：PowerShell 的 `Add-Type` 被安全策略挡了（运行时编译 .NET 代码），
需换加载方式（`[Reflection.Assembly]::LoadFrom`，或写独立 .NET 程序）。
**下一步就查这个。**

## 3.5 现成资产与建模扩展（2026-10-01 新增，**优先用这些，别从零造**）

### ★★★ 电极加工模板：`MACH\resource\template_part\metric\ngElectrode.prt`

**NX 自带，是「从零编 CAM」的替代路线**（因为 batch 模式建不了加工环境，见上一节）：
自带 **完整 CAM 设置 + 电极几何 + 6 道电极专用工序**：
`ROUGH_ALL`(粗) / `REST_MILL`(残料铣) / `PROFILING_BASE`(基准面轮廓) /
`SEMI_FINISH_STEEP_AND_NON-STEEP`(半精) / `FINISH_STEEP_AND_NON-STEEP`(精) / `FLAT_ZIGZAG`(平面往复)

**标准用法**（已验证）：
```python
# 1. 复制模板出来（别改原模板；★ 用英文名！）
# 2. open_part
# 3. 用建模 API 改几何 / 加特征（见下）
# 4. cam.GenerateToolPath(ops)   ← 会按新几何重算
# 5. preview                     ← 刀路回放 + 3D 切除
```

**其它有用的 CAM 素材**：
- `MACH\samples\try_nx_cam\`：`1-Create-Tool-Path\hsm_setup.prt`(26 工序)、
  `3-Feature-Based-Machining\plate_setup_complete.prt`(19)、
  `2-Machine-Tool-Simulation\Machine_tool_simulation.prt`(25) —— **都带真实刀路**
- `MACH\resource\template_set\*.opt`：加工环境模板集（`cam_general` / `mill_planar` / `mill_contour` …）

### ★★ 建模扩展：往模板里加特征

| 目的 | API |
|---|---|
| 新建方块 | `part.Features.CreateBlockFeatureBuilder(NXOpen.Features.Block.Null)`（`None` 也行）|
| 设几何 | `b.SetOriginAndLengths(Point3d(x,y,z), "L", "W", "H")` —— 尺寸传**字符串表达式** |
| **布尔求和 + 目标** | **`b.SetBooleanOperationAndTarget(FeatureBooleanType.Unite, body)`** |
| 拿特征的实体 | `feature.GetBodies()` |
| 提交 | `b.CommitFeature()`，然后 `b.Destroy()` |
| 改尺寸 | `expr.SetFormula("120")` + `session.UpdateManager.DoUpdate(session.NewMarkId())` |
| 读方块当前参数 | builder 的 `Origin`(Point3d) / `Length`/`Width`/`Height`（Expression，有 `.Value` 和 `.Name`）|

**踩过的坑**：
- 不设 target 就提交 → `NXException: No target solid selected.`
- **`part.Features` 可以直接 `for` 迭代，但 `GetEnumerator()` 不存在**
- **`NXOpen.Body` 没有 `GetBoundingBox()`**（想量尺寸就读特征表达式）
- 布尔枚举：`FeatureBooleanType` = `Create / Unite / Subtract / Intersect / Emboss* / TransferVoids`

### ★★★ 已验证的完整迭代闭环（**这就是本项目的可交付形态**）

```
用户说需求（如「加一个放电头」）
   → 读模型参数（特征表 / 表达式 / builder）
   → 改几何（SetFormula 或新建特征 + Unite）
   → cam.GenerateToolPath(ops)     ← NX 按新几何**重算**刀路
   → preview                        ← 刀路回放 + 3D 切除仿真出图
   → 人看图验收 → 提下一句
```
**全程无鼠标、无 GUI 对话框。** 2026-10-01 用户第一次主动提需求就一轮跑通。

### ★ 中文文件名会让 `open_part` 静默失败（已修）

`Parts.OpenActiveDisplay()` 传**含中文的路径**会**静默失败**（`ok=false`、工作部件不变、不抛异常）。
**已修**：`cmd_open_part` 里加 `_short_path()`，统一转 Windows 8.3 短路径（纯 ASCII）再传。
**写别的文件操作时也要注意**：尽量用短路径或英文路径。

### ★ 不想让用户看到那个半残窗口时：把它最小化

起桥接后用 `ctypes` 调 `user32.ShowWindow(hwnd, 6)`（**SW_MINIMIZE**）。
实测有效，用户全程只看到最终产出的图。

**⭐ 待办（重要）**：这段代码目前只在一次会话的**临时脚本**里
（`%TEMP%\nxtest\cam_bridge.py` 的 `minimize_windows()`），**还没固化进项目**。
下一个 Agent 应该把它做成 `tools/start_bridge.py` 的一个开关（如 `--minimize`）
或默认行为 —— 否则每次都要重写一遍。

**注：`-automation` 模式启动的 NX 窗口是完整的，不需要这么做。**

### ★ 命令速查（本次新用到的）

```bash
python tools/nx_api_lookup.py <关键词>     # 查 NXOpen API 签名（写代码前必查）
python tools/dialog_sweeper.py --once      # 看当前卡着什么弹窗（含成因+解法）
python tools/dialog_sweeper.py --once --pid <PID>   # ★ 只看某个 NX 的弹窗
# 下发任意命令（不只 run_journal）：见 nx-agent 的验收脚本模式
```

**`screenshot` 命令必须传 `out_path`**（否则报「需要 out_path 参数」）。

### 3.6 ★ 企业号演示的 API 事实（2026-10-09 全流程实测，新建模/CAM 必查）

**沙箱回收对策 —— `tools/nxrun.py`（新工具）**：
「起 NX + 跑 journal + 拿回结果」**一次调用完成**（`python tools/nxrun.py <journal.py> [--steps steps.json]`）。
原因：工具调用一结束，拉起的 NX 就被沙箱回收 → 不能"先起桥接再逐条发命令"。
支持 `--steps`（`[{"journal":..},{"cmd":"preview","args":{..}}]`），适合"建模→截图→刀路→截图"串行。

| 坑 | 正解 |
|---|---|
| `OpenActiveDisplay(path)` | **要 2 参** `(path, NXOpen.DisplayPartOption.AllowAdditional)`，且**返回 tuple 要解包** `part, _ = ...` |
| 心跳判活 | `heartbeat.json` 里**没有 online 字段**（那是 `start_bridge.heartbeat_state()` 现算的）→ 判活必须复用该函数 |
| 圆柱布尔 | CylinderBuilder **没有 Target**；`BooleanOption.Type` 两个枚举都报错 → **别设布尔，建独立实体，最后统一合并** |
| 合并实体 | `part.Features.CreateBooleanBuilder(BooleanFeature.Null)` → `Operation=FeatureBooleanType.Unite`、`Target=body0`、`Tool=body1` → `CommitFeature()`（属性名已验证） |
| 布尔相交 | 两实体**表面贴着**会报 `Tool body completely outside target body` → **故意插进对方 2mm** |
| 删特征 | `part.Features.DeleteFeature(f)`；DatumCsys 跳过 |
| ★ CAM 几何重指 | WORKPIECE 引用被删几何时，`GenerateToolPath` **不报错但 0 刀轨**（AskPathExists 全 False）。重指：`cam.CAMGroupCollection.CreateMillGeomBuilder(wp)` → `PartGeometry.GeometryList.Clear()` → `gs = PartGeometry.CreateGeometrySet()` → `PartGeometry.AppendGeometrySet(gs, [body])` → 毛坯同路（BlankGeometry）→ `Commit()` |
| 毛坯遮挡零件 | 不透明方块盖住一切 → **`body.Blank()`**（Ctrl-B）→ 变半透明红框，零件和余量都看得见，**比隐藏更好**。`Layer=100` 没用（默认全图层可见） |
| `ufs.View.FitView()` | **不能无参**：`tag = ufs.View.AskWorkView(); ufs.View.FitView(tag, 1.0)` |
| 空窗口误报 | `blocking_issue()` 会把启动期**无标题空窗口**当未知弹窗 → 已修（标题/正文/按钮全空则跳过） |

**.NET 8.0.31 已装**（downloads/ 里的官方安装包静默装好了），SccS.exe 报错框从此消失。

**企业号完整素材**：`output/enterprise/`（5 个阶段 .prt + enterprise.clsf + shots/ 三张仿真图），
给不会 NX 的人的指南在 `docs/NX演示指南.md`。

### 3.7 ★ 装配在 batch 下**可行**（2026-10-09 实测，推翻"装配未验证"）

`output/enterprise/j_asm_probe2.py`（探针）→ `j_asm_build.py`（真建）→ `j_asm_verify.py`（拿包围盒）。

| API | 实测结论 |
|---|---|
| `session.Parts.NewDisplay(path, NXOpen.Part.Units.Millimeters)` | ✔ batch 下能**新建部件**，返回 tuple 要解包 |
| `part.ComponentAssembly.AddComponent(partName, refSet, compName, Point3d, Matrix3x3, layer)` | ✔ **只传 6 参**（`PartLoadStatus@` 是 out 参数 → 是**返回值**，传了就报 `No overload matches`），返回 `(Component, PartLoadStatus)`；`refSet="Entire Part"`、`layer=-1` |
| `comp.GetPosition()` | ✔ **无参**，返回 `(Point3d, Matrix3x3)`——装配里组件的真实位置+朝向 |
| `NXOpen.Matrix3x3` | 字段可直接赋值：`m.Xx, m.Xy, ... m.Zz`，**行主序**（行 = 新坐标系的轴），列 = 本地轴的世界像 |
| `ufs.ModlGeneral.AskBoundingBox(tag)` | ✔ 返回**扁平 6 元组** `[minx,miny,minz,maxx,maxy,maxz]`（零件自身坐标系）；`AskBoundingBoxExact` 是 2 参签名（别用）；`OpenDisplay(path, None)` 报错 → 用 `OpenActiveDisplay(path, AllowAdditional)` |
| 装配树 | `asm.ComponentAssembly.RootComponent.GetChildren()` ✔ |
| ★ 爆炸图 | `asm.ComponentAssembly.Explosions.Create("名字")` → `Explosion`；`ex.AutoExplodeAssembly(距离)` 自动算爆炸；**`ex.Show(workView)` 才显示**（`ex.Show()` 无参报错；`vw.Explosion = ex` 不存在；UF 另有 `Assem.SetViewExplosion`）；`ex.Hide()` 也是 1 参 |
| 视图切换 | `asm.Views.WorkView.Orient(Matrix3x3)` 可切顶/前/侧视图 |
| journal 内截图 | 直接用 `part.Views.CreateImageExportBuilder()`（见 `bridge/nx_bridge.py:save_screenshot`），**不必回桥接发 screenshot 命令** → 一个 journal 就能边装边出图 |

**架构教训（用户两次纠正后定的规矩）**：
- 装配可视化 = **NX 出数据、网页只画**：`j_asm_build.py` 导出 `assembly_from_nx.json`（origin+matrix），
  `j_asm_verify.py` 导出 `assembly_bbox_from_nx.json`（NX 实测包围盒），
  `tools/render_assembly.py` 只消费这两份，**自己不编一个坐标**，
  并在渲染前用同一套算法离线对一遍包围盒（偏差 0.000mm，故意挪 5mm 会报红）。
- **加工姿态 ≠ 装配姿态**：引擎舱立着铣（三轴够得着），装配时用旋转矩阵横躺。
  手写装配坐标 = 从单件加工版抄数 = 必然错（2026-10-09 被用户抓到两次）。
- **圆柱画法**：只描顶/底两个圆的边 = 用户看到的"两个圆形边"。
  正解：侧面 N 片按法线着色 + 算轮廓线（`tools/render_assembly.py`）。
- 自检新增 `t_assembly_consistency` + `t_assembly_render`（现 23 项），都含反向测试。

### 3.8 ★ 装配 v2：工作流回到 NX（用户第三次纠正，2026-10-09）

用户原话：「工作流尽量不要用网页，而是 nx」「装配为什么会有底板？」「并不像企业号，缺少部件」。

| 意见 | 落实 |
|---|---|
| 别用网页 | 装配过程 = **NX 边装边自己出图**（`j_asm2.py`：AddComponent 一个 → FitView → `Views.CreateImageExportBuilder` 出一张），8 步 8 张，全是 NX 渲染。成品再出 iso/top/front/side 四视角。爆炸图用 NX 的 `Explosions.Create` + `AutoExplodeAssembly` + `Show(workView)`，**存进 .prt**，人在 NX 里能直接拖着看。 |
| 去掉底板 | 底板是**单件加工**的包袱（三轴刀要"从底板往上长"）。装配里零件互相连接：颈部连碟形+工程舰体，支架连引擎舱+工程舰体。v2 无底板。 |
| 补齐部件 | 6 种零件 / 8 组件：工程舰体(Ø26×38)·颈部·碟形主舰体(Ø46×9)·舰桥·支架×2·引擎舱×2。剪影 = 大圆盘 + 下方长圆柱 + 上方小舰桥 + 两侧长引擎舱。 |

- `tools/render_assembly.py --no-html`：**只做几何核对、不出网页**（可视化归 NX，
  这里只当校验器）。8 组件核对偏差 0.000mm。
- 局限要说清：**CAM 逐帧仿真仍做不到 NX 原生**（`Show3dWorkpiece` 只算不显示、
  `ReplayToolPath` 每次停在最终态、截图字节完全相同），所以那部分还得自绘。

## 4. NX 2606 的 API 事实（真机探测 + 文档核实）

**写 NXOpen 代码前，先查 `nx_api_lookup`（离线，0.4 秒出真实签名），
再跑 `nx_probe_capabilities` 读 `state/capabilities.json`。不要凭记忆写 API。**

```bat
python tools\nx_api_lookup.py --limit 4 --summary Postprocess
python tools\nx_api_lookup.py --class CAMSetup
```

它直接检索本机 `NXBIN\managed\NXOpen.xml`（60 MB / 140160 个成员）。
（社区有个 `nxopen-mcp` 用嵌入模型做同样的事 —— 我们本地就有原始文档，不需要装模型。）

已探测到的关键事实：

- **`part.CAMSetup` 在建模部件上不是返回 None，是抛 `NXException 1345023`**。
  `getattr(part, "CAMSetup", None)` 是错的，一律用桥接里的 `get_cam(part)`。
- **`CAMSetup.GetOperations()` 不存在**。枚举加工操作要走
  `GetRoot(View) → 递归 GetMembers() → IsOperation() 过滤 → Tag 去重`
  （已封装为 `iter_operations()`，模板实测枚举出 17 个操作）。
- **`CAMSetup.Postprocess` 是 4 参签名**（operations, post_name, out_dir, OutputUnits），
  老版本 3 参，桥接按 TypeError 分流两种。
- **截图没有 `UF.Disp.CreateImage`**，现行 API 是
  `part.Views.CreateImageExportBuilder()`（实测写出 27 KB PNG）。
- **`GetOperations` 存在，但在 `CAMFeature` / `FeatureGeometryGroup` 上，不在 `CAMSetup` 上**
  —— 这类"名字对但类不对"的坑，肉眼查代码看不出来。
- **新建部件别用 `Parts.NewDisplay`/`FileNew`**：前者文件名不受控会撞名，
  后者要传本地化字符串。桥接的做法是**复制模板 .prt 到目标路径再打开**。
- CAM 加工模板在 `<NX>\MACH\resource\template_part\metric\`（`mill_planar.prt` 等 76 个）；
  建模模板在 `<NX>\UGII\templates\`。

## 5. 怎么扩展（三个固定动作）

**加一个桥接命令**（NX 内能力）：
1. `bridge/nx_bridge.py` 里写 `@command("xxx")` 函数；
2. `mcp/server.py` 的 TOOLS 加声明 + `handle_tools_call` 的 mapping 加一行；
3. `tools/selftest.py` 加对应离线用例（用 `sys.modules["NXOpen"] = 假模块` 注入桩子）。

**加一个校验器**（离线能力）：放 `verify/`，纯文本输入输出，然后按上面 2/3 注册。

**加能力（重要原则）**：**先查 API → 再反射 `fn.__doc__` → 试错。不需要等录制。**

```
1. python tools/nx_api_lookup.py <关键词>     # 查签名和许可要求
2. 不熟的 API 先在 NX 里反射 fn.__doc__          # 比查文档更细，有逐参数含义
3. 写个小 journal 试一次，看真实报错再改         # 报错很具体，改一轮就对了
```

**操作层面的代码 AI 直接写就行** —— 本项目所有已验证能力都是这么来的
（建方块、改尺寸、加特征、`GenerateToolPath`、3D 切除、`OutputClsf`），
**没有一样是靠"先录制再改"做出来的**。

> **用户 2026-10-01 明确指出**：「真实操作录制不需要。网页开发的时候你需要录制什么？不需要吧。」
> —— 这个类比成立。**别再把它写进待办或前置条件。**

### ⚠️ 别把上面这条读成「AI 不许自己写代码」

**AI 完全可以、而且应该自己写 NXOpen 代码。** 桥接里那几个真正的逻辑
（`iter_operations` 树遍历、`get_cam` 兜异常、4 参后处理分流、截图构造器）
全是我直接写出来的，不是录出来的。猜 API 的坑现在也有 `nx_api_lookup` 兜住了。

录制的价值**不是给 AI 语法，是给 AI 工艺规矩**。分界线在这里：

| | AI 自己写代码 ✅ | 必须先看人的做法 ⚠️ |
|---|---|---|
| 性质 | **流程性/确定性**：规则明确，答案唯一 | **工艺性/经验性**：答案在师傅脑子里，不在 API 里 |
| 例子 | 打开/保存/查询/导出/批量改名/拍快照/枚举操作/批量后处理 | 用什么刀、进给转速、走刀策略、留多少余量、怎么避让夹具 |
| 判据 | 成功/失败可自动判定 | 对不对要看几何结果，判据目前还没建 |

一句话：**AI 写得出"调用哪个函数"，但填不进"该填什么工艺参数"。**

**但拿到工艺参数的正路是「问操作者」，不是「录制他的操作」。**
（网页开发也不会去录制用户的点击 —— 用户 2026-10-01 的类比是对的。）

而且问到的答案能直接沉淀成**默认值表**（见 `config/cutting_defaults.json`
+ `tools/cutting_params.py`：按材料/刀具直径算转速进给，并附"该问操作者哪几句话"清单），
**下次同类活儿连问都不用问。** 这比录制可复用得多。

> `templates/electrode_batch.py` 里那个批量循环骨架确实源于一次录制模板，
> 但那是**为了拿"循环 + 失败隔离"的结构**，不是因为写不出建模代码。

### 参数从哪来：三条路，**都不需要等录制**

（这一节也是被用户追问出来的 —— 别再说"没录制就干不了"。）

1. **交互式问（首选）** —— **Agent 对话本身就是交互通道**。
   直接问操作者"这材料是几号？几齿？留多少余量？"，把回答填进下一轮。
   不需要任何新机制、不需要 MCP elicitation。
2. **留空** —— 参数不给就走 NX 模板自带的默认值。
3. **通用知识定初值** —— 已落地：`config/cutting_defaults.json` +
   MCP 工具 `nx_suggest_params`（离线）。按材料/刀具/工序算转速进给切深，
   输出里**附一份"该问操作者哪几句话"的清单**，等于把第 1 条和第 3 条串起来。

```bat
python tools\cutting_params.py --material copper --diameter 8 --operation rough --flutes 3
```

录制的定位因此要改：**不是"必需的原料"，是"加速收敛的先验"** ——
有了它第一轮就接近正确，少问几轮；没有它也能靠"默认值 + 提问"收敛，只是慢。

真正的硬约束只有一条：**改错了要多久才发现**。网页刷新一下就知道，
CAM 得上机床才知道 —— 所以迭代循环必须靠离线判据撑住
（`nc_validate` 体检程序、`nc_diff` 比对改动），而不是靠"参数一开始就对"。

## 6. 测试的坑（都踩过）

- **`NX_AGENT_HOME` 是双重身份**：数据根 + 代码根（`sys.path.insert(HOME/verify)`、
  `config/machine_fanuc.json` 都从它下面找）。临时 HOME 只能给"不碰 nc_validate/nc_diff"的用例。
- 测试一律 `tempfile.mkdtemp()` 临时 HOME，不污染项目 queue/state/snapshots。
- **别用 Git Bash 的 `$$` 当 Windows PID**（MSYS 虚拟 PID，Windows API 查不到）。
- 给 Windows 侧 Python 传路径用 `cygpath -w`。
- **沙箱会在工具调用之间回收子进程**：端到端测桥接必须把"启动→验证→停止"
  写进**同一个** Python 驱动脚本一次跑完（参考 `%TEMP%\nxtest\e2e_live_cam.py` 的模式）。
- docstring 里写 Windows 路径记得加 `r"""` 前缀，否则 `\s` 报 SyntaxWarning。

## 7. 主线卡点与下一步（按优先级）

1. **缺用户厂里的 2~3 个真实 `.nc` 程序（脱敏）** → `nc_lint`/`nc_diff` 的 golden 基准。
   没有它们，校验层只能跑合成样例。
2. **缺用户的工艺参数习惯**（用什么刀、进给转速、留多少余量）→ **直接问，不要录制**。
   问到的答案沉淀进 `config/cutting_defaults.json`。
3. ~~待验证：学生版能否后处理出 NC~~ → **已定论：不能。**
   `MOM: UGPOST License is not available`（2026-10-01 实测，参数已全部调对后的真实报错）。
   完整 API 路径是 `PostprocessWithPostprocessor(ops, .tcl, .def, outfile, units, warning, review)`，
   **商业版可用，学生版卡在许可**。
4. 待验证：桥接长时间常驻（数小时）的稳定性——需要人在真实终端放着跑（用户自己跑）。

## 8. 接手检查清单

- [ ] 跑通 `tools/selftest.py`（16/16）
- [ ] 跑通 `start_bridge.py` + `bridge_probe.py`（三项 PASS）
- [ ] 读 `state/capabilities.json`（没有就先 `nx_probe_capabilities`）
- [ ] 读 `README.md` 第七节（已验证/未验证）
- [ ] 读 `docs/术语表.md`（如果不懂 CAD/CAM 术语）
