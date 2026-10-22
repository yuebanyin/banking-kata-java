import json
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.yml"
REALM_FILE = ROOT / "startup/src/main/resources/keycloak/realm-export.json"


class ComposeAuthenticationConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
        self.keycloak = self.compose["services"]["keycloak"]
        self.realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))

    def test_uses_pullable_legacy_keycloak_image_and_preserves_auth_endpoint(self):
        self.assertEqual("quay.io/keycloak/keycloak:16.0.0", self.keycloak["image"])
        self.assertEqual(["10000:8080"], self.keycloak["ports"])
        self.assertIn("postgres", self.keycloak["depends_on"])
        self.assertIn("local", self.keycloak["networks"])

    def test_imports_the_banking_realm_with_legacy_database_configuration(self):
        environment = dict(item.split("=", 1) for item in self.keycloak["environment"])
        self.assertEqual(
            {
                "DB_VENDOR": "postgres",
                "DB_ADDR": "postgres",
                "DB_DATABASE": "keycloak_db",
                "DB_USER": "postgres",
                "DB_PASSWORD": "admin",
                "KEYCLOAK_IMPORT": "/tmp/realm-export.json",
            },
            environment,
        )
        self.assertIn(
            "./startup/src/main/resources/keycloak/realm-export.json:/tmp/realm-export.json:ro",
            self.keycloak["volumes"],
        )
        self.assertEqual("banking-kata", self.realm["realm"])
        self.assertTrue(self.realm["enabled"])
        client = next(c for c in self.realm["clients"] if c["clientId"] == "test-client")
        self.assertTrue(client["enabled"])
        self.assertTrue(client["serviceAccountsEnabled"])
        self.assertFalse(client["publicClient"])
        self.assertTrue(client["secret"])


if __name__ == "__main__":
    unittest.main()
