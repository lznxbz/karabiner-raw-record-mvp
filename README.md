# Karabiner Raw Record MVP

为 Karabiner-Elements 增加 `from.raw_record`，让原有规则引擎能够匹配指定设备的原始 HID 输入报告。首个已验收场景是 **蓝牙 M5 遥控器 Power → 一次 F13 点击**，并且没有附带系统电源动作。

这是基于 **Karabiner-Elements v16.3.0** 的实验性本机开发版本。提供独立的 `RawRecordMVP-Elements` 和 `RawRecordMVP-EventViewer`，与官方应用并排安装，通过工具切换运行，复用已安装的官方虚拟 HID 驱动。

An experimental Karabiner-Elements fork adding input-only `from.raw_record` matching. The Bluetooth M5 Power-to-F13 mapping passed local hardware validation. The parallel build preserves the official installation and switches between the two input engines.

## 源码入口

**请使用 [feat/parallel-install-mvp](https://github.com/lznxbz/karabiner-raw-record-mvp/tree/feat/parallel-install-mvp) 分支。** 本页同时用于仓库首页；`main` 保留上游代码，MVP 实现、独立安装工具和下列文档位于该功能分支。

- [并排安装、切换与完整验收记录](https://github.com/lznxbz/karabiner-raw-record-mvp/blob/feat/parallel-install-mvp/docs/raw-record-parallel.md)
- [raw_record 语法与实现说明](https://github.com/lznxbz/karabiner-raw-record-mvp/blob/feat/parallel-install-mvp/docs/raw-record-mvp.md)
- [蓝牙 M5 完整规则示例](https://github.com/lznxbz/karabiner-raw-record-mvp/blob/feat/parallel-install-mvp/examples/raw-record/m5-bluetooth.json)

## 已验证的结果

在 Apple Silicon Mac、macOS 27.2、Xcode 27.2、官方 Karabiner-Elements 16.3.0 和官方虚拟 HID 驱动 1.8.0 的环境中完成：

- **9 次实体 Power 操作，恰好产生 9 组 F13 按下/松开。** 覆盖短按、两秒长按、蓝牙断开重连、Mac 睡眠唤醒，以及独立 profile 切换后的输入。
- 每个阶段均由用户确认没有附带休眠、关机或电源对话框；长按没有多次触发。
- 方向键、Home、Back、List 和背面字母键均能显示输入事件。当前测试配置只新增 Power → F13 映射。
- 测试后成功恢复原版通信、M5 接管及原服务状态。原版应用、驱动助手、服务声明和配置的 **28 项文件指纹一致**。
- 8 项相关 C++ 测试套件通过，共 2,488 项断言，包含 83 项 raw_record 断言；另有 6 项 profile 工具测试、16 项并排安装与切换测试、8 项配置及通信路径断言通过。
- 双架构应用完成构建与签名核验。实体测试仅覆盖上述蓝牙设备和本机环境。

USB 模式、x86_64 实机运行、强制崩溃恢复、登录/重启，以及按住按键时切换 profile 尚未实测。USB 示例中的 VID/PID 来自已有协议观察，使用前仍需核对实际设备。

## 构建与安装

需要已安装的官方 Karabiner-Elements **16.3.0** 及其正常运行的虚拟 HID 驱动，以及完整 Xcode、Command Line Tools、XcodeGen、CMake、xz、Python 3 和 Ruby。

在 Xcode 中准备有效的 **Apple Development 应用签名证书**，并确认所需的 Apple WWDR 中间证书可用。将下面的签名身份占位符替换为自己的有效应用签名身份。

```sh
git clone --branch feat/parallel-install-mvp https://github.com/lznxbz/karabiner-raw-record-mvp.git
cd karabiner-raw-record-mvp
git submodule update --init --recursive
security find-identity -v -p codesigning
export PQRS_ORG_CODE_SIGN_IDENTITY="YOUR_APPLICATION_SIGNING_IDENTITY_HASH"
bash scripts/raw-record/parallel/build.sh
```

脚本会构建、运行检查并生成 `RawRecordMVP-Parallel.pkg` 和 `parallel-package.json`，最后打印输出目录；**不会自动安装或切换服务**。

应用及其助手使用同一开发证书签名，外层安装包未做分发签名；这是本机开发产物，不是已公证的分发安装包。Apple Development 应用证书不能作为安装包分发签名证书使用。

连接蓝牙 M5 后，将下面两个路径替换为实际构建产物的位置，运行独立安装工具：

```sh
python3 scripts/raw-record/parallel/install.py \
  /path/to/RawRecordMVP-Parallel.pkg \
  /path/to/parallel-package.json
```

安装通过正常系统流程请求管理员确认，校验安装包及签名，并比较原版文件和配置指纹。首次安装会创建独立的 Power → F13 profile；已有独立配置会保留。完整步骤见[并排安装说明](https://github.com/lznxbz/karabiner-raw-record-mvp/blob/feat/parallel-install-mvp/docs/raw-record-parallel.md)。

## 切换使用与恢复原版

安装后，先打开独立应用并给名称含 **RawRecordMVP** 的后台服务、辅助功能和输入监控项目授予所需权限：

```sh
python3 "/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/tools/raw-record-switch.py" setup
```

启用独立版。工具会临时暂停原版输入引擎，再启动独立版；共享虚拟 HID 驱动继续运行：

```sh
python3 "/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/tools/raw-record-switch.py" developer
```

结束后恢复原版：

```sh
python3 "/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/tools/raw-record-switch.py" original
```

命令以当前登录用户运行，管理员确认由系统请求。**仅打开独立应用不会启用映射**，必须通过切换工具激活。若上次切换中断，先运行 `original` 完成恢复，再开始新的测试。

独立配置位于 `~/.config/karabiner-raw-record-mvp/karabiner.json`；设置了 `XDG_CONFIG_HOME` 时使用对应目录。独立版不会复制或改写原版 profile。首次在其他设备测试 Power 前，应保存工作并确认目标设备已被独立版接管。

## 规则示例

下面是一个 basic manipulator 片段。`key_down` 和 `key_up` 只包含报告正文，**不包含 Report ID**；设备、集合、报告编号和长度均需精确匹配。

```json
{
  "type": "basic",
  "from": {
    "raw_record": {
      "vendor_id": 25098,
      "product_id": 1031,
      "collection_usage_page": 1,
      "collection_usage": 128,
      "report_id": 3,
      "payload_length": 1,
      "key_down": [1],
      "key_up": [0]
    }
  },
  "to": [{ "key_code": "f13", "repeat": false }]
}
```

蓝牙 M5 使用 VID **25098**、PID **1031**、System Control 集合 **0x01/0x80**。`repeat:false` 在按下时输出一次完整 F13 点击，原始释放报告使下一次按下能够再次触发。

`raw_record` 是输入专用定义，输出继续使用普通 `to` 动作。不支持通配符、掩码或任意原始 HID 输出；应只用于普通输入管线尚未转换的输入。完整字段约束见[语法说明](https://github.com/lznxbz/karabiner-raw-record-mvp/blob/feat/parallel-install-mvp/docs/raw-record-mvp.md#json)。

## 验收记录与上游归属

独立 EventViewer 提供默认关闭的 F13 验收记录模式，仅记录 F13 按下、松开及时间。启用方式和验证边界见[验收指南](https://github.com/lznxbz/karabiner-raw-record-mvp/blob/feat/parallel-install-mvp/docs/raw-record-parallel.md#optional-f13-hardware-test-recorder)。证书、安装包、本机配置和按键日志不随源码公开。

本项目基于 [pqrs-org/Karabiner-Elements](https://github.com/pqrs-org/Karabiner-Elements)，保留上游历史和 [LICENSE.md](LICENSE.md)。官方应用下载与常规使用说明请参阅 [Karabiner-Elements 官方网站](https://karabiner-elements.pqrs.org/)。
