# -*- coding: UTF-8 -*-
"""Unit tests for mobile/item_resolver.py."""

import json
from http.cookiejar import CookieJar
from unittest.mock import Mock, patch, MagicMock

import pytest

from mobile.item_resolver import (
    KNOWN_CITY_TOKENS,
    DamaiItemDetail,
    DamaiItemResolveError,
    DamaiItemResolver,
    build_search_keyword,
    city_keyword,
    extract_item_id,
    find_conflicting_city,
    normalize_text,
    title_similarity,
)


# ---------------------------------------------------------------------------
# extract_item_id
# ---------------------------------------------------------------------------

class TestExtractItemId:

    def test_extracts_from_full_url(self):
        url = "https://m.damai.cn/shows/item.html?itemId=1016133935724"
        assert extract_item_id(url) == "1016133935724"

    def test_extracts_from_raw_number(self):
        assert extract_item_id("1016133935724") == "1016133935724"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError, match="itemId"):
            extract_item_id("https://m.damai.cn/shows/item.html")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            extract_item_id("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            extract_item_id("   ")

    def test_non_string_raises(self):
        with pytest.raises(ValueError):
            extract_item_id(None)

    def test_extracts_from_lowercase_itemid_param(self):
        url = "https://m.damai.cn/shows/item.html?itemid=1016133935724"
        assert extract_item_id(url) == "1016133935724"

    def test_extracts_from_path_segment(self):
        url = "https://m.damai.cn/shows/1016133935724"
        assert extract_item_id(url) == "1016133935724"

    def test_extracts_from_id_query_param(self):
        url = "https://detail.damai.cn/item.htm?id=1016133935724"
        assert extract_item_id(url) == "1016133935724"

    def test_no_extractable_id_raises(self):
        with pytest.raises(ValueError):
            extract_item_id("not-a-url-or-number")


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText:

    def test_removes_brackets_and_spaces(self):
        assert normalize_text("【北京】 2026 张杰 未·LIVE") == "北京2026张杰未live"

    def test_empty_string_returns_empty(self):
        assert normalize_text("") == ""

    def test_none_returns_empty(self):
        assert normalize_text(None) == ""

    def test_lowercase_conversion(self):
        assert normalize_text("ABC") == "abc"

    def test_removes_various_separators(self):
        result = normalize_text("张杰:演唱会—北京站")
        assert ":" not in result
        assert "—" not in result


# ---------------------------------------------------------------------------
# city_keyword
# ---------------------------------------------------------------------------

class TestCityKeyword:

    def test_strips_shi_suffix(self):
        assert city_keyword("北京市") == "北京"

    def test_strips_zizhizhou_suffix(self):
        assert city_keyword("西双版纳傣族自治州") == "西双版纳傣族"

    def test_strips_diqu_suffix(self):
        assert city_keyword("延边地区") == "延边"

    def test_strips_meng_suffix(self):
        assert city_keyword("兴安盟") == "兴安"

    def test_no_suffix_unchanged(self):
        assert city_keyword("北京") == "北京"

    def test_none_returns_empty(self):
        assert city_keyword(None) == ""

    def test_empty_returns_empty(self):
        assert city_keyword("") == ""


# ---------------------------------------------------------------------------
# build_search_keyword
# ---------------------------------------------------------------------------

class TestBuildSearchKeyword:

    def test_removes_city_bracket_prefix(self):
        title = "【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站"
        assert build_search_keyword(title) == "2026张杰未·LIVE—「开往1982」演唱会-北京站"

    def test_plain_title_unchanged(self):
        assert build_search_keyword("张杰演唱会") == "张杰演唱会"

    def test_display_name_used_as_fallback(self):
        result = build_search_keyword("", "张杰巡演2026")
        assert result == "张杰巡演2026"

    def test_both_empty_raises(self):
        with pytest.raises(ValueError):
            build_search_keyword("", "")

    def test_none_falls_back_to_display(self):
        result = build_search_keyword(None, "张杰")
        assert result == "张杰"


# ---------------------------------------------------------------------------
# DamaiItemDetail
# ---------------------------------------------------------------------------

class TestDamaiItemDetail:

    def _make_detail(self, **kwargs):
        defaults = dict(
            item_id="123456",
            item_name="张杰演唱会",
            item_name_display="【北京】张杰演唱会",
            city_name="北京市",
            venue_name="国家体育场",
            venue_city_name="北京市",
            show_time="2026-04-06",
            price_range="380-1280",
            raw_data={},
        )
        defaults.update(kwargs)
        return DamaiItemDetail(**defaults)

    def test_search_keyword_strips_city_bracket(self):
        detail = self._make_detail(item_name="【北京】张杰演唱会")
        assert "【北京】" not in detail.search_keyword

    def test_search_keyword_from_item_name(self):
        detail = self._make_detail(item_name="张杰演唱会")
        assert detail.search_keyword == "张杰演唱会"

    def test_city_keyword_strips_suffix(self):
        detail = self._make_detail(city_name="北京市")
        assert detail.city_keyword == "北京"

    def test_city_keyword_no_suffix(self):
        detail = self._make_detail(city_name="上海")
        assert detail.city_keyword == "上海"

    def test_raw_data_accessible(self):
        raw = {"item": {"itemName": "test"}}
        detail = self._make_detail(raw_data=raw)
        assert detail.raw_data == raw


# ---------------------------------------------------------------------------
# DamaiItemResolver
# ---------------------------------------------------------------------------

class TestDamaiItemResolver:

    def _make_resolver(self):
        return DamaiItemResolver(timeout=5)

    def test_referer_uses_provided_item_url(self):
        resolver = self._make_resolver()
        url = "https://m.damai.cn/shows/item.html?itemId=123"
        assert resolver._referer_for_item("123", url) == url

    def test_referer_constructed_from_item_id_when_no_url(self):
        resolver = self._make_resolver()
        referer = resolver._referer_for_item("123456", None)
        assert "itemId=123456" in referer
        assert "m.damai.cn" in referer

    def test_fetch_item_detail_success(self):
        """fetch_item_detail parses a well-formed API response."""
        resolver = self._make_resolver()

        success_payload = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "item": {
                    "itemId": "123456",
                    "itemName": "张杰演唱会",
                    "itemNameDisplay": "【北京】张杰演唱会",
                    "cityName": "北京市",
                    "showTime": "2026-04-06",
                },
                "venue": {
                    "venueName": "国家体育场",
                    "venueCityName": "北京市",
                },
                "price": {"range": "380-1280"},
            },
        }

        with patch.object(resolver, "_prime_token", return_value="fake_token"), \
             patch.object(resolver, "_request", return_value=json.dumps(success_payload)):
            detail = resolver.fetch_item_detail(item_id="123456")

        assert detail.item_id == "123456"
        assert detail.item_name == "张杰演唱会"
        assert detail.venue_name == "国家体育场"
        assert detail.price_range == "380-1280"

    def test_fetch_item_detail_api_failure_raises(self):
        """Non-SUCCESS ret raises DamaiItemResolveError."""
        resolver = self._make_resolver()

        failure_payload = {
            "ret": ["FAIL::接口调用失败"],
            "data": None,
        }

        with patch.object(resolver, "_prime_token", return_value="fake_token"), \
             patch.object(resolver, "_request", return_value=json.dumps(failure_payload)):
            with pytest.raises(DamaiItemResolveError, match="失败"):
                resolver.fetch_item_detail(item_id="123456")

    def test_fetch_item_detail_invalid_json_raises(self):
        """Non-JSON response raises DamaiItemResolveError."""
        resolver = self._make_resolver()

        with patch.object(resolver, "_prime_token", return_value="fake_token"), \
             patch.object(resolver, "_request", return_value="not json"):
            with pytest.raises(DamaiItemResolveError, match="不可解析"):
                resolver.fetch_item_detail(item_id="123456")

    def test_fetch_item_detail_missing_item_name_raises(self):
        """Missing item name raises DamaiItemResolveError."""
        resolver = self._make_resolver()

        payload = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "item": {},
                "venue": {},
                "price": {},
            },
        }

        with patch.object(resolver, "_prime_token", return_value="fake_token"), \
             patch.object(resolver, "_request", return_value=json.dumps(payload)):
            with pytest.raises(DamaiItemResolveError, match="演出名称"):
                resolver.fetch_item_detail(item_id="123456")

    def test_fetch_item_detail_accepts_item_url(self):
        """item_url is accepted and item_id is extracted from it."""
        resolver = self._make_resolver()

        payload = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "item": {"itemId": "1016133935724", "itemName": "演唱会"},
                "venue": {},
                "price": {},
            },
        }

        with patch.object(resolver, "_prime_token", return_value="tok"), \
             patch.object(resolver, "_request", return_value=json.dumps(payload)):
            detail = resolver.fetch_item_detail(
                item_url="https://m.damai.cn/shows/item.html?itemId=1016133935724"
            )
        assert detail.item_name == "演唱会"

    def test_prime_token_raises_when_no_cookie(self):
        """_prime_token raises DamaiItemResolveError when cookie is absent."""
        resolver = self._make_resolver()

        with patch.object(resolver, "_request", return_value="ok"):
            with pytest.raises(DamaiItemResolveError, match="_m_h5_tk"):
                resolver._prime_token("123", "https://referer.example", "{}")

    def test_request_reads_response_body(self):
        resolver = self._make_resolver()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        resolver.opener = Mock()
        resolver.opener.open.return_value = response

        body = resolver._request("https://example.com/api", "https://referer.example")

        assert body == '{"ok": true}'
        request = resolver.opener.open.call_args.args[0]
        assert request.full_url == "https://example.com/api"
        assert request.header_items()

    def test_prime_token_returns_cookie_prefix(self):
        resolver = self._make_resolver()
        cookie = Mock()
        cookie.name = "_m_h5_tk"
        cookie.value = "token_part_12345_suffix"
        resolver.cookie_jar = [cookie]

        with patch.object(resolver, "_request", return_value="ok"):
            assert resolver._prime_token("123", "https://referer.example", "{}") == "token"


