from types import SimpleNamespace
from unittest.mock import Mock, patch

from mobile.config import CONFIG_OVERRIDE_ENV_VAR
from mobile.status_dashboard import (
    DEFAULT_CONFIG_PATH,
    ConfiguredSerialCache,
    EnvironmentController,
    PreflightController,
    RUN_SUMMARY_PATH,
    PageProbeCache,
    RunController,
    _run_result_display,
    _probe_page_state,
    _task_progress,
    install_desktop_shortcut,
    _validate_target_updates,
    collect_status,
    update_target_settings,
)


def _status(*, formal=True, connected=True):
    return {
        "config": {
            "effective": {
                "probe_only": not formal,
                "if_commit_order": formal,
            }
        },
        "selected_device": {"state": "device" if connected else "offline"},
        "selected_serial": "serial-1",
    }


def test_formal_submit_controller_starts_one_local_process():
    controller = RunController()
    process = Mock()
    process.poll.return_value = None
    process.pid = 12345

    with patch("mobile.status_dashboard.subprocess.Popen", return_value=process) as popen:
        started, message = controller.start_formal_submit(_status())

    assert started is True
    assert "已启动" in message
    command = popen.call_args.args[0]
    assert command[:3] == [__import__("sys").executable, "-m", "mobile.damai_app"]
    environment = popen.call_args.kwargs["env"]
    assert environment[CONFIG_OVERRIDE_ENV_VAR] == str(DEFAULT_CONFIG_PATH)
    assert environment["HATICKETS_RESULT_JSON"] == str(RUN_SUMMARY_PATH)
    assert controller.snapshot()["running"] is True


def test_formal_submit_controller_refuses_non_formal_or_disconnected_runs():
    controller = RunController()
    with patch("mobile.status_dashboard.subprocess.Popen") as popen:
        assert controller.start_formal_submit(_status(formal=False))[0] is False
        assert controller.start_formal_submit(_status(connected=False))[0] is False

    popen.assert_not_called()


def test_stop_controller_terminates_only_its_own_running_process():
    controller = RunController()
    process = Mock()
    process.poll.return_value = None
    controller._process = process

    stopped, message = controller.stop()

    assert stopped is True
    assert "已停止" in message
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=2.0)


def test_page_probe_cache_skips_device_probe_while_task_is_running():
    cache = PageProbeCache()
    with patch("mobile.status_dashboard._probe_page_state") as probe:
        paused = cache.get("serial", task_running=True)

    assert paused["suspended"] is True
    probe.assert_not_called()


def test_dashboard_page_probe_identifies_detail_activity_without_ui_dump():
    device = Mock()
    device.app_current.return_value = {"activity": "cn.damai.ProjectDetailActivity"}
    with patch("mobile.status_dashboard.u2") as u2:
        with patch("mobile.status_dashboard.PageProbe") as probe:
            u2.connect.return_value = device
            result = _probe_page_state("serial-1")

    assert result["result"]["state"] == "detail_page"
    assert result["result"]["purchase_button"] is None
    probe.assert_not_called()


def test_target_update_is_locked_while_run_is_active():
    with patch("mobile.status_dashboard.RUN_CONTROLLER.snapshot", return_value={"running": True}):
        saved, message = update_target_settings({"keyword": "test"})

    assert saved is False
    assert "任务运行中" in message


def test_target_update_uses_locked_config_writer_after_validation():
    updates = {"keyword": "新演出", "city": "上海"}
    with patch("mobile.status_dashboard.RUN_CONTROLLER.snapshot", return_value={"running": False}):
        with patch("mobile.status_dashboard._validate_target_updates", return_value=updates):
            with patch("mobile.status_dashboard.update_config_values") as write:
                saved, message = update_target_settings(updates)

    assert saved is True
    assert "已保存" in message
    write.assert_called_once_with(updates, str(DEFAULT_CONFIG_PATH))


