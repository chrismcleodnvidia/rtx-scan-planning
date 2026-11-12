"""Native CAD input selection and cache behavior without Isaac Sim."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import core


class NativeContract(unittest.TestCase):
    def test_scene_requires_native_asset_even_with_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            model = folder / 'model'
            model.mkdir()
            (model / 'scan-scene.usda').touch()
            with self.assertRaises(FileNotFoundError):
                core.scene_path(folder)
            (model / 'building.usdc').touch()
            self.assertEqual(core.scene_path(folder), model / 'scan-scene.usda')
            (model / 'scan-scene.usda').unlink()
            self.assertEqual(core.scene_path(folder), model / 'building.usdc')

    def test_unsupported_backend_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            core.write(folder / 'project.json', {'metadata_backend': 'unsupported'})
            with self.assertRaisesRegex(ValueError, 'native NVIDIA CAD'):
                core.bim_metadata(folder)

    def test_native_metadata_cache_invalidates_when_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            scene = folder / 'model/building.usdc'
            scene.parent.mkdir()
            scene.write_text('first source')
            core.write(folder / 'project.json', {
                'metadata_backend': 'nvidia-cad-native', 'source_revision': 'revision'})
            (folder / 'native_ifc.py').write_text('reader version')
            expected = {'authority': 'USD', 'elements': {}}
            with patch.object(core, 'ROOT', folder), patch('native_ifc.metadata', return_value=expected) as reader:
                self.assertEqual(core.bim_metadata(folder), expected)
                self.assertEqual(core.bim_metadata(folder), expected)
                self.assertEqual(reader.call_count, 1)
                scene.write_text('changed source')
                self.assertEqual(core.bim_metadata(folder), expected)
                self.assertEqual(reader.call_count, 2)