# ---------------------------------------------------------------------------
# title_similarity（issue #51+#50：搜索/标题模糊匹配核心纯函数）
# ---------------------------------------------------------------------------


class TestTitleSimilarity:
    """校准值按 multiset 口径钉死（对抗审查修正 3b），用 pytest.approx 锁定。"""

    _KEYWORD = "嘉年华2026周杰伦演唱会"

    def test_title_similarity_substring_is_one(self):
        assert (
            title_similarity(self._KEYWORD, "龙拳·北京 嘉年华2026周杰伦演唱会")
            == 1.0
        )

    def test_title_similarity_reverse_substring_is_one(self):
        # 短标题是候选串的子串（详情页短标题场景）同样返回 1.0
        assert title_similarity("龙拳·北京 嘉年华", "龙拳北京嘉年华2026") == 1.0

    def test_title_similarity_word_order_variant(self):
        # issue #51：词序颠倒（校准值 0.870）
        similarity = title_similarity(
            self._KEYWORD, "周杰伦嘉年华2026演唱会（北京站）"
        )
        assert similarity >= 0.75
        assert similarity == pytest.approx(0.870, abs=1e-3)

    def test_title_similarity_official_word_order(self):
        # issue #51：官方全称词序变体（校准值 0.823）
        similarity = title_similarity(
            self._KEYWORD, "2026周杰伦嘉年华世界巡回演唱会-北京站"
        )
        assert similarity >= 0.75
        assert similarity == pytest.approx(0.823, abs=1e-3)

    def test_title_similarity_truncated_ellipsis(self):
        # issue #51：UI 省略号截断（multiset 校准值 0.854）
        similarity = title_similarity(
            self._KEYWORD, "龙拳·北京 嘉年华2026周杰伦演唱…"
        )
        assert similarity >= 0.75
        assert similarity == pytest.approx(0.854, abs=1e-3)

    def test_title_similarity_unrelated_low(self):
        # 防放松过度：无关演出必须显著低于模糊阈值
        assert title_similarity(self._KEYWORD, "开心麻花爆笑舞台剧") == 0.0
        assert title_similarity(self._KEYWORD, "张学友60+巡回演唱会北京站") < 0.45

    def test_title_similarity_issue50_tour_wording(self):
        # issue #50：「巡演」写法（校准值 0.482，须过相似度加分门槛 0.45）
        similarity = title_similarity(
            "凤凰传奇 演唱会", "凤凰传奇「吉祥如意」2026巡演·广州站"
        )
        assert similarity == pytest.approx(0.482, abs=1e-3)
        assert similarity >= 0.45

    def test_title_similarity_same_city_unrelated_below_bonus_gate(self):
        # 同城无关演出（校准值 0.314）不得触发相似度加分
        similarity = title_similarity("凤凰传奇 演唱会", "五月天2026巡回演唱会广州站")
        assert similarity == pytest.approx(0.314, abs=1e-3)
        assert similarity < 0.45

    def test_title_similarity_empty_or_non_string_returns_zero(self):
        assert title_similarity("", "张杰演唱会") == 0.0
        assert title_similarity("张杰演唱会", "") == 0.0
        assert title_similarity(None, "张杰演唱会") == 0.0
        assert title_similarity("张杰演唱会", MagicMock()) == 0.0


