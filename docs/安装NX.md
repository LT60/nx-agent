# 下载并安装 NX（给自己做 MCP 开发用）

> **要装机就直接看 `下载安装清单.md`**（链接 + 10 步 + 五个坑，可以对着打勾）。
> 这一份是"为什么"——各版本怎么选、每个限制背后的原因、某高校授权怎么问。

> 先看这一条：**NX 的 CAD 部分从 2026 年 7 月起改名为 Designcenter**，
> CAM 部分仍叫 NX CAM。所以你在下载页会看到 "Designcenter Student Edition"，
> 那不是别的软件，就是 NX 的免费学生版。别以为下错了。

> **新增直达链接**（2026-09-30 实查，均可打开）：
> - 中文注册页：`https://trials.sw.siemens.com/zh-CN/trials/nx-cad-cam-student-edition`
> - 英文注册页：`https://trials.sw.siemens.com/en-US/trials/designcenter-cad-nx-cam-student-edition`
> - SSC 客户端下载页：`https://www.siemens.com/en-us/support/siemens-software-center/`


---

## 第 0 步：先决定装哪个（三选一）

| 选项 | 适合 | 关键限制 |
|---|---|---|
| **Designcenter / NX Student Edition**（免费） | **你的首选**：自己电脑做开发 | CAM **不能后处理**；带水印；仅 Win64 |
| **NX Academic Edition**（学校授权） | 学校有西门子学术授权时，**最好用这个** | 需要学校是学术合作伙伴 |
| **30 天商业试用**（免费） | 备用：如果学生版缺开发组件 | 只有 30 天 |
| 用户机器上那套 | 真实生产验证 | **不要动它**，那是生产环境 |

**先做一件事**：问问你们学校（实验室 / 机械或材料学院 / 网络中心）有没有 NX 或
Designcenter 的学术授权。有的话直接用那个，功能和商业版一致，能后处理，
也没有水印问题。工科院校通常都有西门子学术合作。

没有的话，走下面的学生版流程，**免费、不需要验证学生身份、15 岁以上即可**。

---

## 第 1 步：先自检硬件，别下完才发现跑不动

**这一节不用手动看，跑一条命令就行**（我刚写好的）：

```bash
python tools/preflight_check.py
```

它会自动读出你的系统版本、内存、各盘可用空间、显卡型号，
逐条给出「通过 / 警告 / 失败」和该怎么做。3 秒出结果。

下面是官方硬指标，供你对照理解脚本的输出：

| 项目 | 官方最低 | 官方推荐 | 脚本判读 |
|---|---|---|---|
| 操作系统 | Windows 10 64 位 | **Windows 11** | Win10 只算「有限支持」，见下 |
| 内存 | 8 GB | **32 GB** | 8GB 只是「能启动」，不是「能用」 |
| 显卡 | 支持 OpenGL 4.5，2GB 显存 | 专业卡（RTX / Quadro / Radeon Pro） | 脚本读型号，OpenGL 版本要人工确认 |
| 磁盘 | 安装占 9 GB | — | **但下载包本身有 19 GB**，解压还要占一份 |

> **关于 Windows 10：这是个坑，但不是拦路虎。**
>
> 西门子 2026 年 6 月版 Designcenter 的官方系统要求原文是：
> **Supported: Windows 11**；**Limited support: Windows 10**。
> 同时 NX 2606 的开发计划里写明「不再对 Windows 10 进行测试和技术支持」。
>
> 但要理解这句话的准确含义：**「不支持」≠「装不上」**。
> 它指的是官方这个版本没在你这个系统上测过，出问题不接 bug 报告。
> NX 历史上一直如此——当年 NX 12 也宣称不支持 Win7，实际装上照样跑。
>
> 所以：**照装，跑得起来就用。** 只是以后遇到怪问题，先想到这个前提：
> "我这个系统不在官方支持名单里"，能省很多排查时间。
> 不必为此重装系统（Win11 对很多人来说也确实不好用）。

---

## 第 2 步：下载（2026 年 7 月起流程变了）

⚠️ **关键变化：现在只能通过「Siemens Software Center（SSC）」客户端下载安装，
不能再直接下 zip 包了。** 西门子 2026 年 8 月的官方博客原话：

