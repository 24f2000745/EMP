import os
import tempfile
import unittest

# Set before importing app so tests use a disposable database.
_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["EMP_DATABASE"] = _db_path
os.environ["EMP_SECRET_KEY"] = "test-secret-key"
os.environ["EMP_ADMIN_EMAIL"] = "admin@test.local"
os.environ["EMP_ADMIN_PASSWORD"] = "Admin@123"

import app as emp

class EMPFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        emp.init_db()
        emp.app.config.update(TESTING=True)

    def setUp(self):
        self.client = emp.app.test_client()

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_dashboard_requires_login(self):
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 401)

    def test_admin_login_and_dashboard(self):
        response = self.client.post("/api/auth/login", json={
            "email": "admin@test.local", "password": "Admin@123"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["role"], "admin")
        dashboard = self.client.get("/api/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("courses", dashboard.get_json()["counts"])

    def test_student_register(self):
        response = self.client.post("/api/auth/register", json={
            "full_name": "Test Student",
            "email": "student@test.local",
            "password": "StrongPass123",
            "student_number": "STU-001"
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["user"]["role"], "student")

    def test_non_admin_cannot_list_users(self):
        self.client.post("/api/auth/register", json={
            "full_name": "Test Student 2",
            "email": "student2@test.local",
            "password": "StrongPass123"
        })
        response = self.client.get("/api/admin/users")
        self.assertEqual(response.status_code, 403)

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(_db_path)
        except OSError:
            pass

if __name__ == "__main__":
    unittest.main()
