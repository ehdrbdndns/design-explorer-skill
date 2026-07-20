import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from design_explorer_import import load_script_module
from test_preview_evidence import preview_digest, write_png
from test_run_state import direction, evidence, reference


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

    def advance_to_approved(
        self, run_dir: Path, direction_ids: list[str] | None = None
    ) -> None:
        direction_ids = direction_ids or ["d-0"]
        (run_dir / "brief.md").write_text("# Checkout brief", encoding="utf-8")
        run_state.transition_run(run_dir, "brief_ready")
        self.reload_at(run_dir, "brief_ready")
        self.write_research_and_directions(run_dir)
        run_state.transition_run(run_dir, "research_complete")
        self.reload_at(run_dir, "research_complete")
        run_state.transition_run(run_dir, "directions_pending_approval")
        self.reload_at(run_dir, "directions_pending_approval")
        self.assertFalse(run_state.image_generation_allowed(run_dir, "d-0"))
        run_state.transition_run(
            run_dir, "directions_approved", approved_direction_ids=direction_ids
        )
        self.reload_at(run_dir, "directions_approved")
        self.assertFalse(run_state.image_generation_allowed(run_dir, "d-0"))

    def write_project_direction_previews(
        self, run_dir: Path, project: Path, direction_ids: list[str]
    ) -> dict:
        target_viewports = run_state.load_run(run_dir)["target_viewports"]
        (project / "src" / "tokens.css").write_text(
            ":root { --color-surface: #ffffff; --space-4: 1rem; }\n",
            encoding="utf-8",
        )
        (project / "src" / "Button.tsx").write_text(
            "export function Button(){return <button>Continue</button>}\n",
            encoding="utf-8",
        )
        mockups = []
        for direction_id in direction_ids:
            preview_dir = project / "previews" / direction_id
            preview_dir.mkdir(parents=True)
            (preview_dir / "Screen.tsx").write_text(
                "import '../../src/tokens.css';\n"
                "import { Button } from '../../src/Button';\n"
                "export function Screen(){return <main style={{background: "
                "'var(--color-surface)', padding: 'var(--space-4)'}}>"
                f"<h1>{direction_id}</h1><Button /></main>"
                "}\n",
                encoding="utf-8",
            )
            (preview_dir / "entry.tsx").write_text(
                "import { Screen } from './Screen';\n"
                f"export const route = '/design-explorer/{direction_id}';\n"
                "export default Screen;\n",
                encoding="utf-8",
            )
            prompt = run_dir / "prompts" / f"{direction_id}.txt"
            prompt.parent.mkdir(exist_ok=True)
            prompt.write_text(
                f"Render the token-backed {direction_id} code preview.\n",
                encoding="utf-8",
            )
            viewport_checks = {}
            for viewport in target_viewports:
                screenshot_ref = f"evidence/{direction_id}/{viewport}.png"
                width, height = (int(part) for part in viewport.split("x"))
                write_png(run_dir / screenshot_ref, width, height)
                viewport_checks[viewport] = {
                    "screenshot_ref": screenshot_ref,
                    "content": "pass",
                    "overflow": "pass",
                    "accessibility": "pass",
                    "interaction": "pass",
                }
            preview_files = [
                f"previews/{direction_id}/entry.tsx",
                f"previews/{direction_id}/Screen.tsx",
                "src/tokens.css",
                "src/Button.tsx",
            ]
            mockups.append(
                {
                    "direction_id": direction_id,
                    "artifact_kind": "code-preview",
                    "status": "success",
                    "viewport": target_viewports[0],
                    "prompt_ref": f"prompts/{direction_id}.txt",
                    "prompt_digest": "sha256:"
                    + hashlib.sha256(prompt.read_bytes()).hexdigest(),
                    "attempt_count": 0,
                    "output_kind": "local",
                    "output_ref": viewport_checks[target_viewports[0]][
                        "screenshot_ref"
                    ],
                    "preview_mode": "project",
                    "preview_path": f"previews/{direction_id}/entry.tsx",
                    "preview_files": preview_files,
                    "preview_route": f"/design-explorer/{direction_id}",
                    "token_sources": ["src/tokens.css"],
                    "used_tokens": ["--color-surface", "--space-4"],
                    "component_sources": ["src/Button.tsx"],
                    "supporting_provider_refs": [],
                    "source_digest": preview_digest(project, preview_files),
                    "viewport_checks": viewport_checks,
                }
            )
        return {
            "schema_version": 1,
            "generation_attempts_used": 0,
            "last_generation_authorized_at": None,
            "last_generation_direction_id": None,
            "mockups": mockups,
        }

    def write_standalone_direction_previews(
        self, run_dir: Path, direction_ids: list[str]
    ) -> dict:
        target_viewports = run_state.load_run(run_dir)["target_viewports"]
        workspace = run_dir / "standalone"
        source = workspace / "src"
        directions_dir = source / "directions"
        directions_dir.mkdir(parents=True)
        (workspace / "package.json").write_text(
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
        (workspace / "index.html").write_text(
            '<div id="root"></div><script type="module" src="/src/main.tsx"></script>',
            encoding="utf-8",
        )
        (workspace / "vite.config.ts").write_text(
            "import { defineConfig } from 'vite';\n"
            "import react from '@vitejs/plugin-react';\n"
            "export default defineConfig({plugins:[react()]});\n",
            encoding="utf-8",
        )
        (workspace / "tsconfig.json").write_text(
            '{"compilerOptions":{"jsx":"react-jsx","module":"ESNext"}}',
            encoding="utf-8",
        )
        (source / "main.tsx").write_text(
            "import React from 'react';\n"
            "import { createRoot } from 'react-dom/client';\n"
            "import App from './App';\n"
            "createRoot(document.getElementById('root')!).render(<App />);\n",
            encoding="utf-8",
        )
        (source / "tokens.css").write_text(
            ":root { --color-surface: #ffffff; --space-4: 1rem; }\n",
            encoding="utf-8",
        )
        (source / "PreviewCard.tsx").write_text(
            "export function PreviewCard({title}:{title:string}){"
            "return <section><h1>{title}</h1><button>Continue</button></section>}\n",
            encoding="utf-8",
        )
        imports = []
        routes = []
        direction_files = []
        for index, direction_id in enumerate(direction_ids):
            component_name = f"Direction{index}"
            relative = f"standalone/src/directions/{direction_id}.tsx"
            direction_files.append(relative)
            imports.append(
                f"import {component_name} from './directions/{direction_id}';"
            )
            routes.append(
                f"  '/design-explorer/{direction_id}': {component_name},"
            )
            (directions_dir / f"{direction_id}.tsx").write_text(
                "import '../tokens.css';\n"
                "import { PreviewCard } from '../PreviewCard';\n"
                f"export default function {component_name}(){{return <main "
                "style={{background: 'var(--color-surface)', padding: "
                "'var(--space-4)'}}><PreviewCard title='"
                f"{direction_id}"
                "' /></main>"
                "}\n",
                encoding="utf-8",
            )
        (source / "App.tsx").write_text(
            "\n".join(imports)
            + "\nconst routes = {\n"
            + "\n".join(routes)
            + "\n};\n"
            + "export default function App(){const Screen = "
            + "routes[window.location.pathname as keyof typeof routes] ?? Direction0; "
            + "return <Screen />;}\n",
            encoding="utf-8",
        )
        shared_files = [
            "standalone/package.json",
            "standalone/index.html",
            "standalone/vite.config.ts",
            "standalone/tsconfig.json",
            "standalone/src/main.tsx",
            "standalone/src/App.tsx",
            "standalone/src/tokens.css",
            "standalone/src/PreviewCard.tsx",
            *direction_files,
        ]
        source_digest = preview_digest(run_dir, shared_files)
        mockups = []
        for direction_id in direction_ids:
            prompt = run_dir / "prompts" / f"{direction_id}.txt"
            prompt.parent.mkdir(exist_ok=True)
            prompt.write_text(
                f"Render the shared-token {direction_id} code preview.\n",
                encoding="utf-8",
            )
            viewport_checks = {}
            for viewport in target_viewports:
                screenshot_ref = f"evidence/{direction_id}/{viewport}.png"
                width, height = (int(part) for part in viewport.split("x"))
                write_png(run_dir / screenshot_ref, width, height)
                viewport_checks[viewport] = {
                    "screenshot_ref": screenshot_ref,
                    "content": "pass",
                    "overflow": "pass",
                    "accessibility": "pass",
                    "interaction": "pass",
                }
            mockups.append(
                {
                    "direction_id": direction_id,
                    "artifact_kind": "code-preview",
                    "status": "success",
                    "viewport": target_viewports[0],
                    "prompt_ref": f"prompts/{direction_id}.txt",
                    "prompt_digest": "sha256:"
                    + hashlib.sha256(prompt.read_bytes()).hexdigest(),
                    "attempt_count": 0,
                    "output_kind": "local",
                    "output_ref": viewport_checks[target_viewports[0]][
                        "screenshot_ref"
                    ],
                    "preview_mode": "standalone",
                    "preview_path": f"standalone/src/directions/{direction_id}.tsx",
                    "preview_files": shared_files,
                    "preview_route": f"/design-explorer/{direction_id}",
                    "token_sources": ["standalone/src/tokens.css"],
                    "used_tokens": ["--color-surface", "--space-4"],
                    "component_sources": ["standalone/src/PreviewCard.tsx"],
                    "supporting_provider_refs": [],
                    "source_digest": source_digest,
                    "viewport_checks": viewport_checks,
                }
            )
        return {
            "schema_version": 1,
            "generation_attempts_used": 0,
            "last_generation_authorized_at": None,
            "last_generation_direction_id": None,
            "mockups": mockups,
        }

    def generate_once(self, run_dir: Path, calls: list[str]) -> None:
        prompt = run_dir / "prompts" / "d-0.txt"
        prompt.parent.mkdir(exist_ok=True)
        prompt.write_text("full-screen checkout prompt\n", encoding="utf-8")
        prompt_digest = "sha256:" + hashlib.sha256(prompt.read_bytes()).hexdigest()
        self.write_json(
            run_dir,
            "mockup-manifest.json",
            {
                "schema_version": 1,
                "generation_attempts_used": 0,
                "last_generation_authorized_at": None,
                "last_generation_direction_id": None,
                "mockups": [
                    {
                        "direction_id": "d-0",
                        "status": "pending",
                        "viewport": "390x844",
                        "prompt_ref": "prompts/d-0.txt",
                        "prompt_digest": prompt_digest,
                        "attempt_count": 0,
                    }
                ]
            },
        )
        self.assertTrue(run_state.image_generation_allowed(run_dir, "d-0"))
        run_state.authorize_generation(run_dir, "d-0")
        calls.append("d-0")
        write_png(run_dir / "mockups" / "d-0.png", 390, 844)
        manifest = json.loads((run_dir / "mockup-manifest.json").read_text())
        manifest["mockups"][0].update(
            {
                "status": "success",
                "output_kind": "local",
                "output_ref": "mockups/d-0.png",
            }
        )
        self.write_json(run_dir, "mockup-manifest.json", manifest)

    def implementation(
        self, source_root: Path, preview_path: str, preview_files: list[str]
    ):
        source_digest = preview_digest(source_root, preview_files)
        value = {
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
        if value["mode"] == "project":
            value["route_registry_path"] = "previews/routes.json"
            value["route_consumer_path"] = "previews/App.tsx"
        return value

    def offline_http_get(self, project: Path, route: str) -> tuple[int, str]:
        registry_path = project / "previews" / "routes.json"

        class RegistryHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                routes = json.loads(registry_path.read_text(encoding="utf-8"))
                entry = routes.get(self.path)
                if not isinstance(entry, dict):
                    self.send_error(404, "not found")
                    return
                body = (project / entry["component_path"]).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), RegistryHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}{route}"
            try:
                with urlopen(url, timeout=2) as response:
                    return response.status, response.read().decode("utf-8")
            except HTTPError as error:
                return error.code, error.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_project_builds_five_token_backed_previews_without_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            production = project / "src" / "App.tsx"
            production.write_text(
                "export const production = true;\n", encoding="utf-8"
            )
            production_hash = hashlib.sha256(production.read_bytes()).hexdigest()
            direction_ids = [f"d-{index}" for index in range(5)]
            run_dir = run_state.init_run(
                root / "runs",
                "checkout-directions",
                project_path=str(project),
                run_id="project-code-previews",
                target_viewports=["390x844", "1280x800"],
                required_content=["Order summary", "Total"],
                required_interactions=["Edit order"],
                production_paths=["src/App.tsx"],
            )
            provider_calls = []

            self.advance_to_approved(run_dir, direction_ids)
            manifest = self.write_project_direction_previews(
                run_dir, project, direction_ids
            )
            self.write_json(run_dir, "mockup-manifest.json", manifest)

            self.assertEqual(manifest["generation_attempts_used"], 0)
            self.assertEqual(provider_calls, [])
            self.assertEqual(
                hashlib.sha256(production.read_bytes()).hexdigest(), production_hash
            )
            run_state.transition_run(run_dir, "mockups_generated")
            run_state.transition_run(
                run_dir, "implementation_selected", selected_direction_id="d-0"
            )
            selected = self.reload_at(run_dir, "implementation_selected")
            self.assertEqual(selected["selected_direction_id"], "d-0")
            self.assertEqual(provider_calls, [])
            self.assertEqual(
                hashlib.sha256(production.read_bytes()).hexdigest(), production_hash
            )

    def test_standalone_builds_five_previews_from_one_shared_token_layer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            direction_ids = [f"d-{index}" for index in range(5)]
            run_dir = run_state.init_run(
                root,
                "onboarding-directions",
                run_id="standalone-code-previews",
                target_viewports=["390x844", "1280x800"],
                required_content=["Welcome", "Continue"],
                required_interactions=["Continue"],
                production_paths=[],
            )
            provider_calls = []

            self.advance_to_approved(run_dir, direction_ids)
            manifest = self.write_standalone_direction_previews(
                run_dir, direction_ids
            )
            self.write_json(run_dir, "mockup-manifest.json", manifest)

            self.assertEqual(manifest["generation_attempts_used"], 0)
            self.assertEqual(provider_calls, [])
            self.assertEqual(
                {tuple(item["token_sources"]) for item in manifest["mockups"]},
                {("standalone/src/tokens.css",)},
            )
            self.assertEqual(
                {tuple(item["component_sources"]) for item in manifest["mockups"]},
                {("standalone/src/PreviewCard.tsx",)},
            )
            run_state.transition_run(run_dir, "mockups_generated")
            run_state.transition_run(
                run_dir, "implementation_selected", selected_direction_id="d-0"
            )
            selected = self.reload_at(run_dir, "implementation_selected")
            self.assertEqual(selected["selected_direction_id"], "d-0")
            self.assertEqual(provider_calls, [])

    def test_optional_provider_asset_still_requires_authorization(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "src" / "App.tsx").write_text(
                "export const production = true;\n", encoding="utf-8"
            )
            run_dir = run_state.init_run(
                root / "runs",
                "optional-asset",
                project_path=str(project),
                run_id="optional-provider-asset",
                target_viewports=["390x844"],
                required_content=["Order summary"],
                required_interactions=["Edit order"],
                production_paths=["src/App.tsx"],
            )
            provider_calls = []
            self.advance_to_approved(run_dir)
            manifest = self.write_project_direction_previews(
                run_dir, project, ["d-0"]
            )
            entry = manifest["mockups"][0]
            entry["status"] = "pending"
            entry.pop("output_kind")
            entry.pop("output_ref")
            self.write_json(run_dir, "mockup-manifest.json", manifest)

            can_generate = subprocess.run(
                [
                    sys.executable,
                    run_state.__file__,
                    "can-generate",
                    "--run",
                    str(run_dir),
                    "--direction",
                    "d-0",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(can_generate.returncode, 0, can_generate.stderr)
            self.assertEqual(can_generate.stdout.strip(), "true")
            self.assertEqual(provider_calls, [])

            authorization = subprocess.run(
                [
                    sys.executable,
                    run_state.__file__,
                    "authorize-generation",
                    "--run",
                    str(run_dir),
                    "--direction",
                    "d-0",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(authorization.returncode, 0, authorization.stderr)
            provider_ref = "provider:openai:asset-d-0"
            provider_calls.append(provider_ref)
            manifest = json.loads(
                (run_dir / "mockup-manifest.json").read_text(encoding="utf-8")
            )
            entry = manifest["mockups"][0]
            entry["supporting_provider_refs"] = [provider_ref]
            entry["status"] = "success"
            entry["output_kind"] = "local"
            entry["output_ref"] = "evidence/d-0/390x844.png"
            write_png(run_dir / entry["output_ref"], 390, 844)
            self.write_json(run_dir, "mockup-manifest.json", manifest)

            self.assertEqual(manifest["generation_attempts_used"], 1)
            self.assertEqual(entry["attempt_count"], 1)
            self.assertEqual(entry["supporting_provider_refs"], provider_calls)
            self.assertEqual(entry["output_kind"], "local")
            self.assertEqual(entry["output_ref"], "evidence/d-0/390x844.png")
            run_state.transition_run(run_dir, "mockups_generated")
            self.reload_at(run_dir, "mockups_generated")

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
                "export const production = true;",
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
            self.assertFalse(run_state.image_generation_allowed(run_dir, "d-0"))
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
            self.assertFalse(run_state.image_generation_allowed(run_dir, "d-0"))
            run_state.transition_run(
                run_dir, "implementation_selected", selected_direction_id="d-0"
            )
            self.reload_at(run_dir, "implementation_selected")

            (project / "previews").mkdir()
            (project / "previews" / "App.tsx").write_text(
                "import routes from './routes.json';\n"
                "import { Preview } from './Checkout';\n"
                "export const resolve = (path: string) => routes[path] ? Preview : null;",
                encoding="utf-8",
            )
            (project / "previews" / "Checkout.tsx").write_text(
                "export const Preview = () => <main id='checkout-shell'>Expected preview shell</main>;",
                encoding="utf-8",
            )
            (project / "previews" / "routes.json").write_text(
                '{"/design-explorer/checkout":{"component_path":"previews/Checkout.tsx","shell_id":"checkout-shell"}}',
                encoding="utf-8",
            )
            project_files = [
                "previews/App.tsx",
                "previews/Checkout.tsx",
                "previews/routes.json",
            ]
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
                "import { defineConfig } from 'vite';\n"
                "import react from '@vitejs/plugin-react';\n"
                "export default defineConfig({plugins:[react()]})",
                encoding="utf-8",
            )
            (run_dir / "standalone" / "tsconfig.json").write_text(
                '{"compilerOptions":{"jsx":"react-jsx","module":"ESNext"}}',
                encoding="utf-8",
            )
            (run_dir / "standalone" / "src" / "main.tsx").write_text(
                "import React from 'react';\n"
                "import {createRoot} from 'react-dom/client';\n"
                "import App from './App';\n"
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
