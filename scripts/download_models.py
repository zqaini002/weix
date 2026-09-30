#!/usr/bin/env python3
"""Weix AI 模型预下载脚本。

在全新安装后运行，提前下载 embedding 模型和 tokenizer 数据，
避免首次启动时的长时间等待。
"""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def download_embedding_model():
    """下载 sentence-transformers embedding 模型 (~1.3GB)。"""
    model_name = "paraphrase-multilingual-MiniLM-L12-v2"

    # 国内优先使用镜像
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    logger.info("=" * 50)
    logger.info("下载 embedding 模型: %s", model_name)
    logger.info("镜像源: %s", os.environ["HF_ENDPOINT"])
    logger.info("模型大小约 1.3GB，请耐心等待...")
    logger.info("=" * 50)

    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_name)
        dim = model.get_embedding_dimension()
        logger.info("embedding 模型下载完成: dim=%d", dim)
        return True
    except Exception as exc:
        logger.error("embedding 模型下载失败: %s", exc)
        return False


def download_tiktoken():
    """下载 tiktoken 编码器 (~2MB)。"""
    logger.info("-" * 50)
    logger.info("下载 tiktoken 编码器...")

    try:
        import tiktoken

        # 下载常用编码
        for encoding in ["cl100k_base", "o200k_base"]:
            try:
                enc = tiktoken.get_encoding(encoding)
                logger.info("tiktoken %s: vocab_size=%d", encoding, enc.n_vocab)
            except Exception:
                pass

        logger.info("tiktoken 编码器下载完成")
        return True
    except Exception as exc:
        logger.error("tiktoken 下载失败: %s", exc)
        return False


def download_chromadb_embedding():
    """预热 chromadb embedding 模型 (~90MB)。"""
    logger.info("-" * 50)
    logger.info("预热 chromadb embedding 模型...")

    try:
        import chromadb
        from chromadb.utils import embedding_functions

        ef = embedding_functions.DefaultEmbeddingFunction()
        # 触发模型下载
        _ = ef(["test"])
        logger.info("chromadb embedding 模型下载完成")
        return True
    except Exception as exc:
        logger.warning("chromadb embedding 预热失败（可忽略）: %s", exc)
        return False


def main():
    logger.info("Weix AI 模型预下载")
    logger.info("")
    logger.info("预计下载总量: ~1.4GB")
    logger.info("")

    results = {
        "embedding": download_embedding_model(),
        "tiktoken": download_tiktoken(),
        "chromadb": download_chromadb_embedding(),
    }

    logger.info("")
    logger.info("=" * 50)
    success = sum(1 for v in results.values() if v)
    total = len(results)
    logger.info("模型下载完成: %d/%d 成功", success, total)

    if success < total:
        failed = [k for k, v in results.items() if not v]
        logger.warning("以下模型下载失败: %s", ", ".join(failed))
        logger.warning("首次启动时会自动重试下载")
        sys.exit(1)
    else:
        logger.info("所有模型已就绪，首次启动将秒级响应")


if __name__ == "__main__":
    main()
