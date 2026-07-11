# -*- coding: UTF-8 -*-
"""Tests for the BuyButtonGuard safety module."""

import time
from unittest.mock import Mock, PropertyMock, patch

import pytest

from mobile.buy_button_guard import BuyButtonGuard, SAFE_TEXTS, BLOCKED_TEXTS


@pytest.fixture
def mock_device():
    """Create a mock uiautomator2 device."""
    return Mock()


@pytest.fixture
def guard(mock_device):
    """Create a BuyButtonGuard with a mock device."""
    return BuyButtonGuard(mock_device)


# ── is_safe_to_click ──


class TestIsSafeToClick:
    """Tests for is_safe_to_click method."""

    @pytest.mark.parametrize("text", ["立即购买", "立即购票", "立即抢票", "选座购买"])
    def test_safe_texts_return_true(self, guard, text):
        assert guard.is_safe_to_click(text) is True

    def test_all_safe_texts_accepted(self, guard):
        for text in SAFE_TEXTS:
            assert guard.is_safe_to_click(text) is True, f"Expected True for '{text}'"

    @pytest.mark.parametrize("text", ["预约抢票", "预约", "即将开抢", "待开售"])
    def test_blocked_texts_return_false(self, guard, text):
        assert guard.is_safe_to_click(text) is False

    def test_all_blocked_texts_rejected(self, guard):
        for text in BLOCKED_TEXTS:
            assert guard.is_safe_to_click(text) is False, f"Expected False for '{text}'"

    def test_empty_string_returns_false(self, guard):
        assert guard.is_safe_to_click("") is False

    def test_none_returns_false(self, guard):
        assert guard.is_safe_to_click(None) is False

    def test_unknown_text_returns_false(self, guard):
        assert guard.is_safe_to_click("提交抢票预约") is False

    def test_critical_reservation_blocked(self, guard):
        """The MOST important safety property: 预约抢票 must be blocked."""
        assert guard.is_safe_to_click("预约抢票") is False

    def test_critical_purchase_allowed(self, guard):
        """The MOST important safety property: 立即购票 must be allowed."""
        assert guard.is_safe_to_click("立即购票") is True
        assert guard.is_safe_to_click("立即抢票") is True


# ── get_current_text ──


