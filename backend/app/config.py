"""应用配置：统一从环境变量读取（.env / 系统环境）。

字段自动映射同名环境变量（**大小写不敏感**，pydantic v1 `BaseSettings` 的默认行为）。

为什么是 pydantic **v1** 而不是 v2：
    Android 一体化版本把 Python 运行时随 APK 分发，而 pydantic v2 依赖
    `pydantic-core`（Rust 扩展），Chaquopy/Android 上没有任何可用轮子。
    v1.10.24+ 起提供 `py3-none-any` 纯 Python 轮子，且 FastAPI 与 openai SDK
    都仍声明支持 `pydantic>=1.10.13,<3`，因此服务端与 Android 能共用同一份代码。
    详见 docs/run.md「Android 一体化」一节。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import BaseSettings

# 占位 Key（见 .env.example）：这些值不算"已配置 LLM"
_PLACEHOLDER_API_KEYS = {
    "your-api-key-here", "your_api_key_here", "changeme", "replace-me", "sk-xxx",
}


class Settings(BaseSettings):
    class Config:
        # 兼容两种启动位置：从 backend/ 或仓库根目录运行
        env_file = (".env", ".env.local", "../.env")
        env_file_encoding = "utf-8"
        # .env 里可能有本文件不认识的键（如历史遗留的账号配置），一律忽略
        extra = "ignore"

    # ---- LLM（OpenAI 兼容接口）----
    # 下面三项是"默认供应商"（向后兼容沿用旧字段名）：未选具体供应商时生效。
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    # 结构化调用（意图/抽取）可用更强模型，留空则沿用 llm_model
    llm_structured_model: str = ""

    # ---- LLM 多供应商（用户自备 Key；详见 app/llm/providers.py）----
    # 选中的内置/自定义供应商 id（如 deepseek / siliconflow / ollama）；留空=用上面的默认供应商
    llm_provider: str = ""
    # 追加自定义供应商：JSON 对象 {"id": {...}} 或数组 [{"id": ...}, ...]
    # 适合少量短配置；条目多或含长 Key 建议用 LLM_PROVIDERS_FILE
    llm_providers: str = ""
    # 供应商 JSON 文件路径（内容格式同 LLM_PROVIDERS；文件不存在只告警不报错）
    llm_providers_file: str = ""
    # API 方言：auto（先试 chat_completions，404/405 自动退 responses）/ chat_completions / responses
    llm_api_dialect: str = "auto"
    # 随请求下发的额外请求头 / 额外 body（JSON 对象）——给需要自定义头的网关用
    llm_extra_headers: str = ""
    llm_extra_body: str = ""
    # 单次 LLM 请求超时（秒）：推理模型首 token 可能很慢，别设太小
    llm_timeout_s: float = 60.0
    # 单次生成的最大输出 token（**含思考 token**）。原值 1200 过小：
    # 推理模型先想掉一部分，长回答（如逐站列出）就会在半句话处被截断，
    # 而模型其实会给出 finish_reason=length —— 应用现已据此显式声明截断。
    llm_max_tokens: int = 4096
    # 模型的上下文窗口（token）。用于把"输出预算"限制在窗口之内：
    #   实际输出上限 = min(llm_max_tokens, 窗口 - 已用输入 - 安全余量)
    # 各家差别很大（8k / 32k / 128k / 200k），自己填一个与所用模型相符的值，
    # 避免请求因超窗口被上游 400 拒绝。留 0 表示不做窗口约束。
    llm_context_tokens: int = 32000
    # 提示词 token 的估算系数：中文约 1 token ≈ 1.5 字，故 字符数 / 该系数 ≈ token。
    # 只是估算（用于预算与告警），不求精确 —— 精确计数要额外调用 tokenizer。
    llm_chars_per_token: float = 1.5
    # 是否允许把 LLM 请求发往内网/环回地址（自定义供应商 + 本地 Ollama 需要；
    # 生产多租户部署下开启等于开放 SSRF，故默认关闭且仅对显式配置的供应商放行）
    llm_allow_private_base_url: bool = False

    # LLM_MOCK=true 时：不走真实模型，用确定性本地 mock 跑通整链（无 Key 演示/CI 用）
    llm_mock: bool = False

    # ---- 服务器 ----
    host: str = "127.0.0.1"
    port: int = 8000

    # ---- 运行环境 ----
    # dev / production。production 下对"必须显式配置的密钥"做 fail-fast 校验。
    app_env: str = "dev"

    # ---- 输入与成本治理（M11.1 审计修复）----
    # 单条用户消息字符上限（超限直接 422，避免超长输入把 token 成本与延迟打爆）
    max_message_chars: int = 2000
    # 检索层工具并发度（工具之间无依赖；实测 4 工具串行最坏 ~48s）
    tool_concurrency: int = 3
    # 单条工具事实注入生成 prompt 的字符上限（表格类工具走专用渲染，见 generate.py）
    fact_text_max_chars: int = 4000
    # 表格类事实最多注入多少行（超出时显式标注省略行数）
    fact_table_max_rows: int = 40
    # station.lookup 列表展示条数（本地索引排序后取前 N；mcp 自身硬上限仅 10 条）
    station_list_limit: int = 12
    # station.screen（12306 车站大屏）单次下发的明细条数：接口一次返回全天 200–700 条，
    # 必须裁剪后再进 prompt（超出部分由 ToolResult.total/truncated 如实登记）
    station_screen_limit: int = 15
    # 按线路名查站序/指定径路的结果缓存（秒）：每次要抓 3 个约 800KB 页面
    rail_line_cache_ttl_s: int = 3600

    # ---- 性能：决策层优化（2026-09-15 拍板 P0）----
    # 确定性快路径：正则+本地站点库直接给出意图与槽位，**不打 LLM**（实测覆盖 ~56–68% 问法，
    # 预筛选 3.8–11.6s → <0.3s）。出问题时置 false 一键回退到纯 LLM 决策。
    fastpath_enabled: bool = True
    # 结构化调用（意图/槽位）关闭"思考"：这类任务里思考 token 纯属延迟（实测 3.3s → 1.1s）
    llm_structured_no_think: bool = True

    # ---- 本地数据字典（里程/车站档案/离线时刻；2026-09-15 拍板）----
    # 由 `scripts/mirror_dict.py` 构建到 backend/data/dict.db（已 gitignore）。
    # 相对路径按 backend/ 解析。
    dict_db_path: str = "data/dict.db"
    # GTFS 是周更数据：超过该天数视为快照过期（仅用于提示，不在请求路径里自动下载）
    dict_gtfs_max_age_days: int = 5
    # 对黄河铁路网等**个人站点**的请求间隔下限（秒）——礼貌约束，别为了快调小
    dict_site_min_interval_s: float = 2.0

    # ---- 接口文档暴露（/docs、/redoc、/openapi.json）----
    # 开发期保留便于联调；生产必须关闭或置于鉴权/内网之后（未鉴权的 /docs 会完整暴露接口面）
    enable_api_docs: bool = True

    # ---- 前端静态目录 ----
    # 留空时按仓库布局推导（backend/app/../../frontend）。
    # 打包分发场景（Android 一体化 / PyInstaller）里仓库结构并不存在，
    # 必须用本项显式指定解包后的静态目录，否则主页会 404。
    frontend_dir: str = ""

    # ---- 前端代理目标 ----
    api_base: str = "http://127.0.0.1:8000"

    # ---- 数据源（按需启用；留空表示不启用）----
    # 12306 余票/时刻查询反代（参考 Parse12306；需带 Cookie/代理，留空则该工具停用）
    t12306_base: str = ""
    # rail.re（原 moerail.ml）车站/动车组交路查询入口
    railre_base: str = "https://rail.re"
    # rail.re 交路 API（动车组担当车组查询；12306 不提供此数据）
    railre_api_base: str = "https://api.rail.re"
    # cnrail.geogv.org 铁路地图（站名→地图外链）
    cnrail_base: str = "https://cnrail.geogv.org"
    # jprailfan.com 黄河铁路网（中国/日本铁路、客里表、电报码）
    jprailfan_base: str = "https://jprailfan.com"
    # 95306.cn 营业站货运服务信息查询
    freight_95306_base: str = "https://95306.cn"
    # kmrail.cn 昆铁货运（全路各站货运范围/停限装公告）
    kmrail_base: str = "https://kmrail.cn"
    # kyfw.sytlj.com 沈阳铁路局余票/时刻
    sytlj_base: str = "https://kyfw.sytlj.com"
    # 通用网页抓取兜底：http 超时（秒）与单次响应体上限（字节）
    http_timeout: float = 12.0
    # 实测一个 172MB 页面曾让进程 RSS 冲到 920MB → 默认 2MB 上限
    http_max_bytes: int = 2_000_000
    # 搜索命中后**顺手读正文**的条数（0 = 关闭，退回"只给标题+摘要"的行为）。
    #
    # 为什么需要：搜索引擎返回的摘要上限只有 300 字，而百度那条路实测**连摘要都没有**
    # （815KB 的 SERP 里 5 条结果 0 条带摘要），知识型问题等于只有标题可看。
    # 只读前 N 条，是为了把"多花的抓取时间"限制在可预期范围内（并发抓取）。
    web_search_fetch_top_n: int = 2
    # 每个页面注入正文的字符上限（**上限**，不是固定值：实际会按注入预算的剩余量
    # 动态下调，见 web_search._fetch_top_pages）。按页截断，且会如实标注。
    web_search_fetch_chars: int = 1800
    # 离线车次目录（非实时兜底）的缓存有效期（天）；超期后重新下载并保留旧副本兜底
    train_cache_ttl_days: int = 30
    # 12306 预售期参考值（天）：日期超出时在说明里点明“可能超预售期”，
    # 避免把“本来就查不到”误述成“工具故障”
    ticket_presale_days: int = 15
    # 离线车次目录未命中时，用于"探测"该车次起止站的枢纽区间（形如 北京:上海,北京:广州）
    # 目录源自 2022 停更数据，实测缺 G101 等车次（R1 P1-6 / D04）
    hub_probe_pairs: str = "北京:上海,北京:广州,北京:哈尔滨,上海:广州"

    @property
    def structured_model(self) -> str:
        return self.llm_structured_model or self.llm_model

    @property
    def llm_ready(self) -> bool:
        """是否真的配置了可用的 LLM。

        判定顺序（社区版"用户自备 Key"引入多供应商后）：
        1. 当前生效的供应商**自带非占位 Key** → 就绪；
        2. 供应商免 Key（本地 Ollama / LM Studio / 内网网关）且模型名已定 → 就绪；
        3. 兜底看 `LLM_API_KEY` 是否为非占位值。

        占位值（如 `.env.example` 的 `your-api-key-here`）**不算已配置**：
        否则 `LLM_MOCK=false` 时会拿着占位串去调真实接口，拿到 401 并把
        "鉴权失败"这种误导性结论抛给用户（实测）。`scripts/setup.sh` 也据此自动切 LLM_MOCK。
        """
        try:
            from app.llm.providers import resolve_provider

            p = resolve_provider(settings=self)
            if p.model:
                if p.api_key and p.api_key.lower() not in _PLACEHOLDER_API_KEYS:
                    return True
                if not p.needs_key:
                    return True
        except Exception:  # noqa: BLE001 —— 供应商配置写错不应让 /health 500
            pass

        key = (self.llm_api_key or "").strip()
        if not key or key.lower() in _PLACEHOLDER_API_KEYS:
            return False
        return True

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in ("prod", "production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
