import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from design_explorer_import import load_script_module
from test_preview_evidence import preview_digest, write_png
from test_run_state import DIGEST, direction, evidence, reference


run_state = load_script_module("run_state_integration", "design-explorer/scripts/run_state.py")


class WorkflowIntegrationTests(unittest.TestCase):
    def write_json(self, run_dir: Path, name: str, value) -> None:
        (run_dir / name).write_text(json.dumps(value), encoding="utf-8")

    def reload_at(self, run_dir: Path, state: str) -> dict:
        manifest = run_state.load_run(run_dir)
        self.assertEqual(manifest["state"], state)
        return manifest

    def write_research_and_directions(self, run_dir: Path) -> None:
        self.write_json(run_dir, "references.json", [reference()])
        self.write_json(run_dir, "evidence.json", [evidence()])
        (run_dir / "design-evidence.md").write_text("# Evidence", encoding="utf-8")
        (run_dir / "reference-board.md").write_text("# Board", encoding="utf-8")
        self.write_json(
            run_dir,
            "directions.json",
            [direction(f"d-{index}", index) for index in range(5)],
        )
        (run_dir / "mood-directions.md").write_text("# Directions", encoding="utf-8")

    def advance_to_approved(self, run_dir: Path) -> None:
        (run_dir / "brief.md").write_text("# Checkout brief", encoding="utf-8")
        run_state.transition_run(run_dir, "brief_ready")
        self.reload_at(run_dir, "brief_ready")
        self.write_research_and_directions(run_dir)
        run_state.transition_run(run_dir, "research_complete")
        self.reload_at(run_dir, "research_complete")
        run_state.transition_run(run_dir, "directions_pending_approval")
        self.reload_at(run_dir, "directions_pending_approval")
        self.assertFalse(run_state.image_generation_allowed(run_dir))
        run_state.transition_run(
            run_dir, "directions_approved", approved_direction_ids=["d-0"]
        )
        self.reload_at(run_dir, "directions_approved")
        self.assertTrue(run_state.image_generation_allowed(run_dir))

    def generate_once(self, run_dir: Path, calls: list[str]) -> None:
        self.assertTrue(run_state.image_generation_allowed(run_dir))
        calls.append("d-0")
        write_png(run_dir / "mockups" / "d-0.png", 390, 844)
        self.write_json(
            run_dir,
            "mockup-manifest.json",
            {
                "mockups": [
                    {
                        "direction_id": "d-0",
                        "status": "success",
                        "viewport": "390x844",
                        "prompt_digest": DIGEST,
                        "output_ref": "mockups/d-0.png",
                        "attempt_count": 1,
                    }
                ]
            },
        )

    def implementation(
        self, source_root: Path, preview_path: str, preview_files: list[str]
    ):
        source_digest = preview_digest(source_root, preview_files)
        return {
            "selected_direction_id": "d-0",
            "mode": "project" if preview_path.startswith("previews/") else "standalone",
            "preview_path": preview_path,
            "preview_files": preview_files,
            "preview_route": "/design-explorer/checkout",
            "verification": {
                "rendered_viewports": ["390x844", "1280x800"],
                "checks": {
                    "content": "pass",
                    "overflow": "pass",
                    "accessibility": "pass",
                },
                "viewport_checks": {
                    viewport: {
                        "screenshot_ref": f"evidence/{viewport}.png",
                        "source_digest": source_digest,
                        "content": "pass",
                        "overflow": "pass",
                        "accessibility": "pass",
                        "interaction": "pass",
                        "required_content": {
                            "Order summary": "pass",
                            "Total": "pass",
                        },
                        "required_interactions": {"Edit order": "pass"},
                    }
                    for viewport in ("390x844", "1280x800")
                },
            },
        }

    def offline_http_get(self, project: Path, route: str) -> tuple[int, str]:
        app_source = (project / "src" / "App.tsx").read_text(encoding="utf-8")
        routes = json.loads((project / "previews" / "routes.json").read_text())
        wiring = "../previews/routes.json" in app_source and "../previews/Checkout" in app_source
        if wiring and route in routes:
            return 200, (project / "previews" / "Checkout.tsx").read_text(encoding="utf-8")
        return 404, "not found"

    def test_project_lifecycle_gates_provider_and_preserves_production(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "package.json").write_text(
                '{"dependencies":{"react":"1","react-dom":"1"}}', encoding="utf-8"
            )
            production = project / "src" / "App.tsx"
            production.write_text(
                "import { Preview } from '../previews/Checkout';\n"
                "import routes from '../previews/routes.json';\n"
                "export const resolve = (path: string) => routes[path] ? Preview : null;",
                encoding="utf-8",
            )
            production_hash = hashlib.sha256(production.read_bytes()).hexdigest()
            run_dir = run_state.init_run(
                root / "runs",
                "checkout",
                project_path=str(project),
                run_id="project-run",
                target_viewports=["390x844", "1280x800"],
                required_content=["Order summary", "Total"],
                required_interactions=["Edit order"],
                production_paths=["src/App.tsx"],
            )
            self.reload_at(run_dir, "initialized")
            provider_calls = []
            self.assertFalse(run_state.image_generation_allowed(run_dir))
            self.assertEqual(provider_calls, [])

            self.advance_to_approved(run_dir)
            self.generate_once(run_dir, provider_calls)
            self.assertEqual(provider_calls, ["d-0"])
            self.assertLessEqual(
                len(json.loads((run_dir / "mockup-manifest.json").read_text())["mockups"]),
                run_state.load_run(run_dir)["generation_budget"],
            )
            run_state.transition_run(run_dir, "mockups_generated")
            self.reload_at(run_dir, "mockups_generated")
            self.assertFalse(run_state.image_generation_allowed(run_dir))
            run_state.transition_run(
                run_dir, "implementation_selected", selected_direction_id="d-0"
            )
            self.reload_at(run_dir, "implementation_selected")

            (project / "previews").mkdir()
            (project / "previews" / "Checkout.tsx").write_text(
                "export const Preview = () => <main>Expected preview shell</main>;",
                encoding="utf-8",
            )
            (project / "previews" / "routes.json").write_text(
                '{"/design-explorer/checkout":"Checkout"}', encoding="utf-8"
            )
            project_files = ["previews/Checkout.tsx", "previews/routes.json"]
            for viewport in ("390x844", "1280x800"):
                width, height = (int(part) for part in viewport.split("x"))
                write_png(run_dir / "evidence" / f"{viewport}.png", width, height)
            self.write_json(
                run_dir,
                "implementation.json",
                self.implementation(project, "previews/Checkout.tsx", project_files),
            )
            status, body = self.offline_http_get(project, "/design-explorer/checkout")
            self.assertEqual(status, 200)
            self.assertIn("Expected preview shell", body)
            self.assertEqual(self.offline_http_get(project, "/missing")[0], 404)
            self.assertEqual(hashlib.sha256(production.read_bytes()).hexdigest(), production_hash)
            run_state.transition_run(run_dir, "prototype_ready")
            self.reload_at(run_dir, "prototype_ready")
            self.assertEqual(hashlib.sha256(production.read_bytes()).hexdigest(), production_hash)
            with self.assertRaisesRegex(ValueError, "explicit integration approval"):
                run_state.transition_run(run_dir, "integrated")
            self.reload_at(run_dir, "prototype_ready")
            run_state.transition_run(run_dir, "integrated", integration_approved=True)
            self.reload_at(run_dir, "integrated")
            self.assertEqual(hashlib.sha256(production.read_bytes()).hexdigest(), production_hash)

    def test_standalone_fallback_lifecycle_reaches_prototype_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = run_state.init_run(
                root,
                "standalone",
                run_id="standalone-run",
                target_viewports=["390x844", "1280x800"],
                required_content=["Order summary", "Total"],
                required_interactions=["Edit order"],
                production_paths=[],
            )
            self.reload_at(run_dir, "initialized")
            self.advance_to_approved(run_dir)
            calls = []
            self.generate_once(run_dir, calls)
            run_state.transition_run(run_dir, "mockups_generated")
            self.reload_at(run_dir, "mockups_generated")
            run_state.transition_run(
                run_dir, "implementation_selected", selected_direction_id="d-0"
            )
            self.reload_at(run_dir, "implementation_selected")

            (run_dir / "standalone" / "src").mkdir(parents=True)
            (run_dir / "standalone" / "package.json").write_text(
                json.dumps(
                    {
                        "scripts": {"dev": "vite", "build": "vite build"},
                        "dependencies": {"react": "1", "react-dom": "1"},
                        "devDependencies": {
                            "vite": "1",
                            "typescript": "1",
                            "@vitejs/plugin-react": "1",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "standalone" / "index.html").write_text(
                '<div id="root"></div><script type="module" src="/src/main.tsx"></script>',
                encoding="utf-8",
            )
            (run_dir / "standalone" / "vite.config.ts").write_text(
                "import react from '@vitejs/plugin-react'; export default {plugins:[react()]}",
                encoding="utf-8",
            )
            (run_dir / "standalone" / "tsconfig.json").write_text("{}", encoding="utf-8")
            (run_dir / "standalone" / "src" / "main.tsx").write_text(
                "import {createRoot} from 'react-dom/client'; import App from './App'; "
                "createRoot(document.getElementById('root')!).render(<App/>);",
                encoding="utf-8",
            )
            (run_dir / "standalone" / "src" / "App.tsx").write_text(
                "export default function App(){return <main>Expected preview shell</main>}",
                encoding="utf-8",
            )
            standalone_files = [
                "standalone/package.json",
                "standalone/index.html",
                "standalone/vite.config.ts",
                "standalone/tsconfig.json",
                "standalone/src/main.tsx",
                "standalone/src/App.tsx",
            ]
            for viewport in ("390x844", "1280x800"):
                width, height = (int(part) for part in viewport.split("x"))
                write_png(run_dir / "evidence" / f"{viewport}.png", width, height)
            self.write_json(
                run_dir,
                "implementation.json",
                self.implementation(
                    run_dir,
                    "standalone/src/main.tsx",
                    standalone_files,
                ),
            )
            run_state.transition_run(run_dir, "prototype_ready")
            manifest = self.reload_at(run_dir, "prototype_ready")
            self.assertIsNone(manifest["project_path"])
            self.assertEqual(manifest["production_paths"], [])
            self.assertEqual(calls, ["d-0"])


if __name__ == "__main__":
    unittest.main()
