"""start_ticket_grabbing.sh 资金防护闸门测试（U-01）。

A 组：早退闸门——纯 subprocess，无任何 shim / 真机 / poetry。
B 组：深路径——fixture 提供假 poetry / 假 adb / python3 软链，配合
      HATICKETS_DRY_RUN=1 测试钩子，全程绝不启动 python -m damai_app。
C 组：--probe 回归 + 文档守卫。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash/pty 测试仅限 Unix")

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "mobile" / "scripts" / "start_ticket_grabbing.sh"


def run_script(args, env=None, stdin=subprocess.DEVNULL, timeout=60):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        stdin=stdin,
        timeout=timeout,
    )


def _write_config(tmp_path, probe_only, if_commit_order, keyword="张杰 演唱会"):
    config = tmp_path / "config.jsonc"
    payload = {
        "serial": "FAKESERIAL",
        "keyword": keyword,
        "users": ["张三"],
        "city": "上海",
        "date": "2026-08-01",
        "price": "480元",
        "price_index": 0,
        "probe_only": probe_only,
        "if_commit_order": if_commit_order,
        "auto_navigate": True,
    }
    if keyword is None:
        del payload["keyword"]
    config.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


@pytest.fixture
def script_env(tmp_path):
    """假 poetry(恒 exit 0) + 假 adb(输出已连接设备) + python3 软链，全程无真机。"""
    tmpbin = tmp_path / "bin"
    tmpbin.mkdir()
    (tmpbin / "poetry").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    # 假 adb 需实现 get-state：preflight_check_device 对配置里的 serial 走
    # `adb -s <serial> get-state` 精确预检（BSD sed 提取修复后 macOS 也会走到）
    (tmpbin / "adb").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *get-state*) printf "device\\n" ;;\n'
        '  *) printf "List of devices attached\\nFAKESERIAL\\tdevice\\n" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    os.chmod(tmpbin / "poetry", 0o755)
    os.chmod(tmpbin / "adb", 0o755)
    # 真实 python（poetry venv 的 3.x）供版本检查与 mobile.config heredoc 使用
    (tmpbin / "python3").symlink_to(sys.executable)
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    env = {
        "PATH": f"{tmpbin}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "ANDROID_HOME": str(sdk),
        "HATICKETS_DRY_RUN": "1",  # 安全钩子：倒数后到此为止，绝不启动 damai_app
    }
    return env


class TestEarlyExitGate:
    """A 组：早退闸门（模式判定位于一切预检之前，无需 shim）。"""

    def test_bare_yes_exits_1_with_migration_hint(self):
        r = run_script(["--yes"])
        assert r.returncode == 1
        combined = r.stdout + r.stderr
        assert "--probe" in combined
        assert "--commit --yes" in combined  # 可复制的迁移命令
        assert "已写回配置文件" not in combined
        assert "开始执行脚本" not in combined

    def test_short_flag_y_equivalent_to_yes(self):
        r = run_script(["-y"])
        assert r.returncode == 1
        assert "--commit --yes" in (r.stdout + r.stderr)

    def test_probe_commit_mutually_exclusive(self):
        r = run_script(["--probe", "--commit"])
        assert r.returncode == 1  # 参数层拦截，先于 poetry 预检的 exit 2
        assert "互斥" in (r.stdout + r.stderr)

    def test_bare_invocation_non_tty_exits_1(self):
        r = run_script([])
        assert r.returncode == 1
        combined = r.stdout + r.stderr
        assert "非交互" in combined
        assert "--probe 或 --commit" in combined

    def test_commit_without_yes_non_tty_exits_1(self, tmp_path):
        config = _write_config(tmp_path, probe_only=True, if_commit_order=False)
        before = config.read_bytes()
        r = run_script(["--commit", "--config", str(config)])
        assert r.returncode == 1
        assert "非交互环境请用 --commit --yes" in (r.stdout + r.stderr)
        assert config.read_bytes() == before

    def test_early_exit_precedes_prechecks(self, tmp_path):
        """哨兵测试：裸 --yes 的退出必须发生在 poetry 预检之前。"""
        tmpbin = tmp_path / "bin"
        tmpbin.mkdir()
        sentinel = tmp_path / "poetry-was-called"
        (tmpbin / "poetry").write_text(
            f'#!/bin/sh\ntouch "{sentinel}"\nexit 0\n', encoding="utf-8"
        )
        os.chmod(tmpbin / "poetry", 0o755)
        r = run_script(
            ["--yes"], env={"PATH": f"{tmpbin}:/usr/bin:/bin", "HOME": str(tmp_path)}
        )
        assert r.returncode == 1
        assert not sentinel.exists()

    def test_early_exit_does_not_touch_config(self, tmp_path):
        config = _write_config(tmp_path, probe_only=True, if_commit_order=False)
        before = config.read_bytes()
        for argv in (["--yes", "--config", str(config)],
                     ["--probe", "--commit", "--config", str(config)]):
            r = run_script(argv)
            assert r.returncode == 1
            assert config.read_bytes() == before


class TestCommitGateDeepPath:
    """B 组：确认词 / 摘要 / 倒数（shim 环境 + pty，全程无真机、无实际执行）。"""

    def _run_with_pty_input(self, args, env, input_text, timeout=60):
        import pty

        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(
                ["bash", str(SCRIPT), *args],
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
            )
            os.close(slave)
            # 预写确认词：脚本执行到 read -r 时即可读到
            os.write(master, input_text.encode("utf-8"))
            out, _ = proc.communicate(timeout=timeout)
            return proc.returncode, out.decode("utf-8", errors="replace")
        finally:
            os.close(master)

    def test_commit_wrong_confirm_word_leaves_config_untouched(
        self, tmp_path, script_env
    ):
        config = _write_config(tmp_path, probe_only=True, if_commit_order=False)
        before = config.read_bytes()
        code, out = self._run_with_pty_input(
            ["--commit", "--config", str(config)], script_env, "NOT-GO\n"
        )
        assert code == 1
        assert "确认词不匹配" in out
        assert str(config) in out
        assert config.read_bytes() == before  # 闸门在 update_runtime_mode 之前
        assert "开始执行脚本" not in out

    def test_commit_correct_word_GO_then_summary_countdown(self, tmp_path, script_env):
        config = _write_config(tmp_path, probe_only=True, if_commit_order=False)
        code, out = self._run_with_pty_input(
            ["--commit", "--config", str(config)], script_env, "GO\n"
        )
        assert code == 0  # DRY-RUN 钩子处安全退出
        # 输出顺序：写回配置 → 正式提交横幅 → 摘要 → 倒数
        i_write = out.index("已写回配置文件")
        i_summary = out.index("正式下单摘要")
        i_count = out.index("3...")
        assert i_write < i_summary < i_count
        # 摘要四要素
        assert "张杰 演唱会" in out
        assert "480元" in out
        assert "1 人" in out
        assert "city=上海" in out
        # 残留态警示 + 绝未启动 damai_app
        assert "配置已写为正式模式" in out
        assert "开始执行脚本" not in out
        assert "DRY-RUN" in out
        flags = json.loads(config.read_text(encoding="utf-8"))
        assert flags["probe_only"] is False
        assert flags["if_commit_order"] is True

    def test_commit_yes_skips_word_but_not_summary_countdown(
        self, tmp_path, script_env
    ):
        # 配置已是正式模式：无需改写，但摘要+倒数依然无条件出现（AC2）
        config = _write_config(tmp_path, probe_only=False, if_commit_order=True)
        before = config.read_bytes()
        r = run_script(["--commit", "--yes", "--config", str(config)], env=script_env)
        assert r.returncode == 0
        assert "请输入 GO" not in r.stdout
        assert "已写回配置文件" not in r.stdout
        assert "正式下单摘要" in r.stdout
        assert "3..." in r.stdout and "1..." in r.stdout
        assert "正式提交模式" in r.stdout
        assert "开始执行脚本" not in r.stdout
        assert config.read_bytes() == before

    def test_commit_confirm_accepts_keyword(self, tmp_path, script_env):
        config = _write_config(tmp_path, probe_only=False, if_commit_order=True)
        code, out = self._run_with_pty_input(
            ["--commit", "--config", str(config)], script_env, "张杰 演唱会\n"
        )
        assert code == 0
        assert "正式下单摘要" in out

    def test_commit_confirm_tightens_to_GO_when_keyword_missing(
        self, tmp_path, script_env
    ):
        # kw 提取为空时仅接受 GO，绝不放宽
        config = _write_config(
            tmp_path, probe_only=False, if_commit_order=True, keyword=None
        )
        before = config.read_bytes()
        code, out = self._run_with_pty_input(
            ["--commit", "--config", str(config)], script_env, "随便什么词\n"
        )
        assert code == 1
        assert "确认词不匹配" in out
        assert config.read_bytes() == before


class TestProbeRegression:
    """C 组：--probe 路径交互语义零回归。"""

    def test_probe_path_cancel_on_eof_unchanged(self, tmp_path, script_env):
        # 配置为正式模式 → probe 方向需要改写 → 无 --yes 时 read 遇 EOF 即取消
        config = _write_config(tmp_path, probe_only=False, if_commit_order=True)
        before = config.read_bytes()
        r = run_script(["--probe", "--config", str(config)], env=script_env)
        assert r.returncode == 1
        assert "已取消，配置文件未修改" in r.stdout
        assert config.read_bytes() == before

    def test_probe_yes_flips_config_to_safe_mode(self, tmp_path, script_env):
        config = _write_config(tmp_path, probe_only=False, if_commit_order=True)
        r = run_script(["--probe", "--yes", "--config", str(config)], env=script_env)
        assert r.returncode == 0  # DRY-RUN 钩子处退出
        assert "已写回配置文件" in r.stdout
        assert "安全探测模式" in r.stdout
        assert "正式下单摘要" not in r.stdout  # 摘要+倒数只属于 --commit 路径
        flags = json.loads(config.read_text(encoding="utf-8"))
        assert flags["probe_only"] is True
        assert flags["if_commit_order"] is False

    def test_probe_yes_when_already_safe_no_rewrite(self, tmp_path, script_env):
        config = _write_config(tmp_path, probe_only=True, if_commit_order=False)
        before = config.read_bytes()
        r = run_script(["--probe", "--yes", "--config", str(config)], env=script_env)
        assert r.returncode == 0
        assert "已写回配置文件" not in r.stdout
        assert "安全探测模式" in r.stdout
        assert config.read_bytes() == before


class TestDocsGuards:
    """文档守卫：防止旧「裸 --yes 真下单」肌肉记忆被继续训练（AC4）。"""

    DOCS = [REPO / "README.md", REPO / "docs" / "quick-start.md"]

    def test_docs_no_bare_yes_formal_examples(self):
        for doc in self.DOCS:
            for lineno, line in enumerate(
                doc.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "start_ticket_grabbing.sh" in line and "--yes" in line:
                    assert "--probe" in line or "--commit" in line, (
                        f"{doc.name}:{lineno} 存在裸 --yes 示例: {line.strip()}"
                    )

    def test_docs_mention_commit_semantics(self):
        for doc in self.DOCS:
            text = doc.read_text(encoding="utf-8")
            assert "--commit" in text, f"{doc.name} 缺少 --commit 语义说明"

    def test_script_usage_header_mentions_commit(self):
        head = "\n".join(
            SCRIPT.read_text(encoding="utf-8").splitlines()[:10]
        )
        assert "--commit" in head
        assert "--probe" in head
