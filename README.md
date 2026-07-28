# 文枢 WenShu

文枢是一个本地优先的 AI 内容工作台：围绕资料整理、内容策划、写作、审阅与交付组织工作，并在执行文件写入、命令等重要操作前请求确认。模型密钥、会话和本地工作流由用户自己掌握。

本项目是 [OpenWorker](https://github.com/andrewyng/openworker) 的 MIT 许可分支。文枢保留其本地 Agent 引擎和桌面壳基础，并以中文内容工作流为产品方向；OpenWorker 的归属与原始版权声明保留在仓库的 [LICENSE](LICENSE) 中。

## 产品原则

- **本地优先**：Agent 循环、会话、设置和凭据保存在本机；数据只会发送到用户明确选择的模型或连接服务。
- **结果导向**：工作以文档、报告、网页等可继续编辑和交付的文件为目标，而不止于聊天回复。
- **操作可控**：写文件、发送内容和运行命令等重要动作继续使用现有审批机制。
- **自带模型**：可配置 OpenAI、Anthropic、Google、Ollama 等兼容提供方；密钥不会由文枢托管。
- **开发版不自动更新**：文枢 0.1 不连接 OpenWorker Cloud、Gallery、Relay、托管 OAuth 或上游更新服务。

## 从源码运行

需要 Python 3.10+、Node.js 20+。只有运行 Tauri 桌面壳时才需要 Rust 工具链；Linux 开发可以直接使用浏览器界面，不依赖桌面打包。

先在仓库根目录完成一次环境初始化：

```shell
bash packaging/setup_dev_env.sh
```

### Linux 浏览器开发

在第一个终端从仓库根目录启动本地 Agent 服务：

```shell
.venv/bin/openworker-server --cwd "$PWD" --port 8765
```

在第二个终端启动界面：

```shell
cd surfaces/gui
npm install
npm run dev
```

打开 Vite 输出的本地地址即可。服务会为每次启动创建令牌；Vite 开发服务器会读取对应的本地令牌文件并转发请求。

### Tauri 桌面开发

安装 Rust 工具链后，在 `surfaces/gui/` 运行：

```shell
npm install
npm run tauri dev
```

Tauri 壳会选择空闲端口、启动并监督随附的本地 server sidecar。

## 内部兼容名称

文枢 0.1 只切换用户可见品牌和产品元数据。为避免破坏已有脚本、开发环境和本地数据，以下内部名称暂时保留：

- Python 包名 `coworker`；
- CLI 与桌面 sidecar 文件名 `openworker-server`；
- `COWORKER_*` 环境变量；
- 现有 state 目录、数据库布局、HTTP/WS 协议名与认证 header；
- 现有 JavaScript event、localStorage key 和注入变量。

这些名称属于兼容接口，不代表界面仍使用 OpenWorker 品牌。

## 仓库结构

| 目录 | 内容 |
|---|---|
| `coworker/` | Python Agent 引擎、模型提供方、工具、连接器、记忆与自动化 |
| `surfaces/gui/` | React 界面与 Tauri 桌面壳 |
| `stt/` | 本地语音转文字 sidecar |
| `packaging/` | 开发环境初始化与桌面打包脚本 |
| `tests/` | 后端测试 |

## 上游与许可

文枢基于 Andrew Ng 发起的 [OpenWorker](https://github.com/andrewyng/openworker)，其引擎又建立在 [aisuite](https://github.com/andrewyng/aisuite) 之上。感谢 OpenWorker、aisuite 及其贡献者。此分支不会覆盖或移除上游版权声明。

## License

MIT - see [LICENSE](LICENSE).
