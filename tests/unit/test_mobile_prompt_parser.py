"""Unit tests for mobile/prompt_parser.py"""

import pytest

from mobile.prompt_parser import (
    PromptIntent,
    _compact_keyword_phrase,
    _extract_digits,
    _is_low_signal_candidate,
    _parse_chinese_int,
    _parse_city,
    _parse_date,
    _parse_price_hints,
    _parse_price_range,
    _parse_quantity,
    _strip_schedule_fragments,
    choose_price_option,
    is_price_option_available,
    parse_prompt,
    score_price_option,
)


class TestParsePrompt:
    def test_parse_prompt_rejects_empty_input(self):
        with pytest.raises(ValueError, match="prompt 不能为空"):
            parse_prompt("   ")

    def test_parse_common_concert_prompt(self):
        intent = parse_prompt("帮我抢一张 4 月 6 号张杰的演唱会门票，内场")

        assert intent.quantity == 1
        assert intent.date == "04.06"
        assert intent.artist == "张杰"
        assert intent.search_keyword == "张杰 演唱会"
        assert intent.candidate_keywords[:2] == ["张杰 演唱会", "张杰"]
        assert intent.price_hint == "内场"
        assert intent.seat_hint == "内场"
        assert intent.attendee_names == []

    def test_parse_numeric_price_hint(self):
        intent = parse_prompt("帮我抢两张 4月6日 张杰演唱会 1280 元")

        assert intent.quantity == 2
        assert intent.date == "04.06"
        assert intent.price_hint == "1280元"
        assert intent.numeric_price_hint == 1280

    def test_parse_prompt_with_city_and_no_concert_word(self):
        intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票 899")

        assert intent.quantity == 1
        assert intent.date == "04.04"
        assert intent.city == "上海"
        assert intent.artist == "马思唯"
        assert intent.search_keyword == "马思唯 演唱会"
        assert intent.price_hint == "看台899元"
        assert intent.seat_hint == "看台"
        assert intent.numeric_price_hint == 899

    def test_parse_prompt_adds_notes_when_date_and_price_are_missing(self):
        intent = parse_prompt("帮我买一张马思唯上海演唱会")

        assert any("提示词中未识别到观演人姓名" in note for note in intent.notes)
        assert any("提示词中未识别到明确日期" in note for note in intent.notes)
        assert any("提示词中未识别到明确票档偏好" in note for note in intent.notes)

    def test_parse_prompt_supports_station_city_and_slash_date(self):
        intent = parse_prompt("帮我抢两张 成都站 4/18 顽童mj116 演唱会")

        assert intent.quantity == 2
        assert intent.city == "成都"
        assert intent.date == "04.18"
        assert intent.artist == "顽童mj116"
        # issue #45：斜杠日期「4/18」不再被误报为 18 元票价（显式契约固化）
        assert intent.numeric_price_hint is None
        assert intent.price_hint is None

    def test_parse_prompt_extracts_single_attendee_name(self):
        intent = parse_prompt(
            "帮张志涛抢一张 4 月 4 号余佳运的演唱会门票，内场，票价 1080 元"
        )

        assert intent.attendee_names == ["张志涛"]
        assert intent.artist == "余佳运"
        assert intent.search_keyword == "余佳运 演唱会"
        assert intent.date == "04.04"
        assert intent.price_hint == "内场1080元"

    def test_parse_prompt_extracts_multiple_attendee_names(self):
        intent = parse_prompt(
            "帮张志涛和李四抢两张 4 月 4 号余佳运的演唱会门票，内场，票价 1080 元"
        )

        assert intent.attendee_names == ["张志涛", "李四"]
        assert intent.quantity == 2
        assert intent.quantity_explicit is True

    def test_parse_prompt_infers_quantity_from_attendee_names_when_omitted(self):
        intent = parse_prompt(
            "帮张文、张志涛抢，6 月 6 号，陈慧娴的演唱会门票，上海站，内场，票价 1380 元"
        )

        assert intent.attendee_names == ["张文", "张志涛"]
        assert intent.quantity == 2
        assert intent.quantity_explicit is False
        assert not any("购票张数" in note for note in intent.notes)

    def test_parse_prompt_supports_artist_with_city_station_inside_phrase(self):
        intent = parse_prompt(
            "给张三和李四抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元"
        )

        assert intent.attendee_names == ["张三", "李四"]
        assert intent.quantity == 2
        assert intent.city == "北京"
        assert intent.artist == "张杰"
        assert intent.search_keyword == "张杰 演唱会"
        assert intent.price_hint == "内场1680元"

    def test_parse_prompt_supports_city_station_before_artist(self):
        intent = parse_prompt(
            "帮张文、张志涛抢，6 月 6 号，上海站陈慧娴的演唱会门票，内场，票价 1380 元"
        )

        assert intent.attendee_names == ["张文", "张志涛"]
        assert intent.quantity == 2
        assert intent.city == "上海"
        assert intent.artist == "陈慧娴"
        assert intent.search_keyword == "陈慧娴 演唱会"

    def test_parse_prompt_supports_dot_date_without_misreading_zhang_surname_as_quantity(
        self,
    ):
        intent = parse_prompt(
            "给张三和李四抢4.6 张杰的北京站演唱会内场门票，票价 1680 元"
        )

        assert intent.attendee_names == ["张三", "李四"]
        assert intent.quantity == 2
        assert intent.quantity_explicit is False
        assert intent.date == "04.06"
        assert intent.city == "北京"
        assert intent.artist == "张杰"
        assert intent.search_keyword == "张杰 演唱会"

    def test_parse_prompt_filters_low_signal_noisy_candidate_keywords(self):
        intent = parse_prompt(
            "给张志涛抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元"
        )

        assert intent.candidate_keywords[:2] == ["张杰 演唱会", "张杰"]
        assert all("价 元" not in keyword for keyword in intent.candidate_keywords)

    def test_parse_prompt_adds_note_when_attendee_count_mismatches_quantity(self):
        intent = parse_prompt(
            "帮张文和张志涛抢一张 4 月 4 号余佳运的演唱会门票，内场，票价 1080 元"
        )

        assert intent.attendee_names == ["张文", "张志涛"]
        assert intent.quantity == 1
        assert intent.quantity_explicit is True
        assert any("观演人" in note and "购票张数" in note for note in intent.notes)

    def test_parse_prompt_single_digit_day_price_regression(self):
        # issue #45 回归对照组：单位数日「4月4号」修复前后都必须解析出 1080
        intent = parse_prompt(
            "帮张志涛抢一张 4 月 4 号余佳运的演唱会门票，内场，票价 1080 元"
        )

        assert intent.numeric_price_hint == 1080
        assert intent.price_hint == "内场1080元"

    def test_full_title_candidate_keeps_city(self):
        # issue #51 回归：城市全局 replace 把标题前缀「龙拳·北京」挖成「龙拳·」。
        # 修复后新增「保留城市版」完整短语候选（index=2），城市名保留在候选内；
        # candidate_keywords[:2] 顺序锁定不破坏。
        # 注：#45 修复已把「2026」识别为票价 token 并进入 removable_tokens，
        # 因此本工作区现状下前两候选为 ['周杰伦 演唱会','周杰伦']（而非
        # 分析报告成文时的 '嘉年华2026周杰伦 演唱会' 系列）。
        intent = parse_prompt("给xxx抢6月28号 龙拳·北京 嘉年华2026周杰伦演唱会")

        assert intent.candidate_keywords[:2] == ["周杰伦 演唱会", "周杰伦"]
        assert "龙拳·北京" in intent.candidate_keywords[2]
        # 修复前的残缺形态「龙拳· 」不再是唯一保留（保留城市版必须存在）
        assert any("龙拳·北京" in kw for kw in intent.candidate_keywords)

    def test_full_title_candidate_keeps_city_issue50(self):
        # issue #50 同族：城市写在演出短语外时，保留城市版候选含「广州」
        intent = parse_prompt("帮张三抢广州的凤凰传奇演唱会门票")

        assert intent.candidate_keywords[:2] == ["凤凰传奇 演唱会", "凤凰传奇"]
        assert any("广州" in kw for kw in intent.candidate_keywords)

    def test_city_preserving_candidate_absent_without_city(self):
        # 无城市的提示词：候选列表行为与修复前一致（不新增候选）
        intent = parse_prompt("帮我抢一张 4 月 6 号张杰的演唱会门票，内场")

        assert intent.candidate_keywords[:2] == ["张杰 演唱会", "张杰"]

    def test_parse_prompt_e2e_issue45(self):
        # issue #45 端到端：两位数日份「30」不再吞并票价 1380
        result = parse_prompt(
            "帮张三抢一张 5月30号 陈奕迅的演唱会门票，内场，票价1380元"
        )

        assert result.numeric_price_hint == 1380
        assert result.price_hint == "内场1380元"
        assert result.date == "05.30"
        assert result.artist == "陈奕迅"
        assert result.search_keyword == "陈奕迅 演唱会"
        # 可观测性（对抗审查修正 #1）：剥离事件写入 notes（summary「提示:」段
        # 会打印）与 diagnostics 两个通道
        assert any(
            "price_parse.schedule_fragments_stripped" in note
            for note in result.notes
        )
        assert any(
            "price_parse.schedule_fragments_stripped" in item
            for item in result.diagnostics
        )


