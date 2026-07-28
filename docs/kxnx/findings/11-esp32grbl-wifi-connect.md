# 11 · esp32grbl WiFi 连接与设备发现（已被 finding 16 超越，本页仅留独有内容）

> ⚠️ **2026-07-28 收口**：本页早先的两条结论——「奎享 WiFi 逻辑与我方**高度同构**」
> 和「发现走**单一 mDNS**」——都不准确，已由 **[finding 16](16-wifi-discovery-and-connect.md)**
> 用逐行源码更正：
> - 发现是**三级回退**（历史 / 自动搜索 / 手输），不是单一 mDNS；
> - 自动搜索是 **mDNS + SSDP 双通道并行**，不是只有 mDNS；
> - 并排两边策略后暴露了**我方一处真实固件缺陷**（缓存 IP 活锁，见 finding 16 §四）。
>
> **发现/连接/端口/协议校验的权威内容一律看 finding 16。** 本页只保留 16 未覆盖的两点：
> `isNetAddress()` 数据模型（§四）、我方去除 mDNS 的历史缘由（§二末）。其余段落停止维护。
>
> 源文件：`machine/ui/esp32grbl/*.java`（11 文件）

## 一、连接入口 `esp32grbl/a.java`（JDialog 选择器）

奎享连 WiFi 写字机的 UI。三个 Tab：

| Tab | 类 | 方式 |
|-----|----|------|
| 历史连接 | `f.java` | 从 `HistoryConnectedMachine` 存储读列表 |
| 自动搜索 | `b.java` | mDNS `_http._tcp.local.` 发现 |
| 手动连接 | `e.java` | 用户输入 IP + port |

### 端口选择（关键）

```java
// a.java:137-142
static int a(a a2) {
    if (f.a().e() instanceof marlin.a) {
        return 8080;     // Marlin 固件用 8080
    }
    return 23;            // Grbl 固件用 23（Telnet）
}
```

**Grbl → port 23（Telnet）**。这正是我们 `hutuji_pipe.cc` 用的 `HUTUJI_PIPE_PORT 23`。
印证方案 E 选 Telnet:23 是对的——这是奎享原厂就在用的端口。

### 连接流程（`a.java:a(MachineItem)`）

```java
private void a(MachineItem machineItem) {
    this.f.setVisible(true);                          // 显示"连接中"
    // 若当前已连，先断
    if (f.a().e().t()) {
        f.a().e().s().b(true);                        // b(true) = 断连
    }
    // 新建 WiFi 传输（machine/b/d.java）
    object = new d(f.a().e());
    boolean bl = ((machine.b.a)object).b(machineItem.getAddress(), machineItem.getPort());
    if (bl) {
        // 成功：存进历史连接
        HistoryConnectedMachine h = Storage.getStore(HistoryConnectedMachine.class);
        h.addItem(machineItem);
        Storage.flashStore(h);
        this.dispose();
    } else {
        av.a("connect_failed");                      // 失败提示
    }
}
```

调用链：`a.a(MachineItem)` → `machine.b.d.b(addr, port)` → `Socket.connect`。
连接成功后存历史——**下次优先用历史连接**（和我们的 NVS IP 缓存同理）。

## 二、设备发现 `esp32grbl/b.java`（mDNS 自动搜索）

```java
final class b extends JPanel {
    private JmDNS a;
    private DefaultListModel b = new DefaultListModel();
    private ServiceListener f = new c(this);

    public b(a object) {
        this.e = new c.a.a.a.a.a();                   // 网络实例
        this.e.a(...c.a.a.c.c.c()..., new d(this));  // 绑定网络
        this.a = JmDNS.create();
        this.a.addServiceListener("_http._tcp.local.", this.f);
    }
}
```

用 **JmDNS** 监听 `_http._tcp.local.` 服务。Grbl_Esp32 的 WebUI 会注册这个
mDNS 服务，奎享据此发现局域网内的写字机。

### ServiceListener（`c.java`）

```java
public final void serviceResolved(ServiceEvent e) {
    // 解析到的设备加进列表
}
public final void serviceAdded(ServiceEvent e)   { /* 等 resolved */ }
public final void serviceRemoved(ServiceEvent e) { /* 从列表移除 */ }
```

### description.xml 识别（`MachineItem.java`）

发现到设备后，**还要抓 `description.xml` 确认是 Marlin 还是 Grbl**：

