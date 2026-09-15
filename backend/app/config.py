"""应用配置：统一从环境变量读取（.env / 系统环境）。

使用 pydantic-settings，字段自动映射同名环境变量（大小写不敏感）。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 占位 Key（见 .env.example）：这些值不算"已配置 LLM"
_PLACEHOLDER_API_KEYS = {
    "your-api-key-here", "your_api_key_here", "changeme", "replace-me", "sk-xxx",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # 兼容两种启动位置：从 backend/ 或仓库根目录运行
        env_file=[".env", ".env.local", "../.env"],
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- LLM（OpenAI 兼容接口）----
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    # 结构化调用（意图/抽取）可用更强模型，留空则沿用 llm_model
    llm_structured_model: str = ""

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
        """是否真的配置了可用的 LLM Key。

        占位值（如 `.env.example` 的 `your-api-key-here`）**不算已配置**：
        否则 `LLM_MOCK=false` 时会拿着占位串去调真实接口，拿到 401 并把
        "鉴权失败"这种误导性结论抛给用户（实测）。`scripts/setup.sh` 也据此自动切 LLM_MOCK。
        """
        key = (self.llm_api_key or "").strip()
        if not key or key.lower() in _PLACEHOLDER_API_KEYS:
            return False
        return True

    # ---- M11.1 认证相关派生属性 ----
    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in ("prod", "production")

    @property
    def access_token_ttl_s(self) -> int:
        return max(1, self.access_token_ttl_min) * 60

    @property
    def refresh_token_ttl_s(self) -> int:
        return max(1, self.refresh_token_ttl_days) * 24 * 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
