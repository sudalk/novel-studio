from pathlib import Path

import pytest

from app.services.importer import heading_title, split_episodes

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (ROOT / "samples" / "rain-night.txt").read_text(encoding="utf-8")


def test_sample_splits_into_two_episodes():
    result = split_episodes(SAMPLE)
    assert len(result.episodes) == 2
    assert "雨夜来信" in result.episodes[0].title
    assert "天明" in result.episodes[1].title
    assert "林晚轻声道" in result.episodes[0].text
    assert "信已经烧了" in result.episodes[1].text


def test_chapter_heading_variants():
    raw = "第十二章：夜雨\n城门关了。\n\n第2集\n他又回来了。\n## 后记\n雨停了。\n"
    result = split_episodes(raw)
    titles = [episode.title for episode in result.episodes]
    assert titles[0].startswith("第十二章")
    assert titles[1].startswith("第2集")
    assert titles[2] == "后记"


def test_no_heading_becomes_one_episode():
    result = split_episodes("只有一段正文，没有章节标题。")
    assert len(result.episodes) == 1
    assert result.episodes[0].title == "第1集"


def test_short_preamble_is_suggested_title():
    result = split_episodes("灯下\n\n第1章\n他推开门。\n")
    assert result.suggested_title == "灯下"
    assert len(result.episodes) == 1


def test_empty_text_rejected():
    with pytest.raises(ValueError):
        split_episodes("  \n\t")


def test_heading_detector_ignores_ordinary_sentences():
    assert heading_title("第一天他就出门了") is None
    assert heading_title("第3章 出城") == "第3章 出城"
    assert heading_title("## 第1集 雨夜来信") == "第1集 雨夜来信"
