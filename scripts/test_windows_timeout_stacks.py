import io,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from windows_timeout_stacks import capture

class TimeoutStacksTests(unittest.TestCase):
 def test_capture_uses_pinned_debugger_and_retains_failure_diagnostics(self):
  with tempfile.TemporaryDirectory()as temp:
   work=Path(temp);(work/'windows-debugger').mkdir();(work/'windows-debugger/cdb.exe').touch()
   with patch('windows_timeout_stacks.subprocess.run')as run:
    capture(123,work,io.StringIO())
   self.assertEqual(run.call_args.args[0][-1], '~* kb; lm; q')
   self.assertEqual(run.call_args.kwargs['timeout'],45)
   self.assertTrue((work/'timeout-stack-123.log').is_file())
 def test_unconfigured_debugger_does_not_change_command_execution(self):
  with tempfile.TemporaryDirectory()as temp,patch('windows_timeout_stacks.subprocess.run')as run:
   capture(123,Path(temp),io.StringIO());run.assert_not_called()
 def test_diagnostic_failure_is_recorded_without_becoming_success(self):
  with tempfile.TemporaryDirectory()as temp:
   work=Path(temp);(work/'windows-debugger').mkdir();(work/'windows-debugger/cdb.exe').touch();log=io.StringIO()
   with patch('windows_timeout_stacks.subprocess.run',side_effect=OSError()):capture(123,work,log)
   self.assertIn('OSError',log.getvalue())
if __name__=='__main__':unittest.main()
