# NX-Agent：给 Siemens NX 装上一套自治闭环

把 Siemens NX 接入 Agent，让「自然语言需求 → 建模 → CAM 刀路 → 仿真验证」全程无鼠标完成。
仓库包含驱动层、验证层、回滚层的完整实现，以及在 NX 2606 学生版上的真机验证结果与踩坑记录。

---

## 一、为什么不能直接拿 Agent IDE 去套 NX

Agent IDE（Trae / Cursor / Codex 那一类）能全自动，靠的是四层闭环：
**改代码 → 运行 → 测试自动报错 → git 回滚**。这四层都是白送的。

NX 这边只有第一层是白送的，另外三层必须自己造：

| 层 | Agent IDE | NX 现状 | 本仓库对应模块 |
|---|---|---|---|
| 写脚本 | 现成 | 现成（AI 会写 NXOpen / TCL） | — |
| 驱动 | 现成 | **缺** | `bridge/nx_bridge.py` |
| 验证 | 现成 | **缺**（没有"编译报错"这种东西） | `verify/nc_lint.py`（静态体检）+ `verify/nc_diff.py`（回归比对） |
| 回滚 | 现成 | **缺**（`.prt` 是二进制） | `tools/snapshot.py` |

补齐这三层，Agent 就能真正闭环。**这三层是这套东西的全部价值所在**，
不是"再找个更强的 Agent"。

---

## 二、架构

```
   用户开口（自然语言）
        │
        ▼
   ┌──────────────────┐
   │  Agent（任意 MCP  │   WorkBuddy / Codex / Claude Code / Reasonix
   │  客户端）         │   负责：理解意图、写脚本、看报错、自己改
   └────────┬─────────┘
            │ MCP (stdio / JSON-RPC)
            ▼
   ┌──────────────────┐
   │ mcp/server.py    │   把意图翻译成请求；校验类工作本地做掉
   └────────┬─────────┘
            │ 文件队列  queue/in  <>  queue/out
            ▼
   ┌──────────────────┐
   │ bridge/          │   跑在 NX 进程内，直接调 NXOpen
   │ nx_bridge.py     │
   └────────┬─────────┘
            │ NXOpen API
            ▼
        Siemens NX  ←→  .prt / 刀路 / NC 文件
            │
            ▼
   ┌──────────────────┐
   │ verify/nc_lint   │   上机前的强制关卡（静态体检）
   │ verify/nc_diff   │   改了后处理器之后，跟基准比对，自动分高危/低危
   │ tools/snapshot   │   改动前先拍照，出事就退回去
   └──────────────────┘
```

**注意这两个校验器都是"离线"的**：它们只看纯文本，不需要 NX 在线、
不需要机床、不需要许可证。这是能自动化掉的那一大半验证。

**为什么用文件队列而不是 socket？**
跨机器共享盘也能用；企业网不用开端口（模具厂内网通常不给开）；
NX 版本差异不影响传输层；每一笔请求都在磁盘上留痕，出事能翻账。

---

## 三、目录结构

```
nx-agent/
├── AGENTS.md                        ★ 接手本仓库的 AI agent 先读这份（入职手册）
├── mcp/server.py                    MCP 服务端（Agent 的入口，15 个工具）
├── bridge/nx_bridge.py              跑在 NX 里的桥接 journal
├── verify/nc_lint.py                G 代码静态体检（不需要 NX）
├── verify/nc_diff.py                NC 回归比对：改后处理器后，差异在哪、危不危险
├── tools/start_bridge.py            ★ 一条命令启动 NX + 桥接（自动处理同意框）
├── tools/nx_api_lookup.py            离线查 NXOpen 真实 API（写 NXOpen 代码前必查）
├── tools/selftest.py                全项目离线自检（17 项，改完代码先跑这个）
├── tools/bridge_probe.py            桥接冒烟测试（心跳 + ping + info，人用来排故障）
├── tools/snapshot.py                .prt 快照与回滚
├── tools/preflight_check.py         装机自检（硬件门槛 + 装后关键文件/环境变量）
├── tools/net_probe.py               下载通道连通性探测（DNS/TCP/TLS 三层分开量）
├── templates/electrode_batch.py     录制代码的参数化模板
├── config/machine_fanuc.json        机床参数（行程、转速、进给、刀具表）
├── docs/术语表.md                   所有 NX 专业术语的大白话解释（不懂 CAD/CAM 先读这个）
├── docs/启动NX桥接.md                ★ 怎么把桥接跑起来（人只需要做这几步）
├── docs/下载安装清单.md              链接 + 下载安装 10 步 + 五个坑（要装机就对着这份做）
├── docs/安装NX.md                   学生版/学术版/试用的对比与原理说明（想了解"为什么"读这份）
├── docs/授权方案与风险.md            该用正版/学生版/还是破解版，以及最低风险做法
├── docs/后处理是什么.md              后处理的通俗解释 + 可直接转发给用户的问题清单
├── docs/模拟能替代多少真机.md        "真机跑"拆成五层，哪层能模拟、哪层永远不能
├── samples/DEMO_V1.NC               故意埋了 7 个问题的测试样例
├── samples/POST_GOLDEN.NC           后处理基准程序（golden file）
├── samples/POST_AFTER_TCL_EDIT.NC   改后处理器之后的版本（埋了 2 处高危差异）
├── queue/in, queue/out              请求与响应
├── queue/stale/                     过期的请求（自动隔离，附 .why.txt 说明原因）
├── downloads/                       安装器下载暂存（SSCInst.exe 等，不入版本库）
├── state/                           心跳、能力清单、任务、快照清单
└── snapshots/                       .prt 快照
```

---

## 四、部署

### 0) 装 NX 时的前置条件（实查过，最容易在这里卡住）

- **跑 journal 不需要开发许可。** C/C++/Java 编译运行需要 authoring license，
  但 Python 和 VB.NET 的 journal **不需要**——零许可成本就能开始。
- **journal 必须是单文件，且不能引用外部库。** 这条直接约束架构。
  `bridge/nx_bridge.py` 是单文件 + 纯标准库，正好符合，改的时候别破坏这一点。
- **`run_journal.exe` 文件一定在，但学生版上跑不起来。** 学生版安装包是整体打包的
  （MSI 里只有 4 个组件且全部必装），`run_journal.exe` 属于核心组件，装了就有，
  位置 `%UGII_BASE_DIR%\NXBIN\run_journal.exe`，支持
  `run_journal.exe script.py -args "参数1" "参数2"`。
  网上说的「勾 `Programming Tools → Programming Interfaces`」是**商业版**的步骤，
  学生版没有这个界面，别去找。

  **但 2026-09-30 实测：学生版上它无论如何都拿不到许可。** 见第七节。
  它仍然有用 —— 在**商业版 / 学术版**上它是命令行入口，架构上要留着这条腿。

