# -*- coding: UTF-8 -*-
"""Local status dashboard for HalloTickets mobile automation.

Provides a tiny read-only web UI that shows:
- device connection and current focused app/activity
- current config target
- latest run summary
- recent automation logs
- a live phone screenshot via adb screencap
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import uiautomator2 as u2
except Exception:  # pragma: no cover - dashboard should degrade gracefully
    u2 = None

try:
    import adbutils
except Exception:  # pragma: no cover - dashboard should degrade gracefully
    adbutils = None

from mobile.config import (
    Config,
    CONFIG_OVERRIDE_ENV_VAR,
    ConfigError,
    filter_known_config_keys,
    load_config_dict,
    update_config_values,
    update_runtime_mode,
)
from mobile.page_probe import PageProbe


ROOT_DIR = Path(__file__).resolve().parent.parent
MOBILE_DIR = ROOT_DIR / "mobile"
DASHBOARD_DIR = MOBILE_DIR / "dashboard"
LOG_PATH = MOBILE_DIR / "hatickets_mobile.log"
RUN_SUMMARY_PATH = MOBILE_DIR / "tmp" / "run_summary.json"
DEFAULT_CONFIG_PATH = MOBILE_DIR / "config.jsonc"
DESKTOP_INSTALL_SCRIPT = MOBILE_DIR / "scripts" / "install_hallotickets_desktop.ps1"
DEFAULT_PORT = 8765
TARGET_EDITABLE_FIELDS = {
    "keyword",
    "target_title",
    "target_venue",
    "users",
    "city",
    "date",
    "price",
    "price_index",
    "price_strategy",
    "sell_start_time",
    "auto_navigate",
}


class RunController:
    """Own the one local automation process started from the dashboard."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._started_at: str | None = None
        self._last_exit_code: int | None = None
        self._last_error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            running = process is not None and process.poll() is None
            if process is not None and not running and self._last_exit_code is None:
                self._last_exit_code = process.returncode
            return {
                "running": running,
                "started_at": self._started_at,
                "pid": process.pid if running else None,
                "last_exit_code": self._last_exit_code,
                "last_error": self._last_error,
            }

    def start_formal_submit(self, status: dict[str, Any]) -> tuple[bool, str]:
        """Start one confirmed formal run after the dashboard preflight succeeds."""
        device = status.get("selected_device") or {}
        if device.get("state") != "device":
            return False, "目标手机未处于可用连接状态，未启动。"
        if not PREFLIGHT_CONTROLLER.snapshot().get("ready"):
            return False, "请先完成预热检查，确认手机已停在目标详情页或票档页。"

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return False, "已有正式提交任务正在运行，请勿重复启动。"
            try:
                # The only route that enables a real submission is the dashboard's
                # explicit confirmation action. Preflight itself remains read-only.
                update_runtime_mode(False, True, str(DEFAULT_CONFIG_PATH))
            except (ConfigError, OSError, ValueError) as exc:
                self._last_error = str(exc)
                return False, f"无法切换到正式提交模式: {exc}"
            environment = os.environ.copy()
            environment[CONFIG_OVERRIDE_ENV_VAR] = str(DEFAULT_CONFIG_PATH)
            environment["HATICKETS_RESULT_JSON"] = str(RUN_SUMMARY_PATH)
            try:
                self._process = subprocess.Popen(
                    [sys.executable, "-m", "mobile.damai_app", "--result-json", str(RUN_SUMMARY_PATH)],
                    cwd=str(ROOT_DIR),
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                self._last_error = str(exc)
                return False, f"无法启动正式提交任务: {exc}"
            self._started_at = datetime.now().astimezone().isoformat()
            self._last_exit_code = None
            self._last_error = None
            return True, "正式提交任务已启动，正在按当前手机页面和配置执行。"

    def stop(self) -> tuple[bool, str]:
        """Stop the dashboard-owned task without touching the phone's order state."""
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return False, "当前没有由看板启动的运行任务。"
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
            except OSError as exc:
                self._last_error = str(exc)
                return False, f"停止任务失败: {exc}"
            self._last_exit_code = process.returncode
            return True, "电脑端任务已停止。若已进入支付或已占单，请直接在手机 App 检查订单。"


RUN_CONTROLLER = RunController()


class EnvironmentController:
    """Inspect and safely prepare local prerequisites without touching phone settings."""

    CACHE_TTL_S = 12.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._expires_at = 0.0
        self._result: dict[str, Any] = {
            "checked_at": None,
            "items": [],
            "setup": {"running": False, "message": "尚未执行一键配置"},
        }

    def snapshot(self, *, force: bool = False) -> dict[str, Any]:
        runtime = RUN_CONTROLLER.snapshot()
        if runtime["running"]:
            with self._lock:
                result = dict(self._result)
                result["task_locked"] = True
                result["message"] = "正式任务运行中，已暂停环境检测以独占手机连接。"
                return result
        now = time.monotonic()
        with self._lock:
            if not force and self._result["items"] and now < self._expires_at:
                return dict(self._result)
        result = self._inspect()
        with self._lock:
            result["setup"] = dict(self._result.get("setup") or {})
            self._result = result
            self._expires_at = time.monotonic() + self.CACHE_TTL_S
            return dict(result)

    def start_setup(self) -> tuple[bool, str]:
        if RUN_CONTROLLER.snapshot()["running"]:
            return False, "正式任务运行中，不能配置环境或启动 ADB。"
        with self._lock:
            setup = self._result.get("setup") or {}
            if setup.get("running"):
                return False, "环境配置正在进行，请稍候。"
            self._result["setup"] = {"running": True, "message": "正在安装缺失组件并配置项目依赖…"}
        threading.Thread(target=self._setup, daemon=True, name="hallotickets-environment-setup").start()
        return True, "已开始一键配置：缺少时会安装 Android Platform Tools、Poetry 和项目依赖。"

    def _inspect(self) -> dict[str, Any]:
        items: list[dict[str, str]] = []
        python_version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        items.append({"label": "Python 运行环境", "state": "ready", "detail": python_version})
        automation_ready = u2 is not None and adbutils is not None
        items.append(
            {
                "label": "项目自动化依赖", "state": "ready" if automation_ready else "missing",
                "detail": "uiautomator2 与 adbutils 已安装" if automation_ready else "缺少 uiautomator2 或 adbutils 项目依赖",
            }
        )

        poetry_path = shutil.which("poetry")
        items.append(
            {
                "label": "项目依赖管理", "state": "ready" if poetry_path else "missing",
                "detail": "Poetry 可用" if poetry_path else "未找到 Poetry，点击一键配置即可安装",
            }
        )

        adb_path = shutil.which(_get_adb_path())
        if adb_path:
            try:
                adb_version = _decode_output(_run_command([adb_path, "version"], timeout=4.0).stdout)
                detail = (adb_version.splitlines() or ["ADB 可用"])[0]
                adb_state = "ready"
            except Exception as exc:
                detail = f"ADB 无法启动: {exc}"
                adb_state = "missing"
        else:
            detail = "未找到 adb，请安装 Android Platform Tools"
            adb_state = "missing"
        items.append({"label": "Android Platform Tools", "state": adb_state, "detail": detail})

        try:
            config = Config.load_config(str(DEFAULT_CONFIG_PATH), strict_placeholders=False)
            serial = config.serial
            config_state = "ready"
            config_detail = "项目配置已读取"
        except Exception as exc:
            serial = ""
            config_state = "missing"
            config_detail = f"配置异常: {exc}"
        items.append({"label": "项目配置", "state": config_state, "detail": config_detail})

        device = next((item for item in _parse_adb_devices() if item["serial"] == serial), None) if adb_path and serial else None
        if device and device.get("state") == "device":
            device_state, device_detail = "ready", f"{device.get('model') or serial} 已连接"
        elif serial:
            device_state, device_detail = "action", "请连接手机、开启 USB 调试，并选择“传输文件”模式"
        else:
            device_state, device_detail = "missing", "请先在项目配置中填写手机序列号"
        items.append({"label": "安卓真机", "state": device_state, "detail": device_detail})
        items.extend(
            [
                {
                    "label": "开发者选项与 USB 调试",
                    "state": "action",
                    "detail": "需要在手机设置中手动开启，电脑无法代替确认。",
                },
                {
                    "label": "USB 连接模式",
                    "state": "action",
                    "detail": "请在手机 USB 选项中选择“传输文件”。",
                },
                {
                    "label": "大麦 App 与观演人",
                    "state": "action",
                    "detail": "请确认已登录大麦，且观演人已在 App 内完成实名信息添加。",
                },
            ]
        )

        return {
            "checked_at": datetime.now().astimezone().isoformat(),
            "items": items,
            "task_locked": False,
            "message": "环境检查完成。",
        }

    def _setup(self) -> None:
        messages: list[str] = []
        try:
            adb_path = shutil.which(_get_adb_path())
            if not adb_path:
                winget_path = shutil.which("winget")
                if not winget_path:
                    messages.append("未找到 adb，且系统未提供 winget，无法自动安装 Android Platform Tools")
                else:
                    result = subprocess.run(
                        [
                            winget_path,
                            "install",
                            "--id",
                            "Google.PlatformTools",
                            "--exact",
                            "--accept-package-agreements",
                            "--accept-source-agreements",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=300.0,
                        check=False,
                    )
                    if result.returncode == 0:
                        adb_path = shutil.which(_get_adb_path())
                        messages.append("Android Platform Tools 已安装")
                    else:
                        messages.append(
                            f"Android Platform Tools 安装未完成: {_decode_output(result.stderr) or 'winget 返回失败'}"
                        )
            if adb_path:
                _run_command([adb_path, "start-server"], timeout=10.0)
                messages.append("ADB 服务已启动")

            poetry_path = shutil.which("poetry")
            if not poetry_path:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "poetry"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=300.0,
                    check=False,
                )
                if result.returncode == 0:
                    poetry_path = shutil.which("poetry")
                    messages.append("Poetry 已安装")
                else:
                    messages.append(f"Poetry 安装未完成: {_decode_output(result.stderr) or 'pip 返回失败'}")

            if poetry_path:
                result = subprocess.run(
                    [poetry_path, "install", "--no-root"],
                    cwd=str(ROOT_DIR),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=300.0,
                    check=False,
                )
                if result.returncode == 0:
                    messages.append("项目 Python 依赖已就绪")
                else:
                    messages.append(f"依赖安装未完成: {_decode_output(result.stderr) or '请查看 Poetry 输出'}")
            else:
                messages.append("Poetry 不可用，未执行项目依赖安装")
        except Exception as exc:
            messages.append(f"配置过程异常: {exc}")
        finally:
            result = self._inspect()
            with self._lock:
                result["setup"] = {"running": False, "message": "；".join(messages)}
                self._result = result
                self._expires_at = time.monotonic() + self.CACHE_TTL_S


ENVIRONMENT_CONTROLLER = EnvironmentController()


class PreflightController:
    """Record an explicit pre-sale readiness check without placing an order."""

    READY_STATES = {"detail_page", "sku_page", "order_confirm_page"}

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._result: dict[str, Any] = {
            "ready": False,
            "checked_at": None,
            "page_state": None,
            "message": "尚未执行预热检查",
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._result)

    def invalidate(self, message: str = "目标设置已变更，请重新执行预热检查") -> None:
        with self._lock:
            self._result = {
                "ready": False,
                "checked_at": None,
                "page_state": None,
                "message": message,
            }

    def run(self, status: dict[str, Any]) -> tuple[bool, str]:
        if RUN_CONTROLLER.snapshot()["running"]:
            return False, "正式任务运行中，不能抢占手机连接做预热检查。"
        device = status.get("selected_device") or {}
        if device.get("state") != "device":
            message = "手机未连接，无法预热。"
        else:
            probe = _probe_page_state(status.get("selected_serial") or "")
            result = probe.get("result") if isinstance(probe, dict) else None
            page_state = result.get("state") if isinstance(result, dict) else None
            if page_state in self.READY_STATES:
                message = "预热完成：手机已在可进入极速路径的页面，开抢前请保持亮屏且不要离开大麦。"
                with self._lock:
                    self._result = {
                        "ready": True,
                        "checked_at": datetime.now().astimezone().isoformat(),
                        "page_state": page_state,
                        "message": message,
                    }
                return True, message
            detail = probe.get("error") if isinstance(probe, dict) else "无法识别页面"
            message = f"预热未完成：请手动打开目标演出详情页或票档页后重试（当前: {page_state or detail}）。"

        with self._lock:
            self._result = {
                "ready": False,
                "checked_at": datetime.now().astimezone().isoformat(),
                "page_state": None,
                "message": message,
            }
        return False, message


PREFLIGHT_CONTROLLER = PreflightController()


class DeviceObservationCache:
    """Retain the last idle observation while a formal task owns adb exclusively."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._serial: str | None = None
        self._selected_device: dict[str, str] | None = None
        self._devices: list[dict[str, str]] = []
        self._focus: dict[str, Any] | None = None

    def record(
        self,
        serial: str | None,
        selected_device: dict[str, str] | None,
        devices: list[dict[str, str]],
        focus: dict[str, Any] | None,
    ) -> None:
        with self._lock:
            self._serial = serial
            self._selected_device = dict(selected_device) if selected_device else None
            self._devices = [dict(device) for device in devices]
            self._focus = dict(focus) if focus else None

    def snapshot(
        self, serial: str | None
    ) -> tuple[dict[str, str] | None, list[dict[str, str]], dict[str, Any] | None]:
        with self._lock:
            if serial != self._serial:
                return None, [], None
            selected = dict(self._selected_device) if self._selected_device else None
            devices = [dict(device) for device in self._devices]
            focus = dict(self._focus) if self._focus else None
        if selected:
            selected["state"] = "task_controlled"
            selected["last_known_state"] = "device"
        return selected, devices, focus


DEVICE_OBSERVATION_CACHE = DeviceObservationCache()


def _run_result_display(
    run_summary: dict[str, Any] | None, runtime: dict[str, Any]
) -> dict[str, str]:
    """Translate machine outcomes into a truthful, operator-facing task result."""
    if runtime.get("running"):
        return {
            "label": "任务运行中",
            "tone": "warn",
            "detail": "脚本正在执行或等待开售，尚未产生最终结果。",
        }
    if not run_summary or run_summary.get("_error"):
        return {"label": "尚无结果", "tone": "", "detail": "还没有完成过任务。"}

    outcome = str(run_summary.get("outcome") or "")
    reason = str(run_summary.get("terminal_reason") or "")
    success_details = {
        "order_submitted": "订单已提交，手机应已到支付流程，请尽快完成支付。",
        "order_pending_payment": "已占单待支付，请立即在手机完成支付。",
    }
    if outcome in success_details:
        return {"label": "抢票成功", "tone": "ok", "detail": success_details[outcome]}
    if outcome == "success":
        return {"label": "任务成功", "tone": "ok", "detail": "脚本已完成并确认成功。"}
    if reason == "submit_unverified":
        return {
            "label": "结果待确认",
            "tone": "warn",
            "detail": "提交后未能确认结果；为防重复下单已停止，请手动检查订单。",
        }
    if outcome == "interrupted":
        return {"label": "任务已停止", "tone": "warn", "detail": "任务被手动中断，未得到抢票结果。"}

    failure_details = {
        "sold_out": "目标票档已售罄。",
        "captcha": "平台要求人工验证，脚本已停止。",
        "retries_exhausted": "重试次数已耗尽，未能完成下单。",
        "config_or_device_error": "配置或手机连接异常，任务未完成。",
        "terminal_failure": f"任务已停止：{reason or '无法确认订单结果'}。",
    }
    return {
        "label": "抢票失败",
        "tone": "bad",
        "detail": failure_details.get(outcome, f"任务未完成：{outcome or '未知原因'}。"),
    }


_LOG_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_TASK_PROGRESS_STAGES = (
    (100, "已进入支付或占单", "ok", ("抢票成功", "订单提交成功", "未支付订单", "已占单待支付")),
    (90, "正在提交订单", "warn", ("提交订单", "提交后验证", "重新提交订单")),
    (75, "确认观演人", "warn", ("观演人", "订单确认页", "确认购买")),
    (55, "选择票档", "warn", ("选择票价", "选择数量", "price_index")),
    (35, "选择场次", "warn", ("选择场次", "选择日期", "场次")),
    (20, "进入购票流程", "warn", ("点击购票按钮", "购票入口")),
    (10, "等待开售", "warn", ("等待开售", "开始轮询")),
    (0, "任务已启动", "warn", ("event=boot", "次尝试")),
)


def _task_logs_since_start(logs: list[str], started_at: str | None) -> list[str]:
    """Discard previous runs so a stale success line cannot advance a live task."""
    if not started_at:
        return logs
    try:
        start = datetime.fromisoformat(started_at)
    except ValueError:
        return logs
    recent: list[str] = []
    for line in logs:
        match = _LOG_TIMESTAMP_RE.match(line)
        if not match:
            continue
        try:
            logged_at = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            if start.tzinfo is not None:
                logged_at = logged_at.replace(tzinfo=start.tzinfo)
        except ValueError:
            continue
        if logged_at >= start:
            recent.append(line)
    return recent


def _task_progress(
    runtime: dict[str, Any], logs: list[str], run_summary: dict[str, Any] | None
) -> dict[str, Any]:
    """Expose the latest verified automation stage for the dashboard progress rail."""
    if not runtime.get("running"):
        result = _run_result_display(run_summary, runtime)
        return {
            "active": False,
            "percent": 100 if result["label"] not in {"尚无结果", "任务已停止"} else 0,
            "label": result["label"],
            "detail": result["detail"],
            "tone": result["tone"],
        }

    active_logs = _task_logs_since_start(logs, runtime.get("started_at"))
    for line in reversed(active_logs):
        for percent, label, tone, markers in _TASK_PROGRESS_STAGES:
            if any(marker in line for marker in markers):
                return {
                    "active": True,
                    "percent": percent,
                    "label": label,
                    "detail": line,
                    "tone": tone,
                }
    return {
        "active": True,
        "percent": 0,
        "label": "正在启动任务",
        "detail": "正在建立手机会话并读取当前页面。",
        "tone": "warn",
    }


class PageProbeCache:
    """Keep dashboard observation cheap, asynchronous, and out of the hot path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._serial = ""
        self._expires_at = 0.0
        self._value: dict[str, Any] | None = None
        self._refreshing = False

    def get(self, serial: str, *, task_running: bool) -> dict[str, Any]:
        if task_running:
            return {
                "available": False,
                "suspended": True,
                "message": "正式任务运行中，已暂停页面探测以保证抢票响应速度",
            }
        now = time.monotonic()
        with self._lock:
            if self._value is not None and self._serial == serial and now < self._expires_at:
                return self._value
            if not self._refreshing:
                self._serial = serial
                self._refreshing = True
                threading.Thread(
                    target=self._refresh,
                    args=(serial,),
                    daemon=True,
                    name="hatickets-page-probe",
                ).start()
            if self._value is not None and self._serial == serial:
                return self._value
        return {
            "available": False,
            "pending": True,
            "message": "正在后台识别当前页面…",
        }

    def _refresh(self, serial: str) -> None:
        value = _probe_page_state(serial)
        with self._lock:
            if self._serial == serial:
                self._value = value
                # Dashboard identification is informational.  A longer cache
                # avoids repeatedly dumping UI hierarchy while the phone is idle.
                self._expires_at = time.monotonic() + 8.0
            self._refreshing = False


PAGE_PROBE_CACHE = PageProbeCache()


class ScreenshotFrameCache:
    """Capture phone frames in the background so HTTP requests never wait for adb."""

    MIN_CAPTURE_INTERVAL_S = 0.20

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._serial = ""
        self._png: bytes | None = None
        self._frame_id = 0
        self._captured_at: str | None = None
        self._last_capture_started = 0.0
        self._capturing = False
        self._last_error: str | None = None

    def get(self, serial: str, *, task_running: bool) -> dict[str, Any]:
        if task_running:
            return {"suspended": True, "png": None}

        now = time.monotonic()
        with self._lock:
            if self._serial != serial:
                self._serial = serial
                self._png = None
                self._frame_id = 0
                self._captured_at = None
                self._last_error = None
            if not self._capturing and now - self._last_capture_started >= self.MIN_CAPTURE_INTERVAL_S:
                self._capturing = True
                self._last_capture_started = now
                threading.Thread(
                    target=self._capture,
                    args=(serial,),
                    daemon=True,
                    name="hatickets-screen-capture",
                ).start()
            return {
                "png": self._png,
                "frame_id": self._frame_id,
                "captured_at": self._captured_at,
                "capturing": self._capturing,
                "error": self._last_error,
            }

    def _capture(self, serial: str) -> None:
        try:
            png = _adb_exec_out(serial, ["screencap", "-p"], timeout=8.0)
            if not png:
                raise RuntimeError("手机返回了空截图")
        except Exception as exc:
            with self._lock:
                if self._serial == serial:
                    self._last_error = str(exc)
                    self._capturing = False
            return

        with self._lock:
            if self._serial == serial:
                self._png = png
                self._frame_id += 1
                self._captured_at = datetime.now().astimezone().isoformat()
                self._last_error = None
            self._capturing = False


SCREENSHOT_FRAME_CACHE = ScreenshotFrameCache()


class ConfiguredSerialCache:
    """Avoid reparsing the full JSONC config for every live-frame request."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._mtime_ns: int | None = None
        self._serial: str | None = None

    def get(self) -> str | None:
        path = Path(os.environ.get(CONFIG_OVERRIDE_ENV_VAR) or DEFAULT_CONFIG_PATH)
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            return None
        with self._lock:
            if self._path == path and self._mtime_ns == mtime_ns:
                return self._serial
        try:
            raw = load_config_dict(str(path))
        except Exception:
            return None
        serial = raw.get("serial") if isinstance(raw, dict) else None
        value = str(serial).strip() if serial else None
        with self._lock:
            self._path = path
            self._mtime_ns = mtime_ns
            self._serial = value
        return value


CONFIGURED_SERIAL_CACHE = ConfiguredSerialCache()


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, Any],
    *,
    status: HTTPStatus = HTTPStatus.OK,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _json_default(value: Any) -> str:
    """Keep diagnostics from breaking the whole status endpoint."""
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _text_response(
    handler: BaseHTTPRequestHandler,
    text: str,
    *,
    status: HTTPStatus = HTTPStatus.OK,
    content_type: str = "text/plain; charset=utf-8",
) -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _bytes_response(
    handler: BaseHTTPRequestHandler,
    body: bytes,
    *,
    content_type: str,
    status: HTTPStatus = HTTPStatus.OK,
    headers: dict[str, str] | None = None,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    if body:
        handler.wfile.write(body)


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": str(exc), "_path": str(path)}


def _tail_lines(path: Path, *, limit: int = 60) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return [line.rstrip("\r\n") for line in lines[-limit:]]
    except Exception as exc:
        return [f"[读取日志失败] {exc}"]


def _run_command(args: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _get_adb_path() -> str:
    return "adb"


def _decode_output(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def _parse_adb_devices() -> list[dict[str, str]]:
    result = _run_command([_get_adb_path(), "devices", "-l"], timeout=8.0)
    output = _decode_output(result.stdout)
    devices: list[dict[str, str]] = []
    for raw_line in output.splitlines()[1:]:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0]
        state = parts[1]
        extras = {}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                extras[key] = value
        devices.append(
            {
                "serial": serial,
                "state": state,
                "model": extras.get("model", ""),
                "device": extras.get("device", ""),
                "transport_id": extras.get("transport_id", ""),
                "raw": line,
            }
        )
    return devices


def _adb_shell(serial: str, command: str, *, timeout: float = 10.0) -> str:
    result = _run_command(
        [_get_adb_path(), "-s", serial, "shell", command],
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(_decode_output(result.stderr) or "adb shell failed")
    return _decode_output(result.stdout)


def _adb_exec_out(serial: str, args: list[str], *, timeout: float = 15.0) -> bytes:
    result = _run_command(
        [_get_adb_path(), "-s", serial, "exec-out", *args],
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(_decode_output(result.stderr) or "adb exec-out failed")
    return result.stdout


def _detect_focus(serial: str) -> dict[str, Any]:
    focus_commands = [
        "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'",
    ]
    lines: list[str] = []
    errors: list[str] = []
    for command in focus_commands:
        try:
            text = _adb_shell(serial, command, timeout=10.0)
        except Exception as exc:
            errors.append(str(exc))
            continue
        if text:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                break
    current_line = lines[0] if lines else ""
    package = ""
    activity = ""
    if current_line:
        tokens = current_line.replace("{", " ").replace("}", " ").split()
        target = next((token for token in tokens if "/" in token), "")
        if target:
            package, activity = target.split("/", 1)
    return {
        "line": current_line,
        "package": package,
        "activity": activity,
        "raw_lines": lines,
        "errors": errors,
    }


def _probe_page_state(serial: str) -> dict[str, Any]:
    if u2 is None:
        return {"available": False, "error": "uiautomator2 未安装"}
    try:
        device = u2.connect(serial)
        app = device.app_current() or {}
        activity = str(app.get("activity") or "")
        # These activity names are enough for the dashboard and avoid a slow
        # hierarchy dump before the user has even started the task.
        if "ProjectDetail" in activity:
            return {
                "available": True,
                "result": {
                    "state": "detail_page",
                    "purchase_button": None,
                    "price_container": None,
                },
                "lightweight": True,
                "message": "已通过 Android 页面名识别为演出详情页。",
            }
        if "PlayerActivity" in activity:
            return {
                "available": True,
                "result": {
                    "state": "media_player_overlay",
                    "purchase_button": False,
                    "price_container": False,
                },
                "lightweight": True,
                "message": "检测到视频播放浮层；关闭视频后可重新识别购票页面。",
            }
        probe = PageProbe(device, cache_ttl_s=0.0)
        result = probe.probe_current_page(fast=True)
        return {"available": True, "result": result, "lightweight": True}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _safe_config_snapshot() -> dict[str, Any]:
    config_path = os.environ.get(CONFIG_OVERRIDE_ENV_VAR) or str(DEFAULT_CONFIG_PATH)
    try:
        raw = load_config_dict(config_path)
    except Exception as exc:
        return {"error": str(exc), "raw": None, "effective": None}

    try:
        effective = Config.load_config(config_path, strict_placeholders=False).to_dict()
    except Exception as exc:
        effective = {"error": str(exc)}
    return {"raw": raw, "effective": effective}


def _selected_device_from_config(
    config: dict[str, Any],
) -> tuple[str | None, dict[str, str] | None, list[dict[str, str]]]:
    """Resolve the selected adb device without triggering any UI inspection."""
    serial = None
    effective = config.get("effective")
    if isinstance(effective, dict):
        serial = effective.get("serial")
    if not serial and isinstance(config.get("raw"), dict):
        serial = config["raw"].get("serial")
    devices = _parse_adb_devices()
    selected = next((item for item in devices if item["serial"] == serial), None)
    return serial, selected, devices


def _configured_serial() -> str | None:
    """Read the configured serial through the high-frequency cache."""
    return CONFIGURED_SERIAL_CACHE.get()


def _get_latest_artifacts() -> dict[str, Any]:
    tmp_dir = ROOT_DIR / "tmp"
    latest_price_png = sorted(tmp_dir.glob("price_dump_*.png"), key=lambda p: p.stat().st_mtime)
    latest_unknown_xml = sorted(
        tmp_dir.glob("page_probe_unknown_*.xml"), key=lambda p: p.stat().st_mtime
    )
    return {
        "latest_price_dump_png": str(latest_price_png[-1]) if latest_price_png else None,
        "latest_unknown_xml": str(latest_unknown_xml[-1]) if latest_unknown_xml else None,
    }


def _validate_target_updates(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ConfigError("目标设置必须是一个对象")
    unknown = set(payload) - TARGET_EDITABLE_FIELDS
    if unknown:
        raise ConfigError(f"不支持修改的字段: {', '.join(sorted(unknown))}")
    updates: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"keyword", "city", "date", "price"}:
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{key} 不能为空")
            updates[key] = value.strip()
        elif key in {"target_title", "target_venue"}:
            if value is not None and not isinstance(value, str):
                raise ConfigError(f"{key} 必须是字符串或空值")
            updates[key] = value.strip() if isinstance(value, str) and value.strip() else None
        elif key == "users":
            if not isinstance(value, list) or not value:
                raise ConfigError("观演人至少需要填写一位")
            users = [str(name).strip() for name in value]
            if not all(users):
                raise ConfigError("观演人姓名不能为空")
            updates[key] = users
        elif key == "price_index":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ConfigError("票档兜底序号必须是非负整数")
            updates[key] = value
        elif key == "price_strategy":
            if value not in {"exact", "cheapest_available"}:
                raise ConfigError("票档策略仅支持 exact 或 cheapest_available")
            updates[key] = value
        elif key == "sell_start_time":
            if value is None or value == "":
                updates[key] = None
            elif not isinstance(value, str):
                raise ConfigError("开售时间必须是 ISO 格式字符串或空值")
            else:
                try:
                    parsed = datetime.fromisoformat(value)
                except ValueError as exc:
                    raise ConfigError("开售时间格式无效") from exc
                if parsed.tzinfo is None:
                    raise ConfigError("开售时间必须包含时区，例如 +08:00")
                updates[key] = value
        elif key == "auto_navigate":
            if not isinstance(value, bool):
                raise ConfigError("自动导航必须是布尔值")
            updates[key] = value
    if not updates:
        raise ConfigError("没有可保存的目标设置")

    current = load_config_dict(str(DEFAULT_CONFIG_PATH))
    candidate = {**current, **updates}
    Config(**filter_known_config_keys(candidate))
    return updates


def update_target_settings(payload: Any) -> tuple[bool, str]:
    """Persist an idle-time target update with the normal config lock/backup."""
    if RUN_CONTROLLER.snapshot()["running"]:
        return False, "任务运行中不能修改目标；请先停止任务，再保存新的目标。"
    try:
        updates = _validate_target_updates(payload)
        update_config_values(updates, str(DEFAULT_CONFIG_PATH))
    except (ConfigError, ValueError, TypeError) as exc:
        return False, f"目标设置未保存: {exc}"
    PREFLIGHT_CONTROLLER.invalidate()
    return True, "目标设置已保存，下次正式提交会立刻使用新目标。"


def install_desktop_shortcut() -> tuple[bool, str]:
    """Create the local Windows launcher shortcut without touching automation state."""
    if os.name != "nt":
        return False, "桌面快捷方式目前仅支持 Windows。"
    if not DESKTOP_INSTALL_SCRIPT.exists():
        return False, "未找到桌面安装脚本。"
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(DESKTOP_INSTALL_SCRIPT),
            ],
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30.0,
            check=False,
        )
    except OSError as exc:
        return False, f"无法创建桌面图标: {exc}"
    if result.returncode != 0:
        return False, f"创建桌面图标失败: {_decode_output(result.stderr) or 'PowerShell 返回失败'}"
    return True, _decode_output(result.stdout) or "HalloTickets 桌面图标已创建。"


def collect_status() -> dict[str, Any]:
    runtime = RUN_CONTROLLER.snapshot()
    config = _safe_config_snapshot()
    effective = config.get("effective") if isinstance(config.get("effective"), dict) else {}
    raw = config.get("raw") if isinstance(config.get("raw"), dict) else {}
    serial = effective.get("serial") or raw.get("serial")
    focus = None
    page_probe = None
    device_error = None
    if runtime["running"]:
        selected_device, devices, focus = DEVICE_OBSERVATION_CACHE.snapshot(serial)
        if selected_device is None and serial:
            selected_device = {
                "serial": str(serial),
                "state": "task_controlled",
                "model": "",
                "device": "",
                "transport_id": "",
            }
        page_probe = PAGE_PROBE_CACHE.get(str(serial or ""), task_running=True)
    else:
        serial, selected_device, devices = _selected_device_from_config(config)
    if not runtime["running"] and serial and selected_device and selected_device["state"] == "device":
        try:
            focus = _detect_focus(serial)
        except Exception as exc:
            device_error = str(exc)
        page_probe = PAGE_PROBE_CACHE.get(serial, task_running=runtime["running"])
        DEVICE_OBSERVATION_CACHE.record(serial, selected_device, devices, focus)
    elif not runtime["running"] and serial:
        device_error = "目标设备当前未处于可用连接状态"

    run_summary = _read_json_file(RUN_SUMMARY_PATH)
    logs = _tail_lines(LOG_PATH, limit=80)
    last_log = logs[-1] if logs else ""
    return {
        "refreshed_at": datetime.now().astimezone().isoformat(),
        "config": config,
        "run_summary": run_summary,
        "run_result": _run_result_display(run_summary, runtime),
        "task_progress": _task_progress(runtime, logs, run_summary),
        "devices": devices,
        "selected_serial": serial,
        "selected_device": selected_device,
        "focus": focus,
        "page_probe": page_probe,
        "device_error": device_error,
        "last_log": last_log,
        "logs": logs,
        "artifacts": _get_latest_artifacts(),
        "runtime": runtime,
        "preflight": PREFLIGHT_CONTROLLER.snapshot(),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "HalloTicketsDashboard/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            _json_response(self, collect_status())
            return
        if path == "/api/environment":
            _json_response(self, ENVIRONMENT_CONTROLLER.snapshot())
            return
        if path == "/api/screenshot":
            self._handle_screenshot(urllib.parse.parse_qs(parsed.query))
            return
        if path == "/":
            self._serve_file(DASHBOARD_DIR / "index.html")
            return
        local_path = (DASHBOARD_DIR / path.lstrip("/")).resolve()
        if DASHBOARD_DIR.resolve() not in local_path.parents and local_path != DASHBOARD_DIR.resolve():
            _text_response(self, "Not found", status=HTTPStatus.NOT_FOUND)
            return
        self._serve_file(local_path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/run/formal-submit":
            status = collect_status()
            started, message = RUN_CONTROLLER.start_formal_submit(status)
            _json_response(
                self,
                {"ok": started, "message": message, "runtime": RUN_CONTROLLER.snapshot()},
                status=HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
            )
            return
        if parsed.path == "/api/run/stop":
            stopped, message = RUN_CONTROLLER.stop()
            _json_response(
                self,
                {"ok": stopped, "message": message, "runtime": RUN_CONTROLLER.snapshot()},
                status=HTTPStatus.OK if stopped else HTTPStatus.CONFLICT,
            )
            return
        if parsed.path == "/api/preflight":
            status = collect_status()
            ready, message = PREFLIGHT_CONTROLLER.run(status)
            _json_response(
                self,
                {"ok": ready, "message": message, "preflight": PREFLIGHT_CONTROLLER.snapshot()},
                status=HTTPStatus.OK if ready else HTTPStatus.CONFLICT,
            )
            return
        if parsed.path == "/api/environment/setup":
            started, message = ENVIRONMENT_CONTROLLER.start_setup()
            _json_response(
                self,
                {"ok": started, "message": message, "environment": ENVIRONMENT_CONTROLLER.snapshot()},
                status=HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
            )
            return
        if parsed.path == "/api/desktop/install":
            installed, message = install_desktop_shortcut()
            _json_response(
                self,
                {"ok": installed, "message": message},
                status=HTTPStatus.OK if installed else HTTPStatus.CONFLICT,
            )
            return
        if parsed.path == "/api/config/target":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0 or content_length > 16_384:
                    raise ValueError("请求内容长度无效")
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                _json_response(
                    self,
                    {"ok": False, "message": f"无法读取目标设置: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            saved, message = update_target_settings(payload)
            _json_response(
                self,
                {"ok": saved, "message": message},
                status=HTTPStatus.OK if saved else HTTPStatus.CONFLICT,
            )
            return
        else:
            _text_response(self, "Not found", status=HTTPStatus.NOT_FOUND)
            return

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _handle_screenshot(self, query: dict[str, list[str]]) -> None:
        if RUN_CONTROLLER.snapshot()["running"]:
            _text_response(
                self,
                "正式任务运行中，已暂停看板截图以保证手机响应速度",
                status=HTTPStatus.CONFLICT,
            )
            return
        serial = _configured_serial()
        if not serial:
            _text_response(
                self,
                "未配置目标手机，无法抓取截图",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        frame = SCREENSHOT_FRAME_CACHE.get(serial, task_running=False)
        png = frame.get("png")
        if not png:
            if frame.get("error"):
                _text_response(
                    self,
                    f"截图失败: {frame['error']}",
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            _text_response(
                self,
                "正在建立手机实时画面…",
                status=HTTPStatus.ACCEPTED,
            )
            return
        frame_id = str(frame["frame_id"])
        previous_frame = (query.get("after") or [""])[0]
        if previous_frame == frame_id:
            _bytes_response(
                self,
                b"",
                content_type="image/png",
                status=HTTPStatus.NO_CONTENT,
                headers={"X-HaTickets-Frame-Id": frame_id},
            )
            return
        _bytes_response(
            self,
            png,
            content_type="image/png",
            headers={
                "X-HaTickets-Frame-Id": frame_id,
                "X-HaTickets-Captured-At": str(frame.get("captured_at") or ""),
            },
        )

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            _text_response(self, "Not found", status=HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix == ".webmanifest":
            content_type = "application/manifest+json"
        _bytes_response(self, path.read_bytes(), content_type=content_type)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HalloTickets 本地状态看板")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args(argv)

    os.chdir(ROOT_DIR)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"HalloTickets 状态看板已启动：{url}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n状态看板已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
