import unittest
from windows_pluginval_command import arguments

class PluginvalCommandTests(unittest.TestCase):
 def test_installed_editor_success_does_not_skip_validator_gui(self):
  self.assertNotIn('--skip-gui-tests',arguments({'slug':'inflated-ego'},'validator','bundle',True))
 def test_clean_install_keeps_validator_gui(self):
  self.assertNotIn('--skip-gui-tests',arguments({'slug':'inflated-ego'},'validator','bundle',False))
 def test_other_products_unchanged(self):
  self.assertEqual(arguments({'slug':'zorro'},'validator','bundle',True),['validator','--strictness-level','5','--validate','bundle'])
 def test_validation_level_and_actual_installed_bundle_preserved(self):
  self.assertEqual(arguments({'slug':'inflated-ego'},'validator','installed/bundle',True),['validator','--strictness-level','5','--validate','installed/bundle'])
if __name__=='__main__':unittest.main()
