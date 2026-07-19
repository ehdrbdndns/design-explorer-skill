import copy
import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from design_explorer_import import load_script_module


validator = load_script_module("validate_run_evidence", "design-explorer/scripts/validate_run.py")


def write_png(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + name
            + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, level=9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def preview_digest(root: Path, files: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(files):
        name = relative.encode("utf-8")
        data = (root / relative).read_bytes()
        digest.update(struct.pack(">Q", len(name)))
        digest.update(name)
        digest.update(struct.pack(">Q", len(data)))
        digest.update(data)
    return "sha256:" + digest.hexdigest()


def run_manifest(project_path: str | None, production_paths=None):
    return {
        "schema_version": 2,
        "run_id": "run-checkout",
        "slug": "checkout",
        "state": "implementation_selected",
        "created_at": "2026-07-19T12:00:00Z",
        "updated_at": "2026-07-19T12:00:00Z",
        "project_path": project_path,
        "approved_direction_ids": ["a"],
        "selected_direction_id": "a",
        "revision_count": 0,
        "generation_budget": 5,
        "max_attempts_per_direction": 2,
        "target_viewports": ["390x844", "1440x900"],
        "required_content": ["Order summary", "Total"],
        "required_interactions": ["Edit order"],
        "production_paths": list(production_paths or []),
    }


def viewport_check(viewport: str, source_digest: str):
    return {
        "screenshot_ref": f"evidence/{viewport}.png",
        "source_digest": source_digest,
        "content": "pass",
        "overflow": "pass",
        "accessibility": "pass",
        "interaction": "pass",
        "required_content": {"Order summary": "pass", "Total": "pass"},
        "required_interactions": {"Edit order": "pass"},
    }


class PreviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()
        self.project = self.root / "project"
        (self.project / "src").mkdir(parents=True)
        (self.project / "package.json").write_text(
            '{"dependencies":{"react":"1","react-dom":"1"}}', encoding="utf-8"
        )
        (self.project / "src" / "App.tsx").write_text(
            "export const production = true;",
            encoding="utf-8",
        )
        (self.project / "previews").mkdir()
        (self.project / "previews" / "App.tsx").write_text(
            "import registry from './routes.json';\n"
            "import { Preview } from './Checkout';\n"
            "export const resolve = (path: string) => registry[path] ? Preview : null;",
            encoding="utf-8",
        )
        (self.project / "previews" / "Checkout.tsx").write_text(
            "export { Preview } from './Actual';", encoding="utf-8"
        )
        (self.project / "previews" / "Actual.tsx").write_text(
            "import './styles.css'; export const Preview=()=> <main id='checkout-shell'>Preview</main>;",
            encoding="utf-8",
        )
        (self.project / "previews" / "styles.css").write_text(
            "@import './theme.css'; .hero{background:url('./mark.svg')}", encoding="utf-8"
        )
        (self.project / "previews" / "theme.css").write_text("main{color:black}", encoding="utf-8")
        (self.project / "previews" / "mark.svg").write_text("<svg/>", encoding="utf-8")
        (self.project / "previews" / "routes.json").write_text(
            '{"/design-explorer/checkout":{"component_path":"previews/Checkout.tsx","shell_id":"checkout-shell"}}',
            encoding="utf-8",
        )
        for viewport in ("390x844", "1440x900"):
            width, height = (int(part) for part in viewport.split("x"))
            write_png(self.run / f"evidence/{viewport}.png", width, height)
        self.manifest = run_manifest(str(self.project), ["src/App.tsx"])
        project_preview_files = [
            "previews/App.tsx",
            "previews/Checkout.tsx",
            "previews/Actual.tsx",
            "previews/styles.css",
            "previews/theme.css",
            "previews/mark.svg",
            "previews/routes.json",
        ]
        source_digest = preview_digest(self.project, project_preview_files)
        self.implementation = {
            "selected_direction_id": "a",
            "mode": "project",
            "preview_path": "previews/Checkout.tsx",
            "preview_files": project_preview_files,
            "preview_route": "/design-explorer/checkout",
            "route_registry_path": "previews/routes.json",
            "route_consumer_path": "previews/App.tsx",
            "verification": {
                "rendered_viewports": ["390x844", "1440x900"],
                "checks": {
                    "content": "pass",
                    "overflow": "pass",
                    "accessibility": "pass",
                },
                "viewport_checks": {
                    viewport: viewport_check(viewport, source_digest)
                    for viewport in ("390x844", "1440x900")
                },
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, manifest=None, implementation=None):
        (self.run / "run.json").write_text(
            json.dumps(manifest or self.manifest), encoding="utf-8"
        )
        (self.run / "implementation.json").write_text(
            json.dumps(implementation or self.implementation), encoding="utf-8"
        )
        return validator.validate_phase(self.run, "implementation")

    def test_project_preview_requires_complete_machine_verified_evidence(self):
        self.assertEqual(self.validate(), [])

    def test_standalone_preview_is_scoped_to_run_and_requires_vite_entry(self):
        standalone = self.run / "standalone"
        (standalone / "src").mkdir(parents=True)
        (standalone / "package.json").write_text(
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
        (standalone / "index.html").write_text(
            '<div id="root"></div><script type="module" src="/src/main.tsx"></script>',
            encoding="utf-8",
        )
        (standalone / "vite.config.ts").write_text(
            "import { defineConfig } from 'vite';\n"
            "import react from '@vitejs/plugin-react';\n"
            "export default defineConfig({plugins:[react()]})",
            encoding="utf-8",
        )
        (standalone / "tsconfig.json").write_text(
            '{"compilerOptions":{"jsx":"react-jsx","module":"ESNext"}}',
            encoding="utf-8",
        )
        (standalone / "src" / "main.tsx").write_text(
            "import React from 'react';\n"
            "import { createRoot } from 'react-dom/client';\n"
            "import App from './App';\ncreateRoot(document.getElementById('root')!).render(<App />);",
            encoding="utf-8",
        )
        (standalone / "src" / "App.tsx").write_text(
            "export default function App(){return <main>Preview</main>}", encoding="utf-8"
        )
        manifest = run_manifest(None)
        implementation = copy.deepcopy(self.implementation)
        standalone_files = [
            "standalone/package.json",
            "standalone/index.html",
            "standalone/vite.config.ts",
            "standalone/tsconfig.json",
            "standalone/src/main.tsx",
            "standalone/src/App.tsx",
        ]
        implementation.update(
            mode="standalone",
            preview_path="standalone/src/main.tsx",
            preview_files=standalone_files,
        )
        digest = preview_digest(self.run, standalone_files)
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        self.assertEqual(self.validate(manifest, implementation), [])

        (standalone / "hero.svg").write_text("<svg/>", encoding="utf-8")
        (standalone / "index.html").write_text(
            '<div id="root"></div><img src="./hero.svg">'
            '<script type="module" src="/src/main.tsx"></script>',
            encoding="utf-8",
        )
        implementation = copy.deepcopy(implementation)
        digest = preview_digest(self.run, standalone_files)
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(manifest, implementation)
        self.assertTrue(any("unlisted local dependency" in error for error in errors), errors)

        standalone_files.append("standalone/hero.svg")
        implementation["preview_files"] = standalone_files
        digest = preview_digest(self.run, standalone_files)
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        self.assertEqual(self.validate(manifest, implementation), [])

    def test_standalone_rejects_incomplete_vite_react_topology(self):
        standalone = self.run / "standalone"
        (standalone / "src").mkdir(parents=True)
        (standalone / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
        (standalone / "src" / "main.tsx").write_text("entry", encoding="utf-8")
        manifest = run_manifest(None)
        implementation = copy.deepcopy(self.implementation)
        files = ["standalone/package.json", "standalone/src/main.tsx"]
        implementation.update(
            mode="standalone",
            preview_path="standalone/src/main.tsx",
            preview_files=files,
        )
        digest = preview_digest(self.run, files)
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(manifest, implementation)
        self.assertTrue(any("standalone" in error for error in errors), errors)

    def test_standalone_dependency_versions_must_be_nonblank_strings(self):
        standalone = self.run / "standalone"
        (standalone / "src").mkdir(parents=True)
        package = {
            "scripts": {"dev": "vite", "build": "vite build"},
            "dependencies": {"react": "", "react-dom": "1"},
            "devDependencies": {
                "vite": "1",
                "typescript": "1",
                "@vitejs/plugin-react": "1",
            },
        }
        (standalone / "package.json").write_text(json.dumps(package), encoding="utf-8")
        (standalone / "index.html").write_text(
            '<script type="module" src="/src/main.tsx"></script>', encoding="utf-8"
        )
        (standalone / "vite.config.ts").write_text(
            "import react from '@vitejs/plugin-react'; export default {plugins:[react()]}",
            encoding="utf-8",
        )
        (standalone / "tsconfig.json").write_text("{}", encoding="utf-8")
        (standalone / "src" / "main.tsx").write_text(
            "import {createRoot} from 'react-dom/client'; import App from './App'; "
            "createRoot(document.body).render(<App/>);",
            encoding="utf-8",
        )
        (standalone / "src" / "App.tsx").write_text("export default ()=> <main/>", encoding="utf-8")
        files = [
            "standalone/package.json",
            "standalone/index.html",
            "standalone/vite.config.ts",
            "standalone/tsconfig.json",
            "standalone/src/main.tsx",
            "standalone/src/App.tsx",
        ]
        implementation = copy.deepcopy(self.implementation)
        implementation.update(
            mode="standalone",
            preview_path="standalone/src/main.tsx",
            preview_files=files,
        )
        digest = preview_digest(self.run, files)
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(run_manifest(None), implementation)
        self.assertTrue(any("dependencies" in error for error in errors), errors)

    def test_standalone_rejects_echo_comment_only_and_inert_scaffolds(self):
        standalone = self.run / "standalone"
        (standalone / "src").mkdir(parents=True)
        (standalone / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"dev": "echo vite", "build": "echo vite build"},
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
        (standalone / "index.html").write_text(
            '<!-- <script type="module" src="/src/main.tsx"></script> -->',
            encoding="utf-8",
        )
        (standalone / "vite.config.ts").write_text(
            "// import { defineConfig } from 'vite';\n"
            "const fake=\"import react from '@vitejs/plugin-react'; "
            "export default defineConfig({plugins:[react()]})\";",
            encoding="utf-8",
        )
        (standalone / "tsconfig.json").write_text(
            '{"compilerOptions":{}}', encoding="utf-8"
        )
        (standalone / "src" / "main.tsx").write_text(
            "// import {createRoot} from 'react-dom/client';\n"
            "const fake=\"import App from './App'; createRoot(x).render(<App/>)\";",
            encoding="utf-8",
        )
        (standalone / "src" / "App.tsx").write_text(
            "const fake = '<main>not JSX</main>';", encoding="utf-8"
        )
        files = [
            "standalone/package.json",
            "standalone/index.html",
            "standalone/vite.config.ts",
            "standalone/tsconfig.json",
            "standalone/src/main.tsx",
            "standalone/src/App.tsx",
        ]
        implementation = copy.deepcopy(self.implementation)
        implementation.update(
            mode="standalone",
            preview_path="standalone/src/main.tsx",
            preview_files=files,
        )
        digest = preview_digest(self.run, files)
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(run_manifest(None), implementation)
        for expected in ("scripts", "index.html", "main.tsx", "App.tsx", "vite.config.ts", "tsconfig"):
            self.assertTrue(any(expected in error for error in errors), (expected, errors))

    def test_project_route_imports_ignore_multiline_template_and_string_decoys(self):
        consumer = self.project / "previews" / "App.tsx"
        consumer.write_text(
            "const quoted = \"import registry from './routes.json'\";\n"
            "const templated = `first line\n"
            "import registry from './routes.json'\n"
            "import { Preview } from './Checkout'\n"
            "createRoot(root).render(<Preview />)\n"
            "`;",
            encoding="utf-8",
        )
        implementation = copy.deepcopy(self.implementation)
        digest = preview_digest(self.project, implementation["preview_files"])
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("static imports" in error for error in errors), errors)

        consumer.write_text(
            "import type registry from './routes.json';\n"
            "import type { Preview } from './Checkout';",
            encoding="utf-8",
        )
        implementation = copy.deepcopy(self.implementation)
        digest = preview_digest(self.project, implementation["preview_files"])
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("static imports" in error for error in errors), errors)

        consumer.write_text(
            "import {\n"
            "  Preview\n"
            "} from './Checkout';\n"
            "import registry\n"
            "  from './routes.json';\n"
            "export const resolve = (path: string) => registry[path] ? Preview : null;",
            encoding="utf-8",
        )
        implementation = copy.deepcopy(self.implementation)
        digest = preview_digest(self.project, implementation["preview_files"])
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        self.assertEqual(self.validate(implementation=implementation), [])

    def test_js_lexer_ignores_escaped_regex_and_computed_import_decoys(self):
        source = r'''
const quoted = "escaped \" import fake from './quoted'";
const regex = /import\s+fake\s+from\s+["']\.\/regex["']\/\//;
const templated = `import fake from './template'`;
const lazy = import('./computed' + suffix);
const required = require('./required' + suffix);
/* import fake from './commented' */
import {
  real
} from './real';
'''
        self.assertEqual(validator.js_dependencies(source), ["./real"])

    def test_named_import_bindings_exclude_inline_type_and_side_effect_forms(self):
        _, references = validator.js_module_references(
            "import { type Meta, runtime as Registry, type Foo as Bar } "
            "from './routes.json';\n"
            "import type { EntirelyType } from './types';\n"
            "import './side-effect';\n"
            "import DefaultPreview from './default';\n"
            "import * as PreviewNamespace from './namespace';"
        )
        by_path = {reference.specifier: reference for reference in references}
        self.assertEqual(
            validator.import_bindings(by_path["./routes.json"]),
            {"runtime": "Registry"},
        )
        self.assertEqual(validator.import_bindings(by_path["./types"]), {})
        self.assertEqual(validator.import_bindings(by_path["./side-effect"]), {})
        self.assertEqual(
            validator.import_bindings(by_path["./default"]),
            {"default": "DefaultPreview"},
        )
        self.assertEqual(
            validator.import_bindings(by_path["./namespace"]),
            {"*": "PreviewNamespace"},
        )

    def test_project_route_wiring_requires_runtime_bindings_for_both_files(self):
        consumer = self.project / "previews" / "App.tsx"
        for source in (
            "import './routes.json';\nimport './Checkout';",
            "import { type Meta, runtime as Registry } from './routes.json';\n"
            "import type { Preview } from './Checkout';",
        ):
            with self.subTest(source=source):
                consumer.write_text(source, encoding="utf-8")
                implementation = copy.deepcopy(self.implementation)
                digest = preview_digest(self.project, implementation["preview_files"])
                for check in implementation["verification"]["viewport_checks"].values():
                    check["source_digest"] = digest
                errors = self.validate(implementation=implementation)
                self.assertTrue(any("runtime bindings" in error for error in errors), errors)

        consumer.write_text(
            "import { type Meta, runtime as Registry } from './routes.json';\n"
            "import * as PreviewNamespace from './Checkout';\n"
            "export const resolve = (path: string) => "
            "Registry[path] ? PreviewNamespace.Preview : null;",
            encoding="utf-8",
        )
        implementation = copy.deepcopy(self.implementation)
        digest = preview_digest(self.project, implementation["preview_files"])
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        self.assertEqual(self.validate(implementation=implementation), [])

    def test_standalone_calls_must_be_bound_to_real_multiline_imports(self):
        standalone = self.run / "standalone"
        (standalone / "src").mkdir(parents=True)
        (standalone / "package.json").write_text(
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
        (standalone / "index.html").write_text(
            '<div id="root"></div><script type="module" src="/src/main.tsx"></script>',
            encoding="utf-8",
        )
        (standalone / "tsconfig.json").write_text(
            '{"compilerOptions":{"jsx":"react-jsx","module":"ESNext"}}',
            encoding="utf-8",
        )
        (standalone / "src" / "App.tsx").write_text(
            "export default function App(){return <main>Preview</main>}",
            encoding="utf-8",
        )
        (standalone / "src" / "main.tsx").write_text(
            "import 'react';\n"
            "import 'react-dom/client';\n"
            "import './App';\n"
            "const quoted = \"createRoot(root).render(<App />)\";\n"
            "const templated = `line one\ncreateRoot(root).render(<App />)\n`;\n"
            "createRoot(document.getElementById('root')!).render(<App />);",
            encoding="utf-8",
        )
        (standalone / "vite.config.ts").write_text(
            "import 'vite';\n"
            "import '@vitejs/plugin-react';\n"
            "const quoted = \"defineConfig({plugins:[react()]})\";\n"
            "export default defineConfig({plugins:[react()]});",
            encoding="utf-8",
        )
        files = [
            "standalone/package.json",
            "standalone/index.html",
            "standalone/vite.config.ts",
            "standalone/tsconfig.json",
            "standalone/src/main.tsx",
            "standalone/src/App.tsx",
        ]
        implementation = copy.deepcopy(self.implementation)
        implementation.update(
            mode="standalone",
            preview_path="standalone/src/main.tsx",
            preview_files=files,
        )
        digest = preview_digest(self.run, files)
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(run_manifest(None), implementation)
        self.assertTrue(any("main.tsx" in error for error in errors), errors)
        self.assertTrue(any("vite.config.ts" in error for error in errors), errors)

        (standalone / "src" / "main.tsx").write_text(
            "import React, {\n"
            "  StrictMode\n"
            "} from 'react';\n"
            "import {\n"
            "  createRoot as mount\n"
            "} from 'react-dom/client';\n"
            "import PreviewApp\n"
            "  from './App';\n"
            "mount(document.getElementById('root')!).render(<PreviewApp />);",
            encoding="utf-8",
        )
        (standalone / "vite.config.ts").write_text(
            "import {\n"
            "  defineConfig as config\n"
            "} from 'vite';\n"
            "import reactPlugin\n"
            "  from '@vitejs/plugin-react';\n"
            "export default config({plugins:[reactPlugin()]});",
            encoding="utf-8",
        )
        implementation = copy.deepcopy(implementation)
        digest = preview_digest(self.run, files)
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        self.assertEqual(self.validate(run_manifest(None), implementation), [])

    def test_project_route_must_be_wired_and_source_digest_must_match(self):
        route_file = self.project / "previews" / "routes.json"
        route_file.write_text(
            '{"/different":{"component_path":"previews/Checkout.tsx","shell_id":"checkout-shell"}}',
            encoding="utf-8",
        )
        implementation = copy.deepcopy(self.implementation)
        digest = preview_digest(self.project, implementation["preview_files"])
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("preview_route" in error for error in errors), errors)

        route_file.write_text(
            '{"/design-explorer/checkout":{"component_path":"previews/Checkout.tsx","shell_id":"checkout-shell"}}',
            encoding="utf-8",
        )
        self.assertEqual(self.validate(), [])

        consumer = self.project / "previews" / "App.tsx"
        consumer.write_text(
            "// import registry from './routes.json';\n"
            "const fake = \"import { Preview } from './Checkout'\";",
            encoding="utf-8",
        )
        implementation = copy.deepcopy(self.implementation)
        digest = preview_digest(self.project, implementation["preview_files"])
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("static imports" in error for error in errors), errors)

        consumer.write_text(
            "import registry from './routes.json';\n"
            "import { Preview } from './Checkout';",
            encoding="utf-8",
        )
        implementation = copy.deepcopy(self.implementation)
        digest = preview_digest(self.project, implementation["preview_files"])
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        self.assertEqual(self.validate(implementation=implementation), [])

        (self.project / "previews" / "Actual.tsx").write_text("changed", encoding="utf-8")
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("source_digest" in error for error in errors), errors)

    def test_project_dependency_closure_rejects_unlisted_and_symlink_escape(self):
        implementation = copy.deepcopy(self.implementation)
        implementation["preview_files"].remove("previews/mark.svg")
        digest = preview_digest(self.project, implementation["preview_files"])
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("unlisted local dependency" in error for error in errors), errors)

        outside = self.root / "outside.ts"
        outside.write_text("export const escaped = true", encoding="utf-8")
        escape = self.project / "previews" / "escape.ts"
        escape.symlink_to(outside)
        (self.project / "previews" / "Actual.tsx").write_text(
            "import './styles.css'; import './escape'; export const Preview=()=> <main/>;",
            encoding="utf-8",
        )
        implementation = copy.deepcopy(self.implementation)
        implementation["preview_files"].append("previews/escape.ts")
        errors = self.validate(implementation=implementation)
        self.assertTrue(
            any("contained" in error or "existing file" in error for error in errors),
            errors,
        )

    def test_project_render_assets_from_new_url_and_jsx_are_digest_bound(self):
        actual = self.project / "previews" / "Actual.tsx"
        (self.project / "previews" / "hero.svg").write_text(
            "<svg id='hero'/>", encoding="utf-8"
        )
        (self.project / "previews" / "icon.svg").write_text(
            "<svg id='icon'/>", encoding="utf-8"
        )
        (self.project / "previews" / "retina.svg").write_text(
            "<svg id='retina'/>", encoding="utf-8"
        )
        actual.write_text(
            "import './styles.css';\n"
            "const hero = new URL('./hero.svg', import.meta.url);\n"
            "export const Preview=()=> <main><img src={'./icon.svg'} "
            "srcSet={'data:image/svg+xml,%3Csvg%3E 1x, ./retina.svg 2x, "
            "https://example.com/remote.svg 3x'} /></main>;",
            encoding="utf-8",
        )
        implementation = copy.deepcopy(self.implementation)
        digest = preview_digest(self.project, implementation["preview_files"])
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        errors = self.validate(implementation=implementation)
        self.assertGreaterEqual(
            sum("unlisted local dependency" in error for error in errors), 3, errors
        )

        implementation["preview_files"].extend(
            ["previews/hero.svg", "previews/icon.svg", "previews/retina.svg"]
        )
        digest = preview_digest(self.project, implementation["preview_files"])
        for check in implementation["verification"]["viewport_checks"].values():
            check["source_digest"] = digest
        self.assertEqual(self.validate(implementation=implementation), [])

        (self.project / "previews" / "hero.svg").write_text(
            "<svg id='mutated'/>", encoding="utf-8"
        )
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("source_digest" in error for error in errors), errors)

    def test_jsx_brace_literals_and_mixed_srcset_extract_only_local_assets(self):
        source = (
            "const prose = './not-an-asset.svg';\n"
            "export const Preview=()=> <main>"
            "<img src='./direct.svg' />"
            "<a href=\"./manual.pdf\">Manual</a>"
            "<img src={'./icon.svg'} />"
            "<a href={\"./guide.pdf\"}>Guide</a>"
            "<video poster={'../poster.jpg'} />"
            "<source srcSet={'data:image/svg+xml,%3Csvg%3E 1x, "
            "./small.webp 1x, ../large.webp 2x, "
            "https://example.com/remote.webp 3x'} />"
            "</main>;"
        )
        self.assertEqual(
            validator.js_dependencies(source),
            [
                "./direct.svg",
                "./manual.pdf",
                "./icon.svg",
                "./guide.pdf",
                "../poster.jpg",
                "./small.webp",
                "../large.webp",
            ],
        )

    def test_jsx_render_asset_symlink_escape_is_rejected(self):
        outside = self.root / "outside.svg"
        outside.write_text("<svg/>", encoding="utf-8")
        escape = self.project / "previews" / "escape.svg"
        escape.symlink_to(outside)
        (self.project / "previews" / "Actual.tsx").write_text(
            "export const Preview=()=> <img src={'./escape.svg'} />;",
            encoding="utf-8",
        )
        implementation = copy.deepcopy(self.implementation)
        implementation["preview_files"].append("previews/escape.svg")
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("contained" in error or "existing file" in error for error in errors), errors)

    def test_aggregate_checks_cannot_substitute_for_viewport_evidence(self):
        implementation = copy.deepcopy(self.implementation)
        implementation["verification"].pop("viewport_checks")
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("viewport_checks" in error for error in errors), errors)

    def test_rendered_viewports_must_exactly_match_unique_run_targets(self):
        for rendered in (
            ["390x844"],
            ["390x844", "390x844"],
            ["1440x900", "390x844"],
            ["banana", "1440x900"],
        ):
            with self.subTest(rendered=rendered):
                implementation = copy.deepcopy(self.implementation)
                implementation["verification"]["rendered_viewports"] = rendered
                errors = self.validate(implementation=implementation)
                self.assertTrue(any("rendered_viewports" in error for error in errors), errors)

    def test_each_viewport_requires_exact_item_maps_and_pass_statuses(self):
        mutations = (
            lambda check: check["required_content"].pop("Total"),
            lambda check: check["required_content"].update({"Extra": "pass"}),
            lambda check: check["required_interactions"].clear(),
            lambda check: check.update(interaction="pending"),
            lambda check: check["required_content"].update({"Total": "fail"}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                implementation = copy.deepcopy(self.implementation)
                mutate(implementation["verification"]["viewport_checks"]["390x844"])
                errors = self.validate(implementation=implementation)
                self.assertTrue(any("390x844" in error for error in errors), errors)

    def test_screenshot_must_be_safe_existing_png_with_exact_dimensions(self):
        cases = (
            ("../390x844.png", None),
            ("evidence/missing.png", None),
            ("evidence/not-png.png", b"not png"),
        )
        for screenshot_ref, payload in cases:
            with self.subTest(screenshot_ref=screenshot_ref):
                implementation = copy.deepcopy(self.implementation)
                implementation["verification"]["viewport_checks"]["390x844"]["screenshot_ref"] = screenshot_ref
                if payload is not None:
                    (self.run / screenshot_ref).write_bytes(payload)
                errors = self.validate(implementation=implementation)
                self.assertTrue(any("screenshot" in error for error in errors), errors)

        write_png(self.run / "evidence/wrong.png", 391, 844)
        implementation = copy.deepcopy(self.implementation)
        implementation["verification"]["viewport_checks"]["390x844"]["screenshot_ref"] = "evidence/wrong.png"
        errors = self.validate(implementation=implementation)
        self.assertTrue(any("dimensions" in error for error in errors), errors)

    def test_project_paths_must_exist_and_preview_must_not_overlap_production(self):
        cases = []
        missing = copy.deepcopy(self.implementation)
        missing["preview_files"] = ["previews/missing.tsx"]
        missing["preview_path"] = "previews/missing.tsx"
        cases.append((self.manifest, missing, "existing file"))

        overlap_manifest = copy.deepcopy(self.manifest)
        overlap_manifest["production_paths"] = ["previews"]
        cases.append((overlap_manifest, self.implementation, "production_path"))

        missing_production_manifest = copy.deepcopy(self.manifest)
        missing_production_manifest["production_paths"] = ["src/Missing.tsx"]
        cases.append(
            (missing_production_manifest, self.implementation, "production_path must exist")
        )

        unsafe = copy.deepcopy(self.implementation)
        unsafe["preview_path"] = "../Checkout.tsx"
        unsafe["preview_files"] = ["../Checkout.tsx"]
        cases.append((self.manifest, unsafe, "safe project-relative"))

        for manifest, implementation, expected in cases:
            with self.subTest(expected=expected):
                errors = self.validate(manifest, implementation)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_preview_route_is_a_normalized_absolute_url_path(self):
        for route in (
            "preview",
            "//preview",
            "/a//b",
            "/a/../b",
            "/a/%2e%2e/b",
            "/a/%252e%252e/b",
            "/a/%25252e%25252e/b",
            "/a/%252f/b",
            "/a/%255c/b",
            "/a/%ZZ/b",
            "/a/%ff/b",
            "/a/%C3%28/b",
            "/a/%25/b",
            "/preview%253fq=1",
            "/preview%2523fragment",
            "/preview?q=1",
            "/preview#x",
        ):
            with self.subTest(route=route):
                self.assertFalse(validator.valid_preview_route(route))
                implementation = copy.deepcopy(self.implementation)
                implementation["preview_route"] = route
                errors = self.validate(implementation=implementation)
                self.assertTrue(any("preview_route" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