- **journal 必须单文件、不能引用外部库**（见上面第一条），
  所以「靠 `run_journal.exe` + Windows 任务计划每几秒跑一次」这个方案
  在商业版上成立，在学生版上**不成立**，得改用常驻桥接。
- **NX 用自带的 Python 运行时**，不是系统 Python：
  `NXBIN\python\python312.dll` + `NXOpen.pyd`（NX 2506 对应 Python 3.12）。
  所以 journal 里别指望装 pip 包——本仓库所有脚本都是纯标准库，就是为了这个。
- **装完立刻自检，不通过就别往下走：**
  ```
  python tools/preflight_check.py --auto
  ```
  它会自动找到安装目录，检查 `run_journal.exe`、`NXOpen.pyd`、NX 自带 Python 版本、
  `UGII_BASE_DIR` / `SPLM_LICENSE_SERVER` 两个环境变量。
  装之前也先跑一遍（不带 `--auto`），用来确认硬件够不够。
  手动版等价命令：
  ```
  dir "%UGII_BASE_DIR%\NXBIN\run_journal.exe"
  dir "%UGII_BASE_DIR%\NXBIN\python\NXOpen.pyd"
  echo import NXOpen; print("ok") > test_nx.py
  "%UGII_BASE_DIR%\NXBIN\run_journal.exe" test_nx.py
  ```

### 关于免费版（你自己开发用）

- **Designcenter / NX Student Edition 免费**，含建模和完整 CAM 编程，但有三个硬限制：
  1. **CAM 数据不能后处理**（官方原话：CAM data is not available for post-processing,
     **and the displayed output of tool path information has limited precision**）。
     也就是说你没法用它验证"出 NC"那一段，只能验证到刀路为止。
  2. 输出带水印，且学生版保存的文件**商业版打不开**（反向可以，学生版能读商业版文件；
     学校走完整学术授权包的话也能打开学生版文件）。
  3. 仅 Windows 64 位。**2026 年 7 月起只能通过 Siemens Software Center（SSC）客户端
     下载安装**，旧的"直接下 zip"入口已关闭；且**不支持自动更新和月度补丁**。
- 如果学校有 **NX Academic Edition**（功能等同商业版，且能打开学生版文件），优先用那个。
  工科院校通常有西门子学术授权。
- **开发机和用户的生产机是两件事，别混。** 开发机随便折腾，生产机不要动。

> **「学生版不能后处理」对本项目的实际影响**：`nc_lint` / `nc_diff` 需要真实 `.nc` 文件
> 才能被验证。学生版出不了 NC，所以这两个工具目前只能跑合成样例。
> 解决办法是让用户发两三个真实程序过来当基准（脱敏后即可），
> 这样校验器就能在真数据上迭代。

### 1) 装 NX 的那台机器（用户的电脑）

把整个 `nx-agent` 文件夹拷过去，比如放在 `D:\nx-agent`。

先在**你的开发机**上验证队列通路（这一条不需要 NX）：

```bat
set NX_AGENT_HOME=D:\nx-agent
python bridge\nx_bridge.py --once
```

它会打印「不在 NX 进程内，仅能验证队列通路」，然后正常退出——**这是对的**。
如果它挂住不退，那是 bug。

然后在用户的机器上启动真正的桥接。**一条命令，不用点鼠标：**

```bat
python tools\start_bridge.py
```

它内部做的事：

