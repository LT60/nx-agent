# 启动 NX 桥接（一条命令，不用点鼠标）

> **一句话**：打开终端，跑 `python tools\start_bridge.py`，等十几秒就好了。
> Agent 从此可以指挥 NX 干活，**你不需要打开 NX 的编辑器，也不需要看脚本**。

---

## 一、先搞清楚"桥接"是什么

用一个你熟悉的东西打比方：

| 你熟悉的 | 这里的对应物 | 干嘛的 |
|---|---|---|
| 网页开发的**后端服务** | `bridge/nx_bridge.py` | 一直开着，等前端发指令 |
| 前端调后端用的 **HTTP 接口** | `queue/in` 和 `queue/out` 两个文件夹 | Agent 往里放请求，NX 往里放结果 |
| 服务器 | **NX 本身** | 真正干活的那个 |
| 你的编辑器 + 终端 | Agent（WorkBuddy / Codex 等） | 说话、写脚本、改脚本 |

唯一需要记住的区别：**"后端服务"必须挂在 NX 里面，而且要挂在"已经拿到许可证"的那个 NX 进程里。**
原因见第八节，一句话是「学生版只允许在图形界面里跑脚本」。

---

## 二、启动：一条命令

```bat
cd /d D:\nx-agent
python tools\start_bridge.py
```

看到这样就成功了：

```
NX 安装: D:\NX  [学生版（SALT 授权）]
启动命令: D:\NX\NXBIN\ugraf.exe -student -auto=...\bridge\nx_bridge.py

  [3s] 自动点了同意框的「decline」（共 1 次）
  [13s] ✅ 桥接已就绪

心跳: pid=188948 ｜ 模式=watch ｜ 已轮询 3 次 ｜ 在 NX 内=True
```

它背后做的事，就是把下面这条命令替你敲了：

```
D:\NX\NXBIN\ugraf.exe -student -auto=D:\nx-agent\bridge\nx_bridge.py
```

> `-auto=<脚本>` 是 NX 主程序自带的「启动时自动跑这个脚本」开关。
> 因为用的是 NX 主程序本身，许可证跟平常开 NX 完全一样，**不存在许可问题**。

### 常用参数

| 命令 | 作用 |
|---|---|
| `python tools\start_bridge.py` | 启动并等到就绪 |
| `python tools\start_bridge.py --check` | 只看状态，什么都不启动 |
| `python tools\start_bridge.py --stop` | 让桥接体面退出（NX 也跟着退） |
| `python tools\start_bridge.py --timeout 600` | 多等一会儿（慢机器/首次启动） |

---

## 三、首次启动可能会弹一个框，脚本会自己点掉

NX 每次启动可能先弹一个**数据收集同意框**：

```
标题: Product Excellence Program
按钮: [Siemens Trust Center]  [Agree]  [Decline]
内容: 西门子收集软件使用情况数据，用于改进产品……
```

**它会挡住 NX 启动**——不点它，桥接就跑不起来。

`start_bridge.py` 默认会自动点 **Decline**（= 不参与数据收集，同时让 NX 继续启动）。

> 为什么默认点「Decline」而不是「Agree」：替你点 Agree 等于替你同意了数据采集，
> 这种事我不代劳。要改成同意就在命令后面加 `--consent agree`。

### 想让它以后再也不弹：一次设置，永久生效

这个框的设置存在一个很小的 XML 文件里：

```
%LOCALAPPDATA%\Siemens\Designcenter Student Edition2606\
    Designcenter Student Edition_user.dpv
```

内容长这样（`value="true"` 就是"已同意参与"）：

```xml
<Pref Application="Gateway" Category="General" Tab="Data Privacy"
      name="NX_ProductExcellenceProgram"
      title="Product Excellence Program - Participate" value="true"/>
```

**两种关掉方式，任选一种：**

- **图形界面**：NX → 菜单 → 首选项 → 用户默认设置 → Gateway → General → Data Privacy
  → 取消勾选 "Product Excellence Program - Participate" → 重启 NX
- **改文件**：把上面那行的 `value="true"` 改成 `value="false"`

> 对话框自己就是这么说的：「You can also stop any data processing under the
> Product Excellence Program by withdrawing your consent using the option in the
> Customer Defaults dialog (Gateway -> General -> Data Privacy)」。
>
> 你不做这一步也不影响用，只是每次启动脚本会替你点一下 Decline。

---

## 四、NX 界面会"未响应"——这是正常的

桥接要一直守着 `queue/in` 看有没有新请求，就得占着 NX 的主线程转圈，
NX 就没机会重画界面了，窗口会显示"未响应"。

**能不能绕开：不能。** 已经查遍 NX 的完整 API 文档
（`D:\NX\NXBIN\managed\NXOpen.xml`，60 MB），NX 里**不存在**
「定时器」或「空闲回调」这类能让出主线程又保持唤醒的机制。

**实用做法——开两个 NX**：

| NX 实例 | 用途 | 状态 |
|---|---|---|
| 第 1 个 | **你自己建模/编程用** | 正常操作 |
| 第 2 个 | **机器人**，被 `start_bridge.py` 拉起来跑桥接 | 界面无响应，你不用管它 |

