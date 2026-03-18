from __future__ import annotations

from pathlib import Path
import unittest


class RepoSkillTests(unittest.TestCase):
    def test_repo_local_skill_has_frontmatter(self) -> None:
        skill_path = Path(__file__).resolve().parents[1] / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("---\n"))
        self.assertIn("name: repo-local", text)
        self.assertIn("description:", text)

    def test_repo_local_skill_exists_with_project_specific_guidance(self) -> None:
        skill_path = Path(__file__).resolve().parents[1] / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")

        self.assertIn("repository-local skill", text)
        self.assertIn("README.md", text)
        self.assertIn("HISTORY.md", text)
        self.assertIn("mindex/assets/skills/", text)
        self.assertIn("python3 -m unittest discover -s tests -v", text)
        self.assertIn("do not use this skill as a generic setup guide", text)
        self.assertIn("Codex wrapper", text)
        self.assertIn("pushed to GitHub through feature branches and PRs", text)
        self.assertIn("mindex publish-pr", text)
        self.assertIn("does not explicitly mention repo workflow, Git, GitHub, branches, or PRs", text)
        self.assertIn("never allow Mindex-managed behavior to push directly", text)
        self.assertIn("PR URL was confirmed", text)

    def test_packaged_skills_have_frontmatter(self) -> None:
        skills_root = Path(__file__).resolve().parents[1] / "mindex" / "assets" / "skills"

        for skill_name in ("repo", "configure"):
            text = (skills_root / skill_name / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            self.assertIn(f"name: {skill_name}", text)
            self.assertIn("description:", text)

    def test_packaged_skills_capture_branch_and_wrapper_policy(self) -> None:
        skills_root = Path(__file__).resolve().parents[1] / "mindex" / "assets" / "skills"

        repo_text = (skills_root / "repo" / "SKILL.md").read_text(encoding="utf-8")
        configure_text = (skills_root / "configure" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Mindex is a Codex wrapper", repo_text)
        self.assertIn("publish meaningful work to GitHub through a PR workflow", repo_text)
        self.assertIn("avoid direct work on `main`, `master`, `production`", repo_text)
        self.assertIn("prefer a fork owned by the user", repo_text)
        self.assertIn("does not explicitly mention repo workflow, Git, GitHub, branches, or PRs", repo_text)

        self.assertIn("Mindex is a Codex wrapper", configure_text)
        self.assertIn("Mindex-enhanced Codex entry point", configure_text)
        self.assertIn("plain `codex` stays available in its original", configure_text)
        self.assertIn("if the user asks Codex to configure Mindex", configure_text)
        self.assertIn(
            "enforce feature branches, automatic PR publication, full-branch PR descriptions, PR URL verification, and no direct pushes",
            configure_text,
        )
        self.assertIn("explain the `mindex` versus vanilla `codex` distinction", configure_text)


if __name__ == "__main__":
    unittest.main()
