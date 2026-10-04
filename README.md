# OpenRailFanAI

面向中国铁路车迷的 AI 助手，用自然语言查询列车、车站和车组信息，也能回答铁路知识问题。

例如：

- “G1 今天的时刻表”
- “明天北京南到上海虹桥还有票吗？”
- “G1 今天由哪组动车组担当？”
- “什么是重联？”

## 功能

| 功能 | 数据来源 |
| --- | --- |
| 列车时刻、经停站与余票 | 12306 |
| 车次与担当车组的交路记录 | rail.re |
| 车站信息、站名与电报码查询 | 12306 站点库、本地字典 |
| 车站到发信息 | 12306 车站大屏 |
| 线路径路与里程 | 黄河铁路网、本地字典 |
| 铁路知识问答与补充检索 | 大模型、网页搜索 |

支持多轮追问、独立会话、流式回答、停止生成和重新生成。提供浏览器界面及 Android 应用。

查询结果受数据源覆盖、日期和网络状态影响。历史交路不代表当天担当，图定时刻不代表实际运行；余票以 12306 购票页面为准。知识回答可能包含未经检索确认的内容。

## 使用

**Android**：从 [Releases](https://github.com/Xiao-John/OpenRailFanAI/releases) 下载对应安装包，按该版本说明配置模型。

**从源码运行**：需要 Python 3.10 或以上，推荐 3.12。数据查询需要能访问相应数据源的网络。

在仓库根目录执行：

```bash
bash scripts/setup.sh
```

脚本会安装依赖、准备配置并启动服务。浏览器打开 `http://127.0.0.1:8000/`。更换端口：

```bash
PORT=8001 bash scripts/setup.sh
```

真实模型问答需要配置供应商、模型和 API Key，可在界面设置或根目录 `.env` 中配置。支持 OpenAI 兼容接口，包括 DeepSeek、SiliconFlow 等。无 Key 时，启动脚本使用 mock 演示；mock 不代表真实模型效果。

手动安装、模型配置和部署方法见 [运行指南](docs/run.md)。请勿提交 API Key。

## 开发

后端采用 Python、FastAPI 和 Pydantic v1；浏览器界面采用 HTML 与 JavaScript。查询流程为识别请求、提取参数、调用数据源、生成回答。

| 目录 | 内容 |
| --- | --- |
| `backend/` | API、查询编排、数据工具与测试 |
| `frontend/` | 浏览器界面 |
| `android/` | Android 应用 |
| `docs/` | 实现、运行与数据源文档 |
| `scripts/` | 安装、数据准备与构建脚本 |

运行测试：

```bash
bash backend/tests/run_all.sh
```

部分测试需要外部数据源或模型服务。测试条件与单独运行方法见 [运行指南](docs/run.md)。

## 文档

- [文档索引](docs/README.md)
- [架构与实现](docs/EXPL.md)
- [数据源说明](docs/datasources.md)
- [Android 构建](docs/android.md)

## 许可

采用 [MIT License](LICENSE)。第三方数据的使用须遵守相应来源的条款。
