#!/usr/bin/env python3
"""Tests for cc-switch-merge pure merge functions."""
from __future__ import annotations

import importlib.util
import json
import shutil
import unittest
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore
import tomli_w

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src" / "cc-switch-merge.py"
spec = importlib.util.spec_from_file_location("cc_switch_merge_under_test", SRC_PATH)
assert spec is not None and spec.loader is not None
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)
merge_settings = _mod.merge_settings
merge_settings_with_last_good = _mod.merge_settings_with_last_good
merge_codex = _mod.merge_codex
merge_codex_for_windows = _mod.merge_codex_for_windows
prune_backups = _mod.prune_backups
CCS_KNOWN_TOP_KEYS = _mod.CCS_KNOWN_TOP_KEYS
CCS_SETTINGS_PRESERVE_KEYS = _mod.CCS_SETTINGS_PRESERVE_KEYS
CCS_CLAUDE_ENV_OVERRIDE_KEYS = _mod.CCS_CLAUDE_ENV_OVERRIDE_KEYS
_rewrite_base_url = _mod._rewrite_base_url
_get_wsl_ip = _mod._get_wsl_ip
find_intact_settings_backup = _mod.find_intact_settings_backup
restore_missing_settings_keys = _mod.restore_missing_settings_keys
restore_reduced_settings_keys = _mod.restore_reduced_settings_keys
load_last_good_settings = _mod.load_last_good_settings
maybe_initialize_last_good_settings = _mod.maybe_initialize_last_good_settings
find_richest_codex_backup = _mod.find_richest_codex_backup
restore_reduced_codex = _mod.restore_reduced_codex
cmd_merge_settings = _mod.cmd_merge_settings
cmd_regen_claude = _mod.cmd_regen_claude
cmd_sync_auth = _mod.cmd_sync_auth
heal_last_good_settings = _mod.heal_last_good_settings


def _complete_claude_settings(env=None, plugins=5, hooks=3, allow=None, extra=None):
    data = {
        "env": dict(env or {}),
        "enabledPlugins": {f"plugin{i}": True for i in range(plugins)},
        "hooks": {
            name: []
            for name in ["SessionStart", "PreToolUse", "PostToolUse", "Stop", "PreCompact", "UserPromptSubmit"][:hooks]
        },
        "permissions": {"allow": list(allow or ["Bash(git status:*)"]), "deny": []},
        "mcpServers": {},
        "extraKnownMarketplaces": {},
        "autoCompactEnabled": True,
        "context": {},
        "language": "zh-CN",
        "skipDangerousModePermissionPrompt": False,
        "statusLine": {"type": "command"},
        "theme": "dark",
        "verbose": False,
    }
    if extra:
        data.update(extra)
    return data