class TestPromptParserInternals:
    def test_parse_chinese_int_variants(self):
        assert _parse_chinese_int("") is None
        assert _parse_chinese_int("12") == 12
        assert _parse_chinese_int("十六") == 16
        assert _parse_chinese_int("二十") == 20
        assert _parse_chinese_int("二十三") == 23

    def test_extract_digits_returns_first_numeric_price(self):
        assert _extract_digits("看台 899元") == 899
        assert _extract_digits("无价格") is None

    def test_compact_keyword_phrase_removes_single_char_noise(self):
        assert _compact_keyword_phrase("给 张杰 演唱会 价 元") == "张杰 演唱会"

    def test_is_low_signal_candidate_detects_generic_terms(self):
        assert _is_low_signal_candidate("演唱会") is True
        assert _is_low_signal_candidate("张杰 演唱会") is False


class TestChoosePriceOption:
    def test_choose_price_option_matches_exact_numeric_hint(self):
        intent = parse_prompt("帮我抢一张 4 月 6 日张杰演唱会 1280 元")
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "1280元", "tag": "可预约"},
            {"index": 2, "text": "1680元", "tag": "可预约"},
        ]

        selected = choose_price_option(intent, options)

        assert selected["index"] == 1
        assert selected["text"] == "1280元"

    def test_choose_price_option_returns_none_when_seat_hint_is_ambiguous(self):
        intent = parse_prompt("帮我抢一张 4 月 6 日张杰演唱会 内场")
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "1280元", "tag": "可预约"},
            {"index": 2, "text": "1680元", "tag": "可预约"},
        ]

        selected = choose_price_option(intent, options)

        assert selected is None

    def test_choose_price_option_after_issue45_fix(self):
        # issue #45 下游修复验证：票价 1380 精确命中（+100），
        # 而非修复前 seat 文字掩盖或最近邻全拒返回 None
        intent = parse_prompt(
            "帮张三抢一张 5月30号 陈奕迅的演唱会门票，内场，票价1380元"
        )
        options = [
            {"index": 0, "text": "580元", "tag": "可预约"},
            {"index": 1, "text": "1380元", "tag": "可预约"},
        ]

        selected = choose_price_option(intent, options)

        assert selected is not None
        assert selected["index"] == 1