1. 从注册表 / `UGII_BASE_DIR` 找到 `ugraf.exe`；
2. 看到安装目录里有 `SAAS\` 就自动补上 `-student`；
3. 拉起 `ugraf.exe -student -auto=D:\nx-agent\bridge\nx_bridge.py`
   —— **`-auto=` 是 NX 主程序自带的「启动时自动播放某个 journal」开关，
   学生版完全可用**（这是本项目的关键突破口，见第七节）；
4. 盯着屏幕上有没有弹 `Product Excellence Program` 同意框，有就自动点掉
   （默认点 **Decline**，不代你同意遥测）；
5. 轮询 `state/heartbeat.json` 直到桥接上线，或超时（默认 420 秒）报错退出。

常用参数：

| 参数 | 作用 |
|---|---|
| （不带） | 启动并等到就绪 |
| `--check` | 只看状态，不启动（NX 已在跑时用） |
| `--stop` | 停掉桥接（向队列投 `stop`，再兜底杀进程） |
| `--consent decline` | 同意框自动点 Decline（**默认**） |
| `--consent agree` | 同意框自动点 Agree（你明确要开遥测时才用） |
| `--consent none` | 不碰弹窗，你自己点 |
| `--timeout 600` | 就绪等待放宽到 600 秒（老旧机器开机慢） |
| `--nx-dir D:\NX` | 跳过自动探测，直接指定安装目录 |

启动完用 `python tools\bridge_probe.py` 一条命令验证通没通。

> **手工播放仍然可行**（`Developer 选项卡 → Journal Manager → Browse 选
> bridge\nx_bridge.py → Play`），当你不想让脚本代劳启动 NX 时用。
> 注意手工播放**不带任何命令行参数**，所以 `nx_bridge.py` 的默认模式
> **必须是常驻**——这一点在源码里是刻意这么设计的，别改成默认 `--once`。

**商业版 / 学术版还有第二条路**（学生版不行，见第七节）：
```
NX 安装目录\NXBIN\run_journal.exe  D:\nx-agent\bridge\nx_bridge.py --once
```
配合 Windows 任务计划做外部轮询。**在学生版上这条会报 3615094，别浪费时间。**

#### 常驻的代价：NX 界面会冻结（这是 NX 的限制，不是 bug）

NX 界面是单线程的，journal 跑在 NX 主线程上。常驻轮询期间 NX 窗口会显示
「未响应」，点不动任何按钮。**已经查遍 `NXBIN\managed\NXOpen.xml`（60 MB API 文档）：
不存在 AddTimer / AddIdle / 通用回调这类「让出主线程又保持唤醒」的机制**
（能搜到的 Callback 全是 CAE / 特征 / PDM 的领域专用回调）。

工程上的应对：
- **开两个 NX 实例**：一个用户自己用，一个当机器人。实测学生版**可以**同时开两个
  （2026-09-30 验证：两个 `ugraf.exe -student` 共存 60 秒无冲突）。
- 或者只在需要 Agent 干活的那段时间启动常驻。

停止办法（界面已冻结，点不到按钮）：
- Agent 调 MCP 的 `nx_bridge_stop`（推荐），它往队列投一个 `stop` 请求；
- 或 `python tools\start_bridge.py --stop`；
- 或按 `state/bridge.pid` 里记的 PID 结束 `ugraf.exe`。

#### 桥接为什么会「自己关掉」——两个已修的原因

会看到「NX 起来了、按钮也点了，过一会儿窗口没了」，历史上是这两个原因，
**现在都已修掉并写进了自检**：

1. **队列里残留的过期请求。** 之前投过一个 `stop`，桥接没来得及消费；
   下次启动时它第一轮就把这个陈年 `stop` 读出来执行 → 心跳停在
   `loops=1, processed=2`，看起来像"正常结束"，实为自杀。
   现在 `pending()` 会把超过 `--stale-seconds`（默认 1800 秒）的请求
   **挪进 `queue/stale/` 并附一份 `.why.txt`**，不会再被执行。
   自检项 `t_bridge_stale_queue` 专门守这个。
2. **在 Agent 的沙箱里跑。** 我用工具拉起 NX 时，工具调用一结束
   子进程会被连带回收。**你自己在 cmd 里跑不会这样。**

所以：**请在真实终端里跑一次**，确认它稳定存活：

```bat
cd /d D:\nx-agent
python tools\start_bridge.py
python tools\bridge_probe.py
```

`bridge_probe.py` 会给出 `心跳 / ping / info` 三项的 PASS/WARN/FAIL。
三项全 PASS 就说明桥接是真活了。让它开着，然后去 Agent 那边正常对话。

#### 可选参数

| 参数 | 作用 |
|---|---|
| （不带） | 常驻轮询 —— 手工播放就用这个 |
| `--once` | 处理完当前排队请求就退出（自检/批处理用） |
| `--seconds=N` | 常驻的最长生存期，到点自行退出。0 = 不限（默认） |
| `--interval=S` | 轮询间隔秒，默认 0.5 |
| `--max=N` | 处理满 N 个请求后退出（调试用） |
| `--stale-seconds=S` | 超过 S 秒的排队请求视为过期并隔离，默认 1800 |

### 2) 你的机器

**先建虚拟环境**（本项目零第三方依赖，建环境是为了干净，不是缺包）：

```bash
# 只在第一次做
python -m venv C:\Users\lt\.workbuddy\binaries\python\envs\nx-agent
```

**跑全项目自检（17 项，不需要 NX）**：

```bash
C:\Users\lt\.workbuddy\binaries\python\envs\nx-agent\Scripts\python.exe tools/selftest.py
```

这一步是**装 NX 之前的强制关卡**：它把校验层本身先验一遍。
如果自检就不过，等 NX 装好了你分不清是 NX 的问题还是校验器自己的 bug。

它覆盖：语法、`nc_lint` 抓错与不误报、`nc_diff` 高危/低危分离、
MCP 握手与工具调用、桥接离线快速失败、桥接在 NX 外的优雅报错、
快照与回滚（三种指定方式）、录制模板的批量循环与失败隔离、装机自检脚本本身。

**单跑校验器**：

```bash
python verify/nc_lint.py samples/DEMO_V1.NC --config config/machine_fanuc.json
python verify/nc_diff.py samples/POST_GOLDEN.NC samples/POST_AFTER_TCL_EDIT.NC
```

接进 Agent（以 WorkBuddy 的 `~/.workbuddy/mcp.json` 为例，Codex / Claude Code 同理）：

```json
{
  "mcpServers": {
    "nx-agent": {
      "command": "python",
      "args": ["D:/nx-agent/mcp/server.py"],
      "env": { "NX_AGENT_HOME": "D:/nx-agent" }
    }
  }
}
```

写完配置**必须去连接器管理页右上角的"自定义连接器"里点一次"信任"**，否则不会生效。

> 关键：两边的 `NX_AGENT_HOME` 必须指向同一个目录。
> 脚本默认自定位（跟着自己所在的文件夹走），所以只要两边都是
> `nx-agent` 文件夹的根，不配环境变量也能对上。

---

## 四又二分之一、开发循环：相当于网页开发的「刷新一下」

网页：改代码 → 刷新浏览器 → 人看 → 提要求 → 再改
**本项目**：改 journal / 改参数 → `nx_preview` → `preview_gallery.py` 出一页 HTML → 人看 → 提要求 → 再改

```
Agent 改完  →  nx_preview（重播刀路 + 3D 材料切除 + 每步截图）  →  state/preview/*.png
                                                                        ↓
             人说"这里多切了一刀"  ←  tools/preview_gallery.py 生成 HTML，浏览器打开
```

**`nx_preview` 已经能跑了**（2026-10-01 真机实测，0.2 秒返回）。
NX 没有"浏览器式的结果页"，所以自己做了 `tools/preview_gallery.py`：
把每次预览的图按时间排成一页，带时间戳和步骤标签（刀路重播 / 3D 切除 / 当前视图）。

> ⚠️ 实测踩到的一个会把桥接搞死的坑：对**没生成刀路**的操作调重播，
> NX 会弹模态框 → journal 卡在主线程 → **桥接心跳停 91 秒，连 stop 都投不进去，只能杀进程**。
> 现在 `preview` 会先用 `AskPathExists()` 过滤，没刀路就只截当前视图并说明原因。

### 「操作软件」这一步跳不跳得过？

**启动那一步：已经跳过了**（`ugraf.exe -auto=`，零点击）。
**运行期间的弹窗：跳不过** —— 这是 NX 的架构约束（图形界面单线程，journal 跑在主线程，
已查证 NXOpen 里没有定时器/回调可以绕开）。

但正确应对不是"绕过弹窗"，而是**在软件外面盯着它**：

```bat
python tools\dialog_sweeper.py --once     :: 诊断：现在屏幕上有什么模态框
python tools\dialog_sweeper.py --watch    :: 常驻清扫（桥接开着时另开一个窗口跑）
python tools\dialog_sweeper.py --log      :: 看历史：都弹过什么、怎么处理的
```

原理：**NX 内部的代码救不了自己**（它正在等那个框），但**外部进程可以**——
卡住的只是 NX 的主线程，Windows 上别的进程照样能枚举它的窗口、按下按钮。
安全策略：已知弹窗选**保守**项（遥测→Decline，是否保存→No）；
**未知弹窗绝不乱点**，只记录并告警；只剩"同意/覆盖"这类按钮时不动。全程留痕。

---

## 五、自主性是怎么长出来的

这是整套设计的核心，一句话：

> **别让 AI 猜 NXOpen，让 AI 改已经跑通过的代码。**

```
用户在界面上操作一遍  →  录成 journal  →  Agent 参数化  →  入库
        ▲                                                  │
        └──────── 跑不通就把报错回传，Agent 自己改 ◄─────────┘
```

1. 用户提需求（或 Agent 主动问"有哪件事你天天重复做"）
2. **Agent 直接写 NXOpen 代码**：先查 `nx_api_lookup` → 不熟的在 NX 里反射 `fn.__doc__`
   → 写个小 journal 试一次 → 照真实报错改
3. 跑通了 → 存进脚本库 → 下次 Agent 直接调用（可参数化 + 批量循环）

**每跑通一次，Agent 的能力库就大一块。** 这才是可持续的路径。

> **★ 不需要「录制人的操作」**（用户 2026-10-01 纠正，类比很准）：
> 「网页开发的时候你需要录制什么？不需要吧。」
> 本项目所有已验证能力都是**直接写 API** 得来的，一次录制都没用：
> 建方块、改尺寸、加特征（加放电头）、生成刀路、3D 切除仿真、导出刀路数据。
> **录制只在「API 查不到、文档也没有」的极少数角落才有兜底价值，不该写进前置条件。**

### ⚠️ 唯一真正拿不到的是「工艺参数」，而那是**问**来的，不是录来的

这一点纠正过一次（用户 2026-10-01 追问出来的）：**参数不是卡点。** 三条路都能拿到参数：

1. **交互式问** —— Agent 对话本身就是通道。用户跟 Agent 说话，Agent 就能问
   "转速多少 / 几齿 / 留多少余量"，把回答填进下一轮。**这条现在就能用，不需要任何新机制。**
2. **留空** —— 参数不给就走 NX 模板自带的默认值。
3. **凭通用知识定初值** —— 和网页 Agent 先给 `padding: 16px` 一模一样的打法。
   已落地：`config/cutting_defaults.json` + MCP 工具 `nx_suggest_params`，
   按材料/刀具/工序算出转速进给切深，**并附一份"该问操作者哪几句话"的清单**。

所以录制的真正价值是把"第一轮就接近正确"变成可能（少问几轮），而不是"没有就干不了"。

真正的硬约束只有一条：**改错了要多久才发现**。网页刷新一下就知道，CAM 得上机床才知道 ——
所以这条循环必须靠离线判据（`nc_validate` / `nc_diff`）撑住，而不是靠"参数一开始就对"。

---

## 六、安全边界（不可协商）

| 环节 | 谁负责 |
|---|---|
| 写脚本、改参数、批量出文件 | Agent |
| 生成刀路、出 NC | Agent（在 NX 里算） |
| **G 代码上机前的体检** | `nc_lint.py`，不通过就不许出 |
| **改了后处理器的回归比对** | `nc_diff.py`，出现高危差异就不许上机 |
| **仿真 + 机床空运行** | 人 |
| **工艺决策、余量、装夹、签字** | 人 |

其中"改后处理器"是最容易出隐蔽错误的一步（你以为只加了注释，实际动了进给），
`nc_diff.py` 把这一类改动变成纯文本回归测试——**不需要机床就能拦住**。
细节见 `docs/模拟能替代多少真机.md`。

自动化上限（现实值，不是营销值）：

| 任务类型 | 能做到 | 备注 |
|---|---|---|
| 程序单 / 电极清单 / 工艺卡 | ~95% | 纯文本，风险为零 |
| 批量后处理 / 出图 / 归档 | ~90% | 批量循环骨架已备（`templates/electrode_batch.py`）|
| 标准件、电极等规则化建模 | ~80% | 规则越明确越高 |
| 刀路策略生成 | ~65% | 必须人审，必须仿真 |
| **工艺决策与责任签字** | **0%** | 别碰。一块模料报废够做几百次手工 |

---

## 七、已经验证过 / 还没验证的

诚实起见，分开列：

**已在本机跑通（不需要 NX）**
- MCP 协议握手、`tools/list`（14 个工具）、`tools/call`
- `nc_validate` 全流程，能准确抓出样例里的 7 个问题
- `nc_diff` 全流程，能准确把 2 处高危差异（改动的进给、新增的刀长补偿取消）
  和 2 处低危差异（新增的两行注释）分开；高危时返回 `isError=true` 当关卡拦
- 队列往返：Agent → `queue/in` → 桥接 → `queue/out` → Agent
- 桥接不在线时**立即报错**，不会干等卡死；心跳超过 30 秒判为 stale 并说明原因
- 结构化错误回传（`BridgeError` / `NXOpenError` + 完整 traceback）
- `.prt` 快照与回滚（回滚前自动备份现状）
- 桥接的默认模式与降级：NX 外不带参数跑会**自动降为 `--once` 并正常退出**
  （不会挂住），参数 `--seconds/--interval/--max` 解析正确
- 过期请求隔离：1 小时前的 `stop` 被挪进 `queue/stale/` 并附 `.why.txt`，
  同批的新鲜 `ping` 照常执行，心跳如实记 `stale_skipped=1, processed=1`
- 桥接 CAM 树遍历（假对象离线模拟）：四棵树递归 + Tag 去重 + 异常分支跳过 +
  GetOperations 快车道
- `tools/selftest.py` **16/16 全通过**（项目 venv 与用户 miniconda 两边都验过）

**已在真实 NX 2606 上跑通（2026-10-01，三轮端到端实测）**
- `start_bridge.py` 一键启动：**12 / 13 / 16 秒**就绪，零许可报错、零点击
- `ping` 往返 1.35 秒；`NX 版本=v2606`；`state/capabilities.json` 正常落盘
- `nx_new_part`：复制 `mill_planar.prt` 模板（CAM 加工模板）→ 打开 → 成为工作部件 ✅
  （首次打开约 35 秒，NX 懒加载建模环境，之后秒级）
- `nx_probe_capabilities`：完整能力面探测 ✅（详见下面的 API 事实表）
- `nx_list_operations`：**从模板里枚举出 17 个真实加工操作** ✅
  （FACE_MILL_MIDPASS / PLANAR_MILL / POCKETING / DRILL...，
  这就是 `iter_operations()` 走 `GetRoot+GetMembers+IsOperation+Tag去重` 的实机证明）
- `nx_screenshot`：`Views.CreateImageExportBuilder` 写出 27 KB PNG ✅
- `nx_save_part` ✅
- `nx_postprocess`（空刀路）：优雅报错 ✅ —— 但注意报的是
  `MOM can not open output file`，这是**没生成刀路**的表现，不是许可证限制

**装机现状（2026-09-30 晚，已重装到 D 盘并激活成功）**

装的是 **Designcenter Student Edition 2606**，位置 **`D:\NX`**，22.11 GB。
（先前 C 盘的 `C:\Program Files\Siemens\DcStudentEdition2606` 已卸载，
重装时用 `INSTALLDIR=` 指定，SSC 已认出并记录 `INSTALLDIR="D:\NX\"`。）

| 项目 | 结果 |
|---|---|
| `NXBIN\run_journal.exe` | 在 ✅ |
| `NXBIN\ugraf.exe` | 在 ✅ |
| `NXBIN\python\NXOpen.pyd` | 在 ✅ |
| NX 自带 Python | **3.12**（`python312.dll`） |
| `MACH\auxiliary\runugpost.exe` | 在（**单 l**，不是文档里写的 `auxilliary`）✅ |
| `MACH\resource\postprocessor\template_post.dat` | 在 ✅ |
| `SAAS\` 目录 | 在（SaaS 授权模式标志）✅ |
| `NXBIN\managed\` | 在，含 `NXOpen.dll`(26 MB) + **`NXOpen.xml`(60 MB 完整 API 文档)** ✅ |

**激活已成功。** SSC 日志 21:32:43 用新鲜令牌（有效期到 22:02）跑完激活，零失败记录；
`%LOCALAPPDATA%\Siemens\Splm\` 出现 `NX-info.data`(25.6 KB) + `NX-meta.data`；
`ugraf.exe -student` 图形界面启动后持续存活 120 秒以上 → **图形界面下许可证正常**。

### ★ 关键结论：命令行 journal 在学生版上是死路（实测，别再试）

`run_journal.exe` **无论如何都拿不到许可**，报错恒定：

```
Caught unexpected error: 3615094: Unable to reserve license to run journal file
```

穷举测试过的变体，**全部同一个错**（且都在 1 秒内立即失败，根本没进 NX）：

| 变体 | 结果 |
|---|---|
| `run_journal.exe hello.py` | ❌ 3615094 |
| `run_journal.exe -nx hello.py` | ❌ 3615094 |
| `UGII_USE_NXSAAS_MODE=1 run_journal.exe hello.py` | ❌ 3615094 |
| `SPLM_LICENSE_SERVER=29000@LocalHost ...` | ❌ 3615094 |
| `SPLM_LICENSE_SERVER=28000@localhost ...` | ❌ 3615094 |

注意这与之前的 `SPLM_LICENSE_SERVER needs to point to a license server`
**性质不同**：那条是「没有有效授权」，这条是「有授权，但命令行模式无法保留
journal 这种许可」。**这是学生版的硬限制，不是配置问题。**

→ **结论：驱动层必须寄生在一个已经拿到许可的 NX 图形会话里。**
`bridge/nx_bridge.py` 因此改为**默认常驻模式**（手工在 Journal Manager 里播放时
不带命令行参数，所以默认值必须是常驻）。

### ★★ 突破口：`ugraf.exe -auto=<journal>`（学生版可用，零点击）

上面那条死路只堵住了 `run_journal.exe` 这一个入口。**NX 主程序自己有一个开关，
能在启动时自动播放指定的 journal**，它走的是正常图形会话的许可，学生版完全可用：

```bat
"D:\NX\NXBIN\ugraf.exe" -student -auto="D:\nx-agent\bridge\nx_bridge.py"
```

实测结果（2026-09-30）：

| 项目 | 结果 |
|---|---|
| 启动到桥接就绪 | **13 ~ 40 秒**（首次冷启动慢） |
| 许可报错 | **无** |
| 需要点鼠标 | **零点击**（同意框例外，见下） |
| 会话性质 | **真 GUI 会话**，持有完整许可，`NXOpen` 全部可用 |
| `ui.DisplayMessage` / `ui.SetStatus` | ✅ 实测可调用 |
| `state/capabilities.json` | ✅ 正常落盘 |

**这就是 `tools/start_bridge.py` 的内核。** 三件事必须注意：

1. **同意框会挡路。** 首次启动（或把 `value` 改回 `true` 之后）弹出
   `Product Excellence Program` 对话框（class `#32770`），**它阻塞启动**，
   不点掉的话 NX 就停在那儿、桥接永远不上线。表现就是"有时成功有时失败"。
   `start_bridge.py` 默认自动点 **Decline**。永久关掉的办法见 `docs/启动NX桥接.md` 第三节。
2. **`-auto=` 的路径必须是绝对路径**，且路径里有空格要加引号。
3. **`run_journal.exe` 不认识 `-student`**，`ugraf.exe` 才认识。别写串了。

### 为什么外部调用 NX 内核也不行（第三块铁证）

有人会想：既然 journal 能在 NX 里跑，那我在外面用 `ctypes` 直接调 NX 的
底层 DLL 不就行了？实测**不行**：

```python
ctypes.WinDLL("libufun.dll").UF_initialize()   # → 3600045
```

```
3600045: No C++ author or Designcenter Open development license is available
          to be able to run this native application.
```

这是 NX 自己给出的解释：**外部进程调用 NX 内核需要开发者授权**，
学生版没有这个授权。所以「界面外驱动 NX」这条路在学生版上被明确关掉了，
必须走「界面内跑脚本」——也就是上面的 `-auto=`。

### 顺带验证出来的三件事（都是决定架构的）

**1. `ugraf.exe -student -automation` 能拿到许可并长期存活** ✅
实测持续存活 90 秒被外部超时杀掉（不是自己崩的）。
意义：NX **有无界面运行能力**。但它启动后**没有暴露任何监听端口**，
要真正用起来得自己写外部 NX Open 客户端（.NET + `NXBIN\managed\`），
成本较高 —— 记为**将来的升级方向**，不是现在的路。

**2. 学生版可以同时开两个 NX 实例** ✅
实测两个 `ugraf.exe -student` 共存 60 秒无冲突。
意义：**「机器人会话」方案成立** —— 用户自己那个 NX 照常用，另一个专职跑桥接。

**3. NX 里不存在「非阻塞定时器」** ❌
查遍 `NXBIN\managed\NXOpen.xml`：搜 `Timer` 只命中一个运动仿真图表控件属性，
搜 `Idle` 只命中 Tooling 的工位增删方法，`AddTimer` / `AddIdle` / 通用回调**一个都没有**
（670 处 `Callback` 全是 CAE / 特征 / PDM 的领域专用回调）。
→ **常驻期间 NX 界面必然冻结**，绕不开。应对办法见第四节。

**4. 菜单文件不能启动脚本** ❌
`UGII\menus\*.men` 里 459 处 `ACTIONS` **全是 `STANDARD`**。
所以「加个菜单按钮跑 journal」这条路走不通，别去翻 `.men` 语法了。
（正经的入口是 Journal Manager，或商业版/学术版上的 `run_journal.exe`。）

`run_journal.exe` 的完整用法（实测 `-help` 输出，留着给商业版用）：

```
run_journal [ -pim ] [ -nx | -simcenter3d ] [ -r=<filename>[,lang=..][,format=..] ]
            [ -allow_redo ] [ -localize ] <journal-file> [ -args .... ]
```

**注意它不认识 `-student`** —— 那个参数是给 `ugraf.exe` 主程序的学生版入口。
(SSC 记录里 `launchEXE = NXBIN\ugraf.exe`、`launchCommand = -student`，
即正确写法是 `ugraf.exe -student`。之前从 .lnk 里粗暴提取出的 `-studentB` 是解析误差。）

### 学生版授权：和商业版完全是两套东西（实测结论）

- 学生版**不装 FlexLM 许可证服务器**：安装目录里没有 `lmgrd.exe` / `ugslmd.exe`，
  全盘也没有任何 `.lic` 文件。**不靠 `SPLM_LICENSE_SERVER`**，
  所以那个环境变量「没设置」是正常的。
- 授权信息登记在 `HKLM\SOFTWARE\Siemens\NXStudent 2606`：

  ```
  INSTALLDIR    = D:\NX\
  LANGUAGE      = english
  LICENSESERVER = 29000@LocalHost
  UGII_BASE_DIR = D:\NX
  ```

  那个 `29000@LocalHost` 是 **SALT 授权组件**的地址，不是传统许可证服务器；
  实测 **29000 端口没有进程监听也是正常的**。
- 走的是 **SALT 云端激活**：SSC 登录 → 服务器下发凭证 → NX 凭凭证启动。
- **重装时踩过的坑留个记录**：第一次激活失败（`SALT_status_bad_credentials`），
  成因是下载+安装拖了 54 分钟、SSC 登录令牌中途过期，激活拿过期令牌被拒。
  **文件全在，只是没许可证**。重装时 SSC 自动重新激活即成功。
  解法见 `docs/下载安装清单.md` **第七节**，`tools/preflight_check.py` 已内置这条检查。

### NX 启动时的「同意框」（会阻塞启动，必须处理）

NX 首次启动会弹 **Product Excellence Program**（产品卓越计划）遥测同意框：

- 窗口类 `#32770`（标准对话框），标题含 `Product Excellence Program`，
  按钮 `&Agree` / `&Decline`；
- **它是模态的，不点掉 NX 就停在启动画面**，`-auto=` 的 journal 也不会开始跑；
- 选择结果存在
  `%LOCALAPPDATA%\Siemens\Designcenter Student Edition2606\Designcenter Student Edition_user.dpv`：

  ```xml
  <Pref Application="Gateway" Category="General" Tab="Data Privacy"
        name="NX_ProductExcellenceProgram"
        title="Product Excellence Program - Participate" value="true"/>
  ```

  `value="true"` = 已同意（不再弹）；`value="false"` = 已拒绝（也不再弹）。
  **只有这个键「不存在」时才会弹。**
- 两种处理法：
  1. GUI 路径：NX → 首选项 → 用户默认设置 → Gateway → General → Data Privacy；
  2. 直接改上面那个文件（改成 `true` 或 `false` 都行）；
  3. 或者交给 `tools\start_bridge.py --consent decline`（**默认行为**）。

> 我让启动器默认点 **Decline**，而不是 Agree —— 是否上报遥测该由你决定，
> 一个自动化脚本没有资格替你同意。

### ★ NX 突然「要你重新登录」：不是授权过期（2026-10-01 真机实录）

装好用了两天，某次启动 NX 突然要求重新登录 + 验证，第一反应是「授权过期了」——
**不是**。学生版是云端授权，**授权和登录是两层东西**：

| 层 | 打个比方 | 有效期 | 存在哪 |
|---|---|---|---|
| **授权 license** | 软件许可证 Key | **1 年** | `C:\ProgramData\Siemens\SoftwareCenter\data\machineData.json`（没有 `expiration` 字段 = 没过期）|
| **登录令牌 token** | 微信的登录态 | 短（几小时~几天） | `%LOCALAPPDATA%\Siemens\ssc_session\token`（**DPAPI 加密**，绑这台机器 + 这个账户）|

而**发授权的那个服务进程，是由 SSC（Siemens Software Center）托管的**。
SSC 一旦退出，服务就没了，NX 会弹：

```
Designcenter Student Edition Initialization Error
Running instance of the server not found.
```

实测的完整因果链：

```
SSC 刷新令牌失败  → SSC 退出 → 授权服务没了（28000/29000 端口无人监听）
    → NX 报 "server not found" → 你看到「要重新登录」
```

**修法（按顺序）**：

1. 点掉那个错误框，关掉起不来的 NX；
2. 启动 **Siemens Software Center**（开始菜单搜，或运行
   `C:\Program Files\Siemens\SoftwareCenter\SiemensSoftwareCenter.exe`）；
3. SSC 要求登录就登录一次（就是当初注册学生版那个账号）；
4. 登录成功后，**从 SSC 里面点启动 NX**，别直接双击 NX 图标；
5. 想确认服务回来了：`netstat -ano | findstr 28000` —— 有 `LISTENING` 就对了。

**这类弹窗脚本已经认得**：`tools\dialog_sweeper.py --once` 会直接把成因 + 上面这 5 步
打印出来；`start_bridge.py` 等待期间每 10 秒查一次，一发现就以**退出码 3** 报出来，
不会让你干等到超时。

### NX 2606 的 API 事实表（真机探测 + 文档核实，2026-10-01）

写 NXOpen 代码前先跑 `nx_probe_capabilities` 读 `state/capabilities.json`。**不要凭记忆写 API。**

| 你以为的 | 2606 实测 | 正确做法 |
|---|---|---|
| `part.CAMSetup` 没有时返回 `None` | ❌ **抛 `NXException 1345023`** | 用桥接的 `get_cam(part)`（try/except 包住） |
| `cam.GetOperations()` 枚举加工操作 | ❌ **不存在** | `iter_operations()`：`GetRoot(View)` → 递归 `GetMembers()` → `IsOperation()` 过滤 → `Tag` 去重 |
| `cam.Postprocess(ops, name, dir)` 3 参 | ❌ **4 参**（多 `OutputUnits`） | 桥接按 TypeError 分流两种签名 |
| `UF.Disp.CreateImage` 截图 | ❌ **不存在** | `part.Views.CreateImageExportBuilder()` |
| `Parts.NewDisplay` 新建部件 | ⚠️ 文件名不受控会撞名 | 桥接：`复制模板 .prt → OpenActiveDisplay` |
| `FileNew` 构造器新建部件 | ⚠️ 要传本地化字符串 | 同上，复制模板最稳 |
| `Undo` / `session.NewDisplay` | ❌ 属性不存在 | —（探测结果为 false，别依赖） |

CAM 加工模板：`<NX>\MACH\resource\template_part\metric\`（`mill_planar.prt` 等 76 个，
模板自带 17 个预定义加工操作，可当枚举/遍历的试验田）。
建模模板：`<NX>\UGII\templates\`。

### 还没验证

- **学生版能否真的后处理出 NC** —— 官方说不能（`CAM data is not available for post-processing`）。
  `CAMSetup.Postprocess`（4 参）和 `MACH\auxiliary\runugpost.exe` 都在，
  **值得用带真实刀路的部件试一次**。注意：空刀路会报 `MOM can not open output file`，
  那**不是**许可证限制的证据，别误判。
- **桥接长时间常驻（数小时）的稳定性** —— 需要人在真实终端里放着跑一次。
- **`GenerateToolPath` / `ParallelGenerate`** —— 方法存在（探测 true），
  但还没在带几何的真实部件上跑过生成→后处理的完整链路。

### 大概率需要的调整

- `run_journal` 这条指令的失败模式要多分辨一层：许可证问题 vs 脚本问题 vs
  「该 NX 版本 API 名字不同」。现在桥接会把原始报错 + 完整 traceback 回传。
- 自带 Python 是 3.12，本项目脚本只用标准库，语法上没有问题。

---

## 七点五、GitHub 上已有 NX 的 MCP 项目，为什么不直接换过去

查过（2026-10-01），社区确实有，而且**有一个质量相当高**。逐个说清楚：

| 项目 | 干什么 | 对学生版 + 用户的 CAM 场景 |
|---|---|---|
| **`DreamEnding/NX_MCP`**（96★，MIT，活跃维护到 2026-09-30） | 外部 Python sidecar（FastMCP/MCP SDK v2）+ NX 内 journal bridge（127.0.0.1 + 256-bit token 认证），默认 16 个工具 | 架构**跟本项目独立得出的结论一致**（外部进程 + NX 内 journal），这算交叉验证。但：① 它的 bridge 官方启动方式是 **`run_journal.exe`** → 学生版固定报 3615094，直接用不起（可改用 `-auto=` 绕，但它没试过也不支持）；② 工具面是**纯 CAD 建模**（草图/拉伸/装配/制图/测量/STEP），**零 CAM**，而用户的主活是 CAM 编程与后处理；③ 没有判据层（`nc_lint`/`nc_diff`）与回滚层 |
| **`mingfeng6684/nxopen-mcp`** | 不是驱动 NX，是给 agent 提供 **NXOpen API 知识检索**（BGE-M3 + sqlite-vec，从你本机装的 NX 抽取文档建索引） | 思路**完全对**，但那要下几百 MB 嵌入模型。**我们本机就带着同一份原始文档**（`NXBIN\managed\NXOpen.xml`，60 MB / 14 万条成员），直接文本检索更快更准 —— 已做成 `tools/nx_api_lookup.py` + MCP 工具 `nx_api_lookup`，不用联网不用装模型 |
| **`TQJ2007-git/nx-mcp`** | 目标实时模式（HTTP + NX 内 journal server），但**当前只有批量模式**：直接 `run_journal.exe` 跑脚本 | 学生版上直接死（3615094） |

**结论：不换，但吸收。**

- **不换**：因为本项目要解决的是"**CAM + 判据 + 回滚**"，社区方案解决的是"CAD 建模"，交集只有建模那一块；而且学生版的驱动入口是 `-auto=`，它们的 official 路径是 `run_journal.exe`。
- **吸收（值得抄的工程实践）**：
  1. **workspace 禁闭**：所有文件参数限制在工作区内，穿越/绝对路径/软链一律拒（我们有快照回滚，但缺这一层边界）。
  2. **失败即回滚 + undo mark**：每次模型修改都打可见的 undo 标记，失败自动回滚。
  3. **超时语义区分** `not_started` / `unknown`：不确定执行绝不自动重放。
  4. **real-NX 验收门**：mock 测试与"真的在 NX 上跑过"分开标记，后者才是发布门槛 —— 对应我们的 `tools/bridge_probe.py` 与真机 e2e 驱动脚本。
  5. **API 知识检索**（已落地：见下）。

### 落地的第一条：写 NXOpen 前先查，别猜

```bat
python tools\nx_api_lookup.py --limit 4 --summary Postprocess
```

```
[方法] NXOpen.CAM.CAMSetup.Postprocess(NXOpen.CAM.CAMObject[],System.String,System.String,NXOpen.CAM.CAMSetup.OutputUnits)
     Postprocess
     Created in NX4.0.0 License requirements: cam_base ("CAM BASE")
```

MCP 里也有同名工具 `nx_api_lookup`，Agent 写任何 NXOpen 代码之前应该先调它。
它要是不存在，今天踩的这 6 个坑一个都不会有：

```
CAMSetup 上没有 GetOperations（GetOperations 在 CAMFeature 上）
Postprocess 是 4 参不是 3 参
UF.Disp.CreateImage 已被删除（现用 Views.CreateImageExportBuilder）
part.CAMSetup 无 CAM 设置时抛异常而非返回 None
```
（`--class CAMSetup` 列全部成员；首次建索引约 4 秒，之后走缓存 0.4 秒。）

---

## 八、为什么"只装 MCP"不够"不够

常见误解：以为全自动的缺口就是"缺个 MCP"。实际缺口是三个，MCP 只解决第一个：

| 缺口 | 谁来解决 | 现状 |
|---|---|---|
| 1. 通道：Agent 怎么把指令递进去 | MCP + 文件队列 | 本仓库已提供（`mcp/` + `bridge/`） |
| 2. 能力：怎么可靠地把活干成一件事 | 见下方"三层模型" | **Agent 直接写 + 沉淀成封装** |
| 3. **判据：Agent 怎么知道这次做对了** | 只能自建（`nc_lint` + `nc_diff` + 几何算法） | **最难，且无现成方案** |
| 4. 回滚：做错了怎么退回去 | `tools/snapshot.py` | 已提供 |

判断标准很简单：
**Agent 能不能自己发现"这次做错了"？不能的话，那不是全自动，只是自动执行了一次。**

### 更准确的说法：不是"缺能力"，是"缺标准库"

NX 的原语（API）其实是全的，说"缺能力"不准确。准确的是三层：

| 层 | 内容 | 谁提供 |
|---|---|---|
| **原语** | `CreateBlockFeatureBuilder`、`GenerateToolPath` 这类单个函数 | **软件白送**（NX Open，几千个函数） |
| **编排** | 把一串原语按正确顺序和上下文拼成"干成一件事" | **Agent 直接写**（查 API + 反射 + 试错）|
| **能力封装** | 命名 + 参数 + 前置检查 + 后置校验，可安全反复调用 | **要自己造** |

**★ 2026-10-01 的修正**：录制**能**录到一次编排快照，但**编排本身 AI 写得出来**。
当天的证据：「加一个放电头」= 找特征 → 建 builder → 设几何 → 布尔求和 → 提交，
这是一串编排，**Agent 一次写对，没看任何录制**。
所以录制不是"编排的唯一来源"，只是"某次编排的快照"。

录下来的代码参数写死、对象引用写死，只是一次特定条件下的编排快照；
要变成可复用的能力，必须参数化 + 泛化 + 加校验。

类比：**NX 有 syscall，但没有 stdlib，也没有包管理器。**
一段录制出来的 journal 相当于一次 strace 记录；
你要造的是 `create_electrode(name, x, y, z, stock)` 这样的库函数。

原语多 ≠ 能力够，原因有三：

1. **调原语必须知道模型当前的确切状态**（有没有 work part、布尔运算的 target 是谁、
   有没有激活的草图）。人靠眼睛看对话框就知道，Agent 必须自己去查——所以需要
   `probe` 和 `part_info` 这类"读状态"的工具。
2. **原语的报错是给程序员看的**："Boolean operation failed" 不告诉你为什么、下一步改什么。
   这也是为什么回传给 Agent 的必须是原始 traceback 加上下文。
3. **原语没有业务语义。** "做这个电极"在 API 眼里是几十个原语调用。业务语义要自己封。

### 判据：算法为主，视觉为辅

- **G 代码的判据是纯算法问题，不需要视觉。** 坐标越界、安全平面下快速移动、
  转速进给越界、刀补未取消——都是符号级确定性判断（`verify/nc_lint.py` 已实现）。
- **3D 模型的判据分两类：**
  - **算法可判（精确、可复现）**：几何度量（尺寸、壁厚、包围盒、体积）、
    拓扑（是否封闭实体 / 破面 / 自相交）、刀路扫掠体与模型的布尔差集（过切欠切多少）、
    干涉体积。
  - **视觉模型可判（粗、不稳定）**：渲染图像不像、曲面顺不顺、外观缺陷。
- **判据必须确定性。** 视觉模型的判断不可复现——同一条刀路问两次可能给出不同结论。
  Agent 的自治循环靠反馈信号收敛，判据一旦随机，循环就退化成随机游走。
  **把确定性的几何问题交给概率模型判断，是把问题降级。**
- 例子：判断"这把刀会不会切到不该切的地方"，正解是把刀具体（圆柱/球）沿刀路扫出的体
  与模型求布尔交，交集体积 > 0 即干涉。答案精确到浮点且完全可复现。
  让视觉模型"看渲染图判断会不会撞"，得到的是一句"看起来可能有风险"——
  既不可复现，也无法定位是哪个 G 代码块的问题。
- 所以：**视觉用来给人看、用来发现"明显怪"，不是判据主体。**

三个瓶颈按严重程度排序（对应"看不见 3D 模型"那一问）：


1. **没有客观对错判据**（最严重）。浏览器里对错有客观答案：DOM 变了没、
   测试过没过、console 报错没。3D 里"这刀路对不对"没有客观答案，
   它取决于材料、机床、装夹、师傅经验。这是本质困难，不是技术问题。
2. **看不见 / 摸不着几何**（第二，但可部分解决）。正确解法**不是给它看图**，
   而是把几何转成数字：包围盒、体积、最小壁厚、孔位坐标、
   干涉检测结果、过切欠切余量。这些数字比看图可靠得多。
   多模态模型能看懂渲染图，但看**不准**尺寸和工程正确性。
3. **反馈太慢**（第三）。改一次刀路要算几十秒到几分钟，跑仿真更久，
   Agent 的迭代循环被拉长，时间和 token 成本都高。

由此得到一条可操作的分界线：

- **能用数字判的**（干涉、余量、行程超限、转速进给越界、刀具碰撞）
  → **可以做全自动**
- **只能靠眼睛判的**（曲面质量、外观、分型面顺不顺）
  → **永远需要人**

先做前者，别碰后者。程序单、批量后处理、规则化建模都在"数字可判"这一侧；
曲面质量评估在另一侧。


---

## 九、下一步需要你提供的信息

1. **NX 版本号**（NX 里 `帮助 → 关于`）。决定用 Python 还是 VB 写桥接。
2. **一段真实录制**：让用户录一个他每周重复十几次的操作，把 `.py`/`.vb` 发过来。
3. **机床参数**：填进 `config/machine_fanuc.json`（行程、最高转速、刀具表）。
4. **后处理名称**：NX 里后处理列表里的那个名字。

拿到这四样，第 2 项我可以直接改成可循环的脚本，第 3、4 项填进配置，
然后整套就能在他机器上跑第一次真实闭环。

---

## 十、「真机跑」能模拟掉多少？

短答：**能模拟掉一大半，但删不掉最后一步。** 把"真机跑"拆成五层：

| 层 | 在验证什么 | 本仓库能否覆盖 |
|---|---|---|
| ① 语法与安全规则 | 少小数点、缺 M30、S/F 越界、行程超限 | ✅ `nc_lint.py` |
| ② 后处理器改动的影响面 | 这次改 `.tcl` 到底动了哪里 | ✅ `nc_diff.py` |
| ③ 刀路几何 | 该切的切没切掉、撞不撞夹具 | ⚠️ 靠 NX 内置仿真，**但它看不见后处理器** |
| ④ G 代码在特定控制器下的行为 | 控制器认不认、偏置对不对、换刀时序 | ❌ 要 VERICUT / NCSIMUL 这类，很贵 |
| ⑤ 机床物理现实 | 反向间隙、伺服跟随误差、热变形、装夹变形 | ❌ **只能真机** |

三个必须记住的反直觉点：

1. **NX 内置仿真看不到后处理器。** 它仿的是刀路（内部数据），不是后处理后的
   G 代码。后处理器写错了，仿真照样显示正常——所以第 ② 层必须自己补。
2. **仿真器只是模拟控制器的解析器，不是运行它。**
   原文："Even an external G-code simulator models the controller's parser;
   it does not run it." → 第 ④ 层永远有道缝。
3. **同一型号不同序列号的机床，后处理器都要分开维护**，因为调机参数不同。
   这条说明第 ⑤ 层为什么模拟不掉：要验证的东西压根没进模型。

结论：模拟能把上机次数从"每次改都上机"压到"只有首次 + 关键变更才上机"，
但上机这一步删不掉。完整论证与一次具体案例见 `docs/模拟能替代多少真机.md`。
