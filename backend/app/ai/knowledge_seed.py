"""知识库种子数据初始化。

将基础业务知识（FAQ、流程、群规等）写入向量数据库，
使 AI 在回答时可以检索到这些信息。
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

SEED_DOCUMENTS: list[dict] = [
    {
        "text": "陪玩价格：王者荣耀钻石以下 ¥30/h，英雄联盟钻石以上 ¥50/h，具体以陪玩师报价为准。其他游戏价格请咨询陪玩师。",
        "topic": "价格",
        "priority": "high",
    },
    {
        "text": "点单流程：回复「点单」或「陪玩」，按格式填写：游戏 段位 时长 预算 备注。示例：英雄联盟 钻石 2h ¥50 打野",
        "topic": "流程",
        "priority": "high",
    },
    {
        "text": "下单后系统会自动匹配陪玩师，匹配成功后会推送陪玩师信息给你。如果长时间未匹配，可以联系群管理员。",
        "topic": "流程",
        "priority": "medium",
    },
    {
        "text": "陪玩师都是经过筛选和考核的，如果对陪玩师的服务不满意，可以在群内反馈或联系管理员处理。",
        "topic": "服务",
        "priority": "medium",
    },
    {
        "text": "退款政策：如果陪玩师未按时上号或因陪玩师原因无法完成订单，可申请全额退款。如果已经开始了陪玩服务，按已完成时长结算。",
        "topic": "售后",
        "priority": "high",
    },
    {
        "text": "群规：禁止发布广告、色情、暴力等违规内容。禁止私下交易，所有订单需通过正规流程。禁止辱骂、骚扰他人。违规者将被移出群聊。",
        "topic": "群规",
        "priority": "high",
    },
    {
        "text": "工作时间：陪玩服务一般是每天 10:00-24:00，深夜时段可能陪玩师较少。建议提前预约。",
        "topic": "服务",
        "priority": "low",
    },
    {
        "text": "支持的游戏：王者荣耀、英雄联盟、原神、和平精英、永劫无间、CS2、Valorant 等主流游戏。如有其他游戏需求可咨询管理员。",
        "topic": "服务",
        "priority": "medium",
    },
]


async def seed_knowledge_base(vector_store, embedding_manager) -> int:
    """将种子知识写入向量数据库。

    仅当知识库为空时才写入，避免重复。

    Args:
        vector_store: VectorStoreManager 实例。
        embedding_manager: EmbeddingManager 实例。

    Returns:
        写入的文档数量。
    """
    try:
        existing = vector_store.knowledge_base.count()
        if existing > 0:
            logger.info(f"知识库已有 {existing} 条记录，跳过种子初始化")
            return 0
    except Exception:
        pass

    texts = [d["text"] for d in SEED_DOCUMENTS]
    metadatas = [
        {"source": "seed", "topic": d["topic"], "priority": d["priority"], "added_at": time.time()}
        for d in SEED_DOCUMENTS
    ]
    ids = [f"seed_{i}" for i in range(len(texts))]

    embeddings = embedding_manager.embed(texts)
    vector_store.knowledge_base.add(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    logger.info(f"知识库种子数据已初始化: {len(texts)} 条文档")
    return len(texts)