# ---------------------------------------------------------------------------
# _parse_chinese_int
# ---------------------------------------------------------------------------


class TestParseChineseInt:
    def test_zero(self):
        assert _parse_chinese_int("零") == 0

    def test_one(self):
        assert _parse_chinese_int("一") == 1

    def test_two_liang(self):
        assert _parse_chinese_int("两") == 2

    def test_ten(self):
        assert _parse_chinese_int("十") == 10

    def test_ten_plus_five(self):
        assert _parse_chinese_int("十五") == 15

    def test_five_tens(self):
        assert _parse_chinese_int("五十") == 50

    def test_five_tens_five(self):
        assert _parse_chinese_int("五十五") == 55

    def test_arabic_digit(self):
        assert _parse_chinese_int("3") == 3

    def test_empty_string_returns_none(self):
        assert _parse_chinese_int("") is None

    def test_invalid_token_returns_none(self):
        assert _parse_chinese_int("abc") is None

    def test_whitespace_returns_none(self):
        assert _parse_chinese_int("   ") is None


# ---------------------------------------------------------------------------
# _parse_quantity
# ---------------------------------------------------------------------------


class TestParseQuantity:
    def test_arabic_digit(self):
        assert _parse_quantity("买3张") == 3

    def test_chinese_two(self):
        assert _parse_quantity("两张票") == 2

    def test_chinese_ten(self):
        assert _parse_quantity("十张门票") == 10

    def test_chinese_three(self):
        assert _parse_quantity("三张") == 3

    def test_embedded_in_sentence(self):
        assert _parse_quantity("帮我买5张门票") == 5

    def test_no_quantity_defaults_to_one(self):
        assert _parse_quantity("帮我买票") == 1

    def test_just_concert_name_defaults_to_one(self):
        assert _parse_quantity("张杰演唱会") == 1

    def test_yi_zhang(self):
        assert _parse_quantity("要一张") == 1

    def test_two_digit(self):
        assert _parse_quantity("抢12张") == 12


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_month_day_ri(self):
        assert _parse_date("3月15日演唱会") == "03.15"

    def test_month_day_hao(self):
        assert _parse_date("3月15号") == "03.15"

    def test_december_first(self):
        assert _parse_date("12月1日") == "12.01"

    def test_dot_separator(self):
        assert _parse_date("3.15") == "03.15"

    def test_slash_separator(self):
        assert _parse_date("12/1") == "12.01"

    def test_dash_separator(self):
        assert _parse_date("12-1演唱会") == "12.01"

    def test_no_date_returns_none(self):
        assert _parse_date("张杰演唱会") is None

    def test_invalid_month_returns_none(self):
        assert _parse_date("13月5日") is None

    def test_invalid_day_returns_none(self):
        assert _parse_date("3月32日") is None

    def test_embedded_date(self):
        assert _parse_date("帮我抢 4 月 6 日张杰的演唱会") == "04.06"