def test_preflight_marks_detail_page_ready_without_clicking_purchase():
    controller = PreflightController()
    with patch("mobile.status_dashboard.RUN_CONTROLLER.snapshot", return_value={"running": False}):
        with patch(
            "mobile.status_dashboard._probe_page_state",
            return_value={"available": True, "result": {"state": "detail_page"}},
        ) as probe:
            ready, message = controller.run(_status())

    assert ready is True
    assert "预热完成" in message
    assert controller.snapshot()["page_state"] == "detail_page"
    probe.assert_called_once_with("serial-1")


def test_preflight_rejects_wrong_page_and_running_task():
    controller = PreflightController()
    with patch("mobile.status_dashboard.RUN_CONTROLLER.snapshot", return_value={"running": False}):
        with patch(
            "mobile.status_dashboard._probe_page_state",
            return_value={"available": True, "result": {"state": "homepage"}},
        ):
            ready, message = controller.run(_status())
    assert ready is False
    assert "详情页或票档页" in message

    with patch("mobile.status_dashboard.RUN_CONTROLLER.snapshot", return_value={"running": True}):
        ready, message = controller.run(_status())
    assert ready is False
    assert "任务运行中" in message


def test_status_collects_adb_devices_once_per_refresh():
    config = {"effective": {"serial": "serial-1"}, "raw": {}}
    devices = [{"serial": "serial-1", "state": "device"}]
    with patch("mobile.status_dashboard._safe_config_snapshot", return_value=config):
        with patch("mobile.status_dashboard._parse_adb_devices", return_value=devices) as parse:
            with patch("mobile.status_dashboard._detect_focus", return_value={}):
                with patch("mobile.status_dashboard.PAGE_PROBE_CACHE.get", return_value={}):
                    with patch("mobile.status_dashboard.RUN_CONTROLLER.snapshot", return_value={"running": False}):
                        collect_status()

    parse.assert_called_once()


def test_running_task_does_not_issue_dashboard_adb_observation_calls():
    config = {"effective": {"serial": "serial-1"}, "raw": {}}
    with patch("mobile.status_dashboard._safe_config_snapshot", return_value=config):
        with patch("mobile.status_dashboard._parse_adb_devices") as parse:
            with patch("mobile.status_dashboard._detect_focus") as focus:
                with patch("mobile.status_dashboard.RUN_CONTROLLER.snapshot", return_value={"running": True}):
                    status = collect_status()

    assert status["selected_device"]["state"] == "task_controlled"
    assert status["page_probe"]["suspended"] is True
    parse.assert_not_called()
    focus.assert_not_called()


def test_configured_serial_cache_reuses_config_until_the_file_changes():
    cache = ConfiguredSerialCache()
    with patch("mobile.status_dashboard.Path.stat", return_value=SimpleNamespace(st_mtime_ns=42)):
        with patch("mobile.status_dashboard.load_config_dict", return_value={"serial": "phone-1"}) as load:
            assert cache.get() == "phone-1"
            assert cache.get() == "phone-1"

    load.assert_called_once()


def test_run_result_display_uses_clear_success_failure_and_ambiguous_labels():
    assert _run_result_display({"outcome": "order_submitted"}, {"running": False})[
        "label"
    ] == "抢票成功"
    assert _run_result_display({"outcome": "sold_out"}, {"running": False})[
        "label"
    ] == "抢票失败"
    assert _run_result_display(
        {"outcome": "terminal_failure", "terminal_reason": "submit_unverified"},
        {"running": False},
    )["label"] == "结果待确认"
    assert _run_result_display({"outcome": "sold_out"}, {"running": True})[
        "label"
    ] == "任务运行中"


