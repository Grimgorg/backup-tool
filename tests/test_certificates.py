import os
import ssl
import unittest
from unittest import mock

from vaultsync import common


def verify_paths(cafile, capath):
    return ssl.DefaultVerifyPaths(cafile, capath, 'SSL_CERT_FILE', cafile, 'SSL_CERT_DIR', capath)


class CertificatesTest(unittest.TestCase):
    def test_uses_certifi_when_the_system_certificates_are_missing(self):
        # As in the macOS build: OpenSSL points at the build machine's Python folder.
        missing = verify_paths('/Library/Frameworks/Python.framework/x/cert.pem', '/Library/Frameworks/x/certs')
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(common.ssl, 'get_default_verify_paths', return_value=missing):
            os.environ.pop('SSL_CERT_FILE', None)
            common.use_bundled_certificates()
            import certifi
            self.assertEqual(os.environ['SSL_CERT_FILE'], certifi.where())
            self.assertTrue(os.path.isfile(os.environ['SSL_CERT_FILE']))

    def test_keeps_system_certificates_and_explicit_settings(self):
        found = verify_paths(os.path.abspath(__file__), None)  # any existing file stands for a CA file
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(common.ssl, 'get_default_verify_paths', return_value=found):
            os.environ.pop('SSL_CERT_FILE', None)
            common.use_bundled_certificates()
            self.assertNotIn('SSL_CERT_FILE', os.environ)
        with mock.patch.dict(os.environ, {'SSL_CERT_FILE': '/my/ca.pem'}):
            common.use_bundled_certificates()
            self.assertEqual(os.environ['SSL_CERT_FILE'], '/my/ca.pem')


if __name__ == '__main__':
    unittest.main()