# ---------------------------------------------------------------------------
# _parse_city
# ---------------------------------------------------------------------------


class TestParseCity:
    def test_known_city_exact(self):
        assert _parse_city("北京演唱会") == "北京"

    def test_known_city_shanghai(self):
        assert _parse_city("上海演唱会") == "上海"

    def test_zhan_pattern(self):
        assert _parse_city("鸟巢站演唱会") == "鸟巢"

    def test_known_city_with_zhan_suffix(self):
        assert _parse_city("南京站") == "南京"

    def test_unknown_city_returns_none(self):
        assert _parse_city("纽约演唱会") is None

    def test_empty_string_returns_none(self):
        assert _parse_city("") is None

    def test_chengdu(self):
        assert _parse_city("成都跨年演唱会") == "成都"

    def test_no_city_in_prompt(self):
        assert _parse_city("张杰演唱会门票") is None


# ---------------------------------------------------------------------------
# _parse_price_hints
# ---------------------------------------------------------------------------


class TestParsePriceHints:
    def test_seat_and_price(self):
        hint, seat, numeric = _parse_price_hints("VIP500元")
        assert seat == "VIP"
        assert numeric == 500
        assert "VIP" in hint and "500" in hint

    def test_seat_and_price_neichang(self):
        hint, seat, numeric = _parse_price_hints("内场280")
        assert seat == "内场"
        assert numeric == 280
        assert hint == "内场280元"

    def test_seat_only(self):
        hint, seat, numeric = _parse_price_hints("看台")
        assert seat == "看台"
        assert numeric is None
        assert hint == "看台"

    def test_price_only(self):
        hint, seat, numeric = _parse_price_hints("1280元")
        assert hint == "1280元"
        assert seat is None
        assert numeric == 1280

    def test_no_hints(self):
        hint, seat, numeric = _parse_price_hints("张杰演唱会")
        assert hint is None
        assert seat is None
        assert numeric is None

    def test_front_row_vip(self):
        hint, seat, numeric = _parse_price_hints("前排VIP1680")
        assert seat == "VIP"
        assert numeric == 1680

    # -- issue #45：日期/时间/张数数字不再被误判为票价 ----------------------

    def test_parse_price_hints_ignores_two_digit_day_before_price(self):
        # issue #45 场景 1：修复前「5月30号」的日份 30 抢先命中，误报 内场30元
        hint, seat, numeric = _parse_price_hints(
            "帮张三抢一张 5月30号 陈奕迅的演唱会门票，内场，票价1380元"
        )
        assert hint == "内场1380元"
        assert seat == "内场"
        assert numeric == 1380

    def test_parse_price_hints_ignores_day_23_with_bare_price(self):
        # issue #45 场景 2（980→23 截图案例）：修复前返回 ('23元', None, 23)
        hint, seat, numeric = _parse_price_hints(
            "帮李四抢一张 5月23号 周杰伦的演唱会门票 980元"
        )
        assert hint == "980元"
        assert seat is None
        assert numeric == 980

    def test_parse_price_hints_ignores_time_tokens(self):
        # 开抢时间「23点」「23:00」同为 issue #45 根因形态，修复前均误报 23
        _, _, numeric_dian = _parse_price_hints(
            "5月8号 23点开抢 周杰伦演唱会 980元"
        )
        _, _, numeric_colon = _parse_price_hints(
            "5月8号 23:00开抢 周杰伦演唱会 980元"
        )
        assert numeric_dian == 980
        assert numeric_colon == 980

    def test_parse_price_hints_ignores_quantity_digits(self):
        # 张数「12张」修复前被误吃为 12 元
        _, _, numeric = _parse_price_hints("帮我抢12张 5月30号 演唱会 580元")
        assert numeric == 580

    def test_parse_price_hints_ignores_year_token(self):
        _, _, numeric = _parse_price_hints("2026年5月30号 张杰演唱会 票价1380元")
        assert numeric == 1380

    def test_parse_price_hints_ignores_slash_date_and_alnum_artist(self):
        # 斜杠日期「4/18」不误报 18 元；艺人名「mj116」里的 116 不被宽松层误吃
        hint, seat, numeric = _parse_price_hints(
            "帮我抢两张 成都站 4/18 顽童mj116 演唱会"
        )
        assert hint is None
        assert seat is None
        assert numeric is None

    def test_parse_price_hints_context_anchor_beats_leading_bare_number(self):
        # Tier1 上下文锚定（票价…）优先于位置更靠前的 Tier2 裸数字
        _, _, numeric = _parse_price_hints("编号45 张杰演唱会 票价1380元")
        assert numeric == 1380

    def test_parse_price_hints_keeps_seat_adjacent_digits(self):
        # 回归保护：seat token 预剥离不破坏「seat 紧贴数字」的既有行为
        hint, seat, numeric = _parse_price_hints("前排VIP1680")
        assert (hint, seat, numeric) == ("VIP1680元", "VIP", 1680)

        hint, seat, numeric = _parse_price_hints("内场280")
        assert (hint, seat, numeric) == ("内场280元", "内场", 280)

        hint, seat, numeric = _parse_price_hints("看台票 899")
        assert seat == "看台"
        assert numeric == 899

    def test_parse_price_hints_no_backtrack_truncation(self):
        # 对抗审查实锤缺陷回归：无 (?!\d) 时「1380号」会贪婪回溯成「138」
        # 骗过负向 lookahead；修复后三位以上数字紧跟单位必须整体排除
        hint, seat, numeric = _parse_price_hints("1380号 张杰演唱会")
        assert hint is None
        assert seat is None
        assert numeric is None

        # 两位数紧跟单位（30号 / 25分）同样排除
        assert _parse_price_hints("30号 张杰演唱会")[2] is None
        assert _parse_price_hints("开抢25分 张杰演唱会")[2] is None

    def test_parse_price_hints_bare_year_with_anchor(self):
        # 裸年份（无「年」字）依赖 Tier1 锚定词兜底，不被误报为票价
        _, _, numeric = _parse_price_hints("张杰2024巡演 票价680元")
        assert numeric == 680

    def test_parse_price_hints_currency_symbol_anchor(self):
        # Tier1 货币符号锚定：¥/￥ 后的数字优先于日期残余
        _, _, numeric = _parse_price_hints("5月30号 张杰演唱会 ¥1380")
        assert numeric == 1380


