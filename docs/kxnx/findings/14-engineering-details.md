# 14 · 工程细节：奎享踩过坑才那样写的地方

> 前 13 份 findings 关注**协议**（发什么指令）。本文关注**工程实践**——
> 那些"为什么这么写"的细节。奎享做这行多年，这些细节多半是踩坑后的产物。
> 对 hutuji 的价值：避免我们重踩。

## 一、事件派发强制切 UI 线程，且区分同步/异步

来源 `com/kenjoy/kenjoycnc/at.java`（所有控制器的基类）。

```java
public class at extends b {
    // 注册监听：同步等（invokeAndWait）
    public final void a(String object, f f2, int n2, boolean bl) {
        if (SwingUtilities.isEventDispatchThread()) { super.a(...); return; }
        SwingUtilities.invokeAndWait(() -> this.b(object, f2, n2, bl));
    }

    // 派发事件：异步不等（invokeLater）
    public final void a(a a2) {
        if (SwingUtilities.isEventDispatchThread()) { super.a(a2); return; }
        SwingUtilities.invokeLater(() -> super.a(a2));
    }
}
```

两处用了**不同**的调度语义，这不是随手写的：

| 操作 | 调度 | 为什么 |
|------|------|--------|
| 注册监听 | `invokeAndWait`（同步） | 注册必须在后续事件到达前完成，否则漏事件 |
| 派发事件 | `invokeLater`（异步） | **绝不能让 UI 处理拖慢串口/网络接收线程** |

### 对 hutuji 的直接映射（潜在同类问题）

我们 `hutuji_job.cc::Notify()` 在**转发任务线程里同步调**云端推送：

```cpp
void Job::Notify(const std::string& message) {
    // ... 构造 JSON ...
    Application::GetInstance().SendMcpMessage(str);   // ← 同步调用
}
```

而 `StreamToGrbl()` 每 5 秒会调一次 `Notify()` 推进度。如果云端 WebSocket
发送阻塞（网络抖动、云端慢），**转发循环会被拖住**，表现为出图卡顿。

奎享的做法等价于：进度推送应该"投递后即返回"，不等对端。

**验证边界**：我没有实测 `SendMcpMessage` 是否会阻塞（取决于 xiaozhi
`Application` 的实现和 WebSocket 发送缓冲）。这是**推断的风险**，不是已确认的
bug。若将来出现"每 5 秒卡一下"的现象，优先查这里。

## 二、坐标整数简化格式化（省字节 = 省窗口）

来源 `av.java:574-580`。

```java
private static String a(float f2) {
    int n2 = (int)f2;
    if ((float)n2 == f2) {
        return String.valueOf(n2);      // 5.0 → "5"，不是 "5.000"
    }
    return String.valueOf(f2);
}
```

整数值去掉小数位。这跟窗口化流控（[[09-flow-control-deep]]）是**配套设计**：
窗口按**字节**计数，坐标写短，窗口里就能多塞行。

对比：

| | 写法 | 字节数 |
|---|------|--------|
| 奎享 | `G1X75Y222` | 9 |
| hutuji 现状 | `G1 X75.000 Y222.000 F1500` | 25 |

在 100 字节窗口下，这是 **11 行 vs 4 行**的在途容量差别。

我们 `hutuji_gwrite.toml` 用 `{x:.3f}` 固定三位。**P0 阶段不动**（0.001mm
分辨率对 0.1mm 笔触是安全余量，且 vpype-gcode 模板不支持条件格式化）。
但做 P2 窗口化流控时，这条要一起考虑——否则窗口利用率只有奎享的 1/3。

## 三、固件错误码翻译成可执行的维护动作

来源 `machine/e/a.java`（K_MT 机型，换纸机）。

```java
if (object.equals("error:check y min sensor")) {
    JOptionPane.showMessageDialog(null,
        "请检查放纸传感器，可能需要清洁或者已损坏。若上面有遮挡物请移走。",
        "维护", 0);
}
if (object.equals("error:check y max sensor")) {
    JOptionPane.showMessageDialog(null,
        "请检查纸张对位传感器，可能需要清洁或者已损坏。", "维护", 0);
}
```

奎享**不把固件错误原文抛给用户**，而是翻译成"你该做什么"。

### 对 hutuji 的映射

我们现状（`hutuji_job.cc::StreamToGrbl`）：

```cpp
if (wr == WaitResult::Failed) {
    last_error_ = "error:" + std::to_string(err);   // ← 用户听到 "error:9"
    return false;
}
```

语音场景下这个问题比 GUI 更严重——用户听到"出错了，error:9"完全不知道该干什么。
Grbl 常见错误码的可操作翻译：

| 码 | Grbl 含义 | 该跟用户说什么 |
|----|-----------|----------------|
| 9 | G-code locked out during alarm or jog | 机器在报警状态，需要先解锁（`$X`） |
| 8 | Not idle（我们当 Deferred 重试） | 机器忙，稍等重试 |
| 2 | Bad number format | G-code 格式错（云端生成问题，不是用户问题） |
| 20 | Unsupported G-code command | 指令不支持（同上，云端问题） |
| 24 | 两个 G 命令冲突 | 同上 |
| 33 | Invalid target | 坐标超行程（限幅漏了） |
| 60 | （本项目）未授权 | 写字机未授权 |

**这是 P1 级改进**（不阻塞出图，但直接影响"量产可用"的体验目标）。

## 四、跨固件的机型派生模式

奎享的机型不是平铺的，而是**继承树**：

```
machine/c.java（抽象基类，13 个抽象方法）
├── grbl/a.java          Grbl 通用
├── marlin/a.java        Marlin 通用
│   ├── e/a.java         K_MT（Marlin + 自动换纸，覆盖错误诊断）
│   └── awauto/*         AW 自动机型
├── kat/b.java           KAT 自家协议
│   └── a/a.java         CardWriter（KAT + 卡片机，覆盖端口/握手）
└── ebb/a.java           EBB（EiBotBoard）
```

派生机型只覆盖**差异点**：
- `g(String)` — 识别 banner（`"K_MT"` / `"CardWriter"` / `"Grbl"`）
- `h(String)` — 解析版本
- `d()` — 设原点（带机型专属 offset）
- `c(String)` — 追加机型专属消息处理

**对我们的意义**：hutuji 的写字机是"Grbl + 换纸"，在奎享的分类里对应
`grbl/a.java` 的一个派生。我们的换纸走 `M701/M711/M30` 是自研的，
与奎享的 K_MT/AW 不兼容（已确认跳过），但**基础笔控/流控/握手应该完全走
Grbl 通用那套**——这正是 P0 对齐的依据。

## 五、汇总：本文对 hutuji 的可执行结论

| 发现 | 优先级 | 动作 |
|------|--------|------|
| 进度推送可能阻塞转发 | P1（待确认） | 查 `SendMcpMessage` 是否阻塞；若是，改异步投递 |
| 坐标格式化省字节 | P2（配套窗口化） | 做窗口流控时一起改 |
| 错误码翻译 | **P1** | `error:NN` → 可操作的中文提示 |
| 机型继承模式 | 参考 | 印证 P0 走 Grbl 通用笔控是对的 |

## 证据边界

- 事件调度、格式化、错误翻译：**字面量代码，强证据**
- "进度推送会拖慢转发"：**推断**，未实测 `SendMcpMessage` 阻塞行为
- 窗口利用率 11 vs 4 行：算术推导，基于字节数
