import os
import tempfile
import unittest
from unittest import mock

import version


class VersionTest(unittest.TestCase):
    def test_first_release_of_a_base_version_is_patch_zero(self):
        self.assertEqual(version.next_release('1.0', []), '1.0.0')
        self.assertEqual(version.next_release('1.1', ['v1.0.0', 'v1.0.7']), '1.1.0')

    def test_next_release_counts_up_from_the_highest_tag(self):
        tags = ['v1.0.0', 'v1.0.2', 'v1.0.10', 'v1.0.9', 'v1.00.3', 'v1.0.x', 'other', 'v1.0.1-rc']
        self.assertEqual(version.next_release('1.0', tags), '1.0.11')

    def test_version_file_must_be_major_minor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'VERSION')
            for content, ok in (('1.2\n', True), ('1.2.3', False), ('v1', False)):
                with open(path, 'w') as f:
                    f.write(content)
                with mock.patch.object(version, 'VERSION_FILE', path):
                    if ok:
                        self.assertEqual(version.base_version(), '1.2')
                    else:
                        self.assertRaises(ValueError, version.base_version)

    def test_test_builds_carry_the_commit(self):
        with mock.patch.object(version, '_git', side_effect=lambda *a: 'v1.0.0 v1.0.1' if a[0] == 'tag' else 'abc1234'), \
                mock.patch.object(version, 'base_version', return_value='1.0'):
            self.assertEqual(version.compute(release=True), '1.0.2')
            self.assertEqual(version.compute(), '1.0.2-dev+abc1234')


if __name__ == '__main__':
    unittest.main()