# ---------------------------------------------------------------------------
# _parse_price_range（issue #45 同族缺陷：短横线日期 vs 价格区间）
# ---------------------------------------------------------------------------


class TestParsePriceRange:
    def test_parse_price_range_survives_date_prefix(self):
        assert _parse_price_range("5月30号 500-800元") == (500, 800)

    def test_parse_price_range_not_fooled_by_dash_date(self):
        # 修复前「12-15」短横线日期被误判为 (12, 15) 价格区间
        assert _parse_price_range("12-15 张杰演唱会") == (None, None)

    def test_parse_price_range_keeps_two_digit_range(self):
        # 50 不是合法月份，两位数价格区间不被日期剥离误伤
        assert _parse_price_range("50-80元") == (50, 80)


# ---------------------------------------------------------------------------
# _strip_schedule_fragments
# ---------------------------------------------------------------------------


class TestStripScheduleFragments:
    def test_strip_schedule_fragments_preserves_range_interior_digits(self):
        # 数字边界 lookaround 防误吃：区间内部的「80-13」不能被当短日期剥掉
        assert "980-1380元" in _strip_schedule_fragments("980-1380元")

    def test_strip_schedule_fragments_removes_schedule_tokens(self):
        stripped = _strip_schedule_fragments(
            "2026年5月30号 2026-04-06 23:00 19点30分 抢12张 4/18"
        )
        assert not any(ch.isdigit() for ch in stripped)

    def test_strip_schedule_fragments_keeps_invalid_month_pair(self):
        # 50-80 不是合法「月-日」，必须原样保留
        assert "50-80" in _strip_schedule_fragments("50-80元")


