"""Import preview and commit retain labels from a portable YOLO export."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from workbench.pipeline import export_project
from workbench.review_workflow import ReviewWorkflow
from workbench.store import ProjectStore


class ImportAnnotationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vision-import-annotations-')
        self.root = Path(self.temp.name)
        self.image = self.root / 'original.png'
        Image.new('RGB', (80, 60), (120, 130, 140)).save(self.image)
        validation_image = self.root / 'validation.png'
        Image.new('RGB', (80, 60), (140, 130, 120)).save(validation_image)
        shape = {'id': 'box-1', 'type': 'rectangle', 'label': 'grip_done',
                 'x': 10, 'y': 12, 'width': 30, 'height': 20}
        assets = []
        for index, (path, split) in enumerate(((self.image, 'train'), (validation_image, 'val')), 1):
            assets.append({'id': f'asset-{index}', 'name': path.name, 'width': 80, 'height': 60,
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'image_path': str(path), 'batch_id': f'source-batch-{index}',
                           'split': split, 'source': {}, 'revision': 1,
                           'review_state': 'approved', 'shapes': [shape]})
        snapshot = {'id': 'source', 'name': 'portable', 'revision': 1,
                    'classes': ['grip_done'], 'assets': assets}
        exported = export_project(snapshot, self.root / 'exports', 'yolo_detection')
        self.exported_image = next((Path(exported['path']) / 'images' / 'train').glob('*.png'))
        self.store = ProjectStore(self.root / 'projects')
        self.pid = self.store.create_project('destination')['id']
        self.workflow = ReviewWorkflow(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_selected_export_image_imports_its_annotation(self):
        preview = self.workflow.preview(self.pid, [str(self.exported_image)])
        self.assertEqual(preview['issues'], [])
        self.assertEqual(preview['items'][0]['shape_count'], 1)
        result = self.workflow.commit_import(self.pid, preview['token'], ['0'])
        self.assertEqual((result['added'], result['updated']), (1, 0))
        asset = self.store.get_asset(self.pid, result['asset_ids'][0])
        self.assertEqual(asset['shapes'][0]['label'], 'grip_done')
        self.assertEqual(asset['review_state'], 'pending')

    def test_existing_unlabeled_image_receives_annotation(self):
        blank = self.store.add_assets(self.pid, [{'path': self.image, 'shapes': []}])
        asset_id = blank['asset_ids'][0]
        preview = self.workflow.preview(self.pid, [str(self.exported_image)])
        self.assertEqual(preview['items'][0]['shape_count'], 1)
        result = self.workflow.commit_import(self.pid, preview['token'], ['0'])
        self.assertEqual((result['added'], result['updated'], result['duplicates']), (0, 1, 0))
        self.assertEqual(result['updated_asset_ids'], [asset_id])
        self.assertEqual(self.store.get_asset(self.pid, asset_id)['shapes'][0]['label'], 'grip_done')


if __name__ == '__main__':
    unittest.main()