> The new Designcenter Student Edition is available to download and install from
> **Siemens Software Center** from July 2026 onwards.
> It was **previously available to download from Siemens Support Center**.

所以在网上看到的"直接下 `SiemensNXStudentEdition-xxxx_wntx64.zip`"的老教程，
都是 2026 年 7 月之前的做法，现在入口已经关了。

### 完整步骤

1. 打开注册页：
   `https://trials.sw.siemens.com/en-US/trials/nx-cad-cam-student-edition`
   （中文学生资源页：`https://plm.sw.siemens.com/zh-CN/nx/student-software/`）
2. 登录或新建一个 Siemens 账号（可以用 Google / LinkedIn 直接登）。
3. 填 "Trial Details" 表单 → Launch 试用。
4. **下载并安装 "Siemens Software Center"（SSC）这个客户端**（从第 3 步之后的页面拿）。
5. 打开 SSC，用**你注册试用时的同一个邮箱**登录并完成验证。
   ⚠️ 必须保持登录状态——**以后每次用 NX 都要从 SSC 启动它**。
6. 在 SSC 里找到 **Designcenter Student Edition** → 点 **Download**。
   下载包约 **19 GB**，看网速，可能要几十分钟到几小时。
7. 状态变成 **Downloaded** 后，点 **Install**（或点右上角三个点看安装选项）。
8. 状态变成 **Installed** 后，点 **Open** 启动。

### 版本与许可证

- 学生版**每次商业版发布都会跟一个新版**：商业版每年 **6 月和 12 月**发，
  学生版大约**晚几周到一个多月**。在 SSC 里可以随时更新到最新版。
- 许可证文件**已经打包在安装包里，会自动配置**，
  **有效期约一年**。到期后重新注册一个账号就能拿新许可证
  （所以要用能长期收信的邮箱）。
- 学生版的许可证只覆盖 **NX-STUDENT1** 这一组应用，不是完整应用清单。
- **不支持月度补丁，也不支持自动更新**——每个大版本系列只有最初那个版本
  （官方 README 原话：Automatic updates are not available with the NX Student Edition.
  Monthly updates are not supported. Only the full install is available.）。
- 装的是**完整安装包**，不是增量包，所以每次都比较大。


---

## 第 3 步：安装（这一步有个必须勾的选项）

1. **把安装包解压到一个短路径**，比如 `C:\temp`。
   官方 README 明确建议短路径，中文长路径容易出问题。
2. 运行目录里的 `setup.exe`，允许它修改计算机。
3. **关于组件勾选（实测澄清）**：网上教程常说「展开 `Programming Tools`，
   把 `Programming Interfaces`（UGOPEN）勾上」—— 那是**商业版**的步骤。
   **学生版的 MSI 里只有 4 个组件且全是必装的**，没有任何可选功能树，
   `run_journal.exe` 属于核心组件，装了就有。**不用找那个勾选框，找不到的。**
   （要看学生版的完整安装流程，直接看 `docs/下载安装清单.md`。）
4. 安装位置选 SSD。整个安装大概 **30～90 分钟**，中途别关。
   **学生版走 SSC 安装的话路径是固定的**（会装到 `C:\Program Files\Siemens\`）；
   想装到别的盘只能卸载后用 MSI 手工装，做法见 `docs/下载安装清单.md` 第 4 步。

---

## 第 4 步：装完立刻自检（3 分钟，别跳过）

**一条命令**：

```bash
python tools/preflight_check.py --auto
```

它会自动找到 NX 安装目录，然后逐条检查：
`run_journal.exe`、`NXOpen.pyd`、NX 自带 Python 版本、
`UGII_BASE_DIR` / `SPLM_LICENSE_SERVER` 两个环境变量，
还会顺手看看 `runugpost.exe` 在不在（这个关系到能不能绕开学生版的后处理限制）。

如果想手动跑，是这几条：

```bat
:: 1. 找到 NX 安装目录（把输出记下来）
reg query "HKLM\SOFTWARE\Unigraphics Solutions" /s

:: 2. 确认两个关键文件存在（<NX_DIR> 换成上一步查到的路径）
dir "<NX_DIR>\NXBIN\run_journal.exe"
dir "<NX_DIR>\NXBIN\python\NXOpen.pyd"