# ---------------------------------------------------------------------------
# is_price_option_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_available_tag(self):
        assert is_price_option_available({"tag": "可预约"}) is True

    def test_empty_tag(self):
        assert is_price_option_available({"tag": ""}) is True

    def test_missing_tag_key(self):
        assert is_price_option_available({}) is True

    def test_no_ticket(self):
        assert is_price_option_available({"tag": "无票"}) is False

    def test_sold_out(self):
        assert is_price_option_available({"tag": "售罄"}) is False

    def test_already_sold_out(self):
        assert is_price_option_available({"tag": "已售罄"}) is False

    def test_not_selectable(self):
        assert is_price_option_available({"tag": "不可选"}) is False

    def test_temp_unavailable(self):
        assert is_price_option_available({"tag": "暂不可售"}) is False


# ---------------------------------------------------------------------------
# score_price_option
# ---------------------------------------------------------------------------


class TestScorePriceOption:
    def _intent(self, price_hint=None, seat_hint=None, numeric_price=None):
        return PromptIntent(
            raw_prompt="test",
            price_hint=price_hint,
            seat_hint=seat_hint,
            numeric_price_hint=numeric_price,
        )

    def test_no_hints_available_score_ten(self):
        intent = self._intent()
        score = score_price_option(intent, {"text": "580元", "tag": "可预约"})
        assert score == 10

    def test_unavailable_penalized(self):
        intent = self._intent()
        score = score_price_option(intent, {"text": "580元", "tag": "售罄"})
        assert score == -1000

    def test_price_hint_match(self):
        intent = self._intent(price_hint="内场")
        score = score_price_option(intent, {"text": "内场580元", "tag": "可预约"})
        assert score >= 120

    def test_seat_hint_match(self):
        intent = self._intent(seat_hint="内场")
        score = score_price_option(intent, {"text": "内场580元", "tag": ""})
        assert score >= 80

    def test_numeric_exact_match(self):
        intent = self._intent(numeric_price=1280)
        score = score_price_option(intent, {"text": "1280元", "tag": ""})
        # fix-plan #26 Step 3：精确命中分数从 150 降为 100（区分 +120 文字 hint 加分）
        assert score >= 100

    def test_numeric_mismatch_penalty(self):
        intent = self._intent(numeric_price=1280)
        score_near = score_price_option(intent, {"text": "1380元", "tag": ""})
        score_far = score_price_option(intent, {"text": "580元", "tag": ""})
        assert score_near > score_far

    def test_presale_tag_bonus(self):
        intent = self._intent()
        score = score_price_option(intent, {"text": "580元", "tag": "预售"})
        assert score == 10

    def test_ke_xuan_tag_bonus(self):
        intent = self._intent()
        score = score_price_option(intent, {"text": "580元", "tag": "可选"})
        assert score == 10