def test_environment_snapshot_reports_python_project_adb_and_device_readiness():
    controller = EnvironmentController()
    completed = SimpleNamespace(stdout=b"Android Debug Bridge version 1.0.41\n", stderr=b"")
    config = SimpleNamespace(serial="serial-1")
    with patch("mobile.status_dashboard.RUN_CONTROLLER.snapshot", return_value={"running": False}):
        with patch("mobile.status_dashboard.shutil.which", side_effect=lambda name: f"C:/{name}.exe"):
            with patch("mobile.status_dashboard._run_command", return_value=completed):
                with patch("mobile.status_dashboard.Config.load_config", return_value=config):
                    with patch(
                        "mobile.status_dashboard._parse_adb_devices",
                        return_value=[{"serial": "serial-1", "state": "device", "model": "Phone"}],
                    ):
                        snapshot = controller.snapshot(force=True)

    states = {item["label"]: item["state"] for item in snapshot["items"]}
    assert states["Python 运行环境"] == "ready"
    assert states["Android Platform Tools"] == "ready"
    assert states["安卓真机"] == "ready"


def test_environment_setup_is_locked_while_formal_task_is_running():
    controller = EnvironmentController()
    with patch("mobile.status_dashboard.RUN_CONTROLLER.snapshot", return_value={"running": True}):
        started, message = controller.start_setup()

    assert started is False
    assert "正式任务运行中" in message


def test_environment_setup_installs_missing_platform_tools_and_poetry():
    controller = EnvironmentController()
    paths = iter([None, "C:/winget.exe", "C:/adb.exe", None, "C:/poetry.exe"])
    successful = SimpleNamespace(returncode=0, stderr=b"")
    inspected = {"checked_at": "now", "items": [], "task_locked": False}
    with patch("mobile.status_dashboard.shutil.which", side_effect=lambda _: next(paths)):
        with patch("mobile.status_dashboard.subprocess.run", return_value=successful) as run:
            with patch("mobile.status_dashboard._run_command"):
                with patch.object(controller, "_inspect", return_value=inspected):
                    controller._setup()

    commands = [call.args[0] for call in run.call_args_list]
    assert any("Google.PlatformTools" in command for command in commands)
    assert [__import__("sys").executable, "-m", "pip", "install", "poetry"] in commands
    assert any(command[-2:] == ["install", "--no-root"] for command in commands)


def test_desktop_shortcut_install_invokes_the_local_windows_installer():
    completed = SimpleNamespace(
        returncode=0,
        stdout="HalloTickets 桌面图标已创建。".encode("utf-8"),
        stderr=b"",
    )
    with patch("mobile.status_dashboard.subprocess.run", return_value=completed) as run:
        installed, message = install_desktop_shortcut()

    assert installed is True
    assert "已创建" in message
    command = run.call_args.args[0]
    assert command[:5] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]


def test_target_validation_accepts_exact_match_and_timed_start_fields():
    current = {
        "keyword": "旧演出",
        "users": ["观演人"],
        "city": "上海",
        "date": "8.1",
        "price": "480元",
        "price_index": 0,
        "price_strategy": "exact",
        "auto_navigate": True,
    }
    payload = {
        "target_title": "精确演出标题",
        "target_venue": "测试场馆",
        "sell_start_time": "2026-08-02T17:21:00+08:00",
        "auto_navigate": False,
    }
    with patch("mobile.status_dashboard.load_config_dict", return_value=current):
        with patch("mobile.status_dashboard.Config"):
            updates = _validate_target_updates(payload)

    assert updates == payload


def test_task_progress_uses_the_latest_current_run_log_stage():
    runtime = {"running": True, "started_at": "2026-08-02T17:20:00+08:00"}
    logs = [
        "2026-08-02 17:20:01 [INFO] mobile.damai_app:126 - 等待开售，将在指定时间前开始轮询",
        "2026-08-02 17:21:00 [INFO] mobile.fast_pipeline:451 - 选择票价...",
    ]
    progress = _task_progress(runtime, logs, None)

    assert progress["percent"] == 55
    assert progress["label"] == "选择票档"


def test_task_progress_starts_from_zero_before_the_first_stage_log():
    progress = _task_progress({"running": True, "started_at": None}, [], None)

    assert progress["percent"] == 0
    assert progress["label"] == "正在启动任务"


def test_task_progress_finishes_with_the_verified_result():
    progress = _task_progress({"running": False}, [], {"outcome": "order_submitted"})

    assert progress["percent"] == 100
    assert progress["label"] == "抢票成功"