:: 3. 跑一个最小脚本，验证 Python + NXOpen 能起来
echo import NXOpen; print("NXOpen imported successfully") > C:\temp\test_nx.py
"<NX_DIR>\NXBIN\run_journal.exe" C:\temp\test_nx.py
```

**判读结果：**

| 现象 | 含义 | 怎么办 |
|---|---|---|
| 第 3 步打印出 `NXOpen imported successfully` | **全通了** | 告诉我，我们进下一步 |
| 提示找不到 `run_journal.exe` | 学生版不会出现（组件全装）；商业版是没勾 Programming Interfaces | 先跑 `preflight_check.py --auto`；确实缺就重跑安装程序 → Modify |
| 导入 NXOpen 报错 | Python 运行时或路径有问题 | 把完整报错发我 |
| 报许可证相关错误 | 学生版许可证没配上 | 把报错发我 |

> 顺便看一眼 `NXBIN\python\` 下的 `python3*.dll` 是哪个版本。
> 那是 NX 自带的 Python，桥接脚本必须用它能认的语法。
> （本项目所有脚本只依赖标准库，就是为了不受这个版本影响。）


---

## 第 5 步：五个必须知道的坑

1. **学生版的 CAM 不能后处理。** 官方试用页原文：
   "CAM data is not available for post-processing,
   **and the displayed output of tool path information has limited precision**"。
   后面半句也重要：**连刀路信息的显示精度都是受限的**。
   这意味着你在自己电脑上测不到"出 NC 程序"那一步，只能测到刀路为止。

   > **这对本项目意味着什么**：`nc_lint` / `nc_diff` 这两个校验器
   > 需要真实的 `.nc` 文件才能验证。而学生版出不了 NC 文件。
   > 解决办法不是换软件，而是**让用户发两三个他厂里真实的 .nc 程序过来**
   > （去掉零件号、客户名这类敏感信息就行）。
   > 有了真样本当基准，校验器就能在真数据上迭代，而不是只跑我造的样例。

2. **安装程序会覆盖 `SPLM_LICENSE_SERVER` 环境变量。**
   官方 README 原话：If you have an existing installation of NX, this installation will
   overwrite the value of the environment variable, SPLM_LICENSE_SERVER,
   and point to the NX Student Edition license included in the installation.
   → **装之前先把这个变量的当前值记下来**（自检脚本会帮你打出来），
   否则你机器上原来那套 NX 的许可证就废了，而且很难查。

3. **学生版保存的 .prt，商业版打不开。** 官方原文：
   Part files created in Designcenter Student Edition cannot be retrieved in a
   commercial version of Designcenter, **but can be retrieved in the full academic
   packages available to educational establishments**。
   → 所以**千万别拿它给用户做生产文件**，会污染他们的数据。
   （好消息：学校如果走学术授权包，是能打开学生版文件的。）

4. **版本只能往上涨，不能往回退。**
   2506 的学生版能打开 NX2506 及更早的文件；
   但用 2506 存过的文件，**只能在 2506 或更新的版本里打开**。
   官方还特别提醒：用"添加/删除程序"卸载会**把整套 NX 连注册表和开始菜单一起清掉**，
   想退回旧版本只能卸载后拿旧安装包重装。
   → 用户那边是什么版本，你这边最好对齐或更高。

5. **没有自动更新，也没有月度补丁。**
   每个大版本系列只有最初那个版本可以装，要更新就在 SSC 里手动装下一版。

---

## 附：你的机器实测结果（2026-09-30）

已经跑过一次 `preflight_check.py`，结论如下：

| 项目 | 实测 | 判定 |
|---|---|---|
| 操作系统 | Windows 10 Pro 22H2（build 19045） | ⚠️ 官方「有限支持」——照装，不影响 |
| 内存 | **63.6 GB** | ✅ 官方推荐值的两倍 |
| 显卡 | NVIDIA **RTX A4000 16GB** + **RTX 3090 24GB** | ✅ 远超门槛，都是认证卡水平 |
| 磁盘 | C: 77GB / **D: 1278GB** / E: 302GB 可用 | ✅ 装 D 盘 |
| 已装 NX | 无 | ✅ 无许可证冲突 |
| SPLM_LICENSE_SERVER | 未设置 | ✅ 干净 |
| Siemens Software Center | 未安装 | 需要先装 |

**总评：硬件完全过剩，唯一需要注意的是系统是 Win10。下载和安装可以开始了。**

装到 **D 盘**（可用空间最多，而且大概率是 SSD/存储盘）。


---

## 装不下去怎么办（备用方案）

- **走 30 天商业自评估试用**：NX 产品页的 self-evaluation 表单申请，那是**完整版**，
  一定有 Programming Tools，可以用来验证整条链路。缺点是只有 30 天，适合做冲刺验证。
- **直接用用户的机器当开发环境**：他那台有真实授权和工作环境。
  你可以远程或者亲自去跑一遍，缺点是不能随时迭代。
- **硬件实在不够**：诚实说，NX 是重负载软件。硬要跑会非常难受，
  建议优先考虑上面两条。

---

## 某高校怎么拿到授权（已查证的线索）

先说结论：**公开信息里查不到"某高校有西门子 NX 学术授权"的明确证据，
但有一条最值得走的线索——相关学院的 CAD/CAM 实验室。**

### 三条线索（按靠谱程度排）

**1. 相关学院「CAD/CAM 实验室」 ← 最可能的入口**

某高校机电工程实验教学中心是**广东省高等学校实验教学示范中心**，下设
机械测绘一体化室、CAD 实验室、CAE 室、**CAD/CAM 实验室**、学生设计工作室、
金工车间 等 20 多个分室。教 CAD/CAM 课的地方，最可能装了 UG/NX。
（该学院的培养方案里也有「产品 CAD/CAM/CAPP 实践」「工业产品测绘与 3D 建模实践」
这类课程，说明确实有这套环境。）

- 地址：广州市番禺区大学城外环西路 230 号（**就是主校区，你走过去就行**）
- 电话：**（略）**
- 邮箱：**jd@example.edu.cn**
- 实验室介绍：`https://jd.example.edu.cn/info/1144/4085.htm`

