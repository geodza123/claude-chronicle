"""Exercise installation in an explicit prefix, without changing HOME or real services."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="chronicle-install-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "user's isolated root"
        self.data = self.base / "custom data"
        self.stubs = self.base / "stubs"
        self.stubs.mkdir()
        self.calls = self.base / "unexpected-calls"
        for name in ("claude", "systemctl", "launchctl"):
            stub = self.stubs / name
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$INSTALL_TEST_CALLS"\nexit 97\n')
            stub.chmod(0o755)
        self.env = dict(os.environ)
        for key in ("CHRONICLE_HOME", "CLAUDE_HOME", "CHRONICLE_NO_TIMER", "PYTHONPATH"):
            self.env.pop(key, None)
        self.env.update(
            CHRONICLE_INSTALL_ROOT=str(self.root),
            CHRONICLE_HOME=str(self.data),
            INSTALL_TEST_CALLS=str(self.calls),
            PATH=str(self.stubs) + os.pathsep + self.env["PATH"],
        )

    @property
    def command(self):
        return self.root / ".local/bin/chronicle"

    def run_install(self, *args, check=True):
        result = subprocess.run(
            ["bash", str(PROJECT / "install.sh"), *args], env=self.env,
            capture_output=True, text=True, check=check,
        )
        self.assertFalse(self.calls.exists(), "installer called a model or a real-service interface")
        return result

    def skill(self, name):
        return self.root / ".claude/skills" / name

    def create_foreign_skill(self, name):
        skill = self.skill(name)
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("Existing user skill; mentions chronicle but belongs to another tool.\n")
        (skill / "extra.txt").write_text("keep me\n")

    def test_install_keeps_custom_data_location_in_wrapper(self):
        self.run_install()
        self.assertTrue((self.data / "chronicle.db").is_file())
        self.assertTrue(self.command.is_file())
        # A scheduled process does not inherit the install command's CHRONICLE_HOME.
        runtime_env = dict(self.env)
        runtime_env.pop("CHRONICLE_HOME")
        result = subprocess.run(
            [str(self.command), "sessions", "--json"], env=runtime_env,
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "[]")
        self.assertFalse((self.root / ".local/share/claude-chronicle/chronicle.db").exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.root / ".config/systemd/user").exists())
        self.run_install("--uninstall")
        self.assertFalse(self.command.exists())
        self.assertTrue((self.data / "chronicle.db").is_file())
        self.assertFalse(self.skill("work").exists())

    def test_foreign_skills_are_restored_after_install_and_update(self):
        for name in ("work", "report"):
            self.create_foreign_skill(name)
        self.run_install()
        self.run_install()
        self.run_install("--uninstall")
        for name in ("work", "report"):
            self.assertIn("belongs to another tool", (self.skill(name) / "SKILL.md").read_text())
            self.assertEqual((self.skill(name) / "extra.txt").read_text(), "keep me\n")
            self.assertFalse((self.skill(name) / ".claude-chronicle-owned").exists())

    def test_uninstall_without_install_leaves_foreign_skills(self):
        self.create_foreign_skill("work")
        self.run_install("--uninstall")
        self.assertTrue((self.skill("work") / "extra.txt").exists())

    def test_uninstall_preserves_changed_installed_skill_and_backup(self):
        self.create_foreign_skill("work")
        self.run_install()
        (self.skill("work") / "SKILL.md").write_text("User changed the installed skill\n")
        self.run_install("--uninstall")
        self.assertEqual((self.skill("work") / "SKILL.md").read_text(), "User changed the installed skill\n")
        backup = self.data / "install-state/previous-skills/work/SKILL.md"
        self.assertIn("belongs to another tool", backup.read_text())

    def test_refuses_to_overwrite_an_existing_skill_backup(self):
        self.create_foreign_skill("work")
        self.run_install()
        (self.skill("work") / ".claude-chronicle-owned").unlink()
        (self.skill("work") / "SKILL.md").write_text("Another replacement\n")
        result = self.run_install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.skill("work") / "SKILL.md").read_text(), "Another replacement\n")
        backup = self.data / "install-state/previous-skills/work/SKILL.md"
        self.assertIn("belongs to another tool", backup.read_text())

    def test_update_refuses_to_overwrite_changed_installed_skill(self):
        self.run_install()
        (self.skill("work") / "SKILL.md").write_text("User's changes\n")
        result = self.run_install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.skill("work") / "SKILL.md").read_text(), "User's changes\n")

    def test_foreign_command_stops_install_before_any_changes(self):
        self.command.parent.mkdir(parents=True)
        original = "#!/bin/sh\necho another tool\n"
        self.command.write_text(original)
        self.command.chmod(0o751)
        self.create_foreign_skill("work")
        result = self.run_install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.command.read_text(), original)
        self.assertEqual(self.command.stat().st_mode & 0o777, 0o751)
        self.assertFalse(self.data.exists())
        self.assertIn("belongs to another tool", (self.skill("work") / "SKILL.md").read_text())
        self.assertFalse(self.skill("report").exists())
        self.run_install("--uninstall")
        self.assertEqual(self.command.read_text(), original)

    def test_broken_command_symlink_stops_install_before_any_changes(self):
        self.command.parent.mkdir(parents=True)
        target = self.base / "missing-other-command"
        self.command.symlink_to(target)
        result = self.run_install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.command.is_symlink())
        self.assertFalse(target.exists())
        self.assertFalse(self.data.exists())


if __name__ == "__main__":
    unittest.main()
