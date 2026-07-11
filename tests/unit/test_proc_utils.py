# -*- coding: UTF-8 -*-
"""Tests for mobile.proc_utils (issue #50: Windows GBK 解码崩溃修复)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import mobile.proc_utils
from mobile.proc_utils import run_captured


class TestRunCaptured:
    def test_run_captured_forces_utf8_and_replace(self):
        """『所有 text 模式调用都带 encoding』的 Windows 语义 mock 验证（不依赖平台）。"""
        completed = subprocess.CompletedProcess(
            args=["adb"], returncode=0, stdout="", stderr=""
        )
        with patch(
            "mobile.proc_utils.subprocess.run", return_value=completed
        ) as mock_run:
            result = run_captured(["adb", "devices"])

        assert result is completed
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

    def test_run_captured_forwards_cmd_timeout_check_env(self):
        completed = subprocess.CompletedProcess(
            args=["adb"], returncode=0, stdout="", stderr=""
        )
        with patch(
            "mobile.proc_utils.subprocess.run", return_value=completed
        ) as mock_run:
            run_captured(
                ["adb", "shell", "pm", "dump", "cn.damai"],
                timeout=3.0,
                check=True,
                env={"PATH": "/usr/bin"},
            )

        assert mock_run.call_args.args[0] == ["adb", "shell", "pm", "dump", "cn.damai"]
        kwargs = mock_run.call_args.kwargs
        assert kwargs["timeout"] == 3.0
        assert kwargs["check"] is True
        assert kwargs["env"] == {"PATH": "/usr/bin"}

    def test_run_captured_decodes_multibyte_output_end_to_end(self):
        """端到端：GBK 下会在 0xa7 崩溃的字节序列，显式 utf-8 后任何 locale 均成功。"""
        result = run_captured(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write('channelName=大麦‧\\n'.encode('utf-8'))",
            ]
        )
        assert result.returncode == 0
        assert "大麦‧" in result.stdout

    def test_run_captured_propagates_check_and_timeout(self):
        """异常原样透传：保证调用方的 except CalledProcessError/SubprocessError 分支生效。"""
        with patch(
            "mobile.proc_utils.subprocess.run",
            side_effect=subprocess.CalledProcessError(returncode=1, cmd=["adb"]),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                run_captured(["adb", "devices"], check=True)

        with patch(
            "mobile.proc_utils.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="adb", timeout=1),
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                run_captured(["adb", "devices"], timeout=1)


def _iter_subprocess_text_calls(tree: ast.AST):
    """Yield subprocess.run/Popen/check_output/check_call/call 调用节点中
    带 text=True / universal_newlines=True 的调用及其关键字集合。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and func.attr in {"run", "Popen", "check_output", "check_call", "call"}
        ):
            continue
        keyword_names = {kw.arg for kw in node.keywords if kw.arg}
        text_mode = any(
            kw.arg in {"text", "universal_newlines"}
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        if text_mode:
            yield node, keyword_names


def test_no_naked_text_subprocess_in_mobile():
    """守卫测试：mobile/ 内 text 模式 subprocess 调用必须显式传 encoding，
    或位于 proc_utils.py 内（防止 issue #50 类问题复发）。"""
    mobile_dir = Path(mobile.proc_utils.__file__).resolve().parent
    violations = []
    for py_file in sorted(mobile_dir.rglob("*.py")):
        if py_file.name == "proc_utils.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node, keyword_names in _iter_subprocess_text_calls(tree):
            if "encoding" not in keyword_names:
                violations.append(f"{py_file.relative_to(mobile_dir)}:{node.lineno}")
    assert violations == [], (
        "以下 subprocess 调用为 text 模式但未显式传 encoding，"
        f"请改用 mobile.proc_utils.run_captured 或补 encoding 参数: {violations}"
    )