class TestGetCurrentText:
    def test_returns_text_when_button_found(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = True
        mock_el.get_text.return_value = "立即购买"
        mock_device.return_value = mock_el
        assert guard.get_current_text() == "立即购买"

    def test_returns_none_when_button_not_found(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = False
        mock_device.return_value = mock_el
        assert guard.get_current_text() is None

    def test_returns_none_when_exception(self, guard, mock_device):
        mock_device.side_effect = Exception("device error")
        assert guard.get_current_text() is None


# ── wait_until_safe ──


class TestWaitUntilSafe:
    def test_immediately_safe(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = True
        mock_el.get_text.return_value = "立即购买"
        mock_device.return_value = mock_el

        # Provide enough time.time() values for the single check
        with patch("mobile.buy_button_guard.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.0]
            mock_time.sleep = Mock()
            assert guard.wait_until_safe(timeout_s=1.0, poll_ms=50) is True

    def test_transitions_from_blocked_to_safe(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = True
        # 3 blocked polls, then safe
        mock_el.get_text.side_effect = ["预约抢票", "预约抢票", "预约抢票", "立即购买"]
        mock_device.return_value = mock_el

        with patch("mobile.buy_button_guard.time") as mock_time:
            # time() calls: deadline calc, then check after each poll
            mock_time.time.side_effect = [0.0, 0.1, 0.2, 0.3, 0.4]
            mock_time.sleep = Mock()
            assert guard.wait_until_safe(timeout_s=10.0, poll_ms=50) is True

    def test_timeout_with_blocked_text(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = True
        mock_el.get_text.return_value = "预约抢票"
        mock_device.return_value = mock_el

        with patch("mobile.buy_button_guard.time") as mock_time:
            # First call sets deadline, subsequent calls exceed it
            mock_time.time.side_effect = [0.0, 11.0]
            mock_time.sleep = Mock()
            assert guard.wait_until_safe(timeout_s=10.0, poll_ms=50) is False

    def test_timeout_button_not_found(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = False
        mock_device.return_value = mock_el

        with patch("mobile.buy_button_guard.time") as mock_time:
            mock_time.time.side_effect = [0.0, 11.0]
            mock_time.sleep = Mock()
            assert guard.wait_until_safe(timeout_s=10.0, poll_ms=50) is False


# ── multi-ID candidates + coordinate fallback (issue #41) ──


LEGACY_ID = "cn.damai:id/btn_buy_view"
CONTAINER_ID = "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"


def _make_element(exists=True, text=None):
    """构造单个元素桩（exists 用真实 bool，避免 Mock 恒 truthy 陷阱）。"""
    el = Mock()
    el.exists = exists
    el.get_text.return_value = text
    return el


def _make_dispatch_device(mapping):
    """构造按 resourceId 分派元素桩的假设备；未注册的 ID 返回不存在元素。"""
    device = Mock()

    def _selector(**kwargs):
        el = mapping.get(kwargs.get("resourceId"))
        if el is None:
            absent = Mock()
            absent.exists = False
            return absent
        return el

    device.side_effect = _selector
    return device


class _ExplodingInfoElement:
    """exists 为真但读取 .info 抛异常的元素桩。"""

    exists = True

    def get_text(self):
        return ""

    @property
    def info(self):
        raise RuntimeError("boom")


class TestMultiIdCandidates:
    """issue #41：大麦 ≥9.0.2x 移除 btn_buy_view 后的多 resource-id 候选。"""

    def test_find_buy_button_falls_back_to_container_id(self):
        container = _make_element(exists=True, text="")
        guard = BuyButtonGuard(_make_dispatch_device({CONTAINER_ID: container}))
        el = guard._find_buy_button()
        assert el is container
        assert guard._last_matched_resource_id == CONTAINER_ID

    def test_find_buy_button_prefers_legacy_id(self):
        """v8.x 回归：两个候选同时存在时旧 ID 优先。"""
        legacy = _make_element(exists=True, text="立即购票")
        container = _make_element(exists=True, text="")
        guard = BuyButtonGuard(
            _make_dispatch_device({LEGACY_ID: legacy, CONTAINER_ID: container})
        )
        el = guard._find_buy_button()
        assert el is legacy
        assert guard._last_matched_resource_id == LEGACY_ID

    def test_find_buy_button_none_when_all_absent(self):
        guard = BuyButtonGuard(_make_dispatch_device({}))
        assert guard._find_buy_button() is None
        assert guard._last_matched_resource_id is None

    def test_get_current_text_none_for_canvas_container(self):
        """Canvas 容器无文案：get_current_text 维持原语义返回 None。"""
        container = _make_element(exists=True, text="")
        guard = BuyButtonGuard(_make_dispatch_device({CONTAINER_ID: container}))
        assert guard.get_current_text() is None

    def test_wait_until_safe_true_on_v8_text(self):
        legacy = _make_element(exists=True, text="立即购票")
        guard = BuyButtonGuard(_make_dispatch_device({LEGACY_ID: legacy}))
        with patch("mobile.buy_button_guard.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.0]
            mock_time.sleep = Mock()
            assert guard.wait_until_safe(timeout_s=1.0, poll_ms=50) is True

    def test_wait_until_safe_blocks_reservation_on_container(self):
        """安全属性回归：容器读到「预约抢票」时 wait_until_safe 仍拒绝。"""
        container = _make_element(exists=True, text="预约抢票")
        guard = BuyButtonGuard(_make_dispatch_device({CONTAINER_ID: container}))
        with patch("mobile.buy_button_guard.time") as mock_time:
            mock_time.time.side_effect = [0.0, 11.0]
            mock_time.sleep = Mock()
            assert guard.wait_until_safe(timeout_s=10.0, poll_ms=50) is False


class TestGetCtaCenterCoords:
    """issue #41：Canvas 自绘 CTA 的坐标兜底定位。"""

    def test_center_coords_from_container_bounds(self):
        container = _make_element(exists=True, text="")
        container.info = {
            "bounds": {"left": 341, "top": 2544, "right": 1248, "bottom": 2691}
        }
        guard = BuyButtonGuard(_make_dispatch_device({CONTAINER_ID: container}))
        assert guard.get_cta_center_coords() == (794, 2617)
        assert guard._last_matched_resource_id == CONTAINER_ID

    def test_none_when_no_candidate_exists(self):
        guard = BuyButtonGuard(_make_dispatch_device({}))
        assert guard.get_cta_center_coords() is None

    def test_none_when_info_raises(self):
        guard = BuyButtonGuard(
            _make_dispatch_device({CONTAINER_ID: _ExplodingInfoElement()})
        )
        assert guard.get_cta_center_coords() is None

    def test_none_when_device_raises(self):
        device = Mock(side_effect=Exception("device error"))
        guard = BuyButtonGuard(device)
        assert guard.get_cta_center_coords() is None