# ---------------------------------------------------------------------------
# find_conflicting_city（issue #50：错城市 veto 依据）
# ---------------------------------------------------------------------------


class TestFindConflictingCity:
    def test_returns_conflicting_city(self):
        assert find_conflicting_city("凤凰传奇巡回演唱会北京站", "广州") == "北京"

    def test_target_city_present_returns_none(self):
        assert find_conflicting_city("凤凰传奇巡回演唱会广州站", "广州") is None

    def test_multi_city_copy_with_target_not_vetoed(self):
        # 「北京·上海联演」类多城市文案：目标城市在文案中即不算冲突
        assert find_conflicting_city("北京·上海联演", "上海") is None

    def test_none_target_returns_none(self):
        assert find_conflicting_city("张杰演唱会北京站", None) is None

    def test_none_text_returns_none(self):
        assert find_conflicting_city(None, "广州") is None

    def test_city_suffix_stripped_from_target(self):
        # target 带「市」后缀时按 city_keyword 归一（北京市 → 北京）
        assert find_conflicting_city("张杰演唱会北京站", "北京市") is None

    def test_known_city_tokens_exported(self):
        # prompt_parser 迁移契约：城市全集从 item_resolver 导出
        assert "北京" in KNOWN_CITY_TOKENS
        assert "呼和浩特" in KNOWN_CITY_TOKENS
