"""文档配置示例与 config.example.jsonc 单源对齐的防漂移守护（U-04）。

README 与 quick-start 的手动配置示例块用 <!-- CONFIG_EXAMPLE:BEGIN/END --> 标记包裹，
本文件用与运行时完全同一的解析链（_strip_jsonc_comments + json.loads / Config.load_config）
断言：示例可解析、字段集 ⊆ 模板、无幽灵字段、照抄即可启动。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from mobile.config import Config, ConfigError, _strip_jsonc_comments

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"
QUICK_START = REPO / "docs" / "quick-start.md"
DOCS = [README, QUICK_START]
CANONICAL = REPO / "mobile" / "config.example.jsonc"
SCRIPT = REPO / "mobile" / "scripts" / "start_ticket_grabbing.sh"

# 幽灵字段全集：config.py Config.__init__ 的 deprecated-ignored 形参 + 全仓无消费方的 item_url
GHOST_FIELDS = {
    "udid",
    "item_url",
    "app_package",
    "app_activity",
    "platform_version",
    "driver_backend",
    "server_url",
    "device_name",
}

BLOCK_RE = re.compile(
    r"<!--\s*CONFIG_EXAMPLE:BEGIN\s*-->\s*```jsonc\s*\n(.*?)```\s*<!--\s*CONFIG_EXAMPLE:END\s*-->",
    re.DOTALL,
)


def _extract_block_text(doc_path: Path) -> str:
    matches = BLOCK_RE.findall(doc_path.read_text(encoding="utf-8"))
    assert len(matches) == 1, (
        f"{doc_path.name} 应恰好包含 1 个 CONFIG_EXAMPLE 标记块，实际 {len(matches)} 个"
    )
    return matches[0]


def _parse_jsonc(text: str) -> dict:
    return json.loads(_strip_jsonc_comments(text))


def _canonical_dict() -> dict:
    return _parse_jsonc(CANONICAL.read_text(encoding="utf-8"))


@pytest.mark.parametrize("doc_path", DOCS, ids=lambda p: p.name)
class TestDocConfigExample:
    def test_doc_has_exactly_one_config_example_block(self, doc_path):
        _extract_block_text(doc_path)  # 内含 len==1 断言

    def test_doc_example_parses_without_trailing_comma(self, doc_path):
        # 与运行时同一解析链：尾逗号回归（原 README:183 问题）在此直接红
        example = _parse_jsonc(_extract_block_text(doc_path))
        assert isinstance(example, dict)

    def test_doc_example_fields_subset_of_canonical(self, doc_path):
        example = _parse_jsonc(_extract_block_text(doc_path))
        assert set(example) <= set(_canonical_dict())

    def test_doc_example_has_no_ghost_fields(self, doc_path):
        example = _parse_jsonc(_extract_block_text(doc_path))
        assert not (set(example) & GHOST_FIELDS)

    def test_doc_example_keyword_is_nonempty_string(self, doc_path):
        # 杀死 keyword:null 回归（config.py 启动即崩的根因）
        example = _parse_jsonc(_extract_block_text(doc_path))
        assert isinstance(example.get("keyword"), str)
        assert example["keyword"].strip()

    def test_doc_example_covers_required_keys(self, doc_path):
        example = _parse_jsonc(_extract_block_text(doc_path))
        required = {
            "serial",
            "keyword",
            "users",
            "city",
            "date",
            "price",
            "price_index",
            "probe_only",
            "if_commit_order",
            "auto_navigate",
        }
        assert required <= set(example)

    def test_doc_example_verbatim_loads_via_load_config(self, doc_path, tmp_path):
        # AC-2 机器化：块内 jsonc 原文（含注释）原样写入文件后 schema 有效可加载。
        # 示例含"你的设备序列号"等占位符，U-05 的占位符黑名单默认（strict）会正确
        # 拒载——这里关掉 strict 只验证 schema/解析链，占位符拦截由下面的用例锁定。
        raw = _extract_block_text(doc_path)
        cfg_path = tmp_path / "config.jsonc"
        cfg_path.write_text(raw, encoding="utf-8")
        Config.load_config(str(cfg_path), strict_placeholders=False)

    def test_doc_example_placeholders_rejected_by_default(self, doc_path, tmp_path):
        # U-04×U-05 集成契约：照抄示例不改占位符时，默认 strict 加载必须给中文报错
        raw = _extract_block_text(doc_path)
        cfg_path = tmp_path / "config.jsonc"
        cfg_path.write_text(raw, encoding="utf-8")
        with pytest.raises(ConfigError, match="占位符"):
            Config.load_config(str(cfg_path))

    def test_doc_example_defaults_are_safe(self, doc_path, tmp_path):
        # 文档教的默认姿势必须是安全探测
        raw = _extract_block_text(doc_path)
        cfg_path = tmp_path / "config.jsonc"
        cfg_path.write_text(raw, encoding="utf-8")
        cfg = Config.load_config(str(cfg_path), strict_placeholders=False)
        assert cfg.probe_only is True
        assert cfg.if_commit_order is False


class TestDocsSingleSource:
    def test_both_docs_example_blocks_are_identical(self):
        assert _extract_block_text(README) == _extract_block_text(QUICK_START)

    def test_readme_cp_command_precedes_example(self):
        text = README.read_text(encoding="utf-8")
        i_heading = text.index("### 3.2 手动配置")
        i_cp = text.index("cp mobile/config.example.jsonc mobile/config.jsonc")
        i_block = text.index("<!-- CONFIG_EXAMPLE:BEGIN -->")
        assert i_heading < i_cp < i_block

    def test_readme_no_dead_config_link(self):
        text = README.read_text(encoding="utf-8")
        assert "](./mobile/config.jsonc)" not in text
        assert "](mobile/config.jsonc)" not in text

    def test_docs_no_ghost_wording(self):
        # 仅限这两份文档；docs/mobile-ticket-logic.md 等不在本 AC 内
        for doc in DOCS:
            text = doc.read_text(encoding="utf-8")
            assert "udid" not in text, f"{doc.name} 仍含 udid"
            assert "item_url" not in text, f"{doc.name} 仍含 item_url"


class TestScriptGuards:
    def test_script_no_item_url_hint(self):
        assert "item_url" not in SCRIPT.read_text(encoding="utf-8")

    @pytest.mark.skipif(sys.platform == "win32", reason="Bash syntax check requires a Unix shell")
    def test_script_bash_syntax_ok(self):
        r = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=30
        )
        assert r.returncode == 0, r.stderr

    def test_script_echo_numbering_intact(self):
        # 只改第 3 条文本、不重排序号：1-4 各恰好 1 次，第 5 条在 probe/commit 分支各 1 次
        content = SCRIPT.read_text(encoding="utf-8")
        for n in range(1, 5):
            assert content.count(f'echo "   {n}.') == 1
        assert content.count('echo "   5.') == 2


class TestCanonicalExample:
    def test_example_config_has_cta_field_with_comment(self):
        parsed = _canonical_dict()
        assert "wait_cta_ready_timeout_ms" in parsed
        # 与 config.py 代码默认值一致：纯文档性新增、运行时零差异
        assert parsed["wait_cta_ready_timeout_ms"] == 0
        # 该键所在行之前相邻 3 行内有用途注释（防止字段只出现在注释里的假绿）
        lines = CANONICAL.read_text(encoding="utf-8").splitlines()
        idx = next(
            i for i, line in enumerate(lines)
            if '"wait_cta_ready_timeout_ms"' in line and not line.strip().startswith("//")
        )
        window = lines[max(0, idx - 3): idx]
        assert any(
            line.strip().startswith("//") and ("CTA" in line or "立即购票" in line)
            for line in window
        )

    def test_canonical_example_loads_via_load_config(self, tmp_path):
        # cp 模板工作流对新用户永远可用（schema 层面；占位符须填真实值才过 strict）
        cfg_path = tmp_path / "config.jsonc"
        cfg_path.write_text(CANONICAL.read_text(encoding="utf-8"), encoding="utf-8")
        Config.load_config(str(cfg_path), strict_placeholders=False)

    def test_canonical_example_placeholders_rejected_by_default(self, tmp_path):
        # U-04×U-05 集成契约：直接 cp 模板不改占位符 → 默认 strict 加载中文报错
        cfg_path = tmp_path / "config.jsonc"
        cfg_path.write_text(CANONICAL.read_text(encoding="utf-8"), encoding="utf-8")
        with pytest.raises(ConfigError, match="占位符"):
            Config.load_config(str(cfg_path))

    def test_legacy_config_with_ghost_fields_still_loads(self, tmp_path):
        # backward_compat：老配置残留幽灵字段仍可加载（load_config 白名单取值 + deprecated 形参吞掉）
        legacy = {
            "udid": "OLD-SERIAL",
            "item_url": "https://m.damai.cn/shows/item.html?itemId=123",
            "app_package": "cn.damai",
            "app_activity": ".launcher.splash.SplashMainActivity",
            "platform_version": "13",
            "keyword": "张杰 演唱会",
            "users": ["张三"],
            "city": "上海",
            "date": "2026-08-01",
            "price": "480元",
            "price_index": 0,
            "probe_only": True,
            "if_commit_order": False,
            "auto_navigate": True,
        }
        cfg_path = tmp_path / "config.jsonc"
        cfg_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        Config.load_config(str(cfg_path))


def _github_slug(heading: str) -> str:
    """近似 GitHub heading slugger：小写、去标点（保留 CJK/字母/数字/连字符）、空格转连字符。"""
    slug = heading.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)  # Python 的 \w 含 CJK
    slug = re.sub(r"\s+", "-", slug)
    return slug


@pytest.mark.parametrize("doc_path", DOCS, ids=lambda p: p.name)
class TestAnchors:
    def test_internal_anchor_links_resolve(self, doc_path):
        # AC-4 最大可机器化程度（中文 slug 规则存在实现差异，终审靠 GitHub 渲染人工点击）
        text = doc_path.read_text(encoding="utf-8")
        # 排除代码围栏内的文本
        prose = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        headings = re.findall(r"^#{1,6} +(.+)$", prose, flags=re.MULTILINE)
        slugs = {_github_slug(h) for h in headings}
        anchors = re.findall(r"\]\(#([^)]+)\)", prose)
        for anchor in anchors:
            assert anchor in slugs, f"{doc_path.name} 锚点 #{anchor} 无对应标题"

    def test_no_ghost_step_five(self, doc_path):
        assert "第 5 步" not in doc_path.read_text(encoding="utf-8")