# ---------------------------------------------------------------------------
# choose_price_option (extended)
# ---------------------------------------------------------------------------


class TestChoosePriceOptionExtended:
    def _intent(self, price_hint=None, seat_hint=None, numeric_price=None):
        return PromptIntent(
            raw_prompt="test",
            price_hint=price_hint,
            seat_hint=seat_hint,
            numeric_price_hint=numeric_price,
        )

    def test_empty_list_returns_none(self):
        assert choose_price_option(self._intent(), []) is None

    def test_all_unavailable_with_price_hint_returns_none(self):
        intent = self._intent(price_hint="内场")
        options = [
            {"text": "内场580元", "tag": "售罄"},
            {"text": "580元", "tag": "无票"},
        ]
        assert choose_price_option(intent, options) is None

    def test_no_price_hint_all_unavailable_returns_none(self):
        intent = self._intent()
        assert choose_price_option(intent, [{"text": "580元", "tag": "售罄"}]) is None

    def test_score_field_added_to_result(self):
        intent = self._intent(numeric_price=1280)
        result = choose_price_option(
            intent, [{"index": 0, "text": "1280元", "tag": "可预约"}]
        )
        assert result is not None and "score" in result

    def test_single_available_option_returned(self):
        intent = self._intent()
        result = choose_price_option(
            intent, [{"index": 0, "text": "580元", "tag": "可选"}]
        )
        assert result is not None and result["index"] == 0

    def test_high_score_option_wins(self):
        intent = self._intent(numeric_price=580)
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "580元", "tag": "可预约"},
            {"index": 2, "text": "980元", "tag": "可预约"},
        ]
        assert choose_price_option(intent, options)["index"] == 1

    def test_choose_price_option_nearest_neighbor_within_50_percent(self):
        # fix-plan #26 Step 3：用户输入 899 元，候选 880 元应作为最近邻命中
        intent = self._intent(numeric_price=899)
        options = [
            {"index": 0, "text": "880元", "tag": "可预约"},
            {"index": 1, "text": "1880元", "tag": "可预约"},
        ]
        result = choose_price_option(intent, options)
        assert result is not None
        assert result["index"] == 0

    def test_choose_price_option_within_10_percent_tolerance(self):
        # ±10% 容忍：用户输入 1280 元，候选 1380 元（差 7.8%）也应被接受
        intent = self._intent(numeric_price=1280)
        options = [
            {"index": 0, "text": "1380元", "tag": "可预约"},
            {"index": 1, "text": "2580元", "tag": "可预约"},
        ]
        result = choose_price_option(intent, options)
        assert result is not None
        assert result["index"] == 0
        # 容忍区间分数应至少 60，落在 60-80 区间内
        assert result["score"] >= 60

    def test_choose_price_option_range_hit(self):
        # 区间命中：500-800 元区间内的 680 元应得 +50
        intent = PromptIntent(
            raw_prompt="test",
            numeric_price_min=500,
            numeric_price_max=800,
        )
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "680元", "tag": "可预约"},
            {"index": 2, "text": "1280元", "tag": "可预约"},
        ]
        result = choose_price_option(intent, options)
        assert result is not None
        assert result["index"] == 1

    def test_choose_price_option_rejects_extreme_outlier(self):
        # 距离 > 50% 时拒绝最近邻：999 元 hint vs 380 元 (差距 62%)
        intent = self._intent(numeric_price=999)
        options = [{"index": 0, "text": "380元", "tag": "可预约"}]
        assert choose_price_option(intent, options) is None


