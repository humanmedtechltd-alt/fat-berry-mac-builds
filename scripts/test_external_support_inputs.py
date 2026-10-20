import hashlib,tempfile,unittest
from pathlib import Path
from external_support_inputs import restore
class RuntimeContentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.sha=hashlib.sha256(b'sample pack').hexdigest()
        self.entry={'file':'pack.fbw','path':'runtime-content/example/waves/pack.fbw','sha256':self.sha,'bytes':11}
    def fetch(self,entry,root):
        path=root/entry['file'];path.write_bytes(b'sample pack');return path
    def digest(self,path):return hashlib.sha256(path.read_bytes()).hexdigest()
    def test_restores_exact_runtime_bytes(self):
        restore(self.root,[self.entry],self.fetch,self.digest);self.assertEqual((self.root/self.entry['path']).read_bytes(),b'sample pack')
    def test_refuses_path_escape(self):
        self.entry['path']='../customer.fbw'
        with self.assertRaises(ValueError):restore(self.root,[self.entry],self.fetch,self.digest)
    def test_refuses_corrupt_bytes(self):
        self.entry['sha256']='a'*64
        with self.assertRaises(ValueError):restore(self.root,[self.entry],self.fetch,self.digest)
if __name__=='__main__':unittest.main()