实测学生版**可以**同时开两个 NX（两个进程共存无冲突）。
所以：先照常打开你自己的 NX，再跑 `start_bridge.py` 起机器人 NX，两边互不干扰。

---

## 五、怎么确认它真的通了

```bat
cd /d D:\nx-agent
python tools\bridge_probe.py
```

```
[PASS] 心跳
[PASS] ping
[PASS] info（读工作部件）
======================================================================
结论：全通。Agent 现在可以下发 NX 操作了。
```

| 项 | 在检查什么 |
|---|---|
| **心跳** | 桥接进程还活着吗（看 PID + 心跳刷新时间） |
| **ping** | 我放个请求进队列，它能收到并回话吗（证明轮询在转） |
| **info** | 它能读到 NX 当前打开的工作部件吗（证明 NXOpen 真能用） |

**info 显示 WARN 是正常的**，只要你当时没在 NX 里打开任何 `.prt` 文件。

---

## 六、之后 Agent 怎么用

MCP 那边已经配好（`mcp/server.py`，对 Agent 暴露 12 个工具）。你只要说人话：

> "把 D:\work\mold-001.prt 打开，看看里面有几个 CAM 操作"

Agent 会自己调工具、把结果讲给你听。

```
你说话 → Agent 写请求 → queue/in → [NX 里的桥接每 0.5 秒看一次]
                                          ↓ 执行
                                      queue/out → Agent 读结果 → 讲给你听
```

---

## 七、怎么停

```bat
python tools\start_bridge.py --stop
```

它会往队列投一个停止请求，桥接下一轮就自己退出、NX 也跟着退。

> 别直接去任务管理器杀 `ugraf.exe` —— 那会连 NX 一起硬关，
> 万一那个 NX 里有没保存的活儿就麻烦了。

---

## 八、出故障了怎么查

| 症状 | 原因 | 怎么办 |
|---|---|---|
| 一直没动静，等了两三分钟 | **NX 里有同意框在等点击** | 去 NX 窗口点一下；或加 `--consent decline` |
| `--check` 说「没有 heartbeat.json」 | 桥接从来没启动过 | 跑 `start_bridge.py` |
| `--check` 说「PID 已消失」 | 桥接退出了（NX 被关了或崩了） | 重新 `start_bridge.py` |
| `--check` 说「进程还在，但心跳停了 N 秒」 | **NX 里弹窗把桥接挡住了** | 去 NX 窗口把弹窗点掉 |
| `bridge_probe` 说「等了 N 秒没有回音」 | 桥接在转，但 NX 那边执行卡住 | 去 NX 看有没有报错弹窗 |
| `queue/stale/` 里出现文件 | 有过期请求被主动跳过（**这是保护机制，不是故障**） | 看同目录的 `.why.txt` 说明 |
| Agent 说「工具调用失败：NX 桥接不在线」 | 同上 | 先跑 `bridge_probe` 定位，再照上面处理 |

---

## 九、为什么不用 `run_journal.exe`（实测证据）

常规做法是用命令行工具 `run_journal.exe` 跑脚本，但**学生版上它无论如何都拿不到许可**：

```
D:\NX\NXBIN\run_journal.exe bridge\nx_bridge.py
→ Caught unexpected error: 3615094: Unable to reserve license to run journal file
```

穷举试过的写法，**全部同一个错，而且都在 1 秒内失败**（根本没进 NX）：
裸调用 ｜ `-nx` ｜ 加 `UGII_USE_NXSAAS_MODE=1` ｜
指定 `SPLM_LICENSE_SERVER=29000@LocalHost` ｜ 指定 `28000@localhost`。
**即使 NX 图形界面正在运行（许可证已被占用）也一样报错。**

另一条路——外部程序直接调用 NX 的 C 接口（`libufun.dll` 的 `UF_initialize`）也不行，
这次 NX 自己给出了原因：

```
返回码 3600045:
No C++ author or Designcenter Open development license is available
to be able to run this native application.
```

**翻译**：外部/无界面调用需要一张"开发者许可证"，学生版没有这张证。

**所以学生版只开放了一条路：在拿到许可的图形进程里跑脚本。**
`-auto=` 正好就是走这条路，而且不需要人点鼠标。

---

## 十、换成商业版/学术版会更好吗

会，但**不是"能不能用"的区别，只是"更省事"的区别**：

| | 学生版（现在） | 商业版 / 学术版 |
|---|---|---|
| 能不能接 Agent | ✅ 能 | ✅ 能 |
| 启动方式 | `start_bridge.py`（一条命令） | 同样，**而且**可以完全无界面 |
| 界面冻结 | 机器人那个 NX 窗口会无响应 | 可以后台跑，不占界面 |
| 后处理出 NC | ❌ 官方限制，不能 | ✅ 能 |

**注意**：用户厂里用的是正版/商业版，所以**交付给他的版本会比我们在这台学生版上验的还完整**。
学生版只是我们的开发测试机。

---

## 附：备用启动方式（图形界面已经开着的时候）

如果 NX 已经开着、你不想再启一个，也可以在界面里手动加载桥接：

```
Developer 选项卡 → Journal Manager → Browse 选中 bridge\nx_bridge.py → Play
```

效果一样，只是每次都得手点。