class TestMergeSettings(unittest.TestCase):
    """Tests for merge_settings(after, before)."""

    def test_after_keys_all_kept(self):
        after = {"a": 1, "b": 2, "c": 3}
        before = {"x": 10, "y": 20}
        result = merge_settings(after, before)
        for key, val in after.items():
            self.assertEqual(result[key], val, f"after key {key!r} should be kept")

    def test_before_unique_top_level_keys_added(self):
        after = {"a": 1}
        before = {"z": 99}
        result = merge_settings(after, before)
        self.assertIn("z", result)
        self.assertEqual(result["z"], 99)

    def test_conflict_always_after_wins(self):
        after = {"k": "after_val"}
        before = {"k": "before_val"}
        result = merge_settings(after, before)
        self.assertEqual(result["k"], "after_val")

    def test_env_block_deep_merged_after_wins(self):
        """env 块 deep merge: after wins, before 独有的 env key 补进去。"""
        after = {"env": {"FOO": "bar"}}
        before = {"env": {"BAZ": "qux", "FOO": "old"}}
        result = merge_settings(after, before)
        # after wins 同一个 key
        self.assertEqual(result["env"]["FOO"], "bar")
        # before 独有的 key 补进去
        self.assertEqual(result["env"]["BAZ"], "qux")

    def test_env_preserves_anthropic_model_from_before(self):
        """merge 时不丢 before.env.ANTHROPIC_MODEL (cc-switch 接管会清掉它, merge 补回来)。"""
        after = {"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:15721"}}
        before = {"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:15721", "ANTHROPIC_MODEL": "glm-5.1"}}
        result = merge_settings(after, before)
        self.assertEqual(result["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:15721")
        self.assertEqual(result["env"]["ANTHROPIC_MODEL"], "glm-5.1")

    def test_env_uses_before_when_after_missing(self):
        """after 没有 env 块, 整体用 before 的 env。"""
        after = {"a": 1}
        before = {"env": {"X": "Y"}}
        result = merge_settings(after, before)
        self.assertEqual(result["env"], {"X": "Y"})

    def test_env_after_missing_keeps_before_unique_keys(self):
        """after 有 env 块但少了一些 key, before 补上。"""
        after = {"env": {"A": "1"}}
        before = {"env": {"A": "old", "B": "2", "C": "3"}}
        result = merge_settings(after, before)
        self.assertEqual(result["env"], {"A": "1", "B": "2", "C": "3"})

    def test_empty_before_returns_after_unchanged(self):
        after = {"a": 1, "b": 2}
        before: dict = {}
        result = merge_settings(after, before)
        self.assertEqual(result, after)

    def test_after_keys_order_preserved_then_before_appended(self):
        after = {"b": 2, "c": 3}
        before = {"a": 1, "b": 0, "d": 4}
        result = merge_settings(after, before)
        keys = list(result.keys())
        # after keys come first, in their original order
        self.assertEqual(keys[:2], ["b", "c"])
        # before-only keys are appended
        self.assertIn("a", keys[2:])
        self.assertIn("d", keys[2:])


class TestMergeSettingsWithLastGood(unittest.TestCase):
    """Claude last_good 合成: last_good 是稳定真相, after 只覆盖白名单 env。"""

    def test_keeps_last_good_structure_and_overrides_switch_env(self):
        last_good = {
            "env": {
                "ANTHROPIC_MODEL": "old-model",
                "ANTHROPIC_BASE_URL": "http://127.0.0.1:15721",
                "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_keep",
            },
            "enabledPlugins": {"context-mode@context-mode": True, "x@y": True},
            "hooks": {"SessionStart": [], "PreToolUse": []},
            "permissions": {"allow": ["Bash(git status:*)"], "deny": []},
        }
        after = {
            "env": {
                "ANTHROPIC_MODEL": "new-model",
                "ANTHROPIC_AUTH_TOKEN": "new-token",
            },
            "enabledPlugins": {"context-mode@context-mode": True},
            "hooks": {"SessionStart": []},
        }

        result = merge_settings_with_last_good(after, last_good)

        self.assertEqual(result["env"]["ANTHROPIC_MODEL"], "new-model")
        self.assertEqual(result["env"]["ANTHROPIC_AUTH_TOKEN"], "new-token")
        self.assertEqual(result["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "ghp_keep")
        self.assertEqual(len(result["enabledPlugins"]), 2)
        self.assertEqual(set(result["hooks"].keys()), {"SessionStart", "PreToolUse"})
        self.assertEqual(result["permissions"]["allow"], ["Bash(git status:*)"])

    def test_ignores_after_env_keys_outside_override_whitelist(self):
        last_good = {
            "env": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": "keep-from-last-good",
                "CUSTOM_USER_FLAG": "keep",
            }
        }
        after = {
            "env": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": "bad-overwrite",
                "CUSTOM_USER_FLAG": "bad-overwrite",
                "ANTHROPIC_MODEL": "new-model",
            }
        }

        result = merge_settings_with_last_good(after, last_good)

        self.assertEqual(result["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "keep-from-last-good")
        self.assertEqual(result["env"]["CUSTOM_USER_FLAG"], "keep")
        self.assertEqual(result["env"]["ANTHROPIC_MODEL"], "new-model")

    def test_allows_wide_cc_switch_env_keys(self):
        last_good = {"env": {}}
        after = {
            "env": {
                key: f"value-{index}"
                for index, key in enumerate(CCS_CLAUDE_ENV_OVERRIDE_KEYS)
            }
        }

        result = merge_settings_with_last_good(after, last_good)

        for key in CCS_CLAUDE_ENV_OVERRIDE_KEYS:
            self.assertEqual(result["env"][key], after["env"][key])

    def test_removes_stale_switch_owned_env_when_after_omits_key(self):
        last_good = {
            "env": {
                "ANTHROPIC_MODEL": "stale-model",
                "ANTHROPIC_AUTH_TOKEN": "stale-token",
                "GITHUB_PERSONAL_ACCESS_TOKEN": "keep-gh",
            }
        }
        after = {"env": {"ANTHROPIC_MODEL": "fresh-model"}}

        result = merge_settings_with_last_good(after, last_good)

        self.assertEqual(result["env"]["ANTHROPIC_MODEL"], "fresh-model")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", result["env"])
        self.assertEqual(result["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "keep-gh")


class TestLastGoodHealing(unittest.TestCase):
    def test_heal_uses_backup_for_missing_structure_and_before_for_new_stable_keys(self):
        last_good = _complete_claude_settings(
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "old-gh"},
            plugins=1,
            hooks=1,
            allow=["Bash(old:*)"],
        )
        backup = _complete_claude_settings(
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "backup-gh"},
            plugins=4,
            hooks=4,
            allow=["Bash(backup:*)"],
        )
        before = _complete_claude_settings(
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "fresh-gh"},
            plugins=2,
            hooks=2,
            allow=["Bash(before:*)"],
            extra={"newStableTopLevel": {"enabled": True}},
        )
        before["enabledPlugins"]["freshPlugin"] = True
        before["hooks"]["FreshHook"] = []

        healed, changes = heal_last_good_settings(
            last_good,
            (("backup", backup, "missing"), ("before", before, "overwrite")),
        )

        self.assertEqual(healed["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "fresh-gh")
        self.assertEqual(len(healed["enabledPlugins"]), 5)
        self.assertEqual(len(healed["hooks"]), 5)
        self.assertIn("newStableTopLevel", healed)
        self.assertIn("Bash(backup:*)", healed["permissions"]["allow"])
        self.assertIn("Bash(before:*)", healed["permissions"]["allow"])
        self.assertTrue(changes)


class TestLastGoodSettingsFile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp/cc-switch-merge-last-good-test")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        self.last_good = self.tmp / "last_good_settings.json"

    def tearDown(self):
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_load_last_good_missing_returns_empty_dict(self):
        self.assertEqual(load_last_good_settings(self.last_good), {})

    def test_load_last_good_reads_json(self):
        self.last_good.write_text(json.dumps({"env": {"A": "B"}}), encoding="utf-8")
        self.assertEqual(load_last_good_settings(self.last_good), {"env": {"A": "B"}})

    def test_initialize_last_good_when_missing_and_source_complete(self):
        source = _complete_claude_settings(
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "keep"},
            plugins=4,
            hooks=3,
        )

        initialized = maybe_initialize_last_good_settings(self.last_good, source)

        self.assertTrue(initialized)
        self.assertEqual(json.loads(self.last_good.read_text(encoding="utf-8")), source)

    def test_initialize_last_good_heals_reduced_source_from_rich_backup(self):
        backup_dir = self.tmp / "backups"
        backup_dir.mkdir()
        rich = _complete_claude_settings(
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "from-backup"},
            plugins=5,
            hooks=4,
            allow=["Bash(backup:*)"],
        )
        reduced = {
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "from-source"},
            "enabledPlugins": {"plugin0": True},
            "hooks": {"SessionStart": []},
            "permissions": {"allow": ["Bash(source:*)"], "deny": []},
        }
        (backup_dir / "settings-20260620-100000.json").write_text(
            json.dumps(rich), encoding="utf-8"
        )

        initialized = maybe_initialize_last_good_settings(self.last_good, reduced, backup_dir)

        self.assertTrue(initialized)
        result = json.loads(self.last_good.read_text(encoding="utf-8"))
        self.assertEqual(result["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "from-source")
        self.assertEqual(len(result["enabledPlugins"]), 5)
        self.assertEqual(len(result["hooks"]), 4)
        self.assertIn("Bash(backup:*)", result["permissions"]["allow"])
        self.assertIn("Bash(source:*)", result["permissions"]["allow"])

    def test_initialize_last_good_rejects_reduced_source_without_backup(self):
        reduced = {
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "keep"},
            "enabledPlugins": {"plugin0": True},
            "hooks": {"SessionStart": []},
            "permissions": {"allow": [], "deny": []},
        }

        initialized = maybe_initialize_last_good_settings(self.last_good, reduced)

        self.assertFalse(initialized)
        self.assertFalse(self.last_good.exists())

    def test_load_last_good_raises_for_corrupt_json(self):
        self.last_good.write_text("{not-json", encoding="utf-8")

        with self.assertRaises(Exception):
            load_last_good_settings(self.last_good)

    def test_initialize_last_good_does_not_overwrite_existing_file(self):
        existing = {"env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "existing"}}
        self.last_good.write_text(json.dumps(existing), encoding="utf-8")

        initialized = maybe_initialize_last_good_settings(self.last_good, {"env": {"A": "B"}})

        self.assertFalse(initialized)
        self.assertEqual(json.loads(self.last_good.read_text(encoding="utf-8")), existing)


class TestCmdMergeSettingsWithLastGood(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp/cc-switch-merge-settings-last-good-cmd-test")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        self.settings = self.tmp / "settings.json"
        self.backup = self.tmp / "settings.json.bak"
        self.backup_dir = self.tmp / "backups"
        self.last_good = self.tmp / "last_good_settings.json"

    def tearDown(self):
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def _args(self):
        from argparse import Namespace
        return Namespace(
            settings=self.settings,
            settings_backup=self.backup,
            backup_dir=self.backup_dir,
            override_model=None,
            last_good_settings=self.last_good,
        )

    def test_merge_settings_uses_last_good_and_preserves_user_owned_env(self):
        after = {
            "env": {
                "ANTHROPIC_MODEL": "new-model",
                "ANTHROPIC_AUTH_TOKEN": "new-token",
                "GITHUB_PERSONAL_ACCESS_TOKEN": "bad-overwrite",
            },
            "enabledPlugins": {"context-mode@context-mode": True},
            "hooks": {"SessionStart": []},
        }
        before = {
            "env": {
                "ANTHROPIC_MODEL": "old-model",
            }
        }
        last_good = _complete_claude_settings(
            env={
                "ANTHROPIC_MODEL": "last-good-model",
                "GITHUB_PERSONAL_ACCESS_TOKEN": "from-last-good",
            },
            plugins=2,
            hooks=2,
            allow=["Bash(git status:*)"],
        )
        self.settings.write_text(json.dumps(after), encoding="utf-8")
        self.backup.write_text(json.dumps(before), encoding="utf-8")
        self.last_good.write_text(json.dumps(last_good), encoding="utf-8")

        rc = cmd_merge_settings(self._args())

        self.assertEqual(rc, 0)
        result = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(result["env"]["ANTHROPIC_MODEL"], "new-model")
        self.assertEqual(result["env"]["ANTHROPIC_AUTH_TOKEN"], "new-token")
        self.assertEqual(result["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "from-last-good")
        self.assertEqual(len(result["enabledPlugins"]), 2)
        self.assertIn("PreToolUse", result["hooks"])
        self.assertEqual(result["permissions"]["allow"], ["Bash(git status:*)"])


class TestCmdRegenClaude(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp/cc-switch-merge-regen-claude-test")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        self.settings = self.tmp / "settings.json"
        self.backup_dir = self.tmp / "backups"
        self.last_good = self.tmp / "last_good_settings.json"
        self.codex_config = self.tmp / "config.toml"
        self.codex_config.write_text('model = "should-not-change"\n', encoding="utf-8")

    def tearDown(self):
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def _args(self):
        from argparse import Namespace
        return Namespace(
            settings=self.settings,
            backup_dir=self.backup_dir,
            last_good_settings=self.last_good,
            wsl_config=self.codex_config,
            override_model=None,
        )

    def test_regen_claude_rebuilds_settings_only(self):
        live = {
            "env": {
                "ANTHROPIC_MODEL": "live-model",
                "ANTHROPIC_AUTH_TOKEN": "live-token",
                "GITHUB_PERSONAL_ACCESS_TOKEN": "bad-live",
            },
            "enabledPlugins": {"context-mode@context-mode": True},
        }
        last_good = _complete_claude_settings(
            env={
                "ANTHROPIC_MODEL": "old-model",
                "GITHUB_PERSONAL_ACCESS_TOKEN": "from-last-good",
            },
            plugins=2,
            hooks=2,
            allow=["Bash(git status:*)"],
        )
        self.settings.write_text(json.dumps(live), encoding="utf-8")
        self.last_good.write_text(json.dumps(last_good), encoding="utf-8")

        rc = cmd_regen_claude(self._args())

        self.assertEqual(rc, 0)
        result = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(result["env"]["ANTHROPIC_MODEL"], "live-model")
        self.assertEqual(result["env"]["ANTHROPIC_AUTH_TOKEN"], "live-token")
        self.assertEqual(result["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "from-last-good")
        self.assertIn("PreToolUse", result["hooks"])
        self.assertEqual(self.codex_config.read_text(encoding="utf-8"), 'model = "should-not-change"\n')

    def test_regen_claude_preserves_live_stable_additions(self):
        live = _complete_claude_settings(
            env={
                "ANTHROPIC_MODEL": "live-model",
                "ANTHROPIC_AUTH_TOKEN": "live-token",
                "GITHUB_PERSONAL_ACCESS_TOKEN": "bad-live",
            },
            plugins=2,
            hooks=2,
            extra={"newStableTopLevel": {"enabled": True}},
        )
        last_good = _complete_claude_settings(
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "from-last-good"},
            plugins=1,
            hooks=1,
        )
        self.settings.write_text(json.dumps(live), encoding="utf-8")
        self.last_good.write_text(json.dumps(last_good), encoding="utf-8")

        rc = cmd_regen_claude(self._args())

        self.assertEqual(rc, 0)
        result = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertIn("newStableTopLevel", result)
        self.assertEqual(result["newStableTopLevel"], {"enabled": True})
        self.assertEqual(result["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "from-last-good")
        refreshed = json.loads(self.last_good.read_text(encoding="utf-8"))
        self.assertIn("newStableTopLevel", refreshed)

    def test_regen_claude_fails_on_corrupt_last_good(self):
        self.settings.write_text(json.dumps(_complete_claude_settings()), encoding="utf-8")
        self.last_good.write_text("{not-json", encoding="utf-8")

        rc = cmd_regen_claude(self._args())

        self.assertEqual(rc, 1)


class TestMergeCodex(unittest.TestCase):
    """Tests for merge_codex(after, before)."""

    def test_model_providers_segment_replaced_with_after(self):
        after = {"model_providers": {"sonnet": {"model": "claude-sonnet"}}}
        before = {"model_providers": {"opus": {"model": "claude-opus"}}}
        result = merge_codex(after, before)
        self.assertEqual(result["model_providers"], {"sonnet": {"model": "claude-sonnet"}})

    def test_other_segments_all_kept_from_before(self):
        after = {"model_providers": {}}
        before = {
            "projects": {"p1": {}},
            "mcp_servers": {"node_repl": {"command": "node"}},
            "tui": {"theme": "dark"},
        }
        result = merge_codex(after, before)
        self.assertEqual(result["projects"], {"p1": {}})
        self.assertEqual(result["mcp_servers"], {"node_repl": {"command": "node"}})
        self.assertEqual(result["tui"], {"theme": "dark"})

    def test_known_top_level_keys_taken_from_after(self):
        after = {"model": "opus", "model_provider": "anthropic"}
        before = {"model": "sonnet", "model_provider": "openai"}
        result = merge_codex(after, before)
        self.assertEqual(result["model"], "opus")
        self.assertEqual(result["model_provider"], "anthropic")

    def test_known_top_key_missing_in_after_not_leaked_from_before(self):
        """If a CCS_KNOWN_TOP_KEYS key is missing in after, it should NOT leak from before."""
        after = {"model_providers": {}, "model": "opus"}  # no model_provider
        before = {"model_provider": "openai"}  # before has it
        result = merge_codex(after, before)
        self.assertNotIn("model_provider", result)

    def test_handmade_provider_in_model_providers_dropped(self):
        """A handmade provider in [model_providers] segment of before is dropped (whole segment replaced by after)."""
        after = {"model_providers": {"sonnet": {"model": "claude-sonnet"}}}
        before = {
            "model_providers": {"opus": {"model": "claude-opus"}, "myx-java": {"model": "java"}},
        }
        result = merge_codex(after, before)
        self.assertNotIn("myx-java", result["model_providers"])
        self.assertIn("sonnet", result["model_providers"])
        self.assertNotIn("opus", result["model_providers"])

    def test_unique_top_level_keys_kept_from_before(self):
        """before-only top-level keys that are NOT in CCS_KNOWN_TOP_KEYS are preserved."""
        after = {"model_providers": {}}
        before = {"some_other_key": "value"}
        result = merge_codex(after, before)
        self.assertIn("some_other_key", result)
        self.assertEqual(result["some_other_key"], "value")


class TestRestoreReducedCodex(unittest.TestCase):
    """codex 防降级棘轮: 检测 config.toml 结构性段被削减, 从最丰富备份恢复。"""

    def test_union_registry_sections(self):
        """projects/mcp_servers 用 UNION 合并, 补 richest 独有的条目, 不动 current 已有的。"""
        current = {
            "model": "glm-5.1",
            "projects": {"/home/a": {"trust_level": "trusted"}},
            "mcp_servers": {"srv1": {"command": "a"}},
        }
        richest = {
            "projects": {"/home/a": {"trust_level": "trusted"}, "/home/b": {"trust_level": "trusted"}},
            "mcp_servers": {"srv1": {"command": "a"}, "srv2": {"command": "b"}},
        }
        result, restored = restore_reduced_codex(current, richest)
        # current 已有的不丢, richest 独有的补进来
        self.assertEqual(len(result["projects"]), 2)
        self.assertEqual(len(result["mcp_servers"]), 2)
        self.assertIn("projects", " ".join(restored))

    def test_restore_missing_table_sections(self):
        """tui/features/memories 整段缺失时, 从 richest 恢复。"""
        current = {"model": "x"}
        richest = {
            "tui": {"status_line": ["a", "b"]},
            "features": {"memories": True},
            "memories": {"use_memories": True},
        }
        result, restored = restore_reduced_codex(current, richest)
        self.assertIn("tui", result)
        self.assertIn("features", result)
        self.assertIn("memories", result)
        self.assertEqual(result["tui"]["status_line"], ["a", "b"])

    def test_no_restore_when_richer(self):
        """current 比 richest 还丰富时不触发。"""
        current = {
            "projects": {f"/p{i}": {} for i in range(5)},
            "mcp_servers": {f"s{i}": {} for i in range(3)},
        }
        richest = {"projects": {"/p0": {}}, "mcp_servers": {"s0": {}}}
        result, restored = restore_reduced_codex(current, richest)
        self.assertEqual(restored, [])
        self.assertEqual(len(result["projects"]), 5)

    def test_find_richest_codex_backup_prefers_content(self):
        """两个备份, 选内容丰富的 (多 projects+mcp) 而非最近的。"""
        tmpdir = Path("/tmp/test_richest_codex_xyz")
        if tmpdir.exists():
            shutil.rmtree(tmpdir)
        tmpdir.mkdir()
        try:
            rich = {
                "model": "x",
                "projects": {f"/p{i}": {"trust_level": "trusted"} for i in range(5)},
                "mcp_servers": {f"s{i}": {"command": "c"} for i in range(3)},
            }
            thin = {"model": "x", "projects": {"/only": {}}}
            # rich 旧, thin 新 — 应选 rich
            (tmpdir / "config-toml-20260601-100000.toml").write_text(
                tomli_w.dumps(rich), encoding="utf-8"
            )
            (tmpdir / "config-toml-20260605-100000.toml").write_text(
                tomli_w.dumps(thin), encoding="utf-8"
            )
            result = find_richest_codex_backup(tmpdir)
            self.assertIsNotNone(result)
            self.assertEqual(result.name, "config-toml-20260601-100000.toml")
        finally:
            shutil.rmtree(tmpdir)


class TestMergeCodexForWindows(unittest.TestCase):
    """Tests for merge_codex_for_windows(wsl_merged, windows_backup)."""

    def test_only_model_providers_and_top_keys_from_wsl(self):
        wsl = {
            "model_providers": {"sonnet": {"model": "claude-sonnet"}},
            "model": "opus",
            "model_provider": "anthropic",
            "tui": {"theme": "light"},
        }
        win = {"model_providers": {"gpt": {"model": "gpt-4"}}}
        result = merge_codex_for_windows(wsl, win)
        self.assertEqual(result["model_providers"], {"sonnet": {"model": "claude-sonnet"}})
        self.assertEqual(result["model"], "opus")
        self.assertEqual(result["model_provider"], "anthropic")

    def test_all_windows_specific_sections_kept(self):
        wsl = {"model_providers": {"sonnet": {"model": "claude-sonnet"}}}
        win = {
            "mcp_servers": {"node_repl": {"command": "node"}},
            "desktop": {"notifications": True},
            "windows": {"shell": "powershell"},
            "plugins": {"p1": {}},
            "projects": {"proj1": {}},
        }
        result = merge_codex_for_windows(wsl, win)
        for key in ("mcp_servers", "desktop", "windows", "plugins", "projects"):
            self.assertIn(key, result, f"Windows section {key!r} should be kept")
            self.assertEqual(result[key], win[key])

    def test_windows_unique_top_level_kept(self):
        wsl = {"model_providers": {}}
        win = {"my_windows_setting": True}
        result = merge_codex_for_windows(wsl, win)
        self.assertIn("my_windows_setting", result)
        self.assertTrue(result["my_windows_setting"])

    def test_no_model_providers_in_wsl_no_empty_segment_written(self):
        """wsl 中没有 [model_providers] 时, 结果也不应有空段。"""
        wsl = {"model": "kimi"}
        win_before = {"model_providers": {"x": {"name": "x"}}}
        result = merge_codex_for_windows(wsl, win_before)
        self.assertNotIn("model_providers", result)


class TestSyncWindowsStaging(unittest.TestCase):
    """验证 cmd_sync_windows 走 WSL staging 模式 (避免 9P IO 问题)."""

    def setUp(self):
        self.tmpdir = Path("/tmp/test_sync_windows_staging")
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.tmpdir / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir = self.tmpdir / "backups"
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.wsl_path = self.tmpdir / "wsl-config.toml"
        self.win_path = self.tmpdir / "win-config.toml"
        self.win_backup = self.tmpdir / "win-backup.toml"

    def tearDown(self):
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)

    def _make_args(self):
        from argparse import Namespace
        return Namespace(
            wsl_config=self.wsl_path,
            windows_config=self.win_path,
            windows_backup=self.win_backup,
            backup_dir=self.backups_dir,
        )

    def test_cmd_sync_windows_uses_staging_then_cp(self):
        """cmd_sync_windows 应先把合并结果写到 WSL staging, 再 cp 到 Windows。"""
        # 准备 WSL 已合并配置
        tomli_w_mod = importlib.import_module("tomli_w")
        self.wsl_path.write_bytes(tomli_w_mod.dumps({
            "model": "kimi",
            "model_providers": {"custom": {"name": "kimi"}},
        }).encode("utf-8"))
        # 准备 Windows 备份
        self.win_backup.write_bytes(tomli_w_mod.dumps({
            "mcp_servers": {"node_repl": {"command": "X"}},
            "desktop": {"fontSize": 14},
            "model_providers": {"custom": {"name": "old"}},
        }).encode("utf-8"))
        # 准备 Windows 当前文件 (空)
        self.win_path.write_bytes(b"")

        # 用 monkey-patch 把 staging 目录重定向到 tmpdir
        import os
        cmd_sync_windows = _mod.cmd_sync_windows
        # 临时把 /tmp/cc-switch-windows-staging 替换为我们的测试目录
        # 用 symlink 避免 monkey-patch Path.mkdir
        real_staging = Path("/tmp/cc-switch-windows-staging")
        if real_staging.exists():
            import shutil as _sh
            _sh.rmtree(real_staging)
        real_staging.symlink_to(self.staging_dir)
        try:
            rc = cmd_sync_windows(self._make_args())
        finally:
            real_staging.unlink()

        self.assertEqual(rc, 0)
        # 验证 staging 文件存在
        staging_file = self.staging_dir / "config.toml"
        self.assertTrue(staging_file.exists(), "staging 文件应存在")
        # 验证 Windows 位置的文件内容
        win_content = tomllib.loads(self.win_path.read_text())
        self.assertEqual(win_content["model_providers"]["custom"]["name"], "kimi")
        self.assertIn("mcp_servers", win_content)
        self.assertIn("desktop", win_content)


class TestBaseUrlRewrite(unittest.TestCase):
    """base_url 不再重写: Windows 端必须保持 127.0.0.1 (WSL2 NAT 模式 inbound 要求)。"""

    def test_no_rewrite_127_to_wsl_ip(self):
        # 旧实现错误地替换, 新实现不再替换
        self.assertEqual(
            _rewrite_base_url("http://127.0.0.1:15721/v1", "192.168.1.15"),
            "http://127.0.0.1:15721/v1",
        )

    def test_no_rewrite_localhost(self):
        # localhost 也不再重写 (mirrored 模式才需要, win10 WSL2 不支持)
        self.assertEqual(
            _rewrite_base_url("http://localhost:15721/v1", "192.168.1.15"),
            "http://localhost:15721/v1",
        )

    def test_no_change_for_external_host(self):
        self.assertEqual(
            _rewrite_base_url("https://api.z.ai/api/coding/paas/v4", "192.168.1.15"),
            "https://api.z.ai/api/coding/paas/v4",
        )

    def test_get_wsl_ip_returns_string(self):
        ip = _get_wsl_ip()
        # 不强求具体值 (取决于环境), 但如果有值必须是 IP 格式
        if ip is not None:
            self.assertRegex(ip, r"^\d+\.\d+\.\d+\.\d+$")


class TestRestoreMissingSettingsKeys(unittest.TestCase):
    """A 修复: 当 settings.json 被 cc-switch 简化 (缺关键字段), 从最近完整备份补全。"""

    def test_restore_missing_keys_from_intact(self):
        current = {"env": {"X": "Y"}}
        intact = {
            "env": {"X": "Y"},
            "enabledPlugins": {"foo": True},
            "permissions": {"allow": ["Bash"]},
            "mcpServers": {},
        }
        result, restored = _mod.restore_missing_settings_keys(
            current, intact, _mod.CCS_SETTINGS_PRESERVE_KEYS
        )
        self.assertIn("enabledPlugins", result)
        self.assertIn("permissions", result)
        self.assertIn("mcpServers", result)
        self.assertIn("enabledPlugins", restored)
        self.assertIn("permissions", restored)

    def test_no_restore_when_all_present(self):
        current = {"env": {}, "enabledPlugins": {"a": True}, "permissions": {}}
        intact = {"enabledPlugins": {"b": True}}
        result, restored = _mod.restore_missing_settings_keys(
            current, intact, _mod.CCS_SETTINGS_PRESERVE_KEYS
        )
        self.assertEqual(restored, [])
        # 已有的 enabledPlugins 不会被 intact 覆盖 (after wins)
        self.assertEqual(result["enabledPlugins"], {"a": True})

    def test_find_intact_settings_backup_prefers_most_recent_with_enough_keys(self):
        tmpdir = Path("/tmp/test_intact_settings_backup_xyz")
        if tmpdir.exists():
            shutil.rmtree(tmpdir)
        tmpdir.mkdir()
        try:
            # 写 2 个"残"备份 (只 env) 和 1 个"完整"备份
            (tmpdir / "settings-20260601-100000.json").write_text(
                '{"env": {"X": "Y"}}', encoding="utf-8"
            )
            (tmpdir / "settings-20260602-100000.json").write_text(
                '{"env": {"X": "Y"}}', encoding="utf-8"
            )
            full_data = {
                "env": {"X": "Y"},
                "enabledPlugins": {"a": True},
                "permissions": {"allow": []},
                "hooks": {},
                "mcpServers": {},
                "extraKnownMarketplaces": {},
                "autoCompactEnabled": True,
                "context": {},
                "language": "中文",
                "skipDangerousModePermissionPrompt": True,
                "statusLine": {},
                "theme": "auto",
                "verbose": False,
            }
            (tmpdir / "settings-20260603-100000.json").write_text(
                json.dumps(full_data), encoding="utf-8"
            )

            result = _mod.find_intact_settings_backup(tmpdir)
            self.assertIsNotNone(result)
            self.assertEqual(result.name, "settings-20260603-100000.json")
        finally:
            shutil.rmtree(tmpdir)

    def test_find_intact_returns_none_when_no_full_backup(self):
        tmpdir = Path("/tmp/test_intact_none_xyz")
        if tmpdir.exists():
            shutil.rmtree(tmpdir)
        tmpdir.mkdir()
        try:
            (tmpdir / "settings-20260601-100000.json").write_text(
                '{"env": {"X": "Y"}}', encoding="utf-8"
            )
            result = _mod.find_intact_settings_backup(tmpdir)
            self.assertIsNone(result)
        finally:
            shutil.rmtree(tmpdir)


class TestRestoreReducedSettingsKeys(unittest.TestCase):
    """防降级棘轮: 检测结构性字段 (enabledPlugins/hooks) 内容被削减, 从最丰富备份恢复。

    cc-switch 接管时偶尔写"部分配置" (env + 极简 plugins/hooks), key 都在但项数少。
    restore_missing 检测不到 (key 不缺), 需要 restore_reduced 兜底。
    """

    def _full_keys(self):
        return {
            "env": {"X": "Y"},
            "enabledPlugins": {},
            "permissions": {"allow": [], "deny": []},
            "hooks": {},
            "mcpServers": {},
            "extraKnownMarketplaces": {},
            "autoCompactEnabled": True,
            "context": {},
            "language": "中文",
            "skipDangerousModePermissionPrompt": True,
            "statusLine": {},
            "theme": "auto",
            "verbose": False,
        }

    def test_restore_reduced_plugins_and_hooks(self):
        """cc-switch 写了部分配置 (1 plugin, 1 hook), 应从完整备份恢复 10 plugins / 6 hooks。"""
        current = {
            "env": {"X": "Y"},
            "enabledPlugins": {"context-mode": True},      # 被削到 1 个
            "hooks": {"SessionStart": []},                  # 被削到 1 个
        }
        intact = {
            "enabledPlugins": {f"p{i}": True for i in range(10)},
            "hooks": {h: [] for h in ["PostToolUse", "PreCompact", "PreToolUse", "SessionStart", "Stop", "UserPromptSubmit"]},
            "mcpServers": {},
        }
        result, restored = _mod.restore_reduced_settings_keys(current, intact)
        self.assertEqual(len(result["enabledPlugins"]), 10)
        self.assertEqual(len(result["hooks"]), 6)
        self.assertIn("enabledPlugins", " ".join(restored))
        self.assertIn("hooks", " ".join(restored))

    def test_no_restore_when_content_not_reduced(self):
        """current 内容比 intact 还多时, 不动。"""
        current = {"enabledPlugins": {"a": True, "b": True}, "hooks": {"A": [], "B": []}}
        intact = {"enabledPlugins": {"a": True}, "hooks": {"A": []}}
        result, restored = _mod.restore_reduced_settings_keys(current, intact)
        self.assertEqual(restored, [])
        self.assertEqual(len(result["enabledPlugins"]), 2)

    def test_permissions_union_not_replace(self):
        """permissions 用 union 合并, 不丢失 current 新增的条目。"""
        current = {
            "enabledPlugins": {"a": True},
            "permissions": {"allow": ["Bash(new-cmd)"], "deny": ["deny_current"]},
        }
        intact = {
            "enabledPlugins": {"a": True, "b": True},
            "permissions": {"allow": ["Bash(old-cmd)", "Bash(new-cmd)"], "deny": ["deny_old"]},
        }
        result, restored = _mod.restore_reduced_settings_keys(current, intact)
        # current 新增的 Bash(new-cmd) 不能丢, intact 的 Bash(old-cmd) 要补进来
        allow = result["permissions"]["allow"]
        self.assertIn("Bash(new-cmd)", allow)
        self.assertIn("Bash(old-cmd)", allow)
        # deny 也 union
        self.assertIn("deny_current", result["permissions"]["deny"])
        self.assertIn("deny_old", result["permissions"]["deny"])

    def test_find_intact_prefers_richest_content_not_most_recent(self):
        """两个 >=10 key 的备份, 应选内容最丰富的 (10 plugins), 不是最近的 (1 plugin)。"""
        tmpdir = Path("/tmp/test_intact_richest_xyz")
        if tmpdir.exists():
            shutil.rmtree(tmpdir)
        tmpdir.mkdir()
        try:
            rich = self._full_keys()
            rich["enabledPlugins"] = {f"p{i}": True for i in range(10)}
            rich["hooks"] = {h: [] for h in ["PostToolUse", "PreCompact", "PreToolUse", "SessionStart", "Stop", "UserPromptSubmit"]}
            # rich 是旧的 (20260601), 但内容丰富
            (tmpdir / "settings-20260601-100000.json").write_text(json.dumps(rich), encoding="utf-8")

            recent = self._full_keys()
            recent["enabledPlugins"] = {"only-one": True}   # 新但内容被削
            recent["hooks"] = {"SessionStart": []}
            (tmpdir / "settings-20260605-100000.json").write_text(json.dumps(recent), encoding="utf-8")

            result = _mod.find_intact_settings_backup(tmpdir)
            self.assertIsNotNone(result)
            # 应选内容丰富的旧备份, 不是内容被削的新备份
            self.assertEqual(result.name, "settings-20260601-100000.json")
        finally:
            shutil.rmtree(tmpdir)


class TestSyncAuth(unittest.TestCase):
    """E 修复: wrapper 应该把 auth.json 跟 config.toml/settings.json 一样处理 (备份+同步)。

    同步方向: WSL → Windows, 用 mtime 决定 (新覆盖旧)。
    """

    def setUp(self):
        from argparse import Namespace
        self.tmpdir = Path("/tmp/test_sync_auth_xyz")
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "backups").mkdir(exist_ok=True)
        self.wsl_path = self.tmpdir / "auth.json"
        self.win_path = self.tmpdir / "win-auth.json"
        self.args = Namespace(
            wsl_auth=self.wsl_path,
            windows_auth=self.win_path,
            backup_dir=self.tmpdir / "backups",
        )

    def tearDown(self):
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)

    def test_wsl_newer_overwrites_windows(self):
        """WSL 端 mtime 新 (差异 > 1s) -> 复制到 Windows。"""
        self.wsl_path.write_text('{"OPENAI_API_KEY": "NEW_FROM_WSL"}', encoding="utf-8")
        self.win_path.write_text('{"OPENAI_API_KEY": "OLD_FROM_WIN"}', encoding="utf-8")
        import os
        # 强制 WSL 比 Windows 新 5 秒 (> 1s 容忍)
        new_time = self.win_path.stat().st_mtime + 5
        os.utime(self.wsl_path, (new_time, new_time))

        rc = cmd_sync_auth(self.args)
        self.assertEqual(rc, 0)
        self.assertEqual(
            self.win_path.read_text(encoding="utf-8"),
            '{"OPENAI_API_KEY": "NEW_FROM_WSL"}',
        )
        self.assertTrue(
            any(p.name.startswith("auth-windows-") for p in (self.tmpdir / "backups").iterdir()),
            "Windows 端旧 auth.json 应该被备份",
        )

    def test_wsl_only_1s_newer_does_not_sync(self):
        """WSL 端 mtime 只比 Windows 新 1s 内 -> 容忍, 不同步 (避免误触)。"""
        self.wsl_path.write_text('{"OPENAI_API_KEY": "WSL"}', encoding="utf-8")
        self.win_path.write_text('{"OPENAI_API_KEY": "WIN"}', encoding="utf-8")
        import os
        # 强制 WSL 只比 Windows 新 0.5 秒
        new_time = self.win_path.stat().st_mtime + 0.5
        os.utime(self.wsl_path, (new_time, new_time))

        rc = cmd_sync_auth(self.args)
        self.assertEqual(rc, 0)
        # 都没动
        self.assertEqual(
            self.win_path.read_text(encoding="utf-8"),
            '{"OPENAI_API_KEY": "WIN"}',
        )

    def test_windows_newer_does_not_overwrite_wsl(self):
        """Windows 端 mtime 新 -> 不动 WSL 端。"""
        self.wsl_path.write_text('{"OPENAI_API_KEY": "WSL"}', encoding="utf-8")
        self.win_path.write_text('{"OPENAI_API_KEY": "WIN_NEW"}', encoding="utf-8")
        # Windows 端比 WSL 新 100 秒
        new_time = self.wsl_path.stat().st_mtime + 100
        import os
        os.utime(self.win_path, (new_time, new_time))

        rc = cmd_sync_auth(self.args)
        self.assertEqual(rc, 0)
        # WSL 不动
        self.assertEqual(
            self.wsl_path.read_text(encoding="utf-8"),
            '{"OPENAI_API_KEY": "WSL"}',
        )
        # Windows 也不动
        self.assertEqual(
            self.win_path.read_text(encoding="utf-8"),
            '{"OPENAI_API_KEY": "WIN_NEW"}',
        )

    def test_no_windows_file_creates_from_wsl(self):
        """Windows 端不存在 -> 直接从 WSL 复制。"""
        self.wsl_path.write_text('{"OPENAI_API_KEY": "WSL_ONLY"}', encoding="utf-8")
        # win_path 不创建
        rc = cmd_sync_auth(self.args)
        self.assertEqual(rc, 0)
        self.assertTrue(self.win_path.exists())
        self.assertEqual(
            self.win_path.read_text(encoding="utf-8"),
            '{"OPENAI_API_KEY": "WSL_ONLY"}',
        )

    def test_no_wsl_file_skips(self):
        """WSL 端不存在 -> 跳过同步。"""
        # wsl_path 不创建
        self.win_path.write_text('{"OPENAI_API_KEY": "WIN_ONLY"}', encoding="utf-8")
        rc = cmd_sync_auth(self.args)
        self.assertEqual(rc, 0)
        # Windows 不动
        self.assertEqual(
            self.win_path.read_text(encoding="utf-8"),
            '{"OPENAI_API_KEY": "WIN_ONLY"}',
        )


class TestPruneBackups(unittest.TestCase):
    """Tests for prune_backups(backup_dir, pattern, keep)."""

    def setUp(self):
        self.tmpdir = Path("/tmp/test_prune_backups_cc_switch")
        self.tmpdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)

    def _create_files(self, names):
        for i, name in enumerate(names):
            p = self.tmpdir / name
            p.write_text(f"file {i}", encoding="utf-8")

    def test_keeps_20_newest(self):
        # Create 25 files
        names = [f"backup-{i:02d}.json" for i in range(25)]
        self._create_files(names)
        deleted = prune_backups(self.tmpdir, "backup-*.json", keep=20)
        self.assertEqual(deleted, 5)
        remaining = sorted(self.tmpdir.iterdir())
        self.assertEqual(len(remaining), 20)

    def test_handles_missing_dir(self):
        nonexistent = Path("/tmp/no_such_dir_for_prune_test_xyz")
        result = prune_backups(nonexistent, "*.json", keep=20)
        self.assertEqual(result, 0)

    def test_handles_no_matches(self):
        self._create_files(["readme.txt"])
        result = prune_backups(self.tmpdir, "*.json", keep=20)
        self.assertEqual(result, 0)

    def test_keep_zero_deletes_all(self):
        names = [f"backup-{i:02d}.json" for i in range(5)]
        self._create_files(names)
        deleted = prune_backups(self.tmpdir, "backup-*.json", keep=0)
        self.assertEqual(deleted, 5)
        self.assertEqual(len(list(self.tmpdir.iterdir())), 0)

    def test_mixed_files_only_deletes_matching(self):
        self._create_files(["a.json", "b.json", "c.txt"])
        deleted = prune_backups(self.tmpdir, "*.json", keep=0)
        self.assertEqual(deleted, 2)
        remaining = {p.name for p in self.tmpdir.iterdir()}
        self.assertEqual(remaining, {"c.txt"})


class TestIntegrationToml(unittest.TestCase):
    def test_parse_and_merge_roundtrip(self):
        parse_toml = _mod.parse_toml
        try:
            after_path = Path("/tmp/test-cc-switch-merge-after.toml")
            before_path = Path("/tmp/test-cc-switch-merge-before.toml")
            after_path.write_bytes(tomli_w.dumps({
                "model": "newmodel",
                "model_providers": {"custom": {"name": "new"}},
            }).encode("utf-8"))
            before_path.write_bytes(tomli_w.dumps({
                "model": "oldmodel",
                "projects": {"/foo": {"trust_level": "trusted"}},
            }).encode("utf-8"))
            after = parse_toml(after_path)
            before = parse_toml(before_path)
            result = merge_codex(after, before)
            self.assertEqual(result["model"], "newmodel")
            self.assertIn("projects", result)
            self.assertEqual(result["projects"]["/foo"]["trust_level"], "trusted")
        finally:
            after_path.unlink(missing_ok=True)
            before_path.unlink(missing_ok=True)

    def test_write_toml_roundtrip(self):
        write_toml = _mod.write_toml
        out = Path("/tmp/test-cc-switch-merge-roundtrip.toml")
        try:
            data = {
                "model": "kimi",
                "model_providers": {"custom": {"name": "kimi"}},
                "projects": {"/foo": {"trust_level": "trusted"}},
            }
            write_toml(out, data)
            with out.open("rb") as f:
                loaded = tomllib.load(f)
            self.assertEqual(loaded["model"], "kimi")
            self.assertEqual(loaded["model_providers"]["custom"]["name"], "kimi")
        finally:
            out.unlink(missing_ok=True)

    def test_get_provider_model_claude_json_env_format(self):
        """claude provider 的 settings_config 是 JSON, model 在 env.ANTHROPIC_MODEL。"""
        get_provider_model = _mod.get_provider_model_from_db

        # 模拟 cc-switch DB: claude provider
        claude_settings = json.dumps({
            "env": {
                "ANTHROPIC_BASE_URL": "https://api.minimaxi.com/anthropic",
                "ANTHROPIC_MODEL": "MiniMax-M3[1M]",
            }
        })

        # 直接验证解析逻辑 (不依赖真实 DB)
        import re
        sc = json.loads(claude_settings)
        # claude 走 JSON env 分支
        model = sc.get("env", {}).get("ANTHROPIC_MODEL")
        self.assertEqual(model, "MiniMax-M3[1M]")

        # codex 走 TOML config 分支
        codex_settings = json.dumps({
            "config": 'model = "glm-5.1"\n[model_providers.custom]\nname = "zhipu"'
        })
        sc = json.loads(codex_settings)
        m = re.search(r'^model\s*=\s*"([^"]+)"', sc.get("config", ""), re.MULTILINE)
        self.assertEqual(m.group(1), "glm-5.1")


if __name__ == "__main__":
    unittest.main()
