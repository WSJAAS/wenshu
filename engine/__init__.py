"""文枢 · 文本重塑引擎（engine）

独立于后端的纯 Python 模块集合，可单独调用。

模块：
    local_rewriter.py       去 AI 痕迹：逐条规则表改写，仅依赖标准库
    text_diagnostics.py     文本体检：评分、Show/Tell、感官密度、套话检出
    chunking.py             分块：把长文切成可处理单元
    aigc_round_service.py   轮次改写与任务隔离（内容指纹）
    aigc_records.py         改写记录
    llm_client.py           模型调用（依赖 litellm）
    app_config.py           引擎侧配置（DPAPI 加密存储密钥）

来源：baibaiAIGC（AGPL-3.0）。改动见仓库根 CHANGES.md。
"""