# ---------------------------------------------------------------------------
# parse_prompt — validation and notes
# ---------------------------------------------------------------------------


class TestParsePromptValidation:
    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_prompt("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            parse_prompt("   ")

    def test_none_raises(self):
        with pytest.raises((ValueError, AttributeError)):
            parse_prompt(None)

    def test_no_date_adds_note(self):
        intent = parse_prompt("帮我抢张杰演唱会")
        assert any("日期" in note for note in intent.notes)

    def test_no_price_adds_note(self):
        intent = parse_prompt("帮我抢张杰演唱会")
        assert any("票档" in note for note in intent.notes)


class TestParsePromptNotes:
    def test_with_date_and_price_no_extra_notes(self):
        intent = parse_prompt("帮我抢一张 4月6日 张杰演唱会 1280元")
        assert not any("日期" in n for n in intent.notes)
        assert not any("票档" in n for n in intent.notes)

    def test_without_date_note_added(self):
        intent = parse_prompt("帮我抢张杰演唱会 1280元")
        assert any("日期" in n for n in intent.notes)

    def test_without_price_note_added(self):
        intent = parse_prompt("帮我抢 4月6日 张杰演唱会")
        assert any("票档" in n for n in intent.notes)


# ---------------------------------------------------------------------------
# Edge cases: keyword extraction
# ---------------------------------------------------------------------------


class TestKeywordExtractionEdgeCases:
    def test_only_stopwords_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_prompt("帮我买票")

    def test_artist_without_concert_keyword_uses_tail_fallback(self):
        intent = parse_prompt("张三北京3月15日")
        assert intent.search_keyword is not None

    def test_artist_with_live_keyword(self):
        intent = parse_prompt("五月天 live 4月6日")
        assert intent.artist is not None

    def test_candidate_keywords_no_duplicates(self):
        intent = parse_prompt("张杰演唱会 3月15日")
        seen = set()
        for kw in intent.candidate_keywords:
            assert kw not in seen
            seen.add(kw)

    def test_numeric_price_mismatch_decreases_score(self):
        intent = PromptIntent(raw_prompt="test", numeric_price_hint=1280)
        score_close = score_price_option(intent, {"text": "1380元", "tag": ""})
        score_far = score_price_option(intent, {"text": "280元", "tag": ""})
        assert score_close > score_far

    def test_score_price_option_penalizes_unavailable_tags(self):
        intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票 899")
        option = {"index": 5, "text": "看台 899元", "tag": "售罄"}

        assert score_price_option(intent, option) < 0

    def test_choose_price_option_returns_none_when_best_score_is_too_low(self):
        intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的 999 元票")
        options = [{"index": 0, "text": "380元", "tag": "可选"}]

        assert choose_price_option(intent, options) is None

    def test_choose_price_option_returns_none_for_unavailable_default_choice(self):
        intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的票")
        options = [{"index": 0, "text": "看台 899元", "tag": "售罄"}]

        assert choose_price_option(intent, options) is None

    def test_choose_price_option_returns_none_for_empty_options(self):
        intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的票")
        assert choose_price_option(intent, []) is None
