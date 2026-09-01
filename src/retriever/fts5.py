# -*- coding: utf-8 -*-
"""检索应答引擎：SQLite FTS5 全文检索与相关性评分。"""
import logging
import re
import sqlite3

from models.db import query

logger = logging.getLogger(__name__)

# 汉字连续段识别：unicode61 不识别中文分词，需做 bigram 切分
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
# 英文/数字连续段识别：由 unicode61 天然分词，原样保留
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")


def segment_bigrams(text: str) -> list[str]:
    """对文本做字符二元组切分，供入库与检索共用。

    汉字连续段切为二元组（"企业微信"→"企业 业微 微信"）；
    英文/数字保留原样，交给 unicode61 分词。

    参数：
        text: 原始文本。

    返回：
        切分后的词项列表。
    """
    tokens = []
    for match in _CJK_RUN.finditer(text):
        run = match.group()
        if len(run) >= 2:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
        else:
            tokens.append(run)
    tokens.extend(_ASCII_WORD.findall(text))
    return tokens


def build_match_query(question: str) -> str:
    """由 bigram 词项生成 FTS5 MATCH 表达式，词项为空时返回空串。

    参数：
        question: 用户问题文本。

    返回：
        FTS5 MATCH 表达式；无有效词项时返回空串。
    """
    terms = list(dict.fromkeys(segment_bigrams(question)))
    if not terms:
        return ""
    return " OR ".join(f'"{term}"' for term in terms)


def search(db_path: str, question: str, top_k: int, score_threshold: float) -> list[dict]:
    """FTS5 全文检索并按置信度阈值过滤。

    参数：
        db_path: 数据库文件路径。
        question: 用户问题。
        top_k: 召回候选条数。
        score_threshold: 置信度阈值，低于视为无结果。

    返回：
        命中候选字典列表（含 title/content/source/doc_id/raw_content/score）。
    """
    match_expr = build_match_query(question)
    if not match_expr:
        logger.info("问题无有效检索词，跳过检索: %s", question)
        return []
    sql = (
        "SELECT title, content, source, doc_id, raw_content, -bm25(kb_docs) AS score "
        "FROM kb_docs WHERE kb_docs MATCH ? "
        "ORDER BY bm25(kb_docs) LIMIT ?"
    )
    try:
        rows = query(db_path, sql, (match_expr, top_k))
    except sqlite3.Error as exc:
        logger.error("FTS5 检索失败: %s | 表达式: %s", exc, match_expr)
        return []
    hits = [row for row in rows if row["score"] >= score_threshold]
    if not hits and rows:
        # bm25 绝对值随语料规模漂移：小语料下查询词命中全部文档时 IDF 较低，
        # -bm25 得分≤0，任何正数阈值都会误杀唯一正确命中，故兜底取 top1
        logger.info(
            "候选 %d 条全部低于阈值 %.2f，兜底返回 top1（score=%.4f）",
            len(rows),
            score_threshold,
            rows[0]["score"],
        )
        hits = rows[:1]
    logger.info(
        "检索命中 %d/%d 条（阈值 %.2f）: %s",
        len(hits),
        len(rows),
        score_threshold,
        question,
    )
    return hits
