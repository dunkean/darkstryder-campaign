"""Local converter smoke tests; no model loading, network or original-book access."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/'tools/extract'))
spec=importlib.util.spec_from_file_location('documents',ROOT/'tools/extract/documents.py')
documents=importlib.util.module_from_spec(spec);spec.loader.exec_module(documents)

class NativeConversion(unittest.TestCase):
    def test_linked_images_tables_and_no_remote_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'output';(folder/'images').mkdir(parents=True)
            Image.new('RGB',(2,2),'red').save(root/'test.png')
            original=root/'page.html'
            original.write_text('<script>DO_NOT_KEEP_THIS</script><h1>Titre</h1>'
                '<a href="other.html"><img src="test.png" alt="Plan"></a>'
                '<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>'
                '<img src="https://example.invalid/no-network.png">'
                '<img src="../../outside.png">')
            report={'warnings':[],'media':[]}
            text,structure=documents.html_document(original,folder,report,root)
            self.assertNotIn('DO_NOT_KEEP_THIS',text)
            self.assertIn('# Titre',text)
            self.assertIn('[![Plan](../images/',text)
            self.assertIn(')](other.html)',text)
            self.assertIn('| A | B |',text)
            self.assertEqual(len(report['media']),1)
            self.assertEqual({w['kind'] for w in report['warnings']},
                             {'remote-image-not-fetched','missing-local-image'})
            self.assertEqual(structure['kind'],'html')

if __name__=='__main__':unittest.main()
