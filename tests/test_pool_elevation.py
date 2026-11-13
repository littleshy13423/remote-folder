import threading
import unittest
from unittest.mock import Mock, patch

from app.pool import AuthFailed, ConnectionDropped, Pool


class PoolElevationTests(unittest.TestCase):
    session = {"id": "test", "host": "server", "port": 22, "username": "alice"}

    def setUp(self):
        self.pool = Pool()
        self.dial = self.patch("app.pool.dial")
        self.dial.side_effect = self.fake_connection
        self.patch("app.pool.sudo_access.find_sftp_server").return_value = "/usr/lib/openssh/sftp-server"
        self.su = self.patch("app.pool.sudo_access.open_su_sftp")
        self.sudo = self.patch("app.pool.sudo_access.open_sftp")
        self.su.return_value.get_channel.return_value.closed = False
        self.sudo.return_value.get_channel.return_value.closed = False
        self.connect()
        self.addCleanup(self.pool.disconnect, "test")

    def patch(self, name):
        patcher = patch(name)
        self.addCleanup(patcher.stop)
        return patcher.start()

    @staticmethod
    def fake_connection(*_args):
        client, sftp = Mock(), Mock()
        client.get_transport.return_value.is_active.return_value = True
        sftp.get_channel.return_value.closed = False
        return client, sftp, "/home/alice"

    def connect(self):
        self.pool.connect(self.session, "login", None, False)
        self.live = self.pool.call("test", lambda live: live)

    def test_su_borrows_authenticated_channel_and_discards_root_password(self):
        user = self.live.user_sftp
        self.pool.enable_sudo("test", "root secret", method="su")
        for _ in range(2):
            with self.pool.transfer(self.session) as link:
                self.assertEqual(link.elevation_method, "su")
                self.assertIs(link.sftp, self.su.return_value)
                self.assertIsNone(link.client)
                self.assertIsNone(link.sudo_password)
        self.assertEqual(self.su.call_count, 1)
        self.assertEqual(self.dial.call_count, 1)
        self.sudo.assert_not_called()
        self.assertNotIn("root secret", vars(self.live).values())
        self.pool.disable_sudo("test")
        self.su.return_value.close.assert_called_once_with()
        self.assertIs(self.live.sftp, user)
        self.assertIsNone(self.pool.elevation_method("test"))

    def test_passwordless_sudo_and_switch_to_su(self):
        self.pool.enable_sudo("test", "")
        with self.pool.transfer(self.session) as first:
            self.assertTrue(first.sudo)
            self.assertEqual(first.elevation_method, "sudo")
            self.assertIsNotNone(first.client)
        with self.pool.transfer(self.session) as second:
            self.assertIs(second, first)
        self.assertEqual([c.args[-1] for c in self.sudo.call_args_list], ["", ""])
        self.su.side_effect = PermissionError("failed")
        with self.assertRaises(AuthFailed):
            self.pool.enable_sudo("test", "incorrect", method="su")
        self.assertEqual(self.pool.elevation_method("test"), "sudo")
        self.assertFalse(first.closed)
        self.su.side_effect = None
        self.pool.enable_sudo("test", "root secret", method="su")
        self.assertTrue(first.closed)
        self.assertEqual(self.pool.elevation_method("test"), "su")

    def test_su_disable_waits_for_borrowed_channel(self):
        self.pool.enable_sudo("test", "secret", method="su")
        entered = threading.Event()
        def disable():
            entered.set()
            self.pool.disable_sudo("test")
        with self.pool.transfer(self.session):
            worker = threading.Thread(target=disable, daemon=True)
            worker.start()
            self.assertTrue(entered.wait(1))
            self.su.return_value.close.assert_not_called()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.pool.sudo_enabled("test"))
        self.su.return_value.close.assert_called_once_with()

    def test_elevated_channel_failure_preserves_login(self):
        self.pool.enable_sudo("test", "secret", method="su")
        with self.assertRaises(ConnectionError):
            with self.pool.transfer(self.session):
                raise EOFError()
        self.assertFalse(self.pool.sudo_enabled("test"))
        self.assertTrue(self.pool.connected("test"))
        self.assertIs(self.live.sftp, self.live.user_sftp)
        self.live.client.close.assert_not_called()

    def test_reconnect_and_transport_drop_revoke_transfers(self):
        self.pool.enable_sudo("test", "sudo secret")
        with self.pool.transfer(self.session) as first:
            pass
        self.connect()
        self.assertTrue(first.closed)
        self.assertIsNone(first.sudo_password)
        self.assertFalse(self.pool.sudo_enabled("test"))
        self.pool.enable_sudo("test", "sudo secret")
        with self.pool.transfer(self.session) as second:
            self.assertIsNot(second, first)
        self.live.client.get_transport.return_value.is_active.return_value = False
        with self.assertRaises(ConnectionDropped):
            with self.pool.transfer(self.session):
                self.fail("Transfer continued after disconnect")
        self.assertTrue(second.closed)
        self.assertIsNone(self.live.sudo_password)
        self.assertFalse(self.pool.connected("test"))


if __name__ == "__main__":
    unittest.main()
