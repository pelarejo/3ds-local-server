import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class DeploymentFilesTests(SimpleTestCase):
    def test_shell_entrypoints_have_valid_syntax(self):
        for filename in ("entrypoint.sh", "threels"):
            with self.subTest(filename=filename):
                result = subprocess.run(
                    ["sh", "-n", Path(settings.BASE_DIR) / filename],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_static_and_environment_settings_are_available(self):
        self.assertIn("whitenoise.middleware.WhiteNoiseMiddleware", settings.MIDDLEWARE)
        self.assertTrue(str(settings.STATIC_ROOT))
        self.assertTrue(str(settings.SYSTEM_ROOT))
        self.assertTrue(str(settings.DATABASES["default"]["NAME"]))

    def test_container_configuration_does_not_copy_private_data(self):
        dockerignore = (settings.BASE_DIR / ".dockerignore").read_text()
        for private_path in ("content", "data", "system", ".threels.env"):
            self.assertIn(private_path, dockerignore.splitlines())

        compose = (settings.BASE_DIR / "compose.yaml").read_text()
        self.assertIn("${THREELS_CONTENT_PATH:-./content}", compose)
        self.assertIn("${THREELS_SYSTEM_PATH:-./system}", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn("${THREELS_DATA_PATH:-./data}", compose)

    def test_setup_collects_mount_paths_and_does_not_default_to_localhost(self):
        helper = (settings.BASE_DIR / "threels").read_text()
        for variable in (
            "THREELS_CONTENT_PATH",
            "THREELS_SYSTEM_PATH",
            "THREELS_DATA_PATH",
        ):
            self.assertIn(variable, helper)
        self.assertIn("detect_lan_ipv4", helper)
        self.assertNotIn(
            'prompt "LAN hostname or IP used by clients" "localhost"', helper
        )