**2. 学校正版软件服务平台（大概率没有 NX，但值得花两分钟确认）**

- 入口：登录「学校门户」→ 应用中心 → 业务系统 → 正版软件
- 网址：`https://newms.example.edu.cn` — **必须用校园网，外网打不开**
- ⚠️ 但从学校 2026 年正版软件租赁项目的采购内容看，覆盖的是
  Windows / Office / WPS / 福昕 PDF / 企业邮箱 / 企业微信微盘 / 电子签章，
  **没有 CAD/CAM 类工业软件**。所以在这儿下不到 NX。

**3. 学校其他部门**

- 实验教学中心：`https://syjxzx.example.edu.cn/`
- 网络与现代教育技术中心：`https://netcenter.example.edu.cn/`

### 问的时候一定要问清这四件事

1. 学校或学院有没有西门子 NX（UG）的**学术授权**？
2. 授权形式是哪种：**浮动许可服务器**（要地址和端口）、单机许可、还是只在实验室机器上装？
3. **非相关学院的学生**（你是计算机学院的研究生）能不能申请使用？
4. 能不能**在校园网内自己的笔记本上**装客户端连许可服务器？

> 第 3、4 条最关键。教学授权常见形态是"并发浮动许可 + 只在实验室局域网可用"，
> 这种情况下你把 NX 装到自己电脑上也连不上许可，白折腾。

### 可以直接复制的问询模板

> 老师您好：
>
> 我是计算机相关专业的研究生（学号 XXX）。我在做一个用程序自动化驱动 NX
> 完成模具建模与 CAM 编程的研究项目，需要在自己电脑上装一套 NX 用于开发调试，
> 想咨询几个问题：
>
> 1. 学校或相关学院是否有西门子 NX（UG）的学术授权？
> 2. 授权是浮动许可（有服务器地址）还是单机许可？
> 3. 非相关学院的学生能否申请使用？
> 4. 能否在校园网内自己的笔记本上安装客户端连接许可服务器？
>
> 如果需要走申请流程，麻烦告知该找哪个部门、需要什么材料。非常感谢！
>
> 姓名 / 学院 / 联系电话

### 如果学校确实没有

回到第 0 步：用免费的 Designcenter / NX Student Edition，或者走 30 天商业试用。
学校这条路是"有就更好"，没有也不影响你启动。

---

## 装完之后

回到 `README.md` 第四节，把你的 NX 路径填进去，然后按第五节开始录第一个操作。

---