```java
public MachineItem(f obj, int port, JList jList) {
    if (!obj.b().endsWith("description.xml")) return;
    URL url = new URL(obj.b());
    Element root = new SAXBuilder().build(url.openStream()).getRootElement();
    Element device = getChild(root, "device");
    if (device != null) {
        Element model = getChild(device, "modelNumber");
        if (model != null && model.getText().equals("Marlin")) {
            this.address = obj.a().getHostAddress();
            this.port = port;
            // 加进列表（去重）
        }
    }
}
```

抓设备的 `description.xml`（UPnP 风格的设备描述），解析 XML 里的
`<device><modelNumber>` 字段，若 = "Marlin" 才认。

> **本页当年留的那句猜测（「对 Grbl_Esp32 的发现可能走另一条路」）已由
> finding 16 §二 用源码解答**：这段属于**独立的 SSDP/UPnP 通道**（`d.java` 回调），
> 与 mDNS 通道并行、各自往同一列表加候选。`modelNumber == "Marlin"` 是
> ESP3D WebUI 的固定值，不代表运动固件类型。细节看 16，本页不再维护。

### 对 hutuji 的启示

我们早期的 `hutuji_pipe.cc` 用过 mDNS（`grblesp.local`）发现，后来因为
**mDNS 阻塞 7-60s** 改成了 NVS IP 缓存 + 非阻塞 connect（见记忆
`s3-grbl-telnet-first-link`）。奎享用 JmDNS 是 GUI 应用，用户等着选，
阻塞可接受；我们语音场景冷启要快，去掉 mDNS 是对的。

## 三、设备类型判定与端口映射

| 固件 | port | 发现方式 |
|------|------|----------|
| Grbl_Esp32 | **23** | mDNS `_http._tcp` + description.xml |
| Marlin | **8080** | 同上（modelNumber="Marlin" 时改 port） |

奎享按"连的是哪个固件控制器"决定 port：
- 当前控制器是 `marlin.a` → 8080
- 否则（Grbl）→ 23

**对我们**：我们固定 Grbl_Esp32，port 永远 23，不需要这层判定。

## 四、MachineItem：设备数据模型

```java
public class MachineItem {
    public String name;       // 用户起的别名
    public String address;    // IP
    public int port;

    public boolean isNetAddress() {
        return address.matches("^([0-9]{1,3}\\.){3}[0-9]{1,3}$")   // IPv4
            || address.matches("^(([0-9a-fA-F]{1,4}):){7}...$");      // IPv6
    }
}
```

简单 POJO。`isNetAddress()` 判断是不是 IP（vs 串口名如 COM3）。
hutuji 没有这层——我们只走 WiFi，没有串口/WiFi 混选 UI。

## 五、对 hutuji 的对照与启示

| 维度 | 奎享 | hutuji | 评估 |
|------|------|--------|------|
| WiFi port | Grbl=23, Marlin=8080 | 固定 23 | ✓ 一致 |
| 传输类 | `machine/b/d.java`（Socket）| `hutuji_pipe.cc`（lwip socket）| 同构 |
| 发现 | mDNS + description.xml | 早期 mDNS，已去 | 我们更激进 |
| IP 缓存 | `HistoryConnectedMachine` | NVS IP 缓存 | 同理 |
| 连接超时 | 10s（`Socket.connect`）| NVS 命中 2s | 我们更快 |
| 多设备选择 | UI 列表让用户选 | 固定单设备 | 我们无此需求 |

### 可借鉴

1. **history 去重 + 别名**：奎享存历史连接时按 IP 去重，允许起别名。
   我们的 NVS 只存一个 IP。若以后有多台写字机可借鉴，**当前单设备不改**。

2. **description.xml 设备指纹**：奎享抓 XML 确认设备类型。我们走固定 IP +
   `$I` 探活（`[VER:...]`）确认是 Grbl。**我们的更轻量**，不改。

3. **port 23 的再次印证**：奎享原厂就用 23 连 Grbl_Esp32。我们之前担心
   Telnet:23 会不会有问题，现在有原厂背书——**方案 E 选 23 是对的**。

## 六、证据边界

- `description.xml` 的完整结构未抓实物（需要实际 Grbl_Esp32 WebUI 才有），
  从代码推断是 UPnP 风格的 `<root><device><modelNumber>` 树。
- mDNS 服务名 `_http._tcp.local.` 是硬编码字面量，无歧义。
- `c.a.a.a.a.a()` / `c.a.a.c.c.c()` 是混淆的网络库 API（疑为
  `javax.jmdns` 的包装），具体实现未读，但 JmDNS 语义明确。
