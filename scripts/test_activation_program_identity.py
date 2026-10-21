import unittest
from accept_production_activation import host_checks_for_format

class ProgramIdentityTests(unittest.TestCase):
    def test_vst3_custom_slot_does_not_assert_an_unsupported_au_program_name(self):
        checks={'audioTestParameters':{'Effect':0.75},'stateProgramRestoresAsByFormat':{'VST3':'Custom'}}
        vst3=host_checks_for_format(checks,'VST3');au=host_checks_for_format(checks,'AudioUnit')
        self.assertEqual(vst3['stateProgramRestoresAs'],'Custom')
        self.assertNotIn('stateProgramRestoresAs',au)
        self.assertEqual(au['audioTestParameters'],{'Effect':0.75})
        self.assertIn('stateProgramRestoresAsByFormat',checks)
    def test_existing_shared_program_identity_remains_required(self):
        for kind in ['VST3','AudioUnit']:
            self.assertEqual(host_checks_for_format({'stateProgramRestoresAs':'Modified'},kind)['stateProgramRestoresAs'],'Modified')

if __name__=='__main__':unittest.main()
